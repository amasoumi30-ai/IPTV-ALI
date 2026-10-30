#!/usr/bin/env python3
"""Read-only, self-hosted Xtream-style portal for your own media and playlist.

Python 3.10+, standard library only. An MVP, not a commercial Xtream server.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import mimetypes
import os
import re
import socketserver
from collections import defaultdict
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

HERE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get('VOD_MEDIA_ROOT', HERE / 'media')).resolve()
CATALOG = Path(os.environ.get('VOD_CATALOG', HERE / 'catalog.json')).resolve()
LIVE_FILE = Path(os.environ.get('VOD_LIVE_M3U', HERE.parent / 'IPTV.m3u')).resolve()
USER = os.environ.get('VOD_USERNAME', '')
PASS = os.environ.get('VOD_PASSWORD', '')
HOST = os.environ.get('VOD_HOST', '127.0.0.1')
PORT = int(os.environ.get('VOD_PORT', '8099'))
PUBLIC_URL = os.environ.get('VOD_PUBLIC_URL', '').rstrip('/')
EXTENSIONS = {'.mp4', '.mkv', '.avi', '.mov', '.webm', '.ts', '.m3u8'}
EPISODE = re.compile(r'[sS](\d{1,2})[eE](\d{1,3})')
SEASON = re.compile(r'(?:season|فصل)[ _-]?(\d{1,2})', re.I)


def stable_id(kind: str, key: str) -> int:
    """Stable positive IDs, separate namespaces by media kind."""
    base = {'live': 100000, 'movie': 600000000, 'series': 1200000000, 'episode': 1600000000}[kind]
    return base + (int(hashlib.sha256((kind + ':' + key).encode()).hexdigest()[:8], 16) % 300000000)


def check_link(link: str) -> str:
    if not isinstance(link, str) or urlsplit(link).scheme not in ('http', 'https'):
        raise ValueError('Only http(s) direct media URLs are supported')
    return link


def read_manifest() -> dict:
    if not CATALOG.exists():
        return {'movies': [], 'series': []}
    data = json.loads(CATALOG.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or not isinstance(data.get('movies', []), list) or not isinstance(data.get('series', []), list):
        raise ValueError('catalog.json must have movies and series arrays')
    return data


def new_media(kind: str, title: str, location: str, **meta) -> dict:
    local = Path(location).is_absolute()
    if not local:
        check_link(location)
    result = {'id': stable_id(kind, location), 'title': title, 'source': location,
              'extension': (Path(urlsplit(location).path).suffix.lower().lstrip('.') or 'mp4'),
              'local': local, 'category': meta.get('category') or 'Other',
              'poster': meta.get('poster') or '', 'plot': meta.get('plot') or ''}
    return result


def movie_list(manifest: dict) -> list[dict]:
    movies = []
    folder = ROOT / 'movies'
    if folder.exists():
        for f in sorted(folder.rglob('*')):
            if f.is_file() and f.suffix.lower() in EXTENSIONS:
                relative = f.relative_to(folder)
                category = relative.parts[0] if len(relative.parts) > 1 else 'Movies'
                movies.append(new_media('movie', f.stem, str(f.resolve()), category=category))
    for item in manifest.get('movies', []):
        movies.append(new_media('movie', item['title'], check_link(item['url']),
                                category=item.get('category', 'Movies'), poster=item.get('poster'), plot=item.get('plot')))
    return list({m['id']: m for m in movies}.values())


def series_list(manifest: dict) -> list[dict]:
    series = {}
    folder = ROOT / 'series'
    if folder.exists():
        for f in sorted(folder.rglob('*')):
            if not f.is_file() or f.suffix.lower() not in EXTENSIONS:
                continue
            relative = f.relative_to(folder)
            if len(relative.parts) < 2:
                continue
            show_name = relative.parts[0]
            match = EPISODE.search(f.stem)
            if not match:
                continue  # Unnumbered files deliberately skipped, never guessed
            season, number = int(match.group(1)), int(match.group(2))
            s = series.setdefault(show_name, {'id': stable_id('series', show_name), 'title': show_name,
                                                'category': 'Series', 'poster': '', 'plot': '', 'episodes': []})
            s['episodes'].append({**new_media('episode', f.stem, str(f.resolve())), 'season': season, 'episode': number})
    for show in manifest.get('series', []):
        name = show['title']
        s = series.setdefault(name, {'id': stable_id('series', name), 'title': name,
                                      'category': show.get('category', 'Series'),
                                      'poster': show.get('poster', ''), 'plot': show.get('plot', ''), 'episodes': []})
        s.update({k: show[k] for k in ('category', 'poster', 'plot') if k in show})
        for ep in show.get('episodes', []):
            season = int(ep['season']); number = int(ep['episode'])
            url = check_link(ep['url'])
            s['episodes'].append({**new_media('episode', ep.get('title', f'S{season:02}E{number:02}'), url),
                                  'season': season, 'episode': number})
    for s in series.values():
        s['episodes'] = sorted({e['id']: e for e in s['episodes']}.values(),
                               key=lambda e: (e['season'], e['episode'], e['title']))
    return sorted(series.values(), key=lambda x: x['title'].casefold())


def live_list() -> list[dict]:
    """Parse existing live channels only; never modify the source M3U."""
    if not LIVE_FILE.is_file():
        return []
    channels = []
    meta = None
    for line in LIVE_FILE.read_text(encoding='utf-8-sig', errors='replace').splitlines():
        line = line.strip()
        if line.startswith('#EXTINF:'):
            title = line.split(',', 1)[-1].strip()
            match = re.search(r'group-title="([^"]*)"', line)
            category = match.group(1) if match else 'Live TV'
            match_logo = re.search(r'tvg-logo="([^"]*)"', line)
            meta = (title, category, match_logo.group(1) if match_logo else '')
        elif meta and (line.startswith('http://') or line.startswith('https://')):
            title, category, logo = meta
            channels.append({'id': stable_id('live', line + title), 'title': title,
                             'source': line, 'category': category, 'poster': logo})
            meta = None
    return list({e['id']: e for e in channels}.values())


def grouped_categories(items: list[dict], start: int) -> tuple[list[dict], dict[str, str]]:
    groups = sorted({m['category'] for m in items})
    mapping = {group: str(start + i) for i, group in enumerate(groups)}
    return ([{'category_id': mapping[group], 'category_name': group, 'parent_id': 0} for group in groups], mapping)


def build_data() -> dict:
    manifest = read_manifest()
    movies, series, live = movie_list(manifest), series_list(manifest), live_list()
    movie_cat, movie_ids = grouped_categories(movies, 2000)
    series_cat, series_ids = grouped_categories(series, 3000)
    live_cat, live_ids = grouped_categories(live, 1000)
    return dict(movies=movies, series=series, live=live, movie_categories=movie_cat,
                series_categories=series_cat, live_categories=live_cat,
                movie_ids=movie_ids, series_ids=series_ids, live_ids=live_ids)


class PortalHandler(BaseHTTPRequestHandler):
    server_version = 'IPTV-ALI-VOD/0.1'

    def log_message(self, fmt: str, *args) -> None:
        # Never log URL paths or query strings: Xtream credentials appear in them.
        print(f'[{self.log_date_time_string()}] request completed')

    def respond(self, data, status: int = 200, content_type: str = 'application/json; charset=utf-8'):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8') if content_type.startswith('application/json') else data
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def authorized(self, user: str, password: str) -> bool:
        return bool(USER and PASS and hmac.compare_digest(user, USER) and hmac.compare_digest(password, PASS))

    def public_base(self):
        return PUBLIC_URL or f'http://{self.headers.get("Host", "127.0.0.1:8099")}'

    def link(self, kind: str, entry: dict) -> str:
        ext = entry.get('extension') or ('ts' if kind == 'live' else 'mp4')
        if kind == 'live': ext = 'ts'
        return f'{self.public_base()}/{kind}/{quote(USER, safe="")}/{quote(PASS, safe="")}/{entry["id"]}.{ext}'

    def do_GET(self):
        request = urlsplit(self.path)
        qs = {k: v[0] for k, v in parse_qs(request.query).items()}
        path = unquote(request.path)
        if path == '/health':
            return self.respond({'status': 'ok'})
        if path in ('/player_api.php', '/get.php'):
            if not self.authorized(qs.get('username', ''), qs.get('password', '')):
                return self.respond({'user_info': {'auth': 0, 'status': 'Disabled'}}, 401)
            try:
                data = build_data()
            except (ValueError, KeyError, TypeError, OSError, json.JSONDecodeError) as exc:
                return self.respond({'error': str(exc)}, 500)
            if path == '/get.php':
                return self.playlist(data)
            return self.api(qs, data)
        match = re.fullmatch(r'/(live|movie|series)/([^/]+)/([^/]+)/(\d+)\.(\w+)', path)
        if match:
            kind, user, password, media_id, _ext = match.groups()
            if not self.authorized(user, password):
                return self.respond({'error': 'unauthorized'}, 401)
            try:
                data = build_data()
            except (ValueError, OSError) as exc:
                return self.respond({'error': str(exc)}, 500)
            candidates = data['live'] if kind == 'live' else data['movies'] if kind == 'movie' else [ep for s in data['series'] for ep in s['episodes']]
            match_entry = next((m for m in candidates if str(m['id']) == media_id), None)
            if not match_entry:
                return self.respond({'error': 'not found'}, 404)
            return self.media(match_entry)
        return self.respond({'error': 'not found'}, 404)

    def media(self, entry: dict):
        source = entry['source']
        if not entry.get('local'):
            self.send_response(302)
            self.send_header('Location', source)
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            return
        file = Path(source).resolve()
        if not file.is_file() or not file.is_relative_to(ROOT):
            return self.respond({'error': 'not found'}, 404)
        size = file.stat().st_size
        start, end = 0, size - 1
        range_header = self.headers.get('Range', '')
        if range_header:
            match = re.fullmatch(r'bytes=(\d*)-(\d*)', range_header)
            if not match or (not match.group(1) and not match.group(2)):
                return self.invalid_range(size)
            if not match.group(1):
                suffix = int(match.group(2))
                if suffix == 0: return self.invalid_range(size)
                start = max(size - suffix, 0)
            else:
                start = int(match.group(1))
                end = int(match.group(2)) if match.group(2) else size - 1
            if start >= size or end < start:
                return self.invalid_range(size)
            end = min(end, size - 1)
        length = end - start + 1
        self.send_response(206 if range_header else 200)
        self.send_header('Content-Type', mimetypes.guess_type(str(file))[0] or 'application/octet-stream')
        self.send_header('Accept-Ranges', 'bytes')
        if range_header:
            self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        self.send_header('Content-Length', str(length))
        self.end_headers()
        with file.open('rb') as fd:
            fd.seek(start)
            remaining = length
            while remaining > 0:
                block = fd.read(min(1024 * 1024, remaining))
                if not block: break
                try:
                    self.wfile.write(block)
                except (BrokenPipeError, ConnectionResetError):
                    break
                remaining -= len(block)

    def invalid_range(self, size):
        self.send_response(416)
        self.send_header('Content-Range', f'bytes */{size}')
        self.send_header('Content-Length', '0')
        self.end_headers()

    def playlist(self, data):
        rows = ['#EXTM3U']
        for entry in data['live']:
            rows += [f'#EXTINF:-1 group-title="{entry["category"]}",{entry["title"]}', self.link('live', entry)]
        for entry in data['movies']:
            rows += [f'#EXTINF:-1 group-title="VOD Movies - {entry["category"]}",{entry["title"]}', self.link('movie', entry)]
        for show in data['series']:
            for episode in show['episodes']:
                rows += [f'#EXTINF:-1 group-title="VOD Series - {show["title"]}",{episode["title"]}', self.link('series', episode)]
        return self.respond(('\n'.join(rows) + '\n').encode(), content_type='audio/x-mpegurl; charset=utf-8')

    def api(self, qs, data):
        action = qs.get('action', '')
        if not action:
            parsed = urlsplit(self.public_base())
            return self.respond({'user_info': {'username': USER, 'password': PASS, 'message': '', 'auth': 1,
                         'status': 'Active', 'is_trial': '0', 'active_cons': '0', 'created_at': '0',
                         'max_connections': '1', 'allowed_output_formats': ['ts', 'm3u8']},
                'server_info': {'url': parsed.hostname or '127.0.0.1',
                         'port': str(parsed.port or (443 if parsed.scheme == 'https' else 80)),
                         'https_port': '443', 'server_protocol': parsed.scheme or 'http',
                         'timezone': 'UTC', 'timestamp_now': int(datetime.now(timezone.utc).timestamp()),
                         'time_now': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}})
        for kind in ('live', 'vod', 'series'):
            name = {'live': 'live', 'vod': 'movie', 'series': 'series'}[kind]
            if action == f'get_{kind}_categories':
                return self.respond(data[name + '_categories'])
        if action == 'get_live_streams':
            return self.respond([{'num': i, 'name': m['title'], 'stream_type': 'live', 'stream_id': m['id'],
                                  'stream_icon': m['poster'], 'category_id': data['live_ids'][m['category']],
                                  'direct_source': ''} for i, m in enumerate(data['live'], 1)
                                 if not qs.get('category_id') or qs['category_id'] == data['live_ids'][m['category']]])
        if action == 'get_vod_streams':
            return self.respond([{'num': i, 'name': m['title'], 'stream_type': 'movie', 'stream_id': m['id'],
                                  'stream_icon': m['poster'], 'category_id': data['movie_ids'][m['category']],
                                  'container_extension': m['extension'], 'added': '0', 'direct_source': ''}
                                 for i, m in enumerate(data['movies'], 1)
                                 if not qs.get('category_id') or qs['category_id'] == data['movie_ids'][m['category']]])
        if action == 'get_vod_info':
            m = next((m for m in data['movies'] if str(m['id']) == qs.get('vod_id')), None)
            if not m: return self.respond({'error': 'not found'}, 404)
            return self.respond({'info': {'movie_image': m['poster'], 'plot': m['plot'], 'genre': m['category'],
                                           'rating': '', 'releasedate': ''},
                        'movie_data': {'stream_id': m['id'], 'name': m['title'], 'added': '0',
                                       'category_id': data['movie_ids'][m['category']],
                                       'container_extension': m['extension']}})
        if action == 'get_series':
            return self.respond([{'num': i, 'name': s['title'], 'series_id': s['id'], 'cover': s['poster'],
                                  'plot': s['plot'], 'category_id': data['series_ids'][s['category']],
                                  'last_modified': '0', 'rating': ''} for i, s in enumerate(data['series'], 1)
                                 if not qs.get('category_id') or qs['category_id'] == data['series_ids'][s['category']]])
        if action == 'get_series_info':
            s = next((s for s in data['series'] if str(s['id']) == qs.get('series_id')), None)
            if not s: return self.respond({'error': 'not found'}, 404)
            episodes = defaultdict(list)
            for e in s['episodes']:
                episodes[str(e['season'])].append({'id': str(e['id']), 'episode_num': e['episode'],
                  'title': e['title'], 'container_extension': e['extension'],
                  'info': {'movie_image': s['poster'], 'plot': s['plot']}})
            seasons = [{'id': n, 'season_number': n, 'name': f'Season {n}',
                        'episode_count': len(eps)} for n, eps in ((int(k), v) for k, v in episodes.items())]
            return self.respond({'seasons': sorted(seasons, key=lambda x: x['id']),
                                 'info': {'name': s['title'], 'cover': s['poster'], 'plot': s['plot'],
                                          'category_id': data['series_ids'][s['category']]}, 'episodes': episodes})
        if action in ('get_short_epg', 'get_simple_data_table'):
            return self.respond({'epg_listings': []})
        return self.respond([])


if __name__ == '__main__':
    if not USER or not PASS or USER == PASS:
        raise SystemExit('Set distinct VOD_USERNAME and VOD_PASSWORD environment variables first.')
    if HOST not in ('127.0.0.1', 'localhost', '::1'):
        print('WARNING: Use HTTPS reverse proxy and firewall for access outside your computer.')
    with ThreadingHTTPServer((HOST, PORT), PortalHandler) as server:
        print(f'VOD portal is running on {HOST}:{PORT} (Ctrl+C to stop)')
        server.serve_forever()