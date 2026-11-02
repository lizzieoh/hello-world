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
import random
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
           "PLAYS_BEFORE_SLIDES": "4", "SLIDES": "6", "SLIDE_SECS": "2.5", "PRINT_LIGHTEN": "35",
           "OUT_DEVICE": "", "IN_DEVICE": "", "LOW_VOLUME": "40", "RING_EVERY": "120", "LOCATION": "MIAMI",
           "END_PAUSE": "5", "LISTEN_MAX": "30"}
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
        "location": cfg["LOCATION"],  # printed on the receipt, next to the line number
        # How much lighter the receipt's pictures print, 0-100 (0 = as filmed). Thermal paper prints dark.
        "print_lighten": min(100, max(0, int(cfg["PRINT_LIGHTEN"]))),
        "grab": cfg["GRAB"] != "0",
        # Sound devices by (part of) their name; empty = the system default (on the Pi, run.sh points that at the
        # USB adapter). E.g. on a laptop: OUT_DEVICE="External Headphones", IN_DEVICE="USB Audio".
        "out_device": cfg["OUT_DEVICE"] or None, "in_device": cfg["IN_DEVICE"] or None,
        # Listening ends when they press #. For callers who never do: a pause this long after speaking, or this
        # long in all (seconds). No speech at all for no_speech seconds counts as a silent caller.
        "silence": max(0.5, float(cfg["END_PAUSE"])), "listen_max": max(5.0, float(cfg["LISTEN_MAX"])),
        "no_speech": 6.0,
        "render_timeout": 45.0,                                  # per model, then the next model
        "menu_after": 3.0,                                       # seconds into the clip before the menu is read
        "off_hook_idle": 60.0,                                   # handset left off after the menu -> busy signal
        "history": 12,                                           # recent videos kept for the screen between calls
        "attract_after": 20.0,                                   # after a hang-up, their clip stays up this long
        # After the video has played this many times, it turns into a slideshow of stills from it (0 = never).
        "plays_before_slides": max(0, int(cfg["PLAYS_BEFORE_SLIDES"])),
        "slides": max(2, int(cfg["SLIDES"])), "slide_secs": max(0.5, float(cfg["SLIDE_SECS"])),
        # The phone's volume switch on LOW: the handset and the video's sound at this fraction of loud.
        "low_volume": min(100, max(0, int(cfg["LOW_VOLUME"]))) / 100,
        # The phone's ringer switch ON: while nobody's on the phone, it rings this often (seconds).
        "ring_every": max(20.0, float(cfg["RING_EVERY"])),
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


SOUND_TAGS = re.compile(r"\*[^*]*\*|\[[^\]]*\]|\([^)]*\)")  # *sniff*, [laughter], (music): sounds, not words


def heard_nothing(prompt):
    return re.sub(r"[^a-z ]", "", prompt.lower()).strip() in NOT_SPEECH


BLOCKED = re.compile(r"\b(nude|naked|nudity|porn\w*|sex\w*|nsfw|genital\w*|gore|gory|behead\w*|suicide|nazi\w*|hitler"
                     r"|rape\w*|terroris\w*)\b", re.I)


# Nobody else's intellectual property, ever: the booth only makes original ideas. These names are refused at once;
# everything else is read by a language model first (Fal.check_ip), which also catches characters that are
# described rather than named.
# Only names that can't mean anything else ("frozen", "sonic", "mario" are left to the model).
IP_NAMES = re.compile(r"\b(sponge ?bob|spider[- ]?man|mickey mouse|disney|pixar|marvel|avengers|batman|superman|"
                      r"pok[eé]mon|pikachu|star ?wars|darth vader|yoda|harry potter|hogwarts|barbie|shrek|simpsons|"
                      r"minecraft|fortnite|hello kitty|ghibli|totoro|adobe|photoshop|nike|coca[- ]?cola|pepsi|"
                      r"mcdonald'?s|starbucks|iphone)\b", re.I)
IP_CHECK_PROMPT = (
    "You screen prompts for a public video booth. The booth must NEVER make anyone else's intellectual property. "
    'Reply with JSON only: {"ok": true} or {"ok": false, "what": "<short name of the IP>"}.\n'
    "Not ok: any copyrighted or trademarked character, franchise, film, show, game, comic, book, song or mascot, "
    "named OR clearly described (e.g. a yellow square sponge in brown shorts who lives in a pineapple); any brand, "
    "company, logo, product or app (Adobe, Nike, Coca-Cola, iPhone, fal included); any real person, celebrity, "
    "politician or public figure; any named artist's or studio's style (Pixar, Ghibli, Disney, Wes Anderson).\n"
    "Ok: original ideas, generic animals, objects, foods, drinks, places, cities, landmarks, sports, jobs, genres "
    "and moods (a sponge, a spider, a superhero I made up, a cartoon, anime style, Miami at night, a pumpkin spice "
    "latte, a burger, sneakers, a smartphone, a quarterback). Words used in their ordinary meaning are ok (frozen "
    "lake, a sonic boom). Judge only what is actually asked for.")
REMIX_PROMPT = (
    "You work at a video booth that only makes original ideas. The caller asked for something that borrows "
    "someone else's intellectual property ({what}). Change only the borrowed part: replace each borrowed character, "
    "brand, logo or real person with an original one nobody could mistake for it: a different kind of character "
    "altogether (a superhero becomes, say, a chef, a robot or an animal), NOT its colours, NOT its shape, NOT its outfit, none of its signature traits, no names (SpongeBob is "
    "yellow and square, so not yellow and not square). Keep everything else "
    "exactly as they said it: the places, the action, the time of day and the mood. Make it fun. Reply with the "
    "new prompt only: one sentence, under 30 words.")
REMIXING = "That one's someone else's character, so our agent is making you an original."
ASK_AGAIN = "That one belongs to someone else. Try your own idea after the beep."
ORIGINAL_ONLY = " Original characters and designs only: no logos, brand names or famous people."


# ---------- fal ----------
class Refused(Exception):
    """fal said no to the request itself (bad input, safety checker): the backup model would say no too."""


