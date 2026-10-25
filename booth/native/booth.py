#!/usr/bin/env python3
"""fal call center booth, native: a real phone in, a video on the screen, no browser.

Lift the handset -> greeting and beep -> say a prompt -> "please hold" and elevator music while fal transcribes it
and renders the video -> the video plays muted on the screen -> the menu: 2 sound on/off, 3 regenerate, 0 print.

The pieces, each kept simple so it can't take the others down:
  Phone   the Pico, read straight from the kernel (evdev), so no window ever has to have focus
  Line    the handset: one always-open output stream that mixes voice lines, music and tones, and an always-open mic
  Screen  mpv full screen, driven over its IPC socket: videos stream from fal's URL, text is drawn as an OSD overlay
  Fal     fal_client: Wizper for the words, H3 Max (or FLUX 3 Max) for the video, the other one as the backup
  Printer ESC/POS straight to /dev/usb/lp0: no CUPS, no driver
Everything reports to one queue and one thread runs the call (Booth.run), so there are no races between a hang-up
and a video arriving: each call has a number, and anything from an older call is ignored.

Run by run.sh, which restarts it if it ever exits. Settings: native.conf next to this file. fal key: fal.key.
"""
import base64
import glob
import heapq
import io
import itertools
import json
import logging
import os
import queue
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import wave
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
log = logging.getLogger("booth")


# ---------- settings ----------
def load_config(path=HERE / "native.conf"):
    cfg = {"LINE": "1", "SECS": "10", "MODEL": "h3", "LANGUAGE": "en", "PRINTER": "/dev/usb/lp0", "GRAB": "1",
           "OUT_DEVICE": "", "IN_DEVICE": ""}
    if path.exists():
        for raw in path.read_text().splitlines():
            line = raw.split("#", 1)[0].strip()
            if "=" in line:
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip().strip('"')
    cfg.update({k[6:]: v for k, v in os.environ.items() if k.startswith("BOOTH_")})  # BOOTH_SECS=8 etc. for testing
    return {
        "line": cfg["LINE"],
        "secs": min(15, max(5, int(cfg["SECS"]))),
        "flux_first": cfg["MODEL"].lower() == "flux",
        "language": cfg["LANGUAGE"],
        "printer": cfg["PRINTER"],
        "grab": cfg["GRAB"] != "0",
        # Sound devices by (part of) their name; empty = the system default (on the Pi, run.sh points that at the
        # USB adapter). E.g. on a laptop: OUT_DEVICE="External Headphones", IN_DEVICE="USB Audio".
        "out_device": cfg["OUT_DEVICE"] or None, "in_device": cfg["IN_DEVICE"] or None,
        "listen_max": 8.0, "no_speech": 6.0, "silence": 0.9,   # seconds: longest prompt, give up, end of speech
        "render_timeout": 45.0,                                  # per model, then the next model
        "menu_after": 3.0,                                       # seconds into the clip before the menu is read
        "off_hook_idle": 60.0,                                   # handset left off after the menu -> busy signal
        "history": 12,                                           # recent videos kept for the screen between calls
        "attract_after": 20.0,                                   # after a hang-up, their clip stays up this long
    }


def fal_key():
    key = os.environ.get("FAL_KEY") or ""
    f = HERE / "fal.key"
    if not key and f.exists():
        key = f.read_text().strip()
    return key


# Prompts that never reach the model. The models' own safety checkers are the real filter; this just keeps the
# obvious ones from costing a render.
# What Whisper-style models "hear" in near-silence. Treated as nothing said.
NOT_SPEECH = {"", "you", "thank you", "thanks", "thanks for watching", "thank you for watching", "bye", "okay", "so"}


def heard_nothing(prompt):
    return re.sub(r"[^a-z ]", "", prompt.lower()).strip() in NOT_SPEECH


BLOCKED = re.compile(r"\b(nude|naked|nudity|porn\w*|sex\w*|nsfw|genital\w*|gore|gory|behead\w*|suicide|nazi\w*|hitler"
                     r"|rape\w*|terroris\w*)\b", re.I)


# ---------- fal ----------
class Refused(Exception):
    """fal said no to the request itself (bad input, safety checker): the backup model would say no too."""


class Fal:
    STT = "fal-ai/wizper"

    def __init__(self, cfg, client=None):
        self.cfg = cfg
        if client is None:
            import fal_client
            client = fal_client.SyncClient(key=fal_key(), default_timeout=20)
        self.client = client
        secs = cfg["secs"]
        self.models = [
            ("H3 Max", "minimax/h3-max/text-to-video",
             lambda p: {"prompt": p, "duration": secs, "resolution": "768P", "aspect_ratio": "16:9",
                        "prompt_expansion_mode": "balanced", "enable_safety_checker": True}),
            ("FLUX 3 Max", "blackforestlabs/flux-3-max/text-to-video",
             lambda p: {"prompt": p, "duration": secs, "resolution": "720p", "aspect_ratio": "16:9",
                        "generate_audio": True, "reasoning_level": "low"}),
        ]
        if cfg["flux_first"]:
            self.models.reverse()

    def warm_up(self):
        """Open the connection to fal while the greeting plays, so the real requests skip DNS and TLS."""
        try:
            self.client._client.head("https://queue.fal.run", timeout=3)
        except Exception:
            pass

    def transcribe(self, wav_bytes):
        # The recording rides along inline (a data URI): no upload step, one request, one retry.
        args = {"audio_url": "data:audio/x-wav;base64," + base64.b64encode(wav_bytes).decode(), "task": "transcribe"}
        if self.cfg["language"] != "auto":
            args["language"] = self.cfg["language"]
        for attempt in range(2):
            try:
                return (self.client.run(self.STT, args, timeout=15).get("text") or "").strip()
            except Exception as e:
                log.warning("transcribe failed: %s", e)
                if attempt:
                    raise

    def render(self, prompt):
        """Each model gets one go, in order. Returns (url, model name)."""
        last = None
        for name, endpoint, build in self.models:
            try:
                return self._one(endpoint, build(prompt)), name
            except Refused:
                raise
            except Exception as e:
                log.warning("%s failed: %s", name, e)
                last = e
        raise last or RuntimeError("no models")

    def _one(self, endpoint, args):
        try:
            handle = self.client.submit(endpoint, args)
            deadline = time.monotonic() + self.cfg["render_timeout"]
            while True:
                s = handle.status()
                if type(s).__name__ == "Completed":
                    break
                if time.monotonic() > deadline:
                    try:
                        handle.cancel()
                    except Exception:
                        pass
                    raise TimeoutError(f"{endpoint} took over {self.cfg['render_timeout']:.0f} s")
                time.sleep(0.25)
            data = handle.get()
        except Exception as e:
            code = getattr(e, "status_code", None)
            if code and 400 <= code < 500 and code not in (408, 429):
                raise Refused(str(e)) from e
            raise
        url = (data.get("video") or {}).get("url")
        if not url:
            raise RuntimeError("no video in the result")
        return url


