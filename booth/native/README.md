# The native booth app (no browser)

The same call as the `/booth` web page, run as one Python program on the Pi:

**lift → greeting and beep → say a prompt (or press 1 for one of ours) → "please hold" and elevator music → video plays muted → 2 sound · 3 another take · 0 print**

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
- **The check:** a few unmistakable names are caught at once (`IP_NAMES` in `booth.py`). Everything else goes to Gemini 3.1 Flash Lite and GPT-6 Luna on fal at the same time; the first answer, usually in about a second, decides. If neither answers within 4 s, nothing is made.
- **If it borrows:** GPT-6 Luna (or Gemini 3.1 Flash Lite, if Luna is slow) rewrites it into an original idea with the same places, action and mood, but a different kind of character ("Spider-Man swinging through Miami" becomes "an acrobatic flamingo swinging through Miami"). The rewrite is checked again before it's made. On hold, the screen says "That one's someone else's character, so our agent is making you an original." Their words never reach the video model.
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
| `LANGUAGE` | `en` | What visitors speak (`es`, `fr`…), or `auto` to let the speech model guess |
| `END_PAUSE`, `LISTEN_MAX` | `5`, `30` | Listening ends when the caller presses **\#**; for callers who never do, after this many seconds of quiet once they've spoken, or this long in all |
| `PRINTER` | `/dev/usb/lp0` | The receipt printer |
| `LOCATION` | `MIAMI` | Printed on the receipt under the line number |
| `ROTATE`, `SPEAKER_VOLUME`, `MIC_VOLUME` | | Same as the browser version |
| `LOW_VOLUME` | `40` | The phone's volume switch on low: the handset and the video sound play at this percent of loud |
| `RING_EVERY` | `120` | The phone's ringer switch on: while nobody's on the phone, it rings this often (seconds) |
| `STAFF_PIN` | random | The control API's PIN (on the Pis). Unset: a random one is written to `staff.pin` on first start. |
| `BOOTHS` | `fal-booth-1.local,fal-booth-2.local` | On your Mac: the booths the dashboard shows (hostnames or IPs) |
| `CONTROL_PORT` | `8080` | The control API's port |

Changes take effect when the app restarts. Hold **\*** for 3 seconds on the phone, or reboot.

## Booth control dashboard (your Mac only)

The dashboard runs on your Mac, never on a Pi or a TV:

    python3 dashboard.py        # opens http://localhost:8099 (listens on this Mac only)

It talks straight to each booth's control API over the venue Wi-Fi (the Mac has to be on the same network as the
Pis). Enter the PIN once: `STAFF_PIN` in `native.conf` on the Pis, or `cat ~/hello-world/booth/native/staff.pin` on a
Pi (a random one is made on first start). Which booths it shows: `BOOTHS` in this Mac's `native.conf`.

| Control | What it does |
|---|---|
| **Take it off the screen** | The kill switch. Mid-call: ends the call, calls off a render still running (never shown or kept) and asks the caller to hang up. Between calls: hides the video on the TV from the loop. |
| **Line: Out of order / Open** | Closes the line without unplugging anything. The TV shows OUT OF ORDER and your note (e.g. "Please try Line 2"); a lift gets the busy tone and the note. Open brings it back. |
| **Screen: On / Off** | Turns the TV's picture off and on (HDMI). The app keeps running; calls still work. |
| **Next clip, Ring now** | Skips to the next video in the idle loop; rings the phone once to draw people over (idle only). |
| **Video model** | Which model goes first when one is slow or down: H3 Max or FLUX 3 Max (the other stays the backup). Until the app restarts. |
| **Sound** | Earpiece and mic levels, live. Until the app restarts (then `SPEAKER_VOLUME`/`MIC_VOLUME`). |
| **Printer** | Reprint the last receipt; a test print. |
| **Feed** | The videos the TV loops between calls, each with **Hide** (moved to `cache/archive/hidden/`, never deleted). Clear feed puts them all away. |
| **Power** | Restart the app (warns mid-call), reboot the Pi, shut down (type the line number). |
| **Status** | A live picture of the TV, the state, Wi-Fi, fal reachable, temperature, handset, printer and last print, disk, failed renders and prints, backup-model use, calls, videos, average render, the last error, the log. A strip at the top shows every line and its alerts. |

On each Pi, `control.py` is the API (port 8080, PIN on every request, no page served). `run.sh` starts it beside the
app and restarts it if it exits, so restart, reboot and the log still work when the app is down. It talks to
`booth.py` over a local socket.

## Keys

