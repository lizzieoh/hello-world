# fal call center — landline booth (×3)

Someone lifts a real phone handset and the call starts. They talk to the agent at
fal-call-center.vercel.app and watch it on a screen. When they hang up, the page resets for the next person.

```
 ┌──────────── phone shell ─────────────┐
 │ handset ── 3.5mm mic + 3.5mm ear ────┼──► USB audio adapter ─┐
 │ hook switch ── 2 wires ──► Pico ─────┼──► USB (types F8/F9) ─┤
 └──────────────────────────────────────┘                       ▼
                                                    Raspberry Pi 5 (Chromium kiosk)
                                                         │ micro-HDMI → HDMI
                                                         ▼
                                                      Monitor
 Pi ── Ethernet ──► switch ──► travel router ──► venue Wi-Fi (backup: phone/5G hotspot)
```

The three things that make this reliable:
1. **Wired Ethernet to your own travel router.** The Pis never touch the venue Wi-Fi themselves. You change one setting on site, not three.
2. **Hook switch = keyboard.** The Pico shows up as a USB keyboard, so no drivers. Lifting the handset types F8 and hanging up types F9 plus a page reload. Every visitor starts on a fresh page.
3. **Self-healing kiosk.** The Pi boots straight into the page and grants the mic automatically. Its audio always goes to the handset. It waits for the network instead of showing an error, and it relaunches Chromium if it crashes. Before the show, the SD card is made read-only so a power cut can't corrupt it.

---

## 1. Shopping list

**Per unit (buy 4: three booths plus one spare)**

| Item | Notes |
|---|---|
| Corded landline phone | The handset must have a coiled cord and a separate, modular body. A modern corded phone already has an electret mic, which works directly. A vintage Western Electric 500 or 2500 looks better but needs its carbon transmitter replaced with an electret capsule. |
| Raspberry Pi 5 (4GB) + **official 27W USB-C PSU** + active cooler + case | The official PSU matters, because cheap ones cause random reboots. |
| 32GB microSD, A2 class (SanDisk Extreme / Samsung PRO) | Buy 2 per unit. |
| Raspberry Pi Pico (or Pico H, with header pins already on) + micro-USB cable | This is the hook-switch box. |
| USB audio adapter with **separate mic and headphone jacks**, e.g. Sabrent AU-MMSA | Separate jacks avoid TRRS pin-order confusion. |
| 2× 3.5mm mono/stereo male pigtail cables (bare wires on one end) | One for the earpiece, one for the mic. |
| Micro-HDMI → HDMI cable, 2m | |
| Monitor with HDMI | A monitor is better than a tablet (see note below). |
| Ethernet cable, 3m | |
| Short USB extension, 30cm | Lets the USB plugs leave the phone body cleanly. |

**Shared booth network (buy 1)**

| Item | Notes |
|---|---|
| GL.iNet travel router (Beryl AX GL-MT3000 or Slate AX) | Joins the venue SSID as a "repeater" and gives you a private network. It handles captive portals and can fail over to a phone or hotspot. |
| 5-port gigabit switch + 4 short Ethernet cables | |
| A phone with a hotspot plan or a 5G hotspot, plus its USB cable | This is the backup internet. |
| Power strip, gaffer tape, zip ties, label maker | |

**Tools:** soldering iron, multimeter, heat-shrink, small screwdrivers, and a USB keyboard for setup.

> **Why a monitor, not a tablet?** An iPad can do this using Guided Access and a USB-C hub (audio adapter + Pico). But iPadOS asks for mic permission again on reloads, and hubs drop devices. A Pi + HDMI monitor has no prompts and recovers by itself after a power cut. If you must use tablets, use them only as screens and keep a Pi doing the audio.

---

## 2. Rewire the phone (≈1 hour per phone)

You keep only three parts of the phone: **the handset, its coiled cord, and the hook switch.** The phone's own circuit board gets disconnected.

1. **Open the phone body.** Unplug the coiled cord from the board's handset jack (RJ9/4P4C) and keep the cord.
2. **Find the handset wires.** Cut the RJ9 plug off the body end of the coiled cord, or use a 4P4C breakout. There are 4 wires.
   - With a multimeter, look for the **earpiece pair**: a steady **100–300 Ω** reading. This is usually the inner two pins.
   - The other pair is the **mic** (usually the outer two pins). An electret mic reads differently with the probes reversed. That means it has a polarity, so note which wire reads as **+** (the higher resistance direction is often +; if the mic is silent later, just swap the two wires).
   - *Vintage carbon mic:* it reads a few hundred Ω, the same both ways, and will be nearly silent. Unscrew the mouthpiece and swap in an electret retrofit capsule.
