# Serention Indices — static site

A dependency-free static site for the Serention index family. The product-family
landing page (`index.html`) lists every index; the generic per-index page
(`product.html?slug=<slug>`) renders KPIs + interactive charts from a JSON
snapshot in `data/`. Adding a new index = add a `data/<slug>.json` + a row in
`data/products.json`. No build step, no backend, no database exposed.

```
web/
  index.html            product-family landing
  product.html          generic per-index page (?slug=auto-subprime)
  assets/               style.css, landing.js, product.js (+ Chart.js via CDN)
  data/                 generated JSON snapshots (auto-subprime.json, products.json)
  build_site_data.py    regenerates data/ from csv/ + Inputs/fed/
```

## Refresh the data (after a monthly index rebuild)
```
python web/build_site_data.py      # rewrites web/data/*.json from the latest CSVs
```
Then redeploy (or `git push` if the host auto-builds). The site reads only local
JSON, so it never touches Supabase.

## Preview locally
```
cd web && python -m http.server 8000
# open http://localhost:8000
```

## Deploy to serention.com (recommended: Cloudflare Pages)
The site is plain static files, so any static host works. Cloudflare Pages free tier:

1. Push this repo to GitHub (the `web/` folder is the site root).
2. Cloudflare dashboard → **Workers & Pages → Create → Pages → Connect to Git** →
   pick the repo.
3. Build settings: **Framework preset = None**, **Build command = (blank)**,
   **Build output directory = `web`**. Deploy.
4. **Custom domains → Set up a custom domain → `serention.com`**. Cloudflare adds
   the DNS records (if the domain's nameservers are on Cloudflare it's automatic;
   otherwise add the shown CNAME/A records at your registrar).
5. Retire the old WordPress: once DNS points at Pages, the WP site is no longer
   served. (Export/back it up first if you want it.)

**Netlify / Vercel** are equivalent: set the publish/output directory to `web`,
no build command, then add `serention.com` as a custom domain and update DNS.

## Notes
- Charts use Chart.js + the annotation plugin from jsDelivr CDN (no install).
- Borrower counts are estimates (issuance count × balance run-off) until the
  loan-level table backfill completes; `build_site_data.py` will use exact counts
  automatically once they're in the CSVs.
