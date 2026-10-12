# pcbkit

Scripted KiCad board steps: build a board from a netlist, outline and
holes, rules and stackup, silkscreen tidying, connector labels, autorouter
cleanup, ground pours, JLCPCB fab outputs. Python stdlib + pcbnew only
(render also needs matplotlib). Written against KiCad 10.

Run with the KiCad python (a `python3` that can `import pcbnew`):

    bin/pcbkit <tool> [args]              # or: PYTHONPATH=. python3 -m pcbkit <tool> ...
    bin/pcbkit --config other.toml <tool> [args]
    bin/pcbkit <tool> -h                  # a tool's notes

`PCBKIT_PYTHON` picks another interpreter for `bin/pcbkit` and the KRT
wrappers.

## Tools

| tool | usage | what it does |
|------|-------|--------------|
| mkboard | `<netlist.xml> <project_dir> <out.kicad_pcb>` | fresh board from a KiCad XML netlist (`kicad-cli sch export netlist --format kicadxml`): footprints from the fp-lib-table / system libs, linked to their symbols by KIID path, nets on every pad, parked in a per-sheet grid left of x = 0 |
| outline | `<in> <out> [length] [height] [--origin X,Y]` | Edge.Cuts rectangle with rounded corners, plus corner mounting holes (board-only footprints). Replaces an existing outline and holes |
| setrules | `<board.kicad_pcb> <board.kicad_pro>` | in place: stackup preset and inner-layer roles in the .kicad_pcb, board minimums and net class values in the .kicad_pro |
| silk | `<in> <out>` | values to F.Fab, refs hidden on small parts, line widths raised to the fab minimum, the rest of the refs placed clear of pads, silk, courtyards and each other |
| connlabels | `<in> <out>` | "J3 19V IN" style connector names and per-pin legends on F.Silkscreen, in a group that a re-run replaces. Run after silk |
| viaout | `<in> <out>` | moves vias out of SMD pads of the same net to the nearest clear spot, with stubs |
| prune | `<board.kicad_pcb> <drc.rpt>` | in place: deletes what a kicad-cli DRC report flags as via_dangling / track_dangling. Only when DRC shows 0 unconnected |
| gndpour | `<in> <out>` | ground pours on the outer layers (inset from the short edges for panel rails) and a stitching via grid in a re-runnable group; fills zones |
| jlcfab | `[board] [out_dir]` | Gerbers + drill zip, BOM and CPL for JLCPCB assembly, with the KiCad -> EasyEDA rotation/centroid corrections and a list of parts to check |
| render | `<board> <out.png> x0 y0 x1 y1 [--rpt drc.rpt] [NET ...]` | region PNG: F.Cu red, B.Cu blue, highlighted nets, DRC opens and violations |
| krt-route | (in the project dir) | first-pass routing with [KiCadRoutingTools](https://github.com/drandyhaas/KiCadRoutingTools): strip copper, planes, diff pairs, everything else |
| krt-reroute | `[--keep] NET ...` (in the project dir) | targeted KRT pass on named nets, then a ground pass and a zone refill |
| krt-env | `route\|reroute` | prints the bash settings the two KRT wrappers eval |

Tools that take `<in> <out>` save through pcbnew, which also rewrites the
.kicad_pro next to `<out>`: write `<out>` to a scratch directory and copy
only the .kicad_pcb back. `pcbtool.py` is also a library for scripted hand
routing (`add_track`, `add_via`, `delete_net_copper`, `pad_xy`, DRC report
parsing) and placement (`Placer`: `put`, `put_c`, `pack`, `sheet`).

## Pipeline

1. `mkboard` (unplaced board from the schematic netlist)
2. `outline`
3. `setrules`
4. place by hand (or a project script using `pcbtool.Placer`)
5. route: by hand and/or `krt-route`, then `krt-reroute` on what is open
6. `viaout`, then DRC and `prune` until clean
7. `gndpour`
8. `silk`
9. `connlabels`
10. `jlcfab`

Check after each step with `kicad-cli pcb drc --schematic-parity
--severity-all` (refill zones first: KRT does not refill).

## Config: pcbkit.toml

Found as: `--config FILE`, `$PCBKIT_CONFIG`, the first `pcbkit.toml` in the
board's directory or above, then the working directory or above. Without
one, the defaults below apply. Every key is optional; tables merge key by
key into the defaults. Relative paths are taken from the config's
directory (the project root). Table order matters for `connlabels` (labels
are placed greedily in that order).

```toml
[project]
name = "myboard"                 # board basename; default: the only *.kicad_pcb in the root
libs = { mylib = "footprints/mylib.pretty" }   # on top of the project fp-lib-table
fp_dir = "/usr/share/kicad/footprints"         # default: $KICAD10_FOOTPRINT_DIR or this

[mkboard]
copper_layers = 4
stage_x = -560.0                 # staging grid: x of the first column
sheet_pitch = 90.0               # one column block per sheet
cell = 14.0
per_row = 6

[outline]
length = 180.0                   # positional args override
height = 40.0
origin = [0.0, 0.0]              # --origin X,Y overrides
radius = 1.0
line_width = 0.1
hole_inset = 4.0
hole_lib = "MountingHole"
hole_fp = "MountingHole_3.2mm_M3"
hole_refs = ["H1", "H2", "H3", "H4"]    # [] for no holes

[rules]
stackup = "jlc04161h-7628"       # preset in pcbkit/jlc.py STACKUPS; "" leaves it alone
stackup_file = ""                # or a file with a raw (stackup ...) block
layers = { "In1.Cu" = ["power", "GND"], "In2.Cu" = ["mixed", "PWR"] }
board = { min_clearance = 0.127 }        # overrides pcbkit/jlc.py RULES key by key
netclass_extra = { microvia_diameter = 0.3, microvia_drill = 0.1 }
diff_pair_via_gap = 0.25

[rules.netclasses]
# name = [track, clearance, via_dia, via_drill(, diff_width, diff_gap)]
Default = [0.2, 0.15, 0.5, 0.2]
USB = [0.25, 0.2, 0.5, 0.2, 0.25, 0.15]

[gndpour]
net = "GND"
layers = ["F.Cu", "B.Cu"]
via = [0.6, 0.3]                 # stitching via diameter, drill
via_clearance = 0.3
edge_in = 1.5                    # edge row inset and pitch
edge_pitch = 4.0
grid = 5.0
short_edge_inset = 1.0           # pour stops this far from the short edges
zone_clearance = 0.25
zone_min_width = 0.2
priority = 1
group = "gnd-stitch"
fill = true

[silk]
size = 1.0                       # reference text height
thickness = 0.15
gap = 0.2
min_line = 0.15
small_area = 8.0                 # courtyard mm^2 below which the ref is hidden
hide_prefixes = ["H"]
order = ["J", "U"]               # placed first, in this order
relax_prefixes = ["J"]           # also try vertical, then ignore courtyards
right_overhang = 0.0             # parts may stick out this far on the right

[connlabels]
names = { J1 = "ETHERNET", J2 = "USB" }
legends = { J3 = ["+", "-"] }    # one string per numbered pad, in pad order
name_height = 1.0
legend_height = 1.0
thickness = 0.15
clearance = 0.2          # 0 = no --clearance flag, KRT uses the net class clearances
group = "conn-labels"

[viaout]
clearance = 0.21
keep = [["U1", "29"]]            # pads whose vias stay (thermal pads)
stub_width = 0.2

[jlcfab]
out = "fab"
layers = ""                      # default: all copper + paste, silk, mask, Edge.Cuts
lcsc_field = "LCSC"
mpn_field = "MPN"
skip_dnp = true
# extra [footprint regex, LCSC or "", rotation, dx, dy], before the built-in table
corrections = [["^MyConn_1x04$", "", 180, 0.0, 0.0]]

[krt]
dir = "~/Electronics/krt/KiCadRoutingTools"  # or $KRT_DIR
python = ""                      # or $KRT_PY; default <dir>/../venv/bin/python
work = ""                        # or $KRT_WORK; default ~/.cache/pcbkit/<name>/route
rework = ""                      # krt-reroute scratch; default .../reroute
clearance = 0.2
via = [0.5, 0.2]
track_width = 0.2
same_net_pad_clearance = 0.15    # keeps vias out of SMD pads
signal_layers = ["F.Cu", "B.Cu"]
# keep_groups = ["hand"]   # groups whose tracks/vias krt-route does not strip
route_extra = ["--ordering", "mps"]   # krt-route final pass only; $KRT_EXTRA adds to both
power_nets = ["GND", "+3V3"]
power_widths = [0.4, 0.4]
planes = { nets = ["GND", "+3V3"], layers = ["In1.Cu", "In2.Cu"], via = [0.6, 0.3] }
ground = { net = "GND", width = 0.4 }

[[krt.diff]]
desc = "USB 0.25/0.15"
nets = ["*USB*D+", "*USB*D-"]
width = 0.25
gap = 0.15
# extra = ["--diff-pair-intra-match", "--length-match-tolerance", "0.15"]  # more route_diff.py flags
```

A complete real-world config is in `examples/granite_controller/pcbkit.toml`.

## JLC data

`pcbkit/jlc.py` holds the JLCPCB knowledge: 4-layer rule minimums, the
Gerber and drill export flags, stackup presets (`jlc04161h-7628`: 4L 1.6 mm,
1 oz outer, 0.5 oz inner, 7628 prepreg) and the rotation/centroid table
mapping KiCad footprints to their EasyEDA/LCSC placement origin. Re-verify
an entry in JLC's placement preview when an LCSC number changes: the same
KiCad footprint can map to different EasyEDA layouts.
