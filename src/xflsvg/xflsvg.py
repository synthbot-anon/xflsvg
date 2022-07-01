"""
This class is for parsing and rendering XFL files.

Example usage:
    
    xfl = XflReader('/path/to/file.xfl')
    timeline = xfl.get_timeline('Scene 1')
    for i, frame in enumerate(timeline):
        with SvgRenderer() as renderer:
            frame.render()
        svg = renderer.compile(xfl.width, xfl.height)
        with open(f'frame{i}.svg', 'w') as outfile:
            svg.write(outfile, encoding='unicode')

Overview of XFL:
    An XFL file consists of a Document file and Asset files. A Document contains one or
    more timelines. An Asset contains a single timeline.
    
    Each timeline contains one or more layers. Some layers are designated as mask
    layers, and some layers have parent mask layers. A mask layer, when rendered,
    defines which portion of child layers should get rendered. Layers are rendered
    in reverse order.

    A layer contains one or more element bundles. An element bundle is a collection of
    elements whose frames follow the same loop length. An element bundle contains one
    or more frames and one or more elements.

    An element can be a symbol, shape, or group. A symbol describes a transformation
    and how to loop over an asset to generate frames. A shape define a single
    primitive frame. Shapes are described in Adobe's proprietary format, and they are
    parsed in the domshape/ package provided by PluieElectrique. A group consists of a
    collection of other elements that share a transformation.

    A transformation can describe a linear transformation and a translation of pixel
    positions and colors.

Overview of rendering:
    The XFLReader class provide is a visitor interface for rendering. Renderers should
    subclass the XflRenderer class and implement the relevant methods. A complete
    renderer should implement EITHER:
        render_shape - Render an SVG shape
        push_transform, pop_transform - Start and end position and color transformations
        push_mask, pop_mask - Start and end mask definitions
        push_masked_render, pop_masked_render - Start/end renders with the last mask

    OR
        on_frame_rendered - Should handle Frame, ShapeFrame, and MaskedFrame
    
    The first set of methods are better for actual rendering since they convert the XFL
    file into a sequence. The second method is better for transforming the data into a
    new tree-structured format.

"""


from contextlib import contextmanager
import copy
from glob import glob
import html
import json
from multiprocessing import context
import os
import re
import shutil
import threading
from typing import Sequence
import warnings
import xml.etree.ElementTree as ET

from bs4 import BeautifulSoup
from xfl2svg.shape.edge import edge_format_to_point_lists
from xfl2svg.shape.style import parse_stroke_style

from . import easing
from .util import ColorObject
from .tweens import matrix_interpolation, color_interpolation, shape_interpolation


_frame_index = 0


def consume_frame_identifier():
    global _frame_index
    result = _frame_index
    _frame_index += 1
    return result


def _get_box(domshape):
    domshape = ET.fromstring(domshape)
    box = None

    stroke_widths = {}
    for style in domshape.iterfind(".//{*}StrokeStyle"):
        stroke_style = style[0]
        index = style.get("index")
        width = float(stroke_style.get("weight") or 1)
        stroke_widths[index] = width

    for edge in domshape.find(".//{*}edges").iterfind(".//{*}Edge[@edges]"):
        width = 0
        stroke_index = edge.get("strokeStyle")
        if stroke_index:
            width = stroke_widths.get(stroke_index, 0)

        edge_format = edge.get("edges")
        for point_list in edge_format_to_point_lists(edge_format):
            for point in point_list:
                if type(point) == tuple:
                    point = point[0]
                x, y = [float(x) for x in point.split()]
                box = _expand_box(box, [x - width, y - width, x + width, y + width])

    return box


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


