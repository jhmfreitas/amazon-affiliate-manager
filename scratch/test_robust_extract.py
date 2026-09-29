import os, sys
os.environ['SUPABASE_URL'] = 'mock'
os.environ['SUPABASE_KEY'] = 'mock'
sys.path.insert(0, 'scripts')
import requests, re, json
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from bs4 import BeautifulSoup
from config import get_amazon_cookies, random_headers

GBP_PRICE_PATTERN = re.compile(r"\u00a3\s*([\d,]+(?:\.\d{1,2})?)")
MIN_PRICE = Decimal("0.50")
MAX_PRICE = Decimal("5000.00")

def robust_extract_price(soup):
    # 1. Hidden form inputs (Amazon's add-to-cart / checkout data - most reliable)
    amt_input = soup.select_one('input[name*="customerVisiblePrice"][name*="amount"]')
    cur_input = soup.select_one('input[name*="customerVisiblePrice"][name*="currencyCode"]')
    if amt_input and amt_input.get("value"):
        cur = (cur_input.get("value") if cur_input else "GBP").strip().upper()
        if cur == "GBP":
            try:
                p = Decimal(amt_input["value"].replace(",", ""))
                if MIN_PRICE <= p <= MAX_PRICE:
                    return p.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            except InvalidOperation:
                pass

    val_input = soup.select_one('input[name="priceValue"]')
    sym_input = soup.select_one('input[name="priceSymbol"]')
    if val_input and val_input.get("value"):
        sym = (sym_input.get("value") if sym_input else "£").strip()
        if sym in ("£", "&pound;", "\u00a3", "GBP"):
            try:
                p = Decimal(val_input["value"].replace(",", ""))
                if MIN_PRICE <= p <= MAX_PRICE:
                    return p.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            except InvalidOperation:
                pass

    # 2. Accessibility offscreen spans (.aok-offscreen and .a-offscreen)
    for sel in (".aok-offscreen", ".a-offscreen"):
        for tag in soup.select(sel):
            t = tag.get_text(" ", strip=True)
            m = GBP_PRICE_PATTERN.search(t)
            if m:
                try:
                    p = Decimal(m.group(1).replace(",", ""))
                    if MIN_PRICE <= p <= MAX_PRICE:
                        return p.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                except InvalidOperation:
                    continue

    # 3. Visible price structures
    for sel in (".priceToPay", ".apex-pricetopay-value", "#priceblock_ourprice", "#priceblock_dealprice"):
        for tag in soup.select(sel):
            whole = tag.select_one(".a-price-whole")
            if whole:
                w_str = whole.get_text(strip=True).rstrip(".")
                fraction = tag.select_one(".a-price-fraction")
                f_str = fraction.get_text(strip=True) if fraction else "00"
                sym = tag.select_one(".a-price-symbol")
                sym_str = sym.get_text(strip=True) if sym else "£"
                if "£" in sym_str:
                    try:
                        p = Decimal(f"{w_str}.{f_str}")
                        if MIN_PRICE <= p <= MAX_PRICE:
                            return p.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                    except InvalidOperation:
                        continue
    return None

headers = random_headers()
headers['Referer'] = 'https://www.amazon.co.uk/'
cookies = get_amazon_cookies()

for asin in ['B0GHPHP3DP', 'B0GHPM5MNQ']:
    url = f'https://www.amazon.co.uk/dp/{asin}'
    r = requests.get(url, headers=headers, cookies=cookies, timeout=15)
    soup = BeautifulSoup(r.text, 'html.parser')
    p = robust_extract_price(soup)
    print(f'{asin} -> {p}')
