#!/usr/bin/env python3
"""Watch Cloud Run Job execution progress and stream milestone logs in real-time.

Usage:
    python3 scripts/watch_cloud_run.py [--execution <name>] [--job ctv-news-dub] [--project videos-billylo]
"""

import argparse
import json
import subprocess
import sys
import time

PROJECT_DEFAULT = "videos-billylo"
JOB_DEFAULT = "ctv-news-dub"
REGION_DEFAULT = "us-east4"


def get_latest_execution(job_name: str, region: str, project: str) -> str | None:
    cmd = [
        "gcloud",
        "run",
        "jobs",
        "executions",
        "list",
        f"--job={job_name}",
        f"--region={region}",
        f"--project={project}",
        "--limit=1",
        "--format=value(metadata.name)",
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res.returncode == 0 and res.stdout.strip():
        return res.stdout.strip().split("\n")[0]
    return None


def get_execution_status(exec_name: str, region: str, project: str) -> tuple[str, str]:
    """Returns (status_state, message) where status_state is 'RUNNING', 'SUCCEEDED', 'FAILED'."""
    cmd = [
        "gcloud",
        "run",
        "jobs",
        "executions",
        "describe",
        exec_name,
        f"--region={region}",
        f"--project={project}",
        "--format=json(status)",
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res.returncode != 0:
        return "UNKNOWN", res.stderr.strip()
    try:
        data = json.loads(res.stdout).get("status", {})
        conditions = data.get("conditions", [])
        for c in conditions:
            if c.get("type") == "Completed":
                if c.get("status") == "True":
                    return "SUCCEEDED", c.get("message", "Task completed successfully.")
                elif c.get("status") == "False":
                    return "FAILED", c.get("message", "Task failed.")
        if data.get("completionTime"):
            return "COMPLETED", ""
        return "RUNNING", ""
    except Exception as e:
        return "ERROR", str(e)


def main():
    parser = argparse.ArgumentParser(description="Watch Cloud Run Job execution")
    parser.add_argument("--execution", help="Execution name (e.g. ctv-news-dub-nhfq5)")
    parser.add_argument("--job", default=JOB_DEFAULT, help="Job name")
    parser.add_argument("--region", default=REGION_DEFAULT, help="GCP Region")
    parser.add_argument("--project", default=PROJECT_DEFAULT, help="GCP Project")
    parser.add_argument("--poll-interval", type=int, default=15, help="Poll interval in seconds")
    args = parser.parse_args()

    exec_name = args.execution
    if not exec_name:
        exec_name = get_latest_execution(args.job, args.region, args.project)
        if not exec_name:
            print(f"[!] No execution found for job {args.job} in {args.region}.")
            sys.exit(1)

    print(f"[*] Monitoring Cloud Run Job execution: {exec_name}")
    print(f"[*] Region: {args.region} | Project: {args.project}")
    print(
        f"[*] Console URL: https://console.cloud.google.com/run/jobs/executions/details/{args.region}/{exec_name}?project={args.project}"
    )
    print("=" * 70)

    last_timestamp = None
    seen_hashes = set()

    start_time = time.time()
    while True:
        status, msg = get_execution_status(exec_name, args.region, args.project)

        # Read new logs since last_timestamp
        query_filter = (
            f'resource.type="cloud_run_job" '
            f'resource.labels.job_name="{args.job}" '
            f'labels."run.googleapis.com/execution_name"="{exec_name}"'
        )
        if last_timestamp:
            query_filter += f' timestamp >= "{last_timestamp}"'

        cmd = [
            "gcloud",
            "logging",
            "read",
            query_filter,
            f"--project={args.project}",
            "--limit=100",
            "--order=asc",
            "--format=json(timestamp,textPayload)",
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode == 0 and res.stdout.strip():
            try:
                entries = json.loads(res.stdout)
                for entry in entries:
                    ts = entry.get("timestamp")
                    payload = entry.get("textPayload", "")
                    if not payload or not ts:
                        continue
                    entry_hash = f"{ts}_{payload[:50]}"
                    if entry_hash in seen_hashes:
                        continue
                    seen_hashes.add(entry_hash)
                    last_timestamp = ts

                    # Format timestamp
                    time_part = ts[11:19] if len(ts) >= 19 else ""
                    # Filter out spammy uvicorn health/internal checks if needed, but display milestones
                    if "GET /health" in payload and "200" in payload:
                        continue
                    if "Garbage collection succeeded" in payload:
                        continue
                    print(f"[{time_part}] {payload.strip()}")
            except Exception:
                pass

        if status in ("SUCCEEDED", "FAILED"):
            duration = int(time.time() - start_time)
            mins, secs = divmod(duration, 60)
            print("=" * 70)
            if status == "SUCCEEDED":
                print(f"[✔] Execution {exec_name} FINISHED SUCCESSFULLY in {mins}m {secs}s!")
                print("[*] Web player: https://video.billylo.ca")
                sys.exit(0)
            else:
                print(f"[X] Execution {exec_name} FAILED ({msg}) after {mins}m {secs}s.")
                sys.exit(2)

        time.sleep(args.poll_interval)


if __name__ == "__main__":
    main()
