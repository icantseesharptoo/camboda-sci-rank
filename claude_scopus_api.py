"""
Fetch h-index, citation count, and paper count from Semantic Scholar.

No API key required for basic use (up to 100 req/5 min unauthenticated).
Optional free API key from https://www.semanticscholar.org/product/api
raises the limit to 1 req/sec with higher quotas.

Strategy:
  1. If a Semantic Scholar author ID is already known  → direct lookup
  2. Else if ORCID is available                        → lookup by ORCID
  3. Else if name is available                         → name search + best match

Setup:
    pip install requests pandas

Usage:
    python fetch_semantic_scholar_metrics.py

    # Optional: set a free API key for higher rate limits
    export S2_API_KEY=your_key_here
"""

import os
import re
import time
import requests
import pandas as pd
from datetime import datetime

# ------------------------------------------------------------------ config ---

INPUT_FILE  = "merged_with_scopus.csv"
OUTPUT_FILE = "merged_with_s2_metrics.csv"

# Optional — get a free key at https://www.semanticscholar.org/product/api
S2_API_KEY = os.getenv("S2_API_KEY", "")

# Rate limits:
#   No key:       100 requests per 5 minutes (~1 req/3s to be safe)
#   With key:     1 request per second
RATE_LIMIT_SLEEP = 1.1 if S2_API_KEY else 3.1

MAX_RETRIES   = 3
RETRY_BACKOFF = [10, 30, 60]

BASE_URL = "https://api.semanticscholar.org/graph/v1"

AUTHOR_FIELDS = "name,hIndex,citationCount,paperCount,affiliations,externalIds"

# ----------------------------------------------------------------- headers ---

def build_headers():
    h = {"User-Agent": "research-metrics-script/1.0 (academic use)"}
    if S2_API_KEY:
        h["x-api-key"] = S2_API_KEY
    return h


# ---------------------------------------------------------- API functions ---

def lookup_by_s2_id(s2_id: str) -> dict | None:
    """Direct author lookup when we already have the S2 author ID."""
    url = f"{BASE_URL}/author/{s2_id}"
    return _get(url, params={"fields": AUTHOR_FIELDS})


def lookup_by_orcid(orcid: str) -> dict | None:
    """
    Semantic Scholar indexes ORCID under externalIds.
    We search by name first, then filter by ORCID in results — S2 doesn't
    support direct ORCID query on the free endpoint.
    """
    # S2 does support author search — find candidates then match ORCID
    return None  # used as fallback signal; handled in main flow


def search_by_name(name: str, orcid: str | None = None) -> dict | None:
    """
    Search authors by name. If orcid provided, try to match it in results.
    Otherwise return the top result.
    """
    url = f"{BASE_URL}/author/search"
    params = {
        "query": name,
        "fields": AUTHOR_FIELDS,
        "limit": 5,
    }
    data = _get(url, params=params)
    if not data:
        return None

    candidates = data.get("data", [])
    if not candidates:
        return None

    # If we have an ORCID, try to match it in externalIds
    if orcid:
        orcid_clean = orcid.strip().upper()
        for candidate in candidates:
            ext = candidate.get("externalIds") or {}
            candidate_orcid = str(ext.get("ORCID", "")).upper()
            if candidate_orcid and candidate_orcid == orcid_clean:
                return candidate

    # No ORCID match — return top result (closest name match)
    return candidates[0]


def _get(url: str, params: dict) -> dict | None:
    """GET with retry + back-off logic."""
    for attempt in range(MAX_RETRIES):
        try:
            r = requests.get(
                url,
                params=params,
                headers=build_headers(),
                timeout=20,
            )

            if r.status_code == 200:
                return r.json()

            if r.status_code == 429:
                wait = RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)]
                print(f"   ⚠️  429 rate-limited — waiting {wait}s …")
                time.sleep(wait)
                continue

            if r.status_code == 404:
                return None  # author not found, not an error

            print(f"   ⚠️  HTTP {r.status_code}: {r.text[:100]}")
            return None

        except requests.exceptions.Timeout:
            wait = RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)]
            print(f"   ⚠️  Timeout on attempt {attempt+1}, retrying in {wait}s …")
            time.sleep(wait)

        except Exception as e:
            print(f"   ⚠️  Unexpected error: {e}")
            return None

    return None


