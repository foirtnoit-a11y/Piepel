# Working in this repo

## What the pipeline is

Fusion (scripted) → STEP → OneCNC XR5 (manual) → posted NC → verifier.

OneCNC XR5 has **no scripting, COM, or macro API** — this was checked, not
assumed. It is a black box driven by a human. Do not write code that tries to
automate it, and do not suggest that automating it is possible.

Fusion's **Simulation workspace has no public API** either. Modelling, measuring,
rendering and exporting are scriptable; setting up and solving a study is not.

## Non-negotiables

- **Millimetres everywhere.** Fusion's API is centimetre-native — `Point3D`,
  `BoundingBox3D`, measurement results and `createByReal()` are all cm.
  `fusion/fusionkit/units.py` is the *only* place that conversion may happen.
  Prefer `ValueInput.createByString("12 mm")` over `createByReal`.
- **No bare dimensions in build scripts.** Every number is a named user
  parameter with a comment, declared in the model's `PARAMETERS` dict.
- **Select geometry by property, never by index.** `body.edges.item(7)` breaks
  the moment a feature is added upstream. Filter by geometry — see
  `_fillet_vertical_edges` in `fusion/models/bracket.py`.
- **A clean program must lint silently.** False positives destroy the tool's
  value faster than missed findings. Any new rule needs both a test that it
  fires and confidence it stays quiet on `tests/fixtures/good_bracket.nc`.
- **Stdlib only.** No third-party dependencies. This has to run on a shop
  machine with no `pip`, and `unittest` is the test runner.

## Conventions

- Work coordinates: **Z0 is the top of the stock**, material goes into −Z, and
  X0 Y0 is wherever `MachiningSetup.datum` says. Models are built to match, so
  model coordinates *are* work coordinates.
- Lint severities mean something specific: `crash` will break something,
  `error` will alarm the control or produce a wrong part, `warn` is legal but
  worth a second look, `info` is housekeeping. Do not inflate them.
- Every diagnostic carries a `fix` that says what to change. A finding the
  operator cannot act on is noise.
- Machine limits live in `machining/profiles/*.json`, never hardcoded.

## Engineering claims

Feeds, speeds and cutting-force numbers are checkable — the aluminium power
figure is cross-checked against Machinery's Handbook unit power (~0.25 hp per
in³/min) in `tests/test_feeds.py`. If you change the material table, keep that
test honest rather than adjusting it to fit.

Never present calculated numbers as guarantees. They are starting points, and
the README says so.

## Testing

```bash
python3 -m unittest discover -s tests -v
```

Fusion-dependent modules (`params`, `views`, `report`, `exporter`) cannot be
tested here — there is no Fusion in CI. That is exactly why `units.py` and
`cam.py` are kept free of `adsk` imports: the setup contract they produce is
the thing the verifier depends on, so it must be testable. Keep it that way,
and keep `fusionkit/__init__.py` free of eager imports.

Syntax-check the Fusion modules with `python3 -m py_compile` before committing;
it catches everything except the API calls themselves.
