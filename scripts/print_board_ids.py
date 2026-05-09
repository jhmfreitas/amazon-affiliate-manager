"""
print_board_ids.py
──────────────────
Lists all Pinterest boards and their IDs for the BOARD_MAP.
"""
from pinterest_auth import PinterestAuth

if __name__ == "__main__":
    auth = PinterestAuth()
    try:
        resp = auth.get("https://api.pinterest.com/v5/boards")
        resp.raise_for_status()
        boards = resp.json().get("items", [])
        
        print("\n" + "="*40)
        print("YOUR PINTEREST BOARD IDS")
        print("="*40)
        for b in boards:
            print(f"Name: {b['name']:<25} ID: {b['id']}")
        print("="*40 + "\n")
    except Exception as e:
        print(f"Error: {e}")