class Frame:
    def __init__(self, matrix=None, color=None, children=None):
        global _frame_index
        self.identifier = _frame_index
        _frame_index += 1

        self.matrix = matrix
        self.color = color
        self.children = children or []
        self.data = {}
        self._box = None
        self.parent = None

    def add_child(self, child_frame):
        self.children.append(child_frame)
        child_frame.parent_frame = self

    @property
    def box(self):
        if self._box:
            return self._box

        if not self.children:
            return None

        for child in self.children:
            self._box = _expand_box(self._box, child.box)

        if not self.matrix or not self._box:
            return self._box

        x1 = (
            self.matrix[0] * self._box[0]
            + self.matrix[1] * self._box[1]
            + self.matrix[4]
        )
        x2 = (
            self.matrix[0] * self._box[2]
            + self.matrix[1] * self._box[3]
            + self.matrix[4]
        )
        y1 = (
            self.matrix[2] * self._box[0]
            + self.matrix[3] * self._box[1]
            + self.matrix[5]
        )
        y2 = (
            self.matrix[2] * self._box[2]
            + self.matrix[3] * self._box[3]
            + self.matrix[5]
        )
        self._box = [min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)]

        return self._box

    def prepend_child(self, child_frame):
        self.children.insert(0, child_frame)
        child_frame.parent_frame = self

    def render(self, *args, **kwargs):
        renderer = XflRenderer.current()
        renderer.push_transform(self, *args, **kwargs)

        for child in self.children:
            child.render(*args, **kwargs)

        renderer.pop_transform(self, *args, **kwargs)
        renderer.on_frame_rendered(self, *args, **kwargs)


class ShapeFrame(Frame):
    def __init__(self, domshape):
        super().__init__()
        self.domshape = domshape
        self._box = None

    def render(self, *args, **kwargs):
        renderer = XflRenderer.current()
        renderer.render_shape(self, *args, **kwargs)
        renderer.on_frame_rendered(self, *args, **kwargs)

    @property
    def box(self):
        if self._box:
            return self._box
        self._box = _get_box(self.domshape)
        return self._box


def _transformed_frame(original, matrix=None, color=None):
    result = Frame(matrix, color)
    result.add_child(original)
    return result


class MaskedFrame(Frame):
    def __init__(self, mask, children=None):
        super().__init__(children=children)
        self.mask = mask
        mask.parent_frame = self

    def render(self, *args, **kwargs):
        renderer = XflRenderer.current()

        renderer.push_mask(self, *args, **kwargs)
        self.mask.render()
        renderer.pop_mask(self, *args, **kwargs)

        renderer.push_masked_render(self, *args, **kwargs)
        for child in self.children:
            child.render()
        renderer.pop_masked_render(self, *args, **kwargs)

        renderer.on_frame_rendered(self, *args, **kwargs)


class AnimationObject(Sequence):
    def __init__(self):
        pass

    def __getitem__(self, k: int) -> Frame:
        pass

    def __len__(self) -> int:
        pass

    def __iter__(self):
        for i in range(len(self)):
            yield self[i]


def _get_matrix(xmlnode):
    outer = xmlnode.findChild("matrix", recursive=False)
    if outer == None:
        return None

    inner = outer.findChild("Matrix", recursive=False)
    if inner == None:
        return None

    result = [
        float(inner.get("a", default=1)),
        float(inner.get("b", default=0)),
        float(inner.get("c", default=0)),
        float(inner.get("d", default=1)),
        float(inner.get("tx", default=0)),
        float(inner.get("ty", default=0)),
    ]

    if result == [1, 0, 0, 1, 0, 0]:
        return None

    return result


def _get_color(xmlnode):
    if xmlnode.findChild("frameFilters", recursive=False):
        warnings.warn("Cannot handle frameFilters")

    outer = xmlnode.findChild("color", recursive=False) or xmlnode.findChild(
        "frameColor", recursive=False
    )
    if outer == None:
        return None

    inner = outer.findChild("Color", recursive=False)
    if inner == None:
        return None

    inner = inner.attrs
    result = None

    count = 0
    if "brightness" in inner:
        count += 1
        brightness = float(inner["brightness"])
        if brightness < 0:
            # linearly interpolate towards black
            result = ColorObject(
                mr=1 + brightness,
                mg=1 + brightness,
                mb=1 + brightness,
            )
        else:
            # linearly interpolate towards white
            result = ColorObject(
                mr=1 - brightness,
                mg=1 - brightness,
                mb=1 - brightness,
                dr=brightness,
                dg=brightness,
                db=brightness,
            )
    if "tintMultiplier" in inner or "tintColor" in inner:
        count += 1
        # color*(1-tint_multiplier) + tint_color*tint_multiplier
        tint_multiplier = float(inner.get("tintMultiplier", 0))
        tint_color = inner.get("tintColor", "#000000")

        result = ColorObject(
            mr=1 - tint_multiplier,
            mg=1 - tint_multiplier,
            mb=1 - tint_multiplier,
            dr=tint_multiplier * int(tint_color[1:3], 16) / 255,
            dg=tint_multiplier * int(tint_color[3:5], 16) / 255,
            db=tint_multiplier * int(tint_color[5:7], 16) / 255,
        )

    if set(inner.keys()) & {
        "redMultiplier",
        "greenMultiplier",
        "blueMultiplier",
        "alphaMultiplier",
        "redOffset",
        "greenOffset",
        "blueOffset",
        "alphaOffset",
    }:
        count += 1
        result = ColorObject(
            mr=float(inner.get("redMultiplier", 1)),
            mg=float(inner.get("greenMultiplier", 1)),
            mb=float(inner.get("blueMultiplier", 1)),
            ma=float(inner.get("alphaMultiplier", 1)),
            dr=float(inner.get("redOffset", 0)) / 255,
            dg=float(inner.get("greenOffset", 0)) / 255,
            db=float(inner.get("blueOffset", 0)) / 255,
            da=float(inner.get("alphaOffset", 0)) / 255,
        )

    assert count < 2
    return result


