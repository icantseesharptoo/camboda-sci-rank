import os
import pandas as pd
import numpy as np
import json

_ROOT = os.path.join(os.path.dirname(__file__), "..")
INPUT_FILE = os.path.join(_ROOT, "scholar_full_sync_output.csv")
OUTPUT_JSON = os.path.join(_ROOT, "rankings_summary.json")
OUTPUT_CSV = os.path.join(_ROOT, "rankings_summary.csv")
OUTPUT_JS = os.path.join(_ROOT, "rankings_data.js")

def _load_university_names():
    import csv as _csv
    path = os.path.join(_ROOT, "institutions.csv")
    mapping = {}
    with open(path, encoding="utf-8") as f:
        for row in _csv.DictReader(f):
            mapping[row["domain"]] = row["name"]
    return mapping

university_names = _load_university_names()

# Canonical field names (CSV column → logical name)
FIELD_MAP = {
    "hindex_scholar": "hindex_scholar",
    "h_index": "hindex_s2",
    "citations_scholar": "citations_scholar",
    "citation_count": "citations_s2",
    "paper_count": "paper_count_s2",
}

URI_WEIGHTS = {"H": 0.45, "C": 0.45, "P": 0.10}
SOURCE_BLEND = {"GS": 0.3, "S2": 0.7}
SMALL_COHORT_THRESHOLD = 3

METHODOLOGY = {
    "name": "University Research Index (URI)",
    "formula": "URI = (0.45·H + 0.45·C_norm + 0.10·P_norm) × log(1 + N)",
    "aggregation": "Per-metric university aggregate = sum of log(1 + x_i) over faculty i",
    "H": "H = 0.3·H_GS + 0.7·H_S2 (log-aggregated h-indices)",
    "C": "C_blend = 0.3·C_GS + 0.7·C_S2; C_norm = min-max(C_blend) across universities",
    "P": "P_term = log(1 + P_S2); P_norm = min-max(P_term) across universities",
    "size_factor": "log(1 + N) where N = faculty count at the university",
    "missing_values": "Non-numeric and missing values are coerced to 0 before aggregation",
    "s2_priority": "Semantic Scholar metrics receive 70% weight vs 30% for Google Scholar in H and C",
}


def log_aggregate(values):
    """Sum of log(1+x) across faculty — dampens outliers, rewards breadth."""
    return float(np.sum(np.log1p(np.maximum(values, 0))))


def min_max_normalize(series):
    s_min = series.min()
    s_max = series.max()
    if s_max == s_min:
        return pd.Series(0.0, index=series.index)
    return (series - s_min) / (s_max - s_min)


def assess_data_quality(researchers, faculty_count):
    n = len(researchers)
    missing_s2 = sum(
        1 for r in researchers
        if r["hindex_s2"] == 0 and r["citations_s2"] == 0 and r["paper_count_s2"] == 0
    )
    zero_h_gs = sum(1 for r in researchers if r["hindex_scholar"] == 0)
    zero_h_s2 = sum(1 for r in researchers if r["hindex_s2"] == 0)
    notes = []
    if faculty_count < SMALL_COHORT_THRESHOLD:
        notes.append(f"Small cohort (N={faculty_count}): URI less stable with fewer than {SMALL_COHORT_THRESHOLD} faculty.")
    if missing_s2 > 0:
        pct = round(100 * missing_s2 / n, 1) if n else 0
        notes.append(f"{missing_s2}/{n} faculty ({pct}%) have no Semantic Scholar metrics (all S2 fields zero).")
    if zero_h_gs == n:
        notes.append("All faculty have zero Google Scholar h-index.")
    if zero_h_s2 == n:
        notes.append("All faculty have zero Semantic Scholar h-index.")
    return {
        "faculty_count": faculty_count,
        "missing_s2_count": missing_s2,
        "missing_s2_pct": round(100 * missing_s2 / n, 1) if n else 0,
        "zero_h_gs_count": zero_h_gs,
        "zero_h_s2_count": zero_h_s2,
        "small_cohort": faculty_count < SMALL_COHORT_THRESHOLD,
        "notes": notes,
    }


