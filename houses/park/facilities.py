"""Build the park's CAD-located facilities. All lengths are metres.

The site exporter stores outlines in site coordinates and rotations in degrees.
The type table identifies the safety-perimeter paths in the flattened outlines.
Heights, finishes and details are modelled interpretations of the plan.
"""

from math import atan2, cos, radians, sin, tau

import bpy
from mathutils import Matrix, Quaternion, Vector


def materials() -> dict:
    """Return the reusable Principled materials for these facilities."""
    # Colour pairs are linear RGB. The last entries set texture scale and bump.
    finishes = {
        "green": ((.030, .075, .056), (.051, .123, .088), .34, .32, 90, .0007),
        "teal": ((.045, .192, .178), (.084, .282, .248), .37, .26, 88, .0007),
        "warm": ((.48, .160, .045), (.61, .254, .079), .40, .04, 75, .0014),
        "blue": ((.033, .133, .235), (.069, .219, .310), .40, .03, 83, .0013),
        "cream": ((.58, .52, .37), (.75, .70, .56), .45, .02, 58, .0009),
        "charcoal": ((.028, .037, .035), (.062, .074, .068), .41, .36, 95, .0010),
        "roof": ((.046, .056, .054), (.105, .122, .112), .43, .48, 42, .0012),
        "wood": ((.15, .063, .024), (.34, .181, .084), .53, 0, 5, .0023),
        "wood_light": ((.22, .098, .044), (.42, .240, .122), .51, 0, 5, .0020),
        "steel": ((.42, .48, .49), (.63, .68, .67), .30, .83, 71, .0004),
        "rubber": ((.022, .028, .026), (.061, .068, .062), .73, 0, 75, .0015),
        "stone": ((.25, .23, .20), (.50, .47, .40), .74, 0, 81, .0038),
        "rope": ((.075, .041, .026), (.171, .104, .057), .66, 0, 72, .0012),
    }
    result = {}
    for key, (dark, light, roughness, metallic, scale, bump_size) in finishes.items():
        name = f"Park facilities | {key}"
        mat = bpy.data.materials.get(name)
        if mat is None:
            mat = bpy.data.materials.new(name)
            mat.diffuse_color = (*tuple((a + b) / 2 for a, b in zip(dark, light)), 1)
            nodes = mat.node_tree.nodes
            link = mat.node_tree.links.new
            shader = nodes.get("Principled BSDF")
            shader.inputs["Metallic"].default_value = metallic
            tex = nodes.new("ShaderNodeTexCoord")
            noise = nodes.new("ShaderNodeTexNoise")
            noise.inputs["Scale"].default_value = scale
            noise.inputs["Detail"].default_value = 3.2
            coordinates = tex.outputs["Object"]
            if key.startswith("wood"):
                grain = nodes.new("ShaderNodeVectorMath")
                grain.operation = "MULTIPLY"
                grain.inputs[1].default_value = (.8, 33, 14)
                link(tex.outputs["Generated"], grain.inputs[0])
                coordinates = grain.outputs["Vector"]
            link(coordinates, noise.inputs["Vector"])
            ramp = nodes.new("ShaderNodeValToRGB")
            ramp.color_ramp.elements[0].position = .22
            ramp.color_ramp.elements[0].color = (*dark, 1)
            ramp.color_ramp.elements[1].position = .78
            ramp.color_ramp.elements[1].color = (*light, 1)
            link(noise.outputs["Fac"], ramp.inputs["Fac"])
            link(ramp.outputs["Color"], shader.inputs["Base Color"])
            rough = nodes.new("ShaderNodeMapRange")
            rough.inputs["To Min"].default_value = roughness - .055
            rough.inputs["To Max"].default_value = roughness + .055
            link(noise.outputs["Fac"], rough.inputs["Value"])
            link(rough.outputs["Result"], shader.inputs["Roughness"])
            bump = nodes.new("ShaderNodeBump")
            bump.inputs["Distance"].default_value = bump_size
            bump.inputs["Strength"].default_value = .28
            link(noise.outputs["Fac"], bump.inputs["Height"])
            link(bump.outputs["Normal"], shader.inputs["Normal"])
        result[key] = mat
    return result


def _bounds(points):
    xs, ys = zip(*points)
    return min(xs), max(xs), min(ys), max(ys)


def _cad_bounds(entry, safety_paths):
    x, y = entry["at"]
    angle = radians(entry["rot"])
    c, s = cos(angle), sin(angle)
    return _bounds((c * (u - x) + s * (v - y), -s * (u - x) + c * (v - y))
                   for i, path in enumerate(entry["outline"]) if i not in safety_paths
                   for u, v in path)


