# Public Phone Crawler

A conservative, resumable crawler that inventories publicly displayed US/Canada-style phone numbers on any domain you specify.

## Safety and scope

- Fetches only HTTP(S) URLs whose host matches your `--domain` list (or is inferred from `--seed` URLs).
- Enforces each origin's live `robots.txt` rules.
- Fails closed when robots.txt cannot be evaluated unless the operator explicitly supplies `--robots-fail-open`.
- Uses a minimum one-second per-origin delay by default and honors longer robots crawl delays.
- Does not authenticate, evade controls, submit forms, call numbers, or infer identities.
- Public availability does not guarantee accuracy or unrestricted downstream use. Review the target site's terms and applicable privacy rules before using or sharing results.

The crawler evaluates the live `robots.txt` rather than embedding a snapshot.

## Install

Python 3.11+ is required.

```bash
git clone https://github.com/usagihermes/sju-phone-crawler
cd sju-phone-crawler
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

Or run directly without installing:

```bash
.venv/bin/python sju_phone_crawler.py --help
```

## Quick start (SJU example)

```bash
python sju_phone_crawler.py \
  --domain sju.edu \
  --seed https://www.sju.edu/ \
  --seed https://directory.sju.edu/ \
  --fresh \
  --max-pages 10 \
  --delay 1.5 \
  --out-dir sample-results
```

## General usage

```bash
python sju_phone_crawler.py \
  --domain example.com \
  --seed https://www.example.com/ \
  --max-pages 500 \
  --delay 1.0 \
  --out-dir example-results
```

- `--domain` can be repeated for multiple domains (e.g., `--domain example.com --domain sub.example.com`)
- If `--domain` is omitted, domains are inferred from `--seed` URLs
- At least one `--seed` is required
- Rerun the same command to resume. The SQLite state file defaults to `OUT_DIR/crawl_state.sqlite3`. Use `--fresh` only when you intentionally want to discard prior state.

## Outputs

- `phone_occurrences.csv` — one row per distinct phone/source/context occurrence
- `phone_unique.csv` — one row per normalized phone with source aggregation
- `crawl_errors.csv` — distinct URL-level errors
- `crawl_state.sqlite3` — resumable queue and durable crawl state
- `crawl_state.sqlite3.lock` — advisory lock preventing concurrent writers
- `~/.phone-crawler/logs/phone-crawler.log` — operational log

CSV output uses UTF-8 with BOM for spreadsheet compatibility.

## Important options

| Option | Default | Purpose |
|---|---:|---|
| `--domain` | (inferred from seeds) | Allowed domain(s); repeat for multiple |
| `--seed` | required | Seed URL; repeat as needed |
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
.venv/bin/python -m unittest discover -s tests -v
```

## Data interpretation

The crawler recognizes optional `+1`, three-digit area code, exchange, and four-digit subscriber number. It normalizes matches as `AAA-BBB-CCCC`. It does not currently retain extension numbers. Phone-like numeric strings may be false positives; inspect `source_url` and `context` before relying on them.