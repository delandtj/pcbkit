"""Move a routed board to a regenerated schematic: new symbol paths and
new net names, same copper. Nothing moves.

Usage: pcbkit relink <netlist.xml> <board.kicad_pcb> [--save [out.kicad_pcb]]

Export the netlist with `kicad-cli sch export netlist --format kicadxml`.
For every footprint, by reference: the KIID path (sheet tstamps +
component tstamp), Sheetname and Sheetfile are set from the netlist as
mkboard sets them, and every pad gets the netlist's net for (ref, pin).
The old -> new net map those pads imply must be a bijection; tracks,
vias, zones and copper shapes are renamed through it. The tool stops,
writing nothing, when footprints and netlist components differ, when the
map is not a bijection, when a pad with a net has no netlist node, or
when copper carries a net that no pad maps.

Unused old nets are dropped, zones refilled and kicad-cli DRC run; the
rename map (changed names only), the counts and the DRC summary are
printed. The copy goes to ~/.cache/pcbkit/<board>/relink with the
.kicad_pro (and fp-lib-table) beside it; --save writes it back to the
board, or to the given path.
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


def stop(msg, items):
    print(msg)
    for i in items:
        print('  ', i)
    sys.exit('relink: nothing written')


def main(argv):
    if len(argv) < 2 or argv[:1] in (['-h'], ['--help']):
        sys.exit(__doc__)
    netlist, src = argv[0], argv[1]
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
    split = [f'{o or "<no net>"} -> {" ".join(sorted(n))}' for o, n in sorted(fwd.items()) if len(n) > 1]
    merge = [f'{" ".join(sorted(o or "<no net>" for o in olds))} -> {n}'
             for n, olds in sorted(rev.items()) if len(olds) > 1]
    if split or merge:
        stop('old -> new net map is not a bijection:',
             [f'split {s}' for s in split] + [f'merged {m}' for m in merge])
    nmap = {o: next(iter(n)) for o, n in fwd.items()}
    nopin = sorted(f'{r} pin {p} ({pins[(r, p)]})' for (r, p) in pins if (r, p) not in seen)
    padless = sorted(n for n in nets if n not in rev)

    # copper: every netted item must be on a mapped net
    def netted(it):
        return it.GetNetCode() > 0
    drawings = [d for d in T.seq(b.Drawings()) if hasattr(d, 'GetNetCode') and netted(d)]
    copper = [('track' if t.GetClass() == 'PCB_TRACK' else 'arc' if t.GetClass() == 'PCB_ARC' else 'via', t)
              for t in T.tracks(b) if netted(t)]
    copper += [('zone', z) for z in T.zones(b) if netted(z)]
    copper += [('shape', d) for d in drawings]
    copper += [('pad', p) for fp in loose.values() for p in fp.Pads() if netted(p)]
    orphan = [f'{k} {where(it, b)} {it.GetNetname()}' for k, it in copper if it.GetNetname() not in nmap]
    if orphan:
        stop('copper on nets no pad maps:', orphan)

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
        new = nmap[it.GetNetname()]
        if it.GetNetname() != new:
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
    stale = sorted(n for n in (set(nmap) - set(nmap.values())) if b.FindNet(n) is not None)
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
    print(f'{len(renamed)} nets renamed, {removed} unused nets removed, '
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
