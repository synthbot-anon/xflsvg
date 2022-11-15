import base64
from collections import defaultdict
import json
import math
import os
import re
import shutil

from lxml import etree
from xfl2svg.shape.shape import json_normalize_xfl_domshape

from .svgrenderer import SvgRenderer
from .util import splitext, create_filename, digest
from .xflsvg import XflRenderer, Asset
from .svgrenderer import shape_frame_to_svg
from .filter import AssetFilter


_xml_parser = etree.XMLParser(remove_blank_text=True)


class SampleRenderer(XflRenderer):
    def __init__(self, render_shapes=False, filter=None) -> None:
        super().__init__()
        self.render_shapes = render_shapes
        self.filter = filter
        self._asset_frames = defaultdict(list)
        self._shape_frames = {}
        self.mask_depth = 0
        self.frame_filters = {}

    def render_shape(self, shape_frame, *args, **kwargs):
        if not self.render_shapes:
            return

        if shape_frame.identifier in self._shape_frames:
            return

        if shape_frame.ext == ".trace":
            shape_data = shape_frame.shape_data
        elif shape_frame.ext == ".domshape":
            domshape = etree.fromstring(shape_frame.shape_data)
            shape_data = json_normalize_xfl_domshape(
                domshape, shape_frame.document_dims, self.mask_depth > 0
            )

        if self.mask_depth == 0:
            id = digest(json.dumps(shape_data, sort_keys=True))
            self._shape_frames[id] = shape_frame

    def push_mask(self, masked_snapshot, *args, **kwargs):
        self.mask_depth += 1

    def pop_mask(self, masked_snapshot, *args, **kwargs):
        self.mask_depth -= 1

    def push_transform(self, transformed_frame, *args, **kwargs):
        if transformed_frame.element_type != "asset":
            return

        self.frame_filters[transformed_frame.identifier] = AssetFilter(self.filter)

    def pop_transform(self, transformed_frame, *args, **kwargs):
        # return super().pop_transform(transformed_frame, *args, **kwargs)
        if transformed_frame.element_type != "asset":
            return

        self._asset_frames[transformed_frame.element_id].append(transformed_frame)

    def set_camera(self, x, y, width, height):
        self.force_x = x
        self.force_y = y
        self.force_width = width
        self.force_height = height

    def compile(self, output_filename=None, reader=None, *args, **kwargs):
        renderer = SvgRenderer()

        for asset_id, asset_frames in self._asset_frames.items():
            idx = math.floor(len(asset_frames) / 2)
            selected_frame = asset_frames[idx]
            filter = self.frame_filters[selected_frame.identifier]

            filter.frame_empty = True

            with filter.forked_render_context(renderer):
                selected_frame.render()

            if filter.frame_empty:
                continue

            source = reader.id
            filename = create_filename(source, asset_id, None, idx)

            destination = os.path.join(output_filename, f"{filename}.svg")
            renderer.compile(destination, suffix=False, *args, **kwargs)

        if self.render_shapes:
            for shape_id, shape_frame in self._shape_frames.items():
                with renderer:
                    shape_frame.render()
                filename = create_filename(None, None, shape_id, None)
                destination = os.path.join(output_filename, f"{filename}.svg")
                renderer.compile(destination, suffix=False, *args, **kwargs)
