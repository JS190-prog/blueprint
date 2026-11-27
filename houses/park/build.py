"""A neighbourhood park from its cleaned CAD drawing (tools/site_prep.py -> site.json), photoreal, in Blender.

  blender -b --factory-startup -P houses/park/build.py -- --site <site.json> [--assets <manifest.json>] \
      [--shots top,hero,play] [--res 960x540] [--samples 32] [--blend out.blend]
  ... --film [--fps 60] [--frames 0,174-630] : the one-shot film from the top frame down to the oblique view
      (<out>/film/f####.png + film.json); the drawing-to-3D wipe over its first frame is left to compositing.

Plan metres are world metres (x right, y up, z up, origin at the site window's lower-left corner). What the drawing
does not say is set here with a reason: surface heights, curb size, hedge height, which outlined area is what.
Trees take their species and height from the sheet's quantity table (site.json), their crown from the drawn symbol;
the models come from the asset manifest (one per species) and fall back to a plain trunk and crown without it.
Facilities come from facilities.py (next to this file) when it is there.
"""
import json
import math
import os
import random
import sys
import time

import bmesh
import bpy
from mathutils import Matrix, Vector

HERE = os.path.dirname(os.path.abspath(__file__))
argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OPTS = {}
for i, a in enumerate(argv):
    if a.startswith("--"):
        OPTS[a[2:]] = argv[i + 1] if i + 1 < len(argv) and not argv[i + 1].startswith("--") else True
SITE = json.load(open(OPTS["site"], encoding="utf-8"))
MAN = json.load(open(OPTS["assets"], encoding="utf-8")) if OPTS.get("assets") and os.path.exists(OPTS["assets"]) else {}
OUT = OPTS.get("out", os.path.join(os.environ.get("BLUEPRINT_OUT", "blueprint-out"), "park"))
os.makedirs(OUT, exist_ok=True)
SHOTS = [s for s in str(OPTS.get("shots", "top,hero,play")).split(",") if s]
RES = tuple(map(int, str(OPTS.get("res", "960x540")).split("x")))
SAMPLES = int(OPTS.get("samples", 32))
W, H = SITE["size_m"]
rng = random.Random(7)
t0 = time.time()

# what the drawing leaves open, with the reason
Z_PAVE = 0.0      # the paved walks are the datum
Z_RUBBER = 0.03   # poured rubber surfacing sits a little proud of the pavers it meets
Z_BED = 0.08      # planting beds: soil and lawn just under the curb top, so the curb reads as an edge
CURB = (0.12, 0.15)  # granite curb width and height above the walk (a common 120 x 150 park curb)
HEDGE_H = 0.65    # clipped box / spindle hedges along the walks (회양목 H0.3, 사철나무 H1.2 in the table: a mixed band)
SHRUB_H = 0.45    # azalea and spirea masses (H0.3-0.6 in the table)

# ---------------------------------------------------------------- scene, GPU, colour
for o in list(bpy.data.objects):
    bpy.data.objects.remove(o, do_unlink=True)
scene = bpy.context.scene
cp = bpy.context.preferences.addons["cycles"].preferences
cp.compute_device_type = "OPTIX"
cp.get_devices()
for d in cp.devices:
    d.use = d.type == "OPTIX"
scene.render.engine = "CYCLES"
scene.cycles.device = "GPU"
scene.cycles.samples = SAMPLES
scene.cycles.use_adaptive_sampling = True
scene.cycles.use_denoising = True
scene.cycles.denoiser = "OPTIX"
scene.cycles.max_bounces = 8
scene.cycles.transparent_max_bounces = 24  # leaf cards stack many alpha layers
scene.render.resolution_x, scene.render.resolution_y = RES
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = "PNG"
try:
    scene.view_settings.view_transform = "Khronos PBR Neutral"  # AgX turns warm light beige (SKILL.md rules)
except TypeError:
    scene.view_settings.view_transform = "AgX"
