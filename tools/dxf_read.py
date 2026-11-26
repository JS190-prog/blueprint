"""Read a CAD drawing (DXF) into metres, the numbers a house's build.py works from.

A DWG goes through a DWG-to-DXF converter first (ODA File Converter). Coordinates keep the drawing's axes (x right,
y up) and origin, scaled to metres by its $INSUNITS; a unitless drawing needs --unit (metres per drawing unit).

  python tools/dxf_read.py layers plan.dxf
      units, extents and what each layer holds (entity types, block names): find the wall, door, window,
      furniture, planting and paving layers before anything else.
  python tools/dxf_read.py dump plan.dxf --layers A-FURN,A-DOOR --out entities.json
      every entity on those layers in metres: block inserts with their block, point, rotation, scale and footprint;
      closed outlines and hatches as polygons; lines, arcs, circles, texts and dimension values. Doors, windows,
      furniture, trees and paving are read from here into plan.json, not off a picture.
  python tools/dxf_read.py walls plan.dxf --walls A-WALL --seal A-WIND,A-DOOR --out walls.json --debug walls.png
      the wall layers' closed outlines and hatches as one set of non-overlapping rectangles (overlapping wall
      outlines render as black faces), and the floor: everything the walls enclose once the seal layers (window
      frames, door leaves and swings) close the openings. Same shape as houses/apartment/walls.json, 1 cm grid.
  python tools/dxf_read.py selftest
"""
import argparse
import json
import math
import os
import sys

import cv2
import numpy as np

UNITS = {1: 0.0254, 2: 0.3048, 4: 0.001, 5: 0.01, 6: 1.0}  # $INSUNITS -> metres per unit
OUTLINE = ("LWPOLYLINE", "POLYLINE", "HATCH", "SOLID", "CIRCLE", "ELLIPSE", "SPLINE")


def to_rects(m, min_side=3):
    """Exact rectangle cover of a binary mask: runs per row, merged down while the same run repeats."""
    out = []
    live = {}
    for y in range(m.shape[0] + 1):
        runs = set()
        if y < m.shape[0]:
            xs = np.nonzero(m[y])[0]
            if len(xs):
                br = np.nonzero(np.diff(xs) > 1)[0]
                starts = np.r_[xs[0], xs[br + 1]]
                ends = np.r_[xs[br] + 1, xs[-1] + 1]
                runs = set(zip(starts.tolist(), ends.tolist()))
        for r in list(live):
            if r not in runs:
                out.append((r[0], live.pop(r), r[1], y))
        for r in runs:
            live.setdefault(r, y)
    return [r for r in out if r[2] - r[0] >= min_side and r[3] - r[1] >= min_side]


def fill(mask, loops):
    """Even-odd fill of polygon loops (pixel coordinates) by pixel centres: a 20 cm wall is 20 px, not 21."""
    edges = np.array([(a[0], a[1], b[0], b[1]) for lp in loops for a, b in zip(lp, np.roll(lp, -1, axis=0))], float)
    if not len(edges):
        return
    x0, y0, x1, y1 = edges.T
    r0 = max(0, int(math.floor(min(y0.min(), y1.min()))))
    r1 = min(mask.shape[0], int(math.ceil(max(y0.max(), y1.max()))) + 1)
    for j in range(r0, r1):
        yc = j + 0.5
        hit = (y0 <= yc) != (y1 <= yc)
        if not hit.any():
            continue
        xs = np.sort(x0[hit] + (yc - y0[hit]) * (x1[hit] - x0[hit]) / (y1[hit] - y0[hit]))
        for a, b in zip(xs[0::2], xs[1::2]):
            i0, i1 = max(0, math.ceil(a - 0.5)), min(mask.shape[1], math.ceil(b - 0.5))
            if i1 > i0:
                mask[j, i0:i1] ^= 1


