#!/usr/bin/env python3
"""Automated daily pipeline for dubbing CTV News Toronto at Six into Cantonese.

Features:
  1. Automated Discovery:
     - Finds the latest full broadcast of "CTV News Toronto at Six" on YouTube
       via yt-dlp (filters by title pattern and duration >= 40 mins).
     - Extracts the broadcast date from title/metadata and checks work/ctv_history.json
       to prevent redundant processing.
  2. Flexible Execution & Manual Intervention:
     - Supports manual URL override (--url <URL>) to dub any specific broadcast.
     - Supports resuming existing jobs (--slug <SLUG>).
     - Allows running specific steps (--step [discover|download|prep|direct|render|publish|all]).
     - Dry-run mode (--dry-run) to inspect discovery and planned actions.
     - Preserves manual edits to work/<part>/script.json unless --retranslate is set.
  3. Pacing & Fit Enforcement (No OVER halt):
     - Translates with Gemini 2.5 Flash using natural broadcast Cantonese (新聞口語).
     - Automatically limits character count to ~3.8 chars/sec.
     - Runs edges.py and checks pacing with core.fit_rows.
     - Automatically condenses any lines predicted or rendered as OVER.
  4. Web Publishing:
     - Copies final dubbed video to /Users/billylo/web/video.billylo.ca/videos/ with Chinese naming:
       "CTV 多倫多新聞 YYYY年M月D日 (粵語配音).mp4" and updates "CTV 多倫多新聞 (粵語配音).mp4".
     - Rebuilds manifest.json via node generate-manifest.mjs.
     - Deploys hosting to Firebase (--project videos-billylo).

Usage:
  # Daily automated run (checks YouTube, dubs if new, publishes):
  python3 scripts/auto_dub_ctv.py

  # Check YouTube status only without processing:
  python3 scripts/auto_dub_ctv.py --step discover

  # Dub a specific YouTube URL manually:
  python3 scripts/auto_dub_ctv.py --url https://www.youtube.com/watch?v=FOjbdx4Xg-I

  # Resume rendering for an existing job:
  python3 scripts/auto_dub_ctv.py --slug ctv_toronto_20261017 --step render

  # Publish an existing delivered file to web:
  python3 scripts/auto_dub_ctv.py --slug ctv_toronto_20261017 --step publish
"""

import argparse
import concurrent.futures
import datetime
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
VIDEOS_DIR = ROOT / "videos"
WORK_DIR = ROOT / "work"
JOBS_DIR = WORK_DIR / "jobs"
SCRIPTS_DIR = ROOT / "scripts"
HISTORY_FILE = WORK_DIR / "ctv_history.json"


def find_web_dir() -> pathlib.Path:
    if os.environ.get("WEB_DIR"):
        return pathlib.Path(os.environ["WEB_DIR"])
    local_dev = pathlib.Path("/Users/billylo/web/video.billylo.ca")
    if (local_dev / "firebase.json").exists():
        return local_dev
    bundled = ROOT / "web_app"
    if (bundled / "firebase.json").exists():
        return bundled
    return local_dev


WEB_DIR = find_web_dir()
WEB_VIDEOS_DIR = WEB_DIR / "videos"

# VoiceStudio Python executable
VS_PYTHON_CANDIDATES = [
    pathlib.Path("/app/VoiceStudio/.venv/bin/python"),
    pathlib.Path("/opt/VoiceStudio/.venv/bin/python"),
    ROOT.parent / "VoiceStudio/.venv/bin/python",
    ROOT / "VoiceStudio/.venv/bin/python",
    pathlib.Path.home() / "Library/Application Support/VoiceStudio/runtime/project/.venv/bin/python",
    pathlib.Path("C:/git/VoiceStudio/.venv/Scripts/python.exe"),
]
env_vs_python = os.environ.get("VOICESTUDIO_PYTHON")
if env_vs_python:
    VS_PYTHON_CANDIDATES.insert(0, pathlib.Path(env_vs_python))

PY = sys.executable
for p in VS_PYTHON_CANDIDATES:
    if p and p.is_file():
        PY = str(p)
        break

# Month name mapping for date parsing
MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "sept": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


def log(msg: str):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [*] {msg}", flush=True)


def warn(msg: str):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [!] WARNING: {msg}", flush=True)


def error(msg: str):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [!] ERROR: {msg}", file=sys.stderr, flush=True)


def check_tool(name: str):
    if not shutil.which(name):
        error(f"Required command '{name}' not found on PATH.")
        sys.exit(1)


def load_history() -> dict:
    if HISTORY_FILE.exists():
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            warn(f"Failed to read history file {HISTORY_FILE}: {e}")
    return {"processed": {}}


def save_history(history: dict):
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def parse_broadcast_date(title: str, upload_date: str = "") -> tuple[int, int, int]:
    """Extract (year, month, day) from title or fallback upload_date."""
    m = re.search(
        r"CTV News Toronto at Six for\s+(?:[A-Za-z]+,\s*)?([A-Za-z]+)\.?\s+(\d{1,2}),?\s+(\d{4})",
        title,
        re.IGNORECASE,
    )
    if m:
        month_str = m.group(1).lower().rstrip(".")
        day = int(m.group(2))
        year = int(m.group(3))
        month = MONTHS.get(month_str, 1)
        return year, month, day

    if upload_date and len(upload_date) == 8 and upload_date.isdigit():
        return int(upload_date[:4]), int(upload_date[4:6]), int(upload_date[6:8])

    now = datetime.datetime.now()
    return now.year, now.month, now.day


