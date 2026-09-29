import os, sys
os.environ['SUPABASE_URL'] = 'mock'
os.environ['SUPABASE_KEY'] = 'mock'
sys.path.insert(0, 'scripts')
import requests, re, json
from bs4 import BeautifulSoup
from config import get_amazon_cookies, random_headers

url = 'https://www.amazon.co.uk/dp/B0GHPHP3DP'
headers = random_headers()
headers['Referer'] = 'https://www.amazon.co.uk/s?k=B0GHPHP3DP'
r = requests.get(url, headers=headers, cookies=get_amazon_cookies(), timeout=15)
soup = BeautifulSoup(r.text, 'html.parser')

print('--- Test 1: .aok-offscreen ---')
for tag in soup.select('.aok-offscreen'):
    print(' ', repr(tag.get_text(' ', strip=True)))

print('\n--- Test 2: hidden inputs for customerVisiblePrice ---')
for inp in soup.select('input[name*="customerVisiblePrice"], input[name*="price" i]'):
    print(' ', inp.get('name'), '->', repr(inp.get('value')))

print('\n--- Test 3: twister-plus-buying-options-price-data ---')
for block in soup.select('[class*="twister-plus-buying-options-price-data"], [data-a-state*="twister"]'):
    print(' ', block.get_text(strip=True)[:150])

print('\n--- Test 4: apex-pricetopay-value or reinventPricePriceToPayMargin ---')
for tag in soup.select('.apex-pricetopay-value, .reinventPricePriceToPayMargin, .priceToPay'):
    print(' ', tag.get('class'), '->', repr(tag.get_text(' ', strip=True)))