| Key | What it does |
|---|---|
| Lift / hang up (F8 / F9) | Starts and ends the call |
| **#** while talking | "I'm done", without waiting for the pause |
| **1** before or while talking | Surprise me: one of 30 curated ideas (`SURPRISE` in `booth.py`), straight to hold. No speech-to-text and no IP check. A second silence gets one too, so nobody leaves without a video |
| **\*** while talking or on hold | Start over: nothing is made (a render already running is called off at fal). They get the beep and can try another idea, or hang up. After an error, **\*** tries again |
| **2** | Sound on/off; turning it on restarts the clip from the beginning |
| **3** | Another take of the same prompt, up to 3 a call |
| **0** | Prints the receipt (once per video; if the printer is offline, 0 tries again) |
| Hold **\*** 3 s, handset on the hook | Staff: restart the app (ignored mid-call) |
| Hold **#** 5 s, handset on the hook | Staff: shut the Pi down safely; unplug once the green light stops blinking (ignored mid-call, since callers press #) |
| Dial **\*123#** | Staff: clear the screen's feed of earlier videos (they move to `cache/archive/`, not deleted) |
| Volume switch (F17 loud / F18 low) | Low turns the handset and the video sound down to `LOW_VOLUME` |
| Ringer switch (F19 on / F20 off) | On: while nobody's on the phone, the earpiece rings and the screen turns Neon with "Pick up." every `RING_EVERY` seconds, starting a few seconds after it's switched on. Lifting the handset stops it. |
| Space | Lift/hang up, for testing with a USB keyboard instead of the phone |

A USB keyboard works as the phone, which is handy for testing before the phone is wired.

## What's on screen

The screen is a sign, not a page: one big thing per state, and only the keys that work right now. Colour says what the
line is doing from across the hall. The models, the bill and the links live on the receipt and the event page.

| State | Screen |
|---|---|
| Free | The last 12 videos, full screen and shuffled, with the prompt under each and a Neon **Pick up** block |
| Ringing | All Neon: **Pick up.** |
| Greeting / listening | **Leave your prompt after the beep.** (the voice adds "Press pound when you're done"), then **Go ahead.** with a meter of pixel squares. No countdown. A prompt still going after 7 s gets a soft "mm-hm" in the earpiece (at most three), so callers know the line is open. |
| On hold | Light Purple: their words, a giant clock, and one line naming the model on the call (**Backup line · FLUX 3 Max** if H3 fails) |
| Ready | Their video, and the keys: **2** sound, **3** another take, **0** print the receipt (its QR is how they keep the video) |
| After a hang-up | Their video stays up for 20 s, with **Pick up to make your own** |
| Errors | White: what happened and what to do, with **\*** to try again |
| Staff | Light Sage |

- **OFFLINE** (top right, black) means this Pi can't reach fal: check the router.
- **The receipt** (`sticker.py`) is the same design on both printers: 608 dots wide on the Zebra, 576 on an 80 mm
  receipt printer, as long as it needs. Lockup, when and where, YOU SAID (and WE MADE YOU AN ORIGINAL after an IP
  swap), the video's clearest frame with its timecode, "Generated in", the bill, the total, the QR, the sign-off.
- **The receipt's QR code** is made for each call: it opens `SHARE_BASE` (`https://fal-adobe-max.vercel.app/v`) with the
  video's fal link after `#`, and the page opens it in a popup over the event page (the video, what they said, the
  models, Save and Share), with no server. Set `SHARE_BASE=` (empty) to print the video's own link instead.
- **Typefaces:** Focal Maxi Medium (headlines), Focal Text and HAL Timezone Mono, from `brand/fonts/`. The fonts are
  licensed, so they aren't in git: copy `FocalMaxi-Medium.otf`, `FocalText-Regular.otf`, `FocalText-Medium.otf`,
  `HALTimezone-MonoRegular.otf`, `HALTimezone-Regular.otf` and `Focal-Upright-VF_wght.ttf` into `brand/fonts/` on each
  Pi. Without them, the screen falls back to DejaVu.

## Files

| File | |
|---|---|
| `booth.py` | The whole app. Every part (phone, line, screen, fal, printer) is a class at the top; the call itself is `Booth`. |
| `run.sh` | Started at login. Screen and audio routing, then runs `booth.py` (and the dashboard) forever. |
| `control.py` | The control API, on each Pi (above) |
| `dashboard.py`, `dashboard.html` | The control dashboard, on your Mac (above) |
| `native.conf` | Settings |
| `fal.key` | The fal API key (made by setup, never committed) |
| `voice/` | The greeting, hold, menu and mm-hm lines (`phone-voice/4`: greeting and mm-hms re-voiced from the v3 voice) |
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
| "Printer's out" on the 0 key | `ls -l /dev/usb/lp0` should exist and belong to group `lp`. Replug the printer's USB. |
| Zebra GK420d blinking green, nothing prints | It's paused, which is normal after a restart or a paper swap: tap feed once. Never hold feed with receipt paper loaded: that starts label calibration, which feeds paper without stopping. |
| OFFLINE on screen, every call busy | Network. Check the travel router; the app retries on its own. |