3. **Solder the earpiece to pigtail #1.** Earpiece wire A → tip (and ring), earpiece wire B → sleeve (ground). Plug it into the adapter's **headphone** jack.
4. **Solder the mic to pigtail #2.** Mic **+** → tip, mic **−** → sleeve. Plug it into the adapter's **mic** jack. The adapter powers the electret through this jack.
5. **Find the hook switch.** This is the plunger under the cradle. Set the multimeter to continuity and find a pair of contacts that **changes** when you press and release the plunger. Desolder it from the board, or cut its traces, so the phone's board isn't connected to it.
6. **Wire the hook switch to the Pico.** One contact → **GP15** (physical pin 20), the other → **GND** (pin 18). Those are the only two wires.
7. Put the Pico and the USB adapter inside the phone body. Hot-glue them to the base, and add strain relief: a zip-tie around each cable inside the shell. Run **two USB cables** out the back where the line cord used to go. Glue the old RJ11 wall jack shut so nobody plugs it into a real line.

**Test before closing it up:** plug the USB adapter into a laptop and record a voice memo through the handset. Your voice should be clear, and a test sound should play in the earpiece.

---

## 3. Flash the Pico (5 min, same for all 4)

1. Hold **BOOTSEL** while plugging the Pico into a laptop. A drive called `RPI-RP2` appears.
2. Drag in the CircuitPython `.uf2` for Raspberry Pi Pico from circuitpython.org (latest stable). The drive reboots as `CIRCUITPY`.
3. From the matching Adafruit CircuitPython Bundle (circuitpython.org/libraries), copy the **`adafruit_hid`** folder into `CIRCUITPY/lib/`.
4. Copy [`pico/code.py`](pico/code.py) onto `CIRCUITPY` as `code.py`.
5. Test: open any text editor, lift the handset, and the Pico's LED lights. Press `F8`/`F9` on a key-tester site (e.g. keyboard-test.space) and confirm lift = F8 and hang up = F9. If they're backwards, set `OFF_HOOK_READS_HIGH = False`.

---

## 4. Get the website to respond to the phone (developer, 10 min)

The site needs to treat **F8 = start call** and **F9 = end call**. Paste [`web/phone-keys.js`](web/phone-keys.js) into the app, point the two selectors at the real Call and End buttons, and deploy.

- If nothing on the site changes, hanging up still works because the Ctrl+R reload kills the call. But picking up won't start anything, so do this step.
- The booth URL has `?booth=1`, so the site can hide its on-screen buttons and show "pick up the phone" instead.
- Make sure the site requests the mic with `echoCancellation: true` and `noiseSuppression: true`. The show floor is loud.
- If the site shows a generated result after the call that people should see, set `RELOAD_ON_HANGUP = False` in `code.py` and have the site reset itself after a short delay.

---

## 5. Build Pi #1 (≈45 min, then it's cloned)

1. With **Raspberry Pi Imager**, flash **Raspberry Pi OS (64-bit)**, the version *with desktop*. In Imager's settings, set username `booth`, a password, your **home Wi-Fi** for now, and turn on SSH.
2. Boot the Pi with the monitor, keyboard, USB audio adapter and Pico plugged in.
3. Copy this repo's `booth/` folder to `/home/booth/booth`. Either use `git clone` and copy it, or use a USB stick.
4. Run:
   ```bash
   cd ~/booth/pi
   chmod +x setup.sh
   ./setup.sh 1
   sudo reboot
   ```
5. After the reboot it should go straight to a black "connecting…" screen and then the call-center page, with no cursor, no popups and no permission prompt.
6. **Test the handset:** lift it and the call starts. Talk, and you should hear the agent in the earpiece. Hang up and the page resets.
   - Earpiece too quiet or the agent hears echo: edit `~/booth/booth.conf` (`SPEAKER_VOLUME`, `MIC_VOLUME`), then reboot.
   - No audio at all: check `cat ~/booth/audio.log`. It should name a USB sink and source.
   - Portrait monitor: set `ROTATE="left"` or `"right"` in `booth.conf`.
7. Get to a desktop for maintenance with **Alt+F4**. Chromium comes back 2 seconds later, so instead SSH in (`ssh booth@fal-booth-1.local`) and run `pkill -f kiosk.sh; pkill chromium`.

---

## 6. Make units #2, #3 and the spare

1. Shut down Pi #1. In Imager (or with *SD Card Copier* on another Pi), **clone its SD card** to three more cards.
2. Boot each clone and change its name: `sudo raspi-config nonint do_hostname fal-booth-2` (then 3, then 4), and reboot.
3. Label everything: phone, Pi, SD card, PSU and cable ends get **1 / 2 / 3 / S**.
4. Also image the **spare SD cards** (the second card per unit) and tape them inside each Pi case.

