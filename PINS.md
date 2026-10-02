# PINS — v7 deferred work that must not get lost

Companion to the gating pin (`D:\Downloads\gui_capability_gating_pin (2).md`, which is v6-era and needs its own refresh — see PIN 5). Things here are deliberately **not built yet**; each pin says why and what the first move is.

---

## PIN 1 — "No download" mode → **DONE (v7.5)**

Shipped as the **third value of `DOWNLOAD_TYPE`** (`video` / `audio` / `none`): downloads nothing but the sidecars — subtitles/transcript, thumbnail, infojson + comments. The radio row, the hiding and the state rules all fell out of the schema (`yt_schema7.5.py`); no second gating root.

**What shipped**
- **HIDDEN in `none`** (keys disappear, raw persists, engine never reads them — proven by `test_schema_wiring.py` section 5, now 120 variations): `QUALITY_PRIORITY`, `QUALITY_FALLBACK`, `VIDEO_CODEC_PRIORITY`, `VIDEO_FORMAT`, `AUDIO_CODEC`, `AUDIO_BITRATE_CAP`, `AUDIO_FORMAT`, `AUDIO_QUALITY`, and (user ruling: hidden, not greyed) `EMBED_SUBTITLES`, `SUB_EMBED_LANGS`, `EMBED_THUMBNAILS`, `ENABLE_CHAPTERS` (embed-only). Nothing is greyed by the mode itself.
- **Still live:** `ENABLE_SIDECAR`, every `KEEP_*` + `SUB_*` language/format/auto-subs, `THUMBNAIL_FORMAT`, `ENABLE_JSON`/comments, all playlist settings, `ENABLE_ARCHIVE` (see below), paths/behavior/debug.
- **Engine:** `build_command` takes a `--skip-download` branch — no `-f`/`-x`/merge/bitrate/sort flags, no `--embed-*`, no `--embed-metadata`; sidecar routing, `--write-*` flags and conversion are unchanged. `resolve_effective_settings` forces the embed flags/chapters off *before* reading them. Success detection counts sidecar files (`sidecar_files()`), not media — a perfect none-run is SUCCESS, not FAILED. End summary prints `Sidecars: N file(s), X` / `No sidecars produced`. `summary.log` field `audio_only=` became `type=video|audio|none`.
- **Guard:** a none-run with `ENABLE_SIDECAR` off — or with no sidecar type on and kept — returns ERROR before any network work ("nothing to download"), instead of running a bare `--skip-download`.
- **`plan_subtitles` needed no change:** `embed_on` already requires `download_type == "video"`, so none is a keep-only run and the separate keep pass never triggers.
- **Archive decision (was the open question): a none-run NEVER reads or writes `--download-archive`.** yt-dlp records archive ids even under `--skip-download`, which would make a later real download skip those videos. `ENABLE_ARCHIVE` stays visible in none mode (per the ruling) with a mode hint saying it is not used there; the range preview ignores any existing archive file.
- **Schema-as-guard (shipped with it):** `S.enforce(flat)` replaces any invalid active value with its schema default and says so. The engine prints `[WRONG] KEY: ... -> using default ...` at load; the GUI's settings gather runs the same function, so Preview corrects a mistyped field identically and shows the same `[WRONG]` lines.

---

## PIN 2 — yt-dlp auto-update

**Done (passive, this pass):** engine prints an end-of-run `yt-dlp update available: X → Y` line, and the GUI shows a toolbar link to the releases page when the installed build is older than the latest stable. Both share `check_yt_dlp_update()` in the engine — one background GitHub-API call per launch, deliberately **no** local cache file (judged not worth the state for a once-per-launch check; any failure just means the notice stays silent). Version comparison is date-based (`yt_dlp_outdated`): this machine runs a nightly (`2026.08.30.232658`) newer than the latest stable (`2026.08.19`), and a plain string difference would have flagged that as "update available" on first launch — so leading `YYYY.MM.DD` dates are compared and a newer-than-stable nightly stays silent.

**Pinned (per decision):** actual auto-update. Options when picked up: a GUI button that runs `yt-dlp.exe -U` (works for official binary builds), or download-and-replace. Nothing auto-runs today.

---

## PIN 3 — ~~one schema, two copies~~ → **DONE (v7.5)**: `yt_schema7.5.py` is the one schema; the engine and GUI derive everything from it, and `test_schema_wiring.py` keeps it that way. The table below is the historical drift record

The GUI (`SCHEMA`/`HINTS`/key-type registries in `yt_settings_gui7.py`) and the engine (`.get()` fallbacks in `yt_video_downloader7.py`) are two hand-maintained schemas over the same JSON. **Keys and enums agree today; the defaults have drifted.** Known divergences (engine fallback when a key is missing vs. the live file):

