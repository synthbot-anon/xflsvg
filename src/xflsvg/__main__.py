import argparse
import hashlib
import json
import logging
import os
import re
import traceback

from .rendertrace import RenderTracer, RenderTraceReader
from .svgrenderer import SvgRenderer
from .xflsvg import XflReader


# known buggy files: MLP509_414


def as_number(data):
    bytes = hashlib.sha512(data.encode("utf8")).digest()[:8]
    return int.from_bytes(bytes, byteorder="big")


def should_process(data, args):
    return (as_number(data) - args.id) % args.par == 0


def convert(input_path, output_path, args):
    print("converting", input_path, "->", output_path)
    input_path = os.path.normpath(input_path)
    if input_path.lower().endswith(".xfl"):
        input_folder = os.path.dirname(input_path)
        reader = XflReader(input_folder)
    elif os.path.isdir(input_path):
        reader = RenderTraceReader(input_path)
    else:
        raise Exception(
            "The input needs to be either an xfl file (/path/to/file.xfl) or a render trace (/path/to/trace/)."
        )

    output_path = os.path.normpath(output_path)
    if output_path.lower().endswith(".svg"):
        renderer = SvgRenderer()
        output_folder = os.path.dirname(output_path)

    elif os.path.isdir(output_path) or not os.path.exists(output_path):
        renderer = RenderTracer()
        output_folder = output_path
    else:
        raise Exception(
            "The output needs to be either an svg path (/path/to/file.svg) or a render trace (/path/to/folder)."
        )

    if output_folder:
        os.makedirs(output_folder, exist_ok=True)

    logging.basicConfig(
        filename=os.path.join(output_folder, "logs.txt"),
        level=logging.DEBUG,
        force=True,
    )
    logging.captureWarnings(True)

    if args.use_camera:
        renderer.set_camera(*reader.get_camera())

    try:
        timeline = reader.get_timeline(args.timeline)
        with renderer:
            for frame in list(timeline):
                frame.render()
                renderer.save_frame(frame)

        renderer.compile(output_path, padding=args.padding, scale=args.scale)
    except:
        print(f"error - check {output_folder}/logs.txt for details.")
        logging.exception(traceback.format_exc())


def get_matching_path(input_root, output_root, input_path):
    relpath = os.path.relpath(input_path, input_root)
    return os.path.join(output_root, relpath)


def splitext(path):
    folder, filename = os.path.split(path)
    if "." in filename:
        name, ext = filename.rsplit(".", maxsplit=1)
        return os.path.join(folder, name), f".{ext}"
    return path, ""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "input",
        type=str,
        help="Input file or folder. This can be an XFL file (/path/to/file.xfl) or a render trace (/path/to/trace/).",
    )
    parser.add_argument(
        "output",
        type=str,
        help="Output file or folder. This can be a render trace (/path/to/trace/) or an SVG (/path/to/file.svg).",
    )
    parser.add_argument(
        "--batch",
        action="store_true",
        help="""Recursively process all files in a folder to generate the target type. If used, any option suffix (.xfl, .svg) is stripped off the input and output arguments, and the results are treated as folders that should contain inputs and outputs. Example to process XFL files in /input/root/ and write the resulting SVG files to /output/root/: seq 24 | xargs -L1 -P24 python -m xflsvg /input/root.xfl /output/root.svg --batch --use-camera --par 24 --id""",
    )
    parser.add_argument(
        "--par",
        type=int,
        required=False,
        default=1,
        help="The total number of sibling processes running the same task in parallel. This is for parallel execution with xargs.",
    )
    parser.add_argument(
        "--id",
        type=int,
        required=False,
        default=1,
        help="The sibling index of this process (0 through par-1). This is for parallel execution with xargs.",
    )
    parser.add_argument(
        "--timeline",
        required=False,
        type=str,
        help='Timeline to use within a file. This is either the symbol name (e.g., "~Octavia*Character") or the scene id (e.g., "file://file.xfl/Scene 1").',
    )
    parser.add_argument(
        "--scale",
        required=False,
        type=float,
        default=1,
        help="Scale the image by the given factor. This only applies to SVG outputs. scale > 1 makes the image larger, 0 < scale < 1 makes the image smaller.",
    )
    parser.add_argument(
        "--padding",
        required=False,
        type=float,
        default=0,
        help="Padding width to use in the output. This only applies to SVG outputs. It is applied after any scaling.",
    )
    parser.add_argument(
        "--use-camera",
        action="store_true",
        help="Use the camera box relevant to the scene. This should only be used when rendering a scene, not when rendering a symbol. This only applies to SVG outputs. If not set, use whatever box fits the frame being rendered.",
    )

    args = parser.parse_args()
    if not args.batch:
        convert(args.input, args.output, args)
        return

    input_folder, source_type = splitext(args.input)
    output_folder, target_type = splitext(args.output)

    source_type = source_type.lower()
    target_type = target_type.lower()

    assert source_type in (".xfl", ""), "Input arg must end in either .xfl or /"
    assert target_type in (".svg", ""), "Output arg must end in either .svg or /"

    for root, dirs, files in os.walk(input_folder):
        for fn in files:
            if not should_process(os.path.join(root, fn), args):
                continue

            if source_type == ".xfl":
                name, extension = splitext(fn)
                if extension.lower() == ".xfl":
                    input_path = os.path.join(root, fn)
                    output_path = get_matching_path(input_folder, output_folder, root)
                    convert(input_path, f"{output_path}/{target_type}", args)
            elif source_type == "":
                if fn.lower() == "frames.json":
                    input_path = root
                    output_path = get_matching_path(input_folder, output_folder, root)
                    convert(input_path, f"{output_path}/{target_type}", args)


if __name__ == "__main__":
    main()
