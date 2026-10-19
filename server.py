#!/usr/bin/env python3
import json
import os
import re
import socket
import socketserver
import subprocess
import threading
import time
import urllib.request
from datetime import datetime
from glob import glob
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HOME = Path.home()
STATIC = Path(__file__).resolve().parent / "static"
SOCKET = Path(os.environ.get("BRIDGE_SOCKET", str(Path(__file__).resolve().parent / "run/bridge.sock")))
ALLOWED = {x.strip().lower() for x in os.environ.get("BRIDGE_ALLOWED_LOGINS", "").split(",") if x.strip()}
NTFY_TOPIC = os.environ.get("BRIDGE_NTFY_TOPIC", "")
NTFY_CLICK = os.environ.get("BRIDGE_URL", "")
MAX_UPLOAD = 25 * 1024 * 1024
DEFAULT_CFG = str(HOME / ".claude")
ACCOUNTS_FILE = Path(os.environ.get("BRIDGE_ACCOUNTS", str(Path(__file__).resolve().parent / "accounts.json")))
HOSTNAMES = {"", socket.gethostname(), socket.gethostname().split(".")[0]}


def expand(p):
    return str(Path(p).expanduser()).rstrip("/")


def load_accounts():
    try:
        entries = json.loads(ACCOUNTS_FILE.read_text())
    except FileNotFoundError:
        entries = [{"id": "main", "label": "Main account", "config_dir": "~/.claude", "inbox": "~/inbox"}]
    accounts, presets = {}, {}
    for a in entries:
        accounts[expand(a["config_dir"])] = {"account": a["id"], "label": a.get("label", a["id"]),
                                             "color": a.get("color"), "inbox": Path(expand(a.get("inbox", "~/inbox")))}
        if a.get("tmux"):
            presets[a["id"]] = {"label": a.get("preset_label", a.get("label", a["id"])), "tmux": a["tmux"],
                                "container": a.get("container"), "cwd": expand(a.get("cwd", "~"))}
    return accounts, presets


ACCOUNTS, PRESETS = load_accounts()

KEYS = {"esc": "Escape", "enter": "Enter", "up": "Up", "down": "Down", "tab": "Tab", "stab": "BTab",
        "ctrlc": "C-c", **{str(i): str(i) for i in range(1, 10)}}

STATIC_FILES = {"": "index.html", "index.html": "index.html", "app.js": "app.js", "app.css": "app.css",
                "manifest.webmanifest": "manifest.webmanifest", "icon-180.png": "icon-180.png",
                "icon-512.png": "icon-512.png"}

SPINNER = re.compile(r"^[^\w\s]\s+")
OPTION = re.compile(r"^\s*(?:[❯›>]\s*)?(\d)\.\s+(.+?)\s*$")


def tmux(*args, input=None):
    return subprocess.run(["tmux", *args], input=input, capture_output=True, check=True).stdout.decode()


def read(path):
    try:
        return Path(path).read_text()
    except OSError:
        return None


def claude_procs():
    procs = []
    for d in os.listdir("/proc"):
        if not d.isdigit() or read(f"/proc/{d}/comm") != "claude\n":
            continue
        try:
            status = Path(f"/proc/{d}/status").read_text()
            env = Path(f"/proc/{d}/environ").read_bytes().split(b"\0")
        except OSError:
            continue
        cfg = DEFAULT_CFG
        for e in env:
            if e.startswith(b"CLAUDE_CONFIG_DIR="):
                cfg = e.split(b"=", 1)[1].decode().rstrip("/")
        nspid = re.search(r"^NSpid:\s+(.*)$", status, re.M).group(1).split()
        try:
            reg = json.loads(Path(cfg, "sessions", f"{nspid[-1]}.json").read_text())
        except (OSError, ValueError):
            continue
        if reg.get("kind", "interactive") != "interactive":
            continue
        procs.append({"pid": int(d), "in_container": len(nspid) > 1, "cfg": cfg, "reg": reg})
    return procs


def ancestors(pid):
    seen = set()
    while pid > 1 and pid not in seen:
        seen.add(pid)
        stat = read(f"/proc/{pid}/stat")
        if not stat:
            break
        pid = int(stat.rsplit(")", 1)[1].split()[1])
    return seen


