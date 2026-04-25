"""
score_products.py
─────────────────
Runs weekly (Monday 7am UTC) via GitHub Actions.

For each active product:
  1. Fetches Amazon BSR via scraping (no API needed)
  2. Fetches Google Trends interest score via pytrends
  3. Reads Pinterest save counts from your own pin analytics
  4. Asks Gemini to score each product 1-100
  5. Saves scores back to Supabase

Top-scored product is then picked by generate_pins.py.
"""

import os, json, time, re, requests
from datetime import datetime, timezone
from pinterest_auth import PinterestAuth

# ── Secrets ──────────────────────────────────────────────────
GEMINI_KEY   = os.environ["GEMINI_API_KEY"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta"
    f"/models/gemini-2.5-flash-lite:generateContent?key={GEMINI_KEY}"
)

SUPABASE_HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "return=representation"
}

PINTEREST_API = "https://api.pinterest.com/v5"

# Amazon Associates UK commission rates by category
# Source: affiliate-program.amazon.co.uk/help/node/topic/GRXPHT8U84RAYDXZ
# Last updated: April 2026
COMMISSION_RATES = {
    # 6% — Fashion, Luxury, Accessories
    "amazon fashion":            6.0,
    "clothing":                  6.0,
    "clothing & accessories":    6.0,
    "fashion":                   6.0,
    "luxury":                    6.0,
    "luxury beauty":             6.0,
    "luxury stores beauty":      6.0,
    "luxury stores fashion":     6.0,
    "shoes":                     6.0,
    "handbags":                  6.0,
    "wallets":                   6.0,
    "watches":                   6.0,
 
    # 5% — Home, Kitchen, Books, Music, Automotive, Tools
    "amazon instant video":      5.0,
    "audible":                   5.0,
    "audiobooks":                5.0,
    "automotive":                5.0,
    "books":                     5.0,
    "digital music":             5.0,
    "furniture":                 5.0,
    "handmade":                  5.0,
    "home":                      5.0,
    "home office":               5.0,
    "home improvement":          5.0,
    "jewellery":                 5.0,
    "jewelry":                   5.0,
    "kindle books":              5.0,
    "kitchen":                   5.0,
    "kitchen & dining":          5.0,
    "music":                     5.0,
    "power tools":               5.0,
    "hand tools":                5.0,
    "tools":                     5.0,
 
    # 4% — Beauty, Sports, Luggage
    "beauty":                    4.0,
    "luggage":                   4.0,
    "personal care appliances":  4.0,
    "sports":                    4.0,
    "sports & fitness":          4.0,
    "fitness":                   4.0,
    "outdoors":                  4.0,
 
    # 2.5% — Electronics, Appliances
    "appliances":                2.5,
    "fire tv":                   2.5,
    "mobile electronics":        2.5,
    "electronics":               2.5,
    "headphones":                2.5,
    "pc":                        2.5,
    "computers":                 2.5,
 
    # 1% — Grocery, Gaming, Fresh
    "amazon fresh":              1.0,
    "grocery":                   1.0,
    "pantry":                    1.0,
    "video games":               1.0,
    "video game consoles":       1.0,
    "gaming":                    1.0,
 
    # 0% — Gift cards, wine, apps
    "gift cards":                0.0,
    "gift card":                 0.0,
    "kindle unlimited":          0.0,
    "wine":                      0.0,
    "android apps":              0.0,
    "coach":                     0.0,
 
    # Default for all other categories (e.g. toys, baby, pets, office)
    "default":                   3.0,
}


# ── 1. Load active products ──────────────────────────────────

def load_products():
    resp = requests.get(
        f"{SUPABASE_URL}/rest/v1/products",
        headers=SUPABASE_HEADERS,
        params={"active": "eq.true", "order": "id.asc"}
    )
    resp.raise_for_status()
    products = resp.json()
    print(f"Loaded {len(products)} active products.")
    return products


