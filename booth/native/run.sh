#!/usr/bin/env bash
# Starts the native booth app full screen and restarts it if it ever exits (a crash, or * held for 3 seconds).
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
source <(grep -E '^(ROTATE|SPEAKER_VOLUME|MIC_VOLUME)=' "$HERE/native.conf" | sed 's/ *#.*//')
export DISPLAY="${DISPLAY:-:0}"

# Screen: never sleep, hide cursor, optional rotation.
xset s off; xset s noblank; xset -dpms
unclutter -idle 1 -root &
if [ "${ROTATE:-normal}" != "normal" ]; then
  OUT="$(xrandr | awk '/ connected/{print $1; exit}')"
  xrandr --output "$OUT" --rotate "$ROTATE"
fi

# Route audio to the USB adapter (the handset), every launch, so a replug is harmless.
set_audio() {
  local sink src
  for _ in $(seq 1 20); do
    sink="$(pactl list short sinks 2>/dev/null | awk 'tolower($2) ~ /usb/ {print $2; exit}')"
    src="$(pactl list short sources 2>/dev/null | awk 'tolower($2) ~ /usb/ && $2 !~ /monitor/ {print $2; exit}')"
    [ -n "$sink" ] && [ -n "$src" ] && break
    sleep 1
  done
  if [ -n "$sink" ]; then
    pactl set-default-sink "$sink"; pactl set-sink-mute "$sink" 0
    pactl set-sink-volume "$sink" "${SPEAKER_VOLUME:-85}%"
  fi
  if [ -n "$src" ]; then
    pactl set-default-source "$src"; pactl set-source-mute "$src" 0
    pactl set-source-volume "$src" "${MIC_VOLUME:-90}%"
  fi
  echo "$(date) sink=$sink source=$src"
}

usb_audio_ok() {  # the sound system (PipeWire) has the adapter's earpiece and mic
  pactl list short sinks 2>/dev/null | awk 'tolower($2) ~ /usb/ {f=1} END {exit !f}' &&
    pactl list short sources 2>/dev/null | awk 'tolower($2) ~ /usb/ && $2 !~ /monitor/ {f=1} END {exit !f}'
}

# A messy replug (a half-seated plug) can leave the adapter plugged in but missing from the sound system, so the
# handset is silent and the mic hears nothing. Every 5 s: if the Pi sees the adapter (any USB sound card but the
# Pico's) and the sound system doesn't, twice in a row, restart the sound system, then the app on the new devices.
watch_audio() {
  local misses=0
  while true; do
    sleep 5
    if ! grep -v "Pico" /proc/asound/cards | grep -q "USB-Audio" || usb_audio_ok; then misses=0; continue; fi
    misses=$((misses + 1))
    [ "$misses" -lt 2 ] && continue
    misses=0
    echo "$(date) audio adapter plugged in but missing from the sound system: restarting it" >> "$LOG"
    systemctl --user restart wireplumber pipewire pipewire-pulse
    for _ in $(seq 1 20); do usb_audio_ok && break; sleep 1; done
    if usb_audio_ok; then
      pkill -f "^$HERE/venv/bin/python $HERE/booth.py"   # the main loop routes audio again and restarts the app
    else
      echo "$(date) audio adapter still missing after the restart; replug it" >> "$LOG"
      sleep 60
    fi
  done
}

LOG="$HERE/booth.log"
watch_audio &
while true; do
  [ -f "$LOG" ] && [ "$(stat -c %s "$LOG")" -gt 5000000 ] && mv "$LOG" "$LOG.old"
  pkill -x mpv 2>/dev/null   # a window left over from a hard crash
  set_audio >> "$LOG" 2>&1
  "$HERE/venv/bin/python" "$HERE/booth.py" >> "$LOG" 2>&1
  echo "$(date) booth.py exited ($?), restarting" >> "$LOG"
  sleep 2
done
