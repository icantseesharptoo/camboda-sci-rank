import pandas as pd
import requests
import time
import re


INPUT_FILE = "merged_research_db.csv"
OUTPUT_FILE = "merged_with_scopus.csv"


# ----------------------------
# extract ORCID from text (if full URL or id exists)
# ----------------------------
def normalize_orcid(value):

    if pd.isna(value):
        return None

    value = str(value).strip()

    match = re.search(r"(\d{4}-\d{4}-\d{4}-\d{4})", value)

    if match:
        return match.group(1)

    return None


# ----------------------------
# query ORCID API
# ----------------------------
def get_scopus_from_orcid(orcid_id):

    try:
        url = f"https://pub.orcid.org/v3.0/{orcid_id}"

        headers = {
            "Accept": "application/json"
        }

        r = requests.get(url, headers=headers, timeout=15)

        if r.status_code != 200:
            return None

        data = r.json()

        external_ids = (
            data.get("person", {})
                .get("external-identifiers", {})
                .get("external-identifier", [])
        )

        for eid in external_ids:
            if eid.get("external-id-type") == "Scopus Author ID":
                scopus_id = eid.get("external-id-value")

                return {
                    "scopus_id": scopus_id,
                    "scopus_url": f"https://www.scopus.com/authid/detail.uri?authorId={scopus_id}"
                }

        return None

    except Exception as e:
        print("ORCID error:", e)
        return None


# ----------------------------
# main pipeline
# ----------------------------
def main():

    df = pd.read_csv(INPUT_FILE)

    if "orcid" not in df.columns:
        print("❌ No ORCID column found")
        return

    scopus_ids = []
    scopus_urls = []

    for i, row in df.iterrows():

        orcid_raw = row.get("orcid", None)
        orcid_id = normalize_orcid(orcid_raw)

        if not orcid_id:
            scopus_ids.append(None)
            scopus_urls.append(None)
            continue

        print(f"[{i}] ORCID: {orcid_id}")

        result = get_scopus_from_orcid(orcid_id)

        if result:
            scopus_ids.append(result["scopus_id"])
            scopus_urls.append(result["scopus_url"])
            print("   ✅ Scopus found:", result["scopus_id"])
        else:
            scopus_ids.append(None)
            scopus_urls.append(None)
            print("   ❌ No Scopus ID")

        time.sleep(0.5)  # be polite to ORCID API

    df["scopus_id"] = scopus_ids
    df["scopus_url"] = scopus_urls

    df.to_csv(OUTPUT_FILE, index=False, encoding="utf-8")

    print("\n✅ Done")
    print("Saved:", OUTPUT_FILE)
    print("Matched Scopus IDs:", df["scopus_id"].notna().sum())


if __name__ == "__main__":
    main()