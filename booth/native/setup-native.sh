#!/usr/bin/env bash
# One-time setup of the native booth app (no browser) on a Raspberry Pi 5.
# Run on Raspberry Pi OS (64-bit, with desktop, Bookworm or newer):
#   cd ~/hello-world/booth/native && ./setup-native.sh 1        # booth number 1, 2, 3 (or 4 for the spare)
# Safe to run again (e.g. after pulling new code): settings and the fal key are kept.
set -euo pipefail

N="${1:?usage: ./setup-native.sh <booth-number>}"
APP="$(cd "$(dirname "$0")" && pwd)"   # runs in place, wherever the repo is

echo "==> Packages"
sudo apt-get update
sudo apt-get install -y mpv ffmpeg unclutter x11-xserver-utils pulseaudio-utils fonts-dejavu-core \
  python3-venv python3-numpy python3-pil python3-evdev libportaudio2

echo "==> System: X11, auto-login to desktop, no screen blanking, hostname"
sudo raspi-config nonint do_wayland W1          # X11 (reliable cursor hiding + xset)
sudo raspi-config nonint do_boot_behaviour B4   # desktop, auto-login
sudo raspi-config nonint do_blanking 1          # disable screen blanking
sudo raspi-config nonint do_hostname "fal-booth-$N"
# Read the Pico straight from /dev/input, and write to the receipt printer, without root.
sudo usermod -aG input,lp "$USER"

echo "==> Settings"
chmod +x "$APP/run.sh" "$APP/booth.py"
sed -i "s/^LINE=[0-9]* /LINE=$N /" "$APP/native.conf"
rm -f "$APP/cache/bg.png"   # redrawn on next start

echo "==> Python packages (in a venv that can also see the system numpy, Pillow and evdev)"
[ -d "$APP/venv" ] || python3 -m venv --system-site-packages "$APP/venv"
"$APP/venv/bin/pip" install --quiet --upgrade fal-client sounddevice

echo "==> fal key"
if [ ! -s "$APP/fal.key" ]; then
  read -r -s -p "Paste the fal API key (input hidden), then Enter: " KEY; echo
  umask 077; printf '%s\n' "$KEY" > "$APP/fal.key"
fi
chmod 600 "$APP/fal.key"

echo "==> Autostart (replaces the browser kiosk if it was set up)"
mkdir -p "$HOME/.config/autostart"
cat > "$HOME/.config/autostart/booth.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=fal booth
Exec=$APP/run.sh
X-GNOME-Autostart-enabled=true
DESKTOP

echo "==> Quiet the desktop (no first-run wizard, no update popups)"
sudo apt-get purge -y piwiz 2>/dev/null || true
sudo systemctl disable --now packagekit.service 2>/dev/null || true

echo "==> Self-test (fake phone, screen and fal; about a minute)"
(cd "$APP" && venv/bin/python test_booth.py) || echo "!! self-test failed: see above before the show"

echo
echo "Done. Settings: $APP/native.conf   Log: $APP/booth.log"
echo "Reboot to start the booth: sudo reboot"
echo "Back to the browser version any time: cd $(dirname "$APP")/pi && ./setup.sh $N"
