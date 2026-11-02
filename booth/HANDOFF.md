# Call-a-fal booth: handoff brief

*For any agent or person picking this up. Last updated Nov 1, 2026.*

## 1. What this is

**Call-a-fal** is fal's booth at Adobe MAX: **3 phone booths + 1 spare**. Each booth is a real landline phone wired to a Raspberry Pi with a small screen and a receipt printer.

The call:
1. A visitor lifts the handset, hears a ring, a click, a greeting and a beep, then says a prompt.
2. They hear *"Please hold while I connect you to our fastest agent"* with elevator music, while fal transcribes the prompt and renders a 10-second video.
3. The video plays muted on the screen. Then the menu: **2** sound on/off, **3** regenerate, **0** print a receipt.

Priorities, in order: **speed and reliability** (heavy foot traffic, a weak 20 Mb venue Wi-Fi), then picture quality.

## 2. Status (Nov 1)

| Area | State |
|---|---|
| Booth software (native Python app) | **Built.** 17-scenario self-test. Running on a laptop in test mode. Not yet run end to end on a Pi with the real phone, screen and printer. |
| Browser version (`/booth` page) | Built, on branch `booth` of `lizzieoh/fal-call-center`. **Not merged.** Kept as the fallback. |
| Phone model | **MS-303 beige wall phone** is the model for all booths (the manuals now describe it). Handset colours measured, keypad map printed on its board, hook = 2 wires (direction not yet measured). An earlier HA9888 phone was wired and mapped first; its notes stay below for reference. |
| Phone #2 (pink XY-1980 wall phone) | Opened and photographed; different board (see §7). Not wired. |
| Receipt printer | Rongta 80 mm in hand. Test-print command exists. Real print not yet confirmed. |
| Pi | 1 × CanaKit Pi 5 Starter Kit PRO **8GB** bought. 3 more to order (§5). |
| Screens | Decided: ARZOPA Z1FC 16.1". **None ordered yet.** |
| Network gear, UPS | On the list, not bought. |
| Booth enclosure | Not designed. Payphone-style shell is the direction (§9). |

## 3. Where everything lives

| What | Where |
|---|---|
| Hardware, wiring, Pi and Pico code | GitHub **`lizzieoh/hello-world`**, branch **`claude/landline-demo-booth-setup-05lxch`**, folder `booth/` |
| Native booth app | `booth/native/` (`booth.py`, `setup-native.sh`, `run.sh`, `native.conf`, `test_booth.py`, `laptop.py`, `voice/`, `brand/`). Docs: `booth/native/README.md` |
| Pico (hook + keypad → USB keyboard) | `booth/pico/` (`code.py`, `keymonitor.py`, `keyfinder.py`, `lib/adafruit_hid`, `SETUP.txt`) |
| Browser kiosk setup (fallback) | `booth/pi/` (`setup.sh`, `kiosk.sh`, `booth.conf`) |
| Shopping list | `booth/SHOPPING.md` (the "Order now" table is the source of truth) |
| Wiring guide, HA9888 | `booth/PHONE-WIRING.md` + `booth/photos/` |
| Beginner build manual (HTML) | `booth/manual/index.html`. Published copy: claude.ai artifact `PQbNymz3SqkZQGjypZ73QJ` (private to Lizzie). |
| Web app + `/booth` page | GitHub **`lizzieoh/fal-call-center`**, branch **`booth`** (unmerged; `/` stays on v24) |
| Deck | Figma "adobe max – exp design" (`sJ3YhW0PIkSuw3X22AMKSz`): slides "Schematic / 8 Booth wiring · minimal" and "Schematic / 9 Booth network · minimal", "Hardware setup" frame, "2.6 · elevation" slides |

## 4. How a booth is wired

```
 THE PHONE (stays inside the phone body)
   Handset (earpiece + mic) ── 2 × 3.5 mm ──► Sabrent USB audio adapter ──USB──┐
   Hook switch ── GP15 + GND ──► Pico (acts as a USB keyboard) ────────USB────┤
   Keypad ── ribbon, GP0… ─────►                                              ▼
                                                     Raspberry Pi 5 (runs booth.py)
                                                       ├─ micro-HDMI → mini-HDMI ─► ARZOPA 16.1" screen
                                                       ├─ USB ─► Rongta 80 mm printer
                                                       └─ Ethernet ─► booth router → internet → fal
```

