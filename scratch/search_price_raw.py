import os, sys
os.environ['SUPABASE_URL'] = 'mock'
os.environ['SUPABASE_KEY'] = 'mock'
sys.path.insert(0, 'scripts')
import requests
from bs4 import BeautifulSoup
import re
from config import get_amazon_cookies, random_headers

url = 'https://www.amazon.co.uk/dp/B0GHPHP3DP'
headers = random_headers()
headers['Referer'] = 'https://www.amazon.co.uk/s?k=B0GHPHP3DP'

r = requests.get(url, headers=headers, cookies=get_amazon_cookies(), timeout=15)

print('--- Searching for 19.99 or 23.99 ---')
matches = [m.start() for m in re.finditer(r'(?:19\.99|23\.99)', r.text)]
print(f'Found {len(matches)} occurrences of 19.99 or 23.99:')
for idx in matches[:5]:
    snippet = r.text[max(0, idx - 100):min(len(r.text), idx + 100)]
    print(' Snippet:', repr(snippet.replace('\n', ' ')))

print('\n--- Searching for £ / &pound; / \u00a3 ---')
gbp_matches = [m.start() for m in re.finditer(r'(?:£|&pound;|\u00a3)', r.text)]
print(f'Found {len(gbp_matches)} occurrences of GBP symbol:')
for idx in gbp_matches[:5]:
    snippet = r.text[max(0, idx - 50):min(len(r.text), idx + 50)]
    print(' Snippet:', repr(snippet.replace('\n', ' ')))

print('\n--- Searching for EUR / € ---')
eur_matches = [m.start() for m in re.finditer(r'(?:EUR|€)', r.text)]
print(f'Found {len(eur_matches)} occurrences of EUR symbol:')
for idx in eur_matches[:5]:
    snippet = r.text[max(0, idx - 50):min(len(r.text), idx + 50)]
    print(' Snippet:', repr(snippet.replace('\n', ' ')))
