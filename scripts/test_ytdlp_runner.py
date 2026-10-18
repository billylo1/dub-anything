#!/usr/bin/env python3
"""Diagnostic script to test yt-dlp on Cloud Run (CPU-only, zero GPU cost)."""

import os
import pathlib
import shutil
import subprocess
import sys
import time

print("=" * 60)
print(" YT-DLP DIAGNOSTIC RUNNER (CHEAP CPU SETUP)")
print(" Time:", time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()))
print("=" * 60)

# 1. System tool checks
print("\n[1] Checking installed dependencies...")
for tool in ["yt-dlp", "deno", "ffmpeg", "curl"]:
    path = shutil.which(tool)
    if path:
        print(f"  ✓ {tool}: {path}")
    else:
        print(f"  ✗ {tool}: NOT FOUND")

try:
    ytdlp_ver = subprocess.check_output(["yt-dlp", "--version"], text=True).strip()
    print(f"  yt-dlp version: {ytdlp_ver}")
except Exception as e:
    print(f"  yt-dlp error: {e}")

try:
    deno_ver = subprocess.check_output(["deno", "--version"], text=True).split("\n")[0]
    print(f"  deno version: {deno_ver}")
except Exception as e:
    print(f"  deno error: {e}")

# 2. Check cookies
print("\n[2] Checking YouTube cookies file...")
cookie_candidates = [
    os.environ.get("YOUTUBE_COOKIES_FILE"),
    "/models/youtube_cookies.txt",
    "/tmp/yt_cookies.txt",
]
cookie_path = None
for c in cookie_candidates:
    if c and pathlib.Path(c).is_file() and pathlib.Path(c).stat().st_size > 0:
        cookie_path = c
        print(f"  ✓ Found cookie file: {c} ({pathlib.Path(c).stat().st_size} bytes)")
        break

if not cookie_path:
    print("  ! No cookies file found; yt-dlp will run unauthenticated.")

# 3. Test Search Query on YouTube
print("\n[3] Testing YouTube Search Query (CTV News Toronto at Six)...")
search_cmd = [
    "yt-dlp",
    "--flat-playlist",
    "--playlist-end", "5",
    "--print", "%(id)s\t%(duration)s\t%(upload_date)s\t%(title)s",
]
if cookie_path:
    search_cmd.extend(["--cookies", cookie_path])
search_cmd.append("ytsearch5:CTV News Toronto at Six")

t0 = time.time()
res = subprocess.run(search_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
search_dur = time.time() - t0

if res.returncode != 0:
    print(f"  ✗ Search failed with returncode {res.returncode}:")
    print(res.stderr)
else:
    print(f"  ✓ Search succeeded in {search_dur:.2f}s! Results:")
    for line in res.stdout.strip().split("\n"):
        if line.strip():
            print(f"    - {line}")

# 4. Test format extraction on a target video
print("\n[4] Testing format extraction on latest episode...")
test_vid = "JwU5qB-7GVo"
fmt_cmd = [
    "yt-dlp",
    "-F",
    f"https://www.youtube.com/watch?v={test_vid}",
]
if cookie_path:
    fmt_cmd.extend(["--cookies", cookie_path])

t0 = time.time()
res_fmt = subprocess.run(fmt_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
fmt_dur = time.time() - t0

if res_fmt.returncode != 0:
    print(f"  ✗ Format extraction failed in {fmt_dur:.2f}s:")
    print(res_fmt.stderr)
else:
    print(f"  ✓ Format extraction succeeded in {fmt_dur:.2f}s!")
    lines = res_fmt.stdout.strip().split("\n")
    print("  Sample video formats available:")
    for l in lines[-15:]:
        print(f"    {l}")

# 5. Test downloading a 10-second clip to verify cipher solving and download throughput
print("\n[5] Testing downloading 10-second clip with audio+video merge...")
dl_test_path = pathlib.Path("/tmp/test_clip.mp4")
if dl_test_path.exists():
    dl_test_path.unlink()

dl_cmd = [
    "yt-dlp",
    "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
    "--download-sections", "*00:00:00-00:00:10",
    "--merge-output-format", "mp4",
    "-o", str(dl_test_path),
]
if cookie_path:
    dl_cmd.extend(["--cookies", cookie_path])
dl_cmd.append(f"https://www.youtube.com/watch?v={test_vid}")

t0 = time.time()
res_dl = subprocess.run(dl_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
dl_dur = time.time() - t0

if res_dl.returncode != 0:
    print(f"  ✗ Download test failed in {dl_dur:.2f}s:")
    print(res_dl.stderr)
else:
    if dl_test_path.exists():
        size = dl_test_path.stat().st_size
        print(f"  ✓ Download test succeeded in {dl_dur:.2f}s! Saved {size} bytes ({size / (1024*1024):.2f} MB)")
    else:
        print("  ✗ Download reported success but output file does not exist.")

print("\n" + "=" * 60)
print(" DIAGNOSTIC COMPLETE")
print("=" * 60)
