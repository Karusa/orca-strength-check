#!/usr/bin/env python3
"""
printstrength - estimate the mechanical strength of FDM and resin 3D prints.

Two analysis paths:

  1. G-code (FDM).  Reads sliced G-code from OrcaSlicer / Bambu Studio /
     PrusaSlicer / SuperSlicer / Cura and reconstructs the real deposited
     geometry: every extrusion segment, its width, its feature type
     (outer wall / sparse infill / ...) and its layer.  From that it finds
     the weakest cross-section in Z, X and Y and converts it to a load.

  2. STL (resin, or FDM-as-solid).  Slices the mesh analytically and finds
     the weakest cross-section the same way.  Resin parts are close to
     isotropic so geometry is most of the answer.

Nothing here is a substitute for FEA or for breaking a test coupon.  It is a
first-order estimate: expect +/- 30% on a well characterised material and
worse on a filament you have never dried or calibrated.  The value is in the
ranking -- which section fails first, which axis is weak, what to change.

Pure standard library.  Python 3.8+.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import struct
import sys
from array import array

__version__ = "1.1.0"

# How many candidate planes per vertical axis get a full section-moment pass.
BEND_CANDIDATES = 8

# Stations per axis when solving a fixture/load case.  The UI plots the same
# stations, so the chart minimum and the headline number cannot disagree.
LOAD_STATIONS = 120

# Peak transverse shear over the section average.  1.5 is the rectangular-
# section value; real printed sections vary, so this errs conservative.
SHEAR_PEAK_FACTOR = 1.5


# ---------------------------------------------------------------------------
# Material database
# ---------------------------------------------------------------------------
#
# uts   : ultimate tensile strength of a *printed* part loaded in-plane (XY),
#         solid, well tuned.  NOT the datasheet injection-moulded coupon value,
#         which is typically 10-25% higher.
# mod   : tensile modulus, MPa.
# z     : layer adhesion as a fraction of uts.  The single most important and
#         most variable number here; depends heavily on temperature, cooling
#         and chamber.  Values are for typical, reasonably tuned prints.
# hdt   : heat deflection temperature, degC, ~0.45 MPa.  Used for thermal
#         derating only.
# rho   : g/cm^3.
# elong : elongation at break, %.  Low = brittle = notch sensitive.
# notes : shown in the report.

FDM_MATERIALS = {
    "PLA":      dict(uts=50,  mod=3500, z=0.55, hdt=55,  rho=1.24, elong=4,
                     notes="Brittle. Stiff but creeps under sustained load; softens near 55 C."),
    "PLA+":     dict(uts=45,  mod=3000, z=0.62, hdt=58,  rho=1.24, elong=8,
                     notes="Toughened PLA. Lower peak strength, much better layer adhesion and impact."),
    "PLA-CF":   dict(uts=48,  mod=5500, z=0.42, hdt=58,  rho=1.30, elong=2,
                     notes="Carbon fill raises stiffness, lowers layer adhesion and impact. Very brittle in Z."),
    "PETG":     dict(uts=45,  mod=2100, z=0.72, hdt=70,  rho=1.27, elong=12,
                     notes="Best layer adhesion of the common materials. Ductile, notch tolerant."),
    "PETG-CF":  dict(uts=60,  mod=5000, z=0.50, hdt=78,  rho=1.32, elong=4,
                     notes="Stiffer than PETG, but the carbon halves its layer adhesion advantage."),
    "ABS":      dict(uts=38,  mod=2100, z=0.45, hdt=95,  rho=1.04, elong=15,
                     notes="Needs a chamber. Layer adhesion collapses without one; open-air ABS can hit z=0.25."),
    "ASA":      dict(uts=42,  mod=2200, z=0.45, hdt=100, rho=1.07, elong=14,
                     notes="ABS with UV stability. Same chamber requirement."),
    "ABS-CF":   dict(uts=55,  mod=5500, z=0.35, hdt=105, rho=1.11, elong=3,
                     notes="Dimensionally stable, weak in Z. Orient loads in-plane."),
    "PC":       dict(uts=62,  mod=2300, z=0.42, hdt=115, rho=1.20, elong=8,
                     notes="Strong and heat resistant but hygroscopic; wet PC loses most of its strength."),
    "PC-CF":    dict(uts=85,  mod=6000, z=0.35, hdt=130, rho=1.22, elong=3,
                     notes="High in-plane strength, poor Z. Dry it."),
    "PA6":      dict(uts=55,  mod=1600, z=0.55, hdt=90,  rho=1.12, elong=25,
                     notes="Nylon. Very tough dry, noticeably weaker and more ductile once it absorbs water."),
    "PA12":     dict(uts=48,  mod=1400, z=0.60, hdt=85,  rho=1.01, elong=30,
                     notes="Less hygroscopic than PA6, tough, good fatigue life."),
    "PA-CF":    dict(uts=95,  mod=6500, z=0.35, hdt=120, rho=1.15, elong=4,
                     notes="Very strong in-plane. Z strength is the design limit."),
    "PA-GF":    dict(uts=80,  mod=5000, z=0.40, hdt=115, rho=1.25, elong=5,
                     notes="Glass fill: tougher than carbon, slightly less stiff."),
    "PPA-CF":   dict(uts=130, mod=9000, z=0.35, hdt=190, rho=1.18, elong=3,
                     notes="High-performance nylon. Needs a hot chamber and a hardened hotend."),
    "PET-CF":   dict(uts=90,  mod=6000, z=0.38, hdt=150, rho=1.35, elong=3,
                     notes="Stiff and heat resistant, brittle in Z."),
    "PP":       dict(uts=25,  mod=1300, z=0.60, hdt=90,  rho=0.90, elong=40,
                     notes="Chemically inert, very ductile, low strength. Warps badly."),
    "TPU95A":   dict(uts=28,  mod=60,   z=0.80, hdt=60,  rho=1.20, elong=400,
                     notes="Elastomer. Strength is rarely the limit -- design by deflection, not stress."),
    "TPU85A":   dict(uts=18,  mod=20,   z=0.85, hdt=55,  rho=1.20, elong=500,
                     notes="Soft elastomer. Deflection governs."),
    "HIPS":     dict(uts=25,  mod=1900, z=0.50, hdt=88,  rho=1.05, elong=20,
                     notes="Mostly a support material. Weak."),
    "PEEK":     dict(uts=95,  mod=4000, z=0.45, hdt=250, rho=1.30, elong=20,
                     notes="Requires 400 C hotend and a heated chamber. Crystallinity dominates final strength."),
    "PPS-CF":   dict(uts=90,  mod=8000, z=0.40, hdt=200, rho=1.43, elong=3,
                     notes="Chemical and heat resistant engineering grade."),
}

# Resin is photopolymerised as a continuous solid, so it is nearly isotropic.
# The small z penalty is the layer interface from oxygen inhibition / dose
# overlap.  `cure` is the strength of a properly post-cured part relative to
# green (straight off the plate) -- applied as a multiplier when --green.
RESIN_MATERIALS = {
    "standard":       dict(uts=45, mod=2200, z=0.95, hdt=60,  rho=1.15, elong=5,
                           notes="Generic 405nm resin. Strong in tension, very brittle -- fails without warning."),
    "abs-like":       dict(uts=52, mod=2400, z=0.95, hdt=70,  rho=1.15, elong=8,
                           notes="Marketing name, not real ABS. Somewhat tougher than standard."),
    "tough":          dict(uts=40, mod=1600, z=0.95, hdt=55,  rho=1.13, elong=20,
                           notes="Trades strength for impact resistance and elongation. Creeps under load."),
    "water-washable": dict(uts=32, mod=1700, z=0.90, hdt=50,  rho=1.10, elong=6,
                           notes="Weakest common resin, and it keeps absorbing water. Avoid for load-bearing parts."),
    "rigid":          dict(uts=70, mod=3500, z=0.95, hdt=85,  rho=1.25, elong=3,
                           notes="Ceramic or glass filled. Stiffest option, extremely brittle."),
    "high-temp":      dict(uts=55, mod=3800, z=0.95, hdt=200, rho=1.20, elong=2,
                           notes="Needs a thermal post-cure to reach its rated HDT."),
    "flexible":       dict(uts=12, mod=100,  z=0.90, hdt=40,  rho=1.10, elong=120,
                           notes="Elastomeric. Design by deflection."),
    "castable":       dict(uts=25, mod=1400, z=0.90, hdt=45,  rho=1.10, elong=4,
                           notes="Formulated to burn out cleanly, not to carry load."),
    "dental":         dict(uts=60, mod=2600, z=0.95, hdt=75,  rho=1.18, elong=5,
                           notes="Biocompatible, well characterised, requires a validated cure cycle."),
}

# Aliases people actually type / that appear in slicer profiles.
ALIASES = {
    "pla+": "PLA+", "plapro": "PLA+", "tough pla": "PLA+", "pla pro": "PLA+",
    "pla tough": "PLA+", "pla-ht": "PLA+", "plahs": "PLA", "pla-hs": "PLA",
    "pla_cf": "PLA-CF", "placf": "PLA-CF", "pla cf": "PLA-CF",
    "petg cf": "PETG-CF", "petgcf": "PETG-CF", "pctg": "PETG",
    "abs cf": "ABS-CF", "abs-gf": "ABS-CF", "asa-cf": "ABS-CF", "asa cf": "ABS-CF",
    "pc cf": "PC-CF", "pccf": "PC-CF",
    "nylon": "PA6", "pa": "PA6", "pa6-cf": "PA-CF", "pacf": "PA-CF",
    "pa6 cf": "PA-CF", "pa12-cf": "PA-CF", "pahtcf": "PA-CF", "pa-ht": "PA6",
    "pa6 gf": "PA-GF", "pa6-gf": "PA-GF", "pagf": "PA-GF",
    "ppa cf": "PPA-CF", "ppacf": "PPA-CF", "pahtcf25": "PPA-CF",
    "pet cf": "PET-CF", "petcf": "PET-CF",
    "tpu": "TPU95A", "tpu 95a": "TPU95A", "tpu-95a": "TPU95A",
    "tpu 85a": "TPU85A", "tpu-85a": "TPU85A", "flex": "TPU95A",
    "pekk": "PEEK", "pps": "PPS-CF", "pps cf": "PPS-CF",
    "resin": "standard", "standard resin": "standard", "abs like": "abs-like",
    "abslike": "abs-like", "tough resin": "tough", "durable": "tough",
    "water washable": "water-washable", "washable": "water-washable",
    "rigid resin": "rigid", "engineering": "rigid",
    "high temp": "high-temp", "heat resistant": "high-temp",
}


def lookup_material(name):
    """Resolve a user string or slicer filament_type to (key, props, family)."""
    if not name:
        return None
    raw = name.strip()
    key = raw.upper()
    if key in FDM_MATERIALS:
        return key, FDM_MATERIALS[key], "fdm"
    low = raw.lower()
    if low in RESIN_MATERIALS:
        return low, RESIN_MATERIALS[low], "resin"
    norm = re.sub(r"[\s_]+", " ", low).strip()
    if norm in ALIASES:
        return lookup_material(ALIASES[norm])
    squashed = re.sub(r"[^a-z0-9+]", "", low)
    # Aliases again, but punctuation-insensitive ("PAHT-CF" -> "pahtcf").
    for a, target in ALIASES.items():
        if re.sub(r"[^a-z0-9+]", "", a) == squashed:
            return lookup_material(target)
    for k in FDM_MATERIALS:
        if re.sub(r"[^a-z0-9+]", "", k.lower()) == squashed:
            return k, FDM_MATERIALS[k], "fdm"
    for k in RESIN_MATERIALS:
        if re.sub(r"[^a-z0-9+]", "", k.lower()) == squashed:
            return k, RESIN_MATERIALS[k], "resin"
    # Last resort: longest alias that is a substring (handles "Generic PLA",
    # "Bambu PETG-CF @BBL X1C", "eSUN PLA+ Black" and friends).
    best = None
    for k in list(FDM_MATERIALS) + list(RESIN_MATERIALS):
        kl = k.lower()
        if kl in low and (best is None or len(kl) > len(best)):
            best = kl
    if best:
        for k in FDM_MATERIALS:
            if k.lower() == best:
                return k, FDM_MATERIALS[k], "fdm"
        for k in RESIN_MATERIALS:
            if k.lower() == best:
                return k, RESIN_MATERIALS[k], "resin"
    return None


# ---------------------------------------------------------------------------
# Feature classification
# ---------------------------------------------------------------------------

(F_UNKNOWN, F_OUTER, F_INNER, F_SOLID, F_TOP, F_BOTTOM, F_SPARSE, F_BRIDGE,
 F_GAP, F_OVERHANG, F_SUPPORT, F_SKIRT, F_IRON, F_OTHER) = range(14)

FEATURE_NAMES = {
    F_UNKNOWN: "unclassified", F_OUTER: "outer wall", F_INNER: "inner wall",
    F_SOLID: "solid infill", F_TOP: "top surface", F_BOTTOM: "bottom surface",
    F_SPARSE: "sparse infill", F_BRIDGE: "bridge", F_GAP: "gap fill",
    F_OVERHANG: "overhang wall", F_SUPPORT: "support", F_SKIRT: "skirt/brim",
    F_IRON: "ironing", F_OTHER: "other",
}

# Interlayer bond efficiency.  Walls stack bead-on-bead with a large, hot,
# repeatable contact patch.  Sparse infill crosses itself at points and its
# beads land on air as often as on plastic, so a given mm^2 of sparse infill
# footprint carries far less across the layer line than a mm^2 of wall.
Z_EFFICIENCY = {
    F_OUTER: 1.00, F_INNER: 1.00, F_SOLID: 0.95, F_TOP: 0.90, F_BOTTOM: 0.90,
    F_SPARSE: 0.55, F_BRIDGE: 0.50, F_GAP: 0.70, F_OVERHANG: 0.75,
    F_SUPPORT: 0.0, F_SKIRT: 0.0, F_IRON: 0.0, F_UNKNOWN: 0.85, F_OTHER: 0.50,
}

# In-plane contact quality.  Geometry already accounts for how little sparse
# infill there is, so this only derates bond quality, not quantity.
XY_EFFICIENCY = {
    F_OUTER: 1.00, F_INNER: 1.00, F_SOLID: 1.00, F_TOP: 0.95, F_BOTTOM: 0.95,
    F_SPARSE: 0.85, F_BRIDGE: 0.80, F_GAP: 0.80, F_OVERHANG: 0.85,
    F_SUPPORT: 0.0, F_SKIRT: 0.0, F_IRON: 0.0, F_UNKNOWN: 0.95, F_OTHER: 0.80,
}

# Features that are not part of the object at all.
NON_PART = (F_SUPPORT, F_SKIRT)

_FEATURE_PATTERNS = [
    (F_OVERHANG, ("overhang",)),
    (F_OUTER,    ("outer wall", "external perimeter", "outer perimeter", "wall-outer")),
    (F_INNER,    ("inner wall", "perimeter", "wall-inner", "internal perimeter")),
    (F_BRIDGE,   ("bridge",)),
    (F_TOP,      ("top surface", "top solid infill", "topsurface")),
    (F_BOTTOM,   ("bottom surface", "bottom solid infill", "first layer", "raft")),
    (F_IRON,     ("ironing",)),
    # Cura calls its solid top/bottom layers "SKIN".
    (F_SOLID,    ("internal solid infill", "solid infill", "solidinfill", "skin")),
    (F_GAP,      ("gap fill", "gap infill", "thin wall")),
    (F_SPARSE,   ("sparse infill", "internal infill", "fill", "infill")),
    (F_SUPPORT,  ("support", "prime tower", "wipe tower")),
    (F_SKIRT,    ("skirt", "brim")),
    (F_OTHER,    ("custom", "unknown")),
]


def classify_feature(text):
    t = text.strip().lower()
    for code, needles in _FEATURE_PATTERNS:
        for n in needles:
            if n in t:
                return code
    return F_UNKNOWN


# ---------------------------------------------------------------------------
# G-code parsing
# ---------------------------------------------------------------------------

class GcodeModel:
    """Deposited geometry reconstructed from a sliced G-code file.

    Segments are stored in flat typed arrays -- a 200 MB G-code file is
    several million extrusions and a list of tuples would not fit in RAM.
    """

    def __init__(self):
        self.x1 = array("f"); self.y1 = array("f")
        self.x2 = array("f"); self.y2 = array("f")
        self.w = array("f")                 # extrusion width, mm
        self.h = array("f")                 # layer height at that segment, mm
        self.feat = array("B")
        self.layer = array("i")
        self.layers = []                    # list of dicts
        self.config = {}
        self.slicer = "unknown"
        self.total_volume = 0.0             # mm^3, part only (no support/skirt)
        self.skipped_volume = 0.0
        self.warnings = []

    def __len__(self):
        return len(self.x1)

    def add(self, x1, y1, x2, y2, w, h, feat, layer):
        self.x1.append(x1); self.y1.append(y1)
        self.x2.append(x2); self.y2.append(y2)
        self.w.append(w); self.h.append(h)
        self.feat.append(feat); self.layer.append(layer)

    def bbox(self):
        lo_x = lo_y = float("inf"); hi_x = hi_y = float("-inf")
        for i in range(len(self.x1)):
            if self.feat[i] in NON_PART:
                continue
            hw = self.w[i] * 0.5
            a, b = self.x1[i], self.x2[i]
            lo_x = min(lo_x, a - hw, b - hw); hi_x = max(hi_x, a + hw, b + hw)
            a, b = self.y1[i], self.y2[i]
            lo_y = min(lo_y, a - hw, b - hw); hi_y = max(hi_y, a + hw, b + hw)
        if lo_x == float("inf"):
            return (0.0, 0.0, 0.0, 0.0)
        return (lo_x, lo_y, hi_x, hi_y)


_WORD_RE = re.compile(r"([A-Za-z])\s*(-?\d*\.?\d+(?:[eE]-?\d+)?)")
_CONFIG_RE = re.compile(r"^;\s*([a-zA-Z0-9_\.]+)\s*=\s*(.*?)\s*$")


def _clean_config_value(v):
    v = v.strip()
    if v.startswith('"') and v.endswith('"') and len(v) > 1:
        v = v[1:-1]
    # Multi-extruder settings come through as "PLA;PETG" or ["PLA","PETG"].
    v = v.strip("[]")
    if ";" in v:
        v = v.split(";")[0]
    if "," in v and not re.match(r"^-?[\d\.]+,\s*-?[\d\.]+$", v):
        v = v.split(",")[0]
    return v.strip().strip('"')


def parse_gcode(path, max_segments=4_000_000, progress=False):
    model = GcodeModel()

    # Machine state
    x = y = z = 0.0
    e = 0.0
    abs_xyz = True
    abs_e = True
    volumetric = False
    filament_d = 1.75
    cur_feat = F_UNKNOWN
    layer_idx = -1
    layer_z = None
    prev_layer_z = 0.0
    default_h = None
    in_object = True          # set false inside EXCLUDE_OBJECT / wipe tower
    seen_layer_marker = False
    pending_layer = False
    truncated = False

    # Layer accumulator
    lay_vol = {}
    lay_seg_start = 0

    def filament_area():
        return math.pi * (filament_d * 0.5) ** 2

    def start_layer(newz):
        nonlocal layer_idx, layer_z, prev_layer_z, lay_vol, lay_seg_start
        if layer_idx >= 0:
            close_layer()
        prev_layer_z = layer_z if layer_z is not None else 0.0
        layer_z = newz
        layer_idx += 1
        lay_vol = {}
        lay_seg_start = len(model)

    def close_layer():
        h = (layer_z - prev_layer_z) if layer_z is not None else 0.0
        if h <= 0.001 or h > 2.0:
            h = default_h if default_h else 0.2
        model.layers.append(dict(
            index=layer_idx, z=layer_z if layer_z is not None else 0.0, h=h,
            vol=dict(lay_vol), seg_start=lay_seg_start, seg_end=len(model)))
        # Backfill the true layer height onto this layer's segments.
        for i in range(lay_seg_start, len(model)):
            model.h[i] = h

    fh = open(path, "r", errors="replace")
    try:
        for line in fh:
            if not line:
                continue
            c0 = line[0]

            # ---- comments: features, layers, config ------------------------
            if c0 == ";":
                s = line[1:].strip()
                sl = s.lower()
                if sl.startswith("type:") or sl.startswith("feature:"):
                    cur_feat = classify_feature(s.split(":", 1)[1])
                    continue
                if sl.startswith("layer_change") or sl.startswith("layer:") \
                        or sl.startswith("layer ") or sl.startswith("z:"):
                    seen_layer_marker = True
                    m = re.search(r"z\s*[:=]\s*(-?[\d\.]+)", sl)
                    if m:
                        start_layer(float(m.group(1)))
                        pending_layer = False
                    elif layer_idx < 0 or len(model) > lay_seg_start:
                        # Marker carries no height (Cura's ";LAYER:n").  Defer
                        # until the Z move or first extrusion tells us where
                        # this layer actually sits.  If the current layer is
                        # still empty the marker is a redundant restatement of
                        # a ";Z:" we already handled, so ignore it.
                        pending_layer = True
                    continue
                if "stop_object" in sl or "exclude_object_end" in sl:
                    in_object = True
                    continue
                m = _CONFIG_RE.match(line.rstrip("\n"))
                if m:
                    k = m.group(1).lower()
                    if k not in model.config:
                        model.config[k] = _clean_config_value(m.group(2))
                    continue
                if "generated by" in sl or "prusaslicer" in sl or "orcaslicer" in sl \
                        or "bambustudio" in sl or "cura" in sl or "superslicer" in sl:
                    model.config.setdefault("_header", s)
                continue

            # Trailing comment on a command line
            if ";" in line:
                line = line.split(";", 1)[0]
                if not line.strip():
                    continue

            cmd = line[:3].upper()

            if cmd.startswith("G0") or cmd.startswith("G1"):
                nx = ny = nz = None
                ne = None
                for w_, v_ in _WORD_RE.findall(line):
                    u = w_.upper()
                    if u == "X":
                        nx = float(v_)
                    elif u == "Y":
                        ny = float(v_)
                    elif u == "Z":
                        nz = float(v_)
                    elif u == "E":
                        ne = float(v_)
                tx = nx if nx is not None else x
                ty = ny if ny is not None else y
                if not abs_xyz:
                    tx = x + (nx or 0.0); ty = y + (ny or 0.0)
                tz = z
                if nz is not None:
                    tz = nz if abs_xyz else z + nz

                de = 0.0
                if ne is not None:
                    de = ne - e if abs_e else ne
                    e = ne if abs_e else e + ne

                # Commit a deferred layer marker, or -- if the file has no
                # markers at all -- treat any Z change as a layer change.
                if nz is not None and abs(tz - z) > 1e-6:
                    if pending_layer:
                        start_layer(tz)
                        pending_layer = False
                    elif not seen_layer_marker:
                        if layer_idx < 0 or abs(tz - (layer_z or 0.0)) > 1e-6:
                            start_layer(tz)

                if de > 1e-9:
                    if pending_layer:
                        start_layer(tz if tz > 0 else (layer_z or 0.2))
                        pending_layer = False
                    if layer_idx < 0:
                        start_layer(tz if tz > 0 else 0.2)
                    length = math.hypot(tx - x, ty - y)
                    vol = de if volumetric else de * filament_area()
                    if length > 1e-6 and vol > 0:
                        if len(model) < max_segments:
                            feat = cur_feat if in_object else F_SUPPORT
                            hh = 0.2
                            width = vol / (length * hh) if length * hh > 0 else 0.0
                            model.add(x, y, tx, ty, width, hh, feat, layer_idx)
                            # width is provisional: recomputed once the true
                            # layer height for this layer is known.
                            lay_vol[feat] = lay_vol.get(feat, 0.0) + vol
                            if feat in NON_PART:
                                model.skipped_volume += vol
                            else:
                                model.total_volume += vol
                        else:
                            truncated = True
                x, y, z = tx, ty, tz

            elif cmd.startswith("G2") or cmd.startswith("G3"):
                # Arc move (OrcaSlicer "arc fitting").  Discretise it.
                ccw = cmd.startswith("G3")
                i_off = j_off = 0.0
                nx = ny = None; ne = None; rad = None
                for w_, v_ in _WORD_RE.findall(line):
                    u = w_.upper()
                    if u == "X": nx = float(v_)
                    elif u == "Y": ny = float(v_)
                    elif u == "I": i_off = float(v_)
                    elif u == "J": j_off = float(v_)
                    elif u == "R": rad = float(v_)
                    elif u == "E": ne = float(v_)
                tx = nx if nx is not None else x
                ty = ny if ny is not None else y
                de = 0.0
                if ne is not None:
                    de = ne - e if abs_e else ne
                    e = ne if abs_e else e + ne
                pts = _arc_points(x, y, tx, ty, i_off, j_off, rad, ccw)
                if de > 1e-9 and len(pts) > 1:
                    if pending_layer:
                        start_layer(z if z > 0 else (layer_z or 0.2))
                        pending_layer = False
                    if layer_idx < 0:
                        start_layer(z if z > 0 else 0.2)
                    total = sum(math.hypot(pts[k+1][0]-pts[k][0], pts[k+1][1]-pts[k][1])
                                for k in range(len(pts)-1))
                    vol = de if volumetric else de * filament_area()
                    if total > 1e-6:
                        feat = cur_feat if in_object else F_SUPPORT
                        for k in range(len(pts) - 1):
                            ax, ay = pts[k]; bx, by = pts[k+1]
                            seg_len = math.hypot(bx-ax, by-ay)
                            if seg_len < 1e-6 or len(model) >= max_segments:
                                continue
                            sv = vol * seg_len / total
                            model.add(ax, ay, bx, by, sv/(seg_len*0.2), 0.2, feat, layer_idx)
                        lay_vol[feat] = lay_vol.get(feat, 0.0) + vol
                        if feat in NON_PART:
                            model.skipped_volume += vol
                        else:
                            model.total_volume += vol
                x, y = tx, ty

            elif cmd.startswith("G92"):
                for w_, v_ in _WORD_RE.findall(line):
                    u = w_.upper()
                    if u == "E": e = float(v_)
                    elif u == "X": x = float(v_)
                    elif u == "Y": y = float(v_)
                    elif u == "Z": z = float(v_)
            elif cmd.startswith("G90"):
                abs_xyz = True
            elif cmd.startswith("G91"):
                abs_xyz = False
            elif cmd.startswith("M82"):
                abs_e = True
            elif cmd.startswith("M83"):
                abs_e = False
            elif cmd.startswith("M20"):
                if line[:4].upper().startswith("M200"):
                    mm = re.search(r"[Dd]\s*(-?[\d\.]+)", line)
                    if mm:
                        dv = float(mm.group(1))
                        volumetric = (dv == 0.0)
                        if dv > 0:
                            filament_d = dv
    finally:
        fh.close()

    if layer_idx >= 0:
        close_layer()

    if len(model.layers) == 1 and len(model) > 5000:
        model.warnings.append(
            "Only one layer was detected in a file with thousands of extrusions. "
            "Layer markers and Z moves are both missing, so Z-axis results are "
            "meaningless. This usually means the file is not standard slicer output.")
    if truncated:
        model.warnings.append(
            "G-code exceeded the segment cap; results are based on a partial model.")

    _apply_config(model, filament_d)
    _recompute_widths(model)
    return model


def _arc_points(x0, y0, x1, y1, i, j, r, ccw, max_step=0.4):
    if r is not None and (i == 0 and j == 0):
        # R-form: recover the centre.
        d = math.hypot(x1 - x0, y1 - y0)
        if d < 1e-9 or d > 2 * abs(r):
            return [(x0, y0), (x1, y1)]
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        hsq = r * r - (d / 2) ** 2
        h = math.sqrt(max(hsq, 0.0))
        ux, uy = (x1 - x0) / d, (y1 - y0) / d
        sign = 1 if (r > 0) == ccw else -1
        cx, cy = mx - sign * h * uy, my + sign * h * ux
    else:
        cx, cy = x0 + i, y0 + j
    r0 = math.hypot(x0 - cx, y0 - cy)
    if r0 < 1e-9:
        return [(x0, y0), (x1, y1)]
    a0 = math.atan2(y0 - cy, x0 - cx)
    a1 = math.atan2(y1 - cy, x1 - cx)
    da = a1 - a0
    if ccw:
        while da <= 0: da += 2 * math.pi
    else:
        while da >= 0: da -= 2 * math.pi
    arc_len = abs(da) * r0
    n = max(2, min(200, int(arc_len / max_step) + 1))
    return [(cx + r0 * math.cos(a0 + da * k / n), cy + r0 * math.sin(a0 + da * k / n))
            for k in range(n + 1)]


def _apply_config(model, filament_d):
    cfg = model.config
    hdr = cfg.get("_header", "").lower()
    for name, label in (("orca", "OrcaSlicer"), ("bambu", "BambuStudio"),
                        ("prusa", "PrusaSlicer"), ("super", "SuperSlicer"),
                        ("cura", "Cura")):
        if name in hdr:
            model.slicer = label
            break
    else:
        if "wall_loops" in cfg or "sparse_infill_density" in cfg:
            model.slicer = "OrcaSlicer/BambuStudio"
        elif "perimeters" in cfg:
            model.slicer = "PrusaSlicer/SuperSlicer"
    cfg.setdefault("filament_diameter", str(filament_d))


def _recompute_widths(model):
    """Second pass: now that each layer's true height is known, convert the
    provisional widths (computed against a 0.2 mm placeholder) to real ones."""
    for lay in model.layers:
        h = lay["h"]
        if abs(h - 0.2) < 1e-9:
            continue
        k = 0.2 / h
        for i in range(lay["seg_start"], lay["seg_end"]):
            model.w[i] = model.w[i] * k


# ---------------------------------------------------------------------------
# Section analysis
# ---------------------------------------------------------------------------
#
# A printed part fails at its weakest cross-section.  For each candidate
# cutting plane we need two things:
#
#   A     the area of solid material lying in the plane
#   F     the force that area can carry, which depends on how the beads are
#         oriented relative to the plane normal
#
# For a bead whose axis makes angle t with the plane normal, load is carried
# by the polymer backbone when t = 0 (full UTS) and by the weld between
# adjacent beads when t = 90 (bond strength).  A cos^2 blend between the two
# is the standard first-order lamina result and is what is used here.

class Section:
    """One cutting plane and everything known about it."""

    def __init__(self, axis, pos):
        self.axis = axis           # 'z', 'x' or 'y'
        self.pos = pos             # mm along that axis
        self.area = 0.0            # mm^2 of solid material in the plane
        self.force = 0.0           # N, pure tension normal to the plane
        self.by_feature = {}       # feature code -> mm^2
        self.I1 = self.I2 = 0.0    # second moments about in-plane axes, mm^4
        self.c1 = self.c2 = 0.0    # extreme fibre distances, mm
        self.sigma_eff = 0.0       # area-weighted effective strength, MPa
        self.layer_index = None
        self.arm = 0.0             # lever arm to the loaded end, mm
        self.arm_note = ""
        self.F_break = None        # N at the tip of that arm
        self.defl_at_break = None  # mm
        self.EI = 0.0              # flexural rigidity, N.m^2
        self.bend_axis = ""        # which way it is weakest in bending
        self.enclosed = 0.0        # area enclosed by the wall shell, mm^2
        self.perimeter = 0.0       # wall mid-line perimeter, mm
        self.t_wall = 0.0          # effective wall thickness, mm
        self.open_sum = 0.0        # sum(b*t^3) over wall material
        self.tors_Q = 0.0          # torsional section modulus, mm^3
        self.tors_J = 0.0          # torsional constant, mm^4
        self.tors_model = "none"
        self.kt = 1.0             # geometric stress concentration
        self.kf = 1.0             # what the material actually feels
        self.kt_kind = ""
        self.kt_size = 0.0

    @property
    def S1(self):
        return self.I1 / self.c1 if self.c1 > 1e-9 else 0.0

    @property
    def S2(self):
        return self.I2 / self.c2 if self.c2 > 1e-9 else 0.0


def apply_bending(sec, modulus, arm, arm_note=""):
    """Turn a section's moduli into the numbers an engineer can act on.

    Reports the weaker of the two in-plane bending directions, because the
    load direction is usually not ours to choose, and expresses capacity as
    a force at the end of a lever arm rather than as a moment -- "29 N at
    40 mm" is checkable against the thing you are hanging off the part in a
    way that "1.17 N.m" is not.

    Deflection assumes a cantilever with the load at the tip of that arm,
    which is the standard idealisation and is stated wherever it is shown.
    """
    sec.arm = arm
    sec.arm_note = arm_note
    S1, S2 = sec.S1, sec.S2
    if S1 <= 0 and S2 <= 0:
        return
    # Weak direction: smaller section modulus.
    if S2 <= 0 or (0 < S1 <= S2):
        S, I, sec.bend_axis = S1, sec.I1, "1"
    else:
        S, I, sec.bend_axis = S2, sec.I2, "2"
    sec.EI = modulus * I / 1e6                       # N.mm^2 -> N.m^2
    M = S * sec.sigma_eff                            # N.mm
    if arm > 1e-6:
        sec.F_break = M / arm                        # N at the tip
        if I > 1e-9 and modulus > 0:
            sec.defl_at_break = sec.F_break * arm ** 3 / (3.0 * modulus * I)
    sec.M_break = M / 1000.0                         # N.m


def load_for_deflection(sec, modulus, limit_mm):
    """Tip load that deflects the arm by `limit_mm`.  Cantilever, tip load."""
    I = sec.I1 if sec.bend_axis == "1" else sec.I2
    if I <= 1e-9 or sec.arm <= 1e-6 or modulus <= 0:
        return None
    return 3.0 * modulus * I * limit_mm / sec.arm ** 3


def analyse_layers(model, uts, z_ratio, modulus=0.0, arm_override=None,
                   feats=None, notch_q=0.0):
    """Every layer interface, as a candidate failure plane in Z.

    All beads lie in the plane, so the whole section carries load through
    layer welds -- this is why Z is almost always the weak axis in FDM.

    The lever arm for a layer is everything above it: a part cantilevered
    off the bed bends hardest low down, so the bending-critical layer is
    usually not the same as the thinnest one.
    """
    sigma_z = uts * z_ratio
    z_top = max((l["z"] for l in model.layers), default=0.0)
    sections = []
    for lay in model.layers:
        if lay["index"] == 0:
            continue                      # nothing below the first layer
        sec = Section("z", lay["z"])
        sec.layer_index = lay["index"]
        h = lay["h"]
        eff_area = 0.0
        for feat, vol in lay["vol"].items():
            if feat in NON_PART:
                continue
            a = vol / h if h > 0 else 0.0
            sec.area += a
            sec.by_feature[feat] = sec.by_feature.get(feat, 0.0) + a
            eff_area += a * Z_EFFICIENCY.get(feat, 0.8)
        if sec.area <= 1e-9:
            continue
        sec.force = eff_area * sigma_z
        sec.sigma_eff = sec.force / sec.area
        if feats:
            apply_concentration(sec, feats, notch_q, margin=lay["h"])
            sec.force = sec.sigma_eff * sec.area
        _layer_moments(model, lay, sec, sigma_z)
        arm = arm_override if arm_override is not None else (z_top - sec.pos)
        note = "" if arm_override is not None else "to the top of the part"
        apply_bending(sec, modulus, arm, note)
        sections.append(sec)
    return sections


def _layer_moments(model, lay, sec, sigma_z):
    """Exact area moments of a layer treated as a union of rectangles.

    Each extrusion is a rectangle of length L and width w.  About its own
    centroid I along its axis is L^3 w/12 and across it is L w^3/12; rotate
    both into the global frame and apply the parallel axis theorem.
    """
    sa = 0.0; sx = 0.0; sy = 0.0
    items = []
    # Enclosed area straight off the outer-wall path.  Slicers emit each
    # perimeter as a closed loop, outer contours and holes wound opposite
    # ways, so the signed-area sum needs no ordering and subtracts holes
    # by itself.  The extrusion path is the bead centre line, which is the
    # wall mid-line Bredt's formula wants.
    shoelace = 0.0
    wall_perim = 0.0
    wall_area = 0.0
    open_sum = 0.0
    for i in range(lay["seg_start"], lay["seg_end"]):
        f = model.feat[i]
        if f in NON_PART:
            continue
        if f in (F_OUTER, F_INNER, F_OVERHANG):
            _L = math.hypot(model.x2[i] - model.x1[i], model.y2[i] - model.y1[i])
            wall_area += _L * model.w[i]
            open_sum += _L * model.w[i] ** 3
            if f in (F_OUTER, F_OVERHANG):
                shoelace += (model.x1[i] * model.y2[i] - model.x2[i] * model.y1[i])
                wall_perim += _L
        x1, y1, x2, y2, w = model.x1[i], model.y1[i], model.x2[i], model.y2[i], model.w[i]
        dx, dy = x2 - x1, y2 - y1
        L = math.hypot(dx, dy)
        if L < 1e-9 or w <= 0:
            continue
        a = L * w * Z_EFFICIENCY.get(f, 0.8)
        if a <= 0:
            continue
        cx, cy = (x1 + x2) * 0.5, (y1 + y2) * 0.5
        items.append((cx, cy, dx / L, dy / L, L, w, a))
        sa += a; sx += a * cx; sy += a * cy
    if sa <= 1e-9:
        return
    gx, gy = sx / sa, sy / sa
    Ixx = Iyy = 0.0
    maxy = maxx = 0.0
    for cx, cy, ux, uy, L, w, a in items:
        eff = a / (L * w)
        I_uu = L * w ** 3 / 12.0 * eff     # across the bead
        I_vv = L ** 3 * w / 12.0 * eff     # along the bead
        c2_, s2_ = ux * ux, uy * uy
        Ixx += I_uu * c2_ + I_vv * s2_ + a * (cy - gy) ** 2
        Iyy += I_uu * s2_ + I_vv * c2_ + a * (cx - gx) ** 2
        ey = abs(uy) * L * 0.5 + abs(ux) * w * 0.5
        ex = abs(ux) * L * 0.5 + abs(uy) * w * 0.5
        maxy = max(maxy, abs(cy - gy) + ey)
        maxx = max(maxx, abs(cx - gx) + ex)
    sec.I1, sec.c1 = Ixx, maxy      # bending about X (load along Z)
    sec.I2, sec.c2 = Iyy, maxx      # bending about Y
    sec.centroid = (gx, gy)
    sec.enclosed = abs(shoelace) * 0.5
    sec.perimeter = wall_perim
    sec.t_wall = (wall_area / wall_perim) if wall_perim > 1e-9 else 0.0
    sec.open_sum = open_sum
    sec.tors_Q, sec.tors_J, sec.tors_model = torsion_properties(
        sec.area, sec.enclosed, sec.perimeter, sec.t_wall,
        sec.I1 + sec.I2, sec.open_sum)


def scan_vertical(model, axis, uts, z_ratio, bin_size=0.1, trim=1.0,
                  feats=None, notch_q=0.0):
    """Scan every vertical cutting plane normal to `axis` ('x' or 'y').

    Chord length through a bead is constant as the plane sweeps across it,
    so each bead contributes a flat band to the running total.  That makes
    the whole scan a difference-array accumulation -- O(segments), not
    O(segments * planes).
    """
    n = len(model)
    if n == 0:
        return []
    lo_x, lo_y, hi_x, hi_y = model.bbox()
    lo, hi = (lo_x, hi_x) if axis == "x" else (lo_y, hi_y)
    span = hi - lo
    if span <= 1e-6:
        return []
    nbins = max(4, min(6000, int(span / bin_size) + 1))
    step = span / nbins
    sigma_bond = uts * min(1.0, z_ratio + 0.15)   # side welds beat layer welds

    d_area = [0.0] * (nbins + 2)
    d_force = [0.0] * (nbins + 2)
    d_feat = {}

    for i in range(n):
        f = model.feat[i]
        if f in NON_PART:
            continue
        x1, y1, x2, y2 = model.x1[i], model.y1[i], model.x2[i], model.y2[i]
        w, h = model.w[i], model.h[i]
        if w <= 0 or h <= 0:
            continue
        dx, dy = x2 - x1, y2 - y1
        L = math.hypot(dx, dy)
        if L < 1e-9:
            continue
        ux, uy = dx / L, dy / L
        if axis == "x":
            un, ut = abs(ux), abs(uy)      # along normal / along plane
            a0, b0 = min(x1, x2), max(x1, x2)
        else:
            un, ut = abs(uy), abs(ux)
            a0, b0 = min(y1, y2), max(y1, y2)

        # Chord: how much material the plane cuts through this bead.
        chord = (w / un) if un > 1e-6 else float("inf")
        chord = min(chord, L * ut + w * un)
        if chord <= 0:
            continue
        area = chord * h
        # Orientation blend: backbone when aligned with the normal, weld when
        # lying in the plane.
        sig = (uts * un * un + sigma_bond * ut * ut) * XY_EFFICIENCY.get(f, 0.9)

        half = w * ut * 0.5
        s = int((a0 - half - lo) / step)
        t = int((b0 + half - lo) / step)
        s = max(0, min(nbins - 1, s)); t = max(0, min(nbins - 1, t))
        d_area[s] += area; d_area[t + 1] -= area
        d_force[s] += area * sig; d_force[t + 1] -= area * sig
        if f not in d_feat:
            d_feat[f] = [0.0] * (nbins + 2)
        d_feat[f][s] += area; d_feat[f][t + 1] -= area

    sections = []
    run_a = run_f = 0.0
    runs = {f: 0.0 for f in d_feat}
    for b in range(nbins):
        run_a += d_area[b]; run_f += d_force[b]
        for f in d_feat:
            runs[f] += d_feat[f][b]
        pos = lo + (b + 0.5) * step
        if pos < lo + trim or pos > hi - trim:
            continue
        if run_a <= 1e-6:
            continue
        sec = Section(axis, pos)
        sec.area = run_a
        sec.force = run_f
        sec.sigma_eff = run_f / run_a if run_a > 0 else 0.0
        sec.by_feature = {f: v for f, v in runs.items() if v > 1e-9}
        if feats:
            apply_concentration(sec, feats, notch_q)
            sec.force = sec.sigma_eff * sec.area
        sections.append(sec)
    return sections


def vertical_moments(model, axis, pos, uts, z_ratio):
    """Exact area moments at one vertical plane.  Only run on the few
    critical planes -- this pass is O(segments) each time."""
    sigma_bond = uts * min(1.0, z_ratio + 0.15)
    pts = []
    for i in range(len(model)):
        f = model.feat[i]
        if f in NON_PART:
            continue
        x1, y1, x2, y2 = model.x1[i], model.y1[i], model.x2[i], model.y2[i]
        w, h = model.w[i], model.h[i]
        if w <= 0 or h <= 0:
            continue
        dx, dy = x2 - x1, y2 - y1
        L = math.hypot(dx, dy)
        if L < 1e-9:
            continue
        ux, uy = dx / L, dy / L
        if axis == "x":
            un, ut = abs(ux), abs(uy)
            a0, b0, p1, p2 = min(x1, x2), max(x1, x2), y1, y2
        else:
            un, ut = abs(uy), abs(ux)
            a0, b0, p1, p2 = min(y1, y2), max(y1, y2), x1, x2
        half = w * ut * 0.5
        if pos < a0 - half or pos > b0 + half:
            continue
        chord = (w / un) if un > 1e-6 else float("inf")
        chord = min(chord, L * ut + w * un)
        area = chord * h
        # Where in the plane does the bead cross?
        dn = dx if axis == "x" else dy
        n1 = x1 if axis == "x" else y1
        if abs(dn) > 1e-9:
            t = max(0.0, min(1.0, (pos - n1) / dn))
            q = p1 + (p2 - p1) * t
        else:
            q = (p1 + p2) * 0.5
        zc = _segment_z(model, i)
        sig = (uts * un * un + sigma_bond * ut * ut) * XY_EFFICIENCY.get(f, 0.9)
        pts.append((q, zc, area, sig, chord, h))

    if not pts:
        return None
    sa = sum(p[2] for p in pts)
    gq = sum(p[0] * p[2] for p in pts) / sa
    gz = sum(p[1] * p[2] for p in pts) / sa
    Iq = Iz = 0.0
    cq = cz = 0.0
    for q, zc, a, sig, chord, h in pts:
        Iz += a * (zc - gz) ** 2 + chord * h ** 3 / 12.0
        Iq += a * (q - gq) ** 2 + h * chord ** 3 / 12.0
        cz = max(cz, abs(zc - gz) + h * 0.5)
        cq = max(cq, abs(q - gq) + chord * 0.5)
    return dict(I_bend_vertical=Iz, c_vertical=cz,
                I_bend_lateral=Iq, c_lateral=cq,
                centroid=(gq, gz), area=sa)


def vertical_stations(model, axis, positions, uts, z_ratio, feats=None,
                      notch_q=0.0):
    """Section properties at many vertical planes in a single pass.

    A load case needs moments at every station along the path, and running
    `vertical_moments` once per station would be O(segments x stations).
    Each bead instead accumulates into the stations it spans, which costs
    O(segments + total spans) -- a bead usually spans only a few.
    """
    if not positions:
        return []
    positions = sorted(positions)
    n = len(positions)
    p0 = positions[0]
    step = (positions[-1] - p0) / (n - 1) if n > 1 else 1.0
    if step <= 0:
        return []
    sigma_bond = uts * min(1.0, z_ratio + 0.15)

    A = [0.0] * n; Aq = [0.0] * n; Az = [0.0] * n
    Aq2 = [0.0] * n; Az2 = [0.0] * n; Asig = [0.0] * n
    selfz = [0.0] * n; selfq = [0.0] * n
    qmax = [-1e30] * n; qmin = [1e30] * n
    zmax = [-1e30] * n; zmin = [1e30] * n
    wall_a = [0.0] * n; wall_p = [0.0] * n; open_s = [0.0] * n
    # Silhouette of the part at each station, layer by layer: the width
    # between the outer-wall crossings.  Summing width x layer height gives
    # the enclosed area a lot more faithfully than a bounding box would on
    # anything that is not a rectangle.
    span = {}

    for i in range(len(model)):
        f = model.feat[i]
        if f in NON_PART:
            continue
        x1, y1, x2, y2 = model.x1[i], model.y1[i], model.x2[i], model.y2[i]
        w, h = model.w[i], model.h[i]
        if w <= 0 or h <= 0:
            continue
        dx, dy = x2 - x1, y2 - y1
        L = math.hypot(dx, dy)
        if L < 1e-9:
            continue
        ux, uy = dx / L, dy / L
        if axis == "x":
            un, ut = abs(ux), abs(uy)
            a0, b0 = min(x1, x2), max(x1, x2)
            dn, n1, pp1, pp2 = dx, x1, y1, y2
        else:
            un, ut = abs(uy), abs(ux)
            a0, b0 = min(y1, y2), max(y1, y2)
            dn, n1, pp1, pp2 = dy, y1, x1, x2
        chord = (w / un) if un > 1e-6 else float("inf")
        chord = min(chord, L * ut + w * un)
        if chord <= 0:
            continue
        area = chord * h
        sig = (uts * un * un + sigma_bond * ut * ut) * XY_EFFICIENCY.get(f, 0.9)
        zc = _segment_z(model, i)
        half = w * ut * 0.5

        s = int(math.ceil((a0 - half - p0) / step))
        e = int(math.floor((b0 + half - p0) / step))
        if e < 0 or s > n - 1:
            continue
        s = max(0, s); e = min(n - 1, e)
        for k in range(s, e + 1):
            pos = positions[k]
            if abs(dn) > 1e-9:
                t = (pos - n1) / dn
                t = 0.0 if t < 0 else (1.0 if t > 1 else t)
                q = pp1 + (pp2 - pp1) * t
            else:
                q = (pp1 + pp2) * 0.5
            A[k] += area; Aq[k] += area * q; Az[k] += area * zc
            Aq2[k] += area * q * q; Az2[k] += area * zc * zc
            Asig[k] += area * sig
            selfz[k] += chord * h ** 3 / 12.0
            selfq[k] += h * chord ** 3 / 12.0
            if q + chord * 0.5 > qmax[k]: qmax[k] = q + chord * 0.5
            if q - chord * 0.5 < qmin[k]: qmin[k] = q - chord * 0.5
            if zc + h * 0.5 > zmax[k]: zmax[k] = zc + h * 0.5
            if zc - h * 0.5 < zmin[k]: zmin[k] = zc - h * 0.5
            if f in (F_OUTER, F_INNER, F_OVERHANG):
                wall_a[k] += area
                open_s[k] += chord * h ** 3
                if f in (F_OUTER, F_OVERHANG):
                    key = (k, model.layer[i])
                    cur = span.get(key)
                    lo_q, hi_q = q - chord * 0.5, q + chord * 0.5
                    if cur is None:
                        span[key] = [lo_q, hi_q, h]
                    else:
                        if lo_q < cur[0]: cur[0] = lo_q
                        if hi_q > cur[1]: cur[1] = hi_q

    encl = [0.0] * n
    for (k, _lay), (lo_q, hi_q, hh) in span.items():
        if hi_q > lo_q:
            encl[k] += (hi_q - lo_q) * hh

    out = []
    for k in range(n):
        if A[k] <= 1e-9:
            continue
        gq = Aq[k] / A[k]; gz = Az[k] / A[k]
        sec = Section(axis, positions[k])
        sec.area = A[k]
        sec.sigma_eff = Asig[k] / A[k]
        sec.force = Asig[k]
        sec.I1 = max(Az2[k] - A[k] * gz * gz + selfz[k], 0.0)   # load along Z
        sec.I2 = max(Aq2[k] - A[k] * gq * gq + selfq[k], 0.0)   # load along q
        sec.c1 = max(zmax[k] - gz, gz - zmin[k], 1e-9)
        sec.c2 = max(qmax[k] - gq, gq - qmin[k], 1e-9)
        sec.centroid = (gq, gz)
        if feats:
            apply_concentration(sec, feats, notch_q)
            sec.force = sec.sigma_eff * sec.area
        sec.enclosed = encl[k]
        height = max(zmax[k] - zmin[k], 1e-9)
        mean_w = encl[k] / height if encl[k] > 0 else 0.0
        sec.perimeter = 2.0 * (mean_w + height) if encl[k] > 0 else 0.0
        sec.t_wall = (wall_a[k] / sec.perimeter) if sec.perimeter > 1e-9 else 0.0
        sec.open_sum = open_s[k]
        sec.tors_Q, sec.tors_J, sec.tors_model = torsion_properties(
            sec.area, sec.enclosed, sec.perimeter, sec.t_wall,
            sec.I1 + sec.I2, sec.open_sum)
        out.append(sec)
    return out


_SEG_Z_CACHE = {}


def _segment_z(model, i):
    li = model.layer[i]
    if li in _SEG_Z_CACHE:
        return _SEG_Z_CACHE[li]
    z = 0.0
    for lay in model.layers:
        if lay["index"] == li:
            z = lay["z"] - lay["h"] * 0.5
            break
    _SEG_Z_CACHE[li] = z
    return z


# ---------------------------------------------------------------------------
# STL analysis  (resin, or FDM treated as solid)
# ---------------------------------------------------------------------------
#
# Resin slicers emit proprietary binary formats (.ctb, .pwmx, .goo) with no
# public toolpath semantics, and there are no toolpaths anyway -- each layer
# is cured as a continuous solid.  So for resin the mesh *is* the model, and
# the analysis reduces to finding the smallest cross-section.
#
# Section properties come from contour integrals (Green's theorem) over the
# oriented boundary segments.  Those integrals do not care what order the
# segments are in, only that each is oriented consistently, which the
# triangle winding gives us for free.  No polygon stitching required.

class Mesh:
    def __init__(self, tris, normals):
        self.tris = tris            # list of ((x,y,z) * 3)
        self.normals = normals
        xs = [v[0] for t in tris for v in t]
        ys = [v[1] for t in tris for v in t]
        zs = [v[2] for t in tris for v in t]
        self.bbox = (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))

    def volume(self):
        v = 0.0
        for (a, b, c) in self.tris:
            v += (a[0] * (b[1] * c[2] - c[1] * b[2])
                  - a[1] * (b[0] * c[2] - c[0] * b[2])
                  + a[2] * (b[0] * c[1] - c[0] * b[1])) / 6.0
        return abs(v)


def load_stl(path):
    with open(path, "rb") as fh:
        head = fh.read(5)
        fh.seek(0)
        if head[:5].lower() == b"solid":
            data = fh.read()
            try:
                text = data.decode("utf-8", errors="replace")
            except Exception:
                text = ""
            if "facet" in text and "vertex" in text:
                return _load_stl_ascii(text)
            fh.seek(0)
        return _load_stl_binary(fh)


def _load_stl_ascii(text):
    tris, normals = [], []
    verts = []
    cur_n = (0.0, 0.0, 1.0)
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("facet normal"):
            p = s.split()
            try:
                cur_n = (float(p[2]), float(p[3]), float(p[4]))
            except (ValueError, IndexError):
                cur_n = (0.0, 0.0, 1.0)
            verts = []
        elif s.startswith("vertex"):
            p = s.split()
            verts.append((float(p[1]), float(p[2]), float(p[3])))
        elif s.startswith("endfacet"):
            if len(verts) == 3:
                tris.append(tuple(verts)); normals.append(cur_n)
            verts = []
    if not tris:
        raise ValueError("no triangles found in ASCII STL")
    return Mesh(tris, normals)


def _load_stl_binary(fh):
    fh.seek(80)
    raw = fh.read(4)
    if len(raw) < 4:
        raise ValueError("truncated STL")
    count = struct.unpack("<I", raw)[0]
    body = fh.read(count * 50)
    if len(body) < count * 50:
        count = len(body) // 50
    tris = []; normals = []
    unpack = struct.Struct("<12fH").unpack_from
    for k in range(count):
        v = unpack(body, k * 50)
        normals.append((v[0], v[1], v[2]))
        tris.append(((v[3], v[4], v[5]), (v[6], v[7], v[8]), (v[9], v[10], v[11])))
    if not tris:
        raise ValueError("no triangles found in binary STL")
    return Mesh(tris, normals)


def _section_properties(edges):
    """Area, centroid and second moments from oriented boundary edges.

    edges: list of ((u1,v1),(u2,v2)) oriented so the interior is on the left.
    """
    A2 = 0.0
    for (u1, v1), (u2, v2) in edges:
        A2 += u1 * v2 - u2 * v1
    A = A2 * 0.5
    if abs(A) < 1e-12:
        return None
    sgn = 1.0 if A > 0 else -1.0
    A = abs(A)
    cu = cv = 0.0
    Iuu = Ivv = 0.0
    for (u1, v1), (u2, v2) in edges:
        cr = (u1 * v2 - u2 * v1) * sgn
        cu += (u1 + u2) * cr
        cv += (v1 + v2) * cr
        Ivv += (v1 * v1 + v1 * v2 + v2 * v2) * cr    # about the u axis
        Iuu += (u1 * u1 + u1 * u2 + u2 * u2) * cr    # about the v axis
    cu /= (6.0 * A); cv /= (6.0 * A)
    Iuu = Iuu / 12.0 - A * cu * cu
    Ivv = Ivv / 12.0 - A * cv * cv
    us = [p[0] for e in edges for p in e]
    vs = [p[1] for e in edges for p in e]
    perim = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in edges)
    return dict(area=A, cu=cu, cv=cv, perimeter=perim,
                Iuu=max(Iuu, 0.0), Ivv=max(Ivv, 0.0),
                cmax_u=max(max(us) - cu, cu - min(us)),
                cmax_v=max(max(vs) - cv, cv - min(vs)))


def slice_mesh(mesh, axis, nplanes=400, trim_frac=0.01):
    """Cross-section properties at `nplanes` cuts normal to `axis`."""
    ai = {"x": 0, "y": 1, "z": 2}[axis]
    # In-plane axes, right-handed so orientation works out.
    ui, vi = {"x": (1, 2), "y": (2, 0), "z": (0, 1)}[axis]
    lo = mesh.bbox[ai]; hi = mesh.bbox[ai + 3]
    span = hi - lo
    if span <= 1e-9:
        return []
    pad = span * trim_frac
    lo += pad; hi -= pad
    span = hi - lo
    if span <= 1e-9:
        return []
    step = span / max(1, nplanes - 1)

    # Bucket triangles by the plane range they span so each plane only tests
    # the triangles that actually cross it.
    buckets = [[] for _ in range(nplanes)]
    for ti, tri in enumerate(mesh.tris):
        vals = [tri[0][ai], tri[1][ai], tri[2][ai]]
        tlo, thi = min(vals), max(vals)
        if thi < lo or tlo > hi:
            continue
        s = max(0, int(math.ceil((tlo - lo) / step)))
        t = min(nplanes - 1, int(math.floor((thi - lo) / step)))
        for b in range(s, t + 1):
            buckets[b].append(ti)

    out = []
    for b in range(nplanes):
        if not buckets[b]:
            continue
        pos = lo + b * step
        edges = []
        for ti in buckets[b]:
            tri = mesh.tris[ti]
            seg = _tri_plane_segment(tri, ai, pos, ui, vi, mesh.normals[ti])
            if seg:
                edges.append(seg)
        if len(edges) < 3:
            continue
        props = _section_properties(edges)
        if not props or props["area"] < 1e-6:
            continue
        sec = Section(axis, pos)
        sec.area = props["area"]
        sec.perimeter = props["perimeter"]
        # Normalise to the convention the G-code path uses, so section
        # properties mean the same thing whatever produced them:
        #   I1 / c1  resists a load along Z (or along Y for a Z-cut)
        #   I2 / c2  resists a load along the other in-plane axis
        #   centroid is (in-plane coord, height) for a vertical cut
        if axis == "y":
            # here u=z and v=x, the opposite way round to the other two
            sec.I1 = props["Iuu"]; sec.c1 = props["cmax_u"]
            sec.I2 = props["Ivv"]; sec.c2 = props["cmax_v"]
            sec.centroid = (props["cv"], props["cu"])
        else:
            sec.I1 = props["Ivv"]; sec.c1 = props["cmax_v"]
            sec.I2 = props["Iuu"]; sec.c2 = props["cmax_u"]
            sec.centroid = (props["cu"], props["cv"])
        out.append(sec)
    return out


def _tri_plane_segment(tri, ai, pos, ui, vi, normal):
    """Intersect one triangle with the plane, oriented by the facet normal."""
    d = [tri[k][ai] - pos for k in range(3)]
    pts = []
    for k in range(3):
        k2 = (k + 1) % 3
        d1, d2 = d[k], d[k2]
        if (d1 > 0) != (d2 > 0):
            if abs(d2 - d1) < 1e-15:
                continue
            t = d1 / (d1 - d2)
            p1, p2 = tri[k], tri[k2]
            pts.append((p1[ui] + (p2[ui] - p1[ui]) * t,
                        p1[vi] + (p2[vi] - p1[vi]) * t))
        elif abs(d1) < 1e-12 and abs(d2) < 1e-12:
            return None                     # coplanar face, skip
    if len(pts) != 2:
        return None
    (u1, v1), (u2, v2) = pts
    if abs(u1 - u2) < 1e-12 and abs(v1 - v2) < 1e-12:
        return None
    # Orient: for a CCW section boundary the outward in-plane normal is
    # (dv, -du), which must point the same way as the facet normal.
    nu, nv = normal[ui], normal[vi]
    du, dv = u2 - u1, v2 - v1
    if (dv * nu - du * nv) < 0:
        return ((u2, v2), (u1, v1))
    return ((u1, v1), (u2, v2))


# ---------------------------------------------------------------------------
# Stress concentrations
# ---------------------------------------------------------------------------
#
# Beam theory gives the nominal stress in a section.  What actually starts the
# crack is usually a hole or a sharp inside corner raising that stress locally,
# and a printed part is full of both.  These are the textbook factors, applied
# to geometry read off the slicer's own wall loops.
#
# Kt is the geometric factor; what the material feels is Kf = 1 + q(Kt - 1),
# where q is notch sensitivity.  A brittle polymer feels nearly all of it, a
# ductile one yields locally and redistributes, so q falls with elongation.

KT_CAP = 6.0


def notch_sensitivity(elongation_pct):
    """How much of the geometric factor the material actually feels."""
    if elongation_pct <= 5:
        return 1.0
    return max(0.40, 1.0 - (elongation_pct - 5.0) / 60.0)


def kt_hole(d, w):
    """Circular hole in a finite-width strip, net-section (Peterson's fit)."""
    if w <= d or d <= 0:
        return 1.0
    x = min(d / w, 0.5)
    return max(1.0, 3.00 - 3.13 * x + 3.66 * x ** 2 - 1.53 * x ** 3)


def kt_notch(depth, root_r):
    """Notch of given depth and root radius (Inglis / Neuber form)."""
    if root_r <= 1e-9 or depth <= 0:
        return 1.0
    return min(KT_CAP, 1.0 + 2.0 * math.sqrt(depth / root_r))


def _loops_of_layer(model, lay, tol=0.12):
    """Wall loops for one layer, rebuilt by following the extrusion path.

    Slicers emit each perimeter as one continuous run of moves, so a break in
    the chain is a travel move and therefore a new loop.
    """
    loops = []
    cur = []
    prev = None
    for i in range(lay["seg_start"], lay["seg_end"]):
        f = model.feat[i]
        if f not in (F_OUTER, F_OVERHANG):
            continue
        p1 = (model.x1[i], model.y1[i])
        p2 = (model.x2[i], model.y2[i])
        if prev is None or math.hypot(p1[0] - prev[0], p1[1] - prev[1]) > tol:
            if len(cur) >= 4:
                loops.append(cur)
            cur = [p1]
        cur.append(p2)
        prev = p2
    if len(cur) >= 4:
        loops.append(cur)
    return loops


def _signed_area(pts):
    a = 0.0
    for k in range(len(pts)):
        x1, y1 = pts[k]
        x2, y2 = pts[(k + 1) % len(pts)]
        a += x1 * y2 - x2 * y1
    return a * 0.5


def _centroid(pts):
    n = len(pts)
    return (sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n)


def _resample(pts, step):
    """Even spacing, so curvature is measured over a fixed arc length."""
    out = [pts[0]]
    acc = 0.0
    for k in range(1, len(pts)):
        x0, y0 = pts[k - 1]
        x1, y1 = pts[k]
        d = math.hypot(x1 - x0, y1 - y0)
        if d < 1e-9:
            continue
        acc += d
        while acc >= step:
            t = 1.0 - (acc - step) / d
            out.append((x0 + (x1 - x0) * t, y0 + (y1 - y0) * t))
            acc -= step
    return out


def _convex_hull(pts):
    p = sorted(set((round(a, 4), round(b, 4)) for a, b in pts))
    if len(p) < 3:
        return p
    def half(seq):
        out = []
        for q in seq:
            while len(out) >= 2:
                (ax, ay), (bx, by) = out[-2], out[-1]
                if (bx - ax) * (q[1] - ay) - (by - ay) * (q[0] - ax) > 0:
                    break
                out.pop()
            out.append(q)
        return out
    return half(p)[:-1] + half(reversed(p))[:-1]


def _dist_to_polyline(pt, poly):
    best = float("inf")
    for k in range(len(poly)):
        ax, ay = poly[k]
        bx, by = poly[(k + 1) % len(poly)]
        dx, dy = bx - ax, by - ay
        L2 = dx * dx + dy * dy
        t = 0.0 if L2 < 1e-12 else max(0.0, min(1.0, ((pt[0] - ax) * dx + (pt[1] - ay) * dy) / L2))
        best = min(best, math.hypot(pt[0] - (ax + dx * t), pt[1] - (ay + dy * t)))
    return best


def detect_concentrations(model, bead_w=0.45, max_layers=40):
    """Holes and re-entrant notches in the part's wall loops.

    Sampled over a limited number of layers -- a feature that matters runs
    through many of them, and walking every layer of a tall print to find the
    same hole over and over buys nothing.
    """
    feats = []
    layers = [l for l in model.layers if l["seg_end"] > l["seg_start"]]
    if not layers:
        return feats
    step = max(1, len(layers) // max_layers)
    for lay in layers[::step]:
        loops = _loops_of_layer(model, lay)
        if not loops:
            continue
        signed = [(lp, _signed_area(lp)) for lp in loops]
        outers = [lp for lp, a in signed if a > 0]
        holes = [(lp, -a) for lp, a in signed if a < 0]
        if not outers:
            continue
        outer = max(outers, key=lambda lp: abs(_signed_area(lp)))

        # --- holes -------------------------------------------------------
        for lp, area in holes:
            # The loop is the bead's centre line, so the void is half a bead
            # narrower than the path and the outer edge half a bead wider.
            r = math.sqrt(area / math.pi) - bead_w * 0.5
            if r < bead_w:                      # too small to matter
                continue
            c = _centroid(lp)
            lig = (_dist_to_polyline(c, outer) + bead_w * 0.5) - r
            if lig <= 0:
                continue
            d = 2 * r
            feats.append(dict(kind="hole", x=c[0], y=c[1], z=lay["z"],
                              r=r, size=d, kt=kt_hole(d, d + 2 * lig),
                              layer=lay["index"]))

        # --- re-entrant notches on the outer contour ---------------------
        pts = _resample(outer, max(bead_w, 0.4))
        n = len(pts)
        if n < 12:
            continue
        hull = _convex_hull(outer)
        ccw = _signed_area(outer) > 0
        span = 3
        for k in range(n):
            a = pts[(k - span) % n]
            b = pts[k]
            c = pts[(k + span) % n]
            v1 = (b[0] - a[0], b[1] - a[1])
            v2 = (c[0] - b[0], c[1] - b[1])
            crossz = v1[0] * v2[1] - v1[1] * v2[0]
            # turning into the material is re-entrant
            if (crossz > 0) == ccw:
                continue
            l1 = math.hypot(*v1)
            l2 = math.hypot(*v2)
            if l1 < 1e-9 or l2 < 1e-9:
                continue
            cosang = max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (l1 * l2)))
            turn = math.acos(cosang)
            if turn < 0.35:                     # barely a corner
                continue
            # radius from arc length over turn angle, floored at what the
            # nozzle can physically lay down
            root = max(bead_w * 0.5, (l1 + l2) * 0.5 / max(turn, 1e-6))
            depth = _dist_to_polyline(b, hull)
            if depth < bead_w:
                continue
            kt = kt_notch(depth, root)
            if kt <= 1.05:
                continue
            feats.append(dict(kind="notch", x=b[0], y=b[1], z=lay["z"],
                              r=root, size=depth, kt=kt, layer=lay["index"]))
    # Keep the worst few: many layers report the same feature.
    # The same hole shows up on every layer it passes through.  Merge them
    # and keep the z range, so the factor applies over the feature's real
    # height instead of one sampled layer.
    feats.sort(key=lambda f: -f["kt"])
    merged = []
    for f in feats:
        hit = None
        for m in merged:
            if (abs(f["x"] - m["x"]) < 1.0 and abs(f["y"] - m["y"]) < 1.0
                    and f["kind"] == m["kind"]):
                hit = m
                break
        if hit is not None:
            hit["z_lo"] = min(hit["z_lo"], f["z"])
            hit["z_hi"] = max(hit["z_hi"], f["z"])
            continue
        f["z_lo"] = f["z"]
        f["z_hi"] = f["z"]
        merged.append(f)
        if len(merged) >= 12:
            break
    return merged


def _stitch_loops(edges, tol=1e-4):
    """Turn an unordered set of oriented edges into closed loops."""
    from collections import defaultdict
    key = lambda p: (round(p[0] / tol), round(p[1] / tol))
    nxt = defaultdict(list)
    for a, b in edges:
        nxt[key(a)].append((a, b))
    loops = []
    used = set()
    for i, (a0, b0) in enumerate(edges):
        if i in used:
            continue
        loop = [a0, b0]
        used.add(i)
        cur = b0
        for _ in range(len(edges) + 1):
            cand = None
            for e in nxt.get(key(cur), ()):
                j = edges.index(e)
                if j in used:
                    continue
                cand = (j, e)
                break
            if cand is None:
                break
            j, (a, b) = cand
            used.add(j)
            loop.append(b)
            cur = b
            if math.hypot(b[0] - a0[0], b[1] - a0[1]) < tol * 10:
                break
        if len(loop) >= 4:
            loops.append(loop)
    return loops


def mesh_concentrations(mesh, max_planes=18):
    """Holes and notches in a mesh, from stitched cross-section loops."""
    feats = []
    z0, z1 = mesh.bbox[2], mesh.bbox[5]
    if z1 - z0 <= 1e-9:
        return feats
    n = min(max_planes, 12)
    for k in range(n):
        z = z0 + (z1 - z0) * (k + 0.5) / n
        edges = []
        for ti, tri in enumerate(mesh.tris):
            vals = [tri[0][2], tri[1][2], tri[2][2]]
            if min(vals) > z or max(vals) < z:
                continue
            seg = _tri_plane_segment(tri, 2, z, 0, 1, mesh.normals[ti])
            if seg:
                edges.append(seg)
        if len(edges) < 6:
            continue
        loops = _stitch_loops(edges)
        if not loops:
            continue
        signed = [(lp, _signed_area(lp)) for lp in loops]
        outers = [lp for lp, a in signed if a > 0]
        holes = [(lp, -a) for lp, a in signed if a < 0]
        if not outers:
            continue
        outer = max(outers, key=lambda lp: abs(_signed_area(lp)))
        for lp, area in holes:
            r = math.sqrt(area / math.pi)
            if r < 0.3:
                continue
            c = _centroid(lp)
            lig = _dist_to_polyline(c, outer) - r
            if lig <= 0:
                continue
            feats.append(dict(kind="hole", x=c[0], y=c[1], z=z, r=r,
                              size=2 * r, kt=kt_hole(2 * r, 2 * r + 2 * lig),
                              layer=None))
    feats.sort(key=lambda f: -f["kt"])
    merged = []
    for f in feats:
        if any(abs(f["x"] - m["x"]) < 1.0 and abs(f["y"] - m["y"]) < 1.0 for m in merged):
            continue
        merged.append(f)
        if len(merged) >= 8:
            break
    return merged


def concentrations_for(model):
    """Detected features, cached on the model -- geometry does not change
    between runs, so a slider drag must not re-detect them."""
    cached = getattr(model, "_conc", None)
    if cached is not None:
        return cached
    if isinstance(model, GcodeModel):
        widths = [model.w[i] for i in range(0, len(model), max(1, len(model) // 400))
                  if model.w[i] > 0]
        bead = sorted(widths)[len(widths) // 2] if widths else 0.45
        out = detect_concentrations(model, bead_w=bead)
    else:
        out = mesh_concentrations(model)
    try:
        model._conc = out
    except Exception:
        pass
    return out


def apply_concentration(sec, feats, notch_q, margin=0.0):
    """Raise a section's stress for any concentration it passes through.

    The factor divides the section's allowable, so it flows through tension,
    bending and the load case alike without any of them special-casing it.
    """
    if not feats or not isinstance(notch_q, (int, float)) or notch_q <= 0:
        return
    worst = 1.0
    kind = ""
    size = 0.0
    for f in feats:
        if sec.axis == "z":
            lo = f.get("z_lo", f["z"])
            hi = f.get("z_hi", f["z"])
            hit = (lo - margin) <= sec.pos <= (hi + margin)
        else:
            c = f["x"] if sec.axis == "x" else f["y"]
            hit = abs(c - sec.pos) <= f["r"] + margin
        if hit and f["kt"] > worst:
            worst = f["kt"]
            kind = f["kind"]
            size = f["size"]
    if worst <= 1.0:
        return
    kf = 1.0 + notch_q * (worst - 1.0)
    sec.kt = worst
    sec.kf = kf
    sec.kt_kind = kind
    sec.kt_size = size
    sec.sigma_eff /= kf


def deflection_curve(axes, bbox, fix, fix_c, load_N, load_dir, load_pt, modulus,
                     samples=48):
    """The deflected shape along the load path, not just its end value.

    Same beam model as `load_case_deflection`, integrated twice instead of
    once: curvature from the bending moment, then slope, then displacement.
    The value at the load point comes out equal to the unit-load result, which
    the tests check -- so the curve a viewer draws and the number the report
    quotes cannot drift apart.

    Returns (stations, direction, axis) where stations are (position,
    displacement in mm) and direction is the unit vector the part moves along.
    """
    dom = max("xyz", key=lambda a: abs(load_pt[AXIS_INDEX[a]] - fix_c[AXIS_INDEX[a]]))
    ai = AXIS_INDEX[dom]
    usable = []
    for sec in (axes.get(dom) or []):
        C = section_centroid_3d(sec)
        if C is None or sec.area <= 1e-9:
            continue
        if not separates(fix, bbox, dom, sec.pos, load_pt):
            continue
        usable.append((sec, C))
    if len(usable) < 2:
        return [], (0.0, 0.0, 0.0), dom

    # Integrate outward from the held end.
    fixed_below = fix_c[ai] < load_pt[ai]
    usable.sort(key=lambda t: t[0].pos, reverse=not fixed_below)

    # The part moves along the load, minus whatever runs along the beam.
    n = [0.0, 0.0, 0.0]
    n[ai] = 1.0
    dot = sum(load_dir[k] * n[k] for k in range(3))
    trans = [load_dir[k] - dot * n[k] for k in range(3)]
    tl = math.sqrt(sum(c * c for c in trans))
    direction = tuple(c / tl for c in trans) if tl > 1e-9 else tuple(load_dir)

    kappa = []
    for sec, C in usable:
        r = tuple(load_pt[j] - C[j] for j in range(3))
        m = _cross(r, load_dir)
        mp = MOMENT_MAP[sec.axis]
        m1 = m2 = 0.0
        for gax, slot in mp.items():
            v = m[AXIS_INDEX[gax]]
            if slot == "1":
                m1 = v
            elif slot == "2":
                m2 = v
        mag = math.hypot(m1, m2)
        c = 0.0
        if mag > 1e-12 and modulus > 0:
            comp = 0.0
            if sec.I1 > 1e-9:
                comp += m1 * m1 / sec.I1
            if sec.I2 > 1e-9:
                comp += m2 * m2 / sec.I2
            c = load_N * comp / (modulus * mag)
        kappa.append((sec.pos, c))

    # theta = integral of curvature, v = integral of theta, both from the
    # built-in end where slope and displacement are zero.
    theta = 0.0
    v = 0.0
    out = [(kappa[0][0], 0.0)]
    for k in range(1, len(kappa)):
        ds = abs(kappa[k][0] - kappa[k - 1][0])
        th_prev = theta
        theta += 0.5 * (kappa[k][1] + kappa[k - 1][1]) * ds
        v += 0.5 * (theta + th_prev) * ds
        out.append((kappa[k][0], v))

    if samples and len(out) > samples:
        step = len(out) / float(samples)
        out = [out[min(len(out) - 1, int(i * step))] for i in range(samples)] + [out[-1]]
    return out, direction, dom


# ---------------------------------------------------------------------------
# Torsion
# ---------------------------------------------------------------------------
#
# Torsion is the one place where the obvious shortcut is actively dangerous.
# The polar moment I1+I2 is the right answer only for a circular section.  On
# a closed thin-walled section it overstates torsional stiffness by around
# half again; on an open one -- a wall shell that does not close -- it is out
# by orders of magnitude.  Overstating stiffness is the direction that gets
# parts broken, so none of the models below use it.
#
# A printed part's torsion path is its wall shell, which is a closed thin-
# walled tube, so Bredt's formulas apply:  tau = T/(2*Am*t),  J = 4Am^2*t/P.
# Sparse infill does form closed cells that help, but they are ignored here,
# which errs safe.  A section that is essentially solid gets the compact-
# section approximation instead, and one with no closed wall falls back to
# the open-section formula, which is far weaker -- correctly so.

TORSION_SOLID_THRESHOLD = 0.85     # material / enclosed area
POISSON = 0.35                     # typical for these thermoplastics


def torsion_properties(area, enclosed, perimeter, t_wall, I_polar, open_sum):
    """Torsional section modulus Q and constant J for one section.

    Returns (Q, J, model) where tau_max = T / Q and twist rate = T / (G*J),
    with T in N.mm.  `open_sum` is sum(b*t^3) over the wall material, used
    only when nothing closes.
    """
    solidity = (area / enclosed) if enclosed > 1e-9 else 0.0

    if enclosed > 1e-9 and solidity >= TORSION_SOLID_THRESHOLD:
        # Compact solid section: Saint-Venant approximations.
        Q = 0.20 * area ** 1.5
        J = (area ** 4) / (40.0 * I_polar) if I_polar > 1e-9 else 0.0
        return Q, J, "solid"

    if enclosed > 1e-9 and t_wall > 1e-9 and perimeter > 1e-9:
        Q = 2.0 * enclosed * t_wall                  # Bredt
        J = 4.0 * enclosed ** 2 * t_wall / perimeter
        return Q, J, "closed shell"

    if open_sum > 1e-12 and t_wall > 1e-9:
        J = open_sum / 3.0
        Q = J / t_wall if t_wall > 1e-9 else 0.0
        return Q, J, "open section"

    return 0.0, 0.0, "none"


def shear_modulus(modulus):
    return modulus / (2.0 * (1.0 + POISSON))


# ---------------------------------------------------------------------------
# Fixtures and loads
# ---------------------------------------------------------------------------
#
# A SimulationXpress-style setup: hold something, push somewhere, see what
# breaks.  The physics here is 1D beam theory using the part's real section
# properties, not 3D FEA -- but for a statically determinate single load it
# gives the internal moment at every cut exactly, which is what sets the
# stress in a slender part.
#
# For any cutting plane that separates the fixture from the load, the piece
# on the load side is a free body: the internal moment at that cut is just
# the moment of the applied load about the cut's centroid.  That works for
# straight cantilevers and for L-shaped load paths alike, with no need to
# guess a beam axis.

AXIS_INDEX = {"x": 0, "y": 1, "z": 2}

NAMED_REGIONS = {
    "bottom": ("z", "<"), "top":   ("z", ">"),
    "left":   ("x", "<"), "right": ("x", ">"),
    "front":  ("y", "<"), "back":  ("y", ">"),
}

_REGION_RE = re.compile(r"^\s*([xyz])\s*(<=|>=|<|>)\s*(-?\d*\.?\d+)\s*$", re.I)


class Region:
    """A slab of the bounding box, named or given as an inequality."""

    def __init__(self, axis, lo, hi, label):
        self.axis = axis
        self.lo = lo
        self.hi = hi
        self.label = label

    def contains(self, point):
        v = point[AXIS_INDEX[self.axis]]
        return self.lo - 1e-9 <= v <= self.hi + 1e-9

    def centroid(self, bbox):
        """Mid-point of the slab, centred in the other two axes."""
        x0, y0, z0, x1, y1, z1 = bbox
        c = [(x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2]
        c[AXIS_INDEX[self.axis]] = (self.lo + self.hi) / 2
        return tuple(c)

    def __repr__(self):
        return self.label


def material_centroid(model, region, bbox):
    """Centroid of the material inside a region, not of the bounding box.

    A bounding-box centroid can land in mid-air -- on an L-bracket the box
    centre at the top of the upright leg is off the part entirely, which
    would invent a bending moment that is not there.
    """
    sx = sy = sz = sw = 0.0
    if isinstance(model, GcodeModel):
        for i in range(len(model)):
            if model.feat[i] in NON_PART:
                continue
            w = model.w[i]
            if w <= 0:
                continue
            mx = (model.x1[i] + model.x2[i]) * 0.5
            my = (model.y1[i] + model.y2[i]) * 0.5
            mz = _segment_z(model, i)
            if not region.contains((mx, my, mz)):
                continue
            a = math.hypot(model.x2[i] - model.x1[i],
                           model.y2[i] - model.y1[i]) * w
            sx += mx * a; sy += my * a; sz += mz * a; sw += a
    else:
        for (a_, b_, c_) in model.tris:
            cx = (a_[0] + b_[0] + c_[0]) / 3.0
            cy = (a_[1] + b_[1] + c_[1]) / 3.0
            cz = (a_[2] + b_[2] + c_[2]) / 3.0
            if not region.contains((cx, cy, cz)):
                continue
            u = (b_[0] - a_[0], b_[1] - a_[1], b_[2] - a_[2])
            v = (c_[0] - a_[0], c_[1] - a_[1], c_[2] - a_[2])
            n = _cross(u, v)
            ar = 0.5 * math.sqrt(sum(t * t for t in n))
            sx += cx * ar; sy += cy * ar; sz += cz * ar; sw += ar
    if sw <= 1e-9:
        return region.centroid(bbox), False
    return (sx / sw, sy / sw, sz / sw), True


def parse_region(spec, bbox, band=0.15):
    """'bottom', 'top', 'z<5', 'x >= 30' -> Region.

    Named regions take a band of the bounding box (15% by default) rather
    than a single face, because a printed part is held over an area.
    """
    x0, y0, z0, x1, y1, z1 = bbox
    lim = {"x": (x0, x1), "y": (y0, y1), "z": (z0, z1)}
    s = spec.strip().lower()

    if s in NAMED_REGIONS:
        axis, side = NAMED_REGIONS[s]
        lo, hi = lim[axis]
        span = hi - lo
        if side == "<":
            return Region(axis, lo, lo + span * band, s)
        return Region(axis, hi - span * band, hi, s)

    m = _REGION_RE.match(s)
    if m:
        axis, op, val = m.group(1).lower(), m.group(2), float(m.group(3))
        lo, hi = lim[axis]
        if op in ("<", "<="):
            return Region(axis, lo, min(val, hi), f"{axis}{op}{val:g}")
        return Region(axis, max(val, lo), hi, f"{axis}{op}{val:g}")

    raise ValueError(
        f"cannot read region {spec!r}. Use a name (bottom, top, left, right, "
        f"front, back) or an inequality (z<5, x>30).")


_FORCE_UNITS = {"n": 1.0, "kn": 1000.0, "kgf": 9.80665, "kg": 9.80665,
                "lb": 4.44822, "lbf": 4.44822}

_DIRS = {"+x": (1, 0, 0), "x": (1, 0, 0), "-x": (-1, 0, 0),
         "+y": (0, 1, 0), "y": (0, 1, 0), "-y": (0, -1, 0),
         "+z": (0, 0, 1), "z": (0, 0, 1), "-z": (0, 0, -1),
         "down": (0, 0, -1), "up": (0, 0, 1)}


def parse_load(spec):
    """'50N -Z at top' -> (newtons, unit direction, region string).

    Tolerates '5kg -z top', '50 -Z @ z>35', '2 lb down at right'.
    """
    s = spec.strip()
    parts = re.split(r"\s+(?:at|@|on)\s+", s, maxsplit=1, flags=re.I)
    head = parts[0].strip()
    region = parts[1].strip() if len(parts) > 1 else "top"

    m = re.match(r"^\s*(-?\d*\.?\d+)\s*([a-zA-Z]*)", head)
    if not m:
        raise ValueError(f"cannot read a force from {spec!r}. Try '50N -Z at top'.")
    mag = float(m.group(1))
    unit = (m.group(2) or "n").lower()
    if unit not in _FORCE_UNITS:
        raise ValueError(
            f"unknown force unit {m.group(2)!r}. Use N, kN, kg, kgf, lb.")
    newtons = mag * _FORCE_UNITS[unit]

    rest = head[m.end():].strip().lower()
    if not rest:
        direction = (0, 0, -1)
    elif rest in _DIRS:
        direction = _DIRS[rest]
    else:
        nums = re.findall(r"-?\d*\.?\d+", rest)
        if len(nums) == 3:
            direction = tuple(float(v) for v in nums)
        else:
            raise ValueError(
                f"cannot read a direction from {rest!r}. Use -Z, +X, down, "
                f"or a vector like '0,0,-1'.")
    n = math.sqrt(sum(c * c for c in direction))
    if n < 1e-9:
        raise ValueError("load direction has zero length")
    return newtons, tuple(c / n for c in direction), region


# Which section modulus resists a moment about each global axis, per section
# normal.  ("t" marks the component that twists the section rather than
# bending it.)  S1 goes with I1/c1, S2 with I2/c2.
MOMENT_MAP = {
    "z": {"x": "1", "y": "2", "z": "t"},
    "x": {"y": "1", "z": "2", "x": "t"},
    "y": {"x": "1", "z": "2", "y": "t"},
}


def separates(fix, bbox, axis, pos, load_pt):
    """Does a cut normal to `axis` at `pos` sit between the load and ground?

    The fixture is a region, not a point.  If it straddles the cut, load
    reaches ground without ever crossing it -- an upright bracket held over
    its whole base plate does not load a cut part way along that plate,
    because the material either side of the cut is held anyway.

    A slab fixture spans the full part on the two axes it is not normal to,
    so only cuts normal to the fixture's own axis can ever carry the load.
    """
    ai = AXIS_INDEX[axis]
    if axis == fix.axis:
        f_lo, f_hi = fix.lo, fix.hi
    else:
        f_lo, f_hi = bbox[ai], bbox[ai + 3]
    lp = load_pt[ai]
    if f_lo > pos and f_hi > pos:
        return lp < pos
    if f_lo < pos and f_hi < pos:
        return lp > pos
    return False


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def section_centroid_3d(sec):
    """Full 3D centroid of a section, from its in-plane centroid + position."""
    c = getattr(sec, "centroid", None)
    if c is None:
        return None
    if sec.axis == "z":
        return (c[0], c[1], sec.pos)
    if sec.axis == "x":
        return (sec.pos, c[0], c[1])     # (q, z) = (y, z)
    return (c[0], sec.pos, c[1])         # (q, z) = (x, z)


def solve_load_case(axes, bbox, fix, fix_c, load_N, load_dir, load_pt, scale):
    """Stress and safety factor at every section the load path passes through.

    Returns the governing station plus the full list, so the report can show
    where the margin actually runs out.
    """
    F = tuple(load_N * d for d in load_dir)
    results = []

    for axis, secs in axes.items():
        ai = AXIS_INDEX[axis]
        for sec in secs:
            C = section_centroid_3d(sec)
            if C is None or sec.area <= 1e-9:
                continue
            if not separates(fix, bbox, axis, sec.pos, load_pt):
                continue
            r = tuple(load_pt[k] - C[k] for k in range(3))
            M = _cross(r, F)                       # N.mm
            mp = MOMENT_MAP[axis]
            m1 = m2 = 0.0
            tors = 0.0
            for gax, slot in mp.items():
                v = M[AXIS_INDEX[gax]]
                if slot == "1":
                    m1 = v
                elif slot == "2":
                    m2 = v
                else:
                    tors = v
            S1, S2 = sec.S1, sec.S2
            sigma = 0.0
            if S1 > 1e-9:
                sigma += abs(m1) / S1
            if S2 > 1e-9:
                sigma += abs(m2) / S2
            axial = abs(F[ai])
            sigma += axial / sec.area

            # Transverse shear.  On a short, stubby load path there is barely
            # any lever arm, so bending is small and shear is what actually
            # loads the section -- checking bending alone would miss it.
            shear_V = math.sqrt(sum(F[k] ** 2 for k in range(3) if k != ai))
            tau_v = SHEAR_PEAK_FACTOR * shear_V / sec.area

            # Torsion.  Q is the torsional section modulus for whichever
            # model fits this section -- solid, closed shell or open.  A
            # section with no closed circuit of wall carries torsion very
            # badly, and Q goes to nearly nothing, which is the point.
            tau_t = 0.0
            no_path = False
            if abs(tors) > 1e-9:
                if sec.tors_Q > 1e-9:
                    tau_t = abs(tors) / sec.tors_Q
                else:
                    no_path = True

            # Transverse shear and torsional shear peak at different points
            # on a section, so adding them is conservative rather than exact.
            tau = tau_v + tau_t
            sigma_eq = math.sqrt(sigma * sigma + 3.0 * tau * tau)
            if sigma_eq <= 1e-12 and not no_path:
                continue
            cap = sec.sigma_eff * scale
            sf = 0.0 if no_path else cap / sigma_eq
            results.append(dict(axis=axis, sec=sec, sigma=sigma, tau=tau,
                                tau_shear=tau_v, tau_torsion=tau_t,
                                sigma_eq=sigma_eq, cap=cap,
                                sf=sf, torsion=abs(tors) / 1000.0,
                                torsion_model=sec.tors_model,
                                no_torsion_path=no_path,
                                M1=m1 / 1000.0, M2=m2 / 1000.0,
                                axial_N=axial, shear_N=shear_V,
                                shear_governs=(3.0 * tau_v * tau_v) > (sigma * sigma),
                                torsion_governs=tau_t > tau_v and tau_t > 1e-3))
    if not results:
        return None, []
    worst = min(results, key=lambda r: r["sf"])
    return worst, results


def load_case_deflection(axes, bbox, fix, fix_c, load_N, load_dir, load_pt, modulus):
    """Deflection at the load point, by the unit-load method.

    delta = integral of (M.m)/(EI) along the load path.  Integrated over the
    stations of the dominant axis, so an L-shaped path counts the bending of
    its longest run only -- flagged in the report when that happens.
    """
    dom = max("xyz", key=lambda a: abs(load_pt[AXIS_INDEX[a]] - fix_c[AXIS_INDEX[a]]))
    secs = axes.get(dom) or []
    ai = AXIS_INDEX[dom]
    usable = []
    for sec in secs:
        C = section_centroid_3d(sec)
        if C is None or sec.area <= 1e-9:
            continue
        if not separates(fix, bbox, dom, sec.pos, load_pt):
            continue
        usable.append((sec, C))
    if len(usable) < 2:
        return None, dom, False

    usable.sort(key=lambda t: t[0].pos)
    total = 0.0
    twist = 0.0
    unit = load_dir
    for k, (sec, C) in enumerate(usable):
        if k == 0:
            ds = usable[1][0].pos - usable[0][0].pos
        elif k == len(usable) - 1:
            ds = usable[-1][0].pos - usable[-2][0].pos
        else:
            ds = (usable[k + 1][0].pos - usable[k - 1][0].pos) / 2.0
        ds = abs(ds)
        if ds <= 0:
            continue
        r = tuple(load_pt[j] - C[j] for j in range(3))
        m = _cross(r, unit)                      # moment from a unit load
        mp = MOMENT_MAP[sec.axis]
        m1 = m2 = 0.0
        for gax, slot in mp.items():
            v = m[AXIS_INDEX[gax]]
            if slot == "1":
                m1 = v
            elif slot == "2":
                m2 = v
        I1 = sec.I1 if sec.I1 > 1e-9 else None
        I2 = sec.I2 if sec.I2 > 1e-9 else None
        term = 0.0
        if I1:
            term += m1 * m1 / I1
        if I2:
            term += m2 * m2 / I2
        total += term * ds
        # Twist over the same run: d(theta) = T ds / (G J).
        mt = 0.0
        for gax, slot in mp.items():
            if slot == "t":
                mt = m[AXIS_INDEX[gax]]
        if abs(mt) > 1e-12 and sec.tors_J > 1e-9:
            twist += abs(mt) * load_N * ds / sec.tors_J
    G = shear_modulus(modulus)
    twist_deg = math.degrees(twist / G) if (twist > 0 and G > 0) else 0.0
    if total <= 0:
        return None, dom, False, twist_deg
    # The integration follows one run of the part.  If the load point sits
    # off to the side of that run, there is a second arm cantilevering out
    # whose own bending is not counted, and the answer is an underestimate.
    di = AXIS_INDEX[dom]
    lateral = [AXIS_INDEX[a] for a in "xyz" if AXIS_INDEX[a] != di]
    n = len(usable)
    mean_c = [sum(C[k] for _, C in usable) / n for k in range(3)]
    offset = max(abs(load_pt[k] - mean_c[k]) for k in lateral)
    run = abs(usable[-1][0].pos - usable[0][0].pos) or 1.0
    bent = offset > 0.25 * run
    return load_N * total / modulus, dom, bent, twist_deg


# ---------------------------------------------------------------------------
# Derating
# ---------------------------------------------------------------------------

def thermal_factor(temp_c, hdt):
    """Strength retention at temperature.

    Thermoplastics hold essentially full strength well below HDT, then fall
    off sharply through the glass transition.  The loss is modelled as flat
    until HDT-20, quadratic down to 25% at HDT, then a tail to near zero.
    Quadratic rather than linear because the knee is gradual and the collapse
    is not: PLA at 40 C is fine, PLA at 55 C is not.
    """
    if temp_c is None:
        return 1.0, ""
    knee = hdt - 20.0
    if temp_c <= knee:
        return 1.0, ""
    if temp_c <= hdt:
        f = 1.0 - 0.75 * ((temp_c - knee) / 20.0) ** 2
        note = "approaching HDT" if f < 0.9 else ""
        return f, note
    if temp_c <= hdt + 20:
        f = 0.25 - 0.20 * (temp_c - hdt) / 20.0
        return max(f, 0.05), "above HDT -- the part will soften and creep"
    return 0.03, "far above HDT -- not a structural part at this temperature"


QUALITY_KNOCKDOWN = {"good": 0.95, "typical": 0.85, "poor": 0.70}


class Result:
    pass


def evaluate(sections_by_axis, mat, opts):
    """Reduce per-axis section lists to the governing case and a verdict."""
    res = Result()
    res.material = mat
    res.axes = {}
    res.knockdown = opts["knockdown"]
    res.thermal, res.thermal_note = thermal_factor(opts.get("temp"), mat["props"]["hdt"])
    scale = res.knockdown * res.thermal

    modulus = mat["props"]["mod"]
    for axis, secs in sections_by_axis.items():
        if not secs:
            continue
        tens = min(secs, key=lambda s: s.force if s.force > 0 else float("inf"))
        thin = min(secs, key=lambda s: s.area)
        # The bending-critical section is rarely the thinnest one: capacity
        # falls with section modulus but the moment rises with lever arm.
        bends = [s for s in secs if s.F_break]
        bend = min(bends, key=lambda s: s.F_break) if bends else tens

        d = dict(critical=tens, thinnest=thin, bending=bend, count=len(secs),
                 tension_N=tens.force * scale,
                 sigma=tens.sigma_eff * scale,
                 area=tens.area,
                 bend1_Nm=tens.S1 * tens.sigma_eff * scale / 1000.0,
                 bend2_Nm=tens.S2 * tens.sigma_eff * scale / 1000.0)
        if bend.F_break:
            d.update(
                break_N=bend.F_break * scale,
                moment_Nm=getattr(bend, "M_break", 0.0) * scale,
                arm_mm=bend.arm, arm_note=bend.arm_note,
                EI=bend.EI,
                deflection_mm=(bend.defl_at_break * scale
                               if bend.defl_at_break is not None else None))
            if opts.get("deflect"):
                fd = load_for_deflection(bend, modulus, opts["deflect"])
                d["deflect_limit_N"] = fd
                d["stiffness_governs"] = fd is not None and fd < d["break_N"]
        res.axes[axis] = d

    if res.axes:
        # Govern on the load that actually breaks the part, which for most
        # printed geometry is a bending load, not pure tension.
        withF = {a: d for a, d in res.axes.items() if d.get("break_N")}
        pool = withF or res.axes
        key = "break_N" if withF else "tension_N"
        # Walk a canonical order and only move on a meaningfully smaller
        # value, so a symmetric part does not have its governing axis decided
        # by floating-point noise.
        order = [a for a in ("z", "x", "y") if a in pool]
        res.weakest_axis = order[0]
        for a in order[1:]:
            if pool[a][key] < pool[res.weakest_axis][key] * (1 - 1e-9):
                res.weakest_axis = a
        res.governed_by = "bending" if withF else "tension"
    else:
        res.weakest_axis = None
        res.governed_by = None
    return res


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

BOLD = "\033[1m"; DIM = "\033[2m"; RED = "\033[31m"; YEL = "\033[33m"
GRN = "\033[32m"; CYA = "\033[36m"; RST = "\033[0m"


def _c(s, colour, use):
    return f"{colour}{s}{RST}" if use else s


def fmt_sig(v):
    """Enough significant figures to stay meaningful across six decades."""
    a = abs(v)
    if a >= 100: return f"{v:.0f}"
    if a >= 10:  return f"{v:.1f}"
    if a >= 1:   return f"{v:.2f}"
    if a >= 0.01: return f"{v:.3f}"
    return f"{v:.2e}"


def fmt_defl(v):
    return "<0.05" if 0 <= v < 0.05 else f"{v:.2f}" if v < 1 else f"{v:.1f}"


def fmt_force(n):
    if n >= 1000:
        return f"{n/1000:.2f} kN  ({n/9.81:.0f} kgf)"
    return f"{n:.0f} N  ({n/9.81:.1f} kgf)"


def _loc(axis, sec):
    if axis == "z":
        return (f"layer {sec.layer_index}, z={sec.pos:.1f} mm"
                if sec.layer_index is not None else f"z={sec.pos:.1f} mm")
    return f"{axis}={sec.pos:.1f} mm"


def _composition(sec):
    tot = sum(sec.by_feature.values()) or 1.0
    parts = sorted(sec.by_feature.items(), key=lambda kv: -kv[1])[:4]
    return ", ".join(f"{FEATURE_NAMES[f]} {v/tot*100:.0f}%" for f, v in parts)


AXIS_BEND_DESC = {
    "z": "bent over a layer line (the classic snap)",
    "x": "bent about a plane normal to X",
    "y": "bent about a plane normal to Y",
}

AXIS_LOAD_DESC = {
    "z": "pulling the layers apart (load along Z)",
    "x": "load along X",
    "y": "load along Y",
}


def render_report(res, meta, opts, colour=True):
    L = []
    add = L.append
    C = lambda s, col: _c(s, col, colour)

    mat = res.material
    p = mat["props"]
    add("")
    add(C("=" * 72, DIM))
    add(C(f"  PRINT STRENGTH ESTIMATE  -  {meta['name']}", BOLD))
    add(C("=" * 72, DIM))
    add("")

    # --- inputs -----------------------------------------------------------
    add(C("SOURCE", BOLD))
    for k, v in meta["source_rows"]:
        add(f"  {k:<22} {v}")
    add("")
    add(C("MATERIAL", BOLD))
    add(f"  {'name':<22} {mat['name']}  ({mat['family'].upper()})")
    add(f"  {'in-plane strength':<22} {p['uts']:.0f} MPa")
    if mat["family"] == "fdm":
        add(f"  {'layer adhesion':<22} {p['z']*100:.0f}% of in-plane  "
            f"-> {p['uts']*p['z']:.1f} MPa across layers")
    else:
        add(f"  {'layer adhesion':<22} {p['z']*100:.0f}% (photopolymer, near isotropic)")
    add(f"  {'modulus':<22} {p['mod']:.0f} MPa")
    add(f"  {'elongation at break':<22} {p['elong']:.0f}%"
        + ("   " + C("brittle -- little warning before failure", YEL) if p["elong"] <= 5 else ""))
    add(f"  {'HDT':<22} {p['hdt']:.0f} C")
    add(C(f"  note: {p['notes']}", DIM))
    add("")

    add(C("DERATING", BOLD))
    add(f"  {'process quality':<22} x{res.knockdown:.2f}  ({opts['quality']})")
    tn = f"  ({res.thermal_note})" if res.thermal_note else ""
    if opts.get("temp") is not None:
        add(f"  {'service temperature':<22} x{res.thermal:.2f}  ({opts['temp']:.0f} C){tn}")
    if res.thermal_note:
        add("  " + C(res.thermal_note, YEL if res.thermal > 0.2 else RED))
    add("")

    # --- per axis ---------------------------------------------------------
    order = [a for a in ("z", "x", "y") if a in res.axes]
    add(C("HOW MUCH LOAD IT TAKES", BOLD))
    add(C("  Bending, because that is how printed parts almost always fail.", DIM))
    add("")
    for axis in order:
        d = res.axes[axis]
        is_weak = axis == res.weakest_axis
        head = f"  {axis.upper()} - {AXIS_BEND_DESC[axis]}"
        if is_weak:
            head += "   " + C("<< governs", RED if colour else "")
        add(C(head, BOLD))

        if d.get("break_N"):
            sec = d["bending"]
            loc = _loc(axis, sec)
            note = f" ({sec.arm_note})" if sec.arm_note else ""
            add("      breaks at         "
                + C(fmt_force(d["break_N"]), YEL if is_weak else GRN)
                + f"  applied {d['arm_mm']:.0f} mm out{note}")
            add(C(f"                        = {d['moment_Nm']:.2f} N.m at {loc}", DIM))
            if d.get("deflection_mm") is not None:
                big = d["deflection_mm"] > 0.2 * max(d["arm_mm"], 1e-6)
                add(f"      bends first       {fmt_defl(d['deflection_mm'])} mm "
                    f"at that load" + C("   (cantilever, tip load)", DIM))
                if big:
                    # Linear beam theory assumes the deflection is small next
                    # to the span.  Past roughly a fifth of it the geometry
                    # itself changes and the number stops meaning anything.
                    add("      " + C("^ past small-deflection theory: this part "
                                     "bends out of the way rather than breaking", YEL))
            add(f"      stiffness         EI = {fmt_sig(d['EI'])} N.m^2")
            if d.get("deflect_limit_N") is not None:
                gov = d.get("stiffness_governs")
                add(f"      hits {opts['deflect']:.1f} mm at    "
                    + C(fmt_force(d["deflect_limit_N"]), YEL if gov else GRN)
                    + ("   " + C("<- stiffness governs, not strength", YEL) if gov else ""))
            add(f"      section           {sec.area:.1f} mm^2 at {loc}")
            if sec.kf > 1.001:
                add("      " + C(f"stress raiser: {sec.kt_kind} {sec.kt_size:.1f} mm, "
                                 f"Kt {sec.kt:.2f} -> Kf {sec.kf:.2f} felt "
                                 f"(allowable cut to {sec.sigma_eff:.1f} MPa)", YEL))
            if sec.by_feature:
                add(C(f"                        {_composition(sec)}", DIM))
        else:
            add(C("      no bending capacity computed for this axis", DIM))

        # Tension is the number people expect and the one that almost never
        # governs -- keep it, but keep it in its place.
        t = d["critical"]
        add(C(f"      also: {fmt_force(d['tension_N'])} in pure tension through "
              f"{t.area:.0f} mm^2 at {_loc(axis, t)}", DIM))
        add("")

    # --- fixture / load case ----------------------------------------------
    sim = getattr(res, "sim", None)
    if sim:
        add(C("LOAD CASE", BOLD))
        d = sim["load_dir"]
        dirtxt = "".join(f"{v:+.2f}{a}" for v, a in zip(d, "XYZ") if abs(v) > 1e-6)
        add(f"  held at             {sim['fix']}")
        add(f"  pushed with         {fmt_force(sim['load_N'])}  {dirtxt}"
            f"  at {sim['load_region']}")
        if sim["error"]:
            add("  " + C(sim["error"], RED))
            add("")
        else:
            w = sim["worst"]; sec = w["sec"]
            sf = w["sf"]
            add(f"  sections on the load path  {sim['count']}")
            add("")
            add(C(f"  worst section       {_loc(w['axis'], sec)}  "
                  f"({w['axis'].upper()} cut)", BOLD))
            add(f"      area            {sec.area:.1f} mm^2")
            if sec.by_feature:
                add(C(f"                      {_composition(sec)}", DIM))
            add(f"      stress there    {fmt_sig(w['sigma_eq'])} MPa"
                f"   against {w['cap']:.1f} MPa allowable"
                + C("   (von Mises)", DIM))
            parts = []
            if w["sigma"] > 1e-9:
                parts.append(f"{fmt_sig(w['sigma'])} MPa direct")
            if w["tau_shear"] > 1e-9:
                parts.append(f"{fmt_sig(w['tau_shear'])} MPa shear")
            if w["tau_torsion"] > 1e-3:
                parts.append(f"{fmt_sig(w['tau_torsion'])} MPa torsion")
            if parts:
                add(C(f"                      = {' + '.join(parts)}", DIM))
            bits = []
            if abs(w["M1"]) > 1e-6: bits.append(f"{abs(w['M1']):.2f} N.m bending")
            if abs(w["M2"]) > 1e-6: bits.append(f"{abs(w['M2']):.2f} N.m bending")
            if w["axial_N"] > 1e-6: bits.append(f"{w['axial_N']:.0f} N axial")
            if w["shear_N"] > 1e-6: bits.append(f"{w['shear_N']:.0f} N transverse")
            if bits:
                add(C(f"                      from {' + '.join(bits)}", DIM))
            if w["no_torsion_path"]:
                add("      " + C("this section has no closed wall to carry "
                                 "torsion -- it will unwind rather than resist", RED))
            elif w["torsion_governs"]:
                add("      " + C(f"torsion dominates here, carried by the "
                                 f"{w['torsion_model']}", YEL))
            elif w["shear_governs"]:
                add("      " + C("shear dominates here, not bending -- the load "
                                 "path is too short to develop much moment", YEL))
            if w["tau_torsion"] > 1e-3:
                add(C(f"                      torsion model: {w['torsion_model']}"
                      f" (Q = {fmt_sig(w['sec'].tors_Q)} mm^3)", DIM))
            add(f"      {C('SAFETY FACTOR', BOLD)}   "
                + C(f"{sf:.2f}", RED if sf < 1 else (YEL if sf < 2 else GRN))
                + _verdict(sf, colour))
            if sim["deflection"] is not None:
                note = ""
                if sim["defl_bent"] and sim["deflection"] > 0.05:
                    note = C("   (bending of the "
                             f"{sim['defl_axis'].upper()} run only -- the load "
                             "path turns a corner, so the real figure is larger)", DIM)
                add(f"      deflection      {fmt_defl(sim['deflection'])} mm "
                    f"at the load point" + note)
            if sim.get("twist_deg", 0) > 0.01:
                add(f"      twist           {sim['twist_deg']:.2f} deg over the "
                    f"{sim['defl_axis'].upper()} run")
            pt = sim.get("peak_torsion")
            if pt is not None and pt["sec"] is not w["sec"]:
                add(C(f"      torsion peaks elsewhere: "
                      f"{fmt_sig(pt['tau_torsion'])} MPa at "
                      f"{_loc(pt['axis'], pt['sec'])} "
                      f"(SF {pt['sf']:.2f} there), carried by the "
                      f"{pt['torsion_model']}", DIM))
            add("")
            if sf < 1.0:
                add("  " + C("FAILS under this load case.", RED))
            elif sf < 1.5:
                add("  " + C("Very little margin. Fine only for a static, "
                             "well-understood load.", YEL))
            elif sf < 2.5:
                add("  " + C("Workable margin for a static load; not for impact "
                             "or vibration.", YEL))
            else:
                add("  " + C(f"Comfortable -- {sf:.1f}x margin.", GRN))
            add("")

    # --- applied load -----------------------------------------------------
    if opts.get("load") or opts.get("moment"):
        add(C("APPLIED LOAD", BOLD))
        worst = None
        if opts.get("load"):
            for axis in order:
                d = res.axes[axis]
                if d.get("break_N"):
                    sf = d["break_N"] / opts["load"]
                    worst = sf if worst is None else min(worst, sf)
                    add(f"  {opts['load']:.0f} N at {d['arm_mm']:.0f} mm, bending "
                        f"{axis.upper():<4} safety factor {sf:.2f}" + _verdict(sf, colour))
            for axis in order:
                d = res.axes[axis]
                sf = d["tension_N"] / opts["load"] if opts["load"] > 0 else float("inf")
                add(C(f"  {opts['load']:.0f} N pure tension {axis.upper():<6} "
                      f"safety factor {sf:.2f}", DIM))
        if opts.get("moment"):
            for axis in order:
                d = res.axes[axis]
                cap = max(d["bend1_Nm"], d["bend2_Nm"])
                if cap <= 0:
                    continue
                sf = cap / opts["moment"]
                worst = sf if worst is None else min(worst, sf)
                add(f"  {opts['moment']:.2f} N.m about {axis.upper():<5} safety factor {sf:.2f}"
                    + _verdict(sf, colour))
        add("")
        if worst is not None:
            if worst < 1.0:
                add("  " + C("FAILS. The part cannot carry this load as printed.", RED))
            elif worst < 2.0:
                add("  " + C("Marginal. Fine for a static, well-understood load; "
                             "not for impact, vibration or anything safety related.", YEL))
            else:
                add("  " + C(f"Adequate -- {worst:.1f}x margin on the governing case.", GRN))
            add("")

    # --- advice -----------------------------------------------------------
    rec = recommendations(res, meta, opts)
    if rec:
        add(C("WHAT TO CHANGE", BOLD))
        for r in rec:
            add(f"  - {r}")
        add("")

    if meta.get("warnings"):
        add(C("WARNINGS", BOLD))
        for w in meta["warnings"]:
            add("  ! " + C(w, YEL))
        add("")

    add(C("-" * 72, DIM))
    add(C("  First-order estimate from geometry and published material data.", DIM))
    add(C("  Real parts vary +/-30% with drying, tuning and print temperature,", DIM))
    add(C("  and more than that near stress concentrations. Do not use this as", DIM))
    add(C("  the sole basis for anything load-bearing or safety critical.", DIM))
    add(C("-" * 72, DIM))
    add("")
    return "\n".join(L)


def _verdict(sf, colour):
    if sf < 1.0:
        return "  " + _c("FAIL", RED, colour)
    if sf < 2.0:
        return "  " + _c("marginal", YEL, colour)
    return "  " + _c("ok", GRN, colour)


def recommendations(res, meta, opts):
    out = []
    if not res.axes:
        return out
    mat = res.material
    p = mat["props"]

    cap = lambda d: d.get("break_N") or d["tension_N"]

    # Stiffness before strength: for long or thin parts the thing becomes
    # unusable from flex well before it fractures.
    floppy = [a for a, d in res.axes.items()
              if d.get("deflection_mm") and d["deflection_mm"] > 0.2 * d.get("arm_mm", 1)]
    if floppy:
        out.append(
            f"On {', '.join(x.upper() for x in floppy)} the part deflects more than a "
            f"fifth of its span before breaking, which is past what linear beam theory "
            f"covers -- the deflection figures there are indicative only. In practice a "
            f"part this flexible bends out of the way instead of failing, so design it "
            f"to a deflection limit and ignore the breaking load.")

    stiff = [a for a, d in res.axes.items() if d.get("stiffness_governs")]
    if stiff:
        out.append(
            f"Deflection governs on {', '.join(x.upper() for x in stiff)}, not strength -- "
            f"the part flexes past your limit before it breaks. Stiffness scales with the "
            f"cube of section depth, so making it deeper in the bending direction beats "
            f"any material change; stepping up to a stiffer filament buys far less.")
    elif any(d.get("deflection_mm", 0) and d["deflection_mm"] > 0.15 * d.get("arm_mm", 1)
             for d in res.axes.values()):
        out.append(
            "The part deflects a long way before it breaks. If fit or alignment matters, "
            "design against deflection rather than against fracture -- run --deflect with "
            "your tolerance to see which limit you hit first.")

    # Anisotropy, measured as stress rather than as capacity: the per-axis
    # capacities use different lever arms, so their ratio says nothing about
    # the material.  Effective strength is arm-independent and comparable.
    if "z" in res.axes and len(res.axes) > 1:
        # Strip the stress concentration back out: anisotropy is a property of
        # the material, and a hole that lands on one axis' critical section and
        # not another's would otherwise masquerade as one.
        def _bare(a):
            d = res.axes[a]
            return d["sigma"] * getattr(d["critical"], "kf", 1.0)
        zs = _bare("z")
        inplane = max(_bare(a) for a in res.axes if a != "z")
        if zs > 0 and inplane / zs > 1.25:
            msg = (f"Material is {inplane/zs:.1f}x weaker across layers than in-plane "
                   f"({zs:.0f} vs {inplane:.0f} MPa at the critical sections).")
            if res.weakest_axis == "z":
                msg += (" Z is also what governs here, so reorienting the part to put the "
                        "load in the print plane is the single biggest win available, "
                        "and it costs nothing.")
            else:
                msg += (" Z is not what governs this part, so orientation is already "
                        "working in your favour -- keep it that way.")
            out.append(msg)

    # Where does the weakest layer's area actually come from?
    if "z" in res.axes:
        sec = res.axes["z"].get("bending") or res.axes["z"]["critical"]
        tot = sum(sec.by_feature.values()) or 1.0
        sparse = sec.by_feature.get(F_SPARSE, 0.0) / tot
        walls = (sec.by_feature.get(F_OUTER, 0.0) + sec.by_feature.get(F_INNER, 0.0)) / tot
        if sparse > 0.35:
            out.append(
                f"{sparse*100:.0f}% of the weakest layer is sparse infill, which bonds at "
                f"roughly half the efficiency of walls. Adding wall loops buys more strength "
                f"per gram than raising infill density -- try 4-5 walls before going above 30%.")
        elif walls > 0.8 and sparse < 0.15:
            out.append(
                "The weak section is almost all wall, so it is already efficient. "
                "Further gains need thicker walls or more material, not different settings.")

    # In-plane sections cut between infill lines are carried by walls alone.
    for axis in ("x", "y"):
        if axis not in res.axes:
            continue
        sec = res.axes[axis].get("bending") or res.axes[axis]["critical"]
        tot = sum(sec.by_feature.values()) or 1.0
        wall = (sec.by_feature.get(F_OUTER, 0.0) + sec.by_feature.get(F_INNER, 0.0)) / tot
        if wall > 0.9 and res.weakest_axis == axis:
            out.append(
                f"The governing section ({axis.upper()}) is {wall*100:.0f}% wall -- the cut "
                f"plane falls between infill lines, so infill carries nothing there. "
                f"Wall count is the lever for this part, not infill density.")
            break

    if mat["family"] == "fdm":
        if p["z"] <= 0.45:
            out.append(
                f"{mat['name']} has poor layer adhesion ({p['z']*100:.0f}%). Raise nozzle "
                f"temperature 10-15 C, cut part cooling on internal walls, and use a "
                f"chamber if you have one -- layer adhesion responds strongly to all three.")
        if "CF" in mat["name"] or "GF" in mat["name"]:
            out.append(
                "Fibre-filled filament is stiff but notably worse in Z than its unfilled "
                "base. If the part is Z-loaded, unfilled tends to win outright.")
        if p["elong"] <= 4:
            out.append(
                "Brittle material: it fails suddenly and is sensitive to sharp internal "
                "corners. Fillet them -- a 2 mm radius in a re-entrant corner is often "
                "worth more than any slicer setting.")
        cfg = meta.get("config", {})
        walls_cfg = cfg.get("wall_loops") or cfg.get("perimeters")
        if walls_cfg:
            try:
                if int(float(walls_cfg)) <= 2:
                    out.append(
                        f"Only {int(float(walls_cfg))} wall loops. For a structural part, "
                        f"3-5 is the usual sweet spot.")
            except ValueError:
                pass
    else:
        out.append(
            "Resin is strong but brittle and notch sensitive; it also keeps curing and "
            "embrittling under UV. Post-cure fully, then keep the part out of sunlight.")
        if opts.get("green"):
            out.append(
                "Analysed as green (uncured). A proper post-cure typically recovers "
                "50-100% more strength -- cure it before testing.")

    if opts.get("temp") is not None and res.thermal < 0.9:
        out.append(
            f"At {opts['temp']:.0f} C this material retains only {res.thermal*100:.0f}% of "
            f"its strength. Consider a higher-HDT material "
            f"({'ABS/ASA, PC or a nylon' if mat['family']=='fdm' else 'a high-temp resin'}).")

    out.append(
        "Sustained load is not the same as peak load: all of these polymers creep. "
        "For a part under constant stress, design to roughly half these numbers.")
    return out


# ---------------------------------------------------------------------------
# Drivers
# ---------------------------------------------------------------------------

def run_gcode(path, opts, model=None):
    # `model` lets a container format (a .gcode.3mf) hand over an already
    # parsed model instead of a path.
    if model is None:
        model = parse_gcode(path, max_segments=opts["max_segments"])
    if len(model) == 0:
        raise SystemExit(f"no extrusion moves found in {path} -- is it really G-code?")

    cfg = model.config
    mat = opts.get("material")
    if mat is None:
        guess = cfg.get("filament_type") or cfg.get("filament_settings_id")
        found = lookup_material(guess) if guess else None
        if found is None:
            raise SystemExit(
                "could not determine the material from the G-code "
                f"(filament_type={guess!r}). Pass --material, e.g. --material PETG.\n"
                "Run `printstrength materials` to see what is available.")
        mat = dict(name=found[0], props=found[1], family=found[2], source="G-code")
    if mat["family"] == "resin":
        model.warnings.append(
            f"{mat['name']} is a resin but this is FDM G-code; using it anyway.")

    p = mat["props"]
    uts, zr = p["uts"], p["z"]

    mod = p["mod"]
    feats = concentrations_for(model) if not opts.get("no_kt") else []
    # `qn`, not `q`: the candidate loops below bind `q` to a Section.
    qn = notch_sensitivity(p["elong"])
    z_secs = analyse_layers(model, uts, zr, mod, opts.get("arm"), feats, qn)
    axes = {"z": z_secs}
    if not opts["z_only"]:
        lo_x, lo_y, hi_x, hi_y = model.bbox()
        for axis in ("x", "y"):
            secs = scan_vertical(model, axis, uts, zr, bin_size=opts["bin_size"],
                                 feats=feats, notch_q=qn)
            if secs:
                lo, hi = (lo_x, hi_x) if axis == "x" else (lo_y, hi_y)
                # Section moments cost a full pass each, so only the most
                # promising candidates get one.  Ranking by area alone is not
                # enough: a part can be equally thin at many places and the
                # lever arm is what decides which of them breaks first, so
                # rank by area/arm -- a proxy for the bending demand -- and
                # add the least-loaded sections as a second net.
                def _demand(q):
                    # area carries the capacity, kf takes some of it away, and
                    # the arm sets the demand -- a section weakened by a hole
                    # has to be able to win this ranking or it never gets
                    # evaluated.
                    return q.area / (q.kf * max(max(q.pos - lo, hi - q.pos), 1e-6))
                cands = sorted(secs, key=_demand)[:BEND_CANDIDATES]
                for q in sorted(secs, key=lambda q: q.force)[:BEND_CANDIDATES]:
                    if q not in cands:
                        cands.append(q)
                for q in cands:
                    m = vertical_moments(model, axis, q.pos, uts, zr)
                    if not m:
                        continue
                    q.I1 = m["I_bend_vertical"]; q.c1 = m["c_vertical"]
                    q.I2 = m["I_bend_lateral"]; q.c2 = m["c_lateral"]
                    q.centroid = m["centroid"]
                    if opts.get("arm") is not None:
                        arm, note = opts["arm"], ""
                    else:
                        arm = max(q.pos - lo, hi - q.pos)
                        note = "to the far end"
                    apply_bending(q, mod, arm, note)
            axes[axis] = secs

    res = evaluate(axes, mat, opts)

    lo_x, lo_y, hi_x, hi_y = model.bbox()
    top = max((l["z"] for l in model.layers), default=0.0)
    heights = sorted(l["h"] for l in model.layers) if model.layers else []
    measured = heights[len(heights) // 2] if heights else None
    lh = cfg.get("layer_height")
    lh_txt = (f"{float(lh):.2f} mm" if lh else
              (f"{measured:.2f} mm (measured)" if measured else "?"))
    widths = sorted(model.w[i] for i in range(0, len(model), max(1, len(model) // 400))
                    if model.w[i] > 0)
    ew = widths[len(widths) // 2] if widths else None
    rows = [
        ("file", os.path.basename(path)),
        ("slicer", model.slicer),
    ] + ([("container", cfg["_container"])] if cfg.get("_container") else []) + [
        ("layer height", lh_txt),
    ] + ([("extrusion width", f"{ew:.2f} mm (measured)")] if ew else []) + [
        ("layers", f"{len(model.layers)}"),
        ("bounding box", f"{hi_x-lo_x:.1f} x {hi_y-lo_y:.1f} x {top:.1f} mm"),
        ("extrusions", f"{len(model):,}"),
        ("part volume", f"{model.total_volume/1000:.2f} cm^3"
                        f"  ({model.total_volume*p['rho']/1000:.1f} g)"),
    ]
    for key, label in (("wall_loops", "wall loops"), ("perimeters", "wall loops"),
                       ("sparse_infill_density", "infill"), ("fill_density", "infill"),
                       ("sparse_infill_pattern", "infill pattern"),
                       ("fill_pattern", "infill pattern"),
                       ("nozzle_diameter", "nozzle")):
        if key in cfg and not any(r[0] == label for r in rows):
            rows.append((label, cfg[key]))

    if opts.get("load_case"):
        bbox3 = (lo_x, lo_y, 0.0, hi_x, hi_y, top)
        try:
            attach_load_case(
                res, opts, bbox3, z_secs,
                lambda ax, pos: vertical_stations(model, ax, pos, uts, zr, feats, qn),
                p["mod"], model)
        except ValueError as exc:
            raise SystemExit(str(exc))

    meta = dict(name=os.path.basename(path), source_rows=rows,
                warnings=model.warnings, config=cfg, kind="gcode")
    return res, meta, model


def printed_fraction(area, perimeter, walls, ext_width, infill):
    """How much of a solid cross-section a printed part actually fills.

    A mesh says nothing about walls or infill, so the part is synthesised:
    `walls` perimeters of `ext_width` around the boundary, and `infill` of
    what is left inside.  Far closer to a real print than scaling the whole
    section by one number, and it is the same quantity the G-code path
    measures directly.

    Returns (material_area, wall_area, infill_area).
    """
    if area <= 0:
        return 0.0, 0.0, 0.0
    shell = max(0.0, walls * ext_width)
    wall_area = min(area, perimeter * shell)
    inner = max(0.0, area - wall_area)
    infill_area = inner * max(0.0, min(1.0, infill))
    return wall_area + infill_area, wall_area, infill_area


def run_stl(path, opts, mesh=None):
    if mesh is None:
        mesh = load_stl(path)
    mat = opts.get("material")
    if mat is None:
        raise SystemExit(
            "STL files carry no material information -- pass --material "
            "(e.g. --material 'tough' for resin, --material PLA for FDM).")
    p = mat["props"]
    uts, zr = p["uts"], p["z"]
    solid = opts.get("solid_fraction")

    x0, y0, z0, x1, y1, z1 = mesh.bbox
    ranges = {"z": (z0, z1), "x": (x0, x1), "y": (y0, y1)}
    feats = concentrations_for(mesh) if not opts.get("no_kt") else []
    qn = notch_sensitivity(p["elong"])
    lh = opts.get("layer_height") or 0.2
    ext_w = opts.get("ext_width") or (opts.get("nozzle") or 0.4) * 1.05
    walls = opts.get("walls") if opts.get("walls") is not None else 3
    infill = opts.get("infill") if opts.get("infill") is not None else 0.20
    axes = {}
    for axis in ("z", "x", "y"):
        # Z planes land on real layers when a layer height is known.
        nplanes = opts["planes"]
        if axis == "z" and lh > 0:
            span = ranges["z"][1] - ranges["z"][0]
            nplanes = max(4, min(1200, int(round(span / lh))))
        secs = slice_mesh(mesh, axis, nplanes=nplanes)
        lo, hi = ranges[axis]
        for s in secs:
            if axis == "z":
                sig = uts * zr
            else:
                sig = uts if mat["family"] == "resin" else uts * min(1.0, zr + 0.15)
            # An explicit --solid-fraction is a direct override; otherwise
            # the section is filled the way the print settings say.
            if solid is not None:
                frac = solid
            else:
                mat_a, wall_a, inf_a = printed_fraction(
                    s.area, s.perimeter, walls, ext_w, infill)
                frac = mat_a / s.area if s.area > 1e-9 else 1.0
                s.by_feature = {F_OUTER: wall_a, F_SPARSE: inf_a}
            s.area *= frac
            s.I1 *= frac; s.I2 *= frac
            s.sigma_eff = sig
            if feats:
                apply_concentration(s, feats, qn)
            s.force = s.area * s.sigma_eff
            if opts.get("arm") is not None:
                arm, note = opts["arm"], ""
            elif axis == "z":
                arm, note = hi - s.pos, "to the top of the part"
            else:
                arm, note = max(s.pos - lo, hi - s.pos), "to the far end"
            apply_bending(s, p["mod"], arm, note)
        axes[axis] = secs
    if opts["z_only"]:
        axes = {"z": axes["z"]}

    if opts.get("green") and mat["family"] == "resin":
        for secs in axes.values():
            for s in secs:
                s.force *= 0.55
                s.sigma_eff *= 0.55
                if s.F_break:
                    s.F_break *= 0.55
                    if s.defl_at_break is not None:
                        s.defl_at_break *= 0.55

    res = evaluate(axes, mat, opts)

    vol = mesh.volume() * (solid if solid is not None else 1.0)
    rows = [
        ("file", os.path.basename(path)),
        ("triangles", f"{len(mesh.tris):,}"),
        ("bounding box", f"{x1-x0:.1f} x {y1-y0:.1f} x {z1-z0:.1f} mm"),
        ("volume", f"{vol/1000:.2f} cm^3  ({vol*p['rho']/1000:.1f} g)"),
    ]
    if solid is not None:
        rows.append(("assumed solidity", f"{solid*100:.0f}%"))
    else:
        rows += [
            ("layer height", f"{lh:.2f} mm  (assumed)"),
            ("extrusion width", f"{ext_w:.2f} mm"
                                + (f"  (from a {opts['nozzle']:.1f} nozzle)"
                                   if opts.get("nozzle") and not opts.get("ext_width") else "")),
            ("wall loops", f"{walls}  (assumed)"),
            ("infill", f"{infill*100:.0f}%  (assumed)"),
        ]
    warn = ["Mesh analysis assumes a solid part -- it has no knowledge of walls, "
            "infill or toolpaths. For FDM, analyse the sliced G-code instead."]
    if mat["family"] == "fdm":
        warn.append("FDM material on a mesh: use --solid-fraction to approximate infill, "
                    "or better, slice it and analyse the G-code.")
    if opts.get("load_case"):
        bbox3 = (x0, y0, z0, x1, y1, z1)
        try:
            attach_load_case(
                res, opts, bbox3, axes.get("z", []),
                lambda ax, pos: axes.get(ax, []), p["mod"], mesh)
        except ValueError as exc:
            raise SystemExit(str(exc))

    meta = dict(name=os.path.basename(path), source_rows=rows, warnings=warn,
                config={}, kind="stl")
    return res, meta, mesh


def region_centroid(sim_axes, region, bbox, model):
    """Where the material in a region actually sits.

    Computed from the cross-sections rather than from the mesh triangles: a
    coarse STL has faces spanning the whole part, so a triangle-centroid
    average collapses a slab onto whichever end cap happens to fall inside
    it.  Section areas do not have that problem at any mesh density.
    """
    secs = sim_axes.get(region.axis) or []
    inside = [q for q in secs
              if region.lo - 1e-9 <= q.pos <= region.hi + 1e-9 and q.area > 1e-9]
    pts = []
    for q in inside:
        c = section_centroid_3d(q)
        if c is not None:
            pts.append((c, q.area))
    if pts:
        tot = sum(a for _, a in pts)
        return tuple(sum(c[k] * a for c, a in pts) / tot for k in range(3)), True
    if model is not None:
        return material_centroid(model, region, bbox)
    return region.centroid(bbox), False


def attach_load_case(res, opts, bbox3, z_secs, station_fn, modulus, model=None):
    """Solve the fixture/load case and hang the result off `res`.

    `station_fn(axis, positions)` supplies section properties along each
    vertical axis; G-code and mesh inputs provide their own.
    """
    if not opts.get("load_case"):
        return
    fix = parse_region(opts.get("fix") or "bottom", bbox3)
    load_N, load_dir, load_region = parse_load(opts["load_case"])
    lr = parse_region(load_region, bbox3)

    sim_axes = {"z": z_secs}
    for axis in ("x", "y"):
        ai = AXIS_INDEX[axis]
        lo, hi = bbox3[ai], bbox3[ai + 3]
        if hi - lo <= 1e-6:
            continue
        pos = [lo + (hi - lo) * k / (LOAD_STATIONS - 1)
               for k in range(LOAD_STATIONS)]
        sim_axes[axis] = station_fn(axis, pos)

    load_pt, lr_solid = region_centroid(sim_axes, lr, bbox3, model)
    fix_c, fix_solid = region_centroid(sim_axes, fix, bbox3, model)

    scale = res.knockdown * res.thermal
    worst, allr = solve_load_case(sim_axes, bbox3, fix, fix_c, load_N,
                                  load_dir, load_pt, scale)
    if worst is None:
        res.sim = dict(error=(
            f"no section carries this load. Nothing is cut off from {fix} by "
            f"the load path -- with the part held over that whole region the "
            f"load reaches ground without stressing any section. Hold a "
            f"smaller area (e.g. --fix 'x<5') or load somewhere else."),
            fix=fix, load_N=load_N, load_dir=load_dir,
            load_region=lr, load_pt=load_pt, fix_pt=fix_c)
        return
    defl, dom, bent, twist = load_case_deflection(
        sim_axes, bbox3, fix, fix_c, load_N, load_dir, load_pt, modulus)
    curve, curve_dir, curve_axis = deflection_curve(
        sim_axes, bbox3, fix, fix_c, load_N, load_dir, load_pt, modulus)
    # Torsion often peaks somewhere other than the governing section.  Keep
    # that one too, so a reported twist is never left unexplained.
    tw = [r for r in allr if r["tau_torsion"] > 1e-3]
    peak_t = max(tw, key=lambda r: r["tau_torsion"]) if tw else None
    res.sim = dict(fix=fix, load_N=load_N, load_dir=load_dir, load_region=lr,
                   load_pt=load_pt, fix_pt=fix_c, worst=worst, count=len(allr),
                   peak_torsion=peak_t,
                   on_material=lr_solid and fix_solid,
                   deflection=defl, defl_axis=dom, defl_bent=bent,
                   twist_deg=twist, curve=curve, curve_dir=curve_dir,
                   curve_axis=curve_axis, error=None)



def to_json(res, meta, opts):
    def sec_json(s):
        return dict(axis=s.axis, position_mm=round(s.pos, 3),
                    layer=s.layer_index, area_mm2=round(s.area, 3),
                    effective_strength_MPa=round(s.sigma_eff, 3),
                    section_modulus_mm3=[round(s.S1, 2), round(s.S2, 2)],
                    stress_concentration=(dict(kt=round(s.kt, 3), kf=round(s.kf, 3),
                                               kind=s.kt_kind, size_mm=round(s.kt_size, 3))
                                          if s.kf > 1.001 else None),
                    composition={FEATURE_NAMES[f]: round(v, 3)
                                 for f, v in s.by_feature.items()})
    out = dict(
        tool="printstrength", version=__version__,
        source=dict(meta["source_rows"]), kind=meta["kind"],
        material=dict(name=res.material["name"], family=res.material["family"],
                      **res.material["props"]),
        derating=dict(process_quality=res.knockdown, thermal=round(res.thermal, 3),
                      thermal_note=res.thermal_note),
        weakest_axis=res.weakest_axis,
        governed_by=getattr(res, "governed_by", None),
        axes={a: dict(
                  breaks_at_N=(round(d["break_N"], 4) if d.get("break_N") else None),
                  lever_arm_mm=(round(d["arm_mm"], 2) if d.get("break_N") else None),
                  moment_Nm=(round(d["moment_Nm"], 4) if d.get("break_N") else None),
                  deflection_at_break_mm=(round(d["deflection_mm"], 3)
                                          if d.get("deflection_mm") is not None else None),
                  flexural_rigidity_Nm2=(round(d["EI"], 6) if d.get("break_N") else None),
                  deflection_limit_load_N=(round(d["deflect_limit_N"], 4)
                                           if d.get("deflect_limit_N") else None),
                  stiffness_governs=d.get("stiffness_governs"),
                  bending_section=(sec_json(d["bending"]) if d.get("break_N") else None),
                  tension_N=round(d["tension_N"], 4),
                  effective_strength_MPa=round(d["sigma"], 2),
                  critical_section=sec_json(d["critical"]))
              for a, d in res.axes.items()},
        recommendations=recommendations(res, meta, opts),
        warnings=meta.get("warnings", []),
    )
    sim = getattr(res, "sim", None)
    if sim:
        lc = dict(fixture=str(sim["fix"]),
                  load_N=round(sim["load_N"], 5),
                  load_direction=[round(v, 4) for v in sim["load_dir"]],
                  load_region=str(sim["load_region"]),
                  load_point=[round(v, 3) for v in sim.get("load_pt", ())],
                  fixture_point=[round(v, 3) for v in sim.get("fix_pt", ())],
                  error=sim["error"])
        if not sim["error"]:
            w = sim["worst"]
            lc.update(safety_factor=round(w["sf"], 3),
                      stress_MPa=round(w["sigma_eq"], 4),
                      direct_stress_MPa=round(w["sigma"], 4),
                      shear_stress_MPa=round(w["tau_shear"], 4),
                      torsion_stress_MPa=round(w["tau_torsion"], 4),
                      total_shear_MPa=round(w["tau"], 4),
                      shear_governs=w["shear_governs"],
                      torsion_governs=w["torsion_governs"],
                      torsion_model=w["torsion_model"],
                      torsion_Q_mm3=round(w["sec"].tors_Q, 3),
                      torsion_J_mm4=round(w["sec"].tors_J, 2),
                      no_torsion_path=w["no_torsion_path"],
                      twist_deg=round(sim.get("twist_deg", 0.0), 4),
                      peak_torsion=(dict(
                          stress_MPa=round(sim["peak_torsion"]["tau_torsion"], 4),
                          safety_factor=round(sim["peak_torsion"]["sf"], 3),
                          axis=sim["peak_torsion"]["axis"],
                          position_mm=round(sim["peak_torsion"]["sec"].pos, 3),
                          model=sim["peak_torsion"]["torsion_model"])
                          if sim.get("peak_torsion") else None),
                      shear_N=round(w["shear_N"], 2),
                      allowable_MPa=round(w["cap"], 3),
                      bending_Nm=[round(w["M1"], 4), round(w["M2"], 4)],
                      torsion_Nm=round(w["torsion"], 4),
                      axial_N=round(w["axial_N"], 2),
                      sections_on_path=sim["count"],
                      deflection_mm=(round(sim["deflection"], 4)
                                     if sim["deflection"] is not None else None),
                      deflection_underestimated=sim["defl_bent"],
                      deflected_shape=dict(
                          axis=sim.get("curve_axis"),
                          direction=[round(v, 5) for v in sim.get("curve_dir", ())],
                          stations=[[round(a, 4), round(b, 6)] for a, b in sim.get("curve", [])]),
                      worst_section=sec_json(w["sec"]))
        out["load_case"] = lc
    if opts.get("load"):
        out["applied_load_N"] = opts["load"]
        out["safety_factors"] = {
            a: dict(bending=(round(d["break_N"] / opts["load"], 3)
                             if d.get("break_N") else None),
                    tension=round(d["tension_N"] / opts["load"], 3))
            for a, d in res.axes.items()}
    return json.dumps(out, indent=2)


# ---------------------------------------------------------------------------
# Structured API for a front end
# ---------------------------------------------------------------------------
#
# The CLI reports the governing case.  A UI wants the whole curve -- capacity
# at every station so you can see where the margin runs out, and the geometry
# of the critical section so you can see what is carrying the load.  These
# helpers expose that without changing anything the CLI relies on.

UI_PROFILE_STATIONS = LOAD_STATIONS
UI_MAX_OUTLINE = 4000


def _downsample(items, limit):
    if len(items) <= limit:
        return items
    step = len(items) / float(limit)
    return [items[min(len(items) - 1, int(k * step))] for k in range(limit)]


def profile_axis(secs, scale, load_lookup=None):
    """Capacity (and safety factor, when a load case is set) along one axis."""
    out = []
    for sec in _downsample(secs, UI_PROFILE_STATIONS):
        row = dict(pos=round(sec.pos, 3), area=round(sec.area, 3),
                   sigma=round(sec.sigma_eff * scale, 3))
        if sec.F_break:
            row["break_N"] = round(sec.F_break * scale, 2)
            row["arm"] = round(sec.arm, 2)
        if load_lookup is not None:
            r = load_lookup.get(id(sec))
            if r:
                row["sf"] = round(r["sf"], 4)
                row["stress"] = round(r["sigma_eq"], 4)
                row["cap"] = round(r["cap"], 4)
        out.append(row)
    return out


def section_outline(model, axis, pos):
    """The material in one cutting plane, as rectangles a UI can draw.

    For a layer these are the beads themselves; for a vertical cut they are
    the footprints where beads cross the plane.  Coordinates are returned in
    the plane's own two axes so the front end can draw them directly.
    """
    items = []
    if isinstance(model, Mesh):
        ai = {"x": 0, "y": 1, "z": 2}[axis]
        ui, vi = {"x": (1, 2), "y": (2, 0), "z": (0, 1)}[axis]
        for ti, tri in enumerate(model.tris):
            vals = [tri[0][ai], tri[1][ai], tri[2][ai]]
            if min(vals) > pos or max(vals) < pos:
                continue
            seg = _tri_plane_segment(tri, ai, pos, ui, vi, model.normals[ti])
            if seg:
                (u1, v1), (u2, v2) = seg
                if axis == "y":
                    u1, v1, u2, v2 = v1, u1, v2, u2
                items.append(dict(kind="line", a=[round(u1, 3), round(v1, 3)],
                                  b=[round(u2, 3), round(v2, 3)], f="outline"))
            if len(items) >= UI_MAX_OUTLINE:
                break
        return items

    if axis == "z":
        lay = None
        for l in model.layers:
            if abs(l["z"] - pos) < 1e-6:
                lay = l
                break
        if lay is None:
            lay = min(model.layers, key=lambda l: abs(l["z"] - pos))
        rng = range(lay["seg_start"], lay["seg_end"])
        for i in _downsample(list(rng), UI_MAX_OUTLINE):
            f = model.feat[i]
            if f in NON_PART:
                continue
            items.append(dict(kind="bead",
                              a=[round(model.x1[i], 3), round(model.y1[i], 3)],
                              b=[round(model.x2[i], 3), round(model.y2[i], 3)],
                              w=round(model.w[i], 3),
                              f=FEATURE_NAMES.get(f, "other")))
        return items

    # Vertical cut: each crossing bead becomes a chord x layer-height rect.
    for i in range(len(model)):
        f = model.feat[i]
        if f in NON_PART:
            continue
        x1, y1, x2, y2 = model.x1[i], model.y1[i], model.x2[i], model.y2[i]
        w, h = model.w[i], model.h[i]
        if w <= 0 or h <= 0:
            continue
        dx, dy = x2 - x1, y2 - y1
        L = math.hypot(dx, dy)
        if L < 1e-9:
            continue
        ux, uy = dx / L, dy / L
        if axis == "x":
            un, ut = abs(ux), abs(uy)
            a0, b0, p1, p2, dn, n1 = min(x1, x2), max(x1, x2), y1, y2, dx, x1
        else:
            un, ut = abs(uy), abs(ux)
            a0, b0, p1, p2, dn, n1 = min(y1, y2), max(y1, y2), x1, x2, dy, y1
        half = w * ut * 0.5
        if pos < a0 - half or pos > b0 + half:
            continue
        chord = (w / un) if un > 1e-6 else float("inf")
        chord = min(chord, L * ut + w * un)
        if abs(dn) > 1e-9:
            t = max(0.0, min(1.0, (pos - n1) / dn))
            q = p1 + (p2 - p1) * t
        else:
            q = (p1 + p2) * 0.5
        zc = _segment_z(model, i)
        items.append(dict(kind="rect",
                          a=[round(q - chord / 2, 3), round(zc, 3)],
                          b=[round(q + chord / 2, 3), round(zc + h, 3)],
                          f=FEATURE_NAMES.get(f, "other")))
        if len(items) >= UI_MAX_OUTLINE:
            break
    return items


def ui_analyze(path, options=None):
    """One call returning everything a front end needs, as plain data."""
    o = dict(options or {})

    class A:
        pass
    a = A()
    a.material = o.get("material")
    a.load = o.get("load")
    a.fix = o.get("fix")
    a.temp = o.get("temp")
    a.quality = o.get("quality", "typical")
    a.uts = o.get("uts")
    a.layer_adhesion = o.get("layer_adhesion")
    a.solid_fraction = o.get("solid_fraction")
    a.layer_height = o.get("layer_height")
    a.nozzle = o.get("nozzle")
    a.ext_width = o.get("ext_width")
    a.walls = o.get("walls")
    a.infill = o.get("infill") * 100 if o.get("infill") is not None else None
    a.planes = o.get("planes", 400)
    a.bin_size = o.get("bin_size", 0.1)
    a.z_only = o.get("z_only", False)
    a.green = o.get("green", False)
    a.arm = o.get("arm")
    a.deflect = o.get("deflect")
    a.max_segments = o.get("max_segments", 4_000_000)

    try:
        opts = build_opts(a)
    except SystemExit as exc:
        return dict(ok=False, error=str(exc))

    try:
        res, meta, model = analyse_path(path, opts)
    except SystemExit as exc:
        return dict(ok=False, error=str(exc))
    except Exception as exc:
        return dict(ok=False, error=f"{type(exc).__name__}: {exc}")

    out = json.loads(to_json(res, meta, opts))
    out["ok"] = True
    out["error"] = None
    scale = res.knockdown * res.thermal

    # Profiles, plus the safety factor at every station when a load case is on.
    sim = getattr(res, "sim", None)
    lookup = {}
    prof_axes = {}
    if isinstance(model, GcodeModel):
        p = res.material["props"]
        uts, zr = p["uts"], p["z"]
        lo_x, lo_y, hi_x, hi_y = model.bbox()
        top = max((l["z"] for l in model.layers), default=0.0)
        bbox3 = (lo_x, lo_y, 0.0, hi_x, hi_y, top)
        _feats = concentrations_for(model)
        _q = notch_sensitivity(p["elong"])
        z_secs = analyse_layers(model, uts, zr, p["mod"], opts.get("arm"), _feats, _q)
        prof_axes["z"] = z_secs
        for axis in ("x", "y"):
            ai = AXIS_INDEX[axis]
            lo, hi = bbox3[ai], bbox3[ai + 3]
            if hi - lo <= 1e-6:
                continue
            pos = [lo + (hi - lo) * k / (UI_PROFILE_STATIONS - 1)
                   for k in range(UI_PROFILE_STATIONS)]
            st = vertical_stations(model, axis, pos, uts, zr, _feats, _q)
            for q in st:
                arm = (opts["arm"] if opts.get("arm") is not None
                       else max(q.pos - lo, hi - q.pos))
                apply_bending(q, p["mod"], arm,
                              "" if opts.get("arm") is not None else "to the far end")
            prof_axes[axis] = st
    else:
        bbox3 = model.bbox
        for axis in ("z", "x", "y"):
            prof_axes[axis] = slice_mesh(model, axis, nplanes=UI_PROFILE_STATIONS)

    if sim and not sim["error"]:
        fix = sim["fix"]
        _, allr = solve_load_case(prof_axes, bbox3, fix, sim["fix_pt"],
                                  sim["load_N"], sim["load_dir"],
                                  sim["load_pt"], scale)
        for r in allr:
            lookup[id(r["sec"])] = r

    out["profiles"] = {ax: profile_axis(secs, scale, lookup if lookup else None)
                       for ax, secs in prof_axes.items() if secs}

    # Geometry of the sections worth looking at.
    crit = {}
    for ax, d in res.axes.items():
        sec = d.get("bending") or d["critical"]
        try:
            crit[ax] = dict(pos=round(sec.pos, 3), axis=ax,
                            area=round(sec.area, 3),
                            outline=section_outline(model, ax, sec.pos))
        except Exception:
            pass
    if sim and not sim["error"]:
        w = sim["worst"]
        try:
            crit["load_case"] = dict(pos=round(w["sec"].pos, 3), axis=w["axis"],
                                     area=round(w["sec"].area, 3),
                                     outline=section_outline(model, w["axis"],
                                                             w["sec"].pos))
        except Exception:
            pass
    out["sections"] = crit
    out["concentrations"] = [
        dict(kind=f["kind"], x=round(f["x"], 3), y=round(f["y"], 3),
             z=round(f["z"], 3), r=round(f["r"], 3), size=round(f["size"], 3),
             kt=round(f["kt"], 3)) for f in concentrations_for(model)]
    out["notch_sensitivity"] = round(notch_sensitivity(res.material["props"]["elong"]), 3)
    out["bbox"] = [round(v, 3) for v in bbox3]
    out["materials"] = dict(
        fdm=sorted(FDM_MATERIALS), resin=sorted(RESIN_MATERIALS))
    return out


def cmd_materials(args):
    print()
    print(f"{'FDM FILAMENTS':<14}{'UTS':>6} {'Z%':>5} {'MOD':>7} {'HDT':>6} {'ELONG':>7}")
    print("-" * 72)
    for k, v in FDM_MATERIALS.items():
        print(f"{k:<14}{v['uts']:>5.0f} {v['z']*100:>5.0f} {v['mod']:>7.0f} "
              f"{v['hdt']:>5.0f} {v['elong']:>6.0f}%")
    print()
    print(f"{'RESINS':<14}{'UTS':>6} {'Z%':>5} {'MOD':>7} {'HDT':>6} {'ELONG':>7}")
    print("-" * 72)
    for k, v in RESIN_MATERIALS.items():
        print(f"{k:<14}{v['uts']:>5.0f} {v['z']*100:>5.0f} {v['mod']:>7.0f} "
              f"{v['hdt']:>5.0f} {v['elong']:>6.0f}%")
    print()
    print("UTS in MPa (printed part, in-plane).  Z% = layer adhesion as a "
          "fraction of UTS.\nMOD = tensile modulus, MPa.  HDT in C.")
    print()


def build_opts(args):
    mat = None
    if getattr(args, "material", None):
        found = lookup_material(args.material)
        if found is None:
            raise SystemExit(
                f"unknown material {args.material!r}. "
                f"Run `printstrength materials` for the list.")
        mat = dict(name=found[0], props=dict(found[1]), family=found[2], source="--material")
        if getattr(args, "uts", None):
            mat["props"]["uts"] = args.uts
        if getattr(args, "layer_adhesion", None):
            mat["props"]["z"] = args.layer_adhesion / 100.0
    # --load takes either a bare force or a whole load case.
    raw = getattr(args, "load", None)
    load_val, load_case = None, None
    if raw is not None:
        raw = str(raw).strip()
        try:
            load_val = float(raw)
        except ValueError:
            load_case = raw
    if getattr(args, "fix", None) and load_case is None:
        if load_val is not None:
            load_case = f"{load_val}N -Z at top"
            load_val = None
        else:
            raise SystemExit("--fix needs a --load to go with it, "
                             "e.g. --load '50N -Z at top'")
    return dict(
        material=mat,
        load=load_val,
        load_case=load_case,
        fix=getattr(args, "fix", None),
        moment=getattr(args, "moment", None),
        temp=getattr(args, "temp", None),
        quality=getattr(args, "quality", "typical"),
        arm=getattr(args, "arm", None),
        deflect=getattr(args, "deflect", None),
        knockdown=QUALITY_KNOCKDOWN[getattr(args, "quality", "typical")],
        solid_fraction=getattr(args, "solid_fraction", None),
        layer_height=getattr(args, "layer_height", None),
        nozzle=getattr(args, "nozzle", None),
        ext_width=getattr(args, "ext_width", None),
        walls=getattr(args, "walls", None),
        infill=(getattr(args, "infill", None) / 100.0
                if getattr(args, "infill", None) is not None else None),
        planes=getattr(args, "planes", 400),
        bin_size=getattr(args, "bin_size", 0.1),
        z_only=getattr(args, "z_only", False),
        green=getattr(args, "green", False),
        max_segments=getattr(args, "max_segments", 4_000_000),
    )


# ---------------------------------------------------------------------------
# 3MF
# ---------------------------------------------------------------------------
#
# Bambu Studio and OrcaSlicer write a sliced plate as `.gcode.3mf`: a ZIP that
# carries the G-code alongside the model, thumbnails and plate settings.  A
# plain `.3mf` is the model only -- the modern replacement for STL, with real
# units and a build transform.
#
# Either way the G-code inside is the better input, so it wins when present.

def is_zip(head):
    return head[:2] == b"PK" and head[2:4] in (b"\x03\x04", b"\x05\x06", b"\x07\x08")


def _tmf_gcode_entry(names):
    """The sliced plate, if this archive has one."""
    cands = [n for n in names if n.lower().endswith((".gcode", ".gco"))]
    if not cands:
        return None
    # plate_1 before plate_2; Metadata/ before anything else
    cands.sort(key=lambda n: (0 if "metadata/" in n.lower() else 1, n.lower()))
    return cands[0]


_V_RE = re.compile(r'<\s*vertex\b([^>]*)/?>', re.I)
_T_RE = re.compile(r'<\s*triangle\b([^>]*)/?>', re.I)
_ATTR_RE = re.compile(r'([a-zA-Z_][\w:.-]*)\s*=\s*"([^"]*)"')
_ITEM_RE = re.compile(r'<\s*item\b([^>]*)/?>', re.I)

_UNIT_SCALE = {"micron": 0.001, "millimeter": 1.0, "centimeter": 10.0,
               "inch": 25.4, "foot": 304.8, "meter": 1000.0}


def _attrs(text):
    return {k.lower(): v for k, v in _ATTR_RE.findall(text)}


def parse_3mf_model(xml):
    """Mesh out of a 3MF model part, honouring units and the build transform."""
    unit = "millimeter"
    m = re.search(r'<\s*model\b([^>]*)>', xml, re.I)
    if m:
        unit = _attrs(m.group(1)).get("unit", "millimeter").lower()
    scale = _UNIT_SCALE.get(unit, 1.0)

    verts = []
    for a in _V_RE.findall(xml):
        d = _attrs(a)
        try:
            verts.append((float(d.get("x", 0)) * scale,
                          float(d.get("y", 0)) * scale,
                          float(d.get("z", 0)) * scale))
        except ValueError:
            pass
    tris_idx = []
    for a in _T_RE.findall(xml):
        d = _attrs(a)
        try:
            tris_idx.append((int(d["v1"]), int(d["v2"]), int(d["v3"])))
        except (KeyError, ValueError):
            pass
    if len(verts) < 3 or not tris_idx:
        raise ValueError("3MF model part has no usable mesh")

    # The build item may place the object with a 3x4 row-major transform.
    xform = None
    im = _ITEM_RE.search(xml)
    if im:
        t = _attrs(im.group(1)).get("transform")
        if t:
            nums = [float(v) for v in t.split()]
            if len(nums) == 12:
                xform = nums
    if xform:
        a, b, c, d_, e, f, g, h, i, tx, ty, tz = xform
        verts = [(x * a + y * d_ + z * g + tx * scale,
                  x * b + y * e + z * h + ty * scale,
                  x * c + y * f + z * i + tz * scale) for x, y, z in verts]

    tris, normals = [], []
    n = len(verts)
    for i1, i2, i3 in tris_idx:
        if not (0 <= i1 < n and 0 <= i2 < n and 0 <= i3 < n):
            continue
        p, q, r = verts[i1], verts[i2], verts[i3]
        u = (q[0] - p[0], q[1] - p[1], q[2] - p[2])
        v = (r[0] - p[0], r[1] - p[1], r[2] - p[2])
        nv = _cross(u, v)
        L = math.sqrt(sum(t * t for t in nv)) or 1.0
        tris.append((p, q, r))
        normals.append((nv[0] / L, nv[1] / L, nv[2] / L))
    if not tris:
        raise ValueError("3MF model part has no valid triangles")
    return Mesh(tris, normals)


def open_3mf(path, max_segments=4_000_000):
    """Return a parsed model from a .3mf or .gcode.3mf archive."""
    import zipfile
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        gpath = _tmf_gcode_entry(names)
        if gpath:
            import tempfile
            data = z.read(gpath).decode("utf-8", errors="replace")
            with tempfile.NamedTemporaryFile("w", suffix=".gcode", delete=False) as fh:
                fh.write(data)
                tmp = fh.name
            try:
                model = parse_gcode(tmp, max_segments=max_segments)
            finally:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
            model.config.setdefault("_container", f"3MF · {gpath}")
            return model
        models = [n for n in names if n.lower().endswith(".model")]
        if not models:
            raise SystemExit(
                "this .3mf contains neither G-code nor a model part. If it came from "
                "Bambu Studio, use 'Export plate sliced file'.")
        models.sort(key=lambda n: (0 if "3dmodel" in n.lower() else 1, n.lower()))
        return parse_3mf_model(z.read(models[0]).decode("utf-8", errors="replace"))


STEP_SURFACES = ("B_SPLINE_SURFACE_WITH_KNOTS", "RATIONAL_B_SPLINE_SURFACE",
                 "B_SPLINE_SURFACE", "TOROIDAL_SURFACE", "CONICAL_SURFACE",
                 "SPHERICAL_SURFACE", "SURFACE_OF_REVOLUTION",
                 "SURFACE_OF_LINEAR_EXTRUSION", "OFFSET_SURFACE",
                 "CYLINDRICAL_SURFACE", "PLANE")


def _step_message(text):
    """Say precisely why a STEP file cannot be read, naming what is in it.

    STEP is a boundary representation: faces are trimmed analytic and NURBS
    surfaces, not triangles.  Turning those into cross-sections needs a full
    CAD kernel, and a half-done tessellation would give quietly wrong areas
    and therefore quietly wrong strength.
    """
    present = [t for t in STEP_SURFACES if t in text]
    faces = text.count("ADVANCED_FACE") + text.count("FACE_SURFACE")
    names = ", ".join(present) if present else "none recognised"
    return (f"this is a STEP file -- a boundary-representation format. It describes "
            f"{faces or 'its'} face{'' if faces == 1 else 's'} as trimmed surfaces "
            f"({names}), not as triangles, and turning those into cross-sections needs "
            f"a full CAD kernel.\nExport STL or 3MF from your CAD instead, or better, "
            f"slice it and analyse the G-code -- that carries the real walls and infill.")


def analyse_path(path, opts):
    if not os.path.exists(path):
        raise SystemExit(f"no such file: {path}")
    ext = os.path.splitext(path)[1].lower()
    with open(path, "rb") as _fh:
        _head = _fh.read(8)
    if ext == ".3mf" or is_zip(_head):
        model = open_3mf(path, opts.get("max_segments", 4_000_000))
        if isinstance(model, GcodeModel):
            return run_gcode(path, opts, model=model)
        return run_stl(path, opts, mesh=model)
    if ext in (".step", ".stp"):
        with open(path, "r", errors="replace") as fh:
            head = fh.read(400000)
        raise SystemExit(_step_message(head))
    if ext == ".stl":
        return run_stl(path, opts)
    if ext in (".gcode", ".gco", ".g", ".nc", ".bgcode"):
        if ext == ".bgcode":
            raise SystemExit(
                "binary G-code (.bgcode) is not supported. In OrcaSlicer turn off "
                "'Export as binary G-code', or convert with `bgcode decode`.")
        return run_gcode(path, opts)
    # Sniff it.
    with open(path, "rb") as fh:
        head = fh.read(4096)
    if b"ISO-10303-21" in head:
        with open(path, "r", errors="replace") as fh:
            raise SystemExit(_step_message(fh.read(400000)))
    if head[:5].lower() == b"solid" or len(head) >= 84:
        try:
            return run_stl(path, opts)
        except Exception:
            pass
    return run_gcode(path, opts)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)

    # OrcaSlicer post-processing: the slicer appends the G-code path and
    # expects the file to be edited in place.  Detect that shape early.
    if argv and argv[0] not in ("materials", "-h", "--help", "--version") \
            and "--orca" in argv:
        return post_process(argv)

    ap = argparse.ArgumentParser(
        prog="printstrength",
        description="Estimate the mechanical strength of FDM and resin 3D prints.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  printstrength bracket.gcode
  printstrength bracket.gcode --material PETG --load 250

  # a load case, SimulationXpress style: hold something, push somewhere
  printstrength bracket.gcode --fix bottom  --load "5kg -Z at top"
  printstrength bracket.gcode --fix "x<5"   --load "40N -Z at x>35"
  printstrength shelf.stl -m tough --fix left --load "2kg down at right"

  regions:  bottom top left right front back,  or  z<5  x>30  y<=2
  forces:   N kN kg kgf lb      directions: +X -X +Y -Y +Z -Z up down
  printstrength bracket.gcode --temp 60 --quality poor
  printstrength part.stl --material tough --load 80
  printstrength bracket.gcode --json > report.json
  printstrength materials

OrcaSlicer integration:
  Print Settings -> Others -> Post-processing Scripts:
      /usr/bin/python3 /path/to/printstrength.py --orca;
  A .strength.txt report is written next to the G-code on every slice.
""")
    ap.add_argument("target", nargs="?", help="a .gcode or .stl file, or 'materials'")
    ap.add_argument("--material", "-m", help="override the material (see 'materials')")
    ap.add_argument("--load", metavar="SPEC",
                    help="a plain force in newtons ('250'), or a full load case "
                         "('50N -Z at top', '5kg down at x>35')")
    ap.add_argument("--fix", metavar="REGION",
                    help="what is held: bottom, top, left, right, front, back, "
                         "or an inequality like z<5 (default: bottom)")
    ap.add_argument("--moment", type=float, metavar="NM", help="applied bending moment, N.m")
    ap.add_argument("--temp", type=float, metavar="C", help="service temperature, degC")
    ap.add_argument("--arm", type=float, metavar="MM",
                    help="lever arm for the bending load; default is derived "
                         "from the part (to the top, or to the far end)")
    ap.add_argument("--deflect", type=float, metavar="MM",
                    help="maximum acceptable deflection; reports the load that "
                         "causes it and whether stiffness governs before strength")
    ap.add_argument("--quality", choices=list(QUALITY_KNOCKDOWN), default="typical",
                    help="print quality knockdown (default: typical)")
    ap.add_argument("--uts", type=float, help="override ultimate tensile strength, MPa")
    ap.add_argument("--layer-adhesion", type=float, metavar="PCT",
                    help="override layer adhesion, %% of UTS")
    ap.add_argument("--solid-fraction", type=float, default=None,
                    help="mesh only: override the print model with a flat solid "
                         "fraction (0-1). By default walls and infill are modelled.")
    ap.add_argument("--layer-height", type=float, metavar="MM",
                    help="mesh only: layer height (default 0.2). G-code carries its own.")
    ap.add_argument("--nozzle", type=float, metavar="MM",
                    help="mesh only: nozzle diameter (default 0.4); extrusion width "
                         "is taken as 1.05x this unless --ext-width is given")
    ap.add_argument("--ext-width", type=float, metavar="MM",
                    help="mesh only: extrusion width, overriding the nozzle default")
    ap.add_argument("--walls", type=int, metavar="N",
                    help="mesh only: wall loops (default 3)")
    ap.add_argument("--infill", type=float, metavar="PCT",
                    help="mesh only: infill density in %% (default 20)")
    ap.add_argument("--planes", type=int, default=400, help="STL only: slice count")
    ap.add_argument("--bin-size", type=float, default=0.1,
                    help="G-code only: section scan resolution, mm")
    ap.add_argument("--z-only", action="store_true",
                    help="only analyse layer-adhesion (Z) failure; much faster")
    ap.add_argument("--green", action="store_true",
                    help="resin: analyse as uncured, straight off the plate")
    ap.add_argument("--max-segments", type=int, default=4_000_000,
                    help="memory guard for very large G-code files")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a report")
    ap.add_argument("--no-color", action="store_true")
    ap.add_argument("--orca", action="store_true",
                    help="OrcaSlicer post-processing mode")
    ap.add_argument("--version", action="version", version=f"printstrength {__version__}")

    args = ap.parse_args(argv)
    if args.target in (None, "materials"):
        if args.target == "materials":
            cmd_materials(args)
            return 0
        ap.print_help()
        return 1

    opts = build_opts(args)
    res, meta, _ = analyse_path(args.target, opts)

    if args.json:
        print(to_json(res, meta, opts))
    else:
        colour = (not args.no_color) and sys.stdout.isatty()
        print(render_report(res, meta, opts, colour=colour))

    sim = getattr(res, "sim", None)
    if sim and not sim["error"]:
        return 0 if sim["worst"]["sf"] >= 1.0 else 2
    if opts.get("load"):
        caps = [d.get("break_N") or d["tension_N"] for d in res.axes.values()]
        worst = min((c / opts["load"] for c in caps), default=float("inf"))
        return 0 if worst >= 1.0 else 2
    return 0


def post_process(argv):
    """Run as an OrcaSlicer post-processing script.

    Orca appends the output G-code path as the last argument and re-reads the
    file afterwards, so the file must survive intact.  We only prepend
    comments, and write the full report alongside it.
    """
    args = [a for a in argv if a != "--orca"]
    path = None
    for a in reversed(args):
        if not a.startswith("-") and os.path.exists(a):
            path = a
            break
    if path is None:
        sys.stderr.write("printstrength --orca: no G-code path given\n")
        return 1

    class Shim:
        pass
    sh = Shim()
    sh.material = None
    for i, a in enumerate(args):
        if a in ("-m", "--material") and i + 1 < len(args):
            sh.material = args[i + 1]
    sh.quality = "typical"
    for i, a in enumerate(args):
        if a == "--quality" and i + 1 < len(args):
            sh.quality = args[i + 1]
    sh.load = None
    for i, a in enumerate(args):
        if a == "--load" and i + 1 < len(args):
            sh.load = float(args[i + 1])
    opts = build_opts(sh)

    try:
        res, meta, _ = analyse_path(path, opts)
    except SystemExit as exc:
        sys.stderr.write(f"printstrength: {exc}\n")
        return 0            # never break the user's slice
    except Exception as exc:
        sys.stderr.write(f"printstrength: {type(exc).__name__}: {exc}\n")
        return 0

    report = render_report(res, meta, opts, colour=False)
    side = os.path.splitext(path)[0] + ".strength.txt"
    try:
        with open(side, "w") as fh:
            fh.write(report)
    except OSError:
        pass

    # Prepend a compact summary as G-code comments so it travels with the file
    # and shows up in Orca's own G-code viewer.
    lines = ["; ===== printstrength =====",
             f"; material: {res.material['name']}",
             f"; weakest axis: {res.weakest_axis.upper() if res.weakest_axis else '?'}"]
    for a, d in res.axes.items():
        if d.get("break_N"):
            lines.append(
                f"; {a.upper()}: breaks at {d['break_N']:.0f} N applied "
                f"{d['arm_mm']:.0f} mm out ({d['moment_Nm']:.2f} N.m), "
                f"EI {fmt_sig(d['EI'])} N.m^2, {d['tension_N']:.0f} N in tension")
        else:
            lines.append(f"; {a.upper()}: {d['tension_N']:.0f} N tensile "
                         f"({d['area']:.1f} mm^2 at {d['sigma']:.1f} MPa)")
    lines.append(f"; full report: {os.path.basename(side)}")
    lines.append("; =========================")
    try:
        with open(path, "r", errors="replace") as fh:
            body = fh.read()
        with open(path, "w") as fh:
            fh.write("\n".join(lines) + "\n" + body)
    except OSError as exc:
        sys.stderr.write(f"printstrength: could not annotate G-code: {exc}\n")
    sys.stderr.write(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
