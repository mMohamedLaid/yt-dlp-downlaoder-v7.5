"""
yt_schema7.5.py - the ONE place that says what a setting is.

The settings JSON stores only chosen VALUES. This module describes everything
a value could be: type, default, allowed choices, which switch gates it, which
download modes hide it, its hint text, its widget, its place in the JSON.

yt_video_downloader7.5.py and yt_settings_gui7.5.py both import this; neither
keeps its own copy any more (no engine `.get(key, fallback)`, no GUI SCHEMA /
HINTS / HIDDEN_IN_MODE / ARRAY_*_KEYS / INT_KEYS / CHIP_KEYS ...). If you add
a setting, the ONLY edit is one F(...) line below.

HOW TO ADD A SETTING (the whole procedure)
------------------------------------------
1. Add ONE F(...) line to FIELDS below, in the order you want it displayed.
2. Run `python yt_schema7.5.py [path/to/settings.json]`. It validates the schema
   itself and reports what your JSON is missing / has extra.
3. Done. DEFAULTS, the GUI tuples, HIDDEN_IN_MODE, HINTS, type registries and
   the nested-JSON layout are all DERIVED from FIELDS below - never edited
   by hand.

HOW TO ADD A NEW *MODE* (v7.5 did exactly this for DOWNLOAD_TYPE "none")
--------------------------------------------------------------------------
Add the mode to `choices` on DOWNLOAD_TYPE (or move it there from
`planned_choices`), then add it
to the `hidden_in` set of every key that must disappear in that mode, and give
`disabled_in={"none": "reason"}` to any key that should grey out instead.

HOW TO RETIRE A SETTING
-----------------------
Set status="retired" (keeps the key known so old files don't trip the
unknown-key check, but removes it from DEFAULTS and the GUI).

PLANNED settings (status="planned") are documented here so they are not lost,
but are NOT in DEFAULTS and are not shown or read until you flip them to
"active".
"""

from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path

SCHEMA_VERSION = 2   # 2 = v7.5: disabled_in, structural_disables, expand_templates

# --------------------------------------------------------------------------
# Type names (the "type" of a field = what it is in the JSON at rest)
#   bool | int | str | enum | int_list | str_list | path
# Widget names (how the GUI edits it)
#   bool | toggles | ruler | enum | spinbox | text
# --------------------------------------------------------------------------
TYPES = {"bool", "int", "str", "enum", "int_list", "str_list", "path"}
WIDGETS = {"bool", "toggles", "ruler", "enum", "spinbox", "text"}
STATUSES = {"active", "planned", "retired"}

FIELDS: list[dict] = []


def F(key, type, default, *, widget=None, choices=None, planned_choices=None,
      parent=None, hidden_in=(), hint="", mode_hints=None, group=(),
      min=None, max=None, pattern=None, strict=True, chips=None,
      lang_picker=False, advanced=False, browse=None, text_width=None,
      disabled_in=None, status="active", note=""):
    """Declare one setting. Everything but key/type/default is optional.
    hidden_in   = modes where the key is not rendered at all (mode-fact).
    disabled_in = {mode: reason}: rendered but greyed, reason shown (a
                  structural fact about the mode, independent of any parent)."""
    if widget is None:
        widget = {"bool": "bool", "int": "spinbox", "enum": "enum"}.get(type, "text")
    FIELDS.append(dict(
        key=key, type=type, default=default, widget=widget,
        choices=list(choices) if choices else None,
        planned_choices=list(planned_choices) if planned_choices else None,
        parent=parent, hidden_in=set(hidden_in), hint=hint,
        mode_hints=mode_hints or {}, group=tuple(group),
        min=min, max=max, pattern=pattern, strict=strict, chips=chips,
        lang_picker=lang_picker, advanced=advanced, browse=browse,
        text_width=text_width, disabled_in=dict(disabled_in or {}),
        status=status, note=note))