def discover_latest_episode() -> dict | None:
    """Find the latest full episode of CTV News Toronto at Six on YouTube."""
    check_tool("yt-dlp")
    log("Searching YouTube for latest 'CTV News Toronto at Six' broadcast...")

    cookies = find_yt_cookies()
    candidates = []
    seen_ids = set()

    search_queries = [
        ("ytsearch25:CTV News Toronto at Six", 25),
        ("https://www.youtube.com/@CTVNews/videos", 50),
    ]

    for query, limit in search_queries:
        cmd = [
            "yt-dlp",
            "--remote-components",
            "ejs:github",
            "--flat-playlist",
            "--playlist-end",
            str(limit),
            "--print",
            "%(id)s\t%(duration)s\t%(upload_date)s\t%(title)s",
        ]
        if cookies:
            cmd.extend(["--cookies", cookies])
        cmd.append(query)

        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode == 0 and res.stdout.strip():
            for line in res.stdout.strip().split("\n"):
                parts = line.split("\t")
                if len(parts) >= 4:
                    vid, dur_str, udate, title = parts[0], parts[1], parts[2], parts[3]
                    if vid not in seen_ids:
                        seen_ids.add(vid)
                        try:
                            dur = float(dur_str) if dur_str and dur_str != "NA" else 0
                        except ValueError:
                            dur = 0
                        candidates.append({"id": vid, "duration": dur, "upload_date": udate, "title": title})

    # Filter for full broadcast: title contains "CTV News Toronto at Six" and duration >= 2000s
    full_episodes = []
    for c in candidates:
        title_lower = c["title"].lower()
        if "ctv news toronto at six" in title_lower and c["duration"] >= 2000:
            year, month, day = parse_broadcast_date(c["title"], c["upload_date"])
            date_str = f"{year:04d}-{month:02d}-{day:02d}"
            c["year"] = year
            c["month"] = month
            c["day"] = day
            c["date_str"] = date_str
            c["url"] = f"https://www.youtube.com/watch?v={c['id']}"
            full_episodes.append(c)

    if not full_episodes:
        warn("No matching full episode of CTV News Toronto at Six found in search results.")
        return None

    # Sort descending by broadcast date
    full_episodes.sort(key=lambda x: (x["year"], x["month"], x["day"]), reverse=True)
    latest = full_episodes[0]
    log(f"Found latest episode: '{latest['title']}' ({latest['date_str']}, duration: {int(latest['duration']) // 60}m)")
    return latest