class Fal:
    STT = "fal-ai/wizper"
    STT_BACKUP = "fal-ai/elevenlabs/speech-to-text/scribe-v2"
    STT_BACKUP_AFTER = 1.5  # s
    STT_DEADLINE = 12       # s, then the caller hears "lines are busy"
    IP_CHECK = "openrouter/router"
    IP_CHECK_MODELS = ("anthropic/claude-haiku-4.5", "openai/gpt-6-luna")  # about 1 s; the second if the first fails
    LLM_NAMES = {"anthropic/claude-haiku-4.5": "Claude Haiku 4.5", "openai/gpt-6-luna": "GPT-6 Luna"}

    def _llm(self, model, system, prompt, bill, label, timeout=8, **kw):
        """One language-model call through fal. Its cost (fal reports it) goes on the receipt's bill."""
        r = self.client.run(self.IP_CHECK, {"model": model, "system_prompt": system, "prompt": prompt, **kw},
                            timeout=timeout)
        if bill is not None:
            bill.append((label, self.LLM_NAMES.get(model, model), float((r.get("usage") or {}).get("cost") or 0)))
        return r.get("output") or ""

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

    def load_prices(self):
        """fal's current prices for the models in use; the PRICES list if fal can't be reached."""
        self.prices = dict(PRICES)
        try:
            ids = [e for _, e, _ in self.models] + [self.STT, self.STT_BACKUP]
            r = self.client._client.get("https://api.fal.ai/v1/models/pricing",
                                        params=[("endpoint_id", e) for e in ids], timeout=8)
            for p in r.json().get("prices", []):
                self.prices[p["endpoint_id"]] = float(p["unit_price"])
            log.info("prices: %s", {k.split("/")[1]: v for k, v in self.prices.items()})
        except Exception as e:
            log.warning("price lookup failed, using the built-in list: %s", e)

    def endpoint(self, name):
        return next(e for n, e, _ in self.models if n == name)

    def warm_up(self):
        """Open the connection to fal while the greeting plays, so the real requests skip DNS and TLS."""
        try:
            self.client._client.head("https://queue.fal.run", timeout=3)
        except Exception:
            pass

    def transcribe(self, wav_bytes):
        """Wizper first; if it hasn't answered in STT_BACKUP_AFTER s (it once stalled for 77 s), Scribe joins the race.
        First good answer wins; the other is left to finish on its own. Returns (text, endpoint)."""
        uri = "data:audio/x-wav;base64," + base64.b64encode(wav_bytes).decode()
        lang = self.cfg["language"]
        tries = [(self.STT, {"audio_url": uri, "task": "transcribe", **({} if lang == "auto" else {"language": lang})}),
                 (self.STT_BACKUP, {"audio_url": uri, "diarize": False, "tag_audio_events": False})]
        answers = queue.Queue()

        def one(endpoint, args):
            try:
                text = SOUND_TAGS.sub("", self.client.run(endpoint, args, timeout=15).get("text") or "")
                answers.put((endpoint, " ".join(text.split()), None))
            except Exception as e:
                answers.put((endpoint, None, e))

        t0 = time.monotonic()
        deadline = t0 + self.STT_DEADLINE
        started, failed = 0, 0
        while True:
            # start the next one at once, or when the last one failed, or when Wizper is slow
            if started < len(tries) and (started == 0 or failed == started or
                                         time.monotonic() - t0 > self.STT_BACKUP_AFTER):
                threading.Thread(target=one, args=tries[started], daemon=True).start()
                started += 1
            wait = deadline - time.monotonic()
            if wait <= 0:
                raise TimeoutError(f"no transcript in {self.STT_DEADLINE:.0f} s")
            try:
                endpoint, text, err = answers.get(timeout=min(wait, 0.1))
            except queue.Empty:
                continue
            if err is None:
                return text, endpoint
            log.warning("transcribe failed (%s): %s", endpoint.split("/")[-1], err)
            failed += 1
            if failed == len(tries):
                raise err

    def check_ip(self, prompt, bill=None):
        """None if the prompt is an original idea, else what it borrows (a character, brand, real person...).
        Raises if no checker answered: then the booth makes nothing, rather than risk it. Each check's model and
        cost are added to `bill`, for the receipt."""
        m = IP_NAMES.search(prompt)
        if m:  # free, so it isn't on the bill
            return m.group(0)
        last = None
        for model in self.IP_CHECK_MODELS:
            try:
                out = self._llm(model, IP_CHECK_PROMPT, prompt, bill, "IP CHECK", timeout=6, temperature=0,
                                max_tokens=60)
                verdict = json.loads(re.search(r"\{.*?\}", out, re.S).group(0))
                return None if verdict.get("ok") is True else str(verdict.get("what") or "someone else's IP")
            except Exception as e:
                log.warning("IP check failed (%s): %s", model, e)
                last = e
        raise last

    def remix(self, prompt, borrowed, bill=None):
        """The prompt turned into an original idea with the same energy, checked again before it's used. Raises
        if it can't be made original (the caller is then asked for another idea)."""
        last = None
        for model in self.IP_CHECK_MODELS:
            try:
                out = self._llm(model, REMIX_PROMPT.format(what=borrowed), prompt, bill, "MADE ORIGINAL",
                                temperature=0.7, max_tokens=80)
                original = " ".join(out.strip().strip('"“”').split())
                if not original:
                    raise RuntimeError("empty remix")
                still = self.check_ip(original, bill)
                if still:
                    raise RuntimeError(f"the remix still borrows {still}: {original!r}")
                return original
            except Exception as e:
                log.warning("remix failed (%s): %s", model, e)
                last = e
        raise last

    def render(self, prompt):
        """Each model gets one go, in order. Returns (url, model name)."""
        last = None
        for name, endpoint, build in self.models:
            try:
                return self._one(endpoint, build(prompt + ORIGINAL_ONLY)), name
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


