"""Shell settings for the KiCadRoutingTools wrappers (sh/krt_route.sh,
sh/krt_reroute.sh), from [krt] in pcbkit.toml.

Usage: pcbkit krt-env route|reroute     (prints bash assignments to eval)

KRT is located by KRT_DIR (env) or [krt] dir; its python (a venv that can
import pcbnew) by KRT_PY or [krt] python, default <dir>/../venv/bin/python.
The scratch dir (outside the project: KRT rewrites the .kicad_pro next to
the board) is KRT_WORK or [krt] work / rework, default
~/.cache/pcbkit/<name>/route and .../reroute.
"""
import os, shlex, sys

from . import config


def arr(name, items):
    return '%s=(%s)' % (name, ' '.join(shlex.quote(str(i)) for i in items))


def var(name, v):
    return '%s=%s' % (name, shlex.quote(str(v)))


def main(argv):
    if len(argv) != 1 or argv[0] not in ('route', 'reroute'):
        sys.exit(__doc__)
    mode = argv[0]
    cfg = config.load(None)
    K = cfg['krt']
    name = config.board_name(cfg)
    kdir = os.environ.get('KRT_DIR') or os.path.expanduser(K['dir'])
    if not kdir:
        sys.exit('set KRT_DIR or [krt] dir to the KiCadRoutingTools checkout')
    kpy = os.environ.get('KRT_PY') or os.path.expanduser(K['python']) \
        or os.path.join(os.path.dirname(kdir.rstrip('/')), 'venv', 'bin', 'python')
    work = os.environ.get('KRT_WORK') or os.path.expanduser(K['work' if mode == 'route' else 'rework']) \
        or os.path.expanduser('~/.cache/pcbkit/%s/%s' % (name, mode))
    via_d, via_h = K['via']
    common = ['--clearance', K['clearance'], '--via-size', via_d, '--via-drill', via_h, '--strict-sizes',
              '--same-net-pad-clearance', K['same_net_pad_clearance']]
    sig = ['--layers', *K['signal_layers'], '--track-width', K['track_width'],
           '--escalation', 'off', '--keep-input-copper']
    power = ['--power-nets', *K['power_nets'], '--power-nets-widths', *K['power_widths']] if K['power_nets'] else []
    P = K['planes']
    planes = ['--nets', *P['nets'], '--plane-layers', *P['layers'],
              '--via-size', P['via'][0], '--via-drill', P['via'][1]] if P['nets'] else []
    g = K['ground']
    lines = [var('PROJ', cfg['_root']), var('NAME', name), var('KRT_DIR', kdir), var('KRT_PY', kpy),
             var('WORK', work), arr('COMMON', common), arr('SIG', sig), arr('POWER', power),
             arr('ROUTE_EXTRA', K['route_extra']), arr('PLANES', planes),
             var('PLANES_DESC', ', '.join('%s on %s' % nl for nl in zip(P['nets'], P['layers']))),
             var('GROUND_NET', g['net']),
             arr('GROUND', ['--nets', g['net'], '--power-nets', g['net'], '--power-nets-widths', g['width']]),
             var('DIFF_N', len(K['diff'])),
             arr('KEEP_GROUPS', K.get('keep_groups', []))]
    for i, d in enumerate(K['diff']):
        lines.append(var('DIFF_DESC_%d' % i, d.get('desc', ' '.join(d['nets']))))
        lines.append(arr('DIFF_%d' % i, ['--nets', *d['nets'], '--track-width', d['width'],
                                         '--diff-pair-gap', d['gap'], *d.get('extra', [])]))
    print('\n'.join(lines))