class _Facility:
    def __init__(self, index, entry, spec, collection, palette):
        self.index = index
        self.entry = entry
        self.kind = spec["type"]
        self.target = _cad_bounds(entry, set(spec.get("safety_paths", ())))
        self.collection = collection
        self.palette = palette
        self.parts = []
        self.z = .03 if self.kind in _PLAY_TYPES else 0
        self.buried = -.10 - self.z

    def mesh(self, part, vertices, faces, finish, bevel=0, smooth=()):
        points = [Vector(p) for p in vertices]
        center = sum(points, Vector((0, 0, 0))) / len(points)
        name = f"Park {self.index + 1:02d} {self.kind} | {part}"
        mesh = bpy.data.meshes.new(name)
        mesh.from_pydata([p - center for p in points], [], faces)
        mesh.update()
        for index in smooth:
            mesh.polygons[index].use_smooth = True
        obj = bpy.data.objects.new(name, mesh)
        self.collection.objects.link(obj)
        obj.location = center
        obj["facility_index"] = self.index
        obj["facility_block"] = self.entry["block"]
        obj["facility_type"] = self.kind
        mesh.materials.append(self.palette[finish])
        if bevel:
            edge = obj.modifiers.new("Rounded edges", "BEVEL")
            edge.width = bevel
            edge.segments = 2
            edge.harden_normals = True
        self.parts.append(obj)

    def box(self, part, center, size, finish, bevel=.012, rotation=None):
        transform = rotation or Quaternion()
        dx, dy, dz = (d / 2 for d in size)
        corners = [(-dx, -dy), (dx, -dy), (dx, dy), (-dx, dy)]
        vertices = [Vector(center) + transform @ Vector((x, y, z))
                    for z in (-dz, dz) for x, y in corners]
        faces = [(3, 2, 1, 0), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5),
                 (2, 3, 7, 6), (3, 0, 4, 7)]
        self.mesh(part, vertices, faces, finish, min(bevel, min(size) * .29))

    def beam(self, part, a, b, width, depth, finish, bevel=.012):
        a, b = Vector(a), Vector(b)
        direction = b - a
        rotation = Vector((0, 0, 1)).rotation_difference(direction)
        self.box(part, (a + b) / 2, (width, depth, direction.length), finish, bevel, rotation)

    def tube(self, part, points, radius, finish, sides=12, closed=False):
        points = [Vector(p) for p in points]
        last = len(points) - 1
        vertices = []
        tangents = []
        for i, point in enumerate(points):
            before = points[i - 1] if closed or i > 0 else point
            after = points[(i + 1) % len(points)] if closed or i < last else point
            tangents.append((after - before).normalized())
        tangent = tangents[0]
        axis = tangent.cross(Vector((0, 1, 0)) if abs(tangent.y) < .85 else Vector((0, 0, 1)))
        axis.normalize()
        for point, direction in zip(points, tangents):
            axis = tangent.rotation_difference(direction) @ axis
            normal = direction.cross(axis)
            vertices.extend(point + radius * (cos(tau * j / sides) * axis + sin(tau * j / sides) * normal)
                            for j in range(sides))
            tangent = direction
        faces = [(i * sides + j, i * sides + (j + 1) % sides,
                  ((i + 1) % len(points)) * sides + (j + 1) % sides,
                  ((i + 1) % len(points)) * sides + j)
                 for i in range(len(points) if closed else last) for j in range(sides)]
        smooth = range(len(faces))
        if not closed:
            faces.extend((tuple(reversed(range(sides))), tuple(range(last * sides, (last + 1) * sides))))
        self.mesh(part, vertices, faces, finish, smooth=smooth)

    def post(self, part, x, y, top, radius=.051, finish="green"):
        self.tube(part, [(x, y, self.buried), (x, y, top)], radius, finish, sides=16)
        self.tube("post cap", [(x, y, top - .008), (x, y, top + .014)], radius * 1.04, "charcoal", sides=16)

    def lathe(self, part, x, y, rings, finish, sides=32):
        """Revolve a bottom-to-top radius profile. Repeat the first ring to close a hollow shell."""
        vertices = [(x + r * cos(tau * j / sides), y + r * sin(tau * j / sides), z)
                    for z, r in rings for j in range(sides)]
        faces = [(i * sides + j, i * sides + (j + 1) % sides,
                  (i + 1) * sides + (j + 1) % sides, (i + 1) * sides + j)
                 for i in range(len(rings) - 1) for j in range(sides)]
        self.mesh(part, vertices, faces, finish, smooth=range(len(faces)))

    def finish(self):
        """Fit the model's nominal dimensions to the physical CAD extents once."""
        x0, x1, y0, y1 = _bounds((p.co.x + obj.location.x, p.co.y + obj.location.y)
                                  for obj in self.parts for p in obj.data.vertices)
        X0, X1, Y0, Y1 = self.target
        sx, sy = (X1 - X0) / (x1 - x0), (Y1 - Y0) / (y1 - y0)
        ax, ay = self.entry["at"]
        scale = Matrix.Diagonal((sx, sy, 1, 1))
        turn = Matrix.Rotation(radians(self.entry["rot"]), 4, "Z")
        offset = Vector((X0 - sx * x0, Y0 - sy * y0, self.z))
        for obj in self.parts:
            obj.data.transform(scale)
            obj.location = Vector((ax, ay, 0)) + turn @ (scale @ obj.location + offset)
            obj.rotation_euler.z = radians(self.entry["rot"])
        return self.parts


