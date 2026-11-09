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
        self.ip = None  # a word the IP check flags wherever it appears (the remix too); None = all original
        self.remix = "a round pink sea creature in a tiny top hat dancing on the beach"
        self.prompts = []  # what each video model was asked for
        self.models = {}   # language model -> a status code or 'hang', for the IP check race

    def run(self, endpoint, args, timeout=None, start_timeout=None):
        self.calls.append(endpoint)
        assert start_timeout, "without it fal_client retries a timed-out request up to 10 times"
        if endpoint == B.Fal.IP_CHECK:
            if isinstance(self.plan.get(endpoint), int):
                raise HTTPError(self.plan[endpoint])
            what = self.models.get(args["model"])
            if what == "hang":
                time.sleep(30)
            if isinstance(what, int):
                raise HTTPError(what)
            time.sleep(0.05)
            usage = {"cost": 0.0003}
            if args["system_prompt"] != B.IP_CHECK_PROMPT:  # asked to remix it into an original
                return {"output": f'"{self.remix}"', "usage": usage}
            borrowed = self.ip and self.ip in args["prompt"].lower()
            verdict = {"ok": False, "what": self.ip} if borrowed else {"ok": True}
            return {"output": f"```json\n{json.dumps(verdict)}\n```", "usage": usage}  # fenced, as some models do
        assert args["audio_url"].startswith("data:audio/x-wav;base64,")
        assert args.get("language", "en") == "en" and args.get("language_code", "eng") == "eng"
        if self.plan.get(endpoint) == "hang":
            time.sleep(30)
        time.sleep(0.05)
        return {"text": self.text}

    def submit(self, endpoint, args):
        self.calls.append(endpoint)
        self.prompts.append(args["prompt"])
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
B.AUTO_SOUND_AFTER, B.AUTO_PRINT_AFTER = 0.3, 0.3  # the after-video timers, quick for the tests


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
    # speech to text, both IP checkers at once, then the video
    # speech to text, then the video starts alongside both IP checkers (it waits on their verdict to be shown)
    assert client.calls[0] == B.Fal.STT and sorted(client.calls[1:4]) == sorted(
        [B.Fal.IP_CHECK, B.Fal.IP_CHECK, "minimax/h3-max/text-to-video"]), client.calls
    url = booth.screen.calls("video")[-1][1]
    assert url == URL and booth.screen.calls("video")[-1][2] is True  # muted
    wait(lambda: booth.view["scene"] == "ready" and "ANOTHER TAKE" in booth.screen.last_text, 2, "ready screen")
    assert ("frame", B.FRAME) in booth.screen.log
    booth.post(("file_loaded",))
    wait(lambda: "ready" in booth.line.playing(), 6, "Your video is ready. Press 2 to hear it.")
    assert "HEAR IT" in booth.screen.last_text and "PRINT" not in booth.screen.last_text
    booth.post(("key", "2"))
    wait(lambda: "generating_audio" in booth.line.playing(), 1, "the generating-audio cue first")
    assert ("mute", False) not in booth.screen.log and "ADDING SOUND" in booth.screen.last_text
    wait(lambda: ("mute", False) in booth.screen.log and booth.screen.calls("restart_clip"), 3, "sound on")
    assert booth.take["phase"] == "sound" and "HEAR IT" not in booth.screen.last_text, "2 is gone once heard"
    booth.post(("key", "2"))  # does nothing now
    time.sleep(0.3)
    assert booth.take["phase"] == "sound"
    booth.post(("key", "3"))  # another take: the new one starts silent, with 2 again
    wait(lambda: booth.state == "hold", 3, "regenerating")
    assert ("mute", True) in booth.screen.log
    wait(lambda: booth.state == "ready", 8, "second video")
    assert client.calls.count("minimax/h3-max/text-to-video") == 2
    assert booth.take["phase"] == "silent" and "1 LEFT" in booth.screen.last_text
    booth.cfg["secs"] = 1.0  # one play with sound, quickly
    played = []
    real_play = booth.line.play
    booth.line.play = lambda name, *a, **k: (played.append(name), real_play(name, *a, **k))[1]
    booth.post(("file_loaded",))
    # nobody presses anything: the sound comes in on its own, plays once, then "Press 0", then it prints itself
    wait(lambda: booth.take["phase"] == "sound", 8, "the sound on its own")
    wait(lambda: booth.take["phase"] == "prompt" and "print_prompt" in booth.line.playing(), 14, "Press 0 to print")
    assert ("mute", True) == booth.screen.calls("mute")[-1][:2], "quiet again after one play with sound"
    wait(lambda: "printing" in played, 4, "Printing your video now (on its own)")
    wait(lambda: booth.take["phase"] == "done" and "receipt" in played, 25, "Take your receipt")
    assert "TAKE YOUR RECEIPT" in booth.screen.last_text
    data = (TMP / "printer.bin").read_bytes()
    assert data.startswith(b"\x1b@") and data.endswith(b"\x1dV\x42\x00") and b"\x1dv0\x00" in data
    booth.post(("key", "3"))  # one receipt a call: nothing works once it's printed
    time.sleep(0.3)
    assert booth.state == "ready" and booth.take["phase"] == "done"
    booth.post(("hook", False))
    wait(lambda: booth.state == "idle", 3, "idle")
    # hung up: their video stays, with * to start over, and no feed of anyone else's
    wait(lambda: booth.view.get("scene") == "after" and "START OVER" in booth.screen.last_text, 3, "their video stays")
    time.sleep(1.0)  # (attract_after is 0.5 s here: the old feed would have come by now)
    assert booth.view.get("scene") == "after" and not booth.screen.calls("playlist"), booth.screen.log[-3:]
    booth.post(("key", "*"))
    wait(lambda: booth.view.get("scene") == "attract" and booth.view.get("fresh"), 3, "* starts over")
    assert "PICK UP" in booth.screen.last_text and booth.take is None and not booth.screen.calls("playlist")
    stop.set()


