#!/usr/bin/env python3
"""Drives whole calls through the booth with the hardware faked: no handset, screen, Pico, printer or fal needed.

The real mixer, voice-activity detection, fal fallback logic, video cache and receipt printer run; only the sound
card, mpv and fal's servers are stand-ins. Run it on any machine with numpy, Pillow and ffmpeg:
    python3 test_booth.py
"""
import json
import shutil
import subprocess
import tempfile
import threading
import time
import traceback
from pathlib import Path

import numpy as np

import booth as B

TMP = Path(tempfile.mkdtemp(prefix="booth-test-"))
CLIP = TMP / "clip.mp4"
subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=1280x720:d=10:r=24", "-pix_fmt", "yuv420p",
                str(CLIP)], check=True)
URL = CLIP.as_uri()


# ---------- stand-ins ----------
class Screen:
    def __init__(self):
        self.log = []
        self.last_text = ""

    def __getattr__(self, name):
        return lambda *a, **kw: self.log.append((name, *a, *kw.values()))

    def text(self, ass):
        self.last_text = ass

    def calls(self, name):
        return [c for c in self.log if c[0] == name]


class HTTPError(Exception):
    def __init__(self, code):
        super().__init__(f"HTTP {code}")
        self.status_code = code


class InProgress: pass
class Completed: pass


class Client:
    """fal_client.SyncClient's shape. `plan` says what each endpoint does: 'ok', a status code, or 'hang'."""

    def __init__(self):
        self.plan, self.text, self.calls = {}, "a red phone ringing on the moon", []
        self.ip = None  # what the IP check says the prompt borrows; None = an original idea

    def run(self, endpoint, args, timeout=None):
        self.calls.append(endpoint)
        if endpoint == B.Fal.IP_CHECK:
            if isinstance(self.plan.get(endpoint), int):
                raise HTTPError(self.plan[endpoint])
            time.sleep(0.05)
            verdict = {"ok": False, "what": self.ip} if self.ip else {"ok": True}
            return {"output": f"```json\n{json.dumps(verdict)}\n```"}  # with the fences some models add
        assert args["audio_url"].startswith("data:audio/x-wav;base64,")
        assert args.get("language", "en") == "en"
        if self.plan.get(endpoint) == "hang":
            time.sleep(30)
        time.sleep(0.05)
        return {"text": self.text}

    def submit(self, endpoint, args):
        self.calls.append(endpoint)
        assert args["duration"] == 10
        what, polls = self.plan.get(endpoint, "ok"), [0]

        class Handle:
            def status(_):
                polls[0] += 1
                return Completed() if what != "hang" and polls[0] >= 3 else InProgress()

            def get(_):
                if isinstance(what, int):
                    raise HTTPError(what)
                return {"video": {"url": URL}}

            def cancel(_):
                self.calls.append("cancel")

        return Handle()


class Line(B.Line):
    """The real Line, with the sound card replaced by a loop that pulls audio 10x faster than real time and feeds
    the mic whatever the test says the caller is doing."""

    def __init__(self, post, cfg):
        self.mic = lambda t: 0.0  # caller's loudness at t seconds into listening
        self.heard_out = []
        super().__init__(post, cfg)

    def _keep_open(self):
        out = np.zeros((4800, 1), np.float32)
        t = 0
        while True:
            self._play(out, 4800, None, None)
            self.heard_out.append(float(np.abs(out).max()))
            if self.rec:
                amp = self.mic(self.rec["n"] / B.MIC_SR)
                noise = np.random.default_rng(t).normal(0, 0.002, (800, 1)).astype(np.float32)
                voice = amp * np.sin(np.arange(800) / B.MIC_SR * 2 * np.pi * 220).reshape(-1, 1).astype(np.float32)
                self._listen(noise + voice, 800, None, None)
                self._listen(noise + voice, 800, None, None)
            t += 1
            time.sleep(0.01)

    def playing(self):
        with self.lock:
            return [next((n for n, s in self.sounds.items() if c.data is s), "other") for c in self.clips]


