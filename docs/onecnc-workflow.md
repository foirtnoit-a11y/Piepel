# The OneCNC XR5 leg of the pipeline

OneCNC cannot be scripted, so this is the part a human drives. The goal is to
make that leg short, repeatable, and checkable at both ends.

## What was established

Searching Autodesk-adjacent and OneCNC's own release material turns up no API
reference, no macro language, and no COM/automation surface for XR5. What it
does have:

- **Import**: STEP, IGES, DXF/DWG, SAT, Parasolid, Rhino, SolidWorks.
- **Job format**: `*.XFA` for XR5. The `*.ONECNC` format replaced it in XR7.
- **Post**: configurable, Fanuc/Haas dialect among the supplied posts.

So the automation boundary is: STEP in, G-code out. Everything either side of
that is fair game.

## The loop

**1. Build in Fusion.** Run `PiepelBuild`. You get, in `~/piepel_out/<model>/`:

```
bracket.step        the file OneCNC imports
bracket.stl         for printing or meshing
setup.json          the contract the verifier checks against
setup_sheet.txt     print this and take it to the machine
report.json         mass, CoM, inertia, bounding boxes, interference
bracket_iso.png     four fixed camera angles
bracket_front.png
bracket_right.png
bracket_top.png
```

Read the DFM warnings in the dialog before going further. A corner radius
smaller than the cutter radius cannot be cut by any round tool, and finding
that out now is much cheaper than finding it out when the toolpath refuses to
generate.

**2. Import the STEP into OneCNC.** If a face comes in missing or a solid
arrives as a surface body, export IGES instead (`exporter.export_iges`) — that
mismatch is usually a tolerance disagreement between kernels, not a modelling
error.

**3. Set up the job to match `setup_sheet.txt`.** This is the step that has to
agree with Fusion:

- Stock size as printed on the sheet.
- Datum where the sheet says. `center_top` means X0 Y0 on the centre of the
  block and Z0 on the **top face** — not the vise jaws.
- Work offset G54 unless the sheet says otherwise.
- Tool numbers and their length offsets as listed. If OneCNC assigns a
  different `H` than the tool number, put it in the model's `ToolSpec` as
  `length_offset` so the verifier expects it and does not cry wolf.

**4. Pick your toolpaths and post.** Use the feeds calculator for the numbers:

```bash
cd machining
python3 feeds.py alu6061 -d 10 -f 3 --ap 10 --ae 1.0 --stickout 35 --max-rpm 8100
```

Enter those as starting values and run the first part at 70–80%.

**5. Verify before you cut.**

```bash
python3 -m gcode.cli ~/posted/bracket.nc -s ~/piepel_out/bracket/setup.json
```

Nothing at `crash` level, or you do not press cycle start. `error` findings will
alarm the control or make a wrong part. `warn` deserves ten seconds of thought.

## Reading the common findings

**`CUT_BELOW_STOCK` on a drilling cycle.** Almost always breakthrough on a
through hole: the drill point must exit the far side, so `Z` goes past the
stock bottom by the point length plus a millimetre or two. That is correct
machining and an incorrect stock definition — set `stock_allowance_bottom` in
`MachiningSetup` so the model accounts for the sacrificial gap, and make sure
there is physically a gap (parallels spaced clear of the holes, or a sacrificial
plate). The check is doing its job either way: it is asking whether anything
solid is under those holes.

**`RAPID_XY_IN_STOCK` inside a pocket.** The verifier has no material-removal
model, so it does not know the pocket was already cleared. Confirm that the
region really is air at that point in the program, then ignore it. This is the
one rule that produces defensible false positives.

**`H_TOOL_MISMATCH`.** Either the post is emitting the wrong offset — worth
fixing at the source, it will recur on every job — or the shop genuinely maps
tools to offsets differently, in which case record it in `ToolSpec.length_offset`.

**`ARC_RADIUS_MISMATCH`.** The post is emitting `I`/`J` values inconsistent with
the endpoints. The control will alarm. Tighten the arc filtering tolerance in
OneCNC's post configuration, or have it output arcs as line segments.

## Closing the loop back to the model

When the verifier finds something the *model* should have prevented — a pocket
too narrow for any sensible tool, a floor too thin to hold down, a hole too
close to an edge — add it to `machinability_warnings()` in the model file so it
is caught at build time on the next part, not at post time.

That is what makes this a pipeline rather than a pile of scripts: findings move
upstream.
