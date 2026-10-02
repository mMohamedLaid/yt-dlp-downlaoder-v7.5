import subprocess
import sys
import os
import re
import json
import tempfile
import threading
import urllib.request
import importlib.util
from pathlib import Path
from datetime import datetime

# ===============================================
#   YouTube Downloader v7.5 - Python (json edition)   [modes: video | audio | none]
#   Engine; reads yt_settings7.json; setting names/defaults/rules come
#   from yt_schema7.5.py (the one schema - no fallbacks live here)
# ===============================================

SCRIPT_DIR = Path(__file__).parent


def _import_sibling(fname):
    """
    Load a sibling module from this file's own folder, by path. A plain
    `import` can never load a file whose version position holds a dot
    (yt_schema7.5.py - Python reads that dot as a package separator), so
    the loader goes through importlib's file-location machinery instead,
    under the module name the filename spells. This small helper is
    deliberately duplicated in the engine, the GUI and the wiring test:
    it has to run before the schema exists to be imported from.
    """
    p = Path(__file__).parent / fname
    if not p.is_file():
        raise ImportError(f"{fname} not found next to {Path(__file__).name} "
                          f"- it must sit in the same folder.")
    spec = importlib.util.spec_from_file_location(fname[:-3], p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[fname[:-3]] = mod
    spec.loader.exec_module(mod)
    return mod


SCHEMA = _import_sibling("yt_schema7.5.py")
SETTINGS_FILE = SCRIPT_DIR / "yt_settings7.json"

for stream in (sys.stdout, sys.stderr):
    try: stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass

ROW_TPL = "%(playlist_title)S|||%(playlist_index)s|||%(id)s|||%(title)S"

# ---------- live console filtering during download ----------
# yt-dlp's normal output is a firehose (webpage/API JSON chatter, comment
# paging, per-file merge/embed steps). All of it still goes to the run's
# log file for debugging - only a small, deliberate whitelist gets echoed
# live to the console: which item is downloading, live progress, and any
# warning/error. A unique tag on our own --progress-template line is what
# makes progress reliably distinguishable from everything else, rather
# than trying to regex yt-dlp's default human-readable progress text.
PROGRESS_TAG = "YTP7LIVE"
PROGRESS_RE = re.compile(r"^" + re.escape(PROGRESS_TAG) + r"\|")
QUOTED_PATH_RE = re.compile(r'"([^"]*[\\/])([^"\\/]+)"')

MEDIA_EXTENSIONS = {"mp4", "mkv", "webm", "m4a", "mp3", "opus", "flac", "wav", "aac", "ogg"}

# The only values PLAYLIST_URL_HANDLING accepts. Anything else falls back to
# treat_as_video - the least destructive choice for a typo'd settings file,
# since it can never accidentally pull a whole playlist.
PLAYLIST_URL_HANDLING_MODES = {"treat_as_video", "start_from_index", "start_from_video"}


def has_media_output(target_dir):
    """
    Whether an actual video/audio file exists - not just "the folder has
    something in it." A run that fails before ever reaching the video/
    audio stream (e.g. subtitle fetching gets rate-limited first) can still
    leave stray files behind - an un-embedded .vtt that never made it to
    sidecar_items because the run aborted before that step - and treating
    that as "produced something" would be wrong. Real media files land
    directly in target_dir's own top level (sidecar items and playlist
    per-type folders don't), so this deliberately doesn't recurse.
    """
    if not target_dir.exists():
        return False
    return any(f.is_file() and f.suffix.lower().lstrip(".") in MEDIA_EXTENSIONS
               for f in target_dir.iterdir())


def sidecar_files(target_dir, treated):
    """
    Every sidecar file a run left behind - the none-mode counterpart of
    has_media_output(). A none-run produces no media, so "did it work?"
    can't be answered by looking for media: a perfect none-run would read
    as "nothing produced" and be misclassified FAILED. Same layout rules
    as build_command's output routing: a single video keeps its sidecars
    in sidecar_items\\; a playlist uses per-type subfolders.
    """
    names = (["sidecar_items"] if treated == "video"
             else ["subtitles", "thumbnails", "infojsons"])
    out = []
    for n in names:
        d = Path(target_dir) / n
        if d.is_dir():
            out += [f for f in d.iterdir() if f.is_file()]
    return out


def none_mode_blocker(settings):
    """
    '' when a none-mode run has something to fetch, else the reason it
    can't. Judged from settings alone (before any network work): none
    downloads ONLY sidecars, so with the master switch off - or with every
    sidecar type off / not kept - the command would be a bare
    --skip-download that does nothing and then reads as a failure.
    """
    if not settings["ENABLE_SIDECAR"]:
        return "DOWNLOAD_TYPE is none (sidecars only) but ENABLE_SIDECAR is off"
    wants = ((settings["ENABLE_SUBTITLES"] and settings["KEEP_SUBTITLE_FILE"])
             or (settings["ENABLE_THUMBNAILS"] and settings["KEEP_THUMBNAIL_FILE"])
             or settings["ENABLE_JSON"])
    if not wants:
        return ("DOWNLOAD_TYPE is none (sidecars only) but no sidecar is switched on "
                "to keep (subtitles, thumbnails or JSON)")
    return ""


def human_size(n):
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024 or unit == "GiB":
            return f"{n:.1f}{unit}" if unit != "B" else f"{n}{unit}"
        n /= 1024


def total_media_size(target_dir):
    """
    Sum of every top-level media file's size, in bytes - the same
    top-level-only, extension-based scope as has_media_output(), for the
    same reason (sidecar/per-type subfolders hold subtitles/thumbnails/
    json, not the media itself). Returns (total_bytes, file_count).
    """
    if not target_dir.exists():
        return 0, 0
    files = [f for f in target_dir.iterdir() if f.is_file() and f.suffix.lower().lstrip(".") in MEDIA_EXTENSIONS]
    return sum(f.stat().st_size for f in files), len(files)


def shorten_quoted_paths(line):
    """
    Strips the directory portion out of any quoted filesystem path in a
    line, keeping just the filename - used only for the console's own
    live view. The full line still goes to the log file untouched.

    Deliberately regex-based rather than stripping a precomputed target_dir
    prefix: yt-dlp sanitizes the folder name for the real filesystem (see
    the toolkit notes on output-template sanitization), so the path yt-dlp
    actually prints often differs character-for-character from the raw
    title this script printed at [1/5] - trying to reconstruct yt-dlp's
    exact sanitization here would be fragile and version-dependent, where
    "just keep whatever's after the last slash inside the quotes" isn't.
    """
    return QUOTED_PATH_RE.sub(lambda m: f'"{m.group(2)}"', line)
LIVE_KEEP_PATTERNS = [
    re.compile(r"^\[download\] Downloading item \d+ of \d+"),
    re.compile(r"Downloading \d+ items? of \d+"),
    re.compile(r"^\[Merger\] Merging formats into"),
    re.compile(r"^\[EmbedSubtitle\]"),
    re.compile(r"^\[EmbedThumbnail\]"),
    re.compile(r"^\[Metadata\] Adding metadata to"),
    re.compile(r"^\[ThumbnailsConvertor\] Converting thumbnail"),
    re.compile(r"^WARNING:"),
    re.compile(r"^ERROR:"),
]


def build_progress_template():
    return ("download:" + PROGRESS_TAG +
            "|%(progress._percent_str)s|%(progress._total_bytes_str)s|"
            "%(progress._speed_str)s|%(progress._eta_str)s")


def run_yt_dlp_streamed(cmd, log_path, live_output=True, on_progress=None, on_event=None, audio_mode=False):
    """
    Runs a yt-dlp command, writing every raw line to log_path (so a failed
    run can still be dumped in full, same as before) while filtering a
    small whitelist to two places:

    - on_progress(pct, size, speed, eta): fires for every parsed download
      progress update, as raw strings (e.g. pct="45.2%"). This is the hook
      a future GUI progress bar binds to directly - real numbers, not text
      to redraw - instead of parsing anything printed to a console.
    - on_event(line): fires for every other whitelisted line (item
      headers, merge/embed/metadata stage announcements, warnings/errors).
      Always the untouched raw line, even when audio_mode relabels what
      the console shows - a future consumer of this hook shouldn't have
      "thumbnail" silently turn into "album art" underneath it.

    Both callbacks fire regardless of live_output. live_output only
    controls whether this function ALSO prints its own console view
    (progress overwriting in place via \\r, everything else as its own
    line) - the CLI's current behavior when no callbacks are given.

    audio_mode=True relabels yt-dlp's own "Adding thumbnail" wording to
    "Adding album art" for this function's console output only - cosmetic,
    since an embedded image in an audio file IS album art, and the [3/5]
    preview line already draws this same distinction ("Embedded as album
    art" vs "Embedded in video"). yt-dlp's own text is otherwise untouched
    everywhere else (the log, on_event, non-thumbnail lines).
    """
    progress_active = False
    with open(log_path, "w", encoding="utf-8") as logf, \
         subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True, encoding="utf-8", errors="replace", bufsize=1) as proc:
        for raw in proc.stdout:
            line = raw.rstrip("\n")

            if PROGRESS_RE.match(line):
                parts = line.split("|")
                if len(parts) == 5:
                    pct, size, speed, eta = (p.strip() for p in parts[1:5])
                    shown = f"[PROGRESS] {pct} of {size} at {speed} ETA {eta}"
                    # The log gets the same readable line the console shows,
                    # not the raw internal marker - a log file can't "\r
                    # overwrite in place" like a terminal, so this doesn't
                    # cut down how many lines land in it, but it makes each
                    # one mean something to a person reading it afterward
                    # instead of a bare "YTP7LIVE|..." tag.
                    logf.write(shown + "\n")
                    if on_progress:
                        on_progress(pct, size, speed, eta)
                    if live_output:
                        print("\r      " + shown.ljust(70), end="", flush=True)
                        progress_active = True
                else:
                    logf.write(line + "\n")  # malformed marker line - keep raw rather than drop it
                continue

            logf.write(line + "\n")

            if any(p.search(line) for p in LIVE_KEEP_PATTERNS):
                clean = line.strip()
                if on_event:
                    on_event(clean)
                if live_output:
                    if progress_active:
                        print()
                        progress_active = False
                    shown = shorten_quoted_paths(clean)
                    if audio_mode and shown.startswith("[EmbedThumbnail]"):
                        shown = shown.replace("Adding thumbnail to", "Adding album art to")
                    print("      " + shown)

        proc.wait()
        if live_output and progress_active:
            print()
    return proc.returncode

