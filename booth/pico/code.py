# fal booth hook switch -> USB keyboard (CircuitPython on a Raspberry Pi Pico).
# Lift handset  -> F8  (site starts the call)
# Hang up       -> F9  (site ends the call), then Ctrl+R to reset the page
#
# Wiring: hook-switch contact between GP15 (pin 20) and GND (pin 18). Nothing else.
import time

import board
import digitalio
import usb_hid
from adafruit_hid.keyboard import Keyboard
from adafruit_hid.keycode import Keycode

HOOK_PIN = board.GP15
OFF_HOOK_READS_HIGH = True  # if pickup/hangup come out backwards, flip this
PICKUP_KEYS = (Keycode.F8,)
HANGUP_KEYS = (Keycode.F9,)
RELOAD_ON_HANGUP = True     # set False if the site shows a post-call result worth keeping
RELOAD_DELAY_S = 0.5
DEBOUNCE_S = 0.08

hook = digitalio.DigitalInOut(HOOK_PIN)
hook.switch_to_input(pull=digitalio.Pull.UP)
led = digitalio.DigitalInOut(board.LED)
led.switch_to_output()

kbd = None
while kbd is None:
    try:
        kbd = Keyboard(usb_hid.devices)
    except OSError:
        time.sleep(1)


def off_hook():
    return hook.value == OFF_HOOK_READS_HIGH


def tap(*keys):
    kbd.press(*keys)
    time.sleep(0.05)
    kbd.release_all()


state = off_hook()
led.value = state
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
                    time.sleep(RELOAD_DELAY_S)
                    tap(Keycode.CONTROL, Keycode.R)
    time.sleep(0.01)
