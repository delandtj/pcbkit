"""Delete the items a kicad-cli DRC report flags as via_dangling or
track_dangling, in place. Re-run DRC and repeat until none are left.
Only when DRC shows 0 unconnected: on an open route both halves count as
dangling and prune eats them.

Usage: pcbkit prune <board.kicad_pcb> <drc.rpt>
"""
import math, re, sys
from . import pcbtool as T


def main(argv):
    if len(argv) != 2:
        sys.exit(__doc__)
    bpath, rpt = argv
    b = T.load(bpath)
    hits = [(k, it) for k, its in T.drc_unconnected(rpt) if k in ('via_dangling', 'track_dangling') for it in its[:1]]
    n = 0
    for kind, (x, y, desc) in hits:
        net = desc[desc.index('[') + 1:desc.index(']')]
        for t in T.tracks(b):
            if t.GetNetname() != net: continue
            if kind == 'via_dangling' and t.GetClass() == 'PCB_VIA':
                p = t.GetPosition()
                if math.hypot(T.mm(p.x) - x, T.mm(p.y) - y) < 0.02: b.Delete(t); n += 1; break
            if kind == 'track_dangling' and t.GetClass() == 'PCB_TRACK':
                m = re.search(r'length ([\d.]+) mm', desc)
                if m and abs(T.mm(t.GetLength()) - float(m[1])) > 0.002:
                    continue
                if any(math.hypot(T.mm(p.x) - x, T.mm(p.y) - y) < 0.02 for p in (t.GetStart(), t.GetEnd())):
                    b.Delete(t); n += 1; break
    b.Save(bpath)
    print('pruned', n, 'of', len(hits))
