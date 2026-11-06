#!/usr/bin/env python3
"""The booth's control API, for the dashboard on Lizzie's Mac (dashboard.py there). No page is served here: the Pi
only answers PIN-protected requests on the venue Wi-Fi (port 8080) for status, a picture of the TV, the feed, and
commands (open/close the line, the kill switch, screen on/off, the video model, earpiece and mic levels, reprint, hide
a video, restart, reboot). It runs beside booth.py, not inside it, so it still answers when the app is down.

Booth commands go to booth.py over a local socket (see serve_control in booth.py); system ones (screen, sound,
power) run here. Everything but the page itself needs the staff PIN: STAFF_PIN in native.conf, or else a random one
written to staff.pin on first start (cat staff.pin to see it).
"""
import json
import mimetypes
import os
import re
import secrets
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
CONTROL_SOCK = "/tmp/fal-booth-ctl.sock"
MPV_SOCK = "/tmp/fal-booth-mpv.sock"
SHOT = Path("/tmp/fal-dash-screen.jpg")
NOISE = re.compile(r"cuda|CUDA|VK_KHR|VDPAU|hwaccel|AVHWDeviceContext|libvdpau", re.I)
ENV = {**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0"),
       "XDG_RUNTIME_DIR": os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")}


def conf():
    out = {}
    path = HERE / "native.conf"
    if path.exists():
        for raw in path.read_text().splitlines():
            line = raw.split("#", 1)[0].strip()
            if "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"')
    out.update({k[6:]: v for k, v in os.environ.items() if k.startswith("BOOTH_")})  # BOOTH_STAFF_PIN=... for testing
    return out


CONF = conf()
PORT = int(CONF.get("CONTROL_PORT", "8080"))
LINE = CONF.get("LINE", "1")


def staff_pin():
    if CONF.get("STAFF_PIN"):
        return CONF["STAFF_PIN"]
    f = HERE / "staff.pin"
    if not f.exists():
        f.write_text(f"{secrets.randbelow(10**6):06d}\n")
        f.chmod(0o600)
    return f.read_text().strip()


PIN = staff_pin()


def sh(*args, timeout=6):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=ENV)
        return r.returncode, (r.stdout or "").strip()
    except Exception as e:
        return 1, str(e)


