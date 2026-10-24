"""Move a routed board to a regenerated schematic: new symbol paths and
new net names, same copper. Nothing moves.

Usage: pcbkit relink <netlist.xml> <board.kicad_pcb> [--save [out.kicad_pcb]] [--allow-shorts]

Export the netlist with `kicad-cli sch export netlist --format kicadxml`.
For every footprint, by reference: the KIID path (sheet tstamps +
component tstamp), Sheetname and Sheetfile are set from the netlist as
mkboard sets them, and every pad gets the netlist's net for (ref, pin).
Board-only footprints (mounting holes) not in the netlist are left alone.

The pads give an old -> new net map. An old net whose pads all go to one
new net that no other old net feeds is a rename: its tracks, vias, zones
and copper shapes follow the map. The other old nets are ambiguous (a
pin moved to another net, a net split or merged); their copper is
resolved by connectivity:
  - tracks / arcs / vias of the old net are clustered by contact (an
    end or via centre inside another item on a shared copper layer,
    1 um tolerance); a cluster takes the new net of the pads its ends
    touch (pad.HitTest on the pad's layer); pads marked * below are
    pins whose net changed;
  - a zone takes the new net of the pads its old fill touches;
  - a cluster touching no pad follows the zone it lands in, else keeps
    the old name if that is still a net;
  - a cluster or zone touching pads of two new nets is a short after the
    schematic change: listed, nothing written. With --allow-shorts the
    cluster takes the net of its first unchanged pad (a zone the
    majority net) and DRC shows the shorting / unconnected items for a
    handroute plan to fix.
The tool stops, writing nothing, when footprints and netlist components
differ, when a pad with a net has no netlist node, when copper carries a
net no pad had, or when ambiguous copper cannot be resolved.

Unused old nets are dropped, zones refilled and kicad-cli DRC run; the
rename map (changed names only), what connectivity resolved, the counts
and the DRC summary are printed. The copy goes to
~/.cache/pcbkit/<board>/relink with the .kicad_pro (and fp-lib-table)
beside it; --save writes it back to the board, or to the given path.
"""
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict

import pcbnew

from . import pcbtool as T


def board_name(name):
    """Net name as the board stores it (see mkboard)."""
    # the XML export unescapes '/' inside auto-generated pin-name nets
    if name.startswith(("Net-(", "unconnected-(")):
        return name.replace("/", "{slash}")
    return name


def parse(netlist):
    """-> comps {ref: (path, sheetname, sheetfile)}, pins {(ref, pin): net},
    nets {net: [(ref, pin), ...]}."""
    root = ET.parse(netlist).getroot()
    comps, pins, nets = {}, {}, {}
    for c in root.find("components"):
        ref = c.get("ref")
        sp = c.find("sheetpath")
        # multi-unit symbols list one uuid per unit; the footprint links to the first
        path = sp.get("tstamps") + c.findtext("tstamps").split()[0]
        props = {p.get("name"): p.get("value") for p in c.findall("property")}
        comps[ref] = (path, props.get("Sheetname", ""), props.get("Sheetfile", ""))
    for n in root.find("nets"):
        name = board_name(n.get("name"))
        nodes = [(node.get("ref"), node.get("pin")) for node in n.findall("node")]
        nets[name] = nodes
        for k in nodes:
            pins[k] = name
    return comps, pins, nets


def where(it, b):
    p = it.GetPosition()
    return f"({T.mm(p.x):.3f}, {T.mm(p.y):.3f}) {b.GetLayerName(it.GetLayer())}"


