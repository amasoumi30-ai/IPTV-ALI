import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'vod'))
import portal


class PortalTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        (root / 'movies' / 'Comedy').mkdir(parents=True)
        (root / 'series' / 'Test Series' / 'Season 01').mkdir(parents=True)
        (root / 'movies' / 'Comedy' / 'Great Movie.mp4').write_bytes(b'0123456789abcdef')
        (root / 'series' / 'Test Series' / 'Season 01' / 'S01E01 Pilot.mp4').write_bytes(b'abcdefghijklmnopqrstuvwxyz')
        catalog = root / 'catalog.json'
        catalog.write_text(json.dumps({'movies': [{'title': 'Online Movie', 'url': 'https://media.example.com/movie.mp4'}],
            'series': [{'title': 'Online Show', 'episodes': [{'season': 2, 'episode': 3, 'url': 'https://media.example.com/ep.mp4'}]}]}))
        live = root / 'channels.m3u'
        live.write_text('#EXTM3U\n#EXTINF:-1 group-title="Free Live",Test Channel\nhttps://example.com/live.m3u8\n')
        cls.prev = portal.ROOT, portal.CATALOG, portal.LIVE_FILE, portal.USER, portal.PASS, portal.PUBLIC_URL
        portal.ROOT, portal.CATALOG, portal.LIVE_FILE = root, catalog, live
        portal.USER, portal.PASS = 'tester', 'testingSecret123'
        cls.server = portal.ThreadingHTTPServer(('127.0.0.1', 0), portal.PortalHandler)
        cls.url = 'http://127.0.0.1:%s' % cls.server.server_address[1]
        portal.PUBLIC_URL = cls.url
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        portal.ROOT, portal.CATALOG, portal.LIVE_FILE, portal.USER, portal.PASS, portal.PUBLIC_URL = cls.prev
        cls.tmp.cleanup()

    def api(self, action, more=''):
        with urlopen(self.url + '/player_api.php?username=tester&password=testingSecret123&action=' + action + more) as r:
            return json.loads(r.read())

    def test_category_separation(self):
        self.assertTrue(self.api('get_vod_categories'))
        self.assertTrue(self.api('get_series_categories'))
        self.assertTrue(self.api('get_live_categories'))
        self.assertEqual(len(self.api('get_vod_streams')), 2)
        self.assertEqual(len(self.api('get_series')), 2)
        self.assertEqual(len(self.api('get_live_streams')), 1)

    def test_series_episode_grouping(self):
        series = next(s for s in self.api('get_series') if s['name'] == 'Test Series')
        detail = self.api('get_series_info', '&series_id=' + str(series['series_id']))
        self.assertEqual(detail['episodes']['1'][0]['episode_num'], 1)
        self.assertEqual(detail['seasons'][0]['season_number'], 1)

    def test_range_streaming(self):
        movie = next(s for s in self.api('get_vod_streams') if s['name'] == 'Great Movie')
        req = Request(f'{self.url}/movie/tester/testingSecret123/{movie["stream_id"]}.mp4', headers={'Range': 'bytes=2-5'})
        with urlopen(req) as response:
            self.assertEqual(response.status, 206)
            self.assertEqual(response.read(), b'2345')

    def test_auth(self):
        with self.assertRaises(HTTPError) as raised:
            urlopen(self.url + '/player_api.php?username=tester&password=bad&action=get_vod_streams')
        self.assertEqual(raised.exception.code, 401)

    def test_redirect_online_media(self):
        movie = next(s for s in self.api('get_vod_streams') if s['name'] == 'Online Movie')
        class NoRedirect(__import__('urllib.request', fromlist=['HTTPRedirectHandler']).HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl): return None
        from urllib.request import build_opener
        with self.assertRaises(HTTPError) as raised:
            build_opener(NoRedirect()).open(f'{self.url}/movie/tester/testingSecret123/{movie["stream_id"]}.mp4')
        self.assertEqual(raised.exception.code, 302)
        self.assertEqual(raised.exception.headers['Location'], 'https://media.example.com/movie.mp4')


if __name__ == '__main__':
    unittest.main()