# ---------- sound on the line ----------
SR = 48000      # output
MIC_SR = 16000  # input: what the speech model wants anyway


def tone(freqs, secs, peak=0.12):
    t = np.arange(int(SR * secs)) / SR
    x = sum(np.sin(2 * np.pi * f * t) for f in freqs) * peak
    edge = min(len(t) // 2, int(0.005 * SR))
    ramp = np.linspace(0, 1, edge)
    x[:edge] *= ramp
    x[-edge:] *= ramp[::-1]
    return x.astype(np.float32)


def silence(secs):
    return np.zeros(int(SR * secs), np.float32)


def phone_band(x):
    """300-3400 Hz, like a real phone line, so everything on the line sounds like it came down one."""
    spec = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / SR)
    spec[(f < 300) | (f > 3400)] = 0
    return np.fft.irfft(spec, len(x)).astype(np.float32)


def load_wav(path):
    with wave.open(str(path)) as w:
        rate, ch, n = w.getframerate(), w.getnchannels(), w.getnframes()
        x = np.frombuffer(w.readframes(n), np.int16).astype(np.float32) / 32768
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    if rate != SR:
        x = np.interp(np.arange(int(len(x) * SR / rate)) * rate / SR, np.arange(len(x)), x)
    return x.astype(np.float32)


