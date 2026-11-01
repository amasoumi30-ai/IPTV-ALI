#!/usr/bin/env python3
"""IPTV-ALI: recheck failed channels against an independent backup catalogue.

Creates a NEW complete playlist; never overwrites input, backup or Jellyfin tuner.
Checks streams from the owner's netcup VPS with ffprobe, not from ChatGPT.
"""
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.parse import urlparse
import ipaddress
import os
import re
import subprocess
import tempfile
import unicodedata

ROOT = Path("/opt/iptv/playlists")
PLAYLIST = ROOT / "IPTV-ALI-UNIQUE.m3u"
STATUS = ROOT / "IPTV-ALI-STATUS.txt"
OUTPUT = ROOT / "IPTV-ALI-RESCUED.m3u"
REPORT = ROOT / "IPTV-ALI-RESCUE-REPORT.txt"
SOURCES = {
    "iran-backups": "https://raw.githubusercontent.com/shayanline/iptv-iran/main/playlists/iran-all-streams.m3u",
    "iran-main": "https://raw.githubusercontent.com/shayanline/iptv-iran/main/playlists/iran.m3u",
    "iptv-org": "https://iptv-org.github.io/iptv/languages/fas.m3u",
}
# Intentional exact aliases only; we do not try to infer uncertain channel matches.
ALIASES = {
    "irib1": "irib1", "iribtv1": "irib1",
    "irib2": "irib2", "iribtv2": "irib2",
    "irib3": "irib3", "iribtv3": "irib3",
    "irib4": "irib4", "iribtv4": "irib4",
    "varzeshtv": "iribvarzesh",
    "iribvarzesh": "iribvarzesh",
    "iribtvvarzesh": "iribvarzesh",
    "iribsport": "iribvarzesh",
    "iribvarzeshtv": "iribvarzesh",
    "bbcpersiantv": "bbcpersian",
    "bbcpersian": "bbcpersian",
    "manototv": "manoto",
}
TITLE = re.compile(r'tvg-name="([^"]+)"')

def key(name):
    name = name.split("|", 1)[0]
    name = re.sub(r"\([^)]*\)|\[[^]]*\]", "", name)
    name = unicodedata.normalize("NFKC", name).lower().replace("+", "plus")
    normalized = re.sub(r"[^a-z0-9\u0600-\u06ff]", "", name)
    return ALIASES.get(normalized, normalized)

def allowed(url):
    try:
        p = urlparse(url)
        if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
            return False
        if p.port not in (None, 80, 443, 8080, 8443):
            return False
        try:
            return ipaddress.ip_address(p.hostname).is_global
        except ValueError:
            return p.hostname.lower() not in ("localhost", "localhost.localdomain")
    except ValueError:
        return False

def candidates(source_text):
    out, meta = [], None
    for line in source_text.splitlines():
        s = line.strip().lstrip("\ufeff")
        if s.startswith("#EXTINF"):
            meta = s
        elif s.startswith("#"):
            continue
        elif meta and allowed(s):
            match = TITLE.search(meta)
            name = (match.group(1) if match else meta.rsplit(",", 1)[-1]).strip()
            out.append((key(name), s))
            meta = None
        elif s:
            meta = None
    return out

def probe(url):
    if not allowed(url):
        return False
    try:
        p = subprocess.run(
            ["ffprobe", "-v", "error", "-rw_timeout", "8000000",
             "-show_entries", "stream=codec_type", "-of", "csv=p=0", url],
            capture_output=True, text=True, timeout=11)
        return p.returncode == 0 and ("video" in p.stdout or "audio" in p.stdout)
    except (subprocess.TimeoutExpired, OSError):
        return False

def save(path, data):
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix="." + path.name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

def main():
    if not PLAYLIST.exists() or not STATUS.exists():
        raise SystemExit("Missing UNIQUE playlist or STATUS; nothing changed.")
    lines = PLAYLIST.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    indices = []
    for i, line in enumerate(lines):
        if not line.startswith("#EXTINF"):
            continue
        stream_idx = next((j for j in range(i+1, len(lines))
                           if lines[j].strip() and not lines[j].lstrip().startswith("#")), None)
        if stream_idx is None or any(lines[j].startswith("#EXTINF") for j in range(i+1, stream_idx)):
            raise SystemExit("Malformed playlist; nothing changed.")
        title = line.rsplit(",", 1)[-1].strip()
        indices.append((title, stream_idx, lines[stream_idx].strip()))
    statuses = [x for x in STATUS.read_text(encoding="utf-8").splitlines()
                if x.startswith("OK | ") or x.startswith("FAIL | ")]
    if len(indices) != 68 or len(statuses) != len(indices):
        raise SystemExit(f"Expected matching 68 channel statuses; got {len(indices)} and {len(statuses)}.")
    if OUTPUT.resolve() == PLAYLIST.resolve():
        raise SystemExit("Output equals input; stopping.")
    feed = defaultdict(list)
    for label, url in SOURCES.items():
        try:
            request = Request(url, headers={"User-Agent": "IPTV-ALI-backup-test/1.0"})
            with urlopen(request, timeout=18) as response:
                content = response.read(3_000_001)
            if len(content) > 3_000_000:
                raise ValueError("Feed exceeds size limit")
            entries = candidates(content.decode("utf-8-sig", "replace"))
            print("SOURCE:", label, "entries:", len(entries), flush=True)
            for identifier, stream in entries:
                feed[identifier].append((label, stream))
        except Exception as ex:
            print("UNAVAILABLE SOURCE:", label, type(ex).__name__, flush=True)

    failed = [(i, name, idx, original) for i, (name, idx, original) in enumerate(indices)
              if statuses[i].startswith("FAIL | ")]
    print("RECHECKING:", len(failed), "previously failed entries", flush=True)

    def check(row):
        i, name, stream_idx, old = row
        options, seen = [], {old}
        for label, url in feed.get(key(name), []):
            if url not in seen:
                seen.add(url)
                options.append((label, url))
            if len(options) >= 8:
                break
        for label, url in options:
            if probe(url):
                return i, name, stream_idx, label, url, len(options)
        return i, name, stream_idx, None, None, len(options)

    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(check, failed))
    report = ["IPTV-ALI backup stream investigation",
              f"CHANNELS PRESERVED: {len(indices)}",
              f"FAILED ENTRIES CHECKED: {len(failed)}"]
    fixed = 0
    for i, name, stream_idx, source, url, options in results:
        if url:
            lines[stream_idx] = url
            fixed += 1
            report.append(f"FOUND | #{i+1} {name} | source={source} | candidates={options}")
        else:
            report.append(f"NOT VERIFIED | #{i+1} {name} | candidates={options}")
    report.insert(3, f"NEWLY VERIFIED REPLACEMENTS: {fixed}")
    report.append("This is a snapshot check, not a permanent uptime or rights guarantee.")
    report.append("ALL ORIGINAL FILES AND JELLYFIN TUNER: NOT CHANGED")
    save(REPORT, "\n".join(report) + "\n")
    save(OUTPUT, "\n".join(lines) + "\n")
    print("\n".join(report), flush=True)
    print("OUTPUT:", OUTPUT)
    print("REPORT:", REPORT)

if __name__ == "__main__":
    main()
