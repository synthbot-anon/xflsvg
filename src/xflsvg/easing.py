class Variable:
    def _wrap_io(fn):
        def wrapped_fn(self, other):
            if not callable(other):
                result = fn(self, lambda x: other)
            else:
                result = fn(self, other)
            return Variable(result)

        return wrapped_fn

    def __init__(self, fn=lambda x: x):
        self.fn = fn

    @_wrap_io
    def __add__(self, other):
        return lambda x: self(x) + other(x)

    @_wrap_io
    def __radd__(self, other):
        return lambda x: other(x) + self(x)

    @_wrap_io
    def __sub__(self, other):
        return lambda x: self(x) - other(x)

    @_wrap_io
    def __rsub__(self, other):
        return lambda x: other(x) - self(x)

    @_wrap_io
    def __mul__(self, other):
        return lambda x: self(x) * other(x)

    @_wrap_io
    def __rmul__(self, other):
        return lambda x: other(x) * self(x)

    def __call__(self, val):
        return self.fn(val)


class Point:
    def __init__(self, x=0, y=0) -> None:
        self.x = x
        self.y = y

    def __repr__(self):
        return f"{self.x, self.y}"


class Line:
    def __init__(self, p1, p2) -> None:
        self.p1 = p1
        self.p2 = p2

    def __call__(self, t):
        x = (self.p2.x - self.p1.x) * t + self.p1.x
        y = (self.p2.y - self.p1.y) * t + self.p1.y
        return Point(x, y)


def segments(points):
    return [Line(x, y) for x, y in zip(points[:-1], points[1:])]


class Bezier:
    def __init__(self, points):
        t = Variable()
        while len(points) > 1:
            points = [x(t) for x in segments(points)]
        self.eqn = points[0]

    def __call__(self, t):
        return Point(self.eqn.x(t), self.eqn.y(t))


class BezierPath:
    def __init__(self, points):
        self.curves = []
        self.pts = []
        # every set of 4 points forms a bezier curve through the outer 2 points
        # so each intermediate point introduces 3 curve "points"
        for i in range(0, len(points) - 2, 3):
            self.curves.append(Bezier(points[i : i + 4]))
            self.pts.append(points[i])
        self.pts.append(points[-1])

    def __call__(self, t):
        if len(self.curves) == 1:
            return self.curves[0](t)

        for i in range(len(self.pts)):
            if self.pts[i].x >= t:
                break

        if self.pts[i].x == t:
            return self.pts[i]

        frac = (t - self.pts[i - 1].x) / (self.pts[i].x - self.pts[i - 1].x)
        return self.curves[i - 1](frac)


def classicEase(intensity):
    delta = (100 - intensity) / 300
    return BezierPath(
        [
            Point(0, 0),
            Point(1 / 3, delta),
            Point(2 / 3, 1 / 3 + delta),
            Point(1, 1),
        ]
    )


