"""
broll_match.py — B-roll timestamp matcher (standalone, runs LOCALLY / in Cowork).

Takes filmed footage + the b-roll cue phrases from your script, finds where each
phrase was ACTUALLY delivered in the audio, and writes broll_placement.csv with
timestamps for CapCut.

Why fuzzy matching: you never deliver a line verbatim. The script says
"recovering too little", you say "recovering way too little" on camera. So this
matches on token similarity over a sliding window of the word-level transcript,
not exact text.

NOT part of the GitHub Actions pipeline — footage lives on your machine, so
this runs on your machine.

SETUP (one time):
    pip install faster-whisper

USAGE:
    # Cues straight from the keyword tool's snapshot (script.py output):
    python broll_match.py myshort.mp4 --snapshot data/latest.json --keyword "high protein snacks for weight loss"

    # Or cues from a text file (one phrase per line, optional "phrase | visual"):
    python broll_match.py myshort.mp4 --cues cues.txt

    # Output: broll_placement.csv next to the footage (override with --out)

DEFAULTS (first-version choices, tune later):
  - Whisper model "small" on CPU — good accuracy for a single close-mic voice;
    use --model turbo if you have the horsepower, or tiny for a quick pass.
  - Confidence >= 0.62  -> MATCHED
  - 0.45 - 0.62         -> LOW (timestamp given, eyeball it before trusting)
  - < 0.45              -> NOT_FOUND (phrase was probably cut or fully ad-libbed)
  - Multiple takes: best match wins; a runner-up within 0.05 that lands >5s away
    is listed in alt_candidates so you can check which take you kept.
"""

import argparse
import csv
import json
import logging
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("broll")

MATCH_THRESHOLD = 0.62
LOW_THRESHOLD = 0.55
ALT_SCORE_WINDOW = 0.05
ALT_MIN_GAP_SEC = 5.0

_WORD_RE = re.compile(r"[a-z0-9']+")

# Whisper writes numbers as digits; scripts often spell them out. Normalize the
# common small ones so "fifteen grams" matches "15 grams".
_NUM_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
    "eleven": "11", "twelve": "12", "thirteen": "13", "fourteen": "14",
    "fifteen": "15", "sixteen": "16", "seventeen": "17", "eighteen": "18",
    "nineteen": "19", "twenty": "20", "thirty": "30", "forty": "40",
    "fifty": "50", "sixty": "60", "seventy": "70", "eighty": "80", "ninety": "90",
}


def norm_tokens(text: str):
    toks = _WORD_RE.findall(text.lower())
    return [_NUM_WORDS.get(t, t) for t in toks]


STOPWORDS = {"the","a","an","to","of","in","on","at","for","and","or","but",
             "it","its","is","are","was","be","you","your","that","this","with",
             "so","just","not","do","does","did"}


def score_span(cue_toks, span_toks, idf=None) -> float:
    """Blend sequence similarity (order matters) with RARITY-WEIGHTED token
    overlap. Stray shared stopwords ("the", "snack" in a snack video) shouldn't
    make a cut line look half-present — only the distinctive words count."""
    if not cue_toks or not span_toks:
        return 0.0
    seq = SequenceMatcher(None, cue_toks, span_toks).ratio()
    idf = idf or {}
    cset, sset = set(cue_toks), set(span_toks)
    weights = {t: (0.15 if t in STOPWORDS else idf.get(t, 1.0)) for t in cset}
    total = sum(weights.values())
    hit = sum(w for t, w in weights.items() if t in sset)
    overlap = hit / total if total else 0.0
    return 0.7 * seq + 0.3 * overlap


