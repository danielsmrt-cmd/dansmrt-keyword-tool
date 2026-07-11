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

## Stage 3 — Close the loop
*Requires write-scope OAuth (`youtube` scope; re-run oauth_setup.py, update
YT_REFRESH_TOKEN). ~1 session. Comes after trust is established.*

- [ ] **8. apply.py** — vidIQ-parity metadata editing. Reads Fix Panel suggestions,
  shows old → new diff, applies ONLY what Dan approves (workflow_dispatch input,
  per video). **Never auto-applies.** videos.update = 50 quota units per video.
- [ ] **9. Drift detection** — daily cron compares each published video's live
  title/desc/tags against its Publish Package; mismatches surface in the Fix Panel.

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