"""
Animate might clip some of these between 0 and 1. This isn't handled at the
moment. Clipping would affect the back* and elastic* eases.
"""
customEases = {
    "none": classicEase(0),
    "quadIn": BezierPath(
        [Point(0, 0), Point(0.55, 0.085), Point(0.68, 0.53), Point(1, 1)]
    ),
    "cubicIn": BezierPath(
        [Point(0, 0), Point(0.55, 0.055), Point(0.675, 0.19), Point(1, 1)]
    ),
    "quartIn": BezierPath(
        [Point(0, 0), Point(0.895, 0.03), Point(0.685, 0.22), Point(1, 1)]
    ),
    "quintIn": BezierPath(
        [Point(0, 0), Point(0.755, 0.05), Point(0.855, 0.06), Point(1, 1)]
    ),
    "sineIn": BezierPath(
        [Point(0, 0), Point(0.47, 0), Point(0.745, 0.715), Point(1, 1)]
    ),
    "backIn": BezierPath(
        [Point(0, 0), Point(0.6, -0.28), Point(0.735, 0.045), Point(1, 1)]
    ),
    "circIn": BezierPath(
        [Point(0, 0), Point(0.6, 0.04), Point(0.98, 0.335), Point(1, 1)]
    ),
    "bounceIn": BezierPath(
        [
            Point(0, 0),
            Point(0.05, 0.035),
            Point(0.05, 0.035),
            Point(0.090909, 0),
            Point(0.2, 0.14),
            Point(0.2, 0.11),
            Point(0.27272728, 0),
            Point(0.5, 0.7917),
            Point(0.5, 0.375),
            Point(0.6363636364, 0),
            Point(0.8, 0.8712),
            Point(1, 1),
            Point(1, 1),
        ]
    ),
    "elasticIn": BezierPath(
        [
            Point(0, 0),
            Point(0.63, 0),
            Point(0, 0),
            Point(0.63, 0),
            Point(0.865, 0.25),
            Point(0.865, 0.25),
            Point(0.925, 0),
            Point(0.865, -0.5),
            Point(1, 0),
            Point(1, 1),
        ]
    ),
    "quadOut": BezierPath(
        [
            Point(0, 0),
            Point(0.25, 0.46),
            Point(0.45, 0.94),
            Point(1, 1),
        ]
    ),
    "cubicOut": BezierPath(
        [Point(0, 0), Point(0.215, 0.61), Point(0.355, 1), Point(1, 1)]
    ),
    "quartOut": BezierPath(
        [
            Point(0, 0),
            Point(0.165, 0.84),
            Point(0.44, 1),
            Point(1, 1),
        ]
    ),
    "quintOut": BezierPath(
        [
            Point(0, 0),
            Point(0.23, 1),
            Point(0.32, 1),
            Point(1, 1),
        ]
    ),
    "sineOut": BezierPath(
        [
            Point(0, 0),
            Point(0.39, 0.575),
            Point(0.565, 1),
            Point(1, 1),
        ]
    ),
    "backOut": BezierPath(
        [
            Point(0, 0),
            Point(0.175, 0.885),
            Point(0.32, 1.275),
            Point(1, 1),
        ]
    ),
    "circOut": BezierPath(
        [
            Point(0, 0),
            Point(0.075, 0.82),
            Point(0.165, 1),
            Point(1, 1),
        ]
    ),
    "bounceOut": BezierPath(
        [
            Point(0, 0),
            Point(0, 0),
            Point(0.2, 0.1288),
            Point(0.3636363636, 1),
            Point(0.5, 0.625),
            Point(0.5, 0.2083),
            Point(0.72727272, 1),
            Point(0.8, 0.89),
            Point(0.8, 0.86),
            Point(0.90909, 1),
            Point(0.95, 0.965),
            Point(0.95, 0.965),
            Point(1, 1),
        ]
    ),
    "elasticOut": BezierPath(
        [
            Point(0, 0),
            Point(0, 1),
            Point(0.145, 1.6),
            Point(0.225, 1),
            Point(0.26, 0.8),
            Point(0.26, 0.8),
            Point(0.38, 1),
            Point(1, 1),
            Point(0.38, 1),
            Point(1, 1),
        ]
    ),
    "quadInOut": BezierPath(
        [
            Point(0, 0),
            Point(0.455, 0.03),
            Point(0.515, 0.955),
            Point(1, 1),
        ]
    ),
    "cubicInOut": BezierPath(
        [
            Point(0, 0),
            Point(0.645, 0.045),
            Point(0.355, 1),
            Point(1, 1),
        ]
    ),
    "quartInOut": BezierPath(
        [
            Point(0, 0),
            Point(0.77),
            Point(0.175, 1),
            Point(1, 1),
        ]
    ),
    "quintInOut": BezierPath(
        [
            Point(0, 0),
            Point(0.86),
            Point(0.07, 1),
            Point(1, 1),
        ]
    ),
    "sineInOut": BezierPath(
        [
            Point(0, 0),
            Point(0.445, 0.05),
            Point(0.55, 0.95),
            Point(1, 1),
        ]
    ),
    "backInOut": BezierPath(
        [
            Point(0, 0),
            Point(0.68, -0.55),
            Point(0.265, 1.55),
            Point(1, 1),
        ]
    ),
    "circInOut": BezierPath(
        [
            Point(0, 0),
            Point(0.785, 0.135),
            Point(0.15, 0.86),
            Point(1, 1),
        ]
    ),
    "bounceInOut": BezierPath(
        [
            Point(0, 0),
            Point(0.025, 0.0175),
            Point(0.025, 0.0175),
            Point(0.0454545),
            Point(0.1, 0.07),
            Point(0.1, 0.055),
            Point(0.1363636),
            Point(0.25, 0.39585),
            Point(0.25, 0.1845),
            Point(0.3181818),
            Point(0.4, 0.4356),
            Point(0.5, 0.5),
            Point(0.5, 0.5),
            Point(0.5, 0.5),
            Point(0.6, 0.5644),
            Point(0.6818181818, 1),
            Point(0.75, 0.8125),
            Point(0.75, 0.60415),
            Point(0.86363636, 1),
            Point(0.9, 0.945),
            Point(0.9, 0.93),
            Point(0.954545, 1),
            Point(0.975, 0.9825),
            Point(0.975, 0.9825),
            Point(1, 1),
        ]
    ),
    "elasticInOut": BezierPath(
        [
            Point(0, 0),
            Point(0.33),
            Point(0, 0),
            Point(0.33),
            Point(0.4, -0.25),
            Point(0.6, 1.25),
            Point(0.67, 1),
            Point(1, 1),
            Point(0.67, 1),
            Point(1, 1),
        ]
    ),
}
