# Review of the supplied crawler

## Summary

The original script was readable and correctly limited discovered links to `sju.edu` and its subdomains. It removed script/style content, handled HTML and PDFs, normalized common North American phone formats, retained source URLs and context, and produced useful occurrence and unique-number CSVs.

## Material issues found

1. **robots.txt was not enforced.** The header asked the operator to review it, but the crawler never downloaded or evaluated it.
2. **The delay was global and unconditional.** It did not respect per-host `Crawl-delay`, and retries/backoff/`Retry-After` were absent.
3. **No resumable state.** A failure, reboot, or interruption discarded the queue and all in-memory results.
4. **Duplicate occurrences could accumulate.** The same number/source/context had no durable uniqueness constraint.
5. **Dropping every query could omit real content.** Pagination and directory filters can be query-driven. Conversely, preserving queries without a cap risks crawl traps.
6. **Redirect aliases were not represented in crawl state.** This could cause repeated work and made diagnostics weaker.
7. **No response-size or PDF-page limit.** An unexpectedly large or malformed resource could consume excessive memory or CPU.
8. **No retry policy.** Temporary 429/5xx and connection failures were immediately recorded as final errors.
9. **Errors and results were written only at completion.** A crash before export lost them.
10. **`--seed` appended to defaults.** Supplying a custom seed did not replace the two defaults, which is surprising for controlled tests.
11. **Telephone links were not inventoried if their number was absent from visible anchor text.**
12. **No durable operational log or packaging metadata.** Reproducing the environment and diagnosing a long run were harder than necessary.

## Improvements implemented

- Per-origin robots.txt parsing and enforcement, including RFC-compatible handling of blank/comment lines between a user-agent and its directives; fails closed when robots is unavailable unless `--robots-fail-open` is explicitly selected.
- Per-origin rate scheduling using the greater of `--delay` and robots `Crawl-delay`.
- Retry/backoff for 429 and transient 5xx responses, including `Retry-After` support.
- SQLite WAL state for resumable queue, statuses, errors, and occurrences.
- An advisory state-file lock that prevents concurrent crawler processes from corrupting or replacing the same SQLite state.
- Durable uniqueness constraint for occurrence deduplication across restarts.
- Meaningful query preservation with tracking-parameter removal and a per-path query-variant cap.
- Bounded downloads and bounded PDF page extraction.
- Checkpoint CSV exports and a persistent log under `~/.hermes/logs/`.
- Extraction from both visible text and `tel:` links.
- Python 3.14 project metadata, requirements, tests, and usage documentation.

## Scope and cautions

The tool inventories publicly presented phone numbers; it does not validate ownership, infer identity, place calls, or bypass access controls. Results can include stale numbers, fax lines, examples, or numbers embedded in historical PDFs. Human review is required before downstream use. Site terms and robots rules can change, so each run must honor the live policies.
