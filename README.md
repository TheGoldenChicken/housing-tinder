# Housing Tinder 🏠

Swipe through Danish homes like a dating app. A scraper collects every current listing from the
major Danish housing sites, sorts them into **for sale**, **for rent** and **andelsbolig**, and
publishes them as static JSON. A phone-friendly web app (installable PWA) filters the listings by
your requirements and shows them one card at a time. Swipe right and the listing with all its
details and links lands in a Google Sheet for later review; swipe left and it is gone for good.

Built for a couple of users, no server required: GitHub Actions runs the scraper on a schedule and
GitHub Pages hosts the app and the data.

```
┌──────────────┐  cron   ┌──────────────┐  deploy  ┌──────────────┐   fetch   ┌──────────┐
│ Danish sites │ ──────▶ │ scraper (uv) │ ───────▶ │ GitHub Pages │ ◀──────── │ PWA on   │
│ 8 sources    │         │ GitHub Action│          │ app + data/  │           │ phone    │
└──────────────┘         └──────────────┘          └──────────────┘           └────┬─────┘
                                                                                   │ swipes
                                                                            ┌──────▼─────┐
                                                                            │ Google     │
                                                                            │ Sheet      │
                                                                            └────────────┘
```

## Sources

| Source | Categories | Method | Notes |
|---|---|---|---|
| boligsiden.dk | sale, andel, rent | JSON API | Biggest sale aggregator; also lists realtor rentals |
| boliga.dk | sale, andel | JSON API | Includes self-sale listings |
| boligportal.dk | rent | JSON API | Biggest rental site (~12k). Cloudflare cookie + CSRF dance, 18 per page |
| findbolig.nu | rent | JSON API | Non-profit housing associations, advertised units only |
| edc.dk | rent | JSON API | EDC's own rental cases |
| home.dk | rent | JSON API | home's rental cases |
| lejebolig.dk | rent | HTML | ~10k listings; zip/coords only on detail pages (fetched gradually) |
| boligzonen.dk | rent | HTML + JSON | ~9k listings; zip/coords only on detail pages (fetched gradually) |

Summer houses, allotment houses (kolonihaver), plots, parking and business premises are excluded
at the source. The same home found on several sites is merged into one listing with links to all of
them. Investigated and skipped: boligdeal.dk (paywalled aggregator with rounded values and no photos),
nybolig.dk and danbolig.dk (no public rental feed found).

## Setup

### 1. Create the GitHub repository

```bash
cd housing_tinder
git init && git add . && git commit -m "Housing tinder"
gh repo create housing-tinder --private --source . --push     # or create it in the browser and push
```

In the repository settings → **Pages** → *Build and deployment* → Source: **GitHub Actions**.

### 2. Run the first scrape

Actions → **Scrape and deploy** → *Run workflow*. The first full run takes 30–45 minutes (around
1,500 polite requests). When it finishes, the app is live at `https://<you>.github.io/housing-tinder/`.

The schedule is in [.github/workflows/scrape.yml](.github/workflows/scrape.yml): the default is
daily at 04:00 UTC. Change the cron line for weekly (`0 4 * * 1`) or monthly (`0 4 1 * *`).

The workflow deploys a fresh snapshot each run; nothing is committed to git. `first_seen` dates and
per-source caches survive because the scraper reads the previous snapshot from the live site before
starting.

### 3. Create the Google Sheet (optional but recommended)

1. Create a new Google Sheet. **Extensions → Apps Script**, replace the code with
   [sheets/Code.gs](sheets/Code.gs), save.
2. **Deploy → New deployment → Web app**. *Execute as:* Me. *Who has access:* Anyone. Deploy and
   copy the web-app URL (ends with `/exec`).
3. Open the deployed app in a browser (your GitHub Pages URL, on the phone or a computer). Tap the
   **⋯** button in the top-right corner to open *Settings*, paste the URL as *Google Sheet endpoint*,
   enter your name, and optionally the sheet's own URL for a quick link. Save.

   Settings live in that browser only, so repeat this on every device you use. Use the same name
   everywhere and your swipes stay in sync across devices through the sheet.