class Element(AnimationObject):
    def __init__(self, xmlnode):
        super().__init__()
        self.xmlnode = xmlnode
        self.matrix = _get_matrix(xmlnode)
        self.color = _get_color(xmlnode)

    def __getitem__(self, k: int) -> Frame:
        result = Frame()
        return result


class DOMSymbolInstance(Element):
    def __init__(self, xflsvg, asset, layer, duration, xmlnode):
        super().__init__(xmlnode)
        self.xflsvg = xflsvg
        self.asset = asset
        self.layer = layer
        self.loop_type = xmlnode.get("loop")
        self.target_asset = xflsvg.get_safe_asset(xmlnode.get("libraryItemName"))

        if self.target_asset:
            self.first_frame = (
                int(xmlnode.get("firstFrame", default=0))
                % self.target_asset.frame_count
            )
            self.last_frame = (
                int(xmlnode.get("lastFrame", default=-1))
                % self.target_asset.frame_count
            )
            self.duration = duration
        else:
            warnings.warn(f'missing asset: {xmlnode.get("libraryItemName")}')
            self.first_frame = 0
            self.last_frame = 0
            self.duration = 1

    def __getitem__(self, iteration: int) -> Frame:
        if not self.target_asset:
            result = Frame()
            result.parent = self
            return result

        if self.loop_type in ("single frame", None):
            frame_index = self.first_frame
        elif self.loop_type == "play once":
            frame_index = min(self.first_frame + iteration, self.last_frame)
        elif self.loop_type == "loop":
            loop_size = (
                self.target_asset.frame_count
            )  # should this take last_frame into account?
            frame_index = (self.first_frame + iteration) % loop_size
        else:
            raise Exception(f"Unknown loop type: {self.loop_type}")

        result = _transformed_frame(
            self.target_asset[frame_index], self.matrix, self.color
        )
        result.parent = self
        return result

    def __len__(self) -> int:
        return self.duration


class DOMShape(Element):
    def __init__(
        self, xflsvg, asset, layer, start_frame_index, duration, path, xmlnode
    ):
        super().__init__(xmlnode)
        self.xflsvg = xflsvg
        self.xmlnode = xmlnode
        self.asset = asset
        self.layer = layer
        self.duration = duration
        self.path = tuple(path)
        self.svg_frame = xflsvg.get_shape(
            xmlnode,
            asset.id,
            layer.index,
            start_frame_index,
            self.path,
        )

    def __getitem__(self, iteration: int) -> Frame:
        result = _transformed_frame(self.svg_frame, self.matrix, self.color)
        result.parent = self
        return result

    def __len__(self) -> int:
        return self.duration


class FrameContext:
    def __init__(self):
        self.xflsvg = None
        self.asset = None
        self.layer = None
        self.start_frame_index = None
        self.duration = None
        self.element_index = 0