def _shelter(f):
    eaves = 2.42
    for x in (-1.66, 1.66):
        for y in (-1.66, 1.66):
            f.beam("timber column", (x, y, f.buried), (x, y, eaves), .16, .16, "wood")
            f.box("granite pedestal", (x, y, .155), (.33, .33, .35), "stone", .027)
            f.box("metal column shoe", (x, y, .346), (.21, .21, .075), "charcoal")
            f.beam("knee brace", (x, y, 1.88), (x * .73, y, 2.35), .09, .10, "wood")
            f.beam("knee brace", (x, y, 1.88), (x, y * .73, 2.35), .09, .10, "wood")
    for edge in (-1.66, 1.66):
        f.box("outer tie beam", (edge, 0, 2.30), (.17, 3.50, .21), "wood")
        f.box("outer tie beam", (0, edge, 2.30), (3.50, .17, .21), "wood")
    for x in (-1.33, -.89, -.44, 0, .44, .89, 1.33):
        f.box("ceiling joist", (x, 0, 2.43), (.075, 3.54, .12), "wood_light")
    for k in range(4):
        turn = Matrix.Rotation(k * tau / 4, 3, "Z")
        f.box("eave fascia", turn @ Vector((0, -2.07, 2.46)), (4.20, .060, .11), "charcoal",
              rotation=turn.to_quaternion())
        f.beam("hip rafter", turn @ Vector((-2.04, -2.04, 2.49)), (0, 0, 3.44), .095, .075, "wood")
        levels = 10
        for row in range(levels):
            lo, hi = row / levels, (row + 1) / levels
            lower, upper = 2.10 * (1 - lo), 2.10 * (1 - hi)
            upper = max(.025, upper)
            profile = [(-lower, -lower, 2.52 + .96 * lo), (lower, -lower, 2.52 + .96 * lo),
                       (upper, -upper, 2.52 + .96 * hi), (-upper, -upper, 2.52 + .96 * hi)]
            vertices = [turn @ Vector((x, y, z - dz)) for dz in (.032, 0) for x, y, z in profile]
            f.mesh("folded roof course", vertices, [(3, 2, 1, 0), (4, 5, 6, 7), (0, 1, 5, 4),
                   (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)], "roof", .004)
            f.tube("roof course seam", [turn @ Vector((-lower + .01, -lower + .006, 2.519 + .96 * lo)),
                   turn @ Vector((lower - .01, -lower + .006, 2.519 + .96 * lo))], .012, "charcoal")
        f.tube("hip flashing", [turn @ Vector((-2.08, -2.08, 2.542)), (0, 0, 3.495)], .022, "roof")
    f.lathe("ridge cap", 0, 0, [(3.465, .105), (3.53, .083), (3.585, .022), (3.59, .001)], "roof")


def _bench(f):
    for x in (-.739, .739):
        f.tube("bench side frame", [(x, -.309, f.buried), (x, -.309, .42), (x, .21, .42),
               (x, .28, f.buried)], .025, "charcoal", sides=16)
        f.tube("armrest", [(x, -.27, .434), (x, -.25, .633), (x, -.202, .665),
               (x, .225, .665), (x, .282, .602)], .019, "green")
        f.beam("backrest support", (x, .158, .39), (x, .304, .86), .041, .031, "charcoal")
    f.box("long seat rail", (0, -.07, .388), (1.44, .057, .047), "charcoal")
    for i in range(6):
        f.box("seat slat", (0, -.255 + .076 * i, .427), (1.42, .066, .046),
              "wood_light" if i % 3 == 0 else "wood")
    for i in range(5):
        angle = Quaternion((1, 0, 0), radians(73))
        f.box("back slat", (0, .207 + .021 * i, .548 + .067 * i), (1.42, .061, .035),
              "wood_light" if i == 3 else "wood", rotation=angle)


def _picnic_table(f):
    for y in (-.52, .52):
        f.box("cross support", (0, y, .365), (1.94, .10, .09), "charcoal")
        f.box("top support", (0, y, .659), (.84, .087, .09), "charcoal")
        for side in (-1, 1):
            f.beam("table leg", (side * .66, y, f.buried), (side * .27, y, .69), .092, .092, "green")
            f.box("stone seat pedestal", (side * .80, y, .168), (.21, .20, .37), "stone", .026)
    turn = Quaternion((0, 0, 1), tau / 4)
    for side in (-1, 1):
        for i in range(3):
            f.box("picnic seat slat", (side * (.70 + .136 * i), 0, .425),
                  (1.6, .121, .050), "wood", rotation=turn)
    for i in range(6):
        f.box("tabletop slat", (-.38 + .152 * i, 0, .721), (1.60, .143, .051),
              "wood_light" if i in (0, 4) else "wood", rotation=turn)


