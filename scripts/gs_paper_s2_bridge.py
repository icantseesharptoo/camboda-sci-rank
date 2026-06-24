"""
For each gs_suspect=unverified author:
  1. Search S2 for papers matching the author's name
  2. Find the author in paper author lists
  3. Get that author's S2 profile + metrics
  4. Assign if s2_h <= gs_h and name matches

No GS scraping needed — uses S2 paper search as bridge.
"""

import csv
import json
import os
import re
import time
import urllib.request
import urllib.parse
import urllib.error
from functools import partial
from rapidfuzz import fuzz

print = partial(print, flush=True)

_ROOT = os.path.join(os.path.dirname(__file__), "..")
INPUT_CSV = os.path.join(_ROOT, "scholar_full_sync_output.csv")
OUTPUT_CSV = os.path.join(_ROOT, "scholar_full_sync_output.csv")

S2_API_BASE = "https://api.semanticscholar.org/graph/v1"
S2_API_KEY = os.environ.get("S2_API_KEY", "s2k-FShcrmhCdVNMSOlsDvk1kvUMotFlTEIyIqqgE3Fl")
API_SLEEP = 1.2


def s2_get(path, params=None):
    url = f"{S2_API_BASE}{path}"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    headers = {"Accept": "application/json", "User-Agent": "camboda-sci-rank/1.0"}
    if S2_API_KEY:
        headers["x-api-key"] = S2_API_KEY
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=25) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(15 * (attempt + 1))
                continue
            if e.code == 404:
                return None
            return None
        except Exception:
            time.sleep(5)
    return None


def normalize(s):
    return re.sub(r'[^a-z ]', '', s.lower()).strip()


def name_sim(a, b):
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0
    return fuzz.token_sort_ratio(na, nb)


def name_parts_match(clean_name, candidate_name):
    """Check if family name appears in both + first letter matches."""
    cp = normalize(clean_name).split()
    sp = normalize(candidate_name).split()
    if not cp or not sp:
        return False
    shared = set(cp) & set(p for p in sp if len(p) > 1)
    return len(shared) >= 1


def search_author_papers(name):
    """Search S2 for papers, return papers with author details."""
    data = s2_get("/paper/search", {
        "query": name,
        "fields": "title,authors,authors.authorId,authors.name",
        "limit": 10,
    })
    if not data:
        return []
    return data.get("data", [])


def search_author_direct(name):
    """Direct author search on S2."""
    data = s2_get("/author/search", {
        "query": name,
        "fields": "name,hIndex,citationCount,paperCount,authorId",
        "limit": 10,
    })
    if not data:
        return []
    return data.get("data", [])


def fetch_author_metrics(author_id):
    data = s2_get(f"/author/{author_id}", {
        "fields": "hIndex,citationCount,paperCount,name"
    })
    if not data:
        return None
    return {
        "s2_author_id": str(author_id),
        "s2_name": data.get("name", ""),
        "h_index": data.get("hIndex"),
        "citation_count": data.get("citationCount"),
        "paper_count": data.get("paperCount"),
    }


