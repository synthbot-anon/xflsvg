import os
from xml.etree import ElementTree

from gifski import Gifski
from wand.image import Image
from wand.color import Color

from .svgrenderer import SvgRenderer


class GifRenderer(SvgRenderer):
    def __init__(self, background="#0000"):
        super().__init__()
        self.background = Color(background)

    def compile(self, output_filename, framerate=1 / 24, *args, **kwargs):
        result = []
        width, height = map(round, self.get_frame_dimensions())
        xml_frames = super().compile(*args, **kwargs)

        g = None
        timestamp = 0
        width = None
        height = None

        for xml in xml_frames:
            svg = ElementTree.tostring(xml.getroot(), encoding="utf-8")
            image = Image(
                blob=svg, background=self.background, width=width, height=height
            )

            if g == None:
                width = image.width
                height = image.height
                g = Gifski(width, height)
                g.set_file_output(output_filename)

            rgba = image.make_blob("RGBA")
            g.add_frame_rgba(rgba, timestamp)
            timestamp += framerate

        if g:
            g.finish()


def splitext(path):
    folder, filename = os.path.split(path)
    if "." in filename:
        name, ext = filename.rsplit(".", maxsplit=1)
        return os.path.join(folder, name), f".{ext}"
    return path, ""
