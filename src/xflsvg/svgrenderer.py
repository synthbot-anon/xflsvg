from heapq import merge
import math
import os

from bs4 import BeautifulSoup
import numpy
import xml.etree.ElementTree as ET

from .xflsvg import XflRenderer
from xfl2svg.shape.shape import xfl_domshape_to_svg, dict_shape_to_svg


_EMPTY_SVG = '<svg height="1px" width="1px" viewBox="0 0 1 1" />'
_IDENTITY_MATRIX = [1, 0, 0, 1, 0, 0]


def color_to_svg_filter(color):
    # fmt: off
    matrix = (
        "{0} 0 0 0 {4} "
        "0 {1} 0 0 {5} "
        "0 0 {2} 0 {6} "
        "0 0 0 {3} {7}"
    ).format(
        color.mr, color.mg, color.mb, color.ma,
        color.dr, color.dg, color.db, color.da,
    )
    # fmt: on

    filter = ET.Element(
        "filter",
        {
            "id": color.id,
            "x": "-20%",
            "y": "-20%",
            "width": "140%",
            "height": "140%",
            "color-interpolation-filters": "sRGB",
        },
    )

    ET.SubElement(
        filter,
        "feColorMatrix",
        {
            "in": "SourceGraphic",
            "type": "matrix",
            "values": matrix,
        },
    )

    return filter


def merge_bounding_boxes(original, addition):
    if addition == None:
        return original

    if original == None:
        return addition

    return (
        min(original[0], addition[0]),
        min(original[1], addition[1]),
        max(original[2], addition[2]),
        max(original[3], addition[3]),
    )


def expand_bounding_box(original, pt):
    if original == None:
        return (*pt, *pt)

    return (
        min(original[0], pt[0]),
        min(original[1], pt[1]),
        max(original[2], pt[0]),
        max(original[3], pt[1]),
    )


def shape_frame_to_svg(shape_frame, mask):
    if shape_frame.ext == ".domshape":
        domshape = ET.fromstring(shape_frame.shape_data)
        return xfl_domshape_to_svg(domshape, mask)
    elif shape_frame.ext == ".trace":
        return dict_shape_to_svg(shape_frame.shape_data)
    else:
        raise Exception("unknown shape type:", shape_frame.ext)


