"""
Scopus Profile Scraper
======================
Scrapes h-index, documents, citations, country, affiliation, and ORCID
from Scopus author profile pages listed in scholar_full_sync_output.csv.

Requirements:
    pip install playwright playwright-stealth pandas tqdm
    playwright install chromium

Usage:
    python scopus_scraper.py
    python scopus_scraper.py --input my_data.csv --output results.csv
    python scopus_scraper.py --proxy "http://user:pass@host:port"
    python scopus_scraper.py --concurrency 3 --delay 4

The script reads `scopus_id` or `scopus_url` columns to find profiles.
Results are written incrementally so progress is never lost on crash.
"""

import argparse
import csv
import json
import logging
import random
import re
import time
from pathlib import Path

import pandas as pd
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from playwright_stealth import Stealth

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("scopus")

# ── Constants ─────────────────────────────────────────────────────────────────
SCOPUS_AUTHOR_BASE = "https://www.scopus.com/authid/detail.uri?authorId={}"
DEFAULT_TIMEOUT = 30_000   # ms
PAGE_SETTLE    = 6_000     # ms – wait after navigation for JS to render
MAX_RETRIES    = 3

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]


# ── Selectors / extraction helpers ───────────────────────────────────────────

def _text(page, selector: str, default="") -> str:
    """Safe inner-text extraction."""
    try:
        el = page.query_selector(selector)
        return el.inner_text().strip() if el else default
    except Exception:
        return default


def _attr(page, selector: str, attr: str, default="") -> str:
    try:
        el = page.query_selector(selector)
        return (el.get_attribute(attr) or "").strip() if el else default
    except Exception:
        return default


def extract_metrics(page) -> dict:
    """
    Pull structured data from a rendered Scopus author page.
    Scopus is an Angular SPA – we try multiple selector strategies and fall
    back to regex over the raw HTML so the script stays resilient across
    minor layout changes.
    """
    data = {
        "scopus_h_index": "",
        "scopus_documents": "",
        "scopus_citations": "",
        "scopus_affiliation": "",
        "scopus_country": "",
        "scopus_orcid": "",
        "scopus_name": "",
        "scrape_status": "ok",
    }

    html = page.content()

    # ── Name ─────────────────────────────────────────────────────────────────
    name = (
        _text(page, "h1.author-profile-name")
        or _text(page, "[data-testid='author-name']")
        or _text(page, ".nameHeading")
        or _text(page, "h1")
    )
    data["scopus_name"] = name

    # ── Affiliation / Country ─────────────────────────────────────────────────
    affil = (
        _text(page, "[data-testid='affiliation-name']")
        or _text(page, ".affiliation-name")
        or _text(page, "span.affiliation")
        or _text(page, "[class*='affiliation']")
    )
    country = (
        _text(page, "[data-testid='affiliation-country']")
        or _text(page, ".affiliation-country")
        or _text(page, "[class*='country']")
    )

    # Regex fallback in HTML
    if not affil:
        m = re.search(r'"affiliationName"\s*:\s*"([^"]+)"', html)
        if m:
            affil = m.group(1)
    if not country:
        m = re.search(r'"country"\s*:\s*"([^"]+)"', html)
        if m:
            country = m.group(1)

    data["scopus_affiliation"] = affil
    data["scopus_country"] = country

    # ── ORCID ─────────────────────────────────────────────────────────────────
    orcid = (
        _attr(page, "a[href*='orcid.org']", "href")
        or _text(page, "[data-testid='orcid']")
        or _text(page, "[class*='orcid']")
    )
    if not orcid:
        m = re.search(r'orcid\.org/([\d\-X]{16,19})', html)
        if m:
            orcid = m.group(1)
    # Normalise: keep just the ID
    orcid = re.sub(r'https?://orcid\.org/', '', orcid).strip()
    data["scopus_orcid"] = orcid

    # ── Metrics: h-index, documents, citations ────────────────────────────────
    #
    # Strategy 1 – look for the metric cards rendered by the SPA
    metric_cards = page.query_selector_all("[class*='metric'], [class*='Metric'], [data-testid*='metric']")
    for card in metric_cards:
        label = card.inner_text().lower()
        value_el = card.query_selector("[class*='value'], [class*='count'], strong, span")
        value = value_el.inner_text().strip() if value_el else card.inner_text().strip()
        num = re.search(r'[\d,]+', value)
        num_str = num.group().replace(",", "") if num else ""
        if "h-index" in label or "h index" in label:
            data["scopus_h_index"] = num_str
        elif "document" in label:
            data["scopus_documents"] = num_str
        elif "citation" in label:
            data["scopus_citations"] = num_str

    # Strategy 2 – labelled stat boxes (older Scopus layout)
    if not data["scopus_h_index"]:
        boxes = page.query_selector_all(".stat-box, .authorStat, [class*='authorStat']")
        for box in boxes:
            txt = box.inner_text().lower()
            nums = re.findall(r'[\d,]+', txt)
            if not nums:
                continue
            n = nums[0].replace(",", "")
            if "h-index" in txt or "h index" in txt:
                data["scopus_h_index"] = n
            elif "document" in txt:
                data["scopus_documents"] = n
            elif "citation" in txt:
                data["scopus_citations"] = n

    # Strategy 3 – JSON embedded in the page (Scopus sometimes embeds state)
    if not data["scopus_h_index"]:
        for pattern, key in [
            (r'"hIndex"\s*:\s*(\d+)', "scopus_h_index"),
            (r'"documentCount"\s*:\s*(\d+)', "scopus_documents"),
            (r'"citationCount"\s*:\s*(\d+)', "scopus_citations"),
            (r'"citedByCount"\s*:\s*(\d+)', "scopus_citations"),
        ]:
            m = re.search(pattern, html)
            if m and not data[key]:
                data[key] = m.group(1)

    # Strategy 4 – visible text scan as last resort
    if not data["scopus_h_index"]:
        full_text = page.inner_text("body")
        # Look for patterns like "h-index\n12" or "h-index 12"
        for label_pat, key in [
            (r'h[\-\s]index[\s\n:]*(\d+)', "scopus_h_index"),
            (r'documents[\s\n:]*(\d[\d,]*)', "scopus_documents"),
            (r'citations[\s\n:]*(\d[\d,]*)', "scopus_citations"),
        ]:
            m = re.search(label_pat, full_text, re.IGNORECASE)
            if m and not data[key]:
                data[key] = m.group(1).replace(",", "")

    return data