class DOMGroup(Element, FrameContext):
    def __init__(
        self, xflsvg, asset, layer, start_frame_index, duration, path, xmlnode
    ):
        super().__init__(xmlnode)
        self.xflsvg = xflsvg
        self.asset = asset
        self.layer = layer
        self.start_frame_index = start_frame_index
        self.duration = duration
        self.path = path
        self.elements = []

        for i, element_xmlnode in enumerate(
            xmlnode.findChild("members", recursive=False).findChildren(recursive=False)
        ):
            element_type = element_xmlnode.name
            if element_type == "DOMShape":
                element = DOMShape(
                    self.xflsvg,
                    self.asset,
                    self.layer,
                    self.start_frame_index,
                    self.duration,
                    [*path, i],
                    element_xmlnode,
                )
            elif element_type == "DOMSymbolInstance":
                element = DOMSymbolInstance(
                    self.xflsvg, self.asset, self.layer, self.duration, element_xmlnode
                )
            elif element_type == "DOMGroup":
                element = DOMGroup(
                    self.xflsvg,
                    self.asset,
                    self.layer,
                    self.start_frame_index,
                    self.duration,
                    [*path, i],
                    element_xmlnode,
                )
            else:
                element = Element(element_xmlnode)

            element.owner_element = self
            self.elements.append(element)

    def __getitem__(self, iteration: int) -> Frame:
        result = Frame(color=self.color)
        result.parent = self
        for child in self.elements:
            result.add_child(child[iteration])

        return result

    def __len__(self) -> int:
        return self.duration


def _get_eases(xmlnode):
    nop = easing.classicEase(0)

    tweens = xmlnode.findChildren("tweens", recursive=False)
    if not tweens:
        return {
            "position": nop,
            "rotation": nop,
            "scale": nop,
            "color": nop,
            "filters": nop,
        }

    tweens = tweens[0]
    result = {}

    for ease in tweens.findChildren("CustomEase"):
        xml_points = ease.findChildren("Point")
        parsed_points = [
            easing.Point(float(x.get("x", 0)), float(x.get("y", 0))) for x in xml_points
        ]
        curve = easing.BezierPath(parsed_points)

        target = ease.get("target")
        if target != "all":
            result[target] = curve
            if target == "filters":
                warnings.warn("Filter ease not supported")

            continue

        result.setdefault("position", curve)
        result.setdefault("rotation", curve)
        result.setdefault("scale", curve)
        result.setdefault("color", curve)
        result.setdefault("filters", curve)

    for ease in tweens.findChildren("Ease"):
        method = ease.get("method", None)
        intensity = ease.get("intensity", None)

        if method and intensity:
            raise Exception(
                "should only specify one of method or acceleration for a tween"
            )

        if method:
            curve = easing.customEases[method]
        elif intensity:
            curve = easing.classicEase(float(intensity))

        target = ease.get("target")
        if target != "all":
            result.setdefault(target, curve)
            if target == "filters":
                warnings.warn("Filter ease not supported")
            continue

        result.setdefault("position", curve)
        result.setdefault("rotation", curve)
        result.setdefault("scale", curve)
        result.setdefault("color", curve)
        result.setdefault("filters", curve)

    result.setdefault("position", nop)
    result.setdefault("rotation", nop)
    result.setdefault("scale", nop)
    result.setdefault("color", nop)
    result.setdefault("filters", nop)

    return result


def shape_tween(element_tweens):
    @contextmanager
    def _tween(n):
        initial_frames = [x.svg_frame for x, y in element_tweens]
        try:
            for domshape, svg_frames in element_tweens:
                domshape.svg_frame = svg_frames[n]
            yield
        finally:
            for i, data in enumerate(element_tweens):
                domshape, svg_frames = data
                domshape.svg_frame = initial_frames[i]

    return _tween


def motion_tween(element_tweens):
    @contextmanager
    def _tween(n):
        initial_matrices = [x.matrix for x, y, z in element_tweens]
        initial_colors = [x.color for x, y, z in element_tweens]

        try:
            for domsymbol, matrices, colors in element_tweens:
                domsymbol.matrix = matrices[n]
                domsymbol.color = colors[n]
            yield
        finally:
            for i, data in enumerate(element_tweens):
                domsymbol, matrices, colors = data
                domsymbol.matrix = initial_matrices[i]
                domsymbol.color = initial_colors[i]

    return _tween


@contextmanager
def trivial_tween(*args):
    yield


