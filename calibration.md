# Calibrating the Opportunity Score against vidIQ

The score is **directional**, not an estimate of vidIQ's proprietary search volume. The goal is that keywords vidIQ rates highly also land high here, in roughly the same order. Do this for 2–3 weeks before trusting the tool solo.

## Procedure

1. Each time you run a `vidiq_keyword_research` call in Claude chat (you're still on budget during calibration), log a row in the table below.
2. After ~15–20 rows, look at the pattern:
   - **Rank agreement matters more than absolute values.** If vidIQ's top pick is also this tool's top pick most days, you're done.
   - If high-vidIQ keywords score low here because of the **competition** sub-score, the log anchors in `score.py` (`log_scale` calls: views 1k–10M, subs 1k–5M) are probably too aggressive for your niche — narrow them (e.g. 1k–2M views).
   - If everything clusters in the 40–60 band, the weights are washing each other out — raise `W_COMPETITION` and lower `W_DEMAND`.
   - If channel-fit is dragging good keywords down, the Jaccard rescale constant (`0.15` in `channel_fit_score`) is too strict — raise it to 0.20–0.25.
3. Sub-scores are stored per keyword in `data/latest.json` under `scores` — always check *which* component disagrees with vidIQ before touching weights.

## Log

| Date | Keyword | vidIQ score | Our composite | Competition (inv) | Demand | Momentum | Fit | Notes |
|------|---------|-------------|---------------|-------------------|--------|----------|-----|-------|
|      |         |             |               |                   |        |          |     |       |

## Weight reference (scripts/score.py)

| Constant | Default | Meaning |
|----------|---------|---------|
| `W_COMPETITION` | 0.40 | inverted saturation of the top-20 SERP |
| `W_DEMAND` | 0.25 | autocomplete depth + total view volume |
| `W_MOMENTUM` | 0.15 | trends slope (null → redistributed) |
| `W_CHANNEL_FIT` | 0.20 | token overlap with your top-20 videos |
| `W_COMPETITION_NO_TREND` | 0.50 | used when momentum is null |
| `W_DEMAND_NO_TREND` | 0.30 | used when momentum is null |
