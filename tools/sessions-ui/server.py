#!/usr/bin/env python3
"""A local page to review funes sessions and build a pruned memory from the ones you keep.

funes is append-only and has no delete, so "prune" means: build a NEW memory from only the kept
sessions (re-converting their original Claude Code transcripts, then `funes index`), and only then,
on a typed confirmation, swap it in. The live memory is never modified while building, and the
swapped-out memory is renamed, not deleted.

Standard library only. Binds 127.0.0.1; every /api call needs the per-run token embedded in the page.

    python tools/sessions-ui/server.py            # then open the printed URL
    python tools/sessions-ui/server.py --port 8765 --funes C:\\path\\funes.exe
"""

import argparse
import http.server
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,200}$")
ROW_RE = re.compile(r"^\[(?P<span>[^\]]+)\] (?P<harness>\S+) (?P<repo>.*?) ?(?P<turns>\d+) turns (?P<id>\S+)\s*$")
OFFSET_RE = re.compile(r"continue with --offset (\d+)")
# What `swap` exchanges inside the funes home. `agents/` and the install id are left alone: the
# agent registry is fixed at ~/.funes/agents whatever the home.
SWAP_ITEMS = ["memory", "state.json", "index-coverage.json"]

ARGS = None
TOKEN = secrets.token_urlsafe(24)
JOB = {
    "state": "idle",  # idle | running | done | error | swapped
    "stage": None,  # converting | reading | embedding | compacting | indexing | finishing
    "done": 0,
    "total": 0,
    "rate": None,  # chunks per second while embedding
    "log": [],
    "newhome": None,
    "kept": 0,
    "started": None,
    "finished": None,
}
SESSION_RE = re.compile(r"^\[(\d+)/(\d+)\]")
EMBED_RE = re.compile(r"embedd(?:ing|ed)\s+(\d+)/(\d+)(?:\s+\((\d+)/s\))?")
STATUS_NUM = {"chunks": re.compile(r"^chunks: (\d+)", re.M), "sessions": re.compile(r"^sessions: (\d+)", re.M),
              "awaiting": re.compile(r"(\d+) chunks awaiting embedding"), "pending": re.compile(r"pending indexing: (\d+)")}
JOB_LOCK = threading.Lock()


# ----------------------------------------------------------------------------- funes plumbing


def find_funes():
    if ARGS.funes:
        return ARGS.funes
    found = shutil.which("funes")
    if found:
        return found
    candidate = Path.home() / ".local" / "bin" / "funes.exe"
    return str(candidate) if candidate.exists() else "funes"


def git_sh_dir():
    """Git for Windows' usr/bin, for `sh` (the integration's converter is a shell script), found from
    `git` on PATH. The WSL launcher that sits in System32 as bash.exe is not it."""
    git = shutil.which("git")
    if git:
        usr_bin = Path(git).resolve().parent.parent / "usr" / "bin"
        if (usr_bin / "sh.exe").exists():
            return str(usr_bin)
    for root in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
        if root and (Path(root) / "Git" / "usr" / "bin" / "sh.exe").exists():
            return str(Path(root) / "Git" / "usr" / "bin")
    return None


def tool_env(extra=None):
    env = dict(os.environ)
    dirs = [str(Path(find_funes()).parent)]
    sh_dir = git_sh_dir()
    if sh_dir:
        dirs.append(sh_dir)
    env["PATH"] = os.pathsep.join(dirs + [env.get("PATH", "")])
    env.update(extra or {})
    return env


def source_args():
    return ["--memory", ARGS.source] if ARGS.source else []


