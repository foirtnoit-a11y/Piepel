"""Worked example: a pocketed mounting plate, machinable in one 3-axis setup.

The philosophy here is that *the script is the parametric model*. Fusion's
parameter table is populated so the operator can nudge a dimension, but the
authoritative definition is this file -- change a number, re-run, get a fresh
body. That avoids the usual mess where a script-built model gets hand-edited
and the two versions drift apart.

Geometry convention, chosen to match the CAM datum:
    Z0 is the TOP face of the part, material goes downward into -Z.
    X0 Y0 is the centre of the plate.
So the model's coordinates are already the work coordinates the operator will
pick up, and the toolpath extents can be compared against them directly.
"""
from __future__ import annotations

import adsk.core
import adsk.fusion

from fusionkit.units import to_internal

# name -> (expression, comment shown in Fusion's parameter dialog)
PARAMETERS = {
    "plate_len":     ("120 mm", "overall X"),
    "plate_wid":     ("80 mm",  "overall Y"),
    "plate_thk":     ("12 mm",  "material thickness"),
    "pocket_len":    ("80 mm",  "pocket X"),
    "pocket_wid":    ("44 mm",  "pocket Y"),
    "pocket_depth":  ("6 mm",   "pocket depth from the top face"),
    "hole_dia":      ("6.8 mm", "tapping drill for M8"),
    "hole_inset":    ("10 mm",  "hole centre from each edge"),
    "corner_r":      ("6 mm",   "corner radius, outer and pocket"),
    "cutter_dia":    ("10 mm",  "roughing cutter this part is designed around"),
}


def _point(x_mm: float, y_mm: float, z_mm: float = 0.0) -> "adsk.core.Point3D":
    """Point3D from millimetres. The API wants centimetres."""
    return adsk.core.Point3D.create(to_internal(x_mm), to_internal(y_mm), to_internal(z_mm))


def _string(expression: str) -> "adsk.core.ValueInput":
    return adsk.core.ValueInput.createByString(expression)


