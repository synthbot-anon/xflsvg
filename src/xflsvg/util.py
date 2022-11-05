from contextlib import contextmanager
from dataclasses import dataclass
import os
import math
import multiprocessing
import xml.etree.ElementTree as ET


@dataclass(frozen=True)
class ColorObject:
    mr: float = 1
    mg: float = 1
    mb: float = 1
    ma: float = 1
    dr: float = 0
    dg: float = 0
    db: float = 0
    da: float = 0

    def to_svg(color):
        # fmt: off
        matrix = (
            "{0} 0 0 0 {4} "
            "0 {1} 0 0 {5} "
            "0 0 {2} 0 {6} "
            "0 0 0 {3} {7}"
        ).format(
            color.mr, color.mg, color.mb, color.ma,
            color.dr, color.dg, color.db, color.da,
        )
        # fmt: on

        filter = ET.Element(
            "filter",
            {
                "id": color.id,
                "x": "-20%",
                "y": "-20%",
                "width": "140%",
                "height": "140%",
                "color-interpolation-filters": "sRGB",
            },
        )

        op1 = ET.SubElement(
            filter,
            "feColorMatrix",
            {
                "in": "SourceGraphic",
                "type": "matrix",
                "values": matrix,
            },
        )

        if color.da:
            op1.attrib["result"] = "result2"

            ET.SubElement(
                filter,
                "feColorMatrix",
                {
                    "in": "SourceGraphic",
                    "type": "matrix",
                    "values": "0 0 0 0 255 0 0 0 0 255 0 0 0 0 255 0 0 0 255 0",
                    "result": "result1",
                },
            )

            ET.SubElement(
                filter,
                "feComposite",
                {
                    "in2": "result1",
                    "in": "result2",
                    "operator": "in",
                    "result": "result3",
                },
            )

        return filter

    def __matmul__(self, other):
        return ColorObject(
            self.mr * other.mr,
            self.mg * other.mg,
            self.mb * other.mb,
            self.ma * other.ma,
            self.mr * other.dr + self.dr,
            self.mg * other.dg + self.dg,
            self.mb * other.db + self.db,
            self.ma * other.da + self.da,
        )

    def __rmul__(self, scalar):
        return ColorObject(
            self.mr * scalar,
            self.mg * scalar,
            self.mb * scalar,
            self.ma * scalar,
            self.dr * scalar,
            self.dg * scalar,
            self.db * scalar,
            self.da * scalar,
        )

    def __add__(self, other):
        return ColorObject(
            self.mr + other.mr,
            self.mg + other.mg,
            self.mb + other.mb,
            self.ma + other.ma,
            self.dr + other.dr,
            self.dg + other.dg,
            self.db + other.db,
            self.da + other.da,
        )

    def is_identity(self):
        return (
            self.mr == 1
            and self.mg == 1
            and self.mb == 1
            and self.ma == 1
            and self.dr == 0
            and self.dg == 0
            and self.db == 0
            and self.da == 0
        )

    @property
    def id(self):
        """Unique ID used to dedup SVG elements in <defs>."""
        result = f"Filter_{hash(self) & 0xFFFFFFFFFFFFFFFF:016x}"
        return result


def splitext(path):
    # This handles /.ext in a way that works better for xflsvg file specs than os.path.splitext.
    folder, filename = os.path.split(path)
    if "." in filename:
        name, ext = filename.rsplit(".", maxsplit=1)
        return os.path.join(folder, name), f".{ext}"
    return path, ""


def get_matching_path(input_root, output_root, input_path):
    relpath = os.path.relpath(input_path, input_root)
    return os.path.join(output_root, relpath)


@dataclass(frozen=True)
class InputFileSpec:
    path: str
    ext: str
    param: str
    relpath: str

    @classmethod
    def from_spec(cls, spec, root=None):
        if "[" in spec:
            param_start = spec.find("[") + 1
            assert spec[-1] == "]"

            param = spec[param_start:-1]
            spec = spec[: param_start - 1]
        else:
            param = None

        path, ext = splitext(spec)
        if os.path.exists(spec):
            path = spec

        # TODO: make this work on windows
        if root == None:
            if spec[0] == "/":
                root = "/"
            else:
                root = ""

        relpath = os.path.relpath(path, root)

        return InputFileSpec(path, ext.lower(), param, relpath)

    def subspec(self, path):
        relpath = os.path.relpath(path, self.path)
        return InputFileSpec(path, self.ext, self.param, relpath)

    @property
    def pathspec(self):
        return f"{os.path.normpath(self.path)}{self.ext}"


@dataclass(frozen=False)
class OutputFileSpec:
    path: str
    ext: str

    @classmethod
    def from_spec(cls, spec):
        path, ext = splitext(spec)
        return OutputFileSpec(path, ext)

    def matching_descendent(self, input):
        new_path = os.path.join(self.path, input.relpath)
        return OutputFileSpec(new_path, self.ext)


