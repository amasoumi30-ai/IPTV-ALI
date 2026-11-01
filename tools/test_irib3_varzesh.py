#!/usr/bin/env python3
"""Build a separate IPTV-ALI testing playlist with two user-tested HLS URLs.

Non-destructive: the original, backup, and Jellyfin tuner remain unchanged.
A working ffprobe response does not guarantee long-term uptime or rights.
"""
from pathlib import Path
import os
import tempfile

BASE = Path("/opt/iptv/playlists")
SOURCE = BASE / "IPTV-ALI-UNIQUE.m3u"
DEST = BASE / "IPTV-ALI-IRIB3-VARZESH-TEST.m3u"
REPLACEMENTS = {
    "IRIB 3": "https://cp11.adabmedia.com/hls2/tv3test.m3u8",
    "Varzesh TV": "https://cp11.adabmedia.com/hls2/varzeshtest.m3u8",
}

def main():
    if not SOURCE.is_file():
        raise SystemExit("Source IPTV-ALI-UNIQUE.m3u not found; no changes made.")
    if SOURCE.resolve() == DEST.resolve():
        raise SystemExit("Refusing to overwrite source.")

    lines = SOURCE.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    count = sum(1 for line in lines if line.startswith("#EXTINF"))
    if count != 68:
        raise SystemExit(f"Expected 68 entries; found {count}; no changes made.")

    replaced = set()
    target = None
    for i, line in enumerate(lines):
        if line.startswith("#EXTINF"):
            name = line.rsplit(",", 1)[-1].strip()
            target = name if name in REPLACEMENTS else None
        elif target and line.strip() and not line.lstrip().startswith("#"):
            if target in replaced:
                raise SystemExit("Duplicate target entry detected; no changes made.")
            lines[i] = REPLACEMENTS[target]
            replaced.add(target)
            target = None

    if replaced != set(REPLACEMENTS):
        raise SystemExit(f"Missing target channel(s): {sorted(set(REPLACEMENTS)-replaced)}; no changes made.")

    output = "\n".join(lines) + "\n"
    fd, tmp = tempfile.mkstemp(prefix=".IPTV-ALI-IRIB3-VARZESH-", dir=BASE)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(output)
        os.chmod(tmp, 0o644)
        os.replace(tmp, DEST)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

    print("CHANNELS:", count)
    print("NEW STREAM LINKS:", len(replaced))
    for name in sorted(replaced):
        print("  UPDATED:", name)
    print("OTHER STREAM LINKS:", "UNCHANGED")
    print("VARZESH ALT 2:", "UNCHANGED")
    print("ORIGINAL / BACKUP:", "UNCHANGED")
    print("JELLYFIN TUNER:", "UNCHANGED")
    print("TEST PLAYLIST:", DEST)

if __name__ == "__main__":
    main()
