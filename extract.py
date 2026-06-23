from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
import pandas as pd
import time
import random
import csv


INPUT_FILE = "scholar_authors.csv"
OUTPUT_FILE = "authors_full.csv"


def delay(a=3, b=8):
    time.sleep(random.uniform(a, b))


def parse_profile(html, url):

    soup = BeautifulSoup(html, "html.parser")

    # Name
    name = ""
    try:
        name = soup.select_one("#gsc_prf_in").get_text(strip=True)
    except:
        pass

    # Affiliation + email
    affiliation = ""
    email = ""

    try:
        lines = soup.select(".gsc_prf_il")
        if len(lines) > 0:
            affiliation = lines[0].get_text(" ", strip=True)
        if len(lines) > 1:
            email = lines[1].get_text(" ", strip=True)
    except:
        pass

    # Metrics table
    citations = ""
    hindex = ""
    i10 = ""

    try:
        rows = soup.select("#gsc_rsb_st tbody tr")

        if len(rows) >= 3:
            citations = rows[0].select("td")[1].get_text(strip=True)
            hindex = rows[1].select("td")[1].get_text(strip=True)
            i10 = rows[2].select("td")[1].get_text(strip=True)

    except:
        pass

    return {
        "name": name,
        "affiliation": affiliation,
        "email": email,
        "citations": citations,
        "hindex": hindex,
        "i10index": i10,
        "profile": url
    }


def save_csv(rows):

    fields = [
        "name",
        "affiliation",
        "email",
        "citations",
        "hindex",
        "i10index",
        "profile"
    ]

    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main():

    df = pd.read_csv(INPUT_FILE)

    results = []

    with sync_playwright() as p:

        browser = p.chromium.launch_persistent_context(
            user_data_dir="./scholar_profile",
            headless=False,
            viewport={"width": 1400, "height": 900},
            args=["--disable-blink-features=AutomationControlled"]
        )

        page = browser.new_page()

        page.goto("https://scholar.google.com")

        input("Login / solve CAPTCHA if needed, then press ENTER...")

        for idx, row in df.iterrows():

            url = row.get("profile_url")

            if not url:
                continue

            print(f"[{idx}] Visiting {url}")

            try:

                page.goto(url, wait_until="domcontentloaded")
                delay(3, 8)

                html = page.content()

                data = parse_profile(html, url)

                results.append(data)

                print(
                    data["name"],
                    "| H-index:",
                    data["hindex"]
                )

            except Exception as e:
                print("Error:", e)

            delay(5, 12)

        browser.close()

    save_csv(results)

    print("\nDone:", len(results), "profiles saved")


if __name__ == "__main__":
    main()