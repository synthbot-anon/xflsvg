from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
import dataclasses
from logging import warning
import math
import warnings
import xml.etree.ElementTree as etree

from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET
import kd_tree
import numpy
from xfl2svg.shape.edge import EDGE_TOKENIZER, edge_format_to_point_lists
from xfl2svg.shape.style import LinearGradient, RadialGradient

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


def _adjust_adobe_matrix_params(rotation, srot, erot, sshear, eshear):
    if rotation > 0:
        if erot < srot:
            erot += 2 * math.p
        erot += rotation * 2 * math.pi
    elif rotation < 0:
        if erot > srot:
            erot -= 2 * math.pi
        erot += rotation * 2 * math.pi
    elif abs(erot - srot) > math.pi:
        srot += (erot - srot) / abs(erot - srot) * 2 * math.pi
    if abs(eshear - sshear) > math.pi:
        sshear += (eshear - sshear) / abs(eshear - sshear) * 2 * math.pi

    return srot, erot, sshear


def simple_matrix_interpolation(start, end, t):
    start_linear, start_translation = deserialize_matrix(start)
    end_linear, end_translation = deserialize_matrix(end)

    srot, sshear, sx, sy = adobe_decomposition(start_linear)
    erot, eshear, ex, ey = adobe_decomposition(end_linear)
    srot, erot, sshear = _adjust_adobe_matrix_params(0, srot, erot, sshear, eshear)

    interpolated_linear = adobe_matrix(
        t * (erot) + (1 - t) * srot,
        t * eshear + (1 - t) * sshear,
        t * ex + (1 - t) * sx,
        t * ey + (1 - t) * sy,
    )
    interpolated_translation = t * end_translation + (1 - t) * start_translation

    return serialize_matrix(interpolated_linear, interpolated_translation)


