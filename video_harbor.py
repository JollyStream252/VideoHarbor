"""VideoHarbor: a small Windows desktop frontend for yt-dlp.

Copyright (c) 2026. MIT License. yt-dlp is a separate upstream project.
"""
from __future__ import annotations

import io
import os
import queue
import re
import shutil
import sys
import threading
import time
import ctypes
import uuid
import traceback
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    import yt_dlp
    from yt_dlp.utils import DownloadError
except ImportError:
    yt_dlp = None
    DownloadError = Exception

try:
    from PIL import Image, ImageDraw, ImageOps, ImageTk
except ImportError:
    Image = ImageDraw = ImageOps = ImageTk = None

APP_NAME = 'VideoHarbor 1.2'
QUALITY_CHOICES = ('Best available', '2160p (4K)', '1440p (2K)', '1080p (Full HD)',
                   '720p (HD)', '480p', '360p', 'Audio only (MP3)')
URL_PATTERN = re.compile(r'https?://[^\s<>"\']+', flags=re.IGNORECASE)
SITE_LABELS = {
    'youtube.com': 'YouTube', 'youtu.be': 'YouTube', 'vimeo.com': 'Vimeo',
    'tiktok.com': 'TikTok', 'instagram.com': 'Instagram',
    'facebook.com': 'Facebook', 'fb.watch': 'Facebook',
    'x.com': 'X', 'twitter.com': 'X', 'reddit.com': 'Reddit',
    'twitch.tv': 'Twitch', 'dailymotion.com': 'Dailymotion',
    'soundcloud.com': 'SoundCloud', 'bilibili.com': 'Bilibili',
    'archive.org': 'Internet Archive', 'kick.com': 'Kick',
}
COLOR_BG = '#101722'
COLOR_PANEL = '#1a2535'
COLOR_PANEL2 = '#223149'
COLOR_TEXT = '#f2f6fc'
COLOR_MUTED = '#a6b4c8'
COLOR_ACCENT = '#39bda4'
COLOR_DANGER = '#e06a73'


def clean_urls(raw: str) -> list[str]:
    """Accept pasted links separated by spaces, commas or newlines."""
    result, seen = [], set()
    for match in URL_PATTERN.findall(raw):
        url = match.rstrip('),;.!]}>')
        if url not in seen:
            seen.add(url)
            result.append(url)
    return result


def safe_name(raw: str | None, fallback: str = 'Untitled') -> str:
    """Use Windows-compatible folder/file names, including on Windows drives."""
    name = (raw or '').strip()
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name)
    name = re.sub(r'\s+', ' ', name).strip(' .')[:90].rstrip(' .')
    if not name:
        name = fallback
    if name.upper().split('.')[0] in {'CON', 'PRN', 'AUX', 'NUL',
                                      *(f'COM{i}' for i in range(1, 10)),
                                      *(f'LPT{i}' for i in range(1, 10))}:
        name = '_' + name
    return name


def website_name(url: str | None, info: dict | None = None) -> str:
    """Interpret webpage name as the website/service name rather than video title."""
    candidates = [url]
    if info:
        candidates += [info.get('webpage_url'), info.get('original_url')]
    for candidate in candidates:
        if not candidate:
            continue
        host = (urllib.parse.urlparse(candidate).hostname or '').lower()
        host = re.sub(r'^(www\.|m\.|mobile\.)', '', host)
        for domain, label in SITE_LABELS.items():
            if host == domain or host.endswith('.' + domain):
                return label
        if host:
            segments = host.split('.')
            core = segments[-2] if len(segments) >= 2 else segments[0]
            if core in {'co', 'com', 'org', 'net', 'gov'} and len(segments) >= 3:
                core = segments[-3]
            return safe_name(core.replace('-', ' ').title(), 'Website')
    if info and info.get('extractor_key'):
        return safe_name(str(info['extractor_key']))
    return 'Website'


def quality_height(quality: str) -> int | None:
    if quality.startswith('Audio') or quality == 'Best available':
        return None
    m = re.match(r'(\d+)p', quality)
    return int(m.group(1)) if m else None


def format_selector(quality: str) -> str:
    if quality.startswith('Audio'):
        return 'bestaudio/best'
    h = quality_height(quality)
    if h is None:
        return 'bv*+ba/b'
    # Never exceed the user-selected resolution cap.
    return (f'bv*[height<={h}]+ba/b[height<={h}]/'
            f'bv*[height<={h}]')


def format_bytes(fmt: dict, duration: float | None) -> tuple[int | None, bool]:
    exact = fmt.get('filesize')
    if isinstance(exact, (int, float)) and exact > 0:
        return int(exact), False
    approx = fmt.get('filesize_approx')
    if isinstance(approx, (int, float)) and approx > 0:
        return int(approx), True
    bitrate = fmt.get('tbr') or fmt.get('vbr') or fmt.get('abr')
    if bitrate and duration:
        return int(duration * float(bitrate) * 1000 / 8), True
    return None, False


def compact_formats(info: dict) -> list[dict]:
    return [{k: fmt.get(k) for k in ('format_id', 'vcodec', 'acodec', 'height',
                                    'tbr', 'abr', 'vbr', 'filesize', 'filesize_approx')}
            for fmt in (info.get('formats') or []) if isinstance(fmt, dict)]


def estimate_size(formats: list[dict], duration: float | None, quality: str,
                  fallback_size: int | None = None) -> tuple[int | None, bool]:
    """Best-effort size estimate, not a promise of the exact selected stream."""
    if quality.startswith('Audio') and duration:
        # The MP3 postprocessor targets 192 kbps, independent of input format size.
        return int(duration * 192_000 / 8), True
    if not formats:
        return fallback_size, bool(fallback_size)
    cap = quality_height(quality)
    video, muxed, audio = [], [], []
    for fmt in formats:
        has_video = fmt.get('vcodec') not in (None, 'none')
        has_audio = fmt.get('acodec') not in (None, 'none')
        height = fmt.get('height') or 0
        if cap and has_video and height and height > cap:
            continue
        if has_video and has_audio:
            muxed.append(fmt)
        elif has_video:
            video.append(fmt)
        elif has_audio:
            audio.append(fmt)
    key = lambda f: (f.get('height') or 0, f.get('tbr') or 0, f.get('vbr') or 0)
    akey = lambda f: (f.get('abr') or 0, f.get('tbr') or 0)
    selected = []
    if quality.startswith('Audio'):
        if audio:
            selected = [max(audio, key=akey)]
        elif muxed:
            selected = [max(muxed, key=key)]
    elif video and audio:
        selected = [max(video, key=key), max(audio, key=akey)]
    elif muxed:
        selected = [max(muxed, key=key)]
    elif video:
        selected = [max(video, key=key)]
    if not selected and cap:
        return estimate_size(formats, duration, 'Best available', fallback_size)
    amounts = [format_bytes(fmt, duration) for fmt in selected]
    if not amounts or any(size is None for size, _ in amounts):
        return fallback_size, bool(fallback_size)
    return sum(size for size, _ in amounts), (quality.startswith('Audio') or
                                              any(approx for _, approx in amounts))


