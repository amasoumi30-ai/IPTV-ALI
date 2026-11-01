#!/usr/bin/env python3
"""IPTV-ALI diagnostic: inspect HLS playlist and test video bytes safely.

Read-only; never touches playlists or Jellyfin. Does not disclose signed URLs.
"""
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
import socket

CHANNELS = {
    "IRIB 3": "https://live-aburayhan1102.telewebion.net/ek/tv3/live/1080p/index.m3u8",
    "VARZESH": "https://ncdn.telewebion.ir/varzesh/live/playlist.m3u8",
}
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "*/*"}


def fetch(url, max_bytes):
    with urlopen(Request(url, headers=HEADERS), timeout=12) as resp:
        return resp.status, resp.headers.get("Content-Type", ""), resp.read(max_bytes)


def classify_error(exc):
    if isinstance(exc, HTTPError):
        return f"HTTP {exc.code}"
    if isinstance(exc, (URLError, TimeoutError, socket.timeout)):
        return type(exc).__name__
    return type(exc).__name__


def diagnostic(name, start_url):
    print(f"\nCHANNEL: {name}", flush=True)
    url = start_url
    for depth in range(4):
        try:
            status, content_type, body = fetch(url, 196608)
        except Exception as exc:
            print("PLAYLIST REQUEST FAILED:", classify_error(exc), flush=True)
            return

        text = body.decode("utf-8-sig", errors="replace")
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        valid = bool(lines and lines[0] == "#EXTM3U")
        segments = sum(line.startswith("#EXTINF:") for line in lines)
        variants = sum(line.startswith("#EXT-X-STREAM-INF:") for line in lines)
        print(f"LEVEL {depth}: HTTP={status}, M3U8={valid}, "
              f"variants={variants}, segments={segments}, type={content_type[:45]}", flush=True)
        if not valid:
            print("REASON: response is not an HLS playlist")
            return

        # Media playlist: identify a likely latest media segment, but do NOT
        # confuse key/initialization URI attributes with media URLs.
        if segments:
            uris = [line for line in lines
                    if not line.startswith("#") and not line.upper().startswith("EXT-X-")]
            if not uris:
                print("REASON: no segment URI line available")
                return
            segment = urljoin(url, uris[-1])
            if urlparse(segment).scheme not in ("http", "https"):
                print("REASON: invalid media segment URI")
                return
            try:
                status, mime, data = fetch(segment, 2048)  # GET, no Range header
                print(f"SEGMENT GET: HTTP={status}; BYTES={len(data)}; type={mime[:45]}")
                print("RESULT:", "MEDIA DATA RECEIVED" if data else "EMPTY RESPONSE")
            except Exception as exc:
                print("SEGMENT GET FAILED:", classify_error(exc))
                print("RESULT: media segment could not be read from this server")
            return

        # Master playlist: only interpret the non-comment line immediately
        # following a correctly formed EXT-X-STREAM-INF tag as a media URI.
        variant_uris = []
        for i, line in enumerate(lines):
            if line.startswith("#EXT-X-STREAM-INF:") and i + 1 < len(lines):
                candidate = lines[i + 1]
                if candidate and not candidate.startswith("#") and not candidate.upper().startswith("EXT-X-"):
                    variant_uris.append(candidate)
        if variant_uris:
            url = urljoin(url, variant_uris[0])
            if urlparse(url).scheme not in ("http", "https"):
                print("REASON: invalid variant URI")
                return
            print("FOLLOWING MASTER VARIANT...", flush=True)
            continue

        broken = sum(1 for line in lines if line.upper().startswith("EXT-X-"))
        print(f"NO USABLE VARIANT OR MEDIA SEGMENT; malformed-tag-lines={broken}")
        print("RESULT: HLS playlist structure needs investigation")
        return
    print("STOP: maximum playlist nesting reached")


for name, url in CHANNELS.items():
    diagnostic(name, url)