def pool(threads):
    threads = int(threads)
    if threads < 1:
        threads = None

    @contextmanager
    def _pool():
        try:
            with multiprocessing.Pool(threads) as pool:
                yield Mapper(pool)
        finally:
            pass

    return _pool


class Mapper:
    def __init__(self, pool):
        self.pool = pool

    def map(self, fn, args):
        original_pids = set([x.pid for x in self.pool._pool])
        future = self.pool.map_async(fn, args)
        while True:
            try:
                result = future.get(0.1)
                return result
            except multiprocessing.TimeoutError:
                current_pids = set([x.pid for x in self.pool._pool])
                if current_pids - original_pids:
                    raise ChildProcessError()
            except:
                raise ChildProcessError()


def merge_bounding_boxes(original, addition):
    if addition == None:
        return original

    if original == None:
        return addition

    return (
        min(original[0], addition[0]),
        min(original[1], addition[1]),
        max(original[2], addition[2]),
        max(original[3], addition[3]),
    )


def expand_bounding_box(original, pt):
    if original == None:
        return (*pt, *pt)

    return (
        min(original[0], pt[0]),
        min(original[1], pt[1]),
        max(original[2], pt[0]),
        max(original[3], pt[1]),
    )


def matmul(matrix, point):
    x = matrix[0] * point[0] + matrix[1] * point[1] + matrix[4]
    y = matrix[2] * point[0] + matrix[3] * point[1] + matrix[5]
    return (x, y)


def path_to_bounding_box(path, matrix):
    point_iter = iter(path)
    last_pt = matmul(matrix, next(point_iter))
    bbox = [*last_pt, *last_pt]
    last_command = "M"

    try:
        while True:
            point = next(point_iter)

            if isinstance(point[0], tuple):
                # Quadratic segment defined by a start, a control point, and an end.
                ctrl_pt = matmul(matrix, point[0])
                end_pt = matmul(matrix, next(point_iter))
                bbox_addition = quadratic_bounding_box(last_pt, ctrl_pt, end_pt)

                bbox = merge_bounding_boxes(bbox, bbox_addition)
                last_pt = end_pt
            else:
                # Line segment defined by a start and an end.
                point = matmul(matrix, point)
                bbox = merge_bounding_boxes(bbox, line_bounding_box(last_pt, point))
    except StopIteration:
        if path[0] == path[-1]:
            pass
        return bbox


def paths_to_bounding_box(paths, matrix):
    result = None
    for path, stroke_width in paths:
        box = path_to_bounding_box(path, matrix)
        box = stroke_bounding_box(box, stroke_width)
        result = merge_bounding_boxes(result, box)

    return result


def line_bounding_box(p1, p2):
    return (min(p1[0], p2[0]), min(p1[1], p2[1]), max(p1[0], p2[0]), max(p1[1], p2[1]))


def quadratic_bezier(p1, p2, p3, t):
    x = (1 - t) * ((1 - t) * p1[0] + t * p2[0]) + t * ((1 - t) * p2[0] + t * p3[0])
    y = (1 - t) * ((1 - t) * p1[1] + t * p2[1]) + t * ((1 - t) * p2[1] + t * p3[1])
    return (x, y)


def quadratic_critical_points(p1, p2, p3):
    x_denom = p1[0] - 2 * p2[0] + p3[0]
    if x_denom == 0:
        x_crit = math.inf
    else:
        x_crit = (p1[0] - p2[0]) / x_denom

    y_denom = p1[1] - 2 * p2[1] + p3[1]
    if y_denom == 0:
        y_crit = math.inf
    else:
        y_crit = (p1[1] - p2[1]) / y_denom

    return x_crit, y_crit


def quadratic_bounding_box(p1, control, p2):
    t3, t4 = quadratic_critical_points(p1, control, p2)

    if t3 > 0 and t3 < 1:
        p3 = quadratic_bezier(p1, control, p2, t3)
    else:
        # Pick either the start or end of the curve arbitrarily so it doesn't affect
        # the max/min point calculation
        p3 = p1

    if t4 > 0 and t4 < 1:
        p4 = quadratic_bezier(p1, control, p2, t4)
    else:
        # Pick either the start or end of the curve arbitrarily so it doesn't affect
        # the max/min point calculation
        p4 = p1

    return (
        min(p1[0], p2[0], p3[0], p4[0]),
        min(p1[1], p2[1], p3[1], p4[1]),
        max(p1[0], p2[0], p3[0], p4[0]),
        max(p1[1], p2[1], p3[1], p4[1]),
    )


def stroke_bounding_box(box, width):
    return (
        box[0] - width / 2,
        box[1] - width / 2,
        box[2] + width / 2,
        box[3] + width / 2,
    )