def find_matches(cue: str, words: list) -> list:
    """words: [{"word": str, "start": float, "end": float}, ...]
    Returns candidate spans sorted best-first:
    [{"score", "start", "end", "text"}, ...]"""
    cue_toks = norm_tokens(cue)
    if not cue_toks:
        return []
    n = len(cue_toks)

    # Inverse-frequency weight per token across the whole transcript: a word
    # that appears constantly ("snack" in a snack video) is weak evidence; a
    # word that appears once ("spiral") is strong evidence.
    from collections import Counter
    all_toks = [t for w in words for t in norm_tokens(w["word"])]
    freq = Counter(all_toks)
    total_words = max(1, len(all_toks))
    idf = {t: min(1.0, 3.0 / max(1, freq.get(t, 0))) for t in cue_toks}
    # Delivery can stretch a phrase (filler words) or compress it. Window sizes
    # from just-under to +60% of the cue length cover both.
    sizes = sorted({max(1, n - 1), n, n + 1, max(2, int(n * 1.3)), max(2, int(n * 1.6))})

    word_toks = [norm_tokens(w["word"]) for w in words]
    flat = []          # (token, word_index)
    for i, toks in enumerate(word_toks):
        for t in toks:
            flat.append((t, i))

    candidates = []
    for size in sizes:
        if size > len(flat):
            continue
        for i in range(len(flat) - size + 1):
            span = flat[i:i + size]
            s = score_span(cue_toks, [t for t, _ in span], idf)
            if s < LOW_THRESHOLD:
                continue
            wi_start, wi_end = span[0][1], span[-1][1]
            candidates.append({
                "score": round(s, 3),
                "start": round(words[wi_start]["start"], 2),
                "end": round(words[wi_end]["end"], 2),
                "text": " ".join(words[k]["word"].strip() for k in range(wi_start, wi_end + 1)),
            })

    # Collapse overlapping candidates: keep the best in each time neighborhood.
    candidates.sort(key=lambda c: -c["score"])
    kept = []
    for c in candidates:
        if any(abs(c["start"] - k["start"]) < ALT_MIN_GAP_SEC for k in kept):
            continue
        kept.append(c)
        if len(kept) >= 4:
            break
    return kept


# Vertical-video pacing windows (retention research): a visual change every
# 1.5-2.0s is the plateau zone; gaps over 2.5s risk a retention cliff; gaps
# under 1.2s read as noise. B-roll inserts are only ONE kind of visual change
# (cuts, zooms, text overlays also count), so a long gap here is a PROMPT to
# add one of those in CapCut, not proof the edit is broken.
GAP_CLIFF = 2.5
GAP_OVERLOAD = 1.2


def pacing_report(rows, words):
    """Analyze the gaps between visual anchors: video start -> each matched
    b-roll -> end of speech. Flags stretches with no planned visual change."""
    marks = sorted(float(r["start_sec"]) for r in rows
                   if r["status"] in ("MATCHED", "LOW") and r["start_sec"] != "")
    if not marks:
        log.info("PACING: no matched cues to analyze.")
        return
    end_of_speech = max(w["end"] for w in words)
    points = [0.0] + marks + [end_of_speech]

    log.info("PACING (visual change target: every %.1f-2.0s; b-roll is one kind "
             "of change — fill flagged gaps with cuts/zooms/text in CapCut):",
             1.5)
    n_flags = 0
    for a, b_ in zip(points, points[1:]):
        gap = b_ - a
        if gap > GAP_CLIFF:
            n_flags += 1
            log.warning("  CLIFF RISK  %s -> %s  (%.1fs with no planned visual "
                        "change — add a cut, zoom, or text overlay in here)",
                        tc(a), tc(b_), gap)
        elif 0 < gap < GAP_OVERLOAD:
            n_flags += 1
            log.warning("  OVERLOAD    %s -> %s  (%.1fs between changes — "
                        "consider dropping one, likely the purpose=pacing cue)",
                        tc(a), tc(b_), gap)
    if not n_flags:
        log.info("  All gaps within the 1.2-2.5s window. Plateau-shaped edit.")


def tc(sec) -> str:
    if sec is None:
        return ""
    m, s = divmod(float(sec), 60)
    return f"{int(m)}:{s:04.1f}"


def load_cues(args) -> list:
    """Returns [{"cue": str, "visual": str}, ...]"""
    if args.snapshot:
        snap = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
        rec = snap.get("keywords", {}).get(args.keyword.strip().lower()) if args.keyword else None
        if not rec or not rec.get("script"):
            log.error("No script found in %s for keyword %r. Run script.py first, "
                      "or pass cues with --cues.", args.snapshot, args.keyword)
            sys.exit(1)
        cues = []
        for seg in rec["script"].get("segments", []):
            for c in seg.get("broll", []):
                if c.get("cue_phrase"):
                    cues.append({"cue": c["cue_phrase"], "visual": c.get("visual", ""),
                                 "purpose": c.get("purpose", "")})
        return cues

    if args.cues:
        cues = []
        for line in Path(args.cues).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            phrase, _, rest = line.partition("|")
            visual, _, purpose = rest.partition("|")
            cues.append({"cue": phrase.strip(), "visual": visual.strip(),
                         "purpose": purpose.strip()})
        return cues

    log.error("Provide cues via --snapshot ... --keyword ... or --cues FILE")
    sys.exit(1)


