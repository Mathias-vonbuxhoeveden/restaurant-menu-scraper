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
import os
import re
import sys
import io
import threading
import anthropic
import requests
import openpyxl
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse


HEADERS = {"User-Agent": "Mozilla/5.0"}
DEBUG = False  # set to True via --debug flag

# Only one Playwright/Chromium instance at a time to cap RAM usage
_playwright_semaphore = threading.Semaphore(1)

# Cap concurrent Claude API calls to avoid 529 Overloaded errors under parallel load
_api_semaphore = threading.Semaphore(5)


def _claude_create(client: anthropic.Anthropic, **kwargs):
    with _api_semaphore:
        return client.messages.create(**kwargs)

SCRAPER_API_KEY = os.environ.get("SCRAPER_API_KEY", "")


def _is_cloudflare_blocked(resp: requests.Response) -> bool:
    """Return True if the response looks like a Cloudflare block page."""
    if resp.status_code in (403, 429, 503):
        return True
    text = resp.text[:3000].lower()
    return "cf-ray" in resp.headers and ("cloudflare" in text or "just a moment" in text)


def _is_cloudflare_html(html: str) -> bool:
    """Return True if rendered HTML is a Cloudflare challenge page."""
    snippet = html[:3000].lower()
    return "cloudflare" in snippet and ("just a moment" in snippet or "enable javascript" in snippet)