def _sign(f):
    large = f.kind == "noticeboard"
    width, height, z = (1.404, .79, 1.44) if large else (.603, .45, 1.37)
    for x in (-.57, .57) if large else (0,):
        f.beam("sign post", (x, .065, f.buried), (x, .065, z + .31 if large else z + .03),
               .071, .071, "green")
        f.box("stone post plinth", (x, .07, .022), (.14, .16, .075), "stone")
    f.box("sign frame", (0, 0, z), (width, .058, height), "charcoal")
    f.box("sign face", (0, -.033, z), (width - .050, .017, height - .056), "cream", .007)
    f.box("sign header", (0, -.043, z + height * .34), (width - .068, .007, .076), "green", .002)
    for k in range(3):
        f.box("notice sheet", ((k - 1) * width * .24, -.046, z - .035),
              (width * .18, .004, height * .41), "cream" if k == 2 else "teal", .001)


def _bin(f):
    # Three concentric CAD diameters correspond to bumper, body and opening.
    f.lathe("stone bumper", 0, 0, [(f.buried, .44), (.012, .49), (.052, .50), (.108, .47),
            (.128, .39), (.13, .34)], "stone")
    f.lathe("bin body", 0, 0, [(.06, .34), (.13, .378), (.76, .378), (.79, .36),
            (.79, .33), (.71, .326), (.17, .326)], "green")
    f.lathe("stainless opening", 0, 0, [(.77, .369), (.80, .38), (.84, .359), (.864, .279),
            (.849, .260), (.818, .260), (.79, .292)], "steel")
    f.tube("bin liner", [(0, 0, .15), (0, 0, .19)], .32, "charcoal", sides=32)


def _fountain(f):
    f.box("fountain plinth", (0, .063, .060), (.39, .92, .16), "stone", .025)
    f.box("granite fountain pedestal", (0, .101, .444), (.32, .48, .761), "stone", .035)
    for x, y, z, r in ((-.419, -.14, .77, .183), (.32, -.13, .67, .175), (0, .289, .92, .158)):
        f.tube("basin support", [(0, y * .51, z - .26), (x, y, z - .08)], .038, "steel")
        f.lathe("bowl", x, y, [(z - .098, .045), (z - .065, r * .77), (z, r), (z + .013, r),
                (z + .013, r * .93), (z - .011, r * .8), (z - .054, .040), (z - .054, .001)], "steel")
        f.tube("bubbler spout", [(x, y + r * .57, z - .022), (x, y + r * .57, z + .091),
               (x, y + r * .29, z + .118)], .016, "steel")


def _deck(f, x0, x1, y0, y1, height):
    count = max(5, round((y1 - y0) / .12))
    pitch = (y1 - y0) / count
    for row in range(count):
        f.box("deck plank", ((x0 + x1) / 2, y0 + pitch * (row + .5), height - .026),
              (x1 - x0, pitch - .007, .052), "wood_light" if row % 5 == 0 else "wood", .008)
    for x in (x0 + .09, x1 - .09):
        f.box("deck joist", (x, (y0 + y1) / 2, height - .11), (.092, y1 - y0, .13), "green")


def _guard(f, start, end, height, finish="teal", panel=False):
    a, b = Vector((*start, height)), Vector((*end, height))
    f.tube("guard top rail", [a, b], .033, finish)
    if panel:
        angle = Quaternion((0, 0, 1), atan2(b.y - a.y, b.x - a.x))
        f.box("HDPE infill", (a + b) / 2 - Vector((0, 0, .30)), ((b - a).length, .045, .43),
              finish, .017, angle)
    else:
        f.tube("guard bottom rail", [a - Vector((0, 0, .59)), b - Vector((0, 0, .59))], .023, finish)
        count = max(2, round((a - b).length / .135))
        for k in range(1, count):
            p = a.lerp(b, k / count)
            f.tube("guard baluster", [p - Vector((0, 0, .59)), p], .015, finish, sides=8)


def _steps(f, a, b, width, top, count, style="stairs"):
    """Place a stair, rung ladder or rope climber between plan anchors."""
    a, b = Vector(a), Vector(b)
    across = Vector((-(b - a).y, (b - a).x)).normalized()
    angle = Quaternion((0, 0, 1), atan2(across.y, across.x))

    def point(t, w, lift=0):
        p = a.lerp(b, t) + across * w
        return Vector((p.x, p.y, .065 + (top - .065) * t + lift))

    for side in (-1, 1):
        w = side * width / 2
        f.tube("climber side rail", [point(0, w, -.08), point(1, w)], .038, "green")
        if style != "net":
            f.tube("climber handrail", [point(0, w, .55), point(1, w, .55)], .028, "teal")
            for t in (0, .55, 1):
                f.tube("handrail upright", [point(t, w), point(t, w, .55)], .022, "green")
    for row in range(1, count + 1):
        t = row / (count + 1)
        p, q = point(t, -width / 2), point(t, width / 2)
        if style == "stairs":
            f.box("wooden stair tread", (p + q) / 2, (width, .14, .055), "wood", .008, angle)
        else:
            f.tube("rope rung" if style == "net" else "ladder rung", [p, q], .019,
                   "rope" if style == "net" else "steel")
    if style == "net":
        for i in (-.32, -.16, 0, .16, .32):
            f.tube("vertical climbing rope", [point(0, i * width), point(.5, i * width, -.055),
                   point(1, i * width)], .018, "rope")
    for side in (-1, 1):
        base = point(0, side * width / 2)
        f.tube("buried climber foot", [(base.x, base.y, f.buried), (base.x, base.y, .15)], .043, "green")