def talk(secs=1.0, start=0.3):
    return lambda t: 0.2 if start <= t < start + secs else 0.0


# ---------- harness ----------
def make(**over):
    cfg = B.load_config(Path("/nonexistent"))
    (TMP / "printer.bin").touch()  # exists, so the booth prints here and never to a real printer on this machine
    cfg.update({"printer": str(TMP / "printer.bin"), "off_hook_idle": 30.0, "attract_after": 0.5,
                "render_timeout": 2.0, **over})
    client = Client()
    booth = B.Booth(cfg)
    booth.line = Line(booth.post, cfg)
    booth.screen = Screen()
    booth.fal = B.Fal(cfg, client=client)
    booth.printer = B.Printer(cfg)
    booth.cache = B.Cache(cfg["history"], root=TMP / f"cache{time.monotonic_ns()}")
    booth.online = True
    stop = threading.Event()
    threading.Thread(target=booth.run, kwargs={"until": stop.is_set, "watch_network": False}, daemon=True).start()
    time.sleep(0.1)
    return booth, client, stop


def wait(cond, secs=10, what=""):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        if cond():
            return
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {what or cond}")


def lift_and_say(booth, mic):
    booth.line.mic = mic
    booth.post(("hook", True))
    wait(lambda: booth.state == "listening", 8, "listening")


# ---------- scenarios ----------
def test_full_call():
    booth, client, stop = make()
    lift_and_say(booth, talk())
    wait(lambda: booth.state == "hold", 8, "on hold")
    wait(lambda: "music" in booth.line.playing(), 3, "hold music")
    wait(lambda: booth.view["said"] == "“a red phone ringing on the moon”", 5, "prompt on screen")
    wait(lambda: booth.state == "ready", 8, "video ready")
    assert client.calls[:3] == ["fal-ai/wizper", B.Fal.IP_CHECK, "minimax/h3-max/text-to-video"], client.calls
    url = booth.screen.calls("video")[-1][1]
    assert url == URL and booth.screen.calls("video")[-1][2] is True  # muted
    assert "Ready in" in booth.view["status"] and "[2]" in booth.screen.last_text
    booth.post(("file_loaded",))
    wait(lambda: "menu" in booth.line.playing(), 6, "menu read")
    booth.post(("key", "2"))
    wait(lambda: ("mute", False) in booth.screen.log and booth.screen.calls("restart_clip"), 3, "sound on")
    wait(lambda: "menu" not in booth.line.playing(), 1, "a key cuts the menu off (after a short fade)")
    booth.post(("key", "0"))
    wait(lambda: booth.view["keys"].get("0") == "Printed", 25, "printed")
    data = (TMP / "printer.bin").read_bytes()
    assert data.startswith(b"\x1b@") and data.endswith(b"\x1dV\x42\x00") and b"\x1dv0\x00" in data
    booth.post(("key", "3"))
    wait(lambda: booth.state == "hold", 3, "regenerating")
    assert ("mute", True) in booth.screen.log
    wait(lambda: booth.state == "ready", 8, "second video")
    assert client.calls.count("minimax/h3-max/text-to-video") == 2
    booth.post(("hook", False))
    wait(lambda: booth.state == "idle", 3, "idle")
    wait(lambda: booth.screen.calls("slides"), 5, "slideshow of recent videos")
    assert len(booth.screen.calls("slides")[-1][1]) >= 2
    stop.set()


def test_no_speech_twice_is_busy():
    booth, client, stop = make(no_speech=0.6)
    lift_and_say(booth, lambda t: 0.0)
    wait(lambda: booth.state == "busy", 15, "busy")
    assert "Didn't hear" in booth.view["center"] and booth.attempt == 2
    assert "busy" in booth.line.playing()
    assert "fal-ai/wizper" not in client.calls
    stop.set()


