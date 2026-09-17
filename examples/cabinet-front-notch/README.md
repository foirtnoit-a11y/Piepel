# Cabinet front — tapered finger notch

Generated for the `kastje` drawing: 12 mm MDF, Ø8 two-flute cutter, StepCraft
M.1000 running WinPC-NC.

| File | |
|---|---|
| `notch_mdf12.nc` | the program — load this in WinPC-NC |
| `setup.json` | what the verifier checks it against |

```bash
cd machining
python3 -m gcode.cli ../examples/cabinet-front-notch/notch_mdf12.nc \
    -s ../examples/cabinet-front-notch/setup.json
```

Regenerate with different numbers:

```bash
python3 cabinet_slot.py --cutter-diameter 7.94 --thickness 18 \
    -o notch.nc --setup setup.json
```

**Zero X on the notch centreline, Y on the panel edge, Z on the top face of
the panel.** There must be a sacrificial board underneath: the last pass goes
0.5 mm past the panel. Two tabs hold the waste slug — break them out by hand.

Full notes, and the derivation of the 71.65, are in
[docs/cabinet-front-notch.md](../../docs/cabinet-front-notch.md).
