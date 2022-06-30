from collections.abc import Iterable
from logging import warning
import math
import warnings
import xml.etree.ElementTree as etree

from bs4 import BeautifulSoup
import kd_tree
import numpy
from xfl2svg.shape.edge import EDGE_TOKENIZER, edge_format_to_point_lists

from .util import ColorObject


def deserialize_matrix(matrix):
    if matrix == None:
        return numpy.identity(2), numpy.zeros(2)

    linear = numpy.array([[matrix[0], matrix[1]], [matrix[2], matrix[3]]])
    translation = numpy.array([matrix[4], matrix[5]])
    return linear, translation


def serialize_matrix(linear, translation):
    return [
        linear[0, 0],
        linear[0, 1],
        linear[1, 0],
        linear[1, 1],
        translation[0],
        translation[1],
    ]


def matrix_interpolation(start, end, n_frames, ease):
    start_linear, start_translation = deserialize_matrix(start)
    end_linear, end_translation = deserialize_matrix(end)

    srot, sshear, sx, sy = adobe_decomposition(start_linear)
    erot, eshear, ex, ey = adobe_decomposition(end_linear)

    if abs(erot - srot) > math.pi:
        srot += (erot - srot) / abs(erot - srot) * 2 * math.pi

    for i in range(n_frames):
        frot = ease["rotation"](i / (n_frames - 1)).y
        fscale = ease["scale"](i / (n_frames - 1)).y
        fpos = ease["position"](i / (n_frames - 1)).y

        interpolated_linear = adobe_matrix(
            frot * erot + (1 - frot) * srot,
            frot * eshear + (1 - frot) * sshear,
            fscale * ex + (1 - fscale) * sx,
            fscale * ey + (1 - fscale) * sy,
        )
        interpolated_translation = (
            fpos * end_translation + (1 - fpos) * start_translation
        )

        yield serialize_matrix(interpolated_linear, interpolated_translation)


def adobe_decomposition(a):
    rotation = math.atan2(a[1, 0], a[0, 0])
    shear = math.pi / 2 + rotation - math.atan2(a[1, 1], a[0, 1])
    if math.cos(shear) < 0:
        shear = shear % (math.pi * 2) - 2 * math.pi
    scale_x = math.sqrt(a[0, 0] ** 2 + a[1, 0] ** 2)
    scale_y = math.sqrt(a[0, 1] ** 2 + a[1, 1] ** 2)

    return rotation, shear, scale_x, scale_y


def adobe_matrix(rotation, shear, scale_x, scale_y):
    rotation_matrix = numpy.array(
        [
            [math.cos(rotation), -math.sin(rotation)],
            [math.sin(rotation), math.cos(rotation)],
        ]
    )

    skew_matrix = numpy.array([[1, math.tan(shear)], [0, 1]])

    scale_matrix = numpy.array([[scale_x, 0], [0, scale_y * math.cos(shear)]])

    return rotation_matrix @ skew_matrix @ scale_matrix


_COLOR_IDENTITIY = ColorObject(1, 1, 1, 1, 0, 0, 0, 0)


def color_interpolation(start, end, n_frames, ease):
    if start == None:
        start = _COLOR_IDENTITIY
    if end == None:
        end = _COLOR_IDENTITIY

    for i in range(n_frames):
        frac = ease["color"](i / (n_frames - 1)).y
        # need to do filters too
        yield frac * end + (1 - frac) * start


def interpolate_points(start, end, i, duration, ease):
    sx, sy = start
    ex, ey = end
    frac = ease["position"](i / (duration - 1)).y
    return [(ex - sx) * frac + sx, (ey - sy) * frac + sy]


def split_colors(color):
    if not color:
        return 0, 0, 0
    if not color.startswith("#"):
        raise Exception(f"invalid color: {color}")
    assert len(color) == 7
    r = int(color[1:3], 16)
    g = int(color[3:5], 16)
    b = int(color[5:7], 16)
    return r, g, b


def interpolate_value(x, y, idx, duration, ease):
    frac = ease(idx / (duration - 1)).y
    result = (1 - frac) * x + frac * y
    return result


def interpolate_color(x, y, i, duration, ease):
    colx, ax = x
    coly, ay = y

    rx, gx, bx = split_colors(colx)
    ry, gy, by = split_colors(coly)
    ri = round(interpolate_value(rx, ry, i, duration, ease["color"]))
    gi = round(interpolate_value(gx, gy, i, duration, ease["color"]))
    bi = round(interpolate_value(bx, by, i, duration, ease["color"]))
    ai = round(interpolate_value(ax, ay, i, duration, ease["color"]))
    return ("#%02X%02X%02X" % (ri, gi, bi), ai)


def get_color_map(xmlnode, name):
    result = {}
    for child in xmlnode.findChildren(name, recursive=False):
        index = int(child.get("index"))
        if child.SolidColor == None:
            warnings.warn(f"missing SolidColor for a tween in {xmlnode}")
            color = ("#000000", 0)
        else:
            color = (
                child.SolidColor.get("color", "#000000"),
                float(child.SolidColor.get("alpha", 1)),
            )
        result[index] = color
    return result


def interpolate_color_map(name, start, end, i, duration, ease):
    if not start:
        return None, set()

    start_map = get_color_map(start, name)
    end_map = get_color_map(end, name)

    if not start_map:
        return start, start_map.keys()

    interp_map = {}
    for key, scol in start_map.items():
        if key not in end_map:
            continue
        ecol = end_map[key]
        interp_map[key] = interpolate_color(scol, ecol, i, duration, ease)

    result = BeautifulSoup(str(start), "xml")
    for child in list(result.findChildren(name, recursive=False)):
        key = int(child.get("index"))
        if key not in interp_map:
            continue
        color, alpha = interp_map[key]
        child.SolidColor.set("color", color)
        if alpha != 1:
            child.SolidColor.set("alpha", alpha)

    return result, set(interp_map.keys())


