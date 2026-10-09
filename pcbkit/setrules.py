"""Apply a stackup preset and inner-layer roles to the .kicad_pcb, and board
rules plus net class values to the .kicad_pro, in place.

Usage: pcbkit setrules <board.kicad_pcb> <board.kicad_pro>

[rules] stackup names a preset in pcbkit.jlc.STACKUPS (or stackup_file a
raw (stackup ...) block); it is inserted only when the board has none.
layers = {"In1.Cu" = ["power", "GND"]} retypes inner layers still marked
signal. board = {...} overrides the JLC minimums in pcbkit.jlc.RULES.
netclasses = {NAME = [track, clearance, via_dia, via_drill, dp_width, dp_gap]}
(the last two only for diff-pair classes); classes in the .kicad_pro without
an entry are reported and left alone. Run after mkboard.
"""
import json, os, re, sys

from . import config, jlc


def stackup(cfg, R):
    if R['stackup_file']:
        return open(config.rel(cfg, R['stackup_file'])).read()
    if R['stackup']:
        if R['stackup'] not in jlc.STACKUPS:
            sys.exit('unknown stackup %r, presets: %s' % (R['stackup'], ', '.join(jlc.STACKUPS)))
        return jlc.STACKUPS[R['stackup']]
    return ''


def main(argv):
    if len(argv) != 2:
        sys.exit(__doc__)
    pcb, pro = argv
    cfg = config.load(pcb)
    R = cfg['rules']

    s = open(pcb).read()
    st = stackup(cfg, R)
    if st and "(stackup" not in s:
        s = s.replace("\t(setup\n", "\t(setup\n" + st, 1)
    for name, (typ, user) in R['layers'].items():
        s = re.sub(r'\((\d+) "%s" signal\)' % re.escape(name),
                   lambda m: '(%s "%s" %s "%s")' % (m[1], name, typ, user), s)
    open(pcb, "w").write(s)

    p = json.load(open(pro))
    p["board"]["design_settings"]["rules"].update(R['board'])
    for c in p["net_settings"]["classes"]:
        v = R['netclasses'].get(c["name"])
        if not v:
            print("no values for", c["name"]); continue
        t, cl, vd, vh = v[:4]
        c.update({"track_width": t, "clearance": cl, "via_diameter": vd, "via_drill": vh})
        c.update(R['netclass_extra'])
        if len(v) > 4:
            c.update({"diff_pair_width": v[4], "diff_pair_gap": v[5],
                      "diff_pair_via_gap": R['diff_pair_via_gap']})
    json.dump(p, open(pro, "w"), indent=2)
    open(pro, "a").write("\n")
    print("ok")
