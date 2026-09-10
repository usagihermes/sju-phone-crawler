#!/usr/bin/env python3.14
"""Respectful, resumable public phone inventory crawler for sju.edu."""

from __future__ import annotations

import argparse
import csv
import fcntl
import io
import logging
import re
import sqlite3
import time
import urllib.robotparser
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any
from urllib.parse import (
    parse_qsl,
    unquote,
    urlencode,
    urldefrag,
    urljoin,
    urlsplit,
    urlunsplit,
)

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?1[\s.\-()]*)?"
    r"(?:\((\d{3})\)|(\d{3}))[\s.\-]*"
    r"(\d{3})[\s.\-]*(\d{4})(?!\d)"
)
SKIP_EXTENSIONS = {
    ".avi", ".css", ".eot", ".gif", ".gz", ".ico", ".jpeg", ".jpg",
    ".js", ".mov", ".mp3", ".mp4", ".png", ".svg", ".tar", ".ttf",
    ".webp", ".woff", ".woff2", ".zip",
}
TRACKING_PARAMETERS = {
    "fbclid", "gclid", "mc_cid", "mc_eid", "msclkid", "ref", "source",
}
DEFAULT_SEEDS = ("https://www.sju.edu/", "https://directory.sju.edu/")
DEFAULT_USER_AGENT = "SJU-Public-Phone-Inventory/2.0 (public research; respectful crawler)"


def allowed_host(host: str | None) -> bool:
    normalized = (host or "").lower().rstrip(".")
    return normalized == "sju.edu" or normalized.endswith(".sju.edu")


def canonicalize(url: str) -> str | None:
    """Return a stable HTTP(S) URL while retaining meaningful query values."""
    url, _ = urldefrag(url.strip())
    try:
        parts = urlsplit(url)
        scheme = parts.scheme.lower()
        if scheme not in {"http", "https"} or not parts.hostname:
            return None
        host = parts.hostname.lower().rstrip(".")
        port = parts.port
    except ValueError:
        return None

    if parts.username or parts.password:
        return None
    netloc = host
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{host}:{port}"
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    query_items = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING_PARAMETERS
    ]
    query = urlencode(sorted(query_items))
    return urlunsplit((scheme, netloc, path, query, ""))


def extension_of(url: str) -> str:
    suffix = Path(urlsplit(url).path.lower()).suffix
    return suffix


def normalize_match(match: re.Match[str]) -> str:
    area = match.group(1) or match.group(2)
    return f"{area}-{match.group(3)}-{match.group(4)}"


