# DanSmrt Keyword Radar

Self-hosted YouTube keyword research, real channel analytics, and video SEO scoring. Replaces the standing two-call vidIQ budget per Short (`vidiq_keyword_research` + `vidiq_generate_titles`) with a free, directionally accurate alternative running entirely on GitHub Actions and a static dashboard. No server, no database, no paid services.

## How it works

Daily at 6:00 AM Hawaii time, a GitHub Action runs these Python steps and commits a JSON snapshot back to the repo:

1. `collect.py` — YouTube Data API v3. Searches only NEW keywords (100 units each), then refreshes stats on all cached videos/channels at 1 unit per 50 IDs. Also syncs your FULL upload catalog via the uploads playlist (1 unit/50 videos) for the channel-fit, analytics, and SEO signals. Self-imposed ceiling: 8,000 of the 10,000 daily units.
2. `autocomplete.py` — unofficial YouTube suggest endpoint. Produces `autocomplete_depth` per keyword; any failure yields `null`, never a crash.
3. `trends.py` — optional pytrends momentum. Best-effort by design; a failure yields `null` momentum and the run continues.
4. `score.py` — Opportunity Score 0–100 (weights documented in the file and in `calibration.md`).
5. `analytics.py` — real watch time, audience retention %, subscriber deltas per video via the YouTube Analytics API (OAuth). Skips gracefully until you complete the one-time OAuth setup below.
6. `seo.py` — Video SEO score for each of your own uploads: auto-matches the video to the seed keyword it best overlaps, then scores title/description/tags against it. Uses cached data only — no extra quota.

`titles.py` runs only on demand (workflow_dispatch) and calls the Claude API for 10 scored title candidates per keyword.

`dashboard.html` is a single static file that fetches `data/latest.json` from raw.githubusercontent.com. Open it locally in a browser or serve it via GitHub Pages. It shows two tables: keyword opportunities, and your own channel's retention/SEO.

## Setup

### 1. YouTube Data API key (SECONDARY Google account)

Use the secondary Google account only — the primary Google Cloud account is restricted.

1. Sign in to https://console.cloud.google.com with the **secondary** account.
2. Create a project (e.g. `dansmrt-keywords`).
3. APIs & Services → Library → search "YouTube Data API v3" → Enable.
4. APIs & Services → Credentials → Create Credentials → API key.
5. Recommended: restrict the key to the YouTube Data API v3 only.
6. Copy the key — never commit it anywhere.

### 2. Create the repo

1. Create a new GitHub repo (private is fine) and push this scaffold to it.
2. Edit `keywords.txt` with your starting seeds (10 included).
3. In `dashboard.html`, set the three constants at the top of the `<script>`: `GH_USER`, `GH_REPO`, `GH_BRANCH`. (Note: if the repo is private, raw.githubusercontent.com fetches won't work from the browser — either make the repo public or serve the dashboard via GitHub Pages from the same repo and change `DATA_URL` to the relative path `data/latest.json`.)

### 3. Add secrets

Repo → Settings → Secrets and variables → Actions → New repository secret:

- `YT_API_KEY` — the key from step 1
- `ANTHROPIC_API_KEY` — your Anthropic key (used only when you run titles)

### 4. One-time OAuth setup (for real watch time / retention / SEO score)

This unlocks `analytics.py` (real watch time, audience retention %, subscriber
gained/lost per video) and `seo.py` (title/description/tag scoring). Both are
private data vidIQ can't see either without you connecting your account to
it. This is a **one-time** browser consent on your own laptop — nothing about
the daily Action's "no server" design changes; GitHub Actions just refreshes
its own access token from here on out.

1. In Google Cloud Console, same project as your `YT_API_KEY` (secondary
   account): **APIs & Services → Credentials → Create Credentials → OAuth
   client ID**.
2. If prompted, configure the OAuth consent screen first: User type
   "External", fill in the required fields, add your own secondary-account
   email under "Test users" (this keeps it in testing mode, which is fine —
   you're the only user).
3. Application type: **Desktop app**. Name it anything (e.g. "DanSmrt
   Keyword Radar CLI"). Create it, then copy the **Client ID** and **Client
   secret** shown.
4. On your laptop, in this repo folder:
   ```
   pip install google-auth-oauthlib --break-system-packages
   python scripts/oauth_setup.py
   ```
5. Paste the Client ID and Client secret when prompted. A browser window
   opens — sign in with the secondary account and approve access.
6. The script prints three values. Add all three as repo secrets (same
   Settings → Secrets screen as step 3):
   - `YT_OAUTH_CLIENT_ID`
   - `YT_OAUTH_CLIENT_SECRET`
   - `YT_REFRESH_TOKEN`

You won't need to repeat this unless you revoke access in your Google
account's security settings. Until you do this step, `analytics.py` logs a
warning and skips itself — the rest of the daily run is unaffected.

### 5. Enable Actions

Repo → Actions tab → enable workflows. The daily cron runs at 16:00 UTC. You can also trigger manually: Actions → "Daily keyword snapshot" → Run workflow. Check `run_titles` to also generate Claude title candidates (top 5 keywords by score, or type one keyword into `titles_keyword`).

### 6. Dashboard (optional GitHub Pages)

Settings → Pages → Deploy from branch → `main` / root. Your dashboard will be live at `https://YOUR_USER.github.io/REPO/dashboard.html`. If using Pages on the same repo, you can simplify `DATA_URL` to `"data/latest.json"`.

## Daily usage

1. Open the dashboard. Green pills (≥70) are your opportunities.
2. Click "Copy for pipeline" — paste the block into Stage 1 of `shorts_pipeline_hybrid.html`.
3. Need titles? Actions → Run workflow → check `run_titles` (or enter one keyword). Titles appear in the dashboard after the run commits.
4. Add new keywords by editing `keywords.txt` — each new keyword costs 100 units on its first run only.
5. Scroll to "Your Channel" for retention/watch-time and SEO scores per video, once OAuth is set up (step 4 above).

## Quota math

- 10 keywords, all cached: ~2–4 units/day (list calls only).
- Adding 10 new keywords: 1,000 units once.
- Own-channel full sync: ~1 unit per 50 uploaded videos, weekly.
- YouTube Analytics API (watch time/retention) uses a **separate** quota pool — doesn't touch the 10,000-unit Data API budget at all.
- Ceiling enforced in code at 8,000; hard limit 10,000/day.

## Files

```
keywords.txt                 seed keywords (edit me)
scripts/common.py            shared storage + retry helpers
scripts/collect.py           YouTube API layer (quota-budgeted)
scripts/autocomplete.py      suggest-endpoint depth signal
scripts/trends.py            optional pytrends momentum
scripts/score.py             Opportunity Score (tunable weights)
scripts/titles.py            on-demand Claude title generation
scripts/oauth_setup.py       ONE-TIME, run locally — gets the OAuth refresh token
scripts/analytics.py         real watch time/retention/subs via YouTube Analytics API
scripts/seo.py                video SEO score for your own uploads
.github/workflows/daily.yml  daily cron + manual dispatch
data/latest.json             rolling snapshot (dashboard reads this)
data/snapshots/YYYY-MM-DD.json  daily history
data/cache.json              video/channel stat cache
dashboard.html               static dashboard (brand-styled, two tables)
calibration.md               how to tune the score against vidIQ
```

All data files are human-readable JSON with a `schema_version` field.
