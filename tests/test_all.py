#!/usr/bin/env python3
"""Full test suite for printstrength.

Geometry is checked against closed-form section properties; the G-code path
is checked against fixtures whose material volume is known exactly.
"""
import sys, os, math, json, subprocess, tempfile, shutil
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import printstrength as ps

FAILS = []
GROUP = [""]

def group(name):
    GROUP[0] = name
    print(f"\n{name}")

def check(label, got, want, tol=0.02):
    err = abs(got - want) / abs(want) if want else abs(got)
    ok = err <= tol
    print(f"  {'PASS' if ok else 'FAIL'}  {label:<40} {got:>12.4f} vs {want:>12.4f}")
    if not ok: FAILS.append(f"{GROUP[0]}: {label}")

def check_eq(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label:<40} {str(got):>12} vs {str(want):>12}")
    if not ok: FAILS.append(f"{GROUP[0]}: {label}")

def check_true(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f"   {detail}" if detail else ""))
    if not cond: FAILS.append(f"{GROUP[0]}: {label}")

FIL_A = math.pi * (1.75 / 2) ** 2

# ---------------------------------------------------------------- geometry
group("STL section properties vs closed form")
m = ps.load_stl(f"{HERE}/cube20.stl")
check("cube volume", m.volume(), 8000)
s = min(ps.slice_mesh(m, "z", 200), key=lambda q: q.area)
check("cube min Z area", s.area, 400)
check("cube I", s.I1, 20**4/12)
check("cube section modulus", s.S1, 20**4/12/10)

m = ps.load_stl(f"{HERE}/bar40x10x5.stl")
s = min(ps.slice_mesh(m, "x", 300), key=lambda q: q.area)
check("bar min X area", s.area, 50)
check("bar I weak axis", min(s.I1, s.I2), 10*5**3/12)
check("bar I strong axis", max(s.I1, s.I2), 5*10**3/12)

m = ps.load_stl(f"{HERE}/notched.stl")
s = min(ps.slice_mesh(m, "x", 400), key=lambda q: q.area)
check("notch throat area", s.area, 20)
check_true("notch found near x=20", 17 < s.pos < 23, f"x={s.pos:.2f}")

m = ps.load_stl(f"{HERE}/tube.stl")
check("tube volume", m.volume(), math.pi*(100-49)*30, tol=0.01)
s = min(ps.slice_mesh(m, "z", 100), key=lambda q: q.area)
check("tube annulus area", s.area, math.pi*(100-49), tol=0.01)
check("tube annulus I", s.I1, math.pi*(10**4-7**4)/4, tol=0.01)

# ---------------------------------------------------------------- g-code
group("G-code parsing")
m = ps.parse_gcode(f"{HERE}/solid20.gcode")
check_eq("layer count", len(m.layers), 50)
check("part volume (20x20x10 solid)", m.total_volume, 20*20*10, tol=0.03)
lo_x, lo_y, hi_x, hi_y = m.bbox()
check("bbox X", hi_x-lo_x, 20.0, tol=0.03)
check("bbox Y", hi_y-lo_y, 20.0, tol=0.03)

m2 = ps.parse_gcode(f"{HERE}/cura_abs.gcode")
check_eq("absolute-E layer count matches", len(m2.layers), len(m.layers))
check("absolute-E volume matches relative-E", m2.total_volume, m.total_volume, tol=0.001)

group("G-code: arcs and exclusions")
m = ps.parse_gcode(f"{HERE}/arc.gcode")
check("G2/G3 arc volume", m.total_volume, 2*math.pi*10*0.45*0.2, tol=0.001)
lo_x, lo_y, hi_x, hi_y = m.bbox()
check("arc bbox", hi_x-lo_x, 20.45, tol=0.01)

m = ps.parse_gcode(f"{HERE}/support.gcode")
check("part volume excludes support/skirt", m.total_volume, 3*10*0.45*0.2, tol=0.001)
check("support+skirt counted separately", m.skipped_volume, 6*10*0.45*0.2, tol=0.001)
lo_x, lo_y, hi_x, hi_y = m.bbox()
check("bbox ignores support/skirt", hi_y-lo_y, 0.45, tol=0.02)

# ---------------------------------------------------------------- physics
group("Structural results")
opts = dict(material=dict(name="PLA", props=dict(ps.FDM_MATERIALS["PLA"]),
                          family="fdm", source="test"),
            load=None, moment=None, temp=None, quality="typical",
            knockdown=1.0, solid_fraction=1.0, planes=400, bin_size=0.1,
            z_only=False, green=False, max_segments=4_000_000,
            arm=None, deflect=None, load_case=None, fix=None)
