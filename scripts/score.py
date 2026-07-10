"""
score.py — Opportunity Score (0–100, directional, NOT a vidIQ clone of math).

Composite =  Competition (40%, inverted)
           + Demand proxy (25%)
           + Momentum (15%)          <- if trends null: weight redistributed
           + Channel-fit (20%)          (Competition 50%, Demand 30%)

All sub-scores are stored alongside the composite so Dan can recalibrate the
weights after logging vidIQ scores side-by-side (see calibration.md).

============================ THE MATH, EXPLAINED ============================

COMPETITION (inverted — high competition = low contribution)
  Signals from the top-20 ranking videos for the keyword:
    a) median views of ranking videos      — huge views = saturated SERP
    b) median subs of ranking channels     — huge channels = hard to displace
    c) freshness share                     — % of results < 12 months old
  a) and b) are squashed with log10 and mapped onto 0..1 against fixed
  anchors (views: 1k..10M, subs: 1k..5M — log-linear between anchors).
  Freshness pulls competition DOWN slightly when the SERP is stale (stale
  results = YouTube has nothing new to rank = opening for new content).
  BONUS: if any small channel (<100k subs) ranks with high views (>100k),
  that's direct proof a small player can win → competition score is reduced
  by up to 15 points.

DEMAND (proxy — we have no real search-volume data)
    a) autocomplete_depth normalized against the max depth IN THIS BATCH
       (relative signal: "people type many variations of this")
    b) total view volume of the top-20 results (log-squashed, 10k..100M)

MOMENTUM
    trends slope in [-1, 1] mapped linearly to 0..100 (0 slope = 50).
    If null → weight redistributed as documented above.

CHANNEL-FIT
    Jaccard similarity between the keyword's token set (keyword + its
    autocomplete suggestions) and the token set of Dan's top-20 performing
    videos (titles + tags). Simple, transparent, tunable.
=============================================================================
"""

import logging
import math
import re

import common

log = logging.getLogger("score")

# ---- Tunable weights (see calibration.md) ----------------------------------
W_COMPETITION = 0.40
W_DEMAND = 0.25
W_MOMENTUM = 0.15
W_CHANNEL_FIT = 0.20
# When momentum is null:
W_COMPETITION_NO_TREND = 0.50
W_DEMAND_NO_TREND = 0.30
# -----------------------------------------------------------------------------

STOPWORDS = {"the", "a", "an", "for", "of", "to", "in", "on", "and", "or",
             "is", "are", "your", "you", "my", "how", "what", "why", "with"}


def log_scale(value, lo, hi):
    """Map value onto 0..1 log-linearly between anchors lo..hi (clamped)."""
    if value <= 0:
        return 0.0
    x = math.log10(value)
    a, b = math.log10(lo), math.log10(hi)
    return max(0.0, min(1.0, (x - a) / (b - a)))


def median(values):
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    n = len(vals)
    mid = n // 2
    return vals[mid] if n % 2 else (vals[mid - 1] + vals[mid]) / 2


def tokenize(text: str) -> set:
    return {t for t in re.findall(r"[a-z0-9]+", (text or "").lower())
            if t not in STOPWORDS and len(t) > 1}


def competition_score(top_videos) -> float:
    """0 = wide open, 100 = brutally saturated. (Inverted later.)"""
    if not top_videos:
        return 50.0  # unknown — neutral
    med_views = median([v.get("views") for v in top_videos]) or 0
    med_subs = median([v.get("channel_subs") for v in top_videos]) or 0
    ages = [v.get("age_days") for v in top_videos if v.get("age_days") is not None]
    fresh_share = (sum(1 for a in ages if a < 365) / len(ages)) if ages else 0.5

    views_c = log_scale(med_views, 1_000, 10_000_000)   # 0..1
    subs_c = log_scale(med_subs, 1_000, 5_000_000)      # 0..1
    comp = (0.45 * views_c + 0.45 * subs_c + 0.10 * fresh_share) * 100

    # Small-channel-wins bonus: proof of opportunity reduces competition.
    small_wins = sum(
        1 for v in top_videos
        if (v.get("channel_subs") or 0) < 100_000 and (v.get("views") or 0) > 100_000
    )
    comp -= min(small_wins * 5, 15)
    return max(0.0, min(100.0, comp))


def demand_score(rec, max_depth_in_batch) -> float:
    depth = rec.get("autocomplete_depth")
    depth_c = (depth / max_depth_in_batch) if (depth and max_depth_in_batch) else 0.0
    total_views = sum((v.get("views") or 0) for v in rec.get("top_videos", []))
    volume_c = log_scale(total_views, 10_000, 100_000_000)
    return (0.5 * depth_c + 0.5 * volume_c) * 100


def momentum_score(rec):
    m = rec.get("trend_momentum")
    if m is None:
        return None
    return (m + 1) / 2 * 100  # [-1,1] -> 0..100


def channel_fit_score(rec, own_tokens) -> float:
    if not own_tokens:
        return 50.0  # no own-channel data yet — neutral
    kw_tokens = tokenize(rec["keyword"])
    for s in rec.get("suggestions", [])[:20]:
        kw_tokens |= tokenize(s)
    if not kw_tokens:
        return 0.0
    inter = kw_tokens & own_tokens
    union = kw_tokens | own_tokens
    jaccard = len(inter) / len(union)
    # Jaccard on these set sizes is tiny in absolute terms; rescale so ~0.15 = 100.
    return min(1.0, jaccard / 0.15) * 100


def build_own_tokens(cache) -> set:
    own = cache.get("own_channel", {})
    tokens = set()
    for vid in own.get("video_ids", []):
        v = cache.get("videos", {}).get(vid, {})
        tokens |= tokenize(v.get("title", ""))
        for tag in v.get("tags", []):
            tokens |= tokenize(tag)
    return tokens


def main():
    snap = common.load_snapshot()
    cache = common.load_cache()
    own_tokens = build_own_tokens(cache)

    records = list(snap["keywords"].values())
    max_depth = max((r.get("autocomplete_depth") or 0) for r in records) if records else 0

    for rec in records:
        comp = competition_score(rec.get("top_videos", []))
        dem = demand_score(rec, max_depth)
        mom = momentum_score(rec)
        fit = channel_fit_score(rec, own_tokens)

        comp_inv = 100 - comp  # invert: low competition = high contribution

        if mom is None:
            # Momentum weight redistributed: Competition 50%, Demand 30%.
            composite = (W_COMPETITION_NO_TREND * comp_inv
                         + W_DEMAND_NO_TREND * dem
                         + W_CHANNEL_FIT * fit)
        else:
            composite = (W_COMPETITION * comp_inv
                         + W_DEMAND * dem
                         + W_MOMENTUM * mom
                         + W_CHANNEL_FIT * fit)

        rec["scores"] = {
            "composite": round(composite, 1),
            "competition_raw": round(comp, 1),       # high = saturated
            "competition_inverted": round(comp_inv, 1),
            "demand": round(dem, 1),
            "momentum": None if mom is None else round(mom, 1),
            "channel_fit": round(fit, 1),
            "weights_used": ("no_trend" if mom is None else "standard"),
        }
        log.info("'%s' → %.1f (comp_inv %.0f, demand %.0f, momentum %s, fit %.0f)",
                 rec["keyword"], composite, comp_inv, dem,
                 "null" if mom is None else f"{mom:.0f}", fit)

    common.save_snapshot(snap)
    log.info("score.py done")


if __name__ == "__main__":
    main()
