# Restaurant Menu Scraper

This is the scraping backend for a SaaS tool that helps restaurant owners monitor competitor pricing. Try the live app: **[menu-comparison-compass.lovable.app](https://menu-comparison-compass.lovable.app/)** (in Swedish)

---

## What the product does

Restaurant owners sign up and enter their restaurant plus a list of competitors. The tool automatically scrapes all their menus, matches comparable dishes, and shows a dashboard with where the owner is priced high or low relative to the competition. A daily cron job re-runs the scrapers and sends notifications when prices or menus change.

---

## Architecture

```
┌──────────────────────────────┐
│  Lovable (frontend + matching) │
│  menu-comparison-compass.lovable.app │
│                              │
│  • Signup / restaurant setup │
│  • Dish matching between     │
│    restaurants               │
│  • Pricing dashboard         │
└────────────┬─────────────────┘
             │ POST /scrape (restaurant + competitors)
             ▼
┌──────────────────────────────┐
│  Scraping API — this repo    │
│  Hosted on Render            │
│                              │
│  scraper.py  ← core logic    │
│  pipeline.py ← orchestration │
└────────────┬─────────────────┘
             │ writes results
             ▼
┌──────────────────────────────┐
│  Supabase (database)         │
│  Stores menus + price history│
└──────────────────────────────┘
```

The frontend (Lovable) handles signup, dish matching, and the dashboard. The scraping API (this repo, running on Render) handles all data collection. Results are stored in Supabase and read back by the frontend.

---

## How the scraper works

Given a restaurant URL, the scraper retrieves the menu regardless of how the page is built:

1. **PDF link on the page** — finds and downloads the PDF, extracts text and sends it to Claude Sonnet
2. **Static HTML** — parses HTML with BeautifulSoup, sends text to Claude Haiku
3. **JS-rendered page** — if static HTML yields too little, launches Playwright, navigates to the correct menu page and runs steps 1–2 on the rendered HTML

Claude decides which PDFs, tabs and nav elements lead to the dinner menu, and extracts the dishes into structured JSON.

**Output:** `Name`, `Price`, `Category`, `Description` per dish.

---

## Files

| File | Purpose |
|------|---------|
| `scraper.py` | Low-level scraping for a single URL |
| `pipeline.py` | Looks up URLs via SerpAPI, runs parallel scraping, writes multi-sheet Excel |
| `evaluate.py` | Computes precision/recall/F1 against ground-truth Excel files |
| `run_eval.py` | CLI wrapper for evaluate.py — runs one or all test cases |
| `requirements.txt` | Python dependencies |
| `EXPERIMENTS.md` | Experiment log with F1 history and learnings per iteration |

---

## Installation

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

### Environment variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `ANTHROPIC_API_KEY` | Always | Claude calls for extraction and navigation |
| `SERPAPI_KEY` | For `pipeline.py` | URL lookup via Google |
| `SCRAPER_API_KEY` | Optional | Fallback when blocked by Cloudflare |

---

## Usage

### Scrape a single restaurant

```bash
python scraper.py --url https://example-restaurant.com
python scraper.py --url https://example-restaurant.com --output result.xlsx --debug
```

### Scrape a prospect + competitors (pipeline)

```bash
python pipeline.py \
  --name "Ricordi" \
  --address "Kungsträdgårdsgatan 18, Stockholm" \
  --competitors "Calle P, Bistro Arsenalen, Restaurang Pava"
```

Output: `Ricordi.xlsx` with one sheet per restaurant.

---

## Evaluation

Test cases live in `tests/cases/<name>/` and contain:
- `payload.json` — list of restaurants and URLs to scrape
- `ground_truth.xlsx` — reference data to measure against (Name, Price, Category per restaurant)

```bash
# All test cases
python run_eval.py --all

# A specific test case
python run_eval.py --case "lilla ego"

# A specific restaurant within a test case
python run_eval.py --case tradition --only "Pelikan"
```

Results are written to `eval_report.json` and printed to the terminal with per-restaurant F1 and detailed diff tables.

### Test cases

| Test case | Restaurants | What it covers |
|-----------|-------------|----------------|
| `kommendoren` | Kommendören, Aubergine, Ted | Static HTML, hidden Elementor sections |
| `tradition` | Tradition, Tennstopet, Pelikan, Bistro Bestick | PDF menus, navigation to PDF link |
| `tranan` | Tranan, Tennstopet | Multi-step navigation to PDF |
| `lilla ego` | Lilla Ego, Haggans, ART, Vineriet | Mixed HTML + PDF, snacks categories |
| `Crispy kvarnholmen` | Crispy Pizza Bistro, Don Felice, Kvarnholmen | Deep navigation (3 hops to PDF) |
| `Hantverket` | Hantverket | — |

---

## Scraper internals

### Flow (`scraper.py`)

```
URL
 ├─ is PDF? → scrape_pdf_from_url() → Claude Sonnet extraction
 │
 ├─ scrape_static_html()
 │   ├─ find_pdf_url() → scrape_pdf_from_url()  (if PDF link found)
 │   └─ parse_menu_from_html() → extract_with_claude()  (Claude Haiku)
 │
 └─ if < 5 dishes: scrape_dynamic() (Playwright)
     ├─ at each nav step: find_pdf_url() on rendered HTML
     ├─ pick_next_action() → Claude Haiku picks next click
     └─ same PDF/HTML extraction as above
```

### Model selection

| Task | Model |
|------|-------|
| Extract menu from HTML | `claude-haiku-4-5` |
| Extract menu from PDF | `claude-sonnet-4-6` |
| Choose PDF link / nav element / tab | `claude-haiku-4-5` |

### Concurrency

- Max 1 Playwright instance at a time (`threading.Semaphore(1)`) — keeps RAM usage low on Render
- Max 5 concurrent Claude API calls (`threading.Semaphore(5)`) — avoids rate-limit errors under parallel pipeline load

---

## Accuracy

F1 score against ground truth drives iterative improvement of `scraper.py`. See `EXPERIMENTS.md` for full history. Current aggregates (iter 6):

| Test case | F1 |
|-----------|----|
| Kommendoren | 94.6% |
| Lilla ego | 94.3% |
| Tradition | 98.5% |
| Tranan | 94.1% |
| Crispy kvarnholmen | ~82% (LLM variance in PDF extraction) |
