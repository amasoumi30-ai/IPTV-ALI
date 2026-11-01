#!/usr/bin/env python3
"""Non-destructive candidate scan of 26 GEM channels in IPTV-ALI.

Only probes public HLS URLs documented by third-party community lists.
No proxy, credentials, token scraping, geo-bypass, or tuner changes.
Existing playlist and backups are never overwritten.
"""
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse
import ipaddress
import os
import subprocess
import tempfile

FOLDER = Path("/opt/iptv/playlists")
SOURCE = FOLDER / "IPTV-ALI-IRIB3-VARZESH-TEST.m3u"
OUTPUT = FOLDER / "IPTV-ALI-GEM-CANDIDATE-TEST.m3u"
REPORT = FOLDER / "IPTV-ALI-GEM-STATUS.txt"

# Historical community-published paths. Not represented as current or
# authorized by GEM Group. We never construct proxy-bypass links.
SLUGS = {
    "GEM 24B": "24b",
    "GEM Bollywood": "gem_bollywood",
    "GEM Classic": "gem_classic",
    "GEM Comedy": "gem_comedy",
    "GEM Drama": "gem_drama",
    "GEM Drama +": "gem_drama_plus",
    "GEM Entertainment": "gem_entertainment",
    "GEM Film": "gem_film",
    "GEM Fit": "gem_fit",
    "GEM Food": "gem_food",
    "GEM Junior": "gem_junior",
    "GEM Kids": "gem_kids",
    "GEM Life": "gem_life",
    "GEM Mifa": "mifa",
    "GEM Mifa +": "mifa_plus",
    "GEM Nature": "gem_nature",
    "GEM Onyx": "onyx",
    "GEM Pixel": "gem_pixel",
    "GEM River": "river",
    "GEM River +": "river_plus",
    "GEM Rubix": "gem_rubix",
    "GEM Rubix +": "gem_rubix_plus",
    "GEM Series +": "gem_series_plus",
    "GEM Sport": "gem_sport",
    "GEM TV": "gem_tv",
    "GEM TV +": "gem_tv_plus",
}

# Alternative public locations explicitly published under these labels.
EXTRA = {
    "GEM Comedy": ["http://216.66.42.47:7777/GemComedy_HD.m3u8"],
    "GEM Pixel": ["https://glserver1.tamashaland.com/live/aBCH96886YznRbGOiqTnnQ__/stream_0.m3u8"],
    "GEM TV": ["http://65.21.196.79/gem_tv/master.m3u8"],
    "GEM 24B": ["http://66.102.120.18:8000/play/a044/index.m3u8"],
    "GEM Series +": ["http://65.21.196.79/gem_series/master.m3u8"],
}
# The GEM Series link above is GEM Series, not Series+; exclude to avoid
# accidentally assigning the wrong program to a different station.
EXTRA["GEM Series +"] = []

def safe_http_url(url):
    try:
        p = urlparse(url)
        if p.scheme not in ("http", "https") or not p.hostname:
            return False
        if p.username is not None or p.password is not None:
            return False
        if p.port not in (None, 80, 443, 7777, 8000):
            return False
        try:
            return ipaddress.ip_address(p.hostname).is_global
        except ValueError:
            return p.hostname not in {"localhost", "localhost.localdomain"}
    except ValueError:
        return False

def probe(url):
    if not safe_http_url(url):
        return False
    args = ["ffprobe", "-v", "error", "-rw_timeout", "7000000",
            "-show_entries", "stream=codec_type", "-of", "csv=p=0", url]
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=11)
        return p.returncode == 0 and "video" in p.stdout and "audio" in p.stdout
    except (subprocess.TimeoutExpired, OSError):
        return False

def make_candidates(name, original):
    slug = SLUGS[name]
    host = "https://d2e40kvaojifd6.cloudfront.net/stream"
    options = [
        (f"{host}/{slug}/playlist.m3u8", "old-cloudfront-master"),
        (f"{host}/{slug}/playlist_960x540_2000k.m3u8", "old-cloudfront-sd"),
    ]
    options += [(url, "third-party-community") for url in EXTRA.get(name, [])]
    visited, result = {original}, []
    for url, kind in options:
        if url not in visited and safe_http_url(url):
            result.append((url, kind))
            visited.add(url)
    return result

def atomic_write(path, text):
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix="." + path.name + "-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

def main():
    if not SOURCE.is_file():
        raise SystemExit("Missing current IRIB3/VARZESH playlist. No changes made.")
    lines = SOURCE.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    target = []
    for i, line in enumerate(lines):
        if not line.startswith("#EXTINF"):
            continue
        name = line.rsplit(",", 1)[-1].strip()
        if name not in SLUGS:
            continue
        url_i = next((j for j in range(i+1, len(lines))
                      if lines[j].strip() and not lines[j].startswith("#")), None)
        if url_i is None or any(lines[j].startswith("#EXTINF") for j in range(i+1, url_i)):
            raise SystemExit("Invalid stream record. No changes made.")
        target.append((name, url_i, lines[url_i].strip()))
    total = sum(s.startswith("#EXTINF") for s in lines)
    if total != 68 or len(target) != 26 or len({name for name, _, _ in target}) != 26:
        raise SystemExit(f"Expected 68 entries and 26 distinct GEM channels; found {total} and {len(target)}. No changes made.")

    def check(item):
        name, index, old = item
        options = make_candidates(name, old)
        for url, origin in options:
            if probe(url):
                return name, index, url, origin, len(options)
        return name, index, None, None, len(options)

    print("Checking 26 GEM channels against historical public sources...", flush=True)
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(check, target))
    updated = 0
    reports = ["IPTV-ALI GEM candidate scan (from netcup IP)",
               f"TOTAL CHANNEL ENTRIES PRESERVED: {total}",
               f"GEM CHANNELS CHECKED: {len(target)}"]
    for name, index, url, origin, attempts in results:
        if url:
            lines[index] = url
            updated += 1
            reports.append(f"MEDIA FOUND | {name} | {origin} | attempts-available={attempts}")
        else:
            reports.append(f"NOT VERIFIED | {name} | candidates-available={attempts}")
    reports.insert(3, f"NEW GEM MEDIA STREAMS DETECTED: {updated}")
    reports += [
        "Media found only means ffprobe detected video and audio at check time.",
        "It does not prove channel identity, rights, or future availability.",
        "Do not redistribute third-party streams without permission.",
        "JELLYFIN CURRENT TUNER: NOT MODIFIED",
        "SOURCE AND BACKUPS: NOT MODIFIED",
    ]
    atomic_write(REPORT, "\n".join(reports) + "\n")
    if updated:
        atomic_write(OUTPUT, "\n".join(lines) + "\n")
        print("NEW TEST PLAYLIST:", OUTPUT, flush=True)
    else:
        print("No usable GEM candidates found. No new playlist created.", flush=True)
    print("\n".join(reports), flush=True)
    print("REPORT:", REPORT, flush=True)

if __name__ == "__main__":
    main()
