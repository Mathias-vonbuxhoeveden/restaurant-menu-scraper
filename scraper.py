#!/usr/bin/env python3
"""
Restaurant Menu Scraper
Input:  --url  URL to a restaurant website
Output: menu.xlsx with columns Rätt, Pris, Kategori, Beskrivning

Flow:
  1. Fetch HTML with requests
  2. Look for .pdf links  →  download + extract text + Claude Haiku extraction
  3. Try parsing static HTML for menu rows
  4. Only if nothing found: use Playwright (JS-rendered HTML, no PDFs)
"""

import argparse
import json
import re
import sys
import io
import anthropic
import requests
import openpyxl
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse


HEADERS = {"User-Agent": "Mozilla/5.0"}
DEBUG = False  # set to True via --debug flag


def is_pdf_url(url: str) -> bool:
    return urlparse(url).path.lower().endswith(".pdf")


def rows_to_excel(rows: list[tuple[str, str, str, str]], output_path: str = "menu.xlsx") -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Meny"
    ws.append(["Name", "Price", "Category", "Description"])
    for dish, price, category, description in rows:
        ws.append([dish, price, category, description])
    for col in ws.columns:
        max_len = max(len(str(cell.value or "")) for cell in col)
        ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 80)
    wb.save(output_path)
    print(f"Sparade {len(rows)} rätter till {output_path}")


# ---------------------------------------------------------------------------
# PDF: find link + download + Claude extraction
# ---------------------------------------------------------------------------

def find_pdf_url(html: str, base_url: str, menu_type: str = "dinner") -> str | None:
    """
    Collect all PDF links from the page and ask Claude to pick the one
    most likely to be the full à la carte menu. Returns the chosen URL,
    or None if no PDF links exist.
    """
    soup = BeautifulSoup(html, "html.parser")
    pdfs: list[dict] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        full = urljoin(base_url, a["href"])
        if is_pdf_url(full) and full not in seen:
            seen.add(full)
            text = a.get_text(strip=True)
            pdfs.append({"text": text, "url": full})

    if not pdfs:
        return None
    if len(pdfs) == 1:
        return pdfs[0]["url"]

    # Multiple PDFs — let Claude pick the right one
    client = anthropic.Anthropic()
    pdf_list = "\n".join(
        f'{i+1}. text="{p["text"]}"  url={p["url"]}'
        for i, p in enumerate(pdfs)
    )
    nav_instruction = _menu_type_instruction(menu_type, "navigate")
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=8,
        messages=[{"role": "user", "content": f"""En restaurangsida har flera PDF-menyer. Välj den som innehåller: {nav_instruction}

{pdf_list}

Svara med ENBART siffran."""}],
    )
    choice = response.content[0].text.strip()
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(pdfs):
            print(f"  Claude valde PDF {idx+1}/{len(pdfs)}: {pdfs[idx]['url']}")
            return pdfs[idx]["url"]
    except ValueError:
        pass
    return pdfs[0]["url"]


_NAME_INSTRUCTION_HTML = (
    '"name": ENBART rättens namn — vanligtvis första raden eller första komma-separerade delen. '
    'Aldrig ingredienser eller beskrivning.\n\n'
    'Exempel: "POMODORO, SPAGHETTI, TOMATSÅS, STRACCIATELLA, BASILIKA  195kr"\n'
    '→ name="POMODORO", price="195", description="SPAGHETTI, TOMATSÅS, STRACCIATELLA, BASILIKA"'
)

LANGUAGE_NAMES = {
    "sv": "svenska",
    "en": "engelska",
}

