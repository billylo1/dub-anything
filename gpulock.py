"""One GPU job at a time.

The tools share one card with the VoiceStudio backend: a render beside another
model crawls or fails with "Generation failed". Every tool calls hold_gpu() just
before it loads a model (or asks the backend to run one), waits there while
another tool holds the GPU, and keeps it until it exits. Child processes it
starts (dub.py's voice conversion) share its hold. Standard library only, so
tools in other environments (separate.py) can import it too.
"""
import json
import os
import pathlib
import sys
import time

LOCK = pathlib.Path(__file__).resolve().parent / "work" / "gpu.lock"
OWNER = LOCK.with_suffix(".owner")
_held = None


def _try_lock(f):
    if sys.platform == "win32":
        import msvcrt
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)


def owner():
    try:
        o = json.loads(OWNER.read_text(encoding="utf-8"))
        return f"{o['what']}, pid {o['pid']}, since {o['since']}"
    except (OSError, ValueError, KeyError):
        return "another tool"


def hold_gpu(what=None):
    """Wait until no other tool holds the GPU, then hold it until this process exits."""
    global _held
    if _held is not None or os.environ.get("DUBRAMA_GPU") == "held":
        return
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    f = open(LOCK, "a+")
    waited = time.time()
    while True:
        try:
            _try_lock(f)
            break
        except OSError:
            if waited:
                print(f"  waiting for the GPU (held by {owner()})", flush=True)
                waited = 0
            time.sleep(2)
    _held = f
    os.environ["DUBRAMA_GPU"] = "held"
    what = what or pathlib.Path(sys.argv[0]).name
    OWNER.write_text(json.dumps({"what": " ".join([what] + sys.argv[1:2]), "pid": os.getpid(),
                                 "since": time.strftime("%H:%M:%S")}), encoding="utf-8")
