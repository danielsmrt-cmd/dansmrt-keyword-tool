"""
digest.py — Stage 4, item 11. Weekly digest builder.

Writes a `weekly_digest` block into today's snapshot summarizing:
  1. LAST VIDEO — the most recent upload's retention/views/watch-time vs the
     channel average (so Dan sees how the newest video actually did).
  2. WHAT CHANGED — week-over-week opportunity-score movement per keyword,
     compared against the nearest snapshot ~7 days back (biggest risers/fallers).
  3. TOP PICK — this week's #1 keyword by composite score, with the pre-scored
     best title so the next-video decision is one glance.

Reads cached/snapshot data only. Zero API calls. Safe to run daily; the
dashboard shows it as the "Weekly" tab and it's most useful on Mondays.

Design note: the diff needs history. It looks back up to LOOKBACK_DAYS and uses
the OLDEST snapshot within that window (so early on, before a full week of
history exists, it still produces a directional diff instead of nothing).
"""

import logging
from datetime import date, datetime, timedelta

import common

log = logging.getLogger("digest")

LOOKBACK_DAYS = 7


def _parse_date(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def find_prior_snapshot(today):
    """Nearest snapshot at least 1 day old, within LOOKBACK_DAYS. Returns
    (snapshot_dict, its_date) or (None, None)."""
    if not common.SNAPSHOT_DIR.exists():
        return None, None
    candidates = []
    for p in common.SNAPSHOT_DIR.glob("*.json"):
        d = _parse_date(p.stem)
        if d and d < today and (today - d).days <= LOOKBACK_DAYS:
            candidates.append((d, p))
    if not candidates:
        return None, None
    # oldest within the window = closest to a true week-over-week compare
    candidates.sort()
    d, p = candidates[0]
    return common.load_json(p, default=None), d


def channel_average_retention(videos):
    vals = []
    for v in videos.values():
        a = v.get("analytics") or {}
        r = a.get("retention_clamped")
        if r is None:
            raw = a.get("avg_view_percentage")
            r = min(raw, 100.0) if raw is not None else None
        if r is not None:
            vals.append(r)
    return round(sum(vals) / len(vals), 1) if vals else None


def most_recent_video(videos):
    """Best-effort 'newest' pick: prefer published_at if present, else the
    video with the smallest window/highest recency signal we have."""
    best, best_key = None, None
    for vid, v in videos.items():
        pub = v.get("published_at") or ""
        key = pub  # ISO strings sort correctly
        if best_key is None or key > best_key:
            best, best_key = (vid, v), key
    return best


def build_last_video(videos):
    mv = most_recent_video(videos)
    if not mv:
        return None
    vid, v = mv
    a = v.get("analytics") or {}
    ret = a.get("retention_clamped")
    if ret is None and a.get("avg_view_percentage") is not None:
        ret = min(a["avg_view_percentage"], 100.0)
    ch_avg = channel_average_retention(videos)
    delta = None if (ret is None or ch_avg is None) else round(ret - ch_avg, 1)
    return {
        "video_id": vid,
        "title": v.get("title", ""),
        "retention": ret,
        "channel_avg_retention": ch_avg,
        "retention_vs_avg": delta,
        "views_window": a.get("views_window"),
        "estimated_minutes_watched": a.get("estimated_minutes_watched"),
        "looped": a.get("looped", False),
    }


def build_what_changed(today_kws, prior_kws):
    if not prior_kws:
        return {"available": False, "risers": [], "fallers": []}
    moves = []
    for kw, rec in today_kws.items():
        now = (rec.get("scores") or {}).get("composite")
        was = (prior_kws.get(kw, {}).get("scores") or {}).get("composite")
        if now is None or was is None:
            continue
        moves.append({"keyword": kw, "now": now, "was": was, "delta": round(now - was, 1)})
    risers = sorted([m for m in moves if m["delta"] > 0], key=lambda m: -m["delta"])[:3]
    fallers = sorted([m for m in moves if m["delta"] < 0], key=lambda m: m["delta"])[:3]
    return {"available": True, "risers": risers, "fallers": fallers}


def build_top_pick(today_kws):
    ranked = sorted(
        (r for r in today_kws.values() if (r.get("scores") or {}).get("composite") is not None),
        key=lambda r: r["scores"]["composite"], reverse=True,
    )
    if not ranked:
        return None
    top = ranked[0]
    titles = sorted(top.get("titles", []), key=lambda t: t.get("score", 0), reverse=True)
    best = titles[0] if titles else None
    return {
        "keyword": top["keyword"],
        "score": top["scores"]["composite"],
        "best_title": best.get("title") if best else None,
        "best_title_score": best.get("score") if best else None,
        "retention_signal": (top.get("scores") or {}).get("retention_signal"),
    }


def main():
    snap = common.load_snapshot()
    videos = snap.get("own_channel", {}).get("videos", {})
    today = _parse_date(snap.get("date")) or date.today()

    prior, prior_date = find_prior_snapshot(today)
    prior_kws = (prior or {}).get("keywords", {}) if prior else {}

    digest = {
        "generated_at": common.utc_now_iso(),
        "compared_against": prior_date.isoformat() if prior_date else None,
        "last_video": build_last_video(videos),
        "what_changed": build_what_changed(snap.get("keywords", {}), prior_kws),
        "top_pick": build_top_pick(snap.get("keywords", {})),
    }
    snap["weekly_digest"] = digest
    common.save_snapshot(snap)

    lv = digest["last_video"]
    tp = digest["top_pick"]
    log.info("digest.py done — last video %s, top pick %s (vs %s)",
             (lv or {}).get("title", "n/a")[:30],
             (tp or {}).get("keyword", "n/a"),
             digest["compared_against"] or "no prior snapshot")


if __name__ == "__main__":
    main()