- **Everything the visitor touches or hears is local to the Pi.** Only the recorded prompt (~0.3 MB up) and the finished video (~12 MB down) cross the network. Slow Wi-Fi means a longer hold, never a broken call.
- **Booth network:** Pis → 8-port switch → GL.iNet travel router. Venue Wi-Fi is the main uplink, with a 5G hotspot or tethered phone over USB as automatic failover. Pi Wi-Fi off.

## 5. Final hardware decisions

| Part | Decision | Why |
|---|---|---|
| Computer | **CanaKit Raspberry Pi 5 Starter Kit PRO, Turbine Black, 128GB, 4GB** ($209.99, Amazon) ×3, plus the 8GB PRO already owned | Kit includes the Pi, 45W power supply, case with fan, heatsink, 128GB card, card reader and 2 micro-HDMI cables. All four units match, so the spare is a drop-in. 4GB is plenty; the 8GB becomes the studio Pi for future pieces. Rejected: Essentials ($189.99, wouldn't match), RasTech (third-party parts, unnamed card). |
| Screen | **ARZOPA Z1FC 16.1" 1080p IPS** ($109.99) ×4, size 16.1", style Z1FC/144Hz | Best *verified* picture under the $150 budget (337 nits measured by PCWorld), 16:9 like the videos, no burn-in. Not the 2.5K styles. Upgrade path: ViewSonic VP16-OLED (~$230–250). Rejected: KYY (unverified), official Pi Monitor (45% gamut), Acer PM161Q (measured dim). |
| Screen cable | **Micro-HDMI (Pi) to mini-HDMI (ARZOPA)**, 3–6 ft ×6 | One direct cable, no adapters |
| Screen power | USB-C wall charger, 30W, ×4 (if not in the ARZOPA box) | Never power the screen from the Pi: it dims |
| Printer | **Rongta 80 mm thermal, USB, auto-cutter** (have 1; order 3) | ESC/POS; the app prints raw raster to `/dev/usb/lp0`, no driver |
| Phone | **MS-303** wall phone, same model in every booth | One keypad map for all; the board prints R1–R4 / C1–C3 |
| Pico | **Pico W**, pre-soldered headers (the one in hand), wired from its underside with plug-on jumpers | One firmware build ("Pico W") for all |
| Audio | Sabrent AU-MMSA + Fancasee 3.5 mm pigtails | Have 1; order 4 more of each |

Full quantities, cables and small parts are in `booth/SHOPPING.md`.

## 6. Shopping summary (to order)

- CanaKit Pi 5 Starter Kit PRO, 4GB × **3**
- ARZOPA Z1FC 16.1" × **4**
- Micro-HDMI to mini-HDMI cable × **6**
- USB-C 30W charger for each screen × **4** (if not included)
- Spare microSD (SanDisk, Samsung or official, 32GB+, A2) × **4**
- Rongta 80 mm printer × **3**, plus 80 × 80 mm thermal paper × **1 case**
- Pico × **4**, Sabrent × **4**, pigtail packs × **4**, spare coiled handset cords × **4**
- Wire: **26 AWG stranded silicone** (6–10 colours) or 28 AWG rainbow ribbon; Dupont jumpers (M-F, F-F); heat-shrink assortment; 0.8 mm 63/37 solder
- Mounting: ¾" adhesive zip-tie mounts with screws (the $9.99 100-pack with 3M PE foam tape is fine), 4" black UV zip ties, **Chanzon 2:1 1" clear heat-shrink** (non-adhesive) to sleeve each Pico, M2 nylon standoffs, 3M VHB tape, Kapton tape
- Show network: GL.iNet travel router ×2, 8-port switch, Cat6 ×10, 5G hotspot (or a tethered phone), APC 600VA UPS ×2, power strips

## 7. Wiring facts (measured on the real phones)

**Beige HA9888 wall phone (board HA9888TSD)**
- **Handset (HSM plug, G+ R− Y− B+):**
  - earpiece = green + red (reads ~230 Ω); green → earpiece pigtail **tip**, red → **sleeve**, ring unused
  - mic = black (+) + yellow (−); black → mic pigtail **tip**, yellow → **sleeve**
- **Hook switch (PX2):**
  - the first two pads, nearest the phone's left edge, **close when the handset is lifted**
  - wire them to **GP15 (pin 20)** and **GND (pin 18)**, either way round
  - `code.py` has `OFF_HOOK_READS_HIGH = False`
  - the pads are factory hot-glued; tap the two wires from the grey ribbon near the PX2 plug instead of digging at the glue
- **Keypad (grey ribbon, red stripe = wire 1):**
  - carbon pads, so a meter's continuity beep won't detect presses
  - wired in ribbon order to GP0, GP1…
  - mapped with the key monitor: **rows = ribbon wires 1–4 (GP0–GP3)**; **columns = wire 7 (GP6) left, 6 (GP5) middle, 5 (GP4) right**
  - `code.py` uses a scanner with a settle delay, because carbon pads are too slow for `keypad.KeyMatrix`
- **Ignore:** BELL (red/black wires), LINE (red/white striped cords to the back jack), MWLI (red light; optional, untested).

**Beige MS-303 wall phone (boards MS-303 MB and MS-303 HK): the model the manual now uses**
- **Handset:** soldered to pads, measured: **green = M−, red = M+, black = R+, yellow = R−**. So the earpiece is black + yellow and the mic is red (+) + green (−). This is different from the HA9888.
  - Earpiece pigtail: black → tip (pigtail red), yellow → sleeve (pigtail black), white taped off.
  - Mic pigtail: red → tip (pigtail red), green → sleeve (pigtail black), white taped off.
- **Hook switch:** small MS-303 HK board with **2 white wires** to the main board's HK spot (glued). Cut at the main board, solder to GP15 (pin 20) and GND (pin 18). Direction (closes on lift or on hang-up) not yet measured.
- **Keypad:** grey ribbon soldered to the main board, which **prints the map**: C3, C2, C1, R4, R3, R2, R1 (top to bottom). Wire R1–R4 → GP0–GP3, **C1 → GP6, C2 → GP5, C3 → GP4**, so the existing `code.py` (ROW_PINS GP0–GP3, COL_PINS GP6, GP5, GP4) works unchanged. Verify by pressing every key. **Colour code (rainbow from R1):** R1 red, R2 orange, R3 yellow, R4 green, C1 blue, C2 purple, C3 grey; hook = the two white wires (yellow sleeve) to pins 18 and 20.
- **Front panel:** ringer on/off and volume sliders on a small board with a white plug: wired to the Pico (below). The label window under the keypad is for an instruction card.
- **Ignore:** BZ (buzzer, red/black), TIP/RING (phone line, red/green twisted).

**Volume and ringer sliders (MS-303 front panel):** built and measured on the first unit with a pin watcher. Each slider is an on/off switch across 2 of the panel's 4 wires: VOLUME on GP16 + GP17 (pins 21, 22; joined = loud), RINGER on GP18 + GP19 (pins 24, 25; joined = on). Wire every unit the same way. The Pico sends F17/F18 (volume loud/low) and F19/F20 (ringer on/off) at start-up, on every flip, on every lift and hang-up, and every 30 s. `booth.py`: low plays the handset and the video sound at `LOW_VOLUME` percent (default 40); ringer on rings the earpiece and shows "Incoming call" every `RING_EVERY` s (default 120) while idle. (Superseded an earlier 3-step slider design on F13–F15; the Quick Build PDF still shows that one until it's regenerated.)