def run_funes(args, extra_env=None, timeout=120):
    return subprocess.run(
        [find_funes(), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=tool_env(extra_env),
    )


_LIVE_HOME = {}


def live_home(refresh=False):
    """The funes home the live memory sits in, from `funes status` (`memory: <home>/memory`). Asked
    once per server run, since it only changes with the environment; `refresh` asks again."""
    if refresh or "home" not in _LIVE_HOME:
        out = run_funes(["status"]).stdout
        m = re.search(r"^memory: (.+)$", out, re.M)
        if not m:
            raise RuntimeError("could not read the memory path from `funes status`")
        _LIVE_HOME["home"] = Path(m.group(1).strip()).parent
    return _LIVE_HOME["home"]


def model_cache():
    """The Hugging Face model cache the live funes uses, so a rebuild reuses it instead of downloading
    the weights again into the new home."""
    for key in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE"):
        if os.environ.get(key):
            return os.environ[key]
    hf_home = os.environ.get("HF_HOME")
    if hf_home:
        return str(Path(hf_home) / "hub")
    beside_home = live_home() / "hf" / "hub"
    if beside_home.exists():
        return str(beside_home)
    return str(Path.home() / ".cache" / "huggingface" / "hub")


def fetch_sessions():
    rows, offset = [], 0
    while True:
        r = run_funes(["sessions", *source_args(), "--limit", "200", "--offset", str(offset)])
        if r.returncode != 0:
            raise RuntimeError((r.stderr or r.stdout).strip() or "funes sessions failed")
        page, cur = [], None
        for line in r.stdout.splitlines():
            m = ROW_RE.match(line)
            if m:
                span = m["span"].split(" \u2192 ")
                cur = {
                    "id": m["id"],
                    "harness": m["harness"],
                    "repo": m["repo"].strip() or "(no repo)",
                    "turns": int(m["turns"]),
                    "start": span[0],
                    "end": span[-1],
                    "snippet": "",
                }
                page.append(cur)
            elif cur is not None and line.startswith("  ") and not cur["snippet"]:
                cur["snippet"] = line.strip()
        rows = page + rows  # each older page goes in front
        nxt = OFFSET_RE.search(r.stdout)
        if not nxt:
            break
        offset = int(nxt.group(1))
    return rows


# Where each supported agent keeps the transcripts a rebuild converts again. A rebuild needs both
# these and the integration's own `convert` script, so an agent is supported once it has an entry.
TRANSCRIPT_ROOTS = {
    "claude": lambda: Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "projects",
}


def converter():
    """The integration's `convert` script, found under its folder in the agent registry
    (`~/.funes/agents/<harness>/`), or None when it ships none."""
    root = Path.home() / ".funes" / "agents" / ARGS.harness
    return next((p for p in sorted(root.rglob("convert")) if p.is_file()), None) if root.is_dir() else None


def transcripts():
    """The agent's own transcripts by session id (the file stem), sub-agents included."""
    root = TRANSCRIPT_ROOTS[ARGS.harness]()
    found = {}
    if root.is_dir():
        for p in root.rglob("*.jsonl"):
            if p.name != "journal.jsonl":
                found.setdefault(p.stem, p)
    return found


def plan(keep):
    src = transcripts()
    found = [i for i in keep if i in src]
    return {"keep": len(keep), "found": len(found), "missing": [i for i in keep if i not in src]}


# ----------------------------------------------------------------------------- rebuild job


def log(line):
    with JOB_LOCK:
        JOB["log"].append(line)
        del JOB["log"][:-1500]


def finish(state, line):
    log(line)
    with JOB_LOCK:
        JOB["state"] = state
        JOB["finished"] = time.time()
        if state == "done":
            JOB["stage"] = "finishing"
            JOB["done"] = JOB["total"] = max(JOB["total"], 1)


def set_progress(stage, done=None, total=None, rate=None):
    with JOB_LOCK:
        JOB["stage"] = stage
        if done is not None:
            JOB["done"], JOB["total"] = done, total
        JOB["rate"] = rate


def progress_from_line(text):
    """What `funes index` prints, as a stage and counts: `[k/N] session` while it reads sessions,
    `embedding a/b` and `embedded a/b (r/s)` while it embeds, then compact and search-index lines."""
    m = SESSION_RE.match(text)
    if m:
        return set_progress("reading", int(m[1]), int(m[2]))
    m = EMBED_RE.search(text)
    if m:
        return set_progress("embedding", int(m[1]), int(m[2]), int(m[3]) if m[3] else None)
    low = text.lower()
    if low.startswith(("compacting", "merging")):
        return set_progress("compacting")
    if low.startswith("building"):
        return set_progress("indexing")


def funes_running():
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq funes.exe"], capture_output=True, text=True).stdout
    return "funes.exe" in out.lower()


def rebuild(keep, newhome):
    try:
        src = transcripts()
        convert = converter()
        if convert is None:
            return finish("error", f"no `convert` script for {ARGS.harness} under ~/.funes/agents/{ARGS.harness} - run `funes add {ARGS.harness}`")
        if not git_sh_dir() and not shutil.which("sh"):
            return finish("error", "no `sh` found - install Git for Windows")
        spool = newhome / "spool-in"
        spool.mkdir(parents=True)
        log(f"converting {len(keep)} transcripts into {spool}")
        set_progress("converting", 0, len(keep))
        for n, sid in enumerate(keep, 1):
            r = subprocess.run(
                ["sh", convert.as_posix(), src[sid].as_posix(), (spool / f"{sid}.funes.jsonl").as_posix()],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=tool_env(),
            )
            if r.returncode != 0:
                return finish("error", f"converting {sid} failed: {(r.stderr or r.stdout).strip()[:400]}")
            set_progress("converting", n, len(keep))
            if n % 25 == 0 or n == len(keep):
                log(f"  converted {n}/{len(keep)}")
        log("indexing into the new memory (CPU embedding; this is the slow part)")
        env = tool_env({"FUNES_HOME": str(newhome), "HF_HUB_CACHE": model_cache()})
        proc = subprocess.Popen(
            [find_funes(), "index", str(spool), "--yes"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
        )
        buf = b""
        while True:
            ch = proc.stdout.read(1)
            if not ch:
                break
            if ch in (b"\r", b"\n"):
                text = buf.decode("utf-8", "replace").strip()
                buf = b""
                if text:
                    log(text)
                    progress_from_line(text)
            else:
                buf += ch
        if proc.wait() != 0:
            return finish("error", f"funes index exited {proc.returncode}")
        shutil.rmtree(spool, ignore_errors=True)
        finish("done", f"built {newhome}. The live memory is untouched; swap it in from the page when ready.")
    except Exception as e:  # keep the job state truthful whatever went wrong
        finish("error", f"{type(e).__name__}: {e}")


def start_rebuild(keep):
    with JOB_LOCK:
        if JOB["state"] == "running":
            raise RuntimeError("a build is already running")
    if funes_running():
        raise RuntimeError("funes is running (an index or a hook); wait for it to finish, then build")
    p = plan(keep)
    if p["missing"]:
        raise RuntimeError(f"{len(p['missing'])} kept sessions have no transcript left to rebuild from")
    home = live_home()
    newhome = home.parent / f"{home.name}-pruned"
    if newhome.exists():
        raise RuntimeError(f"{newhome} already exists - move or delete it first")
    with JOB_LOCK:
        JOB.update(
            state="running", stage="converting", done=0, total=len(keep), rate=None, log=[],
            newhome=str(newhome), kept=len(keep), started=time.time(), finished=None,
        )
    threading.Thread(target=rebuild, args=(keep, newhome), daemon=True).start()
    return {"newhome": str(newhome)}


def memory_status(home):
    """`funes status` for the memory in `home`, as the counts it prints: chunks, sessions, chunks still
    awaiting an embedding, source sessions not yet indexed."""
    r = run_funes(["status"], {"FUNES_HOME": str(home)})
    text = r.stdout
    out = {"home": str(home), "exists": (Path(home) / "memory").exists(), "text": text.strip()[:1500]}
    for key, rx in STATUS_NUM.items():
        m = rx.search(text)
        out[key] = int(m[1]) if m else None
    return out


def status_report():
    live = live_home()
    report = {"live": memory_status(live)}
    if ARGS.source:
        report["source"] = memory_status(Path(ARGS.source).parent)
    with JOB_LOCK:
        if JOB["newhome"] and Path(JOB["newhome"]).exists():
            report["building"] = memory_status(Path(JOB["newhome"]))
    report["funes_running"] = funes_running()
    return report


def swap_items(home, newhome, items, stamp):
    """Put the built memory in place of the live one, all or nothing: every live item is renamed to
    `<item>.before-prune-<stamp>`, then every built item takes its place. If any rename fails, the
    steps already done are undone in reverse, so the live memory is back as it was. Returns the
    backups made, as `item -> backup` lines."""
    if not (newhome / "memory").exists():
        raise RuntimeError(f"the new build has no memory folder in {newhome}")
    backups = []  # (live path, backup path), in the order they were made
    installed = []  # (live path, built path), in the order they were moved in
    try:
        for item in items:
            live = home / item
            if live.exists():
                backup = home / f"{item}.before-prune-{stamp}"
                if backup.exists():
                    raise RuntimeError(f"{backup} already exists")
                live.rename(backup)
                backups.append((live, backup))
        for item in items:
            built = newhome / item
            if built.exists():
                built.rename(home / item)
                installed.append((home / item, built))
    except Exception as e:
        problems = []
        for live, built in reversed(installed):
            try:
                live.rename(built)
            except OSError as undo:
                problems.append(f"could not move {live} back to {built}: {undo}")
        for live, backup in reversed(backups):
            try:
                backup.rename(live)
            except OSError as undo:
                problems.append(f"could not restore {backup} to {live}: {undo}")
        if problems:
            raise RuntimeError(f"swap failed ({e}) and the rollback was incomplete: " + "; ".join(problems)) from e
        raise RuntimeError(f"swap failed ({e}); rolled back, your memory is as it was") from e
    return [f"{live.name} -> {backup.name}" for live, backup in backups]


def swap():
    with JOB_LOCK:
        if JOB["state"] != "done":
            raise RuntimeError("no finished build to swap in")
        newhome = Path(JOB["newhome"])
    home = live_home(refresh=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    # The check sits right before the renames to leave the shortest window for a hook to start in. If one
    # still does and opens the memory between two renames, a rename fails and `swap_items` rolls back.
    if funes_running():
        raise RuntimeError("funes is running; wait for it to finish, then swap")
    moved = swap_items(home, newhome, SWAP_ITEMS, stamp)
    # A funes that started after the check could have opened the old memory just before it moved, so
    # confirm what is in place now and say so if anything else is running.
    after = memory_status(home)
    warning = None
    if funes_running():
        warning = "funes started during the swap; run `funes status` and check the counts before you carry on"
    if not after["exists"] or not after["chunks"]:
        warning = "the swapped-in memory does not read back as a memory; the previous one is kept as the backup"
    shutil.rmtree(newhome / "spool-in", ignore_errors=True)
    try:
        newhome.rmdir()  # only when nothing is left in it; a build refuses to start beside an existing one
    except OSError:
        pass
    with JOB_LOCK:
        JOB["state"] = "swapped"
    log("swapped in. Previous memory kept as *.before-prune-" + stamp)
    return {"moved": moved, "kept_as": f"*.before-prune-{stamp}", "home": str(home), "warning": warning,
            "chunks": after["chunks"], "sessions": after["sessions"]}


# ----------------------------------------------------------------------------- http


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "funes-sessions-ui"

    def log_message(self, *_):
        pass

    def _host_ok(self):
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost")

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj))

    def _authorized(self):
        return self._host_ok() and secrets.compare_digest(self.headers.get("X-Token", ""), TOKEN)

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        if url.path == "/":
            if not self._host_ok():
                return self._send(403, "bad host", "text/plain")
            page = (HERE / "index.html").read_text(encoding="utf-8").replace("__TOKEN__", TOKEN)
            return self._send(200, page, "text/html")
        if not self._authorized():
            return self._json(403, {"error": "forbidden"})
        q = urllib.parse.parse_qs(url.query)
        try:
            if url.path == "/api/sessions":
                home = live_home()
                shown = ARGS.source or str(home / "memory")
                return self._json(200, {"sessions": fetch_sessions(), "home": f"{shown}  →  a new memory is built in {home}"})
            if url.path == "/api/sketch":
                sid = (q.get("id") or [""])[0]
                if not ID_RE.match(sid):
                    return self._json(400, {"error": "bad id"})
                r = run_funes(["sketch", sid, *source_args()])
                return self._json(200, {"text": (r.stdout or r.stderr)[:40000]})
            if url.path == "/api/job":
                with JOB_LOCK:
                    return self._json(200, dict(JOB, log=JOB["log"][-400:], now=time.time()))
            if url.path == "/api/status":
                return self._json(200, status_report())
        except Exception as e:
            return self._json(500, {"error": f"{type(e).__name__}: {e}"})
        return self._json(404, {"error": "not found"})

    def do_POST(self):
        if not self._authorized():
            return self._json(403, {"error": "forbidden"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            keep = body.get("keep") or []
            if not isinstance(keep, list) or not all(isinstance(i, str) and ID_RE.match(i) for i in keep):
                return self._json(400, {"error": "keep must be a list of session ids"})
            if self.path == "/api/plan":
                return self._json(200, plan(keep))
            if self.path == "/api/rebuild":
                if not keep:
                    return self._json(400, {"error": "nothing to keep"})
                return self._json(200, start_rebuild(keep))
            if self.path == "/api/swap":
                if body.get("confirm") != "SWAP":
                    return self._json(400, {"error": 'type SWAP to confirm'})
                return self._json(200, swap())
        except Exception as e:
            return self._json(500, {"error": f"{type(e).__name__}: {e}"})
        return self._json(404, {"error": "not found"})


def main():
    global ARGS
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--funes", help="path to funes.exe (default: PATH, then ~/.local/bin)")
    ap.add_argument(
        "--harness",
        default="claude",
        choices=sorted(TRANSCRIPT_ROOTS),
        help="the agent whose transcripts a rebuild converts again (default: claude)",
    )
    ap.add_argument(
        "--source",
        help="a local memory path to list and sketch sessions from when it is not the live one, e.g. an "
        "old memory you are rebuilding from. Builds and swaps still use the live home.",
    )
    ARGS = ap.parse_args()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", ARGS.port), Handler)
    print(f"funes sessions UI: http://127.0.0.1:{ARGS.port}/   (Ctrl+C to stop)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    sys.exit(main())
