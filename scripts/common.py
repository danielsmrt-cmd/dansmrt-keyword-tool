"""
common.py — shared helpers for the DanSmrt keyword tool.

Storage model (the ONLY storage layer — no database anywhere):
  data/cache.json                 rolling cache: keyword -> video IDs, video/channel stats
  data/snapshots/YYYY-MM-DD.json  daily snapshot (append-only history)
  data/latest.json                rolling copy of the most recent snapshot (dashboard reads this)

All JSON files carry a schema_version field so the format can evolve safely.
"""

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
SNAPSHOT_DIR = DATA_DIR / "snapshots"
CACHE_PATH = DATA_DIR / "cache.json"
LATEST_PATH = DATA_DIR / "latest.json"
KEYWORDS_PATH = ROOT / "keywords.txt"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def load_json(path: Path, default=None):
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logging.warning("Could not read %s (%s); using default", path, e)
        return default


def save_json(path: Path, obj):
    """Human-readable JSON, atomic-ish write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False, sort_keys=True)
        f.write("\n")
    tmp.replace(path)


def load_keywords() -> list:
    """Seed keywords, one per line. Dan edits keywords.txt directly."""
    if not KEYWORDS_PATH.exists():
        return []
    lines = KEYWORDS_PATH.read_text(encoding="utf-8").splitlines()
    seen, out = set(), []
    for line in lines:
        kw = line.strip().lower()
        if kw and not kw.startswith("#") and kw not in seen:
            seen.add(kw)
            out.append(kw)
    return out


def load_cache() -> dict:
    cache = load_json(CACHE_PATH, default=None)
    if not cache:
        cache = {
            "schema_version": SCHEMA_VERSION,
            "keywords": {},   # kw -> {"searched_at":..., "video_ids":[...]}
            "videos": {},     # video_id -> stats/snippet
            "channels": {},   # channel_id -> stats
            "own_channel": {},  # Dan's channel: {"channel_id":..., "videos":{...}}
        }
    return cache


def save_cache(cache: dict):
    cache["schema_version"] = SCHEMA_VERSION
    save_json(CACHE_PATH, cache)


def load_snapshot() -> dict:
    """Today's snapshot — modules append their sections to it during a run."""
    path = SNAPSHOT_DIR / f"{today_str()}.json"
    snap = load_json(path, default=None)
    if not snap:
        snap = {
            "schema_version": SCHEMA_VERSION,
            "date": today_str(),
            "generated_at": utc_now_iso(),
            "quota_units_used": 0,
            "keywords": {},       # kw -> per-keyword record
            "own_channel": {"videos": {}},  # video_id -> {title, analytics, seo, ...}
        }
    snap.setdefault("own_channel", {"videos": {}})
    return snap


def save_snapshot(snap: dict):
    snap["generated_at"] = utc_now_iso()
    save_json(SNAPSHOT_DIR / f"{snap['date']}.json", snap)
    save_json(LATEST_PATH, snap)


def kw_record(snap: dict, kw: str) -> dict:
    """Get-or-create the per-keyword record in a snapshot."""
    return snap["keywords"].setdefault(kw, {
        "keyword": kw,
        "top_videos": [],
        "autocomplete_depth": None,
        "suggestions": [],
        "trend_momentum": None,
        "scores": None,
        "titles": [],
    })


def own_video_record(snap: dict, video_id: str) -> dict:
    """Get-or-create a record for one of Dan's own videos in a snapshot."""
    return snap["own_channel"]["videos"].setdefault(video_id, {
        "video_id": video_id,
        "title": "",
        "published_at": None,
        "views": None,
        "analytics": None,
        "seo": None,
        "analysis": None,
    })


def retry_call(fn, retries=2, base_delay=1.5, label="call"):
    """Run fn() with up to `retries` retries and exponential backoff.
    Returns (result, None) on success or (None, last_exception) on failure."""
    last = None
    for attempt in range(retries + 1):
        try:
            return fn(), None
        except Exception as e:  # noqa: BLE001 — deliberate catch-all per spec
            last = e
            wait = base_delay * (2 ** attempt)
            logging.warning("%s failed (attempt %d/%d): %s — retrying in %.1fs",
                            label, attempt + 1, retries + 1, e, wait)
            time.sleep(wait)
    logging.error("%s failed after %d attempts: %s", label, retries + 1, last)
    return None, last