# Each menu type has three prompt snippets:
#   extract  — injected into the extraction prompt (what to include/exclude)
#   navigate — injected into link/tab selection prompts (what page/tab to look for)
#   pdf      — injected into the PDF extraction prompt
MENU_TYPES: dict[str, dict[str, str]] = {
    "dinner": {
        "extract": (
            "Extrahera middagsmenyn / à la carte. "
            "Inkludera: förätter, huvudrätter, desserter, barnmeny, "
            "delningsrätter där man kan beställa för en person (använd då priset per person). "
            "Exkludera: lunchrätter, dagens rätt, veckomenyer, drycker, viner, öl, shots, "
            "tillbehör utan eget rättspris, stora delningsrätter som enbart säljs som hel portion för flera. "
            "Om en rätt har flera prisalternativ (t.ex. liten/stor), använd det lägsta priset. "
            "Om sidan saknar tydlig uppdelning mellan lunch och middag, extrahera alla maträtter med pris."
        ),
        "navigate": (
            'middagsmeny / à la carte. Prioritera etiketter som "meny", "mat", "à la carte", "dinner", "food". '
            'Undvik: lunchmeny, dagens lunch, veckans meny, drycker, events, specialmenyer.'
        ),
        "pdf": (
            "Extrahera middagsmenyn / à la carte. "
            "Inkludera: förätter, huvudrätter, desserter, barnmeny, "
            "delningsrätter där man kan beställa för en person (använd priset per person). "
            "Exkludera: lunchrätter, dagens rätt, drycker, viner, öl, "
            "tillbehör utan eget rättspris, stora delningsrätter för flera. "
            "Om en rätt har flera prisalternativ (t.ex. liten/stor), använd det lägsta priset."
        ),
    },
}


def _menu_type_instruction(menu_type: str, context: str) -> str:
    """Return the prompt snippet for the given menu_type and context (extract/navigate/pdf)."""
    spec = MENU_TYPES.get(menu_type, MENU_TYPES["dinner"])
    return spec.get(context, spec["extract"])


def _language_instruction(language: str) -> str:
    lang_name = LANGUAGE_NAMES.get(language, language)
    return (
        f"Om menyn finns på flera språk, använd {lang_name}. "
        f"Om menyn bara finns på ett språk, använd det oavsett vilket."
    )


def extract_with_claude(text: str, language: str = "sv", menu_type: str = "dinner") -> list[tuple[str, str, str, str]]:
    """Extract menu rows from HTML text using Claude Haiku."""
    client = anthropic.Anthropic()
    name_instruction = _NAME_INSTRUCTION_HTML
    extract_instruction = _menu_type_instruction(menu_type, "extract")

    prompt = f"""Du är en assistent som extraherar restaurangmenyer.

{extract_instruction}

Returnera ett JSON-array där varje objekt har exakt dessa fält:
- {name_instruction}
- "price": priset som ett rent heltal utan enhet (t.ex. "139"), eller tom sträng om inget pris. Varje rätt har exakt ett pris — blanda aldrig ihop priser mellan olika rätter. Om texten är kolumnformaterad, se till att priset på samma rad som rätten används.
- "category": sektionsrubriken som föregår denna rätt i menyn (t.ex. "FÖRRÄTT", "HUVUDRÄTT", "ANTIPASTI"). En rubrik gäller för alla rätter som följer tills nästa rubrik dyker upp. Tom sträng om ingen rubrik finns.
- "description": ingredienser, tillbehör och övrig beskrivningstext — allt som inte är namnet. {_language_instruction(language)}

Inkludera INTE: tillbehör utan eget pris, pizza-baser eller pizza-typer (t.ex. "rossa", "bianca"), sidorätter listade som tillägg, eller avdelningsrubriker.

Returnera ENBART det råa JSON-arrayet — ingen markdown, inga backticks, ingen förklaring.

TEXT:
{text}"""

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=8192,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    items = json.loads(raw)
    return [(item["name"], item["price"], item["category"], item["description"]) for item in items]