ROOT = scene.collection


def coll(name):
    c = bpy.data.collections.new(name)
    ROOT.children.link(c)
    return c


C_GROUND, C_PLANT, C_TREE, C_FAC = coll("ground"), coll("planting"), coll("trees"), coll("facilities")


def inp(node, names, value):
    for n in names if isinstance(names, (list, tuple)) else [names]:
        if n in node.inputs:
            node.inputs[n].default_value = value
            return
    raise KeyError(names)


# ---------------------------------------------------------------- materials
def tex_of(key):
    """Texture maps from the manifest: {'diffuse': path, 'normal': path, 'rough': path, 'size_m': 2.0} or None."""
    t = MAN.get("textures", {}).get(key) or MAN.get(key)
    return t if isinstance(t, dict) and t.get("diffuse") and os.path.exists(t["diffuse"]) else None


def material(name, color, rough=0.8, tex=None, size=1.0, bump=0.3, var=0.12, metal=0.0):
    """Principled with the manifest's maps when there are any, else a colour broken up by noise (no flat CG fills)."""
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    N, L = m.node_tree.nodes, m.node_tree.links
    b = N["Principled BSDF"]
    inp(b, "Roughness", rough)
    inp(b, "Metallic", metal)
    tc = N.new("ShaderNodeTexCoord")
    mp = N.new("ShaderNodeMapping")
    mp.inputs["Scale"].default_value = (1 / size, 1 / size, 1 / size)
    L.new(tc.outputs["Object"], mp.inputs["Vector"])
    t = tex_of(tex) if tex else None
    if t:
        sz = t.get("size_m", size)
        mp.inputs["Scale"].default_value = (1 / sz, 1 / sz, 1 / sz)

        def img(path, non_color):
            n = N.new("ShaderNodeTexImage")
            n.image = bpy.data.images.load(path, check_existing=True)
            n.projection = "BOX"
            n.projection_blend = 0.2
            if non_color:
                n.image.colorspace_settings.name = "Non-Color"
            L.new(mp.outputs["Vector"], n.inputs["Vector"])
            return n
        d = img(t["diffuse"], False)
        hsv = N.new("ShaderNodeHueSaturation")
        L.new(d.outputs["Color"], hsv.inputs["Color"])
        hsv.inputs["Value"].default_value = t.get("value", 1.0)
        L.new(hsv.outputs["Color"], b.inputs["Base Color"])
        if t.get("rough") and os.path.exists(t["rough"]):
            L.new(img(t["rough"], True).outputs["Color"], b.inputs["Roughness"])
        if t.get("normal") and os.path.exists(t["normal"]):
            nm = N.new("ShaderNodeNormalMap")
            L.new(img(t["normal"], True).outputs["Color"], nm.inputs["Color"])
            L.new(nm.outputs["Normal"], b.inputs["Normal"])
        return m
    noise = N.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 3.0
    noise.inputs["Detail"].default_value = 8.0
    L.new(mp.outputs["Vector"], noise.inputs["Vector"])
    ramp = N.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].color = (*[c * (1 - var) for c in color], 1)
    ramp.color_ramp.elements[1].color = (*[min(1, c * (1 + var)) for c in color], 1)
    L.new(noise.outputs["Fac"], ramp.inputs["Fac"])
    L.new(ramp.outputs["Color"], b.inputs["Base Color"])
    bm_ = N.new("ShaderNodeBump")
    bm_.inputs["Strength"].default_value = bump
    n2 = N.new("ShaderNodeTexNoise")
    n2.inputs["Scale"].default_value = 40.0
    L.new(mp.outputs["Vector"], n2.inputs["Vector"])
    L.new(n2.outputs["Fac"], bm_.inputs["Height"])
    L.new(bm_.outputs["Normal"], b.inputs["Normal"])
    return m


