# Call-a-fal booth: simple build

One booth, start to finish, in 9 steps. Repeat for each booth. For the full detail, see `HANDOFF.md` and the manual (`booth/manual/index.html`).

**Time:** about 3 hours for the first booth, about 1.5 hours each after that.

---

## What you need (per booth)

- [ ] Wall phone (MS-303 beige)
- [ ] CanaKit Pi 5 Starter Kit PRO (Pi, power supply, case, memory card, cables)
- [ ] ARZOPA Z1FC 16.1" screen + a USB-C charger for it
- [ ] Micro-HDMI to mini-HDMI cable
- [ ] Raspberry Pi Pico W + female-to-female jumper wires
- [ ] Sabrent USB audio adapter + 2 × 3.5 mm pigtail cables
- [ ] Rongta 80 mm receipt printer + paper
- [ ] Wire (26 AWG, several colours), heat-shrink, zip ties + adhesive mounts, 1" clear heat-shrink for the Pico
- [ ] Power strip

**Tools:** soldering iron at 350 °C (660 °F), solder, multimeter, wire strippers, flush cutters, heat gun, small screwdriver, tape and a marker.

---

## 1. Open the phone (15 min)
1. Unscrew the metal back plate and lift the front shell off gently.
2. Unplug the white plug from the front panel (ringer and volume sliders). It's the only plug; keep its 4 wires for the Pico (volume and ringer). Everything else gets cut.
3. Take a photo before cutting. Always cut wires **close to the main board** to keep the most length.

## 2. Handset → audio adapter (20 min)
Cut the 4 handset wires at the M−, M+, R+, R− pads. On the MS-303, **black + yellow = earpiece**, **red + green = mic**.

| Handset wire | Pad | Pigtail wire | Plug part |
|---|---|---|---|
| Black | R+ | Red | earpiece **tip** |
| Yellow | R− | Black | earpiece **sleeve** |
| Red | M+ | Red | mic **tip** |
| Green | M− | Black | mic **sleeve** |

Each pigtail's white wire (ring) isn't used: snip it short and heat-shrink the end. Heat-shrink each joint, then ½" (13 mm) heat-shrink over each finished splice.
Plug the pigtails into the Sabrent: 🎧 earpiece, 🎤 mic.
✅ **Check:** black to yellow reads 100–300 Ω. Record and play back; if the mic is very quiet, swap red and green.

## 3. Hook switch (10 min)
1. The hook board (MS-303 HK) has **two white wires** to **HK** on the main board. Cut both close to the main board and strip 3 mm (⅛ in).
2. Meter on beep, probes on the two wires; press the plunger, then let go.

✅ Beeps when **released** (lifted): nothing to change. Beeps when **pressed**: set `OFF_HOOK_READS_HIGH = True` in the Pico's `code.py`.

## 4. Keypad + hook → Pico (30 min)
1. The board prints the keypad map: **C3, C2, C1, R4, R3, R2, R1**. Mark the **R1** wire with a dot, then cut the ribbon close to the board.
2. Turn the Pico **underside up** (pins sticking out, names printed), USB at the top: pins 1–20 run down the right edge.
3. For the prototype: solder each wire to half a female-to-female jumper, heat-shrink it, and push it onto the pin. Hot-glue the rows once everything works.

| Wire | R1 | R2 | R3 | R4 | C1 | C2 | C3 | Hook | Hook |
|---|---|---|---|---|---|---|---|---|---|
| Colour | red | orange | yellow | green | blue | purple | grey | white | white |
| Pico | GP0 | GP1 | GP2 | GP3 | **GP6** | GP5 | **GP4** | GND | GP15 |
| Pin | 1 | 2 | 4 | 5 | **9** | 7 | **6** | 18 | 20 |

The columns go in reverse on purpose, so the Pico's code works unchanged. After each joint, check the neighbouring pins don't beep together.

## 5. Load the Pico (10 min)
1. Hold **BOOTSEL**, plug the Pico into a laptop, and drag the CircuitPython `.uf2` onto the **RPI-RP2** drive. Use the **"Pico W"** build (the board says Pico W).
2. Copy everything in `booth/pico/` (`code.py`, `keymonitor.py`, `keyfinder.py`, `lib/`) onto the **CIRCUITPY** drive.

✅ **Check:** lift the handset and the Pico's LED lights. In a text editor, every key types its digit.
*Wired as in step 4, the code works as-is. A wrong digit? Tell Claude what each key typed: it's a one-line fix.*

## 6. Close up the phone (15 min)
1. Once everything works, slide the Pico into **clear heat-shrink** with the USB end open, and shrink it.
2. Stick the Pico and the Sabrent inside with **adhesive mounts**, away from the metal back plate.
3. Zip-tie all the wires so a tug pulls on the ties, not the solder joints.
4. Run both USB cables out through the old **LINE** jack hole, anchor them inside with a zip tie, and close the phone.

## 7. Set up the Pi (30 min, mostly waiting)
1. Assemble the CanaKit: heatsink on, fan plugged into the **FAN** socket, case closed.
2. Plug in the screen (micro-HDMI to mini-HDMI), keyboard, Ethernet, then power.
3. On first boot, open Terminal and run:
   ```
   git clone -b claude/landline-demo-booth-setup-05lxch https://github.com/lizzieoh/hello-world.git
   cd hello-world/booth/native
   ./setup-native.sh 1        # the booth number: 1, 2, 3 or 4
   sudo reboot
   ```
   Paste the fal key when it asks. The self-test should end with **all tests passed** (17/17 today).

## 8. Connect everything (10 min)

| From | To |
|---|---|
| Phone: Sabrent USB | Pi USB |
| Phone: Pico USB | Pi USB |
| Printer USB | Pi USB |
| Screen | Its own USB-C charger (not the Pi) |
| Pi, screen, printer | Power strip |

## 9. Test call ✅
- [ ] Lift the handset: ring, click, greeting, beep
- [ ] Say a prompt: you hear "please hold" and music, and the video appears muted
- [ ] **2**: sound on · **3**: a new take · **0**: the receipt prints and cuts
- [ ] Hang up: the screen loops recent videos
- [ ] Unplug the screen's power, plug it back in: it comes back on by itself
- [ ] Pull the Ethernet while on hold: you get a busy signal. Plug it back in: the next call works
- [ ] 20 calls in a row with no problems = booth done

---

## Show-day cheat sheet

| Problem | Fix |
|---|---|
| Something's stuck | Hold **\*** for 3 s: the app restarts in seconds |
| Need to power off | Hold **#** for 5 s, wait for the green light to stop blinking, then unplug |
| Red dot on screen | The network is down: check the router |
| "Printer offline" | Replug the printer's USB, then press **0** again |
| Dead booth | Swap in the spare unit; each booth's memory card works in any Pi |