def test_hash_ends_listening():
    booth, client, stop = make()
    lift_and_say(booth, lambda t: 0.2)  # talks without ever pausing
    time.sleep(0.3)
    booth.post(("key", "#"))
    wait(lambda: booth.state == "hold", 3, "hold after #")
    stop.set()


def test_pause_does_not_end_listening():
    booth, client, stop = make()
    lift_and_say(booth, lambda t: 0.2 if t < 1 or 3 <= t < 4 else 0.0)  # stops to think for 2 s mid-sentence
    wait(lambda: (r := booth.line.rec) is not None and r["n"] > 4.5 * B.MIC_SR, 5, "past the pause")
    assert booth.state == "listening", booth.state
    booth.post(("key", "#"))
    wait(lambda: booth.state == "hold", 3, "hold after #")
    stop.set()


def test_stalled_speech_to_text_falls_back():
    client = Client()
    client.plan["fal-ai/wizper"] = "hang"
    cfg = B.load_config()
    t = time.monotonic()
    text, endpoint = B.Fal(cfg, client).transcribe(b"RIFF" + bytes(4000))
    took = time.monotonic() - t
    assert endpoint == B.Fal.STT_BACKUP and text == client.text, (text, endpoint)
    assert B.Fal.STT_BACKUP_AFTER <= took < B.Fal.STT_BACKUP_AFTER + 1, took


def test_fallback_to_flux_on_server_error():
    booth, client, stop = make()
    client.plan["minimax/h3-max/text-to-video"] = 500
    lift_and_say(booth, talk())
    wait(lambda: booth.state == "ready", 15, "ready via backup")
    assert booth.take["model"] == "FLUX 3 Max", booth.take
    stop.set()


def test_fallback_on_timeout():
    booth, client, stop = make(render_timeout=0.5)
    client.plan["minimax/h3-max/text-to-video"] = "hang"
    lift_and_say(booth, talk())
    wait(lambda: booth.state == "ready", 15, "ready after timeout")
    assert "cancel" in client.calls and booth.take["model"] == "FLUX 3 Max"
    stop.set()


def test_refusal_is_not_retried():
    booth, client, stop = make()
    client.plan["minimax/h3-max/text-to-video"] = 422
    lift_and_say(booth, talk())
    wait(lambda: booth.state == "busy", 15, "busy after refusal")
    assert "didn't go through" in booth.view["center"]
    assert "blackforestlabs/flux-3-max/text-to-video" not in client.calls
    stop.set()


def test_flux_first_setting():
    booth, client, stop = make(flux_first=True)
    booth.fal = B.Fal(booth.cfg, client=client)
    lift_and_say(booth, talk())
    wait(lambda: booth.state == "ready", 15, "ready")
    assert booth.take["model"] == "FLUX 3 Max" and "minimax/h3-max/text-to-video" not in client.calls
    stop.set()


def test_blocked_prompt():
    booth, client, stop = make()
    client.text = "a naked man"
    lift_and_say(booth, talk())
    wait(lambda: booth.state == "busy", 10, "busy")
    assert "friendly" in booth.view["center"]
    assert not any("text-to-video" in c for c in client.calls)
    stop.set()


def test_hang_up_mid_render_keeps_the_video():
    booth, client, stop = make()
    lift_and_say(booth, talk())
    wait(lambda: booth.state == "hold", 8, "hold")
    booth.post(("hook", False))
    wait(lambda: booth.state == "idle", 3, "idle")
    wait(lambda: booth.cache.recent(), 10, "video kept for the loop")
    wait(lambda: booth.screen.calls("video"), 3, "their video up on the screen")
    assert booth.state == "idle" and booth.screen.calls("video")[-1][1] == URL
    stop.set()