# Human names accepted anywhere a language is expected in settings - English,
# en, and en.* are all treated as the same thing. One table, in the schema.
LANGUAGE_ALIASES = SCHEMA.LANGUAGE_ALIASES


def _flatten_json_tables(node: dict, out: dict, path: str = "") -> dict:
    """
    Every setting is a leaf value under some nested object -
    ["VIDEO QUALITY"], ["SIDECAR METADATA (Master Switch)"]["SUBTITLES"],
    etc. Those objects exist purely for grouping (the GUI's section/
    subsection headings) - this file has never cared about that structure,
    only the flat key -> value map. This walks every nested object
    depth-first collecting leaves, keeping each value's real JSON type
    (bool/list/int/str) rather than stringifying it - every settings[...]
    read elsewhere in this file works with that native type directly.
    Raises if the same key
    appears twice, since that would be silently ambiguous for every
    settings[KEY] read downstream.
    """
    for key, value in node.items():
        if isinstance(value, dict):
            _flatten_json_tables(value, out, f"{path}.{key}" if path else key)
        else:
            if key in out:
                raise ValueError(
                    f"Setting '{key}' is defined more than once "
                    f"(also under {path!r}) - every key must be unique "
                    f"across the whole settings file.")
            out[key] = value
    return out


def load_settings():
    if not SETTINGS_FILE.exists():
        print(f"[ERROR] Settings file not found: {SETTINGS_FILE}")
        input("Press Enter to exit..."); sys.exit(1)
    with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
        try:
            document = json.load(f)
        except json.JSONDecodeError as e:
            print(f"[ERROR] {SETTINGS_FILE} isn't valid JSON: {e}")
            input("Press Enter to exit..."); sys.exit(1)
    raw = _flatten_json_tables(document, {})

    # The schema is the only place defaults live: file values over schema
    # defaults, so every setting exists from here on (no per-read fallbacks).
    # Problems are reported, never fatal - same leniency as before.
    problems = SCHEMA.check_document(document)
    for k in problems["unknown"]:
        print(f"[WARN] Settings file has a key the schema doesn't know: {k} (ignored)")
    for k in problems["missing"]:
        print(f"[INFO] {k} not in the settings file - using schema default {SCHEMA.DEFAULTS[k]!r}")

    # The guard against hand-edited JSON: a value that fails the schema's
    # own validation is replaced by that key's default - loudly, instead of
    # flowing on to yt-dlp. (The schema is the one copy of this rule.)
    raw, wrong = SCHEMA.enforce(raw)
    for msg in wrong:
        print(f"[WRONG] {msg}")
    raw = SCHEMA.with_defaults(raw)

    # Path templating ({ROOT}, {SOME_KEY}) lives in the schema module too -
    # the GUI calls the very same function.
    return SCHEMA.expand_templates(raw, SCRIPT_DIR)


def resolve_effective_settings(settings, capabilities):
    """
    One resolution of raw settings (+ probed capabilities, when there are
    any) into the concrete booleans and derived values everything
    downstream actually branches on. print_configuration(), build_command(),
    run_subtitle_keep_pass(), and log_summary() all used to independently
    re-parse the same "yes"/"no" strings and re-derive the same values
    (SUB_KEEP_LANGS alone was read raw in three separate places) - that
    duplication is exactly the shape that already caused two real bugs
    (a wrong encoding, a wrong reply-detection check) and directly caused
    the subtitle-keep-pass path bug fixed earlier in this file. One
    resolution, read everywhere else, closes that off structurally instead
    of relying on remembering to keep N copies in sync by hand.

    Mirrors the raw/effective distinction from the GUI design notes: a
    flag here is only true if its own setting says yes AND its parent
    (e.g. ENABLE_SIDECAR) is also effectively on - exactly "effective =
    raw AND parent-effective" from that model, just computed for the CLI
    today instead of a GUI tree. capabilities is only meaningful for a
    single already-probed video (see probe_capabilities' own docstring) -
    pass None for playlists, same as every existing caller already does.
    """
    def yn(key):
        return bool(settings[key])

    enable_sidecar = yn("ENABLE_SIDECAR")
    enable_json = enable_sidecar and yn("ENABLE_JSON")
    download_type = settings["DOWNLOAD_TYPE"].lower()

    # The memory rule, enforced at the source: in none mode there is no
    # media file, so the embed-side settings are hidden in the GUI - and
    # hidden means never read here. They are forced off BEFORE yn() could
    # touch them (short-circuit order matters), so nothing downstream
    # can ever see a stale raw value.
    none_mode = download_type == "none"

    eff = {
        "download_type": download_type,
        "enable_sidecar": enable_sidecar,
        "enable_chapters": enable_sidecar and not none_mode and yn("ENABLE_CHAPTERS"),
        "enable_subs": enable_sidecar and yn("ENABLE_SUBTITLES"),
        "auto_subs_wanted": yn("ENABLE_AUTO_SUBS"),
        "embed_subs": not none_mode and yn("EMBED_SUBTITLES"),
        "keep_subs": yn("KEEP_SUBTITLE_FILE"),
        "sub_languages": settings["SUB_LANGUAGES"],
        "sub_format": settings["SUB_FORMAT"],
        "embed_langs": [] if none_mode else settings["SUB_EMBED_LANGS"],
        "keep_langs": settings["SUB_KEEP_LANGS"],
        "enable_thumbs": enable_sidecar and yn("ENABLE_THUMBNAILS"),
        "keep_thumbs": yn("KEEP_THUMBNAIL_FILE"),
        "embed_thumbs": not none_mode and yn("EMBED_THUMBNAILS"),
        "thumbnail_format": settings["THUMBNAIL_FORMAT"],
        "enable_json": enable_json,
        "enable_comments": enable_json and yn("ENABLE_COMMENTS"),
        "keep_json": yn("KEEP_JSON_FILE"),
        "manual_subs": capabilities["manual_subs"] if capabilities else None,
        "auto_subs": capabilities["auto_subs"] if capabilities else None,
        "has_chapters": capabilities["chapters"] if capabilities else None,
    }
    eff["sub_plan"] = plan_subtitles(settings, eff, capabilities)
    return eff


def get_clipboard():
    result = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
                            capture_output=True, text=True, encoding="utf-8")
    text = result.stdout.strip()
    if not text:
        return ""
    first_line = text.splitlines()[0].strip()
    return first_line if re.match(r'^https?://\S+$', first_line) else ""

# ---------- yt-dlp currency check ----------
UPDATE_PAGE_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest"