def _slide(f, start, end, width, top, finish="steel", rim="teal"):
    start, end = Vector(start), Vector(end)
    across = Vector((-(end - start).y, (end - start).x)).normalized()

    def point(t, across_ratio, wall=0):
        # The chute begins flat at the deck, slopes down, and levels at the run-out.
        eased = max(0, min(1, (t - .05) / .87))
        drop = eased * eased * (3 - 2 * eased)
        p = start.lerp(end, t) + across * (width / 2 * across_ratio)
        return (p.x, p.y, top * (1 - drop) + .225 * drop + wall)

    count = 26
    ts = [k / count for k in range(count + 1)]
    cross_section = [(-1, .103), (-.89, .022), (-.71, 0), (.71, 0), (.89, .022), (1, .103)]
    vertices = [point(t, q, z) for t in ts for q, z in cross_section]
    columns = len(cross_section)
    # Accommodate both sides of the thin formed metal lip in the solid mesh.
    faces = [(i * columns + j, (i + 1) * columns + j, (i + 1) * columns + j + 1, i * columns + j + 1)
             for i in range(count) for j in range(columns - 1)]
    f.mesh("formed slide chute", vertices, faces, finish, smooth=range(len(faces)))
    solid = f.parts[-1].modifiers.new("Sheet thickness", "SOLIDIFY")
    solid.thickness = .016
    for side in (-1, 1):
        f.tube("slide rolled edge", [point(t, side, .104) for t in ts], .024, rim)
        f.tube("slide exit foot", [point(.91, side * .73, f.buried - .225), point(.91, side * .73)], .034, "green")
        f.tube("slide entrance rail", [point(0, side, .59), point(.09, side, .55), point(.18, side, .23)], .027, rim)
    f.tube("slide cross support", [point(.63, -.8, -.036), point(.63, .8, -.036)], .036, "green")


def _bridge(f, left, right, y, width, left_height, right_height):
    def height(t):
        return left_height + t * (right_height - left_height) - .114 * sin(tau * t / 2)

    for k in range(16):
        t = (k + .5) / 16
        f.box("bridge slat", (left + t * (right - left), y, height(t) - .030), ((right - left) / 16 - .011, width, .065),
              "wood_light" if k % 4 == 0 else "wood", .011)
    for side in (-1, 1):
        rail_y = y + side * width / 2
        for lift in (.018, .60):
            f.tube("bridge tension rope", [(left + t * (right - left), rail_y,
                   height(t) + lift) for t in (i / 16 for i in range(17))],
                   .027, "rope")
        for k in range(1, 14):
            t = k / 14
            z = height(t)
            x = left + t * (right - left)
            f.tube("bridge rope baluster", [(x, rail_y, z), (x, rail_y, z + .6)], .013, "rope", sides=8)


def _barrel_roof(f, x, y0, y1, width, z):
    # Curved transverse section carried on six painted ribs.
    for row in range(5):
        lo = y0 + (y1 - y0) * row / 5
        hi = y0 + (y1 - y0) * (row + 1) / 5
        arc = [(x + width * (j / 16 - .5), z + .31 * sin(tau * j / 32)) for j in range(17)]
        vertices = [(u, y, v) for y in (lo + .006, hi - .006) for u, v in arc]
        faces = [(j, j + 1, j + 18, j + 17) for j in range(16)]
        f.mesh("moulded barrel roof panel", vertices, faces, "teal" if row in (0, 4) else "blue",
               smooth=range(16))
        roof = f.parts[-1].modifiers.new("Canopy wall thickness", "SOLIDIFY")
        roof.thickness = .033
        f.tube("barrel roof rib", [(u, lo, v) for u, v in arc], .025, "green")
    f.tube("barrel roof end rib", [(u, y1, v) for u, v in arc], .025, "green")