def ring():
    """A phone ringing for people walking by: the two-tone trill, 2 s on and 4 s off, three times."""
    t = np.arange(int(2.0 * SR)) / SR
    trill = (tone([440, 480], 2.0, peak=0.2) * (0.5 + 0.5 * np.sin(2 * np.pi * 20 * t))).astype(np.float32)
    return np.concatenate([trill, silence(4.0), trill, silence(4.0), trill])


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
        self.gain = 1.0  # the volume switch: 1 on loud, cfg["low_volume"] on low
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
            "ring": ring(),
            "beep": tone([1000], 0.4),
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
        outdata[:, 0] = np.clip(mix * self.gain, -1, 1)

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
            self.rec = {"cid": cid, "chunks": [], "n": 0, "floor": 0.01, "heard": False, "last_loud": None,
                        "wall0": time.monotonic()}

    def stop_recording(self):
        """# pressed: whatever was said so far is the prompt."""
        with self.lock:
            r = self.rec
            if r:
                r["heard"] = r["heard"] or r["n"] > 0.6 * MIC_SR
                self._finish()

    def finish_if_heard(self):
        """Hung up while talking: what was said is the prompt, if they said anything."""
        with self.lock:
            r = self.rec
            if r and r["heard"]:
                self._finish()
            else:
                self.rec = None

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
        log.info("recorded %.1f s (speech heard: %s, ended %.1f s before the end, gain %.1fx, wall %.1f s)",
                 r["n"] / MIC_SR, r["heard"], since, min(30.0, 0.9 / peak) if peak > 0 else 0,
                 time.monotonic() - r["wall0"])
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
        self.vol = 100  # percent; kept here so a restarted mpv comes back at the same level
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
            "--keep-open=yes", "--image-display-duration=inf", "--loop-file=inf", "--mute=yes", f"--volume={self.vol}",
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
        self.cmd("set_property", "image-display-duration", "inf")
        self.cmd("set_property", "loop-playlist", "no")
        self.cmd("loadfile", str(self.bg), "replace")

    def slides(self, images, secs):
        """Stills from the video, one after another, round and round."""
        path = HERE / "cache" / "slides.m3u"
        path.write_text("\n".join(images) + "\n")
        self.cmd("set_property", "image-display-duration", secs)
        self.cmd("set_property", "loop-file", "no")
        self.cmd("set_property", "loop-playlist", "inf")
        self.cmd("loadlist", str(path), "replace")

    def video(self, src, mute=True):
        self.cmd("set_property", "image-display-duration", "inf")
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

    def volume(self, pct):
        self.vol = pct
        self.cmd("set_property", "volume", pct)

    def restart_clip(self):
        self.cmd("seek", 0, "absolute")

    def text(self, ass):
        self._send({"command": {"name": "osd-overlay", "id": 1, "format": "ass-events", "data": ass,
                                "res_x": 1920, "res_y": 1080, "z": 0}})