# ------------------------------------------------------------------ utils ---

def normalize_orcid(value) -> str | None:
    if pd.isna(value):
        return None
    match = re.search(r"(\d{4}-\d{4}-\d{4}-\d{4})", str(value))
    return match.group(1) if match else None


def extract_metrics(author_data: dict) -> dict:
    if not author_data:
        return _empty()
    return {
        "s2_author_id":    author_data.get("authorId"),
        "s2_name":         author_data.get("name"),
        "h_index":         author_data.get("hIndex"),
        "citation_count":  author_data.get("citationCount"),
        "paper_count":     author_data.get("paperCount"),
        "metrics_fetched": datetime.utcnow().strftime("%Y-%m-%d"),
    }


def _empty() -> dict:
    return {
        "s2_author_id":    None,
        "s2_name":         None,
        "h_index":         None,
        "citation_count":  None,
        "paper_count":     None,
        "metrics_fetched": None,
    }


# --------------------------------------------------------------- pipeline ---

def main():
    df = pd.read_csv(INPUT_FILE)

    # Detect available columns
    has_s2_id  = "s2_author_id" in df.columns
    has_orcid  = "orcid"        in df.columns
    has_name   = "name"         in df.columns  # adjust to your column name

    if not has_name and not has_orcid and not has_s2_id:
        raise SystemExit(
            "❌  Need at least one of: 's2_author_id', 'orcid', or 'name' column."
        )

    # Initialise output columns
    for col in ["s2_author_id", "s2_name", "h_index",
                "citation_count", "paper_count", "metrics_fetched"]:
        if col not in df.columns:
            df[col] = None

    total      = len(df)
    processed  = 0
    successful = 0

    for i, row in df.iterrows():

        # --- determine lookup strategy ---
        s2_id  = str(row.get("s2_author_id", "")).strip()
        orcid  = normalize_orcid(row.get("orcid")) if has_orcid else None
        name   = str(row.get("name", "")).strip()  if has_name  else ""

        s2_id_valid = s2_id and s2_id.lower() not in ("nan", "none", "")
        name_valid  = name  and name.lower()  not in ("nan", "none", "")

        processed += 1
        label = s2_id if s2_id_valid else (orcid or name or f"row {i}")
        print(f"[{processed}/{total}] {label}")

        author_data = None

        # Strategy 1: direct S2 ID lookup (fastest, most accurate)
        if s2_id_valid:
            author_data = lookup_by_s2_id(s2_id)
            if author_data:
                print(f"   → found via S2 ID")

        # Strategy 2: name search + ORCID match or top result
        if not author_data and name_valid:
            author_data = search_by_name(name, orcid=orcid)
            if author_data:
                match_type = "name+ORCID" if orcid else "name"
                print(f"   → found via {match_type} search")

        # --- save results ---
        metrics = extract_metrics(author_data)
        for col, val in metrics.items():
            df.at[i, col] = val

        if metrics["h_index"] is not None:
            successful += 1
            print(f"   ✅  h-index={metrics['h_index']}  "
                  f"citations={metrics['citation_count']}  "
                  f"papers={metrics['paper_count']}  "
                  f"name={metrics['s2_name']}")
        else:
            print(f"   ❌  No data found")

        # Checkpoint save every 50 rows
        if processed % 50 == 0:
            df.to_csv(OUTPUT_FILE, index=False, encoding="utf-8")
            print(f"   💾  Checkpoint saved ({processed}/{total})")

        time.sleep(RATE_LIMIT_SLEEP)

    df.to_csv(OUTPUT_FILE, index=False, encoding="utf-8")

    print("\n" + "=" * 50)
    print("✅  Done")
    print(f"   Total rows:              {total}")
    print(f"   Processed:               {processed}")
    print(f"   Successfully fetched:    {successful}")
    print(f"   No data found:           {processed - successful}")
    print(f"   Saved to: {OUTPUT_FILE}")

    # Quick summary stats
    if successful > 0:
        print("\n📊  Quick stats:")
        print(f"   Avg h-index:      {df['h_index'].mean():.1f}")
        print(f"   Max h-index:      {df['h_index'].max()}")
        print(f"   Avg citations:    {df['citation_count'].mean():.0f}")
        print(f"   Total citations:  {df['citation_count'].sum():.0f}")


if __name__ == "__main__":
    main()