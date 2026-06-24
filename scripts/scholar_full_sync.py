"""
scholar_full_sync.py
====================
Scrapes ALL Google Scholar author profiles for every domain (paginating
through all pages, not just the first 10 results), checks each author against
an existing CSV of known profiles, skips duplicates, then for brand-new
authors also queries Semantic Scholar to fill in s2_author_id, h_index,
citation_count, paper_count, etc.

Expected existing CSV columns
------------------------------
name, affiliation, email, domain, citations_scholar, hindex_scholar,
i10_scholar, profile_scholar, orcid, scopus_status, scopus_id, scopus_url,
s2_author_id, s2_name, h_index, citation_count, paper_count,
metrics_fetched, fix_status, s2_profile_url

Usage
-----
1. Place  merged_with_s2_metrics_fixed2_corrected.csv  in the working dir.
2. Run:
       python scholar_full_sync.py

The script opens a visible Chromium window so you can log into Google Scholar
the first time it runs.  It saves progress after every author so you can
safely interrupt and restart.

Output
------
scholar_full_sync_output.csv   – merged result (old + new authors)
"""

import csv
import os
import random
import time
import logging
import sys
from urllib.parse import quote

import pandas as pd
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

_ROOT = os.path.join(os.path.dirname(__file__), "..")
EXISTING_CSV = os.path.join(_ROOT, "data", "legacy", "merged_with_s2_metrics_fixed2_corrected.csv")
OUTPUT_CSV   = os.path.join(_ROOT, "scholar_full_sync_output.csv")

S2_API_BASE  = "https://api.semanticscholar.org/graph/v1"
S2_API_KEY   = os.environ.get("S2_API_KEY", "s2k-FShcrmhCdVNMSOlsDvk1kvUMotFlTEIyIqqgE3Fl")

PROFILE_DIR  = os.path.join(_ROOT, "scholar_profile")

MAX_S2_CANDIDATES = 5

DOMAINS = [
    "rupp.edu.kh", "itc.edu.kh", "paragoniu.edu.kh", "rua.edu.kh",
    "cadt.edu.kh", "aupp.edu.kh",
    "num.edu.kh", "cadt.edu.kh", "aupp.edu.kh", "uc.edu.kh",
    "puc.edu.kh", "nortonu.com", "cam-ed.edu.kh", "kit.edu.kh", "aib.edu.kh",
    "camtech.edu.kh"

    # "nie.edu.kh", "npic.edu.kh", "nubb.edu.kh", "sru.edu.kh",
    # "nuck.edu.kh", "nmu.edu.kh", "nia.edu.kh", "rac.gov.kh",
    # "nib.edu.kh", "era.gov.kh", "psbu.edu.kh", "ntti.edu.kh",
    # "cardi.org.kh", "ppiedu.com", "camtech.edu.kh", "ciedi.edu.kh",
    # "bbu.edu.kh", "iic.edu.kh", "puthisastra.edu.kh", "iu.edu.kh",
    # "beltei.edu.kh", "aeu.edu.kh", "ppiu.edu.kh", "cus.edu.kh",
    # "mekong.edu.kh", "ume.edu.kh", "diu.edu.kh", "usea.edu.kh",
    # "westernuniversity.edu.kh", "hru.edu.kh", "vanda.edu.kh",
    # "angkor.edu.kh", "pcu.edu.kh", "lifeun.edu.kh",
    # "khemarakuniversity.edu.kh", "clu-edu.com", "cumt.edu.kh",
    # "akuks.com", "cityuniversity.education", "cup-university.com",
    # "eamu.edu.kh", "spi.edu.kh", "ppua.edu.kh", "aub.edu.kh",
    # "uef.edu.kh", "dmuc.edu.kh", "efi.mef.gov.kh",
]

