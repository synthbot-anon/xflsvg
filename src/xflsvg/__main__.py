import argparse
import hashlib
import json
import logging
import os
import re
import traceback

from .filter import AssetFilter
from .gifrenderer import GifRenderer
from .pngrenderer import PngRenderer
from .rendertrace import RenderTracer, RenderTraceReader
from .svgrenderer import SvgRenderer
from .samplerenderer import SampleRenderer
from .util import splitext, get_matching_path
from .xflsvg import XflReader


# known buggy files: MLP509_414 (tween), MLP422_593 and MLP509_056 (shape), MLP509_275 (stroke id)
# ... MLP214_079 (missing shapes)
# known missing stuff: LinearGradient for strokes


def as_number(data):
    bytes = hashlib.sha512(data.encode("utf8")).digest()[:8]
    return int.from_bytes(bytes, byteorder="big")


def should_process(data, args):
    return (as_number(data) - args.id) % args.par == 0


def convert(input_path, output_path, asset, asset_filter, focus_fn, args):
    input_path = os.path.normpath(input_path)
    if input_path.lower().endswith(".xfl"):
        input_folder = os.path.dirname(input_path)
        reader = XflReader(input_folder, asset_filter)
    elif input_path.lower().endswith(".trace"):
        input_folder = os.path.dirname(input_path)
        reader = RenderTraceReader(input_folder, asset_filter)
    else:
        raise Exception(
            "The input needs to be either an xfl file (/path/to/file.xfl) or a render trace (/path/to/frames.json.trace)."
        )

    output_path = os.path.normpath(output_path)
    if output_path.lower().endswith(".svg"):
        renderer = SvgRenderer()
        output_folder = os.path.dirname(output_path)
    elif output_path.lower().endswith(".png"):
        renderer = PngRenderer(background=args.background)
        output_folder = os.path.dirname(output_path)
    elif output_path.lower().endswith(".gif"):
        renderer = GifRenderer(background=args.background)
        output_folder = os.path.dirname(output_path)
    elif output_path.lower().endswith(".samples"):
        renderer = SampleRenderer()
        output_folder = splitext(output_path)[0]
        output_path = output_folder
    elif os.path.isdir(output_path) or not os.path.exists(output_path):
        renderer = RenderTracer()
        output_folder = output_path
    else:
        raise Exception(
            "The output needs to be either an image path (/path/to/file.svg, /path/to/file.png) or a render trace (/path/to/folder)."
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
        timeline = reader.get_timeline(asset)
        with asset_filter.filtered_render_context(reader.id, renderer, focus_fn):
            frames = list(timeline)
            if args.no_stills and len(frames) <= 1:
                return
            for frame in frames:
                frame.render()

        renderer.compile(
            output_path, reader=reader, padding=args.padding, scale=args.scale
        )
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
        help='Input file or folder. This can be an XFL file (/path/to/file.xfl) or a render trace (/path/to/trace/). To specify a timeline, you can append the symbol name in brackets ("/file.xfl[~Octavia*Character]"). To specify multiple timelines, you can specify a symbol sample label folder in brackets ("/file.xfl[/path/to/labels/.samples]")',
    )
    parser.add_argument(
        "output",
        type=str,
        help="Output file or folder. This can be a render trace (/path/to/folder/.trace), an SVG (/path/to/file.svg), a PNG (.../file.png), a GIF (.../file.gif), or a symbol sample folder (.../folder/.samples).",
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
        help="Padding width (in pixels) to use in the output. This only applies to SVG outputs. It is applied after any scaling.",
    )
    parser.add_argument(
        "--use-camera",
        action="store_true",
        help="Use the camera box relevant to the scene. This should only be used when rendering a scene, not when rendering a symbol. This only applies to SVG outputs. If not set, use whatever box fits the frame being rendered.",
    )
    parser.add_argument(
        "--discard",
        type=str,
        help='Skip rendering any assets in the given samples folder (e.g., /path/to/labels/.samples). Optionally specify which labels to use in brackets (e.g., ".../labels/.samples[Noisy,SizeRef]").',
    )
    parser.add_argument(
        "--retain",
        type=str,
        help='Skip rendering assets NOT in the given samples folder (e.g., /path/to/labels/.samples). Optionally specify which labels to use in brackets (e.g., ".../labels/.samples[Clean,Noisy]").',
    )
    parser.add_argument(
        "--focus",
        type=str,
        help='Individually render each asset in the given samples folder (e.g., /path/to/labels/.samples). Optionally specify which labels to use in brackets (e.g., ".../labels/.samples[Clean,Noisy]").',
    )
    parser.add_argument(
        "--background",
        type=str,
        default="#00000000",
        help="Use a background color for transparent pixels when converting to PNG or GIF. Default: #00000000.",
    )
    parser.add_argument(
        "--no-stills",
        action="store_true",
        default=False,
    )

    args = parser.parse_args()
    input = args.input.split("[", maxsplit=1)[0].rstrip("/\\")
    filter = AssetFilter(args)

    input_folder, source_type = splitext(input)
    output, target_type = splitext(args.output)

    source_type = source_type.lower()
    target_type = target_type.lower()

    assert source_type in (
        ".xfl",
        ".trace",
    ), "Input arg must end in either .xfl or .trace"
    assert target_type in (
        ".svg",
        ".png",
        ".gif",
        ".samples",
        ".trace",
    ), "Output arg must end in either .svg or /"

    if not args.batch:
        for timeline, output_path, focus_fn in filter.get_tasks(
            input_folder, output, False
        ):
            print(
                "got task:", f"{input}", f"{output_path}{target_type}", timeline, filter
            )
            convert(
                input, f"{output_path}{target_type}", timeline, filter, focus_fn, args
            )
        return

    for root, dirs, files in os.walk(input_folder, followlinks=True):
        for fn in files:
            if not should_process(os.path.join(root, fn), args):
                continue

            if source_type == ".xfl":
                name, extension = splitext(fn)
                if extension.lower() != ".xfl":
                    continue
                input_path = os.path.join(root, fn)
            elif source_type == ".trace":
                if fn.lower() != "frames.json":
                    continue
                input_path = os.path.join(root, fn)

            input_relpath = os.path.relpath(root, input_folder)
            completed_conversions = {}
            for timeline, output_path, focus_fn in filter.get_tasks(
                input_relpath, output, True
            ):
                if timeline in completed_conversions:
                    # TODO: copy the result over
                    pass

                print(input_path, f"{output_path}{target_type}", timeline, filter)
                convert(
                    input_path,
                    f"{output_path}{target_type}",
                    timeline,
                    filter,
                    focus_fn,
                    args,
                )

                completed_conversions[timeline] = output_path
            break


if __name__ == "__main__":
    main()