| Key | Engine fallback | File reality |
|---|---|---|
| `ENABLE_ARCHIVE` | `True` (both read sites) | `false` |
| `SUB_FORMAT` | `vtt` | `best` |
| `THUMBNAIL_FORMAT` | `jpg` | `auto` |
| `MAX_PARSED_COMMENTS` | `500` | `5` |
| `MAX_PLAYLIST_VIDEOS` | `0` (unlimited) | `6` |
| `QUALITY_PRIORITY` | `[720]` | `[480, 720]` |

Direction when picked up: one shared schema module (or a schema block inside the JSON itself) that both files read — defaults live in exactly one place, and a drift test becomes trivial.

---

## PIN 4 — lrc (karaoke) + audio plans

`lrc` is now a valid `SUB_FORMAT` (GUI enum + hints; the engine already passes `--convert-subs` through and yt-dlp converts via ffmpeg — no engine change was needed). Stated intent: *"I want to do something with it and audio"* — undecided. Natural fits, pick later:

- audio-mode default `SUB_FORMAT=lrc` (or a one-click preset: audio + keep-subs + lrc);
- a post-run step that copies/renames the kept `.lrc` from `sidecar_items\` next to the audio file with a matching basename, since lyrics-aware players pick `.lrc` up by proximity (current output routing deliberately keeps sidecars in their own subfolder);
- folds naturally into PIN 1's `none`-mode: "audio library + lyrics, no video" is exactly `none` + keep + lrc.

---

## PIN 5 — ~~the v6-era gating doc needs its v7 refresh~~ → FULFILLED by `GUI_PLAN.md`

The refresh now lives in **`GUI_PLAN.md`** (as-built corrections to the gating model, corrected rules, and the proactive → reactive roadmap with entry criteria). This pin remains only for one still-open code item it recorded: settings nesting is cosmetic — `MAX_PLAYLIST_VIDEOS`/`ENABLE_ARCHIVE` sit under "SIDECAR METADATA (Master Switch)" in the JSON but are not gated by `ENABLE_SIDECAR` in either file (queued as Phase 1 item 6 in the plan). `gui_capability_gating_pin (2).md` in Downloads is historical; don't update it, don't delete it — it's superseded, not lost.

---

## PIN 6 — "post-URL processing" (the dream)

**Interpretation, correct me:** after a URL is entered/pasted and *before* any download, the tool reads what the URL actually is and what each video actually offers (tracks, dubs, chapters, upload date, language) and resolves the settings against that — instead of the engine guessing from settings alone and special-casing playlists because it "cannot see" per-video facts. This is the engine half of GUI_PLAN Phase 2 (reactive layer) and its entry criterion 3 ("engine exposes data, not stdout").

Why it is pinned rather than built: the subtitle tester (`yt_batch_subs.py`) is still establishing what the data *means* (see PIN 7). Don't freeze a resolver on facts that are still moving.

What the findings already say it needs to know per video: manual tracks; ASR original (`<lang>-orig` = ASR of the original audio); dub-derived ASR (extra `-orig` tracks — present on some auto-dubbed videos, not others, cause unknown); auto-dub vs human-dub audio; the **audio list is per request** (the Short's dub list differed between runs; its caption list did not), so captions are the more reliable signal of what exists; translations are only listed by yt-dlp when an ASR track exists (A6 shows the player can translate a manual track that yt-dlp does not list).

Design note to decide then: a playlist could be resolved as N standalone videos (one probe per video, then the single-video logic) — removes every playlist special case in `plan_subtitles` at the cost of one extra request per video (429 risk; archive interaction). The "playlists never get per-video review" ruling is about GUI review, not about engine resolution.

---

## PIN 7 — subtitle resolution needs a rewrite (decision logic is hard to hold in your head)

Current rules, as the code really does them (`plan_subtitles`, verified, not the folk version):
- Each purpose falls back **on its own**: embed uses `SUB_EMBED_LANGS` else `SUB_LANGUAGES`; keep uses `SUB_KEEP_LANGS` else `SUB_LANGUAGES`. It is not "both empty → SUB_LANGUAGES".
- **Playlist + embed on:** the *embed* list is used (not `SUB_LANGUAGES` unless the embed list is empty). Keep is honored only if it equals the embed list; otherwise embedded files are deleted and nothing is kept (warning text says so, misleadingly).
- **Playlist + embed off:** the keep list is used, every language fetched.
- **Playlist + auto on:** embed the first language only, as `L(-orig)?`.
- **Single video:** tracks resolved per language against real tracks; embed 1 per language, cap 3; keep all manual variants; auto only as fallback.

Entry point when picked up: separate (a) *which languages, per purpose*, (b) *what this video offers* (normalized catalog), (c) *the policy* that maps a to b. **Full spec now lives in `SUBTITLE_LOGIC.md`** (what the code does, what the data shows, the upgrade: one language table, `TRACK_CHECK` probe setting, classifier + resolver, translation warning instead of a toggle). The date theory is dead; the dub-caption cause is not needed for the design.

---

## Reference: how yt-dlp names tracks (verified against real data; read this before trusting any older comment)

Source: the track dumps from the subtitle tester (28 videos, then a 427-video playlist). Run was yt-dlp nightly 2026.08.30.

- **A translated track is keyed by its TARGET code alone.** Key `ja`, not `en-ja`. In the info JSON its `lang` is the SOURCE language and `tlang` is the target. 66k of 69k auto entries in the big run are translations; none has a combined name. Only `en` starts with "en" among translation targets, so a pattern like `en.*` cannot match a translation flood; only `all` can.
- **Every listed translation is derived from the ASR original** (`kind=asr`), even when a human track exists in that language. yt-dlp lists NO translations for a video without an ASR track, but the player can still translate the human track (Arabic >> Arabic; the newest video). Translating from a manual track is unreachable through yt-dlp's list (adding `tlang=` to the manual track URL: untested).
- **`L-orig` = ASR of the original audio** (`kind=asr`, `tlang` empty). Plain `L` in the auto list is one of: a *same-language copy* of the original (`tlang` empty), a *translation into L* (`tlang` set), or a **no-URL placeholder** (this language has a manual track). Tell them apart by `tlang`/`has_url`, never by the name.
- **Manual track keys can carry a suffix:** `en-US`, `en-nP7-2PuUl7o`, `ar-kH2EDplw2PE`. The `lang` field has the real code.
- **Extra `-orig` tracks = dub captions (ASR of an auto-dub's audio).** Only on videos that list auto-dubs (121 of 121 in the 426-video run; none without). 121 of 309 auto-dubbed videos have them, 188 don't. **Not the upload date** (share by year 14/23/35/42/56/42/70/21/51%), not manual subs (57% vs 61%), never on human dubs. Mostly per video (116 of 121 cover all dubs). Cause unknown; stability between probes and views untested. Content is essentially a translation, timed to the dub audio. Full write-up: `SUBTITLE_LOGIC.md`.
- **The audio list varies per request** (a Short's dub list changed between runs; its caption list did not).
- **Probe cost** (427 videos, sequential, 1 s pause): median 4.5 s, mean 4.8 s, max 10.3 s, 1 failure, 34 min total.


---

## PIN 8 — comments picker: one number where yt-dlp takes five

**Facts.** yt-dlp's YouTube `max_comments` extractor-arg is positional: `max-comments,max-parents,max-replies,max-replies-per-thread,max-depth`, default `all,all,all,all,all` (yt-dlp README, fetched 2026-10-01 — it was four values earlier; **five now**). There is also `comment_sort`: `top` or `new` (default `new`).

**What the code does.** Engine passes `max_comments={MAX_COMMENTS}` raw, so a comma list would work; but (1) the "no limit" sentinel is compared against `all` and the stale four-value `all,all,all,all`, so a full five-value default would print as a limit; (2) the GUI treats `MAX_COMMENTS` as a plain text field validated as digits or `all` only, so the extra values can't be entered at all; (3) `comment_sort` isn't exposed; (4) `MAX_PARSED_COMMENTS` is a separate, display-side limit (the readable `.parsed.txt`), clamped against `MAX_COMMENTS` when that is numeric.

**Plan.** Default view (collapsed): Total comments · **Sort** (new / top) · Parsed limit. A "More control" disclosure adds the other four fields (Top-level · Replies · Replies per thread · Depth; blank = all). Storage stays one string key `MAX_COMMENTS` (`"10"` or `"10,5,20,3"`), so existing files keep working; the GUI composes/splits it and trims trailing `all`s. New key `COMMENT_SORT`. Validation: each field digits or `all`. Clamp the parsed limit against the *first* field. Engine: fix the sentinel to "every field is `all`". Stays gated by `ENABLE_COMMENTS` ← `ENABLE_JSON`.

**Note to show next to the parsed limit (verified in `parse_info_json`):** `MAX_PARSED_COMMENTS` keeps the first N entries of the downloaded list, from the top down, in yt-dlp's order, and **replies count toward N** (so the cut can land in the middle of a thread). Sort is YouTube-side (README), so it also decides *which* comments are downloaded when the limit is below the total: `new` gives the newest, `top` the highest-ranked.
