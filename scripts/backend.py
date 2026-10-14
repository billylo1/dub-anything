"""Keep the VoiceStudio backend running.

Usage: scripts/backend.py status | start | stop | restart

start launches the backend detached from the shell that asks (on Windows through
the process service, so it outlives an agent's session), with OMNIVOICE_DATA_DIR,
OMNIVOICE_CACHE_DIR and the full PATH set, and waits until /health says ok. The
log goes to backend.log beside the data folder (or $VOICESTUDIO_LOG). The
checkout is $VOICESTUDIO_DIR, or VoiceStudio beside this repo. dub_anything.py calls
ensure() before GPU work and restarts a backend that stopped answering.
"""

import argparse
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time

import requests

ROOT = pathlib.Path(__file__).resolve().parents[1]
API = os.environ.get("VOICESTUDIO_API", "http://127.0.0.1:3900")
PORT = int(re.search(r":(\d+)", API.split("//", 1)[-1]).group(1)) if re.search(r":\d+", API.split("//", 1)[-1]) else 80
VS_DIR = pathlib.Path(os.environ.get("VOICESTUDIO_DIR") or ROOT.parent / "VoiceStudio")


def user_env(name):
    """A variable as the user set it, also when this shell started before it was set."""
    if os.environ.get(name):
        return os.environ[name]
    if sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                return os.path.expandvars(winreg.QueryValueEx(k, name)[0])
        except OSError:
            return ""
    return ""


def full_env():
    """This process's environment plus the user's Dub Anything variables and the machine and
    user PATH (an agent's shell may predate uv or ffmpeg being installed)."""
    env = dict(os.environ)
    for name in ("OMNIVOICE_DATA_DIR", "OMNIVOICE_CACHE_DIR"):
        if user_env(name):
            env[name] = user_env(name)
    if sys.platform == "win32":
        import winreg

        paths = []
        for root, key in (
            (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
            (winreg.HKEY_CURRENT_USER, "Environment"),
        ):
            try:
                with winreg.OpenKey(root, key) as k:
                    paths.append(os.path.expandvars(winreg.QueryValueEx(k, "Path")[0]))
            except OSError:
                pass
        seen, parts = set(), []
        for p in (env.get("PATH", "") + ";" + ";".join(paths)).split(";"):
            if p and p.lower() not in seen:
                seen.add(p.lower())
                parts.append(p)
        env["PATH"] = ";".join(parts)
    return env


def log_path(env=None):
    env = env or full_env()
    if os.environ.get("VOICESTUDIO_LOG"):
        return pathlib.Path(os.environ["VOICESTUDIO_LOG"])
    data = env.get("OMNIVOICE_DATA_DIR")
    return (pathlib.Path(data).parent if data else VS_DIR) / "backend.log"


def health(timeout=5):
    try:
        return requests.get(f"{API}/health", timeout=timeout).json()
    except (requests.RequestException, ValueError):
        return None


def healthy():
    h = health()
    return bool(h) and h.get("status") == "ok"


def listening_pid():
    if sys.platform == "win32":
        out = subprocess.run(["netstat", "-ano", "-p", "tcp"], capture_output=True, text=True).stdout
        for line in out.splitlines():
            f = line.split()
            if len(f) >= 5 and f[3].upper() == "LISTENING" and f[1].endswith(f":{PORT}"):
                return int(f[4])
        return None
    out = subprocess.run(["lsof", "-ti", f"tcp:{PORT}", "-sTCP:LISTEN"], capture_output=True, text=True).stdout
    return int(out.split()[0]) if out.split() else None


def stop():
    pid = listening_pid()
    if not pid:
        print("backend: not running")
        return
    if sys.platform == "win32":
        # The listener is uv's Python; its parent (uv / the venv launcher) goes too.
        parent = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').ParentProcessId",
            ],
            capture_output=True,
            text=True,
        ).stdout.strip()
        for p in [pid] + ([int(parent)] if parent.isdigit() else []):
            subprocess.run(["taskkill", "/PID", str(p), "/T", "/F"], capture_output=True)
    else:
        os.kill(pid, 15)
    for _ in range(30):
        if not listening_pid():
            break
        time.sleep(1)
    print(f"backend: stopped (pid {pid})")


def start(wait=600):
    if healthy():
        print(f"backend: already up ({API})")
        return True
    if listening_pid():
        # Up but not answering "ok": still starting, or wedged. Give it a moment first.
        for _ in range(30):
            if healthy():
                print("backend: up")
                return True
            time.sleep(2)
        print("backend: not answering; restarting it")
        stop()
    env = full_env()
    log = log_path(env)
    uv = shutil.which("uv", path=env["PATH"])
    if not uv:
        raise SystemExit("backend: uv not found on PATH (see docs/setup.md)")
    if not (VS_DIR / "backend").is_dir():
        raise SystemExit(f"backend: no VoiceStudio checkout at {VS_DIR} (set VOICESTUDIO_DIR)")
    cmd = [
        uv,
        "run",
        "--no-sync",
        "uvicorn",
        "main:app",
        "--app-dir",
        "backend",
        "--host",
        re.sub(r"^\w+://|:\d+.*$", "", API),
        "--port",
        str(PORT),
    ]
    if sys.platform == "win32":
        # A child of this shell dies with an agent's session; one started by the
        # process service (WMI) doesn't. A batch file carries the environment.
        launcher = ROOT / "work" / "backend-start.cmd"
        launcher.parent.mkdir(exist_ok=True)
        lines = ["@echo off"] + [
            f'set "{k}={env[k].replace("%", "%%")}"'
            for k in ("PATH", "OMNIVOICE_DATA_DIR", "OMNIVOICE_CACHE_DIR")
            if env.get(k)
        ]
        lines += [f'cd /d "{VS_DIR}"', subprocess.list2cmdline(cmd) + f' > "{log}" 2>&1']
        launcher.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
        ps = (
            "$si = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ShowWindow=[uint16]0}; "
            f"$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{{"
            f"CommandLine='cmd.exe /c \"{launcher}\"'; CurrentDirectory='{VS_DIR}'; ProcessStartupInformation=$si}}; "
            "exit $r.ReturnValue"
        )
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True)
        if r.returncode:
            raise SystemExit(f"backend: could not start it ({r.returncode}): {r.stderr.strip()[:300]}")
    else:
        with open(log, "ab") as f:
            subprocess.Popen(cmd, cwd=VS_DIR, env=env, stdout=f, stderr=subprocess.STDOUT, start_new_session=True)
    print(f"backend: starting (log {log})", flush=True)
    t0 = time.time()
    while time.time() - t0 < wait:
        if healthy():
            print(f"backend: up after {time.time() - t0:.0f} s")
            return True
        time.sleep(3)
    print(f"backend: no answer after {wait} s; see {log}")
    return False


def ensure():
    """True once the backend answers ok, starting it if needed."""
    return healthy() or start()


def restart():
    """A fresh backend: frees the GPU memory its PyTorch cache holds (docs/directing.md, gotchas)."""
    stop()
    return start()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["status", "start", "stop", "restart"])
    args = ap.parse_args()
    if args.cmd == "status":
        h, pid = health(), listening_pid()
        print(
            f"backend: {(h or {}).get('status', 'down')}"
            + (f", {h.get('device')}" if h else "")
            + (f", pid {pid}" if pid else "")
            + f"; log {log_path()}"
        )
        sys.exit(0 if h and h.get("status") == "ok" else 1)
    if args.cmd == "stop":
        stop()
    else:
        sys.exit(0 if (restart() if args.cmd == "restart" else start()) else 1)


if __name__ == "__main__":
    main()