def matrix_interpolation(start, end, n_frames, rotation, ease):
    start_linear, start_translation = deserialize_matrix(start)
    end_linear, end_translation = deserialize_matrix(end)

    srot, sshear, sx, sy = adobe_decomposition(start_linear)
    erot, eshear, ex, ey = adobe_decomposition(end_linear)
    srot, erot, sshear = _adjust_adobe_matrix_params(
        rotation, srot, erot, sshear, eshear
    )

    for i in range(n_frames):
        frot = ease["rotation"](i / (n_frames - 1)).y
        fscale = ease["scale"](i / (n_frames - 1)).y
        fpos = ease["position"](i / (n_frames - 1)).y

        interpolated_linear = adobe_matrix(
            frot * (erot) + (1 - frot) * srot,
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


@dataclass(frozen=True)
class SolidColor:
    color: str
    alpha: float

    def to_xfl(self):
        return f"""<SolidColor color="{self.color}" alpha="{self.alpha}" />"""


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


def interpolate_value(x, y, frac):
    return (1 - frac) * x + frac * y


def interpolate_color(colx, ax, coly, ay, t):
    rx, gx, bx = split_colors(colx)
    ry, gy, by = split_colors(coly)
    ai = interpolate_value(ax, ay, t)

    if ai == 0:
        return "#FFFFFF", 0

    ri = round(interpolate_value(rx * ax, ry * ay, t) / ai)
    gi = round(interpolate_value(gx * ax, gy * ay, t) / ai)
    bi = round(interpolate_value(bx * ax, by * ay, t) / ai)

    return "#%02X%02X%02X" % (ri, gi, bi), ai


def calculate_stop_paths(init, fin):
    # Goal: map all start point to their nearest end point and all end points to their
    # nearest start points, then return the mappings as the target path.

    available_starts = kd_tree.KDTree([(x[0],) for x in init], 1)
    available_ends = kd_tree.KDTree([(x[0],) for x in fin], 1)

    init_map = dict((x[0], x) for x in init)
    fin_map = dict((x[0], x) for x in fin)

    forward_map = defaultdict(list)
    cover_count = defaultdict(lambda: 0)

    # Map each start point to its nearest ending point
    for stop in init:
        ratio = stop[0]
        match = available_ends.get_nearest((ratio,), False)[0]

        # add a path ratio -> match
        forward_map[ratio].append(match)
        cover_count[match] += 1

    # Map each unused end point to its nearest starting point
    for stop in fin:
        ratio = stop[0]
        if cover_count[ratio] > 0:
            continue
        match = available_starts.get_nearest((ratio,), False)[0]

        # If this point is covering another redundantly, prefer to remap it
        # rather than double-mapping it
        if forward_map[match]:
            potential_redundancy = forward_map[match][0]
            if cover_count[potential_redundancy] > 1:
                forward_map[match].remove(potential_redundancy)
                cover_count[potential_redundancy] -= 1

        forward_map[match].append(ratio)

    for start in sorted(forward_map):
        for end in forward_map[start]:
            yield init_map[start], fin_map[end]


def interpolate_stops(start, end, t):
    ratio = t * end[0] + (1 - t) * start[0]
    color, alpha = interpolate_color(start[1], start[2], end[1], end[2], t)
    return (ratio, color, alpha)


def interpolate_radial_gradients(x, y, t):
    new_matrix = simple_matrix_interpolation(x.matrix, y.matrix, t)
    new_radius = (1 - t) * x.radius + t * y.radius
    new_focal_point = (1 - t) * x.focal_point + t * y.focal_point
    new_stops = (
        interpolate_stops(a, b, t) for a, b in calculate_stop_paths(x.stops, y.stops)
    )
    return RadialGradient(
        new_matrix, new_radius, new_focal_point, tuple(new_stops), x.spread_method
    )


def interpolate_linear_gradients(x, y, t):
    xvec = (x.end[0] - x.start[0], x.end[1] - x.start[1])
    yvec = (y.end[0] - y.start[0], y.end[1] - y.start[1])

    xrot = math.atan2(xvec[1], xvec[0])
    yrot = math.atan2(yvec[1], yvec[0])
    xdist = math.sqrt(xvec[0] ** 2 + xvec[1] ** 2)
    ydist = math.sqrt(yvec[0] ** 2 + yvec[1] ** 2)
    xmid = (x.start[0] + xvec[0] / 2, x.start[1] + xvec[1] / 2)
    ymid = (y.start[0] + yvec[0] / 2, y.start[1] + yvec[1] / 2)

    rot = (1 - t) * xrot + t * yrot
    mid0 = (1 - t) * xmid[0] + t * ymid[0]
    mid1 = (1 - t) * xmid[1] + t * ymid[1]
    dist = (1 - t) * xdist + t * ydist

    new_start = (-math.cos(rot) * dist / 2 + mid0, -math.sin(rot) * dist / 2 + mid1)
    new_end = (math.cos(rot) * dist / 2 + mid0, math.sin(rot) * dist / 2 + mid1)
    new_stops = (
        interpolate_stops(a, b, t) for a, b in calculate_stop_paths(x.stops, y.stops)
    )
    return LinearGradient(new_start, new_end, tuple(new_stops), x.spread_method)


def interpolate_solid_colors(x, y, t):
    new_color, new_alpha = interpolate_color(x.color, x.alpha, y.color, y.alpha, t)
    return SolidColor(new_color, new_alpha)


def interpolate_solid_with_gradient(solid, gradient, t):
    new_stops = []
    for ratio, scol, salpha in gradient.stops:
        new_color, new_alpha = interpolate_color(
            scol, salpha, solid.color, solid.alpha, t
        )
        new_stops.append((ratio, new_color, new_alpha))

    new_stops = tuple(new_stops)
    return dataclasses.replace(gradient, stops=new_stops)


def get_fill_def(xmlnode):
    if xmlnode.SolidColor:
        return SolidColor(
            xmlnode.SolidColor.get("color", "#000000"),
            float(xmlnode.SolidColor.get("alpha", 1)),
        )
    elif xmlnode.LinearGradient:
        return LinearGradient.from_xfl(ET.fromstring(str(xmlnode.LinearGradient)))
    elif xmlnode.RadialGradient:
        return RadialGradient.from_xfl(ET.fromstring(str(xmlnode.RadialGradient)))

    return None


def interpolate_fill_styles(start, end, t):
    if end == None:
        return start

    if isinstance(start, SolidColor):
        if isinstance(end, SolidColor):
            return interpolate_solid_colors(start, end, t)
        else:
            return interpolate_solid_with_gradient(start, end, t)
    elif isinstance(start, LinearGradient):
        if isinstance(end, LinearGradient):
            return interpolate_linear_gradients(start, end, t)
        elif isinstance(end, SolidColor):
            return interpolate_solid_with_gradient(end, start, 1 - t)
        else:
            assert False, f"cannot interpolate LinearGradient with {end}"
    elif isinstance(start, RadialGradient):
        if isinstance(end, RadialGradient):
            return interpolate_radial_gradients(start, end, t)
        elif isinstance(end, SolidColor):
            return interpolate_solid_with_gradient(end, start, 1 - t)
        else:
            assert False, f"cannot interpolate RadialGradient with {end}"

    assert False, f"unknown fill style: {start}"


def replace_fill(element, start, replacement):
    new_fill = next(BeautifulSoup(replacement.to_xfl(), "xml").children)

    if isinstance(start, SolidColor):
        element.SolidColor.replace_with(new_fill)
        return
    elif isinstance(start, LinearGradient):
        element.LinearGradient.replace_with(new_fill)
        return
    elif isinstance(start, RadialGradient):
        element.RadialGradient.replace_with(new_fill)
        return

    raise Exception(f"Unknown fill type: {start}")


def interpolate_color_maps(start_shape, end_shape, i, duration, ease):
    t = ease["color"](i / (duration - 1)).y
    new_strokes = ""
    new_fills = ""

    if start_shape.strokes:
        new_strokes = BeautifulSoup(str(start_shape.strokes), "xml")

        if end_shape.strokes:
            end_fills = defaultdict(lambda: None)
            for stroke_style in end_shape.strokes.findChildren("StrokeStyle"):
                index = int(stroke_style.get("index"))
                # TODO: tween stroke weight and VariablePointWidth elements
                end_fills[index] = get_fill_def(stroke_style)

            for stroke_style in new_strokes.findChildren("StrokeStyle"):
                index = int(stroke_style.get("index"))
                start_fill = get_fill_def(stroke_style)
                interpolated = interpolate_fill_styles(start_fill, end_fills[index], t)
                replace_fill(stroke_style, start_fill, interpolated)

        new_strokes = str(next(new_strokes.children))

    if start_shape.fills and end_shape.fills:
        new_fills = BeautifulSoup(str(start_shape.fills), "xml")

        if end_shape.fills:
            end_fills = defaultdict(lambda: None)
            for fill_style in end_shape.fills.findChildren("FillStyle"):
                index = int(fill_style.get("index"))
                end_fills[index] = get_fill_def(fill_style)

            for fill_style in new_fills.findChildren("FillStyle"):
                index = int(fill_style.get("index"))
                start_fill = get_fill_def(fill_style)
                interpolated = interpolate_fill_styles(start_fill, end_fills[index], t)
                replace_fill(fill_style, start_fill, interpolated)

            new_fills = str(next(new_fills.children))

    return new_strokes, new_fills


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
        for pl in edge_format_to_point_lists(edge_list):
            for pt in pl:
                if type(pt[0]) in (list, tuple):
                    continue
                x, y = (20 * pt[0], 20 * pt[1])
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
        fills, strokes = interpolate_color_maps(
            start.xmlnode, end.xmlnode, i, n_frames, ease
        )

        edges_by_startpoint = _get_edges_by_startpoint(start.xmlnode)

        edges = []
        for segment_xmlnode in segment_xmlnodes:
            fillStyle1 = _segment_index(segment_xmlnode.get("fillIndex1", None))
            # this one should always be None?
            fillStyle0 = None
            strokeStyle = _segment_index(segment_xmlnode.get("strokeIndex1", None))

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

            edge_str = edges_by_startpoint.get(startA)[0]
            clone = BeautifulSoup(edge_str, "xml").Edge
            clone["edges"] = points
            edges.append(str(clone))

        edges = "".join(edges)
        yield f"""<DOMShape>{fills}{strokes}<edges>{edges}</edges></DOMShape>"""

    yield end.xmlnode