class DOMFrame(AnimationObject, FrameContext):
    def __init__(self, xflsvg, layer: "Layer", xmlnode):
        super().__init__()
        self.xflsvg = xflsvg
        self.layer = layer
        self.asset = self.layer.asset
        self.xmlnode = xmlnode
        self.start_frame_index = int(xmlnode.get("index"))
        self.duration = int(xmlnode.get("duration", default=1))
        self.end_frame_index = self.start_frame_index + self.duration
        self._frames = {}
        self.element_index = 0
        self.elements = []
        self.tween_type = xmlnode.get("tweenType", None)
        self.eases = None
        self.tween = trivial_tween

        for i, element_xmlnode in enumerate(
            self.xmlnode.elements.findChildren(recursive=False)
        ):
            element_type = element_xmlnode.name
            if element_type == "DOMShape":
                element = DOMShape(
                    self.xflsvg,
                    self.asset,
                    self.layer,
                    self.start_frame_index,
                    self.duration,
                    [i],
                    element_xmlnode,
                )
            elif element_type == "DOMSymbolInstance":
                element = DOMSymbolInstance(
                    self.xflsvg, self.asset, layer, self.duration, element_xmlnode
                )
            elif element_type == "DOMGroup":
                element = DOMGroup(
                    self.xflsvg,
                    self.asset,
                    self.layer,
                    self.start_frame_index,
                    self.duration,
                    [i],
                    element_xmlnode,
                )
            else:
                element = Element(element_xmlnode)

            element.owner_element = self
            self.elements.append(element)

    def init_tween(self, nextFrame):
        if not self.elements:
            return
        if not nextFrame.elements:
            return
        if self.duration == 1:
            return

        self.eases = _get_eases(self.xmlnode)

        if self.tween_type == "motion":
            start_element = list(
                filter(lambda x: isinstance(x, DOMSymbolInstance), self.elements)
            )
            end_element = list(
                filter(lambda x: isinstance(x, DOMSymbolInstance), nextFrame.elements)
            )

            if not start_element or not end_element:
                return

            element_tweens = []
            for start, end in zip(start_element, end_element):
                matrices = list(
                    matrix_interpolation(
                        start.matrix,
                        end.matrix,
                        self.duration + 1,
                        self.eases,
                    )
                )
                colors = list(
                    color_interpolation(
                        start.color,
                        end.color,
                        self.duration + 1,
                        self.eases,
                    )
                )

                element_tweens.append((start, matrices, colors))
            self.tween = motion_tween(element_tweens)
            return
        elif self.tween_type == "shape":
            segment_xmlnodes = self.xmlnode.findChildren("MorphSegment")
            if not segment_xmlnodes:
                return
            segment_xmlnodes = segment_xmlnodes

            start_element = list(
                filter(lambda x: isinstance(x, DOMShape), self.elements)
            )
            end_element = list(
                filter(lambda x: isinstance(x, DOMShape), nextFrame.elements)
            )

            element_tweens = []

            for start, end in zip(start_element, end_element):
                shape_data = list(
                    shape_interpolation(
                        segment_xmlnodes,
                        start,
                        end,
                        self.duration + 1,
                        self.eases,
                    )
                )
                shapes = []
                for i, data in enumerate(shape_data):
                    shape_frame = self.xflsvg.get_shape(
                        data,
                        self.asset.id,
                        self.layer.index,
                        self.start_frame_index + i,
                        (0,),
                    )
                    shape_frame.owner_element = self
                    shape_frame.frame_index = i + self.start_frame_index
                    shapes.append(shape_frame)

                element_tweens.append((start, shapes))

            self.tween = shape_tween(element_tweens)
            return

        raise Exception("cannot init tween with tween_type ==", self.tween_type)

    def __getitem__(self, frame_index: int) -> Frame:
        if frame_index in self._frames:
            return self._frames[frame_index]

        new_frame = Frame()
        new_frame.parent = self
        iteration = frame_index - self.start_frame_index

        if not self.has_index(frame_index):
            return new_frame

        with self.tween(iteration):
            for element in self.elements:
                new_frame.add_child(element[iteration])

        self._frames[frame_index] = new_frame
        return new_frame

    def __len__(self) -> int:
        return self.duration

    @property
    def frames(self):
        for i in range(self.start_frame_index, self.end_frame_index):
            yield self[i]

    def has_index(self, frame_index):
        if frame_index < self.start_frame_index:
            return False

        if frame_index >= self.end_frame_index:
            return False

        return True


