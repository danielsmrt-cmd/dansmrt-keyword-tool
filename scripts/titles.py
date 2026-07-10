"""
titles.py — Claude-powered title generation + scoring (replaces vidiq_generate_titles).

Runs ONLY on demand (workflow_dispatch with run_titles=true, or per-keyword via
--keyword), never in the daily cron — controls Anthropic API spend.

For each target keyword:
  1. Pull the top-20 ranking titles from today's snapshot (cached data — no
     extra YouTube quota).
  2. One Claude API call (claude-sonnet-4-6) asking for 10 candidates as
     strict JSON: [{"title","score","rationale"}]. Score criteria: pattern
     match with ranking winners, curiosity gap, clarity, ≤60 chars.
  3. Store candidates in the snapshot under rec["titles"].
"""

import argparse
import json
import logging
import os
import re
import sys

import requests

import common

log = logging.getLogger("titles")

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-sonnet-4-6"

SYSTEM = (
    "You are a YouTube title strategist for DanSmrtCoaching: fitness and "
    "nutrition coaching for adults over 40. Voice: anti-hype, plain-spoken, "
    "grounded in the K.I.S.S. of D.E.S.S. framework (Keep It Stupid Simple: "
    "Diet, Exercise, Sleep, Stress). No miracle claims, no clickbait lies — "
    "but strong curiosity gaps and specificity are good. "
    "Respond ONLY with a JSON array, no prose, no markdown fences."
)

PROMPT_TEMPLATE = """Keyword: "{keyword}"

Top-ranking YouTube titles for this keyword right now:
{ranking_titles}

Generate 10 title candidates for a video by Dan targeting this keyword and an
over-40 audience. For each, score 0-100 based on: pattern-match with the
winners above, curiosity gap, clarity, and length <= 60 characters (hard
requirement — reject/rewrite anything longer).

Return STRICT JSON only, exactly this shape:
[{{"title": "...", "score": 0, "rationale": "..."}}]"""


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
                "max_tokens": 2000,
                "system": SYSTEM,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=60,
        )
        r.raise_for_status()
        return r.json()

    resp, err = common.retry_call(call, label="anthropic messages")
    if err:
        return None
    text = "".join(b.get("text", "") for b in resp.get("content", [])
                   if b.get("type") == "text")
    # Strip markdown fences defensively even though we asked for none.
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        data = json.loads(text)
        if isinstance(data, list):
            out = []
            for item in data:
                if isinstance(item, dict) and item.get("title"):
                    out.append({
                        "title": str(item["title"])[:80],
                        "score": max(0, min(100, int(item.get("score", 0)))),
                        "rationale": str(item.get("rationale", ""))[:300],
                    })
            return out or None
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        log.error("Could not parse Claude JSON (%s): %s", e, text[:200])
    return None


def generate_for_keyword(snap, kw: str) -> bool:
    rec = snap["keywords"].get(kw)
    if not rec:
        log.warning("'%s' not in latest snapshot — run collect first", kw)
        return False
    ranking = [v["title"] for v in rec.get("top_videos", []) if v.get("title")][:20]
    if not ranking:
        log.warning("'%s' has no ranking titles cached — skipping", kw)
        return False
    prompt = PROMPT_TEMPLATE.format(
        keyword=kw,
        ranking_titles="\n".join(f"- {t}" for t in ranking),
    )
    titles = call_claude(prompt)
    if titles:
        titles.sort(key=lambda t: -t["score"])
        rec["titles"] = titles
        rec["titles_generated_at"] = common.utc_now_iso()
        log.info("'%s' → %d title candidates (best: %s)", kw, len(titles),
                 titles[0]["title"])
        return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keyword", help="Generate for one keyword only")
    ap.add_argument("--top", type=int, default=5,
                    help="Otherwise: generate for the top-N keywords by Opportunity Score")
    args = ap.parse_args()

    snap = common.load_snapshot()
    if not snap["keywords"]:
        log.error("No snapshot data — run collect/score first")
        sys.exit(1)

    if args.keyword:
        targets = [args.keyword.strip().lower()]
    else:
        scored = [(r["keyword"], (r.get("scores") or {}).get("composite", 0))
                  for r in snap["keywords"].values()]
        scored.sort(key=lambda t: -t[1])
        targets = [k for k, _ in scored[:args.top]]

    any_ok = False
    for kw in targets:
        any_ok = generate_for_keyword(snap, kw) or any_ok

    common.save_snapshot(snap)
    log.info("titles.py done")
    sys.exit(0 if any_ok else 1)


if __name__ == "__main__":
    main()