**Pink XY-1980 wall phone (board XY-1980C(M)CE, keypad XY-2550-KB)**
- **Keypad:** 8-wire white ribbon on JK1. The extra wire serves the F/RD keys, probably a 4th column. Wire all 8 to GP0–GP7 and map with the key monitor.
- **Hook switch:** soldered onto the main board (blue S1 "HOOK"), so the board stays in the phone. Find a clean pin pair underneath with the meter; cut the traces leading away if the circuit interferes.
- **Handset:** pads are labelled M+ M− (mic) and R+ R− (earpiece). R+ → tip, R− → sleeve; M+ → tip, M− → sleeve.
- **Each phone model needs its own `ROW_PINS` / `COL_PINS`** in its Pico's `code.py`. Don't swap Picos between models.

**Mounting inside the phone**
- **Insulate:** the phone's back plate is metal. Sleeve the Pico in clear heat-shrink, or use M2 nylon standoffs plus Kapton tape on nearby metal.
- **Strain relief:** zip-tie the wire bundles to a mount next to the Pico. Route the USB cables out through the old LINE jack hole and anchor them inside.
- **USB port:** point the Pico's USB port toward the cable exit. Code updates go over USB, so the phone never needs reopening.

## 8. Software essentials

- **Install on a Pi:** `cd ~/hello-world/booth/native && ./setup-native.sh <1|2|3|4> && sudo reboot`. It asks for the fal key once and stores it in `fal.key`; **never commit it**.
- **Self-test:** `venv/bin/python test_booth.py` (fake hardware, no fal calls).
- **Printer test:** `sudo python3 booth.py --test-print`. On a Mac with a CUPS raw queue: `BOOTH_PRINTER=rongta python3 booth.py --test-print`.
- **Laptop test mode:** `laptop.py`. The screen shows in a browser tab at `localhost:8765`, and the Pico or a keyboard types into it.

