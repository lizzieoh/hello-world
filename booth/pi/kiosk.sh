#!/usr/bin/env bash
# Starts the booth browser full-screen and restarts it if it ever exits.
set -u
BOOTH="$HOME/booth"
source "$BOOTH/booth.conf"
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
  echo "$(date) sink=$sink source=$src" >> "$BOOTH/audio.log"
}

BROWSER="$(command -v chromium-browser || command -v chromium)"
PROFILE="$HOME/.config/booth-chromium"

while true; do
  # Never show "Restore pages?" after a power cut.
  sed -i 's/"exited_cleanly":false/"exited_cleanly":true/; s/"exit_type":"[^"]*"/"exit_type":"Normal"/' \
    "$PROFILE/Default/Preferences" 2>/dev/null
  set_audio
  "$BROWSER" \
    --user-data-dir="$PROFILE" \
    --kiosk --noerrdialogs --disable-infobars --no-first-run \
    --disable-session-crashed-bubble --disable-features=Translate,TranslateUI \
    --password-store=basic --check-for-update-interval=31536000 \
    --autoplay-policy=no-user-gesture-required \
    --use-fake-ui-for-media-stream \
    --overscroll-history-navigation=0 --disable-pinch \
    "file://$BOOTH/start.html#$URL"
  sleep 2
done
