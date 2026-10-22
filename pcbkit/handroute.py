"""Scripted hand routing: apply a plan of deletes and H/V/45 tracks and
vias to a copy of the board, refill zones, run kicad-cli DRC and report
what is wrong in a region. Loop on the plan until it is clean, then
--save writes the result back.

Usage: pcbkit handroute <board.kicad_pcb> <plan.py> [--save [out.kicad_pcb]]

plan.py defines OPS = [...] and optionally REGION = (x0, y0, x1, y1) in mm:
  ('delseg', net, layer, (x0, y0, x1, y1))      exact segment, either direction
  ('delvia', net, (x, y))
  ('delreg', net, layer_or_None, (x0, y0, x1, y1))
                                                 tracks (and vias) of the net
                                                 with an end in the region
  ('trk', net, layer, [(x, y), ...], width)      each leg must be H, V or 45
  ('via', net, (x, y), size, drill)
Layers are KiCad names (F.Cu, B.Cu, or the user names set by setrules,
e.g. SIG). Net names as the board stores them (a '/' inside a label is
'{slash}', e.g. 'Net-(U1-TRACK{slash}SS1)').

The copy goes to ~/.cache/pcbkit/<board>/handroute with the .kicad_pro
beside it (DRC needs the rules). The report lists clearance, shorting
and unconnected items inside REGION; silk, dangling and zone items are
left out. DRC does not refill zones by itself, so the tool does.
"""
import os
import re
import shutil
import subprocess
import sys

import pcbnew

from . import pcbtool as T

SKIP = ('silk', 'dangling', 'thermal', 'zone', 'lib_footprint', 'text_')


def near(a, b, tol=0.011):
    return abs(a - b) <= tol


def p2(p):
    return (T.mm(p.x), T.mm(p.y))


def apply(b, ops):
    """Run the plan's ops on board b; prints what did not match."""
    for op in ops:
        k = op[0]
        if k == 'delseg':
            _, net, layer, (x0, y0, x1, y1) = op
            hit = 0
            for t in T.tracks(b):
                if t.GetClass() != 'PCB_TRACK' or t.GetNetname() != net:
                    continue
                if layer and b.GetLayerName(t.GetLayer()) != layer:
                    continue
                s, e = p2(t.GetStart()), p2(t.GetEnd())
                if (near(s[0], x0) and near(s[1], y0) and near(e[0], x1) and near(e[1], y1)) or \
                        (near(s[0], x1) and near(s[1], y1) and near(e[0], x0) and near(e[1], y0)):
                    b.Delete(t)
                    hit += 1
            if hit != 1:
                print('!! delseg', op, 'matched', hit)
        elif k == 'delvia':
            _, net, (x, y) = op
            hit = 0
            for t in T.tracks(b):
                if t.GetClass() != 'PCB_VIA' or t.GetNetname() != net:
                    continue
                s = p2(t.GetStart())
                if near(s[0], x) and near(s[1], y):
                    b.Delete(t)
                    hit += 1
            if hit != 1:
                print('!! delvia', op, 'matched', hit)
        elif k == 'delreg':
            _, net, layer, (x0, y0, x1, y1) = op
            hit = 0
            for t in T.tracks(b):
                if t.GetNetname() != net:
                    continue
                if layer and t.GetClass() == 'PCB_TRACK' and b.GetLayerName(t.GetLayer()) != layer:
                    continue
                if any(x0 <= p[0] <= x1 and y0 <= p[1] <= y1 for p in (p2(t.GetStart()), p2(t.GetEnd()))):
                    b.Delete(t)
                    hit += 1
            print('delreg', net, layer, 'removed', hit)
        elif k == 'trk':
            _, net, layer, pts, w = op
            for (ax, ay), (bx, by) in zip(pts, pts[1:]):
                dx, dy = abs(bx - ax), abs(by - ay)
                if not (dx < 1e-6 or dy < 1e-6 or abs(dx - dy) < 1e-6):
                    print('!! not H/V/45:', net, (ax, ay), (bx, by))
            T.add_track(b, net, layer, pts, w)
        elif k == 'via':
            _, net, (x, y), size, drill = op
            T.add_via(b, net, x, y, size, drill)
        else:
            raise SystemExit('bad op ' + repr(op))


def drc(pcb, region):
    """kicad-cli DRC on pcb; returns (issue lines inside region, summary lines)."""
    rpt = pcb + '.rpt'
    subprocess.run(['kicad-cli', 'pcb', 'drc', '--schematic-parity', '--severity-all', '-o', rpt, pcb],
                   capture_output=True, text=True)
    x0, y0, x1, y1 = region
    cur, issues, summary = None, [], []
    for line in open(rpt):
        if line.startswith('['):
            cur = line.strip()
        elif cur and line.strip().startswith('@('):
            m = re.match(r'\s*@\(([-\d.]+) mm, ([-\d.]+) mm\): (.*)', line)
            if m and x0 <= float(m[1]) <= x1 and y0 <= float(m[2]) <= y1 and not any(s in cur for s in SKIP):
                issues.append(f'{cur} | {m[1]} {m[2]} {m[3].strip()}')
        elif line.startswith('** Found'):
            summary.append(line.strip())
    return issues, summary


def main(argv):
    if len(argv) < 2 or argv[:1] in (['-h'], ['--help']):
        sys.exit(__doc__)
    src, plan_path = argv[0], argv[1]
    save = None
    if '--save' in argv:
        i = argv.index('--save')
        save = argv[i + 1] if len(argv) > i + 1 and not argv[i + 1].startswith('-') else src
    plan = {}
    exec(open(plan_path).read(), plan)
    b = T.load(src)
    bb = T.box(b.GetBoardEdgesBoundingBox())
    region = plan.get('REGION', bb)

    name = os.path.splitext(os.path.basename(src))[0]
    work = os.path.join(os.path.expanduser('~/.cache/pcbkit'), name, 'handroute')
    os.makedirs(work, exist_ok=True)
    out = os.path.join(work, os.path.basename(src))
    pro = os.path.splitext(src)[0] + '.kicad_pro'
    if os.path.exists(pro):
        shutil.copy(pro, os.path.splitext(out)[0] + '.kicad_pro')

    apply(b, plan['OPS'])
    pcbnew.ZONE_FILLER(b).Fill(b.Zones())
    b.Save(out)
    issues, summary = drc(out, region)
    for line in issues:
        print(line)
    for line in summary:
        print(line)
    if save:
        shutil.copy(out, save)
        print('saved', save)
    else:
        print('copy at', out)
