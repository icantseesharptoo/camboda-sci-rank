import pandas as pd

df = pd.read_csv("merged_with_s2_metrics_fixed2.csv")
print("Total rows:", len(df))
print("Columns:", df.columns.tolist())
print("\nUnique domains:")
domain_counts = df["domain"].value_counts()
print(domain_counts)

print("\nNull counts:")
print(df[["domain", "citations_scholar", "hindex_scholar", "i10_scholar", "h_index", "citation_count", "paper_count"]].isnull().sum())

print("\nSample values:")
print(df[["domain", "citations_scholar", "hindex_scholar", "i10_scholar", "h_index", "citation_count", "paper_count"]].head(10))
