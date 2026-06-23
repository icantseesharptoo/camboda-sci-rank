"""
fix_s2_profiles.py  –  Playwright edition (h-index priority selection)

Uses Playwright for ALL web access:
  - Google Scholar profiles  → scraped via Playwright (bypasses bot blocks)
  - Semantic Scholar API     → fetched via page.request (browser context,
                               avoids many 403/429 issues)

Candidate selection strategy
-----------------------------
When multiple S2 author candidates are returned for a name query, the script
now picks the candidate with the **highest h-index** (ties broken by citation
count), rather than relying primarily on paper-title overlap.

Title overlap is still computed and logged for transparency, but it is used
only as a soft sanity-check: if the top-h-index candidate has zero overlap
AND Scholar titles were successfully scraped, a warning is emitted so you can
review that row manually.

Logic
-----
Skip entirely if:
  - hindex_scholar < 2
  - h_index gap is "slightly" smaller than hindex_scholar
    (gap <= max(2, 0.30 * hindex_scholar))

Investigate if:
  - gap is "much" larger  (gap > max(2, 0.30 * hindex_scholar))
  - OR s2_author_id is missing

For investigation:
  1. Scrape Google Scholar profile page for paper titles.
  2. Search S2 API for the author by name.
  3. For each candidate, fetch h-index + citation count.
  4. Pick the candidate with the highest h-index (ties → most citations).
  5. Optionally log title-overlap as a sanity check.
  6. Fetch fresh metrics for the winner.

Output
------
merged_with_s2_metrics_fixed2_corrected.csv
  extra columns: s2_profile_url, fix_status
"""

import os
import sys
import time
import logging
import argparse
from typing import Optional

import pandas as pd
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────
S2_API_BASE        = "https://api.semanticscholar.org/graph/v1"
S2_API_KEY         = os.environ.get("S2_API_KEY", "s2k-FShcrmhCdVNMSOlsDvk1kvUMotFlTEIyIqqgE3Fl")
INPUT_FILE         = "merged_with_s2_metrics_fixed2.csv"
OUTPUT_FILE        = "merged_with_s2_metrics_fixed2_corrected.csv"

MAX_CANDIDATES     = 10   # fetch more so the h-index sort has a wider pool
MIN_OVERLAP_WARN   = 1    # warn (but don't reject) if overlap is below this
CLOSE_ABS          = 2
CLOSE_REL          = 0.30
# Delay between requests (seconds)
API_SLEEP          = 1.5
SCHOLAR_SLEEP      = 3.0

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Semantic Scholar via Playwright page.request
# ─────────────────────────────────────────────────────────────────────────────

def s2_api_get(page, path: str, params: dict = None) -> Optional[dict]:
    """
    Call the S2 Graph API using Playwright's page.request so the call shares
    the browser's TLS fingerprint and headers, reducing 403/429 risk.
    Retries up to 3 times on 429.
    """
    from urllib.parse import urlencode
    url = f"{S2_API_BASE}{path}"
    if params:
        url = f"{url}?{urlencode(params)}"

    headers = {"Accept": "application/json"}
    if S2_API_KEY:
        headers["x-api-key"] = S2_API_KEY

    for attempt in range(3):
        try:
            resp = page.request.get(url, headers=headers, timeout=25_000)
            if resp.status == 200:
                return resp.json()
            if resp.status == 429:
                wait = 15 * (attempt + 1)
                log.warning("S2 rate-limited, waiting %ss …", wait)
                time.sleep(wait)
                continue
            if resp.status == 404:
                return None
            log.warning("S2 returned HTTP %s for %s", resp.status, url)
            return None
        except Exception as exc:
            log.warning("S2 request error (attempt %d): %s", attempt + 1, exc)
            time.sleep(5)
    return None


