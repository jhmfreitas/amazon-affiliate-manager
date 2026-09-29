"""
test_amazon_api.py
──────────────────
A simple script to test your Amazon Creators API credentials.
Run this directly from your terminal to verify your keys work.
"""

import os
import sys

# Attempt to load from python-amazon-paapi SDK
try:
    from amazon_creatorsapi import AmazonCreatorsApi
    from creatorsapi_python_sdk.exceptions import ApiException
except ImportError:
    print("❌ ERROR: python-amazon-paapi not installed.")
    print("Please run: pip install python-amazon-paapi")
    sys.exit(1)

def test_credentials():
    print("="*50)
    print("AMAZON CREATORS API CREDENTIAL TESTER")
    print("="*50)
    
    # 1. Get credentials from environment or prompt
    cred_id = os.environ.get("AMAZON_CREDENTIAL_ID")
    if not cred_id:
        cred_id = input("Enter your AMAZON_CREDENTIAL_ID: ").strip()
        
    cred_sec = os.environ.get("AMAZON_CREDENTIAL_SECRET")
    if not cred_sec:
        cred_sec = input("Enter your AMAZON_CREDENTIAL_SECRET: ").strip()
        
    tag = os.environ.get("AMAZON_ASSOCIATE_TAG")
    if not tag:
        tag = input("Enter your AMAZON_ASSOCIATE_TAG (e.g. mytag-21): ").strip()

    if not all([cred_id, cred_sec, tag]):
        print("\n❌ ERROR: All three values are required.")
        return

    versions_to_test = ["3.2", "2.2"]
    success = False
    
    for version in versions_to_test:
        print(f"\n⏳ Initializing API client with version {version}...")
        try:
            api = AmazonCreatorsApi(
                credential_id=cred_id,
                credential_secret=cred_sec,
                version=version,
                tag=tag,
                country="UK", # Set to UK as per your setup
                throttling=1
            )
            
            print(f"⏳ Attempting to search Amazon using version {version}...")
            
            # This will trigger the OAuth token request and the actual search
            result = api.search_items(
                keywords="standing desk",
                item_count=1
            )
            
            print(f"\n✅ SUCCESS! Credentials are valid with version {version}.")
            success = True
            
            if result.items:
                item = result.items[0]
                title = item.item_info.title.display_value if item.item_info and item.item_info.title else "Unknown"
                print(f"📦 Found item: {title}")
                print(f"🔗 ASIN: {item.asin}")
            else:
                print("⚠️ No items found, but authentication was successful.")
                
            break # Exit the loop if successful!
                
        except ApiException as e:
            if e.status == 400 and "invalid_client" in str(e.body):
                print(f"❌ Version {version} failed (invalid_client). Trying next...")
            else:
                print(f"\n❌ API Error on version {version}: {e.status} - {e.body}")
        except Exception as e:
            print(f"\n❌ UNEXPECTED ERROR on version {version}: {e}")

    if not success:
        print("\n💡 DIAGNOSIS: Amazon rejected your credentials on all UK endpoints.")
        print("Are you SURE these are 'Creators API' keys and not 'Product Advertising API (PA-API)' or 'Selling Partner API (SP-API)' keys?")

if __name__ == "__main__":
    test_credentials()