def elevator_loop():
    """The same original four-bar tune as the web version (Cmaj7 - Am7 - Dm7 - G7, 96 BPM), rendered once as a
    seamless 10-second loop."""
    bpm, chords = 96, [[60, 64, 67, 71], [57, 60, 64, 67], [62, 65, 69, 72], [55, 59, 62, 65]]
    arp, melody = [0, 1, 2, 3, 2, 1, 2, 1], [[76, 74, 72, 71], [72, 76, 79, 76], [77, 76, 74, 72], [71, 74, 77, 74]]
    eighth = 60 / bpm / 2
    n = int(round(32 * eighth * SR))
    out = np.zeros(n, np.float32)
    hz = lambda m: 440 * 2 ** ((m - 69) / 12)
    waves = {
        "sine": lambda ph: np.sin(ph),
        "triangle": lambda ph: 2 / np.pi * np.arcsin(np.sin(ph)),
        "square": lambda ph: np.sign(np.sin(ph)),
    }

    def note(midi, at, dur, kind, peak):
        m = int((dur + 0.05) * SR)
        t = np.arange(m) / SR
        env = np.where(t < 0.01, t / 0.01 * peak, peak * np.exp(np.log(0.0001 / peak) * (t - 0.01) / max(dur - 0.01, 1e-3)))
        env[t > dur] = 0
        x = waves[kind](2 * np.pi * hz(midi) * t) * env
        idx = (int(at * SR) + np.arange(m)) % n  # wraps round, so the loop has no seam
        np.add.at(out, idx, x.astype(np.float32))

    for step in range(32):
        bar, beat, at = step // 8, step % 8, step * eighth
        chord = chords[bar]
        note(chord[arp[beat]] + 12, at, eighth * 1.6, "sine", 0.2)
        note(chord[arp[beat]] + 24, at, eighth * 0.8, "triangle", 0.03)
        if beat % 4 == 0:
            note(chord[0], at, eighth * 3.5, "square", 0.05)
        if beat % 2 == 0:
            note(melody[bar][beat // 2], at, eighth * 1.8, "triangle", 0.16)
    return phone_band(out * 0.85)


DTMF = {k: (r, c) for r, row in zip([697, 770, 852, 941], ["123", "456", "789", "*0#"])
        for c, k in zip([1209, 1336, 1477], row)}


class Clip:
    def __init__(self, data, tag=None, loop=False, delay=0.0, fade_in=0.0):
        self.data, self.tag, self.loop = data, tag, loop
        self.pos = -int(delay * SR)  # negative = still waiting to start
        self.fade_in = int(fade_in * SR)
        self.fade_out = None  # samples left in a fade to silence

    def read(self, n):
        out = np.zeros(n, np.float32)
        i = 0
        while i < n:
            if self.pos < 0:
                k = min(n - i, -self.pos)
                i += k
                self.pos += k
                continue
            if self.pos >= len(self.data):
                if not self.loop:
                    return out, True
                self.pos = 0
            k = min(n - i, len(self.data) - self.pos)
            out[i:i + k] = self.data[self.pos:self.pos + k]
            if self.fade_in and self.pos < self.fade_in:
                out[i:i + k] *= np.minimum(1, (self.pos + np.arange(k)) / self.fade_in)
            self.pos += k
            i += k
        if self.fade_out is not None:
            left = self.fade_out
            out *= np.clip((left - np.arange(n)) / (0.12 * SR), 0, 1)
            self.fade_out = left - n
            if self.fade_out <= 0:
                return out, True
        return out, False


class Line:
    """The handset. Both streams stay open all day (no device wait on lift); if the adapter is unplugged they are
    reopened every two seconds until it's back."""

    def __init__(self, post, cfg, voice_dir=HERE / "voice"):
        self.post, self.cfg = post, cfg
        self.lock = threading.Lock()
        self.clips = []
        self.rec = None
        v = {name: phone_band(load_wav(voice_dir / f"{name}.wav")) for name in ("greeting", "hold", "menu")}
        click = (np.random.default_rng(1).uniform(-1, 1, int(0.03 * SR)) * 0.3
                 * np.exp(-np.arange(int(0.03 * SR)) / (0.005 * SR))).astype(np.float32)
        self.sounds = {
            # one ring, the click of someone picking up, the greeting, then the "leave your message" beep
            "greeting": np.concatenate([tone([440, 480], 0.7), silence(0.1), click, silence(0.15), v["greeting"],
                                        silence(0.25), tone([1000], 0.4)]),
            "hold": v["hold"],
            "menu": v["menu"],
            "busy": np.concatenate([np.concatenate([tone([480, 620], 0.25), silence(0.25)])] * 12),
            "music": elevator_loop(),
        }
        self.out = self.inp = None
        threading.Thread(target=self._keep_open, daemon=True).start()

    # ----- streams -----
    @staticmethod
    def _device(sd, name, kind):
        if not name:
            return None
        for i, d in enumerate(sd.query_devices()):
            if name.lower() in d["name"].lower() and d[f"max_{kind}_channels"] > 0:
                return i
        raise RuntimeError(f"no {kind} device named like {name!r}")

    def _keep_open(self):
        import sounddevice as sd
        while True:
            try:
                if not (self.out and self.out.active):
                    self.out = sd.OutputStream(samplerate=SR, channels=1, dtype="float32", latency="low",
                                               device=self._device(sd, self.cfg["out_device"], "output"),
                                               callback=self._play)
                    self.out.start()
                if not (self.inp and self.inp.active):
                    self.inp = sd.InputStream(samplerate=MIC_SR, channels=1, dtype="float32", blocksize=MIC_SR // 20,
                                              device=self._device(sd, self.cfg["in_device"], "input"),
                                              callback=self._listen)
                    self.inp.start()
            except Exception as e:
                log.warning("handset audio not ready: %s", e)
                for s in (self.out, self.inp):
                    try:
                        s and s.close()
                    except Exception:
                        pass
                self.out = self.inp = None
                try:  # forget the old device list, so an adapter plugged in since shows up
                    sd._terminate()
                    sd._initialize()
                except Exception:
                    pass
            time.sleep(2)

    def _play(self, outdata, frames, _time, _status):
        mix = np.zeros(frames, np.float32)
        with self.lock:
            for c in list(self.clips):
                chunk, done = c.read(frames)
                mix += chunk
                if done:
                    self.clips.remove(c)
                    if c.tag:
                        self.post(("clip_done", c.tag))
        outdata[:, 0] = np.clip(mix, -1, 1)

    # ----- what plays -----
    def play(self, name, tag=None, **kw):
        """Returns how long it lasts, so the caller can carry on even if the adapter is unplugged mid-call."""
        with self.lock:
            self.clips.append(Clip(self.sounds[name], tag, **kw))
        return len(self.sounds[name]) / SR

    def key(self, digit):
        if digit in DTMF:
            with self.lock:
                self.clips.append(Clip(phone_band(tone(DTMF[digit], 0.15))))

    def hold(self):
        """The hold line, then the music fades in under it and loops until stop()."""
        self.stop()
        self.play("hold")
        self.play("music", loop=True, delay=len(self.sounds["hold"]) / SR + 0.3, fade_in=0.6)

    def stop(self, fade=True):
        with self.lock:
            if fade:
                for c in self.clips:
                    c.tag = None  # a cut-off clip didn't finish
                    if c.fade_out is None:
                        c.fade_out = int(0.12 * SR)
            else:
                self.clips.clear()

    # ----- listening -----
    def record(self, cid):
        with self.lock:
            # times are counted in samples, so a late callback can't cut someone off
            self.rec = {"cid": cid, "chunks": [], "n": 0, "floor": 0.01, "heard": False, "last_loud": None}

    def stop_recording(self):
        """# pressed: whatever was said so far is the prompt."""
        with self.lock:
            r = self.rec
            if r:
                r["heard"] = r["heard"] or r["n"] > 0.6 * MIC_SR
                self._finish()

    def cancel_recording(self):
        with self.lock:
            self.rec = None

    def _listen(self, indata, frames, _time, _status):
        with self.lock:
            r = self.rec
            if not r:
                return
            x = indata[:, 0].copy()
            r["chunks"].append(x)
            r["n"] += len(x)
            rms, now = float(np.sqrt(np.mean(x * x))), r["n"] / MIC_SR
            # Noise floor: the quietest moments, creeping up slowly so a noisy hall adapts. The mic is at the mouth,
            # so speech stands well clear of the crowd.
            if rms > 1e-4:
                r["floor"] = min(rms, r["floor"] * 1.005)
            if rms > max(0.015, r["floor"] * 3):
                r["heard"], r["last_loud"] = True, now
            c = self.cfg
            if ((r["heard"] and now - r["last_loud"] > c["silence"]) or (not r["heard"] and now > c["no_speech"])
                    or now > c["listen_max"]):
                self._finish()

    def _finish(self):  # with self.lock held
        r, self.rec = self.rec, None
        audio = np.concatenate(r["chunks"]) if r["chunks"] else np.zeros(1, np.float32)
        # Handset mics are quiet; bring the loudest moment up to 90% (at most 30x) so the speech model hears words.
        peak = float(np.abs(audio).max())
        if peak > 0:
            audio = audio * min(30.0, 0.9 / peak)
        since = r["n"] / MIC_SR - (r["last_loud"] or r["n"] / MIC_SR)  # seconds since they stopped talking
        self.post(("heard", r["cid"], {"wav": to_wav(audio, MIC_SR), "heard": r["heard"],
                                       "speech_end": time.monotonic() - since}))


def to_wav(x, rate):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())
    return buf.getvalue()


# ---------- the screen ----------
def ass_text(s):
    return re.sub(r"[{}\\\n\r]", " ", s)


class Screen:
    """mpv full screen. Nothing on it is a window anyone can click: videos and the background image are mpv's
    playlist, and every word on screen is one OSD overlay redrawn as a whole."""

    SOCK = "/tmp/fal-booth-mpv.sock"

    def __init__(self, post, extra_args=()):
        self.post = post
        self.extra = list(extra_args)
        self.sock = None
        self.lock = threading.RLock()
        self.bg = HERE / "cache" / "bg.png"
        make_background(self.bg)
        self._start()

    def _start(self):
        try:
            os.unlink(self.SOCK)
        except FileNotFoundError:
            pass
        self.proc = subprocess.Popen([
            "mpv", "--idle=yes", "--force-window=yes", "--fullscreen", "--no-input-terminal", "--msg-level=all=error", "--no-osc", "--no-osd-bar",
            "--osd-level=0", "--no-input-default-bindings", "--input-vo-keyboard=no", "--cursor-autohide=always",
            "--keep-open=yes", "--image-display-duration=inf", "--loop-file=inf", "--mute=yes", "--volume=100",
            "--ytdl=no", "--hwdec=auto-safe", "--cache=yes", "--demuxer-max-bytes=64MiB",
            "--osd-font=DejaVu Sans",
            "--audio-display=no", "--input-ipc-server=" + self.SOCK, *self.extra])
        for _ in range(100):
            try:
                s = socket.socket(socket.AF_UNIX)
                s.connect(self.SOCK)
                self.sock = s
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("mpv didn't start")
        threading.Thread(target=self._events, args=(self.sock,), daemon=True).start()
        self._send({"command": ["observe_property", 1, "playlist-pos"]})

    def _events(self, s):
        buf = b""
        while True:
            try:
                data = s.recv(65536)
            except OSError:
                data = b""
            if not data:
                return
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                ev = msg.get("event")
                if ev == "property-change" and msg.get("name") == "playlist-pos":
                    self.post(("playlist_pos", msg.get("data")))
                elif ev == "file-loaded":
                    self.post(("file_loaded",))
                elif ev == "end-file" and msg.get("reason") == "error":
                    self.post(("video_error",))

    def _send(self, msg):
        data = (json.dumps(msg) + "\n").encode()
        with self.lock:
            try:
                self.sock.sendall(data)
                return
            except OSError as e:
                log.error("mpv went away (%s); restarting it", e)
            try:
                self.proc.kill()
                self.proc.wait(5)
            except Exception:
                pass
            self._start()
        self.post(("screen_restarted",))

    def close(self):
        try:
            self.proc.terminate()
        except Exception:
            pass

    def cmd(self, *args):
        self._send({"command": list(args)})

    # ----- scenes -----
    def background(self):
        self.cmd("set_property", "loop-playlist", "no")
        self.cmd("loadfile", str(self.bg), "replace")

    def video(self, src, mute=True):
        self.cmd("set_property", "mute", mute)
        self.cmd("set_property", "loop-playlist", "no")
        self.cmd("set_property", "loop-file", "inf")
        self.cmd("loadfile", src, "replace")

    def playlist(self, files):
        path = HERE / "cache" / "attract.m3u"
        path.write_text("\n".join(files) + "\n")
        self.cmd("set_property", "mute", True)
        self.cmd("set_property", "loop-file", "no")
        self.cmd("set_property", "loop-playlist", "inf")
        self.cmd("loadlist", str(path), "replace")

    def mute(self, on):
        self.cmd("set_property", "mute", on)

    def restart_clip(self):
        self.cmd("seek", 0, "absolute")

    def text(self, ass):
        self._send({"command": {"name": "osd-overlay", "id": 1, "format": "ass-events", "data": ass,
                                "res_x": 1920, "res_y": 1080, "z": 0}})


def make_background(path):
    """A quiet near-black card with the wordmark, for the moments there's no video."""
    if path.exists():
        return
    from PIL import Image, ImageDraw, ImageFont
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (1920, 1080), (11, 11, 15))
    d = ImageDraw.Draw(img)
    for y in range(1080):  # a faint glow from the bottom
        a = max(0, (y - 540) / 540) ** 2
        d.line([(0, y), (1920, y)], fill=(int(11 + 14 * a), int(11 + 10 * a), int(15 + 22 * a)))
    font = ImageFont.truetype(find_font(bold=True), 44)
    d.text((960, 120), "fal call center", font=font, fill=(90, 90, 104), anchor="mm")
    img.save(path)


