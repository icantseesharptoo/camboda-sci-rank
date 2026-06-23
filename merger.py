import pandas as pd

FILES = [
    "authors_converted.csv",
    "google_scholar_hindex_.csv",
    "google_scholar_hindex_new.csv",
    "google_scholar_hindex.csv"
]

OUTPUT_FILE = "full_list.csv"


def main():

    # load all files
    dfs = [pd.read_csv(f) for f in FILES]

    # concatenate
    df = pd.concat(dfs, ignore_index=True)

    # clean whitespace in columns (optional but useful)
    df.columns = [c.strip() for c in df.columns]

    # remove duplicates (best key = profile URL)
    if "profile" in df.columns:
        df = df.drop_duplicates(subset=["profile"])
    else:
        df = df.drop_duplicates()

    # save result
    df.to_csv(OUTPUT_FILE, index=False, encoding="utf-8")

    print("✅ Merged files:", len(FILES))
    print("📊 Total rows after merge:", len(df))
    print("💾 Saved to:", OUTPUT_FILE)


if __name__ == "__main__":
    main()