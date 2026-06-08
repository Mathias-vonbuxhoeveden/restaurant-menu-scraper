# Restaurant Menu Scraper

Skrapar middagsmenyer från restaurangers webbsidor och exporterar dem till Excel. Används som underlag för restaurangprisövervakning.

---

## Vad det gör

Givet en restaurangs URL hämtar scrapern menyn oavsett hur sidan är uppbyggd:

1. **PDF-länk på sidan** — hittar och laddar ned PDF:en, extraherar text och skickar till Claude Sonnet
2. **Statisk HTML** — parsar HTML med BeautifulSoup, skickar text till Claude Haiku
3. **JS-renderad sida** — om statisk HTML ger för lite, startar Playwright, navigerar till rätt menysida och kör steg 1–2 på den rendrade HTML:en

Claude avgör vilka PDF-er, flikar och nav-element som leder till middagsmenyn, och extraherar rätterna till strukturerad JSON.

**Output:** Excel-fil med kolumnerna `Name`, `Price`, `Category`, `Description`.

---

## Filer

| Fil | Syfte |
|-----|-------|
| `scraper.py` | Lågnivå-skrapning för en enskild URL — den enda filen som ändras i optimeringsloopen |
| `pipeline.py` | Söker upp URL:er via SerpAPI, kör parallell skrapning, skriver multi-sheet Excel |
| `evaluate.py` | Beräknar precision/recall/F1 mot ground-truth Excel-filer |
| `run_eval.py` | CLI-wrapper för evaluate.py — kör ett eller alla testfall |
| `requirements.txt` | Python-beroenden |
| `EXPERIMENTS.md` | Experimentlogg med F1-historik och lärdomar per iteration |

---

## Installation

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

### Miljövariabler

| Variabel | Krävs | Syfte |
|----------|-------|-------|
| `ANTHROPIC_API_KEY` | Alltid | Claude-anrop för extraktion och navigering |
| `SERPAPI_KEY` | För `pipeline.py` | URL-sökning via Google |
| `SCRAPER_API_KEY` | Valfritt | Fallback vid Cloudflare-blockering |

---

## Användning

### Skrapa en enskild restaurang

```bash
python scraper.py --url https://restaurang.se
python scraper.py --url https://restaurang.se --output result.xlsx --debug
```

### Skrapa prospekt + konkurrenter (pipeline)

```bash
python pipeline.py \
  --name "Ricordi" \
  --address "Kungsträdgårdsgatan 18, Stockholm" \
  --competitors "Calle P, Bistro Arsenalen, Restaurang Pava"
```

Output: `Ricordi.xlsx` med en flik per restaurang.

---

## Utvärdering

Testfallen ligger i `tests/cases/<namn>/` och innehåller:
- `payload.json` — lista med restauranger och URL:er att skrapa
- `ground_truth.xlsx` — facit att mäta mot (Name, Price, Category per restaurang)

```bash
# Alla testfall
python run_eval.py --all

# Ett specifikt testfall
python run_eval.py --case lilla\ ego

# En specifik restaurang inom ett testfall
python run_eval.py --case tradition --only "Pelikan"
```

Resultatet skrivs till `eval_report.json` och skrivs ut i terminalen med per-restaurang F1 och detaljerade diff-tabeller.

### Testfall

| Testfall | Restauranger | Vad det testar |
|----------|-------------|----------------|
| `kommendoren` | Kommendören, Aubergine, Ted | Statisk HTML, dolda Elementor-sektioner |
| `tradition` | Tradition, Tennstopet, Pelikan, Bistro Bestick | PDF-menyer, nav till PDF-länk |
| `tranan` | Tranan, Tennstopet | Flerstegsnavigering till PDF |
| `lilla ego` | Lilla Ego, Haggans, ART, Vineriet | Blandad HTML + PDF, snacks-kategorier |
| `Crispy kvarnholmen` | Crispy Pizza Bistro, Don Felice, Kvarnholmen | Djup navigering (3 hopp till PDF) |
| `Hantverket` | Hantverket | — |

---

## Arkitektur

### Scraper-flöde (`scraper.py`)

```
URL
 ├─ är PDF? → scrape_pdf_from_url() → Claude Sonnet-extraktion
 │
 ├─ scrape_static_html()
 │   ├─ find_pdf_url() → scrape_pdf_from_url()  (om PDF-länk hittas)
 │   └─ parse_menu_from_html() → extract_with_claude()  (Claude Haiku)
 │
 └─ om < 5 rätter: scrape_dynamic() (Playwright)
     ├─ vid varje nav-steg: find_pdf_url() på rendrad HTML
     ├─ pick_next_action() → Claude Haiku väljer nästa klick
     └─ samma PDF/HTML-extraktion som ovan
```

### Modellval

| Uppgift | Modell |
|---------|--------|
| Extrahera meny från HTML | `claude-haiku-4-5` |
| Extrahera meny från PDF | `claude-sonnet-4-6` |
| Välj PDF-länk / nav-element / flik | `claude-haiku-4-5` |

### Concurrency

- Max 1 Playwright-instans åt gången (`threading.Semaphore(1)`) — håller nere RAM
- Max 5 parallella Claude API-anrop (`threading.Semaphore(5)`) — undviker 529-fel vid parallell pipeline-körning

---

## Optimeringsloop

F1-score mot ground truth driver iterativ förbättring av `scraper.py`. Se `EXPERIMENTS.md` för fullständig historik. Nuvarande aggregat per testfall (iter 6):

| Testfall | F1 |
|----------|----|
| Kommendoren | 94.6% |
| Lilla ego | 94.3% |
| Tradition | 98.5% |
| Tranan | 94.1% |
| Crispy kvarnholmen | ~82% (LLM-varians i PDF-extraktion) |
