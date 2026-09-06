"""Command line entry point: `python -m gcode.cli part.nc --setup setup.json`."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .interp import run as backplot
from .lint import lint, SEVERITY_ORDER
from .machine import Machine, Setup, Stock, Tool
from .parser import parse, program_number


def load_setup(path: str | None, machine_name: str) -> Setup:
    machine = Machine.load(machine_name)
    if path is None:
        return Setup(machine=machine)
    data = json.loads(Path(path).read_text())
    if "machine" in data:
        machine = Machine.load(data["machine"])
    stock = Stock(**data["stock"]) if "stock" in data else None
    tools = {int(t["number"]): Tool(**t) for t in data.get("tools", [])}
    setup = Setup(
        machine=machine, stock=stock, tools=tools,
        expected_wcs=tuple(data.get("expected_wcs", ("G54",))),
    )
    if "wcs_offset" in data:
        setup.wcs_offset = tuple(data["wcs_offset"])  # type: ignore[attr-defined]
    return setup


def _hms(seconds: float) -> str:
    m, s = divmod(int(round(seconds)), 60)
    h, m = divmod(m, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d}:{s:02d}"


def report(nc_path: str, setup: Setup, as_json: bool = False, quiet_info: bool = False) -> int:
    text = Path(nc_path).read_text(errors="replace")
    blocks = parse(text)
    res = backplot(blocks, setup)
    diags = lint(blocks, res, setup)
    if quiet_info:
        diags = [d for d in diags if d.severity != "info"]

    counts = {s: sum(1 for d in diags if d.severity == s) for s in SEVERITY_ORDER}
    bounds = res.bounds()

    if as_json:
        print(json.dumps({
            "file": nc_path,
            "program": program_number(blocks),
            "machine": setup.machine.name,
            "units": res.units,
            "tools": res.tools_used,
            "cycle_seconds": round(res.cycle_seconds, 1),
            "cut_distance_mm": round(res.cut_distance, 2),
            "rapid_distance_mm": round(res.rapid_distance, 2),
            "bounds": [[round(v, 3) for v in b] for b in bounds] if bounds else None,
            "counts": counts,
            "diagnostics": [
                {"line": d.line_no, "rule": d.rule, "severity": d.severity,
                 "message": d.message, "fix": d.fix} for d in diags
            ],
        }, indent=2))
        return 1 if counts["crash"] or counts["error"] else 0

    name = Path(nc_path).name
    print(f"\n{name}  ->  {setup.machine.name}")
    print("=" * (len(name) + len(setup.machine.name) + 6))
    prog = program_number(blocks)
    print(f"  program        O{prog}" if prog else "  program        (no O number)")
    print(f"  units          {res.units}")
    print(f"  tools          {', '.join('T%d' % t for t in res.tools_used) or 'none'}")
    print(f"  cycle time     {_hms(res.cycle_seconds)}  (air + cut, no accel/decel)")
    print(f"  cutting        {res.cut_distance / 1000:.2f} m")
    print(f"  rapids         {res.rapid_distance / 1000:.2f} m")
    if bounds:
        lo, hi = bounds
        print(f"  extents  X {lo[0]:9.3f} .. {hi[0]:9.3f}")
        print(f"           Y {lo[1]:9.3f} .. {hi[1]:9.3f}")
        print(f"           Z {lo[2]:9.3f} .. {hi[2]:9.3f}")
    if setup.stock is None:
        print("  (no stock defined -- crash checks against the material are skipped)")
    if not hasattr(setup, "wcs_offset"):
        print("  (no wcs_offset given -- travel-limit check is skipped)")

    print()
    if not diags:
        print("  no findings.\n")
        return 0
    for d in diags:
        print("  " + str(d).replace("\n", "\n  "))
    print()
    summary = ", ".join(f"{counts[s]} {s}" for s in SEVERITY_ORDER if counts[s])
    print(f"  {summary}")
    if counts["crash"]:
        print("  DO NOT RUN THIS PROGRAM until the crash findings are resolved.")
    print()
    return 1 if counts["crash"] or counts["error"] else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="gcode-verify",
        description="Backplot and safety-check a posted Fanuc/Haas milling program.")
    ap.add_argument("nc_file", help="the posted .nc / .tap / .txt program")
    ap.add_argument("-m", "--machine", default="haas_vf2",
                    help="machine profile name or path (default: haas_vf2)")
    ap.add_argument("-s", "--setup", help="setup JSON with stock, tools, expected WCS")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--quiet-info", action="store_true", help="hide info-level findings")
    args = ap.parse_args(argv)
    return report(args.nc_file, load_setup(args.setup, args.machine), args.json, args.quiet_info)


if __name__ == "__main__":
    sys.exit(main())
