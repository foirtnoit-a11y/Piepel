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
| 30° | 5.20 | 0.5460 | 0.6328 | +0.0868 |
| **60°** | **3.00** | **0.1807** | **0.3653** | **+0.1847** |
| 80° | 1.04 | 0.0217 | 0.1269 | +0.1052 |
| 90° | 0.00 | 0.0000 | 0.0000 | 0.0000 |

The overshoot peaks at about `drop_max·tan(a)/4`, at roughly 60° into each end
arc. It is always an **over-cut**, which matters: you cannot fix it on a second
pass.

Whether that is a problem depends entirely on the chamfer you are cutting.
0.185 mm on a 1.5 mm chamfer is 12% and invisible. On a 0.4 mm deburr it is
46%, and the chamfer is visibly half again too wide at four places on the part.
That is why the tool reports the error as a fraction of the chamfer rather than
just in millimetres.

## Using it

```bash
cd machining
python3 chamfer.py -D 50 -w 12 -l 40 -c 0.4 \
    --dxf chamfer_50x12.dxf --gcode chamfer_50x12.nc
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

### End styles

| Style | Geometry | Compensation |
|---|---|---|
| `round` | Cut with a W-diameter tool, semicircular ends | Exact on flanks, varies round the ends |
| `square` | Straight ends across the shaft | The straight end becomes a curve; the hand shortcut is much worse here (error reaches the full `drop_max·tan(a)`, not a quarter of it) |
| `open` | Slot runs off the end of the shaft | Two straight passes at fixed offset — exact, nothing to approximate |

## Limits

- **Lengthwise slots only.** A slot running *across* the shaft has both edge
  families curving, and no single-Z path can compensate both. That needs a 3D
  path or a rotary axis.
- **Sharp theoretical edges.** If the slot already carries a radius or a burr
  of unknown size, the chamfer lands where the real edge is, not where the
  model says.
- **Rigid setup assumed.** A 0.185 mm geometric error is meaningless next to a
  shaft deflecting in a vee-block, so clamp close to the cut.
- The generated NC program is a convenience for checking the geometry against
  `gcode.cli`. If OneCNC is posting the job, use the DXF and let it post.
