"""
script.py — Claude-powered YouTube script generation (Stage 2).

Runs ONLY on demand (workflow_dispatch with run_script=true, or --keyword
locally), never in the daily cron — controls Anthropic API spend.

DESIGN NOTE — PACING
The old 1.1 words-per-second rule is RETIRED. This script does NOT write to a
word budget. It writes what the content demands, then attaches a DELIVERY
DIRECTION to each segment (pace / emphasis / pause). Runtime is ESTIMATED after
the fact, and the estimate is advisory only. If a script runs long, the fix is
to cut content — never to talk faster.

For a target keyword:
  1. Pull cached ranking titles + autocomplete suggestions from today's snapshot
     (no extra YouTube quota).
  2. One Claude API call → strict JSON: segments, each with its own delivery
     direction and word-anchored b-roll cues.
  3. Store under rec["script"] so insights.html can render and copy it.

YouTube only. No IG/FB/TikTok copy — that is out of scope by decision.
"""

import argparse
import json
import logging
import os
import re
import sys

import requests

import common

log = logging.getLogger("script")

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-sonnet-4-6"

# Advisory only — used to estimate runtime AFTER the script exists.
WPM_BY_PACE = {"slow": 110, "measured": 135, "fast": 165}

SYSTEM = (
    "You are a YouTube scriptwriter for DanSmrtCoaching: certified fitness and "
    "nutrition coaching for adults over 40.\n\n"
    "VOICE: warm, direct, science-backed, lightly dry. Never hype. Never a "
    "miracle claim. Dan's own story matters — he was overtraining, scaled back, "
    "slept better, stressed less, and his body composition improved FASTER. Use "
    "that mechanism when it genuinely fits; do not force it.\n\n"
    "FRAMEWORK: K.I.S.S. of D.E.S.S. — Keep It Stupid Simple across Diet, "
    "Exercise, Sleep, Stress. Find the real mechanical connection between "
    "pillars; never just list them.\n\n"
    "HARD RULES:\n"
    "- NEVER open with 'Hey guys', 'Welcome back', or any greeting. Start on the "
    "hook.\n"
    "- The CTA is ENGAGEMENT-driven: a specific question answerable in under five "
    "words, confession or commitment style, plus a lurker on-ramp (a one-word "
    "reply option). NO lead magnets. No 'link in bio'. No hard coaching pitch.\n"
    "- Do NOT write to a word count or a words-per-second budget. Write what the "
    "content demands.\n"
    "- Every segment carries a DELIVERY DIRECTION, not a word target. Direction "
    "logic: new or complex information = slow, with a pause after the key claim; "
    "familiar or relatable information = fast; the hook = whatever lands it, but "
    "clarity wins in the first 3 seconds; a punchline or reveal = fast into it, "
    "then PAUSE on it; the CTA = clear and unhurried.\n"
    "- B-roll cues anchor to a SPECIFIC SPOKEN PHRASE that appears verbatim in "
    "that segment's script text. Never to a timestamp. B-roll style is 'witty "
    "grimace energy' — theatrical, slightly comedic, impossible physics preferred "
    "over literal or clinical visuals.\n\n"
    "Respond ONLY with a JSON object. No prose, no markdown fences."
)

PROMPT_TEMPLATE = """Target keyword: "{keyword}"
Format: {fmt}
Soft duration target: {duration} (SOFT — cut content to hit it, never talk faster)
Working title: {title}

What is currently ranking for this keyword on YouTube:
{ranking_titles}

What people actually type (autocomplete):
{suggestions}

Write the script.

Return STRICT JSON only, exactly this shape:
{{
  "working_title": "...",
  "premise": "one sentence: the single mechanical idea this video delivers",
  "dess_pillars": ["Diet"],
  "segments": [
    {{
      "name": "Hook",
      "pace": "slow|measured|fast",
      "direction": "the delivery direction in one sentence — pace, emphasis, where to pause",
      "script": "the actual spoken words",
      "broll": [
        {{"cue_phrase": "verbatim phrase from THIS segment's script", "visual": "what to show"}}
      ]
    }}
  ],
  "cta": {{
    "question": "specific, answerable in under five words",
    "lurker_onramp": "one-word reply option, e.g. 'Just reply SLEEP or STRESS'"
  }}
}}

Segment names for a {fmt}: {segment_hint}
The FINAL segment must be the CTA, delivered clear and unhurried."""

SEGMENT_HINTS = {
    "short": "Hook, Turn, Mechanism, Payoff, CTA",
    "long": "Hook, Stakes, What Changed, Mechanism, The Fix, Where To Start, CTA",
}
DEFAULT_DURATION = {"short": "45–60 seconds", "long": "8–12 minutes"}