res, meta, model = ps.run_gcode(f"{HERE}/solid20.gcode", opts)
# Solid 20x20 PLA: Z section 400 mm^2 at 50*0.55 MPa, knockdown 1.0
check("solid Z tensile capacity (N)", res.axes["z"]["tension_N"], 400*50*0.55, tol=0.08)
# Vertical section 20 wide x 10 tall = 200 mm^2
check("solid X section area (mm^2)", res.axes["x"]["area"], 200, tol=0.03)
# X-section bending: I = 10*20^3/12 = 6667, c = 10, S = 667
crit = res.axes["x"]["critical"]
check("X section modulus (strong)", max(crit.S1, crit.S2), 10*20**3/12/10, tol=0.05)
check("X section modulus (weak)", min(crit.S1, crit.S2), 20*10**3/12/5, tol=0.05)

res2, _, _ = ps.run_gcode(f"{HERE}/sparse20.gcode", opts)
check_true("15% infill is weaker than 100%",
           res2.axes["z"]["tension_N"] < res.axes["z"]["tension_N"] * 0.5,
           f"{res2.axes['z']['tension_N']:.0f} N vs {res.axes['z']['tension_N']:.0f} N")
# 2 walls x 0.45 x 10 mm tall, cut between infill lines = 18 mm^2
check("sparse Y section is walls only", res2.axes["y"]["area"], 4*0.45*10, tol=0.05)

res3, _, _ = ps.run_gcode(f"{HERE}/tall.gcode", opts)
check_eq("tall part is Z-governed", res3.weakest_axis, "z")
check_eq("squat solid part is not Z-governed", res.weakest_axis != "z", True)

group("Bending and stiffness")
# Analytic cantilever: solid 20x20 block, Z section 400 mm^2.
# I = 20^4/12 = 13333 mm^4, c = 10, S = 1333 mm^3.
res, meta, model = ps.run_gcode(f"{HERE}/solid20.gcode", opts)
d = res.axes["z"]; sec = d["bending"]
E = ps.FDM_MATERIALS["PLA"]["mod"]
I = sec.I1 if sec.bend_axis == "1" else sec.I2
check("EI conversion to N.m^2", d["EI"], E*I/1e6, tol=1e-6)
# F = M/L and delta = FL^3/(3EI) must be self-consistent
expect_F = d["moment_Nm"]*1000.0/d["arm_mm"]
check("breaks-at follows from moment/arm", d["break_N"], expect_F, tol=0.001)
expect_d = d["break_N"]*d["arm_mm"]**3/(3*E*I)
check("deflection follows FL^3/3EI", d["deflection_mm"], expect_d, tol=0.001)

# Deflection-limited load must invert cleanly.
opts_d = dict(opts, deflect=1.0)
res_d, _, _ = ps.run_gcode(f"{HERE}/solid20.gcode", opts_d)
dd = res_d.axes["z"]
I2_ = dd["bending"].I1 if dd["bending"].bend_axis == "1" else dd["bending"].I2
check("load for a 1 mm deflection", dd["deflect_limit_N"],
      3*E*I2_*1.0/dd["arm_mm"]**3, tol=0.001)

# The bending-critical section need not be the thinnest one.
res_u, _, _ = ps.run_gcode(f"{HERE}/bracket_upright.gcode", opts)
zu = res_u.axes["z"]
check_true("bending section differs from tension section",
           zu["bending"].pos != zu["critical"].pos,
           f"bend z={zu['bending'].pos:.1f} vs tension z={zu['critical'].pos:.1f}")
check_true("bending section sits low on a cantilever",
           zu["bending"].pos < zu["critical"].pos,
           f"z={zu['bending'].pos:.1f} mm")
check_true("bending capacity is far below tensile capacity",
           zu["break_N"] < zu["tension_N"] / 10,
           f"{zu['break_N']:.0f} N vs {zu['tension_N']:.0f} N")

# Orientation: the same bracket flat must be dramatically stiffer.
res_f, _, _ = ps.run_gcode(f"{HERE}/bracket_flat.gcode", opts)
check_true("flat is far stiffer than upright",
           res_f.axes["z"]["EI"] > 50 * res_u.axes["z"]["EI"],
           f"EI {res_f.axes['z']['EI']:.2f} vs {res_u.axes['z']['EI']:.2f} N.m^2")
check_eq("governing criterion is bending", res_u.governed_by, "bending")

# Anisotropy must be measured as stress, which is lever-arm independent.
zs = res_u.axes["z"]["sigma"]
ip = max(res_u.axes[a]["sigma"] for a in ("x","y"))
check("PLA anisotropy from stress", ip/zs, 1/0.55, tol=0.15)

# A longer arm must reduce capacity in proportion.
opts_a1 = dict(opts, arm=20.0); opts_a2 = dict(opts, arm=40.0)
r1, _, _ = ps.run_gcode(f"{HERE}/solid20.gcode", opts_a1)
r2, _, _ = ps.run_gcode(f"{HERE}/solid20.gcode", opts_a2)
check("doubling the arm halves the load", r1.axes["z"]["break_N"],
      2*r2.axes["z"]["break_N"], tol=0.001)

