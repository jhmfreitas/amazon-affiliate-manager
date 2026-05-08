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

import os, json, random, uuid, time, requests, io
from datetime import datetime, timezone
from PIL import Image, ImageDraw, ImageFont

# ── Secrets ──────────────────────────────────────────────────
GEMINI_KEY      = os.environ["GEMINI_API_KEY"]
SUPABASE_URL    = os.environ["SUPABASE_URL"]
SUPABASE_KEY    = os.environ["SUPABASE_KEY"]
PEXELS_KEY      = os.environ["PEXELS_API_KEY"]

# Amazon Creators API credentials
# Get these from: affiliate-program.amazon.co.uk/creatorsapi
AMAZON_CRED_ID  = os.environ["AMAZON_CREDENTIAL_ID"]
AMAZON_CRED_SEC = os.environ["AMAZON_CREDENTIAL_SECRET"]
AMAZON_TAG      = os.environ["AMAZON_ASSOCIATE_TAG"]
AMAZON_COUNTRY  = os.environ.get("AMAZON_COUNTRY", "co.uk")  # "com" for US

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
SLOTS      = 7    # pins to generate per run (1 per day for a week)
CANDIDATES = 10   # candidates generated per slot
KEEP_TOP   = 3    # top N candidates kept per slot

# Gemini free tier: 15 requests per minute
# We wait between calls and retry on 429 with exponential backoff
GEMINI_RPM_DELAY = 5   # seconds between every Gemini call (safe for free tier)
GEMINI_MAX_RETRY = 4   # max retries on 429


def gemini_call(prompt):
    """
    Call Gemini with automatic retry on 429 (rate limit).
    Waits GEMINI_RPM_DELAY seconds before every call to stay within
    the 15 requests/minute free tier limit.
    Uses exponential backoff on 429: 15s, 30s, 60s, 120s.
    """
    time.sleep(GEMINI_RPM_DELAY)  # always wait before calling

    for attempt in range(GEMINI_MAX_RETRY):
        resp = requests.post(
            GEMINI_URL,
            json={"contents": [{"parts": [{"text": prompt}]}]}
        )

        if resp.status_code in (429, 503):
            reason = "rate limited" if resp.status_code == 429 else "unavailable"
            wait   = 15 * (2 ** attempt)  # 15, 30, 60, 120 seconds
            print(f"  Gemini {reason} ({resp.status_code}) — waiting {wait}s "
                  f"(attempt {attempt + 1}/{GEMINI_MAX_RETRY})...")
            time.sleep(wait)
            continue

        resp.raise_for_status()
        return resp.json()["candidates"][0]["content"]["parts"][0]["text"]

    raise Exception(f"Gemini rate limit persisted after {GEMINI_MAX_RETRY} retries.")


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
        raise ValueError(
            "No active products found. "
            "Add products to the Supabase products table first."
        )
    product = products[0]
    print(f"Top product: {product['name']} (score={product['score']})")
    return product


# ── 2. Amazon Creators API — get affiliate URL ───────────────

