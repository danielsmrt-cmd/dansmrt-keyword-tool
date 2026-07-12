# Roadmap: Keyword Tool × Production Workflow

**The end state:** one loop — Plan → Script → Produce → Publish → Measure → Fix → Plan.

**Scope decision (2026-07-11):** YouTube only. Multi-platform copy generation is
explicitly OUT. Dan is concentrating on YouTube; IG/FB/TikTok copy stays in the
production workflow tools, not here.

---

## Stage 1 — Pure leverage ✅ COMPLETE
*Data already collected, zero risk, read-only scope, zero API cost.*

- [x] **1. collect / autocomplete / trends / score** — daily snapshot pipeline
- [x] **2. analyze.py** — Claude Fix-Panel analysis, change-detected
- [x] **3. insights.html** — Command Center dashboard
- [x] **4. What to Make Next card** — top keywords by Opportunity Score, each with a
  plain-English "why" line derived from the sub-scores (competition / demand /
  channel-fit / autocomplete depth) and a copy-ready brief.
- [x] **5. Publish Package card** — per keyword: best scored title, a YouTube
  description skeleton (keyword inside the first 25 words, 150+ words, chapters
  stub, engagement CTA with lurker on-ramp, coaching + subscribe links, hashtags),
  and a tag set that is greedy-filled against YouTube's real 500-character budget
  (8–15 tags). Live compliance chips make the `keyword_in_title: false` problem
  impossible by construction. Entirely client-side — no API spend, no quota.
- [x] **6. Retire the "plan next week with Gemini" step** — superseded by #4.

## Stage 2 — Script generation in-tool ✅ COMPLETE
*Absorbs the last of Gemini.*

- [x] **7. script.py** — on-demand (workflow checkbox, same spend-control pattern as
  titles.py). Keyword in → full YouTube script draft out. **Segment-based with a
  delivery direction per segment** (pace / emphasis / pause) — NOT a words-per-second
  budget, which is retired. D.E.S.S. framing, engagement-only CTA, B-roll cues
  anchored to spoken words. Grounded in the competitor titles and autocomplete
  suggestions the tool already caches. Runs behind the `run_script` workflow input.
  Every b-roll cue phrase is validated verbatim against its own segment's text;
  unanchored cues are flagged in the dashboard rather than shipped silently.
- [x] ~~**Platform copy generator**~~ — **REMOVED FROM SCOPE.** YouTube only.

## Stage 3 — Close the loop ✅ CODE COMPLETE (awaiting write-scope OAuth)
*Gated on: re-run oauth_setup.py with the `youtube` scope, update YT_REFRESH_TOKEN.*

- [x] **8. apply.py** — vidIQ-parity metadata editing, behind FOUR safety gates:
  (1) nothing writes without `--apply`; (2) `--apply` requires a NAMED video —
  there is no bulk apply; (3) freshness — refuses to apply a suggestion if the
  video changed since Claude analyzed it; (4) the full snippet is fetched and
  carried through, so categoryId/language are never blanked by omission.
  Description writes are OPT-IN and guarded: analyze.py clips every suggestion
  example at 200 chars, so description examples are truncated fragments —
  apply.py detects the clip ceiling and refuses rather than publishing a stub.
  Tag merges strip Claude's label prefixes, put new tags first, and respect both
  the 500-char and 15-tag ceilings. videos.update = 50 quota units.
- [x] **9. Drift detection** — daily cron compares each published video's live
  metadata against the baseline apply.py recorded. Zero extra API calls
  (collect.py already fetches live metadata). Tag REORDERING is ignored — only
  real changes surface. Drift is not an error; `drift.py --accept VID`
  re-baselines an intentional change. Surfaces as a red banner in the Fix Panel.

## Sidecar — B-roll timestamp matcher ✅ BUILT (standalone, runs locally)
*Not part of the Actions pipeline BY DESIGN: footage lives on Dan's machine.
Runs locally / in Cowork. `broll_match.py` in the repo root.*

- [x] Whisper (faster-whisper) word-level transcription of filmed footage.
- [x] Fuzzy phrase matching — sequence similarity + rarity-weighted token
  overlap (stopwords and video-common words barely count), spoken-number
  normalization ("fifteen" == "15"). Delivery never matches the script
  verbatim; this is built for paraphrase.
- [x] Cues pulled straight from script.py output (`--snapshot data/latest.json
  --keyword ...`) or a plain text file (`--cues`, one per line, `| visual`).
- [x] Confidence bands: >=0.62 MATCHED, 0.55-0.62 LOW (eyeball it), else
  NOT_FOUND (line was cut or fully ad-libbed — flagged, never guessed).
- [x] Multiple takes: best match wins, near-tied takes >5s away land in
  alt_candidates. Output: broll_placement.csv (CapCut-ready columns:
  cue_phrase, visual, status, start/end sec, timecode, matched_text,
  confidence, alt_candidates).
- Validated end-to-end with synthesized audio through Whisper tiny; use
  `--model small` (default) or `turbo` on real footage.
- Setup on Dan's machine: `pip install faster-whisper` (one time).

## Decisions log (2026-07-11 session)
- Script Generator v2 (standalone HTML): RETIRED. script.py absorbed the
  segment/delivery-direction engine. Remaining gap: script.py only takes
  tracked keywords — planned fix is a `--topic` flag for non-keyword content
  (Mortgage Effect, story-bank videos), NOT a separate tool. A thin
  segment-level editor UI stays on the shelf until real friction proves it out.
- Platform copy generator: REMOVED (YouTube-only focus).
- B-roll matcher: standalone (footage is local; Actions can't see it), reads
  script.py cues as input. Built this session.
- Current state: Stages 1-2 live and verified on the channel. Stage 3 code
  complete and exported, awaiting write-scope OAuth re-run + push. Stage 4 not
  started. Next session: push Stage 3, OAuth, first live apply on one video,
  then Stage 4 (retention-informed scoring + weekly digest), then `--topic`.

## Stage 4 — Performance feedback into planning
*Ongoing. The compounding stage — the system gets smarter every upload.*

- [ ] **10. Retention-informed scoring** — feed Dan's own avg-view-% by topic back
  into the channel-fit sub-score, so What to Make Next learns what *his* audience
  actually finishes, not just what searches well.
- [ ] **11. Weekly digest** — Monday summary card: last video's numbers, what
  changed, this week's top pick with its brief ready.

---

## Sequencing logic
Stage 1 was pure leverage (data already existed, zero risk). Stage 2 replaced Gemini
once Stage 1 proved the data quality. Stage 3 needs write access, so it comes
after trust is established. Stage 4 is what turns a tool into a system.

## Permanently manual (by design)
Filming, editing, thumbnails, Meta/Buffer scheduling. Judgment work and API swamps.

## Related standing decisions
- Claude analysis (analyze.py) is change-detected: daily runs cost ~nothing; only
  new/edited videos spend API budget.
- Titles (titles.py) and full re-analysis (force_analysis) stay on-demand behind
  workflow checkboxes to control spend.
- Read-only OAuth scope until Stage 3 explicitly upgrades it.
- Script pacing: no words-per-second budget. Delivery direction per segment.