group("Fixtures and loads")
bbox = (0, 0, 0, 40, 20, 10)
r = ps.parse_region("bottom", bbox)
check_eq("named region axis", r.axis, "z")
check("named region takes a band", r.hi, 1.5)
r = ps.parse_region("z<5", bbox)
check("inequality region upper bound", r.hi, 5.0)
r = ps.parse_region("x >= 30", bbox)
check("inequality region lower bound", r.lo, 30.0)
try:
    ps.parse_region("sideways", bbox); check_true("bad region rejected", False)
except ValueError:
    check_true("bad region rejected", True)

for spec, wantN, wantD in [("50N -Z at top", 50.0, (0,0,-1)),
                           ("5kg down at x>35", 5*9.80665, (0,0,-1)),
                           ("2 lb +X at right", 2*4.44822, (1,0,0)),
                           ("100 -y @ back", 100.0, (0,-1,0))]:
    n, d, reg = ps.parse_load(spec)
    ok = abs(n-wantN) < 1e-3 and all(abs(a-b) < 1e-9 for a,b in zip(d,wantD))
    check_true(f"parse {spec!r}", ok, f"got {n:.2f} N {d}")
try:
    ps.parse_load("50 sideways at top"); check_true("bad direction rejected", False)
except ValueError:
    check_true("bad direction rejected", True)

# A slab fixture spans the other two axes, so only cuts normal to its own
# axis can separate the load from ground.
fixb = ps.parse_region("bottom", bbox)
check_true("cut above a bottom fixture carries load",
           ps.separates(fixb, bbox, "z", 5.0, (20,10,9)))
check_true("cut below a bottom fixture does not",
           not ps.separates(fixb, bbox, "z", 0.5, (20,10,9)))
check_true("cut normal to another axis never separates a slab fixture",
           not ps.separates(fixb, bbox, "x", 20.0, (35,10,9)))

# Closed-form cantilever: 40 x 10 x 5 bar, fixed x<5, 20 N down at x>35.
opts_lc = dict(opts, material=dict(name="standard",
                                   props=dict(ps.RESIN_MATERIALS["standard"]),
                                   family="resin", source="test"),
               knockdown=1.0, load_case="20N -Z at x>35", fix="x<5")
res_lc, _, _ = ps.run_stl(f"{HERE}/bar40x10x5.stl", opts_lc)
sim = res_lc.sim
check_true("load case solved", sim and not sim["error"])
I = 10*5**3/12; S = I/2.5
L = sim["load_pt"][0] - sim["worst"]["sec"].pos
M = 20.0*L
check("root moment matches F x L", abs(sim["worst"]["M1"])*1000, M, tol=0.02)
check("stress matches M/S", sim["worst"]["sigma"], M/S, tol=0.02)
check("safety factor matches allowable/stress",
      sim["worst"]["sf"], 45.0/(M/S), tol=0.02)
check("deflection matches FL^3/3EI", sim["deflection"],
      20.0*L**3/(3*2200.0*I), tol=0.03)
check_true("critical section is at the root",
           abs(sim["worst"]["sec"].pos - 5.0) < 0.5,
           f"x={sim['worst']['sec'].pos:.2f}")

# Load scales linearly.
opts_2x = dict(opts_lc, load_case="40N -Z at x>35")
res_2x, _, _ = ps.run_stl(f"{HERE}/bar40x10x5.stl", opts_2x)
check("doubling the load halves the safety factor",
      res_2x.sim["worst"]["sf"], sim["worst"]["sf"]/2, tol=0.01)

# Pushing straight down a column is compression, not bending.
opts_ax = dict(opts, load_case="40N -Z at top", fix="bottom")
res_ax, _, _ = ps.run_gcode(f"{HERE}/bracket_upright.gcode", opts_ax)
check_true("axial load produces no bending",
           abs(res_ax.sim["worst"]["M1"]) < 1e-6
           and abs(res_ax.sim["worst"]["M2"]) < 1e-6)
check_true("axial load is carried as direct stress",
           res_ax.sim["worst"]["axial_N"] > 39)
check("axial stress is F/A", res_ax.sim["worst"]["sigma"],
      40.0/res_ax.sim["worst"]["sec"].area, tol=0.02)

# Sideways on the same column bends it, and the weak direction is weaker.
opts_x = dict(opts, load_case="40N +X at top", fix="bottom")
opts_y = dict(opts, load_case="40N +Y at top", fix="bottom")
rx, _, _ = ps.run_gcode(f"{HERE}/bracket_upright.gcode", opts_x)
ry, _, _ = ps.run_gcode(f"{HERE}/bracket_upright.gcode", opts_y)
check_true("bending the 4 mm direction is weaker than the 20 mm one",
           rx.sim["worst"]["sf"] < ry.sim["worst"]["sf"] / 2,
           f"SF {rx.sim['worst']['sf']:.2f} vs {ry.sim['worst']['sf']:.2f}")
