import os, requests

PINTEREST_TOKEN = os.environ["PINTEREST_TOKEN"]
SUPABASE_URL    = os.environ["SUPABASE_URL"]
SUPABASE_KEY    = os.environ["SUPABASE_KEY"]
BOARD_ID        = os.environ["PINTEREST_BOARD_ID"]

SUPABASE_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation"
}


def get_next_pin():
    """Get oldest approved, unposted pin."""
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
    resp = requests.patch(
        f"{SUPABASE_URL}/rest/v1/pins?id=eq.{pin_id}",
        headers=SUPABASE_HEADERS,
        json={"posted": True, "pinterest_id": pinterest_pin_id}
    )
    resp.raise_for_status()


def post_to_pinterest(pin):
    tags = " ".join(f"#{t.lstrip('#')}" for t in pin["hashtags"])
    description = f"{pin['description']}\n\n{tags}"

    resp = requests.post(
        "https://api.pinterest.com/v5/pins",
        headers={
            "Authorization": f"Bearer {PINTEREST_TOKEN}",
            "Content-Type": "application/json"
        },
        json={
            "board_id":    BOARD_ID,
            "title":       pin["title"][:100],
            "description": description[:500],
            "media_source": {
                "source_type": "image_url",
                "url": pin["image_url"]
            }
        }
    )
    resp.raise_for_status()
    return resp.json()


if __name__ == "__main__":
    pin = get_next_pin()

    if not pin:
        print("No approved pins in queue. Nothing posted today.")
        print("Go approve some pins in your review app.")
        exit(0)

    print(f"Posting pin id={pin['id']}: {pin['title'][:60]}...")
    result = post_to_pinterest(pin)
    pinterest_id = result.get("id", "unknown")

    mark_posted(pin["id"], pinterest_id)
    print(f"Posted. Pinterest pin id: {pinterest_id}")
