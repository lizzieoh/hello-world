# Call-a-fal booth: simple build

One booth, start to finish, in 9 steps. Repeat for each booth. For the full detail, see `HANDOFF.md` and the manual (`booth/manual/index.html`).

**Time:** about 3 hours for the first booth, about 1.5 hours each after that.

---

## What you need (per booth)

- [ ] Wall phone (HA9888 beige)
- [ ] CanaKit Pi 5 Starter Kit PRO (Pi, power supply, case, memory card, cables)
- [ ] ARZOPA Z1FC 16.1" screen + a USB-C charger for it
- [ ] Micro-HDMI to mini-HDMI cable
- [ ] Raspberry Pi Pico
- [ ] Sabrent USB audio adapter + 2 × 3.5 mm pigtail cables
- [ ] Rongta 80 mm receipt printer + paper
- [ ] Wire (26 AWG, several colours), heat-shrink, zip ties + adhesive mounts, 1" clear heat-shrink for the Pico
- [ ] Power strip

**Tools:** soldering iron at 350 °C (660 °F), solder, multimeter, wire strippers, flush cutters, heat gun, small screwdriver, tape and a marker.

---

## 1. Open the phone (15 min)
1. Unscrew the metal back plate and lift off the front shell gently.
2. Tape a label on every cable: **HANDSET, KEYPAD, HOOK, LIGHT, BELL, LINE**.
3. Take a photo. Then unplug everything from the green main board, and remove the board.

## 2. Handset → audio adapter (20 min)
Cut the plug off the 4 handset wires (green, red, black, yellow).

| Wire | Goes to |
|---|---|
| Green | Headphone pigtail **tip** |
| Red | Headphone pigtail **sleeve** |
| Black | Mic pigtail **tip** |
| Yellow | Mic pigtail **sleeve** |

The third wire on each pigtail (ring) is taped off and not used.
Solder each joint and cover it with heat-shrink, then plug the pigtails into the Sabrent: 🎧 for the earpiece, 🎤 for the mic.
✅ **Check:** green to red reads about 230 Ω on the meter.

## 3. Hook switch → Pico (15 min)
1. Find the hook switch's grey ribbon. Leave the glued pads alone.
2. Cut the ribbon about 2 cm (¾ in) before its plug, and peel off the **first two wires** on the side nearest the phone's left edge.
3. Solder one to **pin 20 (GP15)** and the other to **pin 18 (GND)**. Either way round works.

✅ **Check:** those two wires beep on the meter only when the handset is **lifted**.

## 4. Keypad → Pico (20 min)
1. Unplug the keypad ribbon and number its wires from the **red stripe** (wire 1).
2. Solder them in order:

| Wire | 1 | 2 | 3 | 4 | 5 | 6 | 7 |
|---|---|---|---|---|---|---|---|
| Pico pin | 1 | 2 | 4 | 5 | 6 | 7 | 9 |
| GP | GP0 | GP1 | GP2 | GP3 | GP4 | GP5 | GP6 |

Hold the Pico with its USB port at the top; pin 1 is top left. After each joint, check the neighbouring pins don't beep together.

## 5. Load the Pico (10 min)
1. Hold **BOOTSEL**, plug the Pico into a laptop, and drag the CircuitPython `.uf2` onto the **RPI-RP2** drive. Use the "Pico W" build if it's a Pico W.
2. Copy everything in `booth/pico/` (`code.py`, `keymonitor.py`, `keyfinder.py`, `lib/`) onto the **CIRCUITPY** drive.

✅ **Check:** lift the handset and the Pico's LED lights. In a text editor, every key types its digit.
*Same HA9888 phone = same settings, so nothing to change. A different phone model? Map it first (manual Part 10c).*

## 6. Close up the phone (15 min)
1. Slide the Pico into **clear heat-shrink** with the USB end open, and shrink it.
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