def test_no_speech_twice_gets_one_of_ours():
    booth, client, stop = make(no_speech=0.6)
    lift_and_say(booth, lambda t: 0.0)
    wait(lambda: booth.view.get("head") == "Didn't catch that.", 8, "asked again after one silence")
    wait(lambda: booth.state == "hold", 15, "a surprise idea after the second")
    assert booth.view["label"] == "ONE OF OURS" and booth.attempt == 2
    wait(lambda: booth.state == "ready", 10, "ready")
    assert booth.take["surprise"] and booth.take["prompt"] in B.SURPRISE
    assert B.Fal.STT not in client.calls and B.Fal.IP_CHECK not in client.calls  # curated: no speech, no check
    stop.set()


def test_one_is_surprise_me():
    booth, client, stop = make()
    lift_and_say(booth, lambda t: 0.0)
    booth.post(("key", "1"))
    wait(lambda: booth.state == "hold", 3, "on hold with one of ours")
    assert booth.view["said"].strip("“”") in B.SURPRISE and "music" in booth.line.playing()
    wait(lambda: booth.state == "ready", 10, "ready")
    assert client.calls == ["minimax/h3-max/text-to-video"], client.calls
    assert [label for label, _, _ in booth.take["costs"]] == ["VIDEO 10 s"]
    stop.set()


def test_staff_holds_do_nothing_mid_call():
    booth, client, stop = make()
    lift_and_say(booth, talk())
    booth.post(("shutdown",))
    booth.post(("restart",))  # SystemExit if it weren't ignored
    wait(lambda: booth.state == "hold", 8, "the call carries on")
    stop.set()


def test_every_scene_draws():
    booth, client, stop = make()
    booth.take = {"prompt": "a red phone", "you_said": "a phone from a film", "model": "H3 Max", "secs": 9.9,
                  "url": "https://v3b.fal.media/files/b/x/y.mp4", "at": 0}
    for scene, kw in [("attract", {"said": "“a red phone”"}), ("greeting", {}), ("listening", {}),
                      ("hold", {"said": "", "stage": "render", "model": "H3 Max", "done": {}}),
                      ("ready", {"keys": {}}), ("after", {}), ("message", {"head": "Our lines are busy.", "retry": True}),
                      ("staff", {"head": "Restarting…", "sub": ""})]:
        booth.set_view(scene, **kw)
        assert booth.screen.last_text.count("\\pos(") > 1, scene
    assert "v3b.fal.media/files/b/x/y.mp4" in B.share_url(booth.take, booth.cfg)
    assert B.share_url(booth.take, dict(booth.cfg, share_base="")) == booth.take["url"]
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
    client.plan[B.Fal.STT] = "hang"
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
    assert "didn't go through" in booth.view["head"]
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
    assert "friendly" in booth.view["head"]
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