---

## 7. Network: the travel router

Do this at home, then again on the show floor.

1. Plug in the GL.iNet router. Its admin page is `192.168.8.1`. Set an admin password.
2. Its own Wi-Fi is only for your laptop and phone, so name it something like `fal-booth-ops`.
3. **Internet → Repeater:** join your home Wi-Fi for now. On site, you'll switch this to the Adobe MAX SSID.
4. **Internet → Tethering:** plug in the backup phone (USB tethering on) and set **Multi-WAN priority** to Repeater first, Tethering second. If the venue Wi-Fi dies, the booths switch to cellular within seconds.
5. Connect the router's LAN port → switch → the three Pis by Ethernet.
6. On each Pi, turn **Wi-Fi off** so it can't fall back to the noisy hall: `sudo nmcli radio wifi off`. It stays off after reboots.

**Bandwidth:** voice is about 0.1 Mbps per call, so three calls fit easily in 20 Mbps. If the screen streams generated video, plan for 3–5 Mbps per booth, which still fits. Before the show, ask Adobe/the venue:
- Is the 20 Mbps **dedicated** to your booth or shared?
- Is there a **captive portal** (a sign-in page)? GL.iNet handles it: open the admin page and accept the portal there.
- Is **UDP outbound** allowed? Voice/WebRTC prefers UDP. If it's blocked, the site's provider needs TURN over TCP/443. Test with the site itself on day 0.
- Can they give you **wired Ethernet** at the booth? If yes, plug it into the router's WAN port and keep Wi-Fi as a backup.

---

## 8. Lock it down (last step before packing)

When everything works, make each SD card **read-only**. Power can then be pulled at any moment, and every boot is identical:

```bash
sudo raspi-config nonint do_overlayfs 0   # enable read-only overlay
sudo reboot
```

To change settings later: `sudo raspi-config nonint do_overlayfs 1`, reboot, edit, re-enable, reboot.

---

## 9. Burn-in at home (do not skip)

Run all three units for **at least 24 hours**, together on one router, and check each of these:

- [ ] 50 pickup/hangup cycles per phone, including fast slams and leaving it off the hook for 10 minutes
- [ ] Pull the Pi's power mid-call: it comes back to the page by itself in under 60 s
- [ ] Unplug the router's internet: the screen shows "connecting…" and recovers by itself
- [ ] Unplug and replug the USB audio adapter, then reboot: audio is fine
- [ ] Failover: turn off the home Wi-Fi and calls continue over the phone hotspot
- [ ] Play a recording of loud crowd noise nearby and confirm the agent still understands you
- [ ] Wipe the handsets with an alcohol wipe and confirm nothing breaks (you'll do this all day)

---

## 10. Show day

**Setup (30 min):** power strip → router → switch → Pis. Switch the router's Repeater to the MAX SSID and accept any portal. Power on the monitors, then the Pis. Make a test call on each booth.

**Fixing things during the show:**

| Symptom | Fix |
|---|---|
| Stuck screen or weird state | Hang up and lift again (this reloads the page). Still stuck: unplug the Pi's power for 5 s. |
| "connecting…" forever | Router problem: check the GL.iNet admin from your phone. Force it onto tethering. |
| Earpiece silent | Reseat the USB audio adapter, then power-cycle the Pi. |
| Lifting does nothing | Reseat the Pico USB. The LED should follow the hook. |
| Anything else | Swap in the spare unit (it's identical) and debug later. |

**Pack in the go-bag:** spare unit, spare SD cards, spare PSU, extra Ethernet/HDMI cables, USB keyboard, alcohol wipes, gaffer tape, and this printout.

---

## Optional extras

- **Ringer:** a small speaker or a solenoid on a spare Pico pin can ring the phone every few minutes while it's idle to draw people in.
- **Keypad:** wire the phone's keypad matrix to the Pico so dialed digits are typed into the site, e.g. "press 1 for images, 2 for video".
- **Handset hygiene:** foam mic covers on the mouthpieces, changed daily.

## If the site works by phone number instead (Twilio etc.)

If the agent is reached by dialing a real number, skip the Pi audio. Use a **Grandstream HT801** analog adapter per phone with a SIP line (Twilio Elastic SIP or similar), and set its **"Off-hook Auto-Dial"** to the agent's number. Lifting the handset then dials automatically, and the phone works unmodified, with a real dial tone. The screen just shows the site in the same kiosk setup (keep sections 5–8). The catch is that the screen won't know which caller is which unless the site matches each call to its booth's caller ID.