check_true("column bends worst near its base",
           rx.sim["worst"]["sec"].pos < 12,
           f"z={rx.sim['worst']['sec'].pos:.1f} mm")

# Region centroids must follow the material, not the bounding box.
check("load point sits on the upright leg, not the box centre",
      rx.sim["load_pt"][0], 2.0, tol=0.25)

# Transverse shear: a short load path develops little moment, so shear is
# what actually loads the section.
opts_sh = dict(opts, load_case="4kg +X at top", fix="bottom")
res_sh, _, _ = ps.run_gcode(f"{HERE}/bracket_flat.gcode", opts_sh)
w = res_sh.sim["worst"]
check("shear stress is 1.5 V/A", w["tau"],
      1.5*4*9.80665/w["sec"].area, tol=0.01)
check("von Mises combines direct and shear", w["sigma_eq"],
      math.sqrt(w["sigma"]**2 + 3*w["tau"]**2), tol=1e-6)
check_true("shear is flagged when it dominates", w["shear_governs"],
           f"tau {w['tau']:.3f} vs direct {w['sigma']:.3f}")
check_true("ignoring shear would have overstated the margin",
           w["sf"] < w["cap"]/max(w["sigma"], 1e-9) / 2,
           f"SF {w['sf']:.1f} with shear")

# On a long cantilever shear is negligible and bending still rules.
check_true("shear does not dominate a slender cantilever",
           not sim["worst"]["shear_governs"])
check("shear barely shifts a bending-dominated result",
      sim["worst"]["sigma_eq"], sim["worst"]["sigma"], tol=0.01)

def _opts(**kw):
    # solid_fraction defaults to None so a mesh gets the print model; a test
    # that wants the old flat-solid behaviour passes it explicitly.
    base = dict(material=None, load=None, fix=None, temp=None, quality="typical",
                uts=None, layer_adhesion=None, solid_fraction=None, planes=400,
                bin_size=0.1, z_only=False, green=False, arm=None, deflect=None,
                max_segments=4_000_000, layer_height=None, nozzle=None,
                ext_width=None, walls=None, infill=None)
    base.update(kw)
    return ps.build_opts(type("A", (), base)())

group("Print model for meshes")
# A mesh carries no walls or infill, so they are synthesised: walls x width
# around the boundary, infill in what is left.
mat, wall, inf = ps.printed_fraction(area=400.0, perimeter=80.0, walls=3,
                                     ext_width=0.42, infill=0.20)
check("wall area = perimeter x shell", wall, 80*3*0.42, tol=1e-9)
check("infill fills the remainder", inf, (400 - 80*3*0.42)*0.20, tol=1e-9)
check("material is wall + infill", mat, wall + inf, tol=1e-9)
check_eq("no area means no material", ps.printed_fraction(0, 10, 3, 0.4, 0.2)[0], 0.0)
solid_all = ps.printed_fraction(area=10.0, perimeter=100.0, walls=9, ext_width=1.0,
                                infill=0.2)[0]
check_true("the shell cannot exceed the section", solid_all <= 10.0 + 1e-9)
check("100% infill fills everything",
      ps.printed_fraction(400.0, 80.0, 0, 0.42, 1.0)[0], 400.0, tol=1e-9)

r_def, _, _ = ps.run_stl(f"{HERE}/cube20.stl", _opts(material="PLA"))
r_sol, _, _ = ps.run_stl(f"{HERE}/cube20.stl", _opts(material="PLA", solid_fraction=1.0))
check_true("the print model is weaker than solid",
           r_def.axes["z"]["break_N"] < r_sol.axes["z"]["break_N"] * 0.5,
           f"{r_def.axes['z']['break_N']:.0f} N vs {r_sol.axes['z']['break_N']:.0f} N")
check("a 20 mm cube section fills to the modelled fraction",
      r_def.axes["z"]["area"], mat, tol=0.02)
r_more, _, _ = ps.run_stl(f"{HERE}/cube20.stl", _opts(material="PLA", walls=5, infill=40))
check_true("more walls and infill is stronger",
           r_more.axes["z"]["break_N"] > r_def.axes["z"]["break_N"])
r_noz, _, _ = ps.run_stl(f"{HERE}/cube20.stl", _opts(material="PLA", nozzle=0.6))
check_true("a wider nozzle is stronger",
           r_noz.axes["z"]["break_N"] > r_def.axes["z"]["break_N"])
# G-code must ignore these entirely: it carries its own
r_g1, _, _ = ps.run_gcode(f"{HERE}/plate_hole.gcode", _opts())
r_g2, _, _ = ps.run_gcode(f"{HERE}/plate_hole.gcode", _opts(walls=9, infill=95, nozzle=1.0))
check("G-code ignores assumed print settings",
      r_g2.axes["z"]["break_N"], r_g1.axes["z"]["break_N"], tol=1e-9)