def find_font(bold=False):
    b = "-Bold" if bold else ""
    for p in (f"/usr/share/fonts/truetype/dejavu/DejaVuSans{b}.ttf",  # the Pi
              f"/System/Library/Fonts/Supplemental/Arial{' Bold' if bold else ''}.ttf"):  # a Mac, for laptop tests
        if os.path.exists(p):
            return p
    return "DejaVuSans.ttf"


# ---------- the phone (the Pico, as a keyboard) ----------
class Phone:
    """Reads every keyboard that has F8 (the Pico, or a USB keyboard for testing) straight from /dev/input, grabbed
    so the keys never reach the desktop. Plugging the Pico in or out at any time is fine."""

    def __init__(self, post, grab=True):
        import evdev
        self.evdev, self.post, self.grab = evdev, post, grab
        self.open = set()
        threading.Thread(target=self._scan, daemon=True).start()

    def _scan(self):
        e = self.evdev.ecodes
        while True:
            for path in self.evdev.list_devices():
                if path in self.open:
                    continue
                try:
                    dev = self.evdev.InputDevice(path)
                    keys = dev.capabilities().get(e.EV_KEY, [])
                    if e.KEY_F8 not in keys or e.KEY_1 not in keys:
                        dev.close()
                        continue
                    if self.grab:
                        dev.grab()
                except OSError:
                    continue
                log.info("phone keys from %s (%s)", dev.name, path)
                self.open.add(path)
                threading.Thread(target=self._read, args=(dev,), daemon=True).start()
            time.sleep(2)

    def _read(self, dev):
        e = self.evdev.ecodes
        digits = {getattr(e, f"KEY_{d}"): str(d) for d in range(10)}
        digits.update({getattr(e, f"KEY_KP{d}"): str(d) for d in range(10)})
        shift = ctrl = False
        try:
            for ev in dev.read_loop():
                if ev.type != e.EV_KEY:
                    continue
                if ev.code in (e.KEY_LEFTSHIFT, e.KEY_RIGHTSHIFT):
                    shift = ev.value != 0
                elif ev.code in (e.KEY_LEFTCTRL, e.KEY_RIGHTCTRL):
                    ctrl = ev.value != 0
                elif ev.value == 1:  # presses only, no auto-repeat
                    c = ev.code
                    if c == e.KEY_F8:
                        self.post(("hook", True))
                    elif c == e.KEY_F9:
                        self.post(("hook", False))
                    elif c == e.KEY_SPACE:
                        self.post(("toggle",))
                    elif c == e.KEY_R and ctrl:
                        self.post(("restart",))
                    elif (c == e.KEY_8 and shift) or c == e.KEY_KPASTERISK:
                        self.post(("key", "*"))
                    elif (c == e.KEY_3 and shift) or c == e.KEY_KPENTER:
                        self.post(("key", "#"))
                    elif c in digits:
                        self.post(("key", digits[c]))
        except OSError:
            log.warning("keys from %s unplugged", dev.path)
        finally:
            self.open.discard(dev.path)
            try:
                dev.close()
            except Exception:
                pass


