"""Debug: show ALL price elements on an Amazon product page."""
import re, requests
from bs4 import BeautifulSoup

asin = "B0C59CXZVX"
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept-Language": "en-GB,en;q=0.9",
}

resp = requests.get(f"https://www.amazon.co.uk/dp/{asin}", headers=headers, timeout=10)
soup = BeautifulSoup(resp.text, "html.parser")

print(f"Page title: {soup.title.text.strip()[:80] if soup.title else 'none'}")
print(f"Response length: {len(resp.text)} chars\n")

# Find ALL elements that contain a £ sign
print("=== ALL £ PRICE ELEMENTS ===")
for tag in soup.find_all(True):
    text = tag.get_text(strip=True)
    if "£" in text and len(text) < 50 and re.search(r"£[\d]", text):
        classes = " ".join(tag.get("class", []))
        parent = tag.parent
        parent_classes = " ".join(parent.get("class", [])) if parent else ""
        parent_id = parent.get("id", "") if parent else ""
        print(f'  <{tag.name} class="{classes}"> => "{text}"')
        print(f'    parent: <{parent.name} id="{parent_id}" class="{parent_classes[:60]}">')
        print()

# Specifically check core price display
print("\n=== CORE PRICE DISPLAY ===")
core = soup.select_one("#corePriceDisplay_desktop_feature_div")
if core:
    for tag in core.find_all(True):
        text = tag.get_text(strip=True)
        classes = " ".join(tag.get("class", []))
        if text and len(text) < 50:
            print(f'  <{tag.name} class="{classes}"> => "{text}"')
else:
    print("  Not found")

# Check apex price
print("\n=== APEX PRICE ===")
apex = soup.select_one("#apex_desktop")
if apex:
    for span in apex.find_all("span"):
        text = span.get_text(strip=True)
        classes = " ".join(span.get("class", []))
        if "£" in text and len(text) < 30:
            print(f'  <span class="{classes}"> => "{text}"')
else:
    print("  Not found")
