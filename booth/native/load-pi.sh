#!/bin/bash
# Set up booth Pis from this Mac, one after another: prepare the memory card, wait for the Pi to boot, install the
# booth, restart it into the booth.
#
#   ./load-pi.sh               asks for the Wi-Fi once, then the booth number and card for each Pi
#   ./load-pi.sh --pi-only 2   skips the card: (re)installs the booth on fal-booth-2, already on the network
#
# The cards are CanaKit's preloaded Raspberry Pi OS (64-bit, desktop), which sets itself up on first boot from a few
# files on the card (cloud-init). The Pi logs in with this Mac's SSH key, so it has no password. Wi-Fi passwords are
# typed here (hidden) and only written to the card. The fal key is pasted into the Pi's own setup when it asks.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_URL="$(git -C "$HERE" remote get-url origin 2>/dev/null || echo https://github.com/lizzieoh/hello-world.git)"
BRANCH="${BRANCH:-$(git -C "$HERE" rev-parse --abbrev-ref HEAD 2>/dev/null || echo claude/landline-demo-booth-setup-05lxch)}"
BOOTFS="${BOOTFS:-/Volumes/bootfs}"
SSH=(ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=8)
KEYFILE="$HOME/.ssh/id_ed25519"
SSIDS=() PASSES=()

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

wait_for() {  # wait_for <seconds> <path>: succeeds once the folder exists
  for ((i = 0; i < $1; i++)); do [ -d "$2" ] && return 0; sleep 1; done
  return 1
}

