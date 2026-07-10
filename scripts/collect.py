"""
collect.py — YouTube Data API v3 layer. BUILD/RUN FIRST in the daily pipeline.

Quota design (10,000 units/day hard limit; self-imposed ceiling 8,000):
  search.list                 = 100 units  → ONLY for keywords never searched before
  videos.list / channels.list = 1 unit     → up to 50 IDs per call, refreshed daily
  Max ~40 search.list calls per run (4,000 units) leaves plenty of headroom.

Uses the API key from Dan's SECONDARY Google account (env: YT_API_KEY).
Never assume the primary account — it is restricted.
"""

import logging
import os
import sys
from datetime import datetime, timezone

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

import common

log = logging.getLogger("collect")

QUOTA_CEILING = 8000          # self-imposed; abort gracefully at this point
SEARCH_COST = 100
LIST_COST = 1
MAX_NEW_SEARCHES = 40         # per-run cap on search.list calls
OWN_CHANNEL_ID = os.environ.get("OWN_CHANNEL_ID", "")  # optional; else resolved from handle
OWN_CHANNEL_HANDLE = os.environ.get("OWN_CHANNEL_HANDLE", "DanSmrtCoaching")


class QuotaTracker:
    def __init__(self, ceiling=QUOTA_CEILING):
        self.used = 0
        self.ceiling = ceiling

    def spend(self, units: int) -> bool:
        """Return True if the spend fits under the ceiling; record it."""
        if self.used + units > self.ceiling:
            log.warning("Quota ceiling reached (%d used, %d requested) — stopping API calls",
                        self.used, units)
            return False
        self.used += units
        return True


def yt_client():
    key = os.environ.get("YT_API_KEY")
    if not key:
        log.error("YT_API_KEY not set — cannot run collect")
        sys.exit(1)
    return build("youtube", "v3", developerKey=key, cache_discovery=False)


def chunks(lst, n=50):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def search_new_keywords(yt, cache, quota, keywords):
    """search.list ONLY for keywords not yet in cache."""
    new_kws = [k for k in keywords if k not in cache["keywords"]]
    searched = 0
    for kw in new_kws:
        if searched >= MAX_NEW_SEARCHES or not quota.spend(SEARCH_COST):
            log.info("Deferring remaining new keywords to a future run")
            break

        def call(kw=kw):
            return yt.search().list(
                q=kw, part="id", type="video", order="relevance", maxResults=20
            ).execute()

        resp, err = common.retry_call(call, label=f"search.list({kw})")
        if err:
            # YouTube charges quota even for failed calls — keep the spend recorded.
            continue
        ids = [it["id"]["videoId"] for it in resp.get("items", []) if it["id"].get("videoId")]
        cache["keywords"][kw] = {"searched_at": common.utc_now_iso(), "video_ids": ids}
        searched += 1
        log.info("Searched '%s' → %d videos", kw, len(ids))
    return searched


def refresh_videos(yt, cache, quota, video_ids):
    """Batch videos.list on cached IDs — 1 unit per 50 IDs."""
    for batch in chunks(sorted(set(video_ids))):
        if not quota.spend(LIST_COST):
            return

        def call(batch=batch):
            return yt.videos().list(
                id=",".join(batch), part="statistics,snippet,contentDetails", maxResults=50
            ).execute()

        resp, err = common.retry_call(call, label="videos.list")
        if err:
            continue
        for it in resp.get("items", []):
            st = it.get("statistics", {})
            sn = it.get("snippet", {})
            cache["videos"][it["id"]] = {
                "title": sn.get("title", ""),
                "description": sn.get("description", ""),
                "channel_id": sn.get("channelId", ""),
                "channel_title": sn.get("channelTitle", ""),
                "published_at": sn.get("publishedAt", ""),
                "tags": sn.get("tags", []),
                "duration": it.get("contentDetails", {}).get("duration", ""),
                "views": int(st.get("viewCount", 0) or 0),
                "likes": int(st.get("likeCount", 0) or 0),
                "comments": int(st.get("commentCount", 0) or 0),
                "refreshed_at": common.utc_now_iso(),
            }


def refresh_channels(yt, cache, quota, channel_ids):
    for batch in chunks(sorted(set(channel_ids))):
        if not quota.spend(LIST_COST):
            return

        def call(batch=batch):
            return yt.channels().list(
                id=",".join(batch), part="statistics", maxResults=50
            ).execute()

        resp, err = common.retry_call(call, label="channels.list")
        if err:
            continue
        for it in resp.get("items", []):
            st = it.get("statistics", {})
            cache["channels"][it["id"]] = {
                "subs": int(st.get("subscriberCount", 0) or 0),
                "videos": int(st.get("videoCount", 0) or 0),
                "views": int(st.get("viewCount", 0) or 0),
                "refreshed_at": common.utc_now_iso(),
            }


