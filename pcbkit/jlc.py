"""JLCPCB data: board rule minimums, Gerber/drill export settings, stackup
presets and the KiCad -> EasyEDA rotation/centroid table for the CPL.
"""

# 4-layer standard capabilities (written to the .kicad_pro by setrules)
RULES = {
    'min_clearance': 0.127, 'min_track_width': 0.127,
    'min_via_diameter': 0.45, 'min_through_hole_diameter': 0.2,
    'min_via_annular_width': 0.15, 'min_hole_clearance': 0.15,
    'min_hole_to_hole': 0.4, 'min_copper_edge_clearance': 0.3,
    'min_text_height': 0.8, 'min_text_thickness': 0.15,
}

# Protel extensions, no X2/netlist, soldermask subtracted from silk
GERBER_ARGS = ['--no-x2', '--no-netlist', '--subtract-soldermask', '--check-zones']
# PTH and NPTH separate, mm, absolute origin; no drill-map Gerbers (JLC
# would read them as layers)
DRILL_ARGS = ['--format', 'excellon', '--excellon-separate-th', '--excellon-units', 'mm',
              '--excellon-zeros-format', 'decimal', '--drill-origin', 'absolute']


def gerber_layers(copper):
    cu = ['F.Cu'] + ['In%d.Cu' % i for i in range(1, copper - 1)] + ['B.Cu']
    return ','.join(cu + ['F.Paste', 'B.Paste', 'F.Silkscreen', 'B.Silkscreen',
                          'F.Mask', 'B.Mask', 'Edge.Cuts'])


# (stackup ...) blocks inserted at the top of (setup ...)
STACKUPS = {
    # JLC04161H-7628: 4L 1.6 mm, 1 oz outer, 0.5 oz inner, 7628 prepreg
    'jlc04161h-7628': '''\t\t(stackup
\t\t\t(layer "F.SilkS" (type "Top Silk Screen"))
\t\t\t(layer "F.Paste" (type "Top Solder Paste"))
\t\t\t(layer "F.Mask" (type "Top Solder Mask") (thickness 0.01))
\t\t\t(layer "F.Cu" (type "copper") (thickness 0.035))
\t\t\t(layer "dielectric 1" (type "prepreg") (thickness 0.2104) (material "FR4 7628") (epsilon_r 4.4) (loss_tangent 0.02))
\t\t\t(layer "In1.Cu" (type "copper") (thickness 0.0152))
\t\t\t(layer "dielectric 2" (type "core") (thickness 1.065) (material "FR4") (epsilon_r 4.6) (loss_tangent 0.02))
\t\t\t(layer "In2.Cu" (type "copper") (thickness 0.0152))
\t\t\t(layer "dielectric 3" (type "prepreg") (thickness 0.2104) (material "FR4 7628") (epsilon_r 4.4) (loss_tangent 0.02))
\t\t\t(layer "B.Cu" (type "copper") (thickness 0.035))
\t\t\t(layer "B.Mask" (type "Bottom Solder Mask") (thickness 0.01))
\t\t\t(layer "B.Paste" (type "Bottom Solder Paste"))
\t\t\t(layer "B.SilkS" (type "Bottom Silk Screen"))
\t\t\t(dielectric_constraints no)
\t\t)
''',
}

# JLC places each part with its EasyEDA/LCSC library footprint: the CPL
# position is that footprint's origin and rotation 0 is its orientation.
# Entries below were derived by fetching the EasyEDA footprint for each LCSC
# number and fitting its numbered pads onto the KiCad pads at rotation 0
# (pin 1 / cathode / + checked against the EasyEDA symbol pin names and silk
# marks). Matched as (footprint regex, LCSC or None, rotation, dx, dy); first
# match wins. dx, dy (mm, KiCad frame at rotation 0, y down) is where the
# EasyEDA origin sits relative to our reference point (footprint origin for
# SMD, pad centre for THT); it is rotated with the part before use.
# Re-verify when an LCSC number changes: the same KiCad footprint can map to
# different EasyEDA layouts (USBLC6 vs LMR51430 SOT-23).
CORRECTIONS = [
    # SOT-23: EasyEDA layouts differ per part
    (r'^SOT-23-6$', 'C7519', 270, 0, 0),          # USBLC6-2SC6
    (r'^SOT-23-6$', 'C5210749', 180, 0, 0),       # LMR51430, pin 1 lower right
    (r'^SOT-23-5$', 'C141836', 180, 0, 0),        # TLV62569, pin 1 lower right
    (r'^LQFP-48_7x7mm_P0.5mm$', None, 270, 0, 0),
    (r'^SOIC-8_3.9x4.9mm_P1.27mm$', None, 270, 0, 0),
    (r'^SOIC-28W_7.5x17.9mm_P1.27mm$', None, 270, 0, 0),
    (r'^SOP-16_4.55x10.3mm_P1.27mm$', None, 270, 0, 0),
    (r'^SOIC-4_4.55x3.7mm_P2.54mm$', None, 0, 0, 0),   # photoMOS: same layout as KiCad
    (r'^USON-10_2.5x1.0mm_P0.5mm$', None, 270, 0, 0),
    # symmetric 4x0402 array: 90 and 270 are both correct, 0/180 are not
    (r'^R_Array_Convex_4x0402$', None, 90, 0, 0),
    # polarity: EasyEDA pad 1 = + on the tantalum-polymer (silk + mark)
    (r'^CP_EIA-7343-31_Kemet-D$', None, 180, 0, 0),
    # EasyEDA pad 1 = cathode (symbol pin K/C, silk bar) -> no change
    (r'^D_SMA$', None, 0, 0, 0),
    (r'^D_SOD-123F$', None, 0, 0, 0),
    (r'^D_SOD-923$', None, 0, 0, 0),
    (r'^LED_0603_1608Metric$', None, 0, 0, 0),
    (r'^Crystal_SMD_Abracon_ABM8G-4Pin_3.2x2.5mm$', None, 0, 0, 0),
    (r'^L_Sunlord_SWPA3015S$', None, 0, 0, 0),    # unpolarized; EasyEDA is 180
    (r'^L_Changjiang_FXL0630$', None, 0, 0, 0),
    # EasyEDA origin = centre of the pad array, KiCad origin = module centre
    (r'^ESP32-C6-WROOM-1$', None, 0, 0, 2.995),
    # EasyEDA origin 1.308 mm towards the signal-pad row from KiCad's
    (r'^USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal$', None, 0, 0, -1.308),
    # C165948: EasyEDA origin 1.424 mm towards the signal pads (shield slots)
    (r'^USB_C_Receptacle_HRO_TYPE-C-31-M-12$', None, 0, 0, -1.424),
    (r'^RJ45_Hanrun_HR911105A_Horizontal$', None, 0, 0, 0),
    # JST PH vertical: EasyEDA pin 1 is at the +x end, origin at pad centre
    (r'^JST_PH_B\dB-PH-K_1x\d\d_P2.00mm_Vertical$', None, 180, 0, 0),
]
# Two-terminal unpolarized parts need no entry.
UNPOLARIZED = r'^(R|C|L|Fuse)_\d{4}_\d{4}Metric$'
