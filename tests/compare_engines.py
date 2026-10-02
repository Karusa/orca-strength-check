#!/usr/bin/env python3
"""Diff the JavaScript port against the Python reference implementation.

The Python file is the reference: it is the one validated against closed-form
section properties.  This check keeps the port honest -- any drift in the
physics shows up here as a numeric difference, on every fixture.
"""
import json, os, subprocess, sys, math
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import printstrength as ps

TOL = 0.02          # 2%: the port analyses vertical cuts on the station grid
FAILS = []

FLOOR = 1e-3        # below this both numbers are noise, not physics

def close(a, b, tol=TOL):
    if a is None and b is None: return True
    if a is None: a = 0.0
    if b is None: b = 0.0
    if abs(a) < FLOOR and abs(b) < FLOOR: return True
    if abs(b) < 1e-9: return abs(a) < FLOOR
    return abs(a - b) / abs(b) <= tol

def cmp(label, js, py, tol=TOL):
    ok = close(js, py, tol)
    js_s = "None" if js is None else f"{js:12.4f}"
    py_s = "None" if py is None else f"{py:12.4f}"
    err = "" if (js is None or py is None or abs(py) < 1e-9) else f"{abs(js-py)/abs(py)*100:6.2f}%"
    print(f"    {'ok ' if ok else 'DIFF'} {label:<26} js {js_s}  py {py_s}  {err}")
    if not ok: FAILS.append(label)

def run_js(path, opts):
    script = f"""
const PS = require({json.dumps(ROOT + '/web/engine.js')});
const fs = require('fs');
const p = {json.dumps(path)};
let input;
if (p.endsWith('.stl') || p.endsWith('.3mf')) {{
  const b = fs.readFileSync(p);
  input = b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength);
}} else input = fs.readFileSync(p,'utf8');
const r = PS.analyze(input, {json.dumps(opts)});
const out = {{ok:r.ok, error:r.error}};
if (r.ok) {{
  out.weakest = r.weakestAxis; out.volume = r.volume; out.layers = r.layerCount;
  out.material = r.material.name;
  out.axes = {{}};
  for (const a of Object.keys(r.axes)) {{
    const d = r.axes[a];
    out.axes[a] = {{tension:d.tensionN, sigma:d.sigma, area:d.area,
                   breakN:d.breakN||null, EI:d.EI||null, moment:d.momentNm||null}};
  }}
  if (r.sim && !r.sim.error) {{
    const w = r.sim.worst;
    out.sim = {{sf:w.sf, sigma:w.sigma, sigmaEq:w.sigmaEq, tauShear:w.tauShear,
               tauTorsion:w.tauTorsion, torsion:w.torsion, axis:w.axis, pos:w.sec.pos,
               defl:r.sim.deflection, twist:r.sim.twistDeg, loadPt:r.sim.loadPt,
               curveEnd:(r.sim.curve.stations.length ? r.sim.curve.stations[r.sim.curve.stations.length-1][1] : null),
               curveN:r.sim.curve.stations.length, curveAxis:r.sim.curve.axis}};
  }} else if (r.sim) out.simError = r.sim.error;
}}
console.log(JSON.stringify(out));
"""
    res = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(res.stderr[:800])
    return json.loads(res.stdout)

