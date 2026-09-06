"""Named user parameters -- the contract between the script and the operator.

Nothing in a build script should contain a bare dimension. Every number goes
through here, which means it lands in Fusion's Change Parameters dialog with a
comment attached, and can be adjusted without touching Python.
"""
from __future__ import annotations

import adsk.core
import adsk.fusion


class Params:
    """Create-or-update wrapper over `design.userParameters`.

    Re-running a build script must not fail because the parameters already
    exist, so `set` updates in place when it finds one.
    """

    def __init__(self, design: "adsk.fusion.Design"):
        self._design = design
        self._params = design.userParameters

    def set(self, name: str, expression: str, comment: str = "") -> "adsk.fusion.Parameter":
        existing = self._params.itemByName(name)
        if existing is not None:
            existing.expression = expression
            if comment:
                existing.comment = comment
            return existing
        units = _units_of(expression)
        value = adsk.core.ValueInput.createByString(expression)
        return self._params.add(name, value, units, comment)

    def set_many(self, table: dict[str, tuple[str, str]]) -> None:
        """`{name: (expression, comment)}`, applied in order."""
        for name, (expression, comment) in table.items():
            self.set(name, expression, comment)

    def value_mm(self, name: str) -> float:
        """Evaluated value in mm. Raises if the parameter does not exist."""
        from .units import from_internal
        p = self._params.itemByName(name)
        if p is None:
            raise KeyError(f"no user parameter named {name!r}")
        return from_internal(p.value)

    def __contains__(self, name: str) -> bool:
        return self._params.itemByName(name) is not None

    def __getitem__(self, name: str) -> "adsk.fusion.Parameter":
        p = self._params.itemByName(name)
        if p is None:
            raise KeyError(name)
        return p

    def as_dict(self) -> dict[str, dict]:
        """Every user parameter, for the build report."""
        out = {}
        for i in range(self._params.count):
            p = self._params.item(i)
            out[p.name] = {"expression": p.expression, "value": p.value,
                           "units": p.unit, "comment": p.comment}
        return out


def _units_of(expression: str) -> str:
    """Guess the unit string Fusion wants from the expression itself."""
    for unit in ("mm", "cm", "in", "deg", "rad", "kg", "g"):
        if expression.strip().endswith(unit):
            return unit
    return ""