def check_yt_dlp_update(yt_exe):
    """
    (installed_version, latest_release), with a None in whichever slot
    couldn't be determined - installed=None means yt-dlp itself wouldn't
    run at all, latest=None means the network side failed (offline,
    rate-limited, GitHub hiccup). A None is "stay silent", never "claim
    up to date": the caller only speaks up when yt_dlp_outdated() says so.

    Deliberately no local cache: this runs once per launch, off the
    caller's critical path, and any failure just means the notice skips
    a beat - a state file for that wasn't worth it. Auto-update is a
    deliberate non-feature here (pinned, see PINS.md).
    """
    try:
        done = subprocess.run([yt_exe, "--version"], capture_output=True,
                              text=True, timeout=20)
        installed = done.stdout.strip()
    except Exception:
        return None, None
    if not installed:
        return None, None

    try:
        req = urllib.request.Request(
            "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest",
            headers={"Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=6) as resp:
            latest = (json.loads(resp.read().decode("utf-8")).get("tag_name") or "").strip()
    except Exception:
        return installed, None
    return installed, (latest or None)


def _version_date(v):
    m = re.match(r"(\d{4}\.\d{2}\.\d{2})", v)
    return m.group(1) if m else None


def yt_dlp_outdated(installed, latest):
    """
    Whether `installed` should be called older than `latest`. yt-dlp
    versions are date-based (YYYY.MM.DD; nightlies append a .HHMMSS time),
    and the installed build may be a NIGHTLY newer than the latest stable
    release - a plain string difference would then claim "update available"
    for an already-newer build (real case: installed 2026.08.30.232658 vs
    stable 2026.08.19). So the leading dates are compared, and a version
    that doesn't parse as a date on either side falls back to plain
    inequality.
    """
    if not installed or not latest:
        return False
    i, l = _version_date(installed), _version_date(latest)
    if i and l:
        return i < l
    return installed != latest

# ---------- probes (unique temp file per call; --print-to-file bypasses pipe encoding) ----------
def probe(yt, url, template, flat=True, items=None, extra_args=None):
    fd, tmp = tempfile.mkstemp(prefix="yt_probe_", suffix=".txt")
    os.close(fd)
    args = [yt, "-s"]
    if flat: args.append("--flat-playlist")
    if items: args += ["--playlist-items", items]
    if extra_args: args += extra_args
    args += ["--print-to-file", template, tmp, "--", url]
    try:
        subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        text = Path(tmp).read_text(encoding="utf-8")
    finally:
        try: os.unlink(tmp)
        except OSError: pass
    return [l for l in text.splitlines() if l.strip()]


def probe_rows(yt, url, items=None):
    rows = []
    for line in probe(yt, url, ROW_TPL, flat=True, items=items):
        parts = line.split("|||")
        if len(parts) == 4:
            rows.append({"playlist_title": parts[0].strip(), "index": parts[1].strip(),
                         "id": parts[2].strip(), "title": parts[3].strip()})
    return rows


def extract_capabilities(data):
    """
    Pulls "what does this video actually have" facts from an info-dict.
    """
    return {
        "manual_subs": list((data.get("subtitles") or {}).keys()),
        "auto_subs": list((data.get("automatic_captions") or {}).keys()),
        "chapters": bool(data.get("chapters")),
        "language": data.get("language"),
        "heights": sorted({f.get("height") for f in (data.get("formats") or []) if f.get("height")}),
        "comment_count": data.get("comment_count"),
    }


def probe_capabilities(yt, url):
    """
    One real dump of what a specific video actually has, via extract_capabilities.
    Only meaningful for a single video: a playlist would need this run once
    per entry to mean anything, which is exactly the per-video review that
    was ruled out earlier - callers should pass None for playlists rather
    than call this at all.
    """
    fd, tmp = tempfile.mkstemp(prefix="yt_caps_", suffix=".json")
    os.close(fd)
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            subprocess.run([yt, "-s", "--no-playlist", "-J", "--", url], stdout=f, stderr=subprocess.DEVNULL)
        data = json.loads(Path(tmp).read_text(encoding="utf-8"))
    except Exception:
        return None
    finally:
        try: os.unlink(tmp)
        except OSError: pass
    return extract_capabilities(data)


EMBED_TRACK_CAP = 3
LANG_CODE_RE = re.compile(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]+)*")
REGEX_CHARS = re.compile(r"[.*+?()\[\]|\\]")


def parse_sub_langs(entries, label):
    """
    Turns a language list (real JSON array now, not a comma-separated
    string) into a de-duplicated list of language codes (names, short
    codes, or explicit regional codes like en-US). Returns (codes,
    warnings). Regex is deliberately NOT supported: a pattern like en.*
    matches SEVERAL tracks of one language (en, en-orig, en-US, and
    ID-suffixed manual tracks such as en-nP7-2PuUl7o: up to 7 on one video
    in testing), which lets one language eat all 3 embed slots. Old
    pattern-shaped entries are reduced to their language prefix, loudly.
    NOTE on naming (verified on ~66,000 translation entries): a translated
    track is NOT named like 'en-ja'. It is keyed by its TARGET code alone
    (key 'ja'); in the info JSON its 'lang' is the SOURCE language and its
    'tlang' is the target. So en.* cannot match 100+ translations; only the
    'all' keyword can. See PINS.md, 'Reference: how yt-dlp names tracks'.
    """
    codes, warns = [], []
    for entry in (e.strip() for e in entries):
        if not entry:
            continue
        key = entry.lower()
        if key == "all":
            warns.append(f"{label}: 'all' is not supported (it's what causes the auto-translation flood) - ignored")
            continue
        if key in LANGUAGE_ALIASES:
            code = LANGUAGE_ALIASES[key]
        elif LANG_CODE_RE.fullmatch(entry):
            code = entry if "-" in entry else key
        elif REGEX_CHARS.search(entry) and re.match(r"[A-Za-z]{2,3}", entry):
            code = re.match(r"[A-Za-z]{2,3}", entry).group(0).lower()
            warns.append(f"{label}: pattern '{entry}' is no longer supported - using plain '{code}' instead")
        else:
            warns.append(f"{label}: '{entry}' isn't a known language name or code - ignored")
            continue
        if code not in codes:
            codes.append(code)
    return codes, warns


def resolve_language_tracks(code, manual, auto, auto_ok, video_lang):
    """
    What THIS video actually has for one requested language.
    Returns (manual_matches, auto_pick):
      manual_matches - every manual variant (exact code first, then
                       regional en-GB/en-US/... alphabetically)
      auto_pick      - only ever set when there is NO manual track AND auto
                       is allowed: '<code>-orig' if it exists, else the
                       plain code, but only when the video's declared
                       language doesn't contradict it. Auto is a fallback,
                       never an addition next to a manual track.
    """
    base = code.split("-")[0].lower()
    manual_matches = sorted(
        (v for v in manual if v == code or ("-" not in code and v.startswith(code + "-"))),
        key=lambda v: (v != code, v))
    auto_pick = None
    if not manual_matches and auto_ok:
        if f"{code}-orig" in auto:
            auto_pick = f"{code}-orig"
        elif code in auto and (not video_lang or video_lang.lower().split("-")[0] == base):
            auto_pick = code
    return manual_matches, auto_pick


def plan_subtitles(settings, eff, capabilities):
    """
    The single source of truth for everything subtitle-related in a run:
    which exact tracks get embedded, which get kept, and the flags for the
    main command plus (when needed) a separate keep pass. Read by
    print_configuration, build_command, the keep pass, and the auto-
    fallback prompt - none of them re-derive anything.

    SINGLE VIDEO (capabilities known): only exact track codes are ever
    sent to yt-dlp, so no wildcard can match a translation flood or fill
    the embed budget with duplicates.
      embed = ONE track per language (manual first, auto only as fallback),
              capped at 3 tracks total.
      keep  = every manual variant per language; auto only as fallback.

    PLAYLIST (capabilities None, nothing to resolve against):
      auto off -> exact codes, one per language (regional-only videos get
                  nothing - accepted trade-off), embed capped at 3.
      auto on  -> embed only the FIRST language, as 'L(-orig)?' (2 tracks
                  max); keep-only runs use 'L(-orig)?' for every language.
    """
    plan = {"lines": [], "warnings": [], "main_langs": "", "main_embed": False,
            "main_write": False, "main_auto": False,
            "keep_pass_langs": "", "keep_pass_auto": False, "auto_offer": []}
    if not eff["enable_subs"]:
        return plan

    embed_on = eff["embed_subs"] and eff["download_type"] == "video"
    keep_on = eff["keep_subs"]
    if not (embed_on or keep_on):
        return plan

    auto_ok = eff["auto_subs_wanted"]
    embed_raw = eff["embed_langs"] or eff["sub_languages"]
    keep_raw = eff["keep_langs"] or eff["sub_languages"]
    embed_codes, w1 = parse_sub_langs(embed_raw, "SUB_EMBED_LANGS/SUB_LANGUAGES") if embed_on else ([], [])
    keep_codes, w2 = parse_sub_langs(keep_raw, "SUB_KEEP_LANGS/SUB_LANGUAGES") if keep_on else ([], [])
    plan["warnings"] += list(dict.fromkeys(w1 + w2))

    if capabilities is None:
        # ---- playlist ----
        if embed_on:
            codes = embed_codes
            if auto_ok:
                if len(codes) > 1:
                    plan["warnings"].append(f"Playlist + auto-subs: embedding first language only ({codes[0]}); skipped: {', '.join(codes[1:])}")
                patterns = [f"{codes[0]}(-orig)?"] if codes else []
            else:
                if len(codes) > EMBED_TRACK_CAP:
                    plan["warnings"].append(f"Embed cap is {EMBED_TRACK_CAP}; skipped: {', '.join(codes[EMBED_TRACK_CAP:])}")
                patterns = codes[:EMBED_TRACK_CAP]
            plan["main_embed"] = bool(patterns)
            plan["main_write"] = bool(patterns)
            # No per-video track list to compare against here, so "does the
            # keep list match the embed list" is judged on the settings
            # themselves rather than resolved codes.
            plan["main_no_keep_subs"] = plan["main_embed"] and not (keep_on and keep_codes == embed_codes)
            if keep_on and keep_codes != embed_codes:
                plan["warnings"].append("Playlist runs keep exactly what they embed - a separate SUB_KEEP_LANGS list only applies to single videos")
        else:
            patterns = [f"{c}(-orig)?" if auto_ok else c for c in keep_codes]
            plan["main_write"] = bool(patterns)
        plan["main_langs"] = ",".join(patterns)
        plan["main_auto"] = bool(patterns) and auto_ok
        if patterns:
            plan["lines"].append(f"Playlist tracks (varies per video, not checked): {plan['main_langs']}")
            plan["lines"].append("One track per language when auto is off; exact regional-only variants (en-US) are skipped by design")
        return plan

    # ---- single video ----
    manual, auto = eff["manual_subs"], eff["auto_subs"]
    vlang = capabilities.get("language")

    def resolve(codes, one_per_lang, auto_allowed):
        out = []
        for c in codes:
            mm, ap = resolve_language_tracks(c, manual, auto, auto_allowed, vlang)
            if mm:
                out += [(c, t, "manual") for t in (mm[:1] if one_per_lang else mm)]
            elif ap:
                out.append((c, ap, "auto"))
            else:
                out.append((c, None, "none"))
        return out

    emb = resolve(embed_codes, True, auto_ok)
    kep = resolve(keep_codes, False, auto_ok)

    real = [x for x in emb if x[1]]
    if len(real) > EMBED_TRACK_CAP:
        plan["warnings"].append(f"Embed cap is {EMBED_TRACK_CAP} tracks; not embedded: {', '.join(x[0] for x in real[EMBED_TRACK_CAP:])}")
        real = real[:EMBED_TRACK_CAP]
    embed_tracks = [x[1] for x in real]
    keep_tracks = [x[1] for x in kep if x[1]]

    def describe(rows):
        return ", ".join(f"{c} -> {t} ({k})" if t else f"{c} -> none" for c, t, k in rows) or "nothing"
    if embed_on: plan["lines"].append(f"Embed: {describe(real + [x for x in emb if not x[1]])}")
    if keep_on:  plan["lines"].append(f"Keep:  {describe(kep)}")

    if not auto_ok:
        seen = set()
        for c in embed_codes + keep_codes:
            if c in seen: continue
            seen.add(c)
            mm, _ = resolve_language_tracks(c, manual, auto, False, vlang)
            _, ap = resolve_language_tracks(c, manual, auto, True, vlang)
            if not mm and ap:
                plan["auto_offer"].append((c, ap))
        if plan["auto_offer"]:
            plan["lines"].append("No manual track for: " + ", ".join(c for c, _ in plan["auto_offer"])
                                 + " - an auto-generated one exists (ENABLE_AUTO_SUBS=yes would use it)")

    # NOTE on the flags below: yt-dlp only ever fetches a subtitle track (manual
    # or auto) when --write-subs / --write-auto-subs is passed - --embed-subs
    # alone fetches nothing (confirmed against yt-dlp's own source: subtitles
    # are merged into `available_subs` gated strictly behind those two params,
    # in YoutubeDL.process_subtitles). So main_write/main_auto below mean
    # "this pass needs --write-subs / --write-auto-subs to fetch what it's
    # about to embed or keep" - NOT "the user wants to keep a copy." Whether
    # the fetched file also survives past embedding is a separate, later
    # decision (main_no_keep_subs), driven by yt-dlp's --compat-options
    # no-keep-subs, the actual (and only) switch for that.
    main_tracks = embed_tracks if embed_on else keep_tracks
    if main_tracks:
        plan["main_langs"] = ",".join(main_tracks)
        plan["main_embed"] = embed_on and bool(embed_tracks)
        plan["main_write"] = any(t in manual for t in main_tracks)
        plan["main_auto"] = any(t not in manual for t in main_tracks)
        same_as_keep = keep_on and set(keep_tracks) == set(main_tracks)
        # Default yt-dlp behavior keeps the standalone file once it's written;
        # no-keep-subs is what deletes it after embedding. Only skip deleting
        # it when the keep list is exactly what's being embedded - otherwise
        # a stray, unwanted file for languages nobody asked to keep would be
        # left behind (this is what a bare EMBED_SUBTITLES=yes / KEEP=no run
        # would otherwise silently do).
        plan["main_no_keep_subs"] = plan["main_embed"] and not same_as_keep
    if keep_on and keep_tracks and set(keep_tracks) != set(main_tracks):
        plan["keep_pass_langs"] = ",".join(keep_tracks)
        plan["keep_pass_auto"] = any(t not in manual for t in keep_tracks)
    return plan


def maybe_prompt_auto_fallback(settings, eff):
    """
    Single video only, and only when it would actually change something:
    a wanted language has no manual track, auto is off, but an auto track
    exists. Asks once, applies to this run only (settings file untouched).
    Never prompts in dry runs, automated runs (AUTO_CLOSE=yes), or when
    there's no interactive console (e.g. the GUI's worker thread).
    """
    offer = eff["sub_plan"]["auto_offer"]
    if not offer or not settings["SUB_ASK_MISSING"]:
        return False
    if settings["AUTO_CLOSE"] or settings["DEBUG_DRY_RUN"]:
        return False
    if "--dry-run" in sys.argv[1:] or sys.stdin is None or not sys.stdin.isatty():
        return False
    langs = ", ".join(f"{c} ({t})" for c, t in offer)
    try:
        ans = input(f"      [?] No manual subtitle for: {langs}. Use the auto-generated one for this video? [y/N] ")
    except EOFError:
        return False
    return ans.strip().lower() in ("y", "yes")


def padded(index):
    try: return f"{int(index):03d}"
    except (ValueError, TypeError): return str(index)


CODEC_PREFIXES = {
    "av1": ["av01"],
    "h264": ["avc1"],
    "avc": ["avc1"],
    "h265": ["hev1", "hvc1"],
    "hevc": ["hev1", "hvc1"],
    "vp9": ["vp9", "vp09"],
    "vp8": ["vp8", "vp08"],
}


def build_video_format(settings):
    heights = settings["QUALITY_PRIORITY"]
    codecs = [c.lower() for c in settings["VIDEO_CODEC_PRIORITY"]]
    tiers = []
    for h in heights:
        for c in codecs:
            # Codec names in settings are the common/friendly ones; the
            # actual vcodec strings yt-dlp reports don't always match them
            # directly (av1 -> av01, h264 -> avc1), and some codecs have
            # more than one real variant (h265 can show up as hev1 or
            # hvc1 depending on how it was muxed) - trying every known
            # variant at this priority position covers that instead of
            # guessing one and silently missing the other.
            for pre in CODEC_PREFIXES.get(c, [c]):
                tiers.append(f"bv*[height<={h}][vcodec^={pre}]+ba")
        tiers.append(f"bv*[height<={h}]+ba")  # this height, any codec, once the preferred codecs are exhausted
    if settings["QUALITY_FALLBACK"]:
        tiers.append("b")
    return "/".join(tiers) if tiers else "bv*+ba/b"


def build_sort_args(settings):
    # -S only takes a single preferred value per field, not a priority list -
    # video codec preference lives in build_video_format's filter chain
    # instead, which is the mechanism that actually supports an ordered list.
    audio_pref_list = settings["AUDIO_CODEC"]
    audio_pref = audio_pref_list[0] if audio_pref_list else ""
    return ["-S", f"acodec:{audio_pref}"] if audio_pref else []


AUDIO_FORMAT_MATCH = {
    # --audio-format values don't always match the real acodec string yt-dlp
    # reports - same mismatch class as the video codec fix. Confirmed for
    # these two: YouTube's AAC/m4a audio reports as mp4a.40.2/mp4a.40.5,
    # never "m4a" or "aac" directly. Anything not listed here (mp3, flac,
    # alac, wav) isn't natively served by YouTube at all, so requesting it
    # always means a real conversion regardless of source - no mapping needed.
    "m4a": "mp4a", "aac": "mp4a",
}


def describe_audio_transform(yt, url, audio_format):
    """
    yt-dlp skips re-encoding when --audio-format already matches the source
    codec - requesting opus or m4a back from YouTube is free for exactly
    that reason, opus-in-webm and opus-in-.opus both count as a match since
    this is a codec comparison, not a file-extension one. Anything else
    forces a real transcode. This just says which case is about to happen
    rather than letting it happen silently.
    """
    if not audio_format or audio_format.lower() in ("auto", "best"):
        print("      → Audio: kept as-is, no format forced")
        return
    lines = probe(yt, url, "%(acodec)s", flat=False, extra_args=["-f", "ba"])
    source = lines[0].lower() if lines else ""
    target = audio_format.lower()
    match_against = AUDIO_FORMAT_MATCH.get(target, target)
    if match_against in source:
        print(f"      → Audio: already {source}, no re-encode needed")
    else:
        print(f"      → Audio: converting {source or 'source'} → {target} (re-encode)")


def print_configuration(settings, eff, treated, yt_exe, url, capabilities):
    print("\n[2/5] Preparing download...")
    if eff["download_type"] == "audio":
        print(f"      → Mode: Audio-only ({settings['AUDIO_FORMAT']})")
        describe_audio_transform(yt_exe, url, settings["AUDIO_FORMAT"])
    elif eff["download_type"] == "none":
        print("      → Mode: none \u2014 sidecars only (no media file)")
    else:
        print(f"      → Mode: Video ({settings['VIDEO_FORMAT']})")
        wanted = settings["QUALITY_PRIORITY"]
        if capabilities and capabilities["heights"]:
            have = set(capabilities["heights"])
            marked = [f"{h} ({'available' if h in have else 'not here, trying next'})" for h in wanted]
            print(f"      → Quality priority: {', '.join(marked)}")
        elif treated == "playlist":
            print(f"      → Quality priority: {', '.join(str(h) for h in wanted)} (varies per video, not checked)")
        else:
            print(f"      → Quality priority: {', '.join(str(h) for h in wanted)}")
        codecs = settings["VIDEO_CODEC_PRIORITY"]
        if codecs: print(f"      → Codec priority (per quality tier): {', '.join(codecs)}")

    print("\n[3/5] Configuring metadata...")
    if not eff["enable_sidecar"]:
        print("      [×] Sidecar metadata disabled. master switch"); return

    if eff["download_type"] == "none":
        print("      [×] Chapters: n/a (embed-only, no media file)")
    elif eff["enable_chapters"]:
        if capabilities is None:
            print("      [?] Chapters: enabled (varies per video, not checked)")
        elif not eff["has_chapters"]:
            print("      [!] Chapters: enabled, but none available for this video")
        else:
            print("      [✓] Chapters enabled (meaning embedded only)")
        if eff["download_type"] == "audio": print("          |-- Note: Not all audio players support chapters")
    else: print("      [×] Chapters disabled")

    if eff["enable_subs"]:
        auto_wanted = eff["auto_subs_wanted"]
        if capabilities is None:
            print("      [?] Subtitles (manual): enabled (availability varies per video, not checked)")
            if auto_wanted: print("      [?] Subtitles (auto): enabled (availability varies per video, not checked)")
        else:
            if eff["manual_subs"]:
                print(f"      [✓] Subtitles (manual): available ({len(eff['manual_subs'])} languages)")
            else:
                print("      [!] Subtitles (manual): enabled, but none available for this video")
            if auto_wanted:
                if eff["auto_subs"]:
                    print(f"      [✓] Subtitles (auto): available ({len(eff['auto_subs'])} languages)")
                else:
                    print("      [!] Subtitles (auto): enabled, but none available for this video")

        if capabilities is None or eff["manual_subs"] or eff["auto_subs"]:
            plan = eff["sub_plan"]
            print(f"          |-- Format: {eff['sub_format']}")
            if eff["download_type"] == "audio": print("          |-- Cannot embed in audio file")
            if eff["download_type"] == "none": print("          |-- No embedding \u2014 files are only kept")
            for ln in plan["lines"]: print(f"          |-- {ln}")
            for w in plan["warnings"]: print(f"          |-- [!] {w}")
            if plan.get("main_no_keep_subs"):
                print("          |-- Embedded copy's temp subtitle file is deleted afterward (KEEP_SUBTITLE_FILE doesn't cover these languages)")
            if plan["keep_pass_langs"]:
                print(f"          |-- Separate keep pass for: {plan['keep_pass_langs']}")
            if not plan["lines"] and not plan["warnings"]:
                print("          |-- Nothing to keep (KEEP_SUBTITLE_FILE is off)" if eff["download_type"] == "none"
                      else "          |-- Nothing to embed or keep (EMBED_SUBTITLES and KEEP_SUBTITLE_FILE both off)")
    else: print("      [×] Subtitles disabled")

    if eff["enable_thumbs"]:
        # Not capability-checked on purpose - realistically every upload has
        # at least an auto-generated thumbnail, so there's nothing to grey out here.
        print(f"      [✓] Thumbnails enabled ({eff['thumbnail_format']})")
        if eff["download_type"] == "none":
            print("          |-- Keep-only (no embedding) ✓" if eff["keep_thumbs"]
                  else "          |-- [!] KEEP_THUMBNAIL_FILE is off \u2014 nothing to keep")
        if eff["embed_thumbs"]:
            print("          |-- Embedded as album art ✓" if eff["download_type"] == "audio" else "          |-- Embedded in video ✓")
            if eff["thumbnail_format"].lower() in ("auto", "best"):
                # Not this script's doing, and not avoidable from here: mp4/
                # mkv containers can't embed webp at all (confirmed against
                # yt-dlp's own issue tracker), so embedding forces an
                # internal webp->png conversion regardless of this setting.
                # "auto"/"best" only means "don't ALSO force-convert the
                # kept standalone copy" - it can't promise zero conversion
                # happens anywhere once embedding is on.
                print("          |-- Note: embedding may still force an internal webp→png conversion (container limitation, not this setting)")
        if eff["keep_thumbs"]: print("          |-- Keeping separate file ✓")
    else: print("      [×] Thumbnails disabled")

    if eff["enable_json"]:
        print("      [✓] JSON metadata enabled")
        if eff["enable_comments"]:
            if capabilities is None:
                print("          |-- Comments: enabled (varies per video, not checked)")
            elif not capabilities["comment_count"]:
                print("          |-- Comments: enabled, but unavailable/disabled on this video")
            else:
                mc = settings["MAX_COMMENTS"]
                print(f"          |-- Comment limit: {mc}" if not SCHEMA.comments_unlimited(mc) else "          |-- Including all comments")
        else: print("          |-- Comments: off (setting)")
        print("          |-- Keeping JSON file ✓" if eff["keep_json"]
              else "          |-- JSON deleted after parsing")
    else: print("      [×] JSON metadata disabled")



def build_command(url, treated, playlist_id, settings, eff, playlist_start=None, capabilities=None):
    yt_exe = settings["YT_DLP_EXE"]
    cmd = [yt_exe, "--paths", settings["DOWNLOAD_DIR"]]

    download_type   = eff["download_type"]
    enable_sidecar  = eff["enable_sidecar"]
    enable_subs     = eff["enable_subs"]
    keep_subs       = eff["keep_subs"]
    enable_thumbs   = eff["enable_thumbs"]
    keep_thumbs     = eff["keep_thumbs"]
    enable_json     = eff["enable_json"]
    enable_comments = eff["enable_comments"]
    max_comments    = settings["MAX_COMMENTS"]

    # ONE merged youtube extractor-args string (403 fix + comments can't clobber each other)
    ea = ["player_client=default,web_safari", "player_js_version=actual"]
    if enable_comments and not SCHEMA.comments_unlimited(max_comments):
        ea.append(f"max_comments={max_comments}")
    cmd += ["--extractor-args", "youtube:" + ";".join(ea)]

    if settings["SHOW_LIVE_PROGRESS"]:
        # --newline forces one line per update instead of in-place \r
        # updates, which is what makes our own line-by-line filtering in
        # run_yt_dlp_streamed reliable regardless of terminal/pipe quirks.
        cmd += ["--newline", "--progress-template", build_progress_template()]

    # none mode never selects media, so AUDIO_CODEC (the sort preference) is
    # not read at all.
    none_mode = download_type == "none"
    sort_args = [] if none_mode else build_sort_args(settings)

    # Format selection
    if none_mode:
        # No media file at all: every fetched sidecar is simply kept. The
        # output routing below is the same as a normal run's, so the sidecars
        # land exactly where they always do.
        cmd.append("--skip-download")
    elif download_type == "audio":
        # -f matters as much as -x does here: without it, format *selection*
        # still defaults to full video+audio, and -x just throws the video
        # away after downloading it in full.
        cmd += ["-f", "bestaudio/best", "-x"]
        audio_format = settings["AUDIO_FORMAT"].strip()
        if audio_format and audio_format.lower() != "auto":
            cmd += ["--audio-format", audio_format]
        audio_quality = settings["AUDIO_QUALITY"].strip()
        if audio_quality and audio_quality.lower() != "auto":
            cmd += ["--audio-quality", audio_quality]
    else:
        cmd += ["-f", build_video_format(settings)]
        video_format = settings["VIDEO_FORMAT"].strip()
        if video_format and video_format.lower() != "auto":
            cmd += ["--merge-output-format", video_format]

    if sort_args:
        cmd += sort_args

    # Output templates
    if treated == "video":
        if playlist_id: cmd.append("--no-playlist")
        cmd += ["--output", r"%(title)s\%(title)s.%(ext)s"]
        if enable_sidecar:
            if enable_subs and keep_subs: cmd += ["--output", r"subtitle:%(title)s\sidecar_items\%(title)s.%(ext)s"]
            if enable_thumbs and keep_thumbs: cmd += ["--output", r"thumbnail:%(title)s\sidecar_items\%(title)s.%(ext)s"]
            if enable_json: cmd += ["--output", r"infojson:%(title)s\sidecar_items\%(title)s.%(ext)s"]
    else:
        cmd.append("--yes-playlist")
        cmd.append("--no-overwrites")   # don't rewrite the 000 playlist metafiles on repeat runs
        cmd += ["--output", r"%(playlist_title)s\%(playlist_index)03d - %(title)s.%(ext)s"]
        if enable_sidecar:
            if enable_subs and keep_subs: cmd += ["--output", r"subtitle:%(playlist_title)s\subtitles\%(playlist_index)03d - %(title)s.%(ext)s"]
            if enable_thumbs and keep_thumbs: cmd += ["--output", r"thumbnail:%(playlist_title)s\thumbnails\%(playlist_index)03d - %(title)s.%(ext)s"]
            if enable_json: cmd += ["--output", r"infojson:%(playlist_title)s\infojsons\%(playlist_index)03d - %(title)s.%(ext)s"]
            # playlist-level metafiles gated independently
            if enable_thumbs and keep_thumbs: cmd += ["--output", r"pl_thumbnail:%(playlist_title)s\thumbnails\000 - %(playlist_title)s.%(ext)s"]
            if enable_json: cmd += ["--output", r"pl_infojson:%(playlist_title)s\infojsons\000 - %(playlist_title)s.%(ext)s"]

        # -I is the ONLY range mechanism (--playlist-start still exists as an
        # older alias but isn't what current docs point to - no reason to use it)
        max_pl = settings["MAX_PLAYLIST_VIDEOS"]
        if playlist_start != 1 or max_pl != 0:
            start = playlist_start
            if max_pl != 0:
                cmd += ["-I", f"{start}-{start + max_pl - 1}"]
            else:
                cmd += ["-I", f"{start}:"]

    # Archive: only when enabled AND the run actually behaves as a playlist.
    # NEVER in none mode: a sidecar-only run must not mark videos as
    # "downloaded" in the archive (yt-dlp records archive ids even under
    # --skip-download), or a later real download would skip them.
    enable_archive = settings["ENABLE_ARCHIVE"] and not none_mode
    if enable_archive and treated == "playlist":
        archive_dir = settings["ARCHIVE_DIR"]
        if archive_dir and playlist_id:
            cmd += ["--download-archive", str(Path(archive_dir) / f"{playlist_id}.archive.txt")]

    # Metadata flags
    if enable_sidecar:
        if not none_mode:   # nothing to embed INTO when no media file exists
            cmd.append("--embed-metadata")      # title/artist/album/date/track tags
            cmd.append("--no-embed-info-json")  # JSON (incl. comments) never rides along, even on mkv
            # Chapters ride along via --embed-metadata inheritance; only the OFF switch needs to be explicit
            if not eff["enable_chapters"]:
                cmd.append("--no-embed-chapters")

        if enable_subs:
            plan = eff["sub_plan"]
            if plan["main_langs"]:
                cmd += ["--sub-langs", plan["main_langs"]]
                if eff["sub_format"].lower() != "best": cmd += ["--convert-subs", eff["sub_format"]]
                # Order doesn't matter to yt-dlp, but write-subs/write-auto-subs
                # are what actually fetch tracks; embed-subs only embeds
                # whatever those two already pulled down (see plan_subtitles).
                if plan["main_write"]: cmd.append("--write-subs")
                if plan["main_auto"]: cmd.append("--write-auto-subs")
                if plan["main_embed"]: cmd.append("--embed-subs")
                if plan.get("main_no_keep_subs"): cmd += ["--compat-options", "no-keep-subs"]

        if enable_thumbs:
            if keep_thumbs: cmd.append("--write-thumbnail")
            if eff["thumbnail_format"].lower() not in ("auto", "best"): cmd += ["--convert-thumbnails", eff["thumbnail_format"]]
            if eff["embed_thumbs"]: cmd.append("--embed-thumbnail")

        if enable_json:
            cmd.append("--write-info-json")
            if enable_comments: cmd.append("--write-comments")

    # Not a sidecar concern - lives outside enable_sidecar on purpose, so
    # turning off subtitle/thumbnail/json metadata doesn't also silently
    # turn off an unrelated audio-quality decision.
    bitrate_cap = settings["AUDIO_BITRATE_CAP"].strip() if download_type == "video" else ""
    if bitrate_cap and bitrate_cap.lower() != "auto":
        # Targeting the merger specifically, not the generic ffmpeg key -
        # the generic key applies to every ffmpeg pass in the pipeline,
        # which would mean the embed-subs and embed-thumbnail passes
        # each re-encode the audio again on top of the merge's own
        # re-encode, compounding loss for no reason.
        cmd += ["--postprocessor-args", f"Merger:-c:v copy -c:a libopus -b:a {bitrate_cap}"]

    return cmd


def run_subtitle_keep_pass(yt_exe, url, settings, eff, log_dir, playlist_id=None):
    """
    Writes standalone subtitle files for tracks the main command didn't
    already write. Separate command on purpose: --sub-langs is one list
    per command, so it can't carry a different set for writing than
    whatever the main command used for embedding. Only exact track codes
    (from plan_subtitles) are ever passed - no patterns.
    """
    plan = eff["sub_plan"]
    pattern = plan["keep_pass_langs"]
    if not pattern:
        return True

    cmd = [yt_exe, "--paths", settings["DOWNLOAD_DIR"]]
    if playlist_id:
        cmd.append("--no-playlist")
    cmd += ["--skip-download", "--write-subs", "--sub-langs", pattern,
            "--output", r"subtitle:%(title)s\sidecar_items\%(title)s.%(ext)s"]
    if plan["keep_pass_auto"]:
        cmd.append("--write-auto-subs")
    if eff["sub_format"].lower() != "best":
        cmd += ["--convert-subs", eff["sub_format"]]
    live = settings["SHOW_LIVE_PROGRESS"]
    if live:
        cmd += ["--newline", "--progress-template", build_progress_template()]
    cmd.append(url)
    # Deliberately no --download-archive here: the video is already recorded
    # from the main pass, and reusing the archive would make yt-dlp see it
    # as already done and skip this pass entirely.

    log_file = log_dir / f"{datetime.now().strftime('%d-%m-%Y_%H%M%S')}_subs.log"
    returncode = run_yt_dlp_streamed(cmd, log_file, live_output=live, audio_mode=eff["download_type"] == "audio")

    if returncode != 0:
        print(f"      [!] Subtitle keep-pass had issues \u2014 see {log_file.name}")
    else:
        print(f"      [OK] Subtitle keep-pass complete ({pattern})")
    return returncode == 0


def parse_info_json(jf, max_parsed_comments):
    # utf-8-sig reads a BOM cleanly if one is present and behaves exactly
    # like plain utf-8 when there isn't one - safe either way.
    with open(jf, "r", encoding="utf-8-sig") as f:
        data = json.load(f)

    title = data.get("title", "Unknown Title")
    is_playlist = data.get("_type") in ("playlist", "multi_video") or "entries" in data

    # ---------- PLAYLIST-LEVEL METADATA (the 000 file) ----------
    if is_playlist:
        entries = data.get("entries") or []
        count = data.get("playlist_count") or len(entries)
        print(f"      [Playlist] {title}")
        idxs = [e.get("playlist_index") for e in entries if isinstance(e.get("playlist_index"), int)]
        if idxs:
            print(f"          |-- Videos: {count} (downloaded {padded(min(idxs))}-{padded(max(idxs))}, {len(entries)} videos)")
        else:
            print(f"          |-- Videos: {count}")
        out = ["=" * 40, "PLAYLIST DETAILS:",
               f"Title: {title}",
               f"ID: {data.get('id', '')}",
               f"Uploader: {data.get('uploader', '') or data.get('channel', '')}",
               f"Videos: {count}",
               "=" * 40, "",
               "PLAYLIST CONTENTS:"]
        for e in entries:
            if not isinstance(e, dict): continue
            idx = e.get("playlist_index")
            vid = e.get("id", "")
            out.append(f"{padded(idx) if idx is not None else '???'} - {e.get('title', '')}" + (f" (https://youtu.be/{vid})" if vid else ""))
        out_path = jf.with_name(jf.stem + ".parsed.txt")
        out_path.write_text("\n".join(out), encoding="utf-8")
        print(f"          |-- Saved → {out_path.name}")
        return out_path

    # ---------- PER-VIDEO ----------
    idx = data.get("playlist_index")
    vid = data.get("id", "")
    label = f"      [{padded(idx)}] {title}" if idx is not None else f"      [Video] {title}"
    print(f"{label} (https://youtu.be/{vid})" if vid else label)

    caps = extract_capabilities(data)
    chapters = data.get("chapters") or []
    print(f"          |-- Chapters: found ({len(chapters)})" if caps["chapters"] else "          |-- Chapters: None")

    sub_details = []
    if caps["manual_subs"]:
        langs = caps["manual_subs"]
        # Full list, not truncated - these are exactly the languages you'd
        # need to see in full to check SUB_EMBED_LANGS/SUB_KEEP_LANGS
        # actually matched what the video has. Auto-subs below stay a
        # count-only, since those commonly run to 150+ languages and
        # listing all of them would just be noise, not something anyone
        # picks from directly.
        if len(langs) > 12:
            shown = ", ".join(langs[:10])
            sub_details.append(f"manual ({shown}, +{len(langs) - 10} more)")
        else:
            sub_details.append(f"manual ({', '.join(langs)})")
    if caps["auto_subs"]:
        sub_details.append(f"auto ({len(caps['auto_subs'])} langs)")
    print(f"          |-- Subtitles: {', '.join(sub_details)}" if sub_details else "          |-- Subtitles: None")

    comments = data.get("comments") or []
    downloaded = len(comments)
    total = data.get("comment_count")
    if total is None or total < downloaded:
        total = downloaded                      # never claim fewer than we actually hold
    downloaded_disp = min(downloaded, total)    # clamp to available
    parsed_disp = min(downloaded_disp, max_parsed_comments) if max_parsed_comments >= 0 else downloaded_disp
    print(f"          |-- Comments: {total} ({downloaded_disp} downloaded, {parsed_disp} parsed)")

    out = ["=" * 40, "VIDEO DETAILS:", f"Title: {title}", f"ID: {vid}",
           f"URL: https://youtu.be/{vid}" if vid else "URL: ",
           f"Uploader: {data.get('uploader', '')}", f"Channel: {data.get('channel', '')}"]
    if data.get("channel_follower_count"): out.append(f"Channel Subscribers: {data['channel_follower_count']}")
    out += [f"Upload Date: {data.get('upload_date', '')}", f"Duration: {data.get('duration_string', '')}"]
    if data.get("like_count"): out.append(f"Like Count: {data['like_count']}")
    if data.get("comment_count"): out.append(f"Comment Count: {data['comment_count']}")
    if caps["manual_subs"] or caps["auto_subs"]:
        out.append(f"Subtitles (manual): {', '.join(caps['manual_subs']) if caps['manual_subs'] else 'none'}")
        out.append(f"Subtitles (auto): {len(caps['auto_subs'])} languages" if caps["auto_subs"] else "Subtitles (auto): none")
    out += ["=" * 40, ""]
    if data.get("description"): out += ["=" * 40, "DESCRIPTION:", data["description"], "=" * 40, ""]

    if comments:
        out += ["=" * 40, f"COMMENTS (showing up to {max_parsed_comments} of {len(comments)}):", "=" * 40, ""]
        shown = 0
        for c in comments:
            if max_parsed_comments >= 0 and shown >= max_parsed_comments:
                out.append(f"[Limit reached: only first {max_parsed_comments} comments shown]"); break
            # A comment with no parent field at all is top-level, same as an
            # explicit "root" - only an actual different id makes it a reply.
            is_reply = c.get("parent") not in (None, "root")
            if is_reply: out.append("    |-- REPLY:"); prefix = "    "
            else: out.append("-" * 40); prefix = ""
            line = prefix + str(c.get("author", ""))
            if c.get("_time_text"): line += f" - {c['_time_text']}"
            if (c.get("like_count") or 0) > 0: line += f" - {c['like_count']} likes"
            out += [line, prefix, prefix + str(c.get("text", "")), ""]
            shown += 1
    else:
        out += ["[No comments found]", ""]

    en = (data.get("automatic_captions") or {}).get("en")
    if en:
        out += ["=" * 40, "ENGLISH AUTO-GENERATED CAPTIONS:", "=" * 40, ""]
        for sub in en: out += [f"Format: {sub.get('ext', '')} - {sub.get('name', '')}", f"URL: {sub.get('url', '')}", ""]

    out_path = jf.with_name(jf.stem + ".parsed.txt")
    out_path.write_text("\n".join(out), encoding="utf-8")
    print(f"          |-- Saved → {out_path.name}")
    return out_path


def delete_parsed_jsons(file_paths):
    """
    Deletes the raw .info.json files once they've been parsed into their
    .parsed.txt sibling.
    """
    if not file_paths: return
    failed = []
    for p in file_paths:
        try:
            Path(p).unlink()
        except Exception as e:
            failed.append((p, e))
    if failed:
        print(f"      [!] Couldn't delete {len(failed)} .info.json file(s):")
        for p, e in failed:
            print(f"          {p.name}: {e}")


def log_summary(eff, log_dir, status, detected, treated, title, url, start_time, size_bytes=0):
    now = datetime.now()
    elapsed = int((now - start_time).total_seconds())
    epoch = int(now.timestamp())  # sortable, unique-enough, id-like - alongside the readable stamp, not instead of it

    if treated == "playlist" and detected == "video_in_playlist":
        scope = "part of playlist"
    elif treated == "playlist":
        scope = "playlist"
    else:
        scope = "single video"

    def yn(flag):
        return "yes" if flag else "no"
    line = (f"{now.strftime('%d-%m-%Y')} {now.strftime('%H:%M:%S')} ({epoch}) |{elapsed}s| "
            f"{status} | {scope} | {title} | {url} | "
            f"thumb={yn(eff['enable_thumbs'])} | sub={yn(eff['enable_subs'])} | json={yn(eff['enable_json'])} | "
            f"comments={yn(eff['enable_comments'])} | chapters={yn(eff['enable_chapters'])} | type={eff['download_type']} | "
            f"size={human_size(size_bytes)}")
    with open(log_dir / "summary.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def process_one_url(url, settings, yt_exe, download_dir, log_dir):
    """
    Runs the full pipeline for exactly one URL: analyze, configure,
    download, post-process. This is main()'s entire per-run body,
    extracted so a future caller that has more than one URL (a GUI queue,
    or main() itself looping) can call this once per URL instead of the
    logic being hardwired to exactly one clipboard read for its whole
    lifetime - which is what main() used to be.

    Returns a result dict rather than calling input()/sys.exit() itself:
    all terminal-interaction decisions (whether to pause, when to stop)
    stay in the caller, which is what actually lets a future queue-based
    caller keep going after one URL fails instead of the whole process
    exiting on it.
    """
    settings = SCHEMA.with_defaults(settings)   # callers (the GUI preview) may pass a partial dict
    t0 = datetime.now()
    print("\n[1/5] Analyzing URL...")

    # none mode downloads sidecars only - refuse before any network work
    # when there is nothing it could fetch.
    if settings["DOWNLOAD_TYPE"].lower() == "none":
        blocker = none_mode_blocker(settings)
        if blocker:
            print(f"      [✗] {blocker} — nothing to download")
            return {"status": "ERROR", "url": url}

    m_vid = re.search(r"[?&]v=([\w-]+)", url) or re.search(r"youtu\.be/([\w-]+)", url)
    video_id = m_vid.group(1) if m_vid else None
    m_pl = re.search(r"[?&]list=([\w-]+)", url)
    playlist_id = m_pl.group(1) if m_pl else None

    if "playlist?" in url and playlist_id: detected = "playlist"
    elif video_id and playlist_id: detected = "video_in_playlist"
    else: detected = "video"

    playlist_title, video_title, playlist_start, treated = None, None, None, detected
    start_from_url_video = False   # True only when playlist_start came from the URL's own embedded video, not the blanket setting fallback
    rows, total_videos = [], None

    if detected == "video":
        lines = probe(yt_exe, url, "%(title)S", flat=False)
        video_title = lines[0].strip() if lines else "untitled"
    else:
        h = settings["PLAYLIST_URL_HANDLING"].lower()
        if h not in PLAYLIST_URL_HANDLING_MODES:
            print(f"      [!] PLAYLIST_URL_HANDLING='{h}' isn't one of "
                  f"{', '.join(sorted(PLAYLIST_URL_HANDLING_MODES))} - using 'treat_as_video' for this run")
            h = "treat_as_video"
        hit = None
        if detected == "video_in_playlist" and h == "treat_as_video":
            # Gated: no full walk when the playlist is going to be ignored anyway.
            # One-item flat probe only to name the playlist it lives in.
            treated = "video"
            one = probe_rows(yt_exe, url, items="1")
            if one: playlist_title = one[0]["playlist_title"]
            lines = probe(yt_exe, url, "%(title)S", flat=False)
            video_title = lines[0].strip() if lines else "untitled"
        else:
            # ONE flat (metadata-only) walk: titles, indexes, ids AND the true playlist size
            rows = probe_rows(yt_exe, url)
            total_videos = len(rows)
            if total_videos == 0:
                # Empty is ambiguous by design - yt-dlp reports the same
                # "doesn't exist" error for a truly-deleted playlist as for
                # one that's merely private (as opposed to unlisted, which
                # resolves fine). No way to tell these apart from here, so
                # say so plainly rather than guessing, and stop before
                # wasting a full download attempt on it.
                print(f"      → URL: {url}")
                print(f"      → URL type: {detected.replace('_', ' ')}")
                print("      [✗] No videos found — the playlist may not exist, may be private (not the same as unlisted), or may currently be empty")
                return {"status": "ERROR", "url": url, "detected": detected, "treated": treated}
            if rows: playlist_title = rows[0]["playlist_title"]
            if detected == "playlist":
                treated = "playlist"
                hit = rows[0] if rows else None
            elif h == "start_from_index":
                treated = "playlist"
                hit = rows[0] if rows else None
            elif h == "start_from_video":
                treated = "playlist"
                hit = next((r for r in rows if r["id"] == video_id), None)
                if hit:
                    playlist_start = int(hit["index"])
                    start_from_url_video = True
                else:
                    print("      [!] Video not found in playlist index; falling back to full playlist")
                    hit = rows[0] if rows else None
            if treated == "playlist" and hit:
                video_title = f"{padded(hit['index'])} - {hit['title']}"

    if treated == "playlist" and not playlist_start:
        # PLAYLIST_START_INDEX is the setting-level equivalent of what a
        # video-in-playlist URL already gives for free (an explicit
        # starting position derived above from the URL itself). Without
        # this, a plain playlist URL - or a video-in-playlist URL whose
        # video wasn't found in the index - had no way to say "start from
        # position N" short of pasting a specific video's own URL instead.
        # A URL-derived start (set above) always wins over this: it's more
        # specific information about actual intent than a blanket setting.
        try:
            playlist_start = int(settings["PLAYLIST_START_INDEX"])
        except (ValueError, TypeError):
            playlist_start = 1

    folder = playlist_title if treated == "playlist" else video_title
    target_dir = download_dir / (folder or "untitled")

    # Only ever for a single video - a playlist would need this per entry to
    # mean anything, which is the per-video review already ruled out. This
    # also covers "video-in-playlist, treated as treat_as_video": it's being
    # downloaded as a standalone video, so it gets the full video treatment,
    # not the playlist "?" treatment.
    capabilities = probe_capabilities(yt_exe, url) if treated == "video" else None
    eff = resolve_effective_settings(settings, capabilities)
    if capabilities is not None and maybe_prompt_auto_fallback(settings, eff):
        settings = dict(settings, ENABLE_AUTO_SUBS=True)  # this run only
        eff = resolve_effective_settings(settings, capabilities)

    print(f"      → URL: {url}")
    print(f"      → URL type: {detected.replace('_', ' ')}")
    if detected == "video_in_playlist":
        mode = "playlist (start from this video)" if start_from_url_video else treated
        print(f"      → Treated as: {mode}")
    if detected != "video" and playlist_title:
        print(f"      → Playlist title: {playlist_title}")

    # Clamped range + true "how many will actually download" count
    if treated == "playlist":
        enable_archive = settings["ENABLE_ARCHIVE"]
        max_pl = settings["MAX_PLAYLIST_VIDEOS"]
        start = playlist_start
        req_end = start + max_pl - 1 if max_pl != 0 else None
        ends = [x for x in (req_end, total_videos) if x]
        end = min(ends) if ends else None
        range_rows = [r for r in rows if start <= int(r["index"] or 0) <= (end or 10**9)]

        archived = set()
        # none-runs never read or write the archive (see build_command), so
        # "N already downloaded" would describe a skip that won't happen.
        if enable_archive and settings["DOWNLOAD_TYPE"].lower() != "none":
            archive_dir_setting = settings["ARCHIVE_DIR"]
            if archive_dir_setting and playlist_id:
                af = Path(archive_dir_setting) / f"{playlist_id}.archive.txt"
                if af.exists():
                    with open(af, "r", encoding="utf-8", errors="replace") as f:
                        for ln in f:
                            tok = ln.split()
                            if len(tok) == 2: archived.add(tok[1])

        new_count = sum(1 for r in range_rows if r["id"] not in archived)
        already = len(range_rows) - new_count

        if total_videos is not None and start > total_videos:
            # Truncating `end` alone (already done above via min()) can't
            # fix this case: when `start` itself is already past the last
            # video, clamping the end down produces a range where
            # start > end - technically correct (0 videos, nothing
            # downloaded, nothing crashes) but reads exactly like a bug
            # ("Range: 7-6") rather than what actually happened. Naming it
            # directly beats a raw range string doing the explaining.
            #
            # Stopping here entirely, not just clarifying the message: the
            # flat scan already proves zero videos can match, so building
            # the command, invoking yt-dlp, and running post-processing
            # afterward would all be pure waste - a guaranteed-empty
            # command that still fetches playlist metadata just to
            # immediately parse and delete it, then reports "All done!"
            # for a run that downloaded nothing at all.
            print(f"      → Start position {start} is beyond the end of this playlist ({total_videos} videos total) — nothing to download")
            print(f"      → Path: {target_dir}")
            return {"status": "ERROR", "url": url, "target_dir": target_dir,
                    "detected": detected, "treated": treated}
        else:
            rng = f"{start}-{end}" if end else f"{start}-end"
            if req_end is not None and total_videos is not None and req_end > total_videos:
                print(f"      → Requested {start}-{req_end}, but the playlist only has {total_videos} — throttled to {rng}")
            if already:
                print(f"      → Range: {rng} ({new_count} new videos, {already} already downloaded)")
            else:
                print(f"      → Range: {rng} ({new_count} videos)")

    if detected != "playlist":
        print(f"      → Video title: {video_title}")
    print(f"      → Path: {target_dir}")

    print_configuration(settings, eff, treated, yt_exe, url, capabilities)

    print("\n[4/5] Downloading...")
    cmd = build_command(url, treated, playlist_id, settings, eff, playlist_start, capabilities)
    full_cmd = cmd + [url]

    print("      Full command:")
    print("      " + "=" * 60)
    print("      " + subprocess.list2cmdline(full_cmd))
    print("      " + "=" * 60)

    # >>> DEBUG: DRY RUN — surgically removable block >>>
    # To remove: delete this block and the DEBUG_DRY_RUN line in yt_settings7.json.
    dry_run = settings["DEBUG_DRY_RUN"] or "--dry-run" in sys.argv[1:]
    if dry_run:
        print("\n[DEBUG] Dry run: nothing downloaded, nothing parsed.")
        print("[DEBUG] The command above is copy-paste ready for a manual cmd test.")
        return {"status": "DRY_RUN", "url": url, "target_dir": target_dir}
    # <<< END DEBUG BLOCK <<<

    now = datetime.now().strftime("%d-%m-%Y_%H%M%S")
    log_file = log_dir / f"{now}.log"
    live = settings["SHOW_LIVE_PROGRESS"]
    returncode = run_yt_dlp_streamed(full_cmd, log_file, live_output=live, audio_mode=eff["download_type"] == "audio")

    had_errors = False
    if returncode != 0:
        with open(log_file, "r", encoding="utf-8") as f:
            log_content = f.read()
        if "Maximum number of downloads reached" not in log_content:
            had_errors = True

    if had_errors:
        # A nonzero exit code covers everything from "nothing downloaded at
        # all" to "5 of 6 items were flawless and one hit a postprocessor
        # error." Those aren't the same situation and shouldn't get the same
        # response - checking whether target_dir actually has anything in it
        # is what tells them apart. Treating every nonzero exit as total
        # failure would throw away real, usable output (and skip its
        # metadata parsing entirely) over one item among several.
        produced_something = (bool(sidecar_files(target_dir, treated)) if eff["download_type"] == "none"
                              else has_media_output(target_dir))
        if not produced_something:
            print("\n[×] Download failed! Dumping log:")
            print(log_content)
            log_summary(eff, log_dir, "FAILED", detected, treated, target_dir.name, url, t0)
            return {"status": "FAILED", "url": url, "target_dir": target_dir,
                    "detected": detected, "treated": treated, "log_file": log_file}
        elif "Access is denied" in log_content or "WinError 5" in log_content:
            # The specific, recognizable, actionable case: a file open in
            # another program (VLC, a media player, etc.) blocks yt-dlp's
            # temp-file rename during postprocessing. Worth naming directly
            # rather than making the person parse a raw WinError out of a
            # full log dump.
            print("\n[!] One or more files couldn't be fully processed — most likely because they were open in "
                  "another program (e.g. VLC) at the time. Close any open copies of the affected file(s) and "
                  "re-run to finish embedding for just those items; everything else below completed normally.")
        else:
            print("\n[!] Download finished with errors — dumping log:")
            print(log_content)
        # Falls through to post-processing either way, below.

    if treated == "video" and eff["sub_plan"]["keep_pass_langs"]:
        run_subtitle_keep_pass(yt_exe, url, settings, eff, log_dir, playlist_id)

    print("\n[5/5] Post-Processing...")
    if eff["enable_json"]:
        try: max_parsed = int(settings["MAX_PARSED_COMMENTS"])
        except ValueError: max_parsed = 500

        json_dir = target_dir / ("sidecar_items" if treated == "video" else "infojsons")
        search_dirs = [json_dir] if json_dir.exists() else [target_dir]
        for jdir in search_dirs:
            jsons = sorted(jdir.glob("*.info.json"))
            if not jsons: continue
            print("      → Parsing JSON files...")
            for jf in jsons:
                try:
                    parse_info_json(jf, max_parsed)
                except Exception as e:
                    print(f"        [ERROR] {jf.name}: {e}")
            if not eff["keep_json"]:
                print("      → Deleting parsed JSON files...")
                delete_parsed_jsons(jsons)

    print("\n" + "=" * 50)
    print("⚠ Done, but with some errors — see above" if had_errors else "✓ All done!")
    print(f"Location: {target_dir}")
    if eff["download_type"] == "none":
        side = sidecar_files(target_dir, treated)
        total_bytes = sum(f.stat().st_size for f in side)
        if side:
            label = "file" if len(side) == 1 else "files"
            print(f"Sidecars: {len(side)} {label}, {human_size(total_bytes)}")
        else:
            print("No sidecars produced")
    else:
        total_bytes, media_count = total_media_size(target_dir)
        if media_count:
            label = "file" if media_count == 1 else "files"
            print(f"Size: {human_size(total_bytes)} ({media_count} media {label})")
    print("=" * 50)
    status = "PARTIAL" if had_errors else "SUCCESS"
    log_summary(eff, log_dir, status, detected, treated, target_dir.name, url, t0, total_bytes)
    return {"status": status, "url": url, "target_dir": target_dir,
            "detected": detected, "treated": treated,
            "elapsed": (datetime.now() - t0).total_seconds()}


def main():
    print("=" * 50)
    print(" YouTube Downloader v7.5 (Python)")
    print("=" * 50)

    settings = load_settings()
    download_dir = Path(settings["DOWNLOAD_DIR"])
    log_dir = Path(settings["LOG_DIR"])
    archive_dir = Path(settings["ARCHIVE_DIR"])
    for d in (download_dir, log_dir, archive_dir): d.mkdir(parents=True, exist_ok=True)

    yt_exe = settings["YT_DLP_EXE"]

    # Passive currency check, off-thread so it can never delay the run.
    # The verdict prints after the run (below), where it can't interleave
    # with the pipeline's own output - and only when an update actually
    # exists; silent otherwise, including when the check fails.
    update_verdict = {}

    def _check_update():
        update_verdict["pair"] = check_yt_dlp_update(yt_exe)

    update_thread = threading.Thread(target=_check_update, daemon=True,
                                     name="yt-dlp-update-check")
    update_thread.start()

    url = get_clipboard()
    if not url:
        print("\n[1/5] Analyzing URL...")
        print("      [ERROR] Clipboard is empty or doesn't contain a URL.")
        input("Press Enter to exit..."); return

    result = process_one_url(url, settings, yt_exe, download_dir, log_dir)

    # Bounded wait so a fast run still gets the notice; an unreachable
    # GitHub just times this out and the notice is skipped for this run.
    if update_thread.is_alive():
        update_thread.join(timeout=4)
    installed, latest = update_verdict.get("pair", (None, None))
    if yt_dlp_outdated(installed, latest):
        print(f"\n[!] yt-dlp update available: {installed} \u2192 {latest}")
        print(f"    {UPDATE_PAGE_URL}")

    # Failure always pauses regardless of AUTO_CLOSE, so a failed automated
    # run doesn't close its window before anyone's seen why. Dry run and
    # success both respect AUTO_CLOSE, same as before.
    if result["status"] in ("FAILED", "ERROR", "PARTIAL"):
        input("Press Enter to exit...")
        return
    if not settings["AUTO_CLOSE"]:
        input("\nPress Enter to exit...")


if __name__ == "__main__":
    main()