group("Containers: 3MF")
# Bambu Studio and OrcaSlicer export a sliced plate as .gcode.3mf — a ZIP.
m_raw = ps.parse_gcode(f"{HERE}/plate_hole.gcode")
m_zip = ps.open_3mf(f"{HERE}/plate_hole.gcode.3mf")
check_eq("gcode.3mf yields a G-code model", isinstance(m_zip, ps.GcodeModel), True)
check_eq("same layer count as the raw G-code", len(m_zip.layers), len(m_raw.layers))
check("same extruded volume", m_zip.total_volume, m_raw.total_volume, tol=1e-9)
check_true("container is recorded", "3MF" in (m_zip.config.get("_container") or ""))
m_stored = ps.open_3mf(f"{HERE}/stored.gcode.3mf")
check("an uncompressed entry reads identically", m_stored.total_volume,
      m_raw.total_volume, tol=1e-9)
# a plain .3mf is a mesh
mesh = ps.open_3mf(f"{HERE}/cube20.3mf")
check_eq("plain .3mf yields a mesh", isinstance(mesh, ps.Mesh), True)
check("3MF cube volume", mesh.volume(), 8000, tol=0.001)
check_eq("3MF cube triangles", len(mesh.tris), 12)
# the whole analysis must agree with the loose file
r_raw, _, _ = ps.run_gcode(f"{HERE}/plate_hole.gcode", _opts())
r_zip, _, _ = ps.analyse_path(f"{HERE}/plate_hole.gcode.3mf", _opts())[0:1] + (None, None)
r_zip = ps.analyse_path(f"{HERE}/plate_hole.gcode.3mf", _opts())[0]
for ax in ("x", "y", "z"):
    check(f"{ax}: 3MF matches the loose G-code",
          r_zip.axes[ax]["break_N"], r_raw.axes[ax]["break_N"], tol=1e-9)
check_true("a ZIP is recognised by its magic bytes", ps.is_zip(b"PK\x03\x04rest"))
check_true("plain text is not a ZIP", not ps.is_zip(b"G1 X10 Y10"))

group("Stress concentrations")
mh = ps.parse_gcode(f"{HERE}/plate_hole.gcode")
mn = ps.parse_gcode(f"{HERE}/plate_notch.gcode")
fh = ps.concentrations_for(mh)
fn = ps.concentrations_for(mn)
check_true("hole detected", any(f["kind"] == "hole" for f in fh), f"{len(fh)} features")
hole = [f for f in fh if f["kind"] == "hole"][0]
check("hole diameter recovered", hole["size"], 6.0, tol=0.02)
# Peterson's net-section fit for a hole in a finite-width strip
x = 6.0/20.0
check("hole Kt matches Peterson", hole["kt"], 3-3.13*x+3.66*x**2-1.53*x**3, tol=0.02)
check_true("notch detected", any(f["kind"] == "notch" for f in fn))
notch = [f for f in fn if f["kind"] == "notch"][0]
# a semicircular edge notch: depth = root radius, so Kt = 1 + 2*sqrt(1) = 3
check("notch Kt matches 1+2*sqrt(t/r)", notch["kt"], 3.0, tol=0.06)
check_true("a plain part has no concentrations",
           not ps.concentrations_for(ps.parse_gcode(f"{HERE}/solid20.gcode")))

check("brittle material feels all of Kt", ps.notch_sensitivity(4), 1.0)
check_true("ductile material feels less", ps.notch_sensitivity(25) < 0.75)
check("very ductile is floored", ps.notch_sensitivity(400), 0.40)
check("Kt for a hole equal to half the width", ps.kt_hole(10, 20),
      3-3.13*0.5+3.66*0.25-1.53*0.125, tol=1e-6)
check_eq("no hole means no factor", ps.kt_hole(0, 20), 1.0)
check_true("Kt is capped", ps.kt_notch(100, 0.001) <= 6.0)

o_on, o_off = _opts(), _opts()
o_off["no_kt"] = True
r_on, _, _ = ps.run_gcode(f"{HERE}/plate_hole.gcode", o_on)
r_off, _, _ = ps.run_gcode(f"{HERE}/plate_hole.gcode", o_off)
check_true("a hole lowers the capacity",
           r_on.axes["x"]["break_N"] < r_off.axes["x"]["break_N"] * 0.85,
           f"{r_on.axes['x']['break_N']:.1f} N vs {r_off.axes['x']['break_N']:.1f} N")
check_true("the critical section moves to the hole",
           abs(r_on.axes["x"]["bending"].pos - hole["x"]) < 4.0,
           f"x={r_on.axes['x']['bending'].pos:.1f} vs hole at x={hole['x']:.1f}")
check("the weakened section carries Kf", r_on.axes["x"]["bending"].kf, hole["kt"], tol=0.02)
# the same part in a ductile material feels less of it
r_pet, _, _ = ps.run_gcode(f"{HERE}/plate_hole.gcode", _opts(material="PETG"))
check_true("a ductile material is less notch sensitive",
           r_pet.axes["x"]["bending"].kf < r_on.axes["x"]["bending"].kf,
           f"PETG Kf {r_pet.axes['x']['bending'].kf:.2f} vs PLA {r_on.axes['x']['bending'].kf:.2f}")

