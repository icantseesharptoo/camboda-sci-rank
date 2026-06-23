"""
Validate Semantic Scholar and ORCID profiles against Google Scholar paper titles.

For each author in merged_with_s2_metrics_fixed2_corrected.csv:
  1. Scrape up to 20 paper titles from their Google Scholar profile (baseline).
  2. If s2_author_id is missing, search Semantic Scholar API by name (+ ORCID match).
  3. Compare S2 paper titles to the Scholar baseline (fuzzy overlap).
  4. Compare ORCID work titles to the Scholar baseline.

Setup:
    pip install pandas requests rapidfuzz playwright beautifulsoup4
    playwright install chromium

Optional (higher S2 rate limits):
    set S2_API_KEY=your_key

Usage:
    py validation_s2_orcid.py
    py validation_s2_orcid.py --limit 10
    py validation_s2_orcid.py --headless --skip-interactive
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from typing import Any

import pandas as pd
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from rapidfuzz import fuzz

INPUT_FILE = "merged_with_s2_metrics_fixed2_corrected.csv"
OUTPUT_FILE = "merged_with_validation.csv"

S2_API_KEY = os.getenv("S2_API_KEY", "s2k-FShcrmhCdVNMSOlsDvk1kvUMotFlTEIyIqqgE3Fl")
BASE_URL = "https://api.semanticscholar.org/graph/v1"
RATE_LIMIT_SLEEP = 1.1 if S2_API_KEY else 3.5
MAX_RETRIES = 4
RETRY_BACKOFF = [15, 30, 60, 90]

MAX_SCHOLAR_PAPERS = 20
MAX_S2_PAPERS = 100
MATCH_THRESHOLD = 85
SEARCH_CANDIDATE_LIMIT = 8

VALIDATION_COLS = [
    "scholar_papers",
    "s2_papers",
    "orcid_papers",
    "s2_overlap",
    "orcid_overlap",
    "s2_validation",
    "orcid_validation",
    "s2_author_id_resolved",
    "s2_name_resolved",
    "s2_search_status",
    "orcid_normalized",
]


def is_missing(value: Any) -> bool:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return True
    text = str(value).strip().lower()
    return text in ("", "nan", "none", "nat", "null")


def clean_s2_id(value: Any) -> str | None:
    if is_missing(value):
        return None
    text = str(value).strip()
    if "." in text:
        try:
            text = str(int(float(text)))
        except ValueError:
            pass
    return text if text.isdigit() else None


def normalize_orcid(value: Any) -> str | None:
    if is_missing(value):
        return None
    match = re.search(r"(\d{4}-\d{4}-\d{4}-[\dX]{4})", str(value), re.IGNORECASE)
    return match.group(1).upper() if match else None


def classify(matches: int) -> str:
    if matches >= 3:
        return "VALIDATED"
    if matches == 2:
        return "LIKELY_MATCH"
    if matches == 1:
        return "WEAK_MATCH"
    return "NO_MATCH"


def fuzzy_overlap(source_titles: list[str], target_titles: list[str]) -> int:
    if not source_titles or not target_titles:
        return 0
    matches = 0
    for source in source_titles:
        source_l = source.lower()
        for target in target_titles:
            if fuzz.token_sort_ratio(source_l, target.lower()) >= MATCH_THRESHOLD:
                matches += 1
                break
    return matches


def name_score(a: str, b: str) -> int:
    return fuzz.token_sort_ratio(a.lower(), b.lower())


def build_s2_headers() -> dict[str, str]:
    headers = {"User-Agent": "camboda-sci-rank-validation/1.0"}
    if S2_API_KEY:
        headers["x-api-key"] = S2_API_KEY
    return headers


def s2_request(url: str, params: dict | None = None) -> dict | list | None:
    for attempt in range(MAX_RETRIES):
        try:
            response = requests.get(
                url,
                params=params,
                headers=build_s2_headers(),
                timeout=30,
            )
            if response.status_code == 200:
                return response.json()
            if response.status_code == 404:
                return None
            if response.status_code == 429:
                wait = RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)]
                print(f"    S2 rate-limited, waiting {wait}s...")
                time.sleep(wait)
                continue
            print(f"    S2 HTTP {response.status_code}: {response.text[:120]}")
            return None
        except requests.RequestException as exc:
            wait = RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)]
            print(f"    S2 request error ({exc}), retry in {wait}s...")
            time.sleep(wait)
    return None


def extract_paper_titles(papers: list[dict] | None, limit: int = MAX_S2_PAPERS) -> list[str]:
    titles: list[str] = []
    for paper in papers or []:
        title = paper.get("title")
        if title:
            titles.append(str(title).strip())
        if len(titles) >= limit:
            break
    return titles


def s2_author_by_id(author_id: str) -> dict | None:
    url = f"{BASE_URL}/author/{author_id}"
    params = {"fields": "name,externalIds,papers.title"}
    data = s2_request(url, params)
    return data if isinstance(data, dict) else None


def s2_search_authors(query: str, limit: int = SEARCH_CANDIDATE_LIMIT) -> list[dict]:
    url = f"{BASE_URL}/author/search"
    params = {
        "query": query,
        "fields": "name,authorId,externalIds,papers.title",
        "limit": limit,
    }
    data = s2_request(url, params)
    if not isinstance(data, dict):
        return []
    return data.get("data") or []


def candidate_orcid(candidate: dict) -> str | None:
    external = candidate.get("externalIds") or {}
    orcid = external.get("ORCID")
    return str(orcid).upper() if orcid else None


def pick_s2_candidate(
    scholar_titles: list[str],
    candidates: list[dict],
    author_name: str,
    orcid: str | None,
) -> tuple[dict | None, str]:
    if not candidates:
        return None, "NOT_FOUND"

    scored: list[tuple[int, int, dict]] = []
    for candidate in candidates:
        cand_name = candidate.get("name") or ""
        papers = extract_paper_titles(candidate.get("papers"))
        overlap = fuzzy_overlap(scholar_titles, papers)
        n_score = name_score(author_name, cand_name)
        orcid_bonus = 25 if orcid and candidate_orcid(candidate) == orcid else 0
        scored.append((overlap, n_score + orcid_bonus, candidate))

    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    best_overlap, best_score, best = scored[0]

    if orcid:
        for candidate in candidates:
            if candidate_orcid(candidate) == orcid:
                papers = extract_paper_titles(candidate.get("papers"))
                overlap = fuzzy_overlap(scholar_titles, papers)
                if overlap >= 1 or name_score(author_name, candidate.get("name", "")) >= 90:
                    return candidate, "ORCID_MATCH"

    if best_overlap >= 2:
        return best, "NAME_SEARCH_VALIDATED"
    if best_overlap == 1 or best_score >= 85:
        return best, "NAME_SEARCH_WEAK"
    if best_score >= 92 and not scholar_titles:
        return best, "NAME_ONLY"
    return best, "NAME_SEARCH_UNCERTAIN"


def resolve_s2_profile(
    row: pd.Series,
    scholar_titles: list[str],
) -> tuple[str | None, str | None, list[str], str]:
    existing_id = clean_s2_id(row.get("s2_author_id"))
    author_name = str(row.get("name", "")).strip()
    orcid = normalize_orcid(row.get("orcid"))

    if existing_id:
        author = s2_author_by_id(existing_id)
        time.sleep(RATE_LIMIT_SLEEP)
        if author:
            titles = extract_paper_titles(author.get("papers"))
            return existing_id, author.get("name"), titles, "EXISTING_ID"
        return existing_id, None, [], "EXISTING_ID_NOT_FOUND"

    if not author_name:
        return None, None, [], "NO_NAME"

    candidates = s2_search_authors(author_name)
    time.sleep(RATE_LIMIT_SLEEP)
    candidate, status = pick_s2_candidate(scholar_titles, candidates, author_name, orcid)
    if not candidate:
        return None, None, [], status

    author_id = str(candidate.get("authorId", "")).strip() or None
    titles = extract_paper_titles(candidate.get("papers"))
    if author_id and len(titles) < 5:
        full = s2_author_by_id(author_id)
        time.sleep(RATE_LIMIT_SLEEP)
        if full:
            titles = extract_paper_titles(full.get("papers"))
    return author_id, candidate.get("name"), titles, status


def extract_orcid_title(work_summary: dict) -> str | None:
    title_block = work_summary.get("title")
    if isinstance(title_block, dict):
        nested = title_block.get("title")
        if isinstance(nested, dict):
            return nested.get("value") or nested.get("title")
        return title_block.get("value") or title_block.get("title")
    return None


def orcid_titles(orcid: str | None) -> list[str]:
    if not orcid:
        return []
    url = f"https://pub.orcid.org/v3.0/{orcid}/works"
    headers = {"Accept": "application/json"}
    try:
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code != 200:
            return []
        data = response.json()
        titles: list[str] = []
        for group in data.get("group", []):
            for summary in group.get("work-summary", []):
                title = extract_orcid_title(summary)
                if title:
                    titles.append(title.strip())
        return titles
    except requests.RequestException as exc:
        print(f"    ORCID error: {exc}")
        return []


def scholar_titles(page, scholar_url: str) -> list[str]:
    try:
        page.goto(scholar_url, wait_until="domcontentloaded", timeout=60000)
        time.sleep(2)
        soup = BeautifulSoup(page.content(), "html.parser")
        titles: list[str] = []
        for element in soup.select(".gsc_a_at"):
            title = element.get_text(strip=True)
            if title:
                titles.append(title)
            if len(titles) >= MAX_SCHOLAR_PAPERS:
                break
        return titles
    except Exception as exc:
        print(f"    Scholar error: {exc}")
        return []


def needs_s2_resolution(row: pd.Series) -> bool:
    if is_missing(row.get("s2_author_id")):
        return True
    return is_missing(row.get("h_index")) and is_missing(row.get("citation_count"))


def prepare_dataframe(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    for col in VALIDATION_COLS:
        if col not in df.columns:
            df[col] = ""
    return df


def save_checkpoint(df: pd.DataFrame, path: str) -> None:
    df.to_csv(path, index=False, encoding="utf-8")


def process_rows(
    df: pd.DataFrame,
    page,
    start: int = 0,
    limit: int | None = None,
    checkpoint_every: int = 10,
) -> None:
    indices = list(df.index)
    if limit is not None:
        indices = indices[start : start + limit]
    else:
        indices = indices[start:]

    total = len(indices)
    for n, idx in enumerate(indices, start=1):
        row = df.loc[idx]
        name = row.get("name", "")
        scholar_url = str(row.get("profile_scholar", "")).strip()

        print(f"\n[{n}/{total}] idx={idx} {name}")

        if not scholar_url or not scholar_url.startswith("http"):
            df.at[idx, "s2_validation"] = "SKIPPED_NO_SCHOLAR"
            df.at[idx, "orcid_validation"] = "SKIPPED_NO_SCHOLAR"
            df.at[idx, "s2_search_status"] = "SKIPPED_NO_SCHOLAR"
            continue

        baseline = scholar_titles(page, scholar_url)
        df.at[idx, "scholar_papers"] = str(len(baseline))

        if not baseline:
            df.at[idx, "s2_validation"] = "NO_SCHOLAR_BASELINE"
            df.at[idx, "orcid_validation"] = "NO_SCHOLAR_BASELINE"
            df.at[idx, "s2_search_status"] = "NO_SCHOLAR_BASELINE"
            print("    No Scholar papers scraped (CAPTCHA, empty profile, or block)")
            continue

        orcid = normalize_orcid(row.get("orcid"))
        df.at[idx, "orcid_normalized"] = orcid or ""

        if needs_s2_resolution(row):
            print("    Searching Semantic Scholar...")
        else:
            print("    Using existing S2 author id...")

        s2_id, s2_name, s2_titles, s2_status = resolve_s2_profile(row, baseline)
        df.at[idx, "s2_author_id_resolved"] = s2_id or ""
        df.at[idx, "s2_name_resolved"] = s2_name or ""
        df.at[idx, "s2_search_status"] = s2_status
        df.at[idx, "s2_papers"] = str(len(s2_titles))

        s2_overlap = fuzzy_overlap(baseline, s2_titles)
        df.at[idx, "s2_overlap"] = str(s2_overlap)
        df.at[idx, "s2_validation"] = classify(s2_overlap) if s2_id else "NO_S2_PROFILE"

        if orcid:
            orcid_works = orcid_titles(orcid)
            time.sleep(0.5)
        else:
            orcid_works = []
        df.at[idx, "orcid_papers"] = str(len(orcid_works))

        orcid_overlap = fuzzy_overlap(baseline, orcid_works)
        df.at[idx, "orcid_overlap"] = str(orcid_overlap)
        df.at[idx, "orcid_validation"] = classify(orcid_overlap) if orcid else "NO_ORCID"

        print(
            f"    Scholar={len(baseline)} S2={len(s2_titles)} ({s2_status}) "
            f"overlap={s2_overlap} -> {df.at[idx, 's2_validation']}"
        )
        print(
            f"    ORCID works={len(orcid_works)} overlap={orcid_overlap} "
            f"-> {df.at[idx, 'orcid_validation']}"
        )

        if n % checkpoint_every == 0:
            save_checkpoint(df, OUTPUT_FILE)
            print(f"    Checkpoint saved -> {OUTPUT_FILE}")


def print_summary(df: pd.DataFrame) -> None:
    print("\n" + "=" * 60)
    print("Validation summary")
    print("=" * 60)
    for col in ("s2_validation", "orcid_validation", "s2_search_status"):
        if col in df.columns:
            print(f"\n{col}:")
            print(df[col].value_counts(dropna=False).to_string())
    resolved = df["s2_author_id_resolved"].apply(lambda v: not is_missing(v)).sum()
    print(f"\nS2 profiles resolved: {resolved}/{len(df)}")
    print(f"Output: {OUTPUT_FILE}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate S2/ORCID against Google Scholar papers")
    parser.add_argument("--input", default=INPUT_FILE)
    parser.add_argument("--output", default=OUTPUT_FILE)
    parser.add_argument("--limit", type=int, default=None, help="Process only N rows")
    parser.add_argument("--start", type=int, default=0, help="Start row offset")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument(
        "--skip-interactive",
        action="store_true",
        help="Do not pause for Scholar CAPTCHA/login",
    )
    parser.add_argument(
        "--profile-dir",
        default="./scholar_profile",
        help="Playwright persistent profile directory for Google Scholar",
    )
    parser.add_argument("--checkpoint-every", type=int, default=10)
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"Input not found: {args.input}", file=sys.stderr)
        return 1

    df = prepare_dataframe(args.input)
    if os.path.exists(args.output):
        existing = prepare_dataframe(args.output)
        for col in VALIDATION_COLS:
            if col in existing.columns:
                df[col] = existing[col].reindex(df.index, fill_value="")

    print(f"Loaded {len(df)} rows from {args.input}")
    if not S2_API_KEY:
        print("Tip: set S2_API_KEY for higher Semantic Scholar rate limits.")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch_persistent_context(
            user_data_dir=args.profile_dir,
            headless=args.headless,
            viewport={"width": 1280, "height": 900},
        )
        page = browser.new_page()
        page.goto("https://scholar.google.com", wait_until="domcontentloaded", timeout=60000)

        if not args.skip_interactive:
            input(
                "If Google Scholar shows CAPTCHA or login, solve it in the browser, "
                "then press ENTER to continue..."
            )

        try:
            process_rows(
                df,
                page,
                start=args.start,
                limit=args.limit,
                checkpoint_every=args.checkpoint_every,
            )
        finally:
            browser.close()

    save_checkpoint(df, args.output)
    print_summary(df)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