def test_star_cancels_while_listening():
    booth, client, stop = make()
    lift_and_say(booth, lambda t: 0.2)
    time.sleep(0.2)
    booth.line.mic = lambda t: 0.0
    booth.post(("key", "*"))
    wait(lambda: booth.view.get("head") == "Start over.", 3, "cancelled")
    wait(lambda: booth.state == "listening", 5, "listening again after the beep")
    booth.post(("hook", False))  # hanging up straight away makes nothing: they said nothing new
    time.sleep(0.5)
    assert not client.calls, client.calls
    stop.set()


def test_star_cancels_the_render_at_fal():
    booth, client, stop = make()
    client.plan["minimax/h3-max/text-to-video"] = "hang"
    lift_and_say(booth, talk())
    wait(lambda: "minimax/h3-max/text-to-video" in client.calls, 8, "rendering")
    booth.post(("key", "*"))
    wait(lambda: "cancel" in client.calls, 3, "job called off at fal")
    wait(lambda: booth.state == "listening", 5, "listening again after the beep")
    time.sleep(0.5)
    assert booth.state != "ready" and not booth.cache.recent()
    assert "blackforestlabs/flux-3-max/text-to-video" not in client.calls  # not passed on to the backup model
    stop.set()


def test_quiet_caller_who_hangs_up_is_still_tried():
    booth, client, stop = make()
    lift_and_say(booth, lambda t: 0.004)  # under the speech detector
    wait(lambda: (r := booth.line.rec) is not None and r["n"] > 2 * B.MIC_SR, 3, "2 s in")
    booth.post(("hook", False))
    wait(lambda: B.Fal.STT in client.calls, 3, "sent to speech to text")
    stop.set()


def test_ip_is_remixed_into_an_original():
    booth, client, stop = make()
    client.text, client.ip = "a man in a red and blue spider suit swinging between skyscrapers", "spider"
    lift_and_say(booth, talk())  # described, not named: the name list misses it, the model doesn't
    wait(lambda: booth.view.get("stage") == "remix", 5, "on hold, making an original")
    wait(lambda: booth.state == "ready", 10, "ready")
    # described, not named: the video got a head start on their words, called off when the check flagged it; only the
    # original is ever shown
    assert client.prompts == [client.text + B.ORIGINAL_ONLY, client.remix + B.ORIGINAL_ONLY], client.prompts
    assert booth.take["prompt"] == client.remix and booth.take["you_said"] == client.text
    assert booth.take["url"] == booth.screen.calls("video")[-1][1] and len(booth.screen.calls("video")) == 1
    labels = [label for label, _, _ in booth.take["costs"]]
    bill = {label: (model, round(usd, 4)) for label, model, usd in booth.take["costs"]}
    # one line a step, named for the model whose answer was used, costing all it took: both checkers race on each
    # check (their words, then the original), so the check line carries all four calls; GPT-6 Luna makes the original
    assert labels.count("IP CHECK") == 1 and labels.count("MADE ORIGINAL") == 1, labels
    assert bill["IP CHECK"][0] in ("Gemini 3.1 Flash Lite", "GPT-6 Luna") and bill["IP CHECK"][1] == 0.0012, bill
    assert bill["MADE ORIGINAL"] == ("GPT-6 Luna", 0.0003), bill
    img = B.receipt_image(booth.take, booth.cfg, "/nonexistent.mp4")
    assert img.height > 600
    stop.set()


def test_ip_name_is_remixed_too():
    booth, client, stop = make()
    client.text = "SpongeBob dancing on the beach"
    lift_and_say(booth, talk())
    wait(lambda: booth.state == "ready", 10, "ready")
    assert client.prompts == [client.remix + B.ORIGINAL_ONLY], client.prompts
    stop.set()


