from collections import defaultdict
from contextlib import contextmanager
import json
import os
from urllib.parse import urlparse
import xml.etree.ElementTree as ET

from xfl2svg.shape.shape import json_normalize_xfl_domshape, dict_shape_to_svg
import zstandard

from .util import ColorObject
from .xflsvg import DOMShape, Frame, MaskedFrame, XflRenderer, consume_frame_identifier
from .xflsvg import ShapeFrame


_IDENTITY_MATRIX = [1, 0, 0, 1, 0, 0]


def color_to_filter(color):
    return {
        "multiply": [color.mr, color.mg, color.mb, color.ma],
        "shift": [color.dr, color.dg, color.db, color.da],
    }


def shape_frame_to_dict(shape_frame, mask):
    if shape_frame.ext == ".domshape":
        domshape = ET.fromstring(shape_frame.shape_data)
        return json_normalize_xfl_domshape(domshape, shape_frame.document_dims, mask)
    elif shape_frame.ext == ".trace":
        return shape_frame.shape_data
    else:
        raise Exception("unknown shape type:", shape_frame.ext)


class RenderTracer(XflRenderer):
    def __init__(self, compression=None, compression_level=None):
        super().__init__()
        self.mask_depth = 0
        self.shapes = {}
        self.context = [[]]
        self.frames = {}
        self._captured_frames = []
        self.labels = []
        self._recorded_frames = set()

        if compression:
            if compression == "zstd":
                self.compress = zstandard.ZstdCompressor(
                    level=compression_level or 6
                ).compress
        else:
            self.compress = lambda x: x

    def add_label(self, label):
        self.labels.append(label)

    def set_camera(self, x, y, width, height):
        pass

    def on_frame_rendered(self, frame, *args, **kwargs):
        if frame.data and not frame.identifier in self._recorded_frames:
            self.labels.extend(frame.data)
            self._recorded_frames.add(frame.identifier)

        if len(self.context) != 1:
            return

        children = self.context[0]

        if len(children) > 1:
            frame_data = {"children": children}
            render_index = consume_frame_identifier()
            self.frames[render_index] = frame_data
        else:
            render_index = children[0]

        self._captured_frames.append(render_index)
        self.context = [[]]

    def render_shape(self, shape_snapshot, *args, **kwargs):
        if shape_snapshot.identifier not in self.shapes:
            shape = shape_frame_to_dict(shape_snapshot, self.mask_depth > 0)
            self.shapes[shape_snapshot.identifier] = shape

        self.context[-1].append(shape_snapshot.identifier)

    def push_transform(self, transformed_snapshot, *args, **kwargs):
        self.context.append([])

    def pop_transform(self, transformed_snapshot, *args, **kwargs):
        frame_data = {}
        if self.mask_depth == 0:
            color = transformed_snapshot.color
            if color and not color.is_identity():
                frame_data["filter"] = color_to_filter(transformed_snapshot.color)

        if (
            transformed_snapshot.matrix
            and transformed_snapshot.matrix != _IDENTITY_MATRIX
        ):
            matrix = [float(x) for x in transformed_snapshot.matrix]
            frame_data["transform"] = matrix

        frame_data["children"] = self.context.pop()
        self.frames[transformed_snapshot.identifier] = frame_data
        self.context[-1].append(transformed_snapshot.identifier)

    def push_mask(self, masked_snapshot, *args, **kwargs):
        self.mask_depth += 1
        self.context.append([])

    def pop_mask(self, masked_snapshot, *args, **kwargs):
        mask_data = {"children": self.context.pop()}
        self.frames[masked_snapshot.identifier] = mask_data
        self.mask_depth -= 1

    def push_masked_render(self, masked_snapshot, *args, **kwargs):
        self.context.append([])

    def pop_masked_render(self, masked_snapshot, *args, **kwargs):
        frame_data = {
            "mask": masked_snapshot.identifier,
            "children": self.context.pop(),
        }

        render_index = consume_frame_identifier()
        self.frames[render_index] = frame_data
        self.context[-1].append(render_index)

    def set_box(*args, **kwargs):
        pass

    def compile(self, output_file=None, *args, **kwargs):
        with open(output_file, "wb") as outp:
            data = self.compress(
                json.dumps(
                    {
                        "shapes": self.shapes,
                        "frames": self.frames,
                        "labels": self.labels,
                    },
                    indent=2,
                ).encode("utf8")
            )
            outp.write(data)

        return self.shapes, self.frames, self.labels


class RenderTraceReader:
    def __init__(self, input_path, compression=None):
        if compression:
            if compression == "zstd":
                self.decompress = zstandard.ZstdDecompressor().decompress
            else:
                raise Exception("unknown compression algorithm:", compression)
        else:
            self.decompress = lambda x: x

        with open(input_path, "rb") as inp:
            data = json.loads(self.decompress(inp.read()))

        self.shapes = data["shapes"]
        self.frames = data["frames"]

        self.frame_cache = {}
        self._reversed_frames = None
        self.frame_labels = {}
        self.seq_labels = []

        self.id = None
        self.source = None
        self.timelines = None
        self.framerate = None
        self.background = None
        self.camera = None

        self.element_info = {}

        for label in data["labels"]:
            if "frame.id" in label:
                frame_id = label["frame.id"]
                self.frame_labels.get(frame_id, []).append(label)

                if label["type"] == "element":
                    element_type = label.get("element_type", None)
                    element_id = label.get("element_id", None)
                    self.element_info[frame_id] = (element_type, element_id)

            elif "frame.id[]" in label:
                self.seq_labels.append(label)

                if label["type"] == "clip":
                    self.sequence = label["frame.id[]"]
                    self.id = self.source = label.get("source", None)
                    self.framerate = label.get("framerate", None)
                    self.background = label.get("background", None)
                    self.camera = label.get("camera", None)

    def get_camera(self):
        return self.camera

    def get_background(self):
        return self.background

    def get_timeline(self, id=None):
        for frame_id in self.sequence:
            r = self.get_table_frame(frame_id)
            yield r

    def get_table_frame(self, render_index):
        render_index_str = str(render_index)

        if render_index_str in self.shapes:
            shape = self.shapes[render_index_str]
            shape = ShapeFrame(shape, ".trace")
            shape.identifier = render_index
            shape.data = self.frame_labels.get(render_index, [])
            self.frame_cache[render_index] = shape
            return shape
        else:
            frame_data = self.frames[render_index_str]
            children = [self.get_table_frame(x) for x in frame_data["children"]]

            if "mask" in frame_data:
                mask = self.get_table_frame(frame_data["mask"])
                frame = MaskedFrame(mask, children)
                frame.identifier = render_index
                frame.data = self.frame_labels.get(render_index, [])
                self.frame_cache[render_index] = frame
                return frame

            transform = frame_data.get("transform", None)
            filter = frame_data.get("filter", None)
            if filter:
                filter = ColorObject(*filter["multiply"], *filter["shift"])

            element_type, element_id = self.element_info.get(render_index, (None, None))

            frame = Frame(
                transform,
                filter,
                children,
                element_type=element_type,
                element_id=element_id,
            )
            frame.identifier = render_index
            frame.data = self.frame_labels.get(render_index, [])
            self.frame_cache[render_index] = frame
            return frame