def make_background(path):
    """A quiet near-black card with the logo, for the moments there's no video. Redrawn when the logo changes."""
    if path.exists() and (not LOGO.exists() or path.stat().st_mtime > LOGO.stat().st_mtime):
        return
    from PIL import Image, ImageDraw, ImageFont
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (1920, 1080), (11, 11, 15))
    d = ImageDraw.Draw(img)
    for y in range(1080):  # a faint glow from the bottom
        a = max(0, (y - 540) / 540) ** 2
        d.line([(0, y), (1920, y)], fill=(int(11 + 14 * a), int(11 + 10 * a), int(15 + 22 * a)))
    if LOGO.exists():
        mark = logo_image(560)
        img.paste((90, 90, 104), (960 - mark.width // 2, 120 - mark.height // 2), mark)
    else:
        font = ImageFont.truetype(find_font(bold=True), 44)
        d.text((960, 120), "CALL-A-FAL", font=font, fill=(90, 90, 104), anchor="mm")
    img.save(path)


def find_font(bold=False):
    b = "-Bold" if bold else ""
    for p in (f"/usr/share/fonts/truetype/dejavu/DejaVuSans{b}.ttf",  # the Pi
              f"/System/Library/Fonts/Supplemental/Arial{' Bold' if bold else ''}.ttf"):  # a Mac, for laptop tests
        if os.path.exists(p):
            return p
    return "DejaVuSans.ttf"


# ---------- the phone (the Pico, as a keyboard) ----------
# The phone body's switches. The Pico sends their positions at startup, on every flip and every so often anyway.
SWITCH_KEYS = {"F17": ("volume", True), "F18": ("volume", False), "F19": ("ringer", True), "F20": ("ringer", False)}

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
        switches = {getattr(e, f"KEY_{k}"): ev for k, ev in SWITCH_KEYS.items()}
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
                    elif c == e.KEY_F10:
                        self.post(("shutdown",))
                    elif c in switches:
                        self.post(switches[c])
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
    """The last few videos, downloaded in the background, each with a few stills pulled from it. Between calls the
    screen shows the stills from all of them (with or without Wi-Fi), and the receipt's stills come from the local
    copy."""

    def __init__(self, keep, root=HERE / "cache", secs=10, stills=3):
        self.root, self.keep, self.secs, self.stills = root, keep, secs, stills
        root.mkdir(parents=True, exist_ok=True)
        self.index = root / "history.json"
        try:
            self.items = [h for h in json.loads(self.index.read_text()) if (root / h["file"]).exists()]
        except Exception:
            self.items = []
        self.lock = threading.Lock()
        threading.Thread(target=self._backfill, daemon=True).start()

    def add(self, take):
        take["done"] = threading.Event()
        threading.Thread(target=self._download, args=(take,), daemon=True).start()

    def _stills_dir(self, name):
        return self.root / "stills" / Path(name).stem

    def _pull_stills(self, name):
        try:
            return [str(Path(p).relative_to(self.root)) for p in
                    video_stills(str(self.root / name), self.secs, self.stills, self._stills_dir(name))]
        except Exception as e:
            log.warning("couldn't pull stills from %s: %s", name, e)
            return []

    def _backfill(self):
        """Videos saved before stills existed get theirs now."""
        for h in list(self.items):
            if not h.get("stills"):
                h["stills"] = self._pull_stills(h["file"])
        with self.lock:
            self.index.write_text(json.dumps(self.items))

    def _download(self, take):
        name = f"{int(time.time() * 1000)}.mp4"
        part = self.root / (name + ".part")
        try:
            with urllib.request.urlopen(take["url"], timeout=30) as r, open(part, "wb") as f:
                while chunk := r.read(1 << 16):
                    f.write(chunk)
            part.rename(self.root / name)
            take["file"] = str(self.root / name)
            stills = self._pull_stills(name)
            with self.lock:
                self.items = [{"file": name, "prompt": take["prompt"], "stills": stills}] + self.items
                for old in self.items[self.keep:]:
                    (self.root / old["file"]).unlink(missing_ok=True)
                    for f in self._stills_dir(old["file"]).glob("*"):
                        f.unlink(missing_ok=True)
                self.items = self.items[:self.keep]
                self.index.write_text(json.dumps(self.items))
        except Exception as e:
            log.warning("couldn't keep a copy of the video: %s", e)
            part.unlink(missing_ok=True)
        finally:
            take["done"].set()

    def clear(self):
        """Empties the feed. Videos and their stills move to archive/<date>/ (kept, out of the loop)."""
        with self.lock:
            items, self.items = self.items, []
            self.index.write_text("[]")
            if not items:
                return 0
            dest = self.root / "archive" / time.strftime("%Y-%m-%d_%H%M%S")
            dest.mkdir(parents=True, exist_ok=True)
            (dest / "history.json").write_text(json.dumps(items))
            for h in items:
                f = self.root / h["file"]
                if f.exists():
                    f.rename(dest / f.name)
                st = self._stills_dir(h["file"])
                if st.exists():
                    st.rename(dest / ("stills-" + st.name))
            return len(items)

    def recent(self):
        with self.lock:
            return [(str(self.root / h["file"]), h["prompt"]) for h in self.items]

    def slides(self):
        """Every saved still, newest video first, as (image, prompt)."""
        with self.lock:
            return [(str(self.root / st), h["prompt"]) for h in self.items for st in h.get("stills") or []
                    if (self.root / st).exists()]


# ---------- the receipt ----------
DOTS = 576  # across an 80 mm receipt at 203 dpi


def video_stills(src, secs, count, out_dir):
    """`count` evenly spaced frames from the video, as JPEGs in out_dir. Returns their paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for i in range(count):
        out = out_dir / f"{i}.jpg"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{secs * (i + 0.5) / count:.2f}", "-i", src,
                        "-frames:v", "1", "-q:v", "2", str(out)], capture_output=True, timeout=30)
        if out.exists() and out.stat().st_size:
            paths.append(str(out))
    return paths


BRAND = HERE / "brand"
LOGO = BRAND / "call-a-fal.png"  # black on transparent; its shape is all that's used, so any colour works


def logo_image(width):
    """The logo's shape at this width, as a mask (255 = ink) for pasting in any colour."""
    from PIL import Image
    a = Image.open(LOGO).convert("RGBA").getchannel("A")
    return a.resize((width, round(width * a.height / a.width)), Image.LANCZOS)

# fal's list prices (USD), used if the live price lookup at start-up can't be reached.
RESET_CODE = "*123#"  # staff: clears the screen's feed of earlier videos

# 3 (regenerate): one of these rides along with the caller's words, so each take really is a different video
TAKES = [
    "A completely different interpretation: new characters, new setting details, wide establishing shot.",
    "Reimagined at a different time of day with different lighting and colors, filmed from a low angle.",
    "Reimagined as a stylized animated film, with a moving handheld camera.",
    "A surprising new take: different subject details and composition, dramatic close-up shots.",
    "Reimagined in a different visual style, aerial drone camera, bold color palette.",
    "A playful, unexpected version with different characters and a fast tracking shot.",
]

PRICES = {"minimax/h3-max/text-to-video": 0.0325, "blackforestlabs/flux-3-max/text-to-video": 0.02125,
          "fal-ai/wizper": 0.00111, "fal-ai/elevenlabs/speech-to-text/scribe-v2": 0.008}


def brand_font(kind, size, weight=None):
    """fal's own fonts when they're installed in brand/fonts (not in git: they're licensed), else DejaVu."""
    from PIL import ImageFont
    files = {"display": "Focal-Upright-VF_wght.ttf", "mono": "HALTimezone-MonoRegular.otf",
             "serif": "HALTimezone-Regular.otf"}
    path = BRAND / "fonts" / files[kind]
    if path.exists():
        font = ImageFont.truetype(str(path), size)
        if weight:
            try:
                font.set_variation_by_axes([weight])
            except Exception:
                pass
        return font
    if kind == "mono":
        bold = (weight or 400) >= 600
        for p, i in ((f"/usr/share/fonts/truetype/dejavu/DejaVuSansMono{'-Bold' if bold else ''}.ttf", 0),
                     ("/System/Library/Fonts/Menlo.ttc", 1 if bold else 0)):
            if os.path.exists(p):
                return ImageFont.truetype(p, size, index=i)
    return ImageFont.truetype(find_font(bold=(weight or 400) >= 600 or kind == "display"), size)


def sharpest_frame(src, at, vf, spread=0.3):
    """The frame at `at` seconds, or one either side of it if that's sharper (less motion blur): three tries,
    scored by how much fine detail they have (the variance of the Laplacian)."""
    from PIL import Image
    best, score = None, -1.0
    for t in (at - spread, at, at + spread):
        png = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(0.0, t):.2f}", "-i", src, "-frames:v", "1",
                              "-vf", vf, "-f", "image2pipe", "-vcodec", "png", "-"],
                             capture_output=True, timeout=30).stdout
        if not png:
            continue
        f = Image.open(io.BytesIO(png)).convert("L")
        a = np.asarray(f, np.float32)
        s = float((4 * a[1:-1, 1:-1] - a[:-2, 1:-1] - a[2:, 1:-1] - a[1:-1, :-2] - a[1:-1, 2:]).var())
        if s > score:
            best, score = f, s
    return best


