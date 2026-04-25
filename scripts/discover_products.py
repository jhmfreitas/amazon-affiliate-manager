import os, re, time, requests
from bs4 import BeautifulSoup

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept-Language": "en-GB,en;q=0.9"
}

SUPABASE_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation"
}

# ── TARGET CATEGORIES ──────────────────────────────
CATEGORIES = {
    "fashion_trunks": "https://www.amazon.co.uk/gp/bestsellers/fashion/1731039031",
    "home_kitchen":   "https://www.amazon.co.uk/gp/bestsellers/kitchen",
    "beauty":         "https://www.amazon.co.uk/gp/bestsellers/beauty",
    "sports":         "https://www.amazon.co.uk/gp/bestsellers/sports",
}

# ── FETCH EXISTING ASINS ───────────────────────────
def get_existing_asins():
    resp = requests.get(
        f"{SUPABASE_URL}/rest/v1/products",
        headers=SUPABASE_HEADERS,
        params={"select": "asin"}
    )
    return {p["asin"] for p in resp.json()}

# ── SCRAPE BESTSELLER PAGE ─────────────────────────
def scrape_category(url):
    print(f"\nScraping: {url}")

    resp = requests.get(url, headers=HEADERS, timeout=15)
    soup = BeautifulSoup(resp.text, "html.parser")

    items = soup.select("div.zg-grid-general-faceout")

    products = []

    for item in items:
        try:
            link = item.find("a", href=True)["href"]
            asin_match = re.search(r"/dp/([A-Z0-9]{10})", link)

            if not asin_match:
                continue

            asin = asin_match.group(1)

            title_tag = item.select_one("div._cDEzb_p13n-sc-css-line-clamp-3_g3dy1")
            name = title_tag.text.strip() if title_tag else "Unknown"

            products.append({
                "asin": asin,
                "name": name
            })

        except Exception as e:
            continue

    print(f"  Found {len(products)} products")
    return products

# ── INSERT INTO SUPABASE ───────────────────────────
def insert_products(products, category):
    inserted = 0

    for p in products:
        payload = {
            "asin": p["asin"],
            "name": p["name"],
            "category": category,
            "active": True
        }

        resp = requests.post(
            f"{SUPABASE_URL}/rest/v1/products",
            headers=SUPABASE_HEADERS,
            json=payload
        )

        if resp.status_code in (200, 201):
            inserted += 1

        time.sleep(0.2)

    return inserted

# ── MAIN ───────────────────────────────────────────
if __name__ == "__main__":
    print("=== PRODUCT DISCOVERY ===")

    existing = get_existing_asins()

    total_new = 0

    for category, url in CATEGORIES.items():
        scraped = scrape_category(url)

        # Filter new ASINs
        new_products = [p for p in scraped if p["asin"] not in existing]

        print(f"  New products: {len(new_products)}")

        inserted = insert_products(new_products, category)

        print(f"  Inserted: {inserted}")

        total_new += inserted

        time.sleep(2)

    print(f"\n=== DONE — {total_new} new products added ===")