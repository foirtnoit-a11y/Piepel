"""Piepel build script -- run this from Fusion's Scripts and Add-Ins dialog.

One run does the whole upstream half of the pipeline:

    build the model  ->  DFM check  ->  measure  ->  render  ->  export STEP
                     ->  write setup.json + the operator's setup sheet

The STEP goes into OneCNC XR5 by hand (it has no scripting interface). When
the posted G-code comes back, setup.json is what the verifier checks it
against:

    python3 -m gcode.cli posted.nc -s <out>/setup.json

Install: put this folder in Fusion's script directory, or symlink it there --
  Windows  %APPDATA%\\Autodesk\\Autodesk Fusion 360\\API\\Scripts\\
  macOS    ~/Library/Application Support/Autodesk/Autodesk Fusion 360/API/Scripts/
then Utilities -> Add-Ins -> Scripts and Add-Ins -> PiepelBuild -> Run.
Leave the dialog open; re-running after an edit is two clicks.
"""
from __future__ import annotations

import os
import sys
import traceback

import adsk.core
import adsk.fusion

# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------
MODEL = "bracket"          # module name in fusion/models/
NEW_DOCUMENT = True        # build into a fresh document, so re-runs stay clean
RENDER = True
EXPORT = True
IMAGE_SIZE = (1600, 1200)

# Where everything lands. Override with the PIEPEL_OUT environment variable.
OUT_DIR = os.environ.get("PIEPEL_OUT") or os.path.join(
    os.path.expanduser("~"), "piepel_out")


def _repo_paths() -> str:
    """Add the repo's `fusion/` directory to sys.path and return it.

    realpath() matters: if this folder is symlinked into Fusion's script
    directory -- which is the sane way to work, since it keeps the code in git
    -- __file__ points at the link and the naive dirname walk lands in the
    wrong place.
    """
    here = os.path.dirname(os.path.realpath(__file__))          # .../scripts/PiepelBuild
    fusion_dir = os.path.dirname(os.path.dirname(here))          # .../fusion
    if fusion_dir not in sys.path:
        sys.path.insert(0, fusion_dir)
    return fusion_dir


def run(context):
    app = adsk.core.Application.get()
    ui = app.userInterface
    try:
        _repo_paths()

        # Imported after the path fix, and re-imported on every run so editing
        # a model file does not require restarting Fusion.
        import importlib
        from fusionkit import cam, params as params_mod, report as report_mod
        from fusionkit import exporter, views
        model = importlib.import_module(f"models.{MODEL}")
        for module in (cam, params_mod, report_mod, exporter, views, model):
            importlib.reload(module)

        if NEW_DOCUMENT:
            app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)

        design = adsk.fusion.Design.cast(app.activeProduct)
        if design is None:
            ui.messageBox("Switch to the Design workspace and run this again.")
            return
        design.designType = adsk.fusion.DesignTypes.ParametricDesignType

        out_dir = os.path.join(OUT_DIR, MODEL)
        os.makedirs(out_dir, exist_ok=True)

        # --- build ---
        params = params_mod.Params(design)
        model.build(design, params)

        # --- design for manufacture ---
        dfm = model.machinability_warnings(params) \
            if hasattr(model, "machinability_warnings") else []

        # --- measure ---
        report = report_mod.build_report(app, design, params, extra={"dfm_warnings": dfm})
        report_path = report_mod.write_report(report, os.path.join(out_dir, "report.json"))

        # --- render ---
        rendered = {}
        if RENDER:
            rendered = views.snapshot_set(app, out_dir, prefix=MODEL, width=IMAGE_SIZE[0],
                                          height=IMAGE_SIZE[1])

        # --- export + the CAM contract ---
        exported = {}
        sheet_path = ""
        if EXPORT:
            exported = exporter.export_all(design, out_dir, MODEL)
            machining = cam.MachiningSetup(
                machine="haas_vf2",
                datum="center_top",
                tools=[
                    cam.ToolSpec(1, params.value_mm("cutter_dia"), "roughing endmill",
                                 flutes=3, stickout=35.0),
                    cam.ToolSpec(2, params.value_mm("hole_dia"), "through-hole drill",
                                 flutes=2, stickout=45.0),
                ],
                notes=["model built by PiepelBuild; Z0 is the top face of the part"],
            )
            setup_dict = cam.setup_from_design(design, machining)
            cam.write_setup(setup_dict, os.path.join(out_dir, "setup.json"))
            sheet_path = os.path.join(out_dir, "setup_sheet.txt")
            with open(sheet_path, "w", encoding="utf-8") as fh:
                fh.write(cam.operator_sheet(setup_dict, MODEL))

        # --- tell the human what happened ---
        lines = [report_mod.summarise(report), ""]
        if dfm:
            lines.append("DESIGN FOR MANUFACTURE:")
            lines += [f"  ! {w}" for w in dfm]
            lines.append("")
        lines.append(f"output: {out_dir}")
        if rendered:
            lines.append(f"  renders   {len(rendered)} ({', '.join(sorted(rendered))})")
        if exported:
            lines.append(f"  exports   {', '.join(sorted(exported))}")
        if sheet_path:
            lines.append("  setup.json + setup_sheet.txt")
            lines.append("")
            lines.append("Next: import the STEP into OneCNC, post, then verify with")
            lines.append("  python3 -m gcode.cli <posted>.nc -s setup.json")
        lines.append(f"  report    {os.path.basename(report_path)}")
        ui.messageBox("\n".join(lines), f"PiepelBuild - {MODEL}")

    except Exception:  # noqa: BLE001 - Fusion swallows tracebacks otherwise
        if ui:
            ui.messageBox(f"PiepelBuild failed:\n\n{traceback.format_exc()}")


def stop(context):
    pass
