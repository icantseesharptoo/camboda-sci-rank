"""
Recheck S2 matches where hindex_scholar - h_index is outside [0, 2].

Strategy:
  1. For each violation, search S2 API for the clean_name.
  2. Fetch papers for each candidate + the current match.
  3. Pick the candidate whose h-index is within [gs_h - 2, gs_h] and
     shares the most paper titles (fuzzy) with the Google Scholar profile.
  4. If the current match is actually the best, keep it.
  5. If no candidate satisfies the h-index rule, compare papers of
     all candidates to identify the correct person.
  6. Update the CSV with corrected S2 data or clear if wrong match.

Uses the Semantic Scholar API only (no Google Scholar scraping).
Fetches GS paper titles from S2 itself (the current match's papers
serve as a sanity baseline — if the current match is wrong, we rely
on name matching and h-index proximity).
"""

import csv
import json
import os
import re
import sys
import time
import urllib.request
import urllib.parse
import urllib.error
from typing import Optional
from functools import partial

print = partial(print, flush=True)

S2_API_BASE = "https://api.semanticscholar.org/graph/v1"
S2_API_KEY = os.environ.get("S2_API_KEY", "s2k-FShcrmhCdVNMSOlsDvk1kvUMotFlTEIyIqqgE3Fl")

INPUT_FILE = r"scholar_full_sync_output.csv"
OUTPUT_FILE = r"scholar_full_sync_output.csv"

MAX_CANDIDATES = 15
API_SLEEP = 1.2


def s2_get(path: str, params: dict = None) -> Optional[dict]:
    url = f"{S2_API_BASE}{path}"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"

    headers = {"Accept": "application/json", "User-Agent": "camboda-sci-rank/1.0"}
    if S2_API_KEY:
        headers["x-api-key"] = S2_API_KEY

    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = 15 * (attempt + 1)
                print(f"    Rate limited, waiting {wait}s...")
                time.sleep(wait)
                continue
            if e.code == 404:
                return None
            print(f"    HTTP {e.code} for {path}")
            return None
        except Exception as e:
            print(f"    Request error (attempt {attempt+1}): {e}")
            time.sleep(5)
    return None


def fetch_author_papers(author_id: str, limit: int = 100) -> list[str]:
    data = s2_get(f"/author/{author_id}/papers",
                  {"fields": "title", "limit": limit})
    if not data:
        return []
    return [p["title"].strip() for p in data.get("data", []) if p.get("title")]


def fetch_author_metrics(author_id: str) -> Optional[dict]:
    data = s2_get(f"/author/{author_id}",
                  {"fields": "hIndex,citationCount,paperCount,name"})
    if not data:
        return None
    return {
        "s2_name": data.get("name", ""),
        "h_index": data.get("hIndex"),
        "citation_count": data.get("citationCount"),
        "paper_count": data.get("paperCount"),
    }


def search_authors(name: str) -> list[dict]:
    data = s2_get("/author/search", {
        "query": name.strip(),
        "fields": "name,affiliations,hIndex,citationCount,paperCount,authorId",
        "limit": MAX_CANDIDATES,
    })
    if not data:
        return []
    return data.get("data", [])


def normalize(s: str) -> str:
    return re.sub(r'[^a-z0-9 ]', '', s.lower()).strip()


def fuzzy_title_overlap(titles_a: list[str], titles_b: list[str]) -> int:
    if not titles_a or not titles_b:
        return 0
    from rapidfuzz import fuzz
    matches = 0
    for ta in titles_a:
        na = normalize(ta)
        for tb in titles_b:
            if fuzz.token_sort_ratio(na, normalize(tb)) >= 82:
                matches += 1
                break
    return matches


def name_similarity(a: str, b: str) -> float:
    from rapidfuzz import fuzz
    return fuzz.token_sort_ratio(normalize(a), normalize(b))


