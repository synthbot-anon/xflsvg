import base64
from collections import defaultdict
import hashlib
import json
import math
import os
import re
import shutil

from lxml import etree
from xfl2svg.shape.shape import json_normalize_xfl_domshape

from .svgrenderer import SvgRenderer
from .util import splitext
from .xflsvg import XflRenderer, Asset
from .svgrenderer import shape_frame_to_svg

_EXPLICIT_FLA = re.compile(r"f-(.*)\.(fla|xfl)", re.IGNORECASE)
_IMPLICIT_FLA = re.compile(r"(.*)\.(fla|xfl)", re.IGNORECASE)

_EXPLICIT_SYM = re.compile(r"s-(.*)\.sym", re.IGNORECASE)
_IMPLICIT_SYM = re.compile(r"(.*_f[0-9]{0,4})\.(png|svg|gif)", re.IGNORECASE)

_EXPLICIT_SHAPE = re.compile(r"d-(.*)\.shape", re.IGNORECASE)


def _unescape_filename_part(filename):
    return (
        filename.replace("_m", "?")
        .replace("_p", "|")
        .replace("_r", ">")
        .replace("_l", "<")
        .replace("_q", '"')
        .replace("_c", ":")
        .replace("_t", "~")
        .replace("_b", "\\")
        .replace("_f", "/")
        .replace("_s", "*")
        .replace("__", "_")
    )


def filename_to_id(filename):
    parts = [_unescape_filename_part(x) for x in filename.split("__")]
    return "_".join(parts)


def id_to_filename(id):
    return (
        id.replace("_", "__")
        .replace("*", "_s")
        .replace("/", "_f")
        .replace(r"\\", "_b")
        .replace("~", "_t")
        .replace(":", "_c")
        .replace('"', "_q")
        .replace("<", "_l")
        .replace(">", "_r")
        .replace("|", "_p")
        .replace("?", "_m")
    )


def hash(data):
    half_sha512 = hashlib.sha512(data.encode("utf-8")).digest()[:32]
    return base64.urlsafe_b64encode(half_sha512).decode("ascii")


def create_filename(fla_id, symbol_id, shape, frame):
    safe_fla = fla_id and f"f-{id_to_filename(fla_id)}.xfl"
    safe_sym = symbol_id and f"s-{id_to_filename(symbol_id)}.sym"
    safe_shape = shape and f"d-{shape}.shape"
    safe_frame = (frame != None) and f'f{"%04d" % frame}'
    pieces = filter(lambda x: x, [safe_fla, safe_sym, safe_shape, safe_frame])
    return "_".join(pieces)


def extract_fla_name(full_path):
    for file_part in full_path.split(os.sep)[::-1]:
        matches = _EXPLICIT_FLA.search(file_part)
        if matches:
            return filename_to_id(matches.group(1))
    for file_part in full_path.split(os.sep)[::-1]:
        matches = _IMPLICIT_FLA.search(file_part)
        if matches:
            return filename_to_id(matches.group(1))
    return None


def extract_symbol_name(full_path):
    for file_part in full_path.split(os.sep)[::-1]:
        matches = _EXPLICIT_SYM.search(file_part)
        if matches:
            return filename_to_id(matches.group(1))
    return None


def extract_shape_name(full_path):
    for file_part in full_path.split(os.sep)[::-1]:
        matches = _EXPLICIT_SHAPE.search(file_part)
        if matches:
            return filename_to_id(matches.group(1))
    return None


def extract_ids(filepath):
    name = splitext(filepath)[0]
    frame_start = name.rfind("_f")
    frame_end = name.find(".", frame_start)
    if frame_start == -1 or frame_end == -1:
        frame = 0
    else:
        try:
            frame = int(name[frame_start:frame_end])
        except:
            frame = None
    return (
        extract_fla_name(filepath),
        extract_symbol_name(filepath),
        extract_shape_name(filepath),
        frame,
    )


_xml_parser = etree.XMLParser(remove_blank_text=True)


class SampleRenderer(XflRenderer):
    def __init__(self, render_shapes=False) -> None:
        super().__init__()
        self._asset_frames = defaultdict(list)
        self._shape_frames = {}
        self.mask_depth = 0
        self.render_shapes = render_shapes

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

        id = hash(json.dumps(shape_data, sort_keys=True))
        self._shape_frames[id] = shape_frame

    def push_mask(self, masked_snapshot, *args, **kwargs):
        self.mask_depth += 1

    def pop_mask(self, masked_snapshot, *args, **kwargs):
        self.mask_depth -= 1

    def on_frame_rendered(self, frame, *args, **kwargs):
        if frame.element_type != "asset":
            return

        self._asset_frames[frame.element_id].append(frame)

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
            with renderer:
                selected_frame.render()

            source = reader.id
            filename = create_filename(source, asset_id, None, idx)

            destination = os.path.join(output_filename, f"{filename}.svg")
            renderer.compile(destination, suffix=False, *args, **kwargs)

        if self.render_shapes:
            for shape_id, shape_frame in self._shape_frames.items():
                with renderer:
                    shape_frame.render()
                filename = create_filename(None, None, shape_id, idx)
                destination = os.path.join(output_filename, f"{filename}.svg")
                renderer.compile(destination, suffix=False, *args, **kwargs)

    def output_completed(self, output_path):
        return False


class SampleReader:
    _labels_by_asset = {}
    _assets_by_label = {}
    _asset_paths_by_fla = {}

    def __init__(self, input_folder):
        self.input_folder = input_folder

    @classmethod
    def load_samples(cls, input_path):
        if input_path in cls._labels_by_asset:
            return (
                cls._labels_by_asset[input_path],
                cls._assets_by_label[input_path],
                cls._asset_paths_by_fla[input_path],
            )

        assets_path, ext = splitext(input_path)

        if ext == ".samples":
            reader = SampleReader(assets_path)
            labels, orig_paths = reader.get_labels()
        else:
            raise Exception("cannot create a filter from input type", ext)

        cls._labels_by_asset[input_path] = labels
        cls._asset_paths_by_fla[input_path] = orig_paths

        # reverse the labels dictionary so it's easier to find things by label
        assets_by_label = defaultdict(set)
        cls._assets_by_label[input_path] = assets_by_label
        for asset, labels in labels.items():
            for l in labels:
                assets_by_label[l].add(asset)

        return (
            cls._labels_by_asset[input_path],
            cls._assets_by_label[input_path],
            cls._asset_paths_by_fla[input_path],
        )

    def get_labels(self):
        result = defaultdict(set)
        orig_paths = {}
        for root, dirs, files in os.walk(self.input_folder):
            if not files:
                continue

            relpath = os.path.relpath(root, self.input_folder)
            labels = set(relpath.split(os.sep))

            for f in files:
                try:
                    fla, asset, shape, frame = extract_ids(f)
                    if fla == None:
                        print("failed to parse filename label from:", f)
                        continue
                    label = os.path.basename(root)
                    result[(fla, asset)].update(labels)
                    asset_path = os.path.splitext(os.path.join(relpath, f))[0]
                    orig_paths.setdefault(fla, {}).setdefault(asset, set()).add(
                        asset_path
                    )
                except:
                    print("failed to parse filename label from:", f)

        return result, orig_paths
