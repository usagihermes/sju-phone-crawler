# Controlled crawl analysis

## Test design

The crawler was run from both default seeds with a **1.5-second minimum per-origin delay** and a total cap of 10 processed pages. It was then run again without `--fresh`, raising the cap to 12. The second invocation began from 10 completed pages and processed exactly two more pages, demonstrating that the SQLite queue and results resume correctly.

A separate live robots test used `https://www.sju.edu/user/login`, a path disallowed by SJU's robots file. The crawler classified it as `blocked` without requesting the protected page.

## Results after resumed run

| Metric | Result |
|---|---:|
| URLs completed | 12 |
| URLs pending for a future resume | 202 |
| Distinct occurrence rows | 26 |
| Unique normalized phones | 2 |
| Crawl errors | 0 |
| Source type | HTML only |
| CSV/SQLite count agreement | Yes |

## Numbers observed

| Phone | Distinct occurrences | Distinct source URLs | Interpretation from sampled context |
|---|---:|---:|---|
| `610-660-1000` | 24 | 12 | SJU's main institutional number, repeated in page footers and `tel:` links |
| `610-660-1300` | 2 | 1 | Transfer-admission contact shown on the transfer page |

The two occurrences per page for some numbers are not database duplicates: one comes from visible page text and another from a `tel:` link, with different context. The unique CSV correctly collapses them by phone and source URL.

## Queue observations

Most discovered URLs belong to `www.sju.edu`. The crawler also discovered SJU subdomains including `directory`, `admission`, `sites`, `nest`, `magazine`, and `hawkssb`. Query-driven registration links triggered the configured per-path variant cap, demonstrating crawl-trap control.

## Limitations

This is deliberately a small functional test, not a complete inventory. Only 12 pages were fetched and no PDF was encountered. The results can contain stale numbers, shared switchboards, fax lines, examples, or other numeric false positives. Source context must be reviewed before any downstream use.
