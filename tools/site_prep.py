"""Clean a landscape drawing set (DXF sheets + an external base reference) into one site file before any 3D.

A landscape sheet set splits one site over several sheets (planting, facilities, paving) that all reference one base
drawing (an xref), and carries notes a model must not see: texts, leaders, dimensions, title blocks, quantity tables,
the lines that tie a group of trees to its label, and sometimes a second copy of the plan beside the first. This tool
merges the sheets and the base, keeps only what lies inside the site window, drops the notes, sorts what is left into
classes (tree, shrub, curb, paving, facility ...) by layer, reads each tree's species and size from the sheet's own
quantity table, and writes metres in a local frame (origin at the window's lower-left corner):

  python tools/site_prep.py site.config.json --out site.json --svg plan.svg --png preview.png

site.json: trees (species, size, height, crown, position), areas (closed outlines and hatches per class), lines
(open polylines per class), facilities (block inserts with their footprint and outline). plan.svg: the cleaned
linework per class (for a 2D drawing scene), preview.png: the same, coloured per class. The config keys are described
in houses/park/README.md; texts never reach any output, so a site name on the sheet cannot leak into a film.
"""
import argparse
import fnmatch
import json
import math
import re
import sys
from collections import Counter

NOTE_TYPES = {"TEXT", "MTEXT", "DIMENSION", "LEADER", "MULTILEADER", "ATTRIB", "ATTDEF", "VIEWPORT", "POINT", "TOLERANCE"}
COLOURS = {"tree": (40, 140, 40), "shrub": (70, 170, 90), "groundcover": (120, 190, 110), "lawn": (150, 200, 120),
           "curb": (60, 60, 200), "paving": (130, 130, 130), "facility": (0, 130, 255), "boundary": (0, 0, 0)}


def area(pts):
    """Shoelace area of a closed outline, square metres."""
    return abs(sum(pts[i][0] * pts[i - 1][1] - pts[i - 1][0] * pts[i][1] for i in range(len(pts)))) / 2


def match(name, pats):
    return any(fnmatch.fnmatchcase(name, p) for p in pats)


def size_h(size):
    """'H3.5xR12' -> 3.5 (metres); None when the size has no height."""
    m = re.search(r"H\s*([\d.]+)", size or "", re.I)
    return float(m.group(1)) if m else None


def size_key(size):
    """Order two sizes of one species: height, then root collar / breast diameter (R, B), then width."""
    num = lambda k: float(m.group(1)) if (m := re.search(k + r"\s*([\d.]+)", size or "", re.I)) else 0.0
    return (num("H"), max(num("R"), num("B")), num("W"))