def test_ip_that_cant_be_remixed_asks_again():
    booth, client, stop = make()
    client.text = "SpongeBob dancing on the beach"
    client.remix = "SpongeBob in a top hat dancing on the beach"  # still borrowed, from both models
    lift_and_say(booth, talk())
    wait(lambda: booth.view.get("head") == "Let's try one of yours.", 10, "asked for another idea")
    assert booth.state in ("greeting", "listening") and booth.state != "busy"
    wait(lambda: booth.state == "listening", 5, "listening again after the beep")
    assert not any("text-to-video" in c for c in client.calls), client.calls
    stop.set()


def test_ip_check_race_one_down():
    client = Client()
    client.ip = "spider"
    client.models["google/gemini-3.1-flash-lite"] = 500
    fal = B.Fal(B.load_config(), client)
    assert fal.check_ip("a man in a spider suit") == "spider"  # GPT-6 Luna decides on its own
    assert fal.check_ip("a cat on the moon") is None


def test_ip_check_hang_gives_up_in_time():
    client = Client()
    for m in B.Fal.IP_CHECK_MODELS:
        client.models[m] = "hang"
    t = time.monotonic()
    try:
        B.Fal(B.load_config(), client).check_ip("a cat on the moon")
        raise AssertionError("a check that never answered let the prompt through")
    except TimeoutError:
        pass
    assert time.monotonic() - t < B.Fal.IP_CHECK_DEADLINE + 0.5


def test_ip_check_down_makes_nothing():
    booth, client, stop = make()
    client.plan[B.Fal.IP_CHECK] = 500
    lift_and_say(booth, talk())
    wait(lambda: booth.state == "busy", 10, "busy")
    # the video's head start is called off when no checker answers: nothing is shown, kept or printed
    time.sleep(1.5)
    assert booth.state == "busy" and booth.take is None and not booth.screen.calls("video"), booth.screen.log
    stop.set()


def test_off_hook_too_long():
    booth, client, stop = make(off_hook_idle=1.0)
    lift_and_say(booth, talk())
    wait(lambda: booth.state == "ready", 10, "ready")
    wait(lambda: booth.state == "busy", 12, "busy")
    assert "hang up" in booth.view["sub"].lower()
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
    booth.cfg["attract_after"] = 10  # their clip stays up long enough to turn into stills
    booth.post(("file_loaded",))
    time.sleep(2)
    assert not booth.screen.calls("slides"), "never other callers' stills under this caller's video"
    booth.post(("hook", False))  # once they've hung up, it can turn into stills
    booth.post(("file_loaded",))
    wait(lambda: booth.screen.calls("slides"), 25, "slideshow")
    images, secs = booth.screen.calls("slides")[-1][1:3]
    assert len(images) == 3 and secs == 2.5, (len(images), secs)  # 3 stills from the one video so far
    assert all(Path(i).exists() for i in images)
    stop.set()


def test_idle_screen_shuffles_earlier_videos():
    booth, client, stop = make()
    for n in range(2):
        lift_and_say(booth, talk())
        wait(lambda: booth.state == "ready", 10, "ready")
        wait(lambda: booth.take.get("done") and booth.take["done"].is_set(), 20, "saved with stills")
        booth.post(("hook", False))
        wait(lambda: booth.state == "idle", 3)
    booth.post(("attract", booth.cid))  # the feed of earlier videos, when it's shown (the booth starting up)
    wait(lambda: booth.screen.calls("playlist"), 5, "idle shuffle")
    files = booth.screen.calls("playlist")[-1][1]
    assert set(files) == {f for f, _ in booth.cache.recent()} and len(set(files)) == 2, files
    assert all(a != b for a, b in zip(files, files[1:] + files[:1])), "the same video twice in a row"
    assert [p for _, p in booth.attract] == [dict(booth.cache.recent())[f] for f in files], "prompts match"
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
    wait(lambda: booth.view.get("scene") == "attract" and not booth.attract, 3, "blank screen")
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
    assert booth.ringing and "PICK UP." in booth.screen.last_text
    booth.post(("hook", True))
    wait(lambda: booth.state == "greeting", 3, "picked up")
    assert "ring" not in booth.line.playing() and not booth.ringing
    time.sleep(1.5)
    assert "ring" not in booth.line.playing(), "no ringing during a call"
    booth.post(("hook", False))
    wait(lambda: booth.state == "idle", 3)
    if booth.lingering:  # their call still on the screen: no ringing over it until * clears it
        time.sleep(1.5)
        assert "ring" not in booth.line.playing()
        booth.post(("key", "*"))
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


