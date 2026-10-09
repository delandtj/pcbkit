#!/usr/bin/env bash
# Targeted KRT pass: clear and reroute only the named nets, keep everything else.
#
#   pcbkit krt-reroute [--keep] NET [NET ...]     # in the project dir
#
# --keep: do not clear the nets first; KRT treats existing copper as fixed and
# routes only what is still open (use after laying escapes by script).
#
# Use after moving a few parts: list every non-ground net on the moved parts
# plus the nets DRC reports open. All copper of those nets is deleted first
# (KRT's --rip-existing-nets leaves stubs at old pad positions), then:
#   1. route.py on the named nets, [krt] signal_layers only
#   2. route.py on [krt] ground without rip-up (closes pads the move opened)
#   3. kicad-cli DRC with --refill-zones --save-board (KRT does not refill)
# The refilled board is copied back. Ground stubs left at moved pads are not
# cleaned here; DRC shows them as track_dangling (see pcbkit prune).
#
# Env: same as krt_route.sh (KRT_DIR, KRT_PY); KRT_EXTRA adds route.py flags to
#      the signal pass (e.g. "--ordering mps"); KRT_WORK defaults to [krt] rework
#      and must be outside the project.
set -euo pipefail
KEEP=0
[ "${1:-}" = --keep ] && { KEEP=1; shift; }
[ $# -gt 0 ] || { echo "usage: pcbkit krt-reroute [--keep] NET [NET ...]" >&2; exit 2; }

HERE=$(cd "$(dirname "$0")/../.." && pwd)
env=$(PYTHONPATH="$HERE${PYTHONPATH:+:$PYTHONPATH}" "${PCBKIT_PYTHON:-python3}" -m pcbkit krt-env reroute) || exit 1
eval "$env"
case "$WORK/" in "$PROJ"/*) echo "KRT_WORK must be outside the project" >&2; exit 1 ;; esac

rm -rf "${WORK:?}" && mkdir -p "$WORK/drc"
for d in "$WORK" "$WORK/drc"; do
    for ext in kicad_pro kicad_dru; do
        if [ -f "$PROJ/$NAME.$ext" ]; then cp "$PROJ/$NAME.$ext" "$d/"; fi
    done
done
B=$WORK/$NAME.kicad_pcb
"$KRT_PY" - "$PROJ/$NAME.kicad_pcb" "$B" "$KEEP" "$@" <<'PY'
import sys, pcbnew
b = pcbnew.LoadBoard(sys.argv[1])
rip = set() if sys.argv[3] == "1" else set(sys.argv[4:])
t = b.Tracks()   # index: SWIG iterators break on Python 3.14
gone = [t[i] for i in range(len(t)) if t[i].GetNetname() in rip]
for it in gone:
    b.Delete(it)
b.Save(sys.argv[2])
print("cleared", len(gone), "tracks/vias")
PY

cd "$KRT_DIR"
rc=0
"$KRT_PY" py_router/route.py "$B" "$WORK/1-sig.kicad_pcb" --nets "$@" "${SIG[@]}" "${COMMON[@]}" "${POWER[@]}" ${KRT_EXTRA:-} \
    --json-out "$WORK/sig.json" > "$WORK/sig.log" 2>&1 || rc=$?
[ "$rc" -eq 0 ] || [ "$rc" -eq 3 ] || { echo "signal pass failed ($rc), see $WORK/sig.log" >&2; exit "$rc"; }
rc=0
"$KRT_PY" py_router/route.py "$WORK/1-sig.kicad_pcb" "$WORK/2-gnd.kicad_pcb" "${GROUND[@]}" "${SIG[@]}" "${COMMON[@]}" \
    --json-out "$WORK/gnd.json" > "$WORK/gnd.log" 2>&1 || rc=$?
[ "$rc" -eq 0 ] || [ "$rc" -eq 3 ] || { echo "$GROUND_NET pass failed ($rc), see $WORK/gnd.log" >&2; exit "$rc"; }

cp "$WORK/2-gnd.kicad_pcb" "$WORK/drc/$NAME.kicad_pcb"
cd "$WORK/drc"
kicad-cli pcb drc --severity-all --refill-zones --save-board -o drc.rpt "$NAME.kicad_pcb" \
    2>&1 | grep Found || true
cp "$WORK/drc/$NAME.kicad_pcb" "$PROJ/$NAME.kicad_pcb"
echo "board copied back; now run: kicad-cli pcb drc --schematic-parity --severity-all $NAME.kicad_pcb"
