"""
seo.py — Video SEO score for Dan's own uploads (vidIQ's "Video Score" widget,
reimplemented). Runs on cached data only — zero extra API calls/quota.

For each of Dan's own videos:
  1. Auto-match it to the seed keyword (from keywords.txt) it overlaps with
     most, by token overlap of title+tags vs. keyword+its autocomplete
     suggestions. (Dan isn't asked to manually tag videos with a target
     keyword — this infers it. If it guesses wrong, the keyword with the
     next-highest overlap is visible in the breakdown for a manual override
     later if that becomes worth building.)
  2. Score title / description / tags against that matched keyword using
     transparent, documented rules (not a black box).

============================ THE MATH, EXPLAINED ============================
TITLE (40 pts)
  +20  matched keyword's tokens are present in the title (order-independent)
  +20  title length in the 40-70 char sweet spot (YouTube truncates ~60-70
       chars in search/suggested; too short wastes the space)

DESCRIPTION (30 pts)
  +15  matched keyword's tokens appear in the first 25 words (what shows
       before "Show more")
  +15  description is at least 150 words (enough room for context + links,
       matches general YouTube SEO guidance)

TAGS (30 pts)
  +15  tag count in the 8-15 range (too few = missed matches, too many =
       diluted relevance)
  +15  at least one tag contains the matched keyword (exact or token subset)
=============================================================================
"""

import logging
import re

import common
from score import tokenize  # reuse the same tokenizer/stopword list

log = logging.getLogger("seo")


def _token_idf(keywords_core):
    """Document-frequency weighting across the SEED KEYWORD SET.
    A token that appears in the core of many seed keywords is generic
    ("40", "weight", "loss") and should barely count toward a match; a token
    unique to one keyword ("protein", "circadian", "cardio") is distinctive
    and should dominate. Returns {token: weight} where weight = log(N / df).
    """
    import math
    n = max(1, len(keywords_core))
    df = {}
    for toks in keywords_core.values():
        for t in set(toks):
            df[t] = df.get(t, 0) + 1
    # +1 smoothing; a token in ALL keywords gets weight ~0, a token in 1 gets the max.
    return {t: math.log((n + 1) / (c + 0.5)) for t, c in df.items()}


def best_matching_keyword(video, keywords_data, keywords_core, idf):
    """Rarity-weighted, two-sided keyword->video matching.

    keywords_data: {kw: tokens from kw + its autocomplete suggestions} (recall net)
    keywords_core: {kw: tokens from the keyword phrase ITSELF} (must-have signal)
    idf:           {token: rarity weight} across the seed set

    A keyword only matches if the video contains its DISTINCTIVE core tokens —
    not just generic age/topic words shared across the whole seed set. The score
    is the rarity-weighted overlap of the keyword's own phrase tokens, so
    "protein for adults over 40" can no longer win on {adults, 40} alone when
    "protein" is nowhere in the video.
    Returns (keyword, score) or (None, 0.0).
    """
    vid_tokens = tokenize(video.get("title", "")) | set(
        t for tag in video.get("tags", []) for t in tokenize(tag)
    )
    if not vid_tokens or not keywords_data:
        return None, 0.0

    best_kw, best_score = None, 0.0
    for kw, kw_tokens in keywords_data.items():
        core = keywords_core.get(kw, set())
        if not core:
            continue
        # Rarity-weighted coverage of the keyword's OWN phrase tokens.
        core_hit = core & vid_tokens
        num = sum(idf.get(t, 0.0) for t in core_hit)
        den = sum(idf.get(t, 0.0) for t in core)
        if den == 0:
            continue
        weighted = num / den

        # Gate: the single most distinctive token in the keyword's core MUST be
        # present. This is what blocks a match built only on generic tokens.
        rarest = max(core, key=lambda t: idf.get(t, 0.0))
        if rarest not in vid_tokens:
            continue

        if weighted > best_score:
            best_kw, best_score = kw, weighted
    return best_kw, best_score