M = {
    "pave": material("pavers", (0.55, 0.53, 0.50), 0.85, "pavers", 1.2),
    "rubber": material("rubber", (0.42, 0.20, 0.15), 0.9, "rubber", 1.0, bump=0.6),
    "rubber_b": material("rubber_toddler", (0.30, 0.38, 0.22), 0.9, "rubber", 1.0, bump=0.6),
    "lawn": material("lawn", (0.16, 0.26, 0.07), 0.95, "lawn", 1.5, bump=0.8, var=0.25),
    "soil": material("bark_mulch", (0.16, 0.11, 0.07), 0.95, "mulch", 0.8, bump=0.8),
    "curb": material("granite", (0.62, 0.61, 0.58), 0.7, "granite", 0.6),
    "hedge": material("hedge_leaves", (0.07, 0.17, 0.05), 0.75, "hedge", 0.5, bump=1.0, var=0.3),
    "shrub": material("shrub_leaves", (0.10, 0.22, 0.06), 0.7, "shrub", 0.5, bump=1.0, var=0.3),
    "ground": material("surround", (0.03, 0.03, 0.029), 0.95, None, 2.0, var=0.06),  # dark asphalt: the park reads first
    "bark": material("trunk", (0.20, 0.15, 0.11), 0.9, None, 0.3, bump=1.0),
}


# ---------------------------------------------------------------- geometry
def link(o, c):
    c.objects.link(o)
    return o


def area(pts):
    return abs(sum(pts[i][0] * pts[i - 1][1] - pts[i - 1][0] * pts[i][1] for i in range(len(pts)))) / 2


def slab(name, pts, z0, z1, top, side, c=C_GROUND):
    """A closed outline extruded from z0 to z1, top on material 0; concave outlines triangulated by ear clipping."""
    if math.dist(pts[0], pts[-1]) < 1e-6:
        pts = pts[:-1]
    bm = bmesh.new()
    vs = [bm.verts.new((x, y, z0)) for x, y in pts]
    try:
        f = bm.faces.new(vs)
    except ValueError:
        bm.free()
        return None
    bm.normal_update()
    if f.normal.z < 0:
        f.normal_flip()
    ret = bmesh.ops.extrude_face_region(bm, geom=[f])
    nv = [e for e in ret["geom"] if isinstance(e, bmesh.types.BMVert)]
    bmesh.ops.translate(bm, vec=(0, 0, z1 - z0), verts=nv)
    bm.normal_update()
    for g in bm.faces:
        g.material_index = 0 if g.normal.z > 0.5 else 1
    tops = [g for g in bm.faces if g.normal.z > 0.5 or g.normal.z < -0.5]
    bmesh.ops.triangulate(bm, faces=tops, quad_method="BEAUTY", ngon_method="EAR_CLIP")
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    me.materials.append(top)
    me.materials.append(side)
    return link(bpy.data.objects.new(name, me), c)


def ribbon(name, pts, closed, width, z0, z1, mat, c=C_GROUND):
    """A curb: the outline raised into a wall and thickened (solidify), edges eased with a small bevel."""
    bm = bmesh.new()
    lo = [bm.verts.new((x, y, z0)) for x, y in pts]
    hi = [bm.verts.new((x, y, z1)) for x, y in pts]
    n = len(pts)
    for i in range(n if closed else n - 1):
        j = (i + 1) % n
        if lo[i] is not lo[j]:
            try:
                bm.faces.new((lo[i], lo[j], hi[j], hi[i]))
            except ValueError:
                pass
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    me.materials.append(mat)
    o = link(bpy.data.objects.new(name, me), c)
    s = o.modifiers.new("solid", "SOLIDIFY")
    s.thickness = width
    s.offset = 0
    s.use_even_offset = True
    b = o.modifiers.new("bevel", "BEVEL")
    b.width = 0.01
    b.segments = 2
    b.limit_method = "ANGLE"
    return o