def get_affiliate_url(asin, fallback_url=None):
    """
    Get affiliate URL from Amazon Creators API (python-amazon-paapi v6+).

    Uses the new amazon_creatorsapi module which replaces the old
    amazon_paapi module as of version 6.0.0.

    Falls back to the stored URL in the products table if the API
    is unavailable (e.g. monthly sales drop below the threshold).
    """
    try:
        from amazon_creatorsapi import AmazonCreatorsApi, Country
        from amazon_creatorsapi.errors import (
            AmazonCreatorsApiError,
            ItemsNotFoundError,
            TooManyRequestsError,
            AssociateValidationError
        )

        # Map country string to Country enum
        # Docs: country=Country.US, Country.UK, Country.DE etc.
        # Only include Country enum values confirmed in the library docs
        country_map = {
            "com":    Country.US,
            "co.uk":  Country.UK,
            "co.jp":  Country.JP,
            "de":     Country.DE,
            "fr":     Country.FR,
            "es":     Country.ES,
            "it":     Country.IT,
            "ca":     Country.CA,
            "com.au": Country.AU,
            "com.br": Country.BR,
        }

        country_enum = country_map.get(AMAZON_COUNTRY, Country.UK)

        # Debug: Print first 4 chars of credentials (safe)
        print(f"  Debug: Using ID starting with {AMAZON_CRED_ID[:4]}...")
        print(f"  Debug: Using Tag: {AMAZON_TAG}")

        amazon = AmazonCreatorsApi(
            access_key    = AMAZON_CRED_ID.strip(),
            secret_key    = AMAZON_CRED_SEC.strip(),
            version       = "2.2",
            tag           = AMAZON_TAG.strip(),
            country       = country_enum,
            throttling    = 1
        )

        # detail_page_url is returned by default and already contains
        # your affiliate tag in the format:
        # https://www.amazon.co.uk/dp/ASIN?tag=yourtag-21&linkCode=...
        items = amazon.get_items([asin])

        if items and items[0].detail_page_url:
            url = items[0].detail_page_url
            print(f"  ✓ Affiliate URL: {url[:80]}...")
            return url
        else:
            print(f"  Creators API returned no URL for {asin}.")

    except ImportError:
        print("  amazon_creatorsapi not installed — run: pip install python-amazon-paapi")
    except ItemsNotFoundError:
        print(f"  ASIN {asin} not found in Amazon catalogue.")
    except AssociateValidationError:
        print(f"  Associate account not validated — check your tag: {AMAZON_TAG}")
    except TooManyRequestsError:
        print(f"  Creators API rate limit hit — using fallback URL.")
    except AmazonCreatorsApiError as e:
        print(f"  Creators API error: {e} — using fallback URL.")
    except Exception as e:
        print(f"  Unexpected error from Creators API: {e} — using fallback URL.")

    # Fallback — use URL stored in products table
    if fallback_url:
        print(f"  Using stored affiliate URL from products table.")
        return fallback_url

    print(f"  Warning: no affiliate URL available. Pin will have no link.")
    return None


# ── 3. Generate pin candidates with Gemini ───────────────────

def generate_candidates(product, n, pinterest_keywords=None):
    keyword_block = ""
    if pinterest_keywords:
        keyword_block = f"""\n\nREAL PINTEREST SEARCH DATA — These are phrases people actually search.
You MUST weave these naturally into your titles and descriptions:
{json.dumps(pinterest_keywords[:20], indent=2)}\n"""
    prompt = f"""You are a Pinterest SEO and affiliate marketing expert.
Generate {n} UNIQUE high-converting Pinterest pin candidates for this product.

Product:  {product['name']}
Niche:    {product['niche']}
Audience: {product['audience']}
Category: {product['category']}

IMPORTANT CONTEXT: Pinterest is a visual SEARCH ENGINE. Users discover content
by searching keywords. Hashtags are DEAD on Pinterest — do NOT use them.
The algorithm ranks pins by keyword relevance in title + description + alt_text.
{keyword_block}

Return ONLY a valid JSON array, no markdown:
[{{
  "title":         "max 100 chars. FRONT-LOAD the primary search keyword phrase, then add a hook. Example: 'Standing Desk Setup Ideas — Why I Never Sit At Work Anymore'",
  "description":   "Max 500 characters. Think of this as two sections: The first 75 chars are the HOOK (what users see in the feed) — front-load your absolute best keywords here naturally. The next 425 chars are the BODY (what users read when they click) — weave in 4-6 related search phrases naturally. End with a soft CTA. NO hashtags.",
  "alt_text":      "max 490 characters. Describe what the image shows for accessibility. Naturally include 2-3 relevant keywords.",
  "keywords":      ["6-8 Pinterest search phrases people actually type", "long-tail phrases like 'best standing desk for small home office'", "mix of broad and specific"],
  "pexels_search": "2-4 word lifestyle scene query, NOT the product name",
  "hook_type":     "one of: pain_point | curiosity | social_proof | listicle | urgency"
}}]

Rules:
- Every pin MUST have a different hook_type and angle
- Never mention the brand name — focus on the lifestyle benefit
- Title: ALWAYS start with the primary search keyword phrase, then add the emotional hook
- Description: Write flowing, readable sentences — NOT keyword lists.
- Description: First 75 characters MUST contain your #1 keyword phrase.
- alt_text: Describe the lifestyle image scene, weave in keywords naturally
- keywords: These are for internal reference only — phrases people search on Pinterest
- The soft CTA should feel natural, not salesy
- ZERO hashtags anywhere. They hurt Pinterest SEO."""

    text = gemini_call(prompt)
    text = text.replace("```json", "").replace("```", "").strip()
    candidates = json.loads(text)
    
    # Enforce hard truncation at 490 chars (Gap 1)
    for c in candidates:
        if "description" in c:
            c["description"] = c["description"][:490]
            
    return candidates


