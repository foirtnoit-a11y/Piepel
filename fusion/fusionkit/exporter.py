"""Export geometry for the downstream toolchain.

STEP is the handoff to OneCNC XR5, which reads STEP, IGES, DXF, SAT and
Parasolid but has no scripting interface of its own -- so this is the last
point where anything is automated until the posted G-code comes back.
"""
from __future__ import annotations

import os

import adsk.core
import adsk.fusion


def _ensure_dir(path: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    return path


def export_step(design: "adsk.fusion.Design", path: str, component=None) -> str:
    """Write STEP. This is the file you import into OneCNC."""
    mgr = design.exportManager
    options = mgr.createSTEPExportOptions(_ensure_dir(path), component or design.rootComponent)
    mgr.execute(options)
    return path


def export_iges(design: "adsk.fusion.Design", path: str, component=None) -> str:
    """IGES fallback, for when a STEP import comes in with missing faces."""
    mgr = design.exportManager
    options = mgr.createIGESExportOptions(_ensure_dir(path), component or design.rootComponent)
    mgr.execute(options)
    return path


def export_stl(design: "adsk.fusion.Design", path: str, body=None,
               refinement: str = "high") -> str:
    """STL for printing or for meshing in the FEA pipeline."""
    mgr = design.exportManager
    options = mgr.createSTLExportOptions(body or design.rootComponent, _ensure_dir(path))
    options.meshRefinement = {
        "low": adsk.fusion.MeshRefinementSettings.MeshRefinementLow,
        "medium": adsk.fusion.MeshRefinementSettings.MeshRefinementMedium,
        "high": adsk.fusion.MeshRefinementSettings.MeshRefinementHigh,
    }[refinement]
    mgr.execute(options)
    return path


def export_all(design: "adsk.fusion.Design", out_dir: str, stem: str) -> dict[str, str]:
    """STEP for CAM, STL for meshing. Returns {format: path}."""
    return {
        "step": export_step(design, os.path.join(out_dir, f"{stem}.step")),
        "stl": export_stl(design, os.path.join(out_dir, f"{stem}.stl")),
    }