def puff(name, pts, height, mat, c=C_PLANT, cell=0.35):
    """A clipped hedge or shrub mass: the outline extruded, subdivided and pushed out by noise into a leafy mound."""
    o = slab(name, pts, Z_BED, Z_BED + height, mat, mat, c)
    if not o:
        return None
    r = o.modifiers.new("remesh", "REMESH")
    r.mode = "VOXEL"
    r.voxel_size = 0.07
    sm = o.modifiers.new("smooth", "SMOOTH")
    sm.iterations = 8
    sm.factor = 0.9
    tx = bpy.data.textures.new(name + "_n", "CLOUDS")
    tx.noise_scale = cell
    dp = o.modifiers.new("leafy", "DISPLACE")
    dp.texture = tx
    dp.strength = 0.09
    dp.mid_level = 0.5
    for p in o.data.polygons:
        p.use_smooth = True
    return o


# ---------------------------------------------------------------- ground: walks, beds, play surfaces, curbs
A = SITE["areas"]
boundary = max(A["boundary"], key=lambda a: area(a["pts"]))["pts"]
big = area(boundary)
slab("walks", boundary, Z_PAVE - 0.25, Z_PAVE, M["pave"], M["curb"])


def fade_far(m, centre, r0, r1, dark):
    """Darken a material with distance from a point: the park sits on ground that sinks into the deck's black."""
    N, L = m.node_tree.nodes, m.node_tree.links
    b = N["Principled BSDF"]
    src = b.inputs["Base Color"].links[0].from_socket
    geo = N.new("ShaderNodeNewGeometry")
    dist = N.new("ShaderNodeVectorMath")
    dist.operation = "DISTANCE"
    dist.inputs[1].default_value = centre
    L.new(geo.outputs["Position"], dist.inputs[0])
    mr = N.new("ShaderNodeMapRange")
    mr.interpolation_type = "SMOOTHERSTEP"  # a linear ramp leaves a visible ring where it starts
    mr.inputs["From Min"].default_value, mr.inputs["From Max"].default_value = r0, r1
    L.new(dist.outputs["Value"], mr.inputs["Value"])
    mix = N.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    L.new(mr.outputs["Result"], mix.inputs["Factor"])
    sock = {x.identifier: x for x in mix.inputs}   # the colour sockets, not the float ones that share the names
    L.new(src, sock["A_Color"])
    sock["B_Color"].default_value = (*dark, 1)
    L.new(next(x for x in mix.outputs if x.identifier == "Result_Color"), b.inputs["Base Color"])
    # the sky's glare at grazing angles would keep the far ground grey whatever its colour: fade the specular out too
    sp = N.new("ShaderNodeMapRange")
    sp.interpolation_type = "SMOOTHERSTEP"
    sp.inputs["From Min"].default_value, sp.inputs["From Max"].default_value = r0, r1
    sp.inputs["To Min"].default_value, sp.inputs["To Max"].default_value = 0.3, 0.0
    L.new(dist.outputs["Value"], sp.inputs["Value"])
    L.new(sp.outputs["Result"], b.inputs["Specular IOR Level"])


# a wide plain apron round the park, so the camera never sees the model's edge (no neighbours: the site stays anonymous).
# It sinks into near-black from 45 m to 90 m off the park centre (the park reaches 47 m), so no horizon or slab edge shows and the film's last frame hands
# over to the deck's dark stage.
apron = [(-700, -700), (W + 700, -700), (W + 700, H + 700), (-700, H + 700)]
slab("apron", apron, -0.27, -0.02, M["ground"], M["ground"])
fade_far(M["ground"], (W / 2, H / 2, 0), 45, 90, (0.004, 0.004, 0.004))

# surfaces: the regions the curbs close off (site.json zones, tools/site_prep.py). What each kind is comes from the
# park's look file (--look): {"kinds": {"planted": "lawn", "hatched": "pavers", "open": "pavers"},
# "zones": {"<id>": "rubber", ...}}, with the reason for each override written there.
LOOK = json.load(open(OPTS["look"], encoding="utf-8")) if OPTS.get("look") else {}
SURF = {"lawn": (M["lawn"], Z_BED), "soil": (M["soil"], Z_BED), "pavers": (M["pave"], Z_PAVE + 0.005),
        "rubber": (M["rubber"], Z_RUBBER), "rubber_b": (M["rubber_b"], Z_RUBBER)}
