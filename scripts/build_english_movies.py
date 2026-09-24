import re
import urllib.request

MOVIES='https://iptv-org.github.io/iptv/categories/movies.m3u'
ENGLISH='https://iptv-org.github.io/iptv/languages/eng.m3u'
OUT='english-movies-auto.m3u'

def get(url):
    req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'})
    with urllib.request.urlopen(req,timeout=45) as r:
        return r.read().decode('utf-8','replace')

def parse(text):
    lines=text.replace('\r','').split('\n')
    out=[]
    info=None
    for line in lines:
        line=line.strip()
        if line.startswith('#EXTINF:'):
            info=line
        elif info and line and not line.startswith('#'):
            m=re.search(r'tvg-id="([^"]+)"',info)
            key=m.group(1) if m else line
            out.append((key,info,line))
            info=None
    return out

movies=parse(get(MOVIES))
english=parse(get(ENGLISH))
eng_ids={x[0] for x in english}
selected=[]
seen=set()
for key,info,url in movies:
    if key in eng_ids and key not in seen:
        selected.append((info,url)); seen.add(key)

with open(OUT,'w',encoding='utf-8',newline='\n') as f:
    f.write('#EXTM3U\n')
    f.write('# English-language Movies - generated from iptv-org public playlists\n')
    for info,url in selected:
        f.write(info+'\n'+url+'\n')
print(f'Generated {len(selected)} English movie channels')
