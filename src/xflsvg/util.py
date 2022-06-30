from dataclasses import dataclass


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
        return f"Filter_{hash(self) & 0xFFFFFFFFFFFFFFFF:16x}"
