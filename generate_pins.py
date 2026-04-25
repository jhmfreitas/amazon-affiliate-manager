"""
generate_pins.py
────────────────
Runs weekly (Monday 9am UTC, after score_products.py).

1. Reads top-scored active product from Supabase
2. Gets affiliate URL via Amazon Creators API (auto-generated)
3. Generates 10 pin candidates per slot with Gemini
4. Scores candidates — keeps top 3 per slot
5. Downloads image from Pexels → uploads to Supabase Storage
6. Saves all pins to Supabase with link_url and product_id
"""

import os, json, random, uuid, time, requests
from datetime import datetime

# ── Secrets ──────────────────────────────────────────────────
GEMINI_KEY      = os.environ["GEMINI_API_KEY"]
SUPABASE_URL    = os.environ["SUPABASE_URL"]
SUPABASE_KEY    = os.environ["SUPABASE_KEY"]
PEXELS_KEY      = os.environ["PEXELS_API_KEY"]

# Amazon Creators API
AMAZON_CRED_ID  = os.environ["AMAZON_CREDENTIAL_ID"]
AMAZON_CRED_SEC = os.environ["AMAZON_CREDENTIAL_SECRET"]
AMAZON_TAG      = os.environ["AMAZON_ASSOCIATE_TAG"]

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

# ── Config ───────────────────────────────────────────────────
SLOTS            = 7    # pins to generate per run (1 per day for a week)
CANDIDATES       = 10   # candidates generated per slot
KEEP_TOP         = 3    # top N candidates kept per slot


# ── 1. Get top product ───────────────────────────────────────

def get_top_product():
    """Fetch the highest-scored active product from Supabase."""
    resp = requests.get(
        f"{SUPABASE_URL}/rest/v1/products",
        headers=SUPABASE_HEADERS,
        params={
            "active": "eq.true",
            "order":  "score.desc",
            "limit":  "1"
        }
    )
    resp.raise_for_status()
    products = resp.json()
    if not products:
        raise ValueError("No active products found. Add products to the table first.")
    product = products[0]
    print(f"Top product: {product['name']} (score={product['score']})")
    return product


# ── 2. Amazon Creators API — get affiliate URL ───────────────

def get_affiliate_url(asin, fallback_url=None):
    """
    Get affiliate URL from Amazon Creators API.
    Falls back to stored URL if API is unavailable
    (e.g. if monthly sales drop below threshold).
    """
    try:
        from amazon_paapi import AmazonApi
        amazon = AmazonApi(
            AMAZON_CRED_ID,
            AMAZON_CRED_SEC,
            AMAZON_TAG,
            "co.uk"   # change to "com" for US Associates account
        )
        result = amazon.get_items(asin)
        if result and result.items_result and result.items_result.items:
            url = result.items_result.items[0].detail_page_url
            print(f"Affiliate URL from Creators API: {url[:60]}...")
            return url
    except Exception as e:
        print(f"Creators API unavailable: {e}")
        print("Falling back to stored affiliate URL.")

    # Fallback — use URL stored in products table
    if fallback_url:
        print(f"Using stored affiliate URL.")
        return fallback_url

    print("Warning: no affiliate URL available for this product.")
    return None


# ── 3. Generate pin candidates with Gemini ───────────────────

def generate_candidates(product, n):
    prompt = f"""You are a Pinterest affiliate marketing expert.
Generate {n} UNIQUE high-converting Pinterest pin candidates for this product.

Product:  {product['name']}
Niche:    {product['niche']}
Audience: {product['audience']}
Category: {product['category']}

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
- Emotional resonance with audience: {product['audience']}
- CTA clarity and naturalness

Candidates:
{json.dumps(candidates, indent=2)}

Return ONLY a JSON array in the same order:
[{{"score": 8.5, "reason": "strong pain point, good SEO"}}]"""

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
        params={"query": query, "per_page": 15,
                "orientation": "portrait", "size": "large"}
    )
    resp.raise_for_status()
    photos = resp.json().get("photos", [])
    if not photos:
        return None, None
    photo = random.choice(photos)
    return photo["src"]["large2x"], photo["photographer"]