kinds = {"planted": "lawn", "hatched": "pavers", "open": "pavers", **LOOK.get("kinds", {})}
for z in sorted(SITE.get("zones", []), key=lambda z: SURF[LOOK.get("zones", {}).get(str(z["id"]), kinds[z["kind"]])][1]):
    name = LOOK.get("zones", {}).get(str(z["id"]), kinds[z["kind"]])
    top, zt = SURF[name]
    slab(f"zone_{z['id']}_{name}", z["pts"], Z_PAVE - 0.05, zt, top, M["soil"] if zt > Z_PAVE + 0.01 else top)
# curbs on every drawn edge line: they sit on the shared border of two surfaces
for i, a in enumerate(a for a in A.get("curb", []) if not a["hatch"]):
    ribbon(f"curb_{i}", a["pts"], True, CURB[0], Z_PAVE - 0.05, Z_PAVE + CURB[1], M["curb"])
for i, ln in enumerate(SITE["lines"].get("curb", [])):
    ribbon(f"curb_line_{i}", ln["pts"], False, CURB[0], Z_PAVE - 0.05, Z_PAVE + CURB[1], M["curb"])
seen = set()
for a in (a for a in A.get("curb", []) if a["hatch"]):  # hatched areas whose edge was not drawn as its own line
    key = (round(area(a["pts"]), 0), round(min(p[0] for p in a["pts"]), 0))
    if key not in seen and area(a["pts"]) > 1.0:
        seen.add(key)
        ribbon(f"curb_h{len(seen)}", a["pts"], True, CURB[0], Z_PAVE - 0.05, Z_PAVE + CURB[1], M["curb"])

# ---------------------------------------------------------------- planting masses
for i, a in enumerate(x for x in A.get("hedge", []) if x["hatch"] and area(x["pts"]) < big * 0.5):
    puff(f"hedge_{i}", a["pts"], HEDGE_H, M["hedge"], cell=0.3)
for i, a in enumerate(x for x in A.get("shrub", []) if x["hatch"]):
    puff(f"shrub_{i}", a["pts"], SHRUB_H, M["shrub"], cell=0.45)
for i, a in enumerate(A.get("groundcover", [])):
    slab(f"groundcover_{i}", a["pts"], Z_BED, Z_BED + 0.06, M["shrub"], M["shrub"], C_PLANT)

# ---------------------------------------------------------------- trees
_src = {}


def tree_src(species):
    """The species' model from the manifest, imported once into a hidden collection; None when there is none."""
    if species in _src:
        return _src[species]
    e = MAN.get("species", {}).get(species) or MAN.get(species)
    path = e and (e.get("lod") or e.get("model"))
    if not path or not os.path.exists(path):
        _src[species] = None
        return None
    before = set(bpy.data.objects)
    if path.lower().endswith(".blend"):
        with bpy.data.libraries.load(path, link=False) as (src, dst):
            dst.objects = list(src.objects)
        new = [o for o in dst.objects if o]
    else:
        bpy.ops.import_scene.gltf(filepath=path)
        new = [o for o in bpy.data.objects if o not in before]
    c = bpy.data.collections.new("src_" + species)
    for o in new:
        for u in list(o.users_collection):
            u.objects.unlink(o)
        c.objects.link(o)
    pts = [o.matrix_world @ Vector(b) for o in new if o.type == "MESH" for b in o.bound_box]
    mn = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    mx = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    _src[species] = (c, mn, mx)
    return _src[species]