def track_own_channel(yt, cache, quota):
    """Track ALL of Dan's own videos via the uploads playlist (channel-fit +
    analytics + SEO scoring need the full catalog, not just top-20 by views).
    Full sync runs weekly (Mondays); other days reuse the cached ID list and
    just refresh stats."""
    own = cache.setdefault("own_channel", {})
    cid = own.get("channel_id") or OWN_CHANNEL_ID
    if not cid:
        if not quota.spend(LIST_COST):
            return
        def call():
            return yt.channels().list(forHandle=OWN_CHANNEL_HANDLE, part="id").execute()
        resp, err = common.retry_call(call, label="channels.list(forHandle)")
        if err or not resp.get("items"):
            log.warning("Could not resolve own channel from handle '%s'", OWN_CHANNEL_HANDLE)
            return
        cid = resp["items"][0]["id"]
    own["channel_id"] = cid

    is_monday = datetime.now(timezone.utc).weekday() == 0
    if own.get("video_ids") and not is_monday:
        refresh_videos(yt, cache, quota, own["video_ids"])
        return

    if not own.get("uploads_playlist_id"):
        if not quota.spend(LIST_COST):
            return
        def call():
            return yt.channels().list(id=cid, part="contentDetails").execute()
        resp, err = common.retry_call(call, label="channels.list(contentDetails)")
        if err or not resp.get("items"):
            log.warning("Could not resolve uploads playlist for own channel")
            return
        own["uploads_playlist_id"] = (
            resp["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
        )

    playlist_id = own["uploads_playlist_id"]
    video_ids, page_token, pages = [], None, 0
    while pages < 10:  # cap at 10 pages (500 videos) to bound quota/runtime
        if not quota.spend(LIST_COST):
            break

        def call(page_token=page_token):
            return yt.playlistItems().list(
                playlistId=playlist_id, part="contentDetails",
                maxResults=50, pageToken=page_token
            ).execute()

        resp, err = common.retry_call(call, label="playlistItems.list(own uploads)")
        if err:
            break
        video_ids.extend(
            it["contentDetails"]["videoId"] for it in resp.get("items", [])
        )
        page_token = resp.get("nextPageToken")
        pages += 1
        if not page_token:
            break

    if video_ids:
        own["video_ids"] = video_ids
        log.info("Own channel: synced %d uploaded videos", len(video_ids))
        refresh_videos(yt, cache, quota, video_ids)


def write_snapshot(cache, snap, keywords):
    """Copy enriched top-video data for each keyword into today's snapshot."""
    now = datetime.now(timezone.utc)
    for kw in keywords:
        entry = cache["keywords"].get(kw)
        if not entry:
            continue
        rec = common.kw_record(snap, kw)
        top = []
        for vid in entry["video_ids"]:
            v = cache["videos"].get(vid)
            if not v:
                continue
            ch = cache["channels"].get(v["channel_id"], {})
            age_days = None
            try:
                pub = datetime.fromisoformat(v["published_at"].replace("Z", "+00:00"))
                age_days = (now - pub).days
            except (ValueError, AttributeError):
                pass
            top.append({
                "video_id": vid,
                "title": v["title"],
                "views": v["views"],
                "channel_title": v["channel_title"],
                "channel_subs": ch.get("subs"),
                "age_days": age_days,
                "tags": v.get("tags", []),
            })
        rec["top_videos"] = top


def write_own_channel_snapshot(cache, snap):
    """Base record (title/views/published_at) for each of Dan's own videos —
    analytics.py and seo.py fill in the analytics/seo sub-fields later in the
    same run."""
    own = cache.get("own_channel", {})
    for vid in own.get("video_ids", []):
        v = cache.get("videos", {}).get(vid)
        if not v:
            continue
        rec = common.own_video_record(snap, vid)
        rec["title"] = v.get("title", "")
        rec["published_at"] = v.get("published_at")
        rec["views"] = v.get("views")


def main():
    keywords = common.load_keywords()
    if not keywords:
        log.error("keywords.txt is empty — nothing to do")
        return
    cache = common.load_cache()
    snap = common.load_snapshot()
    quota = QuotaTracker()
    yt = yt_client()

    try:
        search_new_keywords(yt, cache, quota, keywords)
        all_vids = [v for kw in keywords for v in cache["keywords"].get(kw, {}).get("video_ids", [])]
        refresh_videos(yt, cache, quota, all_vids)
        ch_ids = [cache["videos"][v]["channel_id"] for v in all_vids if v in cache["videos"]]
        refresh_channels(yt, cache, quota, ch_ids)
        track_own_channel(yt, cache, quota)
    except HttpError as e:
        log.error("YouTube API error (continuing with what we have): %s", e)

    write_snapshot(cache, snap, keywords)
    write_own_channel_snapshot(cache, snap)
    snap["quota_units_used"] = quota.used
    common.save_cache(cache)
    common.save_snapshot(snap)
    log.info("collect.py done — quota units consumed this run: %d / %d ceiling",
             quota.used, QUOTA_CEILING)


if __name__ == "__main__":
    main()
