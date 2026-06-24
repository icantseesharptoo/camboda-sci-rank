"""
Look up Scopus Author IDs via the ORCID public API for authors
who have an ORCID but no Scopus URL in the CSV.
"""

import csv
import json
import os
import re
import time
import urllib.request
import urllib.error
from functools import partial

print = partial(print, flush=True)

_ROOT = os.path.join(os.path.dirname(__file__), "..")
INPUT_FILE = os.path.join(_ROOT, "scholar_full_sync_output.csv")
OUTPUT_FILE = os.path.join(_ROOT, "scholar_full_sync_output.csv")
API_SLEEP = 0.5


def normalize_orcid(value):
    if not value or value.strip().lower() in ('', 'nan', 'none'):
        return None
    match = re.search(r"(\d{4}-\d{4}-\d{4}-[\dX]{4})", value, re.IGNORECASE)
    return match.group(1).upper() if match else None


def get_scopus_from_orcid(orcid_id):
    url = f"https://pub.orcid.org/v3.0/{orcid_id}"
    headers = {"Accept": "application/json", "User-Agent": "camboda-sci-rank/1.0"}
    req = urllib.request.Request(url, headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except (urllib.error.HTTPError, urllib.error.URLError, Exception) as e:
        print(f"    Error fetching ORCID {orcid_id}: {e}")
        return None

    external_ids = (
        data.get("person", {})
            .get("external-identifiers", {})
            .get("external-identifier", [])
    )

    for eid in external_ids:
        if eid.get("external-id-type") == "Scopus Author ID":
            scopus_id = eid.get("external-id-value")
            if scopus_id:
                return {
                    "scopus_id": scopus_id,
                    "scopus_url": f"https://www.scopus.com/authid/detail.uri?authorId={scopus_id}",
                }
    return None


def main():
    with open(INPUT_FILE, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        all_rows = list(reader)

    header = all_rows[0]
    data = all_rows[1:]
    gi = {col: i for i, col in enumerate(header)}

    candidates = []
    for i, row in enumerate(data):
        orcid = normalize_orcid(row[gi['orcid']])
        scopus_url = row[gi['scopus_url']].strip()
        has_scopus = scopus_url and scopus_url.lower() not in ('nan', 'none', '')
        if orcid and not has_scopus:
            candidates.append((i, orcid))

    print(f"Authors with ORCID but no Scopus: {len(candidates)}")
    print()

    found = 0
    for vi, (row_idx, orcid) in enumerate(candidates):
        row = data[row_idx]
        name = row[gi['clean_name']] or row[gi['name']]

        result = get_scopus_from_orcid(orcid)
        time.sleep(API_SLEEP)

        if result:
            found += 1
            row[gi['scopus_id']] = result['scopus_id']
            row[gi['scopus_url']] = result['scopus_url']
            if not row[gi['scopus_status']].strip() or row[gi['scopus_status']].strip().lower() in ('', 'nan'):
                row[gi['scopus_status']] = 'found'
            print(f"[{vi+1}/{len(candidates)}] {name:<30} -> Scopus ID: {result['scopus_id']}")
        else:
            if (vi + 1) % 25 == 0:
                print(f"[{vi+1}/{len(candidates)}] {name:<30} -> no Scopus")

        if found > 0 and found % 10 == 0:
            with open(OUTPUT_FILE, 'w', encoding='utf-8', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(header)
                writer.writerows(data)

    with open(OUTPUT_FILE, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(data)

    print(f"\nDone. Found Scopus for {found}/{len(candidates)} authors.")
    print(f"Saved to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