def find_violations(rows, header_map):
    gi = header_map
    violations = []
    for i, row in enumerate(rows):
        s2_id = row[gi['s2_author_id']].strip()
        if not s2_id:
            continue
        try:
            gs_h = float(row[gi['hindex_scholar']]) if row[gi['hindex_scholar']].strip() else None
            s2_h = float(row[gi['h_index']]) if row[gi['h_index']].strip() else None
        except (ValueError, KeyError):
            continue
        if gs_h is None or s2_h is None:
            continue

        diff = gs_h - s2_h
        if diff < 0 or diff > 2:
            violations.append((i, gs_h, s2_h, diff))
    return violations


def process_violation(row, gi, gs_h, s2_h, diff):
    clean_name = row[gi['clean_name']].strip()
    original_name = row[gi['name']].strip()
    current_s2_id = row[gi['s2_author_id']].strip()
    current_s2_name = row[gi['s2_name']].strip()

    search_name = clean_name or original_name
    print(f"  Searching S2 for '{search_name}'...")

    # Fetch current match's papers for comparison
    current_papers = []
    if current_s2_id:
        current_papers = fetch_author_papers(current_s2_id)
        time.sleep(API_SLEEP)

    # Search for candidates
    candidates = search_authors(search_name)
    time.sleep(API_SLEEP)

    if not candidates:
        print(f"    No S2 candidates found.")
        return None

    # Evaluate each candidate
    scored = []
    for c in candidates:
        c_id = str(c.get("authorId", "")).strip()
        c_h = c.get("hIndex") or 0
        c_cit = c.get("citationCount") or 0
        c_name = c.get("name", "")
        c_papers = c.get("paperCount") or 0

        h_diff = gs_h - c_h
        in_range = 0 <= h_diff <= 2
        name_sim = name_similarity(search_name, c_name)

        scored.append({
            "id": c_id,
            "name": c_name,
            "h": c_h,
            "cit": c_cit,
            "papers": c_papers,
            "h_diff": h_diff,
            "in_range": in_range,
            "name_sim": name_sim,
        })

    # Print candidates
    for s in scored:
        flag = "OK" if s["in_range"] else "OUT"
        print(f"    [{flag}] id={s['id']:>12s}  h={s['h']:3.0f}  diff={s['h_diff']:+.0f}"
              f"  name_sim={s['name_sim']:.0f}  name=\"{s['name']}\"  papers={s['papers']}")

    # Filter: candidates in h-index range with decent name similarity
    good = [s for s in scored if s["in_range"] and s["name_sim"] >= 60]

    if not good:
        # No candidate in range - try paper comparison for close ones
        close = [s for s in scored if -3 <= s["h_diff"] <= 5 and s["name_sim"] >= 60]
        if close and current_papers:
            print(f"    No candidate in [0,2] range, checking papers for {len(close)} close candidates...")
            best_overlap = -1
            best_candidate = None
            for c in close:
                if not c["id"]:
                    continue
                c_papers = fetch_author_papers(c["id"])
                time.sleep(API_SLEEP)
                overlap = fuzzy_title_overlap(current_papers, c_papers)
                print(f"      {c['name']} (h={c['h']}): {overlap} paper overlaps")
                if overlap > best_overlap:
                    best_overlap = overlap
                    best_candidate = c

            if best_candidate and best_overlap >= 2:
                print(f"    -> Paper match found: {best_candidate['name']} ({best_overlap} overlaps)")
                return best_candidate

        # Check if current match has good name similarity (might be correct despite h-index gap)
        current_name_sim = name_similarity(search_name, current_s2_name)
        if current_name_sim >= 80 and abs(diff) <= 5:
            print(f"    -> Keeping current match (name_sim={current_name_sim:.0f}, moderate gap)")
            return "keep"

        print(f"    -> No good match found, will clear S2 fields")
        return "clear"

    # Among good candidates, prefer higher name similarity, then closer h-index
    good.sort(key=lambda s: (s["name_sim"], -abs(s["h_diff"])), reverse=True)

    best = good[0]

    # If best is the same as current, keep
    if best["id"] == current_s2_id:
        print(f"    -> Current match is correct")
        return "keep"

    # Verify with paper comparison if we have current papers
    if current_papers and best["id"]:
        best_papers = fetch_author_papers(best["id"])
        time.sleep(API_SLEEP)
        overlap = fuzzy_title_overlap(current_papers, best_papers)
        print(f"    -> Best candidate {best['name']} has {overlap} paper overlaps with current")

        if overlap >= 3:
            # Same person, different profile - pick the one in range
            print(f"    -> Same person (high overlap), picking in-range profile")
            return best
        elif overlap == 0 and len(current_papers) > 3:
            # Different person - pick the new one if in range
            print(f"    -> Different person (no overlap), switching to in-range candidate")
            return best

    print(f"    -> Switching to: {best['name']} (h={best['h']}, diff={best['h_diff']:.0f})")
    return best


