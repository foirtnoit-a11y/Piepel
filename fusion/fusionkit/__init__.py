"""Helpers for parametric Fusion build scripts.

Deliberately no eager imports. `units` and `cam` are pure Python and must stay
importable outside Fusion so the setup contract they produce can be tested on
a normal machine; the rest (`params`, `views`, `report`, `exporter`) need the
adsk modules and only load inside Fusion.

    from fusionkit import cam, units          # anywhere
    from fusionkit import params, views       # inside Fusion only
"""

__all__ = ["units", "cam", "params", "views", "report", "exporter"]
