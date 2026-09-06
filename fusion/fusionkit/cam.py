"""Emit the machining setup that the G-code verifier will check against.

OneCNC sits in the middle of this pipeline and cannot be scripted, so the two
ends have to agree by contract instead. Fusion knows the part envelope and
where the datum is; it writes that out as `setup.json`, and when the posted
program comes back the verifier checks it against the same file. If the
operator picks up on a different corner, the stock numbers stop matching the
toolpath extents and the verifier says so.

Coordinates here are *work* coordinates: the origin is the part datum, Z0 is
the top of the stock, and Z is negative into the material.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field

from .units import point_mm

# Where the operator picks up the datum with an edge finder.
DATUM_MODES = ("center_top", "corner_top", "center_bottom")


@dataclass
class ToolSpec:
    number: int
    diameter: float
    description: str = ""
    flutes: int = 2
    stickout: float = 0.0
    corner_radius: float = 0.0
    length_offset: int | None = None


@dataclass
class MachiningSetup:
    machine: str = "haas_vf2"
    datum: str = "center_top"
    stock_allowance_xy: float = 2.0   # mm of material per side
    stock_allowance_top: float = 1.0  # mm on the top face
    stock_allowance_bottom: float = 0.0
    clearance: float = 5.0            # safe-Z above the stock
    expected_wcs: tuple[str, ...] = ("G54",)
    tools: list[ToolSpec] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def stock_from_bbox(bbox_min_mm, bbox_max_mm, setup: MachiningSetup) -> dict:
    """Work-coordinate stock block around the part's bounding box."""
    if setup.datum not in DATUM_MODES:
        raise ValueError(f"datum must be one of {DATUM_MODES}, got {setup.datum!r}")

    lx = bbox_max_mm[0] - bbox_min_mm[0]
    ly = bbox_max_mm[1] - bbox_min_mm[1]
    lz = bbox_max_mm[2] - bbox_min_mm[2]

    sx = lx + 2 * setup.stock_allowance_xy
    sy = ly + 2 * setup.stock_allowance_xy
    sz = lz + setup.stock_allowance_top + setup.stock_allowance_bottom

    if setup.datum == "center_top":
        x_min, x_max = -sx / 2, sx / 2
        y_min, y_max = -sy / 2, sy / 2
        z_top, z_bottom = 0.0, -sz
    elif setup.datum == "corner_top":
        x_min, x_max = 0.0, sx
        y_min, y_max = 0.0, sy
        z_top, z_bottom = 0.0, -sz
    else:  # center_bottom -- datum on the vise jaws
        x_min, x_max = -sx / 2, sx / 2
        y_min, y_max = -sy / 2, sy / 2
        z_top, z_bottom = sz, 0.0

    return {
        "x_min": round(x_min, 4), "x_max": round(x_max, 4),
        "y_min": round(y_min, 4), "y_max": round(y_max, 4),
        "z_bottom": round(z_bottom, 4), "z_top": round(z_top, 4),
        "clearance": setup.clearance,
    }


def setup_from_design(design, setup: MachiningSetup) -> dict:
    """Build the verifier's setup dict from the model's actual envelope."""
    bb = design.rootComponent.boundingBox
    lo, hi = point_mm(bb.minPoint), point_mm(bb.maxPoint)
    stock = stock_from_bbox(lo, hi, setup)
    return {
        "machine": setup.machine,
        "expected_wcs": list(setup.expected_wcs),
        "stock": stock,
        "tools": [
            {k: v for k, v in asdict(t).items() if v is not None}
            for t in setup.tools
        ],
        "_part_bbox_mm": {
            "min": [round(v, 4) for v in lo],
            "max": [round(v, 4) for v in hi],
            "size": [round(hi[i] - lo[i], 4) for i in range(3)],
        },
        "_datum": setup.datum,
        "_notes": setup.notes,
    }


def write_setup(setup_dict: dict, path: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(setup_dict, fh, indent=2)
    return path


def operator_sheet(setup_dict: dict, part_name: str) -> str:
    """The bit of paper that goes to the machine with the job.

    OneCNC needs a human to drive it, so this is the handoff: what stock to
    cut, where to pick up the datum, and which tools go in which pockets.
    """
    stock = setup_dict["stock"]
    bbox = setup_dict.get("_part_bbox_mm", {})
    size = bbox.get("size", [0, 0, 0])
    sx = stock["x_max"] - stock["x_min"]
    sy = stock["y_max"] - stock["y_min"]
    sz = stock["z_top"] - stock["z_bottom"]

    datum_text = {
        "center_top": "centre of the stock, top face  (X0 Y0 on the centre, Z0 on top)",
        "corner_top": "front-left corner, top face  (X0 Y0 at the corner, Z0 on top)",
        "center_bottom": "centre of the stock, bottom face  (Z0 on the vise jaws)",
    }[setup_dict.get("_datum", "center_top")]

    lines = [
        f"SETUP SHEET -- {part_name}",
        "=" * (16 + len(part_name)),
        "",
        f"Machine        {setup_dict['machine']}",
        f"Work offset    {', '.join(setup_dict['expected_wcs'])}",
        "",
        f"Finished part  {size[0]:.2f} x {size[1]:.2f} x {size[2]:.2f} mm",
        f"Stock needed   {sx:.2f} x {sy:.2f} x {sz:.2f} mm",
        f"Datum          {datum_text}",
        f"Clearance      Z{stock['z_top'] + stock['clearance']:.2f} "
        f"(rapid above this height only)",
        f"Deepest cut    Z{stock['z_bottom']:.2f} is the bottom of the stock -- "
        f"anything below it is the vise",
        "",
        "TOOLS",
    ]
    if setup_dict["tools"]:
        for t in setup_dict["tools"]:
            h = t.get("length_offset") or t["number"]
            lines.append(
                f"  T{t['number']:<3} H{h:<3} {t['diameter']:>6.2f} mm  "
                f"{t.get('flutes', '?')}FL  {t.get('description', '')}"
                + (f"   stickout {t['stickout']:g} mm" if t.get("stickout") else "")
            )
    else:
        lines.append("  (none defined)")

    if setup_dict.get("_notes"):
        lines += ["", "NOTES"] + [f"  - {n}" for n in setup_dict["_notes"]]

    lines += [
        "",
        "AFTER POSTING FROM ONECNC",
        "  python3 -m gcode.cli <posted>.nc -s setup.json",
        "  Do not run the program until it reports no crash findings.",
    ]
    return "\n".join(lines)
