#!/usr/bin/env python3
"""fal call center booth, native: a real phone in, a video on the screen, no browser.

Lift the handset -> greeting and beep -> say a prompt -> "please hold" and elevator music while fal transcribes it
and renders the video -> the video plays muted on the screen -> the menu: 2 sound on/off, 3 regenerate, 0 print.

The pieces, each kept simple so it can't take the others down:
  Phone   the Pico, read straight from the kernel (evdev), so no window ever has to have focus
  Line    the handset: one always-open output stream that mixes voice lines, music and tones, and an always-open mic
  Screen  mpv full screen, driven over its IPC socket: videos stream from fal's URL, text is drawn as an OSD overlay
  Fal     fal_client: Scribe for the words, a language-model race for the IP check, H3 Max (or FLUX 3 Max) for
          the video, each with a backup
  Printer ESC/POS straight to /dev/usb/lp0: no CUPS, no driver
Everything reports to one queue and one thread runs the call (Booth.run), so there are no races between a hang-up
and a video arriving: each call has a number, and anything from an older call is ignored.

Run by run.sh, which restarts it if it ever exits. Settings: native.conf next to this file. fal key: fal.key.
"""
import base64
import glob
import heapq
import inspect
import io
import itertools
import json
import logging
import math
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
           "OUT_DEVICE": "", "IN_DEVICE": "", "LOW_VOLUME": "40", "RING_EVERY": "120", "LOCATION": "MIAMI BEACH",
           "END_PAUSE": "5", "LISTEN_MAX": "30", "SHARE_BASE": "https://fal-adobe-max.vercel.app/v"}
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
        "share_base": cfg["SHARE_BASE"].strip(),  # the event page the QR codes open; empty = the video's own link
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
        "render_timeout": 20.0,                                  # per model, then the next model (H3: ~11 s)
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
    "named, misspelled, translated OR clearly described (e.g. a yellow square sponge in brown shorts who lives in a "
    "pineapple); their signature items, logos, catchphrases and lyrics (a lightsaber, a swoosh, golden arches); any "
    "brand, company, logo, product or app (Adobe, Nike, Coca-Cola, iPhone, fal included); any real person, "
    "celebrity, athlete, politician or public figure, named or described by their role (the president, the governor "
    "of Florida); any named artist's, director's or studio's style (Pixar, Ghibli, Disney, Wes Anderson).\n"
    "Ok: original ideas, generic animals, objects, foods, drinks, places, cities, landmarks, sports, jobs, genres "
    "and moods (a sponge, a spider, a superhero I made up, a cartoon, anime style, Miami at night, a pumpkin spice "
    "latte, a burger, sneakers, a smartphone, a quarterback). Public-domain myths, legends, fairy tales and classic "
    "literary characters are ok (Zeus, King Arthur, a mermaid, Dracula, Sherlock Holmes, Alice in Wonderland), but "
    "not a studio's version of them. Words used in their ordinary meaning are ok (frozen lake, a sonic boom). "
    "If a description fits a well-known character, film, mascot, logo or person, it is not ok even without a name. "
    "When unsure, block. Judge only what is actually asked for.")
REMIX_PROMPT = (
    "You work at a video booth that only makes original ideas. The caller's idea borrows someone else's "
    "intellectual property ({what}). Swap out only the borrowed part. Replace each borrowed character, brand, logo "
    "or real person with an original one of a different kind altogether (a superhero becomes, say, a chef, a robot "
    "or an animal): not its colours, shape, outfit, powers, signature items, catchphrases or name. A named studio's, "
    "show's or artist's look becomes a plain description of a look, with no name (80s neon pastels, hand-painted "
    "animation). Drop words that point back to it (the famous theme park, the studio that made...). Keep the "
    "caller's own words for everything else: the same places (a swamp stays a swamp), action, time of day and mood; "
    "add no new places or times. Make it fun and easy to picture. Reply with the new prompt only: one sentence, "
    "under 30 words.")
# The remix tried first: keeps what made them want it (the genre, tone, joke, camera, look), so the original isn't
# bland. In a side-by-side on 6 real flagged calls (Nov 6) it held the caller's idea far better than REMIX_PROMPT,
# and 5 of 6 passed the re-check; when it doesn't, REMIX_PROMPT (6 of 6) is the fallback.
REMIX_PUNCHY = (
    "You write prompts for a video booth that only makes original ideas. The caller's idea borrows someone else's "
    "intellectual property ({what}). Keep what made them want it: the genre, the tone, the joke, the stakes, the kind "
    "of camera and the look. Replace every borrowed character, brand, logo or real person with an original one of a "
    "different kind altogether (never its colours, shape, outfit, powers, signature items, catchphrases or name), and "
    "turn a named studio's, show's or artist's style into a plain description of how that style looks and moves "
    "(only if they asked for one; a sitcom becomes a handheld office documentary, an anime studio becomes soft "
    "hand-painted animation). Otherwise pick the camera and light that suit their idea. Keep the caller's "
    "own places and action. Write it like a director's one-line shot: a specific subject doing something specific, "
    "where, with camera and light. No signs, screens, text or lettering in the shot. Vivid, funny or epic, never "
    "bland. Reply with the prompt only: one sentence, under 35 words.")
# 1 (surprise me): one of ours, for anyone stuck for an idea (and for a second silence). Originals only, Miami and
# making things, each worth trying on H3 Max before the show; none goes through the IP check.
SURPRISE = [
    "A flamingo DJ spinning records on a South Beach rooftop at sunset",
    "A manatee astronaut floating past the moon over Biscayne Bay",
    "Pastel art deco hotels on Ocean Drive blooming into giant tropical flowers",
    "A paper lantern drifting down a neon-lit Miami street on a warm night",
    "A pelican lifeguard blowing a whistle on a crowded beach",
    "A hummingbird barista pouring a cafecito at a tiny walk-up window",
    "A paintbrush painting the sunrise over the ocean, one stroke at a time",
    "A lime-green vintage convertible driving through a storm of confetti",
    "A giant mango rolling down an empty highway at golden hour",
    "A choir of parrots singing in a palm tree at dawn",
    "A pencil sketch of a chair folding itself into a real wooden chair",
    "Purple lightning drawing glowing patterns across the night sky over the bay",
    "A pastel lifeguard tower sprouting palm leaves at golden hour",
    "A robot florist arranging orchids in a sunlit greenhouse",
    "A jellyfish made of stained glass drifting through a coral reef",
    "Origami cranes taking off from a desk and flying out an open window",
    "A lighthouse keeper's cat watching a storm roll in over the sea",
    "A vintage typewriter typing out flowers instead of letters",
    "An iguana sunbathing on a lounge chair beside a turquoise pool",
    "A hot air balloon shaped like a lemon drifting over the Everglades",
    "A potter's wheel spinning a vase made of ocean water",
    "Neon palm trees swaying in a warm night breeze",
    "A tiny sailboat crossing a bathtub ocean during a bubble storm",
    "A marching band of crabs parading along the shoreline",
    "A cloud raining confetti over a rooftop party",
    "A seagull stealing a croqueta from a beach picnic, in slow motion",
    "A city of cardboard boxes coming to life at night",
    "A koi fish swimming through the sky between skyscrapers",
    "A violinist playing on a pier as dolphins jump in time",
    "A sunflower field turning to follow a disco ball",
]
STT_NAMES = {"fal-ai/elevenlabs/speech-to-text/scribe-v2": "ElevenLabs Scribe v2",
             "fal-ai/cohere-transcribe": "Cohere Transcribe"}
MODEL_NAMES = {}  # a model's name as the screen and receipt show it, when it differs: H3 Max is just "H3 Max" (Lizzie, Nov 6)


def model_name(name):
    return MODEL_NAMES.get(name, name or "")


BUSY = ("Our lines are busy.", "")
REFUSED = ("That one didn't go through.", "")
FRIENDLY = ("Let's keep it friendly.", "")
ORIGINAL_ONLY = (" Original characters and designs only: no logos, brand names or famous people. No signs, text, "
                 "lettering, neon words, patches or badges; unbranded equipment.")


# ---------- fal ----------
class Cancelled(Exception):
    """The caller pressed * to cancel: the fal job is called off and nothing is made."""


class Refused(Exception):
    """fal said no to the request itself (bad input, safety checker): the backup model would say no too."""