def test_sticker():
    import sticker
    cfg = B.load_config(Path("/nonexistent"))
    costs = [("VIDEO", "H3 Max · 10 s", 0.56), ("WORDS", "Scribe v2 · 6 s", 0.0008)]
    long = " ".join(["a skateboarder jumping a burning ramp at sunset in downtown Miami"] * 4)
    for take in ({"prompt": "orchid", "model": "H3 Max", "secs": 9.6, "url": URL, "costs": costs},
                 {"prompt": long, "you_said": long, "model": "H3 Max", "secs": 9.6, "url": URL, "costs": costs}):
        img = sticker.sticker_image(take, cfg, str(CLIP), B)
        assert img.width == sticker.W and 1400 < img.height < 2800, img.size  # as long as it needs, never a scroll
        narrow = sticker.sticker_image(take, cfg, str(CLIP), B, width=B.DOTS)  # the receipt printer's
        assert narrow.width == B.DOTS and len(B.escpos(narrow)) > narrow.height * B.DOTS // 8
    img.save(TMP / "sticker.png")
    h = img.height
    data = sticker.zpl(img)
    assert data.startswith(b"^XA") and data.endswith(b"^XZ") and f"^LL{h}".encode() in data
    data = sticker.epl(img)
    assert data.startswith(b"\r\nN\r\n") and data.endswith(b"P1\r\n")
    assert b"Q%d,%d\r\n" % (h, sticker.LABEL_GAP) in data
    assert len(data) > h * sticker.W // 8
    assert not sticker.speaks_zpl("/dev/usb/lp9") and not sticker.in_epl("/dev/usb/lp9")


def test_label_3x6():
    """The receipt on one 3x6 label: exactly a label long whatever they said, one IP CHECK row however many checks
    ran, and ZPL that senses the gap and places the art on the head."""
    import sticker
    cfg = B.load_config(Path("/nonexistent"))
    assert cfg["zebra_paper"] == "label" and cfg["label_x"] == 131
    costs = [("SPEECH TO TEXT", "ElevenLabs Scribe v2", 0.0025), ("IP CHECK", "Gemini 3.1 Flash Lite", 0.0001),
             ("IP CHECK", "GPT-6 Luna", 0.0), ("MADE ORIGINAL", "Gemini 3.1 Flash Lite", 0.0002),
             ("VIDEO 10 s", "H3 Max", 0.56)]
    assert [s[0] for s in sticker.steps(costs)] == ["SPEECH TO TEXT", "IP CHECK", "MADE ORIGINAL", "VIDEO 10 s"]
    assert sticker.steps(costs)[1][1] == ["Gemini 3.1 Flash Lite", "GPT-6 Luna"]
    long = " ".join(["a skateboarder jumping a burning ramp at sunset in downtown Miami"] * 4)
    for take in ({"prompt": "orchid", "secs": 9.6, "url": URL, "costs": costs[:1] + costs[-1:]},
                 {"prompt": long, "you_said": long, "secs": 9.6, "url": URL, "costs": costs}):
        img = sticker.label_3x6(take, cfg, str(CLIP), B)
        assert img.size == (sticker.LABEL_W, sticker.LABEL_H), img.size
        assert sticker.label_image(take, cfg, str(CLIP), B, 180, 2).height <= sticker.LABEL_SAFE
    img.save(TMP / "label3x6.png")
    data = sticker.label_zpl(img, cfg["label_x"])
    assert data.startswith(b"^XA^MNY") and b"^PW832" in data and b"^LH131,0" in data and data.endswith(b"^XZ")


def test_listening_is_quiet_and_says_so():
    """No "mm-hm" in the earpiece, however long they talk: the screen says LISTENING and its meter moves instead."""
    booth, client, stop = make(silence=2.0, listen_max=40.0)
    played = []
    real = booth.line.play
    booth.line.play = lambda name, *a, **k: (played.append(name), real(name, *a, **k))[1]
    lift_and_say(booth, talk(secs=12))
    wait(lambda: booth.state == "listening", 5, "listening")
    assert "LISTENING" in booth.screen.last_text
    wait(lambda: booth.state == "hold", 20, "hold after the long prompt")
    assert not [n for n in played if n.startswith("mmhm")], played
    assert "mmhm0" not in booth.line.sounds
    stop.set()


