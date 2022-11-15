import os
from xml.etree import ElementTree

from tqdm import tqdm
import pyvips
from multiprocessing import Pool

from .svgrenderer import SvgRenderer, split_colors
from .util import DEFAULT_POOL

import wand.color
import wand.image


def vips_convert_to_png(args):
    xml, bg = args
    svg = ElementTree.tostring(xml.getroot(), encoding="utf-8")
    im = pyvips.Image.new_from_buffer(svg, options="")

    background = im.new_from_image(bg)
    im = background.composite(im, "over")

    return im.write_to_buffer(".png"), im.width, im.height


def wand_convert_to_png(args):
    xml, bg, width, height = args
    svg = ElementTree.tostring(xml.getroot(), encoding="utf-8")
    im = wand.image.Image(blob=svg, background=bg, width=width, height=height)

    return im.make_blob("png"), im.width, im.height


def convert_svgs_to_pngs(xml_frames, background, pool, quiet=False):
    try:
        bg = split_colors(background)
        args = [(xml, bg) for xml in xml_frames]
        with pool() as p:
            png_frames = p.map(
                vips_convert_to_png, tqdm(args, "rasterizing", disable=quiet)
            )

    except ChildProcessError:
        print("everything is fine... trying again with wand")
        bg = background and wand.color.Color(background)
        first_frame = wand_convert_to_png((xml_frames[0], bg, None, None))
        _, width, height = first_frame

        args = [(xml, bg, width, height) for xml in xml_frames[1:]]
        with pool() as p:
            other_frames = p.map(
                wand_convert_to_png, tqdm(args, "rasterizing", disable=quiet)
            )

        png_frames = [first_frame, *other_frames]
        print("ok that worked")

    return png_frames


class PngRenderer(SvgRenderer):
    def __init__(self):
        super().__init__()

    def compile(
        self,
        output_filename=None,
        suffix=True,
        background="#0000",
        pool=DEFAULT_POOL,
        *args,
        **kwargs,
    ):
        result = []
        xml_frames = super().compile(*args, **kwargs)
        png_frames = convert_svgs_to_pngs(
            xml_frames, background, pool, quiet=kwargs.get("quiet", False)
        )

        for i, png_frame in enumerate(png_frames):
            png, width, height = png_frame
            result.append(png)

            if output_filename:
                name, ext = splitext(output_filename)
                sfx = suffix and "_f%04d" % i or ""
                with open(f"{name}{sfx}{ext}", "wb") as outp:
                    outp.write(png)

        return result


def splitext(path):
    folder, filename = os.path.split(path)
    if "." in filename:
        name, ext = filename.rsplit(".", maxsplit=1)
        return os.path.join(folder, name), f".{ext}"
    return path, ""
