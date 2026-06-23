import pandas as pd
import requests
from fuzzywuzzy import fuzz


SCHOLAR_FILE = "full_list.csv"
OUTPUT_FILE = "merged_research_db.csv"


# ----------------------------
# ORCID API LOOKUP
# ----------------------------
def get_orcid_data(name):
    """
    Simple ORCID search (public API)
    """

    try:
        url = f"https://pub.orcid.org/v3.0/search/?q={name}"
        headers = {"Accept": "application/json"}

        r = requests.get(url, headers=headers, timeout=10)

        if r.status_code != 200:
            return None

        data = r.json()

        if "result" not in data:
            return None

        if len(data["result"]) == 0:
            return None

        orcid_id = data["result"][0]["orcid-identifier"]["path"]

        return {
            "orcid": orcid_id
        }

    except:
        return None


# ----------------------------
# SCOPUS API (placeholder)
# ----------------------------
def get_scopus_data(name):
    """
    Requires Elsevier API key.
    This is a placeholder structure.
    """

    API_KEY = "162736c2f0f6e13f5f814ae8f5c7d7c5"

    try:
        url = (
            "https://api.elsevier.com/content/search/scopus"
            f"?query=AUTHLASTNAME({name})"
        )

        headers = {
            "X-ELS-APIKey": API_KEY,
            "Accept": "application/json"
        }

        r = requests.get(url, headers=headers, timeout=10)

        if r.status_code != 200:
            return None

        data = r.json()

        return {
            "scopus": "found" if data else "none"
        }

    except:
        return None


# ----------------------------
# NAME MATCHING
# ----------------------------
def is_same_person(name1, name2):

    if pd.isna(name1) or pd.isna(name2):
        return False

    score = fuzz.token_set_ratio(str(name1), str(name2))

    return score > 90


# ----------------------------
# MAIN PIPELINE
# ----------------------------
def main():

    df = pd.read_csv(SCHOLAR_FILE)

    enriched = []

    for idx, row in df.iterrows():

        name = row.get("name", "")

        print(f"[{idx}] Processing: {name}")

        # ORCID lookup
        orcid_data = get_orcid_data(name)

        # Scopus lookup (optional)
        scopus_data = get_scopus_data(name)

        enriched.append({
            "name": name,
            "affiliation": row.get("affiliation", ""),
            "email": row.get("email", ""),
            "domain": row.get("domain", ""),
            "citations_scholar": row.get("citations", 0),
            "hindex_scholar": row.get("hindex", 0),
            "i10_scholar": row.get("i10index", 0),
            "profile_scholar": row.get("profile", ""),
            "orcid": orcid_data["orcid"] if orcid_data else None,
            "scopus_status": scopus_data["scopus"] if scopus_data else None
        })

    out = pd.DataFrame(enriched)

    # remove duplicates (based on name similarity)
    final = []

    for i, row in out.iterrows():

        duplicate = False

        for f in final:
            if is_same_person(row["name"], f["name"]):
                duplicate = True
                break

        if not duplicate:
            final.append(row.to_dict())

    final_df = pd.DataFrame(final)

    final_df.to_csv(OUTPUT_FILE, index=False, encoding="utf-8")

    print("\n✅ Done")
    print("Final researchers:", len(final_df))
    print("Saved:", OUTPUT_FILE)


if __name__ == "__main__":
    main()