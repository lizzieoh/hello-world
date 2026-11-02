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

**From a Mac, no keyboard or screen setup on the Pi:** run `./load-pi.sh` here (or double-click the **Load Pi** app, which runs it in Terminal). It asks once for the Wi-Fi networks (passwords hidden), then for each Pi: the booth number, its memory card (CanaKit's preloaded Raspberry Pi OS; the script writes the hostname, a `booth` user that logs in with this Mac's SSH key, and the Wi-Fi), then waits for the Pi to boot, downloads this branch onto it, runs `setup-native.sh` (paste the fal key when asked) and restarts it into the booth. `./load-pi.sh --pi-only 2` redoes just the install on `fal-booth-2` when it's already on the network.

**Or by hand on the Pi.** Run it from wherever the repo is on the Pi (the manual clones it to `~/hello-world`). It runs in place.

```bash
cd ~/hello-world/booth/native
./setup-native.sh 1        # 1, 2, 3, or 4 for the spare
sudo reboot
```

The setup script:
- installs mpv, ffmpeg, PortAudio and the Python packages
- sets X11, auto-login and no screen blanking
- always drives the main HDMI port (next to the power plug) at 1920×1080 from a saved screen profile, with HDMI audio off, so a slow-to-wake screen like the ARZOPA can't leave it black. The profile is the screen plugged in during setup, else the ARZOPA's in `edid/`. A different monitor later: run setup again with it plugged in, then reboot.
- names the Pi `fal-booth-N`
- adds you to the `input` and `lp` groups (Pico and printer without root)
- asks for the fal key once, saved to `fal.key`, readable only by you
- replaces the browser autostart with `run.sh`
- runs the self-test

To go back to the browser version, run `cd ~/hello-world/booth/pi && ./setup.sh 1`.

## What it won't make

Only original ideas, and nobody walks away without a video. Before anything is generated, each prompt is checked for anyone else's intellectual property: characters, franchises, brands, logos, real people and named styles, named or just described (a yellow sponge in brown shorts who lives in a pineapple counts).
- **The check:** a few unmistakable names are caught at once (`IP_NAMES` in `booth.py`). Everything else goes to Claude Haiku 4.5 on fal, with GPT-6 Luna if Haiku doesn't answer, which takes about a second.
- **If it borrows:** the same model rewrites it into an original idea with the same places, action and mood, but a different kind of character ("Spider-Man swinging through Miami" becomes "an acrobatic flamingo swinging through Miami"). The rewrite is checked again before it's made. On hold, the screen says "That one's someone else's character, so our agent is making you an original." Their words never reach the video model.
- **If no original works:** no busy tone. The screen asks for another idea and they get the beep again.
- **If the checker can't be reached:** nothing is made.
- **On the receipt:** the check and the rewrite are on the itemized bill with their model and cost (each a fraction of a cent). "YOU SAID" shows the caller's words, and "WE MADE YOU AN ORIGINAL" shows what was made.

Every video prompt also asks for original characters and designs only. Rude words get the busy tone (`BLOCKED`).

Hanging up doesn't lose the video. Hang up after **#**, or mid-sentence without pressing it, and the video is still made and goes up on the screen when it's ready. Only **\*** cancels.

## Settings: `native.conf`

| Setting | Default | |
|---|---|---|
| `LINE` | `1` | Shown on screen and printed on the receipt |
| `SECS` | `10` | Clip length, 5 to 15 |
| `MODEL` | `h3` | `h3`: H3 Max first, FLUX 3 Max as backup. `flux`: the other way round. |
| `LANGUAGE` | `en` | `auto` lets Wizper guess the language (a little slower) |
| `END_PAUSE`, `LISTEN_MAX` | `5`, `30` | Listening ends when the caller presses **\#**; for callers who never do, after this many seconds of quiet once they've spoken, or this long in all |
| `PRINTER` | `/dev/usb/lp0` | The receipt printer |
| `LOCATION` | `MIAMI` | Printed on the receipt under the line number |
| `ROTATE`, `SPEAKER_VOLUME`, `MIC_VOLUME` | | Same as the browser version |
| `LOW_VOLUME` | `40` | The phone's volume switch on low: the handset and the video sound play at this percent of loud |
| `RING_EVERY` | `120` | The phone's ringer switch on: while nobody's on the phone, it rings this often (seconds) |

Changes take effect when the app restarts. Hold **\*** for 3 seconds on the phone, or reboot.

## Keys

| Key | What it does |
|---|---|
| Lift / hang up (F8 / F9) | Starts and ends the call |
| **#** while talking | "I'm done", without waiting for the pause |
| **\*** while talking or on hold | Cancel: nothing is made (a render already running is called off at fal). They get the beep and can try another idea, or hang up. |
| **2** | Sound on/off; turning it on restarts the clip from the beginning |
| **3** | The same prompt again; the last video keeps playing under the hold clock |
| **0** | Prints the receipt (once per video; if the printer is offline, 0 tries again) |
| Hold **\*** 3 s | Staff: restart the app |
| Hold **#** 5 s | Staff: shut the Pi down safely; unplug once the green light stops blinking |
| Dial **\*123#** | Staff: clear the screen's feed of earlier videos (they move to `cache/archive/`, not deleted) |
| Volume switch (F17 loud / F18 low) | Low turns the handset and the video sound down to `LOW_VOLUME` |
| Ringer switch (F19 on / F20 off) | On: while nobody's on the phone, the earpiece rings and the screen says "Incoming call" every `RING_EVERY` seconds, starting a few seconds after it's switched on. Lifting the handset stops it. |
| Space | Lift/hang up, for testing with a USB keyboard instead of the phone |

A USB keyboard works as the phone, which is handy for testing before the phone is wired.

## What's on screen

- **Top left:** the line number and a dot. Green means fal is reachable. Red means **offline**, so check the router.
- **On hold:** "Connecting you to our fastest agent", then the prompt, with a clock counting from the moment the caller stopped talking.
- **Ready:** the video, the time it took, and the key hints. Pressed keys turn green.
- **Between calls:** the last 12 videos play in a shuffled order, never the same one twice in a row, each with its prompt.

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
| No sound in the earpiece | `grep sink= booth.log` should show a USB sink. After a replug the booth fixes itself within about 15 seconds (`grep "audio adapter" booth.log`); if it says "still missing", replug the adapter firmly, then restart (hold \*). |
| "Printer offline" | `ls -l /dev/usb/lp0` should exist and belong to group `lp`. Replug the printer's USB. |
| Red dot, every call busy | Network. Check the travel router; the app retries on its own. |
