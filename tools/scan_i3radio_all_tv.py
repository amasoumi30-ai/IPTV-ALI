#!/usr/bin/env python3
"""IPTV-ALI i3radio TV scan. Public pages only. Reports; no GitHub changes.
Requires: pip install playwright requests beautifulsoup4
          python -m playwright install chromium
          ffprobe on PATH.
Example: python scan_i3radio_all_tv.py --limit 120
"""
import argparse
import asyncio
import csv
import html
import json
import re
import shutil
import subprocess
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlsplit
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
import requests

BASE = "https://www.i3radio.com/"
GENRES = ["News","Sports","MoviesSeries","Documentary","LifeStyle","Entertainment","Music"]
GROUPS = {"News":"News","Sports":"Sports","MoviesSeries":"Movies","Documentary":"Documentary",
          "LifeStyle":"Lifestyle","Entertainment":"Entertainment","Music":"Music"}
MEDIA = re.compile(r"\.(?:m3u8|mpd)(?:[?#]|$)", re.I)
EMBED = re.compile(r'https?[^"\'<>\s\\]+?\.(?:m3u8|mpd)(?:\?[^"\'<>\s\\]*)?', re.I)
ADULT = re.compile(r"\b(?:porn|xxx|adult|erotic|sex|playboy|18\+)\b", re.I)
PRIVATE = {"token","auth","authorization","sig","signature","access_token","jwt",
           "hdnts","hdnea","expires","expire","exp","key","api_key",
           "session","username","password","pass","user"}

def safe(s):
    return re.sub(r"\s+"," ",str(s).replace('"',"'")).strip()

def allowed_stream(url):
    try:
        p=urlsplit(url)
        return bool(p.scheme=="https" and p.hostname and MEDIA.search(url)
                    and not p.username and not p.password
                    and not (set(k.lower() for k in parse_qs(p.query)) & PRIVATE))
    except Exception:
        return False

def discover(session, max_pages, delay):
    items={}
    for genre in GENRES:
        previous=None
        for n in range(1,max_pages+1):
            directory=BASE+"tv/"+genre+(("?pagina="+str(n)) if n>1 else "")
            try:
                r=session.get(directory,timeout=22)
                r.raise_for_status()
            except Exception as e:
                print("[Directory]",genre,n,str(e)[:130],flush=True)
                break
            soup=BeautifulSoup(r.text,"html.parser")
            found={}
            for node in soup.descendants:
                if getattr(node,"name",None) in ("h3","h4","h5","h6"):
                    if re.search(r"\bAll\s+\S+(?:\s+\S+)?\s+Radio\b",node.get_text(" ",strip=True),re.I):
                        break
                if getattr(node,"name",None)!="a" or not node.get("href"):
                    continue
                u=urljoin(directory,node["href"]).split("#")[0]
                p=urlsplit(u)
                if p.hostname not in ("i3radio.com","www.i3radio.com") or not p.path.startswith("/user/"):
                    continue
                name=safe(node.get_text(" ",strip=True) or p.path.split("/")[-1])
                name=re.sub(r"^TELEVISION\s*","",name,flags=re.I)
                if not ADULT.search(name):
                    found[u]={"page":u,"name":name,"genre":genre}
            signature=tuple(sorted(found))
            if not found or signature==previous:
                break
            previous=signature
            old=len(items)
            for u,item in found.items():
                items.setdefault(u,item)
            if n==1 or n%5==0:
                print("[Directory]",genre,"page",n, "new",len(items)-old, flush=True)
            if len(items)==old:
                break
            time.sleep(delay)
    print("[Discovery]",len(items),"distinct candidate profiles",flush=True)
    return list(items.values())

async def scan(browser,item,observe,sem):
    async with sem:
        found=set()
        rec={**item,"tv":False,"country":"","streams":[],"error":""}
        context=await browser.new_context()
        page=await context.new_page()
        page.on("request",lambda req: found.add(req.url) if MEDIA.search(req.url) else None)
        page.on("response",lambda res: found.add(res.url) if MEDIA.search(res.url) else None)
        try:
            await page.route("**/*",lambda r:r.abort() if r.request.resource_type in ("image","font") else r.continue_())
            await page.goto(item["page"],wait_until="domcontentloaded",timeout=27000)
            soup=BeautifulSoup(await page.content(),"html.parser")
            title=safe(soup.title.get_text(" ",strip=True)) if soup.title else ""
            page_text=safe(soup.get_text(" ",strip=True)[:2400])
            rec["tv"]=bool(re.search(r"\bTELEVISION\b", title+" "+page_text,re.I))
            if not rec["tv"]:
                rec["error"]="Not confirmed as television"
                return rec
            h1=soup.find("h1")
            candidate=safe(h1.get_text(" ",strip=True)) if h1 else ""
            if candidate and not re.search(r"asks for your consent|use your personal data|cookie|privacy notice",candidate,re.I):
                rec["name"]=candidate
            if ADULT.search(rec["name"]):
                rec["tv"]=False
                rec["error"]="Adult channel excluded"
                return rec
            await page.wait_for_timeout(int(observe*1000))
            if not found:
                for selector in ("video",".vjs-big-play-button",".jw-icon-playback","button[aria-label='Play']"):
                    try:
                        el=page.locator(selector).first
                        if await el.count():
                            await el.click(timeout=1000)
                            await page.wait_for_timeout(1200)
                            break
                    except Exception:
                        pass
            for frame in page.frames:
                try:
                    raw=await frame.content()
                    found.update(EMBED.findall(raw))
                except Exception:
                    pass
            rec["streams"]=sorted({html.unescape(u).replace("\\/","/")
                                   for u in found if allowed_stream(html.unescape(u))})
        except Exception as e:
            rec["error"]=safe(str(e))[:180]
        finally:
            await context.close()
        return rec

