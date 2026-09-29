"""
discover_from_pinterest.py (V4 Creators API Edition)
────────────────────────────────────────────────────
The Amazon Creators API Affiliate Pipeline.

1.  DEMAND: Hits Pinterest Autocomplete (UK + US) to find what's trending.
2.  DISCOVERY: Searches Amazon through the Creators API for those trends.
3.  EXTRACTION: Reads titles, prices, images, and sales rank from API results.
4.  STORAGE: Saves to Supabase with full SEO metadata.
"""

import os, time, requests
from datetime import datetime, timezone
from amazon_creatorsapi import AmazonCreatorsApi
from amazon_creatorsapi.errors import ItemsNotFoundError
from amazon_creatorsapi.models import SearchItemsResource
from config import log, get_commission, supabase_get, supabase_post

# ── CONFIGURATION ───────────────────────────────────────────

AMAZON_TAG = os.environ.get("AMAZON_ASSOCIATE_TAG", "pinnpurchas0f-21") # Fallback if not set
MIN_PRICE  = 15.0

# Keyword noise filters — things that waste a request because they were
# never going to match real UK Amazon listings.
REJECT_KEYWORD_MARKERS = [
    "myntra", "meesho", "flipkart", "ajio",          # non-Amazon retailers
    " rs", "rs ", "₹", "inr", "rupees",               # Indian pricing/region
    "under 500", "under 1000", "under 2000",          # almost always ₹ price bands
    "prime video", "amazon prime", "tv show", "movie", # Prime Video drift
]

NICHES = [
    # Clothing - specific sub-niches that match real Pinterest searches
    {"name": "summer dresses women",       "category": "clothing",  "audience": "women looking for casual and going-out dresses"},
    {"name": "women's co-ord sets",        "category": "clothing",  "audience": "women who love matching outfit sets and effortless style"},
    {"name": "women's going out tops",     "category": "clothing",  "audience": "women looking for date night and night out outfits"},

    # Shoes - product-level specificity
    {"name": "white sneakers women",       "category": "shoes",     "audience": "women looking for clean everyday trainers"},
    {"name": "heeled boots women",         "category": "shoes",     "audience": "women who love ankle boots and heeled boots"},

    # Jewellery - trending styles
    {"name": "gold layered necklaces",     "category": "jewellery", "audience": "women who love dainty gold jewellery and layering"},
    {"name": "sterling silver rings women","category": "jewellery", "audience": "women looking for minimalist and stackable rings"},

    # Accessories - high-intent items
    {"name": "crossbody bags women",       "category": "fashion",   "audience": "women looking for everyday and going-out bags"},
]

# ── Seasonal niches (auto-rotate by month) ───────────────────
# Pinterest users search 2-4 weeks ahead of the season, so we
# target the CURRENT season plus early-next-season queries.

