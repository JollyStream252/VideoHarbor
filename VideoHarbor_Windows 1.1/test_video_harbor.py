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
