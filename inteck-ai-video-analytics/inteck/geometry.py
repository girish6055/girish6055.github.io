"""Zone / line geometry helpers (pure Python, no shapely dependency)."""
from typing import List, Sequence, Tuple

Point = Tuple[float, float]
Polygon = Sequence[Point]
Line = Sequence[Point]


def point_in_polygon(point: Point, polygon: Polygon) -> bool:
    """Ray-casting test. Points exactly on an edge may test either way."""
    if not polygon or len(polygon) < 3:
        return False
    x, y = point
    inside = False
    n = len(polygon)
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if (yi > y) != (yj > y):
            x_cross = (xj - xi) * (y - yi) / ((yj - yi) or 1e-9) + xi
            if x < x_cross:
                inside = not inside
        j = i
    return inside


def bbox_anchor(bbox: Sequence[float], mode: str = "bottom_center") -> Point:
    """Representative point of a detection box.

    ``bottom_center`` approximates where a person stands, which is what zone
    membership should be judged on for floor-mounted cameras.
    """
    x1, y1, x2, y2 = bbox[:4]
    if mode == "center":
        return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)
    if mode == "top_center":
        return ((x1 + x2) / 2.0, y1)
    return ((x1 + x2) / 2.0, y2)


def side_of_line(point: Point, line: Line) -> float:
    """Signed cross product: >0 one side, <0 the other, 0 on the line."""
    (x1, y1), (x2, y2) = line[0], line[1]
    px, py = point
    return (x2 - x1) * (py - y1) - (y2 - y1) * (px - x1)


def crossing_direction(prev: Point, curr: Point, line: Line) -> str:
    """Returns 'positive', 'negative' or '' when the segment crosses ``line``."""
    a = side_of_line(prev, line)
    b = side_of_line(curr, line)
    if a == 0 or b == 0 or (a > 0) == (b > 0):
        return ""
    if not _segments_intersect(prev, curr, line[0], line[1]):
        return ""
    return "positive" if b > 0 else "negative"


def direction_label(sign: str, line: Line) -> str:
    """Maps the signed side to a human direction ('up'/'down'/'left'/'right')."""
    (x1, y1), (x2, y2) = line[0], line[1]
    dx, dy = x2 - x1, y2 - y1
    horizontal = abs(dx) >= abs(dy)
    if horizontal:
        return "down" if sign == "positive" else "up"
    return "left" if sign == "positive" else "right"


def _orientation(a: Point, b: Point, c: Point) -> int:
    val = (b[1] - a[1]) * (c[0] - b[0]) - (b[0] - a[0]) * (c[1] - b[1])
    if abs(val) < 1e-9:
        return 0
    return 1 if val > 0 else 2


def _segments_intersect(p1: Point, q1: Point, p2: Point, q2: Point) -> bool:
    o1 = _orientation(p1, q1, p2)
    o2 = _orientation(p1, q1, q2)
    o3 = _orientation(p2, q2, p1)
    o4 = _orientation(p2, q2, q1)
    return o1 != o2 and o3 != o4


def pairwise_clusters(points: Sequence[Point], radius: float) -> List[List[int]]:
    """Single-link clustering by distance; returns index groups."""
    n = len(points)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    r2 = radius * radius
    for i in range(n):
        for j in range(i + 1, n):
            dx = points[i][0] - points[j][0]
            dy = points[i][1] - points[j][1]
            if dx * dx + dy * dy <= r2:
                union(i, j)

    groups: dict = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


def scale_polygon(polygon: Polygon, sx: float, sy: float) -> List[Point]:
    return [(px * sx, py * sy) for px, py in polygon]


def polygon_bounds(polygon: Polygon) -> Tuple[int, int, int, int]:
    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    return int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))