def _get_mask_layer(asset, xmlnode):
    layer_index = xmlnode.get("parentLayerIndex", None)
    if not layer_index:
        return None

    parent_layer = asset.layers[int(layer_index)]
    if parent_layer.layer_type == "mask":
        return parent_layer

    return None


class Layer(AnimationObject):
    def __init__(self, xflsvg, asset: "Asset", id: str, index: int, xmlnode):
        super().__init__()
        self.xflsvg = xflsvg
        self.asset = asset
        self.id = id
        self.index = index
        self.xmlnode = xmlnode
        self.name = xmlnode.get("name", None)
        self.visible = xmlnode.get("visible", "true") != "false"
        self.domframes = []
        self.end_frame_index = 0
        self.layer_type = xmlnode.get("layerType", "normal")
        self.mask_layer = _get_mask_layer(asset, xmlnode)
        self._frames = {}

        if self.xmlnode.frames:
            for bundle_xmlnode in self.xmlnode.frames.findChildren(recursive=False):
                new_domframe = DOMFrame(xflsvg, self, bundle_xmlnode)
                new_domframe.owner_element = self
                self.domframes.append(new_domframe)

                if self.end_frame_index == None:
                    self.end_frame_index = new_domframe.end_frame_index
                else:
                    self.end_frame_index = max(
                        self.end_frame_index, new_domframe.end_frame_index
                    )

        for prev_frame, next_frame in zip(self.domframes[:-1], self.domframes[1:]):
            if prev_frame.tween_type:
                prev_frame.init_tween(next_frame)

    def __getitem__(self, frame_index: int) -> Frame:
        if frame_index in self._frames:
            return self._frames[frame_index]

        new_frame = Frame()
        new_frame.parent = self
        new_frame.data["layer"] = self.name or ""
        new_frame.data["frame"] = frame_index

        for domframe in self.domframes:
            if domframe.has_index(frame_index):
                new_frame.add_child(domframe[frame_index])

        self._frames[frame_index] = new_frame

        return new_frame

    def __len__(self) -> int:
        return self.end_frame_index

    @property
    def frames(self):
        for i in range(self.end_frame_index):
            yield self[i]


class Asset(AnimationObject):
    def __init__(
        self, xflsvg, id: str, xmlnode, timeline=None, width=None, height=None
    ):
        super().__init__()
        self.xflsvg = xflsvg
        self.id = id
        self.layers = []
        self._frames = {}
        self.frame_count = 0
        self.width = width
        self.height = height

        timeline = timeline or xmlnode.timeline

        for index, xmlnode in enumerate(timeline.layers.findChildren(recursive=False)):
            layer_id = f"{self.id}_L{index}"
            layer = Layer(xflsvg, self, layer_id, index, xmlnode)
            layer.owner_element = self
            self.layers.append(layer)
            self.frame_count = max(self.frame_count, layer.end_frame_index)

    def __getitem__(self, frame_index: int) -> Frame:
        if frame_index in self._frames:
            return self._frames[frame_index]

        new_frame = Frame()
        new_frame.parent = self
        new_frame.data["timeline"] = self.id
        new_frame.data["frame"] = frame_index
        if self.width:
            new_frame.data["width"] = self.width
        if self.height:
            new_frame.data["height"] = self.height

        masked_frames = {}
        for layer in self.layers:
            if layer.layer_type == "mask":
                layer_frame = MaskedFrame(layer[frame_index])
                layer_frame.owner_element = self
                layer_frame.frame_index = frame_index

                masked_frames[layer.index] = layer_frame
                new_frame.prepend_child(layer_frame)

            elif layer.layer_type == "normal":
                layer_frame = layer[frame_index]
                if layer.mask_layer:
                    masked_frames[layer.mask_layer.index].prepend_child(layer_frame)
                else:
                    new_frame.prepend_child(layer_frame)

        self._frames[frame_index] = new_frame

        return new_frame

    def __len__(self) -> int:
        return self.frame_count

    @property
    def frames(self):
        for i in range(self.frame_count):
            yield self[i]