def score_title(title: str, kw_tokens: set):
    title_tokens = tokenize(title)
    overlap = kw_tokens & title_tokens
    # Allow one missing token (e.g. a stopword variant) on multi-word keywords.
    has_kw = bool(kw_tokens) and len(overlap) >= max(1, len(kw_tokens) - 1)
    length_ok = 40 <= len(title or "") <= 70
    pts = (20 if has_kw else 0) + (20 if length_ok else 0)
    return pts, {"keyword_in_title": has_kw, "length_ok": length_ok, "length": len(title or "")}


def score_description(desc: str, kw_tokens: set):
    words = (desc or "").split()
    first25 = tokenize(" ".join(words[:25]))
    has_kw_early = bool(kw_tokens) and bool(kw_tokens & first25)
    long_enough = len(words) >= 150
    pts = (15 if has_kw_early else 0) + (15 if long_enough else 0)
    return pts, {"keyword_in_first_25_words": has_kw_early, "word_count": len(words)}


def score_tags(tags: list, kw_tokens: set):
    count_ok = 8 <= len(tags or []) <= 15
    has_kw_tag = any(kw_tokens & tokenize(t) for t in (tags or [])) if kw_tokens else False
    pts = (15 if count_ok else 0) + (15 if has_kw_tag else 0)
    return pts, {"tag_count": len(tags or []), "count_ok": count_ok, "keyword_in_a_tag": has_kw_tag}


def score_video(video: dict, keyword: str, kw_tokens: set):
    t_pts, t_detail = score_title(video.get("title", ""), kw_tokens)
    d_pts, d_detail = score_description(video.get("description", ""), kw_tokens)
    g_pts, g_detail = score_tags(video.get("tags", []), kw_tokens)
    composite = t_pts + d_pts + g_pts
    return {
        "matched_keyword": keyword,
        "composite": composite,
        "title": {"points": t_pts, **t_detail},
        "description": {"points": d_pts, **d_detail},
        "tags": {"points": g_pts, **g_detail},
    }


def main():
    cache = common.load_cache()
    snap = common.load_snapshot()
    own = cache.get("own_channel", {})
    video_ids = own.get("video_ids", [])
    if not video_ids:
        log.warning("No own-channel videos cached yet — run collect.py first")
        return

    # Build two token sets per keyword:
    #   keywords_core = tokens from the keyword phrase itself (the must-have signal)
    #   keywords_data = core + autocomplete suggestions (the wider recall net)
    keywords_core, keywords_data = {}, {}
    for kw in common.load_keywords():
        rec = snap["keywords"].get(kw, {})
        core = tokenize(kw)
        keywords_core[kw] = core
        toks = set(core)
        for s in rec.get("suggestions", [])[:20]:
            toks |= tokenize(s)
        keywords_data[kw] = toks
    idf = _token_idf(keywords_core)

    seo_results = {}
    for vid in video_ids:
        video = cache.get("videos", {}).get(vid)
        if not video:
            continue
        kw, overlap = best_matching_keyword(video, keywords_data, keywords_core, idf)
        if not kw or overlap == 0:
            seo_results[vid] = {
                "matched_keyword": None, "composite": None,
                "note": "No seed keyword overlaps this video's title/tags — "
                        "add a relevant keyword to keywords.txt to enable scoring.",
            }
            common.own_video_record(snap, vid)["seo"] = seo_results[vid]
            continue
        seo_results[vid] = score_video(video, kw, tokenize(kw))
        common.own_video_record(snap, vid)["seo"] = seo_results[vid]

    own["seo"] = seo_results
    common.save_cache(cache)
    common.save_snapshot(snap)
    scored = [r for r in seo_results.values() if r.get("composite") is not None]
    log.info("seo.py done — scored %d/%d own videos (avg %.0f/100)",
             len(scored), len(video_ids),
             (sum(r["composite"] for r in scored) / len(scored)) if scored else 0)


if __name__ == "__main__":
    main()