CASES = [
    ("solid20.gcode",         {}),
    ("sparse20.gcode",        {}),
    ("tall.gcode",            {}),
    ("cura_abs.gcode",        {"material": "PLA"}),
    ("bracket_flat.gcode",    {}),
    ("bracket_upright.gcode", {}),
    ("arc.gcode",             {"material": "PLA"}),
    ("support.gcode",         {"material": "PLA"}),
    ("plate_hole.gcode",      {}),
    ("plate_hole.gcode.3mf",  {}),
    ("stored.gcode.3mf",      {}),
    ("cube20.3mf",            {"material": "PLA"}),
    ("plate_hole.gcode.3mf",  {"fix": "x<35", "load": "10N -Z at x>65"}),
    ("plate_notch.gcode",     {}),
    ("plate_hole.gcode",      {"fix": "x<35", "load": "10N -Z at x>65"}),
    ("plate_hole.gcode",      {"material": "PETG"}),
    ("bracket_flat.gcode",    {"fix": "x<5", "load": "20N -Z at x>35"}),
    ("bracket_upright.gcode", {"fix": "bottom", "load": "4kg +X at top"}),
    ("bracket_upright.gcode", {"fix": "bottom", "load": "40N -Z at top"}),
    ("bracket_flat.gcode",    {"fix": "bottom", "load": "4kg +X at top"}),
    ("solid20.gcode",         {"material": "PETG", "temp": 60, "quality": "poor"}),
    ("tall.gcode",            {"material": "ABS", "deflect": 1.0}),
    ("bar40x10x5.stl",        {"material": "standard", "fix": "x<5", "load": "20N -Z at x>35"}),
    ("cube20.stl",            {"material": "tough"}),
    ("cube20.stl",            {"material": "PLA", "walls": 5, "infill": 0.40}),
    ("cube20.stl",            {"material": "PLA", "nozzle": 0.6, "layerHeight": 0.3}),
    ("notched.stl",           {"material": "PLA", "solidFraction": 1.0}),
    ("notched.stl",           {"material": "standard"}),
    ("tube.stl",              {"material": "rigid"}),
]

for fname, opts in CASES:
    path = os.path.join(HERE, fname)
    label = fname + ("  " + json.dumps(opts) if opts else "")
    print(f"\n{label}")
    pyopts = dict(opts)
    for a, b in (("layerAdhesion", "layer_adhesion"), ("layerHeight", "layer_height"),
                 ("extWidth", "ext_width"), ("solidFraction", "solid_fraction")):
        if a in pyopts: pyopts[b] = pyopts.pop(a)
    try:
        py = ps.ui_analyze(path, pyopts)
    except Exception as exc:
        print(f"    python raised {exc}"); FAILS.append(label); continue
    try:
        js = run_js(path, opts)
    except Exception as exc:
        print(f"    node raised {exc}"); FAILS.append(label); continue

    if not py.get("ok") or not js.get("ok"):
        same = bool(py.get("ok")) == bool(js.get("ok"))
        print(f"    {'ok ' if same else 'DIFF'} both {'failed' if not same else 'agree on failure'}"
              f"  js={js.get('error')} py={py.get('error')}")
        if not same: FAILS.append(label)
        continue

    cmp("weakest axis", 1 if js["weakest"] == py["weakest_axis"] else 0, 1)
    for ax in sorted(py["axes"]):
        if ax not in js["axes"]: FAILS.append(f"{label}: missing axis {ax}"); continue
        p_, j_ = py["axes"][ax], js["axes"][ax]
        cmp(f"{ax}: tension N", j_["tension"], p_["tension_N"])
        cmp(f"{ax}: breaks at N", j_["breakN"], p_["breaks_at_N"])
        cmp(f"{ax}: EI", j_["EI"], p_["flexural_rigidity_Nm2"])
    if "load_case" in py and py["load_case"] and not py["load_case"].get("error"):
        lc, sj = py["load_case"], js.get("sim")
        if not sj: FAILS.append(f"{label}: js has no load case"); continue
        cmp("SF", sj["sf"], lc["safety_factor"])
        cmp("von Mises", sj["sigmaEq"], lc["stress_MPa"])
        cmp("shear", sj["tauShear"], lc["shear_stress_MPa"])
        cmp("torsion", sj["tauTorsion"], lc["torsion_stress_MPa"])
        cmp("deflection", sj["defl"], lc["deflection_mm"])
        cmp("twist", sj["twist"], lc["twist_deg"])
        pc = (lc.get("deflected_shape") or {}).get("stations")
        if pc:
            cmp("deflected shape end", sj["curveEnd"], pc[-1][1])
            cmp("deflected shape stations", sj["curveN"], len(pc))

print("\n" + "="*70)
if FAILS:
    print(f"{len(FAILS)} DIFFERENCE(S):")
    for f in FAILS: print("  -", f)
    sys.exit(1)
print("JS PORT MATCHES PYTHON REFERENCE")
