"""Read measurable facts back out of the model.

The renders show whether the geometry looks right. This shows whether it *is*
right: mass, centre of mass, inertia, bounding box, per-body breakdown, and
interference between components. A picture can't tell you the wall came out at
1.8 mm when you asked for 4; this can.

All lengths are millimetres, mass in grams, volume in mm^3.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import adsk.core
import adsk.fusion

from .units import from_internal, mass_g, point_mm, volume_mm3

_ACCURACY = adsk.fusion.CalculationAccuracy.VeryHighCalculationAccuracy


def _bbox(entity) -> dict | None:
    bb = getattr(entity, "boundingBox", None)
    if bb is None:
        return None
    lo, hi = point_mm(bb.minPoint), point_mm(bb.maxPoint)
    return {
        "min": [round(v, 4) for v in lo],
        "max": [round(v, 4) for v in hi],
        "size": [round(hi[i] - lo[i], 4) for i in range(3)],
    }


def _physical(entity) -> dict | None:
    try:
        props = entity.getPhysicalProperties(_ACCURACY)
    except (AttributeError, RuntimeError):
        props = getattr(entity, "physicalProperties", None)
    if props is None:
        return None

    out = {
        "mass_g": round(mass_g(props.mass), 4),
        "volume_mm3": round(volume_mm3(props.volume), 3),
        "area_mm2": round(props.area * 100.0, 3),  # cm^2 -> mm^2
        "center_of_mass_mm": [round(v, 4) for v in point_mm(props.centerOfMass)],
    }
    try:
        ok, xx, yy, zz, xy, yz, xz = props.getXYZMomentsOfInertia()
        if ok:
            # kg*cm^2 -> g*mm^2
            k = 1000.0 * 100.0
            out["moments_of_inertia_g_mm2"] = {
                "xx": round(xx * k, 3), "yy": round(yy * k, 3), "zz": round(zz * k, 3),
                "xy": round(xy * k, 3), "yz": round(yz * k, 3), "xz": round(xz * k, 3),
            }
    except (AttributeError, ValueError, RuntimeError):
        pass
    return out


def body_report(body: "adsk.fusion.BRepBody") -> dict:
    d = {
        "name": body.name,
        "visible": body.isVisible,
        "material": body.material.name if body.material else None,
        "faces": body.faces.count,
        "edges": body.edges.count,
        "bounding_box_mm": _bbox(body),
    }
    phys = _physical(body)
    if phys:
        d.update(phys)
        bb = d["bounding_box_mm"]
        if bb:
            envelope = bb["size"][0] * bb["size"][1] * bb["size"][2]
            if envelope > 0:
                # How much of the bounding box is actually material. Low values
                # mean a thin or skeletal part; a sudden change between builds
                # means a feature failed.
                d["solidity"] = round(d["volume_mm3"] / envelope, 4)
    return d


def interference_report(design: "adsk.fusion.Design") -> list[dict]:
    """Which bodies overlap. Empty list is the answer you want."""
    bodies = adsk.core.ObjectCollection.create()
    for body in design.rootComponent.bRepBodies:
        if body.isVisible:
            bodies.add(body)
    for occ in design.rootComponent.allOccurrences:
        for body in occ.bRepBodies:
            if body.isVisible:
                bodies.add(body)
    if bodies.count < 2:
        return []
    try:
        inp = design.createInterferenceInput(bodies)
        inp.areCoincidentFacesIncluded = False
        results = design.analyzeInterference(inp)
    except (AttributeError, RuntimeError) as exc:
        return [{"error": f"interference check failed: {exc}"}]
    return [
        {
            "a": results.item(i).entityOne.name,
            "b": results.item(i).entityTwo.name,
            "volume_mm3": round(volume_mm3(results.item(i).interferenceBody.volume), 4)
            if results.item(i).interferenceBody else None,
        }
        for i in range(results.count)
    ]


def min_distance_mm(app, entity_a, entity_b) -> float:
    """Shortest distance between two entities, in mm.

    This is the clearance check. Pass two faces, edges, or bodies and you get
    the real gap rather than an impression of one.
    """
    return from_internal(app.measureManager.measureMinimumDistance(entity_a, entity_b).value)


def build_report(app, design: "adsk.fusion.Design", params=None,
                 extra: dict | None = None) -> dict:
    """Everything worth knowing about the current state of the model."""
    root = design.rootComponent
    bodies = [body_report(b) for b in root.bRepBodies]
    report = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "document": app.activeDocument.name if app.activeDocument else None,
        "design_type": "parametric" if design.designType ==
        adsk.fusion.DesignTypes.ParametricDesignType else "direct",
        "units": design.fusionUnitsManager.defaultLengthUnits,
        "root_component": root.name,
        "body_count": len(bodies),
        "bodies": bodies,
        "totals": {
            "mass_g": round(sum(b.get("mass_g", 0.0) for b in bodies), 4),
            "volume_mm3": round(sum(b.get("volume_mm3", 0.0) for b in bodies), 3),
        },
        "overall_bounding_box_mm": _bbox(root),
        "interference": interference_report(design),
        "timeline_features": design.timeline.count
        if design.designType == adsk.fusion.DesignTypes.ParametricDesignType else None,
    }
    if params is not None:
        report["parameters"] = params.as_dict()
    if extra:
        report.update(extra)
    return report


def write_report(report: dict, path: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    return path


def summarise(report: dict) -> str:
    """One screen of text, for the Fusion message box."""
    lines = [
        f"{report['body_count']} body(s), {report['totals']['mass_g']:.1f} g total",
    ]
    bb = report.get("overall_bounding_box_mm")
    if bb:
        lines.append("envelope {:.2f} x {:.2f} x {:.2f} mm".format(*bb["size"]))
    for b in report["bodies"]:
        lines.append(f"  {b['name']}: {b.get('mass_g', 0):.1f} g"
                     + (f", {b['material']}" if b.get("material") else "")
                     + (f", solidity {b['solidity']:.2f}" if "solidity" in b else ""))
    hits = [h for h in report.get("interference", []) if "error" not in h]
    lines.append(f"interference: {len(hits) or 'none'}")
    for h in hits:
        lines.append(f"  {h['a']} <-> {h['b']}  {h['volume_mm3']} mm3")
    return "\n".join(lines)