def probe(url,timeout):
    args=["ffprobe","-v","error","-rw_timeout","8000000",
          "-analyzeduration","2000000","-probesize","2000000",
          "-show_entries","stream=codec_type","-of","csv=p=0",url]
    try:
        r=subprocess.run(args,capture_output=True,text=True,timeout=timeout)
        streams=set(r.stdout.splitlines())
        if r.returncode==0 and "video" in streams:
            return True,"video"+(", audio" if "audio" in streams else "")
        return False,safe(r.stderr or "no video")[:220]
    except subprocess.TimeoutExpired:
        return False,"timeout"
    except Exception as e:
        return False,safe(e)[:220]

def output(folder,scanned,checked):
    folder.mkdir(parents=True,exist_ok=True)
    rows=[]; lines=["#EXTM3U"]; seen=set()
    for item in scanned:
        urls=item["streams"] if item["streams"] else [""]
        for url in urls:
            ok,info=checked.get(url,(False,item["error"] or "No media URL"))
            status="VIDEO_OK" if ok else ("FAILED" if url else "NO_URL")
            rows.append({"channel":item["name"],"genre":item["genre"],"page":item["page"],
                         "stream":url,"status":status,"details":info})
            if ok and url not in seen:
                seen.add(url)
                name=safe(item["name"])
                group=GROUPS.get(item["genre"],"Entertainment")
                if re.search(r"kids?|junior|children|baby",name,re.I):
                    group="Kids"
                lines.extend([f'#EXTINF:-1 tvg-name="{name}" group-title="{group}",{name}',url])
    with (folder/"report.csv").open("w",encoding="utf-8-sig",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=["channel","genre","page","stream","status","details"])
        writer.writeheader();writer.writerows(rows)
    (folder/"IPTV-I3RADIO-VIDEO-OK.m3u").write_text("\n".join(lines)+"\n",encoding="utf-8")
    (folder/"scanned_pages.json").write_text(json.dumps(scanned,ensure_ascii=False,indent=2),encoding="utf-8")
    summary=f"Scanned profiles: {len(scanned)}\nUnique working video URLs: {len(seen)}\n"
    summary+="Note: video detected is not proof of channel identity, rights, or future uptime.\n"
    (folder/"SUMMARY.txt").write_text(summary,encoding="utf-8")
    zip_path=folder/"IPTV-I3RADIO-RESULTS.zip"
    with zipfile.ZipFile(zip_path,"w",zipfile.ZIP_DEFLATED) as z:
        for name in ("report.csv","IPTV-I3RADIO-VIDEO-OK.m3u","scanned_pages.json","SUMMARY.txt"):
            z.write(folder/name,arcname=name)
    print("FINISHED:",len(seen),"unique video-working candidates",flush=True)
    print("UPLOAD THIS FILE:",zip_path.resolve(),flush=True)

async def main(a):
    if not shutil.which("ffprobe"):
        raise SystemExit("ffprobe not found. Open terminal where ffprobe -version works.")
    out=Path.home()/"Downloads"/"i3radio-all-results"
    out.mkdir(parents=True,exist_ok=True)
    inv=out/"all_tv_pages.json"
    if a.reuse_discovery and inv.exists():
        candidates=json.loads(inv.read_text(encoding="utf-8"))
        print("[Discovery] reused",len(candidates),"profiles",flush=True)
    else:
        session=requests.Session()
        session.headers["User-Agent"]="Mozilla/5.0 IPTV-ALI TV Scanner"
        candidates=discover(session,a.max_pages,a.delay)
        inv.write_text(json.dumps(candidates,ensure_ascii=False,indent=2),encoding="utf-8")
    batch=candidates[a.start: a.start+a.limit if a.limit else None]
    print("[Scan]",len(batch),"profiles selected",flush=True)
    if not batch:
        return
    sem=asyncio.Semaphore(a.concurrency)
    results=[]
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True)
        jobs=[scan(browser,it,a.observe,sem) for it in batch]
        for i,task in enumerate(asyncio.as_completed(jobs),1):
            results.append(await task)
            if i%20==0 or i==len(jobs):
                print("[Scan]",i,"/",len(jobs),flush=True)
        await browser.close()
    urls={u for item in results for u in item["streams"]}
    print("[Probe]",len(urls),"distinct URLs",flush=True)
    checked={}
    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        futures={pool.submit(probe,url,a.probe_timeout):url for url in urls}
        for i,future in enumerate(as_completed(futures),1):
            checked[futures[future]]=future.result()
            if i%25==0 or i==len(futures):
                print("[Probe]",i,"/",len(futures),flush=True)
    output(out/f"batch-{a.start:05}-{a.start+len(batch)-1:05}",results,checked)

if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--start",type=int,default=0)
    parser.add_argument("--limit",type=int,default=120)
    parser.add_argument("--max-pages",type=int,default=60)
    parser.add_argument("--concurrency",type=int,default=3)
    parser.add_argument("--workers",type=int,default=5)
    parser.add_argument("--observe",type=int,default=4)
    parser.add_argument("--probe-timeout",type=int,default=16)
    parser.add_argument("--delay",type=float,default=0.75)
    parser.add_argument("--reuse-discovery",action="store_true")
    a=parser.parse_args()
    asyncio.run(main(a))
