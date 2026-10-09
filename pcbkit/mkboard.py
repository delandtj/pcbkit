"""Create a fresh .kicad_pcb from a KiCad XML netlist.

Usage: pcbkit mkboard <netlist.xml> <project_dir> <out.kicad_pcb>

Export the netlist with `kicad-cli sch export netlist --format kicadxml`.
Footprints are loaded from the project's fp-lib-table libs, [project] libs
or the system libs ([project] fp_dir), linked to their schematic symbols by
KIID path (so F8 / parity sees them as in sync), and every pad gets its net.
Parts are dropped in a grid per sheet left of x = 0, outside any outline;
real placement comes later. Only for an unplaced board. Write <out> to a
scratch directory and copy only the .kicad_pcb back: pcbnew's Save rewrites
the .kicad_pro next to the board.
"""
import os
import re
import sys
import xml.etree.ElementTree as ET
import pcbnew

from . import config


def project_libs(proj, cfg):
    libs = {}
    t = os.path.join(proj, 'fp-lib-table')
    if os.path.isfile(t):
        for name, uri in re.findall(r'\(name "([^"]*)"\)\s*\(type "[^"]*"\)\s*\(uri "([^"]*)"\)', open(t).read()):
            libs[name] = uri.replace('${KIPRJMOD}', proj)
    libs.update({k: os.path.join(proj, os.path.expanduser(v)) for k, v in cfg['project']['libs'].items()})
    return libs


def main(argv):
    if len(argv) != 3:
        sys.exit(__doc__)
    netlist, proj, out = argv
    cfg = config.load(os.path.join(proj, 'x'))
    M = cfg['mkboard']
    libs = project_libs(proj, cfg)

    def libpath(lib):
        return libs.get(lib, f"{cfg['project']['fp_dir']}/{lib}.pretty")

    root = ET.parse(netlist).getroot()
    board = pcbnew.BOARD()
    board.SetCopperLayerCount(M['copper_layers'])

    # nets
    pins = {}  # (ref, pad) -> NETINFO_ITEM
    for n in root.find("nets"):
        name = n.get("name")
        # the XML export unescapes '/' inside auto-generated pin-name nets
        if name.startswith(("Net-(", "unconnected-(")):
            name = name.replace("/", "{slash}")
        ni = pcbnew.NETINFO_ITEM(board, name)
        board.Add(ni)
        for node in n.findall("node"):
            pins[(node.get("ref"), node.get("pin"))] = ni

    sheet_col = {}
    sheet_count = {}
    missing = []
    for c in root.find("components"):
        ref = c.get("ref")
        fpid = c.findtext("footprint")
        if not fpid:
            missing.append(ref)
            continue
        lib, name = fpid.split(":", 1)
        fp = pcbnew.FootprintLoad(libpath(lib), name)
        if fp is None:
            sys.exit(f"cannot load {fpid} for {ref}")
        fp.SetReference(ref)
        fp.SetValue(c.findtext("value"))
        fp.SetFPID(pcbnew.LIB_ID(lib, name))

        sp = c.find("sheetpath")
        # multi-unit symbols list one uuid per unit; the footprint links to the first
        fp.SetPath(pcbnew.KIID_PATH(sp.get("tstamps") + c.findtext("tstamps").split()[0]))
        props = {p.get("name"): p.get("value") for p in c.findall("property")}
        fp.SetSheetname(props.get("Sheetname", ""))
        fp.SetSheetfile(props.get("Sheetfile", ""))
        for f in c.findall("fields/field"):
            fname = f.get("name")
            if fname in ("Footprint", "Reference", "Value"):
                continue
            fp.SetField(fname, f.text or "")
            fld = fp.GetField(fname)
            fld.SetVisible(False)
            fld.SetLayer(pcbnew.F_Fab)
        # mirror the symbol's BOM flag, as Update PCB from Schematic would
        attrs = fp.GetAttributes() & ~pcbnew.FP_EXCLUDE_FROM_BOM
        if "exclude_from_bom" in props:
            attrs |= pcbnew.FP_EXCLUDE_FROM_BOM
        fp.SetAttributes(attrs)
        if "dnp" in props:
            fp.SetDNP(True)

        # staging grid, one column block per sheet, left of origin
        sheet = props.get("Sheetname", "root")
        col = sheet_col.setdefault(sheet, len(sheet_col))
        i = sheet_count.get(sheet, 0)
        sheet_count[sheet] = i + 1
        x = M['stage_x'] + col * M['sheet_pitch'] + (i % M['per_row']) * M['cell']
        y = (i // M['per_row']) * M['cell']
        fp.SetPosition(pcbnew.VECTOR2I_MM(x, y))

        board.Add(fp)
        for pad in fp.Pads():
            ni = pins.get((ref, pad.GetNumber()))
            if ni is not None:
                pad.SetNet(ni)

    board.Save(out)
    print(f"saved {out}: {len(board.GetFootprints())} footprints, "
          f"{board.GetNetCount()} nets, missing fp: {missing}")