def _segment_index(index):
    if index == None:
        return None
    return int(index) + 1


def _xfl_point(point):
    x, y = point
    return f"{round(x, 6)} {round(y, 6)}"


def _parse_number(num: str) -> float:
    """Parse an XFL edge format number."""
    if num[0] == "#":
        # Signed, 32-bit number in hex
        parts = num[1:].split(".")
        # Pad to 8 digits
        hex_num = "{:>06}{:<02}".format(*parts)
        num = int.from_bytes(bytes.fromhex(hex_num), "big", signed=True)
        return num
    else:
        # Account for hex un-scaling
        return float(num) * 256


# def _point_index(x):
# return int(x / 20)


def _get_start_point(shape):
    edges = shape.xmlnode.Edge.get("edges")
    tokens = iter(EDGE_TOKENIZER.findall(edges))

    moveTo = next(tokens)
    x = _parse_number(next(tokens))
    y = _parse_number(next(tokens))
    return (x, y)


class KDMap:
    def __init__(self):
        self.points = kd_tree.KDTree([], 2)
        self.items = {}

    def add(self, point, value):
        self.points.add_point(point)
        self.items.setdefault(point, []).append(value)

    def get(self, point):
        dist, pt = self.points.get_nearest(point, True)
        return self.items[pt]


def _get_edges_by_startpoint(shape):
    result = KDMap()

    for edge in shape.edges.findChildren("Edge", recursive=False):
        edge_str = str(edge)
        edge_list = edge.get("edges")
        if not edge_list:
            continue
        point_lists = edge_format_to_point_lists(edge_list)
        for pl in point_lists:
            for pt in pl:
                if type(pt) in (list, tuple):
                    continue
                x, y = [20 * float(x) for x in pt.split(" ")]
                result.add((x, y), edge_str)

    return result


def _parse_coord(coord):
    if not coord:
        return 0, 0
    x, y = coord.split(", ")
    return _parse_number(x), _parse_number(y)


def shape_interpolation(segment_xmlnodes, start, end, n_frames, ease):
    yield start.xmlnode

    for i in range(1, n_frames - 1):
        fills, fill_keys = interpolate_color_map(
            "FillStyle", start.xmlnode.fills, end.xmlnode.fills, i, n_frames, ease
        )
        fills = fills and fills.fills or ""

        strokes, stroke_keys = interpolate_color_map(
            "StrokeStyle", start.xmlnode.strokes, end.xmlnode.strokes, i, n_frames, ease
        )
        strokes = strokes and strokes.strokes or ""

        edges_by_startpoint = _get_edges_by_startpoint(start.xmlnode)

        edges = []
        for segment_xmlnode in segment_xmlnodes:
            fillStyle1 = _segment_index(segment_xmlnode.get("fillIndex1", None))
            # this one should always be None?
            fillStyle0 = None  # _segment_index(segment_xmlnode.get('fillIndex2', None))
            strokeStyle = _segment_index(segment_xmlnode.get("strokeIndex1", None))
            if strokeStyle not in stroke_keys:
                strokeStyle = None

            points = []
            startA = segment_xmlnode.get("startPointA", None)
            startB = segment_xmlnode.get("startPointB", None)
            if startA:
                startA = _parse_coord(startA)
            else:
                startA = _get_start_point(start)
            if startB:
                startB = _parse_coord(startB)
            else:
                startB = startB or _get_start_point(end)

            prev_point = interpolate_points(startA, startB, i, n_frames, ease)
            points.append(f"!{_xfl_point(prev_point)}")

            for curve in segment_xmlnode.findChildren("MorphCurves", recursive=False):
                anchA = _parse_coord(curve.get("anchorPointA"))
                anchB = _parse_coord(curve.get("anchorPointB"))

                if curve.get("isLine", None):
                    lineTo = interpolate_points(anchA, anchB, i, n_frames, ease)
                    points.append(f"|{_xfl_point(lineTo)}")
                else:
                    ctrlA = _parse_coord(curve.get("controlPointA"))
                    ctrlB = _parse_coord(curve.get("controlPointB"))
                    ctrl = interpolate_points(ctrlA, ctrlB, i, n_frames, ease)
                    quadTo = interpolate_points(anchA, anchB, i, n_frames, ease)
                    points.append(f"[{_xfl_point(ctrl)} {_xfl_point(quadTo)}")

            points = "".join(points)

            # edge_str = edges_by_startpoint[(_point_index(startA[0]), _point_index(startA[1]))]
            edge_str = edges_by_startpoint.get(startA)[0]
            clone = BeautifulSoup(edge_str, "xml").Edge
            clone["edges"] = points
            edges.append(str(clone))

            # fillStyle0 = fillStyle0 and f'fillStyle0="{fillStyle0}" ' or ""
            # fillStyle1 = fillStyle1 and f'fillStyle1="{fillStyle1}" ' or ""
            # strokeStyle = strokeStyle and f'strokeStyle="{strokeStyle}" ' or ""
            # edges.append(f"""<Edge {fillStyle0}{fillStyle1}{strokeStyle}edges="{points}"/>""")

        edges = "".join(edges)
        yield f"""<DOMShape>{fills}{strokes}<edges>{edges}</edges></DOMShape>"""

    yield end.xmlnode
