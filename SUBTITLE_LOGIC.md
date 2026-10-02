# Subtitle logic — what the code does, what the data shows, how to upgrade it

Status: design spec, nothing here is built yet. Companion to `PINS.md` (deferred work) and `GUI_PLAN.md` (architecture).
Every claim is tagged where it matters: **[code]** read from the source, **[data]** counted from the tester dumps, **[docs]** from yt-dlp's README, **[guess]** reasoning that has not been tested.

Evidence base: `subtitle_test_tracks.json` (27 videos) + `subtitle_test_2_tracks.json` (1 video) + the 427-video SCP playlist run (426 readable, one probe failed). yt-dlp nightly 2026.08.30. Run date 2026-10-01.

---

## Part 1 — What the code does today

**Settings it reads.** `SUB_LANGUAGES`, `SUB_EMBED_LANGS`, `SUB_KEEP_LANGS` (three lists), `ENABLE_AUTO_SUBS`, `SUB_ASK_MISSING`, `SUB_FORMAT`, `EMBED_SUBTITLES`, `KEEP_SUBTITLE_FILE`. In the live JSON `SUB_LANGUAGES` holds codes (`en`) but the embed/keep lists hold names (`Arabic`); `parse_sub_langs` accepts both. **[code]**

**List fallback.** Each purpose falls back on its own: embed uses `SUB_EMBED_LANGS`, else `SUB_LANGUAGES`; keep uses `SUB_KEEP_LANGS`, else `SUB_LANGUAGES`. It is *not* "both empty → SUB_LANGUAGES". **[code]**