def _play_a(f):
    h, west_h = 1.28, .98
    _deck(f, -3.55, -1.57, -.67, .35, west_h)
    _deck(f, .43, 1.49, -.69, 1.36, h)
    _deck(f, 1.49, 2.53, -1.95, .52, h)
    for x, y in ((-3.55, -.16), (-3.55, .85), (-2.57, -.67), (-2.57, 1.32), (-1.56, -.63), (-1.56, .36),
                 (.43, -.68), (.43, 1.36), (1.47, 1.36), (1.47, .54), (2.50, .54),
                 (2.50, -1.95), (1.47, -1.95)):
        f.post("play tower post", x, y, 2.75 if x > 1.4 and y < .65 else 2.08)
    _bridge(f, -1.56, .43, -.15, .76, west_h, h)
    for a, b, finish, panel in (
        ((-3.55, -.16), (-2.57, -.67), "blue", True), ((-3.55, .35), (-2.57, .35), "teal", False),
        ((.43, 1.36), (1.47, 1.36), "teal", False), ((.43, -.69), (1.47, -.69), "blue", True),
        ((2.50, -.35), (2.50, -1.95), "warm", True), ((1.48, -1.95), (2.50, -1.95), "teal", False),
    ):
        _guard(f, a, b, (west_h if a[0] < 0 else h) + .69, finish, panel)
    _barrel_roof(f, 1.93, -1.95, .62, 1.18, 2.66)
    f.box("east slide boarding plate", (2.57, -.13, h - .035), (.29, .56, .07), "wood")
    f.box("west slide boarding plate", (-3.46, .31, west_h - .035), (.29, .49, .07), "wood")
    _slide(f, (2.69, -.13), (4.91, 1.76), .53, h, rim="teal")
    _slide(f, (-3.55, .31), (-4.86, .31), .46, west_h, finish="warm", rim="warm")
    _steps(f, (.96, -2.927), (.96, -.70), .95, h, 9)
    _steps(f, (-2.04, -1.27), (-2.04, -.62), .81, west_h, 5, "ladder")
    _steps(f, (-3.45, 1.82), (-2.84, .36), .87, west_h, 7, "net")
    f.tube("balance trapeze", [(.74, 1.44, 1.94), (.74, 1.82, 1.67), (.98, 1.97, 1.56),
           (1.18, 1.82, 1.67), (1.18, 1.44, 1.94)], .024, "green")


def _play_b(f):
    h = 1.36
    _deck(f, -1.78, .33, -.83, 1.24, h)
    for x, y in ((-1.78, -.83), (-1.78, 1.24), (-1.05, 1.24), (.31, 1.25), (.31, -.83)):
        f.post("play tower post", x, y, 2.75 if x >= -1.05 else 2.15)
    for x in (-1.04, .31):
        f.post("roof entrance post", x, .04, 2.75)
    _barrel_roof(f, -.26, .04, 1.36, 1.65, 2.67)
    for a, b, finish, panel in (
        ((-1.78, -.83), (-.72, -.83), "warm", True), ((.31, -.83), (.31, .28), "blue", False),
        ((-.78, 1.24), (.31, 1.24), "teal", False), ((-1.78, .24), (-1.78, 1.24), "teal", False),
    ):
        _guard(f, a, b, h + .69, finish, panel)
    _steps(f, (-.21, -1.86), (-.21, -.81), .90, h, 7)
    _steps(f, (-2.12, -.31), (-1.76, -.31), .95, h, 6, "net")
    _steps(f, (-1.27, 1.86), (-1.27, 1.21), .80, h, 5, "ladder")
    f.box("slide boarding plate", (.44, .67, h - .036), (.32, .55, .072), "wood")
    _slide(f, (.59, .67), (2.60, .67), .52, h, rim="warm")


def _chain(f, x, y, top, bottom):
    for i in range(23):
        z = top - (.5 + i) * (top - bottom) / 23
        points = [(x + (.019 * cos(tau * j / 12) if i % 2 else 0),
                   y + (.019 * cos(tau * j / 12) if not i % 2 else 0),
                   z + .047 * sin(tau * j / 12)) for j in range(12)]
        f.tube("swing chain link", points, .005, "steel", sides=6, closed=True)


def _swing(f):
    for side in (-1, 1):
        y = side * 1.76
        for x in (-.70, .70):
            f.tube("A-frame steel leg", [(x, y, f.buried), (.51 * x, y, 1.30), (0, y * .94, 2.41)],
                   .057, "green", sides=16)
            f.box("swing footing cover", (x, y, .025), (.25, .26, .060), "charcoal")
        f.tube("A-frame tie", [(-.50, y, .97), (.50, y, .97)], .035, "teal")
    f.tube("swing crossbeam", [(0, -1.84, 2.42), (0, 1.84, 2.42)], .070, "teal", sides=20)
    for y in (-.59, .59):
        f.box("rubber belt seat", (0, y, .53), (.22, .49, .038), "rubber", .017)
        for edge in (y - .21, y + .21):
            _chain(f, 0, edge, 2.36, .54)
            f.tube("seat chain hanger", [(0, edge - .025, 2.363), (0, edge + .025, 2.363)], .046, "steel")
    # The CAD shows U-shaped side barriers on either side of the swing frame.
    for side in (-1, 1):
        outer, inner = side * 3.42, side * 1.72
        for x, y in ((outer, -1.76), (outer, 0), (outer, 1.76), (inner, -1.76), (inner, 1.76)):
            f.post("side barrier post", x, y, .91, radius=.052)
        for z in (.45, .82):
            f.tube("side barrier rail", [(inner, -1.76, z), (outer, -1.76, z),
                   (outer, 1.76, z), (inner, 1.76, z)], .032, "green")
        for y in (-1.76, 1.76):
            for i in range(1, 10):
                x = outer + (inner - outer) * i / 10
                f.tube("barrier baluster", [(x, y, .45), (x, y, .82)], .013, "green", sides=8)
        for i in range(-10, 11):
            if i:
                y = i * .155
                f.tube("barrier baluster", [(outer, y, .45), (outer, y, .82)], .013, "green", sides=8)