# ── 4. Score candidates with Gemini ──────────────────────────

def score_candidates(candidates, product):
    prompt = f"""You are a Pinterest SEO and conversion expert.
Score each pin candidate for this product: {product['name']}

Score 1-10 on:
- Keyword front-loading (do the first 50 chars contain the primary search term?)
- Keyword coverage (are 4-6 related search phrases woven into the description naturally?)
- Hook strength (does the title stop the scroll?)
- Emotional resonance with audience: {product['audience']}
- CTA clarity and naturalness
- Description readability (flows like natural language, not keyword stuffing)

Candidates:
{json.dumps(candidates, indent=2)}

Return ONLY a JSON array in the same order, no markdown:
[{{"score": 8.5, "reason": "one sentence explaining the score"}}]"""

    text = gemini_call(prompt)
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
    photo = random.choice(photos)
    return photo["src"]["large2x"], photo["photographer"]


# ── 5.5 Create Moodboard Collage ──────────────────────────────

def create_moodboard(bg_url, product_url, title):
    """
    Downloads the aesthetic Pexels background and the Amazon product image,
    resizes the background to 1000x1500 (Pinterest ratio),
    and places the product in a styled card with a shadow and gradient text overlay.
    Returns JPEG image bytes.
    """
    from PIL import ImageFilter
    import textwrap

    # 1. Download Background (Pexels)
    bg_resp = requests.get(bg_url, timeout=20)
    bg_resp.raise_for_status()
    bg = Image.open(io.BytesIO(bg_resp.content)).convert("RGBA")
    
    # 2. Resize Background (Cover 1000x1500)
    target_ratio = 1000 / 1500
    bg_ratio = bg.width / bg.height
    if bg_ratio > target_ratio:
        # Too wide -> crop sides
        new_width = int(target_ratio * bg.height)
        offset = (bg.width - new_width) // 2
        bg = bg.crop((offset, 0, offset + new_width, bg.height))
    else:
        # Too tall -> crop top/bottom
        new_height = int(bg.width / target_ratio)
        offset = (bg.height - new_height) // 2
        bg = bg.crop((0, offset, bg.width, offset + new_height))
        
    bg = bg.resize((1000, 1500), Image.Resampling.LANCZOS)
    
    # 2.5 Add subtle gradient overlay at the bottom for text readability
    gradient = Image.new('RGBA', (1000, 1500), color=(0,0,0,0))
    draw = ImageDraw.Draw(gradient)
    for y in range(800, 1500):
        alpha = int((y - 800) / 700 * 220)  # Fade to dark
        draw.line([(0, y), (1000, y)], fill=(0, 0, 0, alpha))
    bg = Image.alpha_composite(bg, gradient)

    # 3. Download Product Image (Amazon)
    prod_resp = requests.get(product_url, timeout=20)
    prod_resp.raise_for_status()
    prod = Image.open(io.BytesIO(prod_resp.content)).convert("RGBA")
    
    # 4. Resize Product Image
    prod.thumbnail((600, 600), Image.Resampling.LANCZOS)
    
    # 5. Create a styled card with a light shadow
    card_padding = 60
    card_w = prod.width + (card_padding * 2)
    card_h = prod.height + (card_padding * 2)
    card_x = (1000 - card_w) // 2
    card_y = 250  # Positioned in the top half
    
    # Shadow
    shadow = Image.new('RGBA', bg.size, (0,0,0,0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_rect = [card_x + 10, card_y + 15, card_x + card_w + 10, card_y + card_h + 15]
    shadow_draw.rounded_rectangle(shadow_rect, radius=30, fill=(0, 0, 0, 90))
    shadow = shadow.filter(ImageFilter.GaussianBlur(15))
    bg = Image.alpha_composite(bg, shadow)
    
    # White Card
    card = Image.new('RGBA', bg.size, (0,0,0,0))
    card_draw = ImageDraw.Draw(card)
    card_rect = [card_x, card_y, card_x + card_w, card_y + card_h]
    card_draw.rounded_rectangle(card_rect, radius=30, fill=(255, 255, 255, 255))
    bg = Image.alpha_composite(bg, card)
    
    # 6. Paste Product onto Card
    prod_x = card_x + card_padding
    prod_y = card_y + card_padding
    bg.paste(prod, (prod_x, prod_y), prod) # use prod as mask
    
    # 7. Add text overlay
    try:
        font = ImageFont.truetype("arialbd.ttf", 64)
    except IOError:
        try:
            font = ImageFont.truetype("segoeuib.ttf", 64)
        except IOError:
            font = ImageFont.load_default()
            
    draw = ImageDraw.Draw(bg)
    lines = textwrap.wrap(title, width=28)
    
    # Center text in the bottom section
    current_y = card_y + card_h + 80
    
    for line in lines:
        if hasattr(font, "getbbox"):
            bbox = font.getbbox(line)
            w = bbox[2] - bbox[0]
            h = bbox[3] - bbox[1]
        elif hasattr(draw, "textbbox"):
            bbox = draw.textbbox((0, 0), line, font=font)
            w = bbox[2] - bbox[0]
            h = bbox[3] - bbox[1]
        else:
            w, h = draw.textsize(line, font=font)
            
        draw.text(((1000 - w) // 2, current_y), line, font=font, fill=(255, 255, 255, 255))
        current_y += h + 20
        
    # Return as JPEG bytes
    out = io.BytesIO()
    bg.convert("RGB").save(out, format="JPEG", quality=90)
    return out.getvalue()


# ── 6. Upload image to Supabase Storage ──────────────────────

def upload_image(img_bytes):
    file_name = f"{uuid.uuid4()}.jpg"
    bucket    = "pin-images"

    upload_resp = requests.post(
        f"{SUPABASE_URL}/storage/v1/object/{bucket}/{file_name}",
        headers={
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "apikey":        SUPABASE_KEY,   # required alongside Authorization
            "Content-Type":  "image/jpeg",
            "x-upsert":      "true"
        },
        data=img_bytes
    )

    if not upload_resp.ok:
        print(f"  Storage error {upload_resp.status_code}: {upload_resp.text}")
        upload_resp.raise_for_status()

    return f"{SUPABASE_URL}/storage/v1/object/public/{bucket}/{file_name}"


# ── 7. Board mapping by category ─────────────────────────────

# Maps product categories to the most relevant Pinterest board.
# IDs from your PinNPurchase account boards.
BOARD_MAP = {
    # Category/niche → Pinterest board ID
    "home office":      "1128785162795137658",  # Home Office Setup Ideas
    "desk":             "1128785162795137658",  # Home Office Setup Ideas
    "standing desk":    "1128785162795137658",  # Home Office Setup Ideas
    "aesthetic desk":   "1128785162795137662",  # Aesthetic Desk Setup
    "kitchen":          "1128785162795137663",  # Kitchen Gadgets Worth It
    "kitchen_dining":   "1128785162795137663",  # Kitchen Gadgets Worth It
    "home_kitchen":     "1128785162795137663",  # Kitchen Gadgets Worth It
    "sports":           "1128785162795137664",  # Fitness Gear for Home Workouts
    "fitness":          "1128785162795137664",  # Fitness Gear for Home Workouts
    "gym":              "1128785162795137664",  # Fitness Gear for Home Workouts
    "sports_fitness":   "1128785162795137664",  # Fitness Gear for Home Workouts
    "beauty":           "1128785162795137666",  # Skincare Routine Essentials
    "fashion":          "1128785162795137672",  # Fashion Finds Under £50
    "gifts":            "1128785162795137670",  # Gifts for Her
    "home":             "1128785162794432647",  # Budget Home Upgrades
    "home_improvement": "1128785162794432647",  # Budget Home Upgrades
    "furniture":        "1128785162794432647",  # Budget Home Upgrades
    "garden":           "1128785162794432647",  # Budget Home Upgrades
    "tech":             "1128785162794246403",  # Tech
    "electronics":      "1128785162794246403",  # Tech
    "travel":           "1128785162794244746",  # Travel
    "luggage":          "1128785162794244746",  # Travel
}
DEFAULT_BOARD = "1128785162794453906"           # Amazon Must-Haves UK

def get_board_for_product(product):
    """Pick the best board ID for a product based on category/niche."""
    cat   = (product.get("category") or "").lower().replace(" ", "_")
    niche = (product.get("niche") or "").lower().replace(" ", "_")
    return BOARD_MAP.get(cat) or BOARD_MAP.get(niche) or DEFAULT_BOARD


# ── 8. Save pin to Supabase ──────────────────────────────────

def save_pin(pin, product, affiliate_url, board_id):
    row = {
        "title":         pin["title"],
        "description":   pin["description"],
        "alt_text":      pin.get("alt_text", ""),
        "keywords":      pin.get("keywords", []),   # Proper keywords column (Gap 2)
        "pexels_search": pin["pexels_search"],
        "hook_type":     pin.get("hook_type", ""),
        "score":         pin.get("score", 0),
        "score_reason":  pin.get("score_reason", ""),
        "image_url":     pin["image_url"],
        "photographer":  pin.get("photographer", ""),
        "link_url":      affiliate_url or "",
        "product_id":    product["id"],
        "board_id":      board_id,
        "approved":      False,
        "posted":        False
    }
    resp = requests.post(
        f"{SUPABASE_URL}/rest/v1/pins",
        headers=SUPABASE_HEADERS,
        json=row
    )
    resp.raise_for_status()
    return resp.json()[0]


# ── Main ─────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print(f"Pin generation — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 60)

    # 1. Get top scored product
    product = get_top_product()

    # 2. Get affiliate URL via Creators API (with fallback)
    print(f"\nGetting affiliate URL for ASIN: {product['asin']}")
    affiliate_url = get_affiliate_url(
        asin         = product["asin"],
        fallback_url = product.get("affiliate_url")
    )

    # Get the Pinterest keywords researched during product scoring
    pinterest_keywords = product.get("pinterest_keywords")
    if pinterest_keywords:
        print(f"\nLoaded {len(pinterest_keywords)} Pinterest search keywords from DB")
    else:
        print(f"\nWarning: No Pinterest keywords found for product in DB")

    # Pick the right board for this product's category
    board_id = get_board_for_product(product)
    print(f"Board ID: {board_id}")

    print(f"\nGenerating {SLOTS} slots × {CANDIDATES} candidates, "
          f"keeping top {KEEP_TOP} each\n")

    all_saved = []

    for slot in range(SLOTS):
        print(f"── Slot {slot + 1}/{SLOTS} ──")

        # 3. Generate candidates with real keyword data
        print(f"  Generating {CANDIDATES} candidates...")
        try:
            candidates = generate_candidates(product, CANDIDATES, pinterest_keywords)
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
            print(f"  [{i+1}/{KEEP_TOP}] score={pin.get('score', 'unranked')} "
                  f"— {pin['title'][:55]}...")

            # Get image from Pexels
            pexels_url, photographer = get_pexels_image(pin["pexels_search"])
            if not pexels_url:
                print(f"  No image found for '{pin['pexels_search']}' — skipping")
                continue

            # Create Moodboard
            print(f"  Creating Moodboard...")
            try:
                # Fallback to a blank image URL if none exists in DB yet
                amazon_url = product.get("image_url") or "https://via.placeholder.com/800"
                moodboard_bytes = create_moodboard(pexels_url, amazon_url, pin["title"])
            except Exception as e:
                print(f"  Moodboard creation failed: {e} — skipping")
                continue

            # Upload Moodboard to Supabase Storage
            print(f"  Uploading image to Supabase...")
            try:
                pin["image_url"]    = upload_image(moodboard_bytes)
                pin["photographer"] = photographer
            except Exception as e:
                print(f"  Upload failed: {e} — skipping")
                continue

            # Save pin row
            try:
                saved = save_pin(pin, product, affiliate_url, board_id)
                all_saved.append(saved)
                print(f"  Saved pin id={saved['id']}")
            except Exception as e:
                print(f"  Save failed: {e} — skipping")
                continue

            time.sleep(0.5)

        time.sleep(2)

    print(f"\n{'='*60}")
    print(f"Done. {len(all_saved)} pins saved.")
    print(f"Product: {product['name']}")
    print(f"Affiliate link: {affiliate_url or '(none)'}")
    print(f"Go to your review app to approve pins.")
    print(f"{'='*60}")