import argparse
from dataclasses import dataclass
from genericpath import isdir
from glob import glob
import hashlib
import json
import logging
import multiprocessing
import os
import re
import traceback
from tqdm import tqdm

from .filter import AssetFilter
from .gifrenderer import GifRenderer
from .pngrenderer import PngRenderer
from .rendertrace import RenderTracer, RenderTraceReader
from .svgrenderer import SvgRenderer
from .webprenderer import WebpRenderer
from .samplerenderer import SampleReader, SampleRenderer
from .util import pool, splitext, get_matching_path, InputFileSpec, OutputFileSpec
from .xflsvg import XflReader


def as_number(data):
    bytes = hashlib.sha512(data.encode("utf8")).digest()[:8]
    return int.from_bytes(bytes, byteorder="big")


def should_process(data, args):
    return (as_number(data) - args.id) % args.poolsize == 0


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


def lock_output(output_path, known_files):
    lock_path = f"{output_path}.progress"
    if os.path.exists(lock_path):
        return True

    if os.path.exists(output_path):
        return False

    open(lock_path, "w").close()
    return True


FRAMERANGE_REGEX = re.compile(r"(.*)_f\d+(-\d+)?(\.[^.]*)")


def lock_output_with_framerange(output_path, known_files):
    lock_path = f"{output_path}.progress"
    if os.path.exists(lock_path):
        return True

    dirname = os.path.dirname(output_path)
    basename = os.path.basename(output_path)

    dir_cache = known_files.get(dirname, None)
    if dir_cache == None:
        known_files[dirname] = dir_cache = set()
        for candidate in os.listdir(dirname):
            match = FRAMERANGE_REGEX.match(candidate)
            if match:
                dir_cache.add(match.group(1) + match.group(3))
            else:
                dir_cache.add(candidate)

    if basename in dir_cache:
        return False

    open(lock_path, "w").close()
    return True


def unlock_output(output_path):
    lock_path = f"{output_path}.progress"
    os.remove(f"{output_path}.progress")


class SeqSplitter:
    def __init__(self, trim=False, split=False) -> None:
        self.current_sequence = []
        self.all_sequences = []
        self.trim = trim
        self.split = split
        self.count = 0

    def append(self, split_here):
        if split_here:
            self.current_sequence.append(None)
        else:
            self.current_sequence.append(self.count)

        self.count += 1

    def finish(self):
        seq = self.current_sequence[:]
        if self.trim or self.split:
            for start in range(len(seq)):
                if seq[start] != None:
                    break

            for end in range(len(seq)):
                if seq[-end - 1] != None:
                    break
            end = len(seq) - end

            seq = seq[start:end]

        seqs = []
        if not self.split:
            seqs.append(seq)
        else:
            seqs.append([])
            for item in seq:
                if item == None:
                    if len(seqs[-1]) != 0:
                        seqs.append([])
                else:
                    seqs[-1].append(item)

        if len(seqs[-1]) == 0:
            seqs.pop()

        return seqs


def create_temp_file(output_path):
    dirname = os.path.dirname(output_path)
    basename = f"temp-{os.path.basename(output_path)}"
    return os.path.join(dirname, basename)


def convert(
    input_path,
    input_type,
    input_asset,
    output_path,
    output_type,
    asset_filter,
    isolate_item,
    seq_labels,
    args,
    known_files={},
):
    input_path = os.path.normpath(input_path)

    if input_type == ".xfl":
        if os.path.isdir(input_path):
            input_folder = input_path
        else:
            input_folder = os.path.dirname(input_path)
        reader = XflReader(input_folder)
    elif input_type == ".trace":
        reader = RenderTraceReader(input_path)
    else:
        raise Exception(
            "The input needs to be either an xfl file (/path/to/file.xfl) or a render trace (/path/to/frames.json.trace)."
        )

    if args.background:
        background = args.background
    elif args.use_document_attrs:
        background = reader.get_background()
    else:
        background = None

    if args.framerate:
        framerate = args.framerate
    else:
        framerate = reader.framerate

    lock_fn = None
    if output_type == ".svg":
        renderer = SvgRenderer()
        output_path = f"{output_path}{output_type}"
        output_folder = os.path.dirname(output_path)
        lock_fn = lock_output_with_framerange
    elif output_type == ".png":
        renderer = PngRenderer()
        output_path = f"{output_path}{output_type}"
        output_folder = os.path.dirname(output_path)
        lock_fn = lock_output_with_framerange
    elif output_type == ".gif":
        renderer = GifRenderer()
        output_path = f"{output_path}{output_type}"
        output_folder = os.path.dirname(output_path)
        lock_fn = lock_output_with_framerange
    elif output_type == ".webp":
        renderer = WebpRenderer()
        output_path = f"{output_path}{output_type}"
        output_folder = os.path.dirname(output_path)
        lock_fn = lock_output_with_framerange
    elif output_type == ".samples":
        renderer = SampleRenderer()
        output_folder = output_path
        output_path = f"{output_path}/"
        lock_fn = lock_output
    elif output_type == ".trace":
        renderer = RenderTracer()
        output_path = f"{output_path}{output_type}"
        output_folder = os.path.dirname(output_path)
        lock_fn = lock_output
    else:
        raise Exception(
            "The output needs to be either an image path (/path/to/file.svg, /path/to/file.png) or a render trace (/path/to/folder)."
        )

    if output_folder:
        os.makedirs(output_folder, exist_ok=True)

    if not lock_fn(output_path, known_files):
        if args.resume:
            print("already completed", output_path)
            return

    logging.basicConfig(
        filename=os.path.join(output_folder, "logs.txt"),
        level=logging.WARNING,
        force=True,
    )
    logging.captureWarnings(True)

    if args.use_document_attrs:
        camera = reader.get_camera()
        renderer.set_camera(*camera)
    else:
        camera = None

    try:
        timeline = reader.get_timeline(input_asset)
        frames = list(timeline)
        rendered_frames = []

        if args.no_stills and len(frames) <= 1:
            print("nothing to render for", output_path)
            return

        splitter = SeqSplitter(args.trim_blanks, args.split_on_blanks)
        sequence = []
        with asset_filter.filtered_render_context(reader.id, renderer, isolate_item):
            for i, frame in enumerate(tqdm(frames, desc="compiling clip")):
                frame.render()
                splitter.append(asset_filter.frame_empty)
                asset_filter.frame_empty = True
                sequence.append(frame.identifier)

        if output_type == ".trace":
            document_info = {
                "type": "clip",
                "frame.id[]": sequence,
                "source": reader.id,
                "framerate": framerate,
            }
            if background:
                document_info["background"] = background

            if camera:
                document_info["camera"] = camera

            renderer.add_label(document_info)

            if seq_labels:
                renderer.add_label(
                    {"type": "tags", "frame.id[]": sequence, "tags": list(seq_labels)}
                )

        renderer.compile(
            output_path,
            sequences=splitter.finish(),
            reader=reader,
            padding=args.padding,
            scale=args.scale,
            background=background,
            framerate=framerate,
            pool=args.threads,
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


def get_matching_path(input_root, output_root, input_path):
    relpath = os.path.relpath(input_path, input_root)
    return os.path.join(output_root, relpath)


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
        help="Output file or folder. This can be a render trace (/path/to/folder/.trace), an SVG (/path/to/file.svg), a PNG (.../file.png), a GIF (.../file.gif), a WEBP (.../file.webp), or a symbol sample folder (.../folder/.samples).",
    )
    parser.add_argument(
        "--batch",
        action="store_true",
        help="""Recursively process all files in a folder to generate the target type. If used, any option suffix (.xfl, .svg) is stripped off the input and output arguments, and the results are treated as folders that should contain inputs and outputs. Example to process XFL files in /input/root/ and write the resulting SVG files to /output/root/: seq 24 | xargs -L1 -P24 python -m xflsvg /input/root.xfl /output/root.svg --batch --use-camera --par 24 --id""",
    )
    parser.add_argument(
        "--poolsize",
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
        "--use-document-attrs",
        action="store_true",
        help="Use the camera box and background relevant to the scene. This should only be used when rendering a scene, not when rendering a symbol.",
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
        "--isolate",
        type=InputFileSpec.from_spec,
        help='Individually render each asset in the given samples folder (e.g., /path/to/labels/.samples). Optionally specify which labels to use in brackets (e.g., ".../labels/.samples[Clean,Noisy]").',
    )
    parser.add_argument(
        "--seq-labels",
        type=InputFileSpec.from_spec,
        help="Attach .samples labels to each output file. This is only applicable for .trace output files.",
        default=None,
    )
    parser.add_argument(
        "--background",
        type=str,
        default=None,
        help="Use a background color for transparent pixels when converting to PNG or GIF. Default: #0000.",
    )
    parser.add_argument(
        "--no-stills",
        action="store_true",
        default=False,
    )
    parser.add_argument(
        "--trim-blanks",
        action="store_true",
        default=False,
    )
    parser.add_argument(
        "--split-on-blanks",
        action="store_true",
        default=False,
    )
    parser.add_argument(
        "--framerate",
        type=float,
        default=None,
    )
    parser.add_argument(
        "--threads",
        type=pool,
        default=pool(1),
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        default=False,
    )

    args = parser.parse_args()
    multiprocessing.set_start_method("spawn")
    filter = AssetFilter(args)

    assert args.input.ext in (
        ".xfl",
        ".trace",
    ), "Input arg must end in either .xfl or .trace"
    assert args.output.ext in (
        ".svg",
        ".png",
        ".gif",
        ".webp",
        ".samples",
        ".trace",
    ), "Output arg must end in either .svg, .png, .gif, .samples, or .trace"

    if not args.batch:
        for input_asset, output_path, isolated_item, seq_labels in filter.get_tasks(
            args.input, args.output, args.batch
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
                isolated_item,
                seq_labels,
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

            # output_location = args.output.matching_descendent(input)
            for input_asset, output_path, isolated_item, seq_labels in filter.get_tasks(
                input, args.output, args.batch
            ):
                print(
                    "processing:",
                    f"{input.path}[{input_asset or ''}] {input.ext} ->",
                    f"{output_path} {args.output.ext}",
                )
                convert(
                    input.path,
                    input.ext,
                    input_asset,
                    output_path,
                    args.output.ext,
                    filter,
                    isolated_item,
                    seq_labels,
                    args,
                )

            if args.input.ext == ".xfl":
                # we matched on a file in the directory for xfls
                # so break since the whole directory has been processed
                break


if __name__ == "__main__":
    main()
