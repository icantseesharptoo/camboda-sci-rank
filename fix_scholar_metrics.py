import pandas as pd
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
import time
import random

INPUT_FILE = "merged_with_s2_metrics_fixed.csv"
OUTPUT_FILE = "merged_with_s2_metrics_fixed2.csv"


def to_int(value):
    try:
        if pd.isna(value):
            return 0
        return int(float(value))
    except:
        return 0


def random_delay():
    time.sleep(random.uniform(3, 8))


def extract_scholar_metrics(page, url):

    try:
        page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=60000
        )

        random_delay()

        soup = BeautifulSoup(
            page.content(),
            "html.parser"
        )

        stats = soup.select("#gsc_rsb_st tbody tr")

        if len(stats) < 3:
            return None

        citations = 0
        hindex = 0
        i10 = 0

        try:
            citations = int(
                stats[0].select("td")[1].get_text(strip=True)
            )
        except:
            pass

        try:
            hindex = int(
                stats[1].select("td")[1].get_text(strip=True)
            )
        except:
            pass

        try:
            i10 = int(
                stats[2].select("td")[1].get_text(strip=True)
            )
        except:
            pass

        return {
            "citations": citations,
            "hindex": hindex,
            "i10": i10
        }

    except Exception as e:
        print("Error:", e)
        return None


def main():

    df = pd.read_csv(INPUT_FILE)

    with sync_playwright() as p:

        browser = p.chromium.launch_persistent_context(
            user_data_dir="./scholar_profile",
            headless=False,
            viewport={
                "width": 1400,
                "height": 900
            },
            args=[
                "--disable-blink-features=AutomationControlled"
            ]
        )

        page = browser.new_page()

        page.goto("https://scholar.google.com")

        input(
            "Solve CAPTCHA/login if needed, then press ENTER..."
        )

        total_updates = 0

        for idx, row in df.iterrows():

            citations = to_int(
                row.get("citations_scholar", 0)
            )

            hindex = to_int(
                row.get("hindex_scholar", 0)
            )

            if citations > 0 and hindex > 0:
                continue

            url = row.get("profile_scholar")

            if pd.isna(url) or not str(url).startswith("http"):
                continue

            print(
                f"[{idx}] {row.get('name')} "
                f"(cit={citations}, h={hindex})"
            )

            metrics = extract_scholar_metrics(
                page,
                url
            )

            if not metrics:
                continue

            print(
                f"  Scholar: "
                f"C={metrics['citations']} "
                f"H={metrics['hindex']} "
                f"I10={metrics['i10']}"
            )

            updated = False

            if citations == 0 and metrics["citations"] > 0:
                df.at[idx, "citations_scholar"] = metrics["citations"]
                updated = True

            if hindex == 0 and metrics["hindex"] > 0:
                df.at[idx, "hindex_scholar"] = metrics["hindex"]
                updated = True

            current_i10 = to_int(
                row.get("i10_scholar", 0)
            )

            if current_i10 == 0 and metrics["i10"] > 0:
                df.at[idx, "i10_scholar"] = metrics["i10"]
                updated = True

            if updated:
                total_updates += 1

                if total_updates % 20 == 0:
                    df.to_csv(
                        OUTPUT_FILE,
                        index=False,
                        encoding="utf-8"
                    )

            random_delay()

        browser.close()

    df.to_csv(
        OUTPUT_FILE,
        index=False,
        encoding="utf-8"
    )

    print(
        f"\nDone. Updated {total_updates} profiles."
    )
    print(
        f"Saved to {OUTPUT_FILE}"
    )


if __name__ == "__main__":
    main()