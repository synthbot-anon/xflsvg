import argparse
from dataclasses import dataclass
from genericpath import isdir
from glob import glob
import hashlib
import json
import logging
import os
import re
import traceback
from tqdm import tqdm

from .filter import AssetFilter
from .gifrenderer import GifRenderer
from .pngrenderer import PngRenderer
from .rendertrace import RenderTracer, RenderTraceReader
from .svgrenderer import SvgRenderer
from .samplerenderer import SampleReader, SampleRenderer
from .util import splitext, get_matching_path
from .xflsvg import XflReader


# known buggy files: MLP509_414 (tween), MLP422_593 and MLP509_056 (shape), MLP509_275 (stroke id)
# ... MLP214_079 (missing shapes), MLP214_107 (rarity's hoof), MLP422_027 (when focusing on Twilight,
# ... there's one behind the background)
# known missing stuff: LinearGradient for strokes
# head roll (loop issue): f-MLP214__138.xfl_s-fafa_tRD_sCharacter.sym.gif
# flashing leg: f-MLP214__390.xfl_s-_tPP_sCharacter.sym.gif


def as_number(data):
    bytes = hashlib.sha512(data.encode("utf8")).digest()[:8]
    return int.from_bytes(bytes, byteorder="big")


def should_process(data, args):
    return (as_number(data) - args.id) % args.par == 0


def output_completed(output_path):
    if os.path.exists(f"{output_path}.lock"):
        return False

    if os.path.exists(output_path):
        return True

    base, ext = os.path.splitext(output_path)
    start = len(base)
    end = -len(ext)
    for candidate in glob(f"{base}*{ext}"):
        try:
            int(candidate[start:end])
            return True
        except:
            pass

    return False


def lock_output(output_path):
    open(f"{output_path}.lock", "w").close()


def unlock_output(output_path):
    os.remove(f"{output_path}.lock")


def convert(
    input_path,
    input_type,
    input_asset,
    output_path,
    output_type,
    asset_filter,
    focus_fn,
    args,
):
    print("output:", output_path, output_type)
    input_path = os.path.normpath(input_path)
    if input_type == ".xfl":
        if os.path.isdir(input_path):
            input_folder = input_path
        else:
            input_folder = os.path.dirname(input_path)
        reader = XflReader(input_folder, asset_filter)
    elif input_type == ".trace":
        input_folder = os.path.dirname(input_path)
        reader = RenderTraceReader(input_folder, asset_filter)
    else:
        raise Exception(
            "The input needs to be either an xfl file (/path/to/file.xfl) or a render trace (/path/to/frames.json.trace)."
        )

    if output_type == ".svg":
        renderer = SvgRenderer()
        output_path = f"{output_path}/{output_type}"
        output_folder = os.path.dirname(output_path)
    elif output_type == ".png":
        renderer = PngRenderer(background=args.background)
        output_path = f"{output_path}/{output_type}"
        output_folder = os.path.dirname(output_path)
    elif output_type == ".gif":
        renderer = GifRenderer(background=args.background)
        output_path = f"{output_path}{output_type}"
        output_folder = os.path.dirname(output_path)
    elif output_type == ".samples":
        renderer = SampleRenderer()
        output_folder = output_path
        output_path = f"{output_path}/{output_type}"
    elif output_type == ".trace":
        renderer = RenderTracer()
        output_path = f"{output_path}{output_type}"
        output_folder = os.path.dirname(output_path)
    else:
        raise Exception(
            "The output needs to be either an image path (/path/to/file.svg, /path/to/file.png) or a render trace (/path/to/folder)."
        )

    if output_completed(output_path):
        print("already completed:", output_path)
        return

    os.makedirs(output_folder, exist_ok=True)
    lock_output(output_path)

    logging.basicConfig(
        filename=os.path.join(output_folder, "logs.txt"),
        level=logging.DEBUG,
        force=True,
    )
    logging.captureWarnings(True)

    if args.use_camera:
        renderer.set_camera(*reader.get_camera())

    try:
        timeline = reader.get_timeline(input_asset)
        with asset_filter.filtered_render_context(reader.id, renderer, focus_fn):
            frames = list(timeline)
            if args.no_stills and len(frames) <= 1:
                return
            for frame in tqdm(frames, desc="compiling clip"):
                frame.render()

        renderer.compile(
            output_path,
            reader=reader,
            padding=args.padding,
            scale=args.scale,
            skip_leading_blanks=args.skip_leading_blanks,
        )

        unlock_output(output_path)

    except KeyboardInterrupt:
        raise
    except:
        print(
            f"error processing",
            input_path,
            f"- check {output_folder}/logs.txt for details.",
        )
        logging.exception(traceback.format_exc())
        unlock_output(output_path)


