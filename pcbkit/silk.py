"""Tidy silkscreen after placement.

Usage: pcbkit silk <in.kicad_pcb> <out.kicad_pcb>

- Value fields go to F.Fab (assembly does not need them on silk).
- Refs starting with a hide_prefixes entry (mounting holes) are hidden.
- Small parts (courtyard < small_area mm^2: 0402/0603 R/C/L, small diodes,
  resistor arrays) get their silk reference hidden; the F.Fab
  ${REFERENCE} text stays for the assembly drawing.
- Footprint silk lines thinner than min_line are widened to it (JLC minimum
  line width 0.15 mm; library parts use 0.12 mm).
- Every other reference is set to size / thickness (JLC minimum text height
  1.0 / 0.15 mm) and moved to the first spot around its courtyard (above,
  below, right, left), else inside its own courtyard, that is inside the
  board, off every pad and silk outline, off other parts' courtyards and off
  already-placed references. Prefixes in `order` go first (connectors, ICs);
  `relax_prefixes` parts also try vertical text and then ignore courtyards.
  If none is free, it is hidden.
- Parts whose courtyard is not within the board's x range (right_overhang
  mm of slack on the right) are hidden.
Write <out> to a scratch directory, as for the other tools.
"""
import sys
import pcbnew

from . import config
from .pcbtool import seq, box, cbox


def overlap(a, b, g):
    return not (a[2] + g <= b[0] or b[2] + g <= a[0] or a[3] + g <= b[1] or b[3] + g <= a[1])


def seg_hits(t, sg, g):
    """Liang-Barsky: does segment sg cross box t grown by g?"""
    x0, y0, x1, y1 = t[0] - g, t[1] - g, t[2] + g, t[3] + g
    (ax, ay), (bx, by) = sg
    dx, dy = bx - ax, by - ay
    u0, u1 = 0.0, 1.0
    for pp, qq in ((-dx, ax - x0), (dx, x1 - ax), (-dy, ay - y0), (dy, y1 - ay)):
        if pp == 0:
            if qq < 0:
                return False
            continue
        r = qq / pp
        if pp < 0:
            u0 = max(u0, r)
        else:
            u1 = min(u1, r)
        if u0 > u1:
            return False
    return True


def main(argv):
    if len(argv) != 2:
        sys.exit(__doc__)
    src, out = argv
    S = config.load(src)['silk']
    SIZE, THICK, GAP, MIN_LINE = S['size'], S['thickness'], S['gap'], S['min_line']
    mm = pcbnew.ToMM

    board = pcbnew.LoadBoard(src)
    FPS = seq(board.Footprints())
    edge = box(board.GetBoardEdgesBoundingBox())
    pads, courts, segs = [], {}, []
    for fp in FPS:
        courts[fp.GetReference()] = cbox(fp)
        pads += [box(p.GetBoundingBox()) for p in seq(fp.Pads())]
        for g in seq(fp.GraphicalItems()):
            it = g.Cast()
            if it.GetLayer() not in (pcbnew.F_SilkS, pcbnew.B_SilkS) or not isinstance(it, pcbnew.PCB_SHAPE):
                continue
            if it.GetWidth() < pcbnew.FromMM(MIN_LINE):
                it.SetWidth(pcbnew.FromMM(MIN_LINE))
            if it.GetLayer() != pcbnew.F_SilkS:
                continue
            st = it.GetShape()
            if st == pcbnew.SHAPE_T_SEGMENT:
                q = [it.GetStart(), it.GetEnd()]
                segs.append(((mm(q[0].x), mm(q[0].y)), (mm(q[1].x), mm(q[1].y))))
            elif st == pcbnew.SHAPE_T_POLY:
                o = it.GetPolyShape().Outline(0)
                pts = [o.CPoint(k) for k in range(o.PointCount())]
                for k in range(len(pts)):
                    a1, a2 = pts[k], pts[(k + 1) % len(pts)]
                    segs.append(((mm(a1.x), mm(a1.y)), (mm(a2.x), mm(a2.y))))
            else:                                   # rect, arc, circle: bbox edges
                x0, y0, x1, y1 = box(it.GetBoundingBox())
                segs += [((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)),
                         ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))]

    taken = []
    hidden = moved = 0
    order = S['order']
    rank = {p: i for i, p in enumerate(order)}
    hide = tuple(S['hide_prefixes'])
    relax = tuple(S['relax_prefixes'])
    for fp in sorted(FPS, key=lambda f: rank.get(f.GetReference()[0], len(order))):
        val = fp.Value()
        if val.GetLayer() == pcbnew.F_SilkS:
            val.SetLayer(pcbnew.F_Fab)
        ref = fp.Reference()
        r = fp.GetReference()
        c = courts[r]
        if hide and r.startswith(hide):
            ref.SetVisible(False)
            continue
        on_board = edge[0] <= c[0] and c[2] <= edge[2] + S['right_overhang']
        area = (c[2] - c[0]) * (c[3] - c[1])
        if area < S['small_area'] or not on_board:
            ref.SetVisible(False)
            hidden += 1
            continue
        ref.SetVisible(True)
        ref.SetLayer(pcbnew.F_SilkS)
        ref.SetTextSize(pcbnew.VECTOR2I_MM(SIZE, SIZE))
        ref.SetTextThickness(pcbnew.FromMM(THICK))
        ref.SetTextAngle(pcbnew.EDA_ANGLE(0, pcbnew.DEGREES_T))
        cx, cy = (c[0] + c[2]) / 2, (c[1] + c[3]) / 2
        cands = [(cx, c[1] - 0.7), (cx, c[3] + 0.7), (cx, c[3] + 0.95), (cx, c[3] + 1.2),
                 (c[2] + 0.4 + len(r) * 0.35, cy), (c[0] - 0.4 - len(r) * 0.35, cy)]
        # then anywhere inside the part's own courtyard, clear of pads (connector bodies)
        inside = []
        yy = c[1] + 0.6
        while yy <= c[3] - 0.6:
            inside.append((cx, yy))
            yy += 0.25
        cands += sorted(inside, key=lambda q: -abs(q[1] - cy))

        def fits(x, y, ang, strict):
            ref.SetTextAngle(pcbnew.EDA_ANGLE(ang, pcbnew.DEGREES_T))
            ref.SetTextPos(pcbnew.VECTOR2I_MM(x, y))
            t = box(ref.GetBoundingBox())
            if not (edge[0] + 0.3 <= t[0] and t[2] <= edge[2] - 0.3 and
                    edge[1] + 0.3 <= t[1] and t[3] <= edge[3] - 0.3):
                return False
            if any(overlap(t, p, GAP) for p in pads) or any(seg_hits(t, sg, GAP) for sg in segs):
                return False
            if strict and any(overlap(t, cc, 0.0) for rr, cc in courts.items() if rr != r):
                return False
            if any(overlap(t, o, GAP) for o in taken):
                return False
            taken.append(t)
            return True

        tries = [(x, y, 0, True) for x, y in cands]
        if relax and r.startswith(relax):
            # connectors: their labels matter for cabling, so relax step by step
            side = [(c[0] - 0.8, cy, 90), (c[2] + 0.8, cy, 90)]
            tries += [(x, y, a, True) for x, y, a in side]
            tries += [(x, y, 0, False) for x, y in cands]
            tries += [(x, y, a, False) for x, y, a in side]
        if any(fits(*t) for t in tries):
            moved += 1
        else:
            ref.SetVisible(False)
            hidden += 1

    board.Save(out)
    print(f"references placed: {moved}, hidden: {hidden}")