def transcribe(path: str, model_name: str) -> list:
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        log.error("faster-whisper is not installed. Run: pip install faster-whisper")
        sys.exit(1)

    log.info("Loading Whisper model %r (first run downloads it)...", model_name)
    model = WhisperModel(model_name, device="auto", compute_type="auto")
    log.info("Transcribing %s ...", path)
    segments, info = model.transcribe(path, word_timestamps=True, language="en",
                                      vad_filter=True)
    words = []
    for seg in segments:
        for w in seg.words or []:
            words.append({"word": w.word, "start": w.start, "end": w.end})
    log.info("Transcribed: %.1f min of audio, %d words",
             (info.duration or 0) / 60, len(words))
    if not words:
        log.error("No speech found in %s", path)
        sys.exit(1)
    return words


def main():
    ap = argparse.ArgumentParser(description="Match script b-roll cues to real timestamps in footage")
    ap.add_argument("footage", help="Video or audio file (anything ffmpeg reads)")
    ap.add_argument("--snapshot", help="Path to the keyword tool's data/latest.json")
    ap.add_argument("--keyword", help="Keyword whose script.py cues to use (with --snapshot)")
    ap.add_argument("--cues", help="Text file of cues: one per line, optional '| visual'")
    ap.add_argument("--model", default="small",
                    help="Whisper model: tiny/base/small/medium/turbo (default small)")
    ap.add_argument("--out", help="Output CSV path (default: broll_placement.csv next to footage)")
    ap.add_argument("--transcript-json", help="(testing) skip Whisper, load words from JSON")
    args = ap.parse_args()

    cues = load_cues(args)
    log.info("%d b-roll cue(s) to locate", len(cues))

    if args.transcript_json:
        words = json.loads(Path(args.transcript_json).read_text(encoding="utf-8"))
    else:
        words = transcribe(args.footage, args.model)

    out_path = Path(args.out) if args.out else Path(args.footage).with_name("broll_placement.csv")
    rows = []
    for c in cues:
        cands = find_matches(c["cue"], words)
        best = cands[0] if cands else None
        alts = [x for x in cands[1:]
                if best and best["score"] - x["score"] <= ALT_SCORE_WINDOW]

        if not best:
            status = "NOT_FOUND"
        elif best["score"] >= MATCH_THRESHOLD:
            status = "MATCHED"
        else:
            status = "LOW"

        rows.append({
            "cue_phrase": c["cue"],
            "visual": c["visual"],
            "purpose": c.get("purpose", ""),
            "status": status,
            "start_sec": best["start"] if best else "",
            "end_sec": best["end"] if best else "",
            "timecode": tc(best["start"]) if best else "",
            "matched_text": best["text"] if best else "",
            "confidence": best["score"] if best else "",
            "alt_candidates": "; ".join(f"{tc(a['start'])} ({a['score']})" for a in alts),
        })
        icon = {"MATCHED": "OK ", "LOW": "LOW", "NOT_FOUND": "-- "}[status]
        log.info("[%s] %-38s -> %s  %s", icon, c["cue"][:38],
                 tc(best["start"]) if best else "not found",
                 f"(conf {best['score']})" if best else "")

    pacing_report(rows, words)

    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    n_ok = sum(r["status"] == "MATCHED" for r in rows)
    n_low = sum(r["status"] == "LOW" for r in rows)
    n_miss = sum(r["status"] == "NOT_FOUND" for r in rows)
    log.info("Done: %d matched, %d low-confidence (eyeball them), %d not found -> %s",
             n_ok, n_low, n_miss, out_path)
    if n_miss:
        log.info("NOT_FOUND usually means the line was cut or heavily ad-libbed. "
                 "If you kept the b-roll idea, place it manually or add the phrase "
                 "you actually said to a --cues file and re-run.")


if __name__ == "__main__":
    main()