# ── Browser / page helpers ────────────────────────────────────────────────────

def build_context(playwright, proxy: str | None, headless: bool):
    """Create a stealth browser context."""
    launch_args = {
        "headless": headless,
        "args": [
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
        ],
    }
    if proxy:
        launch_args["proxy"] = {"server": proxy}

    browser = playwright.chromium.launch(**launch_args)

    ctx_args = {
        "user_agent": random.choice(USER_AGENTS),
        "locale": "en-US",
        "timezone_id": "America/New_York",
        "viewport": {"width": 1280, "height": 800},
        "java_script_enabled": True,
        "permissions": [],
        "extra_http_headers": {
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        },
    }
    if proxy:
        # proxy already set at browser level; don't set again here
        pass

    context = browser.new_context(**ctx_args)

    # Apply stealth patches
    stealth = Stealth()
    stealth.apply_stealth_sync(context)

    # Block images/fonts/media to speed up loading
    context.route(
        "**/*",
        lambda route: route.abort()
        if route.request.resource_type in ("image", "media", "font")
        else route.continue_(),
    )

    return browser, context


def scrape_profile(page, url: str, delay: float) -> dict:
    """Navigate to a Scopus author URL and extract metrics. Retries on failure."""
    result = {"scrape_status": "error", "scopus_url_scraped": url}

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            log.info(f"  → {url}  (attempt {attempt})")
            page.goto(url, timeout=DEFAULT_TIMEOUT, wait_until="domcontentloaded")

            # Let the Angular app finish rendering
            page.wait_for_timeout(PAGE_SETTLE + random.randint(0, 2000))

            # Check for bot-detection / login wall
            body_text = page.inner_text("body").lower()
            if any(kw in body_text for kw in ["captcha", "robot", "verify you are human", "access denied"]):
                log.warning("  ⚠ Bot-detection page detected")
                result["scrape_status"] = "bot_detected"
                break

            if "sign in" in body_text and len(body_text) < 2000:
                log.warning("  ⚠ Login wall detected")
                result["scrape_status"] = "login_required"
                break

            metrics = extract_metrics(page)
            result.update(metrics)
            result["scopus_url_scraped"] = url

            log.info(
                f"  ✓ h={result.get('scopus_h_index','?')}  "
                f"docs={result.get('scopus_documents','?')}  "
                f"cites={result.get('scopus_citations','?')}  "
                f"orcid={result.get('scopus_orcid','?') or '—'}"
            )

            # Polite delay between requests
            time.sleep(delay + random.uniform(0.5, 2.5))
            return result

        except PWTimeout:
            log.warning(f"  ✗ Timeout on attempt {attempt}")
            if attempt < MAX_RETRIES:
                time.sleep(5 * attempt)
        except Exception as exc:
            log.error(f"  ✗ Error on attempt {attempt}: {exc}")
            if attempt < MAX_RETRIES:
                time.sleep(5 * attempt)

    return result


