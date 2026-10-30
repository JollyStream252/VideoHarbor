"""Offline smoke tests for VideoHarbor's metadata, naming and quality logic.
Run: python -m unittest -v test_video_harbor.py
"""
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent))
from video_harbor import (clean_urls, safe_name, website_name, quality_height,
                          format_selector, estimate_size, readable_duration,
                          videos_from_info)


class CoreTests(unittest.TestCase):
    def test_url_paste(self):
        urls = clean_urls('https://youtube.com/watch?v=1\nhttps://vimeo.com/123, '
                          'https://youtube.com/watch?v=1')
        self.assertEqual(urls, ['https://youtube.com/watch?v=1',
                                'https://vimeo.com/123'])

    def test_windows_folder_safety(self):
        self.assertEqual(safe_name('A / B: C?'), 'A _ B_ C_')
        self.assertEqual(safe_name('CON'), '_CON')
        self.assertEqual(safe_name(''), 'Untitled')
        self.assertLessEqual(len(safe_name('x' * 200)), 90)

    def test_site_name(self):
        self.assertEqual(website_name('https://m.youtube.com/watch?v=abc'), 'YouTube')
        self.assertEqual(website_name('https://videos.example.com/abc'), 'Example')

    def test_quality_format_selection(self):
        self.assertEqual(format_selector('Audio only (MP3)'), 'bestaudio/best')
        self.assertIn('height<=1080', format_selector('1080p (Full HD)'))
        self.assertEqual(quality_height('2160p (4K)'), 2160)

    def test_size_from_audio_and_video(self):
        fmts = [
            {'vcodec': 'h264', 'acodec': 'none', 'height': 1080, 'filesize': 10_000_000},
            {'vcodec': 'h264', 'acodec': 'none', 'height': 720, 'filesize': 5_000_000},
            {'vcodec': 'none', 'acodec': 'opus', 'abr': 128, 'filesize': 1_000_000},
        ]
        self.assertEqual(estimate_size(fmts, 90, '1080p (Full HD)'), (11_000_000, False))
        self.assertEqual(estimate_size(fmts, 90, '720p (HD)'), (6_000_000, False))
        self.assertEqual(estimate_size(fmts, 90, 'Audio only (MP3)'), (2_160_000, True))

    def test_playlist_and_fallback_folder(self):
        sample = {'_type': 'playlist', 'title': 'My favorites', 'entries': [
            {'title': 'One', 'webpage_url': 'https://www.youtube.com/watch?v=one', 'duration': 61},
            {'title': 'Two', 'webpage_url': 'https://www.youtube.com/watch?v=two', 'duration': 62},
        ]}
        videos = list(videos_from_info(sample, 'https://www.youtube.com/playlist?list=abc'))
        self.assertEqual(len(videos), 2)
        self.assertEqual(videos[0].group, 'My favorites')
        self.assertEqual(videos[0].index, 1)
        self.assertEqual(videos[1].index, 2)
        sample['title'] = ''
        video = next(videos_from_info(sample, 'https://www.youtube.com/playlist?list=abc'))
        self.assertEqual(video.group, 'YouTube')

    def test_individual_folder_modes(self):
        info = {'title': 'How to cook pasta?', 'webpage_url': 'https://www.youtube.com/watch?v=foo'}
        video = next(videos_from_info(info, 'https://www.youtube.com/watch?v=foo'))
        with TemporaryDirectory() as td:
            self.assertEqual(video.destination(Path(td)).name, 'YouTube')
            self.assertEqual(video.destination(Path(td), 'Video page title').name,
                             'How to cook pasta_')

    def test_duration(self):
        self.assertEqual(readable_duration(125), '2:05')
        self.assertEqual(readable_duration(3675), '1:01:15')


if __name__ == '__main__':
    unittest.main()

# Additional regression checks for 1.2 are defined below and picked up by unittest discovery.
import threading
from types import SimpleNamespace
from unittest.mock import patch
from video_harbor import Video, VideoHarborApp, playlist_counter, app_files_created, safe_delete_tracked


