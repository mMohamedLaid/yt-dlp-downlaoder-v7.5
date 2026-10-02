# GUI plan — v7 (updated)

Supersedes `D:\Downloads\gui_capability_gating_pin (2).md`, which stays where it is as a historical v6-era document (that file's PIN-5 list of stale claims is corrected here). Companion: `PINS.md` — deferred features; this file is the architecture they slot into. The two are **not** competing plans; see Part 4 for the exact intersections.

State of the world this plan assumes: v7 is a **single-preset proactive editor** (`yt_settings7.json` is the preset, `yt_settings_gui7.py` is its editor, `yt_video_downloader7.py` is the engine the Preview button dry-runs). Nothing here changes the "one preset" or "playlists never get per-video review" rulings — those are final until explicitly reopened.

---

## Part 1 — Where we are (as-built, differs from the v6 doc)

The proactive layer is **done**, and several v6-doc claims were superseded in the building:

| v6 doc said | v7 reality |
|---|---|
| "locked" state | `ST_DISABLED` + reason **kinds**: `REASON_SETTINGS` (live), `REASON_EXTERNAL` (dormant — the reserved reactive hook) |
| First-closed-gate: only the outermost closed gate shows a reason | **Nearest-cause-per-node**: every disabled field names its own closest unmet parent (`_status_of`) |
| `--sub-langs` regex resolves per video, no pre-scan needed | **Patterns removed engine-side** (a pattern can match several tracks of one language and crowd the embed slots; `all` would match 100+ translations). Exact codes only; playlist+auto embeds first language only |
| Two passes: embed pass (`--embed-subs`, no `--write-subs`) + write pass | One main pass with `--write-subs` + `--compat-options no-keep-subs` delete-after-embed; a separate keep pass runs **only for the keep≠embed difference, single videos only**, never with `--download-archive` |
| Audio mode: subtitle section collapses behind "Advanced: transcript file" | Structural **disable** of `EMBED_SUBTITLES` + `KEEP_SUBTITLE_FILE` stays live + mode hints. No extra disclosure level |
| Embed quota at the variant level | Cap is **3 tracks, one per language** (playlist: 3 codes; 1 language with auto on). Keep side is uncapped all-variants |
| `detected`/`treated` map onto raw/effective | They are **URL-fact / policy-decision**; the raw is the settings value. Mapping: detected → future capability input, setting → raw, treated → effective outcome |
| Playlist section: one master toggle, three cases | Per-**field** applicability (Part 2, matrix) — the handling toggle is inert even for *plain playlist* URLs |
| (absent) | Whole axes the v6 doc never had: **auto-subs** (fallback-only, `en-orig`, ask-missing, playlist restrictions), `PLAYLIST_START_INDEX` (+ "URL-derived start wins over setting"), **search** as a third visibility mechanism |

Shipped and correct: three states, raw-persists-through-hide (`HIDDEN_IN_MODE`), progressive disclosure (`CollapsibleSection`, collapsible sections, search), the N/M archive preview, per-playlist archive files, `pl_thumbnail:`/`pl_infojson:` routing, capability probe for single videos, `lrc` in `SUB_FORMAT`, the update-available notice.

## Part 2 — The model, corrected (the rules that govern from here)

1. **States:** `NORMAL` / `HIDDEN` (mode-fact, raw persists, not rendered, excluded from search) / `DISABLED` (rendered, greyed, reason shown).
2. **Reasons:** every disabled node reports its nearest cause; the cause *kind* (`SETTINGS` vs `EXTERNAL`) only changes phrasing (instruction vs fact), never rendering or cascade.
3. **Memory rule, restated for non-booleans** (the v6 wording only works for bools): *a key hidden by mode must never be read by the engine in that mode.* "Effective = raw AND parent" is just the boolean case of this. This is a cross-file contract and stays unguarded until the Part 3 tests exist.
4. **Playlist axis is a per-field matrix, not one gate:**

   | field | plain video | vid-in-pl → treat_as_video | vid-in-pl → start_* | plain playlist |
   |---|---|---|---|---|
   | `PLAYLIST_URL_HANDLING` | inert | **live (the decision)** | inert (already decided) | inert |
   | `PLAYLIST_START_INDEX` | inert | inert | live, **overridden by URL start** | live |
   | `MAX_PLAYLIST_VIDEOS` / `ENABLE_ARCHIVE` | inert | inert | live | live |

   "Overridden by URL" is a new flavor: raw visible, engine preferring URL data. The proactive GUI can't know URL context, so it shows all five fields; this matrix activates in the reactive layer.
5. **Compound gates need a new node kind.** Cross-root ANDs (e.g. the N/M preview = playlist-treatment AND archive) can't fall out of single-parent chains. When the reactive layer arrives, add a predicate-gate node whose reason aggregates its unmet inputs. Not needed while proactive.
6. **Search** is a third visibility mechanism: hidden rows are unsearchable; hits pull their ancestor chain so a greyed hit can be switched on in place.

## Part 3 — Roadmap

**Phase 0 — done.** Proactive single-preset editor + gating core + update notice + `lrc`.

**Phase 1 — proactive hardening (small, independent items; order flexible).** *Progress (v7.5): items 1, 2 and 3 complete.*
1. ✅ **DONE — `test_schema_wiring.py`.** **Tests** (the project had none): (a) gate-matrix — enumerate `DOWNLOAD_TYPE` × parent-chain states, assert `_status_of` outputs; (b) hidden-invariant — for every `HIDDEN_IN_MODE` key, in each hiding mode, `build_command`'s argv must be identical when that key's raw value is varied. These pin rules 3 and 4 above.
2. ✅ **DONE — `yt_schema7.5.py`.** **PIN 3 — one schema.** GUI `SCHEMA` and engine `.get()` fallbacks already drift on defaults (table in PINS.md). Single shared source before anything multiplies copies.
3. ✅ **DONE (v7.5) — `none` download type, shipped as the third `DOWNLOAD_TYPE` value; the mode axis is now frozen (video / audio / none).** Record in PINS.md. Do this *before* Phase 2: the mode axis should be final before reactive greys must compose with it, and it doubles as the stress test of the corrected model (first mode where HIDDEN and DISABLED heavily coexist).
4. **PIN 4 — lrc/audio integration** (audio-mode default or preset; sidecar→beside-audio relocation).
5. **PIN 2 — update button** (run `yt-dlp -U` behind a click; the notice already exists).
6. Cosmetic: `MAX_PLAYLIST_VIDEOS`/`ENABLE_ARCHIVE` sit under the SIDECAR section in the JSON but aren't gated by it — move them or document the nesting-is-grouping-only rule in a comment where the JSON is read.

**Phase 2 — the reactive layer (during-GUI).** Answer to "should we wait before proactive → reactive?": **yes, wait until these entry criteria hold** —
1. PIN 3 landed (a during-GUI would otherwise be a *third* schema copy) — **met in v7.5**;
2. PIN 1 decided (mode axis frozen) — **met in v7.5**;
3. **engine exposes data, not stdout**: `process_one_url` (or a sibling) must return `eff` + `sub_plan` + capabilities as structures. Preview currently scrapes printed text with a regex (`FULL_COMMAND_RE`) — fine for a text dialog, useless for widgets. This refactor is the real prerequisite and the largest single item.

Then reactive = **Preview, upgraded**: same probe, but capabilities come back as data and drive the UI —
- `REASON_EXTERNAL` wakes up: scanned video lacks chapters/subs → those fields grey with a fact-reason (mechanism already reserved; `_structural_disables` gains scan-derived entries);
- single videos get the **per-variant view**: real `en`/`en-GB`/`en-US` tracks, per-variant embed/keep, the 3-cap at variant level (as resolved today by `plan_subtitles` — the GUI would consume, not re-derive);
- playlist URLs surface the **N/M preview** and the Part 2 matrix becomes live (including "overridden by URL");
- `SUB_ASK_MISSING` becomes honorable: the engine's CLI auto-fallback prompt (`maybe_prompt_auto_fallback`) becomes a dialog in the GUI flow (it's currently unreachable from the GUI worker by design);
- the N/M compound gate becomes the first predicate-gate node (rule 5) — its planned first test, as the v6 doc intended.

**Phase 3 — explicitly not now:** multi-preset, per-video playlist review. Reopening either is a new plan.

## Part 4 — Relationship to PINS.md

| pin | vs. this plan |
|---|---|
| PIN 1 (no-download) | **Inside** Phase 1 — **done in v7.5**; the prerequisite it was for Phase 2 (mode axis frozen) is met |
| PIN 2 (auto-update) | Inside Phase 1; independent of the reactive layer |
| PIN 3 (schema unification) | Inside Phase 1 — **done in v7.5**; the hard prerequisite for Phase 2 is met |
| PIN 4 (lrc/audio) | Inside Phase 1; folds naturally into PIN 1's `none`-mode |
| PIN 5 (v6 doc refresh) | **Fulfilled by this file**; the Downloads doc is now historical |
