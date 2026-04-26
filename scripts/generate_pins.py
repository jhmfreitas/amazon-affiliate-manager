"""
generate_pins.py
────────────────
Runs weekly (Monday 9am UTC, after score_products.py).

1. Reads top 3 scored active products from Supabase
2. Gets affiliate URL via Amazon Creators API (auto-generated)
3. Distributes 7 pin slots across products (weighted by score)
4. Generates 10 pin candidates per slot with Gemini
5. Scores candidates — keeps top 3 per slot
6. Downloads image from Pexels → uploads to Supabase Storage
7. Saves all pins to Supabase with link_url and product_id

Improvements over v1:
- Multi-product rotation (top 3, not just 1)
- Score-weighted slot distribution
- Skips products already pinned this week
- Uses shared config
"""

import os, json, random, uuid, time, requests
from datetime import datetime, timezone, timedelta
from config import log, supabase_get, supabase_post, SUPABASE_URL, SUPABASE_KEY

# ── Secrets ──────────────────────────────────────────────────
GEMINI_KEY      = os.environ["GEMINI_API_KEY"]
PEXELS_KEY      = os.environ["PEXELS_API_KEY"]

# Amazon Creators API credentials
AMAZON_CRED_ID  = os.environ.get("AMAZON_CREDENTIAL_ID", "")
AMAZON_CRED_SEC = os.environ.get("AMAZON_CREDENTIAL_SECRET", "")
AMAZON_TAG      = os.environ.get("AMAZON_ASSOCIATE_TAG", "")
AMAZON_COUNTRY  = os.environ.get("AMAZON_COUNTRY", "co.uk")

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta"
    f"/models/gemini-2.5-flash-lite:generateContent?key={GEMINI_KEY}"
)

# ── Config ───────────────────────────────────────────────────
TOP_N      = 3    # number of products to promote per run
SLOTS      = 7    # total pins to generate (1 per day for a week)
CANDIDATES = 10   # candidates generated per slot
KEEP_TOP   = 3    # top N candidates kept per slot


# ── 1. Get top products ─────────────────────────────────────

def get_top_products(n=TOP_N):
    """Fetch the highest-scored active products, skipping those pinned this week."""
    # Get more than we need in case some were pinned recently
    products = supabase_get("products", params={
        "active": "eq.true",
        "order":  "score.desc",
        "limit":  str(n * 2)
    })

    if not products:
        raise ValueError(
            "No active products found. "
            "Add products to the Supabase products table first."
        )

    # Check which products already have pins from this week
    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    recent_pins = supabase_get("pins", params={
        "select":     "product_id",
        "created_at": f"gte.{week_ago}",
    })
    recently_pinned = {p["product_id"] for p in recent_pins}

    # Filter out recently pinned, but keep at least some
    fresh = [p for p in products if p["id"] not in recently_pinned]
    selected = (fresh if len(fresh) >= n else products)[:n]

    for p in selected:
        log.info(f"  Selected: {p['name'][:60]} (score={p.get('score', 0)})")

    return selected


def distribute_slots(products, total_slots):
    """
    Distribute pin slots across products, weighted by score.
    Higher-scored products get more slots.
    """
    scores = [max(p.get("score", 1), 1) for p in products]
    total_score = sum(scores)

    # Weighted distribution, minimum 1 slot per product
    allocation = []
    remaining = total_slots

    for i, score in enumerate(scores):
        if i == len(scores) - 1:
            # Last product gets remaining slots
            allocation.append(remaining)
        else:
            slots = max(1, round(total_slots * score / total_score))
            slots = min(slots, remaining - (len(scores) - i - 1))  # leave ≥1 for others
            allocation.append(slots)
            remaining -= slots

    return allocation


# ── 2. Amazon Creators API — get affiliate URL ───────────────

