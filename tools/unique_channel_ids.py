#!/usr/bin/env python3
"""IPTV-ALI: create unique channel IDs/names without removing or altering streams.

Input:  /opt/iptv/playlists/IPTV-ALI-MANAGED.m3u
Output: /opt/iptv/playlists/IPTV-ALI-UNIQUE.m3u
All changes are limited to EXTINF metadata. No active tuner is modified.
"""
from collections import Counter
from pathlib import Path
import os
import re
import tempfile

SOURCE = Path("/opt/iptv/playlists/IPTV-ALI-MANAGED.m3u")
OUTPUT = Path("/opt/iptv/playlists/IPTV-ALI-UNIQUE.m3u")
ID_PATTERN = re.compile(r'(?<![\w-])tvg-id="([^"]*)"')

def main():
    if not SOURCE.is_file():
        raise SystemExit("Missing managed playlist; nothing changed.")
    if SOURCE.resolve() == OUTPUT.resolve():
        raise SystemExit("Input and output collide; refusing to overwrite.")
    lines = SOURCE.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    original_count = sum(line.startswith("#EXTINF") for line in lines)
    if original_count != 68:
        raise SystemExit(f"Expected 68 channels, found {original_count}. Nothing changed.")

    originals = [ID_PATTERN.search(line).group(1) for line in lines
                 if line.startswith("#EXTINF") and ID_PATTERN.search(line)]
    reserved = set(originals)
    names_seen, ids_seen = Counter(), Counter()
    changed_ids, changed_names = [], []
    updated = []
    channel_number = 0
    for line in lines:
        if not line.startswith("#EXTINF"):
            updated.append(line)
            continue
        channel_number += 1
        name = line.rsplit(",", 1)[-1].strip()
        names_seen[name] += 1
        match = ID_PATTERN.search(line)
        old_id = match.group(1) if match else ""
        ids_seen[old_id] += 1

        if not old_id or ids_seen[old_id] > 1:
            # Synthetic ID for a duplicate or missing ID. Keep first original
            # EPG ID intact, and avoid all IDs in the original playlist.
            new_id = f"iptv-ali-channel-{channel_number}"
            while new_id in reserved:
                new_id += "-x"
            reserved.add(new_id)
            if match:
                line = line[:match.start(1)] + new_id + line[match.end(1):]
            else:
                line = line.replace("#EXTINF:", '#EXTINF: tvg-id="' + new_id + '" ', 1)
            changed_ids.append((channel_number, name))

        if names_seen[name] > 1:
            new_name = f"{name} (Alt {names_seen[name]})"
            pos = line.rfind(",")
            line = line[:pos+1] + new_name
            changed_names.append((channel_number, name, new_name))
        updated.append(line)

    output_count = sum(line.startswith("#EXTINF") for line in updated)
    if output_count != original_count:
        raise SystemExit("Channel count mismatch. Nothing changed.")
    file_text = "\n".join(updated) + "\n"
    fd, tmp = tempfile.mkstemp(prefix=".IPTV-ALI-UNIQUE.", dir=OUTPUT.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(file_text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, OUTPUT)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    print("CHANNELS:", output_count)
    print("IDS MADE UNIQUE:", len(changed_ids))
    for number, name in changed_ids:
        print(f"  #{number}: {name}")
    print("DUPLICATE DISPLAY NAMES RENAMED:", len(changed_names))
    for number, old, new in changed_names:
        print(f"  #{number}: {old} -> {new}")
    print("STREAM URLS: UNCHANGED")
    print("GEM AND OTHER FAILED CHANNELS: RETAINED")
    print("JELLYFIN TUNER: UNCHANGED")
    print("NEW PLAYLIST:", OUTPUT)

if __name__ == "__main__":
    main()
