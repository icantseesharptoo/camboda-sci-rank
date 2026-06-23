import pandas as pd
import re


INPUT_FILE = "authors_full.csv"
OUTPUT_FILE = "authors_converted.csv"


def extract_domain(email_field):
    """
    Extract domain from:
    'Verified email at xxx.edu.kh'
    """

    if not isinstance(email_field, str):
        return "unknown"

    # normalize text
    text = email_field.lower()

    # match after "verified email at"
    match = re.search(r"verified email at\s+([a-z0-9.-]+\.[a-z]{2,})", text)

    if match:
        domain = match.group(1)

        # clean trailing junk like " - homepage"
        domain = domain.split()[0]
        domain = domain.replace("-", "").strip()

        return domain

    return "unknown"


def clean_numeric(value):
    try:
        if pd.isna(value):
            return 0
        return int(str(value).strip())
    except:
        return 0


def main():

    df = pd.read_csv(INPUT_FILE)

    # ensure required columns exist
    for col in ["name", "affiliation", "email", "citations", "hindex", "i10index", "profile"]:
        if col not in df.columns:
            df[col] = ""

    # clean numbers
    df["citations"] = df["citations"].apply(clean_numeric)
    df["hindex"] = df["hindex"].apply(clean_numeric)
    df["i10index"] = df["i10index"].apply(clean_numeric)

    # extract domain correctly
    df["domain"] = df["email"].apply(extract_domain)

    # reorder
    df = df[
        [
            "domain",
            "name",
            "affiliation",
            "email",
            "citations",
            "hindex",
            "i10index",
            "profile"
        ]
    ]

    df = df.drop_duplicates(subset=["profile"])

    df.to_csv(OUTPUT_FILE, index=False, encoding="utf-8")

    print("✅ Done")
    print("Saved:", OUTPUT_FILE)
    print("Rows:", len(df))
    print("\nSample domains:")
    print(df["domain"].value_counts().head(10))


if __name__ == "__main__":
    main()