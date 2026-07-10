"""
analyze.py — Claude-powered per-video optimization suggestions (the "Fix Panel"
brain). Goes beyond seo.py's rule scores: sends each video to Claude and gets
back a prioritized, human-readable list of concrete fixes (title rewrites,
description edits, tag additions, hook/retention notes).

CHANGE DETECTION (this is what makes it cheap enough for the daily cron):
  Each video's title + description + tags are hashed. Claude is only called
  when that hash differs from the last analysis, or the video has never been
  analyzed, or --force is passed. Unchanged videos reuse cached suggestions at
  zero cost. So a normal morning run costs ~nothing; it only spends API budget
  the day you publish or edit a video.

Cached under cache["own_channel"]["analysis"][video_id] = {
    "content_hash": ..., "suggestions": [...], "summary": ...,
    "analyzed_at": ...
}
and mirrored into the snapshot's own_channel.videos[vid]["analysis"] for the
dashboard.

Runs in GitHub Actions (has ANTHROPIC_API_KEY). Safe to run locally too.
"""

import argparse
import hashlib
import json
import logging
import os
import re
import sys

import requests

import common

log = logging.getLogger("analyze")

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-sonnet-4-6"

SYSTEM = (
    "You are a YouTube optimization strategist for DanSmrtCoaching: fitness "
    "and nutrition coaching for adults over 40. Voice: anti-hype, plain-spoken, "
    "grounded in the K.I.S.S. of D.E.S.S. framework (Keep It Stupid Simple: "
    "Diet, Exercise, Sleep, Stress). You give specific, actionable optimization "
    "advice — never vague platitudes. Every suggestion names the exact change "
    "to make. Respond ONLY with a JSON object, no prose, no markdown fences."
)

PROMPT_TEMPLATE = """Analyze this YouTube video for search + retention optimization.

TARGET KEYWORD (what this video should rank for): "{keyword}"

CURRENT VIDEO:
- Title ({title_len} chars): {title}
- Description ({desc_words} words): {description}
- Tags ({tag_count}): {tags}

RULE-BASED SEO FLAGS (already computed):
{seo_flags}
{analytics_block}
Return a STRICT JSON object, exactly this shape:
{{
  "summary": "one-sentence overall assessment",
  "priority": "high|medium|low",
  "suggestions": [
    {{
      "area": "title|description|tags|hook|thumbnail",
      "severity": "high|medium|low",
      "issue": "what's wrong, specific",
      "fix": "the exact change to make",
      "example": "concrete rewritten text if applicable, else null"
    }}
  ]
}}

Rules:
- Order suggestions by severity (high first). 3-6 suggestions.
- If the title is missing the target keyword, that's a high-severity title fix; provide a rewritten title <=60 chars that works the keyword in naturally.
- If retention data is present and avg view % is below 40, add a hook suggestion about the first 3 seconds.
- Keep every "fix" and "example" true to Dan's anti-hype, over-40 voice."""


def content_hash(video: dict) -> str:
    basis = json.dumps({
        "title": video.get("title", ""),
        "description": video.get("description", ""),
        "tags": video.get("tags", []),
    }, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def format_seo_flags(seo: dict) -> str:
    if not seo or seo.get("composite") is None:
        return "- No rule-based SEO score (no matched keyword)."
    t, d, g = seo.get("title", {}), seo.get("description", {}), seo.get("tags", {})
    return "\n".join([
        f"- SEO composite: {seo.get('composite')}/100",
        f"- Keyword in title: {t.get('keyword_in_title')}; title length: {t.get('length')} chars (sweet spot 40-70)",
        f"- Keyword in first 25 words of description: {d.get('keyword_in_first_25_words')}; description word count: {d.get('word_count')} (aim 150+)",
        f"- Tag count: {g.get('tag_count')} (aim 8-15); keyword in a tag: {g.get('keyword_in_a_tag')}",
    ])


def format_analytics_block(analytics: dict) -> str:
    if not analytics:
        return ""
    return (
        "\nRETENTION DATA (last 90 days):\n"
        f"- Avg view %: {analytics.get('avg_view_percentage')}\n"
        f"- Avg view duration: {analytics.get('avg_view_duration_sec')}s\n"
        f"- Subscribers gained/lost: +{analytics.get('subscribers_gained')}/"
        f"-{analytics.get('subscribers_lost')}\n"
    )


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
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict) and isinstance(data.get("suggestions"), list):
            # light clamping / normalization
            data["priority"] = str(data.get("priority", "medium")).lower()
            clean = []
            for s in data["suggestions"][:8]:
                if not isinstance(s, dict):
                    continue
                clean.append({
                    "area": str(s.get("area", "general"))[:20],
                    "severity": str(s.get("severity", "medium")).lower()[:10],
                    "issue": str(s.get("issue", ""))[:400],
                    "fix": str(s.get("fix", ""))[:400],
                    "example": (str(s["example"])[:200]
                                if s.get("example") not in (None, "", "null") else None),
                })
            data["suggestions"] = clean
            data["summary"] = str(data.get("summary", ""))[:400]
            return data
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        log.error("Could not parse Claude JSON (%s): %s", e, text[:200])
    return None