def test_hang_up_while_talking_still_makes_it():
    booth, client, stop = make()
    lift_and_say(booth, lambda t: 0.2)  # still talking when they hang up, # never pressed
    wait(lambda: (r := booth.line.rec) is not None and r["heard"], 3, "speech heard")
    booth.post(("hook", False))
    wait(lambda: booth.screen.calls("video"), 10, "their video up on the screen")
    assert booth.state == "idle" and "minimax/h3-max/text-to-video" in client.calls
    stop.set()


def test_hang_up_before_speaking_makes_nothing():
    booth, client, stop = make()
    lift_and_say(booth, lambda t: 0.0)
    booth.post(("hook", False))
    time.sleep(1.0)
    assert not client.calls, client.calls
    stop.set()


def test_ip_is_never_made():
    booth, client, stop = make()
    client.ip = "Spider-Man"  # a described character the name list misses
    lift_and_say(booth, talk())
    wait(lambda: booth.state == "busy", 10, "busy")
    assert "original ideas" in booth.view["center"]
    assert not any("text-to-video" in c for c in client.calls), client.calls
    stop.set()


def test_ip_name_refused_without_asking():
    booth, client, stop = make()
    client.text = "SpongeBob dancing on the beach"
    lift_and_say(booth, talk())
    wait(lambda: booth.state == "busy", 10, "busy")
    assert "original ideas" in booth.view["center"]
    assert client.calls == ["fal-ai/wizper"], client.calls  # refused on the name alone
    stop.set()


def test_ip_check_down_makes_nothing():
    booth, client, stop = make()
    client.plan[B.Fal.IP_CHECK] = 500
    lift_and_say(booth, talk())
    wait(lambda: booth.state == "busy", 10, "busy")
    assert not any("text-to-video" in c for c in client.calls), client.calls
    stop.set()


def test_off_hook_too_long():
    booth, client, stop = make(off_hook_idle=1.0)
    lift_and_say(booth, talk())
    wait(lambda: booth.state == "ready", 10, "ready")
    wait(lambda: booth.state == "busy", 12, "busy")
    assert "hang up" in booth.view["center"]
    stop.set()


def test_hang_up_during_greeting_is_silent():
    booth, client, stop = make()
    booth.post(("hook", True))
    wait(lambda: booth.state == "greeting", 3)
    booth.post(("hook", False))
    wait(lambda: booth.state == "idle", 3)
    time.sleep(2.0)
    assert booth.state == "idle" and not client.calls and booth.line.rec is None
    stop.set()


def test_slideshow_after_four_plays():
    booth, client, stop = make()
    lift_and_say(booth, talk())
    wait(lambda: booth.state == "ready", 10, "ready")
    booth.cfg["secs"] = 0.3  # 4 plays of a 0.3 s clip; the render itself was asked for 10 s
    booth.post(("file_loaded",))
    wait(lambda: booth.screen.calls("slides"), 25, "slideshow")
    images, secs = booth.screen.calls("slides")[-1][1:3]
    assert len(images) == 3 and secs == 2.5, (len(images), secs)  # 3 stills from the one video so far
    assert all(Path(i).exists() for i in images)
    booth.post(("key", "2"))  # sound on: back to the video, from the start
    wait(lambda: booth.screen.calls("video")[-1][2] is False, 3, "video with sound")
    assert booth.take["sound"] and not booth.take["slides"]
    stop.set()


def test_slides_span_all_videos():
    booth, client, stop = make()
    for n in range(2):
        lift_and_say(booth, talk())
        wait(lambda: booth.state == "ready", 10, "ready")
        wait(lambda: booth.take.get("done") and booth.take["done"].is_set(), 20, "saved with stills")
        booth.post(("hook", False))
        wait(lambda: booth.state == "idle", 3)
    wait(lambda: booth.screen.calls("slides"), 5, "attract slideshow")
    images = booth.screen.calls("slides")[-1][1]
    assert len(images) == 6 and len({Path(i).parent for i in images}) == 2, images
    stop.set()


