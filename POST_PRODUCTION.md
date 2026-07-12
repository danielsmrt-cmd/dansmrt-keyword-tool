# Post-Production Checklist — DanSmrtCoaching Shorts

From filmed footage to uploaded Short. Follow in order; the b-roll matcher
depends on Step 2 being done first.

Gear assumed: Galaxy S26 + Saramonic Air SE, editing in CapCut Pro, 9:16.

---

## 1. Import & rough cut (CapCut)

- [ ] Import the A-roll take you're keeping (best take, not first take)
- [ ] Trim the head: **speech starts on the very first frame** — no settle-in,
      no breath, no title card. First 3 seconds decide the Cliff.
- [ ] Cut dead air BUT keep deliberate pauses — every segment's delivery
      direction says where the pauses belong (the pause after a key claim is
      retention, not dead air; the 2 seconds of you glancing at notes is dead air)
- [ ] Remove flubbed takes, keep the final read of each segment

## 2. Audio cleanup — BEFORE running the b-roll matcher

Clean audio = better Whisper transcription = better timestamps. Do this first.

- [ ] **Noise reduction**: select the A-roll clip → Audio → Noise reduction ON
      (kills room hum / AC / street noise the Saramonic still picks up)
- [ ] **Normalize loudness**: Audio → Loudness normalization (or set clip volume
      so speech peaks land loud and consistent — aim for dialogue that never
      clips but never drops to a mumble)
- [ ] Listen once with eyes closed: any word you can't make out, the viewer
      can't either — and Whisper probably can't. Re-level or re-cut it.
- [ ] **Export a working cut now** (even rough) — this is the file the matcher
      transcribes

## 3. Run the b-roll matcher

```
python broll_match.py workingcut.mp4 --snapshot data\latest.json --keyword "your keyword"
```

(or `--cues cues.txt` for non-keyword videos — format: `phrase | visual | purpose`)

- [ ] Open `broll_placement.csv`
- [ ] **MATCHED** rows → place b-roll at those timecodes
- [ ] **LOW** rows → scrub to the timecode and verify by ear before trusting
- [ ] **NOT_FOUND** rows → the line was cut or ad-libbed; place manually or drop
- [ ] Read the **PACING warnings** in the terminal:
      - CLIFF RISK gaps (>2.5s with no visual change) → fill with a cut, punch-in
        zoom, or text overlay. It does NOT have to be b-roll.
      - OVERLOAD gaps (<1.2s) → drop the weakest insert (purpose=pacing first)

## 4. B-roll placement (CapCut)

- [ ] B-roll goes on the track ABOVE the A-roll; audio continuity is unbroken —
      the voice never cuts when the visual does
- [ ] Cut b-roll IN slightly before or exactly on the anchored word; cut OUT on
      a sentence end or beat
- [ ] Match b-roll color/brightness to your A-roll before any stylizing —
      generated clips (OpenArt/Seedance) usually run brighter and more
      saturated than phone footage; pull them toward your footage, not the
      other way around
- [ ] Horizontal b-roll in a vertical frame → blurred-background method:
      bottom track = same clip scaled to fit width + heavy blur;
      top track = clip scaled ~150-180%, action centered

## 5. Captions & text (safe zones)

Most Shorts are watched muted. Captions are not optional.

- [ ] Word-level auto-captions, one word at a time
- [ ] Font: heavy/bold (your brand: **Barlow Condensed Bold**; Anton or
      Archivo Black also read well at speed) — never thin fonts
- [ ] Caption height: **65-70% down from the top** of the frame
- [ ] Keep ALL text and critical action in the center 60% of the width:
      - right ~8% is blocked by like/comment/share
      - bottom ~18% is blocked by title/description
      - top ~12% is blocked by header UI
- [ ] Text overlays only where they add comprehension (numbers, labels, the
      key claim) — captions already carry the words
- [ ] Any CTA overlay sits ABOVE the 75% line

## 6. Sound design & music

- [ ] Music bed at **5-10% volume** — it should index the audio for discovery,
      not compete with your voice
- [ ] Optional but strong: a subtle whoosh on segment transitions, a soft thump
      when a number or key claim lands on screen. Targeted sound effects beat
      trending audio for retention.
- [ ] Final listen on phone speaker (not headphones) — that's where it'll be heard

## 7. Loop check

- [ ] Play the last 2 seconds straight into the first 2 seconds. The script's
      `loop_closure` line should make the seam near-invisible. If the ending
      visibly "ends," tighten the cut so it hands back to the hook.

## 8. Export & upload

- [ ] Export 9:16, highest quality CapCut allows
- [ ] **Rename the file before uploading** — not `VID_20260712.mp4` but the
      target keyword: `high-protein-snacks-for-weight-loss-over-40.mp4`
- [ ] Title/description/tags: copy from the **Publish Package card** (all five
      compliance chips green)
- [ ] Hashtags: the package's three — one broad, one niche, one brand
- [ ] After upload: the daily Action picks it up tomorrow; the Fix Panel will
      flag anything off, and drift detection guards it from there

---

*Pacing reference: a visual change every 1.5-2.0s is the plateau zone. Over
2.5s risks the Cliff; under 1.2s reads as noise. A "change" is any of: cut,
b-roll, zoom, text appearing, color shift, or sound hit.*
