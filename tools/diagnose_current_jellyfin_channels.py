#!/usr/bin/env python3
"""Read-only IPTV-ALI diagnostics for the Jellyfin M3U on the netcup VPS.

Requires only Python 3 and ffprobe. Never edits Jellyfin or the input playlist.
Does not print or save full stream URLs (tokens might be embedded in URLs).
"""
import argparse
import concurrent.futures
import collections
import datetime
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

SOURCE="/opt/iptv/playlists/IPTV-ALI-IRIB3-VARZESH-TEST.m3u"
REPORT="/opt/iptv/playlists/IPTV-ALI-FAILURE-DIAGNOSIS.txt"

def parse_entries(path):
    text=Path(path).read_text(encoding="utf-8-sig",errors="replace")
    lines=text.splitlines()
    result=[]
    label=None
    for l in lines:
        s=l.strip()
        if s.startswith("#EXTINF"):
            label=s.rsplit(",",1)[-1].strip()
        elif label and s and not s.startswith("#"):
            try:
                p=urlsplit(s)
                if p.scheme not in ("http","https") or not p.hostname:
                    result.append((label,s,"INVALID_URL"))
                else:
                    result.append((label,s,""))
            except ValueError:
                result.append((label,s,"INVALID_URL"))
            label=None
    return result

def classify(err):
    s=err.lower()
    if any(x in s for x in ("403 forbidden","http error 403","server returned 403","403 (access denied)")):
        return "HTTP_403_BLOCKED"
    if any(x in s for x in ("404 not found","server returned 404","http error 404")):
        return "HTTP_404_MISSING"
    if any(x in s for x in ("401 unauthorized","http error 401","server returned 401")):
        return "HTTP_401_AUTH"
    if any(x in s for x in ("429 too many requests","http error 429")):
        return "HTTP_429_LIMIT"
    if any(x in s for x in ("name does not resolve","failed to resolve hostname","name or service not known","nodename nor servname","getaddrinfo failed","temporary failure in name resolution")):
        return "DNS_ERROR"
    if any(x in s for x in ("certificate verify failed","ssl","tls","handshake","certificate has expired")):
        return "TLS_ERROR"
    if any(x in s for x in ("timed out","timeout","error number -110","connection refused","network is unreachable")):
        return "NETWORK_TIMEOUT"
    if any(x in s for x in ("invalid data found","no such file","unrecognized option","invalid argument")):
        return "INVALID_MEDIA"
    return "NO_PLAYABLE_MEDIA"

def manifest_http_status(url):
    """Small manifest request for a second clue if ffprobe fails."""
    try:
        req=Request(url,headers={"User-Agent":"Mozilla/5.0","Range":"bytes=0-16383"})
        with urlopen(req,timeout=9) as res:
            data=res.read(16384)
            content_type=res.headers.get("Content-Type","").lower()
            if b"#EXTM3U" in data:
                return "Manifest HTTP 200 with #EXTM3U; segment or codec could fail"
            if "text/html" in content_type:
                return "Server returns HTML, not stream"
            return "Manifest HTTP "+str(res.status)+"; inspect ffprobe/Jellyfin logs"
    except HTTPError as ex:
        return "Manifest HTTP "+str(ex.code)
    except URLError as ex:
        return classify(str(ex.reason))
    except Exception:
        return "Manifest request failed"

def check_one(item,timeout):
    idx,name,url,parse_error=item
    try: host=urlsplit(url).hostname or "?"
    except ValueError: host="?"
    if parse_error:
        return (idx,name,parse_error,host,"Invalid URL format")
    cmd=[
        "ffprobe","-v","error","-rw_timeout","8000000",
        "-analyzeduration","2500000","-probesize","2500000",
        "-show_entries","stream=codec_type","-of","csv=p=0",url
    ]
    try:
        p=subprocess.run(cmd,text=True,capture_output=True,timeout=timeout)
        types=set(p.stdout.strip().splitlines())
        if p.returncode==0 and ("video" in types or "audio" in types):
            kind=("VIDEO+" if "video" in types else "")+("AUDIO" if "audio" in types else "")
            return (idx,name,"MEDIA_OK",host,kind+" (not a Jellyfin playback guarantee)")
        err=p.stderr.strip()
        reason=classify(err)
        clue=manifest_http_status(url)
        return (idx,name,reason,host,clue)
    except subprocess.TimeoutExpired:
        return (idx,name,"NETWORK_TIMEOUT",host,"ffprobe exceeded "+str(timeout)+" sec")
    except Exception as ex:
        return (idx,name,"ERROR",host,type(ex).__name__)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--source",default=SOURCE)
    ap.add_argument("--report",default=REPORT)
    ap.add_argument("--workers",type=int,default=4)
    ap.add_argument("--timeout",type=int,default=18)
    args=ap.parse_args()
    if not shutil.which("ffprobe"):
        raise SystemExit("ffprobe not installed; run: apt install ffmpeg")
    path=Path(args.source)
    if not path.is_file():
        raise SystemExit(f"Playlist does not exist: {path}")
    entries=parse_entries(path)
    if not entries:
        raise SystemExit("No channels found in playlist")
    print(f"[INFO] Checking {len(entries)} channels FROM THIS VPS; no changes to Jellyfin",flush=True)
    ordered=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1,min(args.workers,8))) as pool:
        future_list=[pool.submit(check_one,(i,name,url,error),args.timeout) for i,(name,url,error) in enumerate(entries,1)]
        for n,f in enumerate(concurrent.futures.as_completed(future_list),1):
            ordered.append(f.result())
            if n%10==0 or n==len(entries):
                print(f"[PROGRESS] {n}/{len(entries)}",flush=True)
    ordered.sort()
    counts=collections.Counter(t[2] for t in ordered)
    stamp=datetime.datetime.now(datetime.timezone.utc).isoformat()
    lines=[
        "IPTV-ALI DIAGNOSTIC - read-only",
        "UTC: "+stamp,
        "Playlist: "+str(path),
        "Channels: "+str(len(entries)),
        "Summary: "+", ".join(f"{k}={v}" for k,v in sorted(counts.items())),
        "",
        "IMPORTANT: MEDIA_OK means ffprobe can see audio/video. Real playback in Jellyfin is a separate test.",
        "401/403 are restrictions; this script DOES NOT attempt to bypass those restrictions.",
        "Only hostname, channel name and error classification are stored (not full stream URL).",
        "",
        "CHANNEL | CLASSIFICATION | HOST | DETAILS"
    ]
    for _,name,status,host,clue in ordered:
        clean_name=re.sub(r"[\r\n|]"," ",name)[:100]
        clean_clue=re.sub(r"[\r\n|]"," ",clue)[:130]
        lines.append(f"{clean_name} | {status} | {host} | {clean_clue}")
    target=Path(args.report)
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text("\n".join(lines)+"\n",encoding="utf-8")
    print("[SUMMARY]",dict(counts),flush=True)
    print("[REPORT]",target,flush=True)
    for line in lines[10:]:
        if any(tag in line for tag in ("HTTP_","DNS_","TLS_","TIMEOUT","NO_PLAYABLE","INVALID_","ERROR")):
            print(line,flush=True)

if __name__=="__main__":
    main()