# ---------- recent videos, kept on disk ----------
class Cache:
    """The last few videos, downloaded in the background: they loop on the screen between calls (with or without
    Wi-Fi) and the receipt's stills come from the local copy."""

    def __init__(self, keep, root=HERE / "cache"):
        self.root, self.keep = root, keep
        root.mkdir(parents=True, exist_ok=True)
        self.index = root / "history.json"
        try:
            self.items = [h for h in json.loads(self.index.read_text()) if (root / h["file"]).exists()]
        except Exception:
            self.items = []
        self.lock = threading.Lock()

    def add(self, take):
        take["done"] = threading.Event()
        threading.Thread(target=self._download, args=(take,), daemon=True).start()

    def _download(self, take):
        name = f"{int(time.time() * 1000)}.mp4"
        part = self.root / (name + ".part")
        try:
            with urllib.request.urlopen(take["url"], timeout=30) as r, open(part, "wb") as f:
                while chunk := r.read(1 << 16):
                    f.write(chunk)
            part.rename(self.root / name)
            take["file"] = str(self.root / name)
            with self.lock:
                self.items = [{"file": name, "prompt": take["prompt"]}] + self.items
                for old in self.items[self.keep:]:
                    (self.root / old["file"]).unlink(missing_ok=True)
                self.items = self.items[:self.keep]
                self.index.write_text(json.dumps(self.items))
        except Exception as e:
            log.warning("couldn't keep a copy of the video: %s", e)
            part.unlink(missing_ok=True)
        finally:
            take["done"].set()

    def recent(self):
        with self.lock:
            return [(str(self.root / h["file"]), h["prompt"]) for h in self.items]


# ---------- the receipt ----------
DOTS = 576  # across an 80 mm receipt at 203 dpi


