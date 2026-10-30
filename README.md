> **Windows executable available:** [Download the latest successful VideoHarbor 1.2 Windows build](https://github.com/JollyStream252/VideoHarbor/actions/workflows/build-windows.yml) from the Artifacts section of a successful workflow run. Extract the artifact ZIP and open `VideoHarbor.exe`. The app build is unsigned and may require Windows SmartScreen confirmation. FFmpeg and Deno may need separate installation for some sites. Actual website downloads should be verified by users.
>
> **Automated builds:** The Windows GitHub Actions workflow runs the test suite, packages the app with PyInstaller and uploads a Windows executable. If you prefer source installation, use the scripts below or the included `VideoHarbor_GitHub_1.2_Source.zip`.

# VideoHarbor 1.2 — Windows desktop downloader

VideoHarbor is a **free, local Windows desktop app** built using Python/Tkinter, Pillow and yt-dlp. It downloads videos and playlists from yt-dlp-compatible websites. Its interface displays each video individually so you can inspect and select before downloading. No server account, advertising, or subscription is added by VideoHarbor.

## What changed in 1.2

- **Select checkboxes** to the **left of every thumbnail**, plus `Select all` and `Select none`.
- Playlist entries are numbered in the form `1/10`, `2/10` ... `10/10`. A playlist with no available total still shows the entry number.
- **Searching indicator:** an animated progress bar within the app, an *indeterminate* progress indication on the Windows taskbar, and live text in the window title and status area.
- **Download indicator:** live percentage and active-row highlight, overall progress bar, current video title, and real Windows **taskbar progress** via ITaskbarList3.
- **Clear:** after confirmation, permanently deletes files **created and tracked by VideoHarbor** that belong to *successfully downloaded* entries, and removes the completed rows. Failed and stopped items are retained.
- **Clear All:** after confirmation, permanently deletes **all app-tracked files from the current session**, clears all video rows and queued links, and resets the interface. This can include incomplete/partial downloads. Unrelated/pre-existing files are not deleted.
- **Auto-clearing input:** `Paste + add` or Ctrl+V inside the link box moves new URLs into a link queue, **clearing the input box**. `Add links` or Enter supports typed links. Duplicate URLs are ignored.
- The preview thumbnail is taken from **each individual video**. After download, the thumbnail is refreshed from the metadata of the **actual downloaded item**. When a thumbnail is available and the app created a new media file, it also saves a matching `.jpg` cover image alongside the media file. Windows File Explorer may still use its own video-frame thumbnails instead of this JPG.
- A custom **VideoHarbor.ico** is included and used for the running window and Windows executable.
- **Windows Start Menu and Desktop shortcuts** can be created with `Create_Shortcuts_Windows.bat` after building the exe. You can then pin the running app to the taskbar.

## Install on Windows 10 or 11

1. Extract the ZIP archive into a permanent folder, for example `Documents\VideoHarbor`.
2. Install Python 3.11 or newer from <https://www.python.org/downloads/windows/>. Make sure the `py` launcher is installed. (Alternatively, `winget install -e --id Python.Python.3.13`.)
3. Run **`Setup_Windows.bat`**. This makes a local `.venv`, installs yt-dlp + Pillow and attempts to install FFmpeg (merging/conversions) and Deno (YouTube compatibility) using Windows Package Manager (Winget).
4. If FFmpeg or Deno is freshly installed, sign out/in or restart your PC if necessary so PATH updates.
5. Double-click **`Launch_Windows.bat`** to open the app without a console window. It appears in the Windows taskbar while running.

### Create a standalone `.exe` with its own icon

To build a native-looking Windows application *on your Windows PC*, run **`Build_Exe_Windows.bat`** after setup. It installs PyInstaller and produces:

`dist\VideoHarbor.exe`

The EXE contains the app, Python dependencies and its icon. **FFmpeg and Deno may still need to be installed separately.** Run **`Create_Shortcuts_Windows.bat`** to add Start Menu and Desktop shortcuts. Start VideoHarbor, then **right-click its taskbar icon → Pin to taskbar** to keep it there. The app itself cannot force Windows taskbar pinning.

**Important:** The included archive is a Python source project and Windows build kit, **not an already-compiled Windows EXE**. Building a Windows exe is a Windows-only step. The UI and logic were tested with simulated media, but the executable build and real Windows taskbar integration require testing on Windows.

## How to use VideoHarbor

1. Copy one or more video / playlist URLs from a supported website.
2. Paste them using **Paste + add**, or Ctrl+V within the link input. The URLs go into a queue and the input clears automatically. You can paste multiple lines. You can type URLs and use **Add links**.
3. Press **Analyze**. Each video appears as a separate row, with checkbox, playlist position, thumbnail, title, duration, estimated file size, website, destination folder, and status.
4. Check the videos you want (use Select all / Select none as needed).
5. Choose **Quality** (Best, 2160p, 1440p, 1080p, 720p, 480p, 360p, or MP3 audio) and **Save to** destination.
6. Press **Download checked** or **Download all**.
7. Watch searching/downloading status within the app and on the taskbar. The active video row is highlighted.
8. Use **Open folder** to find the downloaded files.

### Folder naming

- **Playlists:** always use the playlist name, e.g. `Videos\VideoHarbor\My Nature Playlist\001 - Video [id].mp4`.
- **Untitled playlists:** fall back to the webpage/website name, e.g. `Videos\VideoHarbor\YouTube\001 - Video [id].mp4`.
- **Single videos (default):** use the website name, e.g. `Videos\VideoHarbor\Vimeo\My Video [id].mp4`.
- **Single videos (optional):** change `Individual folder` to `Video page title` to use the video's title as the subfolder.

### Clear vs Clear All: IMPORTANT

Both controls **permanently delete files**, and both show a confirmation dialog. **Clear** deletes successfully downloaded files tracked in the current session (including earlier analyses in this same open session), removes completed rows and retains unsuccessful entries. **Clear All** deletes all files tracked during the current session, including failed/partial outputs, and removes every queued link and row. The app **does not scan or delete unrelated files, existing downloads, or whole destination directories**. It only deletes known file paths inside the download root used for each video. Deletion is not reversible using the app. If you close and reopen the program, prior session downloads are not tracked for deletion.

### Accuracy and limitations

- File size before download is a **best-effort estimate** and may be `Unknown`; actual saved file size is shown afterward when a new media file is found.
- The thumbnail is specific to the individual video's source metadata, not the playlist poster. A sidecar JPG is saved when an accessible video thumbnail is available. File Explorer may display a separately generated frame thumbnail for video files.
- Not every website can be downloaded by yt-dlp. No DRM, payment or access restrictions are bypassed. Some links may require authentication, may not expose all details, or may break as sites change.
- You need permission to download the content.
- Windows search taskbar progress is *indeterminate*; the download taskbar progress is based on available transfer totals and is approximate during post-processing.
- The **Stop** button requests cancellation; a network operation may take some time to respond. A partial file may remain.
- Failed or partial downloads stay listed unless you use Clear All.
- The app does not automatically embed cover images into the video file; thumbnail JPGs are separate. Some formats/players generate their own previews.

## Updates

To update yt-dlp later, open PowerShell in the extracted folder:

```powershell
.\.venv\Scripts\python.exe -m pip install -U "yt-dlp[default]"
```

Then rebuild the exe if you use the standalone executable.

## Tests

From the project folder, with Python installed:

```powershell
.\.venv\Scripts\python.exe -m unittest -v test_video_harbor.py
```

Simulated tests cover playlist counters, folder naming, format handling, duplicate link inputs, safe file deletion, and the download worker. Actual downloads from live sites, Windows-specific COM taskbar behavior and the Windows PyInstaller build should be verified locally.

## Official upstream projects

- yt-dlp: <https://github.com/yt-dlp/yt-dlp>
- FFmpeg: <https://ffmpeg.org/>
- Deno: <https://deno.com/>
- Pillow: <https://python-pillow.github.io/>
- PyInstaller: <https://pyinstaller.org/>

VideoHarbor code is MIT-licensed, see `LICENSE`. Upstream tools retain their own licenses.

## GitHub source repository

This project can be stored in a GitHub repository with the files in this folder at repository root. `.gitignore` prevents generated videos, caches, and builds from being committed accidentally. `requirements.txt` lists the core Python dependencies.

The optional GitHub Actions workflow at `.github/workflows/windows-build.yml` runs tests and builds a Windows EXE on pushes to `main` that touch code/build files, or when manually dispatched from the Actions tab. The compiled EXE is an Actions artifact, **not** automatically published as a release. Windows build and taskbar integration still require testing on an actual Windows machine.
