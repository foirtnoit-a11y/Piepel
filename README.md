# Piepel

A Fusion-to-machine toolchain for designing parts in code and getting them cut
safely on a Fanuc/Haas VMC, with OneCNC XR5 doing the CAM in the middle.

## Why it is shaped like this

Three facts set the architecture, and it is worth being blunt about them:

1. **Fusion has a full Python API.** Sketches, features, parameters, materials,
   measurements, exports, and viewport renders are all reachable. Modelling can
   genuinely be automated.
2. **Fusion's Simulation workspace has no public API.** Studies cannot be set
   up or solved from a script. Anything claiming otherwise is guessing.
3. **OneCNC XR5 has no scripting, COM, or macro interface.** It stores jobs as
   `*.XFA` (the `.ONECNC` format arrived in XR7) and reads STEP, IGES, DXF,
   SAT and Parasolid. Its CAM cannot be driven from outside.

So OneCNC is a black box in the middle of the pipeline. This repo automates
hard on both sides of it and treats what comes out as untrusted input:

```
  Fusion (scripted)                OneCNC XR5              Verifier
  ─────────────────                ──────────              ────────
  parametric build                  import STEP            parse + backplot
  DFM checks           ──STEP──▶    pick toolpaths  ──NC──▶ 15 safety rules
  measure + render                  post to Fanuc          crash report
  emit setup.json  ───────────────────────────────────────▶ checked against
                        (the contract both ends agree on)
```

The setup contract is the load-bearing idea. Fusion knows the part envelope,
the datum, and the tool list, and writes them to `setup.json`. When the posted
program comes back, the verifier checks the toolpath against that same file. If
the operator picked up the datum on a different corner, or the stock was cut
short, the numbers stop agreeing and you find out at a terminal instead of at
the spindle.

## What is here

| Path | What it does |
|---|---|
| `machining/gcode/` | Parser, backplotter and safety linter for Fanuc/Haas NC |
| `machining/feeds.py` | Feeds, speeds, chip thinning, power and deflection |
| `machining/profiles/` | Machine definitions (Haas VF-2, generic Fanuc VMC) |
| `fusion/fusionkit/` | Parameters, renders, measurement, export, CAM contract |
| `fusion/models/` | Parametric part definitions — the script *is* the model |
| `fusion/scripts/PiepelBuild/` | The entry point you run from Fusion |
| `tests/` | 56 tests, stdlib `unittest` only |

## Using it

### Verify a posted program

The part of this you will use every day. Nothing to install — Python 3.9+.

```bash
cd machining
python3 -m gcode.cli /path/to/posted.nc -s /path/to/setup.json
```

It backplots the program, estimates cycle time, and reports anything dangerous:

```
  CRASH line    22  [CUT_BELOW_STOCK] tool reaches Z-14.000, which is 1.000 mm
                    below the bottom of the stock (Z-13.000)
                    fix: check the stock height and the part Z datum; if this is
                    a through feature, model the breakthrough allowance into the
                    stock definition
```

Exit code is non-zero when anything at `crash` or `error` level is found, so it
drops straight into a git hook or a CI job. `--json` gives machine-readable
output.

The rules, in the order they cost you money: rapids traversing the part below
the stock top, rapid plunges into material, cutting below the stock bottom,
missing `G43` tool length compensation, `H` not matching `T`, cutting with the
spindle stopped, motion before a work offset, over-speed and over-feed against
the machine profile, travel limits, arc geometry the control will reject, and
modal state (`G41`, `G80`, `G91`, coolant, spindle) left dangling at the end.

**What it does not do.** It has no material-removal model, so it does not know a
pocket has already been cleared — a rapid through air that used to be material
is still reported. It does not simulate holders, fixtures, or clamps. It does
not read macro-B (`#` variables, `IF`/`GOTO`); those sections are flagged as
unsimulated rather than quietly mis-simulated. It is a second pair of eyes, not
a replacement for single-blocking the first part.

### Feeds and speeds

```bash
python3 feeds.py alu6061 -d 12 -f 3 --ap 12 --ae 1.2 --stickout 40 --max-rpm 8100
```

Radial chip thinning is applied automatically, which is why light-stepover peel
cuts produce feeds that look wrong on paper and are not. Output includes
material removal rate, spindle power, and tip deflection from stickout — the
number that explains most chatter.

These are **starting points** from published general-purpose data, not
guarantees. Run the first pass at 70–80%, listen to it, then push it up.

### Model in Fusion

Symlink the script folder into Fusion's script directory so the code stays in
git:

```
Windows  %APPDATA%\Autodesk\Autodesk Fusion 360\API\Scripts\
macOS    ~/Library/Application Support/Autodesk/Autodesk Fusion 360/API/Scripts/
```

Then **Utilities → Add-Ins → Scripts and Add-Ins → PiepelBuild → Run**. Leave
that dialog open; re-running after an edit is two clicks.

One run builds the model, runs design-for-manufacture checks, measures it,
renders four fixed camera angles, exports STEP and STL, and writes `setup.json`
plus a printable setup sheet to `~/piepel_out/<model>/` (override with
`PIEPEL_OUT`).

The renders are the feedback channel. A script that runs without error has told
you nothing about whether the geometry is *right*; fixed camera angles let you
compare one revision against the last. The numbers in `report.json` — mass,
centre of mass, inertia, per-body bounding boxes, interference — usually matter
more. A picture cannot tell you the wall came out at 1.8 mm when you asked for
4 mm.

## Units

Fusion's API is **centimetre-native** regardless of what the document displays.
`ValueInput.createByReal(10)` is ten centimetres, and every `Point3D`,
`BoundingBox3D` and measurement result comes back in cm. This is the single
most common way generated CAD comes out 10× wrong.

Everything crossing into or out of this codebase is in millimetres, and
`fusion/fusionkit/units.py` is the only place the conversion happens.

## Stress analysis

Fusion's simulation cannot be scripted, so structural work splits three ways:

- **Closed-form calculations** catch most design errors in seconds — beam
  bending, stress concentration, bolt preload, press fits, buckling. Not yet
  in this repo.
- **Real FEA outside Fusion**: export STEP, mesh with gmsh, solve with
  CalculiX. Fully automatable, so it can run in a parameter sweep. Not yet in
  this repo.
- **Fusion's own Simulation workspace**, driven by hand with a prescribed
  setup. Slower, but it is the validated commercial solver.

Nothing here replaces a physical prototype for a part that carries load.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

Stdlib only, so it runs on a shop machine without `pip`. The most important
test is `test_clean_program_is_silent`: a correct program must produce zero
findings, or nobody will trust the tool that flags the dangerous ones.
