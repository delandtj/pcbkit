"""JLCPCB fabrication and assembly outputs.

Usage: pcbkit jlcfab [board.kicad_pcb] [out_dir]

Defaults: the project board and <root>/<[jlcfab] out> (fab/). Writes:
  gerbers/                 Gerbers for every copper layer (Protel extensions,
                           no X2/netlist, soldermask subtracted from silk) +
                           Excellon drill (PTH and NPTH separate, mm, absolute
                           origin; no drill-map Gerbers, JLC would read them
                           as layers)
  <name>-gerbers.zip       the above, ready to upload
  <name>-bom.csv           Comment, Designator, Footprint, LCSC Part #
  <name>-cpl.csv           Designator, Mid X, Mid Y, Layer, Rotation
  <name>-rotations.txt     parts whose rotation or centroid was corrected,
                           plus any part missing from the corrections table

DNP footprints (unless skip_dnp = false) and footprints without an LCSC
field are left out of the BOM and CPL. CPL coordinates are absolute board
mm with Y flipped, same as the Gerbers. Through-hole parts use the centre of
their pads (their footprint origin is usually pin 1); SMD parts use the
footprint origin. The corrections ([jlcfab] corrections, then
pcbkit.jlc.CORRECTIONS) then move that to the EasyEDA origin.
"""
import csv
import math
import os
import re
import shutil
import subprocess
import sys
import zipfile

import pcbnew

from . import config, jlc


def run(*args):
    r = subprocess.run(['kicad-cli', *args], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit('kicad-cli %s failed:\n%s%s' % (args[2], r.stdout, r.stderr))


def main(argv):
    if len(argv) > 2:
        sys.exit(__doc__)
    cfg = config.load(argv[0] if argv else None)
    J = cfg['jlcfab']
    pcb = argv[0] if argv else os.path.join(cfg['_root'], config.board_name(cfg) + '.kicad_pcb')
    out = argv[1] if len(argv) > 1 else config.rel(cfg, J['out'])
    name = os.path.splitext(os.path.basename(pcb))[0]
    corrections = [(p, l or None, r, dx, dy) for p, l, r, dx, dy in J['corrections']] + jlc.CORRECTIONS
    board = pcbnew.LoadBoard(pcb)
    layers = J['layers'] or jlc.gerber_layers(board.GetCopperLayerCount())

    gdir = os.path.join(out, 'gerbers')
    shutil.rmtree(gdir, ignore_errors=True)
    os.makedirs(gdir)
    run('pcb', 'export', 'gerbers', '--layers', layers, *jlc.GERBER_ARGS, '-o', gdir + '/', pcb)
    run('pcb', 'export', 'drill', *jlc.DRILL_ARGS, '-o', gdir + '/', pcb)

    zpath = os.path.join(out, name + '-gerbers.zip')
    with zipfile.ZipFile(zpath, 'w', zipfile.ZIP_DEFLATED) as z:
        for f in sorted(os.listdir(gdir)):
            z.write(os.path.join(gdir, f), f)

    mm = pcbnew.ToMM
    lf, mf = J['lcsc_field'], J['mpn_field']
    bom = {}
    cpl = []
    corrected = []
    unverified = []
    skipped = []
    for fp in sorted(board.GetFootprints(), key=lambda f: f.GetReference()):
        ref = fp.GetReference()
        lcsc = fp.GetFieldText(lf).strip() if fp.HasField(lf) else ''
        dnp = fp.IsDNP() and J['skip_dnp']
        if dnp or not lcsc:
            skipped.append('%s (%s)' % (ref, 'DNP' if dnp else 'no LCSC'))
            continue
        fpname = fp.GetFPID().GetLibItemName().wx_str()
        mpn = fp.GetFieldText(mf).strip() if fp.HasField(mf) else ''
        e = bom.setdefault(lcsc, {'refs': [], 'values': set(), 'fp': fpname, 'mpn': mpn})
        e['refs'].append(ref)
        e['values'].add(fp.GetValue())

        pads = list(fp.Pads())
        # footprint attribute, not pads: a module's thermal pad has plated vias
        tht = bool(fp.GetAttributes() & pcbnew.FP_THROUGH_HOLE)
        if tht and pads:
            xs = [mm(p.GetPosition().x) for p in pads]
            ys = [mm(p.GetPosition().y) for p in pads]
            x, y = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
        else:
            x, y = mm(fp.GetPosition().x), mm(fp.GetPosition().y)
        rot = fp.GetOrientationDegrees()
        for pat, part, corr, dx, dy in corrections:
            if re.match(pat, fpname) and part in (None, lcsc):
                if dx or dy:
                    if fp.IsFlipped():
                        sys.exit('%s: centroid offset on a bottom-side part, not handled' % ref)
                    # KiCad orientation is CCW on screen with y down
                    a = math.radians(rot)
                    x += dx * math.cos(a) + dy * math.sin(a)
                    y += -dx * math.sin(a) + dy * math.cos(a)
                if corr or dx or dy:
                    corrected.append('%s %s %s: rot %g -> %g, offset (%g, %g)'
                                     % (ref, fpname, lcsc, rot % 360, (rot + corr) % 360, dx, dy))
                rot += corr
                break
        else:
            if not re.match(jlc.UNPOLARIZED, fpname):
                unverified.append('%s %s %s' % (ref, fpname, lcsc))
        side = 'Bottom' if fp.IsFlipped() else 'Top'
        cpl.append([ref, '%.4fmm' % x, '%.4fmm' % -y, side, '%g' % (rot % 360)])

    bom_path = os.path.join(out, name + '-bom.csv')
    with open(bom_path, 'w', newline='') as f:
        w = csv.writer(f, quoting=csv.QUOTE_ALL)
        w.writerow(['Comment', 'Designator', 'Footprint', 'LCSC Part #'])
        natural = lambda r: (re.sub(r'\d+', '', r), int(re.sub(r'\D', '', r) or 0))
        for lcsc, e in sorted(bom.items(), key=lambda kv: natural(sorted(kv[1]['refs'], key=natural)[0])):
            # one value -> use it; several (e.g. per-channel connector names) -> the MPN
            comment = next(iter(e['values'])) if len(e['values']) == 1 else (e['mpn'] or sorted(e['values'])[0])
            w.writerow([comment, ','.join(sorted(e['refs'], key=natural)), e['fp'], lcsc])

    cpl_path = os.path.join(out, name + '-cpl.csv')
    with open(cpl_path, 'w', newline='') as f:
        w = csv.writer(f, quoting=csv.QUOTE_ALL)
        w.writerow(['Designator', 'Mid X', 'Mid Y', 'Layer', 'Rotation'])
        w.writerows(cpl)

    with open(os.path.join(out, name + '-rotations.txt'), 'w') as f:
        f.write('Rotation/centroid corrections applied (KiCad -> JLC), derived from\n'
                'the EasyEDA footprints. Still check them in the JLC placement preview.\n\n')
        f.write('\n'.join(corrected) + '\n')
        if unverified:
            f.write('\nNOT IN THE TABLE (written uncorrected, verify):\n')
            f.write('\n'.join(unverified) + '\n')

    print('gerbers + drill:', len(os.listdir(gdir)), 'files ->', zpath)
    print('BOM:', len(bom), 'lines,', sum(len(e['refs']) for e in bom.values()), 'parts ->', bom_path)
    print('CPL:', len(cpl), 'placements,', len(corrected), 'rotation-corrected ->', cpl_path)
    print('left out:', ', '.join(skipped))
    if unverified:
        print('WARNING: no rotation entry for:', ', '.join(unverified))