# ── 2. Amazon BSR scraper ────────────────────────────────────

def get_amazon_bsr(asin):
    url     = f"https://www.amazon.co.uk/dp/{asin}"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-GB,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    }
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code != 200:
            print(f"  BSR: Amazon returned {resp.status_code} for {asin}")
            return None
        match = re.search(r"#([\d,]+)\s+in\s+[\w\s]+(?:Best Sellers|Rank)", resp.text)
        if not match:
            match = re.search(r"Best Sellers Rank.*?#([\d,]+)", resp.text, re.DOTALL)
        if match:
            rank = int(match.group(1).replace(",", ""))
            print(f"  BSR: #{rank:,}")
            return rank
        print(f"  BSR: not found for {asin}")
        return None
    except Exception as e:
        print(f"  BSR error: {e}")
        return None


# ── 3. Google Trends ─────────────────────────────────────────

def get_trend_score(product_name):
    try:
        from pytrends.request import TrendReq
        pt      = TrendReq(hl="en-GB", tz=0, timeout=(10, 25))
        keyword = " ".join(product_name.split()[:3])
        pt.build_payload([keyword], timeframe="now 7-d", geo="GB")
        data    = pt.interest_over_time()

        if data.empty:
            print(f"  Trends: no data for '{keyword}'")
            return 50, "stable"

        values = data[keyword].tolist()
        avg    = sum(values) / len(values)
        mid    = len(values) // 2
        first  = sum(values[:mid]) / max(mid, 1)
        second = sum(values[mid:]) / max(len(values) - mid, 1)

        if second > first * 1.15:
            direction = "rising"
        elif second < first * 0.85:
            direction = "falling"
        else:
            direction = "stable"

        print(f"  Trends: score={avg:.1f} direction={direction}")
        return round(avg, 1), direction

    except Exception as e:
        print(f"  Trends error: {e}")
        return 50, "stable"


# ── 4. Pinterest pin saves for this product ──────────────────

def get_pinterest_saves(product_id, auth: PinterestAuth):
    """
    Sum saves across all posted pins for this product.
    Uses shared PinterestAuth — auto-refreshes token on 401.
    """
    try:
        resp = requests.get(
            f"{SUPABASE_URL}/rest/v1/pins",
            headers=SUPABASE_HEADERS,
            params={
                "product_id": f"eq.{product_id}",
                "posted":     "eq.true",
                "select":     "pinterest_id"
            }
        )
        resp.raise_for_status()
        pins = resp.json()

        if not pins:
            print(f"  Pinterest saves: no posted pins yet")
            return 0

        total_saves = 0
        for pin in pins:
            pid = pin.get("pinterest_id")
            if not pid:
                continue
            try:
                # Use auth.get() — handles 401 + token refresh automatically
                r = auth.get(
                    f"{PINTEREST_API}/pins/{pid}",
                    params={"pin_metrics": "true"}
                )
                if r.ok:
                    metrics     = r.json().get("pin_metrics", {})
                    saves       = metrics.get("lifetime_metrics", {}).get("save", 0)
                    total_saves += saves
                else:
                    print(f"  Could not fetch metrics for pin {pid}: {r.status_code}")
                time.sleep(0.3)
            except Exception as e:
                print(f"  Error fetching pin {pid}: {e}")

        print(f"  Pinterest saves: {total_saves} across {len(pins)} pins")
        return total_saves

    except Exception as e:
        print(f"  Pinterest saves error: {e}")
        return 0


# ── 5. Gemini scoring engine ─────────────────────────────────