def fetch_author_metrics(page, author_id: str) -> dict:
    """Fetch full metrics for a known author id."""
    data = s2_api_get(
        page,
        f"/author/{author_id}",
        {"fields": "hIndex,citationCount,paperCount,name"},
    )
    if not data:
        return {}
    return {
        "s2_name":        data.get("name", ""),
        "h_index":        data.get("hIndex"),
        "citation_count": data.get("citationCount"),
        "paper_count":    data.get("paperCount"),
    }


def fetch_author_papers(page, author_id: str, limit: int = 100) -> list[str]:
    """Return a list of lower-cased paper titles for the given S2 author."""
    data = s2_api_get(
        page,
        f"/author/{author_id}/papers",
        {"fields": "title", "limit": limit},
    )
    if not data:
        return []
    return [
        p["title"].lower().strip()
        for p in data.get("data", [])
        if p.get("title")
    ]


def search_s2_authors(page, name: str, affiliation: str = "") -> list[dict]:
    """
    Search S2 for author candidates.
    Returns up to MAX_CANDIDATES results, optionally reordered by affiliation
    match — but final selection is done by the caller using h-index.
    """
    data = s2_api_get(page, "/author/search", {
        "query":  name.strip(),
        "fields": "name,affiliations,hIndex,citationCount,paperCount,authorId",
        "limit":  MAX_CANDIDATES,
    })
    if not data:
        return []
    results = data.get("data", [])

    # Soft-sort by affiliation match so same-institution profiles bubble up
    # among candidates with similar h-indices, but h-index wins overall.
    if affiliation:
        aff_lower = affiliation.lower()[:20]
        def _aff_score(c):
            affs = " ".join(c.get("affiliations") or []).lower()
            return 1 if aff_lower in affs else 0
        results.sort(key=_aff_score, reverse=True)

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Google Scholar scraping via Playwright
# ─────────────────────────────────────────────────────────────────────────────

def fetch_scholar_papers_playwright(
    page, scholar_url: str, max_papers: int = 80
) -> list[str]:
    """
    Scrape paper titles from a Google Scholar author profile.
    Clicks 'Show more' repeatedly until we have enough titles or the button
    disappears / becomes disabled.
    Returns a list of lower-cased titles.
    """
    if not scholar_url or "user=" not in scholar_url:
        return []

    user_id = scholar_url.split("user=")[1].split("&")[0]
    url = (
        f"https://scholar.google.com/citations"
        f"?user={user_id}&sortby=pubdate&pagesize=100"
    )

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        time.sleep(SCHOLAR_SLEEP)

        titles: list[str] = []
        while len(titles) < max_papers:
            rows = page.query_selector_all("a.gsc_a_at")
            titles = [
                r.inner_text().lower().strip()
                for r in rows
                if r.inner_text().strip()
            ]

            if len(titles) >= max_papers:
                break

            btn = page.query_selector("#gsc_bpf_more:not([disabled])")
            if not btn:
                break
            btn.click()
            time.sleep(SCHOLAR_SLEEP)

        log.info("  Scraped %d Scholar titles.", len(titles))
        return titles[:max_papers]

    except PWTimeout:
        log.warning("  Timeout loading Scholar profile %s", scholar_url)
        return []
    except Exception as exc:
        log.warning("  Scholar scrape error: %s", exc)
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Decision helpers
# ─────────────────────────────────────────────────────────────────────────────

def should_skip(row: pd.Series) -> Optional[str]:
    """Return a skip-reason string, or None if the row needs investigation."""
    try:
        hs = float(row.get("hindex_scholar"))
    except (TypeError, ValueError):
        return "skipped_low_hindex"

    if hs < 2:
        return "skipped_low_hindex"

    s2_id = str(row.get("s2_author_id", "")).strip()
    if not s2_id or s2_id.lower() in ("nan", "none", ""):
        return None  # missing id → always investigate

    try:
        hs2 = float(row.get("h_index"))
    except (TypeError, ValueError):
        return None  # unknown S2 h-index → investigate

    gap = hs - hs2
    threshold = max(CLOSE_ABS, CLOSE_REL * hs)
    if gap <= threshold:
        return "skipped_close_match"

    return None  # large gap → investigate