def extract_phones(text: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for match in PHONE_RE.finditer(text or ""):
        start = max(0, match.start() - 90)
        end = min(len(text), match.end() + 90)
        snippet = " ".join(text[start:end].split())
        found.append((normalize_match(match), snippet))
    return found


def html_text_links(content: bytes, base_url: str) -> tuple[str, list[str]]:
    soup = BeautifulSoup(content, "html.parser")
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()

    visible_text = soup.get_text(" ", strip=True)
    tel_values: list[str] = []
    links: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        raw_href = anchor["href"].strip()
        if raw_href.lower().startswith("tel:"):
            tel_values.append(unquote(raw_href[4:]))
            continue
        joined = urljoin(base_url, raw_href)
        normalized = canonicalize(joined)
        if normalized and allowed_host(urlsplit(normalized).hostname):
            links.add(normalized)

    searchable_text = " ".join([visible_text, *tel_values])
    return searchable_text, sorted(links)


def pdf_text(content: bytes, max_pages: int) -> str:
    reader = PdfReader(io.BytesIO(content))
    if reader.is_encrypted:
        raise ValueError("encrypted PDF")
    pages = reader.pages[:max_pages]
    return "\n".join(page.extract_text() or "" for page in pages)


class RobotsCache:
    """Fetch and cache robots.txt independently for every origin."""

    def __init__(
        self,
        session: Any,
        user_agent: str,
        timeout: float,
        *,
        fail_open: bool = False,
    ) -> None:
        self.session = session
        self.user_agent = user_agent
        self.timeout = timeout
        self.fail_open = fail_open
        self._cache: dict[str, urllib.robotparser.RobotFileParser] = {}

    @staticmethod
    def _origin(url: str) -> str:
        parts = urlsplit(url)
        return f"{parts.scheme}://{parts.netloc}"

    def _load(self, url: str) -> urllib.robotparser.RobotFileParser:
        origin = self._origin(url)
        if origin in self._cache:
            return self._cache[origin]

        robots_url = f"{origin}/robots.txt"
        parser = urllib.robotparser.RobotFileParser(robots_url)
        try:
            response = self.session.get(
                robots_url,
                timeout=self.timeout,
                allow_redirects=True,
            )
            if response.status_code == 404:
                parser.parse(["User-agent: *", "Allow: /"])
            elif response.status_code == 200:
                # RFC 9309 treats blank and comment-only lines as ignorable.
                # urllib.robotparser can terminate an otherwise valid group
                # when a site places them between User-agent and directives.
                normalized_lines = []
                for raw_line in response.text.splitlines():
                    directive = raw_line.split("#", 1)[0].strip()
                    if directive:
                        normalized_lines.append(directive)
                parser.parse(normalized_lines)
            elif self.fail_open:
                parser.parse(["User-agent: *", "Allow: /"])
            else:
                parser.parse(["User-agent: *", "Disallow: /"])
        except requests.RequestException:
            directive = "Allow: /" if self.fail_open else "Disallow: /"
            parser.parse(["User-agent: *", directive])
        self._cache[origin] = parser
        return parser

    def can_fetch(self, url: str) -> bool:
        parser = self._load(url)
        return parser.can_fetch(self.user_agent, url)

    def crawl_delay(self, url: str) -> float:
        parser = self._load(url)
        delay = parser.crawl_delay(self.user_agent)
        if delay is None:
            delay = parser.crawl_delay("*")
        return float(delay or 0)


class RequestScheduler:
    """Enforce a minimum request interval independently for each origin."""

    def __init__(
        self,
        minimum_delay: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.minimum_delay = minimum_delay
        self.clock = clock
        self.sleep = sleep
        self._last_request: dict[str, float] = {}

    def wait(self, url: str, robots_delay: float) -> None:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        required_delay = max(self.minimum_delay, robots_delay)
        now = self.clock()
        previous = self._last_request.get(origin)
        if previous is not None:
            remaining = required_delay - (now - previous)
            if remaining > 0:
                self.sleep(remaining)
        self._last_request[origin] = self.clock()


class CrawlStore:
    """SQLite-backed queue and result store for crash-safe resumption."""

    TERMINAL_STATUSES = ("done", "error", "blocked", "skipped")

    def __init__(self, path: Path, *, fresh: bool = False) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_path = Path(f"{self.path}.lock")
        self._lock_handle = self._lock_path.open("a+")
        try:
            fcntl.flock(self._lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._lock_handle.close()
            raise RuntimeError(f"crawl state is already in use: {self.path}") from exc

        try:
            if fresh:
                for candidate in (
                    self.path,
                    Path(f"{self.path}-wal"),
                    Path(f"{self.path}-shm"),
                ):
                    candidate.unlink(missing_ok=True)
            self.connection = sqlite3.connect(self.path)
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA synchronous=NORMAL")
            self._create_schema()
            self.connection.execute("UPDATE urls SET status='pending' WHERE status='fetching'")
            self.connection.commit()
        except Exception:
            fcntl.flock(self._lock_handle.fileno(), fcntl.LOCK_UN)
            self._lock_handle.close()
            raise

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS urls (
                url TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'pending',
                discovered_from TEXT,
                final_url TEXT,
                content_type TEXT,
                last_error TEXT,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS occurrences (
                phone TEXT NOT NULL,
                source_url TEXT NOT NULL,
                source_type TEXT NOT NULL,
                context TEXT NOT NULL,
                UNIQUE(phone, source_url, source_type, context)
            );
            CREATE TABLE IF NOT EXISTS errors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                error_type TEXT NOT NULL,
                error TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(url, error_type, error)
            );
            """
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()
        fcntl.flock(self._lock_handle.fileno(), fcntl.LOCK_UN)
        self._lock_handle.close()

    def enqueue(self, url: str, discovered_from: str | None = None) -> bool:
        cursor = self.connection.execute(
            "INSERT OR IGNORE INTO urls(url, discovered_from) VALUES (?, ?)",
            (url, discovered_from),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def next_pending(self) -> str | None:
        row = self.connection.execute(
            "SELECT url FROM urls WHERE status='pending' ORDER BY rowid LIMIT 1"
        ).fetchone()
        if not row:
            return None
        url = str(row[0])
        self.connection.execute(
            "UPDATE urls SET status='fetching', updated_at=CURRENT_TIMESTAMP WHERE url=?",
            (url,),
        )
        self.connection.commit()
        return url

    def mark(
        self,
        url: str,
        status: str,
        *,
        final_url: str | None = None,
        content_type: str | None = None,
        error: str | None = None,
    ) -> None:
        self.connection.execute(
            """
            UPDATE urls
               SET status=?, final_url=?, content_type=?, last_error=?,
                   updated_at=CURRENT_TIMESTAMP
             WHERE url=?
            """,
            (status, final_url, content_type, error, url),
        )
        self.connection.commit()

    def add_occurrence(self, phone: str, url: str, kind: str, snippet: str) -> None:
        self.connection.execute(
            """
            INSERT OR IGNORE INTO occurrences(phone, source_url, source_type, context)
            VALUES (?, ?, ?, ?)
            """,
            (phone, url, kind, snippet),
        )
        self.connection.commit()

    def add_error(self, url: str, error_type: str, error: str) -> None:
        self.connection.execute(
            "INSERT OR IGNORE INTO errors(url, error_type, error) VALUES (?, ?, ?)",
            (url, error_type, error[:500]),
        )
        self.connection.commit()

    def occurrence_count(self) -> int:
        return int(self.connection.execute("SELECT COUNT(*) FROM occurrences").fetchone()[0])

    def unique_phone_count(self) -> int:
        return int(
            self.connection.execute("SELECT COUNT(DISTINCT phone) FROM occurrences").fetchone()[0]
        )

    def processed_count(self) -> int:
        placeholders = ",".join("?" for _ in self.TERMINAL_STATUSES)
        return int(
            self.connection.execute(
                f"SELECT COUNT(*) FROM urls WHERE status IN ({placeholders})",
                self.TERMINAL_STATUSES,
            ).fetchone()[0]
        )

    def pending_count(self) -> int:
        return int(
            self.connection.execute("SELECT COUNT(*) FROM urls WHERE status='pending'").fetchone()[0]
        )

    def query_variant_count(self, url: str) -> int:
        parts = urlsplit(url)
        prefix = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
        return int(
            self.connection.execute(
                "SELECT COUNT(*) FROM urls WHERE url=? OR url LIKE ?",
                (prefix, f"{prefix}?%"),
            ).fetchone()[0]
        )

    def export_csv(self, out_dir: Path) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        occurrence_rows = self.connection.execute(
            """
            SELECT phone, source_url, source_type, context
              FROM occurrences
             ORDER BY phone, source_url, context
            """
        ).fetchall()
        write_csv(
            out_dir / "sju_phone_occurrences.csv",
            ["phone", "source_url", "source_type", "context"],
            occurrence_rows,
        )

        grouped: dict[str, dict[str, Any]] = {}
        for phone, url, kind, snippet in occurrence_rows:
            entry = grouped.setdefault(phone, {"urls": set(), "types": set(), "snips": []})
            entry["urls"].add(url)
            entry["types"].add(kind)
            if snippet not in entry["snips"] and len(entry["snips"]) < 3:
                entry["snips"].append(snippet)
        unique_rows = [
            [
                phone,
                len(data["urls"]),
                " | ".join(sorted(data["types"])),
                " | ".join(sorted(data["urls"])),
                " || ".join(data["snips"]),
            ]
            for phone, data in sorted(grouped.items())
        ]
        write_csv(
            out_dir / "sju_phone_unique.csv",
            ["phone", "source_count", "source_types", "source_urls", "sample_context"],
            unique_rows,
        )
        error_rows = self.connection.execute(
            "SELECT url, error_type, error FROM errors ORDER BY id"
        ).fetchall()
        write_csv(
            out_dir / "sju_crawl_errors.csv",
            ["url", "error_type", "error"],
            error_rows,
        )


def write_csv(path: Path, headers: list[str], rows: Iterable[Iterable[Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        writer.writerows(rows)


def build_session(user_agent: str, retries: int) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": user_agent,
            "Accept": "text/html,application/pdf;q=0.9,*/*;q=0.1",
        }
    )
    retry = Retry(
        total=retries,
        connect=retries,
        read=retries,
        status=retries,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "HEAD"}),
        backoff_factor=1.0,
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def read_limited(response: requests.Response, maximum_bytes: int) -> bytes:
    length = response.headers.get("content-length")
    if length and int(length) > maximum_bytes:
        raise ValueError(f"response exceeds --max-bytes ({length} > {maximum_bytes})")
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_content(chunk_size=65536):
        if not chunk:
            continue
        total += len(chunk)
        if total > maximum_bytes:
            raise ValueError(f"response exceeds --max-bytes ({total} > {maximum_bytes})")
        chunks.append(chunk)
    return b"".join(chunks)


def setup_logging(log_file: Path, verbose: bool) -> logging.Logger:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("sju_phone_crawler")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.handlers.clear()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    if verbose:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)
    return logger


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", action="append", help="Seed URL; repeat as needed")
    parser.add_argument("--max-pages", type=int, default=25000, help="Total terminal URL cap")
    parser.add_argument("--delay", type=float, default=1.0, help="Minimum seconds per host")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--out-dir", type=Path, default=Path("sju_results"))
    parser.add_argument("--state-file", type=Path, help="SQLite state path")
    parser.add_argument("--fresh", action="store_true", help="Discard prior crawl state")
    parser.add_argument("--checkpoint-every", type=int, default=25)
    parser.add_argument("--max-bytes", type=int, default=25_000_000)
    parser.add_argument("--max-pdf-pages", type=int, default=200)
    parser.add_argument("--max-query-variants", type=int, default=20)
    parser.add_argument("--robots-fail-open", action="store_true")
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT)
    parser.add_argument(
        "--log-file",
        type=Path,
        default=Path.home() / ".hermes" / "logs" / "sju-phone-crawler.log",
    )
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


def validate_args(args: argparse.Namespace) -> None:
    if args.max_pages < 1:
        raise ValueError("--max-pages must be at least 1")
    if args.delay < 0.25:
        raise ValueError("--delay must be at least 0.25 seconds")
    if args.timeout <= 0 or args.max_bytes < 1024 or args.max_pdf_pages < 1:
        raise ValueError("timeout and content limits must be positive")
    if args.max_query_variants < 1:
        raise ValueError("--max-query-variants must be at least 1")


def run(args: argparse.Namespace) -> CrawlStore:
    validate_args(args)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    state_file = args.state_file or args.out_dir / "crawl_state.sqlite3"
    logger = setup_logging(args.log_file, args.verbose)
    store = CrawlStore(state_file, fresh=args.fresh)
    session = build_session(args.user_agent, args.retries)
    robots = RobotsCache(
        session,
        args.user_agent,
        args.timeout,
        fail_open=args.robots_fail_open,
    )
    scheduler = RequestScheduler(args.delay)

    seeds = args.seed or list(DEFAULT_SEEDS)
    for seed in seeds:
        normalized = canonicalize(seed)
        if not normalized or not allowed_host(urlsplit(normalized).hostname):
            raise ValueError(f"seed is outside sju.edu or invalid: {seed}")
        store.enqueue(normalized)

    completed_at_start = store.processed_count()
    processed_this_run = 0
    logger.info("crawl start state=%s completed=%d", state_file, completed_at_start)

    try:
        while store.processed_count() < args.max_pages:
            url = store.next_pending()
            if url is None:
                break
            if extension_of(url) in SKIP_EXTENSIONS:
                store.mark(url, "skipped", error="excluded extension")
                continue
            if not robots.can_fetch(url):
                store.mark(url, "blocked", error="robots.txt disallow or unavailable")
                logger.info("robots blocked %s", url)
                continue

            try:
                scheduler.wait(url, robots.crawl_delay(url))
                response = session.get(
                    url,
                    timeout=args.timeout,
                    allow_redirects=True,
                    stream=True,
                )
                final_url = canonicalize(response.url)
                if not final_url or not allowed_host(urlsplit(final_url).hostname):
                    store.mark(url, "skipped", final_url=response.url, error="redirect outside sju.edu")
                    response.close()
                    continue
                response.raise_for_status()
                content_type = (response.headers.get("content-type") or "").lower()
                content = read_limited(response, args.max_bytes)
                response.close()

                is_pdf = "application/pdf" in content_type or extension_of(final_url) == ".pdf"
                if is_pdf:
                    text = pdf_text(content, args.max_pdf_pages)
                    links: list[str] = []
                    kind = "PDF"
                elif "text/html" in content_type or not content_type:
                    text, links = html_text_links(content, final_url)
                    kind = "HTML"
                else:
                    store.mark(url, "skipped", final_url=final_url, content_type=content_type)
                    continue

                for phone, snippet in extract_phones(text):
                    store.add_occurrence(phone, final_url, kind, snippet)

                for link in links:
                    if extension_of(link) in SKIP_EXTENSIONS:
                        continue
                    if store.query_variant_count(link) >= args.max_query_variants:
                        logger.info("query variant cap skipped %s", link)
                        continue
                    store.enqueue(link, discovered_from=final_url)

                store.mark(url, "done", final_url=final_url, content_type=content_type)
            except Exception as exc:  # URL-level isolation is deliberate.
                error_text = str(exc)[:500]
                store.add_error(url, type(exc).__name__, error_text)
                store.mark(url, "error", error=error_text)
                logger.warning("%s %s: %s", url, type(exc).__name__, error_text)

            processed_this_run += 1
            if processed_this_run % args.checkpoint_every == 0:
                store.export_csv(args.out_dir)
                print(
                    f"processed_total={store.processed_count()} "
                    f"pending={store.pending_count()} "
                    f"occurrences={store.occurrence_count()} "
                    f"unique={store.unique_phone_count()}",
                    flush=True,
                )
    finally:
        store.export_csv(args.out_dir)
        session.close()

    logger.info(
        "crawl end processed_total=%d pending=%d occurrences=%d unique=%d",
        store.processed_count(),
        store.pending_count(),
        store.occurrence_count(),
        store.unique_phone_count(),
    )
    return store


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    store: CrawlStore | None = None
    try:
        store = run(args)
        print(
            "DONE: "
            f"{store.processed_count()} processed, "
            f"{store.pending_count()} pending, "
            f"{store.occurrence_count()} occurrences, "
            f"{store.unique_phone_count()} unique phones"
        )
        return 0
    except KeyboardInterrupt:
        print("Interrupted: checkpoint and CSV exports preserved.")
        return 130
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}")
        return 2
    finally:
        if store is not None:
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())
