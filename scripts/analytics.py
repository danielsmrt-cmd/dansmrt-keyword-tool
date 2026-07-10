"""
analytics.py — real per-video watch time, audience retention, and subscriber
deltas via the YouTube Analytics API v2 (OAuth — this is private data vidIQ
cannot see either without you connecting your own account to it).

Uses a refresh token obtained ONCE via scripts/oauth_setup.py (run locally,
never in CI). GitHub Actions exchanges it for a short-lived access token on
every run — no browser, no server, no re-consent needed.

Skips gracefully (exit 0, warning logged) if the OAuth secrets aren't set
yet, so the daily cron never breaks while you're mid-setup.

Quota note: the YouTube Analytics API has its own separate quota pool from
the Data API v3 (the 10,000-unit budget elsewhere in this project). One
report call here covers every video in the date window, so cost is
negligible regardless of channel size.
"""

import logging
import os
import sys
from datetime import date, timedelta

import requests

import common

log = logging.getLogger("analytics")

TOKEN_URL = "https://oauth2.googleapis.com/token"
REPORTS_URL = "https://youtubeanalytics.googleapis.com/v2/reports"
LOOKBACK_DAYS = 90  # retention/watch-time window; adjust freely


def get_access_token():
    client_id = os.environ.get("YT_OAUTH_CLIENT_ID")
    client_secret = os.environ.get("YT_OAUTH_CLIENT_SECRET")
    refresh_token = os.environ.get("YT_REFRESH_TOKEN")
    if not (client_id and client_secret and refresh_token):
        log.warning("OAuth secrets not set (YT_OAUTH_CLIENT_ID / "
                    "YT_OAUTH_CLIENT_SECRET / YT_REFRESH_TOKEN) — skipping "
                    "analytics.py. Run scripts/oauth_setup.py once to enable this.")
        return None

    def call():
        r = requests.post(TOKEN_URL, data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }, timeout=20)
        r.raise_for_status()
        return r.json()["access_token"]

    token, err = common.retry_call(call, label="oauth token refresh")
    if err:
        log.error("Could not refresh access token — check that YT_REFRESH_TOKEN "
                  "hasn't been revoked (re-run scripts/oauth_setup.py if so)")
    return token


def fetch_video_report(access_token):
    """One call, dimensions=video, covers every video with activity in the
    window — no per-video looping needed."""
    end = date.today()
    start = end - timedelta(days=LOOKBACK_DAYS)

    def call():
        r = requests.get(
            REPORTS_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            params={
                "ids": "channel==MINE",
                "startDate": start.isoformat(),
                "endDate": end.isoformat(),
                "metrics": ",".join([
                    "estimatedMinutesWatched",
                    "averageViewDuration",
                    "averageViewPercentage",
                    "views",
                    "subscribersGained",
                    "subscribersLost",
                ]),
                "dimensions": "video",
                "sort": "-estimatedMinutesWatched",
                "maxResults": 200,
            },
            timeout=30,
        )
        r.raise_for_status()
        return r.json()

    data, err = common.retry_call(call, label="youtubeAnalytics.reports")
    if err:
        return None
    cols = [c["name"] for c in data.get("columnHeaders", [])]
    rows = data.get("rows", [])
    return [dict(zip(cols, row)) for row in rows]


def main():
    access_token = get_access_token()
    if not access_token:
        sys.exit(0)  # graceful skip — never break the cron

    rows = fetch_video_report(access_token)
    if rows is None:
        log.warning("Analytics report fetch failed — leaving cached values as-is")
        sys.exit(0)

    cache = common.load_cache()
    own = cache.setdefault("own_channel", {})
    analytics = own.setdefault("analytics", {})
    snap = common.load_snapshot()

    for row in rows:
        vid = row.get("video")
        if not vid:
            continue
        entry = {
            "estimated_minutes_watched": row.get("estimatedMinutesWatched", 0),
            "avg_view_duration_sec": row.get("averageViewDuration", 0),
            "avg_view_percentage": row.get("averageViewPercentage", 0.0),
            "views_window": row.get("views", 0),
            "subscribers_gained": row.get("subscribersGained", 0),
            "subscribers_lost": row.get("subscribersLost", 0),
            "window_days": LOOKBACK_DAYS,
            "refreshed_at": common.utc_now_iso(),
        }
        analytics[vid] = entry
        common.own_video_record(snap, vid)["analytics"] = entry

    common.save_cache(cache)
    common.save_snapshot(snap)
    log.info("analytics.py done — %d videos with analytics data (last %d days)",
             len(rows), LOOKBACK_DAYS)


if __name__ == "__main__":
    main()