def score_products_with_gemini(products_data):
    prompt = f"""You are an Amazon affiliate marketing analyst.
Score each product 0-100 for Pinterest promotion potential THIS WEEK.

Scoring weights:
- Amazon BSR rank      30% (lower = more popular = higher score)
- Google Trends score  25% (higher interest = higher score)
- Trend direction      15% (rising=+15, stable=+0, falling=-15)
- Pinterest saves      20% (more saves = better conversion proof)
- Commission rate      10% (higher % = higher score)

BSR scoring guide:
- Under 1,000    → 30 points
- 1,000-10,000   → 25 points
- 10,000-50,000  → 18 points
- 50,000-100,000 → 10 points
- Over 100,000   → 5 points
- Unknown        → 12 points (assume average)

Products to score:
{json.dumps(products_data, indent=2)}

Return ONLY valid JSON array, no markdown:
[
  {{
    "asin":   "...",
    "score":  87.5,
    "reason": "one sentence explaining the score"
  }}
]"""

    resp = requests.post(
        GEMINI_URL,
        json={"contents": [{"parts": [{"text": prompt}]}]}
    )
    resp.raise_for_status()
    text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    text = text.replace("```json", "").replace("```", "").strip()
    return json.loads(text)


# ── 6. Save scores to Supabase ───────────────────────────────

def save_score(product_id, score, reason, bsr, trend_score, trend_dir, saves):
    resp = requests.patch(
        f"{SUPABASE_URL}/rest/v1/products?id=eq.{product_id}",
        headers=SUPABASE_HEADERS,
        json={
            "score":           score,
            "score_reason":    reason,
            "bsr_rank":        bsr,
            "trend_score":     trend_score,
            "trend_dir":       trend_dir,
            "pinterest_saves": saves,
            "last_scored_at":  datetime.now(timezone.utc).isoformat()
        }
    )
    resp.raise_for_status()


# ── Main ─────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print(f"Product scoring — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 60)

    products = load_products()
    if not products:
        print("No active products. Add products to the table first.")
        exit(0)

    # Single shared auth instance — token refreshed once if needed,
    # reused across all Pinterest API calls in this run
    auth = PinterestAuth()

    products_data = []
    signals       = {}

    for p in products:
        print(f"\n── {p['name']} ({p['asin']}) ──")

        bsr                    = get_amazon_bsr(p["asin"])
        trend_score, trend_dir = get_trend_score(p["name"])
        saves                  = get_pinterest_saves(p["id"], auth)
        commission             = p.get("commission") or COMMISSION_RATES.get(
                                   p.get("category", "").lower(),
                                   COMMISSION_RATES["default"]
                                 )

        signals[p["asin"]] = {
            "id":          p["id"],
            "bsr":         bsr,
            "trend_score": trend_score,
            "trend_dir":   trend_dir,
            "saves":       saves,
            "commission":  commission
        }

        products_data.append({
            "asin":        p["asin"],
            "name":        p["name"],
            "category":    p["category"],
            "bsr_rank":    bsr,
            "trend_score": trend_score,
            "trend_dir":   trend_dir,
            "saves":       saves,
            "commission":  commission
        })

        time.sleep(2)

    # Score with Gemini
    print(f"\nScoring {len(products_data)} products with Gemini...")
    scores = score_products_with_gemini(products_data)

    # Save to Supabase
    print("\nSaving scores...")
    for s in scores:
        asin = s["asin"]
        sig  = signals.get(asin, {})
        save_score(
            product_id  = sig["id"],
            score       = s["score"],
            reason      = s["reason"],
            bsr         = sig.get("bsr"),
            trend_score = sig.get("trend_score", 0),
            trend_dir   = sig.get("trend_dir", "stable"),
            saves       = sig.get("saves", 0)
        )
        print(f"  {asin} → {s['score']}/100 — {s['reason']}")

    top         = max(scores, key=lambda x: x["score"])
    top_product = next(p for p in products if p["asin"] == top["asin"])

    print(f"\n{'='*60}")
    print(f"TOP PRODUCT THIS WEEK: {top_product['name']}")
    print(f"Score: {top['score']}/100 — {top['reason']}")
    print(f"{'='*60}")
    print("Done. generate_pins.py will promote this product next run.")