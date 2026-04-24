import os
import base64
import requests

# ── Secrets (all from GitHub Actions secrets) ────────────────
PINTEREST_TOKEN         = os.environ["PINTEREST_TOKEN"]
PINTEREST_REFRESH_TOKEN = os.environ["PINTEREST_REFRESH_TOKEN"]
PINTEREST_APP_ID        = os.environ["PINTEREST_APP_ID"]
PINTEREST_APP_SECRET    = os.environ["PINTEREST_APP_SECRET"]
SUPABASE_URL            = os.environ["SUPABASE_URL"]
SUPABASE_KEY            = os.environ["SUPABASE_KEY"]
BOARD_ID                = os.environ["PINTEREST_BOARD_ID"]

SUPABASE_HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "return=representation"
}

# ── Token management ─────────────────────────────────────────

def refresh_access_token():
    """
    Use the refresh token to get a new access token.
    Pinterest access tokens last 30 days.
    Refresh tokens last 1 year.
    """
    print("Refreshing Pinterest access token...")

    credentials = base64.b64encode(
        f"{PINTEREST_APP_ID}:{PINTEREST_APP_SECRET}".encode()
    ).decode()

    resp = requests.post(
        "https://api.pinterest.com/v5/oauth/token",
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type":  "application/x-www-form-urlencoded"
        },
        data={
            "grant_type":    "refresh_token",
            "refresh_token": PINTEREST_REFRESH_TOKEN,
            "scope":         "pins:write,pins:read,boards:read,boards:write,user_accounts:read"
        }
    )

    if not resp.ok:
        print(f"Token refresh failed: {resp.status_code} {resp.text}")
        resp.raise_for_status()

    data = resp.json()
    new_token = data["access_token"]

    # If Pinterest also returned a new refresh token, log it
    # You will need to manually update PINTEREST_REFRESH_TOKEN
    # in GitHub Secrets if it changes (happens near the 1-year expiry)
    if "refresh_token" in data:
        new_refresh = data["refresh_token"]
        if new_refresh != PINTEREST_REFRESH_TOKEN:
            print("=" * 60)
            print("NEW REFRESH TOKEN ISSUED — update GitHub Secret:")
            print(f"PINTEREST_REFRESH_TOKEN = {new_refresh}")
            print("=" * 60)

    print("Token refreshed successfully.")
    return new_token


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
            "pinterest_id": pinterest_pin_id
        }
    )
    resp.raise_for_status()
    print(f"Pin {pin_id} marked as posted in Supabase.")


# ── Pinterest posting ─────────────────────────────────────────

def build_payload(pin):
    """Build the Pinterest pin payload from a Supabase pin row."""
    tags       = " ".join(f"#{t.lstrip('#')}" for t in (pin.get("hashtags") or []))
    full_desc  = f"{pin['description']}\n\n{tags}".strip()
    link_url   = pin.get("link_url", "")

    payload = {
        "board_id":    BOARD_ID,
        "title":       pin["title"][:100],
        "description": full_desc[:500],
        "media_source": {
            "source_type": "image_url",
            "url":         pin["image_url"]
        }
    }

    # Only add link if one is set — Pinterest rejects empty link fields
    if link_url:
        payload["link"] = link_url

    return payload


def post_pin(payload, token):
    """
    POST a pin to Pinterest.
    Returns (response_json, token_used).
    On 401, refreshes token once and retries automatically.
    """
    def do_post(t):
        return requests.post(
            "https://api.pinterest.com/v5/pins",
            headers={
                "Authorization": f"Bearer {t}",
                "Content-Type":  "application/json"
            },
            json=payload
        )

    resp = do_post(token)

    # Auto-refresh on 401 and retry once
    if resp.status_code == 401:
        print("Got 401 — access token expired. Refreshing and retrying...")
        token = refresh_access_token()
        resp  = do_post(token)

    # Surface the real Pinterest error message if it still fails
    if not resp.ok:
        print(f"Pinterest error {resp.status_code}: {resp.text}")
        resp.raise_for_status()

    return resp.json(), token


# ── Main ─────────────────────────────────────────────────────

if __name__ == "__main__":

    # 1. Get next pin from queue
    pin = get_next_pin()

    if not pin:
        print("No approved pins in queue. Nothing posted today.")
        print("Go to your review app and approve some pins.")
        exit(0)

    print(f"Next pin: id={pin['id']} — {pin['title'][:70]}...")

    # 2. Validate image URL exists
    if not pin.get("image_url"):
        print(f"Pin {pin['id']} has no image URL. Skipping.")
        exit(1)

    # 3. Build payload
    payload = build_payload(pin)
    print(f"Link URL: {payload.get('link', '(none)')}")

    # 4. Post to Pinterest (with auto token refresh on 401)
    print("Posting to Pinterest...")
    result, _ = post_pin(payload, PINTEREST_TOKEN)

    pinterest_id = result.get("id", "unknown")
    print(f"Posted successfully. Pinterest pin ID: {pinterest_id}")
    print(f"Pin URL: https://pinterest.com/pin/{pinterest_id}/")

    # 5. Mark as posted in Supabase
    mark_posted(pin["id"], pinterest_id)

    print("Done.")