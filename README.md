# SJU Public Phone Crawler

A conservative, resumable crawler that inventories publicly displayed US/Canada-style phone numbers on `sju.edu` and discovered `*.sju.edu` pages.

## Safety and scope

- Fetches only HTTP(S) URLs whose host is `sju.edu` or ends in `.sju.edu`.
- Enforces each origin's live `robots.txt` rules.
- Fails closed when robots.txt cannot be evaluated unless the operator explicitly supplies `--robots-fail-open`.
- Uses a minimum one-second per-origin delay by default and honors longer robots crawl delays.
- Does not authenticate, evade controls, submit forms, call numbers, or infer identities.
- Public availability does not guarantee accuracy or unrestricted downstream use. Review SJU's current terms and applicable privacy rules before using or sharing results.

At review time, `https://www.sju.edu/robots.txt` allowed general crawling but disallowed system, account, internal-search, API, and other paths. The crawler evaluates the live file rather than embedding that snapshot.

## Install

Python 3.14 is required.

```bash
cd /home/kame/sju-phone-crawler
python3.14 -m venv .venv
.venv/bin/python -m pip install -e .
```

## Conservative test run

```bash
sju-phone-crawler \
  --fresh \
  --max-pages 10 \
  --delay 1.5 \
  --out-dir sample-results
```

## Full or resumed run

```bash
sju-phone-crawler --max-pages 25000 --delay 1.0 --out-dir sju_results
```

Rerun the same command to resume. The SQLite state file defaults to `OUT_DIR/crawl_state.sqlite3`. Use `--fresh` only when you intentionally want to discard prior state.

A custom seed replaces the defaults:

```bash
sju-phone-crawler \
  --seed https://www.sju.edu/offices/ \
  --max-pages 500 \
  --delay 1.5 \
  --out-dir office-results
```

## Outputs

- `sju_phone_occurrences.csv` — one row per distinct phone/source/context occurrence
- `sju_phone_unique.csv` — one row per normalized phone with source aggregation
- `sju_crawl_errors.csv` — distinct URL-level errors
- `crawl_state.sqlite3` — resumable queue and durable crawl state
- `crawl_state.sqlite3.lock` — advisory lock preventing concurrent writers
- `~/.hermes/logs/sju-phone-crawler.log` — operational log

CSV output uses UTF-8 with BOM for spreadsheet compatibility.

## Important options

| Option | Default | Purpose |
|---|---:|---|
| `--max-pages` | 25000 | Total processed URL cap, including prior resumed work |
| `--delay` | 1.0 | Minimum seconds between requests to the same origin; minimum accepted value is 0.25 |
| `--timeout` | 20 | Per-request timeout in seconds |
| `--retries` | 2 | Retry count for connection errors, 429, and transient 5xx responses |
| `--max-bytes` | 25000000 | Maximum downloaded bytes per response |
| `--max-pdf-pages` | 200 | Maximum pages extracted from one PDF |
| `--max-query-variants` | 20 | Maximum queued query variants for the same origin/path |
| `--checkpoint-every` | 25 | Export CSV snapshots after this many processed pages |
| `--robots-fail-open` | off | Explicitly allow crawling when robots cannot be evaluated; avoid unless authorized |
| `--fresh` | off | Delete the selected SQLite crawl state before starting |

## Tests

```bash
cd /home/kame/sju-phone-crawler
.venv/bin/python -m unittest discover -s tests -v
```

## Data interpretation

The crawler recognizes optional `+1`, three-digit area code, exchange, and four-digit subscriber number. It normalizes matches as `AAA-BBB-CCCC`. It does not currently retain extension numbers. Phone-like numeric strings may be false positives; inspect `source_url` and `context` before relying on them.
