# yt-dlp Downloader v7.5

A Windows-oriented Python wrapper around [yt-dlp](https://github.com/yt-dlp/yt-dlp) with a settings-driven workflow and a small GUI for editing those settings.

Copy a YouTube URL, run the script, and it downloads according to a JSON settings file. It can fetch video, audio-only, or **sidecars only** (subtitles, thumbnails, metadata and comments with no media file), and it handles single videos and playlists.

## Contents

- [Requirements](#requirements)
- [Setup](#setup)
- [Usage](#usage)
- [How it works](#how-it-works)
- [Settings reference](#settings-reference)
- [Subtitle rules](#subtitle-rules)
- [Output layout](#output-layout)
- [Logs](#logs)
- [Known limitations](#known-limitations)
- [Repository files](#repository-files)

## Requirements

| Requirement | Needed for | Notes |
|---|---|---|
| **Windows 10/11** | Everything | The downloader reads the clipboard through PowerShell (`Get-Clipboard`), writes output paths with Windows backslashes, and the GUI uses Windows-specific touches (Notepad++/Notepad launcher, dark title bar, Segoe UI/Consolas fonts). It is not set up to run on Linux or macOS. |
| **Python 3.9+** | Everything | The downloader uses only the standard library. 3.9 is the floor because PySide6 requires it. |
| **[yt-dlp](https://github.com/yt-dlp/yt-dlp/releases/latest)** | Everything | A recent build is expected. Either put `yt-dlp` on your `PATH` or point `YT_DLP_EXE` at the executable. Keep it current, since YouTube changes often. The script prints a notice when a newer release exists but never updates anything itself. |
| **[ffmpeg](https://ffmpeg.org/download.html)** (with ffprobe) | Merging video and audio, embedding subtitles, thumbnails and metadata, audio extraction, subtitle/thumbnail conversion | Not called directly, but yt-dlp needs it for most features here. Put it on your `PATH` or next to `yt-dlp.exe`. The script does not check for it. |
| **[PySide6](https://pypi.org/project/PySide6/)** | Settings GUI only | `pip install PySide6`. Not needed to run downloads. |
| Internet access | Downloading | The script also makes one optional call to the GitHub API to check for a newer yt-dlp. If that fails, it stays silent. |
| A clipboard URL | Starting a download | The first line of the clipboard must be a full `http(s)://` URL. |

> Depending on your yt-dlp version, YouTube extraction may also need an external JavaScript runtime (for example Deno). See yt-dlp's own documentation for current requirements.

## Setup

1. Clone or download the repository. **Keep all the `.py` files and `yt_settings7.json` in the same folder.** They load each other by path, and the schema file's name contains a dot, so it cannot be imported the normal way.

   ```
   git clone https://github.com/mMohamedLaid/yt-dlp-downlaoder-v7.5.git
   ```

2. Install yt-dlp and ffmpeg (see the table above).
3. *(Optional, for the GUI)* `pip install PySide6`
4. **Edit `yt_settings7.json` before your first run.** The shipped file contains the author's personal paths and preferences:

   - `DOWNLOAD_DIR` is `D:/Downloads/Video`
   - `YT_DLP_EXE` is `E:/apps/Youtube downloader/yt-dlp.exe`

   Change both to paths that exist on your machine (or set `YT_DLP_EXE` to `yt-dlp` if it is on your `PATH`). The shipped file also differs from the built-in defaults in other ways, for example archive off, a 6-video playlist cap, and 10 comments. Review it with the GUI or a text editor.

## Usage

**Download**

1. Copy a YouTube video or playlist URL.
2. Run:

   ```
   python yt_video_downloader7.5.py
   ```

3. Add `--dry-run` to print the exact yt-dlp command without downloading anything:

   ```
   python yt_video_downloader7.5.py --dry-run
   ```

The console shows the five stages (analyze URL, prepare, configure metadata, download, post-process), a filtered live progress line, and a final summary with location and size. If a run fails, partly fails, or errors out, the window always waits for Enter so you can read why. Otherwise it respects `AUTO_CLOSE`.

**Edit settings (GUI)**

```
python yt_settings_gui7.5.py
```

A dark-themed editor for `yt_settings7.json`. Settings that don't apply to the current mode are hidden, and settings gated by a switch you turned off are greyed out, with a tooltip saying which switch to flip. It includes search (`Ctrl+F`), save (`Ctrl+S`), reload, undo/redo, and a **Preview command** button that runs a dry run against the URL on your clipboard and shows the resulting yt-dlp command. Preview never downloads anything and never changes your saved settings.

**Validate and test**

```
python yt_schema7.5.py yt_settings7.json   # check your settings file against the schema
python test_schema_wiring.py               # run the wiring tests (no pytest needed)
```

## How it works

- **Modes (`DOWNLOAD_TYPE`)**
  - `video`: downloads video and audio, merged into one file.
  - `audio`: downloads and extracts audio only.
  - `none`: downloads **no media**, only the sidecars you enabled. It needs `ENABLE_SIDECAR` on and at least one sidecar type kept (subtitles, thumbnails, or JSON). The download archive is never read or written in this mode.
- **URL analysis.** The script recognizes plain videos, playlists, and videos inside a playlist (`watch?v=...&list=...`). `PLAYLIST_URL_HANDLING` decides what happens with the last kind.
- **Quality.** `QUALITY_PRIORITY` is tried in exactly the order you list it (it does not assume highest to lowest), combined with `VIDEO_CODEC_PRIORITY` at each height. `QUALITY_FALLBACK` controls whether any available quality is accepted when none of your heights exist.
- **Capability probe.** For a single video, the script first checks which subtitles, chapters and heights that video actually has, and shows what it will do. Playlists are not probed per video.
- **Metadata.** With the sidecar master switch on, it can embed metadata and chapters, and write subtitle, thumbnail and `.info.json` files. Comments are saved with the JSON and turned into a readable `.parsed.txt`.
- **Settings are validated.** Missing keys use the schema default, unknown keys are ignored with a warning, and invalid values are replaced by their default with a `[WRONG]` message. Defaults live in one place, `yt_schema7.5.py`.
- **Resilient runs.** If some items fail but others succeed, the finished files are kept and post-processed, and the run is logged as `PARTIAL`. Files open in another program (for example a media player) can block embedding on Windows. Close them and re-run.

## Settings reference

`yt_settings7.json` is nested JSON. The nesting is only for grouping in the GUI, and every key name must be unique across the file. The **Default** column is the built-in default used when a key is missing from your file.

### Download type, quality, audio

| Key | Default | Description |
|---|---|---|
| `DOWNLOAD_TYPE` | `video` | `video`, `audio`, or `none` (sidecars only). |
| `QUALITY_PRIORITY` | `[720]` | Heights tried in order, e.g. `[1080, 720, 480]`. Video mode only. |
| `QUALITY_FALLBACK` | `true` | Accept whatever is available if none of the listed heights exist. |
| `VIDEO_CODEC_PRIORITY` | `[]` | Codecs tried in order at each height: `av1`, `vp9`, `h265`, `h264`, `vp8`. |
| `VIDEO_FORMAT` | `mp4` | Container: `mp4`, `mkv`, `webm`, or `auto`. |
| `AUDIO_CODEC` | `[]` | Sort preference for the audio stream (`opus`, `aac`, `mp3`, ...). Ranks only, never forces a conversion. |
| `AUDIO_BITRATE_CAP` | `auto` | Video mode only. Re-encodes just the audio track to Opus at this bitrate (e.g. `80k`). `auto` leaves audio alone. |
| `AUDIO_FORMAT` | `auto` | Audio mode only: `auto`, `aac`, `alac`, `flac`, `m4a`, `mp3`, `opus`, `vorbis`, `wav`. `auto` keeps the source codec. |
| `AUDIO_QUALITY` | `auto` | Audio mode only: `0` (best) to `10` (worst), a bitrate like `128K`, or `auto`. |

### URL and playlist handling

| Key | Default | Description |
|---|---|---|
| `PLAYLIST_URL_HANDLING` | `treat_as_video` | For `watch?v=...&list=...` URLs: `treat_as_video` (just that video), `start_from_index` (whole playlist from `PLAYLIST_START_INDEX`), `start_from_video` (whole playlist starting at that video). |
| `MAX_PLAYLIST_VIDEOS` | `0` | Cap on videos per run. `0` = unlimited. |
| `PLAYLIST_START_INDEX` | `1` | Position to start from in a playlist. |
| `ENABLE_ARCHIVE` | `true` | Playlists only. Remember downloaded videos in a per-playlist archive so repeat runs skip them. Never used in `none` mode. |

### Sidecar metadata

| Key | Default | Description |
|---|---|---|
| `ENABLE_SIDECAR` | `true` | Master switch. When `false`, every metadata feature below is skipped entirely. |
| `ENABLE_CHAPTERS` | `true` | Embed chapters. They cannot be exported as a separate file. Not available in `none` mode. |
| `ENABLE_SUBTITLES` | `true` | Subtitle master switch. |
| `EMBED_SUBTITLES` | `true` | Embed into the video. Has no effect in audio mode. Not available in `none` mode. |
| `KEEP_SUBTITLE_FILE` | `true` | Also keep a standalone subtitle file. |
| `SUB_LANGUAGES` | `["en"]` | Languages by name or code (`English` and `en` are the same). See [Subtitle rules](#subtitle-rules). |
| `SUB_FORMAT` | `vtt` | `vtt`, `srt`, `ass`, `lrc`, or `best` (no conversion). |
| `ENABLE_AUTO_SUBS` | `false` | Allow auto-generated captions, only as a fallback when no manual track exists for a language. |
| `SUB_ASK_MISSING` | `true` | On an interactive single-video run, ask once whether to use an auto track when a wanted language has no manual one. |
| `SUB_EMBED_LANGS` | `[]` | Optional override for which languages to embed. Falls back to `SUB_LANGUAGES` when empty. |
| `SUB_KEEP_LANGS` | `[]` | Optional override for which languages to keep as files. Falls back to `SUB_LANGUAGES` when empty. |
| `ENABLE_THUMBNAILS` | `true` | Thumbnail master switch. |
| `EMBED_THUMBNAILS` | `true` | Embed as cover art (album art in audio mode). Not available in `none` mode. |
| `KEEP_THUMBNAIL_FILE` | `true` | Keep a standalone thumbnail file. |
| `THUMBNAIL_FORMAT` | `jpg` | `jpg`, `png`, `webp`, or `auto`. Embedding into mp4/mkv can still force an internal webp-to-png conversion. |
| `ENABLE_JSON` | `true` | Write the video's `.info.json` and a readable `.parsed.txt`. |
| `KEEP_JSON_FILE` | `true` | `false` deletes the raw `.info.json` after parsing. |
| `ENABLE_COMMENTS` | `true` | Fetch comments (requires `ENABLE_JSON`). |
| `MAX_COMMENTS` | `all` | Comments to fetch: a number, `all`, or yt-dlp's comma-separated limit list. |
| `MAX_PARSED_COMMENTS` | `500` | Comments shown in the readable `.parsed.txt`. |

### Paths and behavior

| Key | Default | Description |
|---|---|---|
| `DOWNLOAD_DIR` | `.` | Where downloads go. |
| `LOG_DIR` | `{ROOT}/logs` | Log folder. `{ROOT}` is the script's folder; `{OTHER_KEY}` expands to that key's value. |
| `ARCHIVE_DIR` | `{LOG_DIR}/archives` | Where playlist archive files live. |
| `YT_DLP_EXE` | `yt-dlp` | yt-dlp executable (name on `PATH`, or a full path). |
| `AUTO_CLOSE` | `false` | `true` skips the "Press Enter to exit" pause on success (and disables the interactive subtitle prompt). Failures still pause. |
| `SHOW_LIVE_PROGRESS` | `true` | Filtered live console view. `false` is silent during download (the log file still gets everything). |
| `DEBUG_DRY_RUN` | `false` | Print the command and stop. Same as `--dry-run`. |

The schema also contains a few **planned, not yet active** settings (`TRACK_CHECK`, `TRACK_CHECK_LIMIT`, `COMMENT_SORT`). They have no effect.

## Subtitle rules

Subtitle handling is deliberately strict to avoid pulling in dozens of auto-translated tracks, which can get a run rate-limited (HTTP 429).

- **No wildcards.** Patterns like `en.*` are no longer supported and are reduced to the plain language (`en`) with a warning. `all` is ignored.
- **Single videos** are probed first, and only exact track codes are requested:
  - **Embedding:** one track per language (manual preferred), up to **3 tracks** total.
  - **Keeping:** every manual variant per language (`en`, `en-GB`, `en-US`, ...), with no cap.
  - Auto-generated tracks are used only as a fallback when there is no manual track, and only if `ENABLE_AUTO_SUBS` is on (or you say yes at the prompt).
- **Playlists** are not probed per video. Without auto subs, exact codes are used (up to 3 embedded). With auto subs on, only the **first** language is embedded.
- Subtitles cannot be embedded in audio files. For audio, `lrc` is the useful format (timed lyrics some players read).

## Output layout

Inside `DOWNLOAD_DIR`:

```
Single video:
  <title>\<title>.<ext>
  <title>\sidecar_items\        subtitles, thumbnail, .info.json, .parsed.txt

Playlist:
  <playlist title>\001 - <title>.<ext>
  <playlist title>\subtitles\
  <playlist title>\thumbnails\
  <playlist title>\infojsons\   includes 000 playlist-level files
```

Embedded items (subtitles, thumbnails, chapters, tags) live inside the media file. Separate files only appear for what you chose to keep.

## Logs

In `LOG_DIR`:

- one `DD-MM-YYYY_HHMMSS.log` per run with the full yt-dlp output (plus `_subs.log` for a separate subtitle pass),
- `summary.log`, one line per run: timestamp, duration, status (`SUCCESS`, `PARTIAL`, `FAILED`), scope, title, URL, which sidecars were on, mode, and size.

## Known limitations

- **Windows only** in practice (clipboard via PowerShell, backslash output templates, GUI launcher choices).
- **URL analysis is built around YouTube** (`watch?v=`, `youtu.be/`, `list=`). Other sites yt-dlp supports are not handled by the URL logic.
- **One URL per run**, read from the clipboard. There is no batch/queue mode yet.
- ffmpeg is required for most features but is not checked for. A missing ffmpeg shows up as a yt-dlp error in the log.
- yt-dlp is never auto-updated. You'll just be told when it's out of date.

## Repository files

| File | Purpose |
|---|---|
| `yt_video_downloader7.5.py` | The downloader (engine). Reads the clipboard, builds and runs the yt-dlp command. |
| `yt_settings_gui7.5.py` | PySide6 settings editor with command preview. |
| `yt_schema7.5.py` | The single source of truth for every setting: type, default, allowed values, gating, hints. |
| `yt_settings7.json` | Your settings (the shipped copy has the author's paths, so edit it). |
| `test_schema_wiring.py` | Tests that the schema, settings file, engine and GUI stay consistent. |
| `SUBTITLE_LOGIC.md`, `PINS.md`, `GUI_PLAN.md` | Design notes. |