# Kf must equal 1 + q(Kt-1) exactly. It did not once: the notch-sensitivity
# argument shared a name with a loop variable holding a coordinate, so Kf came
# out as 80 instead of 2.35 and the safety factor was 34x too low.
for mat, fx in (("PLA", f"{HERE}/plate_hole.gcode"), ("PETG", f"{HERE}/plate_hole.gcode"),
                ("PA6", f"{HERE}/plate_notch.gcode")):
    rr, _, mm = ps.run_gcode(fx, _opts(material=mat))
    qq = ps.notch_sensitivity(ps.FDM_MATERIALS[mat]["elong"])
    for ax in ("x", "y", "z"):
        sec = rr.axes[ax]["bending"]
        if sec.kf <= 1.001:
            continue
        check(f"{mat} {ax}: Kf = 1+q(Kt-1)", sec.kf, 1 + qq * (sec.kt - 1), tol=1e-9)
        check_true(f"{mat} {ax}: Kf stays sane", 1.0 <= sec.kf <= 6.0, f"Kf={sec.kf:.2f}")

group("Torsion")
# Torsional model must switch with the section, not default to the polar
# moment, which overstates a thin-walled section badly.
msol = ps.parse_gcode(f"{HERE}/solid20.gcode")
msp  = ps.parse_gcode(f"{HERE}/sparse20.gcode")
ssol = ps.analyse_layers(msol, 50.0, 0.55, 3500.0)[20]
ssp  = ps.analyse_layers(msp, 50.0, 0.55, 3500.0)[20]
check("enclosed area from the outer wall loop", ssol.enclosed, 19.55**2, tol=0.01)
check("wall perimeter from the outer wall loop", ssol.perimeter, 4*19.55, tol=0.01)
check("effective wall thickness", ssol.t_wall, 0.90, tol=0.05)
check_eq("100% infill uses the solid model", ssol.tors_model, "solid")
check_eq("15% infill uses the closed shell model", ssp.tors_model, "closed shell")
# solid 20 mm square: J = 0.1406 a^4, torsional modulus = 0.208 a^3
check("solid J near the closed form", ssol.tors_J, 0.1406*20**4, tol=0.10)
check("solid Q near the closed form", ssol.tors_Q, 0.208*20**3, tol=0.10)
check_true("a hollow section is much weaker in torsion than a solid one",
           ssp.tors_Q < ssol.tors_Q/2,
           f"Q {ssp.tors_Q:.0f} vs {ssol.tors_Q:.0f}")
check_true("polar moment would have overstated stiffness",
           (ssp.I1+ssp.I2) > 1.4*ssp.tors_J,
           f"Ip {ssp.I1+ssp.I2:.0f} vs J {ssp.tors_J:.0f}")
# An open section -- no closed wall circuit -- is weaker again by orders of
# magnitude, which is exactly the case Ip gets catastrophically wrong.
Qo, Jo, mo = ps.torsion_properties(area=50.0, enclosed=0.0, perimeter=0.0,
                                   t_wall=0.9, I_polar=1000.0,
                                   open_sum=50*0.9**3)
check_eq("no closed wall falls back to the open model", mo, "open section")
check_true("open section is far weaker in torsion than a closed one",
           Jo < ssp.tors_J/100, f"J {Jo:.2f} vs {ssp.tors_J:.0f}")

# Vertical stations recover the silhouette, so Bredt has a real enclosed area.
stx = ps.vertical_stations(msp, "x", [25.0], 50.0, 0.55)[0]
check("vertical station enclosed area", stx.enclosed, 20*10, tol=0.02)

# An L-bracket loaded on one arm twists its root: the load sits off the
# section centroid where both arms are present, and in line past the step.
opts_t = dict(opts, load_case="20N -Z at x>35", fix="x<5")
res_t, _, mt = ps.run_gcode(f"{HERE}/bracket_flat.gcode", opts_t)
bb = (0, 0, 0, 40, 40, 4)
fx = ps.parse_region("x<5", bb)
sts = ps.vertical_stations(mt, "x", [6.0, 30.0], 50.0, 0.55)
_, allr = ps.solve_load_case({"x": sts}, bb, fx, res_t.sim["fix_pt"], 20.0,
                             (0, 0, -1), res_t.sim["load_pt"], 1.0)
byx = {round(r["sec"].pos): r for r in allr}
check("torsion where the arms are offset = F x offset",
      byx[6]["torsion"]*1000, 20.0*10.0, tol=0.02)
check_true("no torsion where the centroid lines up with the load",
           byx[30]["torsion"] < 1e-6,
           f"{byx[30]['torsion']:.2e} N.m")