# --------------------------------------------------------------------------
# Shared option lists (referenced by fields below)
# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# LANGUAGES - the ONE name <-> code table (GUI and engine both read it).
#
# The user types/clicks a NAME ("English"); everything downstream works with
# the CODE ("en"). This table is ONLY that mapping. It deliberately does NOT
# list en.* / en-orig / en-US: those are track-matching forms the engine's
# resolver DERIVES from the code at run time (see resolve_language_tracks), so
# they never need to be stored or kept in sync.
#
# (name, code, primary)   primary=True -> one-click chip in the GUI;
#                         False -> reachable through the "More" menu.
# To add a language: add ONE line. Chips, menu, alias lookup all follow.
# --------------------------------------------------------------------------
LANGUAGES = [
    ("English", "en", True),   ("French", "fr", True),
    ("Japanese", "ja", True),  ("Spanish", "es", True),
    ("German", "de", True),    ("Italian", "it", True),
    ("Portuguese", "pt", True), ("Russian", "ru", True),
    ("Korean", "ko", True),    ("Chinese", "zh", True),
    ("Arabic", "ar", True),    ("Hindi", "hi", True),
    ("Dutch", "nl", False),    ("Polish", "pl", False),
    ("Turkish", "tr", False),  ("Swedish", "sv", False),
    ("Ukrainian", "uk", False), ("Vietnamese", "vi", False),
    ("Indonesian", "id", False), ("Thai", "th", False),
    ("Czech", "cs", False),    ("Greek", "el", False),
    ("Hebrew", "he", False),   ("Persian", "fa", False),
    ("Danish", "da", False),   ("Finnish", "fi", False),
    ("Norwegian", "no", False), ("Romanian", "ro", False),
    ("Hungarian", "hu", False), ("Bulgarian", "bg", False),
    ("Croatian", "hr", False), ("Serbian", "sr", False),
    ("Slovak", "sk", False),   ("Slovenian", "sl", False),
    ("Estonian", "et", False), ("Latvian", "lv", False),
    ("Lithuanian", "lt", False), ("Bengali", "bn", False),
    ("Tamil", "ta", False),    ("Telugu", "te", False),
    ("Malayalam", "ml", False), ("Marathi", "mr", False),
    ("Filipino", "fil", False), ("Malay", "ms", False),
    ("Swahili", "sw", False),  ("Afrikaans", "af", False),
]

# Other codes YouTube may use for the same language. UNVERIFIED against real
# track dumps - check with the subtitle tester data before the resolver
# relies on them. (Hebrew: legacy 'iw'.)
LANG_ALT_CODES = {"he": ["iw"]}

LANG_CHIPS = [n for n, _c, primary in LANGUAGES if primary]
MORE_LANGS = [n for n, _c, primary in LANGUAGES if not primary]
LANGUAGE_ALIASES = {n.lower(): c for n, c, _p in LANGUAGES}   # "english" -> "en"
CODE_TO_NAME = {c: n for n, c, _p in LANGUAGES}                # "en" -> "English"