**Models**
- Video: **H3 Max** (`minimax/h3-max/text-to-video`, 768P, 10 s) first, then **FLUX 3 Max** (`blackforestlabs/flux-3-max/text-to-video`) as backup. `MODEL=flux` in `native.conf` swaps the order.
- A request fal refuses (4xx, safety) is not retried on the backup model.
- Speech to text: **Wizper** (`fal-ai/wizper`). If it hasn't answered in 1.5 s, **ElevenLabs Scribe v2** joins and the first answer wins; 12 s deadline.

**Keys**

| Key | Action |
|---|---|
| Lift / hang up | F8 / F9 |
| # while talking | Done talking |
| 2 | Sound on/off |
| 3 | Regenerate (a different take) |
| 0 | Print the receipt |
| Hold \* 3 s | Restart the app |
| Hold # 5 s | Safe shutdown (F10) |
| Dial \*123# | Clear the screen's feed |

**Reliability built in**
- `run.sh` restarts the app in 2 s if it exits, and the app restarts mpv if it dies mid-call.
- Recent videos are cached on the Pi, so the between-calls loop works offline.
- A busy signal plays on any failure.
- A green/red dot shows whether fal is reachable.
- A hang-up must last 0.4 s, so a flickering hook can't drop a call.
- Temperature, fan speed and throttling go to the log.

## 9. Open items, in priority order

1. **First full run on a real Pi:** phone + Pico + Sabrent + ARZOPA + Rongta, via `setup-native.sh`, then 20 test calls. Only the self-test and laptop mode have been run so far.
2. **Screen checks on arrival:**
   - Unplug and replug its power: does it switch itself back on? If not, a venue power blip leaves the booth dark.
   - Does it ever auto-sleep?
   - Set the Pi to output 1080p.
3. **Real receipt print** on the Rongta (`--test-print`), then a call with **0**.
4. **Pink phone:** decide whether it's used at all. If yes, map its keypad and wire its hook switch.
5. **Booth enclosure:** payphone-style shell with the screen inside above the phone, the receipt from a "coin return" slot, and the handset/keypad **no higher than 48" (122 cm)** for ADA.
   - The deck's elevation slides still show **27" monitors at 64" centre**. Update them for the 16.1" screen.
6. **Venue:** confirm the exhibitor Wi-Fi/hotspot rules in writing, order booth power, and get a wired-drop quote.
7. **fal account:** credits and concurrency for 3 booths all day; key stored only on the Pis.
8. **Content and privacy:** the safety checker is on and a blocked-word list exists. Add a sign: "Your voice is used to make your video and isn't stored". Make sure that's true.
9. **Merge decision:** the `booth` branch of `fal-call-center` stays unmerged until Lizzie says so.
10. **Optional:**
    - If OLED screens are chosen later, add overlay pixel-shift and dimming to `booth.py` (burn-in).
    - Blink the phone's red light (MWLI) while rendering, if it tests as an LED.

## 10. Working rules for agents on this project

- Push only to **`claude/landline-demo-booth-setup-05lxch`** in `lizzieoh/hello-world`. **Don't open PRs or merge** without Lizzie asking.
- Never commit `fal.key` or any API key. The fal key lives only on each Pi.
- Lizzie is an artist and art director (experiential design at fal), not an electronics engineer.
  - Explain the why in plain language.
  - Always give **both °C and °F** (and both metric and imperial lengths).
  - Prefer verified specs and measured reviews over marketing claims. Say when something is unverified or out of stock.
- Match the deck's style for anything visual:
  - white slides
  - **Focal** (titles and text) and **HAL Timezone Mono** (labels)
  - indigo accent `#402B9E`
  - header "Call-a-fal • …", "For Internal Use Only", "2026"
- Other Claude sessions push to the same branch. **Pull before editing** and rebase or merge rather than force-push.