PANE_FMT = "\t".join(["#{pane_id}", "#{pane_pid}", "#{session_name}", "#{window_index}", "#{window_name}",
                      "#{pane_title}", "#{session_attached}", "#{window_active}", "#{pane_current_command}"])


def list_sessions():
    panes = []
    for line in tmux("list-panes", "-a", "-F", PANE_FMT).splitlines():
        f = line.split("\t")
        if len(f) == 9:
            panes.append({"pane": f[0], "pid": int(f[1]), "tmux": f[2], "window": f[3], "wname": f[4],
                          "title": SPINNER.sub("", f[5]).strip(), "attached": f[6] != "0" and f[7] == "1",
                          "cmd": f[8]})
    by_pid = {p["pid"]: p for p in panes}
    by_name = {}
    for p in panes:
        if p["cmd"] == "docker":
            by_name.setdefault(p["wname"], p)
    out = []
    for proc in claude_procs():
        reg = proc["reg"]
        pane = None
        if proc["in_container"]:
            pane = by_name.get(reg.get("name"))
        else:
            pane = next((by_pid[a] for a in ancestors(proc["pid"]) if a in by_pid), None)
        if not pane:
            continue
        acct = ACCOUNTS.get(proc["cfg"], {"account": Path(proc["cfg"]).name.removeprefix(".claude-")})
        out.append({
            "id": pane["pane"].lstrip("%"),
            "name": reg.get("name") or pane["wname"],
            "title": "" if pane["title"] in HOSTNAMES else pane["title"],
            "account": acct["account"],
            "status": reg.get("status") or "unknown",
            "waitingFor": reg.get("waitingFor"),
            "updatedAt": reg.get("statusUpdatedAt") or reg.get("updatedAt"),
            "sessionId": reg.get("sessionId"),
            "cwd": reg.get("cwd"),
            "where": f'{pane["tmux"]}:{pane["window"]}',
            "attached": pane["attached"],
            "_cfg": proc["cfg"],
        })
    out.sort(key=lambda s: (s["account"], s["where"]))
    return out


def find_session(sid):
    for s in list_sessions():
        if s["id"] == sid:
            return s
    return None


TAGGED = re.compile(r"<(system-reminder|local-command-caveat|local-command-stdout|local-command-stderr)>.*?</\1>", re.S)


def clean_user(text):
    cmd = re.search(r"<command-name>(.*?)</command-name>", text, re.S)
    if cmd:
        args = re.search(r"<command-args>(.*?)</command-args>", text, re.S)
        return f'{cmd.group(1).strip()} {args.group(1).strip() if args else ""}'.strip()
    return TAGGED.sub("", text).strip()


def clip(text, n):
    return text if len(text) <= n else text[:n] + f"\n… ({len(text) - n} more characters)"


def tool_summary(name, inp):
    for key in ("command", "file_path", "pattern", "url", "description", "query", "prompt", "path", "skill"):
        if isinstance(inp.get(key), str):
            return clip(inp[key], 400)
    return clip(json.dumps(inp, ensure_ascii=False), 300)


def result_text(content):
    if isinstance(content, str):
        return clip(content, 2000)
    parts = []
    for b in content or []:
        if b.get("type") == "text":
            parts.append(b.get("text", ""))
        elif b.get("type") == "image":
            parts.append("[image]")
    return clip("\n".join(parts), 2000)


def parse_entry(d):
    kind = d.get("type")
    if kind not in ("user", "assistant") or d.get("isSidechain") or d.get("isMeta"):
        return []
    ts = d.get("timestamp")
    content = (d.get("message") or {}).get("content")
    if isinstance(content, str):
        text = clean_user(content) if kind == "user" else content.strip()
        return [{"k": "user" if kind == "user" else "claude", "text": text, "ts": ts}] if text else []
    items = []
    for b in content or []:
        bt = b.get("type")
        if bt == "text":
            text = clean_user(b.get("text", "")) if kind == "user" else b.get("text", "").strip()
            if text:
                items.append({"k": "user" if kind == "user" else "claude", "text": text, "ts": ts})
        elif bt == "image" and kind == "user":
            items.append({"k": "user", "text": "[image]", "ts": ts})
        elif bt == "tool_use":
            items.append({"k": "tool", "id": b.get("id"), "name": b.get("name"),
                          "summary": tool_summary(b.get("name"), b.get("input") or {}), "ts": ts})
        elif bt == "tool_result":
            items.append({"k": "result", "for": b.get("tool_use_id"), "error": bool(b.get("is_error")),
                          "text": result_text(b.get("content")), "ts": ts})
    return items


