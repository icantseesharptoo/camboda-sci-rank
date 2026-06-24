"""
Search ORCID for researchers affiliated with each Cambodian institution,
match against existing authors in scholar_full_sync_output.csv to:
  1. Fill missing ORCID IDs for existing authors
  2. Fill missing Scopus IDs via ORCID external identifiers
  3. Report unmatched ORCID profiles (potential new researchers)
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
INSTITUTIONS_CSV = os.path.join(_ROOT, "institutions.csv")

API_SLEEP = 0.4
SEARCH_ROWS = 200


def orcid_api(path, params=None):
    url = f"https://pub.orcid.org/v3.0/{path}"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    headers = {"Accept": "application/json", "User-Agent": "camboda-sci-rank/1.0"}
    req = urllib.request.Request(url, headers=headers)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(10 * (attempt + 1))
                continue
            return None
        except Exception as e:
            if attempt < 2:
                time.sleep(3)
                continue
            return None
    return None


def search_orcid_by_affiliation(institution_name):
    query = f'affiliation-org-name:"{institution_name}"'
    data = orcid_api("search/", {"q": query, "rows": SEARCH_ROWS})
    if not data or "result" not in data:
        return []

    results = []
    for item in (data.get("result") or []):
        orcid_id = (item or {}).get("orcid-identifier", {}).get("path")
        if orcid_id:
            results.append(orcid_id)
    return results


def fetch_orcid_profile(orcid_id):
    data = orcid_api(orcid_id)
    if not data:
        return None

    person = data.get("person", {})
    name_data = person.get("name", {})

    given = ""
    family = ""
    if name_data:
        gn = name_data.get("given-names")
        fn = name_data.get("family-name")
        if gn and isinstance(gn, dict):
            given = gn.get("value", "")
        if fn and isinstance(fn, dict):
            family = fn.get("value", "")

    full_name = f"{given} {family}".strip()

    external_ids = (
        person.get("external-identifiers", {})
              .get("external-identifier", [])
    )

    scopus_id = None
    for eid in external_ids:
        if eid.get("external-id-type") == "Scopus Author ID":
            scopus_id = eid.get("external-id-value")
            break

    return {
        "orcid": orcid_id,
        "name": full_name,
        "given": given,
        "family": family,
        "scopus_id": scopus_id,
    }


def normalize(s):
    return re.sub(r'[^a-z ]', '', s.lower()).strip()


def name_match(name_a, name_b):
    na = normalize(name_a)
    nb = normalize(name_b)
    if not na or not nb:
        return 0
    return fuzz.token_sort_ratio(na, nb)


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
    with open(INSTITUTIONS_CSV, 'r', encoding='utf-8') as f:
        institutions = list(csv.DictReader(f))

    active_institutions = [inst for inst in institutions if int(inst['authors']) > 0]
    print(f"Institutions to search: {len(active_institutions)}")

    header, data, gi = load_csv()

    total_orcid_added = 0
    total_scopus_added = 0
    total_orcid_profiles = 0

    for inst_i, inst in enumerate(active_institutions):
        domain = inst['domain']
        name = inst['name']

        domain_rows = [(i, row) for i, row in enumerate(data)
                       if row[gi['domain']].strip() == domain]

        print(f"\n[{inst_i+1}/{len(active_institutions)}] {name} ({domain}) — {len(domain_rows)} authors in CSV")

        print(f"  Searching ORCID for \"{name}\"...")
        orcid_ids = search_orcid_by_affiliation(name)
        time.sleep(API_SLEEP)

        if not orcid_ids:
            print(f"  No ORCID results.")
            continue

        print(f"  Found {len(orcid_ids)} ORCID profiles")
        total_orcid_profiles += len(orcid_ids)

        for oi, orcid_id in enumerate(orcid_ids):
            already_has = any(
                row[gi['orcid']].strip() == orcid_id
                for _, row in domain_rows
            )
            if already_has:
                continue

            profile = fetch_orcid_profile(orcid_id)
            time.sleep(API_SLEEP)
            if not profile or not profile['name']:
                continue

            best_match_idx = None
            best_score = 0
            for row_i, row in domain_rows:
                clean = row[gi['clean_name']].strip()
                orig = row[gi['name']].strip()
                check_name = clean or orig

                score = name_match(profile['name'], check_name)
                if score > best_score:
                    best_score = score
                    best_match_idx = row_i

            if best_score >= 85 and best_match_idx is not None:
                row = data[best_match_idx]
                existing_orcid = row[gi['orcid']].strip()

                if not existing_orcid or existing_orcid.lower() in ('nan', 'none', ''):
                    row[gi['orcid']] = orcid_id
                    total_orcid_added += 1
                    print(f"  + ORCID {orcid_id} -> {row[gi['clean_name']]} (score={best_score:.0f})")

                existing_scopus = row[gi['scopus_url']].strip()
                if profile['scopus_id'] and (not existing_scopus or existing_scopus.lower() in ('nan', 'none', '')):
                    row[gi['scopus_id']] = profile['scopus_id']
                    row[gi['scopus_url']] = f"https://www.scopus.com/authid/detail.uri?authorId={profile['scopus_id']}"
                    if not row[gi['scopus_status']].strip():
                        row[gi['scopus_status']] = 'found'
                    total_scopus_added += 1
                    print(f"  + Scopus {profile['scopus_id']} -> {row[gi['clean_name']]}")

        if (inst_i + 1) % 5 == 0:
            save_csv(header, data)
            print(f"  [Checkpoint saved]")

    save_csv(header, data)

    print(f"\n{'='*60}")
    print(f"Done.")
    print(f"  ORCID profiles found across all institutions: {total_orcid_profiles}")
    print(f"  New ORCID IDs added to existing authors: {total_orcid_added}")
    print(f"  New Scopus IDs added via ORCID: {total_scopus_added}")
    print(f"  Saved to {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