def test_hang_up_before_an_ip_swap_still_makes_one():
    """They asked for someone else's IP, it couldn't be made original, and they'd already hung up: nobody's there
    to ask for another idea, so the booth makes one of its own and puts it up on the screen."""
    booth, client, stop = make()
    client.text = "SpongeBob dancing on the beach"
    client.remix = "SpongeBob in a top hat dancing on the beach"  # still borrowed, from both models
    lift_and_say(booth, talk())
    wait(lambda: booth.state == "hold", 8, "hold")
    booth.post(("hook", False))
    wait(lambda: booth.state == "idle", 3, "idle")
    wait(lambda: booth.screen.calls("video"), 15, "a video up on the screen anyway")
    assert any("text-to-video" in c for c in client.calls), client.calls
    assert booth.last_take and booth.last_take.get("surprise"), booth.last_take
    stop.set()


def test_hang_up_on_hold_keeps_their_call_on_screen():
    """Hung up while it renders: the hold screen stays, clock running, then their video comes up and stays (no feed
    of other videos) until * on the hook or the next pick-up."""
    booth, client, stop = make()
    lift_and_say(booth, talk())
    wait(lambda: booth.state == "hold", 8, "on hold")
    booth.post(("hook", False))
    wait(lambda: booth.lingering, 2, "still their call")
    assert booth.view.get("scene") == "hold" and not booth.screen.calls("playlist")
    wait(lambda: booth.view.get("scene") == "after" and booth.take, 10, "their video comes up")
    time.sleep(1.0)
    assert booth.view.get("scene") == "after" and not booth.screen.calls("playlist")
    booth.post(("hook", True))  # the next caller
    wait(lambda: booth.state == "greeting", 3, "a new call")
    assert not booth.lingering
    stop.set()


def test_hang_up_before_printing_still_prints():
    """They saw their video and hung up without pressing 0: the receipt prints anyway."""
    booth, client, stop = make()
    lift_and_say(booth, talk())
    wait(lambda: booth.state == "ready", 10, "ready")
    (TMP / "printer.bin").write_bytes(b"")
    booth.post(("hook", False))
    wait(lambda: (TMP / "printer.bin").stat().st_size > 1000, 25, "printed after the hang-up")
    assert booth.take and booth.take["printed"]
    stop.set()


def test_zero_before_a_prompt_reprints_the_last_video():
    """0 before saying anything prints the line's last video again: nothing new is made, and the call ends."""
    booth, client, stop = make()
    booth.post(("hook", True))
    wait(lambda: booth.state == "greeting", 3, "greeting")
    booth.post(("key", "0"))  # nothing made yet on this line: 0 does nothing
    time.sleep(0.3)
    assert booth.state in ("greeting", "listening")
    booth.post(("hook", False))
    wait(lambda: booth.state == "idle", 3, "idle")

    lift_and_say(booth, talk())
    wait(lambda: booth.state == "ready", 10, "ready")
    first = booth.take
    (TMP / "printer.bin").write_bytes(b"")
    booth.post(("hook", False))
    wait(lambda: (TMP / "printer.bin").stat().st_size > 1000, 25, "first receipt")
    wait(lambda: booth.state == "idle", 5, "idle")
    calls = len(client.calls)

    (TMP / "printer.bin").write_bytes(b"")
    done_busy, B.DONE_BUSY_AFTER = B.DONE_BUSY_AFTER, 0.3
    try:
        booth.post(("hook", True))
        wait(lambda: booth.state == "greeting", 3, "greeting")
        booth.post(("key", "0"))
        wait(lambda: booth.state == "reprint", 3, "reprinting")
        wait(lambda: (TMP / "printer.bin").stat().st_size > 1000, 25, "the reprint")
        wait(lambda: booth.state == "busy", 5, "the call ends after the reprint")
    finally:
        B.DONE_BUSY_AFTER = done_busy
    assert booth.last_take is first and len(client.calls) == calls  # nothing new was made
    booth.post(("hook", False))
    wait(lambda: booth.state == "idle", 3, "idle")

    booth.last_take = None  # as after a restart: the last video comes back from disk
    (TMP / "printer.bin").write_bytes(b"")
    booth.post(("hook", True))
    wait(lambda: booth.state == "greeting", 3, "greeting")
    booth.post(("key", "0"))
    wait(lambda: booth.state == "reprint", 3, "reprinting after a restart")
    wait(lambda: (TMP / "printer.bin").stat().st_size > 1000, 25, "the reprint after a restart")
    assert booth.last_take["prompt"] == first["prompt"] and len(client.calls) == calls
    assert booth.last_take.get("call") and booth.last_take.get("costs"), "the disk copy keeps the call number and bill"
    booth.post(("hook", False))
    stop.set()


