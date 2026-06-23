from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
import csv
import random
import time

DOMAINS = [
    # "rupp.edu.kh",
    # "itc.edu.kh",
    # "paragoniu.edu.kh",
    # "rua.edu.kh",
    # "cadt.edu.kh",
    # "aupp.edu.kh",
    "nie.edu.kh",
    "npic.edu.kh",
    "nubb.edu.kh",
    "camtech.edu.kh",
    "puthisastra.edu.kh"
]

OUTPUT_FILE = "scholar_authors.csv"


def random_delay(min_sec=5, max_sec=15):
    time.sleep(random.uniform(min_sec, max_sec))


def parse_current_page(page, domain, seen_ids):
    """
    Extract authors from current Google Scholar search page.
    """

    soup = BeautifulSoup(page.content(), "html.parser")

    cards = soup.select(".gsc_1usr")

    authors = []
    new_records = 0

    for card in cards:

        profile_link = card.select_one("h3 a")

        if not profile_link:
            continue

        href = profile_link.get("href", "")

        if "user=" not in href:
            continue

        scholar_id = href.split("user=")[1].split("&")[0]

        if scholar_id in seen_ids:
            continue

        seen_ids.add(scholar_id)

        name = profile_link.get_text(strip=True)

        affiliation = ""

        aff = card.select_one(".gsc_1usr_aff")
        if aff:
            affiliation = aff.get_text(" ", strip=True)

        email = ""

        email_el = card.select_one(".gsc_1usr_eml")
        if email_el:
            email = email_el.get_text(" ", strip=True)

        interests = []

        for area in card.select(".gsc_1usr_int a"):
            interests.append(area.get_text(strip=True))

        authors.append({
            "domain": domain,
            "scholar_id": scholar_id,
            "name": name,
            "affiliation": affiliation,
            "email": email,
            "interests": "; ".join(interests),
            "profile_url": "https://scholar.google.com" + href
        })

        new_records += 1

    print(
        f"Found {len(cards)} cards "
        f"({new_records} new authors)"
    )

    return authors


def save_csv(records):

    fields = [
        "domain",
        "scholar_id",
        "name",
        "affiliation",
        "email",
        "interests",
        "profile_url"
    ]

    with open(
        OUTPUT_FILE,
        "w",
        newline="",
        encoding="utf-8"
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=fields
        )

        writer.writeheader()
        writer.writerows(records)


def extract_authors(page, domain):

    all_authors = []
    seen_ids = set()

    search_url = (
        "https://scholar.google.com/citations"
        "?view_op=search_authors"
        f"&mauthors={domain}"
        "&hl=en"
    )

    print(f"\nSearching: {domain}")

    page.goto(
        search_url,
        wait_until="networkidle",
        timeout=60000
    )

    page_number = 1

    while True:

        print(
            f"Page {page_number}"
        )

        page.wait_for_selector(
            ".gsc_1usr",
            timeout=30000
        )

        random_delay(3, 8)

        print("Current URL:")
        print(page.url)

        authors = parse_current_page(
            page,
            domain,
            seen_ids
        )

        all_authors.extend(authors)

        # Find Next button
        next_button = page.locator(
            "#gsc_authors_bottom_pag button.gs_btnPR"
        )

        if next_button.count() == 0:
            print("No next button found")
            break

        try:

            if next_button.is_disabled():
                print("Last page reached")
                break

            print("Going to next page...")

            next_button.click()

            page.wait_for_load_state(
                "networkidle"
            )

            random_delay(5, 12)

            page_number += 1

        except Exception as e:

            print(
                f"Pagination stopped: {e}"
            )
            break

    return all_authors


def main():

    all_records = []

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

        page.goto(
            "https://scholar.google.com"
        )

        input(
            "\nSolve CAPTCHA if needed, "
            "then press ENTER..."
        )

        for domain in DOMAINS:

            try:

                authors = extract_authors(
                    page,
                    domain
                )

                all_records.extend(
                    authors
                )

                save_csv(
                    all_records
                )

                print(
                    f"{domain}: "
                    f"{len(authors)} authors"
                )

                random_delay(
                    20,
                    60
                )

            except Exception as e:

                print(
                    f"Error with {domain}: {e}"
                )

        browser.close()

    print(
        f"\nTotal authors collected: "
        f"{len(all_records)}"
    )


if __name__ == "__main__":
    main()