SEASONAL_NICHES = {
    # Spring (March-May): Easter, spring refresh, transitional fashion
    3: [
        {"name": "spring outfits women",       "category": "clothing",  "audience": "women refreshing their wardrobe for spring"},
        {"name": "easter dress women",         "category": "clothing",  "audience": "women looking for Easter Sunday outfits"},
        {"name": "pastel jewellery",           "category": "jewellery", "audience": "women who love pastel and spring-toned accessories"},
    ],
    4: [
        {"name": "spring wedding guest dress", "category": "clothing",  "audience": "women attending spring weddings"},
        {"name": "lightweight jackets women",  "category": "clothing",  "audience": "women looking for transitional spring layers"},
        {"name": "floral dresses women",       "category": "clothing",  "audience": "women who love floral prints for spring"},
    ],
    5: [
        {"name": "summer wedding guest outfit","category": "clothing",  "audience": "women attending summer weddings"},
        {"name": "holiday outfits women",      "category": "clothing",  "audience": "women packing for summer holidays"},
        {"name": "straw bags women",           "category": "fashion",   "audience": "women looking for summer and beach bags"},
    ],
    
    # Summer (June-August): Holidays, festivals, beach, weddings
    6: [
        {"name": "holiday dresses women",      "category": "clothing",  "audience": "women shopping for holiday and vacation dresses"},
        {"name": "festival outfits women",     "category": "clothing",  "audience": "women looking for festival and outdoor event fashion"},
        {"name": "beach accessories women",    "category": "fashion",   "audience": "women looking for beach bags, hats, and sandals"},
        {"name": "summer sandals women",       "category": "shoes",     "audience": "women looking for stylish summer sandals"},
    ],
    7: [
        {"name": "vacation outfits women",     "category": "clothing",  "audience": "women packing stylish holiday wardrobes"},
        {"name": "summer wedding guest dress", "category": "clothing",  "audience": "women attending summer weddings"},
        {"name": "linen clothing women",       "category": "clothing",  "audience": "women looking for breathable summer fabrics"},
        {"name": "statement earrings summer",  "category": "jewellery", "audience": "women who love bold summer jewellery"},
    ],
    8: [
        {"name": "back to work outfits women", "category": "clothing",  "audience": "women refreshing their work wardrobe after summer"},
        {"name": "transitional outfits autumn","category": "clothing",  "audience": "women planning autumn wardrobe staples"},
        {"name": "ankle boots women",          "category": "shoes",     "audience": "women looking for early autumn boots"},
    ],
    
    # Autumn (September-November): Back to school, layering, Halloween, cosy
    9: [
        {"name": "autumn outfits women",       "category": "clothing",  "audience": "women looking for cosy autumn fashion"},
        {"name": "knit jumpers women",         "category": "clothing",  "audience": "women looking for stylish knitwear"},
        {"name": "chunky gold jewellery",      "category": "jewellery", "audience": "women who love bold autumn accessories"},
    ],
    10: [
        {"name": "winter coat women",          "category": "clothing",  "audience": "women shopping for winter coats early"},
        {"name": "knee high boots women",      "category": "shoes",     "audience": "women looking for statement autumn boots"},
        {"name": "cosy loungewear women",      "category": "clothing",  "audience": "women looking for comfortable stay-at-home outfits"},
    ],
    11: [
        {"name": "christmas party dress",      "category": "clothing",  "audience": "women looking for festive party outfits"},
        {"name": "gift ideas for her",         "category": "jewellery", "audience": "people shopping for women's Christmas gifts"},
        {"name": "winter accessories women",   "category": "fashion",   "audience": "women looking for scarves, gloves, and hats"},
    ],
    
    # Winter (December-February): Christmas, NYE, January sales, Valentine's
    12: [
        {"name": "new year's eve outfit women","category": "clothing",  "audience": "women looking for NYE party outfits"},
        {"name": "christmas jumper women",     "category": "clothing",  "audience": "women looking for festive Christmas knitwear"},
        {"name": "sparkly jewellery women",    "category": "jewellery", "audience": "women looking for statement party jewellery"},
    ],
    1: [
        {"name": "january sales fashion women","category": "clothing",  "audience": "women looking for fashion deals in the January sales"},
        {"name": "capsule wardrobe women",     "category": "clothing",  "audience": "women planning a minimal wardrobe refresh"},
        {"name": "dainty necklaces women",     "category": "jewellery", "audience": "women who love minimalist everyday jewellery"},
    ],
    2: [
        {"name": "valentine's day outfit",     "category": "clothing",  "audience": "women looking for date night outfits"},
        {"name": "red dress women",            "category": "clothing",  "audience": "women looking for red and romantic dresses"},
        {"name": "heart jewellery women",      "category": "jewellery", "audience": "women looking for Valentine's Day gifts and accessories"},
    ],
}


def get_active_niches():
    """Combine evergreen niches with seasonal ones for the current month."""
    current_month = datetime.now().month
    seasonal = SEASONAL_NICHES.get(current_month, [])
    
    all_niches = NICHES + seasonal
    log.info(f"Active niches: {len(NICHES)} evergreen + {len(seasonal)} seasonal = {len(all_niches)} total")
    return all_niches

# ── 1. PINTEREST DEMAND (UK + US) ──────────────────────────

