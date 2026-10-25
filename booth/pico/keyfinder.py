# Keypad finder: the Pico works out the keypad's wiring itself, so the multimeter isn't needed for it.
#
# 1. Wire the keypad ribbon to the Pico in order: wire 1 (the red stripe) -> GP0, wire 2 -> GP1, wire 3 -> GP2 ...
#    (up to 15 wires, GP0-GP14; GP15 stays for the hook switch).
# 2. In code.py set FIND_KEYS = True and save. The Pico restarts into this finder.
# 3. Open a text editor on the laptop and click into it. The Pico types instructions: hold each key it asks for.
# 4. At the end it types two lines (ROW_PINS and COL_PINS). Put them in code.py in place of the old ones and set
#    FIND_KEYS = False again.
#
# It sees carbon-pad keys a meter's beeper misses: the Pico's own pull-ups notice a contact of up to tens of kilohms.
import time

import board
import digitalio
import usb_hid
from adafruit_hid.keyboard import Keyboard
from adafruit_hid.keyboard_layout_us import KeyboardLayoutUS

KEYS = "123456789*0#"
ROWS = ("123", "456", "789", "*0#")
COLS = ("147*", "2580", "369#")
WAIT_S = 30  # per key, then it moves on

pins = []
for n in range(15):
    p = digitalio.DigitalInOut(getattr(board, "GP%d" % n))
    p.switch_to_input(pull=digitalio.Pull.UP)
    pins.append(p)
led = digitalio.DigitalInOut(board.LED)
led.switch_to_output()

time.sleep(1)  # let the laptop see the keyboard first
layout = KeyboardLayoutUS(Keyboard(usb_hid.devices))


def say(text):
    print(text, end="")
    layout.write(text)


def scan():
    """Every pair of GP pins that are joined right now, as (lower, higher)."""
    joined = set()
    for i, drive in enumerate(pins):
        drive.switch_to_output(value=False)
        time.sleep(0.0005)  # long wires and carbon pads: give it a moment
        for j, p in enumerate(pins):
            if j > i and not p.value:
                joined.add((i, j))
        drive.switch_to_input(pull=digitalio.Pull.UP)
    return joined


def steady(want_pressed, base, secs):
    """Waits until keys are held (or all released) for 60 ms. Returns the pairs, or None on timeout."""
    end, since, last = time.monotonic() + secs, None, None
    while time.monotonic() < end:
        now = scan() - base
        led.value = bool(now)
        if bool(now) == want_pressed and (since is None or now == last):
            since = since or time.monotonic()
            if time.monotonic() - since > 0.06:
                return now
        else:
            since = None
        last = now
    return None


def wires(pairs):
    return ", ".join("%d+%d" % (a + 1, b + 1) for a, b in sorted(pairs)) or "nothing"


say("\nKeypad finder. Don't touch any keys for a second...\n")
time.sleep(1)
base = scan()  # wires that are always joined don't count
if base:
    say("(always joined, ignored: wires %s)\n" % wires(base))

found = {}
for key in KEYS:
    say("Hold %s ... " % key)
    pairs = steady(True, base, WAIT_S)
    if pairs is None:
        say("nothing seen, skipped\n")
        continue
    found[key] = pairs
    say("wires %s\n" % wires(pairs))
    steady(False, base, 10)


def common(keys):
    """The one wire every key in this group shares."""
    sets = [set(w for pair in found[k] for w in pair) for k in keys if k in found]
    if not sets:
        return None
    shared = set.intersection(*sets)
    return shared.pop() if len(shared) == 1 else None


rows = [common(r) for r in ROWS]
cols = [common(c) for c in COLS]
if None not in rows and None not in cols and len(set(rows + cols)) == 7:
    say("\nIt's a grid. Put these two lines in code.py (replacing the old ROW_PINS and COL_PINS):\n\n")
    say("ROW_PINS = (%s)\n" % ", ".join("board.GP%d" % w for w in rows))
    say("COL_PINS = (%s)\n" % ", ".join("board.GP%d" % w for w in cols))
    say("\nThen set FIND_KEYS = False and save.\n")
else:
    say("\nNot a simple grid (or some keys were skipped). Send the list above to Claude.\n")

while True:
    led.value = bool(scan() - base)
    time.sleep(0.05)
