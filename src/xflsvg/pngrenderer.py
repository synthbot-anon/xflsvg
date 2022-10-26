import os
from xml.etree import ElementTree

from tqdm import tqdm
import pyvips
from multiprocessing import Pool

from .svgrenderer import SvgRenderer, split_colors

import wand.color
import wand.image


def vips_convert_to_png(args):
    xml, bg = args
    svg = ElementTree.tostring(xml.getroot(), encoding="utf-8")
    im = pyvips.Image.new_from_buffer(svg, options="")

    background = im.new_from_image(bg)
    im = background.composite(im, "over")

    return im.write_to_buffer(".png")


def wand_convert_to_png(args):
    xml, bg, width, height = args
    svg = ElementTree.tostring(xml.getroot(), encoding="utf-8")

    background = wand.color.Color(bg)
    im = wand.image.Image(blob=svg, background=background, width=width, height=height)

    return im.make_blob("png"), im.width, im.height


class PngRenderer(SvgRenderer):
    def __init__(self):
        super().__init__()

    def compile(
        self,
        output_filename=None,
        suffix=True,
        background="#0000",
        pool=None,
        *args,
        **kwargs,
    ):
        result = []
        xml_frames = super().compile(*args, **kwargs)

        bg = split_colors(background)
        args = [(xml, bg) for xml in xml_frames]

        try:
            bg = split_colors(background)
            args = [(xml, bg) for xml in xml_frames]
            with pool() as p:
                png_frames = p.map(vips_convert_to_png, tqdm(args, "rasterizing"))

        except ChildProcessError:
            print("failed to rasterize with vips... trying again with wand")
            first_frame = wand_convert_to_png((xml_frames[0], background, None, None))
            _, width, height = first_frame

            args = [(xml, background, width, height) for xml in xml_frames[1:]]
            with pool() as p:
                other_frames = p.map(wand_convert_to_png, tqdm(args, "rasterizing"))

            png_frames = [first_frame, *other_frames]

        for i, png_frame in enumerate(png_frames):
            png, width, height = png_frame
            result.append(png)

            if output_filename:
                name, ext = splitext(output_filename)
                sfx = suffix and "%04d" % i or ""
                with open(f"{name}{sfx}{ext}", "wb") as outp:
                    outp.write(png)

        return result


def splitext(path):
    folder, filename = os.path.split(path)
    if "." in filename:
        name, ext = filename.rsplit(".", maxsplit=1)
        return os.path.join(folder, name), f".{ext}"
    return path, ""