def drc(pcb):
    """kicad-cli DRC; -> (Counter of violation types, unconnected count, report path)."""
    rpt = os.path.splitext(pcb)[0] + '.rpt'
    subprocess.run(['kicad-cli', 'pcb', 'drc', '--severity-all', '--format', 'report', '-o', rpt, pcb],
                   capture_output=True, text=True)
    kinds, unconn, sect = Counter(), None, None
    for line in open(rpt):
        m = re.match(r'\*\* Found (\d+) (.*) \*\*', line)
        if m:
            sect = m[2]
            if sect.startswith('unconnected'):
                unconn = int(m[1])
        elif line.startswith('[') and sect == 'DRC violations':
            kinds[line.split(']')[0][1:]] += 1
    return kinds, unconn, rpt


ACC = 1000  # 1 um: anchor tolerance for track / via / pad contact


def kind(t):
    return {'PCB_TRACK': 'track', 'PCB_ARC': 'arc', 'PCB_VIA': 'via'}[t.GetClass()]


def cu_layers(it):
    return [l for l in it.GetLayerSet().Seq() if pcbnew.IsCopperLayer(l)]


def anchors(t):
    """Points where a track / arc / via connects, with the layers it connects on."""
    if t.GetClass() == 'PCB_VIA':
        return [(t.GetPosition(), cu_layers(t))]
    return [(t.GetStart(), [t.GetLayer()]), (t.GetEnd(), [t.GetLayer()])]


