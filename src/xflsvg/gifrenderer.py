import os
from xml.etree import ElementTree

from io import BytesIO
from gifski import Gifski
from tqdm import tqdm
from PIL import Image
import pyvips
from multiprocessing import Pool

from .svgrenderer import SvgRenderer, split_colors


def convert_to_rgba(args):
    xml, bg = args
    svg = ElementTree.tostring(xml.getroot(), encoding="utf-8")
    im = pyvips.Image.new_from_buffer(svg, options="")

    if bg[-1] != 0:
        background = im.new_from_image(bg)
        im = background.composite(im, "over")

    png = BytesIO(im.pngsave_buffer(compression=0))
    im = Image.open(png)
    return im.tobytes(), im.width, im.height


class GifRenderer(SvgRenderer):
    def __init__(self):
        super().__init__()
        # self.background = Color(background)

    def compile(
        self, output_filename, framerate=24, background="#0000", *args, **kwargs
    ):
        result = []
        # width, height = map(round, self.get_frame_dimensions())
        xml_frames = super().compile(*args, **kwargs)

        bg = split_colors(background)
        args = [(xml, bg) for xml in xml_frames]
        # with Pool(1) as p:
        # rgba_frames = p.map(convert_to_rgba, tqdm(args, 'rasterizing'))
        rgba_frames = map(convert_to_rgba, tqdm(args, "rasterizing"))

        rgba_frames = list(rgba_frames)
        _, width, height = rgba_frames[0]
        g = Gifski(width, height)
        g.set_file_output(output_filename)
        timestamp = 0

        for rgba, width, height in tqdm(rgba_frames, desc="creating gif"):
            # svg = ElementTree.tostring(xml.getroot(), encoding="utf-8")
            # image = Image(
            # blob=svg, background=self.background, width=width, height=height
            # )
            g.add_frame_rgba(rgba, timestamp)
            timestamp += 1 / framerate

        g.finish()


def splitext(path):
    folder, filename = os.path.split(path)
    if "." in filename:
        name, ext = filename.rsplit(".", maxsplit=1)
        return os.path.join(folder, name), f".{ext}"
    return path, ""