def get_affiliate_url(asin, fallback_url=None):
    """
    Get affiliate URL from Amazon Creators API (python-amazon-paapi v6+).
    Falls back to the stored URL in the products table if the API
    is unavailable.
    """
    if not (AMAZON_CRED_ID and AMAZON_CRED_SEC and AMAZON_TAG):
        log.info("  Amazon Creators API credentials not set — using fallback.")
        if fallback_url:
            return fallback_url
        return f"https://www.amazon.co.uk/dp/{asin}?tag={AMAZON_TAG}" if AMAZON_TAG else None

    try:
        from amazon_creatorsapi import AmazonCreatorsApi
        from amazon_creatorsapi.models import GetItemsResource
        from amazon_creatorsapi.errors import (
            AmazonCreatorsApiError,
            ItemsNotFoundError,
            TooManyRequestsError,
            AssociateValidationError
        )

        amazon = AmazonCreatorsApi(
            credential_id     = AMAZON_CRED_ID,
            credential_secret = AMAZON_CRED_SEC,
            version           = "2.2",
            tag               = AMAZON_TAG,
            country           = AMAZON_COUNTRY
        )

        items = amazon.get_items(
            [asin],
            resources=[GetItemsResource.ITEMINFO_TITLE]
        )

        if items and items[0].detail_page_url:
            url = items[0].detail_page_url
            log.info(f"  Affiliate URL from Creators API: {url[:70]}...")
            return url
        else:
            log.info(f"  Creators API returned no URL for {asin}.")

    except ImportError:
        log.warning("  amazon_creatorsapi not installed — run: pip install python-amazon-paapi")
    except ItemsNotFoundError:
        log.warning(f"  ASIN {asin} not found in Amazon catalogue.")
    except AssociateValidationError:
        log.warning(f"  Associate account not validated — check credentials and tag.")
    except TooManyRequestsError:
        log.warning(f"  Creators API rate limit hit — using fallback URL.")
    except AmazonCreatorsApiError as e:
        log.warning(f"  Creators API error: {e} — using fallback URL.")
    except Exception as e:
        log.warning(f"  Unexpected error from Creators API: {e} — using fallback URL.")

    # Fallback — use URL stored in products table
    if fallback_url:
        log.info(f"  Using stored affiliate URL from products table.")
        return fallback_url

    log.warning(f"  No affiliate URL available for {asin}. Pin will have no link.")
    return None


# ── 3. Generate pin candidates with Gemini ───────────────────

def generate_candidates(product, n):
    prompt = f"""You are a Pinterest affiliate marketing expert.
Generate {n} UNIQUE high-converting Pinterest pin candidates for this product.

Product:  {product['name']}
Niche:    {product.get('niche', 'general')}
Audience: {product.get('audience', 'general shoppers')}
Category: {product.get('category', 'general')}

Return ONLY a valid JSON array, no markdown:
[{{
  "title":         "max 100 chars, pain-point hook, keyword-rich for Pinterest SEO",
  "description":   "150-200 words, first-person, conversational, ends with soft CTA",
  "hashtags":      ["12 tags", "no # prefix", "mix broad and niche"],
  "pexels_search": "2-4 word lifestyle scene query, NOT the product name",
  "hook_type":     "one of: pain_point | curiosity | social_proof | listicle | urgency"
}}]

Rules:
- Every pin MUST have a different hook_type and angle
- Never mention the brand name — focus on the lifestyle benefit
- Pinterest SEO: include keywords people actually search for
- The soft CTA should feel natural, not salesy"""

    resp = requests.post(
        GEMINI_URL,
        json={"contents": [{"parts": [{"text": prompt}]}]}
    )
    resp.raise_for_status()
    text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    text = text.replace("```json", "").replace("```", "").strip()
    return json.loads(text)


# ── 4. Score candidates with Gemini ──────────────────────────

def score_candidates(candidates, product):
    prompt = f"""You are a Pinterest conversion expert.
Score each pin candidate for this product: {product['name']}

Score 1-10 on:
- Hook strength (stops the scroll)
- Keyword density (Pinterest SEO)
- Emotional resonance with audience: {product.get('audience', 'general shoppers')}
- CTA clarity and naturalness

Candidates:
{json.dumps(candidates, indent=2)}

Return ONLY a JSON array in the same order, no markdown:
[{{"score": 8.5, "reason": "one sentence explaining the score"}}]"""

    resp = requests.post(
        GEMINI_URL,
        json={"contents": [{"parts": [{"text": prompt}]}]}
    )
    resp.raise_for_status()
    text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    text = text.replace("```json", "").replace("```", "").strip()
    scores = json.loads(text)

    for i, c in enumerate(candidates):
        c["score"]        = scores[i]["score"]
        c["score_reason"] = scores[i]["reason"]

    return sorted(candidates, key=lambda x: x["score"], reverse=True)


# ── 5. Pexels image ──────────────────────────────────────────

def get_pexels_image(query):
    resp = requests.get(
        "https://api.pexels.com/v1/search",
        headers={"Authorization": PEXELS_KEY},
        params={
            "query":       query,
            "per_page":    15,
            "orientation": "portrait",
            "size":        "large"
        }
    )
    resp.raise_for_status()
    photos = resp.json().get("photos", [])
    if not photos:
        return None, None

    # Prefer higher resolution images — sort by width, pick from top 5
    photos.sort(key=lambda p: p["src"].get("original", p["src"]["large2x"]), reverse=True)
    photo = random.choice(photos[:5])
    return photo["src"]["large2x"], photo["photographer"]