class Transcript:
    def __init__(self, path):
        self.path, self.offset, self.items, self.partial = path, 0, [], b""
        self.lock = threading.Lock()

    def refresh(self):
        with self.lock:
            try:
                size = os.path.getsize(self.path)
            except OSError:
                return self.items
            if size < self.offset:
                self.offset, self.items, self.partial = 0, [], b""
            if size > self.offset:
                with open(self.path, "rb") as fh:
                    fh.seek(self.offset)
                    chunk = fh.read()
                self.offset += len(chunk)
                lines = (self.partial + chunk).split(b"\n")
                self.partial = lines.pop()
                for line in lines:
                    try:
                        self.items.extend(parse_entry(json.loads(line)))
                    except ValueError:
                        pass
            return self.items


transcripts = {}


def transcript_for(cfg, session_id):
    key = (cfg, session_id)
    if key not in transcripts:
        hits = glob(os.path.join(cfg, "projects", "*", f"{session_id}.jsonl"))
        if not hits:
            return None
        transcripts[key] = Transcript(hits[0])
    return transcripts[key]


def screen(pane):
    text = tmux("capture-pane", "-p", "-J", "-t", pane, "-S", "-60")
    lines = text.rstrip("\n").split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    options, selected = [], None
    for line in lines[-25:]:
        m = OPTION.match(line)
        if m:
            if re.match(r"^\s*[❯›>]", line):
                selected = m.group(1)
            options = [o for o in options if o["key"] != m.group(1)] + [{"key": m.group(1), "label": m.group(2)}]
    return {"text": "\n".join(lines[-60:]), "options": options, "selected": selected}


def send_text(pane, text):
    tmux("load-buffer", "-b", "bridge", "-", input=text.encode())
    tmux("paste-buffer", "-p", "-d", "-b", "bridge", "-t", pane)
    time.sleep(0.3)
    tmux("send-keys", "-t", pane, "Enter")


def new_window(preset):
    p = PRESETS[preset]
    pane = tmux("new-window", "-d", "-P", "-F", "#{pane_id}", "-t", f'{p["tmux"]}:', "-c", p["cwd"]).strip()
    if p["container"]:
        tmux("send-keys", "-t", pane, "-l", f'docker exec -it -w {p["cwd"]} {p["container"]} bash -l')
        tmux("send-keys", "-t", pane, "Enter")
        for _ in range(20):
            time.sleep(0.5)
            if tmux("display", "-p", "-t", pane, "#{pane_current_command}").strip() == "docker":
                break
        time.sleep(1.0)
    tmux("send-keys", "-t", pane, "-l", "claude")
    tmux("send-keys", "-t", pane, "Enter")
    return pane.lstrip("%")


def save_upload(session, name, body):
    inbox = (ACCOUNTS.get(session["_cfg"]) or ACCOUNTS.get(DEFAULT_CFG) or {"inbox": HOME / "inbox"})["inbox"]
    inbox.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name).name)[-80:] or "file"
    path = inbox / f'{datetime.now():%Y%m%d-%H%M%S}-phone-{safe}'
    path.write_bytes(body)
    return str(path)


def ntfy(title, message):
    if not NTFY_TOPIC:
        return
    headers = {"Title": title.encode("utf-8"), "Tags": "speech_balloon"}
    if NTFY_CLICK:
        headers["Click"] = NTFY_CLICK
    try:
        urllib.request.urlopen(urllib.request.Request(f"https://ntfy.sh/{NTFY_TOPIC}", data=message.encode(),
                                                      headers=headers), timeout=10)
    except OSError:
        pass


def watcher():
    last, sent = {}, {}
    while True:
        try:
            for s in list_sessions():
                key = s["sessionId"]
                prev = last.get(key)
                last[key] = s["status"]
                if s["attached"] or prev is None or prev == s["status"]:
                    continue
                if s["status"] == "waiting" and time.time() - sent.get(key, 0) > 60:
                    sent[key] = time.time()
                    ntfy(f'{s["name"]} needs you', "Waiting for an answer")
                elif prev == "busy" and s["status"] == "idle":
                    ntfy(f'{s["name"]} finished', "Done")
        except Exception:
            pass
        time.sleep(4)


