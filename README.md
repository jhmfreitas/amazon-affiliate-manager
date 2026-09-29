7:00am — score_products.py runs
         scrapes BSR, pulls Google Trends, reads Pinterest saves
         Gemini scores all products → saves to Supabase

9:00am — generate_pins.py runs
         reads top-scored product
         gets affiliate URL from Creators API
         generates 7 slots × 10 candidates = 70 pins
         keeps top 3 per slot = 21 pins saved to Supabase
         all with your affiliate link embedded

You open review app Monday afternoon
         approve the best pins
         system posts one per day automatically all week



# MISSING

Generate affiliate link
Search and create products automatically
Refine query in google trends, we cannot put directly the name of Amazon


1. Discovery engine
2. Scoring engine
3. Niche clustering
4. Content variation engine
5. Image generation pipeline
6. A/B testing loop


## Price refresh

`refresh_prices.yml` runs every Wednesday at 05:30 UTC and can also be started
manually. It reads active products from Supabase and refreshes only prices that
are visible as valid GBP offers on Amazon UK product pages. Manual runs start in
preview mode by default; set `dry_run` to `false` to apply the reported changes.
The workflow needs only `SUPABASE_URL` and `SUPABASE_KEY`, not Creators API
credentials.
