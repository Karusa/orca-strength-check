"""Generate test geometry with known analytic section properties."""
import struct, math, os, sys

OUT = os.path.dirname(os.path.abspath(__file__))

def write_binary_stl(path, tris):
    with open(path, "wb") as f:
        f.write(b"\0" * 80)
        f.write(struct.pack("<I", len(tris)))
        for t in tris:
            n = normal(t)
            f.write(struct.pack("<12fH", *n, *t[0], *t[1], *t[2], 0))

def normal(t):
    a, b, c = t
    u = [b[i]-a[i] for i in range(3)]
    v = [c[i]-a[i] for i in range(3)]
    n = [u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0]]
    L = math.sqrt(sum(x*x for x in n)) or 1.0
    return [x/L for x in n]

def box(x0,y0,z0,x1,y1,z1):
    """Axis-aligned box, outward normals."""
    p = [(x0,y0,z0),(x1,y0,z0),(x1,y1,z0),(x0,y1,z0),
         (x0,y0,z1),(x1,y0,z1),(x1,y1,z1),(x0,y1,z1)]
    faces = [(0,3,2,1),(4,5,6,7),(0,1,5,4),(1,2,6,5),(2,3,7,6),(3,0,4,7)]
    tris=[]
    for a,b,c,d in faces:
        tris.append((p[a],p[b],p[c]))
        tris.append((p[a],p[c],p[d]))
    return tris

# 1. 20mm cube. Z-section: 400 mm^2, I = 20^4/12 = 13333.3, c=10, S=1333.3
write_binary_stl(os.path.join(OUT,"cube20.stl"), box(0,0,0,20,20,20))

# 2. Rectangular bar 40 x 10 x 5 (x,y,z).
#    Section normal to X: 10 x 5 = 50 mm^2
#    I about the horizontal (bending in Z) = 10*5^3/12 = 104.17, c=2.5, S=41.67
write_binary_stl(os.path.join(OUT,"bar40x10x5.stl"), box(0,0,0,40,10,5))

# 3. Notched bar: 40 x 10 x 5 with a step down to 4mm wide in the middle.
#    Min X-section = 4 x 5 = 20 mm^2
tris = box(0,0,0,18,10,5) + box(18,3,0,22,7,5) + box(22,0,0,40,10,5)
write_binary_stl(os.path.join(OUT,"notched.stl"), tris)

# 4. Hollow tube: OD 20, ID 14, height 30, as a polygonal approximation.
def tube(od, id_, h, n=128):
    tris=[]
    ro, ri = od/2, id_/2
    for k in range(n):
        a0 = 2*math.pi*k/n; a1 = 2*math.pi*(k+1)/n
        o0=(ro*math.cos(a0), ro*math.sin(a0)); o1=(ro*math.cos(a1), ro*math.sin(a1))
        i0=(ri*math.cos(a0), ri*math.sin(a0)); i1=(ri*math.cos(a1), ri*math.sin(a1))
        # outer wall (normal outward)
        tris.append(((o0[0],o0[1],0),(o1[0],o1[1],0),(o1[0],o1[1],h)))
        tris.append(((o0[0],o0[1],0),(o1[0],o1[1],h),(o0[0],o0[1],h)))
        # inner wall (normal inward -> points toward axis)
        tris.append(((i0[0],i0[1],0),(i1[0],i1[1],h),(i1[0],i1[1],0)))
        tris.append(((i0[0],i0[1],0),(i0[0],i0[1],h),(i1[0],i1[1],h)))
        # bottom annulus (normal -z)
        tris.append(((o0[0],o0[1],0),(i0[0],i0[1],0),(i1[0],i1[1],0)))
        tris.append(((o0[0],o0[1],0),(i1[0],i1[1],0),(o1[0],o1[1],0)))
        # top annulus (normal +z)
        tris.append(((o0[0],o0[1],h),(o1[0],o1[1],h),(i1[0],i1[1],h)))
        tris.append(((o0[0],o0[1],h),(i1[0],i1[1],h),(i0[0],i0[1],h)))
    return tris
write_binary_stl(os.path.join(OUT,"tube.stl"), tube(20,14,30))

print("fixtures written to", OUT)
