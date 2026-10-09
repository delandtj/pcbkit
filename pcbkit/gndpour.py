"""Outer-layer ground pours plus stitching vias to the inner plane.

Usage: pcbkit gndpour <in.kicad_pcb> <out.kicad_pcb>

- Replaces any [gndpour] net zone on the [gndpour] layers (F.Cu/B.Cu) with
  one over the outline, stopping short_edge_inset mm short of the two short
  edges (panel rails with mouse bites go there; zone_clearance, zone_min_width,
  thermal reliefs, islands removed). Footprint keep-outs still apply.
- Stitching vias (via = [dia, drill]), kept in a group so a re-run replaces
  them: a row edge_in mm in from the board edge every edge_pitch mm, and a
  grid mm grid over the rest. A via goes only where it clears every pad,
  track, via and courtyard of the board by via_clearance, and stays out of
  footprint keep-out areas.
- Zones are filled (fill = false skips it).
Run after routing (it needs to see the tracks). Write <out> to a scratch
directory, as for the other tools.
"""
import sys
import pcbnew

from . import config
from .pcbtool import seq, box


def main(argv):
    if len(argv) != 2:
        sys.exit(__doc__)
    src, out = argv
    G = config.load(src)['gndpour']
    VIA_D, VIA_H = G['via']
    CLR = G['via_clearance']
    EDGE_IN, EDGE_PITCH, GRID = G['edge_in'], G['edge_pitch'], G['grid']
    SHORT_IN = G['short_edge_inset']

    board = pcbnew.LoadBoard(src)
    gnd = board.GetNetcodeFromNetname(G['net'])
    if gnd < 0:
        sys.exit('no net %s' % G['net'])
    layers = [board.GetLayerID(n) for n in G['layers']]

    # drop previous outer ground zones and stitching group
    for z in seq(board.Zones()):
        if not z.GetIsRuleArea() and z.GetNetCode() == gnd and z.GetLayer() in layers:
            board.Delete(z)
    for g in seq(board.Groups()):
        if g.GetName() == G['group']:
            for it in list(g.GetItems()):
                board.Delete(it)
            board.Delete(g)

    eb = board.GetBoardEdgesBoundingBox()
    E = box(eb)
    inset = pcbnew.FromMM(SHORT_IN)

    def pour(layer):
        z = pcbnew.ZONE(board)
        z.SetLayer(layer)
        z.SetNetCode(gnd)
        o = z.Outline()
        o.NewOutline()
        if eb.GetWidth() >= eb.GetHeight():
            x0, x1, y0, y1 = eb.GetLeft() + inset, eb.GetRight() - inset, eb.GetTop(), eb.GetBottom()
        else:
            x0, x1, y0, y1 = eb.GetLeft(), eb.GetRight(), eb.GetTop() + inset, eb.GetBottom() - inset
        for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
            o.Append(x, y)
        z.SetAssignedPriority(G['priority'])
        z.SetPadConnection(pcbnew.ZONE_CONNECTION_THERMAL)
        z.SetMinThickness(pcbnew.FromMM(G['zone_min_width']))
        z.SetLocalClearance(pcbnew.FromMM(G['zone_clearance']))
        z.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)
        board.Add(z)

    for layer in layers:
        pour(layer)

    # obstacles: pads, tracks, vias, courtyards (as boxes), keep-out rule areas
    obst = []
    for f in seq(board.Footprints()):
        c = f.GetCourtyard(pcbnew.F_CrtYd)
        obst.append(box(c.BBox()) if c.OutlineCount() else box(f.GetBoundingBox(False)))
        obst += [box(p.GetBoundingBox()) for p in seq(f.Pads())]
        obst += [box(z.GetBoundingBox()) for z in seq(f.Zones())]
    obst += [box(t.GetBoundingBox()) for t in seq(board.Tracks())]
    r = VIA_D / 2 + CLR

    def free(x, y):
        if not (E[0] + 1.0 <= x <= E[2] - 1.0 and E[1] + 1.0 <= y <= E[3] - 1.0):
            return False
        return not any(o[0] - r < x < o[2] + r and o[1] - r < y < o[3] + r for o in obst)

    pts = []
    x = E[0] + EDGE_IN
    while x <= E[2] - EDGE_IN:
        pts += [(x, E[1] + EDGE_IN), (x, E[3] - EDGE_IN)]
        x += EDGE_PITCH
    y = E[1] + EDGE_IN + EDGE_PITCH
    while y <= E[3] - EDGE_IN - EDGE_PITCH:
        pts += [(E[0] + EDGE_IN, y), (E[2] - EDGE_IN, y)]
        y += EDGE_PITCH
    y = E[1] + GRID
    while y < E[3] - 1:
        x = E[0] + GRID
        while x < E[2] - 1:
            pts.append((x, y))
            x += GRID
        y += GRID

    grp = pcbnew.PCB_GROUP(board)
    grp.SetName(G['group'])
    board.Add(grp)
    n = 0
    for x, y in pts:
        if not free(x, y):
            continue
        v = pcbnew.PCB_VIA(board)
        v.SetPosition(pcbnew.VECTOR2I_MM(x, y))
        v.SetWidth(pcbnew.FromMM(VIA_D))
        v.SetDrill(pcbnew.FromMM(VIA_H))
        v.SetNetCode(gnd)
        board.Add(v)
        grp.AddItem(v)
        obst.append((x - VIA_D / 2, y - VIA_D / 2, x + VIA_D / 2, y + VIA_D / 2))
        n += 1

    if G['fill']:
        pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    board.Save(out)
    print(f"{G['net']} pours on {'/'.join(G['layers'])}, {n} stitching vias")
