from heapq import merge
import math
import os

from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET

from .tweens import get_color_map
from .xflsvg import XflRenderer
from xfl2svg.shape.shape import xfl_domshape_to_svg


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
        self._frame_boxes = []
        self.boxes = []

        self.force_x = None
        self.force_y = None
        self.force_width = None
        self.force_height = None

    def render_shape(self, shape_snapshot, *args, **kwargs):
        if self.mask_depth == 0:
            svg = self.shape_cache.get(shape_snapshot.identifier, None)
            if not svg:
                domshape = ET.fromstring(shape_snapshot.domshape)
                svg = xfl_domshape_to_svg(domshape, False)
                self.shape_cache[shape_snapshot.identifier] = svg
        else:
            svg = self.mask_cache.get(shape_snapshot.identifier, None)
            if not svg:
                domshape = ET.fromstring(shape_snapshot.domshape)
                svg = xfl_domshape_to_svg(domshape, True)
                self.mask_cache[shape_snapshot.identifier] = svg

        fill_g, stroke_g, extra_defs, shape_box = svg
        self.boxes[-1] = merge_bounding_boxes(self.boxes[-1], shape_box)

        self.defs.update(extra_defs)
        id = f"Shape{shape_snapshot.identifier}"

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
        self.boxes.append(None)

    def pop_transform(self, transformed_snapshot, *args, **kwargs):
        transform_data = {}
        if (
            transformed_snapshot.matrix
            and transformed_snapshot.matrix != _IDENTITY_MATRIX
        ):
            matrix = " ".join([str(x) for x in transformed_snapshot.matrix])
            transform_data["transform"] = f"matrix({matrix})"
            a, b, c, d, tx, ty = [float(x) for x in transformed_snapshot.matrix]
            prev_box = self.boxes.pop()
            if prev_box != None:
                coordinates = [
                    prev_box[0]*a + prev_box[1]*b + tx,
                    prev_box[0]*c + prev_box[1]*d + ty,
                    prev_box[2]*a + prev_box[3]*b + tx,
                    prev_box[2]*c + prev_box[3]*d + ty,
                ]

                self.boxes[-1] = merge_bounding_boxes(self.boxes[-1], [
                    min(coordinates[0], coordinates[2]),
                    min(coordinates[1], coordinates[3]),
                    max(coordinates[0], coordinates[2]),
                    max(coordinates[1], coordinates[3])
                ])


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

        frame_box = None
        for box in self.boxes:
            frame_box = merge_bounding_boxes(frame_box, box)
        self._frame_boxes.append(frame_box)
        self.boxes = []
        self.defs = {}
        self.context = [
            [],
        ]

        if self.force_x != None:
            return

    def set_camera(self, x, y, width, height):
        self.force_x = x
        self.force_y = y
        self.force_width = width
        self.force_height = height

    def compile(
        self, output_filename=None, scale=1, padding=0, suffix=True, *args, **kwargs
    ):
        result = []
        box = self._frame_boxes[0] or [0, 0, 0, 0]
        for next_box in self._frame_boxes[1:]:
            box = merge_bounding_boxes(box, next_box)

        x = _conditional(self.force_x, box[0]) - padding / scale
        y = _conditional(self.force_y, box[1]) - padding / scale
        width = _conditional(self.force_width, box[2] - box[0]) + 2 * padding / scale
        height = _conditional(self.force_height, box[3] - box[1]) + 2 * padding / scale

        for i, data in enumerate(self._captured_frames):
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
