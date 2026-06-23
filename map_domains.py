import pandas as pd
import json

df = pd.read_csv("merged_with_s2_metrics_fixed2.csv")

# Known mapping of domains to clean university names
university_names = {
    "puthisastra.edu.kh": "University of Puthisastra",
    "nubb.edu.kh": "National University of Battambang",
    "npic.edu.kh": "National Polytechnic Institute of Cambodia",
    "camtech.edu.kh": "Cambodia University of Technology and Science",
    "nie.edu.kh": "National Institute of Education",
    "cadt.edu.kh": "Cambodia Academy of Digital Technology",
    "dmu.ac.uk": "De Montfort University",
    "itc.edu.kh": "Institute of Technology of Cambodia",
    "paragoniu.edu.kh": "Paragon International University",
    "rua.edu.kh": "Royal University of Agriculture",
    "rupp.edu.kh": "Royal University of Phnom Penh",
    "aupp.edu.kh": "American University of Phnom Penh",
    "sru.edu.kh": "Svay Rieng University",
    "uc.edu.kh": "The University of Cambodia",
    "uhs.edu.kh": "University of Health Sciences",
    "puc.edu.kh": "Paññāsāstra University of Cambodia",
    "num.edu.kh": "National University of Management",
    "nmu.edu.kh": "Naratteipa Medical University", # wait, let's check what nmu is
    "bbu.edu.kh": "Build Bright University",
    "westernuniversity.edu.kh": "Western University Cambodia",
    "ppua.edu.kh": "Phnom Penh International University", # wait, let's verify
    "mekong.edu.kh": "Cambodian Mekong University",
    "ume.edu.kh": "University of Management and Economics",
    "usea.edu.kh": "University of South-East Asia",
    "aub.edu.kh": "Asia Euro University", # wait, let's verify
    "rule.edu.kh": "Royal University of Law and Economics",
    "rac.gov.kh": "Royal Academy of Cambodia",
    "psbu.edu.kh": "Preah Sihanouk Raja Buddhist University",
    "ntti.edu.kh": "National Technical Training Institute",
    "diu.edu.kh": "Dewey International University",
    "angkor.edu.kh": "Angkor University",
    "eamu.edu.kh": "East Asia Management University",
    "aib.edu.kh": "Asian Institute of Cambodia" # wait, let's verify or map these
}

# Let's inspect the actual unique domains and write them out to a JSON file
mapping = {}
for domain, group in df.groupby("domain"):
    affiliations = group["affiliation"].dropna()
    most_common = affiliations.mode().iloc[0] if len(affiliations) > 0 else domain
    count = len(group)
    mapping[domain] = {
        "most_common_affiliation": most_common,
        "author_count": count,
        "mapped_name": university_names.get(domain, most_common)
    }

with open("domain_mapping.json", "w", encoding="utf-8") as f:
    json.dump(mapping, f, indent=4, ensure_ascii=False)

print("Mapping written to domain_mapping.json")
