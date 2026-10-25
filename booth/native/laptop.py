#!/usr/bin/env python3
"""The booth on a laptop, for test calls before the Pi is ready: the same call (booth.py's Booth, Line and Fal),
with the screen in a browser tab instead of mpv. The Pico (or the laptop keyboard) types into that tab.

    BOOTH_OUT_DEVICE="External Headphones" BOOTH_IN_DEVICE="USB Audio" python3 laptop.py

then open http://localhost:8765 and click into it once. Keys: F8 lift, F9 hang up (or Space), 0-9 * #.
No printer: 0 saves the receipt as a PNG in cache/ and opens it (needs ffmpeg for the stills; without it, text only).
The fal key comes from FAL_KEY, fal.key next to this file, or ~/.fal/.env.txt (a line fal_key=...).
"""
import json
import logging
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import booth as B

PORT = 8765
log = logging.getLogger("booth")


def load_key():
    if os.environ.get("FAL_KEY") or (B.HERE / "fal.key").exists():
        return
    env = Path.home() / ".fal" / ".env.txt"
    if env.exists():
        for line in env.read_text().splitlines():
            k, _, v = line.partition("=")
            if k.strip().lower() == "fal_key" and v.strip():
                os.environ["FAL_KEY"] = v.strip().strip('"')


class BrowserScreen:
    """Booth's Screen, drawn by the page below: it polls /state and posts back what the video did."""

    def __init__(self, post):
        self.post = post
        self.lock = threading.Lock()
        self.booth = None
        self.state = {"src": None, "muted": True, "loop": True, "seq": 0, "restart": 0, "playlist": [], "pos": 0}

    def _set(self, **kw):
        with self.lock:
            self.state.update(kw)
            self.state["seq"] += 1

    def background(self):
        self._set(src=None, playlist=[])

    def video(self, src, mute=True):
        self._set(src=src, muted=mute, loop=True, playlist=[])

    def playlist(self, files):
        urls = ["/media/" + Path(f).name for f in files]
        self._set(src=urls[0] if urls else None, muted=True, loop=False, playlist=urls, pos=0)
        self.post(("playlist_pos", 0))

    def mute(self, on):
        with self.lock:
            self.state["muted"] = on

    def restart_clip(self):
        with self.lock:
            self.state["restart"] += 1

    def cmd(self, *args):
        if args and args[0] == "playlist-next":
            self.next_clip()

    def next_clip(self):
        with self.lock:
            pl = self.state["playlist"]
            if not pl:
                return
            pos = (self.state["pos"] + 1) % len(pl)
            self.state.update(pos=pos, src=pl[pos])
            self.state["seq"] += 1
        self.post(("playlist_pos", pos))

    def text(self, _ass):
        pass  # the page draws booth.view itself

    def snapshot(self):
        with self.lock:
            s = dict(self.state)
        b = self.booth
        s.update(view=b.view if b else {}, online=b.online if b else True, line=b.cfg["line"] if b else "",
                 state=b.state if b else "")
        return s

    def close(self):
        pass


class LaptopPrinter:
    """0 on a laptop: the receipt opens as a picture instead of printing."""

    def __init__(self, cfg):
        self.cfg = cfg

    def print(self, take):
        if take.get("done"):
            take["done"].wait(20)
        img = B.receipt_image(take, self.cfg, take.get("file") or take["url"])
        out = B.HERE / "cache" / f"receipt-{int(time.time())}.png"
        img.save(out)
        subprocess.run(["open", str(out)], check=False)


