"""droso-pipe worker: asks the NAS for work, runs the tool, hands the results back.

    python droso_worker.py --config worker.toml           # keep running
    python droso_worker.py --config worker.toml --once    # one run at most, then exit

Pure standard library (Python 3.11+); psutil is optional and only used to detect recording software.
Same program on Windows (Rig 1 PC) and Linux (milky). The config says how this machine reaches the
store: a mounted path/UNC share ("path"), or rclone ("rclone").
"""

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import tomllib
import traceback
import urllib.error
import urllib.request
from pathlib import Path

CHUNK = 8 * 1024 * 1024


def log(msg: str):
    print(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {msg}", flush=True)


# --- server ------------------------------------------------------------------------------------

class Api:
    def __init__(self, url: str, token: str):
        self.url, self.token = url.rstrip("/"), token

    def post(self, path: str, body: dict | None = None):
        req = urllib.request.Request(f"{self.url}/api/{path}", data=json.dumps(body or {}).encode(),
                                     headers={"Authorization": f"Bearer {self.token}",
                                              "Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=60) as r:
            return None if r.status == 204 else json.loads(r.read() or b"{}")


# --- storage -----------------------------------------------------------------------------------

class Storage:
    """How this machine reaches the store: a path (Z:\\, \\\\lab-nas\\videos, /mnt/nas/videos) or rclone."""

    def __init__(self, cfg: dict):
        self.mode = cfg.get("mode", "path")
        self.root = Path(cfg["root"]) if self.mode == "path" else None
        self.remote = cfg.get("remote", "").rstrip("/")
        self.rclone = cfg.get("rclone", "rclone")

    def input_path(self, rel: str, scratch: Path) -> Path:
        if self.mode == "path":
            return self.root / rel                       # read directly from the share
        local = scratch / Path(rel).name
        subprocess.run([self.rclone, "copyto", f"{self.remote}/{rel}", str(local)], check=True)
        return local

    def upload(self, folder: Path, staging_rel: str):
        if self.mode == "path":
            dest = self.root / staging_rel
            dest.mkdir(parents=True, exist_ok=True)
            for f in folder.iterdir():
                if f.is_file():
                    shutil.copyfile(f, dest / f.name)
        else:
            subprocess.run([self.rclone, "copy", str(folder), f"{self.remote}/{staging_rel}"], check=True)


# --- guards ------------------------------------------------------------------------------------

def running_processes() -> set[str]:
    try:
        import psutil
        return {(p.info["name"] or "").lower() for p in psutil.process_iter(["name"])}
    except ImportError:
        cmd = ["tasklist", "/FO", "CSV", "/NH"] if os.name == "nt" else ["ps", "-eo", "comm="]
        out = subprocess.run(cmd, capture_output=True, text=True).stdout
        return {line.split(",")[0].strip('"').lower() for line in out.splitlines() if line}


def may_work(cfg: dict) -> tuple[bool, str]:
    """Recording always wins: no work while recording software runs, or outside the allowed hours."""
    busy = {p.lower() for p in cfg.get("pause_while_running", [])} & running_processes()
    if busy:
        return False, f"recording software running ({', '.join(sorted(busy))})"
    hours = cfg.get("active_hours")                     # e.g. "18:00-07:00"
    if hours:
        start, end = (dt.time.fromisoformat(h) for h in hours.split("-"))
        now = dt.datetime.now().time()
        inside = start <= now < end if start < end else (now >= start or now < end)
        if not inside:
            return False, f"outside active hours {hours}"
    return True, ""


# --- one run -----------------------------------------------------------------------------------

def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def heartbeat(api: Api, run_id: int, stop: threading.Event, cancelled: threading.Event, every: int = 60):
    while not stop.wait(every):
        try:
            if api.post(f"runs/{run_id}/heartbeat").get("cancel"):
                cancelled.set()
                return
        except Exception as e:  # noqa: BLE001 - a missed heartbeat is not fatal
            log(f"heartbeat failed: {e}")


def execute(cfg: dict, api: Api, storage: Storage, spec: dict) -> None:
    run_id, tool_key = spec["run"], f"{spec['tool']}@{spec['version']}"
    work = Path(cfg["scratch"]) / f"run-{run_id}"
    shutil.rmtree(work, ignore_errors=True)
    (work / "in").mkdir(parents=True)
    out = work / "out"
    out.mkdir()
    stop, cancelled = threading.Event(), threading.Event()
    beat = threading.Thread(target=heartbeat, args=(api, run_id, stop, cancelled), daemon=True)
    beat.start()
    output_lines: list[str] = []
    try:
        inputs = [{**i, "path": str(storage.input_path(i["rel_path"], work / "in"))} for i in spec["inputs"]]
        (work / "inputs.json").write_text(json.dumps(inputs, indent=2))
        (work / "params.json").write_text(json.dumps(spec["params"], indent=2))
        cmd = [*cfg["tools"][tool_key]["command"],
               "--inputs", str(work / "inputs.json"), "--params", str(work / "params.json"), "--out", str(out)]
        log(f"run {run_id}: {tool_key} for {spec.get('recording')}")
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
        for line in proc.stdout:
            output_lines.append(line.rstrip())
            if cancelled.is_set():
                proc.kill()
        if proc.wait() != 0:
            raise RuntimeError("cancelled by the server" if cancelled.is_set() else f"tool exited with {proc.returncode}")

        outputs = json.loads((out / "outputs.json").read_text())["outputs"]
        files = [{"path": f.name, "sha256": sha256_of(f), "size_bytes": f.stat().st_size}
                 for f in sorted(out.iterdir()) if f.is_file() and f.name != "outputs.json"]
        storage.upload(out, spec["staging"])
        api.post(f"runs/{run_id}/complete", {"files": files, "outputs": outputs, "log": "\n".join(output_lines[-300:])})
        log(f"run {run_id}: done, {len(files)} file(s) handed over")
    except Exception as e:  # noqa: BLE001 - report every failure to the server
        detail = "".join(traceback.format_exception_only(e)).strip()
        log(f"run {run_id}: failed: {detail}")
        try:
            api.post(f"runs/{run_id}/fail", {"error": detail, "log": "\n".join(output_lines[-300:])})
        except Exception as e2:  # noqa: BLE001
            log(f"could not report the failure: {e2}")
    finally:
        stop.set()
        if not cfg.get("keep_scratch"):
            shutil.rmtree(work, ignore_errors=True)


# --- main loop ---------------------------------------------------------------------------------

def work_once(cfg: dict, api: Api, storage: Storage) -> bool:
    ok, why = may_work(cfg)
    if not ok:
        log(f"waiting: {why}")
        return False
    spec = api.post("runs/claim", {"tools": list(cfg["tools"])})
    if not spec:
        return False
    execute(cfg, api, storage, spec)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()
    cfg = tomllib.loads(Path(args.config).read_text())
    if os.name != "nt":
        os.umask(0o002)                                  # staging files stay writable for the NAS group
    api, storage = Api(cfg["server"], cfg["token"]), Storage(cfg["storage"])
    log(f"worker {cfg.get('name', '?')} offering {', '.join(cfg['tools'])}")
    while True:
        try:
            did = work_once(cfg, api, storage)
        except (urllib.error.URLError, OSError) as e:
            log(f"server not reachable: {e}")
            did = False
        if args.once:
            return
        if not did:
            time.sleep(cfg.get("poll_seconds", 30))


if __name__ == "__main__":
    sys.exit(main())