# CSV output columns — must match exactly
FIELDNAMES = [
    "name", "affiliation", "email", "domain",
    "citations_scholar", "hindex_scholar", "i10_scholar", "profile_scholar",
    "orcid", "scopus_status", "scopus_id", "scopus_url",
    "s2_author_id", "s2_name", "h_index", "citation_count", "paper_count",
    "metrics_fetched", "fix_status", "s2_profile_url",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def human_sleep(a=4, b=12):
    time.sleep(random.uniform(a, b))


def normalise_name(name: str) -> str:
    """Lower-case, strip whitespace — used for duplicate detection."""
    return name.lower().strip()


def load_existing(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        log.warning("Existing CSV not found at '%s'; starting fresh.", path)
        return pd.DataFrame(columns=FIELDNAMES)
    df = pd.read_csv(path, dtype=str)
    # Ensure all expected columns exist
    for col in FIELDNAMES:
        if col not in df.columns:
            df[col] = ""
    return df[FIELDNAMES]


def known_profiles(df: pd.DataFrame) -> set:
    """
    Return a set of (normalised_name, domain) tuples already in the dataset.
    Also return a set of profile_scholar URLs for URL-based dedup.
    """
    name_domain = set()
    urls = set()
    for _, row in df.iterrows():
        n = normalise_name(str(row.get("name", "")))
        d = str(row.get("domain", "")).strip().lower()
        u = str(row.get("profile_scholar", "")).strip()
        if n:
            name_domain.add((n, d))
        if u:
            urls.add(u)
    return name_domain, urls


def append_row(path: str, row: dict):
    """Append a single dict row to the CSV (creates file + header if absent)."""
    file_exists = os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


# ─────────────────────────────────────────────────────────────────────────────
# Google Scholar scraping
# ─────────────────────────────────────────────────────────────────────────────

def parse_author_profile(page, url: str) -> dict:
    """Load a single Scholar author profile and extract metrics."""
    page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    human_sleep(3, 7)

    soup = BeautifulSoup(page.content(), "html.parser")

    def _text(sel):
        el = soup.select_one(sel)
        return el.get_text(strip=True) if el else ""

    name        = _text("#gsc_prf_in")
    affiliation = _text(".gsc_prf_il")

    # email is usually the second .gsc_prf_il element
    ils = soup.select(".gsc_prf_il")
    email = ils[1].get_text(strip=True) if len(ils) > 1 else ""

    citations = hindex = i10 = ""
    try:
        rows = soup.select("#gsc_rsb_st tbody tr")
        citations = rows[0].select("td")[1].get_text(strip=True)
        hindex    = rows[1].select("td")[1].get_text(strip=True)
        i10       = rows[2].select("td")[1].get_text(strip=True)
    except Exception:
        pass

    return {
        "name":              name,
        "affiliation":       affiliation,
        "email":             email,
        "citations_scholar": citations,
        "hindex_scholar":    hindex,
        "i10_scholar":       i10,
        "profile_scholar":   url,
    }


def iter_domain_authors(page, domain: str):
    """
    Generator: yield each author profile dict found for *domain*,
    paginating through ALL pages of results.
    """
    base_url = (
        "https://scholar.google.com/"
        f"citations?view_op=search_authors&mauthors={quote(domain)}"
    )
    page.goto(base_url, wait_until="domcontentloaded", timeout=30_000)
    human_sleep(5, 10)

    page_num = 1
    while True:
        log.info("  [%s] Parsing page %d of results …", domain, page_num)
        soup = BeautifulSoup(page.content(), "html.parser")
        cards = soup.select(".gsc_1usr")

        if not cards:
            log.info("  [%s] No author cards found on page %d.", domain, page_num)
            break

        for card in cards:
            link = card.select_one("h3 a")
            if not link:
                continue
            profile_url = "https://scholar.google.com" + link["href"]
            try:
                data = parse_author_profile(page, profile_url)
                yield data
                # Go back to the search results page
                page.go_back(wait_until="domcontentloaded", timeout=20_000)
                human_sleep(4, 9)
            except Exception as exc:
                log.warning("  Error parsing profile %s: %s", profile_url, exc)
                try:
                    page.go_back(wait_until="domcontentloaded", timeout=15_000)
                except Exception:
                    page.goto(base_url, wait_until="domcontentloaded", timeout=30_000)
                human_sleep(3, 6)

        # ── Paginate ──────────────────────────────────────────────────────
        # Scholar's author-search pagination buttons are inside
        # #gsc_authors_bottom_pag — the last button is "next".
        try:
            btns = page.locator("#gsc_authors_bottom_pag button").all()
            if not btns:
                break
            next_btn = btns[-1]          # last button = "next page"
            disabled = next_btn.get_attribute("disabled")
            if disabled is not None:
                break
            next_btn.click()
            human_sleep(8, 18)
            page_num += 1
        except Exception:
            break


# ─────────────────────────────────────────────────────────────────────────────
# Semantic Scholar
# ─────────────────────────────────────────────────────────────────────────────

def s2_get(page, path: str, params: dict = None):
    from urllib.parse import urlencode
    url = f"{S2_API_BASE}{path}"
    if params:
        url += "?" + urlencode(params)
    headers = {"Accept": "application/json"}
    if S2_API_KEY:
        headers["x-api-key"] = S2_API_KEY
    for attempt in range(3):
        try:
            resp = page.request.get(url, headers=headers, timeout=20_000)
            if resp.status == 200:
                return resp.json()
            if resp.status == 429:
                wait = 15 * (attempt + 1)
                log.warning("S2 rate-limited; sleeping %ds …", wait)
                time.sleep(wait)
                continue
            log.warning("S2 HTTP %s for %s", resp.status, url)
            return None
        except Exception as exc:
            log.warning("S2 request error (attempt %d): %s", attempt + 1, exc)
            time.sleep(5)
    return None


def fetch_s2_metrics(page, name: str, affiliation: str = "") -> dict:
    """
    Search Semantic Scholar for *name*, pick the best candidate
    (highest h-index), return a partial row dict.
    """
    data = s2_get(page, "/author/search", {
        "query":  name.strip(),
        "fields": "name,affiliations,hIndex,citationCount,paperCount,authorId",
        "limit":  MAX_S2_CANDIDATES,
    })
    time.sleep(1.5)

    if not data or not data.get("data"):
        return {}

    candidates = data["data"]

    # Rank by h-index desc, then citations desc
    def _key(c):
        return (c.get("hIndex") or 0, c.get("citationCount") or 0)

    best = sorted(candidates, key=_key, reverse=True)[0]
    author_id = str(best.get("authorId", "")).strip()
    if not author_id:
        return {}

    return {
        "s2_author_id":   author_id,
        "s2_name":        best.get("name", ""),
        "h_index":        best.get("hIndex", ""),
        "citation_count": best.get("citationCount", ""),
        "paper_count":    best.get("paperCount", ""),
        "metrics_fetched": "yes",
        "fix_status":     "new_author",
        "s2_profile_url": f"https://www.semanticscholar.org/author/{author_id}",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    log.info("Loading existing data from '%s' …", EXISTING_CSV)
    existing_df = load_existing(EXISTING_CSV)
    log.info("  %d existing rows loaded.", len(existing_df))

    known_nd, known_urls = known_profiles(existing_df)

    # If output file doesn't exist yet, seed it with the existing data
    if not os.path.exists(OUTPUT_CSV):
        log.info("Writing existing rows to '%s' …", OUTPUT_CSV)
        existing_df.to_csv(OUTPUT_CSV, index=False)

    new_count = 0

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=PROFILE_DIR,
            headless=False,
            viewport={"width": 1400, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.new_page()

        # Let the user log in if needed
        page.goto("https://scholar.google.com")
        log.info("=" * 60)
        log.info("Please log in to Google Scholar if prompted.")
        input("Press ENTER once Scholar is accessible …")
        log.info("=" * 60)

        for domain in DOMAINS:
            log.info("\n══ Processing domain: %s ══", domain)

            try:
                for author in iter_domain_authors(page, domain):
                    profile_url = author.get("profile_scholar", "").strip()
                    norm_name   = normalise_name(author.get("name", ""))
                    dom_lower   = domain.lower()

                    # ── Duplicate check ───────────────────────────────────
                    if profile_url in known_urls:
                        log.info(
                            "  SKIP (url known): %s", author.get("name", "?")
                        )
                        continue
                    if (norm_name, dom_lower) in known_nd:
                        log.info(
                            "  SKIP (name+domain known): %s",
                            author.get("name", "?")
                        )
                        continue

                    log.info(
                        "  NEW author: %-40s  h=%s  cit=%s",
                        author.get("name", "?"),
                        author.get("hindex_scholar", "?"),
                        author.get("citations_scholar", "?"),
                    )

                    # ── Build full row ─────────────────────────────────────
                    row = {col: "" for col in FIELDNAMES}
                    row.update(author)
                    row["domain"] = domain

                    # ── Semantic Scholar lookup ────────────────────────────
                    s2 = fetch_s2_metrics(
                        page,
                        author.get("name", ""),
                        author.get("affiliation", ""),
                    )
                    row.update(s2)

                    # Append to output file immediately (crash-safe)
                    append_row(OUTPUT_CSV, row)

                    # Update in-memory dedup sets
                    known_nd.add((norm_name, dom_lower))
                    if profile_url:
                        known_urls.add(profile_url)

                    new_count += 1
                    log.info(
                        "  Saved row #%d  s2_id=%s  h_s2=%s",
                        new_count,
                        row.get("s2_author_id", ""),
                        row.get("h_index", ""),
                    )

                    human_sleep(5, 12)

            except Exception as exc:
                log.error("Failed processing domain %s: %s", domain, exc, exc_info=True)

            # Polite pause between domains
            human_sleep(20, 50)

        context.close()

    log.info("\nDone.  %d new authors added.  Output: %s", new_count, OUTPUT_CSV)


if __name__ == "__main__":
    main()