def ensure_backend():
    """Ensure VoiceStudio backend is active."""
    log("Checking VoiceStudio backend status...")
    status_cmd = [PY, str(SCRIPTS_DIR / "backend.py"), "status"]
    res = subprocess.run(status_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res.returncode == 0 and "up" in res.stdout.lower():
        log("VoiceStudio backend is already up.")
        return
    log("Starting VoiceStudio backend...")
    start_cmd = [PY, str(SCRIPTS_DIR / "backend.py"), "start"]
    res = subprocess.run(start_cmd)
    if res.returncode != 0:
        error("Failed to start VoiceStudio backend.")
        sys.exit(1)


def ensure_voices_synced():
    """Sync voices.json and reference wavs from /models/voices/ if available."""
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    voices_json = WORK_DIR / "voices.json"
    voices_dir = WORK_DIR / "voices"
    voices_dir.mkdir(parents=True, exist_ok=True)

    src_models = pathlib.Path("/models/voices")
    if src_models.exists():
        src_json = src_models / "voices.json"
        if src_json.exists() and (not voices_json.exists() or voices_json.stat().st_size == 0):
            log(f"Syncing voices.json from {src_json}...")
            shutil.copy2(src_json, voices_json)

        wav_files = list(src_models.glob("*.wav")) + list((src_models / "voices").glob("*.wav"))
        copied = 0
        for w in wav_files:
            dest = voices_dir / w.name
            if not dest.exists() or dest.stat().st_size == 0:
                try:
                    shutil.copy2(w, dest)
                    copied += 1
                except Exception:
                    pass
        if copied > 0:
            log(f"Synced {copied} voice reference audio files to {voices_dir}.")


def find_yt_cookies() -> str | None:
    candidates = []
    env_cookie = os.environ.get("YOUTUBE_COOKIES_FILE")
    if env_cookie:
        candidates.append(pathlib.Path(env_cookie))
    candidates.extend(
        [
            pathlib.Path("/models/youtube_cookies.txt"),
            ROOT / "youtube_cookies.txt",
            pathlib.Path("/tmp/yt_cookies.txt"),
        ]
    )
    for c in candidates:
        if c.is_file() and c.stat().st_size > 0:
            local_target = pathlib.Path("/tmp/yt_cookies_local.txt")
            if c != local_target:
                try:
                    shutil.copy2(c, local_target)
                    return str(local_target)
                except Exception:
                    return str(c)
            return str(c)
    return None


_SYNCED_FINAL_PARTS = set()


def get_checkpoint_dir(slug: str) -> pathlib.Path | None:
    models_dir = pathlib.Path("/models")
    if models_dir.is_dir():
        cp = models_dir / "checkpoints" / slug
        try:
            cp.mkdir(parents=True, exist_ok=True)
            return cp
        except Exception as e:
            warn(f"Failed to create checkpoint directory {cp}: {e}")
    return None


def save_part_checkpoint(slug: str, part_name: str):
    cp = get_checkpoint_dir(slug)
    if not cp:
        return
    src_work = WORK_DIR / part_name
    if src_work.is_dir():
        dst_work = cp / "work" / part_name
        dst_work.mkdir(parents=True, exist_ok=True)
        for item in src_work.iterdir():
            if item.is_file():
                dst_file = dst_work / item.name
                try:
                    if not dst_file.exists() or dst_file.stat().st_size != item.stat().st_size:
                        shutil.copy2(item, dst_file)
                except Exception as e:
                    warn(f"Checkpoint save error for {item.name}: {e}")
            elif item.is_dir() and item.name in ("takes", "refs"):
                dst_sub = dst_work / item.name
                dst_sub.mkdir(parents=True, exist_ok=True)
                for subitem in item.iterdir():
                    if subitem.is_file():
                        dst_file = dst_sub / subitem.name
                        try:
                            if not dst_file.exists() or dst_file.stat().st_size != subitem.stat().st_size:
                                shutil.copy2(subitem, dst_file)
                        except Exception as e:
                            warn(f"Checkpoint save error for {subitem.name}: {e}")
    # Copy dubbed mp4 if generated
    dubbed_dir = VIDEOS_DIR / f"{slug}-parts" / "dubbed"
    if dubbed_dir.is_dir():
        cp_dubbed = cp / "dubbed"
        cp_dubbed.mkdir(parents=True, exist_ok=True)
        for f in dubbed_dir.glob(f"{part_name}.*.mp4"):
            dst_file = cp_dubbed / f.name
            try:
                if not dst_file.exists() or dst_file.stat().st_size != f.stat().st_size:
                    shutil.copy2(f, dst_file)
            except Exception as e:
                warn(f"Checkpoint save error for {f.name}: {e}")


def sync_all_completed_checkpoints(slug: str):
    cp = get_checkpoint_dir(slug)
    if not cp:
        return
    parts = sorted([p.name for p in WORK_DIR.glob(f"{slug}-*") if (p / "state.json").exists()])
    parts_filter = os.environ.get("PARTS_FILTER")
    if parts_filter:
        allowed = set(p.strip() for p in parts_filter.split(","))
        parts = [
            p
            for p in parts
            if p in allowed or p.rsplit("-", 1)[-1] in allowed or f"{int(p.rsplit('-', 1)[-1]):02d}" in allowed
        ]

    for part in parts:
        if part in _SYNCED_FINAL_PARTS:
            continue
        try:
            with open(WORK_DIR / part / "state.json", "r", encoding="utf-8") as f:
                st = json.load(f)
            if st.get("stage") == "final":
                log(f"Syncing completed checkpoint for {part} to GCS...")
                save_part_checkpoint(slug, part)
                _SYNCED_FINAL_PARTS.add(part)
                log(f"Successfully synced checkpoint for {part}.")
        except Exception as e:
            warn(f"Failed to sync checkpoint for {part}: {e}")


def restore_checkpoints(slug: str):
    cp = get_checkpoint_dir(slug)
    if not cp or not cp.is_dir():
        return
    # Restore video
    cp_video = cp / f"{slug}.mp4"
    local_video = VIDEOS_DIR / f"{slug}.mp4"
    if cp_video.exists() and not local_video.exists():
        try:
            log(f"Restoring video from checkpoint: {cp_video} -> {local_video.name}...")
            shutil.copy2(cp_video, local_video)
        except Exception as e:
            warn(f"Failed to restore video from checkpoint: {e}")

    # Restore parts
    cp_work = cp / "work"
    if cp_work.is_dir():
        for p in cp_work.iterdir():
            if not p.is_dir():
                continue
            dst_p = WORK_DIR / p.name
            st_file = dst_p / "state.json"
            cp_st_file = p / "state.json"
            if not cp_st_file.exists():
                continue
            try:
                with open(cp_st_file, "r", encoding="utf-8") as f:
                    cp_st = json.load(f)
            except Exception:
                continue

            if cp_st.get("stage") != "final":
                continue

            local_is_final = False
            if st_file.exists():
                try:
                    with open(st_file, "r", encoding="utf-8") as f:
                        loc_st = json.load(f)
                    local_is_final = loc_st.get("stage") == "final"
                except Exception:
                    pass

            if not local_is_final:
                try:
                    log(f"Restoring completed checkpoint for {p.name}...")
                    shutil.copytree(p, dst_p, dirs_exist_ok=True)
                except Exception as e:
                    warn(f"Failed to restore {p.name} from checkpoint: {e}")

            _SYNCED_FINAL_PARTS.add(p.name)

    # Restore dubbed mp4s
    cp_dubbed = cp / "dubbed"
    if cp_dubbed.is_dir():
        dst_dubbed = VIDEOS_DIR / f"{slug}-parts" / "dubbed"
        dst_dubbed.mkdir(parents=True, exist_ok=True)
        for f in cp_dubbed.glob("*.mp4"):
            target_f = dst_dubbed / f.name
            if not target_f.exists() or target_f.stat().st_size != f.stat().st_size:
                try:
                    shutil.copy2(f, target_f)
                except Exception:
                    pass


def download_episode(url: str, slug: str, quality: str | None = None) -> pathlib.Path:
    """Download video with yt-dlp."""
    VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
    target = VIDEOS_DIR / f"{slug}.mp4"
    if target.exists() and target.stat().st_size > 1024 * 1024:
        log(f"Video already exists at {target.name} ({target.stat().st_size // (1024 * 1024)} MB), skipping download.")
        return target

    cp = get_checkpoint_dir(slug)
    if cp:
        cp_video = cp / f"{slug}.mp4"
        if cp_video.exists() and cp_video.stat().st_size > 1024 * 1024:
            log(f"Restoring video from checkpoint: {cp_video} -> {target.name}...")
            shutil.copy2(cp_video, target)
            return target

    fmt = quality or "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
    log(f"Downloading video from {url} -> {target.name}...")
    cmd = [
        "yt-dlp",
        "--remote-components",
        "ejs:github",
        "-f",
        fmt,
        "--merge-output-format",
        "mp4",
        "-o",
        str(VIDEOS_DIR / f"{slug}.%(ext)s"),
    ]
    cookies = find_yt_cookies()
    if cookies:
        log(f"Using YouTube cookies from: {cookies}")
        cmd.extend(["--cookies", cookies])
    cmd.append(url)

    res = subprocess.run(cmd)
    if res.returncode != 0:
        error("yt-dlp download failed.")
        sys.exit(1)
    if not target.exists():
        matches = list(VIDEOS_DIR.glob(f"{slug}.*"))
        if matches:
            target = matches[0]
        else:
            error("Downloaded video file not found.")
            sys.exit(1)

    if cp and target.exists() and target.stat().st_size > 1024 * 1024:
        cp_video = cp / f"{slug}.mp4"
        if not cp_video.exists() or cp_video.stat().st_size != target.stat().st_size:
            try:
                log(f"Caching downloaded video to checkpoint: {cp_video}...")
                shutil.copy2(target, cp_video)
            except Exception as e:
                warn(f"Failed to cache video to checkpoint: {e}")

    return target


def init_job(video_path: pathlib.Path, slug: str, minutes: float = 6.0):
    """Initialize job in dub_anything.py if not already initialized."""
    job_file = JOBS_DIR / slug / "job.json"
    if job_file.exists():
        log(f"Job '{slug}' already initialized.")
        return

    log(f"Initializing job '{slug}' (source: en -> target: yue, voices: actor, split: {minutes}m)...")
    cmd = [
        PY,
        str(SCRIPTS_DIR / "dub_anything.py"),
        "new",
        str(video_path),
        "--slug",
        slug,
        "--source",
        "en",
        "--target",
        "yue",
        "--voices",
        "actor",
        "--minutes",
        str(minutes),
    ]
    res = subprocess.run(cmd)
    if res.returncode != 0:
        error("dub_anything.py new failed.")
        sys.exit(1)


def ensure_split(slug: str, video_path: pathlib.Path, minutes: float = 6.0) -> list[dict]:
    """Ensure the video is split into parts and return parts list."""
    parts_dir = VIDEOS_DIR / f"{slug}-parts"
    manifest = parts_dir / "parts.json"

    # Check if parts are cached in GCS checkpoint
    cp = get_checkpoint_dir(slug)
    if cp:
        cp_parts_dir = cp / "parts"
        if (cp_parts_dir / "parts.json").exists() and not manifest.exists():
            try:
                log(f"Restoring split parts from checkpoint: {cp_parts_dir} -> {parts_dir}...")
                shutil.copytree(cp_parts_dir, parts_dir, dirs_exist_ok=True)
                if manifest.exists():
                    with open(manifest, "r", encoding="utf-8") as f:
                        return json.load(f).get("parts", [])
            except Exception as e:
                warn(f"Failed to restore split parts from checkpoint: {e}")

    if manifest.exists():
        with open(manifest, "r", encoding="utf-8") as f:
            return json.load(f).get("parts", [])

    log(f"Splitting '{video_path.name}' into {minutes}m parts...")
    parts_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        PY,
        str(SCRIPTS_DIR / "split.py"),
        str(video_path),
        "--minutes",
        str(minutes),
        "--out",
        str(parts_dir),
    ]
    res = subprocess.run(cmd)
    if res.returncode != 0:
        error("split.py failed.")
        sys.exit(1)

    # Cache split parts to checkpoint
    if cp and manifest.exists():
        try:
            cp_parts_dir = cp / "parts"
            cp_parts_dir.mkdir(parents=True, exist_ok=True)
            for item in parts_dir.iterdir():
                if item.is_file():
                    shutil.copy2(item, cp_parts_dir / item.name)
            log(f"Cached split parts to checkpoint: {cp_parts_dir}")
        except Exception as e:
            warn(f"Failed to cache split parts to checkpoint: {e}")

    if manifest.exists():
        with open(manifest, "r", encoding="utf-8") as f:
            return json.load(f).get("parts", [])
    return []