# ── 6. Upload image to Supabase Storage ──────────────────────

def upload_image(pexels_url):
    img_data  = requests.get(pexels_url, timeout=20).content
    file_name = f"{uuid.uuid4()}.jpg"
    bucket    = "pin-images"

    upload_resp = requests.post(
        f"{SUPABASE_URL}/storage/v1/object/{bucket}/{file_name}",
        headers={
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type":  "image/jpeg",
            "x-upsert":      "true"
        },
        data=img_data
    )

    if not upload_resp.ok:
        print(f"Storage error {upload_resp.status_code}: {upload_resp.text}")
        upload_resp.raise_for_status()

    return f"{SUPABASE_URL}/storage/v1/object/public/{bucket}/{file_name}"


# ── 7. Save pin to Supabase ──────────────────────────────────

def save_pin(pin, product, affiliate_url):
    resp = requests.post(
        f"{SUPABASE_URL}/rest/v1/pins",
        headers=SUPABASE_HEADERS,
        json={
            "title":         pin["title"],
            "description":   pin["description"],
            "hashtags":      pin["hashtags"],
            "pexels_search": pin["pexels_search"],
            "hook_type":     pin["hook_type"],
            "score":         pin["score"],
            "score_reason":  pin["score_reason"],
            "image_url":     pin["image_url"],
            "photographer":  pin.get("photographer", ""),
            "link_url":      affiliate_url or "",
            "product_id":    product["id"],
            "approved":      False,
            "posted":        False
        }
    )
    resp.raise_for_status()
    return resp.json()[0]


# ── Main ─────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print(f"Pin generation run — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 60)

    # 1. Get top product
    product = get_top_product()

    # 2. Get affiliate URL
    affiliate_url = get_affiliate_url(
        asin         = product["asin"],
        fallback_url = product.get("affiliate_url")
    )

    print(f"\nGenerating {SLOTS} pin slots × {CANDIDATES} candidates = "
          f"{SLOTS * CANDIDATES} total Gemini calls")
    print(f"Keeping top {KEEP_TOP} per slot = up to {SLOTS * KEEP_TOP} pins\n")

    all_saved = []

    for slot in range(SLOTS):
        print(f"Slot {slot + 1}/{SLOTS} — {product['name']}")

        # 3. Generate candidates
        print(f"  Generating {CANDIDATES} candidates...")
        try:
            candidates = generate_candidates(product, CANDIDATES)
        except Exception as e:
            print(f"  Generation failed: {e} — skipping slot")
            continue

        # 4. Score and rank
        print(f"  Scoring candidates...")
        try:
            ranked = score_candidates(candidates, product)
        except Exception as e:
            print(f"  Scoring failed: {e} — using unranked order")
            ranked = candidates

        top = ranked[:KEEP_TOP]

        # 5. Save each top candidate
        for i, pin in enumerate(top):
            print(f"  Candidate {i+1} (score={pin['score']}) — {pin['title'][:50]}...")

            # Get image
            pexels_url, photographer = get_pexels_image(pin["pexels_search"])
            if not pexels_url:
                print(f"  No image for '{pin['pexels_search']}' — skipping")
                continue

            # Upload to Supabase Storage
            print(f"  Uploading image...")
            try:
                image_url = upload_image(pexels_url)
            except Exception as e:
                print(f"  Image upload failed: {e} — skipping")
                continue

            pin["image_url"]    = image_url
            pin["photographer"] = photographer

            # Save pin
            saved = save_pin(pin, product, affiliate_url)
            all_saved.append(saved)
            print(f"  Saved pin id={saved['id']}")

            time.sleep(0.5)  # gentle rate limiting

        time.sleep(2)

    print(f"\n{'='*60}")
    print(f"Done. {len(all_saved)} pins saved to Supabase.")
    print(f"Go to your review app to approve them.")
    print(f"{'='*60}")