def parse_pdf(pdf_bytes: bytes, language: str = "sv", menu_type: str = "dinner") -> list[tuple[str, str, str, str]]:
    """Send PDF natively to Claude Sonnet — reads embedded text for text-based PDFs,
    falls back to vision automatically for scanned/image PDFs."""
    import base64

    print(f"  PDF: {len(pdf_bytes):,} bytes — skickar till Claude Sonnet (native PDF)")
    b64 = base64.standard_b64encode(pdf_bytes).decode()

    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=8192,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": b64,
                    },
                },
                {
                    "type": "text",
                    "text": f"""{ _menu_type_instruction(menu_type, "pdf")} Returnera ett JSON-array där varje objekt har exakt dessa fält:
- "name": rättens namn exakt som det står på menyn — vanligtvis ett kort ord eller fras med versaler (t.ex. "ARANCINI", "COTOLETTA ALLA MILANESE"). Aldrig ingredienser eller beskrivning.
- "price": priset som ett rent heltal utan enhet (t.ex. "139"), eller tom sträng om inget pris.
- "category": sektionsrubriken som föregår denna rätt (t.ex. "FÖRRÄTT", "ANTIPASTI"). Tom sträng om ingen finns.
- "description": ingredienser, tillbehör och övrig beskrivningstext. {_language_instruction(language)} Aldrig rättens namn igen.

Returnera ENBART det råa JSON-arrayet — ingen markdown, inga backticks, ingen förklaring.""",
                },
            ],
        }],
    )

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    items = json.loads(raw)
    return [(item["name"], item["price"], item["category"], item["description"]) for item in items]


def scrape_pdf_from_url(pdf_url: str, language: str = "sv", menu_type: str = "dinner") -> list[tuple[str, str, str, str]]:
    print(f"Laddar ned PDF: {pdf_url}")
    resp = requests.get(pdf_url, timeout=30, headers=HEADERS)
    resp.raise_for_status()
    print(f"  {len(resp.content):,} bytes nedladdade")
    rows = parse_pdf(resp.content, language=language, menu_type=menu_type)
    print(f"  Hittade {len(rows)} rätter i PDF")
    return rows


# ---------------------------------------------------------------------------
# HTML parsing — Claude-based (same approach as PDF)
# ---------------------------------------------------------------------------

MAX_HTML_TEXT_CHARS = 15_000


def parse_menu_from_html(html: str, language: str = "sv", menu_type: str = "dinner") -> list[tuple[str, str, str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["nav", "footer", "header", "script", "style"]):
        tag.decompose()

    text = soup.get_text(separator="\n", strip=True)
    if not text.strip():
        print("  [parse] Sidan gav ingen text efter rensning")
        return []

    if len(text) > MAX_HTML_TEXT_CHARS:
        print(f"  [parse] Text trunkerad: {len(text)} → {MAX_HTML_TEXT_CHARS} tecken")
        text = text[:MAX_HTML_TEXT_CHARS]

    print(f"  [parse] Skickar {len(text)} tecken till Claude …")
    try:
        return extract_with_claude(text, language=language, menu_type=menu_type)
    except Exception as e:
        print(f"  [parse] Claude-extraktion misslyckades: {e}")
        return []


def scrape_static_html(url: str, language: str = "sv", menu_type: str = "dinner") -> tuple[str, list[tuple[str, str, str, str]]]:
    """Returns (raw_html, rows). raw_html is always returned for PDF-link scanning."""
    print("Hämtar statisk HTML …")
    resp = requests.get(url, timeout=15, headers=HEADERS)
    resp.raise_for_status()
    print(f"  HTTP {resp.status_code}, {len(resp.content):,} bytes, content-type: {resp.headers.get('content-type', '?')}")
    html = resp.text
    rows = parse_menu_from_html(html, language=language, menu_type=menu_type)
    print(f"  Statisk HTML: {len(rows)} rätter")
    return html, rows


# ---------------------------------------------------------------------------
# Playwright (only for JS-rendered pages without PDF)
# ---------------------------------------------------------------------------

def pick_menu_link(links: list[dict], base_url: str, page_title: str, menu_type: str = "dinner") -> str | None:
    """
    Ask Claude to pick the navigation link most likely leading to the target menu.
    Returns the resolved absolute URL, or None if no good match.

    `links` is a list of {"text": str, "href": str} dicts (non-PDF only).
    """
    if not links:
        return None

    client = anthropic.Anthropic()
    link_list = "\n".join(
        f'{i+1}. "{l["text"]}"  →  {l["href"]}'
        for i, l in enumerate(links)
    )
    nav_instruction = _menu_type_instruction(menu_type, "navigate")
    prompt = f"""Du hjälper till att hitta rätt länk på en restaurangsida.

Sida: {base_url}
Titel: {page_title}

Navigeringslänkar:
{link_list}

Välj länken som leder till: {nav_instruction} Om ingen länk verkar relevant, svara 0.

Svara med ENBART siffran."""

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=8,
        messages=[{"role": "user", "content": prompt}],
    )
    choice = response.content[0].text.strip()
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(links):
            return urljoin(base_url, links[idx]["href"])
    except ValueError:
        pass
    return None


