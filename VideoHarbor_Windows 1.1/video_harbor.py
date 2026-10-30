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

APP_NAME = 'VideoHarbor'
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
    # Fall back to the best available format only if the chosen cap is unavailable.
    return (f'bv[height<={h}]+ba/b[height<={h}]/'
            f'bv*[height<={h}]+ba/bv+ba/b')


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
        for i, entry in enumerate(info['entries'], 1):
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
                        entry.get('thumbnail'))
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


class VideoHarborApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('VideoHarbor — yt-dlp Desktop Downloader')
        self.geometry('1190x730')
        self.minsize(950, 710)
        self.configure(bg=COLOR_BG)
        self.events = queue.Queue()
        self.stop_flag = threading.Event()
        self.worker_running = False
        self.log_lines: list[str] = []
        self.log_box = None
        self.log_window = None
        self.videos: list[Video] = []
        self.by_key: dict[str, Video] = {}
        self.images: dict[str, Any] = {}
        self.image_jobs = ThreadPoolExecutor(max_workers=4, thread_name_prefix='thumb')
        self.save_var = tk.StringVar(value=str(Path.home() / 'Videos' / 'VideoHarbor'))
        self.quality_var = tk.StringVar(value='Best available')
        self.folder_mode_var = tk.StringVar(value='Website name')
        self.status_var = tk.StringVar(value='Paste video or playlist links, then click Analyze.')
        self.count_var = tk.StringVar(value='No videos analyzed')
        self.progress_var = tk.DoubleVar(value=0)
        self._make_style()
        self._build_ui()
        self.quality_var.trace_add('write', self._update_size_rows)
        self.folder_mode_var.trace_add('write', self._update_folder_rows)
        self.after(100, self._poll)
        self.protocol('WM_DELETE_WINDOW', self._on_close)

    def _make_style(self):
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('.', background=COLOR_BG, foreground=COLOR_TEXT,
                        font=('Segoe UI', 10))
        style.configure('TFrame', background=COLOR_BG)
        style.configure('Panel.TFrame', background=COLOR_PANEL)
        style.configure('TLabel', background=COLOR_BG, foreground=COLOR_TEXT)
        style.configure('Muted.TLabel', background=COLOR_BG, foreground=COLOR_MUTED)
        style.configure('Heading.TLabel', font=('Segoe UI Semibold', 20),
                        background=COLOR_BG, foreground=COLOR_TEXT)
        style.configure('Panel.TLabel', background=COLOR_PANEL, foreground=COLOR_TEXT)
        style.configure('PanelMuted.TLabel', background=COLOR_PANEL, foreground=COLOR_MUTED)
        style.configure('TButton', padding=(12, 9), background=COLOR_PANEL2,
                        foreground=COLOR_TEXT, borderwidth=0)
        style.map('TButton', background=[('active', '#32445d'), ('disabled', '#253044')],
                  foreground=[('disabled', '#7c8a9e')])
        style.configure('Accent.TButton', background=COLOR_ACCENT,
                        foreground=COLOR_BG, font=('Segoe UI Semibold', 10))
        style.map('Accent.TButton', background=[('active', '#6dddc8'),
                                               ('disabled', '#446e70')])
        style.configure('Danger.TButton', background=COLOR_DANGER, foreground='white')
        style.configure('TEntry', fieldbackground=COLOR_PANEL, foreground=COLOR_TEXT,
                        insertcolor=COLOR_TEXT, padding=8)
        style.configure('TCombobox', fieldbackground=COLOR_PANEL, background=COLOR_PANEL,
                        foreground=COLOR_TEXT, arrowcolor=COLOR_TEXT, padding=8)
        style.map('TCombobox', fieldbackground=[('readonly', COLOR_PANEL)],
                  foreground=[('readonly', COLOR_TEXT)])
        style.configure('Treeview', background=COLOR_PANEL, foreground=COLOR_TEXT,
                        fieldbackground=COLOR_PANEL, rowheight=64, borderwidth=0)
        style.map('Treeview', background=[('selected', '#314a61')],
                  foreground=[('selected', 'white')])
        style.configure('Treeview.Heading', background=COLOR_PANEL2,
                        foreground=COLOR_TEXT, relief='flat', padding=8)
        style.configure('Horizontal.TProgressbar', troughcolor=COLOR_PANEL2,
                        background=COLOR_ACCENT, thickness=12)

    def _build_ui(self):
        wrap = ttk.Frame(self, padding=20)
        wrap.pack(fill='both', expand=True)
        title = ttk.Frame(wrap)
        title.pack(fill='x', pady=(0, 14))
        ttk.Label(title, text='VideoHarbor', style='Heading.TLabel').pack(side='left')
        ttk.Label(title, text='Powered by yt-dlp  •  Local desktop app',
                  style='Muted.TLabel').pack(side='left', padx=18, pady=11)

        ttk.Label(wrap, text='Video or playlist links (paste one or more URLs)',
                  font=('Segoe UI Semibold', 11)).pack(anchor='w')
        inputrow = ttk.Frame(wrap)
        inputrow.pack(fill='x', pady=(7, 12))
        self.link_box = tk.Text(inputrow, height=3, wrap='word',
                                bg=COLOR_PANEL, fg=COLOR_TEXT,
                                insertbackground=COLOR_TEXT,
                                selectbackground='#36506f', bd=0,
                                relief='flat', padx=12, pady=10,
                                font=('Consolas', 10))
        self.link_box.pack(side='left', expand=True, fill='both', padx=(0, 10))
        linkbuttons = ttk.Frame(inputrow)
        linkbuttons.pack(side='right', fill='y')
        self.analyze_btn = ttk.Button(linkbuttons, text='Analyze links',
                                      style='Accent.TButton', command=self.analyze)
        self.analyze_btn.pack(fill='x', pady=(0, 6))
        smallbuttons = ttk.Frame(linkbuttons)
        smallbuttons.pack(fill='x')
        ttk.Button(smallbuttons, text='Paste', command=self.paste).pack(side='left')
        ttk.Button(smallbuttons, text='Clear', command=self.clear).pack(side='left', padx=(4, 0))

        settings = ttk.Frame(wrap)
        settings.pack(fill='x', pady=(2, 12))
        ttk.Label(settings, text='Save downloads to:').grid(row=0, column=0, sticky='w', pady=(0, 4))
        ttk.Label(settings, text='Individual video folders:').grid(
            row=0, column=2, sticky='w', padx=(14, 0), pady=(0, 4))
        ttk.Label(settings, text='Quality:').grid(
            row=0, column=3, sticky='w', padx=(14, 0), pady=(0, 4))
        ttk.Entry(settings, textvariable=self.save_var).grid(row=1, column=0, sticky='ew', padx=(0, 8))
        ttk.Button(settings, text='Browse…', command=self.browse).grid(row=1, column=1)
        self.quality_combo = ttk.Combobox(settings, textvariable=self.quality_var,
                                          values=QUALITY_CHOICES, state='readonly', width=23)
        self.quality_combo.grid(row=1, column=3, padx=(14, 0), sticky='ew')
        ttk.Combobox(settings, textvariable=self.folder_mode_var,
                     values=('Website name', 'Video page title'),
                     state='readonly', width=20).grid(row=1, column=2, padx=(14, 0), sticky='ew')
        settings.columnconfigure(0, weight=1)

        middle_head = ttk.Frame(wrap)
        middle_head.pack(fill='x', pady=(0, 6))
        ttk.Label(middle_head, text='Analyzed videos',
                  font=('Segoe UI Semibold', 12)).pack(side='left')
        ttk.Label(middle_head, textvariable=self.count_var,
                  style='Muted.TLabel').pack(side='right')

        tableframe = ttk.Frame(wrap)
        tableframe.pack(fill='both', expand=True)
        cols = ('name', 'duration', 'size', 'site', 'folder', 'status')
        self.table = ttk.Treeview(tableframe, columns=cols, show='tree headings',
                                  selectmode='extended', height=3)
        self.table.heading('#0', text='Thumbnail')
        self.table.column('#0', width=112, minwidth=100, stretch=False, anchor='center')
        specs = [('name', 'Video name', 270), ('duration', 'Duration', 75),
                 ('size', 'File size*', 102), ('site', 'Website', 95),
                 ('folder', 'Save subfolder', 145), ('status', 'Status', 110)]
        for name, label, width in specs:
            self.table.heading(name, text=label)
            self.table.column(name, width=width, minwidth=75,
                              stretch=(name in ('name', 'folder')),
                              anchor=('w' if name in ('name', 'folder') else 'center'))
        sb = ttk.Scrollbar(tableframe, orient='vertical', command=self.table.yview)
        self.table.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        self.table.pack(side='left', fill='both', expand=True)
        self._default_thumb = self._blank_thumbnail()

        actions = ttk.Frame(wrap)
        actions.pack(fill='x', pady=(12, 8))
        self.download_all_btn = ttk.Button(actions, text='Download all',
                                            style='Accent.TButton', command=self.download_all,
                                            state='disabled')
        self.download_all_btn.pack(side='left', padx=(0, 8))
        self.download_selected_btn = ttk.Button(actions, text='Download selected',
                                                 command=self.download_selected,
                                                 state='disabled')
        self.download_selected_btn.pack(side='left', padx=(0, 8))
        self.cancel_btn = ttk.Button(actions, text='Stop', style='Danger.TButton',
                                     command=self.cancel, state='disabled')
        self.cancel_btn.pack(side='left')
        ttk.Button(actions, text='Open download folder',
                   command=self.open_folder).pack(side='right')
        ttk.Button(actions, text='Activity log',
                   command=self.show_log).pack(side='right', padx=(0, 8))

        ttk.Progressbar(wrap, mode='determinate', variable=self.progress_var,
                        maximum=100).pack(fill='x', pady=(3, 6))
        ttk.Label(wrap, textvariable=self.status_var,
                  style='Muted.TLabel').pack(fill='x')
        ttk.Label(wrap, text='* File size may be estimated or unavailable before download. '\
                  'Use only content you have permission to save.',
                  style='Muted.TLabel', wraplength=1050).pack(fill='x', pady=(4, 0))

    def _blank_thumbnail(self):
        if Image is None:
            return None
        im = Image.new('RGB', (100, 56), '#294056')
        draw = ImageDraw.Draw(im)
        draw.polygon([(41, 16), (41, 42), (66, 29)], fill='#a6d8d0')
        return ImageTk.PhotoImage(im)

    def paste(self):
        try:
            content = self.clipboard_get()
            self.link_box.insert('insert', content)
        except tk.TclError:
            pass

    def clear(self):
        if self.worker_running:
            messagebox.showinfo('Busy', 'Stop the current operation before clearing.')
            return
        self.link_box.delete('1.0', 'end')
        self.videos.clear()
        self.by_key.clear()
        self.images.clear()
        self.table.delete(*self.table.get_children())
        self.count_var.set('No videos analyzed')
        self.progress_var.set(0)
        self.status_var.set('Paste video or playlist links, then click Analyze.')
        self._set_buttons()

    def browse(self):
        location = filedialog.askdirectory(title='Choose download folder',
                                          initialdir=self.save_var.get())
        if location:
            self.save_var.set(location)

    def open_folder(self):
        path = Path(self.save_var.get()).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        if sys.platform == 'win32':
            os.startfile(str(path))
        elif sys.platform == 'darwin':
            import subprocess
            subprocess.Popen(['open', str(path)])
        else:
            import subprocess
            subprocess.Popen(['xdg-open', str(path)])

    def _send(self, event: str, *args):
        self.events.put((event, *args))

    def show_log(self):
        if self.log_window is not None and self.log_window.winfo_exists():
            self.log_window.lift()
            return
        popup = tk.Toplevel(self)
        popup.title('VideoHarbor — Activity log')
        popup.geometry('820x440')
        popup.configure(bg=COLOR_BG)
        self.log_window = popup
        frame = ttk.Frame(popup, padding=12)
        frame.pack(fill='both', expand=True)
        self.log_box = tk.Text(frame, bg=COLOR_PANEL, fg=COLOR_TEXT,
                               font=('Consolas', 10), insertbackground=COLOR_TEXT,
                               bd=0, padx=12, pady=12, wrap='word', state='normal')
        sb = ttk.Scrollbar(frame, orient='vertical', command=self.log_box.yview)
        self.log_box.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        self.log_box.pack(fill='both', expand=True)
        self.log_box.insert('end', '\n'.join(self.log_lines))
        self.log_box.see('end')
        self.log_box.configure(state='disabled')

        def close():
            self.log_box = None
            self.log_window = None
            popup.destroy()
        popup.protocol('WM_DELETE_WINDOW', close)

    def _append_log(self, message):
        self.log_lines.append(str(message))
        if len(self.log_lines) > 500:
            self.log_lines = self.log_lines[-500:]
        if self.log_box is not None and self.log_box.winfo_exists():
            self.log_box.configure(state='normal')
            self.log_box.insert('end', str(message) + '\n')
            self.log_box.see('end')
            self.log_box.configure(state='disabled')

    def _set_buttons(self):
        busy = self.worker_running
        self.analyze_btn.configure(state='disabled' if busy else 'normal')
        self.download_all_btn.configure(state='normal' if self.videos and not busy else 'disabled')
        self.download_selected_btn.configure(state='normal' if self.videos and not busy else 'disabled')
        self.cancel_btn.configure(state='normal' if busy else 'disabled')

    def _start(self, job, *args):
        if self.worker_running:
            return
        self.worker_running = True
        self.stop_flag.clear()
        self._set_buttons()
        threading.Thread(target=job, args=args, daemon=True).start()

    def analyze(self):
        if yt_dlp is None:
            messagebox.showerror('yt-dlp is missing',
                                 'Run Setup_Windows.bat first to install yt-dlp.')
            return
        urls = clean_urls(self.link_box.get('1.0', 'end'))
        if not urls:
            messagebox.showwarning('No links found', 'Paste one or more full https:// URLs.')
            return
        self.videos.clear()
        self.by_key.clear()
        self.images.clear()
        self.table.delete(*self.table.get_children())
        self.progress_var.set(0)
        self.count_var.set('Analyzing…')
        self.status_var.set(f'Analyzing {len(urls)} link(s). Larger playlists may take time…')
        self._start(self._analyze_worker, urls)

    def _analyze_worker(self, urls):
        logger = GuiLogger(self._send)
        total = 0
        try:
            opts = {'quiet': True, 'no_warnings': False, 'logger': logger,
                    'skip_download': True, 'ignoreerrors': True, 'extract_flat': False,
                    'noplaylist': False, 'socket_timeout': 22, 'retries': 3}
            with yt_dlp.YoutubeDL(opts) as ydl:
                for n, url in enumerate(urls, 1):
                    if self.stop_flag.is_set():
                        break
                    self._send('status', f'Analyzing link {n}/{len(urls)}: {url[:92]}')
                    try:
                        info = ydl.extract_info(url, download=False)
                        if not info:
                            self._send('log', f'No accessible videos returned for {url}')
                            continue
                        for video in videos_from_info(info, url):
                            if self.stop_flag.is_set():
                                break
                            # The API normally provides full playlist metadata. For
                            # flat-only results, resolve an entry to show duration/size.
                            if not video.formats and video.url.startswith('http'):
                                try:
                                    detail = ydl.extract_info(video.url, download=False)
                                    if detail and isinstance(detail, dict) and 'entries' not in detail:
                                        video.title = detail.get('title') or video.title
                                        video.duration = detail.get('duration') or video.duration
                                        video.formats = compact_formats(detail)
                                        video.fallback_size = (detail.get('filesize') or
                                                               detail.get('filesize_approx') or
                                                               video.fallback_size)
                                        video.thumbnail = detail.get('thumbnail') or video.thumbnail
                                except Exception as exc:
                                    self._send('log', f'Metadata limited for {video.title}: {exc}')
                            total += 1
                            video.key = f'v{total}'
                            self._send('item', video)
                            if video.thumbnail:
                                try:
                                    self.image_jobs.submit(self._load_thumbnail, video.key, video.thumbnail)
                                except RuntimeError:
                                    pass
                    except Exception as exc:
                        self._send('log', f'Failed to analyze {url}: {exc}')
            self._send('done', 'analysis', total, self.stop_flag.is_set())
        except BaseException as exc:
            self._send('fatal', 'Analysis', f'{exc}')

    def _load_thumbnail(self, key, url):
        if Image is None or self.stop_flag.is_set():
            return
        try:
            request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(request, timeout=8) as resp:
                raw = resp.read(2_000_000)
            image = Image.open(io.BytesIO(raw))
            image = ImageOps.exif_transpose(image).convert('RGB')
            image.thumbnail((100, 56))
            canvas = Image.new('RGB', (100, 56), '#294056')
            canvas.paste(image, ((100 - image.width)//2, (56 - image.height)//2))
            self._send('thumbnail', key, canvas)
        except Exception:
            pass

    def _update_folder_rows(self, *_):
        if not hasattr(self, 'table'):
            return
        for item in self.videos:
            if self.table.exists(item.key):
                folder = item.group if item.playlist else (
                    item.title if self.folder_mode_var.get() == 'Video page title' else item.site)
                self.table.set(item.key, 'folder', safe_name(folder, item.site))

    def _update_size_rows(self, *_):
        if not hasattr(self, 'table'):
            return
        for item in self.videos:
            if self.table.exists(item.key):
                size, approx = estimate_size(item.formats, item.duration,
                                             self.quality_var.get(), item.fallback_size)
                self.table.set(item.key, 'size', readable_size(size, approx))

    def _add_video(self, video):
        self.videos.append(video)
        self.by_key[video.key] = video
        size, approx = estimate_size(video.formats, video.duration,
                                     self.quality_var.get(), video.fallback_size)
        self.table.insert('', 'end', iid=video.key, image=self._default_thumb,
                          values=(video.title, readable_duration(video.duration),
                                  readable_size(size, approx), video.site,
                                  safe_name(video.group if video.playlist else (
                                      video.title if self.folder_mode_var.get() == 'Video page title'
                                      else video.site), video.site), video.status))
        self.count_var.set(f'{len(self.videos)} video(s) found')

    def download_all(self):
        self._download(list(self.videos))

    def download_selected(self):
        chosen = [self.by_key[key] for key in self.table.selection() if key in self.by_key]
        if not chosen:
            messagebox.showinfo('Select videos',
                                'Select one or more rows first (Ctrl+click or Shift+click).')
            return
        self._download(chosen)

    def _download(self, chosen):
        if yt_dlp is None or not chosen:
            return
        base = Path(self.save_var.get().strip()).expanduser()
        if not base.is_absolute():
            messagebox.showwarning('Choose a folder', 'Please enter a full folder path.')
            return
        try:
            base.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror('Invalid download folder', str(exc))
            return
        quality = self.quality_var.get()
        if shutil.which('ffmpeg') is None:
            if quality.startswith('Audio'):
                messagebox.showerror('FFmpeg required',
                                     'FFmpeg is needed to convert audio to MP3. Install it first.')
                return
            if not messagebox.askyesno('FFmpeg not found',
                                       'FFmpeg is recommended to merge high-quality video and audio. '
                                       'Continue without it?'):
                return
        self.progress_var.set(0)
        for video in chosen:
            video.status = 'Queued'
            self.table.set(video.key, 'status', video.status)
        self._start(self._download_worker, chosen, base, quality, self.folder_mode_var.get())

    def _download_worker(self, chosen, base, quality, individual_mode):
        completed = 0
        failed = 0
        count = len(chosen)
        logger = GuiLogger(self._send)
        try:
            for index, video in enumerate(chosen):
                if self.stop_flag.is_set():
                    break
                folder = video.destination(base, individual_mode)
                folder.mkdir(parents=True, exist_ok=True)
                self._send('status', f'Downloading {index + 1}/{count}: {video.title}')
                self._send('video_status', video.key, 'Downloading')
                template = ('%(title).145s [%(id)s].%(ext)s' if not video.playlist else
                            f'{video.index:03d} - %(title).125s [%(id)s].%(ext)s')

                def progress_hook(data, ix=index):
                    if self.stop_flag.is_set():
                        raise UserCancelled('Download stopped by user')
                    if data.get('status') == 'downloading':
                        got = data.get('downloaded_bytes') or 0
                        total = data.get('total_bytes') or data.get('total_bytes_estimate')
                        fraction = min(got / total, 0.99) if total else 0
                        self._send('progress', (ix + fraction) / count * 100)
                    elif data.get('status') == 'finished':
                        self._send('status', f'Processing {video.title}…')

                def pp_hook(data):
                    if self.stop_flag.is_set():
                        raise UserCancelled('Download stopped by user')

                opts = {'format': format_selector(quality), 'paths': {'home': str(folder)},
                        'outtmpl': {'default': template}, 'noplaylist': True,
                        'quiet': True, 'no_warnings': False, 'logger': logger,
                        'progress_hooks': [progress_hook], 'postprocessor_hooks': [pp_hook],
                        'ignoreerrors': False, 'overwrites': False,
                        'socket_timeout': 25, 'retries': 5, 'fragment_retries': 5,
                        'continuedl': True, 'merge_output_format': 'mkv/mp4',
                        'concurrent_fragment_downloads': 3}
                if quality.startswith('Audio'):
                    opts['postprocessors'] = [{'key': 'FFmpegExtractAudio',
                                               'preferredcodec': 'mp3',
                                               'preferredquality': '192'}]
                try:
                    with yt_dlp.YoutubeDL(opts) as ydl:
                        rc = ydl.download([video.url])
                    if rc:
                        raise RuntimeError(f'yt-dlp exited with code {rc}')
                    completed += 1
                    self._send('video_status', video.key, 'Complete')
                    self._send('log', f'Saved: {video.title} → {folder}')
                except UserCancelled:
                    self._send('video_status', video.key, 'Stopped')
                    break
                except Exception as exc:
                    if self.stop_flag.is_set():
                        self._send('video_status', video.key, 'Stopped')
                        break
                    failed += 1
                    self._send('video_status', video.key, 'Failed')
                    self._send('log', f'Download failed: {video.title}: {exc}')
                self._send('progress', (index + 1) / count * 100)
            self._send('done', 'download', completed, failed, self.stop_flag.is_set())
        except BaseException as exc:
            self._send('fatal', 'Download', f'{exc}')

    def cancel(self):
        if not self.worker_running:
            return
        self.stop_flag.set()
        self.status_var.set('Stopping at the next safe checkpoint…')
        self._append_log('Stop requested. A network request may take a few moments to return.')

    def _poll(self):
        try:
            for _ in range(250):
                try:
                    evt = self.events.get_nowait()
                except queue.Empty:
                    break
                event, *args = evt
                if event == 'item':
                    self._add_video(args[0])
                elif event == 'thumbnail':
                    key, pil_img = args
                    if key in self.by_key and ImageTk is not None:
                        image = ImageTk.PhotoImage(pil_img)
                        self.images[key] = image
                        self.table.item(key, image=image)
                elif event == 'log':
                    self._append_log(args[0])
                elif event == 'status':
                    self.status_var.set(args[0])
                elif event == 'progress':
                    self.progress_var.set(args[0])
                elif event == 'video_status':
                    key, status = args
                    if key in self.by_key:
                        self.by_key[key].status = status
                        self.table.set(key, 'status', status)
                elif event == 'done':
                    if args[0] == 'analysis':
                        _, count, stopped = args
                        self.status_var.set(f'{count} video(s) found.' +
                                            (' Analysis stopped.' if stopped else ' Ready to download.'))
                    else:
                        _, completed, failed, stopped = args
                        self.status_var.set(f'Finished: {completed} downloaded, {failed} failed.' +
                                            (' Stopped early.' if stopped else ''))
                        if not stopped:
                            self.progress_var.set(100)
                    self.worker_running = False
                    self._set_buttons()
                elif event == 'fatal':
                    operation, message = args
                    self._append_log(f'{operation} error: {message}')
                    self.status_var.set(f'{operation} failed: {message}')
                    self.worker_running = False
                    self._set_buttons()
        finally:
            try:
                self.after(100, self._poll)
            except tk.TclError:
                pass

    def _on_close(self):
        if self.worker_running:
            if not messagebox.askyesno('Quit VideoHarbor',
                                       'An operation is still in progress. Stop it and exit?'):
                return
            self.stop_flag.set()
        self.image_jobs.shutdown(wait=False, cancel_futures=True)
        self.destroy()


def main():
    if yt_dlp is None or Image is None:
        missing = 'yt-dlp' if yt_dlp is None else 'Pillow'
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror('Missing dependency',
                             f'{missing} is missing. Run Setup_Windows.bat first.')
        root.destroy()
        return 1
    app = VideoHarborApp()
    app.mainloop()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