def readable_size(n: int | None, approximate: bool = False) -> str:
    if n is None:
        return 'Unknown'
    number = float(n)
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if number < 1024 or unit == 'TB':
            return f'{"≈" if approximate else ""}{number:.1f} {unit}'
        number /= 1024
    return 'Unknown'


def readable_duration(value: Any) -> str:
    if not isinstance(value, (int, float)) or value < 0:
        return 'Unknown'
    secs = int(value)
    h, remainder = divmod(secs, 3600)
    m, s = divmod(remainder, 60)
    return f'{h:d}:{m:02d}:{s:02d}' if h else f'{m:d}:{s:02d}'


@dataclass
class Video:
    key: str
    title: str
    url: str
    site: str
    group: str
    playlist: bool
    index: int
    duration: float | None
    formats: list[dict] = field(default_factory=list)
    fallback_size: int | None = None
    thumbnail: str | None = None
    status: str = 'Ready'
    playlist_total: int = 0
    saved_files: list[Path] = field(default_factory=list)
    completed_media: Path | None = None
    actual_size: int | None = None
    download_root: Path | None = None

    def destination(self, root: Path, individual_mode: str = 'Website name') -> Path:
        folder = self.group if self.playlist else (
            self.title if individual_mode == 'Video page title' else self.site)
        return root / safe_name(folder, self.site)


def videos_from_info(info: dict, input_url: str):
    """Yield playlist entries, preserving input-order and playlist naming."""
    if not isinstance(info, dict):
        return
    source_site = website_name(input_url, info)
    if 'entries' in info and info['entries'] is not None:
        group = safe_name(info.get('title'), source_site)
        entries = info['entries']
        playlist_total = len(entries) if hasattr(entries, '__len__') else 0
        for i, entry in enumerate(entries, 1):
            if not isinstance(entry, dict):
                continue
            video_url = (entry.get('webpage_url') or entry.get('original_url')
                         or entry.get('url') or '')
            if video_url and not video_url.startswith('http'):
                # A few flat extractors expose only the ID of the video.
                video_url = f'https://www.youtube.com/watch?v={video_url}' if \
                    source_site == 'YouTube' else ''
            if not video_url:
                continue
            yield Video('', entry.get('title') or f'Video {i}', video_url,
                        website_name(video_url, entry), group, True,
                        entry.get('playlist_index') or i, entry.get('duration'),
                        compact_formats(entry),
                        entry.get('filesize') or entry.get('filesize_approx'),
                        entry.get('thumbnail'), playlist_total=playlist_total)
    else:
        video_url = info.get('webpage_url') or input_url
        yield Video('', info.get('title') or 'Untitled video', video_url,
                    source_site, source_site, False, 0, info.get('duration'),
                    compact_formats(info),
                    info.get('filesize') or info.get('filesize_approx'),
                    info.get('thumbnail'))


class UserCancelled(Exception):
    pass


class GuiLogger:
    def __init__(self, send):
        self.send = send

    def debug(self, msg):
        if msg and ('Downloading' in msg or '[download]' in msg):
            self.send('log', str(msg))

    def warning(self, msg):
        self.send('log', 'Warning: ' + str(msg))

    def error(self, msg):
        self.send('log', 'Error: ' + str(msg))




class WindowsTaskbarProgress:
    """Windows 7+ ITaskbarList3 progress bar; harmless no-op on other OSes."""
    NO_PROGRESS, INDETERMINATE, NORMAL, ERROR, PAUSED = 0, 1, 2, 4, 8

    def __init__(self, hwnd: int):
        self.object = None
        self._com_initialized = False
        self.hwnd = hwnd
        if sys.platform != 'win32':
            return
        try:
            class GUID(ctypes.Structure):
                _fields_ = [('Data1', ctypes.c_uint32), ('Data2', ctypes.c_uint16),
                            ('Data3', ctypes.c_uint16), ('Data4', ctypes.c_ubyte * 8)]

            def guid(value):
                u = uuid.UUID(value)
                return GUID.from_buffer_copy(u.bytes_le)

            # Tk may expose a child HWND. The root is the taskbar window.
            ctypes.windll.user32.GetAncestor.argtypes = (ctypes.c_void_p, ctypes.c_uint)
            ctypes.windll.user32.GetAncestor.restype = ctypes.c_void_p
            self.hwnd = ctypes.windll.user32.GetAncestor(hwnd, 2) or hwnd
            rc_init = ctypes.windll.ole32.CoInitialize(None)
            self._com_initialized = (rc_init >= 0)
            clsid = guid('56fdf344-fd6d-11d0-958a-006097c9a090')
            iid = guid('ea1afb91-9e28-4b86-90e9-9e9f8a5ee155')
            pointer = ctypes.c_void_p()
            co = ctypes.windll.ole32.CoCreateInstance
            co.argtypes = (ctypes.POINTER(GUID), ctypes.c_void_p, ctypes.c_uint,
                           ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))
            co.restype = ctypes.c_long
            rc = co(ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(pointer))
            if rc < 0 or not pointer.value:
                return
            self.object = pointer
            self._call(3, ctypes.c_long, ())  # HrInit
        except Exception:
            self.object = None

    def _call(self, index, restype, signature, *args):
        if self.object is None:
            return
        try:
            ptr = ctypes.cast(self.object, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))
            address = ptr.contents[index]
            func = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *signature)(address)
            func(self.object, *args)
        except Exception:
            pass

    def state(self, mode):
        self._call(10, ctypes.c_long, (ctypes.c_void_p, ctypes.c_uint), self.hwnd, mode)

    def value(self, percent):
        self._call(9, ctypes.c_long,
                   (ctypes.c_void_p, ctypes.c_ulonglong, ctypes.c_ulonglong),
                   self.hwnd, min(1000, max(0, round(percent * 10))), 1000)

    def close(self):
        self.state(self.NO_PROGRESS)
        self._call(2, ctypes.c_ulong, ())
        self.object = None
        if self._com_initialized:
            ctypes.windll.ole32.CoUninitialize()
            self._com_initialized = False