ask_wifi() {
  say "Wi-Fi for the Pis (asked once; first one listed wins when several are in range)"
  echo "Add the Wi-Fi where you're setting up now, plus where the booths will run. Phones-only guest networks: skip."
  while true; do
    read -r -p "Wi-Fi name (Enter on its own when done): " ssid
    [ -n "$ssid" ] || { [ ${#SSIDS[@]} -gt 0 ] && break; echo "At least one, please."; continue; }
    read -r -s -p "Password for \"$ssid\" (hidden): " pass; echo
    SSIDS+=("$ssid") PASSES+=("$pass")
  done
  unset pass
}

yaml_q() { local s=${1//\\/\\\\}; s=${s//\"/\\\"}; printf '"%s"' "$s"; }

write_card() {  # write_card <n>
  local n=$1 host="fal-booth-$1"
  say "Booth $n: put its memory card in the Mac"
  until wait_for 5 "$BOOTFS"; do :; done
  sleep 1
  if ! head -1 "$BOOTFS/user-data" 2>/dev/null | grep -q "^#cloud-config" || [ ! -f "$BOOTFS/meta-data" ]; then
    echo "This card doesn't have a Raspberry Pi OS that sets itself up on first boot."
    echo "Write \"Raspberry Pi OS (64-bit)\" to it with Raspberry Pi Imager (skip Edit settings), then run this again."
    return 1
  fi
  if grep -q "^# fal booth Pi" "$BOOTFS/user-data"; then
    read -r -p "This card was already set up ($(grep -m1 '^hostname:' "$BOOTFS/user-data")). Set it up as $host instead? [y/N] " a
    [[ "$a" == [yY]* ]] || return 1
  fi
  local stamp; stamp="$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$HOME/.fal-booth-cards/$stamp"
  cp "$BOOTFS"/{user-data,network-config,meta-data} "$HOME/.fal-booth-cards/$stamp/" 2>/dev/null || true

  cat > "$BOOTFS/user-data" <<EOF
#cloud-config
# fal booth Pi: set up on first boot by cloud-init (written by booth/native/load-pi.sh). Login is by SSH key only;
# set a password later with "sudo passwd booth" if you want to log in with a keyboard.
hostname: $host
manage_etc_hosts: true
timezone: America/Los_Angeles
keyboard:
  model: pc105
  layout: us
users:
- name: booth
  gecos: fal booth
  groups: users,adm,dialout,audio,netdev,video,plugdev,cdrom,games,input,gpio,spi,i2c,render,sudo,lp
  shell: /bin/bash
  lock_passwd: true
  sudo: ALL=(ALL) NOPASSWD:ALL
  ssh_authorized_keys:
  - $(cat "$KEYFILE.pub")
ssh_pwauth: false
enable_ssh: true
runcmd:
- systemctl enable --now ssh
EOF
  {
    echo "# fal booth Pi: Wi-Fi joined on first boot (and Ethernet whenever it's plugged in)."
    echo "network:"
    echo "  version: 2"
    echo "  ethernets:"
    echo "    eth0:"
    echo "      dhcp4: true"
    echo "      optional: true"
    echo "  wifis:"
    echo "    wlan0:"
    echo "      dhcp4: true"
    echo "      optional: true"
    echo "      regulatory-domain: US"
    echo "      access-points:"
    for i in "${!SSIDS[@]}"; do
      echo "        $(yaml_q "${SSIDS[$i]}"):"
      echo "          password: $(yaml_q "${PASSES[$i]}")"
    done
  } > "$BOOTFS/network-config"
  touch "$BOOTFS/ssh"
  sync
  diskutil eject "$BOOTFS" >/dev/null 2>&1 || diskutil unmount "$BOOTFS" >/dev/null 2>&1 || true
  echo "Card ready for $host (its original files are saved in ~/.fal-booth-cards/$stamp)."
  echo "Put it in the Pi, plug in the screen, then the Pi's power."
}

settled() {  # settled <host>: the Pi is up, first-boot setup is finished, and it isn't about to restart
  local host=$1 a b
  a="$("${SSH[@]}" "booth@$host" 'grep -qw resize /boot/firmware/cmdline.txt && exit 1; cloud-init status 2>/dev/null | grep -q "status: done" || exit 1; cat /proc/sys/kernel/random/boot_id' 2>/dev/null)" || return 1
  sleep 20
  b="$("${SSH[@]}" "booth@$host" 'cat /proc/sys/kernel/random/boot_id' 2>/dev/null)" || return 1
  [ -n "$a" ] && [ "$a" = "$b" ]
}

wait_for_pi() {  # wait_for_pi <host>
  local host=$1 t0=$SECONDS
  printf 'Waiting for %s (first boot takes about 3 minutes and restarts once)' "$host"
  until settled "$host"; do
    printf '.'
    sleep 5
    if (( SECONDS - t0 > 900 )); then
      echo
      echo "Still no $host after 15 minutes. Is the Mac on the same Wi-Fi as the Pi? Did the Wi-Fi password match?"
      echo "A screen on the Pi shows its boot. Keep waiting with Enter, or Ctrl-C to stop."
      read -r _
      t0=$SECONDS
    fi
  done
  echo " up."
}

install_booth() {  # install_booth <n>
  local n=$1 host="fal-booth-$1.local" rc
  wait_for_pi "$host"
  say "Booth $n: downloading the booth ($BRANCH)"
  "${SSH[@]}" "booth@$host" "if [ -d ~/hello-world/.git ]; then git -C ~/hello-world pull -q --ff-only --autostash; else git clone -q -b '$BRANCH' '$REPO_URL' ~/hello-world; fi && git -C ~/hello-world log --oneline -1" ||
    { echo "Couldn't download the booth onto the Pi (is it online?)."; return 1; }

  say "Booth $n: installing (about 5 minutes). Paste the fal key when it asks; it stays hidden."
  while true; do
    ssh -t -o StrictHostKeyChecking=accept-new "booth@$host" "cd ~/hello-world/booth/native && ./setup-native.sh $n" && break
    rc=$?
    if [ $rc -ne 255 ]; then echo "Setup stopped with an error (above). Fix it, then: ./load-pi.sh --pi-only $n"; return 1; fi
    echo "Setup was cut off (the Pi may have restarted). Trying again; it carries on where it left off."
    wait_for_pi "$host"
  done

  # Which Wi-Fi wins when several are in range: the order they were typed in.
  local prio=$(( ${#SSIDS[@]} * 10 )) s
  if [ ${#SSIDS[@]} -gt 0 ]; then
    for s in "${SSIDS[@]}"; do
      printf '%s\n' "$s" | "${SSH[@]}" "booth@$host" "read -r want; nmcli -t -f NAME,TYPE connection show | while IFS=: read -r name type; do [ \"\$type\" = 802-11-wireless ] || continue; [ \"\$(nmcli -g 802-11-wireless.ssid connection show \"\$name\")\" = \"\$want\" ] && sudo nmcli connection modify \"\$name\" connection.autoconnect-priority $prio; done; true" || true
      prio=$((prio - 10))
    done
  fi

  say "Booth $n: restarting into the booth"
  "${SSH[@]}" "booth@$host" 'sudo reboot' >/dev/null 2>&1 || true
  sleep 15
  local t0=$SECONDS
  until "${SSH[@]}" "booth@$host" 'pgrep -f booth.py >/dev/null && pgrep -x mpv >/dev/null' 2>/dev/null; do
    sleep 5
    (( SECONDS - t0 > 300 )) && { echo "The booth app didn't start within 5 minutes; check the screen, or ask Claude to read booth.log."; return 1; }
  done
  echo "Booth $n is running. Plug in its phone (Pico + audio adapter) and printer, lift the handset to test."
}

# ---------- main ----------
if [ "${1:-}" = "--pi-only" ]; then
  n="${2:?usage: ./load-pi.sh --pi-only <booth number>}"
  install_booth "$n"
  exit
fi

[ -f "$KEYFILE.pub" ] || { echo "Making this Mac an SSH key (once)."; ssh-keygen -q -t ed25519 -N "" -f "$KEYFILE"; }
if [ -n "$(git -C "$HERE" log --oneline "origin/$BRANCH..HEAD" 2>/dev/null)" ]; then
  echo "Note: this Mac has booth changes not pushed to GitHub yet; the Pis download what's on GitHub ($BRANCH)."
fi
ask_wifi

next=2
while true; do
  read -r -p $'\n'"Booth number for this Pi (1-4) [$next]: " n
  n="${n:-$next}"
  [[ "$n" =~ ^[1-9]$ ]] || { echo "A number 1-9, please."; continue; }
  ssh-keygen -R "fal-booth-$n.local" >/dev/null 2>&1 || true   # a re-set-up Pi has new keys
  if write_card "$n"; then
    install_booth "$n" || true
  fi
  next=$((n + 1))
  read -r -p $'\n'"Set up another Pi? [Y/n] " more
  [[ "$more" == [nN]* ]] && break
done
unset PASSES
echo "All done."
