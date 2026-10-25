# fal booth phone -> USB keyboard (CircuitPython on a Raspberry Pi Pico).
# Lift handset  -> F8   (site: fresh session, start listening)
# Hang up       -> F9   (site: stop listening/audio, leave the image on screen)
# Keypad 0-9 * # -> types that character (site: "press 2 for audio", "press 3 to regenerate")
# Hold * for 3 s -> Ctrl+R (staff reset: reloads the page)
#
# Wiring:
#   hook switch: GP15 (pin 20) and GND (pin 18)
#   keypad ribbon: wire 1 (red stripe) -> GP0, wire 2 -> GP1, ... in order.
#   Which of those are rows and columns: set FIND_KEYS = True once and the Pico works it out (keyfinder.py).
import time

import board
import digitalio
import keypad
import usb_hid
from adafruit_hid.keyboard import Keyboard
from adafruit_hid.keycode import Keycode

FIND_KEYS = False  # True: map the keypad (type into a text editor), then paste its two lines below and set False

HOOK_PIN = board.GP15
OFF_HOOK_READS_HIGH = False  # HA9888 wall phone: pads 1+2 close when the handset is lifted, pulling GP15 low.
                            # If pickup/hangup come out backwards, flip this.
PICKUP_KEYS = (Keycode.F8,)
HANGUP_KEYS = (Keycode.F9,)
RELOAD_ON_HANGUP = False    # True = wipe the screen on hangup instead of leaving the image up
DEBOUNCE_S = 0.08

ROW_PINS = (board.GP0, board.GP1, board.GP2, board.GP3)
COL_PINS = (board.GP4, board.GP5, board.GP6)
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
KEYS_ONLY_WHEN_OFF_HOOK = True

if FIND_KEYS:
    import keyfinder  # noqa: F401  (runs until FIND_KEYS is set back to False)

hook = digitalio.DigitalInOut(HOOK_PIN)
hook.switch_to_input(pull=digitalio.Pull.UP)
led = digitalio.DigitalInOut(board.LED)
led.switch_to_output()
keys = keypad.KeyMatrix(ROW_PINS, COL_PINS, columns_to_anodes=True)

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
star_down_at = None
event = keypad.Event()
while True:
    reading = off_hook()
    if reading != state:
        time.sleep(DEBOUNCE_S)
        if off_hook() == reading:
            state = reading
            led.value = state
            if state:
                tap(*PICKUP_KEYS)
            else:
                tap(*HANGUP_KEYS)
                if RELOAD_ON_HANGUP:
                    time.sleep(0.5)
                    tap(Keycode.CONTROL, Keycode.R)

    while keys.events.get_into(event):
        key = LAYOUT[event.key_number]
        if key == "*":
            star_down_at = time.monotonic() if event.pressed else None
        if event.pressed and (state or not KEYS_ONLY_WHEN_OFF_HOOK):
            tap(*KEYCODES[key])

    if star_down_at is not None and time.monotonic() - star_down_at > STAFF_RESET_HOLD_S:
        star_down_at = None
        tap(Keycode.CONTROL, Keycode.R)

    time.sleep(0.01)
