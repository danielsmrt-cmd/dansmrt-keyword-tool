"""
drift.py — Stage 3. Detect when a video's LIVE metadata has diverged from what
was last published through apply.py.

Why this matters: you optimize a title, apply it, and three weeks later you (or
YouTube Studio's autosuggest, or a bulk edit, or a future you who forgot) change
it back. Nothing tells you. The Fix Panel would happily re-suggest the same fix
forever without ever noticing the loop.

Runs in the daily cron. Costs ZERO extra API calls — collect.py already pulls
each video's live title/description/tags into the cache every morning. drift.py
just compares that against own_channel.published[vid], which apply.py wrote at
the moment of publishing.

Drift is NOT an error. Deliberately rewriting a title is a legitimate thing to
do. This only surfaces it so the change is visible and intentional rather than
silent. Acknowledge a drift with --accept to re-baseline it.

Writes own_channel.videos[vid]["drift"] into the snapshot for the dashboard.

Usage:
    python scripts/drift.py                 # detect and report (daily cron)
    python scripts/drift.py --accept VID    # re-baseline: live becomes the new truth
"""

import argparse
import logging
import sys

import common

log = logging.getLogger("drift")

TRACKED = ("title", "description", "tags")


def compare(published: dict, live: dict) -> dict:
    """Return only the fields that actually diverged."""
    drifted = {}
    for field in TRACKED:
        was = published.get(field)
        now = live.get(field)
        if was is None:
            continue
        if field == "tags":
            # Order-insensitive, case-insensitive: reordering tags in Studio is
            # not a meaningful change and shouldn't cry wolf.
            was_set = {t.lower() for t in (was or [])}
            now_set = {t.lower() for t in (now or [])}
            if was_set != now_set:
                drifted[field] = {
                    "published": was,
                    "live": now or [],
                    "added": sorted(now_set - was_set),
                    "removed": sorted(was_set - now_set),
                }
        else:
            if (was or "").strip() != (now or "").strip():
                drifted[field] = {
                    "published": was,
                    "live": now or "",
                    # Cheap, readable summary for the dashboard chip.
                    "note": ("shortened" if len(now or "") < len(was or "")
                             else "lengthened" if len(now or "") > len(was or "")
                             else "rewritten"),
                }
    return drifted


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--accept", metavar="VIDEO_ID",
                    help="Accept the live metadata as the new baseline for this video")
    args = ap.parse_args()

    cache = common.load_cache()
    snap = common.load_snapshot()
    own = cache.get("own_channel", {})
    published_all = own.get("published", {})

    if not published_all:
        log.info("No videos have been published through apply.py yet — "
                 "nothing to compare against. (This is normal until Stage 3 runs.)")
        common.save_snapshot(snap)
        sys.exit(0)

    if args.accept:
        vid = args.accept
        live = cache.get("videos", {}).get(vid)
        if not live or vid not in published_all:
            log.error("%s has no published baseline to re-accept", vid)
            sys.exit(1)
        published_all[vid].update({
            "title": live.get("title", ""),
            "description": live.get("description", ""),
            "tags": live.get("tags", []),
            "accepted_at": common.utc_now_iso(),
        })
        common.own_video_record(snap, vid).pop("drift", None)
        common.save_cache(cache)
        common.save_snapshot(snap)
        log.info("Re-baselined %s — live metadata is now the reference.", vid)
        sys.exit(0)

    n_drift = 0
    for vid, published in published_all.items():
        live = cache.get("videos", {}).get(vid)
        rec = common.own_video_record(snap, vid)
        if not live:
            # Video deleted or unlisted — worth knowing, not worth failing over.
            rec["drift"] = {"missing": True, "checked_at": common.utc_now_iso()}
            log.warning("%s was published through apply.py but is no longer in the "
                        "collected video list (deleted, private, or unlisted?)", vid)
            n_drift += 1
            continue

        drifted = compare(published, live)
        if drifted:
            n_drift += 1
            rec["drift"] = {
                "fields": drifted,
                "applied_at": published.get("applied_at"),
                "checked_at": common.utc_now_iso(),
            }
            log.warning("DRIFT on %s (%s): %s changed since it was published on %s",
                        vid, live.get("title", "")[:40],
                        ", ".join(drifted.keys()), published.get("applied_at", "?")[:10])
        else:
            rec.pop("drift", None)

    common.save_snapshot(snap)
    if n_drift:
        log.info("drift.py done — %d video(s) drifted from their published package. "
                 "Review in the Fix Panel; run drift.py --accept VID to re-baseline.",
                 n_drift)
    else:
        log.info("drift.py done — all %d published video(s) match their baseline.",
                 len(published_all))
    sys.exit(0)


if __name__ == "__main__":
    main()