# A Unix socket instead of a TCP port: anything inside a container with host networking could reach a
# 127.0.0.1 port and forge the Tailscale header. Keep ~/bridge/run out of every container's mounts.
class UnixHTTPServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


def public(s):
    return {k: v for k, v in s.items() if not k.startswith("_")}


class Handler(BaseHTTPRequestHandler):
    server_version = "bridge"
    sys_version = ""

    def log_message(self, fmt, *args):
        pass

    def route(self):
        path = urlparse(self.path).path
        if path.startswith("/bridge"):
            path = path[len("/bridge"):]
        return path.strip("/")

    def allowed(self):
        return (self.headers.get("Tailscale-User-Login") or "").lower() in ALLOWED

    def reply(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; img-src 'self' data: blob:; style-src 'self'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self.allowed():
            return self.reply(403, {"error": "forbidden"})
        path = self.route()
        if path in STATIC_FILES:
            f = STATIC / STATIC_FILES[path]
            ctype = {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css",
                     ".png": "image/png", ".webmanifest": "application/manifest+json"}[f.suffix]
            return self.reply(200, f.read_bytes(), ctype)
        if path == "api/sessions":
            return self.reply(200, {"sessions": [public(s) for s in list_sessions()],
                                    "presets": {k: v["label"] for k, v in PRESETS.items()},
                                    "accounts": [{"id": a["account"], "label": a["label"], "color": a["color"]}
                                                 for a in ACCOUNTS.values()]})
        m = re.fullmatch(r"api/sessions/(\d+)/(messages|screen)", path)
        if m:
            s = find_session(m.group(1))
            if not s:
                return self.reply(404, {"error": "session not found"})
            if m.group(2) == "screen":
                return self.reply(200, {**screen("%" + s["id"]), "status": s["status"]})
            q = parse_qs(urlparse(self.path).query)
            t = transcript_for(s["_cfg"], s["sessionId"])
            items = t.refresh() if t else []
            if q.get("sid", [""])[0] != s["sessionId"] or "after" not in q:
                start = max(0, len(items) - 200)
            else:
                start = min(int(q["after"][0]), len(items))
            return self.reply(200, {"sid": s["sessionId"], "start": start, "items": items[start:],
                                    "next": len(items)})
        self.reply(404, {"error": "not found"})

    def do_POST(self):
        if not self.allowed() or self.headers.get("X-Bridge") != "1":
            return self.reply(403, {"error": "forbidden"})
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_UPLOAD:
            return self.reply(413, {"error": "too large"})
        body = self.rfile.read(length)
        path = self.route()
        if path == "api/new":
            preset = json.loads(body or b"{}").get("preset")
            if preset not in PRESETS:
                return self.reply(400, {"error": "unknown preset"})
            return self.reply(200, {"pane": new_window(preset)})
        m = re.fullmatch(r"api/sessions/(\d+)/(send|key|upload)", path)
        if not m:
            return self.reply(404, {"error": "not found"})
        s = find_session(m.group(1))
        if not s:
            return self.reply(404, {"error": "session not found"})
        pane = "%" + s["id"]
        if m.group(2) == "upload":
            name = self.headers.get("X-Filename") or "upload"
            return self.reply(200, {"path": save_upload(s, name, body)})
        data = json.loads(body or b"{}")
        if m.group(2) == "send":
            text = (data.get("text") or "").strip()
            if not text:
                return self.reply(400, {"error": "empty"})
            send_text(pane, text)
        else:
            key = KEYS.get(data.get("key"))
            if not key:
                return self.reply(400, {"error": "unknown key"})
            tmux("send-keys", "-t", pane, key)
        self.reply(200, {"ok": True})


if __name__ == "__main__":
    if not ALLOWED:
        raise SystemExit("BRIDGE_ALLOWED_LOGINS is empty; refusing to start")
    SOCKET.parent.mkdir(mode=0o700, exist_ok=True)
    os.chmod(SOCKET.parent, 0o700)
    SOCKET.unlink(missing_ok=True)
    server = UnixHTTPServer(str(SOCKET), Handler)
    os.chmod(SOCKET, 0o600)
    threading.Thread(target=watcher, daemon=True).start()
    server.serve_forever()