class Site:
    def __init__(self, cfg, root):
        import ezdxf
        from ezdxf.math import Matrix44

        self.ezdxf, self.M44 = ezdxf, Matrix44
        self.cfg = cfg
        self.root = root
        x0, y0, x1, y1 = cfg["window"]
        self.win = (x0, y0, x1, y1)
        self.drop_layers = cfg.get("drop_layers", [])
        self.classes = cfg["classes"]
        self.docs = {}
        self.items = []  # (class, entity-kind, data)
        self.k = None

    def doc(self, name):
        if name not in self.docs:
            d = self.ezdxf.readfile(f"{self.root}/{name}")
            k = {4: 0.001, 5: 0.01, 6: 1.0}.get(d.header.get("$INSUNITS", 0)) or self.cfg.get("unit")
            if not k:
                sys.exit(f"{name}: $INSUNITS not set, put \"unit\" (metres per drawing unit) in the config")
            if self.k and abs(k - self.k) > 1e-12:
                sys.exit(f"{name}: units differ from the other sheets")
            self.k = k
            self.docs[name] = d
        return self.docs[name]

    def classify(self, layer, block, sheet):
        for c in self.classes:
            if block and match(block, c.get("blocks", [])):
                return c["name"]
            if match(layer, c.get("layers", [])) and (not c.get("sheets") or sheet in c["sheets"]):
                return c["name"]
        return None

    def inside(self, pts):
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        cx, cy = (min(xs) + max(xs)) / 2 * self.k, (min(ys) + max(ys)) / 2 * self.k
        x0, y0, x1, y1 = self.win
        return x0 <= cx <= x1 and y0 <= cy <= y1

    def within(self, pts, margin=1.0):
        x0, y0, x1, y1 = self.win
        return all(x0 - margin <= p[0] * self.k <= x1 + margin and y0 - margin <= p[1] * self.k <= y1 + margin for p in pts)

    def local(self, p):
        return [round(p[0] * self.k - self.win[0], 3), round(p[1] * self.k - self.win[1], 3)]

    def simple(self, pts, closed):
        """Local metres to 1 cm, dropping points that sit within 1 cm of the line through their neighbours."""
        import cv2
        import numpy as np

        a = np.array([[p[0] * self.k - self.win[0], p[1] * self.k - self.win[1]] for p in pts], np.float32)
        if len(a) > 2:
            a = cv2.approxPolyDP(a.reshape(-1, 1, 2), 0.01, closed).reshape(-1, 2)
        return [[round(float(x), 2), round(float(y), 2)] for x, y in a]

    def paths(self, e, m):
        from ezdxf import path as P

        try:
            ps = list(P.from_hatch(e)) if e.dxftype() == "HATCH" else [P.make_path(e)]
        except (TypeError, ValueError, AttributeError):
            return []
        out = []
        for p in ps:
            if m is not None:
                p = p.transform(m)
            if len(p):
                pts = [(v.x, v.y) for v in p.flattening(0.02 / self.k)]
                if len(pts) >= 2:
                    out.append(pts)
        return out

    def walk(self, es, sheet, m, parent_layer=None):
        for e in es:
            t = e.dxftype()
            lay = e.dxf.get("layer", "0")
            if lay == "0" and parent_layer:
                lay = parent_layer
            if t in NOTE_TYPES or match(lay, self.drop_layers):
                continue
            if t == "INSERT":
                name = e.dxf.name
                xref = self.cfg.get("xref", {}).get(name)
                if xref:
                    mm = self.M44.chain(e.matrix44(), m) if m is not None else e.matrix44()
                    if sheet == self.cfg.get("xref_from", sheet):
                        self.walk(self.doc(xref).modelspace(), "xref", mm)
                    continue
                cls = self.classify(lay, name, sheet)
                if cls in ("tree", "facility"):
                    self.add_insert(e, cls, sheet, m, lay)
                    continue
                try:
                    subs = list(e.virtual_entities())
                except Exception:
                    continue
                self.walk(subs, sheet, m, lay)
                continue
            cls = self.classify(lay, None, sheet)
            if not cls:
                continue
            closed = t in ("HATCH", "SOLID", "CIRCLE", "ELLIPSE") or bool(getattr(e, "closed", False)) \
                or bool(getattr(e, "is_closed", False))
            for pts in self.paths(e, m):
                if not self.inside(pts):
                    continue
                c = closed or math.dist(pts[0], pts[-1]) * self.k < 0.01
                if not c and not self.within(pts):  # an open line running out of the site is a leader to a note
                    continue
                self.items.append((cls, "area" if c else "line", {"layer": lay, "sheet": sheet, "hatch": t == "HATCH",
                                                                 "pts": self.simple(pts, c)}))

    def add_insert(self, e, cls, sheet, m, lay):
        from ezdxf import bbox

        from ezdxf.math import Vec3

        try:  # a block's base point can lie far from what it draws: place and clip by the drawn extents
            b = bbox.extents([e], fast=True)
            c = Vec3((b.extmin.x + b.extmax.x) / 2, (b.extmin.y + b.extmax.y) / 2, 0) if b.has_data else e.dxf.insert
        except Exception:
            c = e.dxf.insert
        p = m.transform(c) if m is not None else c
        if not self.inside([(p.x, p.y)]):
            return
        outline = []

        def parts(ins):  # nested blocks (a play structure made of a deck block, a slide block ...) opened all the way
            for v in ins.virtual_entities():
                if v.dxftype() == "INSERT":
                    yield from parts(v)
                elif v.dxftype() not in NOTE_TYPES:
                    yield v

        try:
            for v in parts(e):
                for pts in self.paths(v, m):  # a part under 5 cm (pattern dots, hatch specks) is not an outline
                    xs, ys = [q[0] for q in pts], [q[1] for q in pts]
                    if max(max(xs) - min(xs), max(ys) - min(ys)) * self.k >= 0.05:
                        outline.append(self.simple(pts, False))
            b = bbox.extents([e], fast=True)
            w, d = b.size.x * self.k, b.size.y * self.k
        except Exception:
            w = d = 0.0
        self.items.append((cls, "insert", {"layer": lay, "sheet": sheet, "block": e.dxf.name, "at": self.local((p.x, p.y)),
                                           "rot": round(e.dxf.rotation, 2), "w": round(w, 3), "d": round(d, 3),
                                           "outline": outline}))

    def legend(self):
        """Quantity-table rows: symbol block -> [(name, size, count)], from the configured legend layer."""
        lg = self.cfg.get("legend")
        if not lg:
            return {}
        msp = self.doc(lg["sheet"]).modelspace()
        syms, texts = [], []
        for e in msp.query(f'*[layer=="{lg["layer"]}"]'):
            if e.dxftype() == "INSERT":
                syms.append((e.dxf.insert[1], e.dxf.name))
            elif e.dxftype() == "TEXT":
                texts.append((e.dxf.insert[1], e.dxf.insert[0], e.dxf.text.strip()))
        row_h = lg.get("row_h", 1.0) / self.k
        rows = []  # texts on one baseline (within 3 cm): a category cell spanning rows sits a little off it
        for y, x, s in sorted(texts):
            if rows and abs(rows[-1][-1][1] - y) < 0.03 / self.k:
                rows[-1].append((x, y, s))
            else:
                rows.append([(x, y, s)])
        table = []
        for cells in rows:
            cells.sort()
            txt = [c[2] for c in cells]
            cnt = next((t for t in reversed(txt) if re.fullmatch(r"[\d,]+", t)), None)
            if len(txt) >= 3 and cnt and not txt[0].endswith("합계"):
                y = cells[0][1]
                sym = min(syms, key=lambda s: abs(s[0] - y), default=None)
                blk = sym[1] if sym and abs(sym[0] - y) < row_h * 0.6 else None
                table.append({"name": txt[0], "size": txt[1], "count": int(cnt.replace(",", "")), "block": blk, "y": y})
        for row in table:  # rows the sheet left without a symbol take one from the config, with its reason
            ov = lg.get("blocks", {}).get(f'{row["name"]} {row["size"]}')
            if ov:
                row["block"] = ov["block"]
        out = {}
        for row in table:
            if row["block"]:
                out.setdefault(row["block"], []).append(row)
        return out

    def run(self):
        for sheet in self.cfg["sheets"]:
            self.walk(self.doc(sheet).modelspace(), sheet, None)
        trees, facilities, areas, lines = [], [], {}, {}
        legend = self.legend()
        for cls, kind, d in self.items:
            if kind == "insert" and cls == "tree":
                rows = legend.get(d["block"], [])
                crown = round((d["w"] + d["d"]) / 2, 2)
                if len(rows) > 1:  # one symbol, two sizes: the drawn crown picks the row (bigger crown, taller tree)
                    rows = sorted(rows, key=lambda r: size_key(r["size"]))
                    cut = self.cfg.get("legend", {}).get("crown_split", {}).get(d["block"])
                    row = rows[-1] if cut and crown >= cut else rows[0]
                else:
                    row = rows[0] if rows else {"name": None, "size": None}
                trees.append({"species": row["name"], "size": row["size"], "h": size_h(row["size"]), "crown": crown,
                              "x": d["at"][0], "y": d["at"][1], "rot": d["rot"], "block": d["block"]})
            elif kind == "insert":
                facilities.append({k: d[k] for k in ("block", "layer", "sheet", "at", "rot", "w", "d", "outline")})
            elif kind == "area":
                areas.setdefault(cls, []).append({k: d[k] for k in ("layer", "sheet", "hatch", "pts")})
            else:
                lines.setdefault(cls, []).append({k: d[k] for k in ("layer", "sheet", "pts")})
        areas = {c: dedupe(v) for c, v in areas.items()}
        lines = {c: dedupe(v) for c, v in lines.items()}
        x0, y0, x1, y1 = self.win
        check = {}
        if legend:
            want = Counter()
            for rows in legend.values():
                for r in rows:
                    want[f'{r["name"]} {r["size"]}'] += r["count"]
            got = Counter(f'{t["species"]} {t["size"]}' for t in trees)
            check = {"legend": dict(want), "drawn": dict(got),
                     "differ": {k: [want.get(k, 0), got.get(k, 0)] for k in set(want) | set(got) if want.get(k, 0) != got.get(k, 0)}}
        return {"window_m": self.win, "size_m": [round(x1 - x0, 3), round(y1 - y0, 3)],
                "counts": {"trees": len(trees), "facilities": len(facilities),
                           **{f"areas.{c}": len(v) for c, v in areas.items()}, **{f"lines.{c}": len(v) for c, v in lines.items()}},
                "trees_by_species": dict(Counter(t["species"] for t in trees).most_common()),
                "check": check, "trees": trees, "facilities": facilities, "areas": areas, "lines": lines}


