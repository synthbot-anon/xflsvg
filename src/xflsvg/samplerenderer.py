from collections import defaultdict
import math
import os
import re

import xml.etree.ElementTree as ET

from .svgrenderer import SvgRenderer
from .xflsvg import XflRenderer, Asset

_EXPLICIT_FLA = re.compile(r"f-(.*)\.(fla|xfl)", re.IGNORECASE)
_IMPLICIT_FLA = re.compile(r"(.*)\.(fla|xfl)", re.IGNORECASE)

_EXPLICIT_SYM = re.compile(r"s-(.*)\.sym", re.IGNORECASE)
_IMPLICIT_SYM = re.compile(r"(.*_f[0-9]{0,4})\.(png|svg)", re.IGNORECASE)


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


def create_filename(fla_id, symbol_id, frame):
    safe_fla = id_to_filename(fla_id)
    safe_sym = id_to_filename(symbol_id)
    return f'f-{safe_fla}_s-{safe_sym}.sym_f{"%04d" % frame}'


def extract_fla_name(full_path):
    for file_part in full_path.split(os.sep)[::-1]:
        matches = _EXPLICIT_FLA.search(file_part)
        if matches:
            return filename_to_id(matches.group(1))
    for file_part in full_path.split(os.sep)[::-1]:
        matches = _IMPLICIT_FLA.search(file_part)
        if matches:
            return filename_to_id(matches.group(1))
    raise Exception("Missing FLA file in path: %s" % (full_path,))


def extract_symbol_name(full_path):
    for file_part in full_path.split(os.sep)[::-1]:
        matches = _EXPLICIT_SYM.search(file_part)
        if matches:
            return filename_to_id(matches.group(1))
    for file_part in full_path.split(os.sep)[::-1]:
        matches = _IMPLICIT_SYM.search(file_part)
        if matches:
            return filename_to_id(matches.group(1))
    raise Exception("Missing symbol name in path: %s" % (full_path,))


def extract_ids(filepath):
    full_path = os.path.abspath(filepath)

    name = splitext(filepath)[0]
    frame_start = name.rfind("_f") + 2
    frame = int(name[frame_start:])

    return extract_fla_name(full_path), extract_symbol_name(full_path), frame


class SampleRenderer(XflRenderer):
    def __init__(self) -> None:
        super().__init__()
        self._asset_frames = defaultdict(list)

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

            scene = next(iter(reader.get_scene_containers(selected_frame)))
            source = scene[8:].split("/")[0]
            filename = create_filename(source, asset_id, idx)

            destination = os.path.join(output_filename, f"{filename}.svg")
            renderer.compile(destination, suffix=False, *args, **kwargs)


def splitext(path):
    folder, filename = os.path.split(path)
    if "." in filename:
        name, ext = filename.rsplit(".", maxsplit=1)
        return os.path.join(folder, name), f".{ext}"
    return path, ""