def main():
    with open(INPUT_FILE, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        all_rows = list(reader)

    header = all_rows[0]
    data_rows = all_rows[1:]

    gi = {col: i for i, col in enumerate(header)}

    required = ['clean_name', 'name', 's2_author_id', 's2_name', 'h_index',
                'hindex_scholar', 'citation_count', 'paper_count', 'fix_status',
                's2_profile_url']
    for col in required:
        if col not in gi:
            print(f"Missing column: {col}")
            return

    violations = find_violations(data_rows, gi)
    print(f"Found {len(violations)} violations (h-index diff outside [0,2])")
    print()

    # Sort: worst violations first (largest |diff|)
    violations.sort(key=lambda v: -abs(v[3]))

    fixed = 0
    cleared = 0
    kept = 0

    for vi, (row_idx, gs_h, s2_h, diff) in enumerate(violations):
        row = data_rows[row_idx]
        flag = "S2>GS" if diff < 0 else "GAP"
        print(f"\n[{vi+1}/{len(violations)}] Line {row_idx+2} | {flag} diff={diff:+.0f} | "
              f"GS_h={gs_h:.0f} S2_h={s2_h:.0f} | "
              f"name=\"{row[gi['clean_name']]}\" s2=\"{row[gi['s2_name']]}\"")

        result = process_violation(row, gi, gs_h, s2_h, diff)

        if result is None or result == "keep":
            kept += 1
            continue
        elif result == "clear":
            for col in ['s2_author_id', 's2_name', 'h_index', 'citation_count',
                         'paper_count', 's2_profile_url']:
                row[gi[col]] = ''
            row[gi['fix_status']] = 'cleared_wrong_match'
            row[gi['metrics_fetched']] = ''
            cleared += 1
        elif isinstance(result, dict):
            # Fetch full metrics for the new match
            new_id = result["id"]
            metrics = fetch_author_metrics(new_id)
            time.sleep(API_SLEEP)

            if metrics:
                row[gi['s2_author_id']] = new_id
                row[gi['s2_name']] = metrics['s2_name'] or ''
                row[gi['h_index']] = str(metrics['h_index']) if metrics['h_index'] is not None else ''
                row[gi['citation_count']] = str(metrics['citation_count']) if metrics['citation_count'] is not None else ''
                row[gi['paper_count']] = str(metrics['paper_count']) if metrics['paper_count'] is not None else ''
                row[gi['s2_profile_url']] = f"https://www.semanticscholar.org/author/{new_id}"
                row[gi['fix_status']] = 'rechecked_fixed'
                row[gi['metrics_fetched']] = ''
                fixed += 1
            else:
                print(f"    Failed to fetch metrics for {new_id}")
                kept += 1

        # Checkpoint save every 5 fixes
        if (fixed + cleared) % 5 == 0 and (fixed + cleared) > 0:
            with open(OUTPUT_FILE, 'w', encoding='utf-8', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(header)
                writer.writerows(data_rows)
            print(f"  [Checkpoint saved]")

    # Final save
    with open(OUTPUT_FILE, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(data_rows)

    print(f"\n{'='*60}")
    print(f"Done. Fixed: {fixed}, Cleared: {cleared}, Kept: {kept}")
    print(f"Saved to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