def build(design: "adsk.fusion.Design", params) -> "adsk.fusion.BRepBody":
    """Create the plate. Returns the finished body."""
    params.set_many(PARAMETERS)
    root = design.rootComponent
    extrudes = root.features.extrudeFeatures

    L = params.value_mm("plate_len")
    W = params.value_mm("plate_wid")
    pl = params.value_mm("pocket_len")
    pw = params.value_mm("pocket_wid")
    inset = params.value_mm("hole_inset")
    hole_r = params.value_mm("hole_dia") / 2.0

    # --- plate: extruded DOWN from Z0 so the top face lands on the datum ---
    sk = root.sketches.add(root.xYConstructionPlane)
    sk.name = "plate outline"
    sk.sketchCurves.sketchLines.addTwoPointRectangle(_point(-L / 2, -W / 2), _point(L / 2, W / 2))
    plate_input = extrudes.createInput(
        sk.profiles.item(0), adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    plate_input.setDistanceExtent(False, _string("-plate_thk"))
    body = extrudes.add(plate_input).bodies.item(0)
    body.name = "plate"

    # --- pocket ---
    sk_pocket = root.sketches.add(root.xYConstructionPlane)
    sk_pocket.name = "pocket"
    sk_pocket.sketchCurves.sketchLines.addTwoPointRectangle(
        _point(-pl / 2, -pw / 2), _point(pl / 2, pw / 2))
    pocket_input = extrudes.createInput(
        sk_pocket.profiles.item(0), adsk.fusion.FeatureOperations.CutFeatureOperation)
    pocket_input.setDistanceExtent(False, _string("-pocket_depth"))
    extrudes.add(pocket_input)

    # --- four through holes ---
    sk_holes = root.sketches.add(root.xYConstructionPlane)
    sk_holes.name = "mounting holes"
    circles = sk_holes.sketchCurves.sketchCircles
    for sx in (-1, 1):
        for sy in (-1, 1):
            circles.addByCenterRadius(
                _point(sx * (L / 2 - inset), sy * (W / 2 - inset)), to_internal(hole_r))
    hole_profiles = adsk.core.ObjectCollection.create()
    for i in range(sk_holes.profiles.count):
        hole_profiles.add(sk_holes.profiles.item(i))
    hole_input = extrudes.createInput(
        hole_profiles, adsk.fusion.FeatureOperations.CutFeatureOperation)
    hole_input.setAllExtent(adsk.fusion.ExtentDirections.NegativeExtentDirection)
    extrudes.add(hole_input)

    _fillet_vertical_edges(root, body, "corner_r")
    return body


def _fillet_vertical_edges(root, body, radius_expression: str) -> None:
    """Round every Z-parallel edge.

    Picking edges by index is what makes generated CAD brittle -- add a feature
    and the numbering shifts. Selecting by geometry survives edits: an edge is
    a corner if it is a straight line running along Z.
    """
    verticals = adsk.core.ObjectCollection.create()
    for edge in body.edges:
        geom = edge.geometry
        if geom.objectType != adsk.core.Line3D.classType():
            continue
        direction = geom.startPoint.vectorTo(geom.endPoint)
        if abs(direction.x) < 1e-9 and abs(direction.y) < 1e-9:
            verticals.add(edge)
    if verticals.count == 0:
        return
    fillets = root.features.filletFeatures
    fillet_input = fillets.createInput()
    fillet_input.isRollingBallCorner = True
    fillet_input.edgeSetInputs.addConstantRadiusEdgeSet(
        verticals, adsk.core.ValueInput.createByString(radius_expression), True)
    fillets.add(fillet_input)


def machinability_warnings(params) -> list[str]:
    """Design-for-manufacture checks a 3-axis mill imposes on the geometry.

    Worth running before the STEP ever reaches OneCNC: these are the problems
    that otherwise surface as "the toolpath won't generate" an hour later.
    """
    out = []
    cutter_r = params.value_mm("cutter_dia") / 2.0
    corner_r = params.value_mm("corner_r")
    depth = params.value_mm("pocket_depth")
    thickness = params.value_mm("plate_thk")
    pocket_wid = params.value_mm("pocket_wid")
    hole_dia = params.value_mm("hole_dia")
    inset = params.value_mm("hole_inset")

    if corner_r < cutter_r:
        out.append(
            f"pocket corner radius {corner_r:g} mm is smaller than the cutter radius "
            f"{cutter_r:g} mm - a round tool cannot cut it. Open the radius to at least "
            f"{cutter_r:g} mm, or plan a smaller finishing tool")
    elif corner_r < cutter_r * 1.1:
        out.append(
            f"corner radius {corner_r:g} mm exactly matches the cutter; the tool will be "
            f"fully buried in every corner. Allow ~10% more ({cutter_r * 1.1:.1f} mm) so it "
            f"keeps moving")
    if depth >= thickness:
        out.append(f"pocket depth {depth:g} mm is not less than the {thickness:g} mm plate - "
                   f"this cuts through; make it a profile instead")
    elif thickness - depth < 1.5:
        out.append(f"only {thickness - depth:g} mm of floor left under the pocket; it will "
                   f"drum and chatter, and may bow when clamped")
    if depth > 4 * params.value_mm("cutter_dia"):
        out.append(f"pocket is {depth / params.value_mm('cutter_dia'):.1f}x cutter diameter "
                   f"deep - needs a long-reach tool, expect deflection")
    if hole_dia < 3.0 and hole_dia * 6 < thickness:
        out.append(f"{hole_dia:g} mm holes through {thickness:g} mm is over 6x diameter - "
                   f"peck deeply or use a gun drill")
    if inset < hole_dia:
        out.append(f"hole inset {inset:g} mm leaves less than one diameter of material to the "
                   f"edge; it will break out")
    if pocket_wid < params.value_mm("cutter_dia") * 1.2:
        out.append(f"pocket is only {pocket_wid:g} mm wide for a "
                   f"{params.value_mm('cutter_dia'):g} mm cutter - that is a slot, not a "
                   f"pocket; it will need a smaller tool or a trochoidal path")
    return out
