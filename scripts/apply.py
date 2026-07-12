"""
apply.py — Stage 3. Push approved Fix-Panel suggestions to YouTube.

This is the only script in the project that WRITES to your channel. It is built
so that writing is hard to do by accident and impossible to do silently.

FOUR SAFETY GATES — all must pass before anything is written:
  1. EXPLICIT INTENT.  Nothing happens without --apply. The default mode is a
     dry run that computes the diff and writes it to the snapshot as a proposal.
     The daily cron NEVER passes --apply.
  2. NAMED VIDEO.  --apply requires --video VIDEO_ID. There is no "apply all",
     no "apply everything high-severity". One video, named, per invocation.
  3. FRESHNESS.  A suggestion is only applied if the video's content hash still
     matches the hash Claude analyzed. If you edited the title in YouTube Studio
     after the analysis ran, the suggestion is stale and apply.py refuses. Use
     --force-stale only if you know why.
  4. NON-DESTRUCTIVE FIELDS.  Only title / description / tags are ever touched.
     videos.update replaces the whole snippet, so the current snippet is fetched
     first and every other field (categoryId, defaultLanguage, defaultAudioLanguage)
     is carried through unchanged. Nothing is dropped by omission.

After a successful write, the exact metadata that was published is recorded as
own_channel.published[vid]. drift.py uses that to detect later divergence.

Quota: videos.list = 1 unit, videos.update = 50 units. Negligible against the
10,000/day Data API budget.

Usage:
    python scripts/apply.py                          # dry run, all videos, proposals only
    python scripts/apply.py --video VID              # dry run, one video, show diff
    python scripts/apply.py --video VID --apply      # WRITE (title+desc+tags)
    python scripts/apply.py --video VID --apply --fields title,tags
"""

import argparse
import difflib
import logging
import re
import os
import sys

import requests

import common
from analyze import content_hash

log = logging.getLogger("apply")

TOKEN_URL = "https://oauth2.googleapis.com/token"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"

WRITABLE = ("title", "description", "tags")
CARRY_THROUGH = ("categoryId", "defaultLanguage", "defaultAudioLanguage")

TAGS_COUNT_MAX = 15  # our own standard (YouTube allows more; 8-15 is the target band)

# analyze.py clamps every suggestion "example" to 200 chars. A description
# example at/near that ceiling is a TRUNCATED FRAGMENT, not a replacement — and
# publishing it would destroy a good description. Treat it as poisoned.
EXAMPLE_TRUNCATION_CEILING = 195

TITLE_MAX = 100      # YouTube hard limit
DESC_MAX = 5000      # YouTube hard limit
TAGS_CHAR_MAX = 500  # YouTube hard limit across all tags


def get_access_token():
    cid = os.environ.get("YT_OAUTH_CLIENT_ID")
    secret = os.environ.get("YT_OAUTH_CLIENT_SECRET")
    refresh = os.environ.get("YT_REFRESH_TOKEN")
    if not (cid and secret and refresh):
        log.warning("OAuth secrets not set — cannot write. Run scripts/oauth_setup.py.")
        return None

    def call():
        r = requests.post(TOKEN_URL, data={
            "client_id": cid, "client_secret": secret,
            "refresh_token": refresh, "grant_type": "refresh_token",
        }, timeout=20)
        r.raise_for_status()
        return r.json()["access_token"]

    token, err = common.retry_call(call, label="oauth token refresh")
    if err:
        log.error("Token refresh failed. If you only ever authorized the "
                  "read-only scope, re-run scripts/oauth_setup.py — apply.py "
                  "needs the 'youtube' write scope.")
    return token


