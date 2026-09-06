"""Render the model to PNG so the person who wrote the script can see it.

This is the feedback channel. A build script that runs without error has still
told you nothing about whether the geometry is right; a set of renders from
fixed camera angles tells you immediately, and because the angles are fixed
you can compare one revision against the last.
"""
from __future__ import annotations

import os

import adsk.core

# Named angles, rendered in this order.
STANDARD_VIEWS = ("iso", "front", "right", "top")

_ORIENTATIONS = {
    "iso": adsk.core.ViewOrientations.IsoTopRightViewOrientation,
    "iso_left": adsk.core.ViewOrientations.IsoTopLeftViewOrientation,
    "front": adsk.core.ViewOrientations.FrontViewOrientation,
    "back": adsk.core.ViewOrientations.BackViewOrientation,
    "left": adsk.core.ViewOrientations.LeftViewOrientation,
    "right": adsk.core.ViewOrientations.RightViewOrientation,
    "top": adsk.core.ViewOrientations.TopViewOrientation,
    "bottom": adsk.core.ViewOrientations.BottomViewOrientation,
}


def snapshot(app, path: str, view: str = "iso", width: int = 1600, height: int = 1200,
             fit: bool = True) -> str | None:
    """Save one render. Returns the path written, or None if the save failed.

    `viewport.camera` hands back a *copy*, so the orientation must be assigned
    back to the viewport or nothing happens. This trips up everyone once.
    """
    if view not in _ORIENTATIONS:
        raise KeyError(f"unknown view {view!r}; have: {', '.join(sorted(_ORIENTATIONS))}")
    vp = app.activeViewport
    camera = vp.camera
    camera.viewOrientation = _ORIENTATIONS[view]
    camera.isFitView = fit
    vp.camera = camera          # the assignment is what actually moves the camera
    vp.refresh()
    adsk.doEvents()             # let the redraw land before we grab the pixels

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    return path if vp.saveAsImageFile(path, width, height) else None


def snapshot_set(app, out_dir: str, prefix: str = "view",
                 views: tuple[str, ...] = STANDARD_VIEWS,
                 width: int = 1600, height: int = 1200) -> dict[str, str]:
    """Render the standard angles into `out_dir`. Returns {view: path}."""
    written = {}
    for view in views:
        path = os.path.join(out_dir, f"{prefix}_{view}.png")
        if snapshot(app, path, view, width, height):
            written[view] = path
    return written


def snapshot_section(app, out_dir: str, prefix: str = "view") -> str | None:
    """Render with the active section analysis, if the document has one.

    Fusion has no API to *create* a section analysis, so this only captures one
    you made by hand. Worth doing once on a part with internal features --
    a section view shows wall thickness that no external angle can.
    """
    design = adsk.fusion.Design.cast(app.activeProduct)
    if design is None or design.analyses.count == 0:
        return None
    return snapshot(app, os.path.join(out_dir, f"{prefix}_section.png"), "iso")
