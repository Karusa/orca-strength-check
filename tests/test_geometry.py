"""Check section properties against closed-form values."""
import sys, os, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import printstrength as ps

T = os.path.join(os.path.dirname(os.path.abspath(__file__)))
fails = []

def check(label, got, want, tol=0.02):
    err = abs(got - want) / want if want else abs(got)
    ok = err <= tol
    print(f"  {'PASS' if ok else 'FAIL'}  {label:<42} got {got:10.3f}  want {want:10.3f}  ({err*100:.2f}%)")
    if not ok:
        fails.append(label)

print("\n20mm cube")
m = ps.load_stl(os.path.join(T, "cube20.stl"))
check("volume (mm^3)", m.volume(), 8000)
z = ps.slice_mesh(m, "z", nplanes=200)
s = min(z, key=lambda s: s.area)
check("min Z section area", s.area, 400)
check("I about in-plane axis", s.I1, 20**4/12)
check("section modulus", s.S1, 20**4/12/10)
x = ps.slice_mesh(m, "x", nplanes=200)
check("min X section area", min(q.area for q in x), 400)

print("\n40 x 10 x 5 bar")
m = ps.load_stl(os.path.join(T, "bar40x10x5.stl"))
check("volume", m.volume(), 2000)
x = ps.slice_mesh(m, "x", nplanes=300)
s = min(x, key=lambda q: q.area)
check("min X section area", s.area, 50)
# section normal to X spans (y,z) = 10 wide x 5 tall
Iy = 10*5**3/12      # bending that deflects in Z
Iz = 5*10**3/12      # bending that deflects in Y
check("I (smaller, bending in Z)", min(s.I1, s.I2), Iy)
check("I (larger, bending in Y)", max(s.I1, s.I2), Iz)
check("section modulus (weak)", min(s.S1, s.S2), Iy/2.5)
z = ps.slice_mesh(m, "z", nplanes=200)
check("min Z section area", min(q.area for q in z), 400)

print("\nnotched bar (4mm throat)")
m = ps.load_stl(os.path.join(T, "notched.stl"))
x = ps.slice_mesh(m, "x", nplanes=400)
s = min(x, key=lambda q: q.area)
check("min X section area", s.area, 20)
check("notch located near x=20", s.pos, 20, tol=0.15)

print("\ntube OD20 ID14 h30")
m = ps.load_stl(os.path.join(T, "tube.stl"))
ro, ri = 10, 7
check("volume", m.volume(), math.pi*(ro**2-ri**2)*30, tol=0.01)
z = ps.slice_mesh(m, "z", nplanes=100)
s = min(z, key=lambda q: q.area)
check("min Z section area", s.area, math.pi*(ro**2-ri**2), tol=0.01)
check("I annulus", s.I1, math.pi*(ro**4-ri**4)/4, tol=0.01)

print()
if fails:
    print(f"{len(fails)} FAILURES: {fails}")
    sys.exit(1)
print("all geometry checks passed")