class Drawing:
    def __init__(self, path, unit=None):
        import ezdxf  # only the DXF commands need it

        self.ezdxf = ezdxf
        self.doc = ezdxf.readfile(path)
        self.msp = self.doc.modelspace()
        code = self.doc.header.get("$INSUNITS", 0)
        self.k = unit or UNITS.get(code)
        if not self.k:
            sys.exit(f"$INSUNITS is {code}: pass --unit <metres per drawing unit> (0.001 for mm)")
        self.units = code

    def m(self, v, nd=4):
        return round(v * self.k, nd)

    def xy(self, p):
        return [self.m(p[0]), self.m(p[1])]

    def flat(self, layers):
        """Entities on these layers, block inserts opened up (a part on layer 0 takes its insert's layer)."""
        def walk(es, parent):
            for e in es:
                lay = e.dxf.get("layer", "0")
                if lay == "0" and parent:
                    lay = parent
                if e.dxftype() == "INSERT":
                    yield from walk(e.virtual_entities(), lay)
                elif lay in layers:
                    yield e
        yield from walk(self.msp, None)

    def paths(self, e):
        from ezdxf import path as P

        if e.dxftype() == "HATCH":
            return list(P.from_hatch(e))
        try:
            return [P.make_path(e)]
        except (TypeError, ValueError):
            return []

    def loops(self, e, step=0.005):
        """Flattened outlines of one entity in metres, (closed?, [[x, y], ...]) per path."""
        out = []
        for p in self.paths(e):
            pts = [self.xy(v) for v in p.flattening(step / self.k)] if len(p) else []
            if len(pts) >= 2:
                closed = e.dxftype() in ("HATCH", "SOLID", "CIRCLE") or getattr(e, "closed", False) or getattr(e, "is_closed", False) \
                    or pts[0] == pts[-1]
                out.append((closed, pts))
        return out

    def bbox(self, e):
        from ezdxf import bbox

        b = bbox.extents([e], fast=True)
        return [self.m(b.extmin.x), self.m(b.extmin.y), self.m(b.extmax.x), self.m(b.extmax.y)] if b.has_data else None


def layers(d):
    from collections import Counter

    kinds, blocks = {}, {}
    for e in d.msp:
        lay = e.dxf.get("layer", "0")
        kinds.setdefault(lay, Counter())[e.dxftype()] += 1
        if e.dxftype() == "INSERT":
            blocks.setdefault(lay, Counter())[e.dxf.name] += 1
    from ezdxf import bbox

    ext = bbox.extents(d.msp, fast=True)
    out = {"insunits": d.units, "metres_per_unit": d.k,
           "extents_m": [d.m(ext.extmin.x, 2), d.m(ext.extmin.y, 2), d.m(ext.extmax.x, 2), d.m(ext.extmax.y, 2)] if ext.has_data else None,
           "layers": {k: {"entities": dict(v), **({"blocks": dict(blocks[k].most_common(12))} if k in blocks else {})}
                      for k, v in sorted(kinds.items())}}
    return out


def dump(d, names):
    out = []
    for e in d.msp:
        lay, t = e.dxf.get("layer", "0"), e.dxftype()
        if lay not in names:
            continue
        r = {"type": t, "layer": lay}
        if t == "INSERT":
            r.update(block=e.dxf.name, at=d.xy(e.dxf.insert), rot=round(e.dxf.rotation, 2),
                     scale=[round(e.dxf.xscale, 4), round(e.dxf.yscale, 4)], bbox=d.bbox(e))
            atts = {a.dxf.tag: a.dxf.text for a in e.attribs}
            if atts:
                r["attribs"] = atts
        elif t == "LINE":
            r.update(a=d.xy(e.dxf.start), b=d.xy(e.dxf.end))
        elif t == "ARC":
            r.update(c=d.xy(e.dxf.center), r=d.m(e.dxf.radius), a0=round(e.dxf.start_angle, 2), a1=round(e.dxf.end_angle, 2))
        elif t == "CIRCLE":
            r.update(c=d.xy(e.dxf.center), r=d.m(e.dxf.radius))
        elif t in ("TEXT", "MTEXT"):
            r.update(at=d.xy(e.dxf.insert), text=e.dxf.text if t == "TEXT" else e.plain_text())
        elif t == "DIMENSION":
            r.update(value_m=d.m(e.get_measurement()) if isinstance(e.get_measurement(), float) else None,
                     text=e.dxf.get("text", ""), bbox=d.bbox(e))
        elif t in OUTLINE:
            lp = d.loops(e)
            if t == "HATCH":
                r["pattern"] = e.dxf.pattern_name
            r.update(closed=all(c for c, _ in lp), loops=[p for _, p in lp])
        else:
            r["bbox"] = d.bbox(e)
        out.append(r)
    return out


