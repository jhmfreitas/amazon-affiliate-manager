"""
post_pin.py
───────────
Runs daily (10am UTC) via GitHub Actions.

1. Gets next approved, unposted pin from Supabase
2. Posts it to Pinterest (with auto token refresh on 401)
3. Marks it as posted in Supabase
"""

import os
import requests
from datetime import datetime, timezone
from pinterest_auth import PinterestAuth

# ── Secrets ──────────────────────────────────────────────────
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
BOARD_ID     = os.environ["PINTEREST_BOARD_ID"]

SUPABASE_HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "return=representation"
}

PINTEREST_API = "https://api.pinterest.com/v5"


# ── Supabase helpers ─────────────────────────────────────────

def get_next_pin():
    """Get the oldest approved, unposted pin from Supabase."""
    resp = requests.get(
        f"{SUPABASE_URL}/rest/v1/pins",
        headers=SUPABASE_HEADERS,
        params={
            "approved": "eq.true",
            "posted":   "eq.false",
            "order":    "created_at.asc",
            "limit":    "1"
        }
    )
    resp.raise_for_status()
    pins = resp.json()
    return pins[0] if pins else None


def mark_posted(pin_id, pinterest_pin_id):
    """Mark a pin as posted in Supabase."""
    resp = requests.patch(
        f"{SUPABASE_URL}/rest/v1/pins?id=eq.{pin_id}",
        headers=SUPABASE_HEADERS,
        json={
            "posted":       True,
            "pinterest_id": pinterest_pin_id,
            "posted_at":    datetime.now(timezone.utc).isoformat()
        }
    )
    resp.raise_for_status()
    print(f"Pin {pin_id} marked as posted.")


# ── Pinterest posting ─────────────────────────────────────────

def build_payload(pin):
    """Build the Pinterest pin payload from a Supabase pin row."""
    tags      = " ".join(f"#{t.lstrip('#')}" for t in (pin.get("hashtags") or []))
    full_desc = f"{pin['description']}\n\n{tags}".strip()
    link_url  = pin.get("link_url", "")

    payload = {
        "board_id":    BOARD_ID,
        "title":       pin["title"][:100],
        "description": full_desc[:500],
        "media_source": {
            "source_type": "image_url",
            "url":         pin["image_url"]
        }
    }

    # Only add link if one exists — Pinterest rejects empty link field
    if link_url:
        payload["link"] = link_url

    return payload


# ── Main ─────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print(f"Daily pin post — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 60)

    # 1. Get next pin from queue
    pin = get_next_pin()
    if not pin:
        print("No approved pins in queue. Nothing posted today.")
        print("Go to your review app and approve some pins.")
        exit(0)

    print(f"Next pin: id={pin['id']} — {pin['title'][:70]}...")
    print(f"Link URL: {pin.get('link_url') or '(none)'}")

    # 2. Validate image URL
    if not pin.get("image_url"):
        print(f"Pin {pin['id']} has no image URL — skipping.")
        exit(1)

    # 3. Build payload
    payload = build_payload(pin)

    # 4. Post to Pinterest — auto-refresh on 401 handled by PinterestAuth
    print("Posting to Pinterest...")
    auth = PinterestAuth()
    resp = auth.post(f"{PINTEREST_API}/pins", json=payload)

    if not resp.ok:
        print(f"Pinterest error {resp.status_code}: {resp.text}")
        resp.raise_for_status()

    result       = resp.json()
    pinterest_id = result.get("id", "unknown")
    print(f"Posted! Pinterest pin ID: {pinterest_id}")
    print(f"Pin URL: https://pinterest.com/pin/{pinterest_id}/")

    # 5. Mark as posted in Supabase
    mark_posted(pin["id"], pinterest_id)
    print("Done.")