def get_matching_path(input_root, output_root, input_path):
    relpath = os.path.relpath(input_path, input_root)
    return os.path.join(output_root, relpath)


@dataclass(frozen=True)
class InputFileSpec:
    path: str
    ext: str
    param: str
    is_folder: bool
    relpath: str

    _labels_by_asset = {}
    _assets_by_label = {}
    _asset_paths_by_fla = {}

    @classmethod
    def from_spec(cls, spec, root=None):
        if "[" in spec:
            param_start = spec.find("[") + 1
            assert spec[-1] == "]"

            param = spec[param_start:-1]
            spec = spec[: param_start - 1]
        else:
            param = None

        path, ext = splitext(spec)

        is_folder = (not os.path.isfile(path)) or (path[-1] in ("/", "\\"))

        # TODO: make this work on windows
        if root == None:
            if spec[0] == "/":
                root = "/"
            else:
                root = ""

        relpath = os.path.relpath(path, root)

        return InputFileSpec(path, ext.lower(), param, is_folder, relpath)

    def subspec(self, path):
        relpath = os.path.relpath(path, self.path)
        return InputFileSpec(path, self.ext, self.param, self.is_folder, relpath)


@dataclass(frozen=False)
class OutputFileSpec:
    path: str
    ext: str

    @classmethod
    def from_spec(cls, spec):
        path, ext = splitext(spec)
        return OutputFileSpec(path, ext)

    def matching_descendent(self, input):
        new_path = os.path.join(self.path, input.relpath)
        return OutputFileSpec(new_path, self.ext)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "input",
        type=InputFileSpec.from_spec,
        help='Input file or folder. This can be an XFL file (/path/to/file.xfl) or a render trace (/path/to/trace/). To specify a timeline, you can append the symbol name in brackets ("/file.xfl[~Octavia*Character]"). To specify multiple timelines, you can specify a symbol sample label folder in brackets ("/file.xfl[/path/to/labels/.samples]")',
    )
    parser.add_argument(
        "output",
        type=OutputFileSpec.from_spec,
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
        default=0,
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
        type=InputFileSpec.from_spec,
        help='Skip rendering any assets in the given samples folder (e.g., /path/to/labels/.samples). Optionally specify which labels to use in brackets (e.g., ".../labels/.samples[Noisy,SizeRef]").',
    )
    parser.add_argument(
        "--retain",
        type=InputFileSpec.from_spec,
        help='Skip rendering assets NOT in the given samples folder (e.g., /path/to/labels/.samples). Optionally specify which labels to use in brackets (e.g., ".../labels/.samples[Clean,Noisy]").',
    )
    parser.add_argument(
        "--focus",
        type=InputFileSpec.from_spec,
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
    parser.add_argument(
        "--skip-leading-blanks",
        action="store_true",
        default=False,
    )

    args = parser.parse_args()
    filter = AssetFilter(args)

    print(args.input.ext)

    assert args.input.ext in (
        ".xfl",
        ".trace",
    ), "Input arg must end in either .xfl or .trace"
    assert args.output.ext in (
        ".svg",
        ".png",
        ".gif",
        ".samples",
        ".trace",
    ), "Output arg must end in either .svg, .png, .gif, .samples, or .trace"

    if not args.batch:
        for input_asset, output_path, focus_fn in filter.get_tasks(
            args.input.path, args.output.path
        ):
            print(
                "processing:",
                f"{args.input.path}{args.input.ext}[{input_asset or ''}] ->",
                f"{output_path}{args.output.ext}",
            )
            convert(
                args.input.path,
                args.input.ext,
                input_asset,
                output_path,
                args.output.ext,
                filter,
                focus_fn,
                args,
            )
        return

    for root, dirs, files in os.walk(args.input.path, followlinks=True):
        for fn in files:
            if not fn.lower().endswith(args.input.ext):
                continue

            if args.input.ext == ".xfl":
                # use the directory path for xfl files
                input = args.input.subspec(f"{root}/")
            else:
                input = args.input.subset(os.path.join(root, fn))

            if not should_process(input.relpath, args):
                continue

            output_location = args.output.matching_descendent(input)
            for input_asset, output_path, focus_fn in filter.get_tasks(
                input.path, output_location.path
            ):
                print(
                    "processing:",
                    f"{input.path}{input.ext}[{input_asset or ''}] ->",
                    f"{output_path}{args.output.ext}",
                )
                convert(
                    input.path,
                    input.ext,
                    input_asset,
                    output_path,
                    args.output.ext,
                    filter,
                    focus_fn,
                    args,
                )

            if args.input.ext == ".xfl":
                # we matched on a file in the directory for xfls
                # so break since the whole directory has been processed
                break


if __name__ == "__main__":
    main()