def collect_nav_links(page, base_url: str) -> list[dict]:
    """
    Extract links only from nav/header elements to avoid body noise.
    Falls back to all links if nav yields nothing.
    Deduplicates by link text (case-insensitive).
    """
    # Prefer semantic navigation elements
    NAV_SELECTOR = "nav a[href], header a[href], [role='navigation'] a[href]"
    raw = []
    for selector in [NAV_SELECTOR, "a[href]"]:
        for a in page.locator(selector).all():
            try:
                text = (a.inner_text() or "").strip()
                href = a.get_attribute("href") or ""
                full = urljoin(base_url, href)
                if text and href and not is_pdf_url(full):
                    raw.append({"text": text, "href": href})
            except Exception:
                pass
        if raw:
            break

    # Deduplicate by normalised text, preserve order
    seen: set[str] = set()
    unique = []
    for l in raw:
        key = l["text"].lower()
        if key not in seen:
            seen.add(key)
            unique.append(l)
    return unique


def collect_interactive_elements(page) -> list[str]:
    """
    Collect texts of visible buttons and tab-like elements on the current page.
    Deduplicates by normalised text.
    """
    seen: set[str] = set()
    texts: list[str] = []
    locator = page.locator("button, [role='tab'], [role='button']")
    for el in locator.all():
        try:
            if not el.is_visible():
                continue
            text = (el.inner_text() or "").strip()
            if text and text.lower() not in seen and 2 <= len(text) <= 60:
                seen.add(text.lower())
                texts.append(text)
        except Exception:
            pass
    return texts


def pick_menu_tab(texts: list[str], page_title: str, menu_type: str = "dinner") -> str | None:
    """
    Ask Claude to pick the button/tab most likely to reveal the target menu content.
    Returns the chosen text, or None.
    """
    if not texts:
        return None

    client = anthropic.Anthropic()
    numbered = "\n".join(f'{i+1}. "{t}"' for i, t in enumerate(texts))
    nav_instruction = _menu_type_instruction(menu_type, "navigate")
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=8,
        messages=[{"role": "user", "content": f"""Du hjälper till att hitta rätt flik på en restaurangs menysida.

Sida: {page_title}

Klickbara element:
{numbered}

Välj det element som troligast visar: {nav_instruction} Om inget element verkar relevant, svara 0.

Svara med ENBART siffran."""}],
    )
    choice = response.content[0].text.strip()
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(texts):
            return texts[idx]
    except ValueError:
        pass
    return None


