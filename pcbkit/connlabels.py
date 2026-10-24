"""Silkscreen function labels for the connectors.

Usage: pcbkit connlabels <in.kicad_pcb> <out.kicad_pcb>

Each connector in [connlabels] names gets "<ref> <NAME>" on F.Silkscreen
where its reference used to sit (the silk reference is hidden, F.Fab keeps
it), and each one in [connlabels] legends gets a short legend centred under
each numbered pin, between the label and the body, on the side away from
the nearer board edge (the other side if that one is taken). Pin rows
along y (connectors on a left or right edge) get the legends beside the
pins and the name upright next to them. Placed in table order around
existing silk and pads.
Everything goes in one group; a re-run replaces it. Run after silk (which
would show the references again).
"""
import os
import sys
import pcbnew

from . import config
from .pcbtool import seq, drop_group


DEBUG = bool(os.environ.get('PCBKIT_DEBUG'))


def main(argv):
    if len(argv) != 2:
        sys.exit(__doc__)
    src, out = argv
    C = config.load(src)['connlabels']
    NAMES, LEGENDS = C['names'], C['legends']
    NAME_H, LEG_H = C['name_height'], C['legend_height']
    CLR = C['clearance']       # silk-to-silk and silk-to-pad clearance (mm)
    MM = pcbnew.FromMM

    board = pcbnew.LoadBoard(src)
    drop_group(board, C['group'])
    group = pcbnew.PCB_GROUP(board)
    group.SetName(C['group'])
    board.Add(group)
    for ref in NAMES:
        board.FindFootprintByReference(ref).Reference().SetVisible(False)

    def box(bb, grow=0.0):
        g = MM(grow)
        return (bb.GetLeft() - g, bb.GetTop() - g, bb.GetRight() + g, bb.GetBottom() + g)

    def shape_boxes(it):
        """A rectangle or polygon outline (a socket drawn around other parts)
        blocks only along its edges, not the area inside it."""
        if it.GetClass() == 'PCB_SHAPE' and hasattr(it, 'Cast'):
            it = it.Cast()
        if it.GetClass() == 'PCB_SHAPE' and it.GetShape() in (pcbnew.S_RECT, pcbnew.S_POLYGON):
            if it.GetShape() == pcbnew.S_RECT:
                pts = [(c.x, c.y) for c in it.GetRectCorners()]
            else:
                o = it.GetPolyShape().Outline(0)
                pts = [(o.CPoint(i).x, o.CPoint(i).y) for i in range(o.PointCount())]
            if len(pts) >= 2 and it.GetFillMode() == pcbnew.FILL_T_NO_FILL:
                g = it.GetWidth() // 2 + MM(CLR)
                return [(min(a[0], b[0]) - g, min(a[1], b[1]) - g, max(a[0], b[0]) + g, max(a[1], b[1]) + g)
                        for a, b in zip(pts, pts[1:] + pts[:1])]
        return [box(it.GetBoundingBox(), CLR)]

    # obstacles: footprint silk (shapes and visible texts), pads, board edge
    obst = []
    for fp in board.GetFootprints():
        for it in seq(fp.GraphicalItems()):
            if it.GetLayer() == pcbnew.F_SilkS and (not hasattr(it, 'IsVisible') or it.IsVisible()):
                obst.extend(shape_boxes(it))
        for f in (fp.Reference(), fp.Value()):
            if f.GetLayer() == pcbnew.F_SilkS and f.IsVisible():
                obst.append(box(f.GetBoundingBox(), CLR))
        for p in fp.Pads():
            if p.IsOnLayer(pcbnew.F_Cu):
                obst.append(box(p.GetBoundingBox(), CLR))
    for d in seq(board.Drawings()):
        if d.GetLayer() == pcbnew.F_SilkS:
            obst.extend(shape_boxes(d))
    edge = box(board.GetBoardEdgesBoundingBox(), -0.3)

    def free(bb):
        x0, y0, x1, y1 = box(bb)
        if x0 < edge[0] or y0 < edge[1] or x1 > edge[2] or y1 > edge[3]:
            return False
        return not any(x0 < b[2] and x1 > b[0] and y0 < b[3] and y1 > b[1] for b in obst)

    def text(s, x, y, h, angle=0):
        t = pcbnew.PCB_TEXT(board)
        t.SetText(s)
        t.SetLayer(pcbnew.F_SilkS)
        t.SetTextSize(pcbnew.VECTOR2I(MM(h), MM(h)))
        t.SetTextThickness(MM(C['thickness']))
        t.SetPosition(pcbnew.VECTOR2I(MM(x), MM(y)))
        t.SetHorizJustify(pcbnew.GR_TEXT_H_ALIGN_CENTER)
        t.SetVertJustify(pcbnew.GR_TEXT_V_ALIGN_CENTER)
        t.SetTextAngle(pcbnew.EDA_ANGLE(angle, pcbnew.DEGREES_T))
        return t

    def ink(t):
        # the stroke outline, not GetBoundingBox() (which pads well past the ink)
        return t.GetEffectiveTextShape().BBox()

    def add(t):
        board.Add(t); group.AddItem(t)
        obst.append(box(ink(t), CLR))

    def place(cands, s, h):
        """First candidate (x, y[, angle]) where the text fits; None if none does."""
        for c in cands:
            t = text(s, *c[:2], h, *c[2:])
            if free(ink(t)):
                add(t)
                return c
        return None

    def place_row(items, h):
        """All texts of a pin legend or none of them (they must also clear each other)."""
        ts = [text(s, x, y, h, *a) for s, x, y, *a in items]
        boxes = [box(ink(t)) for t in ts]
        for t in ts:
            if not free(ink(t)):
                if DEBUG:
                    b = box(ink(t)); bb = [pcbnew.ToMM(v) for v in b]
                    hits = [[pcbnew.ToMM(v) for v in o] for o in obst if b[0] < o[2] and b[2] > o[0] and b[1] < o[3] and b[3] > o[1]]
                    print('  blocked %r at %.2f..%.2f x %.2f..%.2f by %s' % (t.GetText(), bb[0], bb[2], bb[1], bb[3], hits[:3] or 'edge'))
                return False
        gap = MM(CLR / 2)       # between the texts of one row (1.27 mm pitch headers): half the silk clearance
        for i, a in enumerate(boxes):
            for b in boxes[:i]:
                if a[0] < b[2] + gap and a[2] > b[0] - gap and a[1] < b[3] + gap and a[3] > b[1] - gap:
                    if DEBUG:
                        print('  legend texts %r and %r collide' % (ts[i].GetText(), ts[boxes.index(b)].GetText()))
                    return False
        for t in ts:
            add(t)
        return True

    bbox = board.GetBoardEdgesBoundingBox()
    mid_x, mid_y = pcbnew.ToMM(bbox.GetCenter().x), pcbnew.ToMM(bbox.GetCenter().y)
    missing = []

    def geom(ref, flip=False):
        """Courtyard, the side the labels go to (away from the nearer board
        edge, or the other one with flip) and the near edge of the first
        label line. vertical: the pins run along y (connector on a left or
        right edge), the lines are then stacked in x."""
        fp = board.FindFootprintByReference(ref)
        cy = fp.GetCourtyard(pcbnew.F_Cu).BBox()
        c = [pcbnew.ToMM(v) for v in (cy.GetLeft(), cy.GetTop(), cy.GetRight(), cy.GetBottom())]
        pads = [p for p in fp.Pads() if p.GetNumber().isdigit()]
        xs = [pcbnew.ToMM(p.GetPosition().x) for p in pads]
        ys = [pcbnew.ToMM(p.GetPosition().y) for p in pads]
        vertical = len(pads) > 1 and (max(ys) - min(ys)) > (max(xs) - min(xs))
        pos = pcbnew.ToMM(fp.GetPosition().x) if vertical else pcbnew.ToMM(fp.GetPosition().y)
        side = 1 if pos < (mid_x if vertical else mid_y) else -1
        if flip:
            side = -side
        # start from the body's own silk outline (tighter than the courtyard)
        silk = [it.GetBoundingBox() for it in seq(fp.GraphicalItems()) if it.GetLayer() == pcbnew.F_SilkS]
        lo, hi = (0, 2) if vertical else (1, 3)
        if silk:
            e = max(pcbnew.ToMM(bb.GetRight() if vertical else bb.GetBottom()) for bb in silk) if side > 0 \
                else min(pcbnew.ToMM(bb.GetLeft() if vertical else bb.GetTop()) for bb in silk)
        else:
            e = c[hi] if side > 0 else c[lo]
        return fp, c, side, e + side * (CLR + 0.15), vertical     # 0.15: the ink runs a little past the text box

    # pass 1: pin legends right next to the bodies
    nline = {}      # ref -> (near edge of the name line, side, vertical)
    for ref, leg in LEGENDS.items():
        done = False
        for flip in (False, True):
            fp, c, side, near, vertical = geom(ref, flip)
            pads = sorted((p for p in fp.Pads() if p.GetNumber().isdigit()), key=lambda p: int(p.GetNumber()))
            if vertical:
                # horizontal texts beside each pin, their near edge on one line
                items, depth = [], 0.0
                for p, s in zip(pads, leg):
                    w = pcbnew.ToMM(ink(text(s, 0, 0, LEG_H)).GetWidth())
                    items.append((s, near + side * w / 2, pcbnew.ToMM(p.GetPosition().y)))
                    depth = max(depth, w)
            else:
                items = [(s, pcbnew.ToMM(p.GetPosition().x), near + side * (LEG_H + 0.15) / 2) for p, s in zip(pads, leg)]
                depth = LEG_H + 0.15
            if place_row(items, LEG_H):
                nline[ref] = (near + side * (depth + CLR + 0.15), side, vertical)
                done = True
                break
        if not done:
            missing.append(ref + ' legend')

    # pass 2: names, after the legends so they cannot push into a neighbour's
    for ref, name in NAMES.items():
        fp, (cx0, cy0, cx1, cy1), side, near, vertical = geom(ref)
        if ref in nline:
            near, side, vertical = nline[ref]
        ccx, ccy = (cx0 + cx1) / 2, (cy0 + cy1) / 2
        label = '%s %s' % (ref, name)
        w = 0.7 * NAME_H * len(label) / 2 + 0.6
        if vertical:
            n = near + side * (NAME_H + 0.15) / 2
            o = (cx0 if side > 0 else cx1) - side * (CLR + 0.6 + (NAME_H + 0.15) / 2)    # the other side
            cands = [(x, ccy + dy, 90) for x in (n, n + side * 0.5, n + side * 1.1, o) for dy in (0, -1, 1, -2, 2, -3, 3)]
            # then horizontal above / below the body
            horiz = [(ccx + dx, y) for y in (cy0 - (CLR + 0.6 + NAME_H / 2), cy1 + (CLR + 0.6 + NAME_H / 2)) for dx in (0, -1, 1, -2, 2, -3, 3)]
            if not place(cands + horiz, label, NAME_H) and not place(cands + horiz, name, NAME_H) \
                    and not place(cands + horiz, ref, NAME_H):
                missing.append(ref + ' name')
            continue
        n = near + side * (NAME_H + 0.15) / 2
        far = (cy0 if side > 0 else cy1) - side * (CLR + 0.6)    # beyond the other side
        cands = [(ccx, n)] + [(ccx + dx, n) for dx in (-1, 1, -2, 2, -3, 3)] + [(ccx, n + side * 0.5), (cx0 - w, n), (cx1 + w, n),
                 (cx0 - w, ccy), (cx1 + w, ccy), (ccx, n + side * 1.1), (ccx, far)]
        # crowded rows: vertical beside the body, then the name alone, then the bare reference
        vert = [(x, ccy + side * dy / 4, 90) for x in (cx1 + 0.9, cx0 - 0.9, cx1 + 1.5, cx0 - 1.5) for dy in range(0, 11)]
        if not place(cands + vert, label, NAME_H) and not place(vert, name, NAME_H) \
                and not place(cands + vert, ref, NAME_H):
            missing.append(ref + ' name')

    print('not placed:', missing or 'none')
    board.Save(out)
    print('labels for', len(NAMES), 'connectors')