# ── 6. Upload image to Supabase Storage ──────────────────────

def upload_image(pexels_url):
    img_data  = requests.get(pexels_url, timeout=20).content
    file_name = f"{uuid.uuid4()}.jpg"
    bucket    = "pin-images"

    upload_resp = requests.post(
        f"{SUPABASE_URL}/storage/v1/object/{bucket}/{file_name}",
        headers={
            "apikey":        SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type":  "image/jpeg",
            "x-upsert":      "true"
        },
        data=img_data
    )

    if not upload_resp.ok:
        log.error(f"  Storage error {upload_resp.status_code}: {upload_resp.text}")
        upload_resp.raise_for_status()

    return f"{SUPABASE_URL}/storage/v1/object/public/{bucket}/{file_name}"


# ── 7. Save pin to Supabase ──────────────────────────────────

def save_pin(pin, product, affiliate_url):
    resp = supabase_post("pins", {
        "title":         pin["title"],
        "description":   pin["description"],
        "hashtags":      pin["hashtags"],
        "pexels_search": pin["pexels_search"],
        "hook_type":     pin.get("hook_type", ""),
        "score":         pin.get("score", 0),
        "score_reason":  pin.get("score_reason", ""),
        "image_url":     pin["image_url"],
        "photographer":  pin.get("photographer", ""),
        "link_url":      affiliate_url or "",
        "product_id":    product["id"],
        "approved":      False,
        "posted":        False
    })
    return resp.json()[0]


# ── Main ─────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info("=" * 60)
    log.info(f"Pin generation — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    log.info("=" * 60)

    # 1. Get top scored products (multiple)
    products = get_top_products(TOP_N)
    slot_allocation = distribute_slots(products, SLOTS)

    log.info(f"\n{len(products)} products × {SLOTS} total slots, "
             f"keeping top {KEEP_TOP} per slot\n")
    for p, slots in zip(products, slot_allocation):
        log.info(f"  {p['name'][:50]} → {slots} slots")

    # 2. Get affiliate URLs for each product
    affiliate_urls = {}
    for product in products:
        log.info(f"\nGetting affiliate URL for ASIN: {product['asin']}")
        affiliate_urls[product["id"]] = get_affiliate_url(
            asin         = product["asin"],
            fallback_url = product.get("affiliate_url")
        )

    all_saved = []

    # 3. Generate pins for each product
    for product, num_slots in zip(products, slot_allocation):
        affiliate_url = affiliate_urls[product["id"]]
        log.info(f"\n{'─'*60}")
        log.info(f"Product: {product['name'][:60]} ({num_slots} slots)")
        log.info(f"{'─'*60}")

        for slot in range(num_slots):
            log.info(f"\n  ── Slot {slot + 1}/{num_slots} ──")

            # Generate candidates
            log.info(f"  Generating {CANDIDATES} candidates...")
            try:
                candidates = generate_candidates(product, CANDIDATES)
            except Exception as e:
                log.error(f"  Generation failed: {e} — skipping slot")
                continue

            # Score and rank
            log.info(f"  Scoring candidates...")
            try:
                ranked = score_candidates(candidates, product)
            except Exception as e:
                log.warning(f"  Scoring failed: {e} — using unranked order")
                ranked = candidates

            top = ranked[:KEEP_TOP]

            # Save each top candidate
            for i, pin in enumerate(top):
                log.info(f"  [{i+1}/{KEEP_TOP}] score={pin.get('score', '?')} "
                         f"— {pin['title'][:55]}...")

                # Get image from Pexels
                pexels_url, photographer = get_pexels_image(pin["pexels_search"])
                if not pexels_url:
                    log.warning(f"  No image found for '{pin['pexels_search']}' — skipping")
                    continue

                # Upload to Supabase Storage
                log.info(f"  Uploading image...")
                try:
                    pin["image_url"]    = upload_image(pexels_url)
                    pin["photographer"] = photographer
                except Exception as e:
                    log.error(f"  Upload failed: {e} — skipping")
                    continue

                # Save pin row
                try:
                    saved = save_pin(pin, product, affiliate_url)
                    all_saved.append(saved)
                    log.info(f"  Saved pin id={saved['id']}")
                except Exception as e:
                    log.error(f"  Save failed: {e} — skipping")
                    continue

                time.sleep(0.5)

            time.sleep(2)

    log.info(f"\n{'='*60}")
    log.info(f"Done. {len(all_saved)} pins saved across {len(products)} products.")
    for product in products:
        count = sum(1 for s in all_saved if s.get("product_id") == product["id"])
        log.info(f"  {product['name'][:50]}: {count} pins")
    log.info(f"Go to your review app to approve pins.")
    log.info(f"{'='*60}")