class Fal:
    # Measured Nov 4 on phone-quality clips: Scribe got every word right and never stalled (p50 0.8 s); Cohere is
    # quicker but slips on noisy audio, so it only joins when Scribe is slow. Wizper stalled in bursts (up to 53 s).
    STT = "fal-ai/elevenlabs/speech-to-text/scribe-v2"
    STT_BACKUP = "fal-ai/cohere-transcribe"
    STT_BACKUP_AFTER = 1.2  # s
    STT_DEADLINE = 12       # s, then the caller hears "lines are busy"
    SCRIBE_LANG = {"en": "eng", "es": "spa", "fr": "fra", "de": "deu", "pt": "por", "it": "ita", "ja": "jpn",
                   "ko": "kor", "zh": "zho"}  # Scribe wants three-letter codes
    IP_CHECK = "openrouter/router"
    # Raced from the start, first answer wins: neither let any of 46 described or misspelled IP prompts through
    # (Haiku 4.5 let 4 through), and together they answer in about 1 s.
    IP_CHECK_MODELS = ("google/gemini-3.1-flash-lite", "openai/gpt-6-luna")
    IP_CHECK_DEADLINE = 4  # s; no verdict by then and nothing is made
    # Luna's originals passed the re-check 93% of the time (Haiku's 62%); Flash Lite joins if Luna is slow.
    REMIX_MODELS = ("openai/gpt-6-luna", "google/gemini-3.1-flash-lite")
    REMIX_BACKUP_AFTER = 1.5  # s
    REMIX_DEADLINE = 12       # s, including the re-check
    LLM_NAMES = {"google/gemini-3.1-flash-lite": "Gemini 3.1 Flash Lite", "openai/gpt-6-luna": "GPT-6 Luna"}

    def _llm(self, model, system, prompt, bill, label, timeout=8, **kw):
        """One language-model call through fal. Its cost (fal reports it) goes on the receipt's bill."""
        r = self._run(self.IP_CHECK, {"model": model, "system_prompt": system, "prompt": prompt, **kw}, timeout)
        if bill is not None:
            bill.append((label, self.LLM_NAMES.get(model, model), float((r.get("usage") or {}).get("cost") or 0)))
        return r.get("output") or ""

    def __init__(self, cfg, client=None):
        self.cfg = cfg
        if client is None:
            import fal_client
            client = fal_client.SyncClient(key=fal_key(), default_timeout=20)
        self.client = client
        self.hard_timeouts = "start_timeout" in inspect.signature(client.run).parameters
        if not self.hard_timeouts:
            log.warning("fal_client too old for hard timeouts: run setup-native.sh again to update it")
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

    def endpoint(self, name):
        return next(e for n, e, _ in self.models if n == name)

    def put_first(self, flux):
        """Staff dashboard: which video model goes first (the other stays the backup)."""
        self.models.sort(key=lambda m: (m[0] == "FLUX 3 Max") != flux)

    def warm_up(self):
        """Open the connection to fal while the greeting plays, so the real requests skip DNS and TLS."""
        try:
            self.client._client.head("https://queue.fal.run", timeout=3)
        except Exception:
            pass

    def _run(self, endpoint, args, secs):
        """client.run that really gives up after `secs`. Without start_timeout, fal_client quietly retries a
        timed-out request up to 10 times (a 6 s limit once took 47 s)."""
        if self.hard_timeouts:
            return self.client.run(endpoint, args, timeout=secs, start_timeout=secs)
        return self.client.run(endpoint, args, timeout=secs)

    @staticmethod
    def _first(tries, deadline, stagger=0.0):
        """A race between (name, function) tries. The first starts at once; each next one after `stagger` s more, or
        straight away when every one started so far has failed. The first to return wins (name, result) and the rest
        finish on their own. Raises the last error if they all fail, or TimeoutError after `deadline` s."""
        answers = queue.Queue()

        def one(name, fn):
            try:
                answers.put((name, fn(), None))
            except Exception as e:
                answers.put((name, None, e))

        t0 = time.monotonic()
        started = failed = 0
        while True:
            if started < len(tries) and (started == failed or time.monotonic() - t0 >= stagger * started):
                threading.Thread(target=one, args=tries[started], daemon=True).start()
                started += 1
                continue
            wait = t0 + deadline - time.monotonic()
            if wait <= 0:
                raise TimeoutError(f"no answer in {deadline:.0f} s")
            try:
                name, result, err = answers.get(timeout=min(wait, 0.05))
            except queue.Empty:
                continue
            if err is None:
                return name, result
            log.warning("%s failed: %s", name.split("/")[-1], err)
            failed += 1
            if failed == len(tries):
                raise err

    def transcribe(self, wav_bytes):
        """Scribe first; if it hasn't answered in STT_BACKUP_AFTER s, Cohere joins the race. First good answer
        wins. Returns (text, endpoint)."""
        uri = "data:audio/x-wav;base64," + base64.b64encode(wav_bytes).decode()
        lang = self.cfg["language"]
        scribe = {"audio_url": uri, "diarize": False, "tag_audio_events": False}
        if lang in self.SCRIBE_LANG:
            scribe["language_code"] = self.SCRIBE_LANG[lang]
        cohere = {"audio_url": uri, **({} if lang == "auto" else {"language": lang})}

        def ask(endpoint, args):
            text = self._run(endpoint, args, self.STT_DEADLINE)
            return " ".join(SOUND_TAGS.sub("", text.get("text") or "").split())

        endpoint, text = self._first([(self.STT, lambda: ask(self.STT, scribe)),
                                      (self.STT_BACKUP, lambda: ask(self.STT_BACKUP, cohere))],
                                     self.STT_DEADLINE, self.STT_BACKUP_AFTER)
        return text, endpoint

    def check_ip(self, prompt, bill=None):
        """None if the prompt is an original idea, else what it borrows (a character, brand, real person...).
        Raises if no checker answered: then the booth makes nothing, rather than risk it. Each check's model and
        cost are added to `bill`, for the receipt."""
        m = IP_NAMES.search(prompt)
        if m:  # free, so it isn't on the bill
            return m.group(0)

        def ask(model):
            out = self._llm(model, IP_CHECK_PROMPT, prompt, bill, "IP CHECK", timeout=self.IP_CHECK_DEADLINE,
                            temperature=0, max_tokens=60)
            verdict = json.loads(re.search(r"\{.*?\}", out, re.S).group(0))  # no verdict: the other one decides
            return None if verdict.get("ok") is True else str(verdict.get("what") or "someone else's IP")

        return self._first([(m, lambda m=m: ask(m)) for m in self.IP_CHECK_MODELS], self.IP_CHECK_DEADLINE)[1]

    def remix(self, prompt, borrowed, bill=None):
        """The prompt turned into an original idea with the same energy, checked again before it's used. Raises
        if it can't be made original (the caller is then asked for another idea)."""
        def ask(model, brief, temperature):
            out = self._llm(model, brief.format(what=borrowed), prompt, bill, "MADE ORIGINAL",
                            timeout=self.REMIX_DEADLINE, temperature=temperature, max_tokens=100)
            original = " ".join(out.strip().strip('"“”').split())
            if not original:
                raise RuntimeError("empty remix")
            still = self.check_ip(original, bill)
            if still:
                raise RuntimeError(f"the remix still borrows {still}: {original!r}")
            return original

        def race(brief, temperature):
            return self._first([(m, lambda m=m: ask(m, brief, temperature)) for m in self.REMIX_MODELS],
                               self.REMIX_DEADLINE, self.REMIX_BACKUP_AFTER)[1]
        try:
            return race(REMIX_PUNCHY, 0.8)  # the punchier original first
        except Exception as e:
            log.info("punchy remix didn't clear (%s); trying the plain one", e)
            return race(REMIX_PROMPT, 0.7)

    def render(self, prompt, stop=None, trying=None):
        """Each model gets one go, in order. Returns (url, model name). `stop()` true (the caller pressed *) calls the
        job off; `trying(name)` hears which model is on it, so the hold screen can say so."""
        last = None
        for name, endpoint, build in self.models:
            if trying:
                trying(name)
            try:
                return self._one(endpoint, build(prompt + ORIGINAL_ONLY), stop), name
            except (Refused, Cancelled):
                raise
            except Exception as e:
                log.warning("%s failed: %s", name, e)
                last = e
        raise last or RuntimeError("no models")

    def _one(self, endpoint, args, stop=None):
        try:
            handle = self.client.submit(endpoint, args)
            deadline = time.monotonic() + self.cfg["render_timeout"]
            while True:
                s = handle.status()
                if type(s).__name__ == "Completed":
                    break
                if stop and stop():
                    try:
                        handle.cancel()
                    except Exception:
                        pass
                    raise Cancelled()
                if time.monotonic() > deadline:
                    try:
                        handle.cancel()
                    except Exception:
                        pass
                    raise TimeoutError(f"{endpoint} took over {self.cfg['render_timeout']:.0f} s")
                time.sleep(0.25)
            data = handle.get()
        except Cancelled:
            raise
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


def generating_audio(secs=0.9):
    """2 (sound on): a short run of soft rising blips, like something being made, then the video's sound comes in."""
    out = []
    for i, f in enumerate([660, 740, 830, 880, 990, 1110, 1320]):
        n = int(0.05 * SR)
        t = np.arange(n) / SR
        blip = np.sin(2 * np.pi * f * t) * np.exp(-t / 0.018) * 0.12
        out += [blip.astype(np.float32), silence(secs / 7 - 0.05)]
    return np.concatenate(out)


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
        self.meter = (0.0, False, 0.0)
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
            "generating_audio": generating_audio(),
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
        """Hung up while talking: what was said is the prompt. A quiet caller the speech detector missed gets the
        benefit of the doubt after 1.5 s (the speech model then decides if there were words)."""
        with self.lock:
            r = self.rec
            if r and (r["heard"] or r["n"] > 1.5 * MIC_SR):
                r["heard"] = True
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
            self.meter = (min(1.0, rms / max(0.06, r["floor"] * 12)), r["heard"], now - (r["last_loud"] or now))
            c = self.cfg
            if ((r["heard"] and now - r["last_loud"] > c["silence"]) or (not r["heard"] and now > c["no_speech"])
                    or now > c["listen_max"]):
                self._finish()

    def level(self):
        """(0-1 loudness of the last block, speech heard yet, seconds quiet since) while recording."""
        return self.meter if self.rec else (0.0, False, 0.0)

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


def clip_words(s, n):
    """At most n characters, cut between words, with an ellipsis (and the closing quote, if it was quoted)."""
    if len(s) <= n:
        return s
    cut = s[:n - 2].rsplit(" ", 1)[0].rstrip(",.;:-")
    return cut + ("…”" if s.endswith("”") else "…")


# ---------- the screen's look ----------
# fal's UI system at booth scale: one grid (header 96 px, stage, a key strip 174 px), a few type sizes big enough for
# a 16" screen read from across the stand, dressed like the booth's plaques: Light Sage, the wall's Model Purple and
# and the words in big HAL Timezone Mono capitals (no pixel squares or dots: Lizzie, Nov 6). Everything is one ASS overlay.
def ass_colour(hex6):
    return f"&H{hex6[4:6]}{hex6[2:4]}{hex6[0:2]}&"


WHITE, BLACK, NEON, BRONZE = ass_colour("FFFFFF"), ass_colour("000000"), ass_colour("ADFF00"), ass_colour("403700")
SAGE, LIGHT_PURPLE = ass_colour("E5ECE7"), ass_colour("D5BBFF")
MODEL_PURPLE, RED = ass_colour("AB77FF"), ass_colour("EC0648")  # Red: a fault, for the staff only
FACES = {"display": ("Focal Maxi Medium", "FocalMaxi-Medium.otf"), "body": ("Focal Text", "FocalText-Regular.otf"),
         "label": ("Focal Text Medium", "FocalText-Medium.otf"), "mono": ("HAL Timezone Mono", "HALTimezone-MonoRegular.otf")}
# token: (face, size in px, tracking in em)
TYPE = {"H1": ("mono", 260, 0.0), "H2": ("mono", 132, 0.0), "H3": ("mono", 84, 0.0), "K": ("mono", 56, 0.02),
        "D1": ("display", 300, -0.04), "D2": ("display", 150, -0.03), "D3": ("display", 96, -0.02),
        "D4": ("display", 72, -0.02), "B": ("label", 64, -0.01), "S": ("body", 48, -0.01), "M": ("mono", 44, 0.04),
        "MS": ("mono", 34, 0.06)}