def app_asset(filename: str) -> Path:
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent)) / 'assets' / filename


def app_files_created(folder: Path, before: set[Path]) -> list[Path]:
    """Only files newly written during one download attempt, never old user files."""
    try:
        return sorted((p for p in folder.iterdir() if p.is_file() and
                       not p.is_symlink() and p not in before), key=str)
    except OSError:
        return []


def safe_delete_tracked(path: Path, root: Path) -> bool:
    """Guardrail: delete only a known file underneath the configured download root."""
    try:
        if path.is_symlink() or not path.is_file():
            return False
        resolved = path.resolve(strict=True)
        base = root.resolve(strict=True)
        if resolved == base or base not in resolved.parents:
            return False
        path.unlink()
        return True
    except OSError:
        return False


def playlist_counter(video: Video) -> str:
    if not video.playlist:
        return '—'
    return f'{video.index}/{video.playlist_total}' if video.playlist_total else str(video.index)


class VideoHarborApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry('1330x820')
        self.minsize(1050, 700)
        self.configure(bg=COLOR_BG)
        if sys.platform == 'win32':
            try:
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('Jovial.VideoHarbor.1.2')
            except Exception:
                pass
        self._icon_photo = None
        try:
            ico = app_asset('VideoHarbor.ico')
            png = app_asset('VideoHarbor.png')
            if sys.platform == 'win32' and ico.exists():
                self.iconbitmap(default=str(ico))
            if png.exists():
                self._icon_photo = tk.PhotoImage(file=str(png))
                self.iconphoto(True, self._icon_photo)
        except Exception:
            pass
        self.events = queue.Queue()
        self.stop_flag = threading.Event()
        self.worker_running = False
        self.operation = ''
        self.thumbs = ThreadPoolExecutor(max_workers=4, thread_name_prefix='thumbnail')
        self.log_lines = []
        self.log_window = None
        self.log_box = None
        self.videos: list[Video] = []
        self.archive_videos: list[Video] = []
        self.by_key: dict[str, Video] = {}
        self.row_widgets: dict[str, dict] = {}
        self.images = {}
        self.queued_urls: list[str] = []
        self.image_cache = {}
        self.save_var = tk.StringVar(value=str(Path.home() / 'Videos' / 'VideoHarbor'))
        self.quality_var = tk.StringVar(value='Best available')
        self.folder_mode_var = tk.StringVar(value='Website name')
        self.status_var = tk.StringVar(value='Paste links to add them to the queue, then click Analyze.')
        self.active_var = tk.StringVar(value='Idle')
        self.count_var = tk.StringVar(value='0 videos • 0 links')
        self.queue_var = tk.StringVar(value='No links in queue')
        self.progress_var = tk.DoubleVar(value=0)
        self.taskbar = None
        self._make_style()
        self._build_ui()
        self._load_blank_thumb()
        self.quality_var.trace_add('write', lambda *_: self._update_size_rows())
        self.folder_mode_var.trace_add('write', lambda *_: self._update_folder_rows())
        self.after(350, self._initialize_taskbar)
        self.after(100, self._poll)
        self.protocol('WM_DELETE_WINDOW', self._on_close)

    def _initialize_taskbar(self):
        self.taskbar = WindowsTaskbarProgress(self.winfo_id())

    def _make_style(self):
        st = ttk.Style(self)
        st.theme_use('clam')
        st.configure('.', background=COLOR_BG, foreground=COLOR_TEXT, font=('Segoe UI', 10))
        st.configure('TFrame', background=COLOR_BG)
        st.configure('TLabel', background=COLOR_BG, foreground=COLOR_TEXT)
        st.configure('Muted.TLabel', background=COLOR_BG, foreground=COLOR_MUTED)
        st.configure('TButton', padding=(12, 8), background=COLOR_PANEL2,
                     foreground=COLOR_TEXT, borderwidth=0)
        st.map('TButton', background=[('active', '#395273'), ('disabled', '#253044')],
               foreground=[('disabled', '#8393a8')])
        st.configure('Accent.TButton', background=COLOR_ACCENT,
                     foreground=COLOR_BG, font=('Segoe UI Semibold', 10))
        st.map('Accent.TButton', background=[('active', '#70e8cd')])
        st.configure('Danger.TButton', background=COLOR_DANGER, foreground='white')
        st.map('Danger.TButton', background=[('active', '#ef8491')])
        st.configure('TEntry', fieldbackground=COLOR_PANEL, foreground=COLOR_TEXT,
                     insertcolor=COLOR_TEXT, padding=8)
        st.configure('TCombobox', fieldbackground=COLOR_PANEL, background=COLOR_PANEL,
                     foreground=COLOR_TEXT, arrowcolor=COLOR_TEXT, padding=8)
        st.map('TCombobox', fieldbackground=[('readonly', COLOR_PANEL)],
               foreground=[('readonly', COLOR_TEXT)])
        st.configure('Horizontal.TProgressbar', troughcolor=COLOR_PANEL2,
                     background=COLOR_ACCENT, thickness=12)
        st.configure('Blue.Horizontal.TProgressbar', troughcolor=COLOR_PANEL2,
                     background='#39a8fa', thickness=12)

    def _build_ui(self):
        wrap = ttk.Frame(self, padding=16)
        wrap.pack(fill='both', expand=True)
        header = ttk.Frame(wrap)
        header.pack(fill='x', pady=(0, 12))
        img_path = app_asset('VideoHarbor.png')
        if Image is not None and ImageTk is not None and img_path.exists():
            icon_img = Image.open(img_path).convert('RGBA').resize((46, 46), Image.Resampling.LANCZOS)
            self.header_icon = ImageTk.PhotoImage(icon_img)
            tk.Label(header, image=self.header_icon, bg=COLOR_BG).pack(side='left', padx=(0, 10))
        ttk.Label(header, text='VideoHarbor 1.2', font=('Segoe UI Semibold', 21)).pack(side='left')
        ttk.Label(header, text='  •  yt-dlp powered  •  Windows desktop',
                  style='Muted.TLabel').pack(side='left', pady=9)
        ttk.Label(wrap, text='Paste video or playlist URLs (multiple links supported)',
                  font=('Segoe UI Semibold', 11)).pack(anchor='w')
        top = ttk.Frame(wrap)
        top.pack(fill='x', pady=(6, 4))
        self.link_box = tk.Text(top, height=2, bg=COLOR_PANEL, fg=COLOR_TEXT,
                                insertbackground=COLOR_TEXT, selectbackground='#325272',
                                padx=10, pady=8, relief='flat', font=('Consolas', 10))
        self.link_box.pack(side='left', fill='both', expand=True, padx=(0, 10))
        self.link_box.bind('<<Paste>>', self._on_paste_event)
        self.link_box.bind('<Return>', self._on_link_enter)
        controls = ttk.Frame(top)
        controls.pack(side='right', fill='y')
        ttk.Button(controls, text='Paste + add', command=self.paste).pack(side='left', padx=(0, 6))
        self.add_btn = ttk.Button(controls, text='Add links', command=self.add_typed_links)
        self.add_btn.pack(side='left', padx=(0, 6))
        self.analyze_btn = ttk.Button(controls, text='Analyze', style='Accent.TButton',
                                      command=self.analyze)
        self.analyze_btn.pack(side='left')
        ttk.Label(wrap, textvariable=self.queue_var, style='Muted.TLabel').pack(anchor='w', pady=(0, 9))

        options = ttk.Frame(wrap)
        options.pack(fill='x', pady=(0, 12))
        ttk.Label(options, text='Save to:').grid(row=0, column=0, sticky='w', pady=(0, 3))
        ttk.Label(options, text='Individual folder:').grid(row=0, column=2,
                                                           sticky='w', padx=(12, 0), pady=(0, 3))
        ttk.Label(options, text='Quality:').grid(row=0, column=3,
                                                 sticky='w', padx=(12, 0), pady=(0, 3))
        ttk.Entry(options, textvariable=self.save_var).grid(row=1, column=0,
                                                             sticky='ew', padx=(0, 6))
        ttk.Button(options, text='Browse…', command=self.browse).grid(row=1, column=1)
        ttk.Combobox(options, textvariable=self.folder_mode_var, state='readonly',
                     values=('Website name', 'Video page title'), width=20).grid(
                         row=1, column=2, sticky='ew', padx=(12, 0))
        ttk.Combobox(options, textvariable=self.quality_var,
                     values=QUALITY_CHOICES, state='readonly', width=23).grid(
                         row=1, column=3, sticky='ew', padx=(12, 0))
        options.columnconfigure(0, weight=1)

        title_row = ttk.Frame(wrap)
        title_row.pack(fill='x', pady=(0, 7))
        ttk.Label(title_row, text='Video selector', font=('Segoe UI Semibold', 12)).pack(side='left')
        ttk.Button(title_row, text='Select all', command=lambda: self.set_selection(True)).pack(
            side='left', padx=(10, 5))
        ttk.Button(title_row, text='Select none', command=lambda: self.set_selection(False)).pack(side='left')
        ttk.Label(title_row, textvariable=self.count_var,
                  style='Muted.TLabel').pack(side='right')

        table_outer = tk.Frame(wrap, bg=COLOR_PANEL, highlightbackground='#344459', highlightthickness=1)
        table_outer.pack(fill='both', expand=True)
        # Columns: select | playlist number | image | title | duration | size | source | folder | status.
        self.column_weights = (0, 0, 0, 5, 0, 0, 0, 2, 2)
        self.column_widths = (45, 62, 116, 260, 76, 98, 91, 150, 147)
        header = tk.Frame(table_outer, bg=COLOR_PANEL2)
        header.pack(fill='x')
        for col, heading in enumerate(('Select', '# / total', 'Thumbnail', 'Video title',
                                        'Duration', 'Size*', 'Website', 'Save folder', 'Status')):
            lab = tk.Label(header, text=heading, bg=COLOR_PANEL2, fg=COLOR_TEXT,
                           font=('Segoe UI Semibold', 9), anchor='w' if col in (3, 7) else 'center',
                           padx=5, pady=9)
            lab.grid(row=0, column=col, sticky='nsew')
            header.columnconfigure(col, weight=self.column_weights[col], minsize=self.column_widths[col])
        scrolling = tk.Frame(table_outer, bg=COLOR_PANEL)
        scrolling.pack(fill='both', expand=True)
        self.canvas = tk.Canvas(scrolling, bg=COLOR_PANEL, highlightthickness=0, borderwidth=0)
        self.scroll = ttk.Scrollbar(scrolling, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scroll.set)
        self.scroll.pack(side='right', fill='y')
        self.canvas.pack(side='left', fill='both', expand=True)
        self.rows_frame = tk.Frame(self.canvas, bg=COLOR_PANEL)
        self.rows_canvas_item = self.canvas.create_window(0, 0, window=self.rows_frame, anchor='nw')
        self.rows_frame.bind('<Configure>', self._rows_resize)
        self.canvas.bind('<Configure>', self._canvas_resize)
        self.canvas.bind('<MouseWheel>', self._wheel)
        self.rows_frame.bind('<MouseWheel>', self._wheel)

        actions = ttk.Frame(wrap)
        actions.pack(fill='x', pady=(11, 8))
        self.download_btn = ttk.Button(actions, text='Download checked',
                                       style='Accent.TButton', command=self.download_selected)
        self.download_btn.pack(side='left', padx=(0, 6))
        self.download_all_btn = ttk.Button(actions, text='Download all', command=self.download_all)
        self.download_all_btn.pack(side='left', padx=(0, 6))
        self.stop_btn = ttk.Button(actions, text='Stop', style='Danger.TButton', command=self.cancel)
        self.stop_btn.pack(side='left', padx=(0, 12))
        self.clear_btn = ttk.Button(actions, text='Clear', command=self.clear_completed)
        self.clear_btn.pack(side='left', padx=(0, 6))
        self.clear_all_btn = ttk.Button(actions, text='Clear All', style='Danger.TButton',
                                        command=self.clear_all)
        self.clear_all_btn.pack(side='left')
        ttk.Button(actions, text='Open folder', command=self.open_folder).pack(side='right')
        ttk.Button(actions, text='Activity log', command=self.show_log).pack(side='right', padx=(0, 6))

        self.progress = ttk.Progressbar(wrap, maximum=100, mode='determinate',
                                         variable=self.progress_var, style='Blue.Horizontal.TProgressbar')
        self.progress.pack(fill='x', pady=(4, 5))
        ttk.Label(wrap, textvariable=self.active_var,
                  font=('Segoe UI Semibold', 10)).pack(anchor='w')
        ttk.Label(wrap, textvariable=self.status_var, style='Muted.TLabel').pack(anchor='w', pady=(3, 1))
        ttk.Label(wrap, text='Clear deletes completed app-tracked downloads; Clear All deletes all '
                  'app-tracked files and queued links. Both request confirmation. '
                  'Only save content you are authorized to download.',
                  style='Muted.TLabel', wraplength=1200).pack(anchor='w', pady=(5, 0))
        self._set_buttons()

    def _wheel(self, event):
        self.canvas.yview_scroll(-int(event.delta / 120), 'units')

    def _rows_resize(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox('all'))

    def _canvas_resize(self, event):
        self.canvas.itemconfigure(self.rows_canvas_item, width=event.width)

    def _load_blank_thumb(self):
        self.default_thumb = None
        if Image is None or ImageTk is None:
            return
        im = Image.new('RGB', (104, 59), '#2b415a')
        draw = ImageDraw.Draw(im)
        draw.rounded_rectangle((32, 11, 73, 49), radius=11, fill='#356893')
        draw.polygon([(47, 20), (47, 40), (62, 30)], fill='#f3fcff')
        self.default_thumb = ImageTk.PhotoImage(im)

    def _on_paste_event(self, event):
        try:
            self._queue_text(self.clipboard_get())
        except tk.TclError:
            pass
        return 'break'

    def _on_link_enter(self, event):
        self.add_typed_links()
        return 'break'

    def paste(self):
        try:
            self._queue_text(self.clipboard_get())
        except tk.TclError:
            self.status_var.set('Clipboard does not contain text.')

    def add_typed_links(self):
        self._queue_text(self.link_box.get('1.0', 'end'))

    def _queue_text(self, text):
        links = clean_urls(text)
        additions = [u for u in links if u not in self.queued_urls]
        self.queued_urls.extend(additions)
        self.link_box.delete('1.0', 'end')  # Clear input once pasted/added.
        self.queue_var.set(f'{len(self.queued_urls)} link(s) in queue; '
                           f'{len(additions)} newly added. Click Analyze.')
        if not links:
            self.status_var.set('No valid http:// or https:// URL found in pasted text.')
        self._refresh_counts()

    def browse(self):
        folder = filedialog.askdirectory(title='Choose download destination',
                                        initialdir=self.save_var.get())
        if folder:
            self.save_var.set(folder)

    def open_folder(self):
        path = Path(self.save_var.get()).expanduser()
        try:
            path.mkdir(parents=True, exist_ok=True)
            if sys.platform == 'win32':
                os.startfile(str(path))
            else:
                import subprocess
                subprocess.Popen(['open' if sys.platform == 'darwin' else 'xdg-open', str(path)])
        except Exception as exc:
            messagebox.showerror('Could not open folder', str(exc))

    def _refresh_counts(self):
        num_playlists = len({(v.group, v.site) for v in self.videos if v.playlist})
        self.count_var.set(f'{len(self.videos)} video(s)  •  {num_playlists} playlist(s)'
                           f'  •  {len(self.queued_urls)} link(s)')

    def _add_video(self, video):
        self.videos.append(video)
        self.by_key[video.key] = video
        row = tk.Frame(self.rows_frame, bg=COLOR_PANEL if len(self.videos) % 2 else '#1d2c3d',
                       height=68)
        row.pack(fill='x', pady=(0, 1))
        row.pack_propagate(False)
        selected = tk.BooleanVar(value=True)
        bg = row.cget('bg')
        cb = tk.Checkbutton(row, variable=selected, bg=bg, activebackground=bg,
                            selectcolor=COLOR_PANEL2, highlightthickness=0,
                            fg=COLOR_TEXT, activeforeground=COLOR_TEXT,
                            cursor='hand2', bd=0)
        cb.grid(row=0, column=0, sticky='nsew')
        display = playlist_counter(video)
        num = tk.Label(row, text=display, bg=bg, fg='#79b9ff',
                       font=('Segoe UI Semibold', 9), anchor='center')
        num.grid(row=0, column=1, sticky='nsew')
        photo = tk.Label(row, image=self.default_thumb, bg=bg, padx=2)
        photo.grid(row=0, column=2, sticky='nsew')
        names = [video.title, readable_duration(video.duration),
                 readable_size(*estimate_size(video.formats, video.duration,
                                               self.quality_var.get(), video.fallback_size)),
                 video.site, video.destination(Path('.'), self.folder_mode_var.get()).name,
                 video.status]
        labels = []
        for col, text in zip(range(3, 9), names):
            lab = tk.Label(row, text=text, bg=bg, fg=COLOR_TEXT if col != 8 else COLOR_MUTED,
                           font=('Segoe UI', 9), padx=6, anchor='w' if col in (3, 7, 8) else 'center')
            lab.grid(row=0, column=col, sticky='nsew')
            labels.append(lab)
        for col, (weight, minsize) in enumerate(zip(self.column_weights, self.column_widths)):
            row.columnconfigure(col, weight=weight, minsize=minsize)
        row.grid_rowconfigure(0, weight=1)
        widgets = {'row': row, 'check': selected, 'check_widget': cb, 'number': num,
                   'photo': photo, 'name': labels[0], 'duration': labels[1], 'size': labels[2],
                   'site': labels[3], 'folder': labels[4], 'status': labels[5]}
        self.row_widgets[video.key] = widgets
        self._refresh_counts()

    def set_selection(self, value):
        for item in self.row_widgets.values():
            item['check'].set(value)

    def _update_size_rows(self):
        for video in self.videos:
            widgets = self.row_widgets.get(video.key)
            if widgets:
                n, approximate = estimate_size(video.formats, video.duration,
                                               self.quality_var.get(), video.fallback_size)
                widgets['size'].configure(text=readable_size(video.actual_size, False) if
                                          video.actual_size is not None else readable_size(n, approximate))

    def _update_folder_rows(self):
        for video in self.videos:
            widgets = self.row_widgets.get(video.key)
            if widgets:
                widgets['folder'].configure(text=video.destination(
                    Path('.'), self.folder_mode_var.get()).name)

    def _remove_video(self, video):
        widgets = self.row_widgets.pop(video.key, None)
        if widgets:
            widgets['row'].destroy()
        self.by_key.pop(video.key, None)
        self.images.pop(video.key, None)
        self.videos = [v for v in self.videos if v.key != video.key]
        self._refresh_counts()

    def _clear_rows(self):
        for v in list(self.videos):
            self._remove_video(v)
        self.videos.clear()
        self.by_key.clear()
        self.images.clear()
        self._refresh_counts()

    def _set_buttons(self):
        busy = self.worker_running
        self.analyze_btn.configure(state='disabled' if busy else 'normal')
        self.add_btn.configure(state='disabled' if busy else 'normal')
        self.download_btn.configure(state='normal' if self.videos and not busy else 'disabled')
        self.download_all_btn.configure(state='normal' if self.videos and not busy else 'disabled')
        self.stop_btn.configure(state='normal' if busy else 'disabled')
        self.clear_btn.configure(state='normal' if any(v.status == 'Complete' for v in (self.videos + self.archive_videos)) and not busy else 'disabled')
        self.clear_all_btn.configure(state='disabled' if busy else 'normal')

    def _start(self, operation, job, *args):
        if self.worker_running:
            return
        self.worker_running = True
        self.operation = operation
        self.stop_flag.clear()
        if self.taskbar:
            self.taskbar.state(self.taskbar.INDETERMINATE if operation == 'analysis'
                               else self.taskbar.NORMAL)
            if operation != 'analysis':
                self.taskbar.value(0)
        if operation == 'analysis':
            self.progress.configure(mode='indeterminate')
            self.progress.start(12)
        else:
            self.progress.stop()
            self.progress.configure(mode='determinate')
        self._set_buttons()
        threading.Thread(target=job, args=args, daemon=True).start()

    def analyze(self):
        if yt_dlp is None:
            messagebox.showerror('yt-dlp missing', 'Run Setup_Windows.bat first.')
            return
        self.add_typed_links()
        if not self.queued_urls:
            messagebox.showwarning('No URLs', 'Paste a video or playlist link first.')
            return
        # Keep file records from earlier analyses for safe Clear/Clear All.
        self.archive_videos.extend(v for v in self.videos if v.saved_files)
        self._clear_rows()
        self.progress_var.set(0)
        self.active_var.set('Searching videos and playlist details…')
        self.status_var.set(f'Analyzing {len(self.queued_urls)} queued link(s)')
        self._start('analysis', self._analyze_worker, list(self.queued_urls))

    def _send(self, event, *args):
        self.events.put((event, *args))

    def _analyze_worker(self, urls):
        total = 0
        try:
            options = {'quiet': True, 'no_warnings': False, 'logger': GuiLogger(self._send),
                       'skip_download': True, 'ignoreerrors': True, 'extract_flat': False,
                       'noplaylist': False, 'socket_timeout': 22, 'retries': 3}
            with yt_dlp.YoutubeDL(options) as ydl:
                for n, url in enumerate(urls, 1):
                    if self.stop_flag.is_set():
                        break
                    self._send('status', f'Searching link {n}/{len(urls)}: {url[:85]}')
                    self._send('active', f'🔎 Analyzing {n}/{len(urls)} links')
                    self._send('title', f'Analyzing {n}/{len(urls)}')
                    try:
                        info = ydl.extract_info(url, download=False)
                        if not info:
                            self._send('log', f'No videos found at {url}')
                            continue
                        for video in videos_from_info(info, url):
                            if self.stop_flag.is_set():
                                break
                            if not video.formats and video.url.startswith('http'):
                                try:
                                    detail = ydl.extract_info(video.url, download=False)
                                    if detail and isinstance(detail, dict) and 'entries' not in detail:
                                        video.title = detail.get('title') or video.title
                                        video.duration = detail.get('duration') or video.duration
                                        video.formats = compact_formats(detail)
                                        video.fallback_size = detail.get('filesize') or detail.get('filesize_approx') or video.fallback_size
                                        video.thumbnail = detail.get('thumbnail') or video.thumbnail
                                except Exception as exc:
                                    self._send('log', f'Limited details for {video.title}: {exc}')
                            total += 1
                            video.key = f'video_{total}'
                            self._send('item', video)
                            if video.thumbnail:
                                self.thumbs.submit(self._load_thumbnail, video.key, video.thumbnail)
                    except Exception as exc:
                        self._send('log', f'Could not analyze {url}: {exc}')
            self._send('done', 'analysis', total, 0, self.stop_flag.is_set())
        except BaseException as exc:
            self._send('fatal', 'Analysis', str(exc))

    def _fetch_image(self, url):
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=12) as stream:
            data = stream.read(6_000_000)
        return ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert('RGB')

    def _load_thumbnail(self, key, url):
        if Image is None:
            return
        try:
            original = self._fetch_image(url)
            thumb = original.copy()
            thumb.thumbnail((106, 60), Image.Resampling.LANCZOS)
            board = Image.new('RGB', (106, 60), '#2b415a')
            board.paste(thumb, ((106 - thumb.width)//2, (60 - thumb.height)//2))
            self._send('thumbnail', key, board)
        except Exception:
            pass

    def download_selected(self):
        chosen = [v for v in self.videos if self.row_widgets.get(v.key, {}).get('check')
                  and self.row_widgets[v.key]['check'].get()]
        if not chosen:
            messagebox.showinfo('No videos checked', 'Check one or more videos first.')
            return
        self._download(chosen)

    def download_all(self):
        self._download(list(self.videos))

    def _download(self, chosen):
        if not chosen or yt_dlp is None:
            return
        root = Path(self.save_var.get().strip()).expanduser()
        if not root.is_absolute():
            messagebox.showwarning('Invalid destination', 'Choose an absolute save folder.')
            return
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror('Cannot create save folder', str(exc))
            return
        quality = self.quality_var.get()
        if shutil.which('ffmpeg') is None:
            if quality.startswith('Audio'):
                messagebox.showerror('FFmpeg required', 'Install FFmpeg to convert to MP3.')
                return
            if not messagebox.askyesno('FFmpeg not found',
                                       'High-quality audio/video streams may fail to merge. Continue?'):
                return
        self.progress_var.set(0)
        self.active_var.set('Preparing your downloads…')
        for video in chosen:
            video.status = 'Queued'
            self._video_status(video.key, 'Queued')
        self._start('download', self._download_worker, chosen, root, quality,
                    self.folder_mode_var.get())

    def _download_worker(self, chosen, root, quality, folder_mode):
        completed = failed = 0
        count = len(chosen)
        logger = GuiLogger(self._send)
        try:
            for ix, video in enumerate(chosen):
                if self.stop_flag.is_set():
                    break
                video.download_root = root
                folder = video.destination(root, folder_mode)
                folder.mkdir(parents=True, exist_ok=True)
                before = {p for p in folder.iterdir() if p.is_file()}
                self._send('active', f'⬇ Downloading {ix + 1}/{count}: {video.title}')
                self._send('status', f'Saving into {folder}')
                self._send('title', f'Downloading {ix + 1}/{count}: {video.title}')
                self._send('video_status', video.key, 'Downloading 0%')
                template = ('%(title).145s [%(id)s].%(ext)s' if not video.playlist else
                            f'{video.index:03d} - %(title).125s [%(id)s].%(ext)s')

                def progress_hook(data, _ix=ix, _video=video):
                    if self.stop_flag.is_set():
                        raise UserCancelled('Stopped')
                    if data.get('status') == 'downloading':
                        got = data.get('downloaded_bytes') or 0
                        total = data.get('total_bytes') or data.get('total_bytes_estimate')
                        fraction = min(got / total, .99) if total else 0
                        self._send('progress', (_ix + fraction) / count * 100)
                        pct = f'{fraction * 100:.0f}%' if total else readable_size(got)
                        self._send('video_status', _video.key, 'Downloading ' + pct)
                    elif data.get('status') == 'finished':
                        self._send('video_status', _video.key, 'Processing…')

                def postprocess_hook(_data):
                    if self.stop_flag.is_set():
                        raise UserCancelled('Stopped')

                options = {'format': format_selector(quality), 'paths': {'home': str(folder)},
                           'outtmpl': {'default': template}, 'noplaylist': True,
                           'quiet': True, 'no_warnings': False, 'logger': logger,
                           'progress_hooks': [progress_hook],
                           'postprocessor_hooks': [postprocess_hook],
                           'ignoreerrors': False, 'overwrites': False,
                           'socket_timeout': 25, 'retries': 5, 'fragment_retries': 5,
                           'continuedl': True, 'merge_output_format': 'mkv/mp4',
                           'concurrent_fragment_downloads': 3}
                if quality.startswith('Audio'):
                    options['postprocessors'] = [{'key': 'FFmpegExtractAudio',
                                                   'preferredcodec': 'mp3',
                                                   'preferredquality': '192'}]
                try:
                    with yt_dlp.YoutubeDL(options) as ydl:
                        downloaded_info = ydl.extract_info(video.url, download=True)
                    if not downloaded_info or downloaded_info.get('_type') == 'playlist':
                        raise RuntimeError('Expected a single video result')
                    if self.stop_flag.is_set():
                        raise UserCancelled('Stopped')
                    new_files = app_files_created(folder, before)
                    video.saved_files.extend(p for p in new_files if p not in video.saved_files)
                    video.completed_media = next((p for p in new_files if p.suffix.lower() in
                                                   {'.mp4', '.mkv', '.webm', '.mov', '.m4v',
                                                    '.mp3', '.m4a', '.opus', '.flac', '.ogg'}), None)
                    if video.completed_media is not None:
                        video.actual_size = video.completed_media.stat().st_size
                        self._send('actual_size', video.key, video.actual_size)
                    # Use the FINAL downloaded video's own thumbnail, not the
                    # playlist cover image. Save a matching jpg beside the file.
                    thumbnail_url = downloaded_info.get('thumbnail') or video.thumbnail
                    if downloaded_info.get('title'):
                        video.title = downloaded_info['title']
                        self._send('correct_title', video.key, video.title)
                    if thumbnail_url:
                        video.thumbnail = thumbnail_url
                        self.thumbs.submit(self._load_thumbnail, video.key, thumbnail_url)
                        if video.completed_media and Image is not None:
                            try:
                                cover = video.completed_media.with_suffix('.jpg')
                                if not cover.exists():
                                    picture = self._fetch_image(thumbnail_url)
                                    picture.save(cover, 'JPEG', quality=90)
                                    video.saved_files.append(cover)
                            except Exception as exc:
                                self._send('log', f'Could not save thumbnail for {video.title}: {exc}')
                    completed += 1
                    self._send('video_status', video.key, 'Complete')
                    self._send('log', f'Completed: {video.title} — {folder}')
                except UserCancelled:
                    video.saved_files.extend(p for p in app_files_created(folder, before)
                                             if p not in video.saved_files)
                    self._send('video_status', video.key, 'Stopped')
                    break
                except Exception as exc:
                    video.saved_files.extend(p for p in app_files_created(folder, before)
                                             if p not in video.saved_files)
                    failed += 1
                    self._send('video_status', video.key, 'Failed')
                    self._send('log', f'FAILED: {video.title}: {exc}')
                self._send('progress', (ix + 1) / count * 100)
            self._send('done', 'download', completed, failed, self.stop_flag.is_set())
        except BaseException as exc:
            self._send('fatal', 'Download', str(exc))

    def _video_status(self, key, status):
        video = self.by_key.get(key)
        if video is None:
            return
        video.status = status
        widgets = self.row_widgets.get(key)
        if widgets:
            fg = ('#65d4af' if status == 'Complete' else
                  '#f29aa2' if status == 'Failed' else
                  '#6cb7ff' if status.startswith(('Downloading', 'Processing')) else COLOR_MUTED)
            widgets['status'].configure(text=status, fg=fg)
            # Highlight the active video row for an unmistakable in-app indicator.
            active = status.startswith(('Downloading', 'Processing'))
            bg = '#29496c' if active else (COLOR_PANEL if (self.videos.index(video) % 2 == 0)
                                          else '#1d2c3d')
            widgets['row'].configure(bg=bg)
            for field in ('check_widget', 'number', 'photo', 'name', 'duration', 'size',
                          'site', 'folder', 'status'):
                widgets[field].configure(bg=bg)
            widgets['check_widget'].configure(activebackground=bg)

    def cancel(self):
        if self.worker_running:
            self.stop_flag.set()
            self.status_var.set('Stopping after current network operation…')
            if self.taskbar:
                self.taskbar.state(self.taskbar.PAUSED)

    def _remove_files(self, videos):
        deleted = 0
        for video in videos:
            root = video.download_root or Path(self.save_var.get().strip()).expanduser()
            still_present = []
            for f in video.saved_files:
                if not f.exists():
                    continue  # Already deleted by the user, no need to report an error.
                if safe_delete_tracked(f, root):
                    deleted += 1
                else:
                    still_present.append(f)
                    self._append_log(f'NOT DELETED (unsafe or locked file): {f}')
            video.saved_files = still_present
        return deleted

    def clear_completed(self):
        if self.worker_running:
            messagebox.showwarning('Busy', 'Stop the active job first.')
            return
        completed = [v for v in (self.videos + self.archive_videos)
                     if v.status == 'Complete']
        if not completed:
            messagebox.showinfo('Nothing to clear', 'No successfully downloaded items are listed.')
            return
        files = sum(len(v.saved_files) for v in completed)
        if not messagebox.askyesno('Clear completed downloads',
                f'Permanently delete {files} app-tracked file(s) from the disk and remove '
                f'{len(completed)} completed row(s)?\n\nFailed, stopped and pending '
                'items will be kept. Pre-existing files are not deleted.'):
            return
        removed = self._remove_files(completed)
        cleared = [v for v in completed if not v.saved_files]
        for v in cleared:
            if v.key in self.by_key and self.by_key[v.key] is v:
                self._remove_video(v)
        cleared_ids = {id(v) for v in cleared}
        self.archive_videos = [v for v in self.archive_videos if id(v) not in cleared_ids]
        self.status_var.set(f'Cleared {len(cleared)} completed item(s); deleted {removed} file(s).'
                            + (' Some files could not be deleted; see Activity log.'
                               if len(cleared) != len(completed) else ''))
        self._set_buttons()

    def clear_all(self):
        if self.worker_running:
            messagebox.showwarning('Busy', 'Stop the active job first.')
            return
        files = sum(len(v.saved_files) for v in self.videos + self.archive_videos)
        if not messagebox.askyesno('Clear ALL downloads and links',
                f'Permanently delete {files} app-tracked file(s), remove all '
                f'{len(self.videos)} video row(s), and remove all {len(self.queued_urls)} queued link(s)?'
                '\n\nOnly files created by VideoHarbor during this session will be deleted. '
                'This cannot be undone.'):
            return
        deleted = self._remove_files(self.videos + self.archive_videos)
        leftovers = [v for v in self.videos + self.archive_videos if v.saved_files]
        current_ids = {id(v) for v in self.videos}
        self.archive_videos = [v for v in leftovers if id(v) not in current_ids]
        for v in list(self.videos):
            if v not in leftovers:
                self._remove_video(v)
            else:
                self._video_status(v.key, 'Delete failed')
        self.queued_urls.clear()
        self.link_box.delete('1.0', 'end')
        self.progress_var.set(0)
        self.queue_var.set('No links in queue')
        self.active_var.set('Idle')
        self.status_var.set(f'Deleted {deleted} tracked file(s).'
                            + (f' {len(leftovers)} item(s) still have files that could not be deleted.'
                               if leftovers else ' All links and rows cleared.'))
        self.title(APP_NAME)
        self._refresh_counts()
        self._set_buttons()
        if self.taskbar:
            self.taskbar.state(self.taskbar.NO_PROGRESS)

    def show_log(self):
        if self.log_window and self.log_window.winfo_exists():
            self.log_window.lift()
            return
        popup = tk.Toplevel(self)
        popup.title('VideoHarbor 1.2 — Activity log')
        popup.geometry('830x470')
        popup.configure(bg=COLOR_BG)
        self.log_window = popup
        panel = ttk.Frame(popup, padding=12)
        panel.pack(fill='both', expand=True)
        text = tk.Text(panel, bg=COLOR_PANEL, fg=COLOR_TEXT,
                       insertbackground=COLOR_TEXT, font=('Consolas', 10),
                       padx=12, pady=12, bd=0)
        text.pack(fill='both', expand=True)
        text.insert('end', '\n'.join(self.log_lines))
        text.see('end')
        text.configure(state='disabled')
        self.log_box = text
        def on_close():
            self.log_window = None
            self.log_box = None
            popup.destroy()
        popup.protocol('WM_DELETE_WINDOW', on_close)

    def _append_log(self, text):
        self.log_lines.append(str(text))
        self.log_lines = self.log_lines[-500:]
        if self.log_box and self.log_box.winfo_exists():
            self.log_box.configure(state='normal')
            self.log_box.insert('end', str(text) + '\n')
            self.log_box.see('end')
            self.log_box.configure(state='disabled')

    def _poll(self):
        try:
            for _ in range(250):
                try:
                    event, *args = self.events.get_nowait()
                except queue.Empty:
                    break
                if event == 'item':
                    self._add_video(args[0])
                elif event == 'thumbnail':
                    key, picture = args
                    if key in self.row_widgets and ImageTk is not None:
                        imgtk = ImageTk.PhotoImage(picture)
                        self.images[key] = imgtk
                        self.row_widgets[key]['photo'].configure(image=imgtk)
                elif event == 'correct_title':
                    key, actual_title = args
                    if key in self.row_widgets:
                        self.row_widgets[key]['name'].configure(text=actual_title)
                elif event == 'actual_size':
                    key, actual = args
                    if key in self.row_widgets:
                        self.row_widgets[key]['size'].configure(text=readable_size(actual))
                elif event == 'video_status':
                    self._video_status(*args)
                elif event == 'log':
                    self._append_log(args[0])
                elif event == 'status':
                    self.status_var.set(args[0])
                elif event == 'active':
                    self.active_var.set(args[0])
                elif event == 'title':
                    self.title(f'{APP_NAME} — {args[0]}')
                elif event == 'progress':
                    self.progress_var.set(args[0])
                    if self.taskbar and self.operation == 'download':
                        self.taskbar.value(args[0])
                elif event in ('done', 'fatal'):
                    self.progress.stop()
                    self.progress.configure(mode='determinate')
                    self.title(APP_NAME)
                    if event == 'fatal':
                        operation, problem = args
                        self.status_var.set(f'{operation} failed: {problem}')
                        self._append_log(f'{operation} error: {problem}')
                        self.active_var.set(f'❌ {operation} error')
                        if self.taskbar:
                            self.taskbar.state(self.taskbar.ERROR)
                    else:
                        op, completed, failed, stopped = args
                        if op == 'analysis':
                            self.active_var.set('Search stopped' if stopped else 'Analysis complete')
                            self.status_var.set(f'{completed} videos found. '
                                                + ('Search stopped.' if stopped else 'Select videos and download.'))
                        else:
                            self.active_var.set('Download stopped' if stopped else 'Download complete')
                            self.status_var.set(f'{completed} completed, {failed} failed.'
                                                + (' Stopped early.' if stopped else ''))
                            if not stopped:
                                self.progress_var.set(100)
                        if self.taskbar:
                            self.taskbar.state(self.taskbar.ERROR if failed else self.taskbar.NO_PROGRESS)
                    self.worker_running = False
                    self.operation = ''
                    self._set_buttons()
        finally:
            try:
                self.after(100, self._poll)
            except tk.TclError:
                pass

    def _on_close(self):
        if self.worker_running:
            if not messagebox.askyesno('Quit VideoHarbor',
                    'A job is running. Stop and exit? Partially downloaded files may remain.'):
                return
            self.stop_flag.set()
        if self.taskbar:
            self.taskbar.close()
        self.thumbs.shutdown(wait=False, cancel_futures=True)
        self.destroy()


def main():
    if yt_dlp is None or Image is None:
        missing = 'yt-dlp' if yt_dlp is None else 'Pillow'
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror('Missing dependency', f'Please install {missing} using Setup_Windows.bat.')
        root.destroy()
        return 1
    app = VideoHarborApp()
    app.mainloop()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())