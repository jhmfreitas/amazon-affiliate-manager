import os
from amazon_creatorsapi import AmazonCreatorsApi, Country
from config import AMAZON_CRED_ID, AMAZON_CRED_SEC, AMAZON_TAG

api = AmazonCreatorsApi(
    credential_id=AMAZON_CRED_ID,
    credential_secret=AMAZON_CRED_SEC,
    version="2.2",
    tag=AMAZON_TAG,
    country="GB",
    throttling=1
)

print("Searching Amazon API for 'aesthetic white standing desk'...")
try:
    # Need to specify resources to get images and prices
    from amazon_creatorsapi.models import SearchItemsResource
    resources = [
        SearchItemsResource.ITEMINFO_TITLE,
        SearchItemsResource.OFFERS_LISTINGS_PRICE,
        SearchItemsResource.IMAGES_PRIMARY_LARGE,
        SearchItemsResource.IMAGES_PRIMARY_HIGHRES
    ]
    
    result = api.search_items(
        keywords="aesthetic white standing desk",
        item_count=3,
        resources=resources
    )
    
    for item in result.items:
        print(f"\nASIN: {item.asin}")
        if item.item_info and item.item_info.title:
            print(f"Title: {item.item_info.title.display_value}")
        
        if item.images and item.images.primary:
            if item.images.primary.high_res:
                print(f"HighRes: {item.images.primary.high_res.url}")
            elif item.images.primary.large:
                print(f"Large: {item.images.primary.large.url}")
            else:
                print("No large image found")
        
        if item.offers and item.offers.listings:
            price = item.offers.listings[0].price
            print(f"Price: {price.display_amount}")
        
except Exception as e:
    print(f"Error: {e}")