def walls(d, wall_layers, seal_layers, res=100, debug=None, seal_width=0.2):
    solid_loops, seal = [], []
    for e in d.flat(set(wall_layers)):
        for closed, pts in d.loops(e):
            if closed:
                solid_loops.append(pts)
    for e in d.flat(set(seal_layers)):
        seal += d.loops(e)
        if e.dxftype() == "ARC":  # a door swing: its two radii close the sector, one of them across the doorway
            seal.append((False, [d.xy(e.start_point), d.xy(e.dxf.center), d.xy(e.end_point)]))
    if not solid_loops:
        sys.exit(f"no closed outline or hatch on {wall_layers}: walls drawn as loose lines need tracing by hand")
    pts = np.array([p for lp in solid_loops for p in lp] + [p for _, lp in seal for p in lp])
    ox, oy = pts[:, 0].min() - 1.0, pts[:, 1].max() + 1.0  # 1 m margin so the outside is connected
    W = int(math.ceil((pts[:, 0].max() + 1.0 - ox) * res))
    H = int(math.ceil((oy - (pts[:, 1].min() - 1.0)) * res))

    def px(lp):
        a = np.asarray(lp, float)
        return np.c_[(a[:, 0] - ox) * res, (oy - a[:, 1]) * res]

    mask = np.zeros((H, W), np.uint8)
    for lp in solid_loops:  # union: each outline filled on its own, so overlapping outlines do not cancel
        one = np.zeros_like(mask)
        fill(one, [px(lp)])
        mask |= one
    solid = mask.copy()
    for closed, lp in seal:
        if closed:  # a window frame: filled exactly as drawn, so the floor does not grow past the wall's face
            one = np.zeros_like(mask)
            fill(one, [px(lp)])
            solid |= one
        else:
            cv2.polylines(solid, [np.round(px(lp) * 16).astype(np.int32)], False, 1, thickness=max(3, round(seal_width * res)), shift=4)
    ff = (1 - solid).astype(np.uint8)
    cv2.floodFill(ff, np.zeros((H + 2, W + 2), np.uint8), (0, 0), 2)
    footprint = (ff != 2).astype(np.uint8)

    def rm(r):
        return {"x0": round(ox + r[0] / res, 4), "y0": round(oy - r[3] / res, 4), "x1": round(ox + r[2] / res, 4), "y1": round(oy - r[1] / res, 4)}

    wr, fr = to_rects(mask), to_rects(footprint, 2)
    if debug:
        dbg = np.full((H, W, 3), 255, np.uint8)
        dbg[footprint == 1] = (170, 220, 170)
        dbg[solid & (1 - mask) == 1] = (230, 140, 30)
        dbg[mask == 1] = (40, 40, 220)
        cv2.imwrite(debug, dbg)
    return {"_about": "Read off the DXF: x right, y up, metres, the drawing's own origin. walls = the wall layers' outlines as "
                      "non-overlapping rectangles; floor = what the walls enclose with the seal layers closing the openings.",
            "res_px_per_m": res, "wall_layers": wall_layers, "seal_layers": seal_layers,
            "wall_area_m2": round(mask.sum() / res ** 2, 3), "floor_area_m2": round(footprint.sum() / res ** 2, 3),
            "walls": [rm(r) for r in wr], "floor": [rm(r) for r in fr]}


