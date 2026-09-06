"""Unit handling for the Fusion API.

Fusion's API is centimetre-native regardless of what the document displays.
`ValueInput.createByReal(10)` is ten CENTIMETRES, and every geometric quantity
you read back -- BoundingBox3D points, Point3D coordinates, measurement
results, centre of mass -- is in cm. Volume comes back in cm^3 and mass in kg.

Every number crossing into or out of this codebase is in millimetres. These
helpers are the only place the conversion happens, so it can only be wrong in
one spot.
"""
from __future__ import annotations

CM_PER_MM = 0.1
MM_PER_CM = 10.0


def to_internal(mm: float) -> float:
    """mm -> Fusion internal cm. Use for createByReal()."""
    return mm * CM_PER_MM


def from_internal(cm: float) -> float:
    """Fusion internal cm -> mm. Use on anything read back from the API."""
    return cm * MM_PER_CM


def point_mm(point) -> tuple[float, float, float]:
    """An adsk Point3D (cm) as an (x, y, z) tuple in mm."""
    return (from_internal(point.x), from_internal(point.y), from_internal(point.z))


def expr(mm: float) -> str:
    """A unit-tagged expression string, which is always safer than a raw number.

    Passing this to ValueInput.createByString() makes the intent explicit and
    survives the document being set to inches.
    """
    return f"{mm} mm"


def volume_mm3(volume_cm3: float) -> float:
    return volume_cm3 * 1000.0


def mass_g(mass_kg: float) -> float:
    return mass_kg * 1000.0