SCREEN_W, SCREEN_H, MARGIN = 1920, 1080, 72
PAD_X = 56                        # floating keys and chips sit this far in from the picture's edges
HEADER, STRIP = 96, 174           # the header's height, the key strip's height
FRAME = (0, 0, SCREEN_W, SCREEN_H)  # the video on the ready screen: the whole screen, the key strip across its foot
KEY_ORDER = "123456789*0#"        # the strip lists keys in the order they sit on the phone


class Look:
    """ASS for each piece of the screen. Text is measured with the same font files libass draws, so lines wrap where
    the screen will actually break them."""

    def __init__(self, fonts=None):
        self.fonts = fonts = fonts or BRAND / "fonts"
        self._pil = {}
        self.have = {k: (fonts / f).exists() for k, (_, f) in FACES.items()}
        self.lockup = ""
        svg = BRAND / "call-a-fal-lockup.svg"
        if svg.exists():
            self.lockup = svg_to_ass(svg.read_text())

    def face(self, kind):
        return FACES[kind][0] if self.have.get(kind) else "DejaVu Sans"

    def font(self, tok):
        """A PIL font that measures like libass draws `tok`: libass sizes a font so its Windows ascent + descent
        (the OS/2 table) is the size in px; PIL sizes it by the em."""
        if tok not in self._pil:
            from PIL import ImageFont
            kind, size, _ = TYPE[tok]
            path = self.fonts / FACES[kind][1]
            path = str(path) if path.exists() else find_font(bold=kind != "body")
            asc, desc = win_metrics(path)
            self._pil[tok] = (ImageFont.truetype(path, max(1, round(size / (asc + desc)))), size * asc / (asc + desc),
                              size * desc / (asc + desc))
        return self._pil[tok][0]

    def ascent(self, tok):
        self.font(tok)
        return self._pil[tok][1]

    def descent(self, tok):
        self.font(tok)
        return self._pil[tok][2]

    def width(self, text, tok):
        _, size, track = TYPE[tok]
        return self.font(tok).getlength(text) + track * size * max(0, len(text) - 1)

    def wrap(self, text, tok, width, lines=99, balance=False):
        if balance:  # as narrow as it can go without adding a line, so no word sits alone on the last one
            n = len(self.wrap(text, tok, width))
            while 1 < n <= lines and width > 200 and len(self.wrap(text, tok, width - 20)) == n:
                width -= 20
        out, cur = [], ""
        for word in text.split():
            trial = f"{cur} {word}".strip()
            if self.width(trial, tok) <= width or not cur:
                cur = trial
            else:
                out.append(cur)
                cur = word
        out += [cur] if cur else []
        if len(out) > lines:  # the last line that fits gets the ellipsis (and keeps a closing quote)
            out = out[:lines]
            tail = out[-1]
            while tail and self.width(tail + "…”", tok) > width:
                tail = tail.rsplit(" ", 1)[0] if " " in tail else tail[:-1]
            out[-1] = tail.rstrip(",.;:-") + ("…”" if text.endswith("”") else "…")
        return out

    def text(self, x, y, text, tok, colour=BLACK, an=7, alpha=0):
        kind, size, track = TYPE[tok]
        return (f"{{\\an{an}\\pos({x:.0f},{y:.0f})\\fn{self.face(kind)}\\fs{size}\\fsp{track * size:.1f}\\b0"
                f"\\bord0\\shad0\\c{colour}\\alpha&H{alpha:02X}&}}{ass_text(text)}")

    def lines(self, x, y, lines, tok, colour=BLACK, gap=None, an=7):
        gap = gap or TYPE[tok][1] * 1.0
        return [self.text(x, y + i * gap, line, tok, colour, an) for i, line in enumerate(lines)]

    @staticmethod
    def shape(path, colour, alpha=0):
        return f"{{\\an7\\pos(0,0)\\p1\\bord0\\shad0\\c{colour}\\alpha&H{alpha:02X}&}}{path}{{\\p0}}"

    @staticmethod
    def box(x, y, w, h):
        return f"m {x:.0f} {y:.0f} l {x + w:.0f} {y:.0f} l {x + w:.0f} {y + h:.0f} l {x:.0f} {y + h:.0f} "

    def rect(self, x, y, w, h, colour, alpha=0):
        return self.shape(self.box(x, y, w, h), colour, alpha)

    @staticmethod
    def dot(x, y, d):
        """A circle of diameter d with its top left at x, y (four Bézier quarters)."""
        r, k = d / 2, d / 2 * 0.5523
        cx, cy = x + r, y + r
        return (f"m {cx:.0f} {cy - r:.0f} b {cx + k:.0f} {cy - r:.0f} {cx + r:.0f} {cy - k:.0f} {cx + r:.0f} {cy:.0f} "
                f"b {cx + r:.0f} {cy + k:.0f} {cx + k:.0f} {cy + r:.0f} {cx:.0f} {cy + r:.0f} "
                f"b {cx - k:.0f} {cy + r:.0f} {cx - r:.0f} {cy + k:.0f} {cx - r:.0f} {cy:.0f} "
                f"b {cx - r:.0f} {cy - k:.0f} {cx - k:.0f} {cy - r:.0f} {cx:.0f} {cy - r:.0f} ")

    def mat(self, colour, hole=None):
        """The whole screen in one colour, with a hole where the video shows through."""
        path = self.box(0, 0, SCREEN_W, SCREEN_H)
        if hole:  # wound the other way, so it cuts out under either fill rule
            x, y, w, h = hole
            path += f"m {x} {y} l {x} {y + h} l {x + w} {y + h} l {x + w} {y} "
        return self.shape(path, colour)

    def dotted(self, y, x0=MARGIN, x1=SCREEN_W - MARGIN, colour=BLACK):
        """fal's dotted rule: 2 px dots, 1 on and 7 off."""
        return self.shape("".join(self.box(x, y, 2, 2) for x in range(int(x0), int(x1), 16)), colour)

    def logo(self, x, y, h, colour=BLACK):
        if not self.lockup:
            return self.text(x, y, "CALL-A-FAL", "M", colour)
        k = h / 33  # the lockup SVG is 209 x 33
        return f"{{\\an7\\pos({x},{y})\\p1\\bord0\\shad0\\c{colour}\\fscx{k * 100:.1f}\\fscy{k * 100:.1f}}}{self.lockup}{{\\p0}}"

    def keycap(self, x, y, key, label, primary=False, dim=False, ink=BLACK):
        """A 120 px key: White with a 2 px stroke (Model Purple for the one to press), the digit, its label."""
        out = [self.rect(x, y, 120, 120, BLACK, 0x99 if dim else 0)]
        out.append(self.rect(x + 2, y + 2, 116, 116, MODEL_PURPLE if primary else WHITE))
        out.append(self.text(x + 60, y + 60, key, "H3", BLACK, an=5, alpha=0x99 if dim else 0))
        out.append(self.text(x + 144, y + 60, label.upper(), "K", ink, an=4, alpha=0x99 if dim else 0))
        return out, 144 + self.width(label.upper(), "K") + 80

    def pill(self, x, y, key, label, primary=False, dim=False, h=88):
        """A floating key: the digit on a square (Model Purple for the one to press, Light Purple, the keypad's own
        colour, otherwise), its
        label beside it on White. Reads on the video and on Light Sage alike. Returns (drawing, width)."""
        a = 0x99 if dim else 0
        lw = self.width(label.upper(), "M")
        w = h + 24 + lw + 28
        out = [self.rect(x, y, w, h, WHITE, a), self.rect(x, y, h, h, MODEL_PURPLE if primary else LIGHT_PURPLE, a),
               self.text(x + h / 2, y + h / 2, key, "H3", BLACK, an=5, alpha=a),
               self.text(x + h + 24, y + h / 2, label.upper(), "M", BLACK, an=4, alpha=a)]
        return out, w

    def keys(self, keys, x=PAD_X, y=SCREEN_H - PAD_X - 88):
        """Only the keys that do something now, floating bottom left, in phone order. keys = {key: (label, primary,
        dim)}. No tray: the picture runs to the edges under them."""
        out = []
        for k in sorted(keys, key=KEY_ORDER.index):
            label, primary, dim = keys[k]
            cap, w = self.pill(x, y, k, label, primary, dim)
            out += cap
            x += w + 16
        return out

    def chip(self, x, y, text, tok="MS", colour=WHITE, an=1, h=60, pad=22):
        """A White chip of mono caps over the picture. an=1 anchors its bottom left at (x, y), an=3 bottom right."""
        w = self.width(text, tok) + 2 * pad + 12  # a little extra: the curly quotes measure narrow
        x0 = x - w if an == 3 else x
        return [self.rect(x0, y - h, w, h, colour), self.text(x0 + pad, y - h / 2, text, tok, BLACK, an=4)], w

    def strip(self, keys, tray=SAGE):
        """The key strip: only the keys that do something now, in phone order. keys = {key: (label, primary, dim)}."""
        out = [self.rect(0, SCREEN_H - STRIP, SCREEN_W, STRIP, tray)]
        x = MARGIN
        for k in sorted(keys, key=KEY_ORDER.index):
            label, primary, dim = keys[k]
            cap, w = self.keycap(x, SCREEN_H - STRIP + 27, k, label, primary, dim)
            out += cap
            x += w
        return out

    def qr(self, x, y, data, size):
        """A QR code as one drawing: dark modules merged into runs, on a White tile with a 4-module quiet zone."""
        try:
            import qrcode
        except ImportError:
            return []
        q = qrcode.QRCode(border=0, error_correction=qrcode.constants.ERROR_CORRECT_L)
        q.add_data(data)
        q.make(fit=True)
        m = q.get_matrix()
        n = len(m)
        k = max(2, int(size // (n + 8)))
        pad = 4 * k
        path = []
        for r, row in enumerate(m):
            c = 0
            while c < n:
                if row[c]:
                    start = c
                    while c < n and row[c]:
                        c += 1
                    path.append(self.box(x + pad + start * k, y + pad + r * k, (c - start) * k, k))
                else:
                    c += 1
        side = n * k + 2 * pad
        return [self.rect(x, y, side, side, WHITE), self.shape("".join(path), BLACK)], side


def win_metrics(path):
    """A font's Windows ascent and descent (OS/2 usWinAscent, usWinDescent) in ems: what libass sizes by."""
    import struct
    data = Path(path).read_bytes()
    tables = {}
    for i in range(struct.unpack(">H", data[4:6])[0]):
        tag, _, off, _ = struct.unpack(">4sIII", data[12 + 16 * i:28 + 16 * i])
        tables[tag] = off
    try:
        upem = struct.unpack(">H", data[tables[b"head"] + 18:tables[b"head"] + 20])[0]
        asc, desc = struct.unpack(">HH", data[tables[b"OS/2"] + 74:tables[b"OS/2"] + 78])
        return asc / upem, desc / upem
    except (KeyError, struct.error):  # a font collection or no OS/2 table: the usual proportions
        return 0.95, 0.25


def svg_to_ass(svg):
    """An SVG's outlines (absolute M, L, C, Z, as Figma exports them) as an ASS drawing, so the real logo draws as
    vectors at any size."""
    out = []
    for d in re.findall(r'\bd="([^"]+)"', svg):
        for cmd, nums in re.findall(r"([MLCZ])([^MLCZ]*)", d):
            v = [float(n) for n in re.findall(r"-?\d*\.?\d+(?:e-?\d+)?", nums)]
            pts = [f"{v[i]:.2f} {v[i + 1]:.2f}" for i in range(0, len(v) - 1, 2)]
            if cmd == "M":
                out.append("m " + pts[0])
            elif cmd == "L":
                out += ["l " + p for p in pts]
            elif cmd == "C":
                out += ["b " + " ".join(pts[i:i + 3]) for i in range(0, len(pts) - 2, 3)]
    return " ".join(out)


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

    @staticmethod
    def font_args():
        """fal's own fonts for the words on screen (Focal, Focal Text, HAL Timezone Mono), when brand/fonts has them."""
        if (BRAND / "fonts").is_dir():
            return ["--osd-fonts-dir=" + str(BRAND / "fonts"), "--osd-font=HAL Timezone Mono"]
        return ["--osd-font=DejaVu Sans"]

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
            *self.font_args(),
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

    def frame(self, box=None):
        """Put the video in a box on the screen (x, y, w, h at 1920 x 1080), or full screen with None."""
        x, y, w, h = box or (0, 0, SCREEN_W, SCREEN_H)
        for side, v in (("left", x / SCREEN_W), ("top", y / SCREEN_H), ("right", 1 - (x + w) / SCREEN_W),
                        ("bottom", 1 - (y + h) / SCREEN_H)):
            self.cmd("set_property", f"video-margin-ratio-{side}", round(v, 4))

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
    """A plain White card for the moments there's no video (the overlay draws everything on it)."""
    if path.exists():
        from PIL import Image
        if Image.open(path).getpixel((0, 0)) == (255, 255, 255):
            return
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (SCREEN_W, SCREEN_H), (255, 255, 255)).save(path)


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
            if take.get("hidden"):  # pulled by staff while it downloaded: kept out of the loop
                dest = self.root / "archive" / "hidden"
                dest.mkdir(parents=True, exist_ok=True)
                (self.root / name).rename(dest / name)
                take["file"] = str(dest / name)
                return
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

    def hide(self, name):
        """Staff dashboard: one video out of the feed, to cache/archive/hidden/ (kept, out of the loop)."""
        with self.lock:
            keep = [h for h in self.items if h["file"] != name]
            if len(keep) == len(self.items):
                return False
            dest = self.root / "archive" / "hidden"
            dest.mkdir(parents=True, exist_ok=True)
            f = self.root / name
            if f.exists():
                f.rename(dest / f.name)
            self.items = keep
            self.index.write_text(json.dumps(self.items))
            return True

    def listing(self):
        with self.lock:
            return [{"file": h["file"], "prompt": h["prompt"], "still": (h.get("stills") or [None])[0]}
                    for h in self.items]

    def recent(self):
        with self.lock:
            return [(str(self.root / h["file"]), h["prompt"]) for h in self.items]

    def slides(self):
        """Every saved still, newest video first, as (image, prompt)."""
        with self.lock:
            return [(str(self.root / st), h["prompt"]) for h in self.items for st in h.get("stills") or []
                    if (self.root / st).exists()]


def shuffled(items, rounds=6):
    """Several shuffled passes through items, one after another, never the same one twice in a row. mpv loops the
    whole list, so the order doesn't repeat for a while and the prompt on screen still matches by position."""
    out = []
    for _ in range(rounds if len(items) > 1 else 1):
        r = random.sample(items, len(items))
        if out and len(r) > 1 and r[0] == out[-1]:
            r[0], r[-1] = r[-1], r[0]
        out += r
    if len(out) > 2 and out[0] == out[-1]:  # and none twice in a row where the loop wraps round
        out[-1], out[-2] = out[-2], out[-1]
    return out


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

# What fal charges for the show (Nov 10-12), in USD per second of video or of the caller's audio, at the settings the
# booth uses. Kept here because fal's price lookup only gives each model's cheapest resolution, not the one we use.
#   H3 Max 768P: $0.056 (30% off, Nov 1-15; $0.08 from Nov 16).  FLUX 3 Max 720p: $0.17 from Nov 8.
#   Scribe v2: $0.008 a minute.  Cohere: $0.25 an hour.
PRICES = {"minimax/h3-max/text-to-video": 0.056, "blackforestlabs/flux-3-max/text-to-video": 0.17,
          "fal-ai/elevenlabs/speech-to-text/scribe-v2": 0.008 / 60, "fal-ai/cohere-transcribe": 0.25 / 3600}


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
            except Exception:  # HAL Timezone: one weight, already dark enough for thermal paper
                pass
        return font
    if kind == "mono":
        bold = (weight or 400) >= 600
        for p, i in ((f"/usr/share/fonts/truetype/dejavu/DejaVuSansMono{'-Bold' if bold else ''}.ttf", 0),
                     ("/System/Library/Fonts/Menlo.ttc", 1 if bold else 0)):
            if os.path.exists(p):
                return ImageFont.truetype(p, size, index=i)
    return ImageFont.truetype(find_font(bold=(weight or 400) >= 600 or kind == "display"), size)


def share_url(take, cfg, words=True):
    """Where the QR on the receipt goes: the event page, with the video (and, `words`, what was
    said) in the part of the link after #, so the page needs no server and the words never reach its logs. The
    receipt keeps only a short version of the words: they're printed right there, and a shorter link is a coarser QR
    that phones read off thermal paper first time. Without a base (or for a video that isn't on fal's CDN), the video's own link."""
    from urllib.parse import quote, urlsplit
    url, base = take.get("url") or "", cfg.get("share_base", "")
    parts = urlsplit(url)
    if not base or parts.scheme != "https" or not parts.netloc.endswith("fal.media"):
        return url
    def short(text, most):  # the receipt: a short version (the full words are printed right there)
        return text if words or len(text) <= most else text[:most - 2].rsplit(" ", 1)[0].rstrip(",.;:-") + "…"
    asked = take.get("you_said") or ""
    said = short(take.get("prompt") or "", 44 if asked else 70)  # both fit, and the QR stays coarse
    q = {"m": parts.netloc + parts.path, "p": said[:140],
         "o": short(asked, 44)[:100],  # what they asked for, so the page quotes them right after an IP swap
         "l": str(cfg.get("line", "")),
         "s": f"{take.get('gen_secs', take.get('secs', 0)):.1f}", "t": str(int(take.get("at") or time.time())),
         "mo": "flux" if "flux" in str(take.get("model", "")).lower() else "h3"}
    return base + "#" + "&".join(f"{k}={quote(v, safe='/.,').replace('%20', '+')}" for k, v in q.items() if v)


def qr_image(data, target):
    """A QR code about `target` dots wide, every module a whole number of dots (at least 4, 0.5 mm on paper),
    so phones read it off thermal paper. None without the qrcode package."""
    try:
        import qrcode
    except ImportError:
        return None
    from PIL import Image
    qr = qrcode.QRCode(border=2, box_size=1, error_correction=qrcode.constants.ERROR_CORRECT_L)
    qr.add_data(data)
    qr.make(fit=True)
    k = max(4, target // (qr.modules_count + 4))
    q = qr.make_image(fill_color="black", back_color="white").convert("L")
    return q.resize((q.width * k, q.height * k), Image.NEAREST)


def sharpest_frame(src, at, vf, spread=0.3, tries=3):
    """The best frame for paper of `tries` around `at` seconds, `spread` apart: the most fine detail (the variance of
    the Laplacian: no motion blur) times the most contrast (what survives black-and-white thermal printing)."""
    from PIL import Image
    best, score = None, -1.0
    for t in (at + spread * (k - (tries - 1) / 2) for k in range(tries)):
        png = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(0.0, t):.2f}", "-i", src, "-frames:v", "1",
                              "-vf", vf, "-f", "image2pipe", "-vcodec", "png", "-"],
                             capture_output=True, timeout=30).stdout
        if not png:
            continue
        f = Image.open(io.BytesIO(png)).convert("L")
        a = np.asarray(f, np.float32)
        s = float((4 * a[1:-1, 1:-1] - a[:-2, 1:-1] - a[2:, 1:-1] - a[1:-1, :-2] - a[1:-1, 2:]).var()) * float(a.std())
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
    """The receipt, set like a till slip in the booth's brand: the lockup, when and where,
    what they said, the video's clearest frame (chipped with its model, as fal shows every output) and a strip of
    three more, how fast it was made, the itemized call, and a big QR to keep the video."""
    from PIL import Image, ImageDraw
    W, M = DOTS, 36  # width and side margin, in printer dots
    secs = cfg["secs"]
    model = model_name(take.get("model"))
    # the clearest frame of 40-60% through, full width (the video model cuts between shots, so the very middle can
    # be a crowd or a close-up), then three across, early, middle-late and near the end
    cell = (W - 2 * M - 2 * 12) // 3
    shots = [(0.5, f"scale={W}:-2:flags=lanczos", secs * 0.05, 5)]
    shots += [(f, f"scale={cell}:-2:flags=lanczos", 0.3, 3) for f in (0.15, 0.65, 0.92)]
    frames = []
    for frac, vf, spread, tries in shots:
        try:
            f = sharpest_frame(src, secs * frac, vf, spread, tries)
        except FileNotFoundError:  # no ffmpeg (a laptop test): a receipt without stills
            break
        if f:
            frames.append(f)

    # Thermal paper drops strokes one dot thin: Focal at 500 and up, mono at its one (bold enough) weight.
    said_font = brand_font("display", 40, 500)
    head = brand_font("display", 34, 500)
    big = brand_font("mono", 44, 700)
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

    img = Image.new("L", (W, 4400), 255)  # cropped to what was drawn
    d = ImageDraw.Draw(img)
    y = 30

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

    def dots(gap=24):
        """fal's dotted rule: 2-dot squares, 2 on and 2 off."""
        nonlocal y
        for x in range(M, W - M, 4):
            d.rectangle([x, y, x + 1, y + 1], fill=0)
        y += gap

    # Thermal dots spread, so the pictures are lifted to print true (35, the default, -> 1.3 for the big one, which
    # prints a little darker, and 1.5 for the strip).
    lighten = cfg.get("print_lighten", 0)
    stills = iter(frames)

    y = 24
    # the lockup (the fal mark, "fal" and CALL-A-FAL in one line), then the line it came from
    if LOGO.exists():
        mark = logo_image(W - 2 * M - 80)
        img.paste(0, ((W - mark.width) // 2, y + 40), mark)
        y += 40 + mark.height + 26
    else:
        d.text((W // 2, y + 40), "CALL-A-FAL", font=brand_font("display", 60, 700), fill=0, anchor="mt")
        y += 124
    d.text((W // 2, y), f"ZERO LATENCY LINE {str(cfg.get('line', '')).zfill(2)}", font=mono, fill=0, anchor="mt")
    y += 34
    d.text((W // 2, y), "ADOBE MAX 2026 · BOOTH 423", font=small, fill=0, anchor="mt")
    y += 50

    # when and where, set out like a till receipt
    at = time.localtime(take.get("at"))
    dots()
    row(f"CALL #{take.get('call', 0):04d}", time.strftime("%I:%M %p", at).lstrip("0"))
    row(time.strftime("%b %d %Y", at).upper(), (cfg.get("location") or "").upper())
    y += 2
    dots(30)

    # what they asked for, in the brand's headline face
    if take.get("you_said"):  # someone else's IP: what the agent made instead, said kindly
        text(["YOU ASKED FOR"], small, 28)
        text(wrap(f"“{take['you_said']}”", mono), mono, 30)
        y += 18
        text(["WE MADE YOU AN ORIGINAL"], small, 30)
    else:
        text(["ONE OF OURS" if take.get("surprise") else "YOU SAID"], small, 30)
    text(wrap(f"“{take['prompt']}”", said_font), said_font, 46)
    y += 30

    # the clearest frame, full width, nothing over it
    f = next(stills, None)
    if f is not None:
        f = thermal_photo(f, 1 + lighten / 120)
        img.paste(f, (0, y))
        y += f.height + 12
    # and three more across, like a strip of film
    strip = [next(stills, None) for _ in range(3)]
    if all(strip):
        for i, f in enumerate(strip):
            img.paste(thermal_photo(f, 1 + lighten / 70), (M + i * (cell + 12), y))
        y += strip[0].height + 30
    else:
        y += 18

    # how fast
    d.text((M, y), "GENERATED IN", font=big, fill=0)
    d.text((W - M, y), f"{take.get('gen_secs', take['secs']):.1f} s", font=big, fill=0, anchor="ra")
    y += 66
    dots(28)

    # the bill: every model the call used, and what it cost
    text(["ITEMIZED CALL"], small, 34)
    for label, detail, cost in take.get("costs") or []:
        row(label, f"${cost:.4f}")  # four decimals throughout, so the lines add up to the total
        text([model_name(detail)], small, 30, x=M + 16)
    total = sum(round(c, 4) for _, _, c in take.get("costs") or [])  # what's printed above, so it adds up
    d.line([(M, y), (W - M, y)], fill=0, width=2); y += 16
    row("TOTAL", f"${total:.4f}", font=brand_font("mono", 40, 700), gap=56)
    dots(30)

    # the way back to the video: a big QR
    q = qr_image(share_url(take, cfg, words=False), 236) if take.get("url") else None
    if q is not None:
        img.paste(q, (M, y))
        x = M + q.width + 28
        d.text((x, y), "Keep your", font=head, fill=0)
        d.text((x, y + 38), "video", font=head, fill=0)
        y += max(q.height, 190) + 26
        dots(30)

    row("Your creative direct line.", "fal.ai", font=brand_font("display", 26, 500), gap=40)
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
        path = printer_path(self.cfg)
        import sticker
        B = sys.modules[__name__]
        if sticker.speaks_zpl(path):  # a Zebra label printer: the receipt as one sticker, in its language
            data = sticker.label(path, sticker.sticker_image(take, self.cfg, src, B))
        else:  # an 80 mm receipt printer: the same design, 576 dots across
            data = escpos(sticker.sticker_image(take, self.cfg, src, B, width=DOTS))
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
        self.view = {"scene": "attract"}
        self.look = Look()
        self.surprised = []      # the last few surprise ideas, so the feed doesn't repeat itself
        self.ticking = False     # a screen tick is already queued
        self.attract = []
        self.slideshow = []
        self.stt_secs = {}     # render id -> seconds the speech-to-text took (for the bill)
        self.hung_up_cid = None  # a call hung up mid-sentence: what they'd said is still made
        self.orphans = set()     # renders whose caller hung up: shown on the screen when they arrive
        self.llm_bill = {}       # render id -> [(item, model, USD)] for the IP check and remix, for the receipt
        self.cancelled = set()   # render ids called off with *: never made, shown or kept
        self.call_no = 0
        self.online = True
        self.loud = True       # the phone's volume switch
        self.ringer = False    # the phone's ringer switch; off until the Pico says otherwise
        self.ring_seq = 0      # bumped on every ringer flip, so an old ring timer stops
        self.ringing = False
        self.paused = None     # staff dashboard: the line is out of order, with this note on screen
        self.last_take = None  # the latest video made, for a reprint from the dashboard
        self.started = time.time()
        self.stats = {"calls": 0, "videos": 0, "failed": 0, "fallbacks": 0, "pulled": 0, "prints": 0, "print_fails": 0,
                      "last_print": None, "render_secs": []}
        self.attract_pos = 0
        self.last_error = None

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
        """Staff (# held 5 s, handset on the hook): say so on screen, then power the Pi off safely so it can be
        unplugged. With the handset off the hook it's a caller holding #, so nothing happens."""
        if self.off_hook:
            return log.info("# held during a call: not shutting down")
        log.info("shutdown requested")
        self.line.cancel_recording()
        self.line.stop(fade=False)
        self.state = "busy"
        self.screen.background()
        self.set_view("staff", head="Shutting down.", sub="Unplug when the green light stops blinking.")
        threading.Timer(3, lambda: subprocess.run(["sudo", "-n", "shutdown", "-h", "now"], check=False)).start()

    def on_restart(self):
        if self.off_hook:
            return log.info("* held during a call: not restarting")
        log.info("restart requested")
        self.set_view("staff", head="Restarting…", sub="Back in a few seconds.")
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
        if self.off_hook or self.state != "idle" or self.paused is not None:
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
        if self.paused is not None:  # out of order: the busy tone and the note, no call
            self.off_hook = True
            self.cid += 1
            self.ringing = False
            log.info("lifted while the line is out of order")
            return self.busy("Line out of order.", self.paused or "Please try the other line.", retry=False)
        self.stats["calls"] += 1
        self.call_no = next_call_number(Path(getattr(self.cache, "root", HERE / "cache")) / "calls.txt")
        self.take_offset = random.randrange(len(TAKES))
        log.info("lifted (call #%d)", self.call_no)
        self.off_hook = True
        self.cid += 1
        self.attempt = 0
        self.take = None
        self.ringing = False
        self.line.stop(fade=False)
        self.screen.frame(None)
        self.screen.background()
        threading.Thread(target=self.fal.warm_up, daemon=True).start()
        self.greet()

    def greet(self, head=None, sub=None, clip="greeting"):
        """The greeting and the beep (or, with a `head`, just the beep after a message), then listening."""
        self.state = "greeting"
        self.set_view("greeting", head=head, sub=sub)
        self.play(clip if head is None else "beep", "greeted")

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
        if k == "*" and self.off_hook and self.state in ("listening", "hold"):
            return self.cancel()
        if not self.off_hook:
            return
        if k == "1" and self.state in ("greeting", "listening"):
            return self.surprise()
        if k == "*" and self.state == "busy" and self.view.get("retry"):
            self.line.stop(fade=False)
            return self.greet(head="Let's try again.")
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
            self.tape = {"from": time.monotonic(), "levels": {}}  # the listening screen's trace of their voice
            self.set_view("listening", idea=random.choice(SURPRISE))
            self.line.record(self.cid)
            self.on_tick()
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
            if self.attempt < 2:
                return self.greet(head="Didn't catch that.", sub="Try again after the beep.")
            return self.surprise()  # a second silence: nobody leaves without a video
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
            return self.post(("failed", cid, rid, BUSY))
        self.post(("stage", cid, rid, "check", STT_NAMES.get(endpoint, "")))
        self.post(("prompt", cid, rid, prompt))
        if heard_nothing(prompt) or BLOCKED.search(prompt) or rid in self.cancelled:
            return
        self.llm_bill[rid] = bill = []
        # The video starts now, alongside the IP check, so a clean prompt doesn't wait for the verdict (~1.7 s). The
        # check still decides: a flag calls the render off at fal before anything is shown, kept or printed.
        # A name on the list (SpongeBob, Marvel...) is flagged for free, so it never gets a head start.
        spec = None if IP_NAMES.search(prompt) else self._start_render(cid, rid, prompt)

        def call_off():
            if spec:
                spec["stop"] = True
        try:
            borrowed = self.fal.check_ip(prompt, bill)
        except Exception:
            call_off()
            return self.post(("failed", cid, rid, BUSY))
        if rid in self.cancelled:
            return call_off()
        if not borrowed:
            winner = next((m for label, m, _ in bill if label == "IP CHECK"), "")
            self.post(("stage", cid, rid, "checked", winner))
            if spec:
                return self._finish_render(cid, rid, prompt, t0, spec)
            return self._render(cid, rid, prompt, t0)
        call_off()  # flagged: the head start is called off at fal and never shown, kept or printed
        # Someone else's IP: never made. The agent makes them an original with the same energy instead.
        log.info("someone else's IP (%s): %r", borrowed, prompt)
        self.post(("remixing", cid, rid))
        try:
            original = self.fal.remix(prompt, borrowed, bill)
        except Exception:
            return self.post(("ask_again", cid, rid))
        log.info("made an original instead: %r", original)
        self.post(("stage", cid, rid, "remixed", next((m for label, m, _ in bill if label == "MADE ORIGINAL"), ""),
                   original))
        if rid not in self.cancelled:
            self._render(cid, rid, original, t0, you_said=prompt)

    def on_remixing(self, cid, rid):
        if cid == self.cid and rid == self.pending:
            self.view["stage"] = "remix"
            self.view["since"] = time.monotonic()

    def on_stage(self, cid, rid, stage, model="", original=""):
        """The hold screen's three steps, as each one really finishes, with the model that did it."""
        v = self.view
        if cid != self.cid or rid != self.pending or v.get("scene") != "hold":
            return
        if stage == "check":
            v["done"]["hear"] = f"HEARD YOU · {model}".rstrip(" ·").upper()
        elif stage == "checked":
            v["done"]["check"] = f"ORIGINAL ✓ · {model}".rstrip(" ·").upper()
        elif stage == "remixed":
            v["done"]["check"] = f"REMIXED ✓ · {model}".rstrip(" ·").upper()
            v["label"], v["said"] = "OUR AGENT MADE YOU AN ORIGINAL", f"“{original}”"
        elif stage == "render":
            v["backup"] = v.get("model") is not None  # a second model on the line: the first one failed
            v["model"] = model_name(model)
        if stage in ("check", "render") or stage.endswith("ed"):
            v["stage"] = {"check": "check", "checked": "render", "remixed": "render", "render": "render"}[stage]
            v["since"] = time.monotonic()
        self.draw()

    def cancel(self):
        """* while listening or on hold: nothing is made (a render already running is called off at fal), and the
        caller can try another idea after the beep, or hang up."""
        log.info("cancelled with * (%s)", self.state)
        was = self.state
        self.line.cancel_recording()
        if self.pending is not None:
            self.cancelled.add(self.pending)
        self.pending = None
        self.hold_from = None
        self.line.stop()
        self.screen.background()
        if was == "listening":
            self.greet(head="Start over.", sub="New idea after the beep.")
        else:
            self.greet(head="Cancelled.", sub="New idea after the beep.")

    def on_ask_again(self, cid, rid):
        """Couldn't make it original: no busy tone, just another go after the beep. If they've hung up there's no one
        to ask, so the video is one of ours: whoever hangs up mid-call still gets one."""
        if rid in self.orphans:
            idea = random.choice(SURPRISE)
            log.info("hung up and couldn't make it original: one of ours instead, %r", idea)
            threading.Thread(target=self._render, args=(cid, rid, idea, time.monotonic(), 0, None, True),
                             daemon=True).start()
            return
        if cid != self.cid or rid != self.pending or not self.off_hook:
            return
        self.pending = None
        self.hold_from = None
        self.line.stop()
        self.screen.background()
        self.greet(head="Let's try one of yours.", sub="That one belongs to someone else.")

    def on_prompt(self, cid, rid, prompt):
        log.info("heard: %r", prompt)
        if cid != self.cid or rid != self.pending:
            return
        if heard_nothing(prompt):
            self.pending = self.hold_from = None
            self.line.stop(fade=False)
            return self.greet(head="Didn't catch that.", sub="Try again after the beep.")
        if BLOCKED.search(prompt):
            return self.busy(*FRIENDLY)
        self.view["said"] = f"“{prompt}”"
        self.draw()

    def regenerate(self, prompt, take=1, you_said=None):
        """3: the same prompt again, but a different take of it. The model reads the same words the same way every
        time (same person, same street), so each take adds its own direction. The screen and receipt keep their words."""
        self.line.hold()
        self.start_hold(time.monotonic(), said=f"“{prompt}”", keep_video=True, label=f"TAKE {take + 1} OF 3",
                        stage="render", done={"hear": "SAME IDEA", "check": "ORIGINAL ✓"}, take=take)
        self.pending = rid = next(self.seq)
        threading.Thread(target=self._render, args=(self.cid, rid, prompt, time.monotonic(), take, you_said),
                         daemon=True).start()

    def surprise(self):
        """1: one of our ideas, straight to hold. Curated, so no speech-to-text and no IP check."""
        fresh = [i for i in SURPRISE if i not in self.surprised] or SURPRISE
        idea = random.choice(fresh)
        self.surprised = (self.surprised + [idea])[-len(SURPRISE) // 2:]
        log.info("surprise: %r", idea)
        self.line.cancel_recording()
        self.line.stop(fade=False)
        self.line.hold()
        self.start_hold(time.monotonic(), said=f"“{idea}”", label="ONE OF OURS", stage="render",
                        done={"hear": "SURPRISE IDEA", "check": "CURATED ✓ · ONE OF OURS"})
        self.pending = rid = next(self.seq)
        threading.Thread(target=self._render, args=(self.cid, rid, idea, time.monotonic(), 0, None, True),
                         daemon=True).start()

    def _start_render(self, cid, rid, prompt):
        """The video for `prompt`, started in the background before it's cleared. Nothing reaches the screen until
        _finish_render says so; spec["stop"] = True calls it off at fal."""
        spec = {"stop": False, "passed": False, "model": None, "out": None, "t": time.monotonic(),
                "done": threading.Event()}

        def trying(name):
            spec["model"] = name
            if spec["passed"]:
                self.post(("stage", cid, rid, "render", name))

        def go():
            try:
                spec["out"] = self.fal.render(prompt, stop=lambda: spec["stop"] or rid in self.cancelled, trying=trying)
                log.info("rendered in %.1f s (started beside the IP check)", time.monotonic() - spec["t"])
            except Exception as e:
                spec["out"] = e
            finally:
                spec["done"].set()

        threading.Thread(target=go, daemon=True).start()
        return spec

    def _finish_render(self, cid, rid, prompt, t0, spec):
        """The check passed: the head-start video is theirs once it lands."""
        spec["passed"] = True
        if spec["model"]:
            self.post(("stage", cid, rid, "render", spec["model"]))
        spec["done"].wait()
        out = spec["out"]
        if isinstance(out, Cancelled):
            return log.info("render called off at fal")
        if isinstance(out, Refused):
            return self.post(("failed", cid, rid, REFUSED))
        if isinstance(out, Exception) or out is None:
            return self.post(("failed", cid, rid, BUSY))
        url, model = out
        now = time.monotonic()
        self.post(("rendered", cid, rid, {"prompt": prompt, "you_said": None, "take": 0, "url": url, "model": model,
                                          "secs": now - t0, "gen_secs": now - spec["t"], "at": time.time(),
                                          "printed": False, "answered": False, "sound": False, "surprise": False}))

    def _render(self, cid, rid, prompt, t0, take=0, you_said=None, surprise=False):
        t = time.monotonic()
        asked = prompt
        if take:
            asked = f"{prompt.rstrip(' .')}. {TAKES[(take - 1 + self.take_offset) % len(TAKES)]}"
            log.info("take %d: %s", take + 1, asked)
        try:
            url, model = self.fal.render(asked, stop=lambda: rid in self.cancelled,
                                         trying=lambda name: self.post(("stage", cid, rid, "render", name)))
            log.info("rendered in %.1f s", time.monotonic() - t)
        except Cancelled:
            return log.info("render called off at fal")
        except Refused:
            return self.post(("failed", cid, rid, REFUSED))
        except Exception:
            return self.post(("failed", cid, rid, BUSY))
        self.post(("rendered", cid, rid, {"prompt": prompt, "you_said": you_said, "take": take, "url": url, "model": model,
                                          "secs": time.monotonic() - t0, "gen_secs": time.monotonic() - t,
                                          "at": time.time(), "printed": False,
                                          "answered": False, "sound": False, "surprise": surprise}))

    def on_rendered(self, cid, rid, take):
        if rid in self.cancelled:  # finished just as * was pressed: dropped
            return
        log.info("video: %s, %.1f s after they stopped talking", take["model"], take["secs"])
        self.stats["videos"] += 1
        if self.fal and take.get("model") != self.fal.models[0][0]:
            self.stats["fallbacks"] += 1
        self.stats["render_secs"] = (self.stats["render_secs"] + [round(take["secs"], 1)])[-50:]
        self.last_take = take
        self.cache.add(take)  # kept for the screen between calls, even if they hung up while it rendered
        if rid in self.orphans:
            self.orphans.discard(rid)
            self.stt_secs.pop(rid, None)
            self.llm_bill.pop(rid, None)
            if self.state == "idle" and not self.off_hook:  # their caller hung up: it goes up now, then the loop
                self.take = take
                self.screen.frame(FRAME)
                self.screen.video(take["url"], mute=True)
                self.set_view("after")
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
        self.screen.frame(FRAME)
        self.screen.video(take["url"], mute=True)
        self.set_view("ready", keys={})
        self.after(8, "menu", cid, rid)  # in case the clip never reports it's playing
        self.bump_idle()

    def costs(self, take, rid):
        """The receipt's bill: (item, detail, USD)."""
        items = []
        if rid in self.stt_secs:
            secs, endpoint, audio_secs = self.stt_secs.pop(rid)  # both speech models bill by the second of audio
            items.append(("SPEECH TO TEXT", STT_NAMES.get(endpoint, endpoint), audio_secs * PRICES.get(endpoint, 0)))
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
        price = PRICES.get(endpoint, 0)
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
        if t and t["rid"] == rid and t.get("playing_since") == token and self.state == "idle":
            threading.Thread(target=self._wait_for_stills, args=(t, token), daemon=True).start()

    def _wait_for_stills(self, t, token):
        if t.get("done"):
            t["done"].wait(30)  # so this call's own stills are in the slideshow too
        self.post(("slides_ready", t["rid"], token))

    def on_slides_ready(self, rid, token):
        t = self.take
        if not (t and t["rid"] == rid and t.get("playing_since") == token and self.state == "idle"):
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
        self.stats["failed"] += 1
        self.last_error = {"at": time.time(), "what": " ".join(m for m in message if m)}
        if cid == self.cid and rid == self.pending and self.off_hook:
            self.busy(*message)

    # ----- the menu -----
    def act(self, k):
        t = self.take
        t["answered"] = True
        self.line.stop()  # cuts the menu off
        self.line.key(k)
        keys = self.view["keys"]
        if k == "2":
            t["sound"] = not t["sound"]
            if t["sound"]:  # a moment of "making the sound" in the earpiece, then the video comes back with it
                keys["2"] = "Generating audio…"
                self.line.play("generating_audio")
                self.after(len(self.line.sounds["generating_audio"]) / SR, "sound_on", self.cid, t)
            elif t.get("slides"):
                keys["2"] = None
            else:
                self.screen.mute(True)
                keys["2"] = None
        elif k == "3":
            if t.get("take", 0) >= 2:  # three takes a call, so the next caller gets a turn
                keys["3"] = "3 takes max"
                return self.draw()
            self.screen.mute(True)
            self.take = None
            return self.regenerate(t["prompt"], t.get("take", 0) + 1, t.get("you_said"))
        elif k == "0" and not t["printed"]:
            t["printed"] = True
            keys["0"] = "Printing…"
            threading.Thread(target=self._print, args=(self.cid, t), daemon=True).start()
        self.draw()

    def on_sound_on(self, cid, t):
        if cid != self.cid or self.take is not t or not t["sound"] or self.state != "ready":
            return
        if t.get("slides"):  # the slideshow has no sound: back to the video, from the start
            t["slides"] = False
            self.screen.video(t.get("file") or t["url"], mute=False)
            self.slides_later(t)
        else:
            self.screen.mute(False)
            self.screen.restart_clip()
        self.view["keys"]["2"] = "Sound on"
        self.draw()

    def _print(self, cid, take):
        try:
            self.printer.print(take)
            self.post(("printed", cid, take, True))
        except Exception as e:
            log.error("print failed: %s", e)
            self.post(("printed", cid, take, False))

    def on_printed(self, cid, take, ok):
        self.stats["prints" if ok else "print_fails"] += 1
        if ok:
            self.stats["last_print"] = time.time()
        if not ok:
            self.last_error = {"at": time.time(), "what": "Print failed"}
        if not ok:
            take["printed"] = False  # 0 again tries again
        if cid == self.cid and self.take is take and self.state == "ready":
            self.view["keys"]["0"] = "Printed ✓" if ok else "Printer's out"
            self.draw()

    # ----- busy, idle -----
    def busy(self, head, sub="", retry=True):
        """Something went wrong (or the call's over): the busy tone, and on screen what happened and what to do.
        With `retry`, * starts the call over."""
        self.state = "busy"
        self.pending = None
        self.hold_from = None
        self.line.cancel_recording()
        self.line.stop(fade=False)
        self.line.play("busy")
        self.screen.frame(None)
        self.screen.background()
        self.set_view("message", head=head, sub=sub, retry=retry)

    def bump_idle(self):
        self.last_key = time.monotonic()
        self.after(self.cfg["off_hook_idle"], "idle_check", self.cid, self.last_key)

    def on_idle_check(self, cid, armed):
        # After the menu, a handset left off the hook gets the busy signal, like a real line.
        if cid == self.cid and self.off_hook and self.state == "ready" and armed >= self.last_key:
            self.busy("Thanks for calling.", "Please hang up the phone.", retry=False)

    def reset_feed(self):
        """Staff (*123#): nothing from earlier calls on the screen. The videos go to cache/archive, not the bin."""
        n = self.cache.clear()
        log.info("feed reset: %d videos moved to cache/archive", n)
        self.take, self.attract, self.slideshow = None, [], []
        if self.off_hook:
            self.busy(f"Feed cleared ({n} videos put away).", "Hang up the phone.", retry=False)
            self.set_view("staff", head=f"Feed cleared ({n} videos put away).", sub="Hang up the phone.")
        else:
            self.go_idle()

    # ----- the staff dashboard (control.py talks to this over a local socket) -----
    def status(self):
        t = self.take or {}
        secs = self.stats["render_secs"]
        return {
            "line": self.cfg["line"], "state": self.state, "scene": self.view.get("scene"), "off_hook": self.off_hook,
            "paused": self.paused, "online": self.online, "ringer": self.ringer, "loud": self.loud,
            "call_no": self.call_no, "prompt": t.get("prompt") or (self.view.get("said") or "").strip("“”"),
            "first_model": self.fal.models[0][0] if self.fal else None,
            "stats": {k: v for k, v in self.stats.items() if k != "render_secs"},
            "render_avg": round(sum(secs) / len(secs), 1) if secs else None,
            "render_last": secs[-1] if secs else None,
            "last_error": self.last_error, "app_up": round(time.time() - self.started),
            "can_reprint": bool(self.last_take), "feed": len(self.cache.items) if self.cache else 0,
        }

    def on_control(self, cmd, args, reply):
        """One staff command from the dashboard. `reply` gets a dict back (ok + what happened)."""
        def done(ok=True, **kw):
            reply.put({"ok": ok, **kw})
        log.info("dashboard: %s %s", cmd, args or "")
        if cmd == "status":
            return done(status=self.status())
        if cmd == "pause":
            self.paused = str(args.get("note") or "")[:80]
            self.ringing = False
            if not self.off_hook:
                self.draw()
            return done(paused=self.paused)
        if cmd == "resume":
            self.paused = None
            if self.off_hook and self.state == "busy" and self.view.get("head") == "Line out of order.":
                self.set_view("message", head="Line open again.", sub="Hang up, then pick up.", retry=False)
            elif not self.off_hook:
                self.go_idle()
            return done()
        if cmd == "feed":
            return done(feed=self.cache.listing())
        if cmd == "hide":
            ok = self.cache.hide(str(args.get("file", "")))
            if ok and not self.off_hook and self.state == "idle" and not self.take:
                self.on_attract(self.cid)
            return done(ok)
        if cmd == "clear_feed":
            if self.off_hook:
                return done(False, error="Someone's on the phone")
            self.reset_feed()
            return done()
        if cmd == "pull":
            return done(**self.pull())
        if cmd == "next_clip":
            self.on_next_clip()
            return done()
        if cmd == "ring":
            if self.off_hook or self.state != "idle" or self.paused is not None:
                return done(False, error="Only when the line is free")
            self.ringing = True
            self.draw()
            self.after(self.line.play("ring"), "ring_done", self.cid)
            return done()
        if cmd == "model":
            self.fal.put_first(args.get("first") == "flux")
            return done(first_model=self.fal.models[0][0])
        if cmd == "reprint":
            if not self.last_take:
                return done(False, error="Nothing made yet")
            threading.Thread(target=self._print, args=(-1, self.last_take), daemon=True).start()
            return done()
        if cmd == "test_print":
            threading.Thread(target=test_print, daemon=True).start()
            return done()
        done(False, error="unknown command")

    def pull(self):
        """Staff kill switch: whatever's on the TV right now comes off. Mid-call, the call ends (a render still running
        is called off and never shown or kept) and the caller is asked to hang up; idle, the video showing is hidden
        from the feed."""
        def hide_take(t):
            t["hidden"] = True  # a download still running drops it when it lands
            if t.get("file"):
                self.cache.hide(Path(t["file"]).name)
        self.stats["pulled"] += 1
        if self.off_hook:
            log.warning("dashboard pulled the call (%s)", self.state)
            if self.pending is not None:
                self.cancelled.add(self.pending)
            if self.take:
                hide_take(self.take)
            self.take = None
            self.busy("Thanks for calling.", "Please hang up the phone.", retry=False)
            return {"what": "Call ended"}
        if self.take:  # their clip still up after a hang-up
            hide_take(self.take)
            self.take = None
            self.state = "idle"
            self.on_attract(self.cid)
            return {"what": "Video hidden"}
        if self.attract and 0 <= self.attract_pos < len(self.attract):
            name = Path(self.attract[self.attract_pos][0]).name
            self.cache.hide(name)
            self.on_attract(self.cid)
            return {"what": "Video hidden"}
        return {"what": "Nothing on screen"}

    def go_idle(self):
        self.state = "idle"
        self.pending = None
        self.hold_from = None
        if self.take:
            # the clip they just made keeps playing, muted, for a little while before the loop of recent ones
            self.screen.mute(True)
            self.set_view("after")
            self.slides_later(self.take)  # it can turn into its own stills now nobody's watching it with sound
            self.after(self.cfg["attract_after"], "attract", self.cid)
        else:
            self.on_attract(self.cid)

    def on_attract(self, cid):
        if cid != self.cid or self.state != "idle":
            return
        self.take = None  # their clip (or its slideshow) makes way for a shuffle of earlier videos
        self.attract = shuffled(self.cache.recent())
        self.screen.frame(None)
        if self.attract:
            self.screen.playlist([f for f, _ in self.attract])
            self.set_view("attract", said=f"“{self.attract[0][1]}”")
        else:
            self.screen.background()
            self.set_view("attract", said="")

    def on_playlist_pos(self, pos):
        # their own slideshow still lingering after a hang-up, or the shuffle of earlier videos
        shown = self.slideshow if self.take and self.take.get("slides") else self.attract
        if isinstance(pos, int):
            self.attract_pos = pos
        if self.state == "idle" and isinstance(pos, int) and 0 <= pos < len(shown):
            if self.view.get("scene") == "attract":
                self.view["said"] = f"“{shown[pos][1]}”"
                self.draw()

    def on_next_clip(self):
        if self.state == "idle" and self.attract:
            self.screen.cmd("playlist-next", "force")

    def on_screen_restarted(self):
        if self.state == "ready" and self.take:
            self.screen.frame(FRAME)
            self.screen.video(self.take["url"], mute=not self.take["sound"])
        elif self.state == "idle":
            return self.on_attract(self.cid)
        else:
            self.screen.frame(None)
            self.screen.background()
        self.draw()

    # ----- on hold -----
    def start_hold(self, t0, said, keep_video=False, label="YOU SAID", stage="hear", done=None, take=0):
        self.state = "hold"
        self.hold_from = t0
        self.screen.frame(None)
        if not keep_video:
            self.screen.background()
        self.set_view("hold", said=said, label=label, stage=stage, done=dict(done or {}), since=time.monotonic(),
                      model=None)
        self.on_tick()

    def on_tick(self):
        """10 times a second while someone's talking (the meter) or on hold (the clock)."""
        if self.state == "listening" or (self.state == "hold" and self.hold_from is not None):
            if self.ticking:
                return
            self.ticking = True
            self.after(0.1, "tick_next")
            self.draw()

    def on_tick_next(self):
        self.ticking = False
        self.on_tick()

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
    # The screen is a sign, not a page: one big thing per state, and only the keys that work right now. Everything
    # else (the models, the bill, the links) lives on the receipt and the event page.
    def set_view(self, scene, **kw):
        self.view = {"scene": scene, **kw}
        self.draw()

    def draw(self):
        v = self.view
        scene = "ringing" if self.ringing and v.get("scene") == "attract" else v.get("scene", "attract")
        if self.paused is not None and scene in ("attract", "ringing", "after"):
            out = self._plaque(self.look, "Out of order.", self.paused or "Please try the other line.")
        else:
            out = getattr(self, "_scene_" + scene)(v, self.look)
        if not self.online:  # for the staff: this line can't reach fal
            L = self.look  # fal's chip: White, Black mono, a Red dot for the fault
            out.append(L.rect(SCREEN_W - 300, 0, 300, 56, WHITE))
            out.append(L.shape(L.dot(SCREEN_W - 270, 18, 20), RED))
            out.append(L.text(SCREEN_W - 136, 28, "OFFLINE", "MS", BLACK, an=5))
        self.screen.text("\n".join(out))

    def _plaque(self, L, head, sub="", tok="H2", y=None):
        """No video yet: Light Sage, the words big and centred in mono caps, nothing else."""
        out = [L.mat(SAGE)]
        lines = L.wrap(head.upper(), tok, SCREEN_W - 480, lines=3, balance=True)
        size = TYPE[tok][1]
        block = size * len(lines) + (40 + 56 if sub else 0)
        y = (SCREEN_H - 200 - block) / 2 + 40 if y is None else y
        out += L.lines(SCREEN_W / 2, y, lines, tok, gap=size, an=8)
        if sub:
            out += L.lines(SCREEN_W / 2, y + size * len(lines) + 40, L.wrap(sub.upper(), "M", SCREEN_W - 480, lines=2),
                           "M", gap=56, an=8)
        return out

    def _pick_up(self, L, label="PICK UP"):
        """The one thing to do on a free line: a Model Purple key, bottom right."""
        tok = "H3" if len(label) < 12 else "K"
        h, pad = (128, 48) if tok == "H3" else (96, 36)
        w = L.width(label, tok) + 2 * pad
        x, y = SCREEN_W - PAD_X - w, SCREEN_H - PAD_X - h
        return [L.rect(x, y, w, h, MODEL_PURPLE), L.text(x + w / 2, y + h / 2, label, tok, an=5)]

    def _said(self, L, said, room):
        """What was said, as one quiet White chip bottom left, cut to fit."""
        said = (said or "").upper()
        if not said:
            return []
        lines = L.wrap(said, "M", room, lines=2, balance=True)
        w = max(L.width(l, "M") for l in lines) + 2 * 24 + 12
        h = 56 * len(lines) + 32
        y = SCREEN_H - PAD_X - h
        return [L.rect(PAD_X, y, w, h, WHITE)] + L.lines(PAD_X + 24, y + 16, lines, "M", gap=56)

    def _scene_attract(self, v, L):
        """Free line: what this phone has made, edge to edge, what was said in a small chip, and Pick up."""
        if not self.attract:  # nothing made yet: the promise
            return self._plaque(L, "Pick up.", "Say an idea, get a 10-second video.", tok="H1") + self._pick_up(L)
        return self._said(L, v.get("said"), 980) + self._pick_up(L)

    def _scene_ringing(self, v, L):
        return self._plaque(L, "Pick up.", "Incoming call", tok="H1") + self._pick_up(L)

    def _scene_greeting(self, v, L):
        out = self._plaque(L, v.get("head") or "Leave your prompt after the beep.", v.get("sub", ""))
        out += L.keys({"1": ("Surprise me", False, False)})
        return out

    def _scene_listening(self, v, L):
        """Recording: LISTENING, big, over a tape of their voice, like the answering machine the greeting sets up. Each
        bar is their loudest half second, laid down left to right across the LISTEN_MAX track, so the track filling is
        also the time limit. Before they speak, an empty track and a breathing playhead; in a pause, the tape fades
        toward the auto-send (END_PAUSE), and snaps back the moment they talk again."""
        level, heard, quiet = self.line.level() if self.line else (0.0, False, 0.0)
        out = [L.mat(SAGE), L.text(SCREEN_W / 2, 150, "LISTENING", "H1", an=8)]
        now = time.monotonic()
        tape = getattr(self, "tape", None) or {"from": now, "levels": {}}
        step, span, mid, tall = 0.5, 1200, 600, 260
        n = max(1, round(self.cfg["listen_max"] / step))
        x0, pitch = (SCREEN_W - span) / 2, span / n
        w = max(4.0, pitch * 0.62)
        k = min(n - 1, int((now - tape["from"]) / step))
        tape["levels"][k] = max(tape["levels"].get(k, 0.0), level)
        pause = heard and quiet > 1.0
        fade = int(0xC0 * min(1.0, (quiet - 1.0) / max(0.1, self.cfg["silence"] - 1.0))) if pause else 0
        bars = [12 + tall * min(1.0, tape["levels"].get(i, 0.0) * 1.4) for i in range(k)]
        if bars:
            out.append(L.shape("".join(L.box(x0 + i * pitch, mid - h / 2, w, h) for i, h in enumerate(bars)),
                               MODEL_PURPLE, fade))
        tx = x0 + k * pitch
        out.append(L.rect(tx, mid - 3, x0 + span - tx, 6, MODEL_PURPLE, 0x99))  # the track still to fill
        ph = 20 if pause else 20 + 24 * (0.5 + 0.5 * math.sin(now * 3)) + tall * min(1.0, level * 1.4)
        out.append(L.rect(tx, mid - ph / 2, w, ph, MODEL_PURPLE, fade))  # the playhead, on the voice
        out += L.keys({"1": ("Surprise me", False, False), "*": ("Start over", False, False),
                       "#": ("Send", True, False)})
        return out

    def _scene_hold(self, v, L):
        """On hold: a giant clock, their words small above it, and one line saying which model has the call."""
        now = time.monotonic()
        secs = now - (self.hold_from or now)
        out = [L.mat(SAGE)]
        said = (v.get("said") or "Hearing you…").upper()
        out += L.lines(SCREEN_W / 2, 230, L.wrap(said, "K", SCREEN_W - 560, lines=2, balance=True), "K", gap=68, an=8)
        out.append(L.text(SCREEN_W / 2, 390, f"{secs:4.1f}s".strip(), "H1", an=8))  # mono: the digits never jitter
        stage, model = v.get("stage", "hear"), v.get("model") or ""
        doing = {"hear": "Hearing you", "check": "Checking it's original",
                 "remix": "Making it an original", "render": "Rendering"}[stage]
        if stage == "render" and model:
            doing = f"{'Backup line' if v.get('backup') else 'Rendering'} · {model_name(model)}"
        out.append(L.text(SCREEN_W / 2, 690, (doing + "…").upper(), "M", an=8))
        out += L.keys({"*": ("Start over", False, False)})
        return out

    def _scene_ready(self, v, L):
        """Their video, edge to edge, nothing over it but the three keys that work."""
        keys = v.get("keys") or {}
        return L.keys({"2": (keys.get("2") or "Sound", False, False),
                       "3": (keys.get("3") or "Another take", False, keys.get("3") == "3 takes max"),
                       "0": (keys.get("0") or "Print", not keys.get("0"), keys.get("0") == "Printed ✓")})

    def _scene_after(self, v, L):
        """Their caller hung up: their video stays a while, edge to edge, before the feed comes back."""
        if not self.take:
            return self._scene_attract(v, L)
        return self._pick_up(L, "PICK UP TO MAKE YOUR OWN")

    def _scene_message(self, v, L):
        out = self._plaque(L, v.get("head", ""), v.get("sub", ""))
        if v.get("retry"):
            out += L.keys({"*": ("Try again", True, False)})
        return out

    def _scene_staff(self, v, L):
        return self._plaque(L, v.get("head", ""), v.get("sub", ""))


CONTROL_SOCK = "/tmp/fal-booth-ctl.sock"


def serve_control(booth, path=CONTROL_SOCK):
    """The staff dashboard's way in (control.py, on this Pi only): one JSON line per connection,
    {"cmd": ..., "args": {...}}, answered with one JSON line. Commands run on the booth's own loop."""
    try:
        os.unlink(path)
    except OSError:
        pass
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    os.chmod(path, 0o600)
    srv.listen(8)

    def one(conn):
        with conn:
            try:
                conn.settimeout(5)
                msg = json.loads(conn.makefile().readline() or "{}")
                reply = queue.Queue()
                booth.post(("control", str(msg.get("cmd", "")), msg.get("args") or {}, reply))
                out = reply.get(timeout=4)
            except queue.Empty:
                out = {"ok": False, "error": "The booth is busy; try again"}
            except Exception as e:
                out = {"ok": False, "error": str(e)}
            try:
                conn.sendall((json.dumps(out) + "\n").encode())
            except OSError:
                pass

    def loop():
        while True:
            conn, _ = srv.accept()
            threading.Thread(target=one, args=(conn,), daemon=True).start()

    threading.Thread(target=loop, daemon=True).start()


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
    threading.Thread(target=watch_heat, daemon=True).start()
    booth.printer = Printer(cfg)
    booth.cache = Cache(cfg["history"], secs=cfg["secs"])
    Phone(booth.post, grab=cfg["grab"])
    try:
        serve_control(booth)
    except OSError as e:
        log.warning("no staff dashboard socket: %s", e)
    try:
        booth.run()
    finally:
        booth.screen.close()


if __name__ == "__main__":
    test_print() if "--test-print" in sys.argv else main()