def main():
    print(f"Loading {INPUT_FILE}...")
    df = pd.read_csv(INPUT_FILE)

    # Clean types; missing → 0
    for csv_col in FIELD_MAP:
        df[csv_col] = pd.to_numeric(df[csv_col], errors="coerce").fillna(0.0).clip(lower=0)

    df["university"] = df["domain"].map(university_names).fillna(df["domain"])

    records = []
    global_missing_s2 = 0
    global_authors = 0
    global_small_cohorts = 0

    for univ_name, group in df.groupby("university"):
        domains = list(group["domain"].unique())
        faculty_count = len(group)

        researchers = []
        for _, row in group.iterrows():
            def _str(col):
                v = row.get(col, "")
                return str(v) if pd.notna(v) else ""

            orcid_id = _str("orcid")
            orcid_url = f"https://orcid.org/{orcid_id}" if orcid_id else ""
            s2_url = _str("s2_profile_url")
            scopus_url = _str("scopus_url")

            researchers.append({
                "name": _str("name"),
                "clean_name": _str("clean_name"),
                "affiliation": _str("affiliation"),
                "profile_url": _str("profile_scholar"),
                "orcid": orcid_id,
                "orcid_url": orcid_url,
                "scopus_url": scopus_url,
                "s2_profile_url": s2_url,
                "citations_scholar": float(row["citations_scholar"]),
                "hindex_scholar": float(row["hindex_scholar"]),
                "i10_scholar": float(row.get("i10_scholar", 0) or 0),
                "hindex_s2": float(row["h_index"]),
                "citations_s2": float(row["citation_count"]),
                "paper_count_s2": float(row["paper_count"]),
                "h_index": float(row["h_index"]),
                "citation_count": float(row["citation_count"]),
                "paper_count": float(row["paper_count"]),
                "gs_suspect": _str("gs_suspect"),
            })
        researchers.sort(key=lambda x: x["citations_scholar"], reverse=True)

        ranked = [r for r in researchers if not r.get("gs_suspect")]
        h_gs = log_aggregate([r["hindex_scholar"] for r in ranked])
        h_s2 = log_aggregate([r["hindex_s2"] for r in ranked])
        c_gs = log_aggregate([r["citations_scholar"] for r in ranked])
        c_s2 = log_aggregate([r["citations_s2"] for r in ranked])
        p_s2 = log_aggregate([r["paper_count_s2"] for r in ranked])

        quality = assess_data_quality(researchers, faculty_count)
        global_missing_s2 += quality["missing_s2_count"]
        global_authors += faculty_count
        if quality["small_cohort"]:
            global_small_cohorts += 1

        records.append({
            "university": univ_name,
            "domains": domains,
            "faculty_count": faculty_count,
            "researchers": researchers,
            "uri_components": {
                "H_GS": h_gs,
                "H_S2": h_s2,
                "C_GS": c_gs,
                "C_S2": c_s2,
                "P_S2": p_s2,
            },
            "data_quality": quality,
        })

    # URI computation across universities
    df_uri = pd.DataFrame([
        {
            "university": r["university"],
            "faculty_count": r["faculty_count"],
            **r["uri_components"],
        }
        for r in records
    ])

    df_uri["H"] = SOURCE_BLEND["GS"] * df_uri["H_GS"] + SOURCE_BLEND["S2"] * df_uri["H_S2"]
    df_uri["C_blend"] = SOURCE_BLEND["GS"] * df_uri["C_GS"] + SOURCE_BLEND["S2"] * df_uri["C_S2"]
    df_uri["C_norm"] = min_max_normalize(df_uri["C_blend"])
    df_uri["P_term"] = np.log1p(df_uri["P_S2"])
    df_uri["P_norm"] = min_max_normalize(df_uri["P_term"])
    df_uri["uri_base"] = (
        URI_WEIGHTS["H"] * df_uri["H"]
        + URI_WEIGHTS["C"] * df_uri["C_norm"]
        + URI_WEIGHTS["P"] * df_uri["P_norm"]
    )
    df_uri["size_factor"] = np.log1p(df_uri["faculty_count"])
    df_uri["uri"] = df_uri["uri_base"] * df_uri["size_factor"]

    df_uri = df_uri.sort_values(by="uri", ascending=False).reset_index(drop=True)
    df_uri["rank"] = df_uri.index + 1

    uri_lookup = df_uri.set_index("university").to_dict(orient="index")

    global_quality = {
        "total_universities": len(records),
        "total_authors": global_authors,
        "authors_missing_s2": global_missing_s2,
        "authors_missing_s2_pct": round(100 * global_missing_s2 / global_authors, 1) if global_authors else 0,
        "universities_small_cohort": global_small_cohorts,
        "small_cohort_threshold": SMALL_COHORT_THRESHOLD,
        "notes": [
            f"{global_missing_s2} of {global_authors} authors ({round(100 * global_missing_s2 / global_authors, 1) if global_authors else 0}%) lack Semantic Scholar data (all S2 metrics zero).",
            f"{global_small_cohorts} universities have fewer than {SMALL_COHORT_THRESHOLD} faculty in the dataset.",
            "Google Scholar coverage is generally higher than Semantic Scholar for Cambodian institutions.",
            "URI uses log(1+x) aggregation to reduce sensitivity to single high-performing outliers.",
            "Citations and paper output are min-max scaled; h-index blend (H) is not scaled before weighting.",
        ],
    }

    final_output = []
    for r in records:
        univ = r["university"]
        u = uri_lookup[univ]
        comp = r["uri_components"]

        uri_system = {
            "uri": float(u["uri"]),
            "rank": int(u["rank"]),
            "uri_base": float(u["uri_base"]),
            "size_factor": float(u["size_factor"]),
            "components": {
                "H": float(u["H"]),
                "C_norm": float(u["C_norm"]),
                "P_norm": float(u["P_norm"]),
                "C_blend": float(u["C_blend"]),
                "P_term": float(u["P_term"]),
                "H_GS": comp["H_GS"],
                "H_S2": comp["H_S2"],
                "C_GS": comp["C_GS"],
                "C_S2": comp["C_S2"],
                "P_S2": comp["P_S2"],
            },
        }

        final_output.append({
            "university": univ,
            "domains": r["domains"],
            "faculty_count": r["faculty_count"],
            "researchers": r["researchers"],
            "uri_system": uri_system,
            "data_quality": r["data_quality"],
            # Frontend aliases
            "ranking_system": {
                "score": uri_system["uri"],
                "rank": uri_system["rank"],
                "metrics": {
                    "H": uri_system["components"]["H"],
                    "C_norm": uri_system["components"]["C_norm"],
                    "P_norm": uri_system["components"]["P_norm"],
                    "H_GS": comp["H_GS"],
                    "H_S2": comp["H_S2"],
                    "C_GS": comp["C_GS"],
                    "C_S2": comp["C_S2"],
                    "P_S2": comp["P_S2"],
                },
            },
            "weighted_sum_system": {
                "score": uri_system["uri"],
                "rank": uri_system["rank"],
                "metrics": uri_system["components"],
            },
        })

    metadata = {
        "methodology": METHODOLOGY,
        "data_quality": global_quality,
        "weights": URI_WEIGHTS,
        "source_blend": SOURCE_BLEND,
    }

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump({"metadata": metadata, "universities": final_output}, f, indent=4, ensure_ascii=False)
    print(f"Saved JSON rankings to {OUTPUT_JSON}")

    with open(OUTPUT_JS, "w", encoding="utf-8") as f:
        f.write("const rankingsMetadata = ")
        json.dump(metadata, f, indent=4, ensure_ascii=False)
        f.write(";\nconst rankingsData = ")
        json.dump(final_output, f, indent=4, ensure_ascii=False)
        f.write(";\n")
    print(f"Saved JS data to {OUTPUT_JS}")

    csv_rows = []
    for entry in final_output:
        u = entry["uri_system"]
        c = u["components"]
        csv_rows.append({
            "Rank": u["rank"],
            "University": entry["university"],
            "Faculty": entry["faculty_count"],
            "URI": round(u["uri"], 4),
            "H": round(c["H"], 4),
            "C_norm": round(c["C_norm"], 4),
            "P_norm": round(c["P_norm"], 4),
            "URI_base": round(u["uri_base"], 4),
            "Size_factor": round(u["size_factor"], 4),
            "H_GS": round(c["H_GS"], 4),
            "H_S2": round(c["H_S2"], 4),
            "C_GS": round(c["C_GS"], 4),
            "C_S2": round(c["C_S2"], 4),
            "P_S2": round(c["P_S2"], 4),
            "Missing_S2_pct": entry["data_quality"]["missing_s2_pct"],
            "Data_notes": "; ".join(entry["data_quality"]["notes"]),
        })
    df_csv = pd.DataFrame(csv_rows).sort_values("Rank")
    df_csv.to_csv(OUTPUT_CSV, index=False)
    print(f"Saved CSV rankings summary to {OUTPUT_CSV}")

    def _safe_print(text):
        print(text.encode("ascii", "replace").decode("ascii"))

    print("\n--- URI Methodology ---")
    for k, v in METHODOLOGY.items():
        _safe_print(f"  {k}: {v}")
    print("\n--- Global Data Quality ---")
    for note in global_quality["notes"]:
        _safe_print(f"  - {note}")
    print("\n--- Top 5 by URI ---")
    print(df_csv[["Rank", "University", "Faculty", "URI", "H", "C_norm", "P_norm"]].head(5).to_string(index=False))


if __name__ == "__main__":
    main()
