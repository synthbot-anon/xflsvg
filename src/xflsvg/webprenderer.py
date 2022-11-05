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


def vips_convert_to_webp(args):
    xml, bg = args
    svg = ElementTree.tostring(xml.getroot(), encoding="utf-8")
    im = pyvips.Image.new_from_buffer(svg, options="")

    background = im.new_from_image(bg)
    im = background.composite(im, "over")

    return im.write_to_buffer(".webp"), im.width, im.height


def wand_convert_to_webp(args):
    xml, bg, width, height = args
    svg = ElementTree.tostring(xml.getroot(), encoding="utf-8")

    background = wand.color.Color(bg)
    im = wand.image.Image(blob=svg, background=background, width=width, height=height)

    return im.make_blob("webp"), im.width, im.height


def convert_svgs_to_webps(xml_frames, background, pool):
    bg = split_colors(background)
    args = [(xml, bg) for xml in xml_frames]

    try:
        bg = split_colors(background)
        args = [(xml, bg) for xml in xml_frames]
        with pool() as p:
            webp_frames = p.map(vips_convert_to_webp, tqdm(args, "rasterizing"))

    except ChildProcessError:
        print("everything is fine... trying again with wand")
        first_frame = wand_convert_to_webp((xml_frames[0], background, None, None))
        _, width, height = first_frame

        args = [(xml, background, width, height) for xml in xml_frames[1:]]
        with pool() as p:
            other_frames = p.map(wand_convert_to_webp, tqdm(args, "rasterizing"))

        webp_frames = [first_frame, *other_frames]
        print("ok that worked")

    return webp_frames


class WebpRenderer(SvgRenderer):
    def __init__(self):
        super().__init__()

    def compile(
        self,
        output_filename,
        framerate=24,
        sequences=None,
        background=None,
        pool=None,
        suffix=True,
        *args,
        **kwargs,
    ):
        result = []
        xml_frames = super().compile(*args, **kwargs)
        webp_frames = convert_svgs_to_webps(xml_frames, background, pool)
        webp_images = [Image.open(BytesIO(x)) for x, _, _ in webp_frames]

        for seq in sequences:
            name, ext = splitext(output_filename)
            cur_path = f"{name}_f{seq[0]:04d}-{seq[-1]+1:04d}{ext}"
            cur_frames = [webp_images[i] for i in seq]

            cur_frames[0].save(
                cur_path,
                format="webp",
                append_images=cur_frames[1:],
                save_all=True,
                duration=round(1000 / framerate),
                loop=0,
                quality=100,
            )

        return webp_images

    def output_completed(self, output_path):
        return False


def splitext(path):
    folder, filename = os.path.split(path)
    if "." in filename:
        name, ext = filename.rsplit(".", maxsplit=1)
        return os.path.join(folder, name), f".{ext}"
    return path, ""
