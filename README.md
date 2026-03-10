# Tixlytics Take-Home: Bid Engine

A trading bid engine for secondary ticket markets. Reads market data and outputs section-level bid decisions to maximize risk-adjusted profit.

## Usage

```bash
python bid_engine.py [path/to/events.json]
```

Defaults to `events.json` in the current directory. Outputs bid decisions to stdout.

No external dependencies — standard library only (Python 3.10+).

## Files

- **bid_engine.py** — The bid engine script (single file, reads events.json, outputs decisions)
- **events.json** — Market data for three events
- **WRITEUP.md** — Approach, trade-offs, AI usage, and production considerations
