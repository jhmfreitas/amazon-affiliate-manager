import os
import re
import time
import json
import requests
import random
from datetime import datetime, timezone
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO
from config import (
    SUPABASE_URL, SUPABASE_HEADERS, 
    supabase_get, supabase_post, log
)
from pinterest_auth import PinterestAuth

# ── API Keys ─────────────────────────────────────────────────
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY")

# ── Config ───────────────────────────────────────────────────
SLOTS      = 1   # How many different pin designs per product
CANDIDATES = 2   # How many AI variations to brainstorm
KEEP_TOP   = 1   # How many to actually save
AMAZON_COUNTRY = "co.uk"
BOARD_MAP = {
    "kitchen":          "1128785162794244743",  # Aesthetic Kitchen
    "aesthetic_kitchen":"1128785162794244743",
    "bedroom":          "1128785162794244744",  # Cozy Bedroom
    "bathroom":         "1128785162794244745",  # Spa Bathroom
    "home":             "1128785162794432647",  # Budget Home Upgrades
    "furniture":        "1128785162794432647",
}
DEFAULT_BOARD = "1128785162794453906"

# ── 1. Rotation Candidates ────────────────────────────────────

def get_rotation_candidates(limit=3):
    """Fetch a pool of products and pick the best for rotation."""
    resp = requests.get(
        f"{SUPABASE_URL}/rest/v1/products",
        headers=SUPABASE_HEADERS,
        params={
            "active": "eq.true",
            "order":  "score.desc",
            "limit":  "50"
        }
    )
    resp.raise_for_status()
    products = resp.json()
    if not products:
        raise ValueError("No active products found.")

    now = datetime.now(timezone.utc)
    def rotation_rank(p):
        lp = p.get("last_pinned_at")
        if not lp: return 1000 + (p.get("score") or 0)
        try:
            lp_dt = datetime.fromisoformat(lp.replace("Z", "+00:00"))
            days_since = (now - lp_dt).days
            return (p.get("score") or 0) + (min(days_since, 14) * 5)
        except: return p.get("score") or 0

    products.sort(key=rotation_rank, reverse=True)
    return products[:limit]

# ── 2. Affiliate URL ──────────────────────────────────────────

def get_affiliate_url(asin, fallback_url=None):
    """Simple passthrough for now, can be expanded to PA-API."""
    return fallback_url or f"https://www.amazon.co.uk/dp/{asin}?tag=yourtag-21"

# ── 3. Image Handling ─────────────────────────────────────────

def get_pexels_image(query):
    url = "https://api.pexels.com/v1/search"
    headers = {"Authorization": PEXELS_API_KEY}
    params = {"query": query, "per_page": 1, "orientation": "portrait"}
    try:
        resp = requests.get(url, headers=headers, params=params)
        data = resp.json()
        if data["photos"]:
            return data["photos"][0]["src"]["large"], data["photos"][0]["photographer"]
    except: pass
    return None, None

