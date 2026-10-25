# The native booth app (no browser)

The same call as the `/booth` web page, run as one Python program on the Pi:

**lift → greeting and beep → say a prompt → "please hold" and elevator music → video plays muted → 2 sound · 3 regenerate · 0 print**

## Why native instead of the browser

| | Browser kiosk | Native app |
|---|---|---|
| Phone keys | Typed into Chromium; only work while its window has focus | Read straight from the Pico (evdev); nothing can steal focus |
| Printing | Needs a CUPS driver for the receipt printer (Epson's is x86-only) | Sends ESC/POS bytes to `/dev/usb/lp0` directly; no driver |
| Between calls | Recent videos stream from fal again, so they need Wi-Fi | Recent videos are saved on the Pi and loop with no Wi-Fi at all |
| When something breaks | Page reload | `run.sh` restarts the app in 2 s; if mpv dies, the app restarts it mid-call |
| Mic | Browser permission and autoplay rules, worked around with flags | Opened once, stays open |
| Changing the flow | Edit and deploy the website | Edit `booth.py` on the Pi |

The browser version still works and stays in the repo. Each Pi runs one or the other.

## Install (per Pi, about 10 minutes)

Run it from wherever the repo is on the Pi (the manual clones it to `~/hello-world`). It runs in place.

```bash
cd ~/hello-world/booth/native
./setup-native.sh 1        # 1, 2, 3, or 4 for the spare
sudo reboot
```

The setup script:
- installs mpv, ffmpeg, PortAudio and the Python packages
- sets X11, auto-login and no screen blanking
- names the Pi `fal-booth-N`
- adds you to the `input` and `lp` groups (Pico and printer without root)
- asks for the fal key once, saved to `fal.key`, readable only by you
- replaces the browser autostart with `run.sh`
- runs the self-test

To go back to the browser version, run `cd ~/hello-world/booth/pi && ./setup.sh 1`.

## Settings: `native.conf`

| Setting | Default | |
|---|---|---|
| `LINE` | `1` | Shown on screen and printed on the receipt |
| `SECS` | `10` | Clip length, 5 to 15 |
| `MODEL` | `h3` | `h3`: H3 Max first, FLUX 3 Max as backup. `flux`: the other way round. |
| `LANGUAGE` | `en` | `auto` lets Wizper guess the language (a little slower) |
| `PRINTER` | `/dev/usb/lp0` | The receipt printer |
| `ROTATE`, `SPEAKER_VOLUME`, `MIC_VOLUME` | | Same as the browser version |

Changes take effect when the app restarts. Hold **\*** for 3 seconds on the phone, or reboot.

## Keys

| Key | What it does |
|---|---|
| Lift / hang up (F8 / F9) | Starts and ends the call |
| **#** while talking | "I'm done", without waiting for the pause |
| **2** | Sound on/off; turning it on restarts the clip from the beginning |
| **3** | The same prompt again; the last video keeps playing under the hold clock |
| **0** | Prints the receipt (once per video; if the printer is offline, 0 tries again) |
| Hold **\*** 3 s | Staff: restart the app |
| Hold **#** 5 s | Staff: shut the Pi down safely; unplug once the green light stops blinking |
| Dial **\*123#** | Staff: clear the screen's feed of earlier videos (they move to `cache/archive/`, not deleted) |
| Space | Lift/hang up, for testing with a USB keyboard instead of the phone |

A USB keyboard works as the phone, which is handy for testing before the phone is wired.

## What's on screen

- **Top left:** the line number and a dot. Green means fal is reachable. Red means **offline**, so check the router.
- **On hold:** "Connecting you to our fastest agent", then the prompt, with a clock counting from the moment the caller stopped talking.
- **Ready:** the video, the time it took, and the key hints. Pressed keys turn green.
- **Between calls:** the last 12 videos loop, each with its prompt.

## Files

| File | |
|---|---|
| `booth.py` | The whole app. Every part (phone, line, screen, fal, printer) is a class at the top; the call itself is `Booth`. |
| `run.sh` | Started at login. Screen and audio routing, then runs `booth.py` forever. |
| `native.conf` | Settings |
| `fal.key` | The fal API key (made by setup, never committed) |
| `voice/` | The greeting, hold and menu lines (copied from fal-call-center `phone-voice/3`) |
| `cache/` | Recent videos and the background image. Safe to delete. |
| `booth.log` | What happened, including every fal failure and fallback |
| `test_booth.py` | Whole calls with fake hardware (see below) |

## Self-test

```bash
cd ~/hello-world/booth/native && venv/bin/python test_booth.py
```

It runs 13 scenarios in about 30 seconds, with the real mixer, speech detection, fal fallback logic, cache and receipt maker, and fake hardware:
- a full call: menu, sound, print, regenerate, hang up
- silence twice
- \# to finish early
- H3 Max failing, so FLUX takes over
- H3 Max timing out
- a refused prompt, which is not retried
- the `MODEL=flux` setting
- a blocked word
- hanging up during the render
- the handset left off the hook
- hanging up during the greeting
- the sounds
- the receipt

It saves a receipt preview PNG. It runs no hardware and no fal calls, so it costs nothing.

## Troubleshooting

| Symptom | Check |
|---|---|
| Black screen, nothing happens | `tail -50 booth.log` (in the `native` folder) |
| Lifting the handset does nothing | `grep "phone keys" booth.log` should name the Pico. If not, replug it. Are you in the `input` group (`groups`)? |
| No sound in the earpiece | `grep sink= booth.log` should show a USB sink. Replug the adapter, then restart (hold \*). |
| "Printer offline" | `ls -l /dev/usb/lp0` should exist and belong to group `lp`. Replug the printer's USB. |
| Red dot, every call busy | Network. Check the travel router; the app retries on its own. |
