"""
generate_grid_pins.py
──────────────────────
Generates "roundup" pins that feature several products in one themed
grid (e.g. "5 Going-Out Tops Under £25") instead of one product per pin.

Why: comparison/roundup grids are a well-documented higher-CTR Pinterest
format than a single product-on-white-background hero shot, and they
work fine with the plain Amazon catalog photos this pipeline already
scrapes — no new photography or scraping required.

Reuses the same `products` rows and scoring already collected by
discover_products.py / score_products.py. No new Supabase tables or
columns required — grid pins are saved into the existing `pins` table
with template_style="grid_roundup".

LINK TARGET: grid pins link to your Amazon Storefront (STOREFRONT_URL)
so one click can browse the whole themed collection, not just one ASIN.
Set the AMAZON_STOREFRONT_URL secret/env var to your real storefront
link before running this in production — until it's set, this script
falls back to linking the first featured product instead, and prints
a warning so it's obvious in the run logs.

APPROVAL: unlike generate_pins.py, grid pins are never auto-approved.
This is a new, unproven format — review the first batch in your
review app before flipping that.
"""

import os
import sys
import json
import time
import math
import random
import re
import requests
from datetime import datetime, timezone
from io import BytesIO
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import SUPABASE_URL, SUPABASE_HEADERS, supabase_post, log
from generate_pins import (
    COLORS, load_font, wrap_text, get_text_height, draw_rounded_rect,
    upload_image, get_affiliate_url, BOARD_MAP, DEFAULT_BOARD,
    GEMINI_API_KEY,
)

# ── Config ───────────────────────────────────────────────────
GRID_SIZE          = 4    # products per grid pin (2x2 layout)
MIN_GROUP          = 4    # need at least this many same-category products to build one
MAX_GRIDS_PER_RUN  = 3    # how many roundup pins to generate per run

STOREFRONT_URL = os.environ.get("AMAZON_STOREFRONT_URL", "").strip()


# ── 1. Group active products into themes by category ────────

def get_theme_groups():
    """Fetch active products and group same-category items into themes,
    preferring items that haven't been pinned recently."""
    resp = requests.get(
        f"{SUPABASE_URL}/rest/v1/products",
        headers=SUPABASE_HEADERS,
        params={"active": "eq.true", "order": "score.desc", "limit": "200"}
    )
    resp.raise_for_status()
    products = resp.json()

    now = datetime.now(timezone.utc)

    def days_since_pinned(p):
        lp = p.get("last_pinned_at")
        if not lp:
            return 999
        try:
            lp_dt = datetime.fromisoformat(lp.replace("Z", "+00:00"))
            return (now - lp_dt).days
        except Exception:
            return 999

    by_category = {}
    for p in products:
        targeting = f"{p.get('name', '')} {p.get('audience', '')}"
        if not re.search(r"\b(women|woman|female|ladies)\b", targeting, re.IGNORECASE):
            continue
        by_category.setdefault(p.get("category") or "misc", []).append(p)

    groups = []
    for category, items in by_category.items():
        if len(items) < MIN_GROUP:
            continue
        items.sort(key=lambda p: (days_since_pinned(p), p.get("score") or 0), reverse=True)
        groups.append((category, items[:GRID_SIZE]))

    random.shuffle(groups)
    return groups


# ── 2. Gemini roundup copy ───────────────────────────────────