class Document(Asset):
    def __init__(self, xflsvg, xmlnode, timeline=0):
        self.width = float(xmlnode.DOMDocument.get("width", 550))
        self.height = float(xmlnode.DOMDocument.get("height", 400))

        available_timelines = xmlnode.timelines.findChildren(
            "DOMTimeline", recursive=False
        )
        if isinstance(timeline, int):
            dom_timeline = available_timelines[timeline]
        elif isinstance(timeline, str):
            for dom_timeline in available_timelines:
                if dom_timeline.get("name") == timeline:
                    break

        assert dom_timeline, "Unable to find timeline in XFL document"
        timeline_name = dom_timeline.get("name")

        super().__init__(
            xflsvg,
            f"file:///{xflsvg.id}.xfl/{timeline_name}",
            xmlnode,
            timeline=dom_timeline,
            width=self.width,
            height=self.height,
        )


class XflReader:
    def __init__(self, xflsvg_dir: str):
        self.filepath = os.path.normpath(xflsvg_dir)  # deal with trailing /
        self.id = os.path.basename(self.filepath)  # MUST come after normpath
        self._assets = {}
        self._shapes = {}

        document_path = os.path.join(xflsvg_dir, "DOMDocument.xml")
        with open(document_path) as document_file:
            self.xmlnode = BeautifulSoup(document_file, "xml")

        self.background = self.xmlnode.DOMDocument.get("backgroundColor", "#FFFFFF")

        width = float(self.xmlnode.DOMDocument.get("width", 550))
        height = float(self.xmlnode.DOMDocument.get("height", 400))
        self.box = [0, 0, width, height]

    def get_timeline(self, timeline=None):
        if timeline == None:
            timeline = 0

        if isinstance(timeline, int):
            return Document(self, self.xmlnode, timeline)

        if timeline.startswith("file://"):
            prefix = f"file://{self.id}/"
            assert timeline.startswith(prefix)
            scene_name = timeline[len(prefix) :]
            return Document(self, self.xmlnode, scene_name)

        return self.get_asset(timeline)

    def get_camera(self):
        return self.box

    def get_safe_asset(self, safe_asset_id):
        asset_id = html.unescape(safe_asset_id)

        if asset_id in self._assets:
            return self._assets[asset_id]

        default_asset_path = os.path.join(
            self.filepath, "LIBRARY", f"{safe_asset_id}.xml"
        )
        if os.path.exists(default_asset_path):
            asset_path = default_asset_path
        else:
            asset_path = default_asset_path.replace("&", "_")

        if not os.path.exists(asset_path):
            return None

        with open(asset_path) as asset_file:
            asset_soup = BeautifulSoup(asset_file, "xml")

        asset = Asset(self, asset_id, asset_soup)
        self._assets[asset_id] = asset
        return asset

    def get_asset(self, asset_id):
        return self.get_safe_asset(html.escape(asset_id).replace("*", "&#042"))

    def get_shape(self, xmlnode, asset_id, layer_index, frame_index, path):
        key = (asset_id, layer_index, frame_index, tuple(path))
        if key in self._shapes:
            return self._shapes[key]

        result = ShapeFrame(str(xmlnode))
        self._shapes[key] = result
        return result


class XflRenderer:
    _contexts = threading.local()
    _contexts.stack = []

    @classmethod
    def current(cls):
        if XflRenderer._contexts.stack == []:
            raise Exception(
                "render() should only be called within an XflRenderer context."
            )
        return XflRenderer._contexts.stack[-1]

    def render_shape(self, svg_frame, *args, **kwargs):
        pass

    def push_transform(self, transformed_frame, *args, **kwargs):
        pass

    def pop_transform(self, transformed_frame, *args, **kwargs):
        pass

    def push_mask(self, masked_frame, *args, **kwargs):
        pass

    def pop_mask(self, masked_frame, *args, **kwargs):
        pass

    def push_masked_render(self, masked_frame, *args, **kwargs):
        pass

    def pop_masked_render(self, masked_frame, *args, **kwargs):
        pass

    def on_frame_rendered(self, *args, **kwargs):
        pass

    def __enter__(self):
        XflRenderer._contexts.stack.append(self)
        return self

    def __exit__(self, *exc):
        XflRenderer._contexts.stack.pop()
