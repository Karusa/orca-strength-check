/* printstrength engine — JavaScript port of printstrength.py.
 *
 * The Python file remains the reference implementation. This port exists
 * because the page runs in a sandbox that cannot execute Python, and it is
 * checked against the Python on every fixture by tests/compare_engines.py —
 * any drift in the physics shows up there as a numeric diff.
 *
 * Section properties are exact (closed-form, not sampled). The one deliberate
 * difference: vertical cuts are analysed at a fixed station count rather than
 * the Python's finer sweep, so X/Y positions land on the station grid.
 */
'use strict';

const PS = (() => {

// --- materials -------------------------------------------------------------
const FDM = {
  "PLA":     {uts:50, mod:3500, z:0.55, hdt:55,  rho:1.24, elong:4,
              notes:"Brittle. Stiff but creeps under sustained load; softens near 55 C."},
  "PLA+":    {uts:45, mod:3000, z:0.62, hdt:58,  rho:1.24, elong:8,
              notes:"Toughened PLA. Lower peak strength, much better layer adhesion and impact."},
  "PLA-CF":  {uts:48, mod:5500, z:0.42, hdt:58,  rho:1.30, elong:2,
              notes:"Carbon fill raises stiffness, lowers layer adhesion and impact. Very brittle in Z."},
  "PETG":    {uts:45, mod:2100, z:0.72, hdt:70,  rho:1.27, elong:12,
              notes:"Best layer adhesion of the common materials. Ductile, notch tolerant."},
  "PETG-CF": {uts:60, mod:5000, z:0.50, hdt:78,  rho:1.32, elong:4,
              notes:"Stiffer than PETG, but the carbon halves its layer adhesion advantage."},
  "ABS":     {uts:38, mod:2100, z:0.45, hdt:95,  rho:1.04, elong:15,
              notes:"Needs a chamber. Layer adhesion collapses without one; open-air ABS can hit z=0.25."},
  "ASA":     {uts:42, mod:2200, z:0.45, hdt:100, rho:1.07, elong:14,
              notes:"ABS with UV stability. Same chamber requirement."},
  "ABS-CF":  {uts:55, mod:5500, z:0.35, hdt:105, rho:1.11, elong:3,
              notes:"Dimensionally stable, weak in Z. Orient loads in-plane."},
  "PC":      {uts:62, mod:2300, z:0.42, hdt:115, rho:1.20, elong:8,
              notes:"Strong and heat resistant but hygroscopic; wet PC loses most of its strength."},
  "PC-CF":   {uts:85, mod:6000, z:0.35, hdt:130, rho:1.22, elong:3,
              notes:"High in-plane strength, poor Z. Dry it."},
  "PA6":     {uts:55, mod:1600, z:0.55, hdt:90,  rho:1.12, elong:25,
              notes:"Nylon. Very tough dry, noticeably weaker and more ductile once it absorbs water."},
  "PA12":    {uts:48, mod:1400, z:0.60, hdt:85,  rho:1.01, elong:30,
              notes:"Less hygroscopic than PA6, tough, good fatigue life."},
  "PA-CF":   {uts:95, mod:6500, z:0.35, hdt:120, rho:1.15, elong:4,
              notes:"Very strong in-plane. Z strength is the design limit."},
  "PA-GF":   {uts:80, mod:5000, z:0.40, hdt:115, rho:1.25, elong:5,
              notes:"Glass fill: tougher than carbon, slightly less stiff."},
  "PPA-CF":  {uts:130,mod:9000, z:0.35, hdt:190, rho:1.18, elong:3,
              notes:"High-performance nylon. Needs a hot chamber and a hardened hotend."},
  "PET-CF":  {uts:90, mod:6000, z:0.38, hdt:150, rho:1.35, elong:3,
              notes:"Stiff and heat resistant, brittle in Z."},
  "PP":      {uts:25, mod:1300, z:0.60, hdt:90,  rho:0.90, elong:40,
              notes:"Chemically inert, very ductile, low strength. Warps badly."},
  "TPU95A":  {uts:28, mod:60,   z:0.80, hdt:60,  rho:1.20, elong:400,
              notes:"Elastomer. Strength is rarely the limit — design by deflection, not stress."},
  "TPU85A":  {uts:18, mod:20,   z:0.85, hdt:55,  rho:1.20, elong:500,
              notes:"Soft elastomer. Deflection governs."},
  "HIPS":    {uts:25, mod:1900, z:0.50, hdt:88,  rho:1.05, elong:20,
              notes:"Mostly a support material. Weak."},
  "PEEK":    {uts:95, mod:4000, z:0.45, hdt:250, rho:1.30, elong:20,
              notes:"Requires 400 C hotend and a heated chamber. Crystallinity dominates final strength."},
  "PPS-CF":  {uts:90, mod:8000, z:0.40, hdt:200, rho:1.43, elong:3,
              notes:"Chemical and heat resistant engineering grade."},
};
const RESIN = {
  "standard":       {uts:45, mod:2200, z:0.95, hdt:60,  rho:1.15, elong:5,
                     notes:"Generic 405nm resin. Strong in tension, very brittle — fails without warning."},
  "abs-like":       {uts:52, mod:2400, z:0.95, hdt:70,  rho:1.15, elong:8,
                     notes:"Marketing name, not real ABS. Somewhat tougher than standard."},
  "tough":          {uts:40, mod:1600, z:0.95, hdt:55,  rho:1.13, elong:20,
                     notes:"Trades strength for impact resistance and elongation. Creeps under load."},
  "water-washable": {uts:32, mod:1700, z:0.90, hdt:50,  rho:1.10, elong:6,
                     notes:"Weakest common resin, and it keeps absorbing water. Avoid for load-bearing parts."},
  "rigid":          {uts:70, mod:3500, z:0.95, hdt:85,  rho:1.25, elong:3,
                     notes:"Ceramic or glass filled. Stiffest option, extremely brittle."},
  "high-temp":      {uts:55, mod:3800, z:0.95, hdt:200, rho:1.20, elong:2,
                     notes:"Needs a thermal post-cure to reach its rated HDT."},
  "flexible":       {uts:12, mod:100,  z:0.90, hdt:40,  rho:1.10, elong:120,
                     notes:"Elastomeric. Design by deflection."},
  "castable":       {uts:25, mod:1400, z:0.90, hdt:45,  rho:1.10, elong:4,
                     notes:"Formulated to burn out cleanly, not to carry load."},
  "dental":         {uts:60, mod:2600, z:0.95, hdt:75,  rho:1.18, elong:5,
                     notes:"Biocompatible, well characterised, requires a validated cure cycle."},
};
const ALIASES = {
  "pla+":"PLA+","tough pla":"PLA+","pla pro":"PLA+","pla-ht":"PLA+",
  "pla cf":"PLA-CF","placf":"PLA-CF","petg cf":"PETG-CF","pctg":"PETG",
  "abs cf":"ABS-CF","asa-cf":"ABS-CF","asa cf":"ABS-CF","pc cf":"PC-CF",
  "nylon":"PA6","pa":"PA6","pa6-cf":"PA-CF","pa6 cf":"PA-CF","pacf":"PA-CF",
  "paht-cf":"PA-CF","pahtcf":"PA-CF","pa12-cf":"PA-CF","pa6-gf":"PA-GF",
  "ppa cf":"PPA-CF","pet cf":"PET-CF","tpu":"TPU95A","tpu 95a":"TPU95A",
  "tpu-95a":"TPU95A","tpu 85a":"TPU85A","tpu-85a":"TPU85A","flex":"TPU95A",
  "pekk":"PEEK","pps":"PPS-CF","resin":"standard","standard resin":"standard",
  "abs like":"abs-like","abslike":"abs-like","tough resin":"tough",
  "durable":"tough","water washable":"water-washable","washable":"water-washable",
  "rigid resin":"rigid","engineering":"rigid","high temp":"high-temp",
};
const squash = s => s.toLowerCase().replace(/[^a-z0-9+]/g, "");

function lookupMaterial(name) {
  if (!name) return null;
  const raw = String(name).trim();
  if (FDM[raw.toUpperCase()]) return {name: raw.toUpperCase(), props: FDM[raw.toUpperCase()], family: "fdm"};
  const low = raw.toLowerCase();
  if (RESIN[low]) return {name: low, props: RESIN[low], family: "resin"};
  const norm = low.replace(/[\s_]+/g, " ").trim();
  if (ALIASES[norm]) return lookupMaterial(ALIASES[norm]);
  const sq = squash(low);
  for (const k of Object.keys(ALIASES)) if (squash(k) === sq) return lookupMaterial(ALIASES[k]);
  for (const k of Object.keys(FDM)) if (squash(k) === sq) return {name:k, props:FDM[k], family:"fdm"};
  for (const k of Object.keys(RESIN)) if (squash(k) === sq) return {name:k, props:RESIN[k], family:"resin"};
  let best = null;
  for (const k of [...Object.keys(FDM), ...Object.keys(RESIN)]) {
    const kl = k.toLowerCase();
    if (low.includes(kl) && (best === null || kl.length > best.length)) best = kl;
  }
  if (best) {
    for (const k of Object.keys(FDM)) if (k.toLowerCase() === best) return {name:k, props:FDM[k], family:"fdm"};
    for (const k of Object.keys(RESIN)) if (k.toLowerCase() === best) return {name:k, props:RESIN[k], family:"resin"};
  }
  return null;
}

// --- feature classification -------------------------------------------------
const F = {UNKNOWN:0, OUTER:1, INNER:2, SOLID:3, TOP:4, BOTTOM:5, SPARSE:6,
           BRIDGE:7, GAP:8, OVERHANG:9, SUPPORT:10, SKIRT:11, IRON:12, OTHER:13};
const FEATURE_NAMES = {0:"unclassified",1:"outer wall",2:"inner wall",3:"solid infill",
  4:"top surface",5:"bottom surface",6:"sparse infill",7:"bridge",8:"gap fill",
  9:"overhang wall",10:"support",11:"skirt/brim",12:"ironing",13:"other"};
const Z_EFF = {1:1.00,2:1.00,3:0.95,4:0.90,5:0.90,6:0.55,7:0.50,8:0.70,9:0.75,
               10:0,11:0,12:0,0:0.85,13:0.50};
const XY_EFF = {1:1.00,2:1.00,3:1.00,4:0.95,5:0.95,6:0.85,7:0.80,8:0.80,9:0.85,
                10:0,11:0,12:0,0:0.95,13:0.80};
const NON_PART = new Set([F.SUPPORT, F.SKIRT]);

const FEATURE_PATTERNS = [
  [F.OVERHANG, ["overhang"]],
  [F.OUTER,    ["outer wall","external perimeter","outer perimeter","wall-outer"]],
  [F.INNER,    ["inner wall","perimeter","wall-inner","internal perimeter"]],
  [F.BRIDGE,   ["bridge"]],
  [F.TOP,      ["top surface","top solid infill","topsurface"]],
  [F.BOTTOM,   ["bottom surface","bottom solid infill","first layer","raft"]],
  [F.IRON,     ["ironing"]],
  [F.SOLID,    ["internal solid infill","solid infill","solidinfill","skin"]],
  [F.GAP,      ["gap fill","gap infill","thin wall"]],
  [F.SPARSE,   ["sparse infill","internal infill","fill","infill"]],
  [F.SUPPORT,  ["support","prime tower","wipe tower"]],
  [F.SKIRT,    ["skirt","brim"]],
  [F.OTHER,    ["custom","unknown"]],
];
function classifyFeature(text) {
  const t = String(text).trim().toLowerCase();
  for (const [code, needles] of FEATURE_PATTERNS)
    for (const n of needles) if (t.includes(n)) return code;
  return F.UNKNOWN;
}

// --- G-code parsing ---------------------------------------------------------
const WORD_RE = /([A-Za-z])\s*(-?\d*\.?\d+(?:[eE]-?\d+)?)/g;

function cleanConfigValue(v) {
  v = v.trim();
  if (v.length > 1 && v[0] === '"' && v[v.length-1] === '"') v = v.slice(1,-1);
  v = v.replace(/^\[|\]$/g, "");
  if (v.includes(";")) v = v.split(";")[0];
  if (v.includes(",") && !/^-?[\d.]+,\s*-?[\d.]+$/.test(v)) v = v.split(",")[0];
  return v.trim().replace(/^"|"$/g, "");
}

function parseGcode(text) {
  const m = {
    x1:[], y1:[], x2:[], y2:[], w:[], h:[], feat:[], layerIdx:[],
    layers:[], config:{}, slicer:"unknown", totalVolume:0, skippedVolume:0,
    warnings:[], kind:"gcode",
  };
  let x=0,y=0,z=0,e=0;
  let absXYZ=true, absE=true, volumetric=false, filD=1.75;
  let curFeat=F.UNKNOWN, layerIdx=-1, layerZ=null, prevLayerZ=0, defaultH=null;
  let seenMarker=false, pending=false;
  let layVol={}, laySegStart=0;
  const filArea = () => Math.PI * (filD/2) ** 2;

  function closeLayer() {
    let h = layerZ !== null ? layerZ - prevLayerZ : 0;
    if (h <= 0.001 || h > 2.0) h = defaultH || 0.2;
    m.layers.push({index:layerIdx, z: layerZ === null ? 0 : layerZ, h,
                   vol: Object.assign({}, layVol), segStart: laySegStart, segEnd: m.x1.length});
    for (let i = laySegStart; i < m.x1.length; i++) m.h[i] = h;
  }
  function startLayer(nz) {
    if (layerIdx >= 0) closeLayer();
    prevLayerZ = layerZ === null ? 0 : layerZ;
    layerZ = nz; layerIdx += 1; layVol = {}; laySegStart = m.x1.length;
  }
  function addSeg(ax, ay, bx, by, w, feat) {
    m.x1.push(ax); m.y1.push(ay); m.x2.push(bx); m.y2.push(by);
    m.w.push(w); m.h.push(0.2); m.feat.push(feat); m.layerIdx.push(layerIdx);
  }

  const lines = text.split("\n");
  for (let li = 0; li < lines.length; li++) {
    let line = lines[li];
    if (!line) continue;
    if (line[0] === ";") {
      const s = line.slice(1).trim(), sl = s.toLowerCase();
      if (sl.startsWith("type:") || sl.startsWith("feature:")) {
        curFeat = classifyFeature(s.split(":").slice(1).join(":")); continue;
      }
      if (sl.startsWith("layer_change") || sl.startsWith("layer:") ||
          sl.startsWith("layer ") || sl.startsWith("z:")) {
        seenMarker = true;
        const mm = /z\s*[:=]\s*(-?[\d.]+)/.exec(sl);
        if (mm) { startLayer(parseFloat(mm[1])); pending = false; }
        else if (layerIdx < 0 || m.x1.length > laySegStart) pending = true;
        continue;
      }
      const cm = /^;\s*([a-zA-Z0-9_.]+)\s*=\s*(.*?)\s*$/.exec(line);
      if (cm) { if (!(cm[1].toLowerCase() in m.config)) m.config[cm[1].toLowerCase()] = cleanConfigValue(cm[2]); continue; }
      if (sl.includes("generated by") || sl.includes("orcaslicer") || sl.includes("prusaslicer")
          || sl.includes("bambustudio") || sl.includes("cura") || sl.includes("superslicer"))
        if (!("_header" in m.config)) m.config._header = s;
      continue;
    }
    const semi = line.indexOf(";");
    if (semi >= 0) { line = line.slice(0, semi); if (!line.trim()) continue; }
    const cmd = line.slice(0,3).toUpperCase();

    if (cmd.startsWith("G0") || cmd.startsWith("G1")) {
      let nx=null, ny=null, nz=null, ne=null, mt;
      WORD_RE.lastIndex = 0;
      while ((mt = WORD_RE.exec(line)) !== null) {
        const u = mt[1].toUpperCase(), v = parseFloat(mt[2]);
        if (u==="X") nx=v; else if (u==="Y") ny=v; else if (u==="Z") nz=v; else if (u==="E") ne=v;
      }
      let tx = nx===null ? x : nx, ty = ny===null ? y : ny;
      if (!absXYZ) { tx = x + (nx||0); ty = y + (ny||0); }
      let tz = z;
      if (nz !== null) tz = absXYZ ? nz : z + nz;
      let de = 0;
      if (ne !== null) { de = absE ? ne - e : ne; e = absE ? ne : e + ne; }
      if (nz !== null && Math.abs(tz - z) > 1e-6) {
        if (pending) { startLayer(tz); pending = false; }
        else if (!seenMarker && (layerIdx < 0 || Math.abs(tz - (layerZ||0)) > 1e-6)) startLayer(tz);
      }
      if (de > 1e-9) {
        if (pending) { startLayer(tz > 0 ? tz : (layerZ || 0.2)); pending = false; }
        if (layerIdx < 0) startLayer(tz > 0 ? tz : 0.2);
        const len = Math.hypot(tx - x, ty - y);
        const vol = volumetric ? de : de * filArea();
        if (len > 1e-6 && vol > 0) {
          const width = vol / (len * 0.2);
          addSeg(x, y, tx, ty, width, curFeat);
          layVol[curFeat] = (layVol[curFeat] || 0) + vol;
          if (NON_PART.has(curFeat)) m.skippedVolume += vol; else m.totalVolume += vol;
        }
      }
      x = tx; y = ty; z = tz;
    } else if (cmd.startsWith("G2") || cmd.startsWith("G3")) {
      const ccw = cmd.startsWith("G3");
      let i0=0, j0=0, nx=null, ny=null, ne=null, rad=null, mt;
      WORD_RE.lastIndex = 0;
      while ((mt = WORD_RE.exec(line)) !== null) {
        const u = mt[1].toUpperCase(), v = parseFloat(mt[2]);
        if (u==="X") nx=v; else if (u==="Y") ny=v; else if (u==="I") i0=v;
        else if (u==="J") j0=v; else if (u==="R") rad=v; else if (u==="E") ne=v;
      }
      const tx = nx===null ? x : nx, ty = ny===null ? y : ny;
      let de = 0;
      if (ne !== null) { de = absE ? ne - e : ne; e = absE ? ne : e + ne; }
      const pts = arcPoints(x, y, tx, ty, i0, j0, rad, ccw);
      if (de > 1e-9 && pts.length > 1) {
        if (pending) { startLayer(z > 0 ? z : (layerZ || 0.2)); pending = false; }
        if (layerIdx < 0) startLayer(z > 0 ? z : 0.2);
        let total = 0;
        for (let k = 0; k < pts.length-1; k++) total += Math.hypot(pts[k+1][0]-pts[k][0], pts[k+1][1]-pts[k][1]);
        const vol = volumetric ? de : de * filArea();
        if (total > 1e-6) {
          for (let k = 0; k < pts.length-1; k++) {
            const [ax,ay] = pts[k], [bx,by] = pts[k+1];
            const sl2 = Math.hypot(bx-ax, by-ay);
            if (sl2 < 1e-6) continue;
            const sv = vol * sl2 / total;
            addSeg(ax, ay, bx, by, sv/(sl2*0.2), curFeat);
          }
          layVol[curFeat] = (layVol[curFeat] || 0) + vol;
          if (NON_PART.has(curFeat)) m.skippedVolume += vol; else m.totalVolume += vol;
        }
      }
      x = tx; y = ty;
    } else if (cmd.startsWith("G92")) {
      let mt; WORD_RE.lastIndex = 0;
      while ((mt = WORD_RE.exec(line)) !== null) {
        const u = mt[1].toUpperCase(), v = parseFloat(mt[2]);
        if (u==="E") e=v; else if (u==="X") x=v; else if (u==="Y") y=v; else if (u==="Z") z=v;
      }
    } else if (cmd.startsWith("G90")) absXYZ = true;
    else if (cmd.startsWith("G91")) absXYZ = false;
    else if (cmd.startsWith("M82")) absE = true;
    else if (cmd.startsWith("M83")) absE = false;
    else if (line.slice(0,4).toUpperCase().startsWith("M200")) {
      const mm = /[Dd]\s*(-?[\d.]+)/.exec(line);
      if (mm) { const dv = parseFloat(mm[1]); volumetric = (dv === 0); if (dv > 0) filD = dv; }
    }
  }
  if (layerIdx >= 0) closeLayer();

  if (m.layers.length === 1 && m.x1.length > 5000)
    m.warnings.push("Only one layer was detected in a file with thousands of extrusions. " +
      "Layer markers and Z moves are both missing, so Z-axis results are meaningless.");

  const hdr = (m.config._header || "").toLowerCase();
  const names = [["orca","OrcaSlicer"],["bambu","BambuStudio"],["prusa","PrusaSlicer"],
                 ["super","SuperSlicer"],["cura","Cura"]];
  m.slicer = "unknown";
  for (const [k,v] of names) if (hdr.includes(k)) { m.slicer = v; break; }
  if (m.slicer === "unknown") {
    if ("wall_loops" in m.config || "sparse_infill_density" in m.config) m.slicer = "OrcaSlicer/BambuStudio";
    else if ("perimeters" in m.config) m.slicer = "PrusaSlicer/SuperSlicer";
  }
  // second pass: real widths now that layer heights are known
  for (const lay of m.layers) {
    if (Math.abs(lay.h - 0.2) < 1e-9) continue;
    const k = 0.2 / lay.h;
    for (let i = lay.segStart; i < lay.segEnd; i++) m.w[i] *= k;
  }
  m._zcache = new Map();
  return m;
}

function arcPoints(x0, y0, x1, y1, i, j, r, ccw, maxStep) {
  maxStep = maxStep || 0.4;
  let cx, cy;
  if (r !== null && r !== undefined && i === 0 && j === 0) {
    const d = Math.hypot(x1-x0, y1-y0);
    if (d < 1e-9 || d > 2*Math.abs(r)) return [[x0,y0],[x1,y1]];
    const mx = (x0+x1)/2, my = (y0+y1)/2;
    const h = Math.sqrt(Math.max(r*r - (d/2)**2, 0));
    const ux = (x1-x0)/d, uy = (y1-y0)/d;
    const sign = ((r > 0) === ccw) ? 1 : -1;
    cx = mx - sign*h*uy; cy = my + sign*h*ux;
  } else { cx = x0 + i; cy = y0 + j; }
  const r0 = Math.hypot(x0-cx, y0-cy);
  if (r0 < 1e-9) return [[x0,y0],[x1,y1]];
  const a0 = Math.atan2(y0-cy, x0-cx);
  const a1 = Math.atan2(y1-cy, x1-cx);
  let da = a1 - a0;
  if (ccw) { while (da <= 0) da += 2*Math.PI; } else { while (da >= 0) da -= 2*Math.PI; }
  const arcLen = Math.abs(da) * r0;
  const n = Math.max(2, Math.min(200, Math.floor(arcLen/maxStep) + 1));
  const out = [];
  for (let k = 0; k <= n; k++) out.push([cx + r0*Math.cos(a0 + da*k/n), cy + r0*Math.sin(a0 + da*k/n)]);
  return out;
}

function bbox2(m) {
  let loX=Infinity, loY=Infinity, hiX=-Infinity, hiY=-Infinity;
  for (let i = 0; i < m.x1.length; i++) {
    if (NON_PART.has(m.feat[i])) continue;
    const hw = m.w[i]*0.5;
    loX = Math.min(loX, m.x1[i]-hw, m.x2[i]-hw); hiX = Math.max(hiX, m.x1[i]+hw, m.x2[i]+hw);
    loY = Math.min(loY, m.y1[i]-hw, m.y2[i]-hw); hiY = Math.max(hiY, m.y1[i]+hw, m.y2[i]+hw);
  }
  if (loX === Infinity) return [0,0,0,0];
  return [loX, loY, hiX, hiY];
}

function segmentZ(m, i) {
  const li = m.layerIdx[i];
  if (m._zcache.has(li)) return m._zcache.get(li);
  let z = 0;
  for (const lay of m.layers) if (lay.index === li) { z = lay.z - lay.h*0.5; break; }
  m._zcache.set(li, z);
  return z;
}

// --- sections ---------------------------------------------------------------
const LOAD_STATIONS = 120;
const SHEAR_PEAK = 1.5;
const TORSION_SOLID_THRESHOLD = 0.85;
const POISSON = 0.35;

function mkSection(axis, pos) {
  return {axis, pos, area:0, force:0, byFeature:{}, I1:0, I2:0, c1:0, c2:0,
          sigma:0, layerIndex:null, arm:0, armNote:"", Fbreak:null, deflAtBreak:null,
          EI:0, bendAxis:"", Mbreak:0, centroid:null,
          enclosed:0, perimeter:0, tWall:0, openSum:0, torsQ:0, torsJ:0, torsModel:"none",
          kt:1, kf:1, ktKind:"", ktSize:0};
}
const S1 = s => s.c1 > 1e-9 ? s.I1/s.c1 : 0;
const S2 = s => s.c2 > 1e-9 ? s.I2/s.c2 : 0;

function torsionProperties(area, enclosed, perimeter, tWall, Ipolar, openSum) {
  const solidity = enclosed > 1e-9 ? area/enclosed : 0;
  if (enclosed > 1e-9 && solidity >= TORSION_SOLID_THRESHOLD)
    return [0.20*Math.pow(area,1.5), Ipolar > 1e-9 ? Math.pow(area,4)/(40*Ipolar) : 0, "solid"];
  if (enclosed > 1e-9 && tWall > 1e-9 && perimeter > 1e-9)
    return [2*enclosed*tWall, 4*enclosed*enclosed*tWall/perimeter, "closed shell"];
  if (openSum > 1e-12 && tWall > 1e-9) { const J = openSum/3; return [J/tWall, J, "open section"]; }
  return [0, 0, "none"];
}
const shearModulus = E => E / (2*(1+POISSON));

function applyBending(sec, modulus, arm, armNote) {
  sec.arm = arm; sec.armNote = armNote || "";
  const s1 = S1(sec), s2 = S2(sec);
  if (s1 <= 0 && s2 <= 0) return;
  let S, I;
  if (s2 <= 0 || (s1 > 0 && s1 <= s2)) { S = s1; I = sec.I1; sec.bendAxis = "1"; }
  else { S = s2; I = sec.I2; sec.bendAxis = "2"; }
  sec.EI = modulus * I / 1e6;
  const M = S * sec.sigma;
  if (arm > 1e-6) {
    sec.Fbreak = M / arm;
    if (I > 1e-9 && modulus > 0) sec.deflAtBreak = sec.Fbreak * Math.pow(arm,3) / (3*modulus*I);
  }
  sec.Mbreak = M / 1000;
}
const loadForDeflection = (sec, modulus, limit) => {
  const I = sec.bendAxis === "1" ? sec.I1 : sec.I2;
  if (I <= 1e-9 || sec.arm <= 1e-6 || modulus <= 0) return null;
  return 3*modulus*I*limit / Math.pow(sec.arm,3);
};

function analyseLayers(m, uts, zr, modulus, armOverride, feats, q) {
  const sigmaZ = uts * zr;
  let zTop = 0;
  for (const l of m.layers) zTop = Math.max(zTop, l.z);
  const out = [];
  for (const lay of m.layers) {
    if (lay.index === 0) continue;
    const sec = mkSection("z", lay.z);
    sec.layerIndex = lay.index;
    const h = lay.h;
    let effArea = 0;
    for (const k of Object.keys(lay.vol)) {
      const f = +k;
      if (NON_PART.has(f)) continue;
      const a = h > 0 ? lay.vol[k]/h : 0;
      sec.area += a;
      sec.byFeature[f] = (sec.byFeature[f] || 0) + a;
      effArea += a * (Z_EFF[f] !== undefined ? Z_EFF[f] : 0.8);
    }
    if (sec.area <= 1e-9) continue;
    sec.force = effArea * sigmaZ;
    sec.sigma = sec.force / sec.area;
    if (feats && feats.length) {
      applyConcentration(sec, feats, q, lay.h);
      sec.force = sec.sigma * sec.area;
    }
    layerMoments(m, lay, sec);
    const arm = armOverride !== null && armOverride !== undefined ? armOverride : (zTop - sec.pos);
    applyBending(sec, modulus, arm, armOverride != null ? "" : "to the top of the part");
    out.push(sec);
  }
  return out;
}

function layerMoments(m, lay, sec) {
  let sa=0, sx=0, sy=0;
  const items = [];
  let shoelace=0, wallPerim=0, wallArea=0, openSum=0;
  for (let i = lay.segStart; i < lay.segEnd; i++) {
    const f = m.feat[i];
    if (NON_PART.has(f)) continue;
    const x1=m.x1[i], y1=m.y1[i], x2=m.x2[i], y2=m.y2[i], w=m.w[i];
    const dx=x2-x1, dy=y2-y1, L=Math.hypot(dx,dy);
    if (f === F.OUTER || f === F.INNER || f === F.OVERHANG) {
      wallArea += L*w; openSum += L*w*w*w;
      if (f === F.OUTER || f === F.OVERHANG) { shoelace += x1*y2 - x2*y1; wallPerim += L; }
    }
    if (L < 1e-9 || w <= 0) continue;
    const eff = Z_EFF[f] !== undefined ? Z_EFF[f] : 0.8;
    const a = L*w*eff;
    if (a <= 0) continue;
    const cx=(x1+x2)*0.5, cy=(y1+y2)*0.5;
    items.push([cx, cy, dx/L, dy/L, L, w, a]);
    sa += a; sx += a*cx; sy += a*cy;
  }
  if (sa <= 1e-9) return;
  const gx = sx/sa, gy = sy/sa;
  let Ixx=0, Iyy=0, maxy=0, maxx=0;
  for (const [cx,cy,ux,uy,L,w,a] of items) {
    const eff = a/(L*w);
    const Iuu = L*w*w*w/12*eff, Ivv = L*L*L*w/12*eff;
    const c2=ux*ux, s2=uy*uy;
    Ixx += Iuu*c2 + Ivv*s2 + a*(cy-gy)**2;
    Iyy += Iuu*s2 + Ivv*c2 + a*(cx-gx)**2;
    maxy = Math.max(maxy, Math.abs(cy-gy) + Math.abs(uy)*L*0.5 + Math.abs(ux)*w*0.5);
    maxx = Math.max(maxx, Math.abs(cx-gx) + Math.abs(ux)*L*0.5 + Math.abs(uy)*w*0.5);
  }
  sec.I1 = Ixx; sec.c1 = maxy; sec.I2 = Iyy; sec.c2 = maxx;
  sec.centroid = [gx, gy];
  sec.enclosed = Math.abs(shoelace)*0.5;
  sec.perimeter = wallPerim;
  sec.tWall = wallPerim > 1e-9 ? wallArea/wallPerim : 0;
  sec.openSum = openSum;
  const t = torsionProperties(sec.area, sec.enclosed, sec.perimeter, sec.tWall, sec.I1+sec.I2, sec.openSum);
  sec.torsQ = t[0]; sec.torsJ = t[1]; sec.torsModel = t[2];
}

function verticalStations(m, axis, positions, uts, zr, feats, q) {
  if (!positions.length) return [];
  positions = positions.slice().sort((a,b)=>a-b);
  const n = positions.length, p0 = positions[0];
  const step = n > 1 ? (positions[n-1]-p0)/(n-1) : 1;
  if (step <= 0) return [];
  const sigmaBond = uts * Math.min(1, zr + 0.15);
  const A=new Float64Array(n), Aq=new Float64Array(n), Az=new Float64Array(n);
  const Aq2=new Float64Array(n), Az2=new Float64Array(n), Asig=new Float64Array(n);
  const selfz=new Float64Array(n), selfq=new Float64Array(n);
  const wallA=new Float64Array(n), openS=new Float64Array(n);
  const qmax=new Float64Array(n).fill(-1e30), qmin=new Float64Array(n).fill(1e30);
  const zmax=new Float64Array(n).fill(-1e30), zmin=new Float64Array(n).fill(1e30);
  const span = new Map();

  for (let i = 0; i < m.x1.length; i++) {
    const f = m.feat[i];
    if (NON_PART.has(f)) continue;
    const w = m.w[i], h = m.h[i];
    if (w <= 0 || h <= 0) continue;
    const x1=m.x1[i], y1=m.y1[i], x2=m.x2[i], y2=m.y2[i];
    const dx=x2-x1, dy=y2-y1, L=Math.hypot(dx,dy);
    if (L < 1e-9) continue;
    const ux=dx/L, uy=dy/L;
    let un, ut, a0, b0, dn, n1, pp1, pp2;
    if (axis === "x") { un=Math.abs(ux); ut=Math.abs(uy); a0=Math.min(x1,x2); b0=Math.max(x1,x2); dn=dx; n1=x1; pp1=y1; pp2=y2; }
    else              { un=Math.abs(uy); ut=Math.abs(ux); a0=Math.min(y1,y2); b0=Math.max(y1,y2); dn=dy; n1=y1; pp1=x1; pp2=x2; }
    let chord = un > 1e-6 ? w/un : Infinity;
    chord = Math.min(chord, L*ut + w*un);
    if (!(chord > 0)) continue;
    const area = chord*h;
    const sig = (uts*un*un + sigmaBond*ut*ut) * (XY_EFF[f] !== undefined ? XY_EFF[f] : 0.9);
    const zc = segmentZ(m, i);
    const half = w*ut*0.5;
    let s = Math.ceil((a0 - half - p0)/step), t2 = Math.floor((b0 + half - p0)/step);
    if (t2 < 0 || s > n-1) continue;
    s = Math.max(0, s); t2 = Math.min(n-1, t2);
    for (let k = s; k <= t2; k++) {
      const pos = positions[k];
      let q;
      if (Math.abs(dn) > 1e-9) {
        let tt = (pos - n1)/dn; tt = tt < 0 ? 0 : (tt > 1 ? 1 : tt);
        q = pp1 + (pp2-pp1)*tt;
      } else q = (pp1+pp2)*0.5;
      A[k]+=area; Aq[k]+=area*q; Az[k]+=area*zc;
      Aq2[k]+=area*q*q; Az2[k]+=area*zc*zc; Asig[k]+=area*sig;
      selfz[k]+=chord*h*h*h/12; selfq[k]+=h*chord*chord*chord/12;
      if (q+chord*0.5 > qmax[k]) qmax[k]=q+chord*0.5;
      if (q-chord*0.5 < qmin[k]) qmin[k]=q-chord*0.5;
      if (zc+h*0.5 > zmax[k]) zmax[k]=zc+h*0.5;
      if (zc-h*0.5 < zmin[k]) zmin[k]=zc-h*0.5;
      if (f===F.OUTER || f===F.INNER || f===F.OVERHANG) {
        wallA[k]+=area; openS[k]+=chord*h*h*h;
        if (f===F.OUTER || f===F.OVERHANG) {
          const key = k*1000003 + m.layerIdx[i];
          const cur = span.get(key);
          const loQ=q-chord*0.5, hiQ=q+chord*0.5;
          if (!cur) span.set(key, [loQ, hiQ, h]);
          else { if (loQ<cur[0]) cur[0]=loQ; if (hiQ>cur[1]) cur[1]=hiQ; }
        }
      }
    }
  }
  const encl = new Float64Array(n);
  for (const [key, v] of span) { const k = Math.floor(key/1000003); if (v[1]>v[0]) encl[k] += (v[1]-v[0])*v[2]; }

  const out = [];
  for (let k = 0; k < n; k++) {
    if (A[k] <= 1e-9) continue;
    const gq = Aq[k]/A[k], gz = Az[k]/A[k];
    const sec = mkSection(axis, positions[k]);
    sec.area = A[k];
    sec.sigma = Asig[k]/A[k];
    sec.force = Asig[k];
    sec.I1 = Math.max(Az2[k] - A[k]*gz*gz + selfz[k], 0);
    sec.I2 = Math.max(Aq2[k] - A[k]*gq*gq + selfq[k], 0);
    sec.c1 = Math.max(zmax[k]-gz, gz-zmin[k], 1e-9);
    sec.c2 = Math.max(qmax[k]-gq, gq-qmin[k], 1e-9);
    sec.centroid = [gq, gz];
    if (feats && feats.length) {
      applyConcentration(sec, feats, q);
      sec.force = sec.sigma * sec.area;
    }
    sec.enclosed = encl[k];
    const height = Math.max(zmax[k]-zmin[k], 1e-9);
    const meanW = encl[k] > 0 ? encl[k]/height : 0;
    sec.perimeter = encl[k] > 0 ? 2*(meanW + height) : 0;
    sec.tWall = sec.perimeter > 1e-9 ? wallA[k]/sec.perimeter : 0;
    sec.openSum = openS[k];
    const t = torsionProperties(sec.area, sec.enclosed, sec.perimeter, sec.tWall, sec.I1+sec.I2, sec.openSum);
    sec.torsQ=t[0]; sec.torsJ=t[1]; sec.torsModel=t[2];
    out.push(sec);
  }
  return out;
}

const BEND_CANDIDATES = 8;

// The fine sweep behind the per-axis headline numbers. A bead's chord is
// constant as the plane crosses it, so each contributes a flat band and the
// whole scan is a difference-array accumulation: O(segments), not
// O(segments x planes). Finer and more forgiving than the station grid,
// which can fall in the hairline gap between two touching beads.
function scanVertical(m, axis, uts, zr, binSize, trim, feats, q) {
  binSize = binSize || 0.1; trim = trim === undefined ? 1.0 : trim;
  const n = m.x1.length;
  if (!n) return [];
  const [loX, loY, hiX, hiY] = bbox2(m);
  const lo = axis === "x" ? loX : loY, hi = axis === "x" ? hiX : hiY;
  const span = hi - lo;
  if (span <= 1e-6) return [];
  const nbins = Math.max(4, Math.min(6000, Math.floor(span/binSize) + 1));
  const step = span/nbins;
  const sigmaBond = uts * Math.min(1, zr + 0.15);
  const dArea = new Float64Array(nbins + 2), dForce = new Float64Array(nbins + 2);
  const dFeat = {};

  for (let i = 0; i < n; i++) {
    const f = m.feat[i];
    if (NON_PART.has(f)) continue;
    const w = m.w[i], h = m.h[i];
    if (w <= 0 || h <= 0) continue;
    const x1=m.x1[i], y1=m.y1[i], x2=m.x2[i], y2=m.y2[i];
    const dx=x2-x1, dy=y2-y1, L=Math.hypot(dx,dy);
    if (L < 1e-9) continue;
    const ux=dx/L, uy=dy/L;
    let un, ut, a0, b0;
    if (axis === "x") { un=Math.abs(ux); ut=Math.abs(uy); a0=Math.min(x1,x2); b0=Math.max(x1,x2); }
    else              { un=Math.abs(uy); ut=Math.abs(ux); a0=Math.min(y1,y2); b0=Math.max(y1,y2); }
    let chord = un > 1e-6 ? w/un : Infinity;
    chord = Math.min(chord, L*ut + w*un);
    if (!(chord > 0)) continue;
    const area = chord*h;
    const sig = (uts*un*un + sigmaBond*ut*ut) * (XY_EFF[f] !== undefined ? XY_EFF[f] : 0.9);
    const half = w*ut*0.5;
    let s = Math.floor((a0 - half - lo)/step), t = Math.floor((b0 + half - lo)/step);
    s = Math.max(0, Math.min(nbins-1, s)); t = Math.max(0, Math.min(nbins-1, t));
    dArea[s] += area; dArea[t+1] -= area;
    dForce[s] += area*sig; dForce[t+1] -= area*sig;
    if (!dFeat[f]) dFeat[f] = new Float64Array(nbins + 2);
    dFeat[f][s] += area; dFeat[f][t+1] -= area;
  }

  const out = [];
  let runA = 0, runF = 0;
  const runs = {};
  for (const f of Object.keys(dFeat)) runs[f] = 0;
  for (let b = 0; b < nbins; b++) {
    runA += dArea[b]; runF += dForce[b];
    for (const f of Object.keys(dFeat)) runs[f] += dFeat[f][b];
    const pos = lo + (b + 0.5)*step;
    if (pos < lo + trim || pos > hi - trim) continue;
    if (runA <= 1e-6) continue;
    const sec = mkSection(axis, pos);
    sec.area = runA; sec.force = runF; sec.sigma = runA > 0 ? runF/runA : 0;
    sec.byFeature = {};
    for (const f of Object.keys(dFeat)) if (runs[f] > 1e-9) sec.byFeature[f] = runs[f];
    if (feats && feats.length) {
      applyConcentration(sec, feats, q);
      sec.force = sec.sigma * sec.area;
    }
    out.push(sec);
  }
  return out;
}

// --- fixtures and loads -----------------------------------------------------
const AXIS_INDEX = {x:0, y:1, z:2};
const NAMED_REGIONS = {bottom:["z","<"], top:["z",">"], left:["x","<"],
                       right:["x",">"], front:["y","<"], back:["y",">"]};
const FORCE_UNITS = {n:1, kn:1000, kgf:9.80665, kg:9.80665, lb:4.44822, lbf:4.44822};
const DIRS = {"+x":[1,0,0], x:[1,0,0], "-x":[-1,0,0], "+y":[0,1,0], y:[0,1,0],
              "-y":[0,-1,0], "+z":[0,0,1], z:[0,0,1], "-z":[0,0,-1],
              down:[0,0,-1], up:[0,0,1]};

function parseRegion(spec, bbox, band) {
  band = band || 0.15;
  const lim = {x:[bbox[0],bbox[3]], y:[bbox[1],bbox[4]], z:[bbox[2],bbox[5]]};
  const s = String(spec).trim().toLowerCase();
  if (NAMED_REGIONS[s]) {
    const [axis, side] = NAMED_REGIONS[s];
    const [lo, hi] = lim[axis], sp = hi - lo;
    return side === "<" ? {axis, lo, hi: lo + sp*band, label: s}
                        : {axis, lo: hi - sp*band, hi, label: s};
  }
  const m = /^\s*([xyz])\s*(<=|>=|<|>)\s*(-?\d*\.?\d+)\s*$/i.exec(s);
  if (m) {
    const axis = m[1].toLowerCase(), op = m[2], val = parseFloat(m[3]);
    const [lo, hi] = lim[axis];
    return (op === "<" || op === "<=")
      ? {axis, lo, hi: Math.min(val, hi), label: `${axis}${op}${val}`}
      : {axis, lo: Math.max(val, lo), hi, label: `${axis}${op}${val}`};
  }
  throw new Error(`cannot read region "${spec}". Use a name (bottom, top, left, right, front, back) or an inequality (z<5, x>30).`);
}
const regionContains = (r, p) => {
  const v = p[AXIS_INDEX[r.axis]];
  return v >= r.lo - 1e-9 && v <= r.hi + 1e-9;
};
function regionBoxCentroid(r, bbox) {
  const c = [(bbox[0]+bbox[3])/2, (bbox[1]+bbox[4])/2, (bbox[2]+bbox[5])/2];
  c[AXIS_INDEX[r.axis]] = (r.lo + r.hi)/2;
  return c;
}

function parseLoad(spec) {
  const s = String(spec).trim();
  const parts = s.split(/\s+(?:at|@|on)\s+/i);
  const head = parts[0].trim();
  const region = parts.length > 1 ? parts.slice(1).join(" ").trim() : "top";
  const m = /^\s*(-?\d*\.?\d+)\s*([a-zA-Z]*)/.exec(head);
  if (!m) throw new Error(`cannot read a force from "${spec}". Try "50N -Z at top".`);
  const unit = (m[2] || "n").toLowerCase();
  if (!(unit in FORCE_UNITS)) throw new Error(`unknown force unit "${m[2]}". Use N, kN, kg, kgf, lb.`);
  const newtons = parseFloat(m[1]) * FORCE_UNITS[unit];
  const rest = head.slice(m[0].length).trim().toLowerCase();
  let d;
  if (!rest) d = [0,0,-1];
  else if (DIRS[rest]) d = DIRS[rest];
  else {
    const nums = rest.match(/-?\d*\.?\d+/g);
    if (nums && nums.length === 3) d = nums.map(Number);
    else throw new Error(`cannot read a direction from "${rest}". Use -Z, +X, down, or a vector like "0,0,-1".`);
  }
  const n = Math.hypot(d[0], d[1], d[2]);
  if (n < 1e-9) throw new Error("load direction has zero length");
  return {newtons, dir: d.map(v => v/n), region};
}

const MOMENT_MAP = {z:{x:"1", y:"2", z:"t"}, x:{y:"1", z:"2", x:"t"}, y:{x:"1", z:"2", y:"t"}};
const cross = (a,b) => [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]];

function separates(fix, bbox, axis, pos, loadPt) {
  const ai = AXIS_INDEX[axis];
  let fLo, fHi;
  if (axis === fix.axis) { fLo = fix.lo; fHi = fix.hi; }
  else { fLo = bbox[ai]; fHi = bbox[ai+3]; }
  const lp = loadPt[ai];
  if (fLo > pos && fHi > pos) return lp < pos;
  if (fLo < pos && fHi < pos) return lp > pos;
  return false;
}

function sectionCentroid3d(sec) {
  const c = sec.centroid;
  if (!c) return null;
  if (sec.axis === "z") return [c[0], c[1], sec.pos];
  if (sec.axis === "x") return [sec.pos, c[0], c[1]];
  return [c[0], sec.pos, c[1]];
}

function solveLoadCase(axes, bbox, fix, fixC, loadN, loadDir, loadPt, scale) {
  const Fv = loadDir.map(d => loadN*d);
  const results = [];
  for (const axis of Object.keys(axes)) {
    const ai = AXIS_INDEX[axis];
    for (const sec of axes[axis]) {
      const C = sectionCentroid3d(sec);
      if (!C || sec.area <= 1e-9) continue;
      if (!separates(fix, bbox, axis, sec.pos, loadPt)) continue;
      const r = [loadPt[0]-C[0], loadPt[1]-C[1], loadPt[2]-C[2]];
      const M = cross(r, Fv);
      const mp = MOMENT_MAP[axis];
      let m1=0, m2=0, tors=0;
      for (const gax of Object.keys(mp)) {
        const v = M[AXIS_INDEX[gax]];
        if (mp[gax] === "1") m1 = v; else if (mp[gax] === "2") m2 = v; else tors = v;
      }
      const s1 = S1(sec), s2 = S2(sec);
      let sigma = 0;
      if (s1 > 1e-9) sigma += Math.abs(m1)/s1;
      if (s2 > 1e-9) sigma += Math.abs(m2)/s2;
      const axial = Math.abs(Fv[ai]);
      sigma += axial/sec.area;
      const shearV = Math.hypot(...[0,1,2].filter(k => k !== ai).map(k => Fv[k]));
      const tauV = SHEAR_PEAK * shearV / sec.area;
      let tauT = 0, noPath = false;
      if (Math.abs(tors) > 1e-9) {
        if (sec.torsQ > 1e-9) tauT = Math.abs(tors)/sec.torsQ; else noPath = true;
      }
      const tau = tauV + tauT;
      const sigmaEq = Math.sqrt(sigma*sigma + 3*tau*tau);
      if (sigmaEq <= 1e-12 && !noPath) continue;
      const cap = sec.sigma * scale;
      results.push({axis, sec, sigma, tau, tauShear:tauV, tauTorsion:tauT, sigmaEq, cap,
        sf: noPath ? 0 : cap/sigmaEq, torsion: Math.abs(tors)/1000,
        torsionModel: sec.torsModel, noTorsionPath: noPath,
        M1: m1/1000, M2: m2/1000, axialN: axial, shearN: shearV,
        shearGoverns: (3*tauV*tauV) > (sigma*sigma),
        torsionGoverns: tauT > tauV && tauT > 1e-3});
    }
  }
  if (!results.length) return [null, []];
  let worst = results[0];
  for (const r of results) if (r.sf < worst.sf) worst = r;
  return [worst, results];
}

function loadCaseDeflection(axes, bbox, fix, fixC, loadN, loadDir, loadPt, modulus) {
  let dom = "x", best = -1;
  for (const a of ["x","y","z"]) {
    const d = Math.abs(loadPt[AXIS_INDEX[a]] - fixC[AXIS_INDEX[a]]);
    if (d > best) { best = d; dom = a; }
  }
  const secs = axes[dom] || [];
  const ai = AXIS_INDEX[dom];
  const usable = [];
  for (const sec of secs) {
    const C = sectionCentroid3d(sec);
    if (!C || sec.area <= 1e-9) continue;
    if (!separates(fix, bbox, dom, sec.pos, loadPt)) continue;
    usable.push([sec, C]);
  }
  if (usable.length < 2) return [null, dom, false, 0];
  usable.sort((a,b) => a[0].pos - b[0].pos);
  let total = 0, twist = 0;
  for (let k = 0; k < usable.length; k++) {
    const [sec, C] = usable[k];
    let ds;
    if (k === 0) ds = usable[1][0].pos - usable[0][0].pos;
    else if (k === usable.length-1) ds = usable[k][0].pos - usable[k-1][0].pos;
    else ds = (usable[k+1][0].pos - usable[k-1][0].pos)/2;
    ds = Math.abs(ds);
    if (ds <= 0) continue;
    const r = [loadPt[0]-C[0], loadPt[1]-C[1], loadPt[2]-C[2]];
    const mm = cross(r, loadDir);
    const mp = MOMENT_MAP[sec.axis];
    let m1=0, m2=0, mt=0;
    for (const gax of Object.keys(mp)) {
      const v = mm[AXIS_INDEX[gax]];
      if (mp[gax]==="1") m1=v; else if (mp[gax]==="2") m2=v; else mt=v;
    }
    let term = 0;
    if (sec.I1 > 1e-9) term += m1*m1/sec.I1;
    if (sec.I2 > 1e-9) term += m2*m2/sec.I2;
    total += term*ds;
    if (Math.abs(mt) > 1e-12 && sec.torsJ > 1e-9) twist += Math.abs(mt)*loadN*ds/sec.torsJ;
  }
  const G = shearModulus(modulus);
  const twistDeg = (twist > 0 && G > 0) ? (twist/G) * 180/Math.PI : 0;
  if (total <= 0) return [null, dom, false, twistDeg];
  const lateral = [0,1,2].filter(k => k !== ai);
  const n = usable.length;
  const meanC = [0,1,2].map(k => usable.reduce((s,[,C]) => s + C[k], 0)/n);
  const offset = Math.max(...lateral.map(k => Math.abs(loadPt[k] - meanC[k])));
  const run = Math.abs(usable[n-1][0].pos - usable[0][0].pos) || 1;
  return [loadN*total/modulus, dom, offset > 0.25*run, twistDeg];
}

// --- stress concentrations --------------------------------------------------
// Beam theory gives the nominal stress; what starts the crack is usually a
// hole or a sharp inside corner raising it locally. Kt is geometric, Kf is
// what the material feels: Kf = 1 + q(Kt-1), with q falling as the polymer
// gets more ductile and yields locally instead of cracking.
const KT_CAP = 6.0;

function notchSensitivity(elong) {
  if (elong <= 5) return 1.0;
  return Math.max(0.40, 1.0 - (elong - 5.0)/60.0);
}
function ktHole(d, w) {
  if (w <= d || d <= 0) return 1.0;
  const x = Math.min(d/w, 0.5);
  return Math.max(1.0, 3.00 - 3.13*x + 3.66*x*x - 1.53*x*x*x);
}
function ktNotch(depth, root) {
  if (root <= 1e-9 || depth <= 0) return 1.0;
  return Math.min(KT_CAP, 1.0 + 2.0*Math.sqrt(depth/root));
}
function signedArea(pts) {
  let a = 0;
  for (let k = 0; k < pts.length; k++) {
    const p = pts[k], q = pts[(k+1)%pts.length];
    a += p[0]*q[1] - q[0]*p[1];
  }
  return a*0.5;
}
const centroidOf = pts => [pts.reduce((s,p)=>s+p[0],0)/pts.length,
                           pts.reduce((s,p)=>s+p[1],0)/pts.length];
function resample(pts, step) {
  const out = [pts[0]];
  let acc = 0;
  for (let k = 1; k < pts.length; k++) {
    const [x0,y0] = pts[k-1], [x1,y1] = pts[k];
    const d = Math.hypot(x1-x0, y1-y0);
    if (d < 1e-9) continue;
    acc += d;
    while (acc >= step) {
      const t = 1 - (acc - step)/d;
      out.push([x0 + (x1-x0)*t, y0 + (y1-y0)*t]);
      acc -= step;
    }
  }
  return out;
}
function convexHull(pts) {
  const p = [...new Set(pts.map(q => `${q[0].toFixed(4)},${q[1].toFixed(4)}`))]
    .map(s => s.split(",").map(Number)).sort((a,b) => a[0]-b[0] || a[1]-b[1]);
  if (p.length < 3) return p;
  const half = seq => {
    const out = [];
    for (const q of seq) {
      while (out.length >= 2) {
        const [ax,ay] = out[out.length-2], [bx,by] = out[out.length-1];
        if ((bx-ax)*(q[1]-ay) - (by-ay)*(q[0]-ax) > 0) break;
        out.pop();
      }
      out.push(q);
    }
    return out;
  };
  return half(p).slice(0,-1).concat(half([...p].reverse()).slice(0,-1));
}
function distToPoly(pt, poly) {
  let best = Infinity;
  for (let k = 0; k < poly.length; k++) {
    const [ax,ay] = poly[k], [bx,by] = poly[(k+1)%poly.length];
    const dx = bx-ax, dy = by-ay, L2 = dx*dx + dy*dy;
    const t = L2 < 1e-12 ? 0 : Math.max(0, Math.min(1, ((pt[0]-ax)*dx + (pt[1]-ay)*dy)/L2));
    best = Math.min(best, Math.hypot(pt[0]-(ax+dx*t), pt[1]-(ay+dy*t)));
  }
  return best;
}
function loopsOfLayer(m, lay, tol) {
  tol = tol || 0.12;
  const loops = []; let cur = [], prev = null;
  for (let i = lay.segStart; i < lay.segEnd; i++) {
    const f = m.feat[i];
    if (f !== F.OUTER && f !== F.OVERHANG) continue;
    const p1 = [m.x1[i], m.y1[i]], p2 = [m.x2[i], m.y2[i]];
    if (!prev || Math.hypot(p1[0]-prev[0], p1[1]-prev[1]) > tol) {
      if (cur.length >= 4) loops.push(cur);
      cur = [p1];
    }
    cur.push(p2); prev = p2;
  }
  if (cur.length >= 4) loops.push(cur);
  return loops;
}
function detectConcentrations(m, beadW, maxLayers) {
  beadW = beadW || 0.45; maxLayers = maxLayers || 40;
  const feats = [];
  const layers = m.layers.filter(l => l.segEnd > l.segStart);
  if (!layers.length) return feats;
  const step = Math.max(1, Math.floor(layers.length/maxLayers));
  for (let li = 0; li < layers.length; li += step) {
    const lay = layers[li];
    const loops = loopsOfLayer(m, lay);
    if (!loops.length) continue;
    const signed = loops.map(lp => [lp, signedArea(lp)]);
    const outers = signed.filter(([,a]) => a > 0).map(([lp]) => lp);
    const holes = signed.filter(([,a]) => a < 0).map(([lp,a]) => [lp, -a]);
    if (!outers.length) continue;
    let outer = outers[0];
    for (const lp of outers) if (Math.abs(signedArea(lp)) > Math.abs(signedArea(outer))) outer = lp;

    for (const [lp, area] of holes) {
      const r = Math.sqrt(area/Math.PI) - beadW*0.5;
      if (r < beadW) continue;
      const c = centroidOf(lp);
      const lig = (distToPoly(c, outer) + beadW*0.5) - r;
      if (lig <= 0) continue;
      const d = 2*r;
      feats.push({kind:"hole", x:c[0], y:c[1], z:lay.z, r, size:d,
                  kt: ktHole(d, d + 2*lig), layer: lay.index});
    }
    const pts = resample(outer, Math.max(beadW, 0.4));
    const n = pts.length;
    if (n < 12) continue;
    const hull = convexHull(outer);
    const ccw = signedArea(outer) > 0;
    const span = 3;
    for (let k = 0; k < n; k++) {
      const a = pts[(k-span+n)%n], b = pts[k], c = pts[(k+span)%n];
      const v1 = [b[0]-a[0], b[1]-a[1]], v2 = [c[0]-b[0], c[1]-b[1]];
      const crossz = v1[0]*v2[1] - v1[1]*v2[0];
      if ((crossz > 0) === ccw) continue;
      const l1 = Math.hypot(v1[0],v1[1]), l2 = Math.hypot(v2[0],v2[1]);
      if (l1 < 1e-9 || l2 < 1e-9) continue;
      const cosang = Math.max(-1, Math.min(1, (v1[0]*v2[0]+v1[1]*v2[1])/(l1*l2)));
      const turn = Math.acos(cosang);
      if (turn < 0.35) continue;
      const root = Math.max(beadW*0.5, (l1+l2)*0.5/Math.max(turn,1e-6));
      const depth = distToPoly(b, hull);
      if (depth < beadW) continue;
      const kt = ktNotch(depth, root);
      if (kt <= 1.05) continue;
      feats.push({kind:"notch", x:b[0], y:b[1], z:lay.z, r:root, size:depth, kt, layer:lay.index});
    }
  }
  // The same hole appears on every layer it passes through. Merge them and
  // keep the z range, so the factor applies over its real height.
  feats.sort((a,b) => b.kt - a.kt);
  const merged = [];
  for (const f of feats) {
    const hit = merged.find(mm => Math.abs(f.x-mm.x) < 1 && Math.abs(f.y-mm.y) < 1
                                  && f.kind === mm.kind);
    if (hit) { hit.zLo = Math.min(hit.zLo, f.z); hit.zHi = Math.max(hit.zHi, f.z); continue; }
    f.zLo = f.z; f.zHi = f.z;
    merged.push(f);
    if (merged.length >= 12) break;
  }
  return merged;
}
function stitchLoops(edges, tol) {
  tol = tol || 1e-4;
  const key = p => `${Math.round(p[0]/tol)},${Math.round(p[1]/tol)}`;
  const byStart = new Map();
  edges.forEach((e, i) => {
    const k = key(e[0]);
    if (!byStart.has(k)) byStart.set(k, []);
    byStart.get(k).push(i);
  });
  const used = new Set(), loops = [];
  for (let i = 0; i < edges.length; i++) {
    if (used.has(i)) continue;
    const a0 = edges[i][0];
    const loop = [a0, edges[i][1]];
    used.add(i);
    let cur = edges[i][1];
    for (let guard = 0; guard < edges.length + 1; guard++) {
      const cands = byStart.get(key(cur)) || [];
      let j = -1;
      for (const c of cands) if (!used.has(c)) { j = c; break; }
      if (j < 0) break;
      used.add(j);
      loop.push(edges[j][1]);
      cur = edges[j][1];
      if (Math.hypot(cur[0]-a0[0], cur[1]-a0[1]) < tol*10) break;
    }
    if (loop.length >= 4) loops.push(loop);
  }
  return loops;
}
function meshConcentrations(mesh, maxPlanes) {
  maxPlanes = maxPlanes || 12;
  const feats = [];
  const z0 = mesh.bbox[2], z1 = mesh.bbox[5];
  if (z1 - z0 <= 1e-9) return feats;
  for (let k = 0; k < maxPlanes; k++) {
    const z = z0 + (z1-z0)*(k+0.5)/maxPlanes;
    const edges = [];
    for (let ti = 0; ti < mesh.tris.length; ti++) {
      const t = mesh.tris[ti];
      const vals = [t[0][2], t[1][2], t[2][2]];
      if (Math.min(...vals) > z || Math.max(...vals) < z) continue;
      const seg = triPlaneSegment(t, 2, z, 0, 1, mesh.normals[ti]);
      if (seg) edges.push(seg);
    }
    if (edges.length < 6) continue;
    const loops = stitchLoops(edges);
    if (!loops.length) continue;
    const signed = loops.map(lp => [lp, signedArea(lp)]);
    const outers = signed.filter(([,a]) => a > 0).map(([lp]) => lp);
    const holes = signed.filter(([,a]) => a < 0).map(([lp,a]) => [lp, -a]);
    if (!outers.length) continue;
    let outer = outers[0];
    for (const lp of outers) if (Math.abs(signedArea(lp)) > Math.abs(signedArea(outer))) outer = lp;
    for (const [lp, area] of holes) {
      const r = Math.sqrt(area/Math.PI);
      if (r < 0.3) continue;
      const c = centroidOf(lp);
      const lig = distToPoly(c, outer) - r;
      if (lig <= 0) continue;
      feats.push({kind:"hole", x:c[0], y:c[1], z, r, size:2*r,
                  kt: ktHole(2*r, 2*r + 2*lig), layer:null});
    }
  }
  feats.sort((a,b) => b.kt - a.kt);
  const merged = [];
  for (const f of feats) {
    if (merged.some(mm => Math.abs(f.x-mm.x) < 1 && Math.abs(f.y-mm.y) < 1)) continue;
    merged.push(f);
    if (merged.length >= 8) break;
  }
  return merged;
}
function concentrationsFor(model) {
  if (model._conc) return model._conc;
  let out;
  if (model.kind === "stl") out = meshConcentrations(model);
  else {
    const ws = [];
    const stride = Math.max(1, Math.floor(model.x1.length/400));
    for (let i = 0; i < model.x1.length; i += stride) if (model.w[i] > 0) ws.push(model.w[i]);
    ws.sort((a,b) => a-b);
    out = detectConcentrations(model, ws.length ? ws[ws.length>>1] : 0.45);
  }
  model._conc = out;
  return out;
}
function applyConcentration(sec, feats, q, margin) {
  if (!feats || !feats.length || q <= 0) return;
  margin = margin || 0;
  let worst = 1, kind = "", size = 0;
  for (const f of feats) {
    let hit;
    if (sec.axis === "z") {
      const lo = f.zLo !== undefined ? f.zLo : f.z;
      const hi = f.zHi !== undefined ? f.zHi : f.z;
      hit = sec.pos >= lo - margin && sec.pos <= hi + margin;
    }
    else {
      const c = sec.axis === "x" ? f.x : f.y;
      hit = Math.abs(c - sec.pos) <= f.r + margin;
    }
    if (hit && f.kt > worst) { worst = f.kt; kind = f.kind; size = f.size; }
  }
  if (worst <= 1) return;
  sec.kt = worst;
  sec.kf = 1 + q*(worst - 1);
  sec.ktKind = kind;
  sec.ktSize = size;
  sec.sigma /= sec.kf;
}

function deflectionCurve(axes, bbox, fix, fixC, loadN, loadDir, loadPt, modulus, samples) {
  samples = samples || 48;
  let dom = "x", best = -1;
  for (const a of ["x","y","z"]) {
    const d = Math.abs(loadPt[AXIS_INDEX[a]] - fixC[AXIS_INDEX[a]]);
    if (d > best) { best = d; dom = a; }
  }
  const ai = AXIS_INDEX[dom];
  const usable = [];
  for (const sec of (axes[dom] || [])) {
    const C = sectionCentroid3d(sec);
    if (!C || sec.area <= 1e-9) continue;
    if (!separates(fix, bbox, dom, sec.pos, loadPt)) continue;
    usable.push([sec, C]);
  }
  if (usable.length < 2) return {stations:[], dir:[0,0,0], axis:dom};
  const fixedBelow = fixC[ai] < loadPt[ai];
  usable.sort((a,b) => fixedBelow ? a[0].pos - b[0].pos : b[0].pos - a[0].pos);

  const n = [0,0,0]; n[ai] = 1;
  const dot = loadDir[0]*n[0] + loadDir[1]*n[1] + loadDir[2]*n[2];
  const trans = loadDir.map((v,k) => v - dot*n[k]);
  const tl = Math.hypot(trans[0], trans[1], trans[2]);
  const dir = tl > 1e-9 ? trans.map(v => v/tl) : loadDir.slice();

  const kappa = [];
  for (const [sec, C] of usable) {
    const r = [loadPt[0]-C[0], loadPt[1]-C[1], loadPt[2]-C[2]];
    const m = cross(r, loadDir);
    const mp = MOMENT_MAP[sec.axis];
    let m1=0, m2=0;
    for (const gax of Object.keys(mp)) {
      const v = m[AXIS_INDEX[gax]];
      if (mp[gax]==="1") m1=v; else if (mp[gax]==="2") m2=v;
    }
    const mag = Math.hypot(m1, m2);
    let c = 0;
    if (mag > 1e-12 && modulus > 0) {
      let comp = 0;
      if (sec.I1 > 1e-9) comp += m1*m1/sec.I1;
      if (sec.I2 > 1e-9) comp += m2*m2/sec.I2;
      c = loadN*comp/(modulus*mag);
    }
    kappa.push([sec.pos, c]);
  }
  let theta = 0, v = 0;
  let out = [[kappa[0][0], 0]];
  for (let k = 1; k < kappa.length; k++) {
    const ds = Math.abs(kappa[k][0] - kappa[k-1][0]);
    const thPrev = theta;
    theta += 0.5*(kappa[k][1] + kappa[k-1][1])*ds;
    v += 0.5*(theta + thPrev)*ds;
    out.push([kappa[k][0], v]);
  }
  if (samples && out.length > samples) {
    const step = out.length/samples, red = [];
    for (let i = 0; i < samples; i++) red.push(out[Math.min(out.length-1, Math.floor(i*step))]);
    red.push(out[out.length-1]);
    out = red;
  }
  return {stations: out, dir, axis: dom};
}

// Ordered loops per layer, so the viewer can fill them as well as stroke them.
// G-code perimeters come out of the file already ordered; a mesh's section
// edges are stitched into loops. Both end up as closed rings of points.
function shellLoops(model, maxLayers) {
  maxLayers = maxLayers || 110;
  const out = [];
  if (model.kind === "stl") {
    const z0 = model.bbox[2], z1 = model.bbox[5];
    const n = Math.max(2, Math.min(maxLayers, 70));
    for (let k = 0; k < n; k++) {
      const z = z0 + (z1-z0)*(k+0.5)/n;
      const edges = [];
      for (let ti = 0; ti < model.tris.length; ti++) {
        const t = model.tris[ti];
        const v = [t[0][2], t[1][2], t[2][2]];
        if (Math.min(...v) > z || Math.max(...v) < z) continue;
        const seg = triPlaneSegment(t, 2, z, 0, 1, model.normals[ti]);
        if (seg) edges.push(seg);
      }
      if (edges.length < 3) continue;
      const loops = stitchLoops(edges).filter(l => l.length >= 4);
      if (loops.length) out.push({z, loops});
    }
    return out;
  }
  const layers = model.layers;
  if (!layers.length) return out;
  const step = Math.max(1, Math.ceil(layers.length/maxLayers));
  for (let li = 0; li < layers.length; li += step) {
    const lay = layers[li];
    const loops = loopsOfLayer(model, lay).filter(l => l.length >= 4);
    if (loops.length) out.push({z: lay.z, loops});
  }
  return out;
}

// A stack of Z contours: enough of the part to recognise it, cheap enough to
// spin at 60fps. G-code gives its outer-wall loops directly; a mesh is sliced
// at the same heights so both render the same way.
function contourStack(model, maxLayers) {
  maxLayers = maxLayers || 90;
  const out = [];
  if (model.kind === "stl") {
    const z0 = model.bbox[2], z1 = model.bbox[5];
    const n = Math.max(2, Math.min(maxLayers, 60));
    for (let k = 0; k < n; k++) {
      const z = z0 + (z1-z0)*(k+0.5)/n;
      const segs = [];
      for (let ti = 0; ti < model.tris.length; ti++) {
        const t = model.tris[ti];
        const vals = [t[0][2], t[1][2], t[2][2]];
        if (Math.min(...vals) > z || Math.max(...vals) < z) continue;
        const seg = triPlaneSegment(t, 2, z, 0, 1, model.normals[ti]);
        if (seg) segs.push(seg[0][0], seg[0][1], seg[1][0], seg[1][1]);
      }
      if (segs.length) out.push({z, segs: Float32Array.from(segs)});
    }
    return out;
  }
  const layers = model.layers;
  if (!layers.length) return out;
  const step = Math.max(1, Math.ceil(layers.length/maxLayers));
  for (let li = 0; li < layers.length; li += step) {
    const lay = layers[li];
    const segs = [];
    for (let i = lay.segStart; i < lay.segEnd; i++) {
      const f = model.feat[i];
      if (f !== F.OUTER && f !== F.OVERHANG) continue;
      segs.push(model.x1[i], model.y1[i], model.x2[i], model.y2[i]);
    }
    if (segs.length) out.push({z: lay.z, segs: Float32Array.from(segs)});
  }
  // A part with no tagged outer wall still has to be drawn.
  if (!out.length) {
    for (let li = 0; li < layers.length; li += step) {
      const lay = layers[li], segs = [];
      for (let i = lay.segStart; i < lay.segEnd; i++) {
        if (NON_PART.has(model.feat[i])) continue;
        segs.push(model.x1[i], model.y1[i], model.x2[i], model.y2[i]);
      }
      if (segs.length) out.push({z: lay.z, segs: Float32Array.from(segs)});
    }
  }
  return out;
}

// --- derating ---------------------------------------------------------------
const QUALITY = {good:0.95, typical:0.85, poor:0.70};
function thermalFactor(tempC, hdt) {
  if (tempC === null || tempC === undefined) return [1, ""];
  const knee = hdt - 20;
  if (tempC <= knee) return [1, ""];
  if (tempC <= hdt) {
    const f = 1 - 0.75*Math.pow((tempC-knee)/20, 2);
    return [f, f < 0.9 ? "approaching HDT" : ""];
  }
  if (tempC <= hdt + 20)
    return [Math.max(0.25 - 0.20*(tempC-hdt)/20, 0.05), "above HDT — the part will soften and creep"];
  return [0.03, "far above HDT — not a structural part at this temperature"];
}

// --- STL --------------------------------------------------------------------
function loadSTL(input) {
  // A caller may hand us an ArrayBuffer or a view onto one (a Node Buffer is
  // a view into a shared pool). Reading from byte 0 of the underlying buffer
  // in that case parses whatever else happens to be in the pool.
  const buf = ArrayBuffer.isView(input)
    ? input.buffer.slice(input.byteOffset, input.byteOffset + input.byteLength)
    : input;
  const bytes = new Uint8Array(buf);
  const head = String.fromCharCode(...bytes.slice(0,5)).toLowerCase();
  if (head === "solid") {
    const text = new TextDecoder().decode(bytes);
    if (text.includes("facet") && text.includes("vertex")) return loadSTLAscii(text);
  }
  return loadSTLBinary(buf);
}
function loadSTLAscii(text) {
  const tris = [], normals = [];
  let verts = [], curN = [0,0,1];
  for (const line of text.split("\n")) {
    const s = line.trim();
    if (s.startsWith("facet normal")) {
      const p = s.split(/\s+/);
      curN = [parseFloat(p[2])||0, parseFloat(p[3])||0, parseFloat(p[4])||1];
      verts = [];
    } else if (s.startsWith("vertex")) {
      const p = s.split(/\s+/);
      verts.push([parseFloat(p[1]), parseFloat(p[2]), parseFloat(p[3])]);
    } else if (s.startsWith("endfacet")) {
      if (verts.length === 3) { tris.push(verts); normals.push(curN); }
      verts = [];
    }
  }
  if (!tris.length) throw new Error("no triangles found in ASCII STL");
  return mkMesh(tris, normals);
}
function loadSTLBinary(buf) {
  const dv = new DataView(buf);
  if (buf.byteLength < 84) throw new Error("truncated STL");
  let count = dv.getUint32(80, true);
  const avail = Math.floor((buf.byteLength - 84)/50);
  if (count > avail) count = avail;
  const tris = [], normals = [];
  for (let k = 0; k < count; k++) {
    const o = 84 + k*50;
    normals.push([dv.getFloat32(o,true), dv.getFloat32(o+4,true), dv.getFloat32(o+8,true)]);
    tris.push([
      [dv.getFloat32(o+12,true), dv.getFloat32(o+16,true), dv.getFloat32(o+20,true)],
      [dv.getFloat32(o+24,true), dv.getFloat32(o+28,true), dv.getFloat32(o+32,true)],
      [dv.getFloat32(o+36,true), dv.getFloat32(o+40,true), dv.getFloat32(o+44,true)]]);
  }
  if (!tris.length) throw new Error("no triangles found in binary STL");
  return mkMesh(tris, normals);
}
function mkMesh(tris, normals) {
  let lo=[Infinity,Infinity,Infinity], hi=[-Infinity,-Infinity,-Infinity];
  for (const t of tris) for (const v of t) for (let k=0;k<3;k++) {
    if (v[k]<lo[k]) lo[k]=v[k]; if (v[k]>hi[k]) hi[k]=v[k];
  }
  return {tris, normals, bbox:[lo[0],lo[1],lo[2],hi[0],hi[1],hi[2]], kind:"stl",
          layers:[], warnings:[], config:{}, slicer:"mesh",
          volume(){ let v=0; for (const [a,b,c] of tris)
            v += (a[0]*(b[1]*c[2]-c[1]*b[2]) - a[1]*(b[0]*c[2]-c[0]*b[2]) + a[2]*(b[0]*c[1]-c[0]*b[1]))/6;
            return Math.abs(v); }};
}
function triPlaneSegment(tri, ai, pos, ui, vi, normal) {
  const d = [tri[0][ai]-pos, tri[1][ai]-pos, tri[2][ai]-pos];
  const pts = [];
  for (let k = 0; k < 3; k++) {
    const k2 = (k+1)%3, d1 = d[k], d2 = d[k2];
    if ((d1 > 0) !== (d2 > 0)) {
      if (Math.abs(d2-d1) < 1e-15) continue;
      const t = d1/(d1-d2), p1 = tri[k], p2 = tri[k2];
      pts.push([p1[ui] + (p2[ui]-p1[ui])*t, p1[vi] + (p2[vi]-p1[vi])*t]);
    } else if (Math.abs(d1) < 1e-12 && Math.abs(d2) < 1e-12) return null;
  }
  if (pts.length !== 2) return null;
  const [[u1,v1],[u2,v2]] = pts;
  if (Math.abs(u1-u2) < 1e-12 && Math.abs(v1-v2) < 1e-12) return null;
  const nu = normal[ui], nv = normal[vi];
  if (((v2-v1)*nu - (u2-u1)*nv) < 0) return [[u2,v2],[u1,v1]];
  return [[u1,v1],[u2,v2]];
}
function sectionProperties(edges) {
  let A2 = 0;
  for (const [[u1,v1],[u2,v2]] of edges) A2 += u1*v2 - u2*v1;
  let A = A2*0.5;
  if (Math.abs(A) < 1e-12) return null;
  const sgn = A > 0 ? 1 : -1; A = Math.abs(A);
  let cu=0, cv=0, Iuu=0, Ivv=0;
  let umin=Infinity, umax=-Infinity, vmin=Infinity, vmax=-Infinity;
  for (const [[u1,v1],[u2,v2]] of edges) {
    const cr = (u1*v2 - u2*v1)*sgn;
    cu += (u1+u2)*cr; cv += (v1+v2)*cr;
    Ivv += (v1*v1 + v1*v2 + v2*v2)*cr;
    Iuu += (u1*u1 + u1*u2 + u2*u2)*cr;
    umin=Math.min(umin,u1,u2); umax=Math.max(umax,u1,u2);
    vmin=Math.min(vmin,v1,v2); vmax=Math.max(vmax,v1,v2);
  }
  cu /= 6*A; cv /= 6*A;
  Iuu = Iuu/12 - A*cu*cu; Ivv = Ivv/12 - A*cv*cv;
  let perim = 0;
  for (const [[u1,v1],[u2,v2]] of edges) perim += Math.hypot(u2-u1, v2-v1);
  return {area:A, cu, cv, perimeter:perim, Iuu:Math.max(Iuu,0), Ivv:Math.max(Ivv,0),
          cmaxU:Math.max(umax-cu, cu-umin), cmaxV:Math.max(vmax-cv, cv-vmin)};
}
function sliceMesh(mesh, axis, nplanes, trimFrac) {
  nplanes = nplanes || 400; trimFrac = trimFrac === undefined ? 0.01 : trimFrac;
  const ai = AXIS_INDEX[axis];
  const UV = {x:[1,2], y:[2,0], z:[0,1]}[axis];
  let lo = mesh.bbox[ai], hi = mesh.bbox[ai+3];
  let span = hi - lo;
  if (span <= 1e-9) return [];
  const pad = span*trimFrac; lo += pad; hi -= pad; span = hi - lo;
  if (span <= 1e-9) return [];
  const step = span/Math.max(1, nplanes-1);
  const buckets = Array.from({length:nplanes}, () => []);
  for (let ti = 0; ti < mesh.tris.length; ti++) {
    const t = mesh.tris[ti];
    const vals = [t[0][ai], t[1][ai], t[2][ai]];
    const tlo = Math.min(...vals), thi = Math.max(...vals);
    if (thi < lo || tlo > hi) continue;
    const s = Math.max(0, Math.ceil((tlo-lo)/step));
    const e = Math.min(nplanes-1, Math.floor((thi-lo)/step));
    for (let b = s; b <= e; b++) buckets[b].push(ti);
  }
  const out = [];
  for (let b = 0; b < nplanes; b++) {
    if (!buckets[b].length) continue;
    const pos = lo + b*step, edges = [];
    for (const ti of buckets[b]) {
      const seg = triPlaneSegment(mesh.tris[ti], ai, pos, UV[0], UV[1], mesh.normals[ti]);
      if (seg) edges.push(seg);
    }
    if (edges.length < 3) continue;
    const p = sectionProperties(edges);
    if (!p || p.area < 1e-6) continue;
    const sec = mkSection(axis, pos);
    sec.area = p.area;
    sec.perimeter = p.perimeter;
    if (axis === "y") {
      sec.I1 = p.Iuu; sec.c1 = p.cmaxU; sec.I2 = p.Ivv; sec.c2 = p.cmaxV;
      sec.centroid = [p.cv, p.cu];
    } else {
      sec.I1 = p.Ivv; sec.c1 = p.cmaxV; sec.I2 = p.Iuu; sec.c2 = p.cmaxU;
      sec.centroid = [p.cu, p.cv];
    }
    out.push(sec);
  }
  return out;
}

// --- ZIP / DEFLATE ----------------------------------------------------------
//
// Bambu Studio and OrcaSlicer export a sliced plate as `.gcode.3mf`: a ZIP
// holding the G-code beside the model and thumbnails. Reading it needs raw
// DEFLATE. The browser has DecompressionStream, but only asynchronously, and
// making the whole parse path async to reach it would ripple through every
// caller — so inflate is implemented here and stays synchronous. It is
// checked against Python's zlib on real archives.

const LEN_BASE = [3,4,5,6,7,8,9,10,11,13,15,17,19,23,27,31,35,43,51,59,67,83,99,115,131,163,195,227,258];
const LEN_EXTRA = [0,0,0,0,0,0,0,0,1,1,1,1,2,2,2,2,3,3,3,3,4,4,4,4,5,5,5,5,0];
const DIST_BASE = [1,2,3,4,5,7,9,13,17,25,33,49,65,97,129,193,257,385,513,769,1025,1537,2049,3073,4097,6145,8193,12289,16385,24577];
const DIST_EXTRA = [0,0,0,0,1,1,2,2,3,3,4,4,5,5,6,6,7,7,8,8,9,9,10,10,11,11,12,12,13,13];
const CLEN_ORDER = [16,17,18,0,8,7,9,6,10,5,11,4,12,3,13,2,14,1,15];

function buildHuffman(lengths) {
  // Canonical Huffman: counts per bit length, then first code per length.
  let maxLen = 0;
  for (const l of lengths) if (l > maxLen) maxLen = l;
  const blCount = new Int32Array(maxLen + 1);
  for (const l of lengths) if (l) blCount[l]++;
  const next = new Int32Array(maxLen + 2);
  let code = 0;
  for (let b = 1; b <= maxLen; b++) { code = (code + blCount[b-1]) << 1; next[b] = code; }
  const counts = blCount, symbols = new Int32Array(lengths.length);
  const offs = new Int32Array(maxLen + 2);
  let total = 0;
  for (let b = 1; b <= maxLen; b++) { offs[b] = total; total += counts[b]; }
  const fill = offs.slice();
  for (let sym = 0; sym < lengths.length; sym++) {
    const l = lengths[sym];
    if (l) symbols[fill[l]++] = sym;
  }
  return {counts, symbols, maxLen};
}

function makeReader(bytes) {
  let pos = 0, bit = 0, val = 0;
  return {
    bits(n) {
      while (bit < n) {
        if (pos >= bytes.length) throw new Error("deflate: out of input");
        val |= bytes[pos++] << bit;
        bit += 8;
      }
      const out = val & ((1 << n) - 1);
      val >>>= n; bit -= n;
      return out;
    },
    align() { val = 0; bit = 0; },
    byte() { if (pos >= bytes.length) throw new Error("deflate: out of input"); return bytes[pos++]; },
    copy(n) { const s = bytes.subarray(pos, pos + n); pos += n; return s; },
    get pos() { return pos; },
  };
}

function decodeSym(br, tree) {
  let code = 0, first = 0, index = 0;
  for (let len = 1; len <= tree.maxLen; len++) {
    code |= br.bits(1);
    const count = tree.counts[len];
    if (code - first < count) return tree.symbols[index + (code - first)];
    index += count;
    first = (first + count) << 1;
    code <<= 1;
  }
  throw new Error("deflate: bad symbol");
}

function inflateRaw(bytes, expected) {
  const br = makeReader(bytes);
  const out = expected ? new Uint8Array(expected) : new Uint8Array(Math.max(1024, bytes.length * 4));
  let o = 0;
  const push = b => {
    if (o >= out.length) throw new Error("deflate: output overrun");
    out[o++] = b;
  };
  let fixedLit = null, fixedDist = null;
  for (;;) {
    const final = br.bits(1), type = br.bits(2);
    if (type === 0) {
      br.align();
      const a = br.byte(), b = br.byte();
      br.byte(); br.byte();                       // one's complement, unchecked
      const len = a | (b << 8);
      const chunk = br.copy(len);
      for (let i = 0; i < chunk.length; i++) push(chunk[i]);
    } else {
      let lit, dist;
      if (type === 1) {
        if (!fixedLit) {
          const ll = new Uint8Array(288);
          ll.fill(8, 0, 144); ll.fill(9, 144, 256); ll.fill(7, 256, 280); ll.fill(8, 280, 288);
          fixedLit = buildHuffman(ll);
          fixedDist = buildHuffman(new Uint8Array(30).fill(5));
        }
        lit = fixedLit; dist = fixedDist;
      } else if (type === 2) {
        const hlit = br.bits(5) + 257, hdist = br.bits(5) + 1, hclen = br.bits(4) + 4;
        const clen = new Uint8Array(19);
        for (let i = 0; i < hclen; i++) clen[CLEN_ORDER[i]] = br.bits(3);
        const ctree = buildHuffman(clen);
        const lens = new Uint8Array(hlit + hdist);
        for (let i = 0; i < lens.length;) {
          const sym = decodeSym(br, ctree);
          if (sym < 16) lens[i++] = sym;
          else if (sym === 16) { const p = lens[i-1], n = 3 + br.bits(2); for (let k=0;k<n;k++) lens[i++] = p; }
          else if (sym === 17) { const n = 3 + br.bits(3); i += n; }
          else { const n = 11 + br.bits(7); i += n; }
        }
        lit = buildHuffman(lens.subarray(0, hlit));
        dist = buildHuffman(lens.subarray(hlit));
      } else throw new Error("deflate: reserved block type");

      for (;;) {
        const sym = decodeSym(br, lit);
        if (sym === 256) break;
        if (sym < 256) { push(sym); continue; }
        const li = sym - 257;
        if (li >= LEN_BASE.length) throw new Error("deflate: bad length code");
        const length = LEN_BASE[li] + br.bits(LEN_EXTRA[li]);
        const ds = decodeSym(br, dist);
        const d = DIST_BASE[ds] + br.bits(DIST_EXTRA[ds]);
        if (d > o) throw new Error("deflate: distance before start");
        for (let k = 0; k < length; k++) push(out[o - d]);
      }
    }
    if (final) break;
  }
  return o === out.length ? out : out.subarray(0, o);
}

function readZip(buf) {
  const u8 = ArrayBuffer.isView(buf) ? new Uint8Array(buf.buffer, buf.byteOffset, buf.byteLength)
                                     : new Uint8Array(buf);
  const dv = new DataView(u8.buffer, u8.byteOffset, u8.byteLength);
  // End of central directory, scanned back over the comment field
  let eocd = -1;
  for (let i = u8.length - 22; i >= Math.max(0, u8.length - 66000); i--) {
    if (dv.getUint32(i, true) === 0x06054b50) { eocd = i; break; }
  }
  if (eocd < 0) throw new Error("not a ZIP archive (no end-of-central-directory record)");
  const count = dv.getUint16(eocd + 10, true);
  let p = dv.getUint32(eocd + 16, true);
  const entries = [];
  for (let k = 0; k < count && p + 46 <= u8.length; k++) {
    if (dv.getUint32(p, true) !== 0x02014b50) break;
    const method = dv.getUint16(p + 10, true);
    const csize = dv.getUint32(p + 20, true);
    const usize = dv.getUint32(p + 24, true);
    const nlen = dv.getUint16(p + 28, true);
    const elen = dv.getUint16(p + 30, true);
    const clen = dv.getUint16(p + 32, true);
    const off = dv.getUint32(p + 42, true);
    const name = new TextDecoder().decode(u8.subarray(p + 46, p + 46 + nlen));
    entries.push({name, method, csize, usize, off});
    p += 46 + nlen + elen + clen;
  }
  const read = e => {
    if (dv.getUint32(e.off, true) !== 0x04034b50) throw new Error("ZIP: bad local header");
    const nlen = dv.getUint16(e.off + 26, true), elen = dv.getUint16(e.off + 28, true);
    const start = e.off + 30 + nlen + elen;
    const raw = u8.subarray(start, start + e.csize);
    if (e.method === 0) return raw;
    if (e.method === 8) return inflateRaw(raw, e.usize);
    throw new Error(`ZIP: unsupported compression method ${e.method}`);
  };
  return {entries, read};
}

// --- STEP (ISO 10303-21) ----------------------------------------------------
//
// STEP is a boundary representation: faces are trimmed analytic and NURBS
// surfaces, not triangles. Turning those into the cross-sections this tool
// needs means a full tessellator — which is the bulk of what a kernel like
// OpenCASCADE does, and getting it subtly wrong would mean silently wrong
// areas and therefore silently wrong strength.
//
// So: AP242 files that carry tessellated geometry are read directly, and
// everything else is reported precisely — naming the surface types in the
// file — rather than half-parsed.

const STEP_SURFACES = [
  "B_SPLINE_SURFACE_WITH_KNOTS", "RATIONAL_B_SPLINE_SURFACE", "B_SPLINE_SURFACE",
  "TOROIDAL_SURFACE", "CONICAL_SURFACE", "SPHERICAL_SURFACE",
  "SURFACE_OF_REVOLUTION", "SURFACE_OF_LINEAR_EXTRUSION", "OFFSET_SURFACE",
  "CYLINDRICAL_SURFACE", "PLANE",
];
const STEP_TESSELLATED = 1, STEP_BREP = 0;

function isStepText(text) {
  const head = text.slice(0, 2000).toUpperCase();
  return head.includes("ISO-10303-21") || /\bHEADER\s*;/.test(head) && head.includes("FILE_SCHEMA");
}

function parseStep(text) {
  // Entity instances: #id = NAME( args ); possibly spanning lines.
  const body = text.replace(/\/\*[\s\S]*?\*\//g, "");
  const ents = new Map();
  const re = /#(\d+)\s*=\s*([A-Z0-9_]+)\s*\(([\s\S]*?)\)\s*;/g;
  let m;
  while ((m = re.exec(body)) !== null) ents.set(+m[1], {type: m[2], args: m[3]});

  const counts = {};
  for (const e of ents.values()) counts[e.type] = (counts[e.type] || 0) + 1;

  const tess = (counts.TRIANGULATED_FACE_SET || 0)
             + (counts.COMPLEX_TRIANGULATED_FACE_SET || 0)
             + (counts.TRIANGULATED_SURFACE_SET || 0);
  if (tess > 0) {
    const mesh = stepTessellated(ents);
    if (mesh) return mesh;
  }

  const present = STEP_SURFACES.filter(t => counts[t]);
  const faces = counts.ADVANCED_FACE || counts.FACE_SURFACE || 0;
  const names = present.length ? present.join(", ") : "none recognised";
  const err = new Error(
    `This is a STEP file — a boundary-representation format. It describes ${faces || "its"} ` +
    `face${faces === 1 ? "" : "s"} as trimmed surfaces (${names}), not as triangles, and ` +
    `turning those into cross-sections needs a full CAD kernel. ` +
    `Export STL or 3MF from your CAD instead — every CAD does it in one step, and it is ` +
    `what your slicer consumes anyway. Better still, slice it and bring the G-code: that ` +
    `carries the real walls and infill.`);
  err.name = "StepBrepError";
  err.stepCounts = counts;
  throw err;
}

// AP242 tessellated geometry: coordinates list plus integer triangle indices.
function stepTessellated(ents) {
  const num = s => s.split(",").map(v => parseFloat(v)).filter(v => !isNaN(v));
  const coordsOf = id => {
    const e = ents.get(id);
    if (!e) return null;
    const pts = [];
    const re = /\(\s*(-?[\d.eE+-]+)\s*,\s*(-?[\d.eE+-]+)\s*,\s*(-?[\d.eE+-]+)\s*\)/g;
    let m;
    while ((m = re.exec(e.args)) !== null)
      pts.push([parseFloat(m[1]), parseFloat(m[2]), parseFloat(m[3])]);
    return pts.length ? pts : null;
  };
  const tris = [], normals = [];
  for (const [, e] of ents) {
    if (!/TRIANGULATED/.test(e.type)) continue;
    const refs = (e.args.match(/#(\d+)/g) || []).map(r => +r.slice(1));
    let pts = null;
    for (const r of refs) { pts = coordsOf(r); if (pts) break; }
    if (!pts) continue;
    // the last parenthesised integer list is the triangle index list
    const groups = e.args.match(/\(\s*(?:\d+\s*,\s*)*\d+\s*\)/g) || [];
    for (const grp of groups) {
      const idx = num(grp.replace(/[()]/g, ""));
      if (idx.length < 3 || idx.length % 3 !== 0) continue;
      for (let k = 0; k + 2 < idx.length; k += 3) {
        const a = pts[idx[k]-1], b = pts[idx[k+1]-1], c = pts[idx[k+2]-1];
        if (!a || !b || !c) continue;
        const u = [b[0]-a[0], b[1]-a[1], b[2]-a[2]];
        const v = [c[0]-a[0], c[1]-a[1], c[2]-a[2]];
        const n = cross(u, v);
        const L = Math.hypot(n[0], n[1], n[2]) || 1;
        tris.push([a, b, c]);
        normals.push([n[0]/L, n[1]/L, n[2]/L]);
      }
    }
  }
  return tris.length >= 4 ? mkMesh(tris, normals) : null;
}

// --- 3MF --------------------------------------------------------------------
// A sliced `.gcode.3mf` carries the G-code beside the model; a plain `.3mf` is
// the model only, the modern replacement for STL with real units and a build
// transform. The G-code is the better input, so it wins when both are present.

const UNIT_SCALE = {micron:0.001, millimeter:1, centimeter:10, inch:25.4, foot:304.8, meter:1000};

function isZipBytes(u8) {
  return u8.length > 4 && u8[0] === 0x50 && u8[1] === 0x4b &&
         ((u8[2] === 3 && u8[3] === 4) || (u8[2] === 5 && u8[3] === 6) || (u8[2] === 7 && u8[3] === 8));
}
function tmfGcodeEntry(entries) {
  const c = entries.filter(e => /\.(gcode|gco)$/i.test(e.name));
  if (!c.length) return null;
  c.sort((a,b) => (/metadata\//i.test(a.name)?0:1) - (/metadata\//i.test(b.name)?0:1)
                  || a.name.localeCompare(b.name));
  return c[0];
}
function attrsOf(t) {
  const out = {}; let m;
  const re = /([a-zA-Z_][\w:.-]*)\s*=\s*"([^"]*)"/g;
  while ((m = re.exec(t)) !== null) out[m[1].toLowerCase()] = m[2];
  return out;
}
function parse3mfModel(xml) {
  const mm = /<\s*model\b([^>]*)>/i.exec(xml);
  const unit = mm ? (attrsOf(mm[1]).unit || "millimeter").toLowerCase() : "millimeter";
  const scale = UNIT_SCALE[unit] !== undefined ? UNIT_SCALE[unit] : 1;

  const verts = [];
  let m;
  const vre = /<\s*vertex\b([^>]*)\/?>/gi;
  while ((m = vre.exec(xml)) !== null) {
    const a = attrsOf(m[1]);
    verts.push([parseFloat(a.x||0)*scale, parseFloat(a.y||0)*scale, parseFloat(a.z||0)*scale]);
  }
  const idx = [];
  const tre = /<\s*triangle\b([^>]*)\/?>/gi;
  while ((m = tre.exec(xml)) !== null) {
    const a = attrsOf(m[1]);
    const v1 = +a.v1, v2 = +a.v2, v3 = +a.v3;
    if (Number.isInteger(v1) && Number.isInteger(v2) && Number.isInteger(v3)) idx.push([v1,v2,v3]);
  }
  if (verts.length < 3 || !idx.length) throw new Error("3MF model part has no usable mesh");

  let xf = null;
  const im = /<\s*item\b([^>]*)\/?>/i.exec(xml);
  if (im) {
    const t = attrsOf(im[1]).transform;
    if (t) { const n = t.trim().split(/\s+/).map(Number); if (n.length === 12 && n.every(v=>!isNaN(v))) xf = n; }
  }
  let pts = verts;
  if (xf) {
    const [a,b,c,d,e,f,g,h,i,tx,ty,tz] = xf;
    pts = verts.map(([x,y,z]) => [x*a + y*d + z*g + tx*scale,
                                  x*b + y*e + z*h + ty*scale,
                                  x*c + y*f + z*i + tz*scale]);
  }
  const tris = [], normals = [];
  for (const [i1,i2,i3] of idx) {
    const p = pts[i1], q = pts[i2], r = pts[i3];
    if (!p || !q || !r) continue;
    const u = [q[0]-p[0], q[1]-p[1], q[2]-p[2]];
    const v = [r[0]-p[0], r[1]-p[1], r[2]-p[2]];
    const n = cross(u,v);
    const L = Math.hypot(n[0],n[1],n[2]) || 1;
    tris.push([p,q,r]); normals.push([n[0]/L, n[1]/L, n[2]/L]);
  }
  if (!tris.length) throw new Error("3MF model part has no valid triangles");
  return mkMesh(tris, normals);
}
function open3mf(buf) {
  const zip = readZip(buf);
  const g = tmfGcodeEntry(zip.entries);
  if (g) {
    const model = parseGcode(new TextDecoder().decode(zip.read(g)));
    model.container = `3MF · ${g.name}`;
    return model;
  }
  const models = zip.entries.filter(e => /\.model$/i.test(e.name));
  if (!models.length)
    throw new Error("This .3mf contains neither G-code nor a model part. " +
                    "If it came from Bambu Studio, use 'Export plate sliced file'.");
  models.sort((a,b) => (/3dmodel/i.test(a.name)?0:1) - (/3dmodel/i.test(b.name)?0:1));
  return parse3mfModel(new TextDecoder().decode(zip.read(models[0])));
}

// --- faces ------------------------------------------------------------------
// Picking a face beats typing a region: you get the real extent and the real
// centroid instead of a bounding-box band, and the load can follow the face's
// own normal rather than one of the six global directions.
//
// A mesh has faces already — triangles grouped by adjacency and normal. A
// G-code model has none, so its outer wall is grouped the same way: each wall
// segment carries an outward normal, and segments that agree and touch are
// one face.

const FACE_ANGLE = Math.cos(22 * Math.PI/180);

function meshFaces(mesh) {
  const tris = mesh.tris, N = mesh.normals, n = tris.length;
  const key = p => `${Math.round(p[0]*1e4)},${Math.round(p[1]*1e4)},${Math.round(p[2]*1e4)}`;
  const byVert = new Map();
  for (let i = 0; i < n; i++)
    for (const v of tris[i]) {
      const k = key(v);
      if (!byVert.has(k)) byVert.set(k, []);
      byVert.get(k).push(i);
    }
  const unit = v => { const L = Math.hypot(v[0],v[1],v[2]) || 1; return [v[0]/L,v[1]/L,v[2]/L]; };
  const nrm = N.map(unit);
  const seen = new Uint8Array(n), faces = [];
  for (let i = 0; i < n; i++) {
    if (seen[i]) continue;
    const stack = [i], group = [];
    seen[i] = 1;
    const ref = nrm[i];
    while (stack.length) {
      const t = stack.pop();
      group.push(t);
      for (const v of tris[t]) for (const j of (byVert.get(key(v)) || [])) {
        if (seen[j]) continue;
        const d = nrm[j][0]*ref[0] + nrm[j][1]*ref[1] + nrm[j][2]*ref[2];
        if (d < FACE_ANGLE) continue;
        seen[j] = 1; stack.push(j);
      }
    }
    faces.push(group);
  }
  return faces.map((g, idx) => {
    let area = 0, cx = 0, cy = 0, cz = 0, nx = 0, ny = 0, nz = 0;
    const lo = [Infinity,Infinity,Infinity], hi = [-Infinity,-Infinity,-Infinity];
    for (const t of g) {
      const [a,b,c] = tris[t];
      const u = [b[0]-a[0],b[1]-a[1],b[2]-a[2]], v = [c[0]-a[0],c[1]-a[1],c[2]-a[2]];
      const cr = cross(u,v);
      const ar = 0.5*Math.hypot(cr[0],cr[1],cr[2]);
      area += ar;
      cx += ar*(a[0]+b[0]+c[0])/3; cy += ar*(a[1]+b[1]+c[1])/3; cz += ar*(a[2]+b[2]+c[2])/3;
      nx += nrm[t][0]*ar; ny += nrm[t][1]*ar; nz += nrm[t][2]*ar;
      for (const p of [a,b,c]) for (let k=0;k<3;k++) { if(p[k]<lo[k])lo[k]=p[k]; if(p[k]>hi[k])hi[k]=p[k]; }
    }
    if (area < 1e-9) area = 1e-9;
    const nl = Math.hypot(nx,ny,nz) || 1;
    return {id:idx, tris:g, area, centroid:[cx/area, cy/area, cz/area],
            normal:[nx/nl, ny/nl, nz/nl], lo, hi, kind:"mesh"};
  }).filter(f => f.area > 1e-6).sort((a,b) => b.area - a.area).slice(0, 60);
}

function gcodeFaces(model, maxSegs) {
  maxSegs = maxSegs || 30000;
  // Outward normal of a wall bead: perpendicular to it, pointing away from
  // the layer's centre. Good enough to group a flat side together and to
  // keep it apart from the one around the corner.
  const items = [];
  const stride = Math.max(1, Math.ceil(countWalls(model)/maxSegs));
  let seen = 0;
  for (const lay of model.layers) {
    let cx = 0, cy = 0, cn = 0;
    for (let i = lay.segStart; i < lay.segEnd; i++) {
      if (model.feat[i] !== F.OUTER && model.feat[i] !== F.OVERHANG) continue;
      cx += model.x1[i]; cy += model.y1[i]; cn++;
    }
    if (!cn) continue;
    cx /= cn; cy /= cn;
    for (let i = lay.segStart; i < lay.segEnd; i++) {
      if (model.feat[i] !== F.OUTER && model.feat[i] !== F.OVERHANG) continue;
      if ((seen++ % stride) !== 0) continue;
      const x1 = model.x1[i], y1 = model.y1[i], x2 = model.x2[i], y2 = model.y2[i];
      const dx = x2-x1, dy = y2-y1, L = Math.hypot(dx,dy);
      if (L < 1e-6) continue;
      let nx = dy/L, ny = -dx/L;
      const mx = (x1+x2)/2, my = (y1+y2)/2;
      if (nx*(mx-cx) + ny*(my-cy) < 0) { nx = -nx; ny = -ny; }
      items.push({x:mx, y:my, z:lay.z, nx, ny, L});
    }
  }
  if (!items.length) return [];
  // bucket by normal direction, then split each bucket into connected blobs
  const BUCKETS = 16;
  const groups = new Map();
  for (const it of items) {
    const a = Math.atan2(it.ny, it.nx);
    const b = Math.round((a + Math.PI)/(2*Math.PI)*BUCKETS) % BUCKETS;
    if (!groups.has(b)) groups.set(b, []);
    groups.get(b).push(it);
  }
  const faces = [];
  for (const [b, list] of groups) {
    if (list.length < 8) continue;
    let area = 0, cx = 0, cy = 0, cz = 0, nx = 0, ny = 0;
    const lo = [Infinity,Infinity,Infinity], hi = [-Infinity,-Infinity,-Infinity];
    for (const it of list) {
      const w = it.L;
      area += w; cx += it.x*w; cy += it.y*w; cz += it.z*w; nx += it.nx*w; ny += it.ny*w;
      const p = [it.x, it.y, it.z];
      for (let k=0;k<3;k++) { if(p[k]<lo[k])lo[k]=p[k]; if(p[k]>hi[k])hi[k]=p[k]; }
    }
    const nl = Math.hypot(nx,ny) || 1;
    faces.push({id:faces.length, pts:list, area, centroid:[cx/area, cy/area, cz/area],
                normal:[nx/nl, ny/nl, 0], lo, hi, kind:"wall"});
  }
  // plus the two horizontal ends, which the wall grouping cannot see
  const zs = model.layers.map(l => l.z);
  if (zs.length) {
    const z0 = Math.min(...zs), z1 = Math.max(...zs);
    for (const [z, nz, nm] of [[z0,-1,"bottom"],[z1,1,"top"]]) {
      const band = model.layers.filter(l => Math.abs(l.z - z) < (z1-z0)*0.03 + 1e-6);
      if (!band.length) continue;
      let cx=0, cy=0, cn=0;
      const lo=[Infinity,Infinity,z], hi=[-Infinity,-Infinity,z];
      for (const lay of band)
        for (let i = lay.segStart; i < lay.segEnd; i++) {
          if (NON_PART.has(model.feat[i])) continue;
          cx += model.x1[i]; cy += model.y1[i]; cn++;
          lo[0]=Math.min(lo[0],model.x1[i]); hi[0]=Math.max(hi[0],model.x1[i]);
          lo[1]=Math.min(lo[1],model.y1[i]); hi[1]=Math.max(hi[1],model.y1[i]);
        }
      if (!cn) continue;
      faces.push({id:faces.length, pts:[], area:(hi[0]-lo[0])*(hi[1]-lo[1]),
                  centroid:[cx/cn, cy/cn, z], normal:[0,0,nz], lo, hi,
                  kind:"cap", capName:nm});
    }
  }
  return faces.sort((a,b) => b.area - a.area).slice(0, 40);
}
function countWalls(model) {
  let n = 0;
  for (let i = 0; i < model.x1.length; i++)
    if (model.feat[i] === F.OUTER || model.feat[i] === F.OVERHANG) n++;
  return n;
}

function facesFor(model) {
  if (model._faces) return model._faces;
  const f = model.kind === "stl" ? meshFaces(model) : gcodeFaces(model);
  model._faces = f;
  return f;
}

// A face becomes a region for the analysis: the axis its normal is closest
// to, and the face's own extent along that axis. Tighter than a named band,
// and the centroid is the real one.
function faceRegion(face, bbox) {
  const n = face.normal.map(Math.abs);
  const ai = n[0] >= n[1] && n[0] >= n[2] ? 0 : (n[1] >= n[2] ? 1 : 2);
  const axis = ["x","y","z"][ai];
  const pad = Math.max((bbox[ai+3]-bbox[ai])*0.02, 0.3);
  return {axis, lo: face.lo[ai] - pad, hi: face.hi[ai] + pad,
          label: `face (${axis}${face.normal[ai] >= 0 ? "+" : "−"})`};
}

// How much of a solid cross-section a printed part actually fills. A mesh
// says nothing about walls or infill, so the part is synthesised: `walls`
// perimeters of `extWidth` around the boundary, and `infill` of what is left.
// Closer to a real print than scaling the whole section by one number, and it
// is the same quantity the G-code path measures directly.
function printedFraction(area, perimeter, walls, extWidth, infill) {
  if (area <= 0) return {material:0, wall:0, infill:0};
  const shell = Math.max(0, walls*extWidth);
  const wall = Math.min(area, perimeter*shell);
  const inner = Math.max(0, area - wall);
  const inf = inner * Math.max(0, Math.min(1, infill));
  return {material: wall + inf, wall, infill: inf};
}

// --- top level --------------------------------------------------------------
const UI_MAX_OUTLINE = 4000;
function downsample(items, limit) {
  if (items.length <= limit) return items;
  const step = items.length/limit, out = [];
  for (let k = 0; k < limit; k++) out.push(items[Math.min(items.length-1, Math.floor(k*step))]);
  return out;
}

function sectionOutline(model, axis, pos) {
  const items = [];
  if (model.kind === "stl") {
    const ai = AXIS_INDEX[axis], UV = {x:[1,2], y:[2,0], z:[0,1]}[axis];
    for (let ti = 0; ti < model.tris.length && items.length < UI_MAX_OUTLINE; ti++) {
      const t = model.tris[ti], vals = [t[0][ai], t[1][ai], t[2][ai]];
      if (Math.min(...vals) > pos || Math.max(...vals) < pos) continue;
      const seg = triPlaneSegment(t, ai, pos, UV[0], UV[1], model.normals[ti]);
      if (!seg) continue;
      let [[u1,v1],[u2,v2]] = seg;
      if (axis === "y") { [u1,v1,u2,v2] = [v1,u1,v2,u2]; }
      items.push({kind:"line", a:[u1,v1], b:[u2,v2], f:"outline"});
    }
    return items;
  }
  if (axis === "z") {
    let lay = model.layers.find(l => Math.abs(l.z - pos) < 1e-6);
    if (!lay) lay = model.layers.reduce((b,l) => Math.abs(l.z-pos) < Math.abs(b.z-pos) ? l : b, model.layers[0]);
    if (!lay) return items;
    const idx = [];
    for (let i = lay.segStart; i < lay.segEnd; i++) idx.push(i);
    for (const i of downsample(idx, UI_MAX_OUTLINE)) {
      if (NON_PART.has(model.feat[i])) continue;
      items.push({kind:"bead", a:[model.x1[i], model.y1[i]], b:[model.x2[i], model.y2[i]],
                  w:model.w[i], f:FEATURE_NAMES[model.feat[i]] || "other"});
    }
    return items;
  }
  for (let i = 0; i < model.x1.length && items.length < UI_MAX_OUTLINE; i++) {
    const f = model.feat[i];
    if (NON_PART.has(f)) continue;
    const w = model.w[i], h = model.h[i];
    if (w <= 0 || h <= 0) continue;
    const x1=model.x1[i], y1=model.y1[i], x2=model.x2[i], y2=model.y2[i];
    const dx=x2-x1, dy=y2-y1, L=Math.hypot(dx,dy);
    if (L < 1e-9) continue;
    const ux=dx/L, uy=dy/L;
    let un, ut, a0, b0, p1, p2, dn, n1;
    if (axis === "x") { un=Math.abs(ux); ut=Math.abs(uy); a0=Math.min(x1,x2); b0=Math.max(x1,x2); p1=y1; p2=y2; dn=dx; n1=x1; }
    else              { un=Math.abs(uy); ut=Math.abs(ux); a0=Math.min(y1,y2); b0=Math.max(y1,y2); p1=x1; p2=x2; dn=dy; n1=y1; }
    const half = w*ut*0.5;
    if (pos < a0 - half || pos > b0 + half) continue;
    let chord = un > 1e-6 ? w/un : Infinity;
    chord = Math.min(chord, L*ut + w*un);
    let q;
    if (Math.abs(dn) > 1e-9) { let t = (pos-n1)/dn; t = t<0?0:(t>1?1:t); q = p1 + (p2-p1)*t; }
    else q = (p1+p2)*0.5;
    const zc = segmentZ(model, i);
    items.push({kind:"rect", a:[q-chord/2, zc], b:[q+chord/2, zc+h],
                f:FEATURE_NAMES[f] || "other"});
  }
  return items;
}

function materialCentroid(model, region, bbox) {
  let sx=0, sy=0, sz=0, sw=0;
  if (model.kind === "stl") {
    for (const [a,b,c] of model.tris) {
      const cx=(a[0]+b[0]+c[0])/3, cy=(a[1]+b[1]+c[1])/3, cz=(a[2]+b[2]+c[2])/3;
      if (!regionContains(region, [cx,cy,cz])) continue;
      const u=[b[0]-a[0],b[1]-a[1],b[2]-a[2]], v=[c[0]-a[0],c[1]-a[1],c[2]-a[2]];
      const n = cross(u,v), ar = 0.5*Math.hypot(n[0],n[1],n[2]);
      sx+=cx*ar; sy+=cy*ar; sz+=cz*ar; sw+=ar;
    }
  } else {
    for (let i = 0; i < model.x1.length; i++) {
      if (NON_PART.has(model.feat[i])) continue;
      const w = model.w[i];
      if (w <= 0) continue;
      const mx=(model.x1[i]+model.x2[i])*0.5, my=(model.y1[i]+model.y2[i])*0.5, mz=segmentZ(model,i);
      if (!regionContains(region, [mx,my,mz])) continue;
      const a = Math.hypot(model.x2[i]-model.x1[i], model.y2[i]-model.y1[i])*w;
      sx+=mx*a; sy+=my*a; sz+=mz*a; sw+=a;
    }
  }
  if (sw <= 1e-9) return [regionBoxCentroid(region, bbox), false];
  return [[sx/sw, sy/sw, sz/sw], true];
}

function regionCentroid(simAxes, region, bbox, model) {
  const secs = simAxes[region.axis] || [];
  const pts = [];
  for (const q of secs) {
    if (q.pos < region.lo - 1e-9 || q.pos > region.hi + 1e-9 || q.area <= 1e-9) continue;
    const c = sectionCentroid3d(q);
    if (c) pts.push([c, q.area]);
  }
  if (pts.length) {
    const tot = pts.reduce((s,[,a]) => s+a, 0);
    return [[0,1,2].map(k => pts.reduce((s,[c,a]) => s + c[k]*a, 0)/tot), true];
  }
  if (model) return materialCentroid(model, region, bbox);
  return [regionBoxCentroid(region, bbox), false];
}

// Parsing dominates the cost on a real G-code file, so the UI does it once
// per file and re-runs the analysis on every control change.
function parse(input) {
  if (typeof input !== "string") {
    const probe = new Uint8Array(ArrayBuffer.isView(input) ? input.buffer : input,
                                 ArrayBuffer.isView(input) ? input.byteOffset : 0,
                                 Math.min(8, ArrayBuffer.isView(input) ? input.byteLength : input.byteLength));
    if (isZipBytes(probe)) return open3mf(input);
    // A dropped file arrives as bytes; STEP is text, so sniff it.
    const head = new TextDecoder().decode(
      new Uint8Array(ArrayBuffer.isView(input) ? input.buffer : input,
                     ArrayBuffer.isView(input) ? input.byteOffset : 0,
                     Math.min(2048, ArrayBuffer.isView(input) ? input.byteLength : input.byteLength)));
    if (isStepText(head)) {
      const full = new TextDecoder().decode(
        ArrayBuffer.isView(input) ? input : new Uint8Array(input));
      return parseStep(full);
    }
    return loadSTL(input);
  }
  if (isStepText(input)) return parseStep(input);
  return parseGcode(input);
}

function analyze(input, opts) {
  let model;
  try { model = parse(input); }
  catch (err) { return {ok:false, error:`${err.name}: ${err.message}`}; }
  return analyzeModel(model, opts);
}

function analyzeModel(model, opts) {
  opts = opts || {};
  try {
    const quality = opts.quality || "typical";
    const knockdown = QUALITY[quality] !== undefined ? QUALITY[quality] : 0.85;
    const isG = model.kind === "gcode";
    if (isG && model.x1.length === 0)
      return {ok:false, error:"no extrusion moves found — is this really G-code?"};

    let matName = opts.material;
    if (!matName && isG) matName = model.config.filament_type || model.config.filament_settings_id;
    let mat = lookupMaterial(matName);
    if (!mat) {
      if (!isG) return {ok:false, error:"STL files carry no material information — pick a material."};
      return {ok:false, error:`could not determine the material${matName ? ` from "${matName}"` : ""} — pick one.`};
    }
    const props = Object.assign({}, mat.props);
    if (opts.uts) props.uts = opts.uts;
    if (opts.layerAdhesion) props.z = opts.layerAdhesion/100;
    const uts = props.uts, zr = props.z, mod = props.mod;
    const [thermal, thermalNote] = thermalFactor(
      opts.temp === "" || opts.temp === undefined ? null : opts.temp, props.hdt);
    const scale = knockdown * thermal;
    const solid = opts.solidFraction;

    // sections per axis
    const axes = {};
    let bbox3, profAxes = {}, headAxes = {};
    const feats = opts.noKt ? [] : concentrationsFor(model);
    const qn = notchSensitivity(props.elong);
    if (isG) {
      const [loX, loY, hiX, hiY] = bbox2(model);
      let top = 0; for (const l of model.layers) top = Math.max(top, l.z);
      bbox3 = [loX, loY, 0, hiX, hiY, top];
      profAxes.z = analyseLayers(model, uts, zr, mod, opts.arm, feats, qn);
      headAxes.z = profAxes.z;
      for (const ax of ["x","y"]) {
        const ai = AXIS_INDEX[ax], lo = bbox3[ai], hi = bbox3[ai+3];
        if (hi - lo <= 1e-6) continue;
        // Headline: the fine sweep, with section moments on the most
        // promising candidates only (each costs a full pass).
        const swept = scanVertical(model, ax, uts, zr, opts.binSize, undefined, feats, qn);
        if (swept.length) {
          // Rank by area/arm, a proxy for bending demand: a part can be
          // equally thin in many places and the lever arm decides which of
          // them breaks first.
          // kf belongs in the ranking: a section weakened by a hole has to be
          // able to win it, or it never gets its moments computed.
          const demand = q => q.area / (q.kf * Math.max(Math.max(q.pos - lo, hi - q.pos), 1e-6));
          const cands = swept.slice().sort((a,b) => demand(a) - demand(b)).slice(0, BEND_CANDIDATES);
          for (const q of swept.slice().sort((a,b) => a.force - b.force).slice(0, BEND_CANDIDATES))
            if (!cands.includes(q)) cands.push(q);
          for (const q of cands) {
            const mm = verticalStations(model, ax, [q.pos], uts, zr, feats, qn);
            if (!mm.length) continue;
            q.I1 = mm[0].I1; q.c1 = mm[0].c1; q.I2 = mm[0].I2; q.c2 = mm[0].c2;
            q.centroid = mm[0].centroid;
            q.enclosed = mm[0].enclosed; q.perimeter = mm[0].perimeter;
            q.tWall = mm[0].tWall; q.openSum = mm[0].openSum;
            q.torsQ = mm[0].torsQ; q.torsJ = mm[0].torsJ; q.torsModel = mm[0].torsModel;
            const arm = opts.arm != null ? opts.arm : Math.max(q.pos - lo, hi - q.pos);
            applyBending(q, mod, arm, opts.arm != null ? "" : "to the far end");
          }
        }
        headAxes[ax] = swept;
        // Profiles and the load case run on an even station grid.
        const pos = [];
        for (let k = 0; k < LOAD_STATIONS; k++) pos.push(lo + (hi-lo)*k/(LOAD_STATIONS-1));
        const st = verticalStations(model, ax, pos, uts, zr, feats, qn);
        for (const q of st) {
          const arm = opts.arm != null ? opts.arm : Math.max(q.pos - lo, hi - q.pos);
          applyBending(q, mod, arm, opts.arm != null ? "" : "to the far end");
        }
        profAxes[ax] = st;
      }
    } else {
      bbox3 = model.bbox;
      const lh = opts.layerHeight || 0.2;
      const extW = opts.extWidth || (opts.nozzle || 0.4)*1.05;
      const walls = opts.walls !== undefined && opts.walls !== null ? opts.walls : 3;
      const infillF = opts.infill !== undefined && opts.infill !== null ? opts.infill : 0.20;
      for (const ax of ["z","x","y"]) {
        let nplanes = opts.planes || 400;
        if (ax === "z" && lh > 0)
          nplanes = Math.max(4, Math.min(1200, Math.round((bbox3[5]-bbox3[2])/lh)));
        const secs = sliceMesh(model, ax, nplanes);
        const ai = AXIS_INDEX[ax], lo = bbox3[ai], hi = bbox3[ai+3];
        for (const s of secs) {
          const sig = ax === "z" ? uts*zr : (mat.family === "resin" ? uts : uts*Math.min(1, zr+0.15));
          let frac;
          if (solid !== undefined && solid !== null) frac = solid;
          else {
            const pf = printedFraction(s.area, s.perimeter, walls, extW, infillF);
            frac = s.area > 1e-9 ? pf.material/s.area : 1;
            s.byFeature = {1: pf.wall, 6: pf.infill};
          }
          s.area *= frac; s.I1 *= frac; s.I2 *= frac;
          s.sigma = sig * (opts.green && mat.family === "resin" ? 0.55 : 1);
          if (feats.length) applyConcentration(s, feats, qn);
          s.force = s.area * s.sigma;
          const arm = opts.arm != null ? opts.arm
            : (ax === "z" ? hi - s.pos : Math.max(s.pos - lo, hi - s.pos));
          applyBending(s, mod, arm, opts.arm != null ? "" :
            (ax === "z" ? "to the top of the part" : "to the far end"));
          const t = torsionProperties(s.area, s.area, 4*Math.sqrt(s.area), 0, s.I1+s.I2, 0);
          s.torsQ = t[0]; s.torsJ = t[1]; s.torsModel = t[2];
        }
        profAxes[ax] = secs;
      }
    }
    for (const ax of Object.keys(profAxes)) axes[ax] = profAxes[ax];
    if (!isG) headAxes = profAxes;

    // per-axis headline
    const axisOut = {};
    for (const ax of Object.keys(headAxes)) {
      const secs = headAxes[ax];
      if (!secs.length) continue;
      let tens = secs[0], thin = secs[0], bend = null;
      for (const s of secs) {
        if (s.force > 0 && s.force < tens.force) tens = s;
        if (s.area < thin.area) thin = s;
        if (s.Fbreak && (bend === null || s.Fbreak < bend.Fbreak)) bend = s;
      }
      if (!bend) bend = tens;
      const d = {axis:ax, tensionN: tens.force*scale, sigma: tens.sigma*scale,
                 area: tens.area, critical: tens, bending: bend, thinnest: thin};
      if (bend.Fbreak) {
        d.breakN = bend.Fbreak*scale;
        d.momentNm = bend.Mbreak*scale;
        d.armMm = bend.arm; d.armNote = bend.armNote; d.EI = bend.EI;
        d.deflectionMm = bend.deflAtBreak != null ? bend.deflAtBreak*scale : null;
        if (opts.deflect) {
          const fd = loadForDeflection(bend, mod, opts.deflect);
          d.deflectLimitN = fd;
          d.stiffnessGoverns = fd != null && fd < d.breakN;
        }
      }
      axisOut[ax] = d;
    }
    const withF = Object.keys(axisOut).filter(a => axisOut[a].breakN);
    const pool = withF.length ? withF : Object.keys(axisOut);
    const key = withF.length ? "breakN" : "tensionN";
    // Canonical order, and only move on a meaningfully smaller value, so a
    // symmetric part does not have its governing axis decided by float noise.
    const order = ["z","x","y"].filter(a => pool.includes(a));
    let weakest = order[0];
    for (const a of order.slice(1))
      if (axisOut[a][key] < axisOut[weakest][key] * (1 - 1e-9)) weakest = a;

    // load case
    let sim = null;
    if (opts.fix && opts.load) {
      try {
        const fix = parseRegion(opts.fix, bbox3);
        const {newtons, dir, region} = parseLoad(opts.load);
        const lr = parseRegion(region, bbox3);
        const [loadPt] = regionCentroid(axes, lr, bbox3, model);
        const [fixC] = regionCentroid(axes, fix, bbox3, model);
        const [worst, allr] = solveLoadCase(axes, bbox3, fix, fixC, newtons, dir, loadPt, scale);
        if (!worst) {
          sim = {error:`no section carries this load. Nothing is cut off from ${fix.label} — with the part held over that whole region the load reaches ground without stressing any section. Hold a smaller area (e.g. x<5) or load somewhere else.`,
                 fix, loadN:newtons, loadDir:dir, loadRegion:lr};
        } else {
          const [defl, dom, bent, twist] = loadCaseDeflection(axes, bbox3, fix, fixC, newtons, dir, loadPt, mod);
          const curve = deflectionCurve(axes, bbox3, fix, fixC, newtons, dir, loadPt, mod);
          let peakT = null;
          for (const r of allr) if (r.tauTorsion > 1e-3 && (!peakT || r.tauTorsion > peakT.tauTorsion)) peakT = r;
          sim = {fix, loadN:newtons, loadDir:dir, loadRegion:lr, loadPt, fixPt:fixC,
                 worst, count:allr.length, all:allr, peakTorsion:peakT,
                 deflection:defl, deflAxis:dom, deflBent:bent, twistDeg:twist,
                 curve, error:null};
        }
      } catch (err) { sim = {error: err.message}; }
    }

    // profiles + outlines
    const lookup = new Map();
    if (sim && !sim.error) for (const r of sim.all) lookup.set(r.sec, r);
    const profiles = {};
    for (const ax of Object.keys(axes)) {
      profiles[ax] = axes[ax].map(sec => {
        const row = {pos:sec.pos, area:sec.area, sigma:sec.sigma*scale};
        if (sec.Fbreak) { row.breakN = sec.Fbreak*scale; row.arm = sec.arm; }
        const r = lookup.get(sec);
        if (r) { row.sf = r.sf; row.stress = r.sigmaEq; row.cap = r.cap; }
        return row;
      });
    }
    const sections = {};
    for (const ax of Object.keys(axisOut)) {
      const sec = axisOut[ax].bending || axisOut[ax].critical;
      sections[ax] = {pos:sec.pos, axis:ax, area:sec.area, outline:sectionOutline(model, ax, sec.pos)};
    }
    if (sim && !sim.error) {
      const w = sim.worst;
      sections.load_case = {pos:w.sec.pos, axis:w.axis, area:w.sec.area,
                            outline:sectionOutline(model, w.axis, w.sec.pos)};
    }

    let volume = 0, layerCount = 0, layerHeightMm = null, extWidthMm = null;
    if (isG) {
      volume = model.totalVolume; layerCount = model.layers.length;
      const cfgLh = parseFloat(model.config.layer_height);
      if (!isNaN(cfgLh) && cfgLh > 0) layerHeightMm = cfgLh;
      else if (model.layers.length) {
        const hs = model.layers.map(l => l.h).sort((a,b) => a-b);
        layerHeightMm = hs[hs.length >> 1];
      }
      const ws = [];
      const stride = Math.max(1, Math.floor(model.x1.length/400));
      for (let i = 0; i < model.x1.length; i += stride) if (model.w[i] > 0) ws.push(model.w[i]);
      ws.sort((a,b) => a-b);
      if (ws.length) extWidthMm = ws[ws.length >> 1];
    }
    else volume = model.volume() * (solid === undefined || solid === null ? 1 : solid);

    return {ok:true, error:null, kind:model.kind, slicer:model.slicer,
      material:{name:mat.name, family:mat.family, ...props},
      derating:{quality, knockdown, thermal, thermalNote},
      bbox:bbox3, volume, layerCount, layerHeightMm, extWidthMm, config:model.config,
      extrusions: isG ? model.x1.length : model.tris.length,
      axes:axisOut, weakestAxis:weakest, governedBy: withF.length ? "bending" : "tension",
      sim, profiles, sections, warnings:model.warnings,
      concentrations: feats, notchSensitivity: qn,
      container: model.container || null,
      print: isG ? null : {layerHeight: opts.layerHeight || 0.2,
                           extWidth: opts.extWidth || (opts.nozzle || 0.4)*1.05,
                           nozzle: opts.nozzle || 0.4,
                           walls: opts.walls !== undefined && opts.walls !== null ? opts.walls : 3,
                           infill: opts.infill !== undefined && opts.infill !== null ? opts.infill : 0.20,
                           solidOverride: solid === undefined || solid === null ? null : solid}};
  } catch (err) {
    return {ok:false, error:`${err.name}: ${err.message}`};
  }
}

return {analyze, analyzeModel, parse, parseStep, isStepText, inflateRaw, readZip,
        printedFraction,
        open3mf, parse3mfModel,
        contourStack, shellLoops, concentrationsFor,
        facesFor, faceRegion, lookupMaterial, FDM, RESIN, FEATURE_NAMES, QUALITY, parseRegion, parseLoad};
})();
if (typeof module !== "undefined") module.exports = PS;