def thermal_photo(f, gamma):
    """A still made as legible as a thermal printer allows: full contrast, local contrast and edges brought up
    (fine detail is what survives 1-bit dots), lifted for dot spread, then error-diffused at every printer dot."""
    from PIL import Image, ImageFilter, ImageOps
    f = ImageOps.autocontrast(f, cutoff=0.5)
    f = f.filter(ImageFilter.UnsharpMask(radius=24, percent=45, threshold=0))   # shapes stand apart
    f = f.filter(ImageFilter.UnsharpMask(radius=1.0, percent=140, threshold=2))  # edges and faces
    f = f.point(lambda v: round(255 * (v / 255) ** (1 / gamma)))
    return f.convert("1", dither=Image.Dither.FLOYDSTEINBERG).convert("L")


def receipt_image(take, cfg, src):
    """The receipt: lots of white, the logo, small square stills that step from side to side, a few big lines in
    mono, and the itemized bill."""
    from PIL import Image, ImageDraw, ImageOps
    W, M = DOTS, 40  # width and side margin, in printer dots
    S = 230          # each still, square
    frames = []
    # the first and last stills are squares; the middle one is the whole frame, across the receipt
    for frac, vf in ((0.2, f"scale=-2:{S}:flags=lanczos,crop={S}:{S}"), (0.5, f"scale={W}:-2:flags=lanczos"),
                     (0.8, f"scale=-2:{S}:flags=lanczos,crop={S}:{S}")):
        try:
            f = sharpest_frame(src, cfg["secs"] * frac, vf)
        except FileNotFoundError:  # no ffmpeg (a laptop test): a receipt without stills
            break
        if f:
            frames.append(f)

    # Thermal paper drops strokes one dot thin, so everything below the big lines is bold.
    big = brand_font("mono", 50, 700)
    said_font = brand_font("mono", 27, 700)
    mono = brand_font("mono", 24, 700)
    small = brand_font("mono", 20, 700)
    probe = ImageDraw.Draw(Image.new("L", (1, 1)))

    def wrap(text, font, width=W - 2 * M):
        lines, cur = [], ""
        for word in text.split():
            trial = f"{cur} {word}".strip()
            if probe.textlength(trial, font=font) <= width or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = word
        return lines + ([cur] if cur else [])

    img = Image.new("L", (W, 4000), 255)  # cropped to what was drawn
    d = ImageDraw.Draw(img)
    y = 36

    def text(lines, font, gap, x=M):
        nonlocal y
        for line in lines:
            d.text((x, y), line, font=font, fill=0)
            y += gap

    def row(left, right, font=mono, gap=32):
        nonlocal y
        d.text((M, y), left, font=font, fill=0)
        d.text((W - M, y), right, font=font, fill=0, anchor="ra")
        y += gap

    # Thermal dots spread, so the pictures are lifted to print true: 35 (the default) -> 1.5 for the squares, and
    # 1.3 for the big middle one, which prints a little darker.
    lighten = cfg.get("print_lighten", 0)
    stills = iter(frames)

    def still(right, darker=False):
        nonlocal y
        f = next(stills, None)
        if f is None:
            return
        f = thermal_photo(f, 1 + lighten / (120 if darker else 70))
        x = 0 if f.width >= W else (W - M - f.width if right else M)
        img.paste(f, (x, y))
        y += f.height + 44

    # the logo (the fal mark, "fal" and CALL-A-FAL in one line)
    if LOGO.exists():
        mark = logo_image(400)
        img.paste(0, ((W - mark.width) // 2, y), mark)
        y += mark.height + 64
    else:
        d.text((W // 2, y), "CALL-A-FAL", font=brand_font("display", 60, 900), fill=0, anchor="mt")
        y += 104

    still(right=True)

    # which booth, when, where
    at = time.localtime(take.get("at"))
    text([f"LINE {int(cfg['line'] or 0):02d}"], big, 64)
    row(time.strftime("%b %d %Y", at).upper(), time.strftime("%I:%M %p", at), gap=34)
    row((cfg.get("location") or "").upper(), "")
    y += 28

    # what they asked for
    text(["YOU SAID"], small, 30)
    text(wrap(f"“{take.get('you_said') or take['prompt']}”", said_font), said_font, 36)
    if take.get("you_said"):  # someone else's IP: what the agent made instead
        y += 20
        text(["WE MADE YOU AN ORIGINAL"], small, 30)
        text(wrap(f"“{take['prompt']}”", said_font), said_font, 36)
    y += 40

    still(right=False, darker=True)  # full width
    text(["RESOLVED", f"IN {take['secs']:.1f} s"], big, 60)
    y += 36

    # the bill: every model the call used, and what it cost
    text(["ITEMIZED CALL"], small, 34)
    for label, detail, cost in take.get("costs") or []:
        row(label, f"${cost:.3f}" if cost >= 0.001 or not cost else f"${cost:.4f}")
        text([detail], small, 30, x=M + 16)
    total = sum(c for _, _, c in take.get("costs") or [])
    d.line([(M, y), (W - M, y)], fill=0, width=2); y += 16
    row("TOTAL", f"${total:.2f}", font=brand_font("mono", 40, 700), gap=50)
    y += 36

    still(right=True)

    # a QR code to keep the video, with its caption beside it: small, but every module a whole number of dots
    # (at least 3, about 0.4 mm), so phones still read it
    try:
        import qrcode
        qr = qrcode.QRCode(border=0, box_size=1, error_correction=qrcode.constants.ERROR_CORRECT_L)
        qr.add_data(take["url"])
        qr.make(fit=True)
        k = max(3, 120 // qr.modules_count)
        q = qr.make_image(fill_color="black", back_color="white").convert("L")
        q = q.resize((q.width * k, q.height * k), Image.NEAREST)
        img.paste(q, (M, y))
        for i, line in enumerate(("SCAN TO KEEP", "YOUR VIDEO")):
            d.text((M + q.width + 28, y + q.height // 2 - 28 + i * 30), line, font=small, fill=0)
        y += q.height + 36
    except Exception:  # no qrcode package, or no URL: the receipt still prints
        pass

    row("Thank you for calling.", "fal.ai", font=small, gap=30)
    y += 20
    return img.crop((0, 0, W, y)).point(lambda v: 255 if v > 200 else 0).convert("1")


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


def watch_heat(every=60, hot=75):
    """Every minute: temperature, fan speed and throttling into the log, so a hot booth shows up before it's slow.
    The fan itself is run by the Pi's firmware, on the curve setup-native.sh writes to config.txt."""
    def read(path):
        try:
            return Path(path).read_text().strip()
        except OSError:
            return None

    fans = glob.glob("/sys/devices/platform/cooling_fan/hwmon/*/fan1_input")
    warned, first = False, True
    while True:
        temp = read("/sys/class/thermal/thermal_zone0/temp")
        if temp is None:
            return  # not a Pi
        c = int(temp) / 1000
        rpm = read(fans[0]) if fans else None
        try:
            throttled = int(subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True,
                                           timeout=5).stdout.split("=")[1], 16)
        except Exception:
            throttled = 0
        # bits 0-3: under-voltage, frequency capped, throttled now, soft temperature limit
        now = throttled & 0xF
        if c >= hot or now:
            log.warning("HOT: %.1f C, fan %s rpm, throttled=0x%x%s", c, rpm or "none", throttled,
                        " (under-voltage: use the 27 W supply)" if throttled & 1 else "")
            warned = True
        elif warned or first:
            log.info("heat: %.1f C, fan %s rpm", c, rpm or "none (fit the Active Cooler)")
            warned = False
        else:
            log.debug("heat: %.1f C, fan %s rpm", c, rpm)
        first = False
        time.sleep(every)


def next_call_number(path=HERE / "cache" / "calls.txt"):
    """A running count of calls on this line, kept across restarts, for the receipt."""
    try:
        n = int(path.read_text().strip()) + 1
    except Exception:
        n = 1
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(n))
    except Exception:
        pass
    return n


# ---------- the call ----------
class Booth:
    def __init__(self, cfg, line=None, screen=None, fal=None, printer=None, cache=None):
        self.cfg, self.line, self.screen, self.fal, self.printer, self.cache = cfg, line, screen, fal, printer, cache
        self.q = queue.Queue()
        self.timers, self.seq = [], itertools.count()
        self.off_hook = False
        self.cid = 0          # call number: bumped on every lift and hang-up
        self.dialed = ""      # the last few keys, for staff codes
        self.take_offset = 0  # where in TAKES the next call's first regenerate starts, so callers don't all get the same one
        self.state = "idle"   # idle | greeting | listening | hold | ready | busy
        self.attempt = 0
        self.take = None      # the take on screen
        self.pending = None   # number of the render we're waiting for; a newer one (3 pressed) replaces it
        self.hold_from = None
        self.last_key = 0.0
        self.view = {"said": "", "status": "", "keys": None, "center": ""}
        self.attract = []
        self.slideshow = []
        self.stt_secs = {}     # render id -> seconds the speech-to-text took (for the bill)
        self.hung_up_cid = None  # a call hung up mid-sentence: what they'd said is still made
        self.orphans = set()     # renders whose caller hung up: shown on the screen when they arrive
        self.llm_bill = {}       # render id -> [(item, model, USD)] for the IP check and remix, for the receipt
        self.call_no = 0
        self.online = True
        self.loud = True       # the phone's volume switch
        self.ringer = False    # the phone's ringer switch; off until the Pico says otherwise
        self.ring_seq = 0      # bumped on every ringer flip, so an old ring timer stops
        self.ringing = False

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

    def on_shutdown(self):
        """Staff (# held 5 s): say so on screen, then power the Pi off safely so it can be unplugged."""
        log.info("shutdown requested")
        self.line.cancel_recording()
        self.line.stop(fade=False)
        self.state = "busy"
        self.screen.background()
        self.set_view(center="Shutting down. Unplug when the green light stops blinking.")
        threading.Timer(3, lambda: subprocess.run(["sudo", "-n", "shutdown", "-h", "now"], check=False)).start()

    def on_restart(self):
        log.info("restart requested")
        raise SystemExit(0)

    # The Pico repeats the switch positions every so often, so only a change does anything.
    def on_volume(self, loud):
        if loud == self.loud:
            return
        log.info("volume switch: %s", "loud" if loud else "low")
        self.loud = loud
        self.line.gain = 1.0 if loud else self.cfg["low_volume"]
        self.screen.volume(100 if loud else round(100 * self.cfg["low_volume"]))

    def on_ringer(self, on):
        if on == self.ringer:
            return
        log.info("ringer switch: %s", "on" if on else "off")
        self.ringer = on
        self.ring_seq += 1
        if on:
            self.after(3.0, "ring", self.ring_seq)  # one ring soon, so whoever flipped it hears it work
        elif self.ringing:
            self.ringing = False
            self.line.stop()
            self.draw()

    def on_ring(self, seq):
        """Ringer on: while nobody's on the phone, it rings every so often to call people over."""
        if seq != self.ring_seq:
            return
        self.after(self.cfg["ring_every"], "ring", seq)
        if self.off_hook or self.state != "idle":
            return
        log.info("ringing")
        self.ringing = True
        self.draw()
        self.after(self.line.play("ring"), "ring_done", self.cid)

    def on_ring_done(self, cid):
        if cid == self.cid and self.ringing:
            self.ringing = False
            self.draw()

    def pick_up(self):
        if self.off_hook:
            return
        self.call_no = next_call_number(Path(getattr(self.cache, "root", HERE / "cache")) / "calls.txt")
        self.take_offset = random.randrange(len(TAKES))
        log.info("lifted (call #%d)", self.call_no)
        self.off_hook = True
        self.cid += 1
        self.attempt = 0
        self.take = None
        self.ringing = False
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
        if self.state == "listening":  # hung up instead of pressing #: whatever they said is still made
            self.hung_up_cid = self.cid
            self.line.finish_if_heard()
        if self.pending is not None:   # hung up on hold: the video still comes, and goes up on the screen
            self.orphans.add(self.pending)
        self.cid += 1
        self.line.stop()
        self.go_idle()

    def on_key(self, k):
        self.dialed = (self.dialed + k)[-len(RESET_CODE):]
        if self.dialed == RESET_CODE:
            self.dialed = ""
            return self.reset_feed()
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
            log.info("greeting done, listening (call %d, try %d)", self.cid, self.attempt + 1)
            self.state = "listening"
            self.set_view(center="Listening…  press # when you're done.")
            self.line.record(self.cid)
        elif what == "menu":
            self.bump_idle()

    def on_heard(self, cid, rec):
        if cid == self.hung_up_cid and cid != self.cid:
            self.hung_up_cid = None
            if rec["heard"]:
                log.info("hung up while talking: making it anyway")
                rid = next(self.seq)
                self.orphans.add(rid)
                threading.Thread(target=self._transcribe_and_render, args=(cid, rid, rec["wav"], rec["speech_end"]),
                                 daemon=True).start()
            return
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
        t = time.monotonic()
        try:
            prompt, endpoint = self.fal.transcribe(wav)
            secs = time.monotonic() - t
            self.stt_secs[rid] = (secs, endpoint, max(0, len(wav) - 44) / (2 * MIC_SR))
            log.info("transcribed in %.1f s by %s (%d KB of audio)", secs, endpoint.split("/")[-1], len(wav) // 1024)
        except Exception:
            return self.post(("failed", cid, rid, "Our lines are busy. Hang up and try again."))
        self.post(("prompt", cid, rid, prompt))
        if heard_nothing(prompt) or BLOCKED.search(prompt):
            return
        self.llm_bill[rid] = bill = []
        try:
            borrowed = self.fal.check_ip(prompt, bill)
        except Exception:
            return self.post(("failed", cid, rid, "Our lines are busy. Hang up and try again."))
        if not borrowed:
            return self._render(cid, rid, prompt, t0)
        # Someone else's IP: never made. The agent makes them an original with the same energy instead.
        log.info("someone else's IP (%s): %r", borrowed, prompt)
        self.post(("remixing", cid, rid))
        try:
            original = self.fal.remix(prompt, borrowed, bill)
        except Exception:
            return self.post(("ask_again", cid, rid))
        log.info("made an original instead: %r", original)
        self._render(cid, rid, original, t0, you_said=prompt)

    def on_remixing(self, cid, rid):
        if cid == self.cid and rid == self.pending:
            self.view["said"] = REMIXING

    def on_ask_again(self, cid, rid):
        """Couldn't make it original: no busy tone, just another go after the beep."""
        if cid != self.cid or rid != self.pending or not self.off_hook:
            return
        self.pending = None
        self.hold_from = None
        self.line.stop()
        self.screen.background()
        self.state = "greeting"
        self.set_view(center=ASK_AGAIN)
        self.play("beep", "greeted")

    def on_prompt(self, cid, rid, prompt):
        log.info("heard: %r", prompt)
        if cid != self.cid or rid != self.pending:
            return
        if heard_nothing(prompt):
            return self.busy("Sorry, we didn't catch that. Hang up and try again.")
        if BLOCKED.search(prompt):
            return self.busy("Let's keep it friendly. Hang up and try another one.")
        self.view["said"] = f"“{prompt}”"

    def regenerate(self, prompt, take=1, you_said=None):
        """3: the same prompt again, but a different take of it. The model reads the same words the same way every
        time (same person, same street), so each take adds its own direction. The screen and receipt keep their words."""
        self.line.hold()
        self.start_hold(time.monotonic(), said=f"“{prompt}”", keep_video=True)
        self.pending = rid = next(self.seq)
        threading.Thread(target=self._render, args=(self.cid, rid, prompt, time.monotonic(), take, you_said),
                         daemon=True).start()

    def _render(self, cid, rid, prompt, t0, take=0, you_said=None):
        t = time.monotonic()
        asked = prompt
        if take:
            asked = f"{prompt.rstrip(' .')}. {TAKES[(take - 1 + self.take_offset) % len(TAKES)]}"
            log.info("take %d: %s", take + 1, asked)
        try:
            url, model = self.fal.render(asked)
            log.info("rendered in %.1f s", time.monotonic() - t)
        except Refused:
            return self.post(("failed", cid, rid, "That one didn't go through. Hang up and try another."))
        except Exception:
            return self.post(("failed", cid, rid, "Our lines are busy. Hang up and try again."))
        self.post(("rendered", cid, rid, {"prompt": prompt, "you_said": you_said, "take": take, "url": url, "model": model,
                                          "secs": time.monotonic() - t0, "at": time.time(), "printed": False,
                                          "answered": False, "sound": False}))

    def on_rendered(self, cid, rid, take):
        log.info("video: %s, %.1f s after they stopped talking", take["model"], take["secs"])
        self.cache.add(take)  # kept for the screen between calls, even if they hung up while it rendered
        if rid in self.orphans:
            self.orphans.discard(rid)
            self.stt_secs.pop(rid, None)
            self.llm_bill.pop(rid, None)
            if self.state == "idle" and not self.off_hook:  # their caller hung up: it goes up now, then the loop
                self.take = take
                self.screen.video(take["url"], mute=True)
                self.set_view(said=f"“{take['prompt']}”", status="Pick up the phone to make your own.")
                self.after(self.cfg["attract_after"], "attract", self.cid)
            return
        if cid != self.cid or rid != self.pending or not self.off_hook:
            return
        self.pending = None
        take["rid"] = rid
        take["call"] = self.call_no
        take["costs"] = self.costs(take, rid)
        self.take = take
        self.state = "ready"
        self.hold_from = None
        self.line.stop()
        self.screen.video(take["url"], mute=True)
        self.set_view(said=f"“{take['prompt']}”", status=f"Ready in {take['secs']:.1f} s", keys={})
        self.after(8, "menu", cid, rid)  # in case the clip never reports it's playing
        self.bump_idle()

    def costs(self, take, rid):
        """The receipt's bill: (item, detail, USD). Speech-to-text is billed by compute time, so it's an estimate."""
        prices = getattr(self.fal, "prices", PRICES)
        items = []
        if rid in self.stt_secs:
            secs, endpoint, audio_secs = self.stt_secs.pop(rid)
            if endpoint == Fal.STT:  # billed per compute second
                items.append(("SPEECH TO TEXT", "wizper", secs * prices.get(endpoint, PRICES[endpoint])))
            else:  # billed per minute of audio
                items.append(("SPEECH TO TEXT", "scribe", audio_secs / 60 * prices.get(endpoint, PRICES[endpoint])))
        # the IP check (and the original made instead, if it borrowed), one line per kind and model
        merged = {}
        for label, model, usd in self.llm_bill.pop(rid, []):
            merged[(label, model)] = merged.get((label, model), 0.0) + usd
        items += sorted(((label, model, usd) for (label, model), usd in merged.items()),
                        key=lambda i: i[0] != "IP CHECK")  # the check first, then the original it made
        try:
            endpoint = self.fal.endpoint(take["model"])
        except (StopIteration, AttributeError):
            endpoint = ""
        price = prices.get(endpoint, PRICES.get(endpoint, 0))
        items.append((f"VIDEO {self.cfg['secs']} s", take["model"], price * self.cfg["secs"]))
        return items

    def on_file_loaded(self):
        t = self.take
        if self.state == "ready" and t and not t.get("loaded"):
            t["loaded"] = True
            self.after(self.cfg["menu_after"], "menu", self.cid, t["rid"])
            self.slides_later(t)

    # ----- after a few plays, the video becomes a slideshow of its own stills -----
    def slides_later(self, t):
        plays = self.cfg["plays_before_slides"]
        if plays:
            t["playing_since"] = token = time.monotonic()
            self.after(plays * self.cfg["secs"], "to_slides", t["rid"], token)

    def on_to_slides(self, rid, token):
        t = self.take
        if t and t["rid"] == rid and t.get("playing_since") == token and self.state in ("ready", "idle"):
            threading.Thread(target=self._wait_for_stills, args=(t, token), daemon=True).start()

    def _wait_for_stills(self, t, token):
        if t.get("done"):
            t["done"].wait(30)  # so this call's own stills are in the slideshow too
        self.post(("slides_ready", t["rid"], token))

    def on_slides_ready(self, rid, token):
        t = self.take
        if not (t and t["rid"] == rid and t.get("playing_since") == token and self.state in ("ready", "idle")):
            return
        slides = self.cache.slides()
        if len(slides) < 2:
            return
        t["slides"] = True
        self.slideshow = slides
        log.info("slideshow: %d stills from %d videos", len(slides), len({p for _, p in slides}))
        self.screen.slides([img for img, _ in slides], self.cfg["slide_secs"])

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
            if t.get("slides"):  # the slideshow has no sound: back to the video, from the start
                t["slides"] = False
                self.screen.video(t.get("file") or t["url"], mute=not t["sound"])
                self.slides_later(t)
            else:
                self.screen.mute(not t["sound"])
                if t["sound"]:
                    self.screen.restart_clip()
            keys["2"] = "Sound on" if t["sound"] else None
        elif k == "3":
            self.screen.mute(True)
            self.take = None
            return self.regenerate(t["prompt"], t.get("take", 0) + 1, t.get("you_said"))
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

    def reset_feed(self):
        """Staff (*123#): nothing from earlier calls on the screen. The videos go to cache/archive, not the bin."""
        n = self.cache.clear()
        log.info("feed reset: %d videos moved to cache/archive", n)
        self.take, self.attract, self.slideshow = None, [], []
        if self.off_hook:
            self.busy(f"Feed cleared ({n} videos put away). Hang up the phone.")
        else:
            self.go_idle()

    def go_idle(self):
        self.state = "idle"
        self.pending = None
        self.hold_from = None
        if self.take:
            # the clip they just made keeps playing, muted, for a little while before the loop of recent ones
            self.screen.mute(True)
            self.set_view(said=f"“{self.take['prompt']}”", status="Pick up the phone to make your own.")
            self.after(self.cfg["attract_after"], "attract", self.cid)
        else:
            self.on_attract(self.cid)

    def on_attract(self, cid):
        if cid != self.cid or self.state != "idle":
            return
        self.take = None  # their clip (or its slideshow) makes way for the loop of recent ones
        slides = self.cache.slides()
        self.attract = slides if len(slides) >= 2 else self.cache.recent()
        if len(slides) >= 2:
            self.screen.slides([img for img, _ in slides], self.cfg["slide_secs"])
            self.set_view(said=f"“{self.attract[0][1]}”", status="Pick up the phone to make your own.")
        elif self.attract:
            self.screen.playlist([f for f, _ in self.attract])
            self.set_view(said=f"“{self.attract[0][1]}”", status="Pick up the phone to make your own.")
        else:
            self.screen.background()
            self.set_view(center="Pick up the phone.")

    def on_playlist_pos(self, pos):
        # their own slideshow still lingering after a hang-up, or the loop of recent ones
        shown = self.slideshow if self.take and self.take.get("slides") else self.attract
        if self.state == "idle" and isinstance(pos, int) and 0 <= pos < len(shown):
            self.set_view(said=f"“{shown[pos][1]}”", status="Pick up the phone to make your own.")

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
        if self.ringing:
            out.append(r"{\an8\pos(960,40)\fs48\b1\bord2\shad0\3c&H000000&\c&H9EE6FF&}Incoming call · pick up the phone")
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
    threading.Thread(target=booth.fal.load_prices, daemon=True).start()
    threading.Thread(target=watch_heat, daemon=True).start()
    booth.printer = Printer(cfg)
    booth.cache = Cache(cfg["history"], secs=cfg["secs"])
    Phone(booth.post, grab=cfg["grab"])
    try:
        booth.run()
    finally:
        booth.screen.close()


if __name__ == "__main__":
    test_print() if "--test-print" in sys.argv else main()