def get_pinterest_trends(query, region="GB"):
    """
    Uses Google's Autocomplete API to find popular Pinterest trends.
    This is much more stable than hitting Pinterest directly.
    """
    log.info(f"  Fetching Pinterest trends via Google for: '{query}'")
    url = "https://suggestqueries.google.com/complete/search"
    params = {
        "client": "firefox",
        "q": query,
        "hl": "en-GB" if region == "GB" else "en-US",
        "gl": region.lower()
    }

    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        # Google returns [query, [suggestions], ...]
        if len(data) > 1:
            return data[1]
        return []
    except Exception as e:
        log.warning(f"    Trend research failed for '{query}': {e}")
        return []

def is_relevant_keyword(keyword):
    """Filter out keywords that are unlikely to return usable UK results."""
    keyword = keyword.lower()
    return not any(marker in keyword for marker in REJECT_KEYWORD_MARKERS)


def get_demand_keywords(niche):
    log.info(f"Researching global Pinterest demand for: {niche['name']}")

    seeds = [
        f"{niche['name']} outfit ideas",
        f"best {niche['name']} amazon",
        f"{niche['name']} trending {datetime.now().year}",
        f"{niche['name']} under £50",
        f"{niche['name']} pinterest",
    ]

    all_suggestions = []
    for region in ["GB", "US"]:
        for seed in seeds:
            suggestions = get_pinterest_trends(seed, region)
            all_suggestions.extend(suggestions)
            time.sleep(0.3)

    unique = list(dict.fromkeys(all_suggestions))
    clean = []
    for keyword in unique:
        cleaned_keyword = keyword.lower().replace("pinterest", "").replace("ideas", "").replace("  ", " ").strip()
        if len(cleaned_keyword) > 5 and is_relevant_keyword(cleaned_keyword):
            clean.append(cleaned_keyword)

    rejected = len(unique) - len(clean)
    clean = list(dict.fromkeys(clean))[:8]

    log.info(f"  Found {len(clean)} trending keywords ({rejected} filtered as noise): {clean}")
    return clean


# ── 2. AMAZON CREATORS API ───────────────────────────────────

AMAZON_SEARCH_RESOURCES = [
    SearchItemsResource.ITEM_INFO_DOT_TITLE,
    SearchItemsResource.OFFERS_V2_DOT_LISTINGS_DOT_PRICE,
    SearchItemsResource.IMAGES_DOT_PRIMARY_DOT_HIGH_RES,
    SearchItemsResource.IMAGES_DOT_PRIMARY_DOT_LARGE,
    SearchItemsResource.BROWSE_NODE_INFO_DOT_BROWSE_NODES_DOT_SALES_RANK,
]


def make_amazon_client():
    credential_id = os.environ.get("AMAZON_CREDENTIAL_ID", "").strip()
    credential_secret = os.environ.get("AMAZON_CREDENTIAL_SECRET", "").strip()
    if not credential_id or not credential_secret:
        raise RuntimeError("AMAZON_CREDENTIAL_ID and AMAZON_CREDENTIAL_SECRET are required")

    return AmazonCreatorsApi(
        credential_id=credential_id,
        credential_secret=credential_secret,
        version=os.environ.get("AMAZON_CREATORS_API_VERSION", "2.2"),
        tag=AMAZON_TAG,
        country="UK",
        throttling=1,
    )


def normalize_amazon_item(item):
    """Convert one Creators API item into the fields stored in Supabase."""
    title = None
    if item.item_info and item.item_info.title:
        title = item.item_info.title.display_value

    price = 0.0
    listings = item.offers_v2.listings if item.offers_v2 else []
    for listing in listings or []:
        money = listing.price.money if listing.price else None
        if money and money.amount is not None:
            if money.currency not in (None, "GBP"):
                continue
            price = float(money.amount)
            break

    image_url = None
    if item.images and item.images.primary:
        image = item.images.primary.hi_res or item.images.primary.large
        if image:
            image_url = image.url

    bsr = None
    browse_node_info = item.browse_node_info
    if browse_node_info and browse_node_info.browse_nodes:
        bsr = next(
            (node.sales_rank for node in browse_node_info.browse_nodes if node.sales_rank is not None),
            None,
        )

    return {
        "asin": item.asin,
        "name": title[:150] if title else None,
        "price": price,
        "image_url": image_url,
        "bsr": bsr,
    }