def language_code(entry: str) -> str | None:
    """'English' / 'english' / 'en' -> 'en'.  Names resolve through the table;
    a bare code or regional code ('en', 'en-GB', 'pt-BR') passes through as
    written. Returns None when it is neither (caller decides how to warn).
    Pattern forms (en.*) are NOT handled here - not supported any more."""
    e = (entry or "").strip()
    if not e:
        return None
    if e.lower() in LANGUAGE_ALIASES:
        return LANGUAGE_ALIASES[e.lower()]
    if re.fullmatch(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*", e):
        return e if "-" in e else e.lower()
    return None


def language_name(code: str) -> str:
    """'en' -> 'English' (falls back to the code itself)."""
    return CODE_TO_NAME.get((code or "").split("-")[0].lower(), code)


# Section names, defined once so a typo can't fork a group.
S_TYPE, S_QUAL, S_AUDIO, S_URL = "DOWNLOAD TYPE", "VIDEO QUALITY", "AUDIO", "URL HANDLING"
S_SIDE = "SIDECAR METADATA (Master Switch)"
S_ARCH, S_PATHS, S_BEH, S_DBG = "ARCHIVE", "PATHS", "BEHAVIOR", "DEBUG"

# ==========================================================================
# FIELDS - display order == this order. `group` mirrors the JSON nesting.
# `default` == what the engine did when the key was missing (see DRIFT notes).
# ==========================================================================

# ---- DOWNLOAD TYPE -------------------------------------------------------
F("DOWNLOAD_TYPE", "enum", "video", widget="toggles", group=(S_TYPE,),
  choices=["video", "audio", "none"],
  hint="Options: video, audio, none. none = no media file at all - "
       "sidecars only (subtitles/transcript, thumbnail, info.json + "
       "comments). Needs ENABLE_SIDECAR, and at least one sidecar kept.",
  note="PIN 1 (shipped v7.5). In 'none': media-selection and embed-side keys "
       "are HIDDEN (raw persists; the engine must never read them), "
       "--skip-download, and --download-archive is NEVER written.")

# ---- VIDEO QUALITY -------------------------------------------------------
F("QUALITY_PRIORITY", "int_list", [720], group=(S_QUAL,), hidden_in={"audio", "none"},
  chips=["144", "240", "360", "480", "720", "1080", "1440", "2160", "4320"],
  min=1, max=8640,
  hint="Heights tried in exactly this order, comma-separated. First one that "
       "actually exists on the video wins - nothing here assumes "
       "highest-to-lowest.",
  note="DRIFT: live file has [480, 720]; engine fallback is [720] (used here).")
F("QUALITY_FALLBACK", "bool", True, group=(S_QUAL,), hidden_in={"audio", "none"},
  hint="If none of the heights above exist on the video: yes = accept "
       "whatever is available anyway, no = refuse rather than download a "
       "mismatched quality.")
F("VIDEO_CODEC_PRIORITY", "str_list", [], group=(S_QUAL,), hidden_in={"audio", "none"},
  chips=["av1", "vp9", "h265", "h264", "vp8"],
  hint="Codecs tried in exactly this order, comma-separated - same shape as "
       "QUALITY_PRIORITY above, first one the video actually has wins.")
F("VIDEO_FORMAT", "enum", "mp4", group=(S_QUAL,), hidden_in={"audio", "none"},
  choices=["mp4", "mkv", "webm", "auto"],
  hint="mp4, mkv, webm, or auto to leave the merge result untouched")

# ---- AUDIO ---------------------------------------------------------------
F("AUDIO_CODEC", "str_list", [], group=(S_AUDIO,), hidden_in={"none"},
  chips=["opus", "aac", "mp3", "vorbis", "flac", "alac", "ac3", "eac3", "pcm"],
  hint="Sort preference only - never forces a conversion, just ranks "
       "whichever audio stream(s) the video actually offers.")
F("AUDIO_BITRATE_CAP", "str", "auto", widget="ruler", group=(S_AUDIO,),
  hidden_in={"audio", "none"}, strict=False, pattern=r"^(auto|\d+[kK])$",
  choices=["auto", "48k", "64k", "80k", "96k", "112k", "128k", "160k", "192k"],
  hint="Forces a real re-encode of ONLY the audio track (video is left "
       "completely untouched). Retargets to Opus specifically, since Opus is "
       "the strongest performer among common codecs at this kind of bitrate "
       "- if the file's getting smaller regardless, this is the least lossy "
       "way to do it. auto = never touch audio at all. Example value: 80k",
  note="Engine applies it only to the merged video's audio (video mode); "
       "ignored in audio mode, hence hidden there.")
F("AUDIO_FORMAT", "enum", "auto", group=(S_AUDIO,), hidden_in={"video", "none"},
  choices=["auto", "aac", "alac", "flac", "m4a", "mp3", "opus", "vorbis", "wav"],
  hint="These two matter only when DOWNLOAD_TYPE=audio, where -x genuinely "
       "re-encodes (unlike the video branch, where a plain merge never "
       "does). auto keeps the source codec exactly as-is, no forced "
       "conversion - and requesting opus or m4a back from YouTube is free "
       "either way, since that's already the native codec being served. "
       "auto, aac, alac, flac, m4a, mp3, opus, vorbis, wav")
F("AUDIO_QUALITY", "str", "auto", widget="ruler", group=(S_AUDIO,),
  hidden_in={"video", "none"}, strict=False, pattern=r"^(auto|\d{1,2}|\d+[kK])$",
  choices=["auto", "0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10"],
  hint="0 (best) to 10 (worst), an explicit bitrate like 128K, or auto")

# ---- URL HANDLING --------------------------------------------------------
F("PLAYLIST_URL_HANDLING", "enum", "treat_as_video", widget="toggles",
  group=(S_URL,),
  choices=["treat_as_video", "start_from_index", "start_from_video"],
  hint="What to do when a single-video URL also carries a playlist id "
       "(watch?v=...&list=...): treat_as_video = just this video, playlist "
       "context dropped entirely start_from_index = the whole playlist, "
       "starting position from PLAYLIST_START_INDEX below start_from_video = "
       "the playlist, starting wherever this video sits in it (overrides "
       "PLAYLIST_START_INDEX)")

# ---- SIDECAR METADATA ----------------------------------------------------
F("ENABLE_SIDECAR", "bool", True, group=(S_SIDE,),
  hint="If no, every metadata feature below is skipped entirely")

# chapters
F("ENABLE_CHAPTERS", "bool", True, parent="ENABLE_SIDECAR",
  group=(S_SIDE, "CHAPTERS"), hidden_in={"none"},
  hint="Always embedded - yt-dlp has no separate chapter-file export",
  note="Hidden in 'none' mode (chapters are embed-only).")

# subtitles
_SUB = (S_SIDE, "SUBTITLES")
F("ENABLE_SUBTITLES", "bool", True, parent="ENABLE_SIDECAR", group=_SUB)
F("EMBED_SUBTITLES", "bool", True, parent="ENABLE_SUBTITLES", group=_SUB,
  hidden_in={"none"},
  hint="Embed into the video (has no effect for audio-only downloads)",
  disabled_in={"audio": "Can't embed into an audio-only file \u2014 no video "
                        "track to overlay onto"},
  mode_hints={"video": "",
              "audio": "No effect for audio-only downloads \u2014 there's no "
                       "video track to overlay onto."})
F("KEEP_SUBTITLE_FILE", "bool", True, parent="ENABLE_SUBTITLES", group=_SUB,
  hint="Keep a standalone file too, independent of embedding")
F("SUB_LANGUAGES", "str_list", ["en"], parent="ENABLE_SUBTITLES", group=_SUB,
  text_width=32,
  hint="Comma-separated languages (names or codes: English/en are the same). "
       "No regex/wildcards any more - a pattern like en.* can match several "
       "tracks of the same language (en, en-orig, en-US, ID-suffixed ones) "
       "and crowd out the embed slots. Only exact codes are resolved now, "
       "against this specific video's real track list. Used only when the "
       "two per-purpose lists below are empty.")
F("SUB_FORMAT", "enum", "vtt", parent="ENABLE_SUBTITLES", group=_SUB,
  choices=["vtt", "srt", "ass", "lrc", "best"],
  hint="vtt, srt, ass, lrc, or best (no conversion). lrc is the timed-lyrics "
       "format karaoke and music-player apps read - the audio-friendly pick: "
       "a kept .lrc gives an audio-only download synced lyrics any "
       "lyrics-aware player can pick up. (Deeper audio+lrc plans are pinned "
       "in PINS.md.) Note: for YouTube specifically, \"best\"/unconverted "
       "still comes out as vtt - that's YouTube's own native subtitle "
       "format, not a conversion this script or yt-dlp performs.",
  mode_hints={"video": "",
              "audio": "lrc = timed lyrics, the format karaoke/music-player "
                       "apps read \u2014 a kept .lrc is synced lyrics for the "
                       "audio file. (Deeper audio+lrc plans: PINS.md.)"},
  note="DRIFT: live file has 'best'; engine fallback is 'vtt' (used here).")
F("ENABLE_AUTO_SUBS", "bool", False, parent="ENABLE_SUBTITLES", group=_SUB,
  hint="Fallback only - never sits alongside a manual track for the same "
       "language, only fills in when no manual track exists. On a video "
       "whose real language isn't English, an auto-translated \"en\" is "
       "never picked; only the actual auto-original transcript (en-orig) is "
       "used. yes = allow auto-generated captions as that fallback")
F("SUB_ASK_MISSING", "bool", True, parent="ENABLE_SUBTITLES", group=_SUB,
  hint="no = never use auto captions even if a wanted language has none "
       "manually. When that happens on a single video with an auto track "
       "available, the downloader will ask once, for that run only, whether "
       "to use it anyway.")
F("SUB_EMBED_LANGS", "str_list", [], parent="EMBED_SUBTITLES", group=_SUB,
  chips=LANG_CHIPS, lang_picker=True, advanced=True, hidden_in={"none"},
  hint="Per-purpose overrides, for when embedding and keeping should differ. "
       "Same comma-separated language list as SUB_LANGUAGES above (still no "
       "regex). Each one falls back to SUB_LANGUAGES independently when "
       "empty - leaving both empty just uses SUB_LANGUAGES for both. "
       "SUB_EMBED_LANGS - one track per language, capped at 3 embedded "
       "tracks total (manual preferred, auto only as fallback)")
F("SUB_KEEP_LANGS", "str_list", [], parent="KEEP_SUBTITLE_FILE", group=_SUB,
  chips=LANG_CHIPS, lang_picker=True, advanced=True,
  hint="SUB_KEEP_LANGS - every manual variant kept per language (en, en-GB, "
       "en-US, ...), no cap; auto only as fallback")
# planned (SUBTITLE_LOGIC.md 3.2)
F("TRACK_CHECK", "enum", "auto", parent="ENABLE_SUBTITLES", group=_SUB,
  choices=["always", "auto", "never"], status="planned",
  hint="Check each video's subtitle list first: Always / Auto (by size) / "
       "Never. ('auto' here means the tool decides by size - nothing to do "
       "with auto-generated subtitles.)",
  note="SUBTITLE_LOGIC.md 3.2. Single videos ignore it (always probed).")
F("TRACK_CHECK_LIMIT", "int", 25, parent="TRACK_CHECK", group=_SUB, min=1,
  status="planned",
  hint="With TRACK_CHECK=auto: probe each video only up to this many "
       "playlist items.",
  note="SUBTITLE_LOGIC.md 3.2 suggested 25 (~2 min at 4.5 s median).")

# thumbnails
_TH = (S_SIDE, "THUMBNAILS")
F("ENABLE_THUMBNAILS", "bool", True, parent="ENABLE_SIDECAR", group=_TH)
F("EMBED_THUMBNAILS", "bool", True, parent="ENABLE_THUMBNAILS", group=_TH,
  hidden_in={"none"},
  hint="Video cover art, or album art for audio-only",
  mode_hints={"video": "",
              "audio": "Embedded as album art for audio-only downloads."},
  note="Hidden in 'none' mode.")
F("KEEP_THUMBNAIL_FILE", "bool", True, parent="ENABLE_THUMBNAILS", group=_TH)
F("THUMBNAIL_FORMAT", "enum", "jpg", parent="ENABLE_THUMBNAILS", group=_TH,
  choices=["jpg", "png", "webp", "auto"],
  hint="jpg, png, webp, or auto to leave it untouched",
  note="DRIFT: live file has 'auto'; engine fallback is 'jpg' (used here).")

# json / comments
_JS = (S_SIDE, "JSON METADATA")
F("ENABLE_JSON", "bool", True, parent="ENABLE_SIDECAR", group=_JS)
F("KEEP_JSON_FILE", "bool", True, parent="ENABLE_JSON", group=_JS,
  hint="no = raw .info.json is permanently deleted after parsing")
F("ENABLE_COMMENTS", "bool", True, parent="ENABLE_JSON", group=_JS,
  hint="Requires ENABLE_JSON=yes")
F("MAX_COMMENTS", "str", "all", parent="ENABLE_COMMENTS", group=_JS,
  pattern=r"^(\d+|all)(,(\d+|all)){0,4}$",
  hint="Comments actually fetched from the site (extraction-side limit)",
  note="Stays a STRING at rest. yt-dlp takes up to FIVE positional values: "
       "max-comments,max-parents,max-replies,max-replies-per-thread,"
       "max-depth (PIN 8). Engine sentinel for 'no limit' should be 'every "
       "field is all'.")
F("COMMENT_SORT", "enum", "new", parent="ENABLE_COMMENTS", group=_JS,
  choices=["new", "top"], status="planned",
  hint="Which comments YouTube returns first: new or top. Also decides WHICH "
       "comments get downloaded when the limit is below the total.",
  note="PIN 8. yt-dlp extractor-arg comment_sort; yt-dlp's own default is "
       "'new'.")
F("MAX_PARSED_COMMENTS", "int", 500, parent="ENABLE_COMMENTS", group=_JS,
  min=0,
  hint="Comments shown in the readable .parsed.txt (display-side limit)",
  note="DRIFT: live file has 5; engine fallback is 500 (used here). Keeps "
       "the first N entries top-down; replies count toward N.")

# playlist (nested under SIDECAR in the JSON for historical reasons - this is
# grouping only, NOT gating; hence parent=None)
_PL = (S_SIDE, "PLAYLIST")
F("MAX_PLAYLIST_VIDEOS", "int", 0, group=_PL, min=0,
  hint="0 = unlimited",
  note="DRIFT: live file has 6; engine fallback is 0 (used here). Grouped "
       "under SIDECAR in the JSON but NOT gated by ENABLE_SIDECAR.")
F("PLAYLIST_START_INDEX", "int", 1, group=_PL, min=1,
  hint="Where to start in the playlist, by position number - this is the "
       "\"-I N-M\" starting point you'd otherwise only get by pasting a "
       "specific video's own URL (watch?v=X&list=Y with "
       "PLAYLIST_URL_HANDLING set to start_from_video). Use THIS instead "
       "when you just have a plain playlist URL and want to start partway "
       "through without hunting down a specific video's link. If the pasted "
       "URL IS a video-in-playlist link treated as start_from_video, that "
       "URL's own position always wins over this value - it's more specific "
       "information about actual intent than a blanket setting. 1 = start "
       "from the beginning (default).")

# ---- ARCHIVE -------------------------------------------------------------
F("ENABLE_ARCHIVE", "bool", True, group=(S_ARCH,),
  hint="no = always re-download/re-check every video, ignore history",
  mode_hints={"none": "Not used in none mode \u2014 a sidecar-only run never "
                      "reads or writes the archive, so it can't \"consume\" "
                      "a playlist."},
  note="DRIFT: live file has false; engine fallback is True (used here).")

# ---- PATHS ---------------------------------------------------------------
F("DOWNLOAD_DIR", "path", ".", group=(S_PATHS,), browse="dir", text_width=45)
F("LOG_DIR", "path", "{ROOT}/logs", group=(S_PATHS,), browse="dir",
  text_width=45,
  note="{ROOT} = folder of the settings file; {OTHER_KEY} expands to that "
       "key's raw value.")
F("ARCHIVE_DIR", "path", "{LOG_DIR}/archives", group=(S_PATHS,), browse="dir",
  text_width=45)
F("YT_DLP_EXE", "path", "yt-dlp", group=(S_PATHS,), browse="file",
  text_width=45)

# ---- BEHAVIOR / DEBUG ----------------------------------------------------
F("AUTO_CLOSE", "bool", False, group=(S_BEH,))
F("SHOW_LIVE_PROGRESS", "bool", True, group=(S_BEH,),
  hint="yes = console shows a filtered live view during download (current "
       "item, live progress %, and merge/embed/metadata stage lines, plus "
       "warnings/ errors). Everything else - webpage/comment-paging chatter "
       "- still goes to the log file, just not the screen. no = fully "
       "silent during download, like before - log file only.")
F("DEBUG_DRY_RUN", "bool", False, group=(S_DBG,),
  hint="yes = print the command and stop, nothing is downloaded")


# ==========================================================================
# Derived views - NEVER edit these by hand; they are computed from FIELDS.
# ==========================================================================
BY_KEY = {f["key"]: f for f in FIELDS}
ACTIVE = [f for f in FIELDS if f["status"] == "active"]
ACTIVE_KEYS = [f["key"] for f in ACTIVE]
KNOWN_KEYS = set(BY_KEY)          # includes planned + retired (never "unknown")

DEFAULTS = {f["key"]: f["default"] for f in ACTIVE}

# GUI tuple form: key -> (widget kind, options, parent). Drop-in for SCHEMA.
GUI_SCHEMA = {f["key"]: (f["widget"], f["choices"], f["parent"]) for f in ACTIVE}

# key -> set of DOWNLOAD_TYPE values that hide it. Drop-in for HIDDEN_IN_MODE.
HIDDEN_IN_MODE = {f["key"]: set(f["hidden_in"]) for f in ACTIVE if f["hidden_in"]}

HINTS = {f["key"]: f["hint"] for f in ACTIVE}
MODE_HINTS = {f["key"]: dict(f["mode_hints"]) for f in ACTIVE if f["mode_hints"]}
CHIP_KEYS = {f["key"]: f["chips"] for f in ACTIVE if f["chips"]}
LANG_PICKER_KEYS = {f["key"] for f in ACTIVE if f["lang_picker"]}
ADVANCED_OVERRIDE_KEYS = {f["key"] for f in ACTIVE if f["advanced"]}
BROWSE_KEYS = {f["key"]: f["browse"] for f in ACTIVE if f["browse"]}
TEXT_WIDTHS = {f["key"]: f["text_width"] for f in ACTIVE if f["text_width"]}
ARRAY_INT_KEYS = {f["key"] for f in ACTIVE if f["type"] == "int_list"}
ARRAY_STR_KEYS = {f["key"] for f in ACTIVE if f["type"] == "str_list"}
INT_KEYS = {f["key"] for f in ACTIVE if f["type"] == "int"}
BOOL_KEYS = {f["key"] for f in ACTIVE if f["type"] == "bool"}


def modes() -> list[str]:
    """Currently valid DOWNLOAD_TYPE values."""
    return list(BY_KEY["DOWNLOAD_TYPE"]["choices"])


# --------------------------------------------------------------------------
# Values
# --------------------------------------------------------------------------
def flatten(node: dict, out: dict | None = None) -> dict:
    """Nested settings document -> flat {key: value} (same rule as the engine:
    nesting is grouping only, every key unique)."""
    out = {} if out is None else out
    for k, v in node.items():
        if isinstance(v, dict):
            flatten(v, out)
        else:
            if k in out:
                raise ValueError(f"Setting '{k}' is defined more than once.")
            out[k] = v
    return out


def with_defaults(flat: dict) -> dict:
    """What the engine should do right after loading: file values over schema
    defaults. After this, `cfg[key]` always exists - no per-call fallbacks."""
    merged = dict(DEFAULTS)
    merged.update({k: v for k, v in flat.items() if k in BY_KEY})
    return merged


def validate_value(key: str, value) -> list[str]:
    """Return a list of problems (empty = OK). Never raises."""
    f = BY_KEY.get(key)
    if f is None:
        return [f"{key}: not in schema"]
    t, errs = f["type"], []

    def is_int(x):
        return isinstance(x, int) and not isinstance(x, bool)

    if t == "bool":
        if not isinstance(value, bool):
            errs.append(f"{key}: expected true/false, got {value!r}")
    elif t == "int":
        if not is_int(value):
            errs.append(f"{key}: expected an integer, got {value!r}")
        else:
            if f["min"] is not None and value < f["min"]:
                errs.append(f"{key}: {value} is below minimum {f['min']}")
            if f["max"] is not None and value > f["max"]:
                errs.append(f"{key}: {value} is above maximum {f['max']}")
    elif t == "int_list":
        if not (isinstance(value, list) and all(is_int(x) for x in value)):
            errs.append(f"{key}: expected a list of integers, got {value!r}")
        else:
            for x in value:
                if f["min"] is not None and x < f["min"]:
                    errs.append(f"{key}: {x} is below minimum {f['min']}")
                if f["max"] is not None and x > f["max"]:
                    errs.append(f"{key}: {x} is above maximum {f['max']}")
    elif t == "str_list":
        if not (isinstance(value, list) and all(isinstance(x, str) for x in value)):
            errs.append(f"{key}: expected a list of strings, got {value!r}")
    elif t in ("str", "path"):
        if not isinstance(value, str):
            errs.append(f"{key}: expected a string, got {value!r}")
        elif f["pattern"] and not re.match(f["pattern"], value.strip()):
            errs.append(f"{key}: {value!r} doesn't match the allowed format")
    elif t == "enum":
        allowed = f["choices"] or []
        if not isinstance(value, str) or value.lower() not in [c.lower() for c in allowed]:
            errs.append(f"{key}: {value!r} not one of {allowed}")
    if t == "str" and f["strict"] is True and f["choices"] and isinstance(value, str) \
            and value not in f["choices"] and not f["pattern"]:
        errs.append(f"{key}: {value!r} not one of {f['choices']}")
    return errs


def enforce(flat: dict) -> tuple[dict, list[str]]:
    """The guard against hand-edited JSON (and mistyped GUI text fields):
    every ACTIVE key present in `flat` whose value fails validate_value is
    replaced by its schema default. Returns (corrected_copy, messages), one
    message per corrected key, e.g.
      SUB_FORMAT: 'txt' not one of ['vtt', ...] -> using default 'vtt'
    Keys absent from `flat` are left alone (with_defaults fills those), and
    unknown keys pass through untouched. One copy, here - the engine's
    load_settings and the GUI's settings gather both call it."""
    out, msgs = dict(flat), []
    for k in ACTIVE_KEYS:
        if k not in flat:
            continue
        errs = validate_value(k, flat[k])
        if errs:
            d = copy.deepcopy(DEFAULTS[k])
            msgs.append(f"{'; '.join(errs)} -> using default {d!r}")
            out[k] = d
    return out, msgs


def check_document(doc: dict) -> dict:
    """Compare a settings document with the schema.
    Returns {unknown, missing, invalid, planned_present}."""
    flat = flatten(doc)
    return {
        "unknown": sorted(k for k in flat if k not in KNOWN_KEYS),
        "missing": sorted(k for k in ACTIVE_KEYS if k not in flat),
        "invalid": [e for k, v in flat.items() if k in BY_KEY
                    for e in validate_value(k, v)],
        "planned_present": sorted(k for k in flat
                                  if k in BY_KEY and BY_KEY[k]["status"] == "planned"),
    }


def build_document(flat: dict | None = None) -> dict:
    """Flat values (default: DEFAULTS) -> nested document in schema order.
    Use for first-run file creation and for migrating a file that lacks new
    keys: existing values win, missing ones are filled from defaults."""
    values = with_defaults(flat or {})
    doc: dict = {}
    for f in ACTIVE:
        node = doc
        for g in f["group"]:
            node = node.setdefault(g, {})
        node[f["key"]] = values[f["key"]]
    return doc


def structural_disables(values: dict) -> dict:
    """{key: reason} for keys DISABLED by a fact about the current mode alone,
    independent of any parent switch (e.g. audio mode cannot embed subtitles)."""
    mode = values.get("DOWNLOAD_TYPE", "video")
    return {f["key"]: f["disabled_in"][mode]
            for f in ACTIVE if mode in f["disabled_in"]}


def effective_state(values: dict, key: str) -> tuple[str, str]:
    """('normal'|'hidden'|'disabled', nearest-cause) for one key, from the
    schema alone. This IS the GUI's rule (the GUI's _status_of delegates
    here), so GUI, engine and tests cannot drift. Order: hidden by mode ->
    structurally disabled by mode -> nearest unmet parent (a parent that is
    structurally disabled, hidden by mode, or switched off). `values` should
    be a complete flat dict (use with_defaults)."""
    f = BY_KEY[key]
    mode = values.get("DOWNLOAD_TYPE", "video")
    if mode in f["hidden_in"]:
        return "hidden", f"hidden in {mode} mode"
    sd = structural_disables(values)
    if key in sd:
        return "disabled", sd[key]
    parent = f["parent"]
    while parent:
        pf = BY_KEY[parent]
        if parent in sd:
            return "disabled", sd[parent]
        if mode in pf["hidden_in"]:
            return "disabled", f"{parent} is hidden in {mode} mode"
        if pf["type"] == "bool" and not values.get(parent, pf["default"]):
            return "disabled", f"{parent} is off"
        parent = pf["parent"]
    return "normal", ""


_TEMPLATE_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def expand_templates(flat: dict, root) -> dict:
    """Path templating, the ONE copy (engine and GUI both call this):
    {ROOT} = `root` (folder of the scripts/settings), {OTHER_KEY} = that key's
    still-raw value. Re-substituted until stable (one placeholder may name
    another), order-independent, strings only; bool/list/int pass untouched."""
    def sub(m):
        name = m.group(1)
        if name == "ROOT":
            return str(root)
        return str(flat[name]) if name in flat else m.group(0)

    def expand(value):
        if not isinstance(value, str):
            return value
        for _ in range(8):
            new = _TEMPLATE_RE.sub(sub, value)
            if new == value:
                break
            value = new
        return value

    return {k: expand(v) for k, v in flat.items()}


def comments_unlimited(max_comments) -> bool:
    """True when MAX_COMMENTS means 'no limit': every comma field is 'all'
    (yt-dlp takes up to five: max-comments,max-parents,max-replies,
    max-replies-per-thread,max-depth)."""
    parts = [p.strip().lower() for p in str(max_comments).split(",")]
    return all(p == "all" for p in parts)


# --------------------------------------------------------------------------
# Self-check: `python yt_schema7.5.py [settings.json]`
# --------------------------------------------------------------------------
def self_check() -> list[str]:
    errs = []
    seen = set()
    for f in FIELDS:
        k = f["key"]
        if k in seen:
            errs.append(f"duplicate key {k}")
        seen.add(k)
        if f["type"] not in TYPES:
            errs.append(f"{k}: bad type {f['type']}")
        if f["widget"] not in WIDGETS:
            errs.append(f"{k}: bad widget {f['widget']}")
        if f["status"] not in STATUSES:
            errs.append(f"{k}: bad status {f['status']}")
        if not f["group"]:
            errs.append(f"{k}: no group (JSON location)")
        if f["type"] == "enum" and not f["choices"]:
            errs.append(f"{k}: enum without choices")
        if f["status"] == "active":
            errs += [f"default of {e}" for e in validate_value(k, f["default"])]
    for f in FIELDS:  # parents: exist, are bools, no cycles
        p, hops = f["parent"], 0
        while p:
            if p not in BY_KEY:
                errs.append(f"{f['key']}: parent {p} not in schema")
                break
            if BY_KEY[p]["type"] != "bool" and BY_KEY[p]["key"] != "TRACK_CHECK":
                errs.append(f"{f['key']}: parent {p} is not a switch")
            p = BY_KEY[p]["parent"]
            hops += 1
            if hops > 20:
                errs.append(f"{f['key']}: parent cycle")
                break
        if f["status"] == "active" and f["parent"] and BY_KEY[f["parent"]]["status"] != "active":
            errs.append(f"{f['key']}: active key under non-active parent")
    names, codes = [n.lower() for n, _c, _p in LANGUAGES], [c for _n, c, _p in LANGUAGES]
    if len(set(names)) != len(names):
        errs.append("LANGUAGES: duplicate name")
    if len(set(codes)) != len(codes):
        errs.append("LANGUAGES: duplicate code")
    allowed_modes = set(BY_KEY["DOWNLOAD_TYPE"]["choices"]) | \
        set(BY_KEY["DOWNLOAD_TYPE"]["planned_choices"] or [])
    for f in FIELDS:
        bad = f["hidden_in"] - allowed_modes
        if bad:
            errs.append(f"{f['key']}: hidden_in names unknown mode(s) {sorted(bad)}")
        bad = set(f["disabled_in"]) - allowed_modes
        if bad:
            errs.append(f"{f['key']}: disabled_in names unknown mode(s) {sorted(bad)}")
        bad = set(f["mode_hints"]) - allowed_modes
        if bad:
            errs.append(f"{f['key']}: mode_hints names unknown mode(s) {sorted(bad)}")
    return errs


if __name__ == "__main__":
    problems = self_check()
    print(f"schema v{SCHEMA_VERSION}: {len(FIELDS)} fields "
          f"({len(ACTIVE)} active, {len(FIELDS) - len(ACTIVE)} planned/retired)")
    for p in problems:
        print("  SCHEMA ERROR:", p)
    if len(sys.argv) > 1:
        doc = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
        r = check_document(doc)
        print(f"\n{sys.argv[1]}:")
        for label in ("unknown", "missing", "invalid", "planned_present"):
            print(f"  {label}: {r[label] or 'none'}")
        flat = flatten(doc)
        diffs = [(k, DEFAULTS[k], flat[k]) for k in ACTIVE_KEYS
                 if k in flat and flat[k] != DEFAULTS[k]]
        print(f"  values differing from schema default ({len(diffs)}):")
        for k, d, v in diffs:
            print(f"    {k}: default {d!r}  file {v!r}")
    sys.exit(1 if problems else 0)
