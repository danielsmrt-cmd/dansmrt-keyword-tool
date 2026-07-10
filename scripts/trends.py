"""
trends.py — OPTIONAL Google Trends momentum signal via pytrends.

pytrends is unreliable (frequent 429s and breakage). This entire module is
best-effort: any failure results in trend_momentum = null for the affected
keywords, and the exit code is ALWAYS 0 so the cron never fails because of it.

Momentum = slope of a simple linear regression over 90 days of interest,
normalized to [-1, 1].
"""

import logging
import random
import sys
import time

import common

log = logging.getLogger("trends")

BATCH_SIZE = 5  # pytrends payload limit


def linreg_slope(values):
    """Least-squares slope of values vs. index. Pure python, no numpy needed."""
    n = len(values)
    if n < 2:
        return 0.0
    xs = list(range(n))
    mean_x = sum(xs) / n
    mean_y = sum(values) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, values))
    den = sum((x - mean_x) ** 2 for x in xs)
    return num / den if den else 0.0


def normalize_momentum(slope, values):
    """Map slope to [-1, 1]. Interest values are 0–100; a slope that would move
    the series by its own mean over the window counts as ±1 (clamped)."""
    n = len(values)
    mean_y = (sum(values) / n) if n else 0
    if mean_y <= 0:
        return 0.0
    full_swing = mean_y / max(n - 1, 1)  # slope that changes series by mean_y over window
    m = slope / full_swing if full_swing else 0.0
    return max(-1.0, min(1.0, m))


def main():
    keywords = common.load_keywords()
    snap = common.load_snapshot()

    try:
        from pytrends.request import TrendReq
        pytrends = TrendReq(hl="en-US", tz=600, timeout=(10, 25), retries=2, backoff_factor=1.5)
    except Exception as e:  # noqa: BLE001
        log.warning("pytrends unavailable (%s) — all momentum = null", e)
        common.save_snapshot(snap)
        sys.exit(0)

    for i in range(0, len(keywords), BATCH_SIZE):
        batch = keywords[i:i + BATCH_SIZE]
        try:
            pytrends.build_payload(batch, timeframe="today 3-m")
            df = pytrends.interest_over_time()
            for kw in batch:
                rec = common.kw_record(snap, kw)
                if kw in df.columns:
                    series = [int(v) for v in df[kw].tolist()]
                    slope = linreg_slope(series)
                    rec["trend_momentum"] = round(normalize_momentum(slope, series), 3)
                else:
                    rec["trend_momentum"] = None
        except Exception as e:  # noqa: BLE001 — pytrends throws everything
            log.warning("Trends batch %s failed (%s) — momentum = null for batch", batch, e)
            for kw in batch:
                common.kw_record(snap, kw)["trend_momentum"] = None
        time.sleep(random.uniform(2.0, 4.0))

    common.save_snapshot(snap)
    log.info("trends.py done (best-effort)")
    sys.exit(0)


if __name__ == "__main__":
    main()
