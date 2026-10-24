#!/usr/bin/env bash
# One-time setup for a fal call-center booth unit.
# Run on a fresh Raspberry Pi OS (64-bit, with desktop, Bookworm or newer):
#   cd ~/booth/pi && ./setup.sh 1        # booth number 1, 2, 3 (or 4 for the spare)
set -euo pipefail

N="${1:?usage: ./setup.sh <booth-number>}"
HERE="$(cd "$(dirname "$0")" && pwd)"
BOOTH="$HOME/booth"

echo "==> Packages"
sudo apt-get update
sudo apt-get install -y unclutter curl x11-xserver-utils
sudo apt-get install -y chromium-browser || sudo apt-get install -y chromium

echo "==> System: X11, auto-login to desktop, no screen blanking, hostname"
sudo raspi-config nonint do_wayland W1          # X11 (reliable cursor hiding + xset)
sudo raspi-config nonint do_boot_behaviour B4   # desktop, auto-login
sudo raspi-config nonint do_blanking 1          # disable screen blanking
sudo raspi-config nonint do_hostname "fal-booth-$N"

echo "==> Booth files"
mkdir -p "$BOOTH"
cp "$HERE/kiosk.sh" "$HERE/start.html" "$BOOTH/"
chmod +x "$BOOTH/kiosk.sh"
[ -f "$BOOTH/booth.conf" ] || cp "$HERE/booth.conf" "$BOOTH/booth.conf"

echo "==> Autostart"
mkdir -p "$HOME/.config/autostart"
cat > "$HOME/.config/autostart/booth.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=fal booth
Exec=$BOOTH/kiosk.sh
X-GNOME-Autostart-enabled=true
EOF

echo "==> Quiet the desktop (no first-run wizard, no update popups)"
sudo apt-get purge -y piwiz 2>/dev/null || true
sudo systemctl disable --now packagekit.service 2>/dev/null || true

echo
echo "Done. Edit $BOOTH/booth.conf if needed, then: sudo reboot"