FALLBACK_TINT = {"홍단풍": (0.35, 0.07, 0.05), "청단풍": (0.18, 0.30, 0.06), "스트로브잣나무": (0.07, 0.16, 0.08),
                 "소나무(조형)": (0.08, 0.16, 0.07)}
_leaf = {}


def fallback_tree(t, h, crown):
    """No model: a tapered trunk and a lumpy crown of the right size, so the layout reads while assets are missing."""
    sp = t["species"] or "?"
    if sp not in _leaf:
        _leaf[sp] = material("leaf_" + sp, FALLBACK_TINT.get(sp, (0.12, 0.24, 0.06)), 0.7, None, 0.4, bump=1.0, var=0.35)
    bpy.ops.mesh.primitive_cone_add(vertices=10, radius1=0.06 + h * 0.012, radius2=0.03, depth=h * 0.55,
                                    location=(t["x"], t["y"], Z_BED + h * 0.275))
    tr = bpy.context.object
    tr.data.materials.append(M["bark"])
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=3, radius=1, location=(t["x"], t["y"], Z_BED + h * 0.62))
    cr = bpy.context.object
    conifer = sp in ("스트로브잣나무",)
    cr.scale = (crown / 2, crown / 2, (h * 0.42) if conifer else max(crown / 2 * 0.85, h * 0.3))
    tx = bpy.data.textures.new("crown", "CLOUDS")
    tx.noise_scale = 0.35
    d = cr.modifiers.new("lumps", "DISPLACE")
    d.texture = tx
    d.strength = 0.25
    cr.data.materials.append(_leaf[sp])
    for p in cr.data.polygons:
        p.use_smooth = True
    for o in (tr, cr):
        for u in list(o.users_collection):
            u.objects.unlink(o)
        C_TREE.objects.link(o)


n_model = 0
for t in SITE["trees"]:
    h = t["h"] or 3.5
    crown = max(t["crown"], 1.0)
    src = tree_src(t["species"]) if t["species"] else None
    if not src:
        fallback_tree(t, h, crown)
        continue
    c, mn, mx = src
    s = h / (mx.z - mn.z) * rng.uniform(0.94, 1.06)  # nursery stock of one size still differs a little
    e = bpy.data.objects.new(t["species"], None)
    e.instance_type = "COLLECTION"
    e.instance_collection = c
    yaw = rng.uniform(0, 2 * math.pi)
    R = Matrix.Rotation(yaw, 3, "Z")
    off = Vector(((mn.x + mx.x) / 2, (mn.y + mx.y) / 2, mn.z)) * s
    e.location = Vector((t["x"], t["y"], Z_BED)) - R @ off
    e.rotation_euler = (0, 0, yaw)
    e.scale = (s, s, s)
    C_TREE.objects.link(e)
    n_model += 1

# ---------------------------------------------------------------- facilities
fac_py = os.path.join(HERE, "facilities.py")
types_json = os.path.join(HERE, "facility_types.json")
if os.path.exists(fac_py) and os.path.exists(types_json) and not OPTS.get("no-facilities"):
    sys.path.insert(0, HERE)
    import facilities  # noqa: E402

    facilities.build(SITE, json.load(open(types_json, encoding="utf-8")), C_FAC)
print(f"built in {time.time() - t0:.1f} s: {len(SITE['trees'])} trees ({n_model} from models), {len(bpy.data.objects)} objects")

# ---------------------------------------------------------------- light: sky and a morning sun
SUN_AZ = float(OPTS.get("sun_az", 140))  # degrees from plan +x, counter-clockwise: light across the long axis
SUN_EL = float(OPTS.get("sun_el", 38))   # a mid-morning sun: shadows long enough to model the trees, not dusk
world = bpy.data.worlds.new("sky")
scene.world = world
world.use_nodes = True
WN, WL = world.node_tree.nodes, world.node_tree.links
hdri = (MAN.get("hdri") or {}).get("path") if isinstance(MAN.get("hdri"), dict) else MAN.get("hdri")
if hdri and os.path.exists(hdri):
    env = WN.new("ShaderNodeTexEnvironment")
    env.image = bpy.data.images.load(hdri)
    WL.new(env.outputs["Color"], WN["Background"].inputs["Color"])
    WN["Background"].inputs["Strength"].default_value = 0.8
