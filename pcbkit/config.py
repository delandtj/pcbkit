"""Per-project settings from pcbkit.toml.

The file is found, in order: --config FILE (set by the dispatcher), the
PCBKIT_CONFIG env var, the first pcbkit.toml in the board's directory or
above, the first in the working directory or above. Without one every tool
runs on DEFAULTS. Tables merge key by key into DEFAULTS; relative paths are
taken from the directory holding the config (the project root).
"""
import glob
import os
import sys
import tomllib

from . import jlc

NAME = 'pcbkit.toml'
override = None

DEFAULTS = {
    'project': {
        'name': '',             # board basename; '' -> the only *.kicad_pcb in the root
        'libs': {},             # footprint lib name -> .pretty dir; added to fp-lib-table
        'fp_dir': os.environ.get('KICAD10_FOOTPRINT_DIR', '/usr/share/kicad/footprints'),
    },
    'mkboard': {
        'copper_layers': 4,
        'stage_x': -560.0,      # staging grid: first column, left of the outline
        'sheet_pitch': 90.0,    # one column block per sheet
        'cell': 14.0,
        'per_row': 6,
    },
    'outline': {
        'length': 180.0, 'height': 40.0, 'origin': [0.0, 0.0],
        'radius': 1.0, 'line_width': 0.1,
        'hole_inset': 4.0,
        'hole_lib': 'MountingHole', 'hole_fp': 'MountingHole_3.2mm_M3',
        'hole_refs': ['H1', 'H2', 'H3', 'H4'],
    },
    'rules': {
        'stackup': 'jlc04161h-7628',    # preset name, '' to leave the stackup alone
        'stackup_file': '',             # or a file with a raw (stackup ...) block
        'layers': {},                   # "In1.Cu" = ["power", "GND"]
        'board': dict(jlc.RULES),
        'netclass_extra': {'microvia_diameter': 0.3, 'microvia_drill': 0.1},
        'diff_pair_via_gap': 0.25,
        'netclasses': {},               # name = [track, clr, via_dia, via_drill(, dp_width, dp_gap)]
    },
    'gndpour': {
        'net': 'GND', 'layers': ['F.Cu', 'B.Cu'],
        'via': [0.6, 0.3], 'via_clearance': 0.3,
        'edge_in': 1.5, 'edge_pitch': 4.0, 'grid': 5.0,
        'short_edge_inset': 1.0,
        'zone_clearance': 0.25, 'zone_min_width': 0.2, 'priority': 1,
        'group': 'gnd-stitch', 'fill': True,
    },
    'silk': {
        'size': 1.0, 'thickness': 0.15, 'gap': 0.2, 'min_line': 0.15,
        'small_area': 8.0,
        'hide_prefixes': ['H'],
        'order': ['J', 'U'],
        'relax_prefixes': ['J'],
        'right_overhang': 0.0,
    },
    'connlabels': {
        'names': {}, 'legends': {},
        'name_height': 1.0, 'legend_height': 1.0, 'thickness': 0.15,
        'clearance': 0.2, 'group': 'conn-labels',
    },
    'viaout': {
        'clearance': 0.21, 'keep': [], 'stub_width': 0.2,
    },
    'jlcfab': {
        'out': 'fab', 'layers': '',
        'lcsc_field': 'LCSC', 'mpn_field': 'MPN', 'skip_dnp': True,
        'corrections': [],      # [pattern, lcsc or "", rot, dx, dy], checked before the built-in table
    },
    'krt': {
        'dir': '~/Electronics/krt/KiCadRoutingTools', 'python': '', 'work': '', 'rework': '',
        'clearance': 0.2, 'via': [0.5, 0.2], 'track_width': 0.2,
        'same_net_pad_clearance': 0.15,
        'signal_layers': ['F.Cu', 'B.Cu'],
        'planes': {'nets': [], 'layers': [], 'via': [0.6, 0.3], 'thermal_relief': True},
        'diff': [],             # [{desc, nets, width, gap}]
        'power_nets': [], 'power_widths': [],
        'route_extra': [],
        'ground': {'net': 'GND', 'width': 0.4},
    },
}


def merge(a, b):
    out = dict(a)
    for k, v in b.items():
        out[k] = merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def find(start):
    d = os.path.abspath(start)
    while True:
        p = os.path.join(d, NAME)
        if os.path.isfile(p):
            return p
        up = os.path.dirname(d)
        if up == d:
            return None
        d = up


def load(near=None):
    near = os.path.dirname(os.path.abspath(near)) if near else None
    path = override or os.environ.get('PCBKIT_CONFIG') or (near and find(near)) or find(os.getcwd())
    data = {}
    if path:
        with open(path, 'rb') as f:
            data = tomllib.load(f)
    cfg = merge(DEFAULTS, data)
    cfg['_file'] = path
    cfg['_root'] = os.path.dirname(os.path.abspath(path)) if path else (near or os.getcwd())
    return cfg


def rel(cfg, p):
    """Expand ~ and resolve p against the project root."""
    return os.path.join(cfg['_root'], os.path.expanduser(p)) if p else p


def board_name(cfg):
    if cfg['project']['name']:
        return cfg['project']['name']
    pcbs = glob.glob(os.path.join(cfg['_root'], '*.kicad_pcb'))
    if len(pcbs) != 1:
        sys.exit('set [project] name in %s: %d boards in %s' % (NAME, len(pcbs), cfg['_root']))
    return os.path.splitext(os.path.basename(pcbs[0]))[0]