def create_moodboard(bg_url, product_url, title):
    # Download images
    bg_resp = requests.get(bg_url)
    pr_resp = requests.get(product_url)
    
    bg = Image.open(BytesIO(bg_resp.content)).convert("RGBA")
    pr = Image.open(BytesIO(pr_resp.content)).convert("RGBA")
    
    # Resize BG to Pinterest Standard (1000x1500)
    bg = bg.resize((1000, 1500), Image.Resampling.LANCZOS)
    
    # Resize product image
    pr.thumbnail((600, 600), Image.Resampling.LANCZOS)
    
    # Create white circle for product
    mask = Image.new("L", pr.size, 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((0, 0, pr.size[0], pr.size[1]), fill=255)
    
    canvas = Image.new("RGBA", bg.size, (0,0,0,0))
    canvas.paste(bg, (0,0))
    
    # Center product
    pos = ((1000 - pr.size[0]) // 2, (1500 - pr.size[1]) // 2)
    canvas.paste(pr, pos, mask)
    
    # Save
    out = BytesIO()
    canvas.convert("RGB").save(out, "JPEG", quality=90)
    return out.getvalue()

def upload_image(image_bytes):
    # Use binary headers for storage, not JSON headers
    storage_headers = {
        "Authorization": SUPABASE_HEADERS["Authorization"],
        "apikey":        SUPABASE_HEADERS["apikey"],
        "Content-Type":  "image/jpeg"
    }
    filename = f"pin_{int(time.time())}_{random.randint(1000, 9999)}.jpg"
    url = f"{SUPABASE_URL}/storage/v1/object/pin-images/{filename}"
    
    resp = requests.post(url, headers=storage_headers, data=image_bytes)
    if resp.status_code != 200:
        print(f"  Storage Error: {resp.status_code} - {resp.text}")
        resp.raise_for_status()
    return f"{SUPABASE_URL}/storage/v1/object/public/pin-images/{filename}"

# ── 4. Gemini Content Generation ─────────────────────────────

def generate_candidates(product, count, keywords):
    url = f"https://generativelanguage.googleapis.com/v1/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    prompt = f"""
    Create {count} Pinterest Pin ideas for: {product['name']}
    Category: {product.get('category', 'Home')}
    Keywords: {keywords}
    
    STRICT RULES:
    1. Title: Under 90 characters.
    2. Description: 200-400 characters total. Use natural keywords.
    3. NO HASHTAGS (e.g., #HomeDecor). Use flowing sentences instead.
    
    Format: JSON list of objects with 'title', 'description', 'alt_text', 'pexels_search'.
    """
    resp = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]})
    data = resp.json()
    
    if "candidates" not in data:
        print(f"  Gemini Error: {json.dumps(data)}")
        raise ValueError(f"Gemini failed: {data.get('error', {}).get('message', 'Unknown error')}")
        
    txt = data['candidates'][0]['content']['parts'][0]['text']
    candidates = json.loads(re.search(r"\[.*\]", txt, re.S).group(0))

    # Smart Sentence-Aware Truncation
    for c in candidates:
        if len(c['description']) > 500:
            text = c['description'][:500]
            # Look for the last sentence-ender (. ! or ?)
            last_punc = max(text.rfind('.'), text.rfind('!'), text.rfind('?'))
            if last_punc > 100: # Ensure we didn't cut off too much
                c['description'] = text[:last_punc + 1]
            else:
                # Fallback to last space if no punctuation found
                last_space = text.rfind(' ')
                c['description'] = text[:last_space] + "..."
            
    return candidates

def score_candidates(candidates, product):
    # Simple pass-through for now, can be sophisticated later
    for c in candidates: c["score"] = 90
    return candidates

def get_board_for_product(product):
    cat = (product.get("category") or "").lower()
    return BOARD_MAP.get(cat, DEFAULT_BOARD)

def save_pin(pin, product, affiliate_url, board_id):
    row = {
        "title":         pin["title"],
        "description":   pin["description"],
        "image_url":     pin["image_url"],
        "link_url":      affiliate_url,
        "product_id":    product["id"],
        "board_id":      board_id,
        "approved":      False,
        "posted":        False
    }
    return supabase_post("pins", row)

import sys

# ... (keep imports same)

# ── Main ─────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print(f"Pin Generation Carousel — {datetime.now(timezone.utc)}")
    print("=" * 60)

    has_errors = False

    try:
        targets = get_rotation_candidates(limit=3)
        print(f"Processing {len(targets)} products...")
        
        for product in targets:
            print(f"\n--- {product['name'][:50]} ---")
            try:
                affiliate_url = get_affiliate_url(product['asin'], product.get('affiliate_url'))
                board_id = get_board_for_product(product)
                
                # Generate and Save
                candidates = generate_candidates(product, SLOTS, product.get('pinterest_keywords', []))
                for pin in candidates:
                    pexels_url, photog = get_pexels_image(pin['pexels_search'])
                    if not pexels_url: 
                        log.error("No image found for pexels search")
                        has_errors = True
                        continue
                    
                    img_bytes = create_moodboard(pexels_url, product.get('image_url', 'https://via.placeholder.com/800'), pin['title'])
                    pin['image_url'] = upload_image(img_bytes)
                    
                    saved = save_pin(pin, product, affiliate_url, board_id)
                    print(f"  ✓ Saved Pin: {pin['title'][:40]}")

                # Mark as pinned
                requests.patch(
                    f"{SUPABASE_URL}/rest/v1/products?id=eq.{product['id']}",
                    headers=SUPABASE_HEADERS,
                    json={"last_pinned_at": datetime.now(timezone.utc).isoformat()}
                )
            except Exception as e:
                print(f"  ✗ Product Failed: {e}")
                has_errors = True
            
    except Exception as e:
        print(f"FATAL ERROR: {e}")
        has_errors = True

    print("\nRotation Complete.")
    if has_errors:
        print("!! TERMINATING WITH ERROR STATUS !!")
        sys.exit(1)
    else:
        sys.exit(0)