else:  # no HDRI yet: a physical sky whose sun stands where the sun lamp does (a default sky sits at sunset: all orange)
    sky = WN.new("ShaderNodeTexSky")
    for k, v in (("sun_elevation", math.radians(SUN_EL)), ("sun_rotation", math.radians(SUN_AZ) - math.pi / 2), ("sun_disc", False)):
        if hasattr(sky, k):
            setattr(sky, k, v)
    WL.new(sky.outputs["Color"], WN["Background"].inputs["Color"])
    WN["Background"].inputs["Strength"].default_value = 0.25
sun = bpy.data.lights.new("sun", "SUN")
sun.energy = 4.0
sun.angle = math.radians(1.5)
sun.color = (1.0, 0.93, 0.84)
so = link(bpy.data.objects.new("sun", sun), ROOT)
az, el = math.radians(SUN_AZ), math.radians(SUN_EL)
to_sun = Vector((math.cos(el) * math.cos(az), math.cos(el) * math.sin(az), math.sin(el)))
so.rotation_euler = (-to_sun).to_track_quat("-Z", "Y").to_euler()


# ---------------------------------------------------------------- cameras
def camera(name, loc, target, lens):
    cd = bpy.data.cameras.new(name)
    cd.lens = lens
    cd.sensor_width = 36
    cd.clip_start = 0.1
    cd.clip_end = 600
    o = link(bpy.data.objects.new(name, cd), ROOT)
    o.location = loc
    o.rotation_euler = (Vector(target) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()
    return o


def top():
    """The intro reel's drawing frame, exactly: plan (x, y) -> screen (960 + (y-44)*18, 540 + (x-17.25)*18) px."""
    cd = bpy.data.cameras.new("top")
    cd.type = "ORTHO"
    cd.sensor_fit = "HORIZONTAL"
    cd.ortho_scale = 1920 / 18
    cd.clip_end = 600
    o = link(bpy.data.objects.new("cam_top", cd), ROOT)
    o.location = (17.25, 44.0, 120.0)
    o.rotation_euler = (0, 0, math.pi / 2)  # screen right = plan +y, screen up = plan -x
    return o


SHOT = {
    "top": top,
    "hero": lambda: camera("cam_hero", (W + 22, -14, 24), (W * 0.45, H * 0.42, 0), 32),
    "play": lambda: camera("cam_play", (23.5, 20.5, 5.5), (13.5, 47.0, 0.6), 30),  # from the east walk, over the play floor
}


def smoother(u):
    u = min(1.0, max(0.0, u))
    return u * u * u * (u * (u * 6 - 15) + 10)


# The film's one shot, in film seconds: the reel's plan frame held while the 3D wipes in over the lines (done in
# compositing), then the camera comes down and round to the oblique view, ending still as the film fades to the cover.
FPS = int(OPTS.get("fps", 30))
MOVE = (2.9, 10.5)  # the wipe (intro film.py) runs 1.3-2.8 s, on the narration's "이렇게"
FILM_A = dict(T=(17.25, 44.0, 0.0), D=3000.0, p=90.0, h=180.0, w=1920 / 18)  # = the top camera: 18 px/m, plan -x up
FILM_B = dict(T=(16.0, 41.0, 0.0), D=75.0, p=34.0, h=128.8, w=90.0)          # from the south-east corner, over the play floor


def film_pose(t):
    """Camera (location, rotation matrix, lens, clip_start) at film time t. A 3 km telephoto is the ortho top camera
    to within 3 px at the frame edge, so the shot starts on the reel's drawing exactly. The field of view widens
    linearly in angle while the target, pitch, heading and width at the target ease along with it."""
    u = smoother((t - MOVE[0]) / (MOVE[1] - MOVE[0]))
    a, b = FILM_A, FILM_B
    fov0, fov1 = (2 * math.atan(s["w"] / 2 / s["D"]) for s in (a, b))
    w = a["w"] + (b["w"] - a["w"]) * u
    D = w / 2 / math.tan((fov0 + (fov1 - fov0) * u) / 2)
    p = math.radians(a["p"] + (b["p"] - a["p"]) * u)
    h = math.radians(a["h"] + (b["h"] - a["h"]) * u)
    T = Vector(a["T"]).lerp(Vector(b["T"]), u)
    fh = Vector((math.cos(h), math.sin(h), 0))
    f = Vector((fh.x * math.cos(p), fh.y * math.cos(p), -math.sin(p)))  # looking forward and down
    up = Vector((fh.x * math.sin(p), fh.y * math.sin(p), math.cos(p)))
    r = f.cross(up)
    R = Matrix((r, up, -f)).transposed()  # camera local X right, Y up, -Z forward
    return T - f * D, R, 36 * D / w, max(0.1, D - 250)


def film(frames):
    cd = bpy.data.cameras.new("film")
    cd.sensor_fit = "HORIZONTAL"
    cd.sensor_width = 36
    cd.clip_end = 5000
    o = link(bpy.data.objects.new("cam_film", cd), ROOT)
    scene.camera = o
    scene.render.use_persistent_data = True  # only the camera moves: keep the BVH between frames
    d = os.path.join(OUT, "film")
    os.makedirs(d, exist_ok=True)
    # what the compositor needs: frame k is film time k / fps; before MOVE[0] the camera holds frame 0's pose
    json.dump({"fps": FPS, "move": MOVE, "frames": frames, "res": RES}, open(os.path.join(d, "film.json"), "w"))
    for fr in frames:
        loc, R, lens, near = film_pose(fr / FPS)
        o.matrix_world = Matrix.Translation(loc) @ R.to_4x4()
        cd.lens, cd.clip_start = lens, near
        scene.render.filepath = os.path.join(d, f"f{fr:04d}.png")
        t1 = time.time()
        bpy.ops.render.render(write_still=True)
        print(f"film frame {fr} ({fr / FPS:.2f} s) lens {lens:.0f} mm in {time.time() - t1:.1f} s", flush=True)


# self-check: the film starts on the top camera's frame (plan point -> same screen point within 3 px at 1920 wide)
_loc, _R, _lens, _ = film_pose(0)
for _x, _y in ((0.0, 0.0), (34.5, 88.0), (17.25, 44.0)):
    _c = _R.transposed() @ (Vector((_x, _y, 0)) - _loc)
    _sx = 960 + _c.x / -_c.z * _lens / 36 * 1920
    _sy = 540 - _c.y / -_c.z * _lens / 36 * 1920
    assert abs(_sx - (960 + (_y - 44) * 18)) < 3 and abs(_sy - (540 + (_x - 17.25) * 18)) < 3, (_x, _y, _sx, _sy)

if OPTS.get("blend"):
    bpy.ops.wm.save_as_mainfile(filepath=OPTS["blend"])
if OPTS.get("film"):
    spec = str(OPTS.get("frames", f"0,{round(MOVE[0] * FPS)}-{round(MOVE[1] * FPS)}"))
    fr = []
    for part in spec.split(","):
        lo, _, hi = part.partition("-")
        step = 1
        if ":" in hi:
            hi, step = hi.split(":")
        fr += list(range(int(lo), int(hi or lo) + 1, int(step)))
    film(fr)
    SHOTS = []
for s in SHOTS:
    scene.camera = SHOT[s]()
    scene.render.filepath = os.path.join(OUT, f"park_{s}.png")
    t1 = time.time()
    bpy.ops.render.render(write_still=True)
    print(f"rendered {s} in {time.time() - t1:.1f} s -> {scene.render.filepath}")