def generate_grid_copy(category, products):
    """Ask Gemini for ONE roundup pin idea covering all products together."""
    url = f"https://generativelanguage.googleapis.com/v1/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"

    names = [p["name"] for p in products]
    prices = [p.get("price") for p in products if p.get("price")]
    price_cap = math.ceil(max(prices) / 5) * 5 if prices else None
    audience = products[0].get("audience", "")

    prompt = f"""Create ONE high-converting Pinterest "roundup" pin idea covering these {len(products)} Amazon UK products together, as a themed collection — NOT a single product.

CATEGORY: {category}
TARGET AUDIENCE: Women. Keep the title, description, keywords, and visual concept relevant to women. Do not target men or a general audience.
PRODUCT AUDIENCE CONTEXT: {audience}
PRODUCTS: {"; ".join(names)}
PRICE CAP: {f"under £{price_cap}" if price_cap else "not available"}

RULES FOR TITLE:
1. Frame it as a themed edit/roundup, e.g. "{len(products)} Going-Out Tops Under £25"
2. Include the item count and the price cap if available
3. Under 60 characters MAXIMUM

RULES FOR DESCRIPTION (Pinterest SEO — this determines search ranking):
1. First sentence MUST contain the primary keyword phrase naturally
2. 200-400 characters total
3. Mention it's a curated edit/collection, not a single item
4. End with a soft CTA: "Tap to shop the edit"
5. NO hashtags — Pinterest penalises them

RULES FOR ALT TEXT:
1. Describe the grid visually (colours/styles across the items)
2. 100-150 characters

Return ONLY a single JSON object (not a list) with these fields:
- 'title': the roundup hook
- 'description': Pinterest-SEO optimized description
- 'alt_text': visual description for accessibility
- 'keywords': list of 5-8 Pinterest search phrases (no hashtags)
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
            raw_text = data['candidates'][0]['content']['parts'][0]['text'].strip()

            if raw_text.startswith("```json"):
                raw_text = raw_text[7:]
            if raw_text.startswith("```"):
                raw_text = raw_text[3:]
            if raw_text.endswith("```"):
                raw_text = raw_text[:-3]

            return json.loads(raw_text.strip())

        except Exception as e:
            err_msg = str(e)
            if hasattr(e, 'response') and e.response is not None:
                err_msg = e.response.text
            print(f"  Gemini Error (Attempt {attempt + 1}/3): {err_msg}")

            if attempt < 2:
                if "429" in err_msg or "503" in err_msg or "RESOURCE_EXHAUSTED" in err_msg or "UNAVAILABLE" in err_msg:
                    print("  Switching to gemini-2.0-flash for next attempt...")
                    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
                time.sleep(5 * (attempt + 1))
            else:
                return None


# ── 3. Grid image template ───────────────────────────────────

def create_grid_pin_image(theme_title, products):
    """
    Creates a "roundup" pin image (1000x1500) with up to 4 products
    laid out in a 2-column grid, each with its own price tag, under
    one shared theme title and CTA.
    """
    W, H = 1000, 1500
    canvas = Image.new("RGBA", (W, H), COLORS["bg_cream"])
    draw = ImageDraw.Draw(canvas)

    title_font = load_font("title", 50)
    price_font = load_font("badge", 26)
    cta_font   = load_font("cta", 28)

    MARGIN = 60

    accent_color = random.choice([
        COLORS["accent_coral"], COLORS["accent_sage"], COLORS["accent_gold"],
    ])
    draw.rectangle([(0, 0), (W, 6)], fill=accent_color)

    # ── Title ────────────────────────────────────────────────
    lines = wrap_text(theme_title.upper(), title_font, W - MARGIN * 2)
    lines = lines[:3]
    line_height = get_text_height("A", title_font) + 12

    y = 90
    for line in lines:
        w = title_font.getlength(line)
        draw.text(((W - w) / 2, y), line, font=title_font, fill=COLORS["text_dark"])
        y += line_height

    divider_y = y + 16
    div_w = 120
    draw.rectangle(
        [((W - div_w) // 2, divider_y), ((W + div_w) // 2, divider_y + 3)],
        fill=accent_color
    )

    # ── Grid of products ─────────────────────────────────────
    cta_h = 90
    grid_top = divider_y + 30
    grid_bottom = H - MARGIN - cta_h - 20

    cols = 2
    rows = math.ceil(len(products) / cols)
    gap = 24
    cell_w = (W - MARGIN * 2 - gap * (cols - 1)) / cols
    cell_h = (grid_bottom - grid_top - gap * (rows - 1)) / rows

    for i, product in enumerate(products):
        col = i % cols
        row = i // cols
        cx = MARGIN + col * (cell_w + gap)
        cy = grid_top + row * (cell_h + gap)

        draw_rounded_rect(
            draw,
            (int(cx), int(cy), int(cx + cell_w), int(cy + cell_h)),
            radius=18,
            fill=COLORS["bg_white"]
        )

        price = product.get("price")
        price_reserve = 46 if price else 12

        try:
            pr_resp = requests.get(product.get("image_url"), timeout=15)
            pr_resp.raise_for_status()
            pr = Image.open(BytesIO(pr_resp.content)).convert("RGBA")

            pad = 16
            avail_w = cell_w - pad * 2
            avail_h = cell_h - pad * 2 - price_reserve
            pr.thumbnail((int(avail_w), int(avail_h)), Image.Resampling.LANCZOS)

            px = cx + (cell_w - pr.width) / 2
            py = cy + pad + (avail_h - pr.height) / 2
            canvas.paste(pr, (int(px), int(py)), pr if pr.mode == "RGBA" else None)
        except Exception as e:
            print(f"  Grid item image failed for {product.get('asin')}: {e}")

        if price:
            price_text = f"£{int(price)}" if price == int(price) else f"£{price:.2f}"
            tw = price_font.getlength(price_text)
            th = get_text_height(price_text, price_font)
            bpad_x, bpad_y = 16, 8
            bw, bh = tw + bpad_x * 2, th + bpad_y * 2
            bx = cx + (cell_w - bw) / 2
            by = cy + cell_h - bh - 12

            draw_rounded_rect(
                draw,
                (int(bx), int(by), int(bx + bw), int(by + bh)),
                radius=int(bh // 2),
                fill=COLORS["badge_bg"]
            )
            draw.text(
                (bx + bpad_x, by + bpad_y - 2),
                price_text, font=price_font, fill=COLORS["badge_text"]
            )

    # ── Soft CTA pill (matches the single-product template) ─
    cta_text = "Shop the edit on Amazon"
    cta_pad_x, cta_pad_y = 34, 18
    ctw = cta_font.getlength(cta_text) + cta_pad_x * 2
    cth = get_text_height(cta_text, cta_font) + cta_pad_y * 2
    ctx = (W - ctw) / 2
    cty = H - MARGIN - cth

    draw_rounded_rect(
        draw,
        (int(ctx), int(cty), int(ctx + ctw), int(cty + cth)),
        radius=int(cth // 2),
        fill=COLORS["cta_bg"]
    )
    tw2 = cta_font.getlength(cta_text)
    draw.text(
        (ctx + (ctw - tw2) / 2, cty + cta_pad_y - 2),
        cta_text, font=cta_font, fill=COLORS["cta_text"]
    )

    draw.rectangle([(0, H - 4), (W, H)], fill=accent_color)

    out = BytesIO()
    canvas.convert("RGB").save(out, "JPEG", quality=92)
    return out.getvalue()


# ── 4. Save ──────────────────────────────────────────────────

def save_grid_pin(copy, products, board_id, link_url):
    row = {
        "title":          copy["title"],
        "description":    copy["description"],
        "alt_text":       copy.get("alt_text", ""),
        "keywords":       copy.get("keywords", []),
        "image_url":      copy["image_url"],
        "link_url":       link_url,
        "product_id":     products[0]["id"],  # primary item, for schema compatibility
        "board_id":       board_id,
        "template_style": "grid_roundup",
        "approved":       False,  # always manual review until this format is proven
        "posted":         False,
    }
    return supabase_post("pins", row)


# ── Main ─────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print(f"Grid Pin Generation (Roundup) — {datetime.now(timezone.utc)}")
    print("=" * 60)

    if not STOREFRONT_URL:
        print("!! AMAZON_STOREFRONT_URL not set — grid pins will fall back to")
        print("!! linking the first featured product instead of your storefront.")
        print("!! Set that secret/env var before relying on this in production.")

    has_errors = False

    try:
        groups = get_theme_groups()
        if not groups:
            print(f"No category has {MIN_GROUP}+ active products yet — nothing to build.")

        for category, products in groups[:MAX_GRIDS_PER_RUN]:
            print(f"\n--- {category} ({len(products)} items) ---")
            try:
                copy = generate_grid_copy(category, products)
                if not copy:
                    print("  Gemini failed — skipping this group.")
                    has_errors = True
                    continue

                img_bytes = create_grid_pin_image(copy["title"], products)
                copy["image_url"] = upload_image(img_bytes)

                link_url = STOREFRONT_URL or get_affiliate_url(
                    products[0]["asin"], products[0].get("affiliate_url")
                )
                board_id = BOARD_MAP.get(category.lower(), DEFAULT_BOARD)

                save_grid_pin(copy, products, board_id, link_url)
                print(f"  ✓ Saved grid pin: {copy['title'][:50]}")

                for p in products:
                    requests.patch(
                        f"{SUPABASE_URL}/rest/v1/products?id=eq.{p['id']}",
                        headers=SUPABASE_HEADERS,
                        json={"last_pinned_at": datetime.now(timezone.utc).isoformat()}
                    )

            except Exception as e:
                print(f"  ✗ Group failed: {e}")
                has_errors = True

    except Exception as e:
        print(f"FATAL ERROR: {e}")
        has_errors = True

    print("\nGrid generation complete.")
    if has_errors:
        print("!! TERMINATING WITH ERROR STATUS !!")
        sys.exit(1)
    else:
        sys.exit(0)