def title_overlap(a: list[str], b: list[str]) -> int:
    return len(set(a) & set(b))


# ─────────────────────────────────────────────────────────────────────────────
# Core: find the best S2 profile
# ─────────────────────────────────────────────────────────────────────────────

def find_best_s2_profile(page, row: pd.Series) -> dict:
    name        = str(row.get("name", "")).strip()
    affiliation = str(row.get("affiliation", "")).strip()
    scholar_url = str(row.get("profile_scholar", "")).strip()
    current_id  = str(row.get("s2_author_id", "")).strip()

    # Default result: keep everything as-is
    result = {
        "fix_status":     "no_better_found",
        "s2_author_id":   current_id,
        "s2_name":        row.get("s2_name", ""),
        "h_index":        row.get("h_index"),
        "citation_count": row.get("citation_count"),
        "paper_count":    row.get("paper_count"),
        "s2_profile_url": (
            f"https://www.semanticscholar.org/author/{current_id}"
            if current_id and current_id.lower() not in ("nan", "none", "")
            else ""
        ),
    }

    # ── Step 1: scrape Google Scholar titles (for sanity-check) ──────────
    log.info("  Scraping Scholar profile for %s …", name)
    scholar_titles = fetch_scholar_papers_playwright(page, scholar_url)
    if not scholar_titles:
        log.info("  No Scholar titles found; will rely on h-index signal only.")

    # ── Step 2: search S2 ─────────────────────────────────────────────────
    log.info("  Searching S2 for '%s' …", name)
    candidates = search_s2_authors(page, name, affiliation)
    time.sleep(API_SLEEP)

    if not candidates:
        log.info("  No S2 candidates found.")
        return result

    # ── Step 3: rank candidates by h-index desc, then citations desc ──────
    #
    # The search endpoint already returns hIndex and citationCount in the
    # 'fields' we requested, so we don't need an extra API call per candidate.
    def _rank_key(c: dict):
        h   = c.get("hIndex")        or 0
        cit = c.get("citationCount") or 0
        return (h, cit)

    candidates_sorted = sorted(candidates, key=_rank_key, reverse=True)

    log.info("  Candidates ranked by h-index / citations:")
    for c in candidates_sorted:
        log.info(
            "    id=%-15s  name=%-35s  h=%s  cit=%s",
            c.get("authorId", "?"),
            c.get("name", "?"),
            c.get("hIndex", "?"),
            c.get("citationCount", "?"),
        )

    best_candidate = candidates_sorted[0]
    best_id        = str(best_candidate.get("authorId", "")).strip()

    if not best_id:
        log.info("  Top candidate has no authorId; aborting.")
        return result

    # ── Step 4: optional title-overlap sanity check ───────────────────────
    if scholar_titles:
        cand_papers = fetch_author_papers(page, best_id)
        time.sleep(API_SLEEP)
        overlap = title_overlap(scholar_titles, cand_papers)
        log.info(
            "  Top candidate '%s' title overlap = %d",
            best_candidate.get("name", "?"),
            overlap,
        )
        if overlap < MIN_OVERLAP_WARN:
            log.warning(
                "  ⚠  Low overlap (%d) for top-ranked candidate %s ('%s'). "
                "Consider manual review.",
                overlap, best_id, best_candidate.get("name", "?"),
            )
    else:
        log.info("  Skipping overlap check (no Scholar titles).")

    # ── Step 5: apply result ──────────────────────────────────────────────
    if best_id == current_id:
        log.info("  Current profile is already the best match.")
        result["fix_status"] = "already_ok"
    else:
        log.info(
            "  Better profile found: %s  h=%s  cit=%s",
            best_id,
            best_candidate.get("hIndex", "?"),
            best_candidate.get("citationCount", "?"),
        )
        # Fetch a full metrics record for the winner
        metrics = fetch_author_metrics(page, best_id)
        time.sleep(API_SLEEP)
        result.update({
            "fix_status":     "fixed",
            "s2_author_id":   best_id,
            "s2_name":        metrics.get("s2_name", ""),
            "h_index":        metrics.get("h_index"),
            "citation_count": metrics.get("citation_count"),
            "paper_count":    metrics.get("paper_count"),
            "s2_profile_url": f"https://www.semanticscholar.org/author/{best_id}",
        })

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Fix S2 profiles via Playwright (h-index priority)."
    )
    parser.add_argument("--input",   default=INPUT_FILE)
    parser.add_argument("--output",  default=OUTPUT_FILE)
    parser.add_argument(
        "--api-key", default="",
        help="Semantic Scholar API key (overrides S2_API_KEY env var)",
    )
    parser.add_argument(
        "--headed", action="store_true",
        help="Run browser in headed (visible) mode for debugging",
    )
    args = parser.parse_args()

    global S2_API_KEY
    if args.api_key:
        S2_API_KEY = args.api_key

    if not S2_API_KEY:
        log.warning(
            "No S2_API_KEY – requests will be heavily rate-limited. "
            "Set env var S2_API_KEY or pass --api-key."
        )

    # ── Load CSV ──────────────────────────────────────────────────────────
    log.info("Loading %s …", args.input)
    df = pd.read_csv(args.input, dtype={
        "s2_author_id": "object",
        "scopus_id":    "object",
        "orcid":        "object",
    })

    for col in (
        "h_index", "citation_count", "paper_count",
        "citations_scholar", "hindex_scholar", "i10_scholar",
    ):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    for col in ("fix_status", "s2_profile_url"):
        if col not in df.columns:
            df[col] = ""

    # Ensure object dtype on string-output columns to avoid dtype conflicts
    for col in ("fix_status", "s2_profile_url", "s2_author_id", "s2_name"):
        if col in df.columns:
            df[col] = df[col].astype(object)

    log.info("Loaded %d rows.", len(df))

    # ── Launch Playwright browser (one instance for the whole run) ────────
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headed)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="en-US",
            viewport={"width": 1280, "height": 800},
        )
        page = context.new_page()

        for idx, row in df.iterrows():
            name = str(row.get("name", f"row_{idx}")).strip()
            log.info("─── [%d/%d] %s", idx + 1, len(df), name)

            skip_reason = should_skip(row)
            if skip_reason:
                log.info("  Skipping: %s", skip_reason)
                df.at[idx, "fix_status"] = skip_reason
                s2_id = str(row.get("s2_author_id", "")).strip()
                if s2_id and s2_id.lower() not in ("nan", "none", ""):
                    df.at[idx, "s2_profile_url"] = (
                        f"https://www.semanticscholar.org/author/{s2_id}"
                    )
                df.to_csv(args.output, index=False)
                continue

            try:
                res = find_best_s2_profile(page, row)
            except Exception as exc:
                log.error("  Error for %s: %s", name, exc, exc_info=True)
                df.at[idx, "fix_status"] = "error"
                df.to_csv(args.output, index=False)
                continue

            for col, val in res.items():
                if col not in df.columns:
                    df[col] = ""
                    df[col] = df[col].astype(object)
                if df[col].dtype != object and isinstance(val, str):
                    df[col] = df[col].astype(object)
                df.at[idx, col] = val

            df.to_csv(args.output, index=False)
            log.info("  Checkpoint saved → %s", args.output)

        browser.close()

    df.to_csv(args.output, index=False)
    log.info("Done. Saved to %s", args.output)

    if "fix_status" in df.columns:
        log.info("\nSummary:\n%s", df["fix_status"].value_counts().to_string())


if __name__ == "__main__":
    main()