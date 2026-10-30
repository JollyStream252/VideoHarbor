# VideoHarbor — Windows yt-dlp downloader

A lightweight Windows desktop application for downloading authorized videos and playlists with **yt-dlp**. Written in Python/Tkinter; all video processing happens on your computer.

## Install (Windows 10/11)

1. Unzip the archive into a normal folder (for example `Documents\VideoHarbor`).
2. Install Python 3.11+ from [python.org](https://www.python.org/downloads/windows/) if it isn't already installed. Python 3.13 is fine. Be sure the **py launcher** is enabled. Alternatively, run `winget install -e --id Python.Python.3.13` in Windows Terminal and reopen Terminal.
3. Double-click **`Setup_Windows.bat`**. It creates a `.venv` environment and installs yt-dlp + Pillow. It also attempts to install FFmpeg and Deno using Winget, if they aren't already available. Internet access is needed to install packages and analyze/download URLs. Installer agreements might appear.
4. Double-click **`Launch_Windows.bat`**. The app opens without a terminal window.

If FFmpeg or Deno was newly installed, **reboot or sign out/in if necessary** for Windows to refresh PATH. Restart the application afterward. `ffmpeg -version` and `deno --version` should work in a fresh PowerShell window.

To optionally build a **Windows EXE** on your own PC, run **`Build_Exe_Windows.bat`** after setup. The build generates `dist\VideoHarbor.exe`; it cannot include FFmpeg or Deno automatically, so keep those installed separately.

## How to use

1. Paste one or more full video **or playlist** URLs into the input box, one per line (space-separated links also work).
2. Click **Analyze links**. Video metadata will be listed with thumbnail, title, duration, estimated file size (if available), website, intended subfolder, and status. Very large playlists may take time because each entry is inspected.
3. Choose **Quality** (best available, maximum resolution, or MP3 audio-only).
4. Choose a save folder using **Browse**.
5. Choose how individual-video folders are named: **Website name** (default, e.g. `YouTube`) or **Video page title**. A playlist is *always* stored in a folder named for its playlist title; if the playlist title isn't provided by the site, the folder uses its website name.
6. Click **Download all**, or select table rows with Ctrl/Shift and click **Download selected**.
7. Use **Open download folder** to view the results.

### Folder examples

```
Videos\VideoHarbor\
  YouTube\                          <- individual videos, default mode
    Video Name [id].mp4
  Best Travel Videos\               <- playlist name
    001 - First Video [id].mp4
    002 - Second Video [id].mp4
  Vimeo\                            <- fallback for untitled playlist
    001 - Video Name [id].mp4
```

Video extensions depend on which streams the site provides and whether merging is necessary; merged videos may use `.mkv` or `.mp4`.

### Details and limitations

- **Free and open source:** No login, ads, subscription, server, or tracking introduced by VideoHarbor.
- yt-dlp supports many sites but **not every website**. Sites change; protected/DRM streams, private/paid content, restricted geographic content, or unavailable content might not work.
- Videos must be accessible through the link and you must have the right to download them. No DRM or payment bypass is included.
- File sizes are **estimates** when streams don't publish exact sizes, and merging/MP3 conversion changes final size. “Unknown” means no reliable estimate is available.
- Some sites omit thumbnails or duration. In these cases VideoHarbor displays a placeholder thumbnail or “Unknown”.
- Maximum resolution is a **cap**, not an upscaler. If the chosen cap isn't available, yt-dlp can fall back to the best available format. Audio-only uses FFmpeg to convert to MP3.
- Playlist analysis may take time, especially for hundreds of entries; click **Stop** to request cancellation. Stop is best effort while a site/network call or postprocessing is in progress.
- Thumbnails are fetched from the site's image URLs. The app does not send them to any third-party analytics.
- Deno is recommended for recent YouTube extractor support. FFmpeg is required for MP3 conversion and many high-quality merges.
- **Windows security:** Keep the project files in a writable folder. Antivirus may warn when building your own unsigned EXE; don't disable your security protections.

### Update yt-dlp later

Open PowerShell in the extracted folder and run:

```powershell
.\.venv\Scripts\python.exe -m pip install -U "yt-dlp[default]"
```

### Troubleshooting

- **Missing yt-dlp/Pillow**: rerun `Setup_Windows.bat`.
- **FFmpeg not found**: run `winget install -e --id Gyan.FFmpeg` and relaunch app after PATH updates.
- **YouTube extraction problem**: run `winget install -e --id DenoLand.Deno`, update yt-dlp, and restart the app.
- **Some entries show Unknown size**: their site didn't report a reliable size before download.
- **A download fails**: see the Activity log. Try a single video URL first, update yt-dlp, and confirm you can access the URL in your browser.
- **Permission error**: choose a save folder inside your Windows user profile, such as `Videos`.
- **Python installed but setup can't see it**: restart the computer, check `py -3 --version` in PowerShell, and rerun setup.

## Official upstream projects

- [yt-dlp](https://github.com/yt-dlp/yt-dlp)
- [FFmpeg](https://ffmpeg.org/)
- [Deno](https://deno.com/)
- [Pillow](https://github.com/python-pillow/Pillow)

## License

VideoHarbor source code is MIT licensed (see `LICENSE`). Upstream projects retain their own licenses.
