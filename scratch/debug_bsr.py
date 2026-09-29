
import requests
import re
from bs4 import BeautifulSoup
import random
import time

def debug_bsr(asin):
    url = f"https://www.amazon.co.uk/dp/{asin}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        "Accept-Language": "en-GB,en;q=0.9",
    }
    
    print(f"Fetching {url}...")
    resp = requests.get(url, headers=headers)
    print(f"Status: {resp.status_code}")
    print(f"Response Length: {len(resp.text)}")
    
    if "api-services-support@amazon.com" in resp.text:
        print("!!! BLOCKED BY CAPTCHA !!!")
        return
    
    soup = BeautifulSoup(resp.text, "html.parser")
    
    # 1. Look for common labels
    labels = ["Best Sellers Rank", "Bestsellers Rank", "Product details"]
    for l in labels:
        found = soup.find(string=re.compile(l, re.I))
        if found:
            print(f"Found Label: '{l}'")
            # Print surrounding text
            parent = found.find_parent()
            if parent:
                print(f"Context: {parent.get_text()[:200]}...")
    
    # 2. Try the new Method 4 logic
    text = soup.get_text()
    patterns = [
        r"([0-9,]+)\s+in\s+[A-Za-z\s&]+",
        r"Rank\s+#?([0-9,]+)",
        r"#[0-9,]+\s+in\s+[A-Za-z\s&]+"
    ]
    print("\nTesting Regex Patterns:")
    for p in patterns:
        matches = re.finditer(p, text)
        for m in matches:
            print(f"  Pattern '{p}' matched: '{m.group(0)}'")

if __name__ == "__main__":
    debug_bsr("B07PM43QKP")
