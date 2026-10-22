"""Region picture of a board with every copper layer: pads as boxes (F.Cu
red, B.Cu blue, THT grey), tracks F.Cu red / In1 green / B.Cu blue /
In2 dim, zone fills, vias white with net labels, a 1 mm grid with
coordinates. Highlighted nets get a yellow outline. For reading a
routing problem before writing a handroute plan; `render` is the one
with the DRC overlay.

Usage: pcbkit view <board.kicad_pcb> <out.png> x0 y0 x1 y1 [NET ...]
Needs matplotlib.
"""
import sys

import pcbnew

from . import pcbtool as T

# colours by layer name (In1/In2 as named by setrules, or KiCad defaults)
COLOURS = {'F.Cu': '#e03030', 'B.Cu': '#3070e0', 'In1.Cu': '#30c030', 'SIG': '#30c030',
           'In2.Cu': '#806000', 'GND': '#806000'}


def short(net):
    return net.replace('Net-(U1-', '').replace('{slash}', '/')


def main(argv):
    if len(argv) < 6 or argv[:1] in (['-h'], ['--help']):
        sys.exit(__doc__)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle, Circle, Polygon

    b = T.load(argv[0])
    out = argv[1]
    x0, y0, x1, y1 = map(float, argv[2:6])
    hl = set(argv[6:])
    W, H = x1 - x0, y1 - y0
    s = 1.2
    fig = plt.figure(figsize=(W * s, H * s), dpi=100)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(x0, x1)
    ax.set_ylim(y1, y0)
    ax.set_aspect('equal')
    ax.set_facecolor('#101010')

    def inside(x, y, m=2):
        return x0 - m <= x <= x1 + m and y0 - m <= y <= y1 + m

    for z in T.zones(b):
        for L, c in ((pcbnew.B_Cu, '#1d3550'), (pcbnew.F_Cu, '#4a2020')):
            if z.IsOnLayer(L) and z.HasFilledPolysForLayer(L):
                fp = z.GetFilledPolysList(L)
                for i in range(fp.OutlineCount()):
                    o = fp.Outline(i)
                    pts = [(T.mm(o.CPoint(j).x), T.mm(o.CPoint(j).y)) for j in range(o.PointCount())]
                    if pts and any(inside(*q, 3) for q in pts):
                        gnd = z.GetNetname() == 'GND'
                        ax.add_patch(Polygon(pts, closed=True, fc=c, ec='none', alpha=0.5 if gnd else 0.9, zorder=1))
                        if not gnd:
                            cx = sum(q[0] for q in pts) / len(pts)
                            cy = sum(q[1] for q in pts) / len(pts)
                            ax.text(cx, cy, z.GetNetname() + ' ' + b.GetLayerName(L), color='#ccc', fontsize=6,
                                    ha='center', zorder=6)
    for f in b.GetFootprints():
        for p in f.Pads():
            bx = T.box(p.GetBoundingBox())
            cx, cy = (bx[0] + bx[2]) / 2, (bx[1] + bx[3]) / 2
            if not inside(cx, cy):
                continue
            tht = p.GetAttribute() == pcbnew.PAD_ATTRIB_PTH
            col = '#bbbbbb' if tht else ('#3070e0' if p.IsOnLayer(pcbnew.B_Cu) else '#e03030')
            h = p.GetNetname() in hl
            ax.add_patch(Rectangle((bx[0], bx[1]), bx[2] - bx[0], bx[3] - bx[1], fc=col,
                                   ec='yellow' if h else 'none', lw=1.5 if h else 0, alpha=0.55, zorder=3))
            if (bx[2] - bx[0]) * (bx[3] - bx[1]) > 0.2:
                ax.text(cx, cy, f.GetReference() + '.' + p.GetNumber() + '\n' + short(p.GetNetname()),
                        color='white', fontsize=5, ha='center', va='center', zorder=6)
    for t in T.tracks(b):
        sx, sy = T.mm(t.GetStart().x), T.mm(t.GetStart().y)
        if t.GetClass() == 'PCB_VIA':
            if inside(sx, sy):
                r = T.mm(t.Cast().GetWidth(pcbnew.F_Cu)) / 2
                ax.add_patch(Circle((sx, sy), r, fc='white', ec='yellow' if t.GetNetname() in hl else 'none',
                                    lw=1.5, alpha=0.9, zorder=5))
                ax.text(sx, sy + 0.42, short(t.GetNetname()), color='white', fontsize=4.5, ha='center', zorder=6)
            continue
        ex, ey = T.mm(t.GetEnd().x), T.mm(t.GetEnd().y)
        if not (inside(sx, sy) or inside(ex, ey)):
            continue
        L = b.GetLayerName(t.GetLayer())
        w = T.mm(t.GetWidth())
        inner2 = L in ('In2.Cu', 'GND')
        ax.plot([sx, ex], [sy, ey], color=COLOURS.get(L, 'grey'), lw=w * s * 100 / 2.54 * 0.72,
                solid_capstyle='round', alpha=0.4 if inner2 else 0.85, zorder=4 if L == 'B.Cu' else 2)
        if t.GetNetname() in hl:
            ax.plot([sx, ex], [sy, ey], color='yellow', lw=1, zorder=5, alpha=0.9)
    for x in range(int(x0), int(x1) + 1):
        ax.axvline(x, color='#333', lw=0.4, zorder=1)
        ax.text(x, y0 + 0.25, str(x), color='#888', fontsize=5, ha='center')
    for y in range(int(y0), int(y1) + 1):
        ax.axhline(y, color='#333', lw=0.4, zorder=1)
        ax.text(x0 + 0.1, y, str(y), color='#888', fontsize=5, va='center')
    fig.savefig(out)
    print('wrote', out)
