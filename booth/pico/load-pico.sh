#!/bin/bash
# Load a booth Pico in one go: flash CircuitPython, copy the booth files, check them.
#
#   ./load-pico.sh                 uses the newest Pico W CircuitPython .uf2 in ~/Downloads
#   ./load-pico.sh path/to/x.uf2   uses that .uf2 (e.g. the plain-Pico build)
#
# Hold BOOTSEL while plugging the Pico in. A Pico that already shows up as CIRCUITPY
# just gets the files. When one is done, unplug it and plug in the next; Ctrl-C to stop.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
FILES=(keymonitor.py keyfinder.py)   # code.py goes last, so the Pico restarts on the full set
BOOT=/Volumes/RPI-RP2
PY=/Volumes/CIRCUITPY

UF2="${1:-$(ls -t "$HOME"/Downloads/adafruit-circuitpython-raspberry_pi_pico_w-*.uf2 2>/dev/null | head -1 || true)}"
if [ -z "$UF2" ] || [ ! -f "$UF2" ]; then
  echo "No CircuitPython .uf2 found. Download the Pico W one from"
  echo "  https://circuitpython.org/board/raspberry_pi_pico_w/"
  echo "into ~/Downloads, or pass the file: ./load-pico.sh path/to/file.uf2"
  exit 1
fi
echo "Firmware: $(basename "$UF2")"

wait_for() {  # wait_for <seconds> <path>...: succeeds once any of the paths exists
  local secs=$1; shift
  for ((i = 0; i < secs; i++)); do
    for p in "$@"; do [ -d "$p" ] && return 0; done
    sleep 1
  done
  return 1
}

n=0
while true; do
  echo
  echo "Hold BOOTSEL and plug in a Pico..."
  until wait_for 5 "$BOOT" "$PY"; do :; done

  if [ -d "$BOOT" ]; then
    echo "RPI-RP2 found. Flashing CircuitPython..."
    cp -X "$UF2" "$BOOT"/ || true   # macOS sometimes reports an error here even though the flash worked
    if ! wait_for 60 "$PY"; then
      echo "CIRCUITPY never appeared. Unplug, hold BOOTSEL, plug back in and try again."
      continue
    fi
    sleep 2   # let the drive finish mounting
  else
    echo "CIRCUITPY found (CircuitPython already on it). Skipping the flash."
  fi

  echo "Copying booth files..."
  cp -X "${FILES[@]/#/$HERE/}" "$PY"/
  cp -X -R "$HERE"/lib/ "$PY"/lib/
  cp -X "$HERE"/code.py "$PY"/
  sync

  ok=1
  for f in "${FILES[@]}" code.py; do cmp -s "$HERE/$f" "$PY/$f" || { echo "  $f did not copy right"; ok=0; }; done
  diff -rq "$HERE"/lib "$PY"/lib >/dev/null || { echo "  lib did not copy right"; ok=0; }

  board=$(grep -o 'Board ID:.*' "$PY"/boot_out.txt 2>/dev/null || echo "Board ID: unknown")
  uid=$(grep -o 'UID:.*' "$PY"/boot_out.txt 2>/dev/null || echo "UID: unknown")
  if [ $ok = 1 ]; then
    n=$((n + 1))
    echo "Done: Pico #$n loaded ($board, $uid)."
  else
    echo "Copy check FAILED. Run the script again with this Pico still plugged in."
  fi

  echo "Unplug it to load the next one (Ctrl-C to stop)."
  while [ -d "$PY" ] || [ -d "$BOOT" ]; do sleep 1; done
done
