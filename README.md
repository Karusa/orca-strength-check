# orca-strength-check

Estimates the mechanical strength of FDM and resin 3D prints: hold it here, push
it there, does it break? It comes in three forms:

- **Strength check tool for OrcaSlicer** ([`orcaslicer/`](orcaslicer/)): a paint
  tool in the slicer toolbar. Paint the fixture and the load, aim the load arrow,
  enter kg, lb or N, and get a safety factor for the part as it will be printed.
- **`printstrength.py`**: a command-line tool and OrcaSlicer post-processing
  script that reads sliced G-code, `.gcode.3mf`, 3MF or STL. Single file, pure
  standard library, Python 3.8+.
- **Web UI** ([`web/`](web/)): the same engine in the browser, with a
  SimulationXpress-style wizard and a 3D viewer.

## Strength check in OrcaSlicer

The in-slicer tool is a patch against OrcaSlicer
([`orcaslicer/strength-check.patch`](orcaslicer/strength-check.patch)), offered
upstream. It adds:

- `src/libslic3r/Feature/StrengthAnalysis`: the analysis, with no GUI
  dependencies, and Catch2 tests in `tests/libslic3r/test_strength_analysis.cpp`
- `GLGizmoStrength`: the paint tool. Left mouse paints the fixture, right mouse
  paints the load, Shift erases. The Face tool selects whole faces. Drag the
  arrow to aim the load; it snaps to the axes, and ±X/±Y/±Z buttons set it
  exactly.
- `docs/HLSD/strength-analysis.md`: the design

It uses the object's real orientation, wall loops, infill density, line width,
layer height and filament type. The load case is a what-if check, so it is never
written to the model, the G-code or the project file.

To apply it to an OrcaSlicer checkout and build:

```bash
git apply /path/to/orca-strength-check/orcaslicer/strength-check.patch
```