def fetch_html(url: str, timeout: int = 15, render_js: bool = False) -> requests.Response:
    """
    Fetch URL normally; if blocked (403/Cloudflare), retry via ScraperAPI.
    Set render_js=True to use ScraperAPI's JS-rendering endpoint.
    """
    if not render_js:
        try:
            resp = requests.get(url, timeout=timeout, headers=HEADERS)
            if not _is_cloudflare_blocked(resp):
                resp.raise_for_status()
                return resp
            print(f"  Blockerad ({resp.status_code}) av Cloudflare/WAF")
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code != 403:
                raise
            print(f"  HTTP 403 — provar ScraperAPI …")

    if not SCRAPER_API_KEY:
        raise RuntimeError("Blockerad och SCRAPER_API_KEY saknas — kan inte fortsätta.")

    params = f"api_key={SCRAPER_API_KEY}&url={requests.utils.quote(url, safe=':/?=&')}"
    if render_js:
        params += "&render=true"
    scraper_url = f"http://api.scraperapi.com?{params}"
    print(f"  Försöker via ScraperAPI (render_js={render_js}) …")
    resp = requests.get(scraper_url, timeout=60)
    resp.raise_for_status()
    return resp


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
    response = _claude_create(client,
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


# Each menu type has two prompt snippets:
#   extract  — injected into the extraction prompt (what to include/exclude), shared for HTML and PDF
#   navigate — injected into link/tab selection prompts (what page/tab to look for)
MENU_TYPES: dict[str, dict[str, str]] = {
    "dinner": {
        "extract": (
            "Extrahera middagsmenyn / à la carte. "
            "Inkludera: förätter, huvudrätter, desserter, barnmeny, "
            "snacks och aptitretare (t.ex. oliver, nötter, bröd, ostron, charkuterier listade som egna poster), "
            "ostar och ostbrickor, "
            "delningsrätter där man kan beställa för en person (använd då priset per person). "
            "Exkludera: lunchrätter, dagens rätt, veckomenyer, drycker, viner, öl, shots, "
            "tillbehör och sides (t.ex. pommes frites, potatisgratäng, sallad som bilaga, brödkorg) — även om de har eget pris, "
            "såser och smör (t.ex. bearnaise, café de paris smör, rödvinssky, grönpepparsås) — även om de har eget pris, "
            "rena köttkvaliteter/styckdelar listade med ursprungsland eller uppfödningsmetod utan tillagningsmetod i namnet "
            "(t.ex. 'Ryggbiff, USDA Prime, Nebraska, grain fed'), "
            "stora delningsrätter som enbart säljs som hel portion för flera. "
            "Om en rätt har flera prisalternativ (t.ex. liten/stor), använd det lägsta priset. "
            "Om sidan saknar tydlig uppdelning mellan lunch och middag, extrahera alla maträtter med pris."
        ),
        "navigate": (
            'middagsmeny / à la carte. Prioritera etiketter som "meny", "mat", "à la carte", "dinner", "food". '
            'Undvik: lunchmeny, dagens lunch, veckans meny, drycker, events, specialmenyer, sällskapsmeny, festmeny, gruppmenyer, bröllop, bankett.'
        ),
    },
}
MENU_TYPES["a_la_carte"] = MENU_TYPES["dinner"]


def _menu_type_instruction(menu_type: str, context: str) -> str:
    """Return the prompt snippet for the given menu_type and context (extract/navigate)."""
    spec = MENU_TYPES.get(menu_type, MENU_TYPES["dinner"])
    return spec.get(context, spec["extract"])


def _build_extraction_prompt(menu_type: str) -> str:
    """Shared extraction instruction used for both HTML and PDF sources."""
    extract_instruction = _menu_type_instruction(menu_type, "extract")
    return f"""{extract_instruction}

Returnera ett JSON-array där varje objekt har exakt dessa fält:
- "name": ENBART rättens namn — vanligtvis första raden eller första komma-separerade delen. Aldrig ingredienser eller beskrivning.
  Exempel: "POMODORO, SPAGHETTI, TOMATSÅS, STRACCIATELLA, BASILIKA  195kr"
  → name="POMODORO", price="195", description="SPAGHETTI, TOMATSÅS, STRACCIATELLA, BASILIKA"
- "price": priset som ett rent heltal utan enhet (t.ex. "139"), eller tom sträng om inget pris. Varje rätt har exakt ett pris — blanda aldrig ihop priser mellan olika rätter. Om texten är kolumnformaterad, se till att priset på samma rad som rätten används.
- "category": sektionsrubriken som föregår denna rätt i menyn (t.ex. "FÖRRÄTT", "HUVUDRÄTT", "ANTIPASTI"). En rubrik gäller för alla rätter som följer tills nästa rubrik dyker upp. Tom sträng om ingen rubrik finns.
- "description": ingredienser, tillbehör och övrig beskrivningstext från menyn. Kontext: description används av en annan AI för prisjämförelse mellan restauranger — ju mer konkret innehåll, desto bättre. Regler: aldrig rättens namn igen; aldrig kategorinamnet igen; lämna tom sträng ("") om ingen beskrivning finns på menyn; hitta ALDRIG på ingredienser — extrahera bara det som faktiskt står skrivet; behåll originalspråket (svenska/italienska/engelska som det står).

Inkludera INTE: tillbehör utan eget pris, pizza-baser eller pizza-typer (t.ex. "rossa", "bianca"), sidorätter listade som tillägg, eller avdelningsrubriker.

Returnera ENBART det råa JSON-arrayet — ingen markdown, inga backticks, ingen förklaring."""


def _parse_json_array(raw: str) -> list[dict]:
    """Parse a JSON array from a model response, extracting the [...] block if needed."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    start = raw.find("[")
    end = raw.rfind("]")
    if start != -1 and end > start:
        raw = raw[start : end + 1]
    return json.loads(raw)


def extract_with_claude(text: str, language: str = "sv", menu_type: str = "dinner") -> list[tuple[str, str, str, str]]:
    """Extract menu rows from HTML text using Claude Haiku."""
    client = anthropic.Anthropic()
    prompt = f"""Du är en assistent som extraherar restaurangmenyer.

{_build_extraction_prompt(menu_type)}

TEXT:
{text}"""

    messages = [{"role": "user", "content": prompt}]
    for attempt in range(2):
        response = _claude_create(client,
            model="claude-haiku-4-5-20251001",
            max_tokens=8192,
            messages=messages,
        )
        raw = response.content[0].text
        try:
            items = _parse_json_array(raw)
            return [(item["name"], item["price"], item["category"], item["description"]) for item in items]
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            if attempt == 0:
                print(f"  [parse] JSON-fel, försöker igen: {e}")
            else:
                raise


def parse_pdf(pdf_bytes: bytes, language: str = "sv", menu_type: str = "dinner") -> list[tuple[str, str, str, str]]:
    """Send PDF natively to Claude Sonnet — reads embedded text for text-based PDFs,
    falls back to vision automatically for scanned/image PDFs."""
    import base64

    print(f"  PDF: {len(pdf_bytes):,} bytes — skickar till Claude Sonnet (native PDF)")
    b64 = base64.standard_b64encode(pdf_bytes).decode()

    client = anthropic.Anthropic()
    pdf_message = {
        "role": "user",
        "content": [
            {
                "type": "document",
                "source": {"type": "base64", "media_type": "application/pdf", "data": b64},
            },
            {"type": "text", "text": _build_extraction_prompt(menu_type)},
        ],
    }
    for attempt in range(2):
        response = _claude_create(client,
            model="claude-sonnet-4-6",
            max_tokens=8192,
            messages=[pdf_message],
        )
        raw = response.content[0].text
        try:
            items = _parse_json_array(raw)
            return [(item["name"], item["price"], item["category"], item["description"]) for item in items]
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            if attempt == 0:
                print(f"  PDF: JSON-fel, försöker igen: {e}")
            else:
                raise


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

    # Remove elements hidden via inline style
    for tag in soup.find_all(style=re.compile(r"display\s*:\s*none", re.I)):
        tag.decompose()

    # Remove Elementor sections hidden on all device sizes (invisible to all users)
    for tag in soup.find_all(class_=re.compile(r"elementor-hidden-desktop")):
        classes = tag.get("class", [])
        if {"elementor-hidden-desktop", "elementor-hidden-tablet", "elementor-hidden-mobile"}.issubset(set(classes)):
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
    resp = fetch_html(url, timeout=15)
    print(f"  HTTP {resp.status_code}, {len(resp.content):,} bytes, content-type: {resp.headers.get('content-type', '?')}")
    html = resp.text
    rows = parse_menu_from_html(html, language=language, menu_type=menu_type)
    print(f"  Statisk HTML: {len(rows)} rätter")
    return html, rows


# ---------------------------------------------------------------------------
# Playwright (only for JS-rendered pages without PDF)
# ---------------------------------------------------------------------------

def _html_text_snippet(html: str, max_chars: int = 400) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["nav", "footer", "header", "script", "style"]):
        tag.decompose()
    return soup.get_text(separator="\n", strip=True)[:max_chars]


def _is_menu_complete(
    rows: list[tuple[str, str, str, str]],
    page_title: str,
    text_snippet: str,
    menu_type: str = "dinner",
) -> bool:
    """Ask Claude whether the current page content looks like a complete menu."""
    nav_instruction = _menu_type_instruction(menu_type, "navigate")
    if rows:
        sample = "\n".join(f"{r[0]} | {r[1]} kr | {r[2]}" for r in rows[:10])
        content = f"Extraherade rätter ({len(rows)} st, urval):\n{sample}"
    else:
        content = f"Inga rätter extraherade. Sidans text:\n{text_snippet}"

    client = anthropic.Anthropic()
    response = _claude_create(client,
        model="claude-haiku-4-5-20251001",
        max_tokens=8,
        messages=[{"role": "user", "content": f"""Du hjälper till att avgöra om rätt menysida är nådd.

Sida: {page_title}
Letar efter: {nav_instruction}

{content}

Är detta den faktiska menyn med rätter och priser, eller en mellansida som kräver fler klick?
Svara med ENBART: done (menyn nådd) eller continue (fler steg behövs)."""}],
    )
    answer = response.content[0].text.strip().lower()
    print(f"  Claude bedömning: {answer!r}")
    return "done" in answer


def _extract_divi_links(page, base_url: str) -> list[dict]:
    """
    Extract column link URLs from Divi's et_link_options_data JS variable.
    Divi stores clickable-column URLs in a script tag, not in href attributes.
    """
    try:
        items = page.evaluate("""() => {
            for (const s of document.querySelectorAll('script:not([src])')) {
                const m = s.textContent.match(/et_link_options_data\\s*=\\s*(\\[[\\s\\S]*?\\])\\s*;/);
                if (m) { try { return JSON.parse(m[1]); } catch(e) {} }
            }
            return [];
        }""")
        result = []
        for item in (items or []):
            cls = item.get("class", "")
            url = item.get("url", "")
            if not url or not cls:
                continue
            label = page.evaluate(
                f"() => {{ const el = document.querySelector('.{cls}'); return el ? el.innerText.trim().slice(0, 60) : ''; }}"
            )
            result.append({"text": label or cls, "href": url, "type": "link"})
        return result
    except Exception:
        return []


def collect_all_navigable_elements(page, base_url: str, max_items: int = 25) -> list[dict]:
    """
    Collect links and interactive elements from the entire page.
    Returns [{"text": str, "href": str|None, "type": "link"|"button"}, ...]
    """
    seen: set[str] = set()
    elements: list[dict] = []

    # Divi hides column links in a JS variable — extract those first
    for item in _extract_divi_links(page, base_url):
        key = item["href"]
        if key not in seen:
            seen.add(key)
            elements.append(item)

    for a in page.locator("a[href]").all():
        if len(elements) >= max_items:
            break
        try:
            text = (a.inner_text() or "").strip()
            href = a.get_attribute("href") or ""
            full = urljoin(base_url, href)
            if text and href and full not in seen and 2 <= len(text) <= 60:
                seen.add(full)
                el_type = "pdf" if is_pdf_url(full) else "link"
                elements.append({"text": text, "href": href, "type": el_type})
        except Exception:
            pass

    for el in page.locator("button, [role='tab'], [role='button']").all():
        if len(elements) >= max_items:
            break
        try:
            if not el.is_visible():
                continue
            text = (el.inner_text() or "").strip()
            key = text.lower()
            if text and key not in seen and 2 <= len(text) <= 60:
                seen.add(key)
                elements.append({"text": text, "href": None, "type": "button"})
        except Exception:
            pass

    return elements


def pick_next_action(
    elements: list[dict],
    current_url: str,
    page_title: str,
    menu_type: str = "dinner",
) -> dict | None:
    """Ask Claude to pick the element most likely to lead to the menu."""
    if not elements:
        return None

    client = anthropic.Anthropic()
    nav_instruction = _menu_type_instruction(menu_type, "navigate")
    numbered = "\n".join(
        f'{i+1}. [{el["type"]}] "{el["text"]}"' + (f"  → {el['href']}" if el["href"] else "")
        for i, el in enumerate(elements)
    )
    response = _claude_create(client,
        model="claude-haiku-4-5-20251001",
        max_tokens=8,
        messages=[{"role": "user", "content": f"""Du hjälper till att navigera till rätt menysida på en restaurang.

Sida: {page_title} ({current_url})
Letar efter: {nav_instruction}

Klickbara element:
{numbered}

Välj det element som troligast leder till menyn eller till en sida med menyinformation.
Svara 0 BARA om alla element uppenbart leder bort från mat/meny (t.ex. bokningar, kontakt, events, drycker, press).
Om sidan är en restaurangs landningssida utan tydlig menylänk, välj det element som troligast leder vidare till restaurangens egna sidor.
Svara med ENBART siffran."""}],
    )
    choice = response.content[0].text.strip()
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(elements):
            chosen = elements[idx]
            print(f"  Claude valde: [{chosen['type']}] {chosen['text']!r}")
            return chosen
    except ValueError:
        pass
    print("  Claude hittade inget relevant element.")
    return None


def scrape_dynamic(url: str, language: str = "sv", menu_type: str = "dinner") -> list[tuple[str, str, str, str]]:
    print("Playwright: renderar JS-sida …")
    from playwright.sync_api import sync_playwright

    with _playwright_semaphore:
        return _scrape_dynamic_impl(url, language=language, menu_type=menu_type)


def _scrape_dynamic_impl(url: str, language: str = "sv", menu_type: str = "dinner") -> list[tuple[str, str, str, str]]:
    from playwright.sync_api import sync_playwright

    MAX_NAV_STEPS = 3

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--single-process",
                "--disable-extensions",
                "--disable-background-networking",
                "--disable-default-apps",
                "--disable-sync",
                "--mute-audio",
                "--no-first-run",
            ],
        )
        page = browser.new_page()

        def _block_heavy(route):
            if route.request.resource_type in ("image", "font", "media"):
                route.abort()
            else:
                route.continue_()

        page.route("**/*", _block_heavy)
        page.goto(url, wait_until="networkidle", timeout=30000)

        rows: list[tuple[str, str, str, str]] = []
        html = ""
        current_url = url
        visited: set[str] = {url}

        for step in range(MAX_NAV_STEPS):
            html = page.content()
            current_url = page.url
            page_title = page.title() or ""

            rows = parse_menu_from_html(html, language=language, menu_type=menu_type)
            print(f"  Steg {step + 1}/{MAX_NAV_STEPS}: {len(rows)} rätter på '{page_title}'")

            if _is_menu_complete(rows, page_title, _html_text_snippet(html), menu_type=menu_type):
                break

            if step == MAX_NAV_STEPS - 1:
                print(f"  Max navigeringssteg ({MAX_NAV_STEPS}) nådda.")
                break

            elements = collect_all_navigable_elements(page, current_url)
            print(f"  Hittade {len(elements)} klickbara element")
            action = pick_next_action(elements, current_url, page_title, menu_type=menu_type)

            if not action:
                break

            try:
                if action["type"] == "pdf":
                    target = urljoin(current_url, action["href"])
                    print(f"  Claude valde PDF: {target}")
                    browser.close()
                    return scrape_pdf_from_url(target, language=language, menu_type=menu_type)
                elif action["href"]:
                    target = urljoin(current_url, action["href"])
                    # Pre-check: if the resolved URL is already a PDF, skip Playwright navigation
                    if is_pdf_url(target):
                        print(f"  Länk pekar direkt på PDF: {target}")
                        browser.close()
                        return scrape_pdf_from_url(target, language=language, menu_type=menu_type)
                    if target in visited:
                        print(f"  URL redan besökt, avbryter: {target}")
                        break
                    visited.add(target)
                    page.goto(target, wait_until="networkidle", timeout=30000)
                else:
                    page.locator(
                        "button, [role='tab'], [role='button']"
                    ).filter(has_text=action["text"]).first.click()
                    page.wait_for_load_state("networkidle", timeout=10000)
            except Exception as e:
                exc_str = str(e)
                # Playwright triggers a download when navigating to a PDF — extract URL and fetch directly
                if "Download is starting" in exc_str:
                    pdf_match = re.search(r'navigating to "([^"]+)"', exc_str)
                    pdf_candidate = pdf_match.group(1) if pdf_match else page.url
                    if pdf_candidate:
                        print(f"  PDF-download detekterad — hämtar direkt: {pdf_candidate}")
                        browser.close()
                        return scrape_pdf_from_url(pdf_candidate, language=language, menu_type=menu_type)
                print(f"  Navigering misslyckades: {e}")
                break

        browser.close()

    if _is_cloudflare_html(html):
        if SCRAPER_API_KEY:
            print("  Cloudflare detekterat — faller tillbaka på ScraperAPI …")
            try:
                resp = fetch_html(url, render_js=True)
                return parse_menu_from_html(resp.text, language=language, menu_type=menu_type)
            except Exception as exc:
                print(f"  ScraperAPI JS-fallback misslyckades: {exc}")
        else:
            print("  Cloudflare detekterat men SCRAPER_API_KEY saknas — ger upp.")
        return []

    return rows


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
    # Require at least one priced row — nav-link halluciations never have prices
    rows_with_price = [r for r in rows if str(r[1]).strip()]
    if rows_with_price:
        rows_to_excel(rows, args.output)
        return
    if rows:
        print("  Statisk HTML: rätter utan pris — fortsätter till Playwright.")
    else:
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