def read(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def ask_booth(cmd, **args):
    """One command to booth.py; None when the app isn't running."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(6)
            s.connect(CONTROL_SOCK)
            s.sendall((json.dumps({"cmd": cmd, "args": args}) + "\n").encode())
            return json.loads(s.makefile().readline() or "null")
    except OSError:
        return None


def mpv(*command):
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(3)
            s.connect(MPV_SOCK)
            s.sendall((json.dumps({"command": list(command)}) + "\n").encode())
            return json.loads(s.makefile().readline() or "null")
    except (OSError, ValueError):
        return None


# ---------- the system side ----------
def hdmi_output():
    code, out = sh("xrandr")
    m = re.search(r"^(\S+) connected", out, re.M)
    return m.group(1) if m else None


def screen_on():
    for f in Path("/sys/class/drm").glob("card*-HDMI-A-*"):
        if read(f / "status") == "connected":
            return read(f / "enabled") == "enabled"
    return None


def pct(text):
    m = re.search(r"(\d+)%", text or "")
    return int(m.group(1)) if m else None


def wifi():
    """The network this Pi is on and its signal (0-100), via NetworkManager (Raspberry Pi OS)."""
    code, out = sh("nmcli", "-t", "-f", "active,ssid,signal", "dev", "wifi")
    for line in out.splitlines() if code == 0 else []:
        active, _, rest = line.partition(":")
        ssid, _, signal = rest.rpartition(":")
        if active == "yes":
            return {"ssid": ssid.replace("\\:", ":"), "quality": int(signal) if signal.isdigit() else None}
    code, dev = sh("ip", "-4", "route", "get", "8.8.8.8")
    if code == 0 and " dev eth" in dev:
        return {"ssid": "Ethernet", "quality": 100}
    return {"ssid": None, "quality": None}


def my_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return None


def system():
    temp = read("/sys/class/thermal/thermal_zone0/temp")
    code, thr = sh("vcgencmd", "get_throttled")
    try:
        throttled = int(thr.split("=")[1], 16) & 0xF
    except Exception:
        throttled = 0
    code, sinks = sh("pactl", "list", "short", "sinks")
    code, sources = sh("pactl", "list", "short", "sources")
    st = os.statvfs(str(HERE))
    code, up = sh("pgrep", "-f", f"{HERE}/booth.py")
    return {
        "host": socket.gethostname(), "ip": my_ip(), "line": LINE,
        "temp": round(int(temp) / 1000, 1) if temp else None, "throttled": bool(throttled),
        "undervolt": bool(throttled & 1), "disk_free_gb": round(st.f_bavail * st.f_frsize / 1e9, 1),
        "wifi": wifi(), "screen_on": screen_on(), "app_running": bool(up.strip()),
        "handset": "usb" in sinks.lower() and any("usb" in l.lower() and "monitor" not in l for l in sources.splitlines()),
        "printer": any(Path("/dev/usb").glob("lp*")) if Path("/dev/usb").exists() else False,
        "speaker": pct(sh("pactl", "get-sink-volume", "@DEFAULT_SINK@")[1]),
        "mic": pct(sh("pactl", "get-source-volume", "@DEFAULT_SOURCE@")[1]),
        "uptime": round(float((read("/proc/uptime") or "0").split()[0])),
    }


def log_tail(n=60):
    path = HERE / "booth.log"
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 60000))
            lines = f.read().decode("utf-8", "replace").splitlines()[1:]
    except OSError:
        return []
    return [l for l in lines if l.strip() and not NOISE.search(l)][-n:]


_shot_lock = threading.Lock()


def screenshot():
    """What the TV shows right now (mpv draws everything), at most every 2 seconds."""
    with _shot_lock:
        if not SHOT.exists() or time.time() - SHOT.stat().st_mtime > 2:
            tmp = SHOT.with_name("fal-dash-new.jpg")
            mpv("set_property", "screenshot-jpeg-quality", 70)
            if mpv("screenshot-to-file", str(tmp), "window"):
                for _ in range(20):
                    if tmp.exists() and tmp.stat().st_size:
                        break
                    time.sleep(0.05)
                if tmp.exists():
                    tmp.replace(SHOT)
        return SHOT.read_bytes() if SHOT.exists() else None


def restart_app():
    return sh("pkill", "-f", f"^{HERE}/venv/bin/python {HERE}/booth.py")[0] in (0, 1)


def run_system(cmd, args):
    if cmd == "screen":
        out = hdmi_output()
        if not out:
            return {"ok": False, "error": "No screen connected"}
        ok = sh("xrandr", "--output", out, "--auto" if args.get("on") else "--off")[0] == 0
        return {"ok": ok}
    if cmd in ("speaker", "mic"):
        v = max(0, min(100, int(args.get("value", 0))))
        target = ("set-sink-volume", "@DEFAULT_SINK@") if cmd == "speaker" else ("set-source-volume", "@DEFAULT_SOURCE@")
        return {"ok": sh("pactl", *target, f"{v}%")[0] == 0, "value": v}
    if cmd == "restart_app":
        restart_app()
        return {"ok": True, "note": "Back in about 5 seconds"}
    if cmd == "reboot":
        threading.Timer(1, lambda: sh("sudo", "-n", "shutdown", "-r", "now")).start()
        return {"ok": True, "note": "Rebooting: back in about a minute"}
    if cmd == "shutdown":
        threading.Timer(1, lambda: sh("sudo", "-n", "shutdown", "-h", "now")).start()
        return {"ok": True, "note": "Unplug when the green light stops blinking"}
    return None


SYSTEM_CMDS = {"screen", "speaker", "mic", "restart_app", "reboot", "shutdown"}
BOOTH_CMDS = {"pull", "pause", "resume", "feed", "hide", "clear_feed", "next_clip", "ring", "model", "reprint", "test_print"}


# ---------- the web side ----------
class Handler(BaseHTTPRequestHandler):
    server_version = "fal-booth-control"

    def log_message(self, *a):
        pass

    def cors(self):
        self.send_header("Access-Control-Allow-Origin", "http://localhost:8099")  # only the dashboard on the Mac
        self.send_header("Access-Control-Allow-Headers", "X-Pin, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def send(self, code, body, ctype="application/json", cache="no-store"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.cors()
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", cache)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authed(self):
        given = self.headers.get("X-Pin") or ""
        if secrets.compare_digest(given.encode(), PIN.encode()):
            return True
        self.send(401, {"ok": False, "error": "Wrong PIN"})
        return False

    def do_OPTIONS(self):
        self.send_response(204)
        self.cors()
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        if not self.authed():
            return
        if path == "/api/status":
            b = ask_booth("status")
            return self.send(200, {"ok": True, "at": time.time(), "system": system(),
                                   "booth": (b or {}).get("status"), "log": log_tail(40)})
        if path == "/api/screen.jpg":
            img = screenshot()
            return self.send(200, img, "image/jpeg") if img else self.send(503, {"ok": False, "error": "No screen"})
        if path == "/api/feed":
            return self.send(200, ask_booth("feed") or {"ok": False, "error": "The app isn't running"})
        if path == "/api/still":
            rel = parse_qs(urlparse(self.path).query).get("f", [""])[0]
            f = (HERE / "cache" / rel).resolve()
            if rel and str(f).startswith(str((HERE / "cache").resolve())) and f.is_file():
                return self.send(200, f.read_bytes(), mimetypes.guess_type(f.name)[0] or "image/jpeg", "max-age=3600")
            return self.send(404, {"ok": False})
        self.send(404, {"ok": False})

    def do_POST(self):
        if urlparse(self.path).path != "/api/cmd":
            return self.send(404, {"ok": False})
        if not self.authed():
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            msg = json.loads(self.rfile.read(min(n, 10000)) or b"{}")
        except ValueError:
            return self.send(400, {"ok": False, "error": "Bad request"})
        cmd, args = str(msg.get("cmd", "")), msg.get("args") or {}
        if cmd in SYSTEM_CMDS:
            return self.send(200, run_system(cmd, args))
        if cmd in BOOTH_CMDS:
            return self.send(200, ask_booth(cmd, **args) or {"ok": False, "error": "The app isn't running: restart it"})
        self.send(400, {"ok": False, "error": "Unknown command"})


def main():
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"booth control API on {socket.gethostname()}.local:{PORT}  (PIN in native.conf or staff.pin)", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