class Version12Tests(unittest.TestCase):
    def test_numbered_playlist_rows(self):
        info = {'title': 'Travel', 'entries': [
            {'title': f'Video {i}', 'webpage_url': f'https://youtube.com/watch?v=v{i}'}
            for i in range(1, 11)]}
        rows = list(videos_from_info(info, 'https://www.youtube.com/playlist?list=X'))
        self.assertEqual(len(rows), 10)
        self.assertEqual([playlist_counter(x) for x in rows],
                         [f'{i}/10' for i in range(1, 11)])
        self.assertEqual(playlist_counter(next(videos_from_info(
            {'title': 'Solo'}, 'https://youtube.com/watch?v=1'))), '—')

    def test_safe_deletion_only_app_created_files_inside_root(self):
        with TemporaryDirectory() as td:
            root = Path(td) / 'downloads'
            root.mkdir()
            inside = root / 'new.mp4'
            outside = Path(td) / 'personal.mp4'
            old = root / 'old.mp4'
            old.write_bytes(b'old content')
            outside.write_bytes(b'personal content')
            before = set(root.iterdir())
            inside.write_bytes(b'new content')
            self.assertEqual(app_files_created(root, before), [inside])
            self.assertFalse(safe_delete_tracked(outside, root))
            self.assertTrue(old.exists())
            self.assertTrue(safe_delete_tracked(inside, root))
            self.assertFalse(inside.exists())
            self.assertTrue(outside.exists())

    def test_download_creates_media_record_and_reports_progress(self):
        with TemporaryDirectory() as td:
            root = Path(td) / 'downloads'
            video = Video('v1','Test clip','https://video.example/test','Example',
                          'Playlist','True' == 'True',1,120,playlist_total=10)
            class FakeDL:
                def __init__(self, opts):
                    self.opts = opts
                def __enter__(self): return self
                def __exit__(self,*args): return None
                def extract_info(self, url, download):
                    p = Path(self.opts['paths']['home']) / '001 - Test clip.mp4'
                    p.write_bytes(b'mp4 simulated')
                    self.opts['progress_hooks'][0]({'status':'downloading',
                         'downloaded_bytes':8,'total_bytes':16})
                    self.opts['progress_hooks'][0]({'status':'finished'})
                    return {'title':'Test clip', 'thumbnail':None}
            class FakeApp:
                stop_flag = threading.Event()
                def __init__(self): self.events=[]
                def _send(self, *x): self.events.append(x)
            obj = FakeApp()
            with patch('video_harbor.yt_dlp', SimpleNamespace(YoutubeDL=FakeDL)):
                VideoHarborApp._download_worker(obj, [video], root, 'Best available', 'Website name')
            self.assertEqual(video.completed_media.parent.name, 'Playlist')
            self.assertEqual(video.actual_size, len(b'mp4 simulated'))
            self.assertEqual(video.download_root, root)
            self.assertEqual(video.saved_files, [video.completed_media])
            self.assertIn(('video_status', 'v1', 'Complete'), obj.events)
            self.assertTrue(any(e[0] == 'progress' for e in obj.events))


# Channel listings are tested entirely offline with simulated site responses.
from xvideos_channels import channel_from_url, iter_channel_videos


class ChannelTests(unittest.TestCase):
    def test_creator_url_detection(self):
        self.assertEqual(channel_from_url('https://www.xvideos.com/channels/test-user'), ('channels', 'test-user'))
        self.assertEqual(channel_from_url('https://www.xvideos.com/test-user'), ('channels', 'test-user'))
        self.assertEqual(channel_from_url('https://www.xvideos.com/models/artist'), ('models', 'artist'))
        for url in (
            'https://www.xvideos.com/video1234/example',
            'https://www.xvideos.com/video.abc/example',
            'https://www.xvideos.com/search/term',
            'https://www.xvideos.com/',
            'https://notxvideos.com/channels/creator',
            'https://www.xvideos.com/channels',
        ):
            self.assertIsNone(channel_from_url(url), url)

    def test_paginated_creator_and_duplicate_results(self):
        calls = []
        def fetch(kind, slug, page):
            calls.append((kind, slug, page))
            if kind != 'channels':
                return None
            if page == 0:
                return {'nb_videos': 3, 'nb_per_page': 2, 'videos': [
                    {'eid': 'aa11', 't': 'First', 'pn': 'My Channel', 'd': '1:25',
                     'il': '/pictures/first.jpg'},
                    {'eid': 'bb22', 'tf': 'Second', 'pn': 'My Channel'},
                ]}
            if page == 1:
                return {'nb_videos': 3, 'nb_per_page': 2, 'videos': [
                    {'eid': 'aa11', 't': 'First duplicate'},
                    {'id': 123456, 't': 'Third'},
                ]}
        rows = list(iter_channel_videos('https://www.xvideos.com/channels/creator', fetch_page=fetch))
        self.assertEqual([r['title'] for r in rows], ['First', 'Second', 'Third'])
        self.assertEqual([r['index'] for r in rows], [1, 2, 3])
        self.assertEqual([r['total'] for r in rows], [3, 3, 3])
        self.assertEqual([r['group'] for r in rows], ['My Channel'] * 3)
        self.assertEqual(rows[0]['thumbnail'], 'https://www.xvideos.com/pictures/first.jpg')
        self.assertEqual(rows[2]['url'], 'https://www.xvideos.com/video123456')
        self.assertEqual(len(calls), 2)

    def test_stop_during_channel_listing(self):
        stop = [False]
        def fetch(kind, slug, page):
            return {'nb_videos': 100, 'nb_per_page': 1,
                    'videos': [{'eid': 'a123', 't': 'First'}]}
        def stopped():
            return stop[0]
        gen = iter_channel_videos('https://www.xvideos.com/profiles/creator',
                                  stop=stopped, fetch_page=fetch)
        self.assertEqual(next(gen)['title'], 'First')
        stop[0] = True
        self.assertEqual(list(gen), [])

    def test_unavailable_creator(self):
        with self.assertRaises(RuntimeError):
            list(iter_channel_videos('https://www.xvideos.com/channels/nope',
                                     fetch_page=lambda *_: None))
