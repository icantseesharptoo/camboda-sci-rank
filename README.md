# Cambodian University Science Rankings

University Research Index (URI) ranking of Cambodian higher education institutions based on faculty publication metrics from Google Scholar, Semantic Scholar, Scopus, and ORCID.

## Dataset

- **511 researchers** across **36 institutions**
- Google Scholar profiles for all authors (h-index, citations, i10-index)
- Semantic Scholar matches for 361 authors (h-index, citations, paper count)
- Scopus profiles for 69 authors
- ORCID identifiers for 281 authors

## URI Methodology

```
URI = (0.45 * H + 0.45 * C_norm + 0.10 * P_norm) * log(1 + N)
```

| Component | Description |
|-----------|-------------|
| **H** | Blended h-index: `0.3 * H_GS + 0.7 * H_S2`, log-aggregated across faculty |
| **C_norm** | Blended citations: `0.3 * C_GS + 0.7 * C_S2`, min-max normalized |
| **P_norm** | Paper count from S2, double-log transformed, min-max normalized |
| **N** | Faculty count at the university |

Per-faculty metrics are aggregated as `sum(log(1 + x_i))` to dampen outliers and reward breadth over individual stars. Semantic Scholar receives 70% weight to reduce Google Scholar's susceptibility to citation inflation.

## Project Structure

```
camboda-sci-rank/
├── index.html                    # Website (static, no build step)
├── rankings_data.js              # JS data consumed by index.html
├── rankings_summary.json         # Full rankings with researcher details
├── rankings_summary.csv          # Summary table
├── scholar_full_sync_output.csv  # Main researcher database
├── requirements.txt
├── scripts/
│   ├── rank_universities.py      # Generate rankings from CSV
│   ├── scholar_full_sync.py      # Scrape Google Scholar + S2 enrichment
│   ├── recheck_s2.py             # Validate S2 matches by h-index and paper overlap
│   └── orcid_scopus_lookup.py    # Find Scopus profiles via ORCID API
└── data/
    └── legacy/                   # Intermediate CSVs from earlier pipeline stages
```

## Setup

```bash
pip install -r requirements.txt
playwright install chromium
```

## Usage

### Regenerate rankings

```bash
python scripts/rank_universities.py
```

Reads `scholar_full_sync_output.csv`, produces `rankings_summary.json`, `rankings_summary.csv`, and `rankings_data.js`. Open `index.html` in a browser to view the results.

### Sync new authors from Google Scholar

```bash
python scripts/scholar_full_sync.py
```

Opens a Chromium browser to scrape Google Scholar profiles by institutional domain, deduplicates against existing data, and queries the Semantic Scholar API for matching profiles. Saves progress after every author.

### Recheck Semantic Scholar matches

```bash
python scripts/recheck_s2.py
```

Finds authors where the S2 h-index deviates from the Google Scholar h-index beyond the acceptable range, searches for better candidates, and verifies matches by comparing paper titles.

### Look up Scopus profiles via ORCID

```bash
python scripts/orcid_scopus_lookup.py
```

Queries the ORCID public API for each author who has an ORCID but no Scopus link. Adds Scopus Author IDs found in the ORCID external identifiers.

## Data Sources

| Source | Access | Used For |
|--------|--------|----------|
| [Google Scholar](https://scholar.google.com) | Scraped via Playwright | h-index, citations, i10-index, profile URLs |
| [Semantic Scholar API](https://api.semanticscholar.org) | Public API (key optional) | h-index, citations, paper count, author matching |
| [ORCID Public API](https://pub.orcid.org) | Public API | Researcher identifiers, Scopus ID lookup |
| [Scopus](https://www.scopus.com) | Via ORCID external IDs | Author profile links |

## Data Quality Rules

- S2 h-index must not exceed Google Scholar h-index for any author
- Name fields are cleaned of titles (Dr., Prof.) and post-comma suffixes (PhD, etc.)
- S2 matches are validated by paper title overlap when the h-index gap exceeds 2
- Wrong-person matches (name mismatch + large h-index discrepancy) are cleared
