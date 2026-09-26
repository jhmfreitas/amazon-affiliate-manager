import os
import re
import sys
import time
import json
import math
import requests
import random
from datetime import datetime, timezone
from PIL import Image, ImageDraw, ImageFont, ImageFilter
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
    "clothing":   "1128785162795137672",  # Fashion Finds Under £50
    "fashion":    "1128785162795287251",  # Bags & Accessories
    "shoes":      "1128785162795287253",  # Women's Shoes & Boots
    "jewellery":  "1128785162795287250",  # Jewellery Finds
}
DEFAULT_BOARD = "1128785162795137672"  # Fashion Finds Under £50

# ── Font paths (Google Fonts bundled in repo) ────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT  = os.path.dirname(SCRIPT_DIR)
FONTS_DIR  = os.path.join(REPO_ROOT, "assets", "fonts")

# ── Color palette — warm, editorial, premium ─────────────────
COLORS = {
    "bg_cream":       (250, 247, 243),    # Warm cream background
    "bg_white":       (255, 255, 255),    # Pure white
    "text_dark":      (35, 30, 28),       # Near-black, warm
    "text_mid":       (95, 85, 78),       # Medium brown-grey
    "text_light":     (140, 130, 122),    # Light for subtle text
    "accent_coral":   (232, 113, 91),     # Warm coral for badges
    "accent_sage":    (139, 168, 140),    # Sage green accent
    "accent_gold":    (196, 164, 110),    # Gold for premium feel
    "badge_bg":       (35, 30, 28),       # Dark badge background
    "badge_text":     (255, 255, 255),    # White badge text
    "cta_bg":         (35, 30, 28),       # CTA strip background
    "cta_text":       (255, 255, 255),    # CTA text
    "divider":        (225, 218, 210),    # Subtle divider line
    "shadow":         (0, 0, 0, 25),      # Very subtle shadow
}


# ── 1. Rotation Candidates ────────────────────────────────────

def get_rotation_candidates(limit=5):
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
    return fallback_url or f"https://www.amazon.co.uk/dp/{asin}?tag=pinnpurchas0f-21"

# ── 3. Font Loading ──────────────────────────────────────────

def load_font(name, size):
    """Load a bundled Google Font, with system font fallback."""
    font_files = {
        "title":    "Poppins-Bold.ttf",
        "subtitle": "Poppins-SemiBold.ttf",
        "body":     "Poppins-Medium.ttf",
        "badge":    "Poppins-Bold.ttf",
        "cta":      "Poppins-SemiBold.ttf",
    }
    
    filename = font_files.get(name, "Poppins-Medium.ttf")
    font_path = os.path.join(FONTS_DIR, filename)
    
    if os.path.exists(font_path):
        try:
            return ImageFont.truetype(font_path, size)
        except IOError:
            pass
    
    # Fallback to system fonts
    fallbacks = [
        "C:/Windows/Fonts/segoeuib.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/Library/Fonts/Arial Bold.ttf",
    ]
    for path in fallbacks:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except IOError:
                pass
    return ImageFont.load_default()


# ── 4. Image Helpers ─────────────────────────────────────────

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


def wrap_text(text, font, max_width):
    """Word-wrap text to fit within max_width pixels."""
    lines = []
    words = text.split()
    current_line = ""
    for word in words:
        test_line = current_line + " " + word if current_line else word
        width = font.getlength(test_line) if hasattr(font, 'getlength') else font.getsize(test_line)[0]
        if width <= max_width:
            current_line = test_line
        else:
            if current_line:
                lines.append(current_line)
            current_line = word
    if current_line:
        lines.append(current_line)
    return lines


def get_text_height(text, font):
    """Get the height of a single line of text."""
    bbox = font.getbbox(text) if hasattr(font, 'getbbox') else None
    if bbox:
        return bbox[3] - bbox[1]
    return font.getsize(text)[1] if hasattr(font, 'getsize') else 30


