#!/usr/bin/env bash
# Route the board with KiCadRoutingTools (drandyhaas), as a first pass for hand
# routing.
#
#   pcbkit krt-route        # in the project dir: route a copy, copy the .kicad_pcb back
#
# Starts from the board as it is (hand placement kept). Existing tracks, vias
# and copper zones are stripped first; rule areas, items in the [krt]
# keep_groups groups (scripted hand routes) and everything else stay. The
# router sees the kept copper (--keep-input-copper).
#
# Chain (KRT README, #562): planes first ([krt] planes), then each [[krt.diff]]
# pair class, then everything on [krt] signal_layers. KRT rewrites the
# .kicad_pro next to the board it routes ("FAB FLOOR RELAXED") and grades
# itself against that, so it runs on a copy outside the project with
# --escalation off --strict-sizes, and only the .kicad_pcb comes back. Grade
# with `kicad-cli pcb drc --schematic-parity`, never with KRT's own checks.
#
# Env: KRT_DIR (checkout), KRT_PY (venv python with pcbnew visible),
#      KRT_WORK (scratch dir, must be outside the project), KRT_EXTRA (more
#      route.py flags for the final pass); all default from [krt].
set -euo pipefail

HERE=$(cd "$(dirname "$0")/../.." && pwd)
env=$(PYTHONPATH="$HERE${PYTHONPATH:+:$PYTHONPATH}" "${PCBKIT_PYTHON:-python3}" -m pcbkit krt-env route) || exit 1
eval "$env"
case "$WORK/" in "$PROJ"/*) echo "KRT_WORK must be outside the project" >&2; exit 1 ;; esac

rm -rf "${WORK:?}" && mkdir -p "$WORK"
for ext in kicad_pcb kicad_pro kicad_dru; do
    if [ -f "$PROJ/$NAME.$ext" ]; then cp "$PROJ/$NAME.$ext" "$WORK/"; fi
done
B=$WORK/$NAME.kicad_pcb
"$KRT_PY" - "$B" "${KEEP_GROUPS[@]}" <<'PY'
import sys, pcbnew
b = pcbnew.LoadBoard(sys.argv[1])
t = b.Tracks(); z = b.Zones()   # index: SWIG iterators break on Python 3.14
# rule areas (keepouts) are zones too: they stay, only copper zones go
keep = set(sys.argv[2:])   # [krt] keep_groups: hand-routed copper stays
def kept(it):
    g = it.GetParentGroup()
    return g is not None and g.GetName() in keep
for it in [t[i] for i in range(len(t))] + [z[i] for i in range(len(z)) if not z[i].GetIsRuleArea()]:
    if not kept(it):
        b.Delete(it)
b.Save(sys.argv[1])
print("stripped old copper")
PY

cd "$KRT_DIR"
cur=$B
# kept copper is not a rip-up candidate for the main pass
[ ${#KEEP_GROUPS[@]} -eq 0 ] || export KICAD_RIP_PREEXISTING=0
if [ ${#PLANES[@]} -gt 0 ]; then
    echo "== planes: $PLANES_DESC"
    "$KRT_PY" py_router/route_planes.py "$cur" "$WORK/1-planes.kicad_pcb" "${PLANES[@]}"
    cur=$WORK/1-planes.kicad_pcb
fi

for ((i = 0; i < DIFF_N; i++)); do
    declare -n desc="DIFF_DESC_$i" args="DIFF_$i"
    echo "== diff pairs: $desc"
    "$KRT_PY" py_router/route_diff.py "$cur" --output "$WORK/2-diff$i.kicad_pcb" "${args[@]}" "${COMMON[@]}"
    cur=$WORK/2-diff$i.kicad_pcb
    unset -n desc args
done

echo "== everything else (signals on ${SIG[1]} ${SIG[2]})"
rc=0
"$KRT_PY" py_router/route.py "$cur" "$WORK/4-routed.kicad_pcb" --nets "*" \
    "${SIG[@]}" "${COMMON[@]}" "${ROUTE_EXTRA[@]}" "${POWER[@]}" ${KRT_EXTRA:-} \
    --json-out "$WORK/route.json" || rc=$?
# exit 3 = finished but incomplete or below a requested size; still worth importing
[ "$rc" -eq 0 ] || [ "$rc" -eq 3 ] || { echo "route.py failed ($rc)" >&2; exit "$rc"; }
echo "route.py exit $rc"

cp "$WORK/4-routed.kicad_pcb" "$PROJ/$NAME.kicad_pcb"
echo "routed board copied back; now run: kicad-cli pcb drc --schematic-parity --severity-all $NAME.kicad_pcb"