PAGE = r"""<!doctype html><html><head><meta charset="utf-8"><title>fal call center · booth test</title>
<style>
html,body{margin:0;height:100%;background:#0b0b0f;color:#fff;font-family:-apple-system,Helvetica,sans-serif;overflow:hidden}
video{position:absolute;inset:0;width:100%;height:100%;object-fit:contain;background:#0b0b0f}
#top{position:absolute;left:3vw;top:3vh;font-size:1.6vw;color:#a0a0a8}
#dot{color:#5bd16a}#dot.off{color:#ff4b4b}
#center{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;text-align:center;
 font-size:4vw;font-weight:700;padding:0 8vw;text-shadow:0 0 1vw #000}
#bar{position:absolute;left:0;right:0;bottom:0;padding:2vh 4vw 3vh;text-align:center;background:rgba(0,0,0,.6)}
#said{font-size:3.2vw;font-weight:700}#status{font-size:2vw;color:#ffe69e;margin-top:.6vh}
#keys{font-size:2vw;margin-top:1vh}#keys b{margin-left:3vw}#keys .done{color:#5bd16a}
#hint{position:absolute;right:2vw;top:2vh;font-size:1vw;color:#555}
.hide{display:none!important}
</style></head><body>
<video id="v" playsinline></video>
<div id="top">Line <span id="line"></span> <span id="dot">●</span></div>
<div id="hint">click here once · F8 lift · F9 / space hang up · 0–9 * #</div>
<div id="center"></div>
<div id="bar"><div id="said"></div><div id="status"></div><div id="keys"></div></div>
<script>
const v = document.getElementById("v"), $ = (id) => document.getElementById(id);
let seq = -1, restart = 0;
const send = (path, body) => fetch(path, {method: "POST", body: JSON.stringify(body)}).catch(() => {});
document.addEventListener("keydown", (e) => {
  if (e.repeat) return;
  let k = e.key;
  if (k === "F8" || k === "F9" || k === " " || /^[0-9*#]$/.test(k)) { e.preventDefault(); send("/key", {key: k}); }
});
v.addEventListener("playing", () => send("/event", {name: "file_loaded"}));
v.addEventListener("error", () => v.getAttribute("src") && send("/event", {name: "video_error"}));
v.addEventListener("ended", () => send("/event", {name: "ended"}));
async function tick() {
  try {
    const s = await (await fetch("/state")).json();
    $("line").textContent = s.line; $("dot").className = s.online ? "" : "off"; $("dot").textContent = s.online ? "●" : "● offline";
    if (s.seq !== seq) {
      seq = s.seq;
      if (s.src) { if (v.getAttribute("src") !== s.src) { v.src = s.src; } v.loop = s.loop; v.muted = s.muted; v.play().catch(() => {}); v.classList.remove("hide"); }
      else { v.removeAttribute("src"); v.load(); v.classList.add("hide"); }
    }
    v.muted = s.muted;
    if (s.restart !== restart) { restart = s.restart; v.currentTime = 0; v.play().catch(() => {}); }
    const view = s.view || {};
    $("center").textContent = view.center || ""; $("center").classList.toggle("hide", !view.center);
    $("bar").classList.toggle("hide", !!view.center || !(view.said || view.status));
    $("said").textContent = view.said || ""; $("status").textContent = view.status || "";
    const keys = view.keys;
    if (keys) {
      const labels = {"2": "Sound", "3": "Regenerate", "0": "Print"};
      $("keys").innerHTML = ["2", "3", "0"].map((k) => `<b class="${keys[k] ? "done" : ""}">[${k}] ${keys[k] || labels[k]}</b>`).join("");
    } else $("keys").innerHTML = "";
  } catch (e) {}
  setTimeout(tick, 120);
}
tick();
</script></body></html>"""


def serve(booth, screen):
    media = B.HERE / "cache"

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/":
                return self._send(200, PAGE.encode(), "text/html; charset=utf-8")
            if self.path == "/state":
                return self._send(200, json.dumps(screen.snapshot()).encode(), "application/json")
            if self.path.startswith("/media/"):
                f = media / Path(self.path[7:]).name
                if f.exists() and f.suffix == ".mp4":
                    return self._send(200, f.read_bytes(), "video/mp4")
            self._send(404, b"", "text/plain")

        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            if self.path == "/key":
                k = data.get("key")
                if k == "F8":
                    booth.post(("hook", True))
                elif k == "F9":
                    booth.post(("hook", False))
                elif k == " ":
                    booth.post(("toggle",))
                elif k:
                    booth.post(("key", k))
            elif self.path == "/event":
                name = data.get("name")
                if name == "ended":
                    screen.next_clip()
                elif name in ("file_loaded", "video_error"):
                    booth.post((name,))
            self._send(204, b"", "text/plain")

    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    load_key()
    try:  # this Mac's Python may not know where the certificates are; certifi (a fal_client dependency) does
        import certifi
        os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    except ImportError:
        pass
    cfg = B.load_config()
    booth = B.Booth(cfg)
    screen = BrowserScreen(booth.post)
    screen.booth = booth
    booth.screen = screen
    booth.line = B.Line(booth.post, cfg)
    booth.fal = B.Fal(cfg)
    booth.printer = LaptopPrinter(cfg)
    booth.cache = B.Cache(cfg["history"])
    threading.Thread(target=serve, args=(booth, screen), daemon=True).start()
    log.info("open http://localhost:%d and click into the page", PORT)
    booth.run()


if __name__ == "__main__":
    main()
