"""Add the board outline and corner mounting holes to a .kicad_pcb.

Usage: pcbkit outline <in.kicad_pcb> <out.kicad_pcb> [length_mm] [height_mm] [--origin X,Y]

Rectangle from origin to origin + (length, height) on Edge.Cuts with
rounded corners, plus one mounting hole per [outline] hole_refs entry
(default four M3 NPTH, H1-H4), inset from each corner as board-only
footprints. Existing Edge.Cuts items and those refs are replaced, so it is
safe to re-run after mkboard. Write <out> to a scratch directory and copy
only the .kicad_pcb back (pcbnew's Save rewrites the .kicad_pro).
"""
import sys
import pcbnew

from . import config
from .pcbtool import seq


def main(argv):
    origin = None
    for a in list(argv):
        if a.startswith('--origin'):
            i = argv.index(a)
            v = a.split('=', 1)[1] if '=' in a else argv[i + 1]
            del argv[i:i + (1 if '=' in a else 2)]
            origin = [float(t) for t in v.split(',')]
    if not 2 <= len(argv) <= 4:
        sys.exit(__doc__)
    src, out = argv[:2]
    cfg = config.load(src)
    O = cfg['outline']
    L = float(argv[2]) if len(argv) > 2 else float(O['length'])
    H = float(argv[3]) if len(argv) > 3 else float(O['height'])
    X0, Y0 = origin or O['origin']
    R = O['radius']           # corner radius
    INSET = O['hole_inset']   # hole centre from each edge
    HOLE_LIB = f"{cfg['project']['fp_dir']}/{O['hole_lib']}.pretty"
    HOLE_FP = O['hole_fp']
    refs = O['hole_refs']

    board = pcbnew.LoadBoard(src)
    for d in seq(board.Drawings()):
        if d.GetLayer() == pcbnew.Edge_Cuts:
            board.Delete(d)
    for fp in seq(board.Footprints()):
        if fp.GetReference() in refs:
            board.Delete(fp)

    def mm(x, y):
        return pcbnew.VECTOR2I_MM(X0 + x, Y0 + y)

    def seg(x1, y1, x2, y2):
        s = pcbnew.PCB_SHAPE(board, pcbnew.SHAPE_T_SEGMENT)
        s.SetStart(mm(x1, y1))
        s.SetEnd(mm(x2, y2))
        s.SetLayer(pcbnew.Edge_Cuts)
        s.SetWidth(pcbnew.FromMM(O['line_width']))
        board.Add(s)

    def arc(cx, cy, sx, sy, angle):
        a = pcbnew.PCB_SHAPE(board, pcbnew.SHAPE_T_ARC)
        a.SetCenter(mm(cx, cy))
        a.SetStart(mm(sx, sy))
        a.SetArcAngleAndEnd(pcbnew.EDA_ANGLE(angle, pcbnew.DEGREES_T), True)
        a.SetLayer(pcbnew.Edge_Cuts)
        a.SetWidth(pcbnew.FromMM(O['line_width']))
        board.Add(a)

    seg(R, 0, L - R, 0)
    seg(L, R, L, H - R)
    seg(L - R, H, R, H)
    seg(0, H - R, 0, R)
    if R:
        arc(L - R, R, L - R, 0, 90)
        arc(L - R, H - R, L, H - R, 90)
        arc(R, H - R, R, H, 90)
        arc(R, R, 0, R, 90)

    corners = ((INSET, INSET), (L - INSET, INSET), (L - INSET, H - INSET), (INSET, H - INSET))
    for ref, (x, y) in zip(refs, corners):
        fp = pcbnew.FootprintLoad(HOLE_LIB, HOLE_FP)
        fp.SetReference(ref)
        fp.Reference().SetVisible(False)
        fp.SetFPID(pcbnew.LIB_ID(O['hole_lib'], HOLE_FP))
        fp.SetPosition(mm(x, y))
        fp.SetAttributes(pcbnew.FP_BOARD_ONLY | pcbnew.FP_EXCLUDE_FROM_BOM
                         | pcbnew.FP_EXCLUDE_FROM_POS_FILES)
        board.Add(fp)

    board.Save(out)
    print(f"outline {L} x {H} mm at ({X0}, {Y0}), {len(refs[:4])} holes {HOLE_FP} at {INSET} mm inset -> {out}")