def analyze_video(cache, snap, vid, force=False):
    video = cache.get("videos", {}).get(vid)
    if not video:
        return "skipped"
    own = cache.setdefault("own_channel", {})
    analysis_cache = own.setdefault("analysis", {})
    seo = own.get("seo", {}).get(vid, {})
    analytics = own.get("analytics", {}).get(vid)

    h = content_hash(video)
    prev = analysis_cache.get(vid)
    if prev and prev.get("content_hash") == h and not force:
        # Unchanged — reuse, mirror into snapshot, no API call.
        common.own_video_record(snap, vid)["analysis"] = prev
        return "cached"

    keyword = seo.get("matched_keyword") or "over-40 fitness (no matched keyword)"
    prompt = PROMPT_TEMPLATE.format(
        keyword=keyword,
        title=video.get("title", ""),
        title_len=len(video.get("title", "")),
        description=(video.get("description", "") or "")[:1500],
        desc_words=len((video.get("description", "") or "").split()),
        tags=", ".join(video.get("tags", [])) or "(none)",
        tag_count=len(video.get("tags", [])),
        seo_flags=format_seo_flags(seo),
        analytics_block=format_analytics_block(analytics),
    )
    result = call_claude(prompt)
    if not result:
        return "failed"

    entry = {
        "content_hash": h,
        "summary": result["summary"],
        "priority": result["priority"],
        "suggestions": result["suggestions"],
        "analyzed_at": common.utc_now_iso(),
        "matched_keyword": keyword,
    }
    analysis_cache[vid] = entry
    common.own_video_record(snap, vid)["analysis"] = entry
    log.info("'%s' analyzed → %d suggestions (%s priority)",
             video.get("title", vid)[:40], len(entry["suggestions"]), entry["priority"])
    return "analyzed"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true",
                    help="Re-analyze every video even if unchanged")
    ap.add_argument("--video", help="Analyze only this video_id")
    ap.add_argument("--max", type=int, default=25,
                    help="Safety cap on API calls per run")
    args = ap.parse_args()

    cache = common.load_cache()
    snap = common.load_snapshot()
    own = cache.get("own_channel", {})
    video_ids = own.get("video_ids", [])
    if not video_ids:
        log.warning("No own-channel videos cached — run collect.py first")
        return

    if args.video:
        video_ids = [args.video]

    counts = {"analyzed": 0, "cached": 0, "failed": 0, "skipped": 0}
    api_calls = 0
    for vid in video_ids:
        if api_calls >= args.max:
            log.warning("Hit --max API-call cap (%d); deferring the rest", args.max)
            break
        outcome = analyze_video(cache, snap, vid, force=args.force)
        counts[outcome] = counts.get(outcome, 0) + 1
        if outcome in ("analyzed", "failed"):
            api_calls += 1

    common.save_cache(cache)
    common.save_snapshot(snap)
    log.info("analyze.py done — analyzed %d, reused %d cached, %d failed, %d skipped "
             "(%d API calls)",
             counts["analyzed"], counts["cached"], counts["failed"],
             counts["skipped"], api_calls)
    sys.exit(0)


if __name__ == "__main__":
    main()
