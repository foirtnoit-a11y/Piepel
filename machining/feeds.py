"""Feeds, speeds and cutting-load estimates for solid-carbide end milling.

These are *starting points* computed from published general-purpose data, not
guarantees. Real numbers depend on your machine's rigidity, the holder, how
far the tool sticks out, coolant, and how worn the edge is. Take the output,
run it at 70-80% for the first pass, listen to the cut, then push it up.

Everything is metric internally: mm, mm/min, m/min, N, W.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# material data
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Material:
    key: str
    name: str
    vc: float           # surface speed, m/min, solid carbide, flood coolant
    vc_range: tuple[float, float]
    fz_frac: float      # feed per tooth as a fraction of cutter diameter
    kc: float           # specific cutting energy, J/mm^3 (== N/mm^2)
    notes: str = ""


MATERIALS: dict[str, Material] = {m.key: m for m in [
    Material("alu6061", "Aluminium 6061-T6", 400, (250, 700), 0.015, 0.70,
             "gummy without coolant; use 2-3 flute uncoated or ZrN, never a 4FL "
             "steel geometry in a deep slot"),
    Material("alu7075", "Aluminium 7075-T6", 400, (250, 600), 0.014, 0.80,
             "chips better than 6061; same tooling"),
    Material("brass", "Brass C360", 250, (150, 400), 0.012, 0.90,
             "grabby - use a zero/negative rake or slow the feed on entry"),
    Material("steel1018", "Mild steel 1018", 150, (110, 200), 0.010, 2.10,
             "long stringy chips; peck or use a chipbreaker on deep work"),
    Material("steel4140", "Alloy steel 4140 (~28 HRC)", 110, (80, 150), 0.008, 2.50,
             "coated carbide (TiAlN); watch for work hardening on light passes"),
    Material("ss304", "Stainless 304", 100, (70, 130), 0.008, 2.80,
             "work hardens fast - never dwell or rub; keep the feed up"),
    Material("castiron", "Grey cast iron", 130, (90, 180), 0.010, 1.50,
             "abrasive dust; run dry with air blast, not flood"),
    Material("ti64", "Titanium Ti-6Al-4V", 50, (30, 70), 0.006, 3.20,
             "heat goes into the tool; high pressure coolant, low radial "
             "engagement, never let it rub"),
    Material("h13", "Tool steel H13 (~45 HRC)", 80, (50, 110), 0.006, 3.00,
             "rigidity matters more than speed here"),
    Material("acetal", "Acetal / Delrin", 500, (300, 900), 0.020, 0.25,
             "melts if it rubs; single or two flute, sharp and polished"),
]}


# --------------------------------------------------------------------------
# inputs and results
# --------------------------------------------------------------------------
@dataclass
class Cut:
    """One milling operation to be evaluated."""

    material: str
    diameter: float          # cutter diameter, mm
    flutes: int
    ae: float                # radial depth of cut (stepover), mm
    ap: float                # axial depth of cut, mm
    stickout: float = 0.0    # flute tip to holder face, mm; 0 = skip deflection
    shank_diameter: float = 0.0  # defaults to cutter diameter
    is_carbide: bool = True
    max_rpm: float | None = None       # machine limit
    max_feed: float | None = None      # machine limit, mm/min
    max_power_kw: float | None = None  # spindle limit
    finishing: bool = False  # tightens the deflection budget


@dataclass
class FeedResult:
    cut: Cut
    material: Material
    rpm: float
    feed: float              # mm/min at the tool
    fz_commanded: float      # programmed feed per tooth, mm
    fz_target: float         # chip thickness we are aiming for, mm
    chip_thinning: float     # factor applied because ae < D/2
    mrr: float               # mm^3/min
    power_kw: float
    torque_nm: float
    tangential_force: float  # N
    deflection: float        # mm at the tip, 0 if stickout unknown
    engagement_deg: float
    warnings: list[str] = field(default_factory=list)
    clamped: list[str] = field(default_factory=list)

    @property
    def mrr_cc_min(self) -> float:
        return self.mrr / 1000.0

    def gcode(self) -> str:
        return f"S{self.rpm:.0f} F{self.feed:.0f}"

    def format(self) -> str:
        c, m = self.cut, self.material
        L = [
            f"{m.name}  |  {c.diameter:g} mm {c.flutes}FL "
            f"{'carbide' if c.is_carbide else 'HSS'}",
            f"  engagement    ae {c.ae:g} mm ({c.ae / c.diameter * 100:.0f}% D)   "
            f"ap {c.ap:g} mm ({c.ap / c.diameter:.2f} x D)   arc {self.engagement_deg:.0f} deg",
            f"  speed         S{self.rpm:.0f} rpm   (vc {self.rpm * math.pi * c.diameter / 1000:.0f} m/min)",
            f"  feed          F{self.feed:.0f} mm/min",
            f"  chip load     {self.fz_commanded:.4f} mm/tooth commanded"
            + (f"  ({self.chip_thinning:.2f}x thinning applied)" if self.chip_thinning > 1.001 else ""),
            f"  actual chip   {self.fz_target:.4f} mm at the cut",
            f"  removal       {self.mrr_cc_min:.1f} cm3/min",
            f"  power         {self.power_kw:.2f} kW at the cutter   "
            f"({self.torque_nm:.1f} Nm, {self.tangential_force:.0f} N tangential)",
        ]
        if self.deflection:
            L.append(f"  deflection    {self.deflection * 1000:.0f} um at the tip "
                     f"({c.stickout:g} mm stickout)")
        for c_ in self.clamped:
            L.append(f"  CLAMPED       {c_}")
        for w in self.warnings:
            L.append(f"  ! {w}")
        if m.notes:
            L.append(f"  note          {m.notes}")
        return "\n".join(L)


# --------------------------------------------------------------------------
# the calculation
# --------------------------------------------------------------------------
def chip_thinning_factor(diameter: float, ae: float) -> float:
    """Radial chip thinning.

    Below half-diameter engagement the tooth never reaches full chip
    thickness, so the programmed feed per tooth must be scaled *up* to keep
    the real chip the size you intended. This is why light-stepover HSM
    toolpaths run feeds that look absurd on paper.
    """
    if ae >= diameter / 2:
        return 1.0
    ratio = 1.0 - 2.0 * ae / diameter
    denom = math.sqrt(max(1.0 - ratio * ratio, 1e-9))
    return min(1.0 / denom, 4.0)  # cap: past ~4x you are trimming air


def engagement_angle(diameter: float, ae: float) -> float:
    """Arc of contact in degrees."""
    ae = min(max(ae, 0.0), diameter)
    return math.degrees(math.acos(max(-1.0, min(1.0, 1.0 - 2.0 * ae / diameter))))


def compute(cut: Cut) -> FeedResult:
    if cut.material not in MATERIALS:
        raise KeyError(f"unknown material {cut.material!r}; "
                       f"have: {', '.join(sorted(MATERIALS))}")
    mat = MATERIALS[cut.material]
    warnings: list[str] = []
    clamped: list[str] = []

    if cut.diameter <= 0 or cut.flutes < 1:
        raise ValueError("diameter must be positive and flutes at least 1")
    ae = min(cut.ae, cut.diameter)
    ap = cut.ap

    # HSS runs at roughly 40% of carbide surface speed.
    vc = mat.vc * (1.0 if cut.is_carbide else 0.4)
    rpm = vc * 1000.0 / (math.pi * cut.diameter)

    if cut.max_rpm and rpm > cut.max_rpm:
        clamped.append(f"speed limited to S{cut.max_rpm:.0f} by the machine "
                       f"(wanted {rpm:.0f}); surface speed drops to "
                       f"{cut.max_rpm * math.pi * cut.diameter / 1000:.0f} m/min")
        rpm = cut.max_rpm

    fz_target = mat.fz_frac * cut.diameter
    ctf = chip_thinning_factor(cut.diameter, ae)
    fz_cmd = fz_target * ctf
    feed = fz_cmd * cut.flutes * rpm

    if cut.max_feed and feed > cut.max_feed:
        clamped.append(f"feed limited to F{cut.max_feed:.0f} by the machine "
                       f"(wanted {feed:.0f}); real chip load falls to "
                       f"{cut.max_feed / (cut.flutes * rpm):.4f} mm/tooth")
        feed = cut.max_feed
        fz_cmd = feed / (cut.flutes * rpm)

    mrr = ae * ap * feed  # mm^3/min
    power_w = mat.kc * mrr / 60.0  # J/mm^3 * mm^3/s
    torque = power_w / (2 * math.pi * rpm / 60.0) if rpm else 0.0

    # Tangential force from the torque at the cutter radius.
    ft = power_w / (math.pi * cut.diameter * rpm / 60000.0) if rpm else 0.0

    deflection = 0.0
    if cut.stickout > 0:
        d = cut.shank_diameter or cut.diameter
        # Flutes remove material; the effective stiffness diameter of a fluted
        # section is roughly 0.8 of nominal.
        d_eff = 0.8 * d
        E = 600e3 if cut.is_carbide else 200e3  # MPa
        I = math.pi * d_eff ** 4 / 64.0  # mm^4
        # Resultant of tangential and radial force, radial ~ 0.5 x tangential.
        f_res = ft * math.hypot(1.0, 0.5)
        deflection = f_res * cut.stickout ** 3 / (3.0 * E * I)

    # ---- advisories ----
    if ae > 0.95 * cut.diameter:
        warnings.append("full slotting: chips have nowhere to go and the cutter is "
                        "engaged 180 deg. Halve ap, or trochoid the slot instead")
    if ap > 2.0 * cut.diameter and ae > 0.3 * cut.diameter:
        warnings.append(f"ap {ap:g} mm is over 2x diameter at {ae / cut.diameter * 100:.0f}% "
                        f"radial - this is a chatter recipe; drop ae to ~10% D for a "
                        f"deep peel cut")
    budget = 0.010 if cut.finishing else 0.050
    if deflection > budget:
        warnings.append(f"tip deflection {deflection * 1000:.0f} um exceeds the "
                        f"{'finishing' if cut.finishing else 'roughing'} budget of "
                        f"{budget * 1000:.0f} um - shorten the stickout, or drop ae")
    if cut.max_power_kw and power_w / 1000.0 > cut.max_power_kw:
        warnings.append(f"needs {power_w / 1000:.1f} kW but the spindle has "
                        f"{cut.max_power_kw:g} kW - reduce ap or ae")
    if cut.flutes >= 4 and cut.material.startswith("alu") and ae > 0.5 * cut.diameter:
        warnings.append("4+ flutes at heavy radial engagement in aluminium packs the "
                        "gullets; use 3 flutes or lighten ae")
    if ctf > 2.5:
        warnings.append(f"chip thinning is {ctf:.1f}x - the programmed feed is far above "
                        f"the nominal chip load. Correct, but verify the machine can "
                        f"actually hold it around corners")
    if not cut.is_carbide and mat.kc > 2.0:
        warnings.append("HSS in this material will not last; use coated carbide")

    return FeedResult(
        cut=cut, material=mat, rpm=rpm, feed=feed, fz_commanded=fz_cmd,
        fz_target=fz_target, chip_thinning=ctf, mrr=mrr, power_kw=power_w / 1000.0,
        torque_nm=torque, tangential_force=ft, deflection=deflection,
        engagement_deg=engagement_angle(cut.diameter, ae),
        warnings=warnings, clamped=clamped,
    )


def _main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        prog="feeds", description="Starting feeds and speeds for solid carbide end milling.",
        epilog="materials: " + ", ".join(sorted(MATERIALS)))
    ap.add_argument("material")
    ap.add_argument("-d", "--diameter", type=float, required=True, help="cutter diameter, mm")
    ap.add_argument("-f", "--flutes", type=int, default=3)
    ap.add_argument("--ae", type=float, help="radial depth, mm (default 40%% of D)")
    ap.add_argument("--ap", type=float, help="axial depth, mm (default 1x D)")
    ap.add_argument("--stickout", type=float, default=0.0)
    ap.add_argument("--hss", action="store_true")
    ap.add_argument("--finishing", action="store_true")
    ap.add_argument("--max-rpm", type=float)
    ap.add_argument("--max-feed", type=float)
    ap.add_argument("--max-power", type=float, help="spindle power, kW")
    a = ap.parse_args(argv)
    cut = Cut(material=a.material, diameter=a.diameter, flutes=a.flutes,
              ae=a.ae if a.ae is not None else 0.4 * a.diameter,
              ap=a.ap if a.ap is not None else a.diameter,
              stickout=a.stickout, is_carbide=not a.hss, finishing=a.finishing,
              max_rpm=a.max_rpm, max_feed=a.max_feed, max_power_kw=a.max_power)
    print()
    print(compute(cut).format())
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