def call_claude(prompt: str):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        log.error("ANTHROPIC_API_KEY not set")
        return None

    def call():
        r = requests.post(
            ANTHROPIC_URL,
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": MODEL,
                "max_tokens": 4000,
                "system": SYSTEM,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=120,
        )
        r.raise_for_status()
        return r.json()

    resp, err = common.retry_call(call, label="anthropic script")
    if err:
        return None
    text = "".join(b.get("text", "") for b in resp.get("content", [])
                   if b.get("type") == "text")
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError) as e:
        log.error("Could not parse Claude JSON (%s): %s", e, text[:200])
        return None
    if not isinstance(data, dict) or not isinstance(data.get("segments"), list):
        log.error("Unexpected script shape: %s", str(data)[:200])
        return None
    return data


def validate_and_estimate(script: dict) -> dict:
    """Estimate runtime AFTER the script exists (advisory), and verify that every
    b-roll cue phrase actually appears verbatim in its own segment's script."""
    total_sec = 0.0
    orphaned = []
    clean_segments = []

    for seg in script.get("segments", []):
        if not isinstance(seg, dict):
            continue
        body = str(seg.get("script", "")).strip()
        pace = str(seg.get("pace", "measured")).lower()
        if pace not in WPM_BY_PACE:
            pace = "measured"
        n_words = len(body.split())
        seg_sec = (n_words / WPM_BY_PACE[pace]) * 60 if n_words else 0
        total_sec += seg_sec

        cues = []
        for c in seg.get("broll", []) or []:
            if not isinstance(c, dict):
                continue
            phrase = str(c.get("cue_phrase", "")).strip()
            anchored = bool(phrase) and phrase.lower() in body.lower()
            if not anchored and phrase:
                orphaned.append(f"{seg.get('name', '?')}: {phrase}")
            cues.append({
                "cue_phrase": phrase,
                "visual": str(c.get("visual", ""))[:300],
                "anchored": anchored,
            })

        clean_segments.append({
            "name": str(seg.get("name", "Segment"))[:60],
            "pace": pace,
            "direction": str(seg.get("direction", ""))[:300],
            "script": body,
            "broll": cues,
            "words": n_words,
            "est_sec": round(seg_sec, 1),
        })

    script["segments"] = clean_segments
    script["est_runtime_sec"] = round(total_sec)
    script["orphaned_cues"] = orphaned
    script["generated_at"] = common.utc_now_iso()

    if orphaned:
        log.warning("%d b-roll cue(s) not found verbatim in their segment: %s",
                    len(orphaned), "; ".join(orphaned[:3]))
    return script


def generate_for_keyword(snap, kw: str, fmt: str, duration: str) -> bool:
    rec = snap["keywords"].get(kw)
    if not rec:
        log.warning("'%s' not in latest snapshot — run collect first", kw)
        return False

    ranking = [v["title"] for v in rec.get("top_videos", []) if v.get("title")][:15]
    suggestions = (rec.get("suggestions") or [])[:15]
    titles = rec.get("titles") or []
    title = titles[0]["title"] if titles else f"(none generated — write one for '{kw}')"

    prompt = PROMPT_TEMPLATE.format(
        keyword=kw,
        fmt=fmt,
        duration=duration or DEFAULT_DURATION[fmt],
        title=title,
        ranking_titles="\n".join(f"- {t}" for t in ranking) or "- (none cached)",
        suggestions="\n".join(f"- {s}" for s in suggestions) or "- (none cached)",
        segment_hint=SEGMENT_HINTS[fmt],
    )

    script = call_claude(prompt)
    if not script:
        return False

    script["format"] = fmt
    script = validate_and_estimate(script)
    rec["script"] = script

    mins, secs = divmod(script["est_runtime_sec"], 60)
    log.info("'%s' → %d segments, est %d:%02d (advisory), %d orphaned cue(s)",
             kw, len(script["segments"]), mins, secs, len(script["orphaned_cues"]))
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keyword", help="Keyword to script (default: top by Opportunity Score)")
    ap.add_argument("--format", choices=["short", "long"], default="short")
    ap.add_argument("--duration", default="", help="Soft target, e.g. '45-60 seconds'")
    args = ap.parse_args()

    snap = common.load_snapshot()
    if not snap["keywords"]:
        log.error("No snapshot data — run collect/score first")
        sys.exit(1)

    if args.keyword:
        kw = args.keyword.strip().lower()
    else:
        scored = [(r["keyword"], (r.get("scores") or {}).get("composite", 0))
                  for r in snap["keywords"].values()]
        scored.sort(key=lambda t: -t[1])
        kw = scored[0][0]
        log.info("No --keyword given; using top opportunity: '%s'", kw)

    ok = generate_for_keyword(snap, kw, args.format, args.duration)
    common.save_snapshot(snap)
    log.info("script.py done")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
