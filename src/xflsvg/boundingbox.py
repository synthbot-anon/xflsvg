import math


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
