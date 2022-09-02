import os
from xml.etree import ElementTree

from wand.image import Image
from wand.color import Color

from .svgrenderer import SvgRenderer


class PngRenderer(SvgRenderer):
    def __init__(self, background="#0000"):
        super().__init__()
        self.background = Color(background)

    def compile(self, output_filename=None, suffix=True, *args, **kwargs):
        result = []
        xml_frames = super().compile(*args, **kwargs)

        for i, xml in enumerate(xml_frames):
            svg = ElementTree.tostring(xml.getroot(), encoding="utf-8")
            png = Image(blob=svg, background=self.background).make_blob("png")
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