class Grid:
    """Bounding-box buckets so contact tests stay local."""
    def __init__(self, cell=pcbnew.FromMM(1)):
        self.c, self.d = cell, defaultdict(list)

    def add(self, obj, bb):
        c = self.c
        for x in range(bb.GetLeft() // c, bb.GetRight() // c + 1):
            for y in range(bb.GetTop() // c, bb.GetBottom() // c + 1):
                self.d[(x, y)].append(obj)

    def at(self, p):
        return self.d.get((p.x // self.c, p.y // self.c), ())


def bbox_txt(items, b):
    xs = [v for t in items for p, _ in anchors(t) for v in (T.mm(p.x),)]
    ys = [v for t in items for p, _ in anchors(t) for v in (T.mm(p.y),)]
    layers = sorted({b.GetLayerName(l) for t in items for _, ls in anchors(t) for l in ls})
    return (f'{len(items)} items, {" ".join(layers)}, '
            f'({min(xs):.3f}, {min(ys):.3f})-({max(xs):.3f}, {max(ys):.3f})')


def resolve(b, amb, copper, fps, pins, newnames, allow_shorts):
    """Copper on ambiguous old nets -> new net by the pads it touches.
    Returns ({id(item): net}, shorts, unresolved, report lines)."""
    out, shorts, lost, rep = {}, [], [], []
    pgrid = Grid()
    for ref, fp in fps.items():
        for pad in fp.Pads():
            new = pins.get((ref, pad.GetNumber()))
            if new:
                lbl = f'{ref}.{pad.GetNumber()}' + ('*' if pad.GetNetname() != new else '')
                pgrid.add((lbl, new, pad), pad.GetBoundingBox())

    def pick(label, old, hits, items_txt):
        """hits: [(pad label, new net)]; -> net or None."""
        nets = sorted({n for _, n in hits})
        by = ' '.join(sorted({l for l, _ in hits}))
        if len(hits) > 8:
            moved = sorted({l for l, _ in hits if l.endswith('*')})
            by = f'{len(hits)} pads' + (f' incl. {" ".join(moved)}' if moved else '')
        if len(nets) == 1:
            return nets[0], f'{label} ({items_txt}) {old} -> {nets[0]} by {by}'
        if not nets:
            if old in newnames:
                return old, None
            lost.append(f'{label} ({items_txt}) {old}: touches no pad and {old} is gone')
            return None, None
        pads = ', '.join(f'{l} {n}' for l, n in sorted(set(hits)))
        if len(hits) > 8:  # a zone: counts per net, pads of the minority nets
            tally = Counter(n for _, n in hits)
            top = tally.most_common(1)[0][0]
            pads = ', '.join(f'{c} {n}' for n, c in sorted(tally.items())) + '; ' + \
                ', '.join(f'{l} {n}' for l, n in sorted(set(hits)) if n != top)
        shorts.append(f'{label} ({items_txt}) {old}: pads {pads}')
        if not allow_shorts:
            return None, None
        # zone: the majority net; cluster: the first pad whose net did not change
        # (so the moved pin shows up as the short), else the first pad
        keep = sorted(h for h in hits if not h[0].endswith('*')) or sorted(hits)
        first = Counter(n for _, n in hits).most_common(1)[0][0] if label == 'zone' else keep[0][1]
        return first, f'{label} ({items_txt}) {old} -> {first} SHORT, pads {pads}'

    zone_net = {}  # id(zone) -> resolved net
    for k, z in copper:
        if k != 'zone' or z.GetNetname() not in amb:
            continue
        old = z.GetNetname()
        hits = []
        for ref, fp in fps.items():
            for pad in fp.Pads():
                new = pins.get((ref, pad.GetNumber()))
                if not new:
                    continue
                for l in cu_layers(z):
                    if pad.IsOnLayer(l) and z.GetFilledPolysList(l).Collide(pad.GetEffectiveShape(l), 0):
                        hits.append((f'{ref}.{pad.GetNumber()}' + ('*' if pad.GetNetname() != new else ''), new))
                        break
        txt = ' '.join(b.GetLayerName(l) for l in cu_layers(z)) + f', {len(hits)} pads in the fill'
        net, line = pick('zone', old, hits, txt)
        if line:
            rep.append(line)
        if net:
            out[id(z)] = zone_net[id(z)] = net

    for o in amb:
        items = [t for k, t in copper if k in ('track', 'arc', 'via') and t.GetNetname() == o]
        grid = Grid()
        for i, t in enumerate(items):
            grid.add(i, t.GetBoundingBox())
        up = list(range(len(items)))

        def find(i):
            while up[i] != i:
                up[i] = up[up[i]]
                i = up[i]
            return i
        for i, t in enumerate(items):
            for p, ls in anchors(t):
                for j in grid.at(p):
                    u = items[j]
                    if j != i and any(u.IsOnLayer(l) for l in ls) and u.HitTest(p, ACC):
                        up[find(i)] = find(j)
        clusters = defaultdict(list)
        for i in range(len(items)):
            clusters[find(i)].append(items[i])
        kept = 0
        zones = [z for k, z in copper if k == 'zone' and z.GetNetname() == o and id(z) in zone_net]
        for n, cl in enumerate(sorted(clusters.values(), key=lambda c: bbox_txt(c, b)), 1):
            hits = set()
            for t in cl:
                for p, ls in anchors(t):
                    for lbl, new, pad in pgrid.at(p):
                        if any(pad.IsOnLayer(l) and pad.HitTest(p, ACC, l) for l in ls):
                            hits.add((lbl, new))
            if not hits:  # stitching copper: follow the zone it lands in
                zn = {zone_net[id(z)] for z in zones for t in cl for p, ls in anchors(t)
                      for l in ls if z.IsOnLayer(l) and z.GetFilledPolysList(l).Contains(p)}
                if len(zn) == 1:
                    net = zn.pop()
                    for t in cl:
                        out[id(t)] = net
                    if net != o:
                        rep.append(f'cluster {o} #{n} ({bbox_txt(cl, b)}) -> {net} by zone')
                    else:
                        kept += 1
                    continue
            net, line = pick(f'cluster {o} #{n}', o, sorted(hits), bbox_txt(cl, b))
            if net:
                for t in cl:
                    out[id(t)] = net
            moved = any(l.endswith('*') for l, _ in hits)
            if net == o and not moved and not (line and 'SHORT' in line):
                kept += 1
            elif line:
                rep.append(line)
        rep.append(f'{o}: {len(clusters)} clusters, {kept} keep {o}')

    for k, it in copper:  # loose pads and copper shapes: keep the name if it survives
        if k in ('pad', 'shape') and it.GetNetname() in amb:
            if it.GetNetname() in newnames:
                out[id(it)] = it.GetNetname()
            else:
                lost.append(f'{k} {where(it, b)} {it.GetNetname()}: ambiguous and gone')
    return out, shorts, lost, rep


def stop(msg, items):
    print(msg)
    for i in items:
        print('  ', i)
    sys.exit('relink: nothing written')


def main(argv):
    if len(argv) < 2 or argv[:1] in (['-h'], ['--help']):
        sys.exit(__doc__)
    netlist, src = argv[0], argv[1]
    allow_shorts = '--allow-shorts' in argv
    argv = [a for a in argv if a != '--allow-shorts']
    save = None
    if '--save' in argv:
        i = argv.index('--save')
        save = argv[i + 1] if len(argv) > i + 1 and not argv[i + 1].startswith('-') else src

    comps, pins, nets = parse(netlist)
    b = T.load(src)
    fps = {fp.GetReference(): fp for fp in b.GetFootprints()}
    # board-only parts (mounting holes, logos) have no symbol; their pads
    # follow the map like copper
    loose = {r: fps.pop(r) for r in sorted(fps) if r not in comps and fps[r].IsBoardOnly()}

    only_b = sorted(set(fps) - set(comps))
    only_n = sorted(set(comps) - set(fps))
    if only_b or only_n:
        stop('footprints and netlist differ:',
             [f'on board only: {" ".join(only_b)}', f'in netlist only: {" ".join(only_n)}'])

    # pads: old -> new, checked before anything is changed
    fwd, rev = defaultdict(set), defaultdict(set)
    plan, bad, seen = [], [], set()
    for ref, fp in sorted(fps.items()):
        for pad in fp.Pads():
            num = pad.GetNumber()
            old = pad.GetNetname()
            new = pins.get((ref, num))
            seen.add((ref, num))
            if new is None:
                if old:
                    bad.append(f'{ref} pad {num} {old}: no netlist node')
                continue
            fwd[old].add(new)
            rev[new].add(old)
            plan.append((pad, new))
    if bad:
        stop('pads with a net the netlist does not have:', bad)
    nmap = {o: next(iter(n)) for o, n in fwd.items()
            if len(n) == 1 and len(rev[next(iter(n))]) == 1}
    amb = sorted(set(fwd) - set(nmap))
    newnames = set(rev)
    nopin = sorted(f'{r} pin {p} ({pins[(r, p)]})' for (r, p) in pins if (r, p) not in seen)
    padless = sorted(n for n in nets if n not in rev)

    # copper: every netted item must be on a net some pad had
    def netted(it):
        return it.GetNetCode() > 0
    drawings = [d for d in T.seq(b.Drawings()) if hasattr(d, 'GetNetCode') and netted(d)]
    copper = [(kind(t), t) for t in T.tracks(b) if netted(t)]
    copper += [('zone', z) for z in T.zones(b) if netted(z)]
    copper += [('shape', d) for d in drawings]
    copper += [('pad', p) for fp in loose.values() for p in fp.Pads() if netted(p)]
    orphan = [f'{k} {where(it, b)} {it.GetNetname()}' for k, it in copper if it.GetNetname() not in fwd]
    if orphan:
        stop('copper on nets no pad had:', orphan)

    # ambiguous old nets: copper takes the new net of the pads it touches
    newnet = {}  # id(item) -> new net for copper on ambiguous nets
    shorts, lost, report = [], [], []
    if amb:
        tally = defaultdict(Counter)
        for pad, new in plan:
            tally[pad.GetNetname()][new] += 1
        for o in amb:
            report.append(f'ambiguous {o} -> ' + ', '.join(f'{n} ({c} pads)' for n, c in sorted(tally[o].items())))
        newnet, shorts, lost, rep = resolve(b, amb, copper, fps, pins, newnames, allow_shorts)
        report += rep
    if lost or (shorts and not allow_shorts):
        for line in report:
            print(line)
        stop('copper on ambiguous nets that cannot be resolved:',
             [f'short: {x}' for x in shorts] + [f'no net: {x}' for x in lost])

    # apply
    netinfo = {}

    def net(name):
        if name not in netinfo:
            ni = b.FindNet(name)
            if ni is None:
                ni = pcbnew.NETINFO_ITEM(b, name)
                b.Add(ni)
            netinfo[name] = ni
        return netinfo[name]

    npath = 0
    for ref, fp in fps.items():
        path, sname, sfile = comps[ref]
        if fp.GetPath().AsString() != path:
            npath += 1
        fp.SetPath(pcbnew.KIID_PATH(path))
        fp.SetSheetname(sname)
        fp.SetSheetfile(sfile)
    count = Counter()
    for pad, new in plan:
        if pad.GetNetname() != new:
            count['pad'] += 1
        pad.SetNet(net(new))
    for k, it in copper:
        old = it.GetNetname()
        new = nmap[old] if old in nmap else newnet[id(it)]
        if old != new:
            count[k] += 1
            it.SetNet(net(new))

    n_before = b.GetNetCount()
    # RemoveUnusedNets(None) is a no-op without a BOARD_COMMIT (not in the
    # python API); Remove() on the NETINFO_ITEM drops it cleanly
    used = {it.GetNetCode() for it in T.seq(b.AllConnectedItems())}
    for code, ni in list(b.GetNetsByNetcode().items()):
        if code > 0 and code not in used:
            b.Remove(ni)
    b.BuildConnectivity()
    stale = sorted(n for n in set(fwd) - newnames if n and b.FindNet(n) is not None)
    removed = n_before - b.GetNetCount()

    name = os.path.splitext(os.path.basename(src))[0]
    work = os.path.join(os.path.expanduser('~/.cache/pcbkit'), name, 'relink')
    os.makedirs(work, exist_ok=True)
    out = os.path.join(work, os.path.basename(src))
    sdir = os.path.dirname(os.path.abspath(src))
    pro = os.path.splitext(src)[0] + '.kicad_pro'
    if os.path.exists(pro):
        shutil.copy(pro, os.path.splitext(out)[0] + '.kicad_pro')
    else:
        print(f'!! no {pro}: DRC runs with default rules')
    flt = os.path.join(sdir, 'fp-lib-table')
    if os.path.exists(flt):  # DRC checks footprint libs; point the copy at the project's
        with open(os.path.join(work, 'fp-lib-table'), 'w') as f:
            f.write(open(flt).read().replace('${KIPRJMOD}', sdir))

    pcbnew.ZONE_FILLER(b).Fill(b.Zones())
    b.Save(out)

    renamed = sorted((o, n) for o, n in nmap.items() if o != n)
    for o, n in renamed:
        print(f'{o} -> {n}')
    for line in report:
        print(line)
    print(f'{len(renamed)} nets renamed, {len(amb)} ambiguous, {removed} unused nets removed, '
          f'{b.GetNetCount()} nets on board')
    print(f'{len(fps)} footprints relinked, {npath} paths changed, '
          f'{len(loose)} board-only left alone ({" ".join(loose)}); renamed: '
          + ', '.join(f'{count[k]} {k}s' for k in ('pad', 'track', 'arc', 'via', 'zone', 'shape')))
    if stale:
        print('!! old nets still on the board:', ' '.join(stale))
    if padless:
        print(f'nets with no pad on the board ({len(padless)}):', ' '.join(padless))
    if nopin:
        print(f'netlist pins with no pad ({len(nopin)}):', ' '.join(nopin))
    kinds, unconn, rpt = drc(out)
    print(f'DRC {sum(kinds.values())} violations, {unconn} unconnected ({rpt})')
    for k, v in sorted(kinds.items()):
        print(f'  {v} {k}')
    if save:
        shutil.copy(out, save)
        print('saved', save)
    else:
        print('copy at', out)