check("torsional stress is T/Q", byx[6]["tau_torsion"],
      byx[6]["torsion"]*1000/byx[6]["sec"].tors_Q, tol=1e-6)
check_true("twist is reported", res_t.sim["twist_deg"] > 0.01,
           f"{res_t.sim['twist_deg']:.3f} deg")
# A reported twist must never be left unexplained: when torsion peaks away
# from the governing section, that section is surfaced too.
pt = res_t.sim["peak_torsion"]
check_true("peak torsion section is kept", pt is not None)
check_true("peak torsion is not the governing section",
           pt["sec"] is not res_t.sim["worst"]["sec"],
           f"peak at x={pt['sec'].pos:.1f}, worst at "
           f"x={res_t.sim['worst']['sec'].pos:.1f}")
check_true("peak torsion sits before the step in the L",
           pt["sec"].pos < 20.0, f"x={pt['sec'].pos:.1f}")
check("peak torsion equals F x offset / Q", pt["tau_torsion"],
      20.0*10.0/pt["sec"].tors_Q, tol=0.05)
check("von Mises includes torsion", byx[6]["sigma_eq"],
      math.sqrt(byx[6]["sigma"]**2
                + 3*(byx[6]["tau_shear"]+byx[6]["tau_torsion"])**2), tol=1e-6)

# Load applied inside the fixture: nothing is cut off, so nothing is loaded.
opts_bad = dict(opts, load_case="20N -Z at bottom", fix="bottom")
res_bad, _, _ = ps.run_gcode(f"{HERE}/bracket_flat.gcode", opts_bad)
check_true("unloadable setup is reported, not silently wrong",
           res_bad.sim["error"] is not None,
           (res_bad.sim["error"] or "")[:60])

# A named region is a band of the bounding box, so on a thin part it is thin.
rb = ps.parse_region("bottom", (0, 0, 0, 40, 40, 4))
check("bottom band scales with part height", rb.hi, 0.6)

group("Material and derating")
cases = [("PLA","PLA"),("Generic PLA","PLA"),("Bambu PETG-CF","PETG-CF"),
         ("eSUN PLA+ Black","PLA+"),("PAHT-CF","PA-CF"),("nylon","PA6"),
         ("TPU","TPU95A"),("resin","standard"),("Durable","tough"),
         ("water washable","water-washable")]
bad = [q for q, w in cases if (ps.lookup_material(q) or [None])[0] != w]
check_true("material name resolution", not bad, f"failed: {bad}")
check_true("unknown material returns None", ps.lookup_material("unobtanium") is None)

check("PLA at 20 C", ps.thermal_factor(20, 55)[0], 1.0)
check("PLA at 40 C", ps.thermal_factor(40, 55)[0], 0.95, tol=0.05)
check_true("PLA collapses by 60 C", ps.thermal_factor(60, 55)[0] < 0.25)
check("ABS at 60 C unaffected", ps.thermal_factor(60, 95)[0], 1.0)

tags = [("Outer wall",ps.F_OUTER),("WALL-OUTER",ps.F_OUTER),
        ("External perimeter",ps.F_OUTER),("Inner wall",ps.F_INNER),
        ("Sparse infill",ps.F_SPARSE),("FILL",ps.F_SPARSE),
        ("SKIN",ps.F_SOLID),("Internal solid infill",ps.F_SOLID),
        ("Bridge infill",ps.F_BRIDGE),("Support material",ps.F_SUPPORT),
        ("Skirt",ps.F_SKIRT),("Brim",ps.F_SKIRT),("Ironing",ps.F_IRON),
        ("Gap fill",ps.F_GAP),("Overhang wall",ps.F_OVERHANG)]
bad = [t for t, e in tags if ps.classify_feature(t) != e]
check_true("feature tag classification", not bad, f"failed: {bad}")

# ---------------------------------------------------------------- cli
group("CLI")
PY = sys.executable
CLI = f"{ROOT}/printstrength.py"

r = subprocess.run([PY, CLI, f"{HERE}/sparse20.gcode", "--json"],
                   capture_output=True, text=True)
check_eq("--json exits 0", r.returncode, 0)
try:
    d = json.loads(r.stdout)
    check_true("--json is valid JSON", True)
    check_true("json reports a weakest axis", d["weakest_axis"] in ("x","y","z"))
    check_eq("json reports governing criterion", d["governed_by"], "bending")
    za = d["axes"]["z"]
    check_true("json carries bending fields",
               za["breaks_at_N"] and za["lever_arm_mm"]
               and za["flexural_rigidity_Nm2"] is not None)
    check_true("json keeps tension as a secondary field", za["tension_N"] > 0)
except Exception as exc:
    check_true("--json is valid JSON", False, str(exc))

r = subprocess.run([PY, CLI, f"{HERE}/sparse20.gcode", "--load", "50000", "--no-color"],
                   capture_output=True, text=True)
check_eq("overloaded part exits 2", r.returncode, 2)
check_true("overloaded part says FAIL", "FAIL" in r.stdout)

