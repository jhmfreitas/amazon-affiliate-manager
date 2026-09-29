import os, sys
os.environ['SUPABASE_URL'] = 'mock'
os.environ['SUPABASE_KEY'] = 'mock'
sys.path.insert(0, 'scripts')
import requests
from bs4 import BeautifulSoup
from config import get_amazon_cookies, random_headers

url = 'https://www.amazon.co.uk/dp/B0GHPHP3DP'
headers = random_headers()
headers['Referer'] = 'https://www.amazon.co.uk/s?k=B0GHPHP3DP'

r = requests.get(url, headers=headers, cookies=get_amazon_cookies(), timeout=15)
soup = BeautifulSoup(r.text, 'html.parser')

print('Length:', len(r.text))
print('Title:', soup.title.get_text() if soup.title else None)

# Check all elements that have 'price' in id or class
price_elements = []
for el in soup.find_all(attrs={'class': lambda c: c and any('price' in x.lower() for x in c)}):
    t = el.get_text(' ', strip=True)
    if t and len(t) < 80:
        cls_str = " ".join(el.get("class", []))
        price_elements.append(f'{el.name}.{cls_str} -> {repr(t)}')

print(f'Total price class elements: {len(price_elements)}')
for p in price_elements[:15]:
    print(' ', p)