def selftest():
    import tempfile

    import ezdxf

    doc = ezdxf.new(units=4)  # mm, stated: ezdxf.new() alone labels a drawing in metres
    msp = doc.modelspace()
    for a in [(0, 0, 4000, 200), (0, 0, 200, 3000), (3800, 0, 4000, 3000), (0, 2800, 1500, 3000), (2400, 2800, 4000, 3000), (1000, 0, 2000, 200)]:
        x0, y0, x1, y1 = a  # a 4 x 3 m room; the last box overlaps the first wall, the gap at y=3 m is a door
        msp.add_lwpolyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], close=True, dxfattribs={"layer": "WALL"})
    msp.add_line((1500, 2900), (1500, 2000), dxfattribs={"layer": "DOOR"})
    msp.add_arc((1500, 2900), 900, 270, 0, dxfattribs={"layer": "DOOR"})
    blk = doc.blocks.new("CHAIR")
    blk.add_lwpolyline([(0, 0), (450, 0), (450, 500), (0, 500)], close=True)
    msp.add_blockref("CHAIR", (1000, 1000), dxfattribs={"layer": "FURN", "rotation": 90})
    path = os.path.join(tempfile.mkdtemp(), "t.dxf")
    doc.saveas(path)
    d = Drawing(path)
    w = walls(d, ["WALL"], ["DOOR"])
    area = sum((r["x1"] - r["x0"]) * (r["y1"] - r["y0"]) for r in w["walls"])
    want = 4 * 0.2 * 2 + 2.6 * 0.2 * 2 - 0.9 * 0.2  # two long walls, two short ones, minus the door gap
    assert abs(area - want) < 1e-6 and abs(w["wall_area_m2"] - want) < 1e-6, (area, want)
    assert abs(w["floor_area_m2"] - 12.0) < 0.05, w["floor_area_m2"]  # the door swing closes the gap
    assert min(r["x0"] for r in w["walls"]) == 0.0 and max(r["y1"] for r in w["walls"]) == 3.0
    ch = dump(d, {"FURN"})[0]
    assert ch["block"] == "CHAIR" and ch["at"] == [1.0, 1.0] and ch["bbox"] == [0.5, 1.0, 1.0, 1.45], ch
    print("selftest ok: walls", round(area, 3), "m2, floor", w["floor_area_m2"], "m2, block", ch["bbox"])


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=["layers", "dump", "walls", "selftest"])
    ap.add_argument("dxf", nargs="?")
    ap.add_argument("--unit", type=float, help="metres per drawing unit when $INSUNITS is not set")
    ap.add_argument("--layers", help="dump: comma-separated layers")
    ap.add_argument("--walls", help="walls: wall layers")
    ap.add_argument("--seal", default="", help="walls: layers that close the openings (windows, door leaves and swings)")
    ap.add_argument("--res", type=int, default=100, help="walls: grid in px per metre (100 = 1 cm)")
    ap.add_argument("--seal-width", type=float, default=0.2, help="walls: seal lines drawn this wide in metres (about a wall, so a door line on the wall's centre closes the whole doorway)")
    ap.add_argument("--debug", help="walls: write a picture (red walls, orange seal, green floor)")
    ap.add_argument("--out", help="write JSON here instead of printing it")
    a = ap.parse_args()
    if a.cmd == "selftest":
        selftest()
        sys.exit()
    if not a.dxf:
        ap.error("a DXF file is needed")
    dr = Drawing(a.dxf, a.unit)
    split = lambda s: [x for x in (s or "").split(",") if x]
    if a.cmd == "layers":
        res = layers(dr)
    elif a.cmd == "dump":
        res = dump(dr, set(split(a.layers)) or ap.error("--layers is needed"))
    else:
        res = walls(dr, split(a.walls) or ap.error("--walls is needed"), split(a.seal), a.res, a.debug, a.seal_width)
    text = json.dumps(res, ensure_ascii=False, indent=1)
    if a.out:
        open(a.out, "w", encoding="utf-8").write(text)
        print(f"wrote {a.out}" + (f": {len(res['walls'])} wall and {len(res['floor'])} floor rectangles, "
                                  f"floor {res['floor_area_m2']} m2" if a.cmd == "walls" else ""))
    else:
        print(text)