def draw_rounded_rect(draw, xy, radius, fill):
    """Draw a rounded rectangle."""
    x0, y0, x1, y1 = xy
    # Clamp radius to half the smallest dimension
    max_radius = min((x1 - x0) // 2, (y1 - y0) // 2)
    r = min(radius, max_radius)
    
    # Main rectangle
    draw.rectangle([(x0 + r, y0), (x1 - r, y1)], fill=fill)
    draw.rectangle([(x0, y0 + r), (x1, y1 - r)], fill=fill)
    # Four corner circles
    draw.ellipse([(x0, y0), (x0 + 2*r, y0 + 2*r)], fill=fill)
    draw.ellipse([(x1 - 2*r, y0), (x1, y0 + 2*r)], fill=fill)
    draw.ellipse([(x0, y1 - 2*r), (x0 + 2*r, y1)], fill=fill)
    draw.ellipse([(x1 - 2*r, y1 - 2*r), (x1, y1)], fill=fill)


def add_product_shadow(canvas, product_img, x, y):
    """Add a soft drop shadow behind the product image."""
    shadow_offset = 12
    shadow_blur = 20
    
    # Create shadow layer
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    
    # Draw shadow rectangle slightly larger and offset
    shadow_draw.rounded_rectangle(
        [(x + shadow_offset, y + shadow_offset), 
         (x + product_img.width + shadow_offset, y + product_img.height + shadow_offset)],
        radius=16,
        fill=(0, 0, 0, 35)
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(shadow_blur))
    canvas = Image.alpha_composite(canvas, shadow)
    return canvas


# ── 5. Product Hero Template ─────────────────────────────────

def create_pin_image(template_style, bg_url, product_url, title, price=None):
    """
    Creates a single-product pin using the roundup grid's visual style.
    """
    pr_resp = requests.get(product_url, timeout=15)
    pr = Image.open(BytesIO(pr_resp.content)).convert("RGBA")

    W, H = 1000, 1500
    canvas = Image.new("RGBA", (W, H), COLORS["bg_cream"])
    draw = ImageDraw.Draw(canvas)

    title_font = load_font("title", 50)
    badge_font = load_font("badge", 26)
    cta_font = load_font("cta", 28)
    margin = 60
    accent_color = random.choice([
        COLORS["accent_coral"], COLORS["accent_sage"], COLORS["accent_gold"],
    ])
    draw.rectangle([(0, 0), (W, 6)], fill=accent_color)

    lines = wrap_text(title.upper(), title_font, W - margin * 2)[:3]
    line_height = get_text_height("A", title_font) + 12
    y = 90
    for line in lines:
        text_width = title_font.getlength(line)
        draw.text(((W - text_width) / 2, y), line, font=title_font, fill=COLORS["text_dark"])
        y += line_height

    divider_y = y + 16
    div_w = 120
    draw.rectangle(
        [((W - div_w) // 2, divider_y), ((W + div_w) // 2, divider_y + 3)],
        fill=accent_color
    )

    cta_text = "Shop now on Amazon"
    cta_pad_x, cta_pad_y = 34, 18
    cta_width = cta_font.getlength(cta_text) + cta_pad_x * 2
    cta_height = get_text_height(cta_text, cta_font) + cta_pad_y * 2
    cta_x = (W - cta_width) / 2
    cta_y = H - margin - cta_height

    card_x = margin
    card_y = divider_y + 30
    card_width = W - margin * 2
    card_height = cta_y - 20 - card_y
    draw_rounded_rect(
        draw,
        (card_x, card_y, card_x + card_width, card_y + card_height),
        radius=18,
        fill=COLORS["bg_white"]
    )

    price_reserve = 46 if price and price > 0 else 12
    image_padding = 24
    available_width = card_width - image_padding * 2
    available_height = card_height - image_padding * 2 - price_reserve
    pr.thumbnail((int(available_width), int(available_height)), Image.Resampling.LANCZOS)
    pr_x = card_x + (card_width - pr.width) / 2
    pr_y = card_y + image_padding + (available_height - pr.height) / 2
    canvas.paste(pr, (int(pr_x), int(pr_y)), pr)

    if price and price > 0:
        price_text = f"£{int(price)}" if price == int(price) else f"£{price:.2f}"
        text_width = badge_font.getlength(price_text)
        text_height = get_text_height(price_text, badge_font)
        badge_pad_x, badge_pad_y = 16, 8
        badge_width = text_width + badge_pad_x * 2
        badge_height = text_height + badge_pad_y * 2
        badge_x = card_x + (card_width - badge_width) / 2
        badge_y = card_y + card_height - badge_height - 12
        draw_rounded_rect(
            draw,
            (int(badge_x), int(badge_y), int(badge_x + badge_width), int(badge_y + badge_height)),
            radius=int(badge_height // 2),
            fill=COLORS["badge_bg"]
        )
        draw.text(
            (badge_x + badge_pad_x, badge_y + badge_pad_y - 2),
            price_text, font=badge_font, fill=COLORS["badge_text"]
        )

    draw_rounded_rect(
        draw,
        (int(cta_x), int(cta_y), int(cta_x + cta_width), int(cta_y + cta_height)),
        radius=int(cta_height // 2),
        fill=COLORS["cta_bg"]
    )
    cta_text_width = cta_font.getlength(cta_text)
    draw.text(
        (cta_x + (cta_width - cta_text_width) / 2, cta_y + cta_pad_y - 2),
        cta_text, font=cta_font, fill=COLORS["cta_text"]
    )

    draw.rectangle([(0, H - 4), (W, H)], fill=accent_color)

    out = BytesIO()
    canvas.convert("RGB").save(out, "JPEG", quality=92)
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

# ── 6. Gemini Content Generation ─────────────────────────────

def generate_candidates(product, count, keywords):
    url = f"https://generativelanguage.googleapis.com/v1/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    
    category = product.get('category', 'Fashion')
    price = product.get('price', 0)
    price_str = f"£{price:.0f}" if price else "affordable"
    
    prompt = f"""Create {count} high-converting Pinterest pin ideas for this Amazon UK product.

PRODUCT: {product['name']}
CATEGORY: {category}
PRICE: {price_str}
KEYWORDS: {keywords}

RULES FOR TITLE (most important — this is what stops the scroll):
1. Write a HOOK that creates curiosity or shows a benefit
2. Use first-person or second-person voice ("I found...", "You need this...")
3. Include the price if it's a good deal (e.g. "This £25 dress looks designer")
4. Under 60 characters MAXIMUM
5. Examples of great titles:
   - "This £18 bag gets compliments every time"
   - "The summer dress I packed for 3 holidays"
   - "Found the perfect everyday necklace under £20"

RULES FOR DESCRIPTION (Pinterest SEO — this determines search ranking):
1. First sentence MUST contain the primary keyword phrase naturally
2. 200-400 characters total
3. Include 2-3 benefit statements (comfortable, versatile, great quality)
4. End with a soft CTA: "Tap the link to shop" or "Find it on Amazon"
5. NO hashtags — Pinterest penalises them
6. Write in natural, conversational UK English

RULES FOR ALT TEXT:
1. Describe what the product LOOKS like visually
2. 100-150 characters
3. Include colour, material, style

Return ONLY a JSON list of objects with these fields:
- 'title': the scroll-stopping hook
- 'description': Pinterest-SEO optimized description
- 'alt_text': visual description for accessibility
- 'keywords': list of 5-8 Pinterest search phrases (no hashtags)
- 'pexels_search': a 2-3 word search term for finding a lifestyle background image
"""
    
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.8}
    }
    for attempt in range(3):
        try:
            resp = requests.post(url, headers={'Content-Type': 'application/json'}, json=payload, timeout=20)
            resp.raise_for_status()
            data = resp.json()
            raw_text = data['candidates'][0]['content']['parts'][0]['text']
            
            # Clean markdown code block if present
            raw_text = raw_text.strip()
            if raw_text.startswith("```json"):
                raw_text = raw_text[7:]
            if raw_text.startswith("```"):
                raw_text = raw_text[3:]
            if raw_text.endswith("```"):
                raw_text = raw_text[:-3]
                
            candidates = json.loads(raw_text.strip())
            return score_candidates(candidates, product)
            
        except Exception as e:
            err_msg = str(e)
            if hasattr(e, 'response') and e.response is not None:
                err_msg = e.response.text
            print(f"  Gemini Error (Attempt {attempt+1}/3): {err_msg}")
            
            # Fallback to gemini-2.0-flash on quota/availability issues
            if attempt < 2:
                if "429" in err_msg or "503" in err_msg or "RESOURCE_EXHAUSTED" in err_msg or "UNAVAILABLE" in err_msg:
                    print("  Switching to gemini-2.0-flash for next attempt...")
                    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
                time.sleep(5 * (attempt + 1))
            else:
                return []


def score_candidates(candidates, product):
    """Score candidates — prioritize hooks that create curiosity."""
    for c in candidates:
        score = 75
        title = c.get('title', '')
        desc = c.get('description', '')
        
        # Title hook quality
        title_len = len(title)
        if 25 <= title_len <= 55:
            score += 10  # Sweet spot length
        elif title_len < 60:
            score += 5
            
        # First-person / second-person hooks perform best on Pinterest
        lower_title = title.lower()
        if any(w in lower_title for w in ["i ", "i'", "my ", "you ", "your ", "this "]):
            score += 8
            
        # Price mention in title (social proof)
        if "£" in title:
            score += 5
            
        # Description quality
        if 200 <= len(desc) <= 400:
            score += 5
            
        # Penalise hashtags (Pinterest penalises them)
        if "#" in desc:
            score -= 10
            
        c['score'] = min(score, 100)
        
    candidates.sort(key=lambda x: x['score'], reverse=True)
    return candidates


def get_board_for_product(product):
    cat = (product.get("category") or "").lower()
    return BOARD_MAP.get(cat, DEFAULT_BOARD)


def save_pin(pin, product, affiliate_url, board_id, template_style):
    score = product.get("score") or 0
    trend_dir = product.get("trend_dir") or "stable"
    auto_approve = score >= 85 and trend_dir in ("rising", "stable")

    row = {
        "title":         pin["title"],
        "description":   pin["description"],
        "alt_text":      pin.get("alt_text", ""),
        "keywords":      pin.get("keywords", []),
        "image_url":     pin["image_url"],
        "link_url":      affiliate_url,
        "product_id":    product["id"],
        "board_id":      board_id,
        "template_style": template_style,
        "approved":      auto_approve,
        "posted":        False
    }
    return supabase_post("pins", row)

# ── Main ─────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print(f"Pin Generation (Product Hero) — {datetime.now(timezone.utc)}")
    print("=" * 60)

    has_errors = False

    try:
        targets = get_rotation_candidates(limit=5)
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
                    
                    # Always use the Product Hero template now
                    template_style = "product_hero"
                    
                    img_bytes = create_pin_image(
                        template_style, 
                        pexels_url, 
                        product.get('image_url', 'https://via.placeholder.com/800'), 
                        pin['title'],
                        price=product.get('price')
                    )
                    pin['image_url'] = upload_image(img_bytes)
                    
                    saved = save_pin(pin, product, affiliate_url, board_id, template_style)
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
