import argparse
import hashlib
import json
import logging
import os
import traceback

from .rendertrace import RenderTracer, RenderTraceReader
from .svgrenderer import SvgRenderer
from .xflsvg import XflReader


def as_number(data):
    bytes = hashlib.sha512(data.encode("utf8")).digest()[:8]
    return int.from_bytes(bytes, byteorder="big")


def should_process(data, id, par):
    return as_number(data) % par == id


if __name__ == "__main__":
    parser = argparse.ArgumentParser()

    # export to tables
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

    input_path = os.path.normpath(args.input)
    if input_path.lower().endswith(".xfl"):
        input_folder = os.path.dirname(input_path)
        reader = XflReader(input_folder)
    elif os.path.isdir(input_path):
        reader = RenderTraceReader(input_path)
    else:
        raise Exception(
            "The input needs to be either an xfl file (/path/to/file.xfl) or a render trace (/path/to/trace/)."
        )

    output_path = os.path.normpath(args.output)
    if output_path.lower().endswith(".svg"):
        renderer = SvgRenderer()
    elif os.path.isdir(output_path) or not os.path.exists(output_path):
        renderer = RenderTracer()
    else:
        raise Exception(
            "The output needs to be either an svg path (/path/to/file.svg) or a render trace (/path/to/folder)."
        )

    if args.use_camera:
        renderer.set_camera(*reader.get_camera())

    timeline = reader.get_timeline(args.timeline)
    with renderer:
        for frame in list(timeline):
            frame.render()
            renderer.save_frame(frame)

    renderer.compile(output_path, padding=args.padding, scale=args.scale)

    #     parser = argparse.ArgumentParser()
    #     parser.add_argument('--export', required=True, action='store_true')
    #     parser.add_argument('input_folder', type=str)
    #     parser.add_argument('output_folder', type=str)

    #     args = parser.parse_known_args()[0]

    #     for root, dirs, files in os.walk(input_folder):
    #         relpath = os.path.relpath(root, input_folder)
    #         target_folder = os.path.join(args.output_folder, relpath)
    #         if not should_process(target_folder, id, par):
    #             continue

    #         if not os.path.exists(os.path.join(root, 'DOMDocument.xml')):
    #             continue

    #         has_shapes = os.path.exists(os.path.join(target_folder, 'shapes.json'))
    #         has_frames = os.path.exists(os.path.join(target_folder, 'frames.json'))
    #         has_labels = os.path.exists(os.path.join(target_folder, 'labels.json'))
    #         if has_shapes and has_frames and has_labels:
    #             print('-- skipping', root)
    #             continue

    #         print('-- exporting', root)
    #         os.makedirs(target_folder, exist_ok=True)
    #         logging.basicConfig(filename=os.path.join(target_folder, 'logs.txt'), level=logging.DEBUG, force=True)
    #         logging.captureWarnings(True)

    #         try:
    #             export(root, target_folder)
    #         except:
    #             print('error on', root)
    #             logging.exception(traceback.format_exc())

    # elif args.render:
    #     parser = argparse.ArgumentParser()
    #     parser.add_argument("--render", required=True, action='store_true')
    #     parser.add_argument('format', type=str)
    #     parser.add_argument('input_folder', type=str)
    #     parser.add_argument('--timeline', required=True, type=str)
    #     parser.add_argument('output_folder', type=str)
    #     parser.add_argument('--width', required=False, type=float)
    #     parser.add_argument('--height', required=False, type=float)
    #     parser.add_argument('--x', required=False, type=float)
    #     parser.add_argument('--y', required=False, type=float)
    #     parser.add_argument('--scale', required=False, type=float)

    #     args = parser.parse_known_args()[0]

    #     os.makedirs(args.output_folder, exist_ok=True)
    #     input_folder = os.path.abspath(args.input_folder)
    #     with open(os.path.join(input_folder, 'shapes.json'), 'r') as inp:
    #         shapes = json.load(inp)
    #     with open(os.path.join(input_folder, 'frames.json'), 'r') as inp:
    #         frames = json.load(inp)
    #     with open(os.path.join(input_folder, 'labels.json'), 'r') as inp:
    #         labels = json.load(inp)

    #     candidates = set()
    #     candidates.add(args.timeline)

    #     timelines = {}
    #     for frame_id, label in labels.items():
    #         if 'timeline' not in label:
    #             continue
    #         symbol = label['timeline']
    #         if symbol in candidates:
    #             timelines.setdefault(symbol, []).append((frame_id, label['frame']))

    #     for sym, timeline in enumerate(timelines.values()):
    #         output_folder = f'{args.output_folder}/{os.path.basename(input_folder)}/{sym}'
    #         os.makedirs(output_folder, exist_ok=True)
    #         for i, frame_id in enumerate(timeline):
    #             frame_id = frame_id[0]
    #             print('=== rendering frame', frame_id, '===')
    #             frame = get_table_frame(shapes, frames, frame_id)
    #             with SvgRenderer() as renderer:
    #                 frame.render()
    #             x = args.x or 0
    #             y = args.y or 0
    #             width = args.width or labels[frame_id].get('width', 550)
    #             height = args.height or labels[frame_id].get('height', 400)
    #             scale = args.scale or 1
    #             svg = renderer.compile(width, height, x, y, scale)

    #             with open(os.path.join(output_folder, '%04d.svg' % i), 'w') as outp:
    #                 svg.write(outp, encoding='unicode')