def find_best_author(clean_name, gs_h):
    """
    Strategy 1: Search papers by name, find author in results.
    Strategy 2: Direct author search as fallback.
    Pick the candidate with highest h-index that's still <= gs_h.
    """
    seen_ids = set()
    candidates = []

    # Strategy 1: paper search
    papers = search_author_papers(clean_name)
    time.sleep(API_SLEEP)

    for paper in papers:
        for author in (paper.get("authors") or []):
            a_id = author.get("authorId")
            a_name = author.get("name", "")
            if not a_id or a_id in seen_ids:
                continue
            seen_ids.add(a_id)
            sim = name_sim(clean_name, a_name)
            parts = name_parts_match(clean_name, a_name)
            if sim >= 70 or (parts and sim >= 55):
                candidates.append({"id": a_id, "name": a_name, "sim": sim, "source": "paper"})

    # Strategy 2: direct author search
    authors = search_author_direct(clean_name)
    time.sleep(API_SLEEP)

    for author in authors:
        a_id = str(author.get("authorId", ""))
        a_name = author.get("name", "")
        a_h = author.get("hIndex") or 0
        if not a_id or a_id in seen_ids:
            continue
        seen_ids.add(a_id)
        sim = name_sim(clean_name, a_name)
        parts = name_parts_match(clean_name, a_name)
        if sim >= 70 or (parts and sim >= 55):
            candidates.append({
                "id": a_id, "name": a_name, "sim": sim, "source": "direct",
                "h": a_h, "cit": author.get("citationCount", 0),
            })

    if not candidates:
        return None

    # Fetch metrics for paper-search candidates that don't have them
    for c in candidates:
        if "h" not in c:
            metrics = fetch_author_metrics(c["id"])
            time.sleep(API_SLEEP)
            if metrics:
                c["h"] = metrics["h_index"] or 0
                c["cit"] = metrics.get("citation_count", 0)
            else:
                c["h"] = 0
                c["cit"] = 0

    # Filter: s2_h <= gs_h
    valid = [c for c in candidates if c["h"] <= gs_h]
    if not valid:
        return None

    # Pick highest h-index, then highest name similarity
    valid.sort(key=lambda c: (c["h"], c["sim"]), reverse=True)
    return valid[0]


def load_csv():
    with open(INPUT_CSV, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        all_rows = list(reader)
    header = all_rows[0]
    data = all_rows[1:]
    gi = {col: i for i, col in enumerate(header)}
    return header, data, gi


def save_csv(header, data):
    with open(OUTPUT_CSV, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(data)


def main():
    header, data, gi = load_csv()

    suspects = []
    for i, row in enumerate(data):
        if row[gi['gs_suspect']].strip() == 'unverified':
            try:
                gs_h = float(row[gi['hindex_scholar']]) if row[gi['hindex_scholar']].strip() else 0
            except ValueError:
                gs_h = 0
            suspects.append((i, gs_h))

    suspects.sort(key=lambda x: -x[1])
    print(f"Unverified authors to check: {len(suspects)}\n")

    found = 0
    failed = 0

    for vi, (row_idx, gs_h) in enumerate(suspects):
        row = data[row_idx]
        clean = row[gi['clean_name']].strip() or row[gi['name']].strip()

        print(f"[{vi+1}/{len(suspects)}] {clean} (GS_h={gs_h:.0f})")

        best = find_best_author(clean, gs_h)

        if not best:
            print(f"  No valid S2 match found")
            failed += 1
            continue

        # Fetch full metrics
        metrics = fetch_author_metrics(best["id"])
        time.sleep(API_SLEEP)
        if not metrics:
            print(f"  Failed to fetch metrics for {best['id']}")
            failed += 1
            continue

        s2_h = metrics['h_index'] or 0
        if s2_h > gs_h:
            print(f"  SKIP: S2 h={s2_h} > GS h={gs_h:.0f}")
            failed += 1
            continue

        # Assign
        row[gi['s2_author_id']] = metrics['s2_author_id']
        row[gi['s2_name']] = metrics['s2_name']
        row[gi['h_index']] = str(s2_h) if s2_h is not None else ''
        row[gi['citation_count']] = str(metrics['citation_count']) if metrics['citation_count'] is not None else ''
        row[gi['paper_count']] = str(metrics['paper_count']) if metrics['paper_count'] is not None else ''
        row[gi['s2_profile_url']] = f"https://www.semanticscholar.org/author/{best['id']}"
        row[gi['fix_status']] = 'paper_bridge'
        row[gi['gs_suspect']] = ''
        found += 1
        print(f"  ASSIGNED: {metrics['s2_name']} h={s2_h} (sim={best['sim']:.0f}, via {best['source']})")

        if found % 5 == 0:
            save_csv(header, data)
            print(f"  [Checkpoint saved]")

    save_csv(header, data)
    print(f"\n{'='*60}")
    print(f"Done. Found S2: {found}, Failed: {failed}, Total: {len(suspects)}")
    print(f"Saved to {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