def test_reset_code_clears_the_feed():
    booth, client, stop = make()
    lift_and_say(booth, talk())
    wait(lambda: booth.state == "ready", 10, "ready")
    wait(lambda: booth.take.get("done") and booth.take["done"].is_set(), 20, "saved with stills")
    booth.post(("hook", False))
    wait(lambda: booth.state == "idle", 3)
    for k in "*123#":  # dialed with the handset on the hook
        booth.post(("key", k))
    wait(lambda: not booth.cache.recent() and not booth.cache.slides(), 3, "feed empty")
    wait(lambda: booth.view.get("center") == "Pick up the phone.", 3, "blank screen")
    assert list((booth.cache.root / "archive").glob("*/*.mp4")), "videos archived, not deleted"
    stop.set()


def test_volume_switch():
    booth, client, stop = make(low_volume=0.4)
    booth.post(("volume", False))
    wait(lambda: booth.line.gain == 0.4, 2, "handset turned down")
    assert booth.screen.calls("volume") == [("volume", 40)]
    booth.post(("volume", False))  # the Pico repeats the position; nothing changes
    booth.post(("volume", True))
    wait(lambda: booth.line.gain == 1.0, 2, "handset back to loud")
    assert booth.screen.calls("volume") == [("volume", 40), ("volume", 100)]
    stop.set()


def test_ringer_rings_only_while_idle():
    booth, client, stop = make(ring_every=1.0)
    booth.post(("ringer", True))
    wait(lambda: "ring" in booth.line.playing(), 6, "first ring, a few seconds after the switch")
    assert booth.ringing and "Incoming call" in booth.screen.last_text
    booth.post(("hook", True))
    wait(lambda: booth.state == "greeting", 3, "picked up")
    assert "ring" not in booth.line.playing() and not booth.ringing
    time.sleep(1.5)
    assert "ring" not in booth.line.playing(), "no ringing during a call"
    booth.post(("hook", False))
    wait(lambda: "ring" in booth.line.playing(), 4, "rings again once idle")
    booth.post(("ringer", False))
    wait(lambda: "ring" not in booth.line.playing() and not booth.ringing, 2, "ringer off stops it")
    time.sleep(2.0)
    assert "ring" not in booth.line.playing(), "stays quiet with the ringer off"
    stop.set()


def test_sounds():
    music = B.elevator_loop()
    assert abs(len(music) / B.SR - 10.0) < 0.01 and 0.05 < np.abs(music).max() <= 1.0
    seam = abs(float(music[-1]) - float(music[0]))
    assert seam < 0.2, seam
    for name in ("greeting", "hold", "menu"):
        x = B.load_wav(Path(B.__file__).parent / "voice" / f"{name}.wav")
        assert len(x) > B.SR and np.abs(x).max() > 0.05, name
    assert B.to_wav(music[:16000], B.SR)[:4] == b"RIFF"


def test_receipt():
    cfg = B.load_config(Path("/nonexistent"))
    take = {"prompt": "a red phone ringing on the moon while a cat in a tiny astronaut suit answers it",
            "model": "H3 Max", "secs": 9.6, "url": URL}
    img = B.receipt_image(take, cfg, str(CLIP))
    assert img.width == B.DOTS and 1200 < img.height < 2800, img.size
    img.save(TMP / "receipt.png")
    data = B.escpos(img)
    assert len(data) > img.height * B.DOTS // 8


def main():
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_")]
    failed = 0
    for name, fn in tests:
        t = time.monotonic()
        try:
            fn()
            print(f"ok    {name}  ({time.monotonic() - t:.1f} s)")
        except Exception:
            failed += 1
            print(f"FAIL  {name}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed   (receipt preview: {TMP / 'receipt.png'})")
    if not failed:
        shutil.rmtree(TMP / "cache", ignore_errors=True)
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
