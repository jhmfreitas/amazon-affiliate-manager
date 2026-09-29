import sys
import os
import requests
from time import sleep

# Add scripts directory to path to import
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', 'scripts'))

try:
    from score_products import get_amazon_signals
except ImportError as e:
    print(f"Error importing: {e}")
    sys.exit(1)

asins = [
    "B085ZV98JM", "B07PM43QKP", "B0CTKHGN9D", "B0CYSQG7C2", 
    "B0D3G6P95K", "B097RSBWBZ", "B0BKRVC6S6", "B0DDKN6Y32", 
    "B0FDWFYCRX", "B0FQ22WZRW"
]

session = requests.Session()
# Warm up
try:
    session.get("https://www.amazon.co.uk/", timeout=15)
    sleep(1)
except:
    pass

print("Testing BSR extraction on 10 ASINs...\n")

for asin in asins:
    print(f"--- ASIN: {asin} ---")
    signals = get_amazon_signals(asin, session=session)
    print(f"Result: {signals}\n")
    sleep(2)  # be nice to amazon
