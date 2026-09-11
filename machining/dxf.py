"""Minimal DXF R12 writer, stdlib only.

R12 ASCII on purpose: it is the most widely readable CAD interchange format
there is, and OneCNC XR5 is from an era where newer entity types (LWPOLYLINE
and friends) are a gamble. Everything here is LINE, ARC and CIRCLE, which
every version of every CAD package has understood for thirty years.

A curve whose offset varies along its length cannot be expressed as arcs, so
it is emitted as a chain of LINE segments. Chordal error is the caller's
choice; `polyline` reports what it used.
"""
from __future__ import annotations

import math

# $INSUNITS: 1 = inches, 4 = millimetres.
_UNIT_CODES = {"mm": 4, "in": 1}


def _pair(code: int, value) -> str:
    if isinstance(value, float):
        return f"{code}\n{value:.6f}\n"
    return f"{code}\n{value}\n"


class Dxf:
    """Accumulates entities and renders a complete R12 file."""

    def __init__(self, units: str = "mm"):
        if units not in _UNIT_CODES:
            raise ValueError(f"units must be one of {sorted(_UNIT_CODES)}")
        self.units = units
        self._entities: list[str] = []
        self._layers: set[str] = set()
        self._pts: list[tuple[float, float]] = []

    # -- entities ----------------------------------------------------------
    def line(self, x1: float, y1: float, x2: float, y2: float, layer: str = "0") -> "Dxf":
        self._layers.add(layer)
        self._pts += [(x1, y1), (x2, y2)]
        self._entities.append(
            _pair(0, "LINE") + _pair(8, layer)
            + _pair(10, float(x1)) + _pair(20, float(y1)) + _pair(30, 0.0)
            + _pair(11, float(x2)) + _pair(21, float(y2)) + _pair(31, 0.0))
        return self

    def arc(self, cx: float, cy: float, radius: float,
            start_deg: float, end_deg: float, layer: str = "0") -> "Dxf":
        """Arc swept counter-clockwise from start_deg to end_deg."""
        self._layers.add(layer)
        # Bound the extents by the arc's endpoints and its centre; good enough
        # for $EXTMIN/$EXTMAX, which is only a viewport hint.
        for deg in (start_deg, end_deg):
            a = math.radians(deg)
            self._pts.append((cx + radius * math.cos(a), cy + radius * math.sin(a)))
        self._entities.append(
            _pair(0, "ARC") + _pair(8, layer)
            + _pair(10, float(cx)) + _pair(20, float(cy)) + _pair(30, 0.0)
            + _pair(40, float(radius))
            + _pair(50, float(start_deg % 360.0)) + _pair(51, float(end_deg % 360.0)))
        return self

    def circle(self, cx: float, cy: float, radius: float, layer: str = "0") -> "Dxf":
        self._layers.add(layer)
        self._pts += [(cx - radius, cy - radius), (cx + radius, cy + radius)]
        self._entities.append(
            _pair(0, "CIRCLE") + _pair(8, layer)
            + _pair(10, float(cx)) + _pair(20, float(cy)) + _pair(30, 0.0)
            + _pair(40, float(radius)))
        return self

    def polyline(self, points, layer: str = "0", closed: bool = False) -> "Dxf":
        """A chain of LINE segments through `points`."""
        pts = list(points)
        if len(pts) < 2:
            raise ValueError("a polyline needs at least two points")
        if closed and pts[0] != pts[-1]:
            pts.append(pts[0])
        for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
            self.line(x1, y1, x2, y2, layer)
        return self

    def text(self, x: float, y: float, content: str, height: float = 2.5,
             layer: str = "NOTES") -> "Dxf":
        """A note on its own layer, so it can be switched off before machining."""
        self._layers.add(layer)
        self._entities.append(
            _pair(0, "TEXT") + _pair(8, layer)
            + _pair(10, float(x)) + _pair(20, float(y)) + _pair(30, 0.0)
            + _pair(40, float(height)) + _pair(1, content))
        return self

    # -- output ------------------------------------------------------------
    @property
    def entity_count(self) -> int:
        return len(self._entities)

    def _extents(self) -> tuple[float, float, float, float]:
        if not self._pts:
            return (0.0, 0.0, 0.0, 0.0)
        xs = [p[0] for p in self._pts]
        ys = [p[1] for p in self._pts]
        return (min(xs), min(ys), max(xs), max(ys))

    def to_string(self) -> str:
        xmin, ymin, xmax, ymax = self._extents()
        header = (
            _pair(0, "SECTION") + _pair(2, "HEADER")
            + _pair(9, "$ACADVER") + _pair(1, "AC1009")
            + _pair(9, "$INSUNITS") + _pair(70, _UNIT_CODES[self.units])
            + _pair(9, "$EXTMIN") + _pair(10, xmin) + _pair(20, ymin) + _pair(30, 0.0)
            + _pair(9, "$EXTMAX") + _pair(10, xmax) + _pair(20, ymax) + _pair(30, 0.0)
            + _pair(0, "ENDSEC"))

        # A LAYER table keeps pickier importers from inventing their own.
        layers = sorted(self._layers) or ["0"]
        table = (_pair(0, "SECTION") + _pair(2, "TABLES")
                 + _pair(0, "TABLE") + _pair(2, "LAYER") + _pair(70, len(layers)))
        for i, name in enumerate(layers):
            table += (_pair(0, "LAYER") + _pair(2, name) + _pair(70, 0)
                      + _pair(62, (i % 7) + 1) + _pair(6, "CONTINUOUS"))
        table += _pair(0, "ENDTAB") + _pair(0, "ENDSEC")

        body = (_pair(0, "SECTION") + _pair(2, "ENTITIES")
                + "".join(self._entities) + _pair(0, "ENDSEC"))
        return header + table + body + _pair(0, "EOF")

    def write(self, path: str) -> str:
        with open(path, "w", encoding="ascii", errors="replace", newline="\r\n") as fh:
            fh.write(self.to_string())
        return path


def arc_segments(radius: float, sweep_deg: float, chordal_tol: float = 0.005) -> int:
    """Segments needed so a polygonal arc stays within `chordal_tol` of true.

    Chord error for a step angle t is r(1 - cos(t/2)), so invert that.
    """
    if radius <= 0 or sweep_deg == 0:
        return 1
    ratio = max(min(1.0 - chordal_tol / radius, 1.0), -1.0)
    step = 2.0 * math.acos(ratio)
    if step <= 0:
        return 1
    return max(int(math.ceil(math.radians(abs(sweep_deg)) / step)), 2)


def chordal_error(radius: float, sweep_deg: float, segments: int) -> float:
    if segments < 1 or radius <= 0:
        return 0.0
    step = math.radians(abs(sweep_deg)) / segments
    return radius * (1.0 - math.cos(step / 2.0))
