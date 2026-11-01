#!/usr/bin/env python3
"""Unattended, resumable i3radio TV scanner for GitHub Actions.

Scans PUBLIC site pages and writes artifacts for human validation.
It deliberately DOES NOT publish third-party streams to IPTV.m3u.
"""
import argparse
import asyncio
import csv
import datetime as dt
import json
import os
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scan_i3radio_all_tv as scanner

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "i3radio-auto"
STATE = DATA / "state.json"
INVENTORY = DATA / "all_tv_pages.json"
REPORTS = DATA / "reports"
WORKING = Path.home() / "Downloads" / "i3radio-all-results"
DEFAULT_NEXT = 600
BATCH_SIZE = 240

def store(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def load(path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))

def output(name, value):
    # GitHub Actions output variable for artifact name and path.
    filepath = os.environ.get("GITHUB_OUTPUT")
    if filepath:
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(f"{name}={value}\n")
    print(f"[OUTPUT] {name}: {value}", flush=True)

async def run(args):
    DATA.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)
    WORKING.mkdir(parents=True, exist_ok=True)
    state = load(STATE, {"next_start": DEFAULT_NEXT, "completed": False, "completed_batches": 0})
    start = int(state.get("next_start", DEFAULT_NEXT))
    if start < DEFAULT_NEXT:
        raise RuntimeError("Refusing to rescan previously completed batches automatically.")
    if state.get("completed"):
        print("All discovered profiles have already been processed.")
        output("done", "true")
        output("artifact_path", "")
        return

    # Discovery is saved to Git so that page order cannot shift between batches.
    if INVENTORY.exists():
        profiles = load(INVENTORY, [])
        if not isinstance(profiles, list) or len(profiles) <= DEFAULT_NEXT:
            raise RuntimeError("Saved inventory is missing or unexpectedly short.")
        print(f"[DISCOVERY] using {len(profiles)} persisted i3radio profiles", flush=True)
    else:
        session = scanner.requests.Session()
        session.headers["User-Agent"] = "Mozilla/5.0 IPTV-ALI i3radio catalog audit"
        profiles = scanner.discover(session, args.max_pages, args.delay)
        if len(profiles) <= DEFAULT_NEXT:
            raise RuntimeError(f"Found only {len(profiles)} profiles. Not saving or advancing.")
        store(INVENTORY, profiles)
        print(f"[DISCOVERY] saved {len(profiles)} profiles for reproducible batches", flush=True)

    if start >= len(profiles):
        state["completed"] = True
        state["total_profiles"] = len(profiles)
        state["last_update_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
        store(STATE, state)
        output("done", "true")
        output("artifact_path", "")
        return

    # Always provide exactly the same inventory used to establish the cursor.
    shutil.copyfile(INVENTORY, WORKING / "all_tv_pages.json")
    limit = min(args.limit, len(profiles) - start)
    scan_args = argparse.Namespace(
        start=start, limit=limit, max_pages=args.max_pages,
        concurrency=args.concurrency, workers=args.workers,
        observe=args.observe, probe_timeout=args.probe_timeout,
        delay=args.delay, reuse_discovery=True,
    )
    print(f"[BATCH] pages {start} through {start + limit - 1}", flush=True)
    await scanner.main(scan_args)

    folder = WORKING / f"batch-{start:05}-{start + limit - 1:05}"
    report = folder / "report.csv"
    archive = folder / "IPTV-I3RADIO-RESULTS.zip"
    if not report.is_file() or not archive.is_file():
        raise RuntimeError("Scan did not produce its expected report/ZIP. Cursor not advanced.")

    with report.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    pages_with_video = {}
    unique_video_urls = set()
    for row in rows:
        if row.get("status") == "VIDEO_OK":
            unique_video_urls.add(row["stream"])
            # A successful ffprobe is not proof of channel identity.
            pages_with_video.setdefault(row["page"], {
                "name": row.get("channel", ""),
                "genre": row.get("genre", ""),
                "page": row["page"],
            })
    if len(rows) < max(1, limit // 2):
        raise RuntimeError(f"Unexpectedly sparse report ({len(rows)} rows). Cursor not advanced.")

    tag = f"{start:05}-{start + limit - 1:05}"
    summary = {
        "range": tag,
        "scanned_pages": limit,
        "candidate_stream_urls_tested": len({r["stream"] for r in rows if r.get("stream")}),
        "distinct_video_working_urls": len(unique_video_urls),
        "pages_with_working_video": len(pages_with_video),
        "working_page_names": sorted(
            pages_with_video.values(), key=lambda x: (x["genre"], x["name"])
        ),
        "warning": "Playback probe does not establish identity, permission or uptime.",
        "zip_in_workflow_artifacts": "i3radio-" + tag,
    }
    store(REPORTS / (tag + ".json"), summary)
    state.update({
        "next_start": start + limit,
        "last_completed_range": tag,
        "last_successful_video_urls": len(unique_video_urls),
        "last_successful_pages": len(pages_with_video),
        "last_update_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "total_profiles": len(profiles),
        "completed_batches": int(state.get("completed_batches", 0)) + 1,
        "completed": start + limit >= len(profiles),
        "publication": "Candidate results only; original playlists not modified automatically.",
    })
    store(STATE, state)
    output("done", "true" if state["completed"] else "false")
    output("artifact_path", str(archive))
    output("artifact_name", "i3radio-" + tag)
    print(f"[STATE] Advanced to {state['next_start']}/{len(profiles)}. "
          f"Working video pages: {len(pages_with_video)}; URLs: {len(unique_video_urls)}", flush=True)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=BATCH_SIZE)
    parser.add_argument("--max-pages", type=int, default=60)
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--observe", type=float, default=3.0)
    parser.add_argument("--probe-timeout", type=int, default=14)
    parser.add_argument("--delay", type=float, default=0.75)
    options = parser.parse_args()
    if options.limit < 1 or options.limit > 300:
        parser.error("--limit must be between 1 and 300")
    asyncio.run(run(options))
