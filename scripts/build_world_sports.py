import urllib.request

SOURCES = [
    'https://iptv-org.github.io/iptv/categories/sports.m3u',
]
OUT = 'world-sports-auto.m3u'

def get(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=45) as r:
        return r.read().decode('utf-8', 'replace')

def parse(text):
    lines = text.replace('\r', '').split('\n')
    out = []
    info = None
    for line in lines:
        line = line.strip()
        if line.startswith('#EXTINF:'):
            info = line
        elif info and line and not line.startswith('#'):
            out.append((info, line))
            info = None
    return out

selected = []
seen = set()
for source in SOURCES:
    for info, url in parse(get(source)):
        key = (info, url)
        if key not in seen:
            selected.append((info, url))
            seen.add(key)

with open(OUT, 'w', encoding='utf-8', newline='\n') as f:
    f.write('#EXTM3U\n')
    f.write('# World Sports - generated from iptv-org public sports playlist\n')
    for info, url in selected:
        f.write(info + '\n' + url + '\n')

print(f'Generated {len(selected)} world sports channels')
