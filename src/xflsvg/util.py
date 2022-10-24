from dataclasses import dataclass
import os


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