r = subprocess.run([PY, CLI, f"{HERE}/sparse20.gcode", "--load", "10", "--no-color"],
                   capture_output=True, text=True)
check_eq("safe load exits 0", r.returncode, 0)

r = subprocess.run([PY, CLI, f"{HERE}/bracket_upright.gcode", "--deflect", "2",
                    "--no-color"], capture_output=True, text=True)
check_true("--deflect reports a stiffness limit", "hits 2.0 mm at" in r.stdout)
check_true("--deflect can flag stiffness as governing",
           "stiffness governs" in r.stdout)

r = subprocess.run([PY, CLI, f"{HERE}/solid20.gcode", "--arm", "25", "--no-color"],
                   capture_output=True, text=True)
check_true("--arm is honoured", "applied 25 mm out" in r.stdout)

r = subprocess.run([PY, CLI, f"{HERE}/bracket_upright.gcode", "--fix", "bottom",
                    "--load", "40N +X at top", "--no-color"],
                   capture_output=True, text=True)
check_true("load case renders", "LOAD CASE" in r.stdout and "SAFETY FACTOR" in r.stdout)
check_eq("failing load case exits 2", r.returncode, 2)

r = subprocess.run([PY, CLI, f"{HERE}/bracket_upright.gcode", "--fix", "bottom",
                    "--load", "5N +X at top", "--no-color"],
                   capture_output=True, text=True)
check_eq("passing load case exits 0", r.returncode, 0)

r = subprocess.run([PY, CLI, f"{HERE}/bracket_upright.gcode", "--fix", "bottom",
                    "--load", "4kg +X at top", "--json"],
                   capture_output=True, text=True)
lc = json.loads(r.stdout)["load_case"]
check("kg converts to newtons", lc["load_N"], 4*9.80665, tol=1e-4)
check_true("json load case is complete",
           all(lc.get(k) is not None for k in
               ("safety_factor","stress_MPa","load_point","worst_section")))

r = subprocess.run([PY, CLI, f"{HERE}/solid20.gcode", "--fix", "bottom", "--no-color"],
                   capture_output=True, text=True)
check_true("--fix without --load errors cleanly",
           r.returncode != 0 and "needs a --load" in r.stderr)

r = subprocess.run([PY, CLI, f"{HERE}/solid20.gcode", "--load", "50N -Z at nowhere",
                    "--fix", "bottom", "--no-color"], capture_output=True, text=True)
check_true("bad region errors cleanly",
           r.returncode != 0 and "cannot read region" in r.stderr)

r = subprocess.run([PY, CLI, "materials"], capture_output=True, text=True)
check_true("materials listing works", "PLA" in r.stdout and "standard" in r.stdout)

r = subprocess.run([PY, CLI, f"{HERE}/notched.stl", "-m", "tough", "--no-color"],
                   capture_output=True, text=True)
check_true("STL analysis runs",
           r.returncode == 0 and "HOW MUCH LOAD IT TAKES" in r.stdout
           and "breaks at" in r.stdout)

r = subprocess.run([PY, CLI, "nope.gcode"], capture_output=True, text=True)
check_true("missing file errors cleanly", r.returncode != 0 and "no such file" in r.stderr)

r = subprocess.run([PY, CLI, f"{HERE}/notched.stl"], capture_output=True, text=True)
check_true("STL without --material errors cleanly",
           r.returncode != 0 and "no material" in r.stderr.lower()
           or "material information" in r.stderr)

group("OrcaSlicer post-processing mode")
tmp = tempfile.mkdtemp()
try:
    gp = os.path.join(tmp, "job.gcode")
    shutil.copy(f"{HERE}/solid20.gcode", gp)
    before = open(gp).read()
    r = subprocess.run([PY, CLI, "--orca", gp], capture_output=True, text=True)
    check_eq("post-process exits 0", r.returncode, 0)
    after = open(gp).read()
    check_true("report written alongside G-code",
               os.path.exists(os.path.join(tmp, "job.strength.txt")))
    check_true("summary prepended as comments", after.startswith("; ===== printstrength"))
    check_true("original G-code preserved intact", before in after)
    check_true("injected lines are all comments",
               all(l.startswith(";") for l in after.split("\n")[:9] if l.strip()))
    # A broken file must never break the user's slice.
    bad_gp = os.path.join(tmp, "bad.gcode")
    open(bad_gp, "w").write("this is not gcode\n")
    r = subprocess.run([PY, CLI, "--orca", bad_gp], capture_output=True, text=True)
    check_eq("post-process survives bad input", r.returncode, 0)
    check_eq("bad input left untouched", open(bad_gp).read(), "this is not gcode\n")
finally:
    shutil.rmtree(tmp)

print()
print("=" * 62)
if FAILS:
    print(f"{len(FAILS)} FAILURE(S):")
    for f in FAILS: print("  -", f)
    sys.exit(1)
print("ALL TESTS PASSED")
