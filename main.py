import csv
import random
import time
from urllib.parse import quote

from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

DOMAINS = [
    "nie.edu.kh",
    "npic.edu.kh",
    "nubb.edu.kh",
    "sru.edu.kh",
    "nuck.edu.kh",
    "nmu.edu.kh",
    "nia.edu.kh",
    "rac.gov.kh",
    "nib.edu.kh",
    "era.gov.kh",
    "psbu.edu.kh",
    "ntti.edu.kh",
    "cardi.org.kh",
    "ppiedu.com",
    "camtech.edu.kh",
    "ciedi.edu.kh",
    "bbu.edu.kh",
    "iic.edu.kh",
    "puthisastra.edu.kh",
    "iu.edu.kh",
    "beltei.edu.kh",
    "aeu.edu.kh",
    "ppiu.edu.kh",
    "cus.edu.kh",
    "mekong.edu.kh",
    "ume.edu.kh",
    "diu.edu.kh",
    "usea.edu.kh",
    "westernuniversity.edu.kh",
    "hru.edu.kh",
    "vanda.edu.kh",
    "angkor.edu.kh",
    "pcu.edu.kh",
    "lifeun.edu.kh",
    "khemarakuniversity.edu.kh",
    "clu-edu.com",
    "cumt.edu.kh",
    "akuks.com",
    "cityuniversity.education",
    "cup-university.com",
    "eamu.edu.kh",
    "spi.edu.kh",
    "ppua.edu.kh",
    "aub.edu.kh",
    "uef.edu.kh",
    "dmuc.edu.kh",
    "efi.mef.gov.kh"
]

OUTPUT = "google_scholar_hindex_new.csv"


def human_sleep(a=5, b=15):
    time.sleep(random.uniform(a, b))


def parse_author_profile(page, url):
    page.goto(url, wait_until="domcontentloaded")

    human_sleep(3, 8)

    soup = BeautifulSoup(page.content(), "html.parser")

    try:
        name = soup.select_one("#gsc_prf_in").get_text(strip=True)
    except:
        name = ""

    try:
        affiliation = soup.select_one(".gsc_prf_il").get_text(strip=True)
    except:
        affiliation = ""

    try:
        email = soup.select(".gsc_prf_il")[1].get_text(strip=True)
    except:
        email = ""

    citations = ""
    hindex = ""
    i10 = ""

    try:
        rows = soup.select("#gsc_rsb_st tbody tr")

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


def search_domain(page, domain):
    query = f'site:scholar.google.com "{domain}"'

    url = (
        "https://scholar.google.com/"
        f"citations?view_op=search_authors&mauthors={quote(domain)}"
    )

    page.goto(url, wait_until="domcontentloaded")

    human_sleep(5, 10)

    authors = []

    while True:

        soup = BeautifulSoup(page.content(), "html.parser")

        cards = soup.select(".gsc_1usr")

        for card in cards:

            profile_link = card.select_one("h3 a")

            if not profile_link:
                continue

            profile_url = (
                "https://scholar.google.com"
                + profile_link["href"]
            )

            try:
                author = parse_author_profile(page, profile_url)
                authors.append(author)

                print(
                    domain,
                    author["name"],
                    author["hindex"]
                )

            except Exception as e:
                print("Error:", e)

            human_sleep(5, 15)

        next_btn = page.locator("#gsc_authors_bottom_pag button")

        if next_btn.count() == 0:
            break

        try:
            disabled = next_btn.get_attribute("disabled")
            if disabled is not None:
                break

            next_btn.click()
            human_sleep(10, 20)

        except:
            break

    return authors


def save_results(rows):

    with open(
        OUTPUT,
        "w",
        newline="",
        encoding="utf-8"
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=[
                "domain",
                "name",
                "affiliation",
                "email",
                "citations",
                "hindex",
                "i10index",
                "profile"
            ]
        )

        writer.writeheader()

        for row in rows:
            writer.writerow(row)


def main():

    all_rows = []

    with sync_playwright() as p:

        context = p.chromium.launch_persistent_context(
            user_data_dir="./scholar_profile",
            headless=False,
            viewport={"width": 1400, "height": 900},
            args=[
                "--disable-blink-features=AutomationControlled"
            ]
        )

        page = context.new_page()

        page.goto("https://scholar.google.com")

        print(
            "Login manually if needed."
        )

        input(
            "Press ENTER after Scholar is accessible..."
        )

        for domain in DOMAINS:

            print(f"\nProcessing {domain}")

            try:

                authors = search_domain(
                    page,
                    domain
                )

                for a in authors:
                    a["domain"] = domain

                all_rows.extend(authors)

                save_results(all_rows)

                human_sleep(20, 60)

            except Exception as e:
                print(
                    f"Failed {domain}:",
                    e
                )

        context.close()

    save_results(all_rows)


if __name__ == "__main__":
    main()