def translate_chunk_gemini(chunk_lines: list, start_offset: int, api_key: str) -> dict[int, str]:
    """Translate a chunk of lines into broadcast Cantonese using Gemini."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"

    prompt = (
        "You are an expert Cantonese dubbing director and translator for television news (香港/多倫多電視新聞播報標準).\n"
        "Translate each English line into natural, professional, spoken broadcast Cantonese (廣東話新聞口語).\n\n"
        "Rules:\n"
        "1. Style: Authoritative news broadcast Cantonese (e.g. 今日, 報導, 表示, 指出, 已經, 當中, 情況).\n"
        "2. Names & Terms: Keep authentic local terminology:\n"
        "   - Michelle Dubé -> Michelle / 杜碧 / 主播\n"
        "   - Melissa Duggan -> Melissa / 杜根 / 記者\n"
        "   - Olivia Chow -> 鄒至蕙\n"
        "   - Brad Bradford -> 彭德福 / 布拉德福德\n"
        "   - Toronto -> 多倫多\n"
        "   - TPS -> 多倫多警察局\n"
        "   - DRPS -> 杜咸區警察局\n"
        "   - Oshawa -> 奧沙華\n"
        "3. PACING & LENGTH CONSTRAINT: Cantonese speaking rate is ~3.8 characters per second.\n"
        "   Each line specifies its max character limit [max: N chars]. You MUST NOT exceed this length!\n"
        "4. Output format: Return pure JSON array of objects with keys 'i' (int index matching input) and 'text' (Cantonese translation).\n\n"
        "Lines to translate:\n"
    )

    for i, line in enumerate(chunk_lines):
        global_idx = start_offset + i
        dur = max(line.get("end", 0.0) - line.get("start", 0.0), 1.0)
        max_chars = max(int(dur * 3.8), 4)
        speaker = line.get("voice", "anchor")
        en_text = line.get("en", "")
        prompt += f"[{global_idx}] [dur: {dur:.1f}s, max: {max_chars} chars, speaker: {speaker}]: {en_text}\n"

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json"},
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        content = data["candidates"][0]["content"]["parts"][0]["text"]
        parsed = json.loads(content)
        return {int(item["i"]): item["text"] for item in parsed if "i" in item and "text" in item}


def translate_with_gemini(lines: list, api_key: str) -> list[str]:
    """Translate lines into broadcast Cantonese using Gemini in parallel batches."""
    if not lines:
        return []
    chunk_size = 25
    chunks = [(lines[i : i + chunk_size], i) for i in range(0, len(lines), chunk_size)]
    trans_map = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(translate_chunk_gemini, chunk, offset, api_key): offset for chunk, offset in chunks}
        for fut in concurrent.futures.as_completed(futures):
            try:
                res = fut.result()
                trans_map.update(res)
            except Exception as e:
                warn(f"Gemini chunk translation failed: {e}")
    return [trans_map.get(i, "") for i in range(len(lines))]


def translate_fallback(text: str) -> str:
    """Fallback Google Translate endpoint."""
    if not text.strip():
        return ""
    q = urllib.parse.quote(text)
    url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=zh-TW&dt=t&q={q}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return "".join(part[0] for part in data[0] if part[0])
    except Exception:
        return text


def condense_line_gemini(text: str, en_text: str, target_chars: int, api_key: str) -> str:
    """Condense a single line that exceeds its timing window."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
    prompt = (
        f"The following Cantonese news line is too long for its broadcast timing window:\n"
        f"Original Cantonese: {text}\n"
        f"Original English: {en_text}\n\n"
        f"Condense it into concise, fluent spoken Cantonese (廣東話口語) with AT MOST {target_chars} characters, "
        f"keeping the core news fact intact. Return ONLY the condensed Cantonese text without quotes or punctuation explanations."
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            res = data["candidates"][0]["content"]["parts"][0]["text"].strip()
            # Clean up potential markdown or quotes
            res = re.sub(r'["`]', "", res).strip()
            return res[:target_chars] if len(res) > target_chars + 2 else res
    except Exception:
        # Fallback mechanical truncation
        return text[:target_chars]


def check_and_fix_overflows(part: str, api_key: str | None) -> int:
    """Run core.fit_rows on a part to predict overflows and automatically shorten any OVER lines."""
    code = f"""
import sys, json
sys.path.insert(0, 'scripts')
import core
rows = list(core.fit_rows('{part}', predict=True))
over_rows = [
    {{
        'i': r['i'],
        'text': r['text'],
        'window': r['window'],
        'room': r['room'],
        'speech': float(r['speech']) if r.get('speech') is not None else None,
        'flag': r['flag']
    }}
    for r in rows if r.get('flag') in ('OVER', 'tight')
]
print(json.dumps(over_rows))
"""
    res = subprocess.run([PY, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res.returncode != 0:
        return 0

    try:
        flagged = json.loads(res.stdout.strip().split("\n")[-1])
    except Exception:
        return 0

    over_items = [item for item in flagged if item.get("flag") == "OVER"]
    if not over_items:
        return 0

    log(f"{part}: Detected {len(over_items)} line(s) exceeding timing window (OVER). Auto-condensing...")
    script_file = WORK_DIR / part / "script.json"
    if not script_file.exists():
        return 0
    with open(script_file, "r", encoding="utf-8") as f:
        script = json.load(f)

    lines = script.get("lines", [])
    fixed_count = 0
    remove_indices = set()
    for item in over_items:
        idx = item["i"]
        if 0 <= idx < len(lines):
            cur = lines[idx]
            room = item.get("room") or cur.get("end", 0.0) - cur.get("start", 0.0)
            orig_text = cur.get("text", "")
            # If room is too small (< 0.65s) or text is already <= 2 chars and still OVER, drop the line
            if room < 0.65 or len(orig_text.strip()) <= 2:
                log(
                    f"  {part} L{idx:02d}: Room {room:.2f}s is below minimum TTS duration threshold (or text already minimal '{orig_text}'). Dropping line to prevent block."
                )
                remove_indices.add(idx)
                fixed_count += 1
                continue

            target_chars = max(int(room * 3.5), 2)
            orig_text = cur.get("text", "")
            en_text = cur.get("en", "")
            if api_key:
                new_text = condense_line_gemini(orig_text, en_text, target_chars, api_key)
            else:
                new_text = orig_text[:target_chars]
            if len(new_text) >= len(orig_text):
                new_text = orig_text[: max(1, len(orig_text) - 2)]
            log(f"  L{idx:02d}: '{orig_text}' ({len(orig_text)} chars) -> '{new_text}' ({len(new_text)} chars)")
            cur["text"] = new_text
            fixed_count += 1

    if remove_indices:
        script["lines"] = [line for i, line in enumerate(lines) if i not in remove_indices]

    with open(script_file, "w", encoding="utf-8") as f:
        json.dump(script, f, ensure_ascii=False, indent=1)

    return fixed_count


def check_and_fix_pacing_cpu(part: str, api_key: str | None) -> int:
    """Fast, pure-CPU character pacing check without acquiring the GPU lock."""
    script_file = WORK_DIR / part / "script.json"
    if not script_file.exists():
        return 0
    try:
        with open(script_file, "r", encoding="utf-8") as f:
            script = json.load(f)
    except Exception:
        return 0

    lines = script.get("lines", [])
    fixed_count = 0
    for idx, cur in enumerate(lines):
        dur = cur.get("end", 0.0) - cur.get("start", 0.0)
        target_chars = max(int(dur * 3.8), 3)
        orig_text = cur.get("text", "")
        c_count = len([c for c in orig_text if not c.isspace()])
        if c_count > target_chars + 2:
            en_text = cur.get("en", "")
            if api_key:
                new_text = condense_line_gemini(orig_text, en_text, target_chars, api_key)
            else:
                new_text = orig_text[:target_chars]
            log(
                f"  {part} L{idx:02d} CPU pacing: '{orig_text}' ({c_count} chars) -> '{new_text}' ({len(new_text)} chars) for {dur:.1f}s"
            )
            cur["text"] = new_text
            fixed_count += 1

    if fixed_count > 0:
        with open(script_file, "w", encoding="utf-8") as f:
            json.dump(script, f, ensure_ascii=False, indent=1)
    return fixed_count


def direct_single_part(
    part: str,
    api_key: str | None,
    voices_spec: dict,
    default_voice: str,
    retranslate: bool = False,
) -> bool:
    part_dir = WORK_DIR / part
    script_file = part_dir / "script.json"
    draft_file = part_dir / "draft.json"

    if not draft_file.exists():
        return False

    # Check if already directed and marked ready (preserve manual edits)
    if script_file.exists() and not retranslate:
        try:
            with open(script_file, "r", encoding="utf-8") as f:
                sc = json.load(f)
            if sc.get("ready") is True:
                return False
        except Exception:
            pass

    try:
        with open(draft_file, "r", encoding="utf-8") as f:
            draft = json.load(f)
    except Exception as e:
        warn(f"{part}: Failed to read draft.json: {e}")
        return False

    raw_lines = draft.get("lines", [])
    lines = []
    for i, line in enumerate(raw_lines):
        dur = line.get("end", 0.0) - line.get("start", 0.0)
        if dur < 0.4:
            log(f"{part}: Filtering out glitch blip L{i:02d} ({dur:.2f}s): '{line.get('en', '')}'")
            continue
        lines.append(line)

    if not lines:
        log(f"{part}: empty draft, marking ready with no lines.")
        with open(script_file, "w", encoding="utf-8") as f:
            json.dump({"ready": True, "lines": []}, f, ensure_ascii=False, indent=1)
        return True

    log(f"{part}: Translating {len(lines)} lines into Cantonese...")
    cantonese_texts = []
    if api_key:
        cantonese_texts = translate_with_gemini(lines, api_key)

    script_lines = []
    for i, line in enumerate(lines):
        text = cantonese_texts[i] if i < len(cantonese_texts) and cantonese_texts[i] else ""
        if not text:
            text = translate_fallback(line.get("en", ""))
        assigned_voice = line.get("voice") or ""
        if not assigned_voice or assigned_voice not in voices_spec:
            assigned_voice = default_voice
        sl = {
            "start": line.get("start", 0.0),
            "end": line.get("end", 0.0),
            "voice": assigned_voice,
            "text": text,
        }
        if "en" in line:
            sl["en"] = line["en"]
        script_lines.append(sl)

    script_data = {"lines": script_lines, "ready": False}
    with open(script_file, "w", encoding="utf-8") as f:
        json.dump(script_data, f, ensure_ascii=False, indent=1)

    # Apply audio edge adjustments
    log(f"{part}: Applying audio edge adjustments (edges.py)...")
    edge_cmd = [PY, str(SCRIPTS_DIR / "edges.py"), part, "--apply"]
    subprocess.run(edge_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    # Fast CPU character pacing check (marks ready in milliseconds without contending for GPU lock)
    check_and_fix_pacing_cpu(part, api_key)

    # Mark ready
    with open(script_file, "r", encoding="utf-8") as f:
        script_data = json.load(f)
    script_data["ready"] = True
    with open(script_file, "w", encoding="utf-8") as f:
        json.dump(script_data, f, ensure_ascii=False, indent=1)
    log(f"{part}: Marked ready.")
    return True


def direct_parts(slug: str, retranslate: bool = False):
    """Direct all parts in parallel: translate, apply edge alignments, optimize pacing, mark ready."""
    api_key = os.environ.get("GEMINI_API_KEY")
    parts = sorted([p.name for p in WORK_DIR.glob(f"{slug}-*") if (p / "draft.json").exists()])
    parts_filter = os.environ.get("PARTS_FILTER")
    if parts_filter:
        allowed = set(p.strip() for p in parts_filter.split(","))
        parts = [
            p
            for p in parts
            if p in allowed or p.rsplit("-", 1)[-1] in allowed or f"{int(p.rsplit('-', 1)[-1]):02d}" in allowed
        ]
    if not parts:
        return

    # Filter parts that need directing
    pending_parts = []
    for part in parts:
        script_file = WORK_DIR / part / "script.json"
        if script_file.exists() and not retranslate:
            try:
                with open(script_file, "r", encoding="utf-8") as f:
                    sc = json.load(f)
                if sc.get("ready") is True:
                    continue
            except Exception:
                pass
        pending_parts.append(part)

    if not pending_parts:
        return

    log(f"Directing {len(pending_parts)} pending parts for '{slug}' in parallel...")
    voices_spec = {}
    if (WORK_DIR / "voices.json").exists():
        try:
            with open(WORK_DIR / "voices.json", "r", encoding="utf-8") as f:
                voices_spec = json.load(f)
        except Exception:
            pass
    default_voice = (
        "michelle_dube"
        if "michelle_dube" in voices_spec
        else (next(iter(voices_spec.keys())) if voices_spec else "actor")
    )

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = {
            pool.submit(direct_single_part, part, api_key, voices_spec, default_voice, retranslate): part
            for part in pending_parts
        }
        for fut in concurrent.futures.as_completed(futures):
            p = futures[fut]
            try:
                fut.result()
            except Exception as e:
                warn(f"Directing failed for {p}: {e}")


def run_pipeline_with_self_healing(slug: str, max_retries: int = 3) -> bool:
    """Run dub_anything.py run --watch with automated recovery on timing overflows."""
    api_key = os.environ.get("GEMINI_API_KEY")

    stop_event = threading.Event()

    def continuous_director():
        last_sync = 0.0
        while not stop_event.is_set():
            try:
                direct_parts(slug, retranslate=False)
                now = time.time()
                if now - last_sync >= 30.0:
                    sync_all_completed_checkpoints(slug)
                    last_sync = now
            except Exception as e:
                warn(f"Continuous director background pass encountered: {e}")
            stop_event.wait(5.0)

    director_thread = threading.Thread(target=continuous_director, daemon=True)
    director_thread.start()
    log(f"Started continuous automated director thread for '{slug}'.")

    try:
        for attempt in range(1 + max_retries):
            log(f"Starting pipeline watcher for '{slug}' (attempt {attempt + 1}/{1 + max_retries})...")
            # Direct any currently available drafts before starting watcher
            direct_parts(slug, retranslate=False)
            cmd = [PY, str(SCRIPTS_DIR / "dub_anything.py"), "run", slug, "--watch"]
            res = subprocess.run(cmd)

            if res.returncode == 0:
                log("Pipeline completed successfully!")
                return True

            if res.returncode == 2:
                warn("Pipeline paused: timing window overflow (OVER) or review needed.")
                if attempt < max_retries:
                    # Inspect all parts for OVER lines and fix
                    fixed_any = False
                    parts = sorted([p.name for p in WORK_DIR.glob(f"{slug}-*") if (p / "script.json").exists()])
                    for part in parts:
                        n = check_and_fix_overflows(part, api_key)
                        if n > 0:
                            fixed_any = True
                    if fixed_any:
                        log("Auto-corrected timing overflows across parts. Retrying pipeline...")
                        time.sleep(2)
                        continue
                    else:
                        log(
                            "Pipeline paused with exit code 2 but no timing overflows found. Restarting VoiceStudio backend and retrying..."
                        )
                        cmd_restart = [PY, str(SCRIPTS_DIR / "backend.py"), "restart"]
                        subprocess.run(cmd_restart)
                        time.sleep(5)
                        continue
                warn("Manual intervention requested or max retries reached.")
                print("\n" + "=" * 60)
                print("MANUAL INTERVENTION REQUIRED:")
                print("  To review and fix a part, run:")
                print(f"    {PY} scripts/fit.py <part> --only-flags")
                print("  Edit work/<part>/script.json, then resume:")
                print(f"    python3 scripts/auto_dub_ctv.py --slug {slug} --step render")
                print("=" * 60 + "\n")
                return False

            error(f"Pipeline failed with unexpected exit code {res.returncode}.")
            return False
    finally:
        stop_event.set()
        director_thread.join(timeout=2.0)

    return False


def publish_to_web(
    slug: str,
    date_tuple: tuple[int, int, int],
    title: str,
    video_id: str,
    gcs_bucket: str | None = None,
) -> bool:
    """Copy delivered video, upload to GCS if specified, and deploy to Firebase Hosting."""
    delivered = VIDEOS_DIR / f"{slug} (dubbed).mp4"
    if not delivered.exists():
        error(f"Delivered file not found at {delivered}.")
        return False

    year, month, day = date_tuple
    dated_name = f"CTV 多倫多新聞 {year}年{month}月{day}日 (粵語配音).mp4"
    primary_name = "CTV 多倫多新聞 (粵語配音).mp4"

    # Cache delivered video to checkpoint
    cp = get_checkpoint_dir(slug)
    if cp:
        try:
            log(f"Caching delivered video to checkpoint: {cp / delivered.name}...")
            shutil.copy2(delivered, cp / delivered.name)
        except Exception as e:
            warn(f"Failed to cache delivered video to checkpoint: {e}")

    # Optional GCS Upload
    target_bucket = gcs_bucket or os.environ.get("GCS_VIDEOS_BUCKET")
    if target_bucket:
        log(f"Uploading delivered video to Cloud Storage: {target_bucket}...")
        bucket_prefix = target_bucket.rstrip("/")
        cmd_dated = ["gcloud", "storage", "cp", str(delivered), f"{bucket_prefix}/{dated_name}"]
        cmd_primary = ["gcloud", "storage", "cp", str(delivered), f"{bucket_prefix}/{primary_name}"]
        subprocess.run(cmd_dated, check=False)
        subprocess.run(cmd_primary, check=False)

    # Local Web Directory Sync & Firebase Deploy
    if WEB_DIR.exists():
        WEB_VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
        target_dated = WEB_VIDEOS_DIR / dated_name
        target_primary = WEB_VIDEOS_DIR / primary_name

        log(f"Copying delivered video to {target_dated.name}...")
        shutil.copy2(delivered, target_dated)

        log(f"Updating primary latest video: {target_primary.name}...")
        shutil.copy2(delivered, target_primary)

        # Generate web manifest
        manifest_script = WEB_DIR / "generate-manifest.mjs"
        if manifest_script.exists():
            log("Regenerating web video manifest (generate-manifest.mjs)...")
            res_m = subprocess.run(["node", str(manifest_script)], cwd=str(WEB_DIR))
            if res_m.returncode != 0:
                warn("generate-manifest.mjs returned non-zero exit code.")

        # Deploy to Firebase Hosting (strict rule: always pass --project)
        log("Deploying to Firebase Hosting (project: videos-billylo)...")
        deploy_cmd = [
            "npx",
            "-y",
            "firebase-tools@latest",
            "deploy",
            "--only",
            "hosting",
            "--project",
            "videos-billylo",
            "--non-interactive",
        ]
        res_d = subprocess.run(deploy_cmd, cwd=str(WEB_DIR))
        if res_d.returncode != 0:
            error("Firebase deployment failed.")
            return False

        log("Deployment to https://video.billylo.ca successfully finished!")
    else:
        log(f"WEB_DIR ({WEB_DIR}) not found; skipped local web dir copy and hosting deploy.")

    # Record in history
    history = load_history()
    history["processed"][video_id] = {
        "slug": slug,
        "date": f"{year:04d}-{month:02d}-{day:02d}",
        "title": title,
        "completed_at": datetime.datetime.now().isoformat(),
        "file": str(delivered),
    }
    save_history(history)
    return True


def main():
    parser = argparse.ArgumentParser(description="Automate CTV Toronto News at Six daily Cantonese dubbing.")
    parser.add_argument("--url", help="YouTube video URL or ID (manual override)")
    parser.add_argument("--slug", help="Explicit job slug (defaults to ctv_toronto_YYYYMMDD)")
    parser.add_argument("--gcs-bucket", help="GCS bucket URL (e.g. gs://videos-billylo/videos)")
    parser.add_argument(
        "--step",
        choices=["discover", "download", "prep", "direct", "render", "publish", "all"],
        default="all",
        help="Pipeline step to run up to / execute (default: all)",
    )
    parser.add_argument("--force", action="store_true", help="Process even if already recorded in history")
    parser.add_argument("--dry-run", action="store_true", help="Check status and print actions without executing")
    parser.add_argument("--retranslate", action="store_true", help="Overwrite existing script.json translations")
    parser.add_argument("--quality", help="yt-dlp format selector")
    parser.add_argument("--minutes", type=float, default=6.0, help="Part split length in minutes (default: 6.0)")
    parser.add_argument("--max-retries", type=int, default=3, help="Max self-healing retries for OVER lines")
    args = parser.parse_args()

    # Step 1: Discover / Identify Target Episode
    episode_info = None
    if args.url:
        check_tool("yt-dlp")
        cookies = find_yt_cookies()
        cmd = ["yt-dlp", "--remote-components", "ejs:github", "--dump-json", "--no-playlist"]
        if cookies:
            cmd.extend(["--cookies", cookies])
        cmd.append(args.url)
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode != 0:
            error(f"yt-dlp failed to fetch info: {res.stderr.strip()}")
            sys.exit(1)
        raw = json.loads(res.stdout)
        year, month, day = parse_broadcast_date(raw.get("title", ""), raw.get("upload_date", ""))
        episode_info = {
            "id": raw.get("id"),
            "title": raw.get("title", "CTV News Toronto at Six"),
            "upload_date": raw.get("upload_date", ""),
            "year": year,
            "month": month,
            "day": day,
            "date_str": f"{year:04d}-{month:02d}-{day:02d}",
            "url": args.url,
            "duration": raw.get("duration", 0),
        }
    else:
        episode_info = discover_latest_episode()
        if not episode_info:
            log("No recent full broadcast found. Exiting.")
            sys.exit(0)

    vid_id = episode_info["id"]
    date_str = episode_info["date_str"]
    slug = args.slug or f"ctv_toronto_{episode_info['year']:04d}{episode_info['month']:02d}{episode_info['day']:02d}"

    log(f"Target broadcast: '{episode_info['title']}'")
    log(f"  Date: {date_str} | Video ID: {vid_id} | Slug: {slug}")

    # Check History
    history = load_history()
    if vid_id in history.get("processed", {}) and not args.force and args.step == "all":
        rec = history["processed"][vid_id]
        log(f"Broadcast {vid_id} ({date_str}) was already completed on {rec.get('completed_at')}.")
        log(f"File: {rec.get('file')}")
        log("Nothing to do. Use --force to re-process.")
        sys.exit(0)

    if args.dry_run or args.step == "discover":
        log(f"Dry-run / Discover complete. Target slug would be '{slug}'.")
        sys.exit(0)

    # Step 2: Download
    video_path = download_episode(episode_info["url"], slug, quality=args.quality)
    if args.step == "download":
        log("Download complete.")
        sys.exit(0)

    # Step 3: Ensure Backend & Init Job
    ensure_voices_synced()
    ensure_backend()
    init_job(video_path, slug, minutes=args.minutes)
    all_parts = ensure_split(slug, video_path, minutes=args.minutes)

    # Restore any completed checkpoints from GCS
    restore_checkpoints(slug)

    task_index = int(os.environ.get("CLOUD_RUN_TASK_INDEX", "0"))
    task_count = int(os.environ.get("CLOUD_RUN_TASK_COUNT", "1"))

    # If running with multiple tasks, clean up any old flags on task 0 and assign this task its slice of parts
    if task_count > 1 and task_index == 0:
        cp = get_checkpoint_dir(slug)
        if cp:
            for old_flag in cp.glob("task_*_done.flag"):
                try:
                    old_flag.unlink()
                except Exception:
                    pass

    if all_parts and task_count > 1:
        chunk_size = (len(all_parts) + task_count - 1) // task_count
        my_parts = all_parts[task_index * chunk_size : (task_index + 1) * chunk_size]
        my_part_names = [p["name"] for p in my_parts]
        os.environ["PARTS_FILTER"] = ",".join(p.rsplit("-", 1)[-1] for p in my_part_names)
        log(f"Multi-task active: Task {task_index}/{task_count} responsible for {len(my_parts)} parts: {my_part_names}")

    if args.step == "prep":
        log(f"Running machine prep for '{slug}'...")
        cmd_prep = [PY, str(SCRIPTS_DIR / "dub_anything.py"), "run", slug]
        subprocess.run(cmd_prep)
        log("Prep step complete.")
        sys.exit(0)

    if args.step == "direct":
        direct_parts(slug, retranslate=args.retranslate)
        log("Directing step complete.")
        sys.exit(0)

    # Step 4: Render / Process Pipeline with Self-Healing
    # In 'all' or 'render' mode, run_pipeline_with_self_healing starts the background continuous
    # director and invokes `dub_anything.py run slug --watch`. This interleaves part preparation
    # and rendering from Part 1, maximizing GPU utilization without stalling for the whole broadcast to prep.
    success = run_pipeline_with_self_healing(slug, max_retries=args.max_retries)
    sync_all_completed_checkpoints(slug)

    if not success:
        error(f"Pipeline did not finish cleanly. Manual intervention available on slug '{slug}'.")
        sys.exit(2)

    # Multi-task worker synchronization
    if task_count > 1:
        cp = get_checkpoint_dir(slug)
        if task_index > 0:
            if cp:
                flag = cp / f"task_{task_index}_done.flag"
                try:
                    with open(flag, "w", encoding="utf-8") as f:
                        f.write(f"completed at {datetime.datetime.now().isoformat()}\n")
                        f.flush()
                        os.fsync(f.fileno())
                except Exception as e:
                    warn(f"Failed to write completion flag {flag}: {e}")
            log(f"Task {task_index} finished all assigned parts and uploaded checkpoints. Exiting.")
            sys.exit(0)
        else:
            log(f"Task 0 completed its assigned parts. Waiting for tasks 1..{task_count - 1} to finish...")
            wait_timeout = 2400  # 40 mins
            start_wait = time.time()
            for i in range(1, task_count):
                flag = (cp / f"task_{i}_done.flag") if cp else None
                chunk_size = (len(all_parts) + task_count - 1) // task_count
                task_parts = all_parts[i * chunk_size : (i + 1) * chunk_size]
                last_p = task_parts[-1]["name"] if task_parts else None
                while True:
                    if flag and flag.exists():
                        break
                    if last_p and cp and (cp / "dubbed" / f"{last_p}.yue.mp4").exists():
                        break
                    if time.time() - start_wait > wait_timeout:
                        error(f"Timed out waiting for task {i} to finish.")
                        sys.exit(1)
                    time.sleep(10)
                log(f"Task {i} has finished!")

            # Restore completed parts from other tasks
            restore_checkpoints(slug)

            # Assemble and deliver entire broadcast
            log("All tasks completed! Running final assembly and delivery across all parts...")
            if "PARTS_FILTER" in os.environ:
                del os.environ["PARTS_FILTER"]
            cmd_assemble = [PY, str(SCRIPTS_DIR / "dub_anything.py"), "run", slug]
            res_a = subprocess.run(cmd_assemble)
            if res_a.returncode != 0:
                error("Final assembly and delivery failed.")
                sys.exit(1)

    if args.step == "render":
        log("Render step complete.")
        sys.exit(0)

    # Step 7: Web Publishing & Deployment
    date_tuple = (episode_info["year"], episode_info["month"], episode_info["day"])
    published = publish_to_web(slug, date_tuple, episode_info["title"], vid_id, gcs_bucket=args.gcs_bucket)
    if not published:
        error("Web publishing failed.")
        sys.exit(1)

    log(f"All steps completed successfully for '{slug}'!")


if __name__ == "__main__":
    main()