def receipt_image(take, cfg, src):
    from PIL import Image, ImageDraw, ImageFont
    frames = []
    for frac in (0.2, 0.5, 0.8):
        try:
            png = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{cfg['secs'] * frac:.2f}", "-i", src, "-frames:v",
                                  "1", "-vf", f"scale={DOTS}:-2", "-f", "image2pipe", "-vcodec", "png", "-"],
                                 capture_output=True, timeout=30).stdout
        except FileNotFoundError:  # no ffmpeg (a laptop test): a receipt without stills
            break
        if png:
            frames.append(Image.open(io.BytesIO(png)).convert("L"))
    big = ImageFont.truetype(find_font(True), 40)
    mid = ImageFont.truetype(find_font(), 26)
    small = ImageFont.truetype(find_font(), 22)
    probe = ImageDraw.Draw(Image.new("L", (1, 1)))

    def wrap(text, font, width=DOTS - 32):
        lines, cur = [], ""
        for word in text.split():
            trial = f"{cur} {word}".strip()
            if probe.textlength(trial, font=font) <= width or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = word
        return lines + ([cur] if cur else [])

    said = wrap(f"“{take['prompt']}”", mid)
    when = " · ".join(filter(None, [cfg["line"] and f"Line {cfg['line']}", time.strftime("%b %d, %Y  %I:%M %p")]))
    how = f"{take['model']} on fal · {cfg['secs']} s · ready in {take['secs']:.1f} s"
    img = Image.new("L", (DOTS, 3000), 255)  # cropped to what was drawn
    d = ImageDraw.Draw(img)
    y = 20
    d.text((DOTS // 2, y), "fal call center", font=big, fill=0, anchor="mt"); y += 56
    d.text((DOTS // 2, y), when, font=small, fill=0, anchor="mt"); y += 34
    d.line([(16, y), (DOTS - 16, y)], fill=0, width=2); y += 20
    for line in said:
        d.text((DOTS // 2, y), line, font=mid, fill=0, anchor="mt"); y += 34
    y += 12
    for f in frames:
        img.paste(f.convert("1", dither=Image.Dither.FLOYDSTEINBERG).convert("L"), (0, y))
        y += f.height + 12
    d.line([(16, y), (DOTS - 16, y)], fill=0, width=2); y += 16
    d.text((DOTS // 2, y), how, font=small, fill=0, anchor="mt"); y += 36
    d.text((DOTS // 2, y), "Thank you for calling.", font=mid, fill=0, anchor="mt"); y += 50
    return img.crop((0, 0, DOTS, y)).point(lambda v: 255 if v > 127 else 0).convert("1")


def escpos(img):
    """ESC/POS raster: works on any 80 mm Epson-compatible printer, no driver."""
    black = ~np.array(img, dtype=bool)  # in mode "1", True is white
    out = bytearray(b"\x1b@")
    for top in range(0, black.shape[0], 256):  # strips, for printers with a small buffer
        strip = np.packbits(black[top:top + 256], axis=1)
        rows, width = strip.shape
        out += b"\x1dv0\x00" + bytes([width & 255, width >> 8, rows & 255, rows >> 8]) + strip.tobytes()
    out += b"\x1bd\x04" + b"\x1dV\x42\x00"  # feed, then cut
    return bytes(out)


class Printer:
    def __init__(self, cfg):
        self.cfg = cfg

    def print(self, take):
        if take.get("done"):
            take["done"].wait(20)
        src = take.get("file") or take["url"]
        data = escpos(receipt_image(take, self.cfg, src))
        path = printer_path(self.cfg)
        if "/" not in path:
            # a printer queue name (e.g. on a Mac): the same bytes, passed through untouched
            subprocess.run(["lp", "-d", path, "-o", "raw"], input=data, check=True, capture_output=True, timeout=30)
            return
        with open(path, "wb") as f:
            f.write(data)


def printer_path(cfg):
    """The configured printer, or else the first USB printer the kernel found (lp0 can come back as lp1 after a
    replug)."""
    if os.path.exists(cfg["printer"]):
        return cfg["printer"]
    found = sorted(glob.glob("/dev/usb/lp*"))
    return found[0] if found else cfg["printer"]


def test_print():
    """python3 booth.py --test-print : one receipt from a test pattern, to check the printer on its own."""
    import tempfile
    cfg = load_config()
    clip = Path(tempfile.mkdtemp()) / "test.mp4"
    try:
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=s=1280x720:d={cfg['secs']}:r=24",
                        "-pix_fmt", "yuv420p", str(clip)], check=True)
    except FileNotFoundError:
        print("(no ffmpeg here, so the receipt has no pictures; the printer test still counts)")
    take = {"prompt": "This is a test print from the fal call center", "model": "Test pattern", "secs": 0.0,
            "url": str(clip)}
    path = printer_path(cfg)
    print(f"printing to {path} ...")
    Printer(cfg).print(take)
    print("sent. A receipt with a striped test pattern should come out and cut.")


# ---------- the call ----------
class Booth:
    def __init__(self, cfg, line=None, screen=None, fal=None, printer=None, cache=None):
        self.cfg, self.line, self.screen, self.fal, self.printer, self.cache = cfg, line, screen, fal, printer, cache
        self.q = queue.Queue()
        self.timers, self.seq = [], itertools.count()
        self.off_hook = False
        self.cid = 0          # call number: bumped on every lift and hang-up
        self.state = "idle"   # idle | greeting | listening | hold | ready | busy
        self.attempt = 0
        self.take = None      # the take on screen
        self.pending = None   # number of the render we're waiting for; a newer one (3 pressed) replaces it
        self.hold_from = None
        self.last_key = 0.0
        self.view = {"said": "", "status": "", "keys": None, "center": ""}
        self.attract = []
        self.online = True

    def post(self, ev):
        self.q.put(ev)

    def after(self, secs, *ev):
        heapq.heappush(self.timers, (time.monotonic() + secs, next(self.seq), ev))

    def run(self, until=None, watch_network=True):
        self.go_idle()
        if watch_network:
            threading.Thread(target=self._watch_network, daemon=True).start()
        while until is None or not until():
            wait = max(0.0, self.timers[0][0] - time.monotonic()) if self.timers else 0.5
            try:
                ev = self.q.get(timeout=min(wait, 0.5))
            except queue.Empty:
                ev = None
            while self.timers and self.timers[0][0] <= time.monotonic():
                self.handle(heapq.heappop(self.timers)[2])
            if ev:
                self.handle(ev)

    def handle(self, ev):
        try:
            getattr(self, "on_" + ev[0])(*ev[1:])
        except SystemExit:
            raise
        except Exception:
            log.exception("while handling %s", ev[0])

    def play(self, name, what):
        """A line on the handset that the call waits on. If the adapter is unplugged the clip never finishes, so the
        call carries on anyway a moment after it should have ended."""
        tag = (what, self.cid)
        secs = self.line.play(name, tag=tag)
        self.after(secs + 1.5, "clip_done", tag)

    # ----- the phone -----
    def on_hook(self, lifted):
        self.pick_up() if lifted else self.hang_up()

    def on_toggle(self):
        self.on_hook(not self.off_hook)

    def on_restart(self):
        log.info("restart requested")
        raise SystemExit(0)

    def pick_up(self):
        if self.off_hook:
            return
        self.off_hook = True
        self.cid += 1
        self.attempt = 0
        self.take = None
        self.line.stop(fade=False)
        self.screen.background()
        threading.Thread(target=self.fal.warm_up, daemon=True).start()
        self.greet()

    def greet(self):
        self.state = "greeting"
        self.set_view(center="Leave your prompt after the beep.")
        self.play("greeting", "greeted")

    def hang_up(self):
        if not self.off_hook:
            return
        self.off_hook = False
        self.cid += 1
        self.line.cancel_recording()
        self.line.stop()
        self.go_idle()

    def on_key(self, k):
        if k == "#" and self.state == "listening":
            return self.line.stop_recording()
        if not self.off_hook:
            return
        self.bump_idle()
        if self.state == "ready" and self.take and k in "230":
            return self.act(k)
        self.line.key(k)

    # ----- line events -----
    def on_clip_done(self, tag):
        what, cid = tag
        if cid != self.cid:
            return
        if what == "greeted" and self.state == "greeting":
            self.state = "listening"
            self.set_view(center="Listening…  press # when you're done.")
            self.line.record(self.cid)
        elif what == "menu":
            self.bump_idle()

    def on_heard(self, cid, rec):
        if cid != self.cid or self.state != "listening":
            return
        if not rec["heard"]:
            self.attempt += 1
            return self.greet() if self.attempt < 2 else self.busy("Didn't hear anything. Hang up and try again.")
        # On hold straight away: the hold line and music cover the transcription as well as the render.
        self.line.hold()
        self.start_hold(rec["speech_end"], said="")
        self.pending = rid = next(self.seq)
        threading.Thread(target=self._transcribe_and_render, args=(cid, rid, rec["wav"], rec["speech_end"]),
                         daemon=True).start()

    def _transcribe_and_render(self, cid, rid, wav, t0):
        try:
            prompt = self.fal.transcribe(wav)
        except Exception:
            return self.post(("failed", cid, rid, "Our lines are busy. Hang up and try again."))
        self.post(("prompt", cid, rid, prompt))
        if not heard_nothing(prompt) and not BLOCKED.search(prompt):
            self._render(cid, rid, prompt, t0)

    def on_prompt(self, cid, rid, prompt):
        log.info("heard: %r", prompt)
        if cid != self.cid or rid != self.pending:
            return
        if heard_nothing(prompt):
            return self.busy("Sorry, we didn't catch that. Hang up and try again.")
        if BLOCKED.search(prompt):
            return self.busy("Let's keep it friendly. Hang up and try another one.")
        self.view["said"] = f"“{prompt}”"

    def regenerate(self, prompt):
        """3: the same prompt again. The last video keeps playing (muted) under the hold clock."""
        self.line.hold()
        self.start_hold(time.monotonic(), said=f"“{prompt}”", keep_video=True)
        self.pending = rid = next(self.seq)
        threading.Thread(target=self._render, args=(self.cid, rid, prompt, time.monotonic()), daemon=True).start()

    def _render(self, cid, rid, prompt, t0):
        try:
            url, model = self.fal.render(prompt)
        except Refused:
            return self.post(("failed", cid, rid, "That one didn't go through. Hang up and try another."))
        except Exception:
            return self.post(("failed", cid, rid, "Our lines are busy. Hang up and try again."))
        self.post(("rendered", cid, rid, {"prompt": prompt, "url": url, "model": model, "secs": time.monotonic() - t0,
                                          "printed": False, "answered": False, "sound": False}))

    def on_rendered(self, cid, rid, take):
        log.info("video: %s, %.1f s after they stopped talking", take["model"], take["secs"])
        self.cache.add(take)  # kept for the screen between calls, even if they hung up while it rendered
        if cid != self.cid or rid != self.pending or not self.off_hook:
            return
        self.pending = None
        take["rid"] = rid
        self.take = take
        self.state = "ready"
        self.hold_from = None
        self.line.stop()
        self.screen.video(take["url"], mute=True)
        self.set_view(said=f"“{take['prompt']}”", status=f"Ready in {take['secs']:.1f} s", keys={})
        self.after(8, "menu", cid, rid)  # in case the clip never reports it's playing
        self.bump_idle()

    def on_file_loaded(self):
        t = self.take
        if self.state == "ready" and t and not t.get("loaded"):
            t["loaded"] = True
            self.after(self.cfg["menu_after"], "menu", self.cid, t["rid"])

    def on_video_error(self):
        if self.state == "ready" and self.take:
            self.on_menu(self.cid, self.take["rid"])
        elif self.state == "idle":
            self.after(0.5, "next_clip")

    def on_menu(self, cid, rid):
        t = self.take
        if cid != self.cid or self.state != "ready" or not t or t["rid"] != rid or t["answered"] or t.get("menu"):
            return
        t["menu"] = True
        self.play("menu", "menu")

    def on_failed(self, cid, rid, message):
        if cid == self.cid and rid == self.pending and self.off_hook:
            self.busy(message)

    # ----- the menu -----
    def act(self, k):
        t = self.take
        t["answered"] = True
        self.line.stop()  # cuts the menu off
        self.line.key(k)
        keys = self.view["keys"]
        if k == "2":
            t["sound"] = not t["sound"]
            self.screen.mute(not t["sound"])
            if t["sound"]:
                self.screen.restart_clip()
            keys["2"] = "Sound on" if t["sound"] else None
        elif k == "3":
            self.screen.mute(True)
            self.take = None
            return self.regenerate(t["prompt"])
        elif k == "0" and not t["printed"]:
            t["printed"] = True
            keys["0"] = "Printing…"
            threading.Thread(target=self._print, args=(self.cid, t), daemon=True).start()
        self.draw()

    def _print(self, cid, take):
        try:
            self.printer.print(take)
            self.post(("printed", cid, take, True))
        except Exception as e:
            log.error("print failed: %s", e)
            self.post(("printed", cid, take, False))

    def on_printed(self, cid, take, ok):
        if not ok:
            take["printed"] = False  # 0 again tries again
        if cid == self.cid and self.take is take and self.state == "ready":
            self.view["keys"]["0"] = "Printed" if ok else "Printer offline"
            self.draw()

    # ----- busy, idle -----
    def busy(self, message):
        self.state = "busy"
        self.pending = None
        self.hold_from = None
        self.line.cancel_recording()
        self.line.stop(fade=False)
        self.line.play("busy")
        self.screen.background()
        self.set_view(center=message)

    def bump_idle(self):
        self.last_key = time.monotonic()
        self.after(self.cfg["off_hook_idle"], "idle_check", self.cid, self.last_key)

    def on_idle_check(self, cid, armed):
        # After the menu, a handset left off the hook gets the busy signal, like a real line.
        if cid == self.cid and self.off_hook and self.state == "ready" and armed >= self.last_key:
            self.busy("Please hang up the phone.")

    def go_idle(self):
        self.state = "idle"
        self.pending = None
        self.hold_from = None
        if self.take:
            # the clip they just made keeps playing, muted, for a little while before the loop of recent ones
            self.screen.mute(True)
            self.set_view(said=f"“{self.take['prompt']}”", status="Pick up the phone to make your own.")
            self.take = None
            self.after(self.cfg["attract_after"], "attract", self.cid)
        else:
            self.on_attract(self.cid)

    def on_attract(self, cid):
        if cid != self.cid or self.state != "idle":
            return
        self.attract = self.cache.recent()
        if self.attract:
            self.screen.playlist([f for f, _ in self.attract])
            self.set_view(said=f"“{self.attract[0][1]}”", status="Pick up the phone to make your own.")
        else:
            self.screen.background()
            self.set_view(center="Pick up the phone.")

    def on_playlist_pos(self, pos):
        if self.state == "idle" and isinstance(pos, int) and 0 <= pos < len(self.attract):
            self.set_view(said=f"“{self.attract[pos][1]}”", status="Pick up the phone to make your own.")

    def on_next_clip(self):
        if self.state == "idle" and self.attract:
            self.screen.cmd("playlist-next", "force")

    def on_screen_restarted(self):
        if self.state == "ready" and self.take:
            self.screen.video(self.take["url"], mute=not self.take["sound"])
        elif self.state == "idle":
            return self.on_attract(self.cid)
        else:
            self.screen.background()
        self.draw()

    # ----- on hold -----
    def start_hold(self, t0, said, keep_video=False):
        self.state = "hold"
        self.hold_from = t0
        if not keep_video:
            self.screen.background()
        self.set_view(said=said)
        self.on_tick()

    def on_tick(self):
        if self.state != "hold" or self.hold_from is None:
            return
        label = "On hold" if self.view["said"] else "Connecting you to our fastest agent"
        self.view["status"] = f"{label} · {time.monotonic() - self.hold_from:.1f} s"
        self.draw()
        self.after(0.1, "tick")

    # ----- network light (for the staff) -----
    def _watch_network(self):
        while True:
            try:
                socket.create_connection(("queue.fal.run", 443), timeout=3).close()
                ok = True
            except OSError:
                ok = False
            if ok != self.online:
                self.post(("online", ok))
            time.sleep(10)

    def on_online(self, ok):
        self.online = ok
        log.info("network %s", "back" if ok else "down")
        self.draw()

    # ----- words on screen -----
    def set_view(self, said="", status="", keys=None, center=""):
        self.view = {"said": said, "status": status, "keys": keys, "center": center}
        self.draw()

    def draw(self):
        v, out = self.view, []
        dot = r"{\c&H6AD15B&}●" if self.online else r"{\c&H4B4BFF&}● offline"
        out.append(r"{\an7\pos(48,40)\fs28\bord0\shad0\c&HA8A0A0&}"
                   + f"Line {ass_text(self.cfg['line'])}  " + dot)
        if v["center"]:
            out.append(r"{\an5\pos(960,540)\fs76\b1\bord3\shad0\3c&H000000&\c&HFFFFFF&}" + ass_text(v["center"]))
        else:
            if v["said"] or v["status"]:
                out.append(r"{\an7\pos(0,800)\p1\bord0\shad0\c&H000000&\alpha&H60&}m 0 0 l 1920 0 l 1920 280 l 0 280{\p0}")
            if v["said"]:
                out.append(r"{\an2\pos(960,925)\fs62\b1\bord2\shad0\3c&H000000&\c&HFFFFFF&}" + ass_text(v["said"][:120]))
            if v["status"]:
                out.append(r"{\an2\pos(960,995)\fs40\bord2\shad0\3c&H000000&\c&H9EE6FF&}" + ass_text(v["status"]))
            if v["keys"] is not None:
                labels = {"2": "Sound", "3": "Regenerate", "0": "Print"}
                parts = []
                for k in "230":
                    done = v["keys"].get(k)
                    parts.append("{" + (r"\c&H6AD15B&" if done else r"\c&HFFFFFF&") + r"\b1}" + f"[{k}]"
                                 + r"{\b0} " + ass_text(done or labels[k]))
                out.append(r"{\an2\pos(960,1058)\fs38\bord2\shad0\3c&H000000&}" + "      ".join(parts))
        self.screen.text("\n".join(out))


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # not every status poll
    cfg = load_config()
    if not fal_key():
        log.error("no fal key: put it in %s", HERE / "fal.key")
    booth = Booth(cfg)
    booth.line = Line(booth.post, cfg)
    booth.screen = Screen(booth.post)
    booth.fal = Fal(cfg)
    booth.printer = Printer(cfg)
    booth.cache = Cache(cfg["history"])
    Phone(booth.post, grab=cfg["grab"])
    try:
        booth.run()
    finally:
        booth.screen.close()


if __name__ == "__main__":
    test_print() if "--test-print" in sys.argv else main()