def propose(video: dict, analysis: dict, fields) -> dict:
    """Turn Claude's suggestions into a concrete old -> new proposal.

    Only suggestions that carry a usable `example` become changes. A suggestion
    that says "the title is weak" with no rewrite is advice, not an edit — it is
    reported but never applied.
    """
    changes = {}
    for s in analysis.get("suggestions", []):
        area = s.get("area")
        example = s.get("example")
        if area not in fields or not example:
            continue

        if area == "title":
            new = str(example).strip()[:TITLE_MAX]
            if new and new != video.get("title", ""):
                changes["title"] = {"old": video.get("title", ""), "new": new,
                                    "why": s.get("fix", ""), "severity": s.get("severity")}

        elif area == "description":
            new = str(example).strip()[:DESC_MAX]
            old = video.get("description", "") or ""
            vid_ = video.get("id", "?")

            # GUARD A — truncation. analyze.py clips examples at 200 chars, so an
            # example sitting on that ceiling is a severed fragment. Publishing it
            # would replace a full description with a half-sentence.
            if len(new) >= EXAMPLE_TRUNCATION_CEILING:
                log.warning("Skipping description for %s: example is %d chars, at "
                            "analyze.py's 200-char clip ceiling — it is a truncated "
                            "fragment, not a full description. Use the Publish "
                            "Package card to write a real one.", vid_, len(new))
                continue

            # GUARD B — never trade a long description for a short one.
            if new and len(new.split()) < 30:
                log.warning("Skipping description for %s: example is only %d words.",
                            vid_, len(new.split()))
                continue
            if old and len(new) < len(old) * 0.5:
                log.warning("Skipping description for %s: proposed text is less than "
                            "half the length of the current one (%d -> %d chars). "
                            "That is a deletion wearing a rewrite's clothes.",
                            vid_, len(old), len(new))
                continue

            if new and new != old:
                changes["description"] = {"old": old, "new": new,
                                          "why": s.get("fix", ""), "severity": s.get("severity")}

        elif area == "tags":
            old = list(video.get("tags", []))
            raw = str(example).strip()
            # Claude often prefixes the list ("Tags to use:", "Add:", "Suggested tags -").
            # Without this, the label gets welded onto the first tag.
            raw = re.sub(r"^\s*(suggested\s+)?tags?\s*(to\s+use|to\s+add|to\s+keep)?\s*[:\-–]\s*",
                         "", raw, flags=re.IGNORECASE)
            add = [t.strip().strip('"\'') for t in raw.split(",") if t.strip()]
            add = [t for t in add if 0 < len(t) <= 60]

            # NEW TAGS FIRST. If we are capping at 15 and the video already has 17,
            # an old-first merge would add nothing at all — the suggestion would be
            # silently a no-op. The whole point is to get the target keyword in.
            merged, seen = [], set()
            for t in add + old:
                tl = t.lower()
                if tl not in seen:
                    seen.add(tl)
                    merged.append(t)

            # Respect BOTH limits: 500 chars total (YouTube) and 8-15 tags (our standard).
            budget, kept = 0, []
            for t in merged:
                if len(kept) >= TAGS_COUNT_MAX:
                    break
                cost = len(t) + (2 if kept else 0)
                if budget + cost > TAGS_CHAR_MAX:
                    continue
                kept.append(t)
                budget += cost

            if [t.lower() for t in kept] != [t.lower() for t in old]:
                changes["tags"] = {"old": old, "new": kept,
                                   "why": s.get("fix", ""), "severity": s.get("severity")}
    return changes


def render_diff(vid: str, title: str, changes: dict) -> str:
    if not changes:
        return f"  {vid} ({title[:40]}): no actionable changes proposed"
    out = [f"\n=== {vid} — {title[:60]} ==="]
    for field, c in changes.items():
        out.append(f"\n[{field.upper()}]  severity={c.get('severity')}")
        out.append(f"  why: {c.get('why', '')[:160]}")
        if field == "tags":
            oldl = {t.lower() for t in c["old"]}
            newl = {t.lower() for t in c["new"]}
            added = [t for t in c["new"] if t.lower() not in oldl]
            dropped = [t for t in c["old"] if t.lower() not in newl]
            out.append(f"  {len(c['old'])} tags -> {len(c['new'])} tags")
            if added:
                out.append(f"  + {', '.join(added)}")
            if dropped:
                out.append(f"  - {', '.join(dropped)}  (dropped to stay within 15)")
        elif field == "title":
            out.append(f"  - {c['old']}")
            out.append(f"  + {c['new']}")
        else:
            diff = difflib.unified_diff(
                c["old"].splitlines(), c["new"].splitlines(),
                lineterm="", n=0)
            body = [ln for ln in diff if ln.startswith(("+", "-"))
                    and not ln.startswith(("+++", "---"))]
            for ln in body[:12]:
                out.append(f"  {ln}")
            if len(body) > 12:
                out.append(f"  … {len(body) - 12} more changed lines")
    return "\n".join(out)


def fetch_snippet(token: str, vid: str):
    def call():
        r = requests.get(VIDEOS_URL, params={"part": "snippet", "id": vid},
                         headers={"Authorization": f"Bearer {token}"}, timeout=20)
        r.raise_for_status()
        return r.json()
    data, err = common.retry_call(call, label=f"videos.list {vid}")
    if err or not data.get("items"):
        log.error("Could not fetch current snippet for %s", vid)
        return None
    return data["items"][0]["snippet"]