The script creates two tabs: **Likes** (one row per right-swipe with price, size, rooms, year,
bathrooms, energy label, links to every site it was found on, image, coordinates…) and **Swipes**
(every like/pass/unlike, used to sync between devices). Each user enters their own name, so two
people can share one sheet or use separate ones. Without an endpoint, swipes stay in the browser.

### 4. Install on the phone

Open the Pages URL in Safari/Chrome → *Share* → **Add to Home Screen**. It runs full-screen and
caches the app shell offline; listing data is always fetched fresh.

## Using the app

- **Tabs**: For sale / For rent / Andel — each has its own filters and queue.
- **⚙ Filters**: municipalities, zip codes (`2100, 2200-2300`), price or rent, monthly cost, size,
  rooms, property type, year built, own bathroom, balcony, elevator, pets, unlimited rental period,
  max floor, lot size, energy label, how recently the listing appeared, and max distance from a point
  (paste coordinates from Google Maps or use your location). Choosing municipalities also makes the
  app download only those files, which keeps it fast.
- **Swipe** right / ♥ to like, left / ✕ to pass, ↶ to undo. Tap the photo edges to flip through
  pictures, tap the description to expand it. Arrow keys work on desktop.
- **♥ Likes**: review liked listings, remove likes, export CSV, open the sheet.
- Listings first seen in the last three days carry a **NEW** badge.

## Running the scraper locally

```bash
uv sync
uv run scrape --out data --max-pages 2                 # quick test of every source
uv run scrape --out data --sources boligsiden,boliga   # only some sources
uv run scrape --out data --state-url https://<you>.github.io/housing-tinder/data   # keep first_seen
uv run pytest
```

Serve the app locally with `uv run python -m http.server -d . 8000` and open
`http://localhost:8000/app/` with *Data* set to `../data` in settings.

Configuration lives in [config.yaml](config.yaml): which sources run, request delay, and the per-run
detail budget for the two HTML sites.

## Data layout

```
data/index.json               generated_at, counts, per-source status, municipalities with counts, file map
data/<cat>/<municipality>.json  listings for one category + municipality (0 = unknown municipality)
data/<cat>/all.json           everything in one category
data/seen.json                id → first/last seen dates (state between runs)
data/zip_municipality.json    learned zip → municipality map (DAWA was retired, so this is self-built)
data/cache/<source>.json      per-source detail cache (lejebolig, boligzonen)
```

Every listing is a flat JSON object: `id, category, property_type, url, address, zip_code, city,
municipality, municipality_code, lat, lon, price, monthly_cost, deposit, prepaid_rent, size_m2,
lot_m2, rooms, floor, bathrooms, toilets, has_bathroom, year_built, energy_label, balcony, elevator,
terrace, pets_allowed, furnished, available_from, rental_period, listed_date, first_seen, last_seen,
images, description, realtor, sources, extra` (empty fields are omitted). `price` is the sale price,
or the monthly rent for rentals.

## Adding a source

Create `scraper/sources/<name>.py` with a class deriving from `BaseSource`, decorate it with
`@register`, implement `fetch()` yielding `Listing` objects, add the module name to
`scraper/sources/__init__.py` and to `config.yaml`. `self.http` is a polite curl_cffi session that
impersonates Chrome (several sites sit behind Cloudflare). `self.cache` persists between runs.

## Caveats

- Sites change their markup and APIs. Each source fails independently and the status is shown in the
  app's settings; check the Actions log when a source shows ✗.
- GitHub Actions runs from datacenter IPs. BoligPortal's Cloudflare protection has only been verified
  from a home connection; if it blocks the runner, run that source from a machine at home
  (`uv run scrape --sources boligportal --state-url …`) or drop it from `config.yaml`.
- lejebolig.dk hides its very newest listing from anonymous visitors; boligzonen and lejebolig only
  publish street names, not house numbers.
- The 1,000-result cap on EDC's API is far above its rental count today.
- Scrape responsibly: the defaults make roughly one request per second.