def zones(site, cfg, res=20):
    """Split the site into the regions its edge lines close off (curbs, the boundary) and say what each one holds.

    A sheet set rarely draws "this is a bed, this is a walk" as one closed outline per surface: the edges are curb
    lines and the surfaces are implied. Each region gets: its outline (metres), area, the trees and planting masses
    inside, and how much of it the hatches of `zones.hatch_classes` cover. kind: "planted" (trees or planting
    masses), "hatched" (mostly covered by a hatch, e.g. a paving plan's paved areas) or "open" (neither).
    """
    import cv2
    import numpy as np

    zc = cfg.get("zones", {})
    w, h = site["size_m"]
    q = lambda pts: np.array([[p[0] * res, (h - p[1]) * res] for p in pts], np.int32)
    gw, gh = int(w * res) + 1, int(h * res) + 1
    inside = np.zeros((gh, gw), np.uint8)
    bounds = site["areas"].get(zc.get("boundary_class", "boundary"), [])
    if not bounds:
        return []
    bd = max(bounds, key=lambda a: area(a["pts"]))["pts"]
    cv2.fillPoly(inside, [q(bd)], 1)
    wall = np.zeros_like(inside)
    for c in zc.get("edge_classes", ["curb"]):
        for a in site["areas"].get(c, []):
            cv2.polylines(wall, [q(a["pts"])], True, 1, 3)
        for a in site["lines"].get(c, []):
            cv2.polylines(wall, [q(a["pts"])], False, 1, 3)
    cv2.polylines(wall, [q(bd)], True, 1, 3)
    n, lab = cv2.connectedComponents(((inside == 1) & (wall == 0)).astype(np.uint8), connectivity=4)
    hatch = np.zeros_like(inside)
    for c in zc.get("hatch_classes", ["curb"]):
        for a in site["areas"].get(c, []):
            if a["hatch"]:
                cv2.fillPoly(hatch, [q(a["pts"])], 1)
    plant = np.zeros_like(inside)
    for c in zc.get("planting_classes", ["hedge", "shrub", "groundcover"]):
        for a in site["areas"].get(c, []):
            if area(a["pts"]) < area(bd) * 0.5:
                cv2.fillPoly(plant, [q(a["pts"])], 1)
    trees = Counter()
    for t in site["trees"]:
        x, y = int(t["x"] * res), int((h - t["y"]) * res)
        if 0 <= y < gh and 0 <= x < gw:
            trees[int(lab[y, x])] += 1
    out = []
    for i in range(1, n):
        m = lab == i
        px = int(m.sum())
        if px < res * res * 0.5:  # under 0.5 m2: slivers between doubled lines
            continue
        # grow the region over the edge line it was cut by, so neighbouring surfaces meet under the curb
        grown = cv2.dilate(m.astype(np.uint8), np.ones((5, 5), np.uint8)) & inside
        cs, _ = cv2.findContours(grown, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        c = max(cs, key=cv2.contourArea)
        c = cv2.approxPolyDP(c, 0.02 * res, True).reshape(-1, 2)
        hf, pf = float(hatch[m].mean()), float(plant[m].mean())
        kind = "planted" if trees[i] or pf > 0.05 else ("hatched" if hf > 0.5 else "open")
        ys, xs = np.nonzero(m)
        out.append({"id": len(out), "kind": kind, "area_m2": round(px / res / res, 1), "trees": trees[i],
                    "hatch": round(hf, 2), "planting": round(pf, 2),
                    "centre": [round(float(xs.mean()) / res, 2), round(h - float(ys.mean()) / res, 2)],
                    "pts": [[round(float(x) / res, 2), round(h - float(y) / res, 2)] for x, y in c]})
    return out


def dedupe(items):
    """Drop exact repeats (the same outline drawn on two sheets, a hatch and its boundary on one layer)."""
    seen, out = set(), []
    for it in items:
        key = (len(it["pts"]), tuple(round(v, 2) for p in it["pts"][:3] for v in p), tuple(round(v, 2) for v in it["pts"][-1]))
        if key not in seen:
            seen.add(key)
            out.append(it)
    return out


def draw(site, png=None, svg=None, pxm=16):
    import cv2
    import numpy as np

    w, h = site["size_m"]
    W, H = int(w * pxm), int(h * pxm)
    img = np.full((H, W, 3), 255, np.uint8)
    q = lambda pts: np.array([[p[0] * pxm, (h - p[1]) * pxm] for p in pts], np.int32)
    svg_parts = {}
    ss = lambda pts: " ".join(f"{p[0]:.2f},{h - p[1]:.2f}" for p in pts)
    for cls, items in site["areas"].items():
        for it in items:
            col = COLOURS.get(cls, (200, 0, 200))
            cv2.polylines(img, [q(it["pts"])], True, col, 1, cv2.LINE_AA)
            svg_parts.setdefault(cls, []).append(f'<polygon points="{ss(it["pts"])}"/>')
    for cls, items in site["lines"].items():
        for it in items:
            cv2.polylines(img, [q(it["pts"])], False, COLOURS.get(cls, (200, 0, 200)), 1, cv2.LINE_AA)
            svg_parts.setdefault(cls, []).append(f'<polyline points="{ss(it["pts"])}"/>')
    for f in site["facilities"]:
        for pts in f["outline"]:
            cv2.polylines(img, [q(pts)], False, COLOURS["facility"], 1, cv2.LINE_AA)
            svg_parts.setdefault("facility", []).append(f'<polyline points="{ss(pts)}"/>')
    for t in site["trees"]:
        c = (int(t["x"] * pxm), int((h - t["y"]) * pxm))
        cv2.circle(img, c, max(2, int(t["crown"] / 2 * pxm)), COLOURS["tree"] if t["species"] else (0, 0, 255), 1, cv2.LINE_AA)
        svg_parts.setdefault("tree", []).append(f'<circle cx="{t["x"]:.2f}" cy="{h - t["y"]:.2f}" r="{t["crown"] / 2:.2f}"/>')
    if png:
        cv2.imwrite(png, img)
    if svg:
        body = "\n".join(f'<g class="{c}">\n' + "\n".join(v) + "\n</g>" for c, v in svg_parts.items())
        open(svg, "w", encoding="utf-8").write(
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" fill="none" stroke="#000" stroke-width="0.06">\n{body}\n</svg>\n')


def selftest():
    import os
    import tempfile

    import ezdxf

    tmp = tempfile.mkdtemp()
    base = ezdxf.new(units=4)
    base.modelspace().add_lwpolyline([(0, 0), (20000, 0), (20000, 10000), (0, 10000)], close=True, dxfattribs={"layer": "PARK"})
    base.modelspace().add_text("SITE NAME", dxfattribs={"layer": "PARK", "insert": (1000, 1000)})
    base.saveas(os.path.join(tmp, "base.dxf"))
    sh = ezdxf.new(units=4)
    blk = sh.blocks.new("TREE_A")
    blk.add_circle((0, 0), 1500)
    xb = sh.blocks.new("BASE")  # the external reference's stand-in block
    msp = sh.modelspace()
    msp.add_blockref("BASE", (0, 0))
    for x in (5000, 9000, 13000):
        msp.add_blockref("TREE_A", (x, 5000), dxfattribs={"layer": "TREES"})
    msp.add_blockref("TREE_A", (5000, 5000 - 400000), dxfattribs={"layer": "TREES"})  # a second copy far away
    msp.add_lwpolyline([(5000, 5000), (9000, 5000)], dxfattribs={"layer": "TREE_LEADER"})
    msp.add_lwpolyline([(15000, -500), (15000, 10500)], dxfattribs={"layer": "CURB"})  # splits the park in two
    msp.add_blockref("TREE_A", (100000, 3000), dxfattribs={"layer": "QTY"})
    for x, s in ((101000, "Zelkova"), (105000, "H4.0xR15"), (110000, "EA"), (112000, "3")):
        msp.add_text(s, dxfattribs={"layer": "QTY", "insert": (x, 2800)})
    sh.saveas(os.path.join(tmp, "sheet.dxf"))
    cfg = {"sheets": ["sheet.dxf"], "xref": {"BASE": "base.dxf"}, "window": [-1, -1, 21, 11],
           "drop_layers": ["TREE_LEADER", "QTY"], "legend": {"sheet": "sheet.dxf", "layer": "QTY", "row_h": 1.0},
           "classes": [{"name": "tree", "layers": ["TREES"]}, {"name": "boundary", "layers": ["PARK"]},
                       {"name": "curb", "layers": ["CURB"]}]}
    s = Site(cfg, tmp).run()
    assert s["counts"]["trees"] == 3, s["counts"]  # the far copy and the table symbol stay out
    assert s["trees"][0]["species"] == "Zelkova" and s["trees"][0]["h"] == 4.0 and s["trees"][0]["crown"] == 3.0, s["trees"][0]
    assert s["counts"]["areas.boundary"] == 1 and "lines.tree" not in s["counts"]
    assert s["trees"][0]["x"] == 6.0 and s["check"]["differ"] == {}, (s["trees"][0], s["check"])
    assert "SITE NAME" not in json.dumps(s, ensure_ascii=False)
    z = sorted(zones(s, cfg), key=lambda z: z["centre"][0])
    assert [q["kind"] for q in z] == ["planted", "open"] and abs(z[0]["area_m2"] - 15 * 10) < 8, z  # less the edge lines' 15 cm
    assert sum(q["trees"] for q in z) == 3
    print("selftest ok:", s["counts"], "zones", [(q["kind"], q["area_m2"]) for q in z])


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("config", help="site config JSON, or 'selftest'")
    ap.add_argument("--root", help="folder with the DXF files (default: the config's dxf_dir)")
    ap.add_argument("--out", required=False)
    ap.add_argument("--svg")
    ap.add_argument("--png")
    ap.add_argument("--pxm", type=int, default=16, help="preview pixels per metre")
    a = ap.parse_args()
    if a.config == "selftest":
        selftest()
        sys.exit()
    cfg = json.load(open(a.config, encoding="utf-8"))
    site = Site(cfg, a.root or cfg["dxf_dir"]).run()
    site["zones"] = zones(site, cfg)
    if a.out:
        open(a.out, "w", encoding="utf-8").write(json.dumps(site, ensure_ascii=False, separators=(",", ":")))
    draw(site, a.png, a.svg, a.pxm)
    for z in site["zones"]:
        print(f'zone {z["id"]}: {z["kind"]}, {z["area_m2"]} m2, {z["trees"]} trees, hatch {z["hatch"]}, centre {z["centre"]}')
    print(json.dumps({"counts": site["counts"], "trees_by_species": site["trees_by_species"],
                      "check_differ": site["check"].get("differ")}, ensure_ascii=False, indent=1))
