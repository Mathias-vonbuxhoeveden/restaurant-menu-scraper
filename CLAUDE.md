# Restaurant Menu Scraper

## Syfte
Skrapar menyer för en prospektrestaurang och dess konkurrenter, och exporterar dem till en Excel-fil med en flik per restaurang.

## Primärt användningsfall — pipeline.py
Tar restaurangnamn och adress, söker upp URL:er automatiskt via SerpAPI, och skrapar alla menyer parallellt.

```bash
python pipeline.py \
  --name "Ricordi" \
  --address "Kungsträdgårdsgatan 18, Stockholm" \
  --competitors "Calle P, Bistro Arsenalen, Restaurang Pava"
```

**Output:** `<Prospektnamn>.xlsx` med en flik per restaurang, kolumnerna **Name**, **Price**, **Category**, **Description**.

**Kräver:** miljövariabeln `SERPAPI_KEY` satt i terminalen.

## Sekundärt användningsfall — scraper.py
Skrapar en enskild restaurang givet en känd URL.

```bash
python scraper.py --url https://example-restaurant.se
python scraper.py --url https://example-restaurant.se --output result.xlsx --debug
```

## Flöde

### pipeline.py
1. Söker upp varje restaurangs URL via SerpAPI (filtrerar bort recensionssajter)
2. Claude Haiku väljer bäst matchande URL bland kandidaterna
3. Skrapar alla restauranger parallellt med `ThreadPoolExecutor`
4. Sparar resultat i en Excel-fil med en flik per restaurang

### scraper.py (används även av pipeline.py)
1. **URL är en PDF** → Claude Sonnet extraherar direkt
2. **Statisk HTML** → leta efter PDF-länk → Claude Sonnet, annars Claude Haiku på HTML-text
3. **Playwright** → om statisk HTML gav < 5 rätter: JS-rendera sidan, Claude Haiku navigerar till rätt meny-sida och flik, sedan samma PDF/HTML-logik

## Claude-modeller
| Uppgift | Modell |
|---|---|
| Menyextraktion från HTML | `claude-haiku-4-5` |
| Menyextraktion från PDF | `claude-sonnet-4-6` |
| Välj PDF-länk / meny-URL / flik | `claude-haiku-4-5` |

## Filer
| Fil | Syfte |
|---|---|
| `pipeline.py` | Primärt skript — namn + adress + konkurrenter → Excel |
| `scraper.py` | Lågnivå-skrapning för en URL, återanvänds av pipeline |
| `requirements.txt` | Python-beroenden |

## Beroenden
- `anthropic` – Claude API för extraktion och navigeringsbeslut
- `playwright` – headless browser för JS-renderade sidor
- `openpyxl` – skriva Excel-filer
- `requests` – HTTP-requests + SerpAPI-anrop
- `beautifulsoup4` – parsa HTML
