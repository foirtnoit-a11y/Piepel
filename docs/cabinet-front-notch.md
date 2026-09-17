# The tapered finger notch in a cabinet front

The `kastje` drawing dimensions a trapezoidal notch six ways — 65, 40, 20, R6,
R6 and 71.65 — and only four of those are independent. That redundancy is
useful: it means the drawing can be *checked*, not just read, and the check is
what this page is about. `machining/cabinet_slot.py` generates the program;
`tests/test_cabinet_slot.py` holds the check as an assertion.

## Reading the drawing

```
             <------------- 71.651 ------------->     over the R6 tangent points
             <---------- 65.000 ---------->           mouth, sharp corners
  __________/                              \__________    panel edge, Y0
           /                                \
          |                                  |       20.000 deep
           \________________________________/
            <---------- 40.000 ---------->            floor, sharp corners
```

The flank stands at

    phi = atan(depth / flare) = atan(20 / 12.5) = 57.9946 deg

to the panel edge, where `flare = (65 - 40) / 2 = 12.5`. A fillet of radius R
tangent to two lines meeting at an angle backs its tangent point off the sharp
corner by `R * tan(half the angle on the side the centre sits)`. At the mouth
the fillet centre is in the material, where the corner is `180 - phi`, and at
the floor it is in the waste, where the corner is `180 - phi` again from the
other side. Both give the same tangent length:

    t = R * tan(phi / 2) = 6 * tan(28.9973 deg) = 3.3255

so

    mouth span = 65 + 2t = 71.651
    floor span = 40 - 2t = 33.349

**71.651 against the 71.65 that was dimensioned.** That is the whole argument
for this reading of the drawing. Any other interpretation — a shallow face
recess, a section through a groove — has to explain that agreement to two
decimals as a coincidence. `test_mouth_span_matches_the_drawing` fails if the
geometry is ever changed in a way that breaks it.

What the drawing does **not** settle is the `3` at the top of the sheet. It is
not consumed by the notch, and the part of the drawing it dimensions is off the
edge of the photograph. The program cuts through a 12 mm panel; if that 3 turns
out to be a depth, the reading is wrong and `--depth`/`--thickness` are the two
flags that change it.

## Coordinates

Same convention as the rest of the repo, and one the operator can actually
find on the machine:

| | |
|---|---|
| **X0** | the notch centreline |
| **Y0** | the panel edge the notch opens on, panel extending into +Y |
| **Z0** | the **top face** of the panel, material into −Z |

Touch off on the edge, halve the notch, zero on the top face. Every number in
the program is then positive-Y and symmetric in X, which makes a transposition
obvious on sight.

## Why the compensation is computed here, not on the control

The programmed path is the tool *centre*, already offset by the cutter radius.
There is no `G41`/`G42` anywhere in the file, and the generator refuses to emit
a `D` word. Three reasons:

1. **It is checkable.** The verifier backplots what the file says. If the
   control is silently adding an offset from a table, the backplot and the
   machine are cutting two different parts and only one of them is inspected.
2. **Hobby controls differ.** Radius compensation needs a lead-in long enough
   to establish the offset and a lead-out to cancel it; how much, and whether
   the first move is compensated at all, varies by control and by version.
3. **The thing you actually want is easier.** The reason to reach for
   compensation on a job like this is a cutter that is not the diameter printed
   on it. Measure it and pass `--cutter-diameter 7.94`; the path moves by
   0.03 mm and the file still says exactly where the tool will go.

The inside corners are the constraint on cutter size: an R6 corner cannot be
cut by anything larger than Ø12, and the generator refuses rather than
quietly rounding the corner off. Ø12 would match R6 exactly; Ø8 cuts the
corners as programmed arcs of radius 2.

## The cut

An open contour — it starts clear of the panel edge, runs up one flank, across
the floor and back out the other side — so the cutter never plunges into
material. Passes are evened out rather than leaving a remainder skim, because
the leftover 0.4 mm pass is the one that grabs: full depth of engagement, no
chip to take.

The last pass goes `--spoilboard-bite` past the panel bottom, which is why
`setup.json` declares `z_bottom` at −12.5 rather than −12. That is honest, not
a fudge: the sacrificial board really is part of what the cutter goes through,
and declaring it that way keeps `CUT_BELOW_STOCK` meaningful for the mistake it
exists to catch. **There must physically be a sacrificial board under the
panel.**

The waste is a slug about 65 × 20 mm that comes free on the last pass. Two
tabs, one per flank, hold it: 8 mm long, 1.5 mm of material left under the cut,
ramped in and out over 2 mm rather than stepped. Break them out with a chisel
and sand the stubs. `--tab-width 0` turns them off, which is only sensible if
you are holding the slug some other way — a loose piece of MDF next to a
spinning cutter is how a router throws something.

## Feeds

From `feeds.py`, clamped by the machine profile, then programmed at 80% of
that:

```
$ python3 feeds.py mdf -d 8 -f 2 --ae 8 --ap 2.5 --stickout 30 \
      --max-rpm 25000 --max-feed 3000
```

The interesting line in that output is the clamp. The cut wants F5730 to hold a
0.16 mm chip; the machine tops out at F3000, so the real chip load falls to
0.084 mm and the program ships at 0.067. That is the honest shape of routing on
a desktop machine — **feed-limited, not power-limited** — and it is why these
cuts burn rather than break tools. If the wall comes out scorched, the answer
is more feed or fewer rpm, not less.

`feeds.py` also warns that this is full slotting at 180° of engagement, which
it is: the cutter is buried for the whole contour. Nothing to be done about it
on a cut this narrow, but it is the reason for the conservative stepdown.

## Running it

```bash
cd machining
python3 cabinet_slot.py \
    --thickness 12 --cutter-diameter 8 \
    -o ~/notch.nc --setup ~/notch_setup.json

python3 -m gcode.cli ~/notch.nc -s ~/notch_setup.json
```

A pre-generated pair for 12 mm MDF and a Ø8 cutter is in
`examples/cabinet-front-notch/`.

Before cycle start:

- **Sacrificial board under the panel**, and the panel clamped clear of the
  notch — nothing within 40 mm of the centreline on that edge.
- **Zero X on the notch centreline, Y on the panel edge, Z on the top face.**
  Getting Z from the spoilboard instead of the panel face puts every pass
  12 mm deep on the first plunge.
- **Dust extraction on.** MDF dust is abrasive and a respiratory hazard.
- **If WinPC-NC does not drive your spindle**, set the dial to the S value in
  the header by hand. The `S` word is ignored on a hand-dialled AMB, and the
  program will otherwise run at whatever the dial was left on.
- **Dry-run the first pass above the work** and watch the mouth: that is where
  the lead-in is, and it is the move that proves X and Y are where you think.

## What is not covered

The stock extents in `setup.json` assume a 600 × 400 panel with the notch
centred on the 600 edge. Nothing in the *cut* depends on that — it only feeds
the verifier's crash checks — but if your front is a different size, or the
notch is off-centre, edit the `stock` block or pass `--panel-width` /
`--panel-height`. The machine profile's travel limits are only checked when a
`wcs_offset` is supplied, which means the generator cannot tell you whether the
job fits on the table. Measure that yourself.