def scrape_dynamic(url: str, language: str = "sv", menu_type: str = "dinner") -> list[tuple[str, str, str, str]]:
    print("Playwright: renderar JS-sida …")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not DEBUG)
        page = browser.new_page()
        page.goto(url, wait_until="networkidle", timeout=30000)

        page_title = page.title() or ""
        links = collect_nav_links(page, url)
        print(f"  Hittade {len(links)} nav-länk(ar): {[l['text'] for l in links]}")

        target = pick_menu_link(links, url, page_title, menu_type=menu_type)
        if target:
            print(f"  Claude valde: {target}")
            try:
                page.goto(target, wait_until="networkidle", timeout=30000)
                page_title = page.title() or ""
            except Exception as e:
                print(f"  Navigering misslyckades: {e}")
        else:
            print("  Claude hittade ingen tydlig menylänk, skrapar startsidan.")

        # Look for tabs/buttons to reveal the right menu section
        tab_texts = collect_interactive_elements(page)
        if tab_texts:
            print(f"  Hittade {len(tab_texts)} knappar/flikar: {tab_texts}")
            chosen = pick_menu_tab(tab_texts, page_title, menu_type=menu_type)
            if chosen:
                print(f"  Claude valde flik: {chosen!r}")
                try:
                    page.locator(
                        "button, [role='tab'], [role='button']"
                    ).filter(has_text=chosen).first.click()
                    page.wait_for_load_state("networkidle", timeout=10000)
                except Exception as e:
                    print(f"  Klick misslyckades: {e}")
            else:
                print("  Claude hittade ingen relevant flik.")

        html = page.content()
        current_url = page.url
        browser.close()

    # Check for PDF links in the rendered HTML (e.g. Squarespace sites where
    # PDF links are only injected after JS renders)
    pdf_url = find_pdf_url(html, current_url, menu_type=menu_type)
    if pdf_url:
        print(f"  PDF-länk hittad i renderad HTML: {pdf_url}")
        try:
            rows = scrape_pdf_from_url(pdf_url, language=language, menu_type=menu_type)
            if rows:
                return rows
            print("  PDF gav inga rätter, faller tillbaka på HTML-parsning")
        except Exception as e:
            print(f"  PDF-skrapning misslyckades: {e}")

    return parse_menu_from_html(html, language=language, menu_type=menu_type)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Skrapar restaurangmenyer till Excel")
    parser.add_argument("--url", required=True, help="URL till restaurangens hemsida")
    parser.add_argument("--output", default="menu.xlsx", help="Utdatafil (default: menu.xlsx)")
    parser.add_argument("--debug", action="store_true", help="Visa webbläsare + spara PDF-bilder")
    parser.add_argument("--language", default="sv", help="Föredraget språk för beskrivningar (default: sv)")
    parser.add_argument("--menu-type", default="dinner", help="Typ av meny att skrapa (default: dinner)")
    args = parser.parse_args()

    global DEBUG
    DEBUG = args.debug
    url = args.url
    language = args.language
    menu_type = args.menu_type

    # Step 0: URL is itself a PDF → parse directly
    if is_pdf_url(url):
        try:
            rows = scrape_pdf_from_url(url, language=language, menu_type=menu_type)
            if rows:
                rows_to_excel(rows, args.output)
                return
        except Exception as e:
            print(f"  PDF-skrapning misslyckades: {e}")
        sys.exit(1)

    # Step 1: Fetch static HTML (needed for both menu parsing and PDF-link detection)
    try:
        html, rows = scrape_static_html(url, language=language, menu_type=menu_type)
    except Exception as e:
        print(f"  Kunde inte hämta sidan: {e}")
        html, rows = "", []

    # Step 2: PDF link in static HTML → download + Claude extraction
    if html:
        pdf_url = find_pdf_url(html, url, menu_type=menu_type)
        if pdf_url:
            try:
                rows = scrape_pdf_from_url(pdf_url, language=language, menu_type=menu_type)
                if rows:
                    rows_to_excel(rows, args.output)
                    return
                print("  Hittade inga rätter i PDF.")
            except Exception as e:
                print(f"  PDF-skrapning misslyckades: {e}")

    # Step 3: Static HTML had menu rows (no PDF needed)
    if rows:
        rows_to_excel(rows, args.output)
        return
    print("  Hittade inga rätter i statisk HTML.")

    # Step 4: Playwright for JS-rendered pages
    try:
        rows = scrape_dynamic(url, language=language, menu_type=menu_type)
        if rows:
            rows_to_excel(rows, args.output)
            return
        print("  Hittade inga rätter via Playwright.")
    except Exception as e:
        print(f"  Playwright misslyckades: {e}")

    print("Kunde inte extrahera någon meny. Prova att inspektera sidan manuellt.", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