def push(token: str, vid: str, snippet: dict, changes: dict):
    """Rebuild the FULL snippet — videos.update replaces it wholesale, so every
    field we aren't changing must be carried through explicitly."""
    new_snippet = {
        "title": changes.get("title", {}).get("new", snippet.get("title", "")),
        "description": changes.get("description", {}).get("new", snippet.get("description", "")),
        "tags": changes.get("tags", {}).get("new", snippet.get("tags", [])),
    }
    for f in CARRY_THROUGH:
        if snippet.get(f):
            new_snippet[f] = snippet[f]
    if not new_snippet.get("categoryId"):
        log.error("Refusing to update %s: no categoryId on the current snippet. "
                  "videos.update would reject or blank it.", vid)
        return None

    def call():
        r = requests.put(VIDEOS_URL, params={"part": "snippet"},
                         headers={"Authorization": f"Bearer {token}",
                                  "Content-Type": "application/json"},
                         json={"id": vid, "snippet": new_snippet}, timeout=30)
        r.raise_for_status()
        return r.json()

    resp, err = common.retry_call(call, label=f"videos.update {vid}")
    if err:
        log.error("Update FAILED for %s — nothing was changed.", vid)
        return None
    return new_snippet


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", help="Video ID (REQUIRED for --apply)")
    ap.add_argument("--apply", action="store_true",
                    help="Actually write to YouTube. Without this, dry run only.")
    ap.add_argument("--fields", default="title,tags",
                    help="Comma list of fields to touch. 'description' is opt-in: "
                         "analyze.py's examples are 200-char clipped, so most "
                         "description suggestions are fragments, not replacements.")
    ap.add_argument("--force-stale", action="store_true",
                    help="Apply even if the video changed since Claude analyzed it")
    args = ap.parse_args()

    fields = tuple(f.strip() for f in args.fields.split(",")
                   if f.strip() in WRITABLE)
    if not fields:
        log.error("No valid fields. Choose from: %s", ", ".join(WRITABLE))
        sys.exit(1)

    # GATE 2: --apply demands a named video.
    if args.apply and not args.video:
        log.error("Refusing to apply without --video. There is no bulk apply "
                  "in this tool — approve one video at a time.")
        sys.exit(1)

    cache = common.load_cache()
    snap = common.load_snapshot()
    own = cache.get("own_channel", {})
    analysis_all = own.get("analysis", {})
    vids = [args.video] if args.video else own.get("video_ids", [])
    if not vids:
        log.warning("No videos to consider — run collect.py + analyze.py first")
        sys.exit(0)

    token = None
    if args.apply:
        token = get_access_token()
        if not token:
            sys.exit(1)

    n_proposed = n_applied = 0
    for vid in vids:
        video = cache.get("videos", {}).get(vid)
        analysis = analysis_all.get(vid)
        if not video or not analysis:
            continue

        changes = propose(video, analysis, fields)
        rec = common.own_video_record(snap, vid)

        if not changes:
            rec.pop("pending_changes", None)
            if args.video:
                print(render_diff(vid, video.get("title", ""), changes))
            continue

        n_proposed += 1
        rec["pending_changes"] = {
            "changes": changes,
            "content_hash": analysis.get("content_hash"),
            "proposed_at": common.utc_now_iso(),
        }
        print(render_diff(vid, video.get("title", ""), changes))

        if not args.apply:
            continue

        # GATE 3: freshness.
        live_hash = content_hash(video)
        if live_hash != analysis.get("content_hash") and not args.force_stale:
            log.error("STALE: %s changed since Claude analyzed it. The suggestion "
                      "was written against different content. Re-run analyze.py, "
                      "or pass --force-stale if you're sure.", vid)
            continue

        snippet = fetch_snippet(token, vid)
        if not snippet:
            continue
        published = push(token, vid, snippet, changes)
        if not published:
            continue

        n_applied += 1
        own.setdefault("published", {})[vid] = {
            "title": published["title"],
            "description": published["description"],
            "tags": published.get("tags", []),
            "applied_at": common.utc_now_iso(),
            "fields": list(changes.keys()),
        }
        # Keep the local cache in step so drift.py doesn't false-positive
        # before the next collect.py run.
        video.update({"title": published["title"],
                      "description": published["description"],
                      "tags": published.get("tags", [])})
        rec.pop("pending_changes", None)
        rec["applied"] = own["published"][vid]
        log.info("APPLIED to %s: %s", vid, ", ".join(changes.keys()))

    common.save_cache(cache)
    common.save_snapshot(snap)

    if args.apply:
        log.info("apply.py done — %d video(s) updated", n_applied)
    else:
        log.info("apply.py DRY RUN — %d video(s) have proposed changes. "
                 "Nothing was written. Re-run with --video ID --apply to publish.",
                 n_proposed)
    sys.exit(0)


if __name__ == "__main__":
    main()