def search_amazon_products(keyword, client):
    try:
        result = client.search_items(
            keywords=keyword,
            item_count=4,
            currency_of_preference="GBP",
            languages_of_preference=["en_GB"],
            resources=AMAZON_SEARCH_RESOURCES,
        )
    except ItemsNotFoundError:
        log.info(f"    Creators API returned no products for: '{keyword}'")
        return []

    products = [normalize_amazon_item(item) for item in (result.items or []) if item.asin]
    log.info(f"    Creators API returned {len(products)} product(s) for: '{keyword}'")
    return products


# ── MAIN PIPELINE ────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print(f"V4 Pinterest-First (Creators API) — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 60)

    # 1. Load existing ASINs to avoid duplicates
    existing_asins = {p["asin"] for p in supabase_get("products", params={"select": "asin"})}
    total_added = 0
    total_found = 0       # every ASIN Amazon search returned, across all keywords
    total_duplicate = 0   # of those, how many were already in the database

    amazon_client = make_amazon_client()

    for niche in get_active_niches():
        print(f"\nNICHE: {niche['name'].upper()}")

        # Step 1: Find Demand
        keywords = get_demand_keywords(niche)

        for kw in keywords:
            print(f"  Trend: '{kw}'")

            # Step 2: Search Amazon through the Creators API.
            products = search_amazon_products(kw, amazon_client)
            new_products = [product for product in products if product["asin"] not in existing_asins]
            total_found += len(products)
            total_duplicate += len(products) - len(new_products)

            if products and not new_products:
                # This used to be indistinguishable from "found nothing" in
                # the log — it's actually "found products, all already known".
                print(f"    Found {len(products)} product(s) on Amazon, but all "
                      f"{len(products)} already exist in the database — nothing new here.")

            for p_data in new_products:
                asin = p_data["asin"]
                if p_data["name"] and p_data["price"] >= MIN_PRICE and p_data["image_url"]:
                    # Build final product object
                    product = {
                        "asin":          asin,
                        "name":          p_data["name"],
                        "category":      niche["category"],
                        "niche":         niche["name"],
                        "audience":      niche["audience"],
                        "commission":    get_commission(niche["category"]),
                        "price":         p_data["price"],
                        "image_url":     p_data["image_url"],
                        "affiliate_url": f"https://www.amazon.co.uk/dp/{asin}?tag={AMAZON_TAG}&linkCode=ll2",
                        "pinterest_keywords": [kw],
                        "active":        True,
                        "bsr_rank":      p_data.get("bsr"),
                        "keywords_last_updated_at": datetime.now(timezone.utc).isoformat()
                    }

                    try:
                        supabase_post("products", product)
                        print(f"✓ ADDED (£{product['price']})")
                        existing_asins.add(asin)
                        total_added += 1
                    except Exception as e:
                        print(f"✗ DB ERROR: {e}")
                        # Mark ASIN as 'existing' anyway so we don't spam errors for the same product
                        existing_asins.add(asin)
                else:
                    print("SKIPPED (Missing data or < £15)")

    print("\n" + "=" * 60)
    print(f"Done. Amazon returned {total_found} product(s) across all keywords "
          f"({total_duplicate} already known, {total_found - total_duplicate} new attempts).")
    print(f"Added {total_added} new products to the database.")
    if total_found and not total_added:
        print("Zero additions despite non-zero search results usually means: "
              "everything found was a duplicate, or the API results were missing "
              "a title, an image, or a price above the minimum.")
    if not total_found:
        raise RuntimeError(
            "Amazon Creators API returned zero products across all search terms; "
            "verify API access, credentials, API version, and query eligibility."
        )
    print("=" * 60)