The patch was made against OrcaSlicer commit
[`d1d14329d`](https://github.com/OrcaSlicer/OrcaSlicer/commit/d1d14329d95fa9f8bd13c24efecb16d9c7eb1e1a).
Code merged into OrcaSlicer is distributed under OrcaSlicer's AGPL-3.0 license;
Apache-2.0 code may be included in AGPL-3.0 projects.

## Command-line tool

`printstrength.py` estimates strength from the sliced G-code or the STL.

It answers the question you actually have: *if I pull or bend this part, where
does it break and at what load?*

```
$ printstrength bracket.gcode --deflect 2

HOW MUCH LOAD IT TAKES
  Bending, because that is how printed parts almost always fail.

  Z - bent over a layer line (the classic snap)
      breaks at         33 N  (3.3 kgf)  applied 36 mm out (to the top of the part)
                        = 1.17 N.m at layer 20, z=4.2 mm
      bends first       1.4 mm at that load   (cantilever, tip load)
      stiffness         EI = 0.362 N.m^2
      hits 2.0 mm at    47 N  (4.8 kgf)
      section           61.6 mm^2 at layer 20, z=4.2 mm
                        inner wall 60%, outer wall 34%, sparse infill 7%
      also: 1.40 kN  (142 kgf) in pure tension through 62 mm^2 at layer 82, z=16.6 mm

  X - bent about a plane normal to X   << governs
      breaks at         17 N  (1.7 kgf)  applied 36 mm out (to the far end)
      bends first       2.4 mm at that load
      stiffness         EI = 0.109 N.m^2
      hits 2.0 mm at    14 N  (1.4 kgf)   <- stiffness governs, not strength
```

### Why it leads with bending

Look at those two numbers for the same section. Pure tension: **1.40 kN**.
Bending, load at the end of the part: **33 N** — about 3 kg, breakable by hand.

Tensile capacity is the number everyone expects and the one that almost never
governs, because almost nothing you print is loaded in pure tension through its
weakest section. Brackets, hooks, clips, levers, mounts, enclosure tabs: bending.
So the report leads with bending, and expresses it as a **force at a lever arm**
rather than as a moment — "33 N applied 36 mm out" is checkable against the
thing you are actually hanging off the part in a way that "1.17 N·m" is not.
Tension is still there, one line down, in grey.

The bending-critical section is usually **not** the thinnest one: capacity falls
with section modulus but the moment rises with lever arm, so on a cantilever the
part breaks low down. Above, tension is worst at z=16.6 mm and bending at
z=4.2 mm. The tool scans for each separately.

## Load cases: fixtures, loads, materials

The scan above asks "how much could this take, anywhere?". Usually you know
something more specific: this bit is bolted down, that bit gets pushed. Give it
those and you get a single safety factor, the way SimulationXpress does.

```bash
printstrength bracket.gcode --fix bottom --load "5kg -Z at top"
printstrength bracket.gcode --fix "x<5"  --load "40N -Z at x>35"
printstrength shelf.stl -m tough --fix left --load "2kg down at right"
```

```
LOAD CASE
  held at             bottom
  pushed with         39 N  (4.0 kgf)  +1.00X  at top
  sections on the load path  154

  worst section       layer 30, z=6.2 mm  (Z cut)
      area            61.6 mm^2
                      inner wall 60%, outer wall 34%, sparse infill 7%
      stress there    23.4 MPa   against 22.7 MPa allowable
                      from 1.21 N.m bending
      SAFETY FACTOR   0.97  FAIL
      deflection      1.1 mm at the load point

  FAILS under this load case.
```

**Fixtures** — `bottom`, `top`, `left`, `right`, `front`, `back`, or an
inequality: `z<5`, `x>=30`. A named region is a 15% band of the bounding box,
not a single face, because a printed part is held over an area.

**Loads** — `<force><unit> <direction> at <region>`. Units `N`, `kN`, `kg`,
`kgf`, `lb`. Directions `+X`/`-X`/`+Y`/`-Y`/`+Z`/`-Z`, `up`, `down`, or a
vector `0,0,-1`. The region accepts the same names as a fixture.

Exit code is `2` when the safety factor drops below 1, so this drops into CI.

### How the load case is solved

For a statically determinate single load, the internal moment at any cut is
just the moment of the applied load about that cut's centroid. So every section
plane is checked directly — no beam axis to nominate, and L-shaped load paths
work the same as straight ones. At each cut it combines bending about both
in-plane axes with the axial term, compares against that section's own
allowable stress, and reports the worst.

Two details that matter more than they sound:

**A fixture is a region, not a point.** If the held area straddles a cut, load
reaches ground without crossing it, so that cut carries nothing. Hold an upright
bracket over its whole base plate and no cut along that plate is loaded — which
is correct, and which a point-fixture model gets wrong. A consequence: a slab
fixture spans the full part on the two axes it is not normal to, so only cuts
normal to the fixture's own axis can ever be on the load path.

**Region centroids follow the material, not the bounding box.** On an L-bracket
the box centre at the top of the upright leg is off the part entirely; using it
would invent a bending moment that is not there. Centroids come from the
cross-section areas, which stay accurate even on a coarse STL whose faces span
the whole part.

Validated against a closed-form cantilever (40×10×5 bar, fixed at one end,
20 N at the other): moment, stress, safety factor and deflection all agree to
within 1%.

**Shear is checked, not just bending.** On a short, stubby load path there is
barely any lever arm, so the moment stays small and the section is really
loaded in shear. Each cut combines direct stress and transverse shear through
von Mises, and the report says when shear is what dominates. Pushing sideways
on the top of a flat plate is the classic case: checking bending alone reports
a 384× margin, and the honest answer is 79×.

**Torsion is checked too**, and this is where the usual shortcut is dangerous.
The polar moment `I₁+I₂` is right only for a circular section; on a closed
thin-walled one it overstates torsional stiffness by about half again, and on
an open one by orders of magnitude. Overstating stiffness is the direction that
breaks parts, so the tool picks a model per section instead:

| section | model | source of the geometry |
|---|---|---|
| essentially solid | compact Saint-Venant | material area and polar moment |
| walls + infill | closed thin-wall (Bredt) | enclosed area from the outer-wall path |
| no closed wall | open section, `J = ⅓Σbt³` | wall material only |

The enclosed area comes straight off the slicer's outer-wall loops. Perimeter
loops are emitted closed, with holes wound opposite to outlines, so a signed
area sum recovers the enclosed area exactly and subtracts holes by itself — no
path stitching needed. Vertical cuts use the layer-by-layer silhouette instead,
which is exact on the test fixtures.

Infill does form closed cells that help in torsion; they are ignored, which
errs safe. Transverse and torsional shear peak at different points on a
section, so adding them is conservative rather than exact. Twist is reported in
degrees over the load path.


**Stress concentrations are checked.** Beam theory gives the nominal stress in a
section; what actually starts the crack is usually a hole or a sharp inside
corner raising it locally. The detector reads the slicer's own wall loops —
loops wound the other way are holes, concave vertices on the outer contour are
notches — and applies the textbook factors:

| feature | factor | source |
|---|---|---|
| circular hole | `Kt = 3 − 3.13x + 3.66x² − 1.53x³`, `x = d/W` | Peterson, net section |
| notch | `Kt = 1 + 2√(t/r)` | Inglis / Neuber |

Measured against known geometry: a 6 mm hole in a 20 mm strip comes back as
Kt **2.3497** against the analytic **2.3491** (0.03%), and a semicircular edge
notch as **3.06** against the textbook 3.0.

What the material feels is `Kf = 1 + q(Kt − 1)`, where q is notch sensitivity.
A brittle polymer feels nearly all of Kt (PLA, 4% elongation: q = 1.0); a
ductile one yields locally and redistributes (PA6: q = 0.67, TPU floors at
0.40). The factor divides the section's allowable, so it flows through tension,
bending and the load case without any of them special-casing it — and the
candidate ranking includes Kf, so a section weakened by a hole can win its way
into being evaluated.

Mesh input gets the same treatment, with section loops stitched from the
triangle intersections.

**Faces can be picked instead of typed.** Click a face in the 3D view to set
the fixture or the load. A mesh has faces already — triangles grouped by
adjacency and normal; G-code has none, so its outer wall is grouped the same
way from each bead's outward normal, plus the top and bottom caps. A picked
face gives its real extent (`y<0.63`, not a 15% bounding-box band), its real
centroid, and — for a load — its own normal as the push direction.

One honest limit: cut planes stay axis-aligned, so a picked face is mapped to
the axis its normal is closest to. The centroid and extent are exact; the
cutting direction is still one of the three axes.


**Print settings for mesh input.** G-code carries its own layer height, wall
count and extrusion width, and those are used as measured. A mesh carries none
of it, so rather than scaling the whole part by one "solid fraction" the print
is synthesised: for a section of area *A* and boundary length *P*, the material
is `P × (walls × width) + (A − P × walls × width) × infill`. That is the same
quantity the G-code path measures directly, so both inputs mean the same thing.

Defaults are 0.2 mm layers, a 0.4 mm nozzle (extrusion width taken as 1.05×
that), 3 walls and 20% infill; all are adjustable, and `--solid-fraction`
still overrides the model with a flat multiplier. On a 20 mm cube those
defaults give 161 mm² of material in a 400 mm² section — 632 N instead of the
1.57 kN a solid block would take.

Layer height sets where the Z sections fall and is reported, but it does not
scale the strength: its real influence runs through layer adhesion quality,
which no model predicts from height alone, so inventing a factor there would
be false precision.


**File formats.** Sliced G-code is the best input — it carries the real walls,
infill and layer heights.

**`.gcode.3mf` works directly**, which is what Bambu Studio and OrcaSlicer
export for Bambu printers: it is a ZIP holding the G-code beside the model,
thumbnails and plate settings, and the G-code inside is used. A plain `.3mf` is
read as a mesh — the modern replacement for STL, with real units and the build
transform applied. Reading either needs raw DEFLATE; rather than make the whole
parse path async to reach the browser's `DecompressionStream`, the inflater is
written out and checked against zlib on real archives, so it stays synchronous
and works everywhere.

STL works and is sliced internally. STEP is detected
but not tessellated, except for AP242 files that already carry triangulated
geometry: it is a boundary representation whose faces are trimmed analytic and
NURBS surfaces, and turning those into cross-sections needs a full CAD kernel.
A partial tessellator would give quietly wrong areas and therefore quietly
wrong strength, so instead the tool names the surface types it found and tells
you to export STL — which every CAD does in one step, and which your slicer
consumes anyway.


Limits: one load at a time, no contact, and the deflection integration
follows the longest run of the part — when the load path turns a corner the
report says so and the real deflection is larger.

### Stiffness, not just strength

`--deflect` takes the deflection you can live with and reports the load that
causes it. When that load is below the breaking load, **the part flexes past
usefulness before it fractures** and strength is the wrong thing to design
against. That is the normal case for TPU, long spans, thin panels, and anything
where fit or alignment matters more than survival.

Deflection assumes a cantilever with the load at the tip of the reported arm.
That idealisation is stated everywhere it appears. Override the arm with
`--arm` if your load case differs.

The arm is derived from the part: for Z it is the height above the section, for
X and Y the distance to the far end. That works well for tall parts. For a squat
one the Z arm is necessarily short, so the Z capacity comes out very large and
is not a number to quote — the governing axis will be X or Y, which is what the
report marks. Set `--arm` to your real load case and the ambiguity goes away.

### What to trust

The geometry is exact — verified to floating point against closed-form section
properties. All the uncertainty is in the material constants, especially layer
adhesion. So absolute numbers carry the full ±30%, but **ratios between two
configurations of the same part cancel most of it**:

| same L-bracket, same settings | flat on the bed | standing upright |
|---|---|---|
| Z bending capacity | 43.8 N·m | 1.17 N·m |
| Z flexural rigidity EI | 189 N·m² | 0.36 N·m² |

A 37× strength difference and a 520× stiffness difference, from orientation
alone. That comparison is far more reliable than either endpoint, and it drives
the highest-payoff decision available — which costs nothing to act on.

## Install

Copy `printstrength.py` anywhere and run it. No dependencies.

```bash
chmod +x printstrength.py
./printstrength.py part.gcode
```

## OrcaSlicer integration

This goes in **Print Settings → Others → Post-processing Scripts**, not in a
terminal. Orca runs it for you and appends the G-code path as the final
argument, which is why the line looks incomplete on its own.

```
/usr/bin/python3 /path/to/printstrength.py --orca;
```

On Windows, give the full path to both, with forward slashes:

```
C:/Python312/python.exe C:/tools/printstrength.py --orca;
```

Notes:
- Both paths must be absolute. Orca does not run the script from your project
  directory, so a relative path will not resolve.
- The trailing semicolon matters — Orca splits multiple scripts on it.
- Add any other flag you want, e.g. `... --orca --quality poor --load 200;`
- To check the command outside Orca, pass a G-code file yourself:
  `/usr/bin/python3 .../printstrength.py --orca /path/to/some.gcode`

Every slice then produces:
- a summary prepended to the G-code as comments, visible in Orca's own G-code
  preview and carried with the file to the printer,
- a full `yourfile.strength.txt` report next to the G-code.

The post-processor **never fails a slice**. If the material is unrecognised or
anything else goes wrong it writes the reason to stderr and exits 0, leaving
your G-code untouched.

The material is read from the profile's `filament_type`, so it usually needs no
configuration. Override with `--material` if your profile uses an unusual name.

Also works with Bambu Studio, PrusaSlicer, SuperSlicer and Cura — the parser
handles all of their G-code dialects, relative and absolute E, and arc moves
(`G2`/`G3`) from Orca's arc fitting.

## Usage

```
printstrength <file.gcode|file.stl> [options]
printstrength materials              # list materials and their properties
```

| Option | What it does |
|---|---|
| `--material`, `-m` | Override the material (`PETG`, `PA-CF`, `tough`, …) |
| `--load SPEC` | A plain force (`250`) or a whole load case (`"5kg -Z at top"`) |
| `--fix REGION` | What is held: `bottom`, `top`, `left`, `right`, `front`, `back`, `z<5` |
| `--deflect MM` | Deflection you can accept; flags when stiffness governs |
| `--arm MM` | Lever arm for the bending load (default: derived from the part) |
| `--moment NM` | Applied bending moment in N·m |
| `--temp C` | Service temperature; derates against the material's HDT |
| `--quality {good,typical,poor}` | Process knockdown, default `typical` (×0.85) |
| `--uts`, `--layer-adhesion` | Override material properties with your own test data |
| `--solid-fraction` | STL only: fraction of the volume that is solid |
| `--z-only` | Only check layer adhesion. Much faster on large files |
| `--green` | Resin: analyse as uncured, straight off the plate |
| `--json` | Machine-readable output |

Exit code is `2` if `--load` exceeds capacity on any axis, so it drops into CI
or a build script.

## Resin

Resin slicers emit proprietary binary formats (`.ctb`, `.pwmx`, `.goo`) with no
public toolpath semantics — and there are no toolpaths anyway, since each layer
cures as a continuous solid. So for resin the mesh *is* the model:

```bash
printstrength part.stl --material tough --load 80
```

Resin is close to isotropic, so geometry is nearly the whole answer, which is
why the STL is enough. Use `--green` to see the strength before post-curing.

## How it works

**FDM, from G-code.** Every extrusion move is reconstructed into a bead of
known length, width and height — width comes from the extruded volume rather
than from the profile, so it reflects what the slicer actually did, including
variable-width walls and flow compensation. Beads are tagged by feature type
from the slicer's own `;TYPE:` comments, and support, skirt and brim are
excluded from the part.

Three families of cutting plane are then scanned:

- **Z** — every layer interface. All beads lie in the plane, so the whole
  section carries load through layer welds. Sparse infill is derated against
  walls because it lands on air as often as on plastic.
- **X and Y** — every vertical plane. The chord a plane cuts through a bead is
  constant as the plane sweeps across it, which turns the scan into a
  difference-array accumulation: O(segments), not O(segments × planes).

For each plane the tool computes the solid area, and the load it can carry
given bead orientation: a bead aligned with the plane normal carries load on
the polymer backbone at full UTS, a bead lying in the plane carries it across
the weld between beads. The blend between the two is `cos²θ`, the standard
first-order lamina result. Area moments are computed exactly — each bead is a
rotated rectangle, so its second moment closes in closed form — giving section
moduli and therefore bending capacity, not just tension.

Area moments are computed exactly — each bead is a rotated rectangle, so its
second moment closes in closed form — giving section moduli, and from those the
bending capacity, the flexural rigidity `EI`, and the deflection at the breaking
load. Each axis is scanned twice: once for the tension-critical section and once
for the bending-critical one, which are rarely the same plane.

**Resin and STL.** The mesh is sliced analytically. Section area, centroid and
second moments come from contour integrals over the oriented boundary
segments. Those integrals don't care what order the segments are in, only that
each is oriented consistently — which the triangle winding provides for free —
so no polygon stitching is needed and the result is exact to floating point.

Verified against closed-form section properties: a 20 mm cube, a rectangular
bar, a notched bar and a tube all come back exact (`python3 tests/test_all.py`).

## Accuracy — read this

This is a **first-order estimate**, not FEA and not a substitute for breaking a
test coupon.

The geometry is exact. The material data is not. Layer adhesion in particular
is the single most variable number in FDM: it swings with nozzle temperature,
part cooling, chamber temperature, filament moisture and layer time, and it can
easily differ by a factor of two between two printers running the same spool.
The defaults are typical values for reasonably tuned prints.

Expect **±30%** on a well-characterised material and worse on a filament you
have never dried or calibrated. It will also be optimistic near sharp internal
corners, holes and other stress concentrations, which it does not model.

What it is reliably good at is *comparison*: which axis is weak, which section
fails first, whether more walls or more infill is the better use of material,
and whether reorienting the part is worth more than any setting change. Those
answers hold up even when the absolute number is off.

If you have real test data for your filament and printer, feed it in with
`--uts` and `--layer-adhesion` and the absolute numbers get much better.

**Do not use this as the sole basis for anything load-bearing or
safety-critical.**

Other caveats:
- Deflection assumes a cantilever with the load at the tip of the reported arm.
  Real boundary conditions differ; a fixed-fixed beam is 4× stiffer.
- Torsion is not modelled.
- Fatigue is not modelled. Cyclic loading fails far below these numbers.
- Creep is not modelled. Under sustained load, design to roughly half.
- Impact is not modelled; it depends on toughness, not strength.
- Binary G-code (`.bgcode`) is not supported — turn off "Export as binary
  G-code" in Orca, or decode it first.

## Tests

```bash
python3 tests/make_fixtures.py   # geometry with known analytic properties
python3 tests/make_gcode.py      # OrcaSlicer-format G-code fixtures
python3 tests/test_all.py
```

## License

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE). If you build on
this work, keep the NOTICE file with it.