def test_swears_are_masked():
    """Swearing and slurs are starred before anything shows them; ordinary words that contain them are left alone."""
    assert B.mask_swears("Oh, shit. A fucking huge dog") == "Oh, s***. A f****** huge dog"
    for fine in ("a cocktail party", "Dickens in Scunthorpe", "a classic glass", "a shiitake farm"):
        assert B.mask_swears(fine) == fine, fine


def test_dashboard_controls():
    """The staff dashboard's socket: status, out of order (a lift gets the busy note), back open, the model order,
    and hiding a video from the feed."""
    import socket as so
    booth, client, stop = make()
    sock = str(TMP / f"ctl{time.monotonic_ns()}.sock")
    B.serve_control(booth, sock)

    def ask(cmd, **args):
        with so.socket(so.AF_UNIX, so.SOCK_STREAM) as s:
            s.settimeout(6)
            s.connect(sock)
            s.sendall((json.dumps({"cmd": cmd, "args": args}) + "\n").encode())
            return json.loads(s.makefile().readline())

    st = ask("status")["status"]
    assert st["state"] == "idle" and st["paused"] is None and st["first_model"] == "H3 Max", st
    assert ask("pause", note="Please try Line 2")["ok"]
    assert "OUT OF ORDER" in booth.screen.last_text and "PLEASE TRY LINE 2" in booth.screen.last_text
    booth.post(("hook", True))
    wait(lambda: booth.state == "busy" and booth.view.get("head") == "Line out of order.", 3, "out-of-order call")
    assert "busy" in booth.line.playing() and booth.stats["calls"] == 0
    booth.post(("hook", False))
    wait(lambda: booth.state == "idle", 3, "idle")
    assert ask("resume")["ok"] and booth.paused is None and "OUT OF ORDER" not in booth.screen.last_text
    assert ask("model", first="flux")["first_model"] == "FLUX 3 Max"
    assert ask("model", first="h3")["first_model"] == "H3 Max"
    assert not ask("reprint")["ok"]  # nothing made yet
    (booth.cache.root / "a.mp4").write_bytes(b"x")
    booth.cache.items = [{"file": "a.mp4", "prompt": "a red phone", "stills": []}]
    assert ask("feed")["feed"][0]["prompt"] == "a red phone"
    assert ask("hide", file="a.mp4")["ok"] and booth.cache.items == []
    assert (booth.cache.root / "archive" / "hidden" / "a.mp4").exists()
    assert not ask("nonsense")["ok"]
    # the kill switch mid-call: the render is called off, nothing is shown or kept, the caller is asked to hang up
    lift_and_say(booth, talk())
    wait(lambda: booth.state == "hold", 8, "on hold")
    assert ask("pull")["what"] == "Call ended"
    assert booth.state == "busy" and booth.view["head"] == "Thanks for calling." and booth.pending is None
    time.sleep(3)
    assert booth.state == "busy" and booth.take is None, "the video never comes up"
    assert ask("status")["status"]["stats"]["pulled"] == 1
    stop.set()


def test_feed_keeps_the_link_for_favorites():
    """Each saved video keeps fal's link, the receipt's share link and the model, so staff can star it in the
    dashboard and push it to the event page."""
    booth, client, stop = make()
    lift_and_say(booth, talk())
    wait(lambda: booth.cache.items, 20, "the video saved for the screen")
    it = booth.cache.listing()[0]
    assert it["url"] and it["url"] == booth.last_take["url"], it
    assert it["share"] == B.share_url(booth.last_take, booth.cfg) and it["model"] and it["at"] > 0, it
    stop.set()


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