**Single video** (the capability probe has the track list): **[code]**
- Manual match for a wanted code: the exact code first, then every `code-*` variant alphabetically — so `en` does find `en-US` and ID-suffixed tracks like `en-nP7-2PuUl7o`.
- Embed takes ONE track per language, capped at 3 (`EMBED_TRACK_CAP`). Keep takes every manual variant, uncapped.
- Auto is a fallback only, never next to a manual track: `L-orig` if it exists (**no check that L is the video's original language**), else plain `L` but only if the video's declared language matches. So translations are never picked.
- Auto off but an auto track exists → "auto offer" line, and the CLI can prompt (`SUB_ASK_MISSING`; unreachable from the GUI worker).
- One main pass writes subs; `--compat-options no-keep-subs` deletes them after embedding unless keep == embed. A separate download-less *keep pass* runs only when keep ≠ embed, single videos only, never with `--download-archive`.

**Playlist** (no probe, so blind patterns): **[code]**
- Auto off: exact codes, one per language, embed capped at 3. Regional-only tracks (`en-US` with no `en`) are skipped by design.
- Auto on + embed: only the FIRST language, as `L(-orig)?` (that pattern can match 2 tracks, which is why only one language fits under the cap).
- Keep-only: `L(-orig)?` for every language.
- Keep must equal embed or it is dropped; the warning text says "keep list only applies to single videos", which is misleading.

**Known problems** (each follows from the above plus Part 2):
1. `L-orig` is picked without checking it belongs to the original audio. On the test playlist, 121 of 426 videos (28%) carry dub captions, so a blind `fr(-orig)?` hands those a French *dub* transcript and the other 305 nothing, from one identical setting.
2. `L(-orig)?` matches two tracks per language: manual + `-orig`, or on ASR-only videos `-orig` + the plain same-language copy (two transcripts of the same speech).
3. Playlists miss regional/ID-suffixed manual tracks (7 of 28 videos in the small sample; on `3BLoozf3MlM` the only human English track is ID-suffixed).
4. Three lists with independent fallback, mixed codes and names, and a keep/embed rule that differs between video and playlist — nobody can hold this in their head.
5. Legacy comments claimed translations are named `en-xx` and that `en.*` floods translations. **Wrong** — fixed in the engine docstring, the GUI hint and `GUI_PLAN.md`; the true format is in `PINS.md` ("Reference").

---

## Part 2 — What the data shows

### Track naming (all verified)
- **Manual** tracks: key is the code, sometimes suffixed (`en-US`, `en-nP7-2PuUl7o`). The `lang` field has the real code. **[data]**
- **Auto** tracks come in four kinds **[data]**: *original* (`L-orig`, ASR of the original audio), *same-language copy* (plain `L`, `tlang` empty), *translation* (key = TARGET code only, `lang` = source, `tlang` = target; never `en-ja`), *placeholder* (no URL; this language has a manual track).
- Big run, 68,923 auto entries: 65,945 translations (96%), 1,986 originals, 698 same-language copies, 294 placeholders (= the 294 manual tracks). A typical video has 157 auto entries (284 of 426); 180+ means dub captions.
- Manual tracks per video: 178 none (42%), 216 one (51%), 32 two or more. Manual `en` 230 videos, `en-US` 14.

### Translations
- Every listed translation derives from the ASR original (`kind=asr`) — 20 of 20 videos that list any, including videos that also have a human track in that language. **[data]**
- A video with no ASR track lists **zero** translations (8 of 8), yet the player still offers Auto-translate and translates the *human* track (your screenshots: "Arabic >> Arabic", and the newest video whose cues followed the manual rhythm). So manual-sourced translation exists on YouTube's side but is not reachable through yt-dlp's list. Adding `tlang=` to the manual track's URL might work — **[guess], untested**.

### Original language and original ASR
- Rule: original language = audio tagged `original`, else the video's `language` field, else the sole `-orig`. It matched a real `-orig` on 20 of 20 videos where it applies; the `language` field and the original-audio tag never disagreed. **[data]**
- 6 of 28 videos have no `language` at all and no `-orig`/original audio → no answer, and nothing for auto to fill. Two videos know their original language (audio tag) but have no ASR (`pU9sHwNKc2c`, `28iP9c9hERM`, the latter uploaded 1 day earlier).
- All 426 videos of the big run have an original `-orig`.

### Dub captions (extra `-orig` tracks = ASR of an auto-dub's audio)
- Exist **only** on videos that list auto-dubs: 121 of 121 have them, and there are zero extra `-orig` tracks without listed auto-dubs. **[data]**
- But not guaranteed: 121 of 309 auto-dubbed videos (39%) have them, 188 do not. "Auto-dubbed" does not mean "more than one `-orig`".
- **Not the upload date**: share by year 14 / 23 / 35 / 42 / 56 / 42 / 70 / 21 / 51 % (2018→2026), no cutoff, no trend; positives from 2018-10 to 2026-09, negatives from 2018-08 to 2026-06, neighbours from the same month disagree. **[data]**
- Not manual subs (57% vs 61%). Never on human-dubbed videos (7 in the small sample). Mostly per video: 116 of 121 cover all their listed dubs, 5 partial.
- Only visible difference: median duration 42 min (with) vs 25 min (without), but videos with no auto-dubs are also long (41 min), so weak. Views, and whether the split is stable between two probes of the same video, are **untested** (tester now records `view_count`; `analyze_tracks.py --sample` builds the test).
- Content of a dub caption is ≈ a machine translation of the original, but timed to the dub audio, so against the original audio it would drift. **[guess]**
- **Decision:** we do not need to predict who has them. We only need to classify them correctly (any `-orig` beyond the original one is a dub caption) so they are never mistaken for the original.

### Probe cost
Median 4.5 s, mean 4.8 s, fastest 3.5 s, slowest 10.3 s, 1 failure in 427, total 34 min 20 s (sequential, 1 s pause). No sign of rate-limiting at that pace. A 6-video slice ≈ 30 s extra. **[data]**

### yt-dlp facts used
One `--sub-langs` list; yt-dlp has no per-video "first available" logic, so choosing needs the track list. `--sleep-subtitles SECONDS` exists. **[docs]** `--load-info-json` might let a download reuse a probe's result without re-extracting (URLs expire): **[guess], untested**.

---

## Part 3 — The upgrade

### 3.1 One language table instead of three lists
```json
"LANGUAGES": [
  {"lang": "en", "embed": true,  "keep": true},
  {"lang": "fr", "embed": true,  "keep": true},
  {"lang": "ar", "embed": true,  "keep": false},
  {"lang": "it", "embed": false, "keep": true}
]
```
GUI: a small table, Language | Embed ☐ | Keep ☐, reorderable. Order is priority. Embed cap 3 applies down the Embed column; keep takes every row ticked. No fallback rules, no video/playlist difference. Migration from the live file: rows = union of the three lists (names → codes via the existing name map). The one thing lost is a separate embed order. In **no-download mode (PIN 1)** the Embed column *disappears* (hidden, not greyed): that mode is a "sidecar download simulator". The embed flags stay in the file but the engine must never read them in that mode (the memory rule, GUI_PLAN Part 2); Keep stays live. Audio mode stays as today (embed disabled, with a reason) unless you want it hidden too. Do this together with PIN 3 (single schema), since it is a new schema.

### 3.1b `SUB_LANGUAGES` becomes the power-user override
The key survives with a new job: a raw `--sub-langs` string, comma-separated regexes exactly as yt-dlp reads them, pasted however the user likes. It sits under Advanced, collapsed, with a red note: *"No safety net. Patterns go to yt-dlp as written: `en.*` can match several tracks of one language, `all` matches 100+ auto-translations, and nothing stops the embed cap, rate limits (HTTP 429) or a huge download. Not our bug."*
- When non-empty it **replaces** the table's language choice: no probe, no resolver, no cap, no backfill, no warnings. The global behaviours still apply (embed on/off and keep on/off → `--embed-subs` / `no-keep-subs`, `--write-auto-subs` when auto is on, format conversion).
- GUI: the table and `TRACK_CHECK` go DISABLED with the nearest-cause reason "overridden by the raw SUB_LANGUAGES pattern" (a normal SETTINGS reason in GUI_PLAN's model).
- **Migration hazard:** existing files hold `SUB_LANGUAGES: ["en","fr","ar"]` as the old fallback list. The migration must move those into the table and blank the key, or it silently becomes an active raw override.
- Engine: when set, skip `parse_sub_langs` (it currently reduces patterns to prefixes, loudly).
- Nice to have: Preview shows the final yt-dlp command so power users can see what their regex turned into.

### 3.2 Probe setting for playlists
Key `TRACK_CHECK`: `always` / `auto` / `never`, plus `TRACK_CHECK_LIMIT` (suggested 25 ≈ 2 min at the 4.5 s median; your call). Here **"auto" means "the tool decides by size"** and has nothing to do with auto-generated subtitles; the GUI label should say so: *"Check each video's subtitle list first: Always / Auto (by size) / Never"*.
- `always`: probe every downloaded video → the exact method (3.3–3.5).
- `auto`: if the number of videos that will be downloaded (the slice: start index + max videos, not the whole playlist) is ≤ the limit → exact method; above it → fast method.
- `never`: fast method always.
- Single videos are always probed and ignore this setting. Videos already in the archive are skipped before probing. **[guess]**, check the keep-pass/archive interaction.
- **The GUI must say the fast method is not equivalent**: it can't know which tracks exist, so it will not find a track like `en-US` when there is no plain `en`, and `L-orig` may pull in a dub caption.

**Fast-method rules** (derived from the language table; replaces the old "keep must equal embed"):
- Embed rows = the first 3 rows with Embed ticked, as exact codes. With auto subtitles on: only the first Embed row, as `L(-orig)?`. A missing track is **not backfilled** (blind, so it can't know which are missing): the 4th+ rows simply don't count. In the exact method they do count, because a failed row hands its slot to the next.
- Keep: yt-dlp's delete-after-embed (`no-keep-subs`) is all-or-nothing. So if **every embedded row is also Keep-ticked** the files stay; otherwise none are kept.
- Keep-only rows (Keep ticked, Embed not) can't be fetched in the same pass without being embedded too, so while embedding is on they are skipped, with a note. With embedding off, every Keep row is fetched (`L`, or `L(-orig)?` with auto on).

### 3.3 Classifier (new, shared, pure function)
Input: the info JSON track data. Output per video: original language; manual tracks; original ASR track; dub captions (every other `-orig`); translations (entries with `tlang`); placeholders. Port from the tester's `classify_auto` / `_orig_base`. It is the data-not-stdout step that GUI_PLAN Phase 2 needs anyway.

### 3.4 Resolver (per video, per row of the table, in order)
1. **Manual `L`**: exact, else `L-*` variants. Embed takes one, keep takes all.
2. **Only if auto is on**, and only when there is no manual track for `L`:
   a. `L` is the video's original language → `L-orig`. This is the only auto track that is a transcription, not a translation.
   b. otherwise → machine translation `L` (`tlang = L`; keeps the original's timing).
   c. otherwise, only when the exact code exists only as a dub caption (e.g. `de-DE`) → that dub caption.
   d. nothing → report "none" with the reason (e.g. "no speech-recognition track on this video").
3. An empty slot is backfilled: the next language takes it, so the embed cap counts real tracks only. Auto never adds a second track next to a manual one.

**Translations get no toggle** — agreed. Auto-generated subtitles on means "fill gaps with machine output"; for the original language that is a transcript, for any other language it is a translation. The control is a warning, shown in the setting's hint, in Preview and in the run log: *"Original language: en. fr and ar will be machine translations of the speech-recognition transcript."* Two things to keep honest:
- The warning must name the real source. Through yt-dlp it is always the ASR transcript, never the human track, and a video without ASR cannot be translated at all.
- Guard: translations are available for ~150 languages on any video that has ASR, so a long keep list could fan out into many requests (the old flood fear). Suggested: at most **3 machine-translated tracks per video** (a constant, not a setting) and `--sleep-subtitles` between subtitle downloads when more than one auto track is fetched. **[guess]**: number and sleep value to confirm.
- Dub captions are never part of the normal ladder (step 2c is the only exception).

### 3.5 Commands
Exact codes only, never regex. Main pass embeds; the keep pass handles keep ≠ embed — and because every video is resolved individually, it now works for playlist videos too (PIN 6: a playlist becomes N standalone videos, using the same start/max slice as today). `SUB_ASK_MISSING` stays (asks when auto is off and a fill exists); it is dormant in the GUI until Phase 2.

### 3.6 Tests (fixtures already exist)
The tracks JSONs are 453 real videos. Offline tests: classifier gives the right original language and dub-caption set on all of them; resolver output for a handful of table settings; plus the GUI_PLAN Phase 1 tests (gate matrix, hidden invariant).

### 3.7 Build order
1. Classifier + tests on the fixtures. 2. Resolver replaces the single-video path (compare with old output where the change is intended). 3. Per-video playlist loop + `TRACK_CHECK`. 4. Language table + `SUB_LANGUAGES` power-user override + migration (with PIN 3). 5. Translation warning + per-video translation cap. 6. Retire the tester.

### 3.8 Open decisions
`TRACK_CHECK_LIMIT` default; translation cap (3?); label wording; whether step 2c is worth keeping; whether audio mode should hide Embed too; whether fast mode should get a download-less keep pass for keep-only rows; whether the stability test (two probes of the same 30 videos) is run first; the `tlang`-on-manual-URL test; the `--load-info-json` test.

### 3.9 GUI behaviours (fast method, curtains)
**Decided**
- Advanced controls each live behind their **own curtain** (collapsed disclosure), not behind one master toggle. A curtain that holds a non-default value shows a badge ("custom") or opens itself, so an active override can never hide behind a closed curtain. Curtains today: comments "More control", the raw `SUB_LANGUAGES` regex. (A pure *display* switch like "expand all curtains" is fine later; a master that turns features on/off is not: it creates a second gating root and hidden behaviour.)

**Proposed**
- **One new soft state, "not in effect right now"**: the value is valid and stays editable, but the current settings ignore it. Amber (the theme's existing `warn` colour) plus a one-line reason on hover/under the table; no confirmation dialog. Distinct from DISABLED (a parent is off, cannot edit) and HIDDEN (mode-fact). It is the same family as GUI_PLAN's "overridden by URL" flavour: raw visible, engine ignoring it.
- In the fast method it applies to: Embed ticks beyond the first 3 (or beyond the first 1 with auto subtitles on); Keep ticks when not every embedded row is also Keep-ticked ("fast mode keeps embedded files only if every embedded language is also kept"); Keep-only rows while embedding is on.
- It is derivable from settings alone when `TRACK_CHECK = never`, or `auto` with a known `MAX_PLAYLIST_VIDEOS` ≤ / > the limit. With `auto` and unlimited videos it is unknown until the playlist is read (reactive layer), so show a neutral note ("applies if the fast method is used") instead of the tint.
- Build order: first ONE sentence above the table stating what the fast method will do with the current rows; the per-cell tint second. Both come from one pure function (settings → per-cell state + reason), ideally shared with the engine's log so GUI and engine cannot disagree (until PIN 3 lands: two copies and a test that they agree).

---

## Part 4 — The tester: retire or keep?
Retire it from *active work*, keep it on the shelf. The question it was built for is answered: classify, do not predict. Remaining reasons to keep the files (in a `tools/` folder, not deleted): it is the only thing that generates fresh fixtures, and the canary if YouTube changes its naming again. Retire for real after: the classifier is ported and passes on the fixtures, and (optional, ~5 min) the stability test is run.

## Part 5 — Don't-forget list (from the conversation)
- Manual first, always. ✔ 3.4
- Auto off: 3+ languages, a missing language's slot goes to the next one. ✔ 3.4 step 3
- Auto on: original language = transcript, others = translation, warn, no toggle. ✔ 3.4
- Original ASR is decent; dub captions are translation-in-spirit, not a tier. ✔
- One list, not two or three. ✔ 3.1
- Playlist = video setup if probing is cheap (it is) + a switch for bad internet. ✔ 3.2
- "No download" mode (video / audio / none): already **PIN 1** in `PINS.md`; embeds and the Embed column *disappear* there.
- `SUB_LANGUAGES` = raw regex override with a "don't blame us" note. ✔ 3.1b
- Post-URL processing: **PIN 6**. Subtitle rewrite: **PIN 7** (this document).
- Comments picker: **PIN 8** (sort shows in the default row too).
- `lrc` / audio ideas: PIN 4, unchanged.
