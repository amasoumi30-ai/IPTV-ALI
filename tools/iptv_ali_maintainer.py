#!/usr/bin/env python3
"""IPTV-ALI: test the user's Persian channels and try public replacement feeds.
Creates a separate verified playlist; NEVER overwrites the original tuner file.
An OK means ffprobe detected media at test time, not guaranteed uptime or rights.
"""
import argparse
import concurrent.futures
import ipaddress
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unicodedata
from urllib.parse import urlparse
from urllib.request import Request, urlopen

DEFAULT_SOURCE = "/opt/iptv/playlists/IPTV-ALI-PERSIAN-TEST.m3u"
DEFAULT_OUTPUT = "/opt/iptv/playlists/IPTV-ALI-VERIFIED.m3u"
DEFAULT_REPORT = "/opt/iptv/playlists/IPTV-ALI-STATUS.txt"
FEEDS = {
    "iptv-org/public": "https://iptv-org.github.io/iptv/languages/fas.m3u",
    "iptv-iran/community": "https://raw.githubusercontent.com/shayanline/iptv-iran/main/playlists/en/iran.m3u",
}
# Publicly documented broadcaster/CDN routes. May be restricted outside Iran.
BROADCASTER_CANDIDATES = {
    "irib1": ["https://ncdn.telewebion.ir/tv1/live/playlist.m3u8"],
    "irib2": ["https://ncdn.telewebion.ir/tv2/live/playlist.m3u8"],
    "irib3": ["https://ncdn.telewebion.ir/tv3/live/playlist.m3u8"],
    "iribvarzesh": ["https://ncdn.telewebion.ir/varzesh/live/playlist.m3u8"],
}

def safe_url(url):
    try:
        p = urlparse(url)
        if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
            return False
        if p.port not in (None, 80, 443, 8080, 8443):
            return False
        try:
            address = ipaddress.ip_address(p.hostname)
            if not address.is_global:
                return False
        except ValueError:
            if p.hostname.lower() in ("localhost", "localhost.localdomain"):
                return False
        return True
    except ValueError:
        return False

def normalize(name):
    name = unicodedata.normalize("NFKC", name).lower()
    name = re.sub(r"\([^)]*\)|\[[^]]*\]", "", name)
    name = name.replace("+", "plus").replace("＋", "plus")
    key = re.sub(r"[^a-z0-9\u0600-\u06ff]", "", name)
    aliases = {
        "iribtv1": "irib1", "iribtv2": "irib2",
        "iribtv3": "irib3", "iribtv4": "irib4",
        "iribtvvarzesh": "iribvarzesh", "varzeshtv": "iribvarzesh",
        "iribsport": "iribvarzesh", "iribvarzesh": "iribvarzesh",
        "iribtvmostanad": "iribmostanad", "iribtvomid": "iribomid",
        "gem24b": "gem24b", "manototv": "manoto",
    }
    return aliases.get(key, key)

def parse_m3u(text):
    entries, meta = [], None
    for line in text.splitlines():
        line = line.strip().lstrip("\ufeff")
        if line.startswith("#EXTINF"):
            meta = line
        elif line.startswith("#"):
            continue
        elif meta and safe_url(line):
            name = meta.rsplit(",", 1)[-1].strip()
            entries.append((name, meta, line))
            meta = None
        elif line:
            meta = None
    return entries

def download_feed(url):
    req = Request(url, headers={"User-Agent": "IPTV-ALI-public-feed-check/1.0"})
    with urlopen(req, timeout=17) as resp:
        data = resp.read(4_000_001)
    if len(data) > 4_000_000:
        raise ValueError("feed too large")
    return data.decode("utf-8-sig", "replace")

def probe(url, seconds):
    if not safe_url(url):
        return False
    cmd = ["ffprobe", "-v", "error", "-rw_timeout", str(int(seconds * 1_000_000)),
           "-show_entries", "stream=codec_type", "-of", "csv=p=0", url]
    try:
        p = subprocess.run(cmd, text=True, stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, timeout=seconds + 3,
                           check=False)
        return p.returncode == 0 and any(
            kind in p.stdout for kind in ("video", "audio")
        )
    except (subprocess.TimeoutExpired, OSError):
        return False

def atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

def main():
    p = argparse.ArgumentParser(description="Create non-destructive IPTV-ALI health report and verified M3U")
    p.add_argument("--source", default=DEFAULT_SOURCE)
    p.add_argument("--output", default=DEFAULT_OUTPUT)
    p.add_argument("--report", default=DEFAULT_REPORT)
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--timeout", type=int, default=11)
    args = p.parse_args()
    if Path(args.source).resolve() == Path(args.output).resolve():
        raise SystemExit("Refusing to overwrite source playlist")
    original = parse_m3u(Path(args.source).read_text(encoding="utf-8-sig", errors="replace"))
    if not original:
        raise SystemExit("No channels found; no changes made.")
    candidates = {}
    for label, url in FEEDS.items():
        try:
            found = parse_m3u(download_feed(url))
            for name, _, stream_url in found:
                candidates.setdefault(normalize(name), []).append((label, stream_url))
            print("Loaded public candidate source:", label, "entries:", len(found), flush=True)
        except Exception as exc:
            print("Candidate source unavailable:", label, type(exc).__name__, flush=True)
    for name, urls in BROADCASTER_CANDIDATES.items():
        for url in urls:
            candidates.setdefault(name, []).append(("broadcaster", url))
    def check(item):
        name, metadata, original_url = item
        choices = [("existing", original_url)] + candidates.get(normalize(name), [])
        seen = set()
        for origin, url in choices:
            if url in seen:
                continue
            seen.add(url)
            if probe(url, args.timeout):
                return name, metadata, url, origin
        return name, metadata, None, "none"
    print("Testing", len(original), "channels from the netcup server...", flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(args.workers, 10))) as pool:
        result = list(pool.map(check, original))
    accepted = [(name, ext, url, origin) for name, ext, url, origin in result if url]
    report = [f"IPTV-ALI | {len(result)} tested | {len(accepted)} probe OK | {len(result)-len(accepted)} not verified",
              "OK means audio/video detected at test time. Not a guarantee of uninterrupted or licensed streaming.", ""]
    for name, _, url, origin in result:
        report.append(("OK" if url else "FAIL") + " | " + name +
                      (" | source=" + origin if url else ""))
    atomic_write(args.report, "\n".join(report) + "\n")
    if accepted:
        playlist = "#EXTM3U\n#PLAYLIST:IPTV-ALI Verified\n"
        playlist += "".join(ext + "\n" + url + "\n" for _, ext, url, _ in accepted)
        atomic_write(args.output, playlist)
    print("\n".join(report), flush=True)
    print("REPORT:", args.report)
    print("VERIFIED PLAYLIST:", args.output if accepted else "not changed (no verified channels)")
    print("ORIGINAL PLAYLIST: NOT CHANGED")
if __name__ == "__main__":
    main()