# ── Main ──────────────────────────────────────────────────────────────────────

def build_url(row: pd.Series) -> str | None:
    """Derive the Scopus author URL from available columns."""
    # Prefer explicit scopus_url column
    url = str(row.get("scopus_url", "")).strip()
    if url and url.startswith("http"):
        return url

    # Fall back to scopus_id
    sid = str(row.get("scopus_id", "")).strip()
    if sid and sid not in ("", "nan", "None"):
        return SCOPUS_AUTHOR_BASE.format(sid)

    return None


def main():
    parser = argparse.ArgumentParser(description="Scrape Scopus author profiles")
    parser.add_argument("--input",  default="scholar_full_sync_output.csv", help="Input CSV file")
    parser.add_argument("--output", default="scopus_scraped_results.csv",   help="Output CSV file")
    parser.add_argument("--proxy",  default=None, help="Proxy URL e.g. http://@43.130.47.27:21127")
    parser.add_argument("--delay",  type=float, default=3.0, help="Base delay (s) between requests")
    parser.add_argument("--headless", action="store_true", default=True)
    parser.add_argument("--no-headless", dest="headless", action="store_false",
                        help="Show browser window (useful for debugging)")
    parser.add_argument("--limit",  type=int, default=None, help="Only process N rows (for testing)")
    args = parser.parse_args()

    # ── Load input ────────────────────────────────────────────────────────────
    input_path = Path(args.input)
    if not input_path.exists():
        log.error(f"Input file not found: {input_path}")
        raise SystemExit(1)

    df = pd.read_csv(input_path, dtype=str).fillna("")
    log.info(f"Loaded {len(df)} rows from {input_path}")

    if args.limit:
        df = df.head(args.limit)
        log.info(f"Limited to first {args.limit} rows")

    # ── Output: resume support ────────────────────────────────────────────────
    output_path = Path(args.output)
    already_done: set[str] = set()

    # New columns we'll add
    new_cols = [
        "scopus_name", "scopus_h_index", "scopus_documents", "scopus_citations",
        "scopus_affiliation", "scopus_country", "scopus_orcid",
        "scrape_status", "scopus_url_scraped",
    ]

    if output_path.exists():
        done_df = pd.read_csv(output_path, dtype=str).fillna("")
        already_done = set(done_df.get("scopus_url_scraped", pd.Series()).dropna())
        log.info(f"Resuming – {len(already_done)} profiles already scraped")
        out_file = open(output_path, "a", newline="", encoding="utf-8")
        writer = csv.DictWriter(out_file, fieldnames=list(df.columns) + new_cols)
    else:
        out_file = open(output_path, "w", newline="", encoding="utf-8")
        writer = csv.DictWriter(out_file, fieldnames=list(df.columns) + new_cols)
        writer.writeheader()

    # ── Scrape ────────────────────────────────────────────────────────────────
    with sync_playwright() as playwright:
        browser, context = build_context(playwright, args.proxy, args.headless)
        page = context.new_page()

        try:
            for idx, row in df.iterrows():
                url = build_url(row)

                if not url:
                    log.warning(f"Row {idx}: no Scopus URL or ID – skipping")
                    out_row = row.to_dict()
                    for c in new_cols:
                        out_row[c] = ""
                    out_row["scrape_status"] = "no_url"
                    writer.writerow(out_row)
                    out_file.flush()
                    continue

                if url in already_done:
                    log.info(f"Row {idx}: already scraped → {url}")
                    continue

                name_hint = row.get("name", "") or row.get("s2_name", "") or f"row {idx}"
                log.info(f"[{idx+1}/{len(df)}] {name_hint}")

                metrics = scrape_profile(page, url, args.delay)

                out_row = row.to_dict()
                for c in new_cols:
                    out_row[c] = metrics.get(c, "")
                writer.writerow(out_row)
                out_file.flush()

        except KeyboardInterrupt:
            log.warning("Interrupted by user – progress saved.")
        finally:
            out_file.close()
            browser.close()

    log.info(f"Done. Results saved to {output_path}")


if __name__ == "__main__":
    main()