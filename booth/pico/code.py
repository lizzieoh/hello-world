# fal booth phone -> USB keyboard (CircuitPython on a Raspberry Pi Pico).
# Lift handset  -> F8   (site: fresh session, start listening)
# Hang up       -> F9   (site: stop listening/audio, leave the image on screen)
# Keypad 0-9 * # -> types that character (site: "press 2 for audio", "press 3 to regenerate")
# Hold * for 3 s -> Ctrl+R (staff reset: restarts the booth app)
# Hold # for 5 s -> F10 (staff: shut the booth down safely, then it can be unplugged)
#
# Wiring:
#   hook switch: GP15 (pin 20) and GND (pin 18)
#   keypad ribbon: wire 1 (red stripe) -> GP0, wire 2 -> GP1, ... in order.
#   Which of those are rows and columns: set FIND_KEYS = True once and the Pico works it out (keyfinder.py).
import time

import board
import digitalio
import usb_hid
from adafruit_hid.keyboard import Keyboard
from adafruit_hid.keycode import Keycode

FIND_KEYS = False  # True: live keypad monitor over USB serial (keymonitor.py), for mapping a new keypad

HOOK_PIN = board.GP15
OFF_HOOK_READS_HIGH = False  # HA9888 wall phone: pads 1+2 close when the handset is lifted, pulling GP15 low.
                            # If pickup/hangup come out backwards, flip this.
PICKUP_KEYS = (Keycode.F8,)
HANGUP_KEYS = (Keycode.F9,)
RELOAD_ON_HANGUP = False    # True = wipe the screen on hangup instead of leaving the image up
DEBOUNCE_S = 0.08
HANGUP_STEADY_S = 0.4       # a hang-up has to last this long: a worn hook switch flickers open as it's lifted

# HA9888 wall phone, mapped with the key monitor: ribbon wires 1-4 are rows 1-4, wire 7 is the left column,
# 6 the middle, 5 the right (ribbon wire n on GP n-1).
ROW_PINS = (board.GP0, board.GP1, board.GP2, board.GP3)
COL_PINS = (board.GP6, board.GP5, board.GP4)
# Key at (row, col). Rows and columns above come from the key finder. If digits come out wrong, swap entries here.
LAYOUT = ("1", "2", "3",
          "4", "5", "6",
          "7", "8", "9",
          "*", "0", "#")
KEYCODES = {
    "1": (Keycode.ONE,), "2": (Keycode.TWO,), "3": (Keycode.THREE,),
    "4": (Keycode.FOUR,), "5": (Keycode.FIVE,), "6": (Keycode.SIX,),
    "7": (Keycode.SEVEN,), "8": (Keycode.EIGHT,), "9": (Keycode.NINE,),
    "0": (Keycode.ZERO,),
    "*": (Keycode.SHIFT, Keycode.EIGHT),
    "#": (Keycode.SHIFT, Keycode.THREE),
}
STAFF_RESET_HOLD_S = 3.0
STAFF_SHUTDOWN_HOLD_S = 5.0
SHUTDOWN_KEYS = (Keycode.F10,)
KEYS_ONLY_WHEN_OFF_HOOK = True

if FIND_KEYS:
    import keymonitor  # noqa: F401  (runs until FIND_KEYS is set back to False)

hook = digitalio.DigitalInOut(HOOK_PIN)
hook.switch_to_input(pull=digitalio.Pull.UP)
led = digitalio.DigitalInOut(board.LED)
led.switch_to_output()
# The phone's carbon-pad keys are slow to pull a line low, too slow for keypad.KeyMatrix, which reads straight
# after switching rows. This scan waits SETTLE_S per row (the same as keymonitor.py, which sees every key).
SETTLE_S = 0.0005
rows = []
for pin in ROW_PINS:
    r = digitalio.DigitalInOut(pin)
    r.switch_to_input(pull=digitalio.Pull.UP)
    rows.append(r)
cols = []
for pin in COL_PINS:
    c = digitalio.DigitalInOut(pin)
    c.switch_to_input(pull=digitalio.Pull.UP)
    cols.append(c)


def held_keys():
    """Indexes into LAYOUT of every key held right now."""
    held = set()
    for r, row in enumerate(rows):
        row.switch_to_output(value=False)
        time.sleep(SETTLE_S)
        for c, col in enumerate(cols):
            if not col.value:
                held.add(r * len(cols) + c)
        row.switch_to_input(pull=digitalio.Pull.UP)
    return held

kbd = None
while kbd is None:
    try:
        kbd = Keyboard(usb_hid.devices)
    except OSError:
        time.sleep(1)


def off_hook():
    return hook.value == OFF_HOOK_READS_HIGH


def tap(*codes):
    kbd.press(*codes)
    time.sleep(0.05)
    kbd.release_all()


state = off_hook()
led.value = state
print("booth ready, handset", "lifted" if state else "hung up")
star_down_at = None
hash_down_at = None
down = set()      # keys reported as pressed
candidate = set()  # what the last scan saw; a change has to show up twice in a row (debounce)
while True:
    reading = off_hook()
    if reading != state:
        steady = DEBOUNCE_S if reading else HANGUP_STEADY_S
        t0 = time.monotonic()
        while off_hook() == reading and time.monotonic() - t0 < steady:
            time.sleep(0.01)
        if off_hook() == reading:
            state = reading
            led.value = state
            print("hook:", "lifted" if state else "hung up")  # USB serial log, for testing
            if state:
                tap(*PICKUP_KEYS)
            else:
                tap(*HANGUP_KEYS)
                if RELOAD_ON_HANGUP:
                    time.sleep(0.5)
                    tap(Keycode.CONTROL, Keycode.R)

    now = held_keys()
    if now == candidate and now != down:
        for n in now - down:
            key = LAYOUT[n]
            if key == "*":
                star_down_at = time.monotonic()
            if key == "#":
                hash_down_at = time.monotonic()
            print("key:", key, "" if state or not KEYS_ONLY_WHEN_OFF_HOOK else "(ignored: hung up)")
            if state or not KEYS_ONLY_WHEN_OFF_HOOK:
                tap(*KEYCODES[key])
        if LAYOUT.index("*") in down - now:
            star_down_at = None
        if LAYOUT.index("#") in down - now:
            hash_down_at = None
        down = now
    candidate = now

    if star_down_at is not None and time.monotonic() - star_down_at > STAFF_RESET_HOLD_S:
        star_down_at = None
        tap(Keycode.CONTROL, Keycode.R)

    if hash_down_at is not None and time.monotonic() - hash_down_at > STAFF_SHUTDOWN_HOLD_S:
        hash_down_at = None
        print("staff: shutdown")
        tap(*SHUTDOWN_KEYS)

    time.sleep(0.01)
