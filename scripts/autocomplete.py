"""
autocomplete.py — YouTube autocomplete depth signal.

Uses the UNOFFICIAL suggest endpoint:
  https://suggestqueries.google.com/complete/search?client=firefox&ds=yt&q={seed}

This can change or break at any time, so everything is parsed defensively.
On any failure for a keyword: autocomplete_depth = null (never a crash).
Total requests per keyword ≤ 11 (1 plain + 10 modifier letters), 1–2s sleep between.
"""

import logging
import random
import time
import urllib.parse

import requests

import common

log = logging.getLogger("autocomplete")

SUGGEST_URL = "https://suggestqueries.google.com/complete/search"
MODIFIER_LETTERS = ["a", "e", "i", "o", "s", "w", "h", "f", "c", "t"]
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/126.0.0.0 Safari/537.36"),
    "Accept-Language": "en-US,en;q=0.9",
}


def fetch_suggestions(query: str):
    """Return a list of suggestion strings, or None on failure."""
    def call():
        r = requests.get(
            SUGGEST_URL,
            params={"client": "firefox", "ds": "yt", "q": query},
            headers=HEADERS,
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        # Expected shape: [query, [suggestion, ...], ...] — verify before trusting.
        if (isinstance(data, list) and len(data) >= 2 and isinstance(data[1], list)):
            return [s for s in data[1] if isinstance(s, str)]
        raise ValueError(f"Unexpected suggest response shape: {str(data)[:120]}")

    result, err = common.retry_call(call, label=f"autocomplete({query})")
    return result  # None on failure


def collect_for_keyword(kw: str):
    """Plain suggestions + one-level letter expansion. Returns (depth, suggestions)
    where depth is None if even the plain fetch failed."""
    suggestions = set()
    plain = fetch_suggestions(kw)
    if plain is None:
        return None, []
    suggestions.update(s.lower().strip() for s in plain)

    for letter in MODIFIER_LETTERS:
        time.sleep(random.uniform(1.0, 2.0))
        more = fetch_suggestions(f"{kw} {letter}")
        if more:
            suggestions.update(s.lower().strip() for s in more)

    suggestions.discard(kw)
    ordered = sorted(suggestions)
    return len(ordered), ordered


def main():
    keywords = common.load_keywords()
    snap = common.load_snapshot()
    for kw in keywords:
        depth, suggs = collect_for_keyword(kw)
        rec = common.kw_record(snap, kw)
        rec["autocomplete_depth"] = depth
        rec["suggestions"] = suggs
        log.info("'%s' → depth=%s", kw, depth)
        time.sleep(random.uniform(1.0, 2.0))
    common.save_snapshot(snap)
    log.info("autocomplete.py done")


if __name__ == "__main__":
    main()
