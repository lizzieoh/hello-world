# Live keypad monitor: prints every change in which ribbon wires are joined, over USB serial only.
import time
import board
import digitalio

pins = []
for n in range(15):
    p = digitalio.DigitalInOut(getattr(board, "GP%d" % n))
    p.switch_to_input(pull=digitalio.Pull.UP)
    pins.append(p)
led = digitalio.DigitalInOut(board.LED)
led.switch_to_output()


def scan():
    joined = set()
    for i, drive in enumerate(pins):
        drive.switch_to_output(value=False)
        time.sleep(0.0005)
        for j, p in enumerate(pins):
            if j > i and not p.value:
                joined.add((i + 1, j + 1))
        drive.switch_to_input(pull=digitalio.Pull.UP)
    return joined


time.sleep(1)
base = scan()
print("\nMONITOR ready. always joined:", sorted(base))
last, t0 = set(), time.monotonic()
while True:
    now = scan() - base
    led.value = bool(now)
    if now != last:
        time.sleep(0.03)
        if scan() - base == now:
            print("%.1f %s" % (time.monotonic() - t0, sorted(now) if now else "released"))
            last = now