def _rider(f):
    finish = "warm" if f.index % 2 == 0 else "blue"
    f.tube("spring base flange", [(0, 0, f.buried), (0, 0, .055)], .139, "charcoal", sides=20)
    points = [(.085 * cos(tau * i / 16), .085 * sin(tau * i / 16), .060 + .40 * i / 112)
              for i in range(113)]
    f.tube("spring coil", points, .020, "teal")
    f.tube("spring upper mount", [(0, 0, .423), (0, 0, .50)], .113, "charcoal")
    side = [(-.31, .57), (-.28, .67), (-.17, .68), (-.14, .58), (.059, .58), (.121, .810),
            (.221, .88), (.30, .82), (.30, .72), (.23, .66), (.178, .48), (-.205, .46)]
    length = len(side)
    for sign in (-1, 1):
        x = sign * .106
        vertices = [(x + dx, y, z) for dx in (-.021, .021) for y, z in side]
        faces = [tuple(reversed(range(length))), tuple(range(length, 2 * length))]
        faces += [(i, (i + 1) % length, (i + 1) % length + length, i + length) for i in range(length)]
        f.mesh("sculpted HDPE rider side", vertices, [tuple(reversed(face)) for face in faces], finish, .012)
        x = sign * .222
        f.tube("rider side frame", [(x, -.328, .53), (x, -.186, .49), (x, .13, .49), (x, .296, .69)],
               .013, "charcoal")
        for y, z in ((-.17, .49), (.19, .56)):
            f.tube("rider side frame stay", [(sign * .11, y, z), (x, y, z)], .018, "steel")
    f.box("rider seat", (0, -.103, .595), (.239, .198, .048), "rubber", .018)
    for y, z in ((.22, .744), (.005, .494)):
        f.tube("rider handle axle", [(-.225, y, z), (.225, y, z)], .022, "steel")
        for side in (-1, 1):
            f.tube("rubber rider grip", [(side * .144, y, z), (side * .225, y, z)], .028, "rubber")


def _seesaw(f):
    for y in (-.078, .078):
        f.post("seesaw pivot stand", 0, y, .49, radius=.070)
    f.tube("seesaw pivot pin", [(0, -.15, .46), (0, .15, .46)], .032, "steel")
    f.box("seesaw beam", (0, 0, .46), (2.78, .114, .12), "teal", .022)
    for side, finish in ((-1, "warm"), (1, "blue")):
        f.box("seesaw moulded seat", (side * 1.225, 0, .56), (.43, .27, .084), finish, .024)
        f.tube("seesaw handle stem", [(side * .95, 0, .48), (side * .95, 0, .80)], .029, "green")
        f.tube("seesaw handlebar", [(side * .95, -.226, .80), (side * .95, .226, .80)], .024, "steel")
        for k in (-1, 1):
            f.tube("rubber handgrip", [(side * .95, k * .146, .80), (side * .95, k * .226, .80)], .031, "rubber")
        f.tube("seesaw rubber bumper", [(side * 1.22, 0, .295), (side * 1.22, 0, .43)], .044, "rubber")


def _fitness_plate(f, center, length, depth, finish):
    x, y, z = center
    f.box("moulded foot pad", center, (length, depth, .066), finish, .028)
    for i in range(-3, 4):
        f.box("pedal grip rib", (x + i * length / 9, y, z + .037),
              (.010, depth * .74, .007), "rubber", .002)


def _fitness_grips(f):
    f.post("fitness centre column", 0, 0, 1.29, radius=.071)
    for x in (-1, 1):
        for y in (-1, 1):
            end = (x * .553, y * .382, 1.22)
            f.tube("splayed handrail", [(0, y * .09, 1.17), (x * .149, y * .156, 1.20), end], .021, "teal")
            f.tube("handover grip", [(x * .405, y * .302, 1.214), end], .025, "rubber")