class SvgRenderer(XflRenderer):
    HREF = ET.QName("http://www.w3.org/1999/xlink", "href")

    def __init__(self) -> None:
        super().__init__()
        self.defs = {}
        self.context = [
            [],
        ]

        self.mask_depth = 0
        self.shape_cache = {}
        self.mask_cache = {}

        self._captured_frames = []
        self.bounding_points = [[]]
        self.box = None
        self.shape_counts = [0]

        self.force_x = None
        self.force_y = None
        self.force_width = None
        self.force_height = None

    def render_shape(self, shape_snapshot, *args, **kwargs):
        if self.mask_depth == 0:
            cache = self.shape_cache
            id = f"MShape{shape_snapshot.identifier}"
        else:
            cache = self.mask_cache
            id = f"Shape{shape_snapshot.identifier}"

        svg = cache.get(shape_snapshot.identifier, None)
        if not svg:
            svg = shape_frame_to_svg(shape_snapshot, self.mask_depth != 0)
            cache[shape_snapshot.identifier] = svg

        fill_g, stroke_g, extra_defs, shape_box = svg

        if self.mask_depth == 0 and shape_box:
            self.bounding_points[-1].extend(
                [
                    (shape_box[0], shape_box[1]),
                    (shape_box[0], shape_box[3]),
                    (shape_box[2], shape_box[1]),
                    (shape_box[2], shape_box[3]),
                ]
            )
            self.shape_counts[-1] += 1

        self.defs.update(extra_defs)

        if fill_g is not None:
            fill_id = f"{id}_FILL"
            fill_g.set("id", fill_id)
            self.defs[fill_id] = fill_g

            fill_use = ET.Element("use", {SvgRenderer.HREF: "#" + fill_id})
            self.context[-1].append(fill_use)

        if stroke_g is not None:
            stroke_id = f"{id}_STROKE"
            stroke_g.set("id", stroke_id)
            self.defs[stroke_id] = stroke_g

            self.context[-1].append(
                ET.Element("use", {SvgRenderer.HREF: "#" + stroke_id})
            )

    def push_transform(self, transformed_snapshot, *args, **kwargs):
        self.context.append([])
        self.bounding_points.append([])

    def pop_transform(self, transformed_snapshot, *args, **kwargs):
        transform_data = {}
        prev_bounds = self.bounding_points.pop()
        new_bounds = prev_bounds
        if (
            transformed_snapshot.matrix
            and transformed_snapshot.matrix != _IDENTITY_MATRIX
        ):
            matrix = " ".join([str(x) for x in transformed_snapshot.matrix])
            transform_data["transform"] = f"matrix({matrix})"

            if prev_bounds:
                a, b, c, d, tx, ty = [float(x) for x in transformed_snapshot.matrix]
                prev_bounds = numpy.array(prev_bounds)
                mat = numpy.array([[a, b], [c, d]])
                new_bounds = prev_bounds @ mat + [tx, ty]

        self.bounding_points[-1].extend(new_bounds)

        if self.mask_depth == 0:
            color = transformed_snapshot.color
            if color and not color.is_identity():
                filter_element = color_to_svg_filter(transformed_snapshot.color)
                self.defs[color.id] = filter_element
                transform_data["filter"] = f"url(#{color.id})"

        if transform_data != {}:
            transform_element = ET.Element("g", transform_data)
            transform_element.extend(self.context.pop())
            self.context[-1].append(transform_element)
        else:
            items = self.context.pop()
            self.context[-1].extend(items)

    def push_mask(self, masked_snapshot, *args, **kwargs):
        self.mask_depth += 1
        self.context.append([])

    def pop_mask(self, masked_snapshot, *args, **kwargs):
        mask_id = f"Mask_{masked_snapshot.identifier}"
        mask_element = ET.Element("mask", {"id": mask_id})
        mask_element.extend(self.context.pop())

        self.defs[mask_id] = mask_element
        self.context[-1].append(mask_element)

        masked_items = ET.Element("g", {"mask": f"url(#{mask_id})"})
        self.context[-1].append(masked_items)
        self.mask_depth -= 1

    def push_masked_render(self, masked_snapshot, *args, **kwargs):
        self.context.append([])

    def pop_masked_render(self, masked_snapshot, *args, **kwargs):
        children = self.context.pop()
        masked_items = self.context[-1][-1]
        masked_items.extend(children)

    def on_frame_rendered(self, frame, *args, **kwargs):
        if len(self.context) != 1:
            return

        self._captured_frames.append([self.defs, self.context])

        for points in self.bounding_points:
            for point in points:
                self.box = expand_bounding_box(self.box, point)
        self.bounding_points = [[]]
        self.defs = {}
        self.context = [
            [],
        ]
        self.shape_counts.append(0)

        if self.force_x != None:
            return

    def set_camera(self, x, y, width, height):
        self.force_x = x
        self.force_y = y
        self.force_width = width
        self.force_height = height

    def get_svg_box(self, scale, padding):
        box = self.box or [0, 0, 0, 0]
        x = _conditional(self.force_x, box[0]) - padding / scale
        y = _conditional(self.force_y, box[1]) - padding / scale
        width = _conditional(self.force_width, box[2] - box[0]) + 2 * padding / scale
        height = _conditional(self.force_height, box[3] - box[1]) + 2 * padding / scale
        return x, y, width, height

    def get_frame_dimensions(self, **kwargs):
        scale = kwargs.get("scale", 1)
        padding = kwargs.get("padding", 0)
        x, y, width, height = self.get_svg_box(scale, padding)
        return width * scale, height * scale

    def compile(
        self,
        output_filename=None,
        scale=1,
        padding=0,
        suffix=True,
        skip_leading_blanks=False,
        *args,
        **kwargs,
    ):
        result = []
        x, y, width, height = self.get_svg_box(scale, padding)
        found_nonempty_frame = False

        for i, data in enumerate(self._captured_frames):
            if self.shape_counts[i] != 0:
                found_nonempty_frame = True

            if skip_leading_blanks and not found_nonempty_frame:
                continue

            defs, context = data
            svg = ET.Element(
                "svg",
                {
                    "xmlns": "http://www.w3.org/2000/svg",
                    "version": "1.1",
                    "preserveAspectRatio": "none",
                    "x": f"{x*scale}px",
                    "y": f"{y*scale}px",
                    "width": f"{width*scale}px",
                    "height": f"{height*scale}px",
                    "viewBox": f"{x} {y} {width} {height}",
                },
            )

            defs_element = ET.SubElement(svg, "defs")
            defs_element.extend(defs.values())
            svg.extend(context[0])
            image = ET.ElementTree(svg)

            if output_filename:
                name, ext = splitext(output_filename)
                sfx = suffix and "%04d" % i or ""
                with open(f"{name}{sfx}{ext}", "w") as outp:
                    image.write(outp, encoding="unicode")

            result.append(image)

        self._captured_frames = []
        self.bounding_points = [[]]
        self.box = None

        return result


def _conditional(forced_value, calculated_value):
    if forced_value != None:
        return forced_value
    return calculated_value


def _expand_box(orig, addition):
    if addition == None:
        return orig
    if orig == None:
        return addition

    orig[0] = min(orig[0], addition[0])
    orig[1] = min(orig[1], addition[1])
    orig[2] = max(orig[2], addition[2])
    orig[3] = max(orig[3], addition[3])
    return orig


def splitext(path):
    folder, filename = os.path.split(path)
    if "." in filename:
        name, ext = filename.rsplit(".", maxsplit=1)
        return os.path.join(folder, name), f".{ext}"
    return path, ""


def split_colors(color):
    if not color:
        return 0, 0, 0
    if not color.startswith("#"):
        raise Exception(f"invalid color: {color}")

    assert len(color) in (4, 5, 7, 9)
    if len(color) <= 5:
        r = int(color[1], 16)
        g = int(color[2], 16)
        b = int(color[3], 16)
        if len(color) == 5:
            a = int(color[4], 16)
        else:
            a = 15
        return r * 16 + a, g * 16 + g, b * 16 + b, a * 16 + a
    elif len(color) >= 7:
        r = int(color[1:3], 16)
        g = int(color[3:5], 16)
        b = int(color[5:7], 16)
        if len(color) == 9:
            a = int(color[7:9], 16)
        else:
            a = 255
        return r, g, b, a

    assert False, f"invalid color spec: {color}"
