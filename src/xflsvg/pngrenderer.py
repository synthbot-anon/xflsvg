import os
from xml.etree import ElementTree

from tqdm import tqdm
import pyvips
from multiprocessing import Pool

from .svgrenderer import SvgRenderer, split_colors


def convert_to_png(args):

    xml, bg = args
    svg = ElementTree.tostring(xml.getroot(), encoding="utf-8")
    im = pyvips.Image.new_from_buffer(svg, options="")

    background = im.new_from_image(bg)
    im = background.composite(im, "over")

    return im.write_to_buffer(".png")


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

        with pool() as p:
            png_frames = p.map(convert_to_png, tqdm(args, "rasterizing"))

        for i, png in enumerate(png_frames):
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