def _arm_wheels(f):
    for x in (-.459, .432):
        f.post("arm wheel upright", x, .38, 1.46, radius=.030)
    f.tube("wheel mounting crossbar", [(-.510, .331, 1.44), (.487, .331, 1.44)], .022, "green")
    for x in (-.329, .291):
        y, z = -.225, 1.31
        f.tube("arm wheel bracket", [(x, .348, 1.42), (x, y, 1.22), (x, y, z)], .033, "green")
        points = [(x + .250 * cos(tau * k / 40), y + .172 * sin(tau * k / 40),
                   z + .100 * sin(tau * k / 40)) for k in range(40)]
        f.tube("turning wheel rim", points, .022, "teal", closed=True)
        for k in (0, 10, 20, 30):
            f.tube("wheel spoke", [(x, y, z), points[k]], .015, "teal")
        f.tube("arm wheel hub", [(x, y, z - .04), (x, y, z + .040)], .049, "steel")
        u, v, w = points[29]
        f.tube("wheel spinner knob", [(u, v, w), (u, v, w + .081)], .022, "rubber")
    f.box("instruction plaque", (0, .064, 1.49), (.37, .20, .035), "cream", .007,
          Quaternion((1, 0, 0), radians(30)))


def _waist_swing(f):
    _fitness_grips(f)
    for side in (-1, 1):
        f.tube("waist swing pivot", [(0, 0, .68), (side * .18, 0, .68)], .047, "steel")
        f.tube("waist swing rocker", [(side * .13, 0, .68), (side * .28, 0, .20),
               (side * .55, 0, .15)], .049, "green")
        _fitness_plate(f, (side * .523, 0, .20), .39, .25, "teal")


def _exercise_cycle(f):
    for x in (-.37, .38):
        f.tube("exercise cycle foot", [(x, -.141, .025), (x, .141, .025)], .032, "charcoal")
    f.tube("cycle frame", [(-.37, 0, f.buried), (-.37, 0, .12), (.37, 0, .12), (.37, 0, f.buried)],
           .046, "green")
    f.tube("cycle handle column", [(-.40, 0, .13), (-.42, 0, .98), (-.48, 0, 1.13)], .044, "green")
    f.tube("cycle handlebars", [(-.46, -.178, 1.17), (-.394, -.125, 1.15), (-.394, .125, 1.15),
           (-.46, .178, 1.17)], .023, "rubber")
    f.tube("cycle saddle stem", [(.24, 0, .12), (.32, 0, .83)], .037, "steel")
    f.box("cycle saddle", (.335, 0, .86), (.34, .31, .07), "rubber", .031)
    f.tube("enclosed pedal crank", [(-.16, -.061, .34), (-.16, .061, .34)], .152, "teal", sides=24)
    for side in (-1, 1):
        f.tube("pedal crank", [(-.16, side * .112, .34), (-.16 + side * .122, side * .112, .36)], .020, "steel")
        f.box("cycle pedal", (-.16 + side * .122, side * .128, .36), (.12, .087, .038), "rubber", .009)


def _waist_twist(f):
    _fitness_grips(f)
    f.tube("disc support frame", [(-.425, 0, .075), (.425, 0, .075)], .043, "green")
    for x in (-.437, .437):
        f.tube("buried disc spindle", [(x, 0, f.buried), (x, 0, .13)], .052, "steel")
        f.tube("waist twist disc", [(x, 0, .12), (x, 0, .153)], .164, "teal", sides=32)
        for y in (-.08, -.04, 0, .04, .08):
            f.box("disc grip rib", (x, y, .155), (.196, .010, .005), "rubber", .001)


def _exercise_stepper(f):
    _fitness_grips(f)
    for side in (-1, 1):
        for fore in (-1, 1):
            y, z = fore * .188, .21 + .12 * (side == fore)
            f.tube("stepper crank arm", [(side * .115, y * .63, .43), (side * .435, y, z)], .042, "green")
            _fitness_plate(f, (side * .435, y, z), .37, .162, "teal")


_PLAY_TYPES = {"play_towers_a", "play_tower_b", "double_swing", "spring_rider", "seesaw",
               "arm_wheels", "waist_swing", "exercise_cycle", "waist_twist", "exercise_stepper"}

_BUILDERS = {
    "hip_roof_shelter": _shelter,
    "backed_bench": _bench,
    "picnic_table": _picnic_table,
    "information_sign": _sign,
    "noticeboard": _sign,
    "round_bin": _bin,
    "drinking_fountain": _fountain,
    "play_towers_a": _play_a,
    "play_tower_b": _play_b,
    "double_swing": _swing,
    "spring_rider": _rider,
    "seesaw": _seesaw,
    "arm_wheels": _arm_wheels,
    "waist_swing": _waist_swing,
    "exercise_cycle": _exercise_cycle,
    "waist_twist": _waist_twist,
    "exercise_stepper": _exercise_stepper,
}


def build(site: dict, types: dict, collection) -> list:
    """Add every CAD insert to the given collection and return its mesh parts.

    The two slatted picnic tables are already facility inserts. The rest-area
    floor and the marked safety zones belong to the site drawing.
    """
    inserts = site["facilities"]
    specs = [types[entry["block"]] for entry in inserts]
    builders = [_BUILDERS[spec["type"]] for spec in specs]
    palette = materials()
    objects = []
    for index, (entry, spec, builder) in enumerate(zip(inserts, specs, builders)):
        facility = _Facility(index, entry, spec, collection, palette)
        builder(facility)
        objects.extend(facility.finish())
    return objects
