"""pcbkit: scripted KiCad board steps. Run with the KiCad python.

Usage: pcbkit [--config FILE] <tool> [args]     (or python3 -m pcbkit ...)

  mkboard     <netlist.xml> <project_dir> <out.kicad_pcb>
  outline     <in.kicad_pcb> <out.kicad_pcb> [length] [height] [--origin X,Y]
  setrules    <board.kicad_pcb> <board.kicad_pro>
  silk        <in.kicad_pcb> <out.kicad_pcb>
  connlabels  <in.kicad_pcb> <out.kicad_pcb>
  viaout      <in.kicad_pcb> <out.kicad_pcb>
  gndpour     <in.kicad_pcb> <out.kicad_pcb>
  prune       <board.kicad_pcb> <drc.rpt>
  jlcfab      [board.kicad_pcb] [out_dir]
  render      <board.kicad_pcb> <out.png> x0 y0 x1 y1 [--rpt drc.rpt] [NET ...]
  krt-route                                   (in the project dir)
  krt-reroute [--keep] NET [NET ...]          (in the project dir)
  krt-env     route|reroute

Settings come from pcbkit.toml (see README.md); `pcbkit <tool> -h` shows
a tool's notes.
"""
import importlib
import os
import sys

MODULES = {'mkboard': 'mkboard', 'outline': 'outline', 'setrules': 'setrules', 'silk': 'silk',
           'connlabels': 'connlabels', 'viaout': 'viaout', 'gndpour': 'gndpour', 'prune': 'prune',
           'jlcfab': 'jlcfab', 'render': 'pcbtool', 'krt-env': 'krt'}
SCRIPTS = {'krt-route': 'krt_route.sh', 'krt-reroute': 'krt_reroute.sh'}


def main(argv):
    from . import config
    for i, a in enumerate(argv):
        if a == '--config' or a.startswith('--config='):
            v = a.split('=', 1)[1] if '=' in a else argv[i + 1]
            del argv[i:i + (1 if '=' in a else 2)]
            config.override = os.path.abspath(v)
            os.environ['PCBKIT_CONFIG'] = config.override
            break
    if not argv or argv[0] in ('-h', '--help') or argv[0] not in MODULES.keys() | SCRIPTS.keys():
        sys.exit(__doc__)
    tool, args = argv[0], argv[1:]
    if tool in SCRIPTS:
        sh = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sh', SCRIPTS[tool])
        os.execvp('bash', ['bash', sh, *args])
    mod = importlib.import_module('pcbkit.' + MODULES[tool])
    if args[:1] in (['-h'], ['--help']):
        print(mod.__doc__)
        return
    mod.main(args)


if __name__ == '__main__':
    main(sys.argv[1:])
