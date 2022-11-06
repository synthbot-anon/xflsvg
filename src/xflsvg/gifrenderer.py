import multiprocessing
import os
from xml.etree import ElementTree

from io import BytesIO
from gifski import Gifski
from tqdm import tqdm
from PIL import Image
import pyvips
import wand.image
import wand.color
from multiprocessing import Pool, current_process

from .svgrenderer import SvgRenderer, split_colors


def vips_convert_to_rgba(args):
    xml, bg = args
    svg = ElementTree.tostring(xml.getroot(), encoding="utf-8")
    im = pyvips.Image.new_from_buffer(svg, options="")

    background = im.new_from_image(bg)
    im = background.composite(im, "over")

    png = BytesIO(im.pngsave_buffer(compression=0))
    im = Image.open(png)
    return im.tobytes(), im.width, im.height


def wand_convert_to_rgba(args):
    xml, bg, width, height = args
    svg = ElementTree.tostring(xml.getroot(), encoding="utf-8")
    im = wand.image.Image(blob=svg, background=bg, width=width, height=height)

    return im.make_blob("RGBA"), im.width, im.height


class GifRenderer(SvgRenderer):
    def __init__(self):
        super().__init__()

    def compile(
        self,
        output_filename,
        framerate=24,
        sequences=None,
        background=None,
        pool=None,
        *args,
        **kwargs,
    ):
        result = []
        xml_frames = super().compile(*args, **kwargs)

        try:
            bg = split_colors(background)
            args = [(xml, bg) for xml in xml_frames]
            with pool() as p:
                rgba_frames = p.map(vips_convert_to_rgba, tqdm(args, "rasterizing"))

        except ChildProcessError:
            print("failed to rasterize with vips... trying again with wand")
            _, _, width, height = super().get_svg_box(
                kwargs.get("scale", 1), kwargs.get("padding", 0)
            )
            width = int(width)
            height = int(height)
            bg = background and wand.color.Color(background)

            args = [(xml, bg, width, height) for xml in xml_frames]
            with pool() as p:
                rgba_frames = p.map(wand_convert_to_rgba, tqdm(args, "rasterizing"))

        rgba_frames = list(rgba_frames)
        _, width, height = rgba_frames[0]

        for seq in sequences:
            g = Gifski(width, height)
            name, ext = splitext(output_filename)
            g.set_file_output(f"{name}_f{seq[0]:04d}-{seq[-1]+1:04d}{ext}")
            timestamp = 0

            for i in tqdm(seq, desc="creating gif"):
                rgba, width, height = rgba_frames[i]
                g.add_frame_rgba(rgba, timestamp)
                timestamp += 1 / framerate

            g.finish()


def splitext(path):
    folder, filename = os.path.split(path)
    if "." in filename:
        name, ext = filename.rsplit(".", maxsplit=1)
        return os.path.join(folder, name), f".{ext}"
    return path, ""
