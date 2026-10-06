# Chamfering a slot in a shaft with only 2D toolpaths

## The problem

A slot running lengthwise along a round shaft has a top edge that does not lie
in a plane. On a 50 mm shaft with a 12 mm slot:

```
edge sits at  sqrt(25² − 6²) = 24.2693 mm from the axis
           →  25 − 24.2693  =  0.7307 mm below the top of the shaft
```

Along the straight flanks that drop is constant, because the cylinder does not
change along its own axis. But at a rounded slot end the edge sweeps from
x = ±6 back to x = 0, and by the time it reaches the apex it has climbed the
full 0.7307 mm back to the top of the shaft.

A 2D contour runs at one Z. It cannot follow that.

## Why offsetting the path fixes it

A conical chamfer tool trades height for radius. For a flank angle **a**
(45° on a 90° included tool), moving the tool down by `dz` widens the cut by
`dz·tan(a)`, and moving it outward by `dx` does the same thing. The two knobs
are interchangeable.

So a Z error can be paid off in lateral offset. Hold Z constant, and offset the
path outward by exactly the local drop:

```
offset(x) = (R − √(R² − x²)) · tan(a)
```

With the virtual cone point programmed at `Z = −leg/tan(a)`, that offset is
zero at the apex — where the edge is already at full height — and rises to
`0.7307 · tan(a)` at the flanks.

## Why the shop shortcut is nearly right

The usual hand construction is: take the slot outline, widen it by twice the
maximum drop, keep the length and the corner radius. For the example that is a
**40 × 13.4614 rectangle with R6 corners**, run at Z−0.4.

That shape is the true outline *translated sideways*. Along an end arc its
useful component — the part along the outward normal — is `drop_max·cos θ`,
while the requirement is `drop(x)·tan(a)` where `x = (W/2)·cos θ`.

Since `drop(x) ≈ x²/2R`, the requirement is **quadratic** in x and the
translation is **linear**. They agree exactly at both ends of the sweep
(θ = 0° at the flank, θ = 90° at the apex) and diverge in between:

| θ | x | needed | hand gives | error |
|---:|---:|---:|---:|---:|
| 0° | 6.00 | 0.7307 | 0.7307 | 0.0000 |
| 30° | 5.20 | 0.5460 | 0.6204 | +0.0744 |
| **58°** | **3.18** | **0.2030** | **0.3531** | **+0.1501** |
| 80° | 1.04 | 0.0217 | 0.1083 | +0.0866 |
| 90° | 0.00 | 0.0000 | 0.0000 | 0.0000 |

Measured as the tool's **closest approach** to the edge, which is what decides
the cut. Measuring point-to-corresponding-point instead overstates it by about
23% and is wrong — the hand shape is not a true offset curve, so its nearest
point to a given edge point is not the correspondingly-numbered one.

**The error is always an over-cut.** The hand shape never sits closer to the
edge than required, anywhere. That is precisely why the shop method works: an
over-cut still removes the burr, and only an under-cut would leave one. For
deburring it is correct, not merely tolerable.

What varies is the chamfer *width* — roughly 1.4× wider partway round each end.
That matters only if the chamfer is cosmetic or carries a tolerance. On a pure
deburr it is invisible in function and barely visible by eye.

## The chamfer size cancels

This caught me out, so it is worth stating plainly. If you select the geometry
in CAM and type "0.2", the package computes the tool position itself. The
offset you add to the geometry is **not** chamfer compensation — it is purely
the Z-drop fix, and it is the same number whatever chamfer you ask for.

Setting `-c` on this tool therefore does not change the path. It only scales
the reported error against something, so you can see whether the variation
matters at the size you cut.

The one thing that must line up: **your part Z0 has to be the top of the
shaft**, because that is where the compensation is referenced from.

## Using it

```bash
cd machining
python3 chamfer.py -D 50 -w 12 -l 40 -c 0.4 --dxf chamfer_50x12.dxf
```

- `-D` shaft diameter, `-w` slot width, `-l` slot length, `-c` radial chamfer leg
- `-a` tool included angle (default 90)
- `-e round|square|open` — how the slot ends
- `--tip-diameter` if the tool has a flat at the point
- `--tol` chordal tolerance for the polyline (default 5 µm)

The report gives you both routes: the four numbers for the by-hand
construction, and the exact path as DXF.

### The DXF

Three layers:

- `CHAMFER_PATH` — the compensated curve. Select this for the toolpath.
- `SLOT_NOMINAL` — the true slot outline, for eyeballing the compensation.
  **Switch this off before picking geometry**, or you will chain the wrong
  contour.
- `SHAFT_OD` — the shaft circle, for orientation.

R12 ASCII with LINE/ARC/CIRCLE only, which is the most conservative thing any
CAD package will read.

### Chaining

The path is emitted endpoint-continuous, so it chains as a single closed
contour. The report states both numbers that decide this:

```
entity handover gap 0.000000 mm, loop closes to 0.000000 mm
```

They should be exactly zero, not merely small. If a CAM package cannot match
one entity's end to the next one's start it either breaks the chain or leaves a
gap in the chamfer, and a gap is the kind of thing you find on the part rather
than on the screen.

Square-ended slots need care here, because the flank offsets in X while the end
offsets in Y — at the sharp corner the two offsets never meet. The generator
inserts a quarter-circle round join of radius `drop(W/2)·tan(a)` centred on the
true corner, which is both what a constant-offset sweep does and what keeps the
chamfer width constant round the corner. Open-ended slots are two independent
chains; chain each separately.

### End styles

| Style | Geometry | Compensation |
|---|---|---|
| `round` | Cut with a W-diameter tool, semicircular ends | Exact on flanks, varies round the ends |
| `square` | Straight ends across the shaft | The straight end becomes a curve, and the sharp corners get round joins so the contour still chains. The hand shortcut is much worse here — error reaches the full `drop_max·tan(a)`, not a quarter of it |
| `open` | Slot runs off the end of the shaft | Two straight passes at fixed offset — exact, nothing to approximate |

## A second worked example

The 40 mm shaft with a 14 mm slot from the same family:

```
edge drop        1.2650 mm        (20 − √(20² − 7²))
hand rectangle   L × 16.5300, corner R7
worst over-cut   0.2338 mm at 56° into each end arc
```

The drop is 1.73× larger than the 50/12 case, because a proportionally wider
slot reaches further down the curve. At a 0.2 mm chamfer the compensation is
over six times the chamfer itself, which is the regime where it is worth
looking at the first part carefully — the tool warns about exactly this.

## Testing it on metal

The claim this tool makes is specific and checkable: the exact path holds one
chamfer width all the way round, and the hand construction widens by about half
again at one predictable place. `--measure` prints the stations to check.

```bash
python3 chamfer.py -D 50 -w 12 -l 40 -c 0.4 --measure
```

```
  theta      X        Y     surface    exact    by hand    diff
     0     6.000   14.000   -0.7307   0.5657    0.5657  +0.0000
    30     5.196   17.000   -0.5460   0.5657    0.6885  +0.1228
    60     3.000   19.196   -0.1807   0.5657    0.8269  +0.2612
    80     1.042   19.909   -0.0217   0.5657    0.7144  +0.1487
    90     0.000   20.000   -0.0000   0.5657    0.5657  +0.0000
```

X is from the slot centreline, Y from the slot centre, and the figure is the
chamfer **face** width — the dimension you can see on a comparator, related to
the radial leg by `face = leg / sin(a)`.

**Before cutting**, in OneCNC: import, switch `SLOT_NOMINAL` off, and confirm
the path chains as one closed contour. If it will not chain, the DXF is wrong
and nothing downstream matters. Then check the extents read ±6.731 in X and
±20.000 in Y for this example, and backplot.

**The decisive test** is an A/B on one piece of scrap: cut two identical slots,
chamfer one with the hand rectangle and one from the DXF, then measure both at
**θ = 60° (X3.000, Y19.196)**. The prediction is 0.827 against 0.566 — a 1.46×
difference, which is visible under a loupe and unambiguous on a comparator.
Same tool, same Z, same material, one variable.

Cheap substitutes if there is no comparator: a drop of layout dye on the edge
before the pass shows the witness band directly, and a photo of the slot end
next to a rule will show a visibly fatter chamfer partway round the corner if
the hand path is in use.

If the measured numbers do not match the table, the discrepancy is diagnostic
rather than just disappointing:

| What you measure | What it means |
|---|---|
| Uniformly too wide or narrow, everywhere | Z is off, or the tool offset is set on the flat rather than the virtual point (see `--tip-diameter`) |
| Correct on the flanks, wide partway round the ends | The hand construction is still in use — the DXF did not get chained, or `SLOT_NOMINAL` was selected |
| Correct at the ends, wrong on the flanks | The edge drop is wrong, so the shaft is not the diameter you entered — measure it |
| Wide on one side of the slot only | The slot is not centred on the shaft; the compensation assumes it is |
| Varies along a straight flank | Shaft deflection or runout in the setup, not geometry — nothing in the path can fix that |

## Limits

- **Lengthwise slots only.** A slot running *across* the shaft has both edge
  families curving, and no single-Z path can compensate both. That needs a 3D
  path or a rotary axis.
- **Sharp theoretical edges.** If the slot already carries a radius or a burr
  of unknown size, the chamfer lands where the real edge is, not where the
  model says.
- **Rigid setup assumed.** A 0.185 mm geometric error is meaningless next to a
  shaft deflecting in a vee-block, so clamp close to the cut.
- **DXF only, by design.** OneCNC posts the job, so this emits geometry and
  stops there. Verifying what OneCNC posted is a separate step —
  `python3 -m gcode.cli <posted>.nc -s setup.json`.
