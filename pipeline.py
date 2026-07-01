#!/usr/bin/env python3
"""
Restaurant Pipeline
-------------------
Söker upp och skrapar menyer för ett prospekt + konkurrenter.

Usage:
    python pipeline.py \
        --name "Ricordi" \
        --address "Götgatan 12, Stockholm" \
        --competitors "Pasta Basta, La Famiglia, Il Posto"

Output: <Prospektnamn>.xlsx  med en flik per restaurang.
"""

import argparse
import io
import logging
import sys
import threading

import os

from concurrent.futures import ThreadPoolExecutor, as_completed

import anthropic
import openpyxl
import requests

# Import reusable scraping functions from scraper.py
from scraper import (
    find_pdf_url,
    is_pdf_url,
    scrape_pdf_from_url,
    scrape_static_html,
    scrape_dynamic,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Google Search
# ---------------------------------------------------------------------------

EXCLUDED_DOMAINS = {
    "tripadvisor", "yelp", "google", "facebook", "instagram",
    "thefork", "bokabord", "restaurangguiden", "opentable",
    "eniro", "hitta", "gula", "foursquare",
}


def _is_excluded(url: str) -> bool:
    from urllib.parse import urlparse
    host = urlparse(url).netloc.lower()
    return any(domain in host for domain in EXCLUDED_DOMAINS)


def find_restaurant_url(query: str, restaurant_name: str) -> str | None:
    """
    Söker med SerpAPI, hämtar upp till 5 träffar och låter Claude välja
    den URL som troligast är restaurangens officiella hemsida.
    Returnerar vald URL eller None.
    """
    api_key = os.environ.get("SERPAPI_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "SERPAPI_KEY saknas. Sätt nyckeln i samma terminal: export SERPAPI_KEY=din_nyckel"
        )
    resp = requests.get(
        "https://serpapi.com/search.json",
        params={
            "q": query,
            "num": 5,
            "engine": "google",
            "api_key": api_key,
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"SerpAPI-fel: {data['error']}")
    results = data.get("organic_results", [])
    if not results:
        log.warning("Inga sökträffar för: %s", query)
        return None

    candidates = [r for r in results if not _is_excluded(r["link"])]
    if not candidates:
        log.warning("Alla träffar var uteslutna (recensionssajter m.m.) för: %s", query)
        return None

    if len(candidates) == 1:
        url = candidates[0]["link"]
        log.info("Enda kandidat: %s", url)
        return url

    # Låt Claude välja bästa URL
    numbered = "\n".join(
        f'{i+1}. title="{r["title"]}"  url={r["link"]}'
        for i, r in enumerate(candidates)
    )
    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=8,
        messages=[{"role": "user", "content": f"""Du hjälper till att hitta en restaurangs officiella hemsida.

Restaurang: {restaurant_name}
Sökfråga: {query}

Kandidater:
{numbered}

Välj den URL som troligast är restaurangens egna officiella hemsida. Undvik recensionssajter, kataloger och sociala medier. Om ingen är rimlig, svara 0.

Svara med ENBART siffran."""}],
    )
    choice = response.content[0].text.strip()
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(candidates):
            url = candidates[idx]["link"]
            log.info("Claude valde URL för '%s': %s", restaurant_name, url)
            return url
    except ValueError:
        pass

    log.warning("Claude hittade ingen bra URL för '%s'", restaurant_name)
    return None


# ---------------------------------------------------------------------------
# Scrape one restaurant → list of (Rätt, Pris, Kategori, Beskrivning)
# ---------------------------------------------------------------------------

def scrape_menu(url: str, language: str = "sv", menu_type: str = "dinner", restaurant_name: str | None = None, restaurant_address: str | None = None) -> list[tuple[str, str, str, str]]:
    """
    Kör samma fallback-kedja som scraper.py main() men returnerar
    raderna i stället för att spara till fil.
    """
    # 0. URL is itself a PDF → parse directly
    if is_pdf_url(url):
        try:
            return scrape_pdf_from_url(url, language=language, menu_type=menu_type)
        except Exception as exc:
            log.warning("PDF-skrapning misslyckades (%s): %s", url, exc)
            return []

    # 1. Statisk HTML
    try:
        html, rows = scrape_static_html(url, language=language, menu_type=menu_type)
    except Exception as exc:
        log.warning("Kunde inte hämta statisk HTML (%s): %s", url, exc)
        html, rows = "", []

    # 2. PDF-länk i statisk HTML
    if html:
        pdf_url = find_pdf_url(html, url, menu_type=menu_type)
        if pdf_url:
            try:
                pdf_rows = scrape_pdf_from_url(pdf_url, language=language, menu_type=menu_type)
                if pdf_rows:
                    return pdf_rows
                log.warning("PDF hittades men gav inga rätter: %s", pdf_url)
            except Exception as exc:
                log.warning("PDF-skrapning misslyckades (%s): %s", pdf_url, exc)

    # 3. Statisk HTML hade tillräckligt många rätter
    MIN_ROWS = 5
    if len(rows) >= MIN_ROWS:
        return rows
    if rows:
        log.info("Statisk HTML gav bara %d rätter (under tröskel %d), provar nästa fallback …", len(rows), MIN_ROWS)
    else:
        log.info("Inga rätter i statisk HTML, provar Playwright …")

    # 4. Playwright (JS-renderad sida)
    try:
        rows = scrape_dynamic(url, language=language, menu_type=menu_type, restaurant_name=restaurant_name, restaurant_address=restaurant_address)
        if rows:
            return rows
        log.warning("Playwright hittade inga rätter för %s", url)
    except Exception as exc:
        log.warning("Playwright misslyckades (%s): %s", url, exc)

    return []


# ---------------------------------------------------------------------------
# Concurrent multi-restaurant scraping — the ONE place both the production API
# (api.py) and the local eval harness (run_eval.py) trigger scraping from, so
# both exercise identical concurrency behavior (same thread-per-restaurant
# contention that only shows up when several menus scrape at once).
# ---------------------------------------------------------------------------

class _ThreadLocalStdout:
    """
    Routes print() output to a per-thread buffer instead of the real stdout, so
    concurrent scrape_menu() calls (one per ThreadPoolExecutor worker) don't
    interleave or clobber each other's captured output. Falls through to the
    real stdout for any thread that hasn't registered a buffer.
    """

    def __init__(self, real_stdout):
        self._real = real_stdout
        self._local = threading.local()

    def write(self, s):
        buf = getattr(self._local, "buf", None)
        (buf if buf is not None else self._real).write(s)

    def flush(self):
        self._real.flush()

    def set_buffer(self, buf):
        self._local.buf = buf

    def clear_buffer(self):
        self._local.buf = None


_thread_stdout = _ThreadLocalStdout(sys.stdout)
sys.stdout = _thread_stdout


class _ThreadLogFilter(logging.Filter):
    """Only lets through log records emitted by one specific thread."""

    def __init__(self, thread_id: int):
        super().__init__()
        self._thread_id = thread_id

    def filter(self, record: logging.LogRecord) -> bool:
        return record.thread == self._thread_id


def _scrape_one(name: str, address: str, url: str, menu_type: str) -> tuple[str, list, str]:
    """Returns (name, rows, nav_log). nav_log combines this call's print() and logging output."""
    log.info("=== Startar: %s ===", name)

    if not url:
        log.warning("Ingen website_url för '%s' — hoppar över", name)
        return name, [], "(ingen URL)"

    stdout_buf = io.StringIO()
    log_buf = io.StringIO()

    handler = logging.StreamHandler(log_buf)
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter("%(levelname)s  %(message)s"))
    handler.addFilter(_ThreadLogFilter(threading.get_ident()))
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    _thread_stdout.set_buffer(stdout_buf)

    try:
        rows = scrape_menu(url, menu_type=menu_type, restaurant_name=name, restaurant_address=address)
    except Exception as exc:
        rows = []
        stdout_buf.write(f"EXCEPTION: {exc}\n")
        log.error("Scraping misslyckades för '%s': %s", name, exc)
    finally:
        _thread_stdout.clear_buffer()
        root_logger.removeHandler(handler)

    log.info("=== Klar: %s (%d rätter) ===", name, len(rows))
    nav_log = f"URL: {url}\n" + stdout_buf.getvalue() + log_buf.getvalue()
    return name, rows, nav_log


def scrape_restaurants_concurrent(
    restaurants: list[tuple[str, str, str, str]],
) -> dict[str, dict]:
    """
    Scrapes multiple restaurants concurrently, one thread per restaurant — the same
    orchestration used for a real /api/scrape request, so callers (production and eval)
    see identical timing/resource-contention behavior instead of two implementations
    that can silently drift apart.

    restaurants: [(name, address, website_url, menu_type), ...]
    Returns {name: {"rows": [...], "nav_log": str}}
    """
    results: dict[str, dict] = {}
    if not restaurants:
        return results

    with ThreadPoolExecutor(max_workers=len(restaurants)) as executor:
        futures = {
            executor.submit(_scrape_one, name, address, url, menu_type): name
            for name, address, url, menu_type in restaurants
        }
        for future in as_completed(futures):
            name, rows, nav_log = future.result()
            results[name] = {"rows": rows, "nav_log": nav_log}

    return results


# ---------------------------------------------------------------------------
# Excel: write multiple sheets into one workbook
# ---------------------------------------------------------------------------

def write_workbook(sheets: list[tuple[str, list[tuple[str, str, str, str]]]], output_path: str) -> None:
    """
    sheets = [(sheet_name, rows), ...]  (första = prospekt, resten = konkurrenter)
    rows   = [(Rätt, Pris, Kategori, Beskrivning), ...]
    """
    wb = openpyxl.Workbook()
    # Remove default empty sheet
    wb.remove(wb.active)

    for sheet_name, rows in sheets:
        # Excel sheet names max 31 chars, no special chars
        safe_name = sheet_name[:31].replace("/", "-").replace("\\", "-").replace("*", "").replace("?", "").replace("[", "").replace("]", "").replace(":", "")
        ws = wb.create_sheet(title=safe_name)
        ws.append(["Name", "Price", "Category", "Description"])
        for row in rows:
            ws.append(list(row))
        # Auto-width
        for col in ws.columns:
            max_len = max((len(str(cell.value or "")) for cell in col), default=0)
            ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 80)

    wb.save(output_path)
    log.info("Sparade workbook: %s  (%d flikar)", output_path, len(sheets))


# ---------------------------------------------------------------------------
# City extraction
# ---------------------------------------------------------------------------

def extract_city(address: str) -> str:
    """
    Försöker plocka ut stadsnamnet ur en adress som "Götgatan 12, Stockholm".
    Returnerar sista komma-separerade delen, annars hela adressen.
    """
    parts = [p.strip() for p in address.split(",") if p.strip()]
    return parts[-1] if parts else address


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Scrapa prospekt + konkurrenter till en Excel-fil")
    parser.add_argument("--name", required=True, help="Namn på prospektrestaurangen")
    parser.add_argument("--address", required=True, help="Adress till prospektrestaurangen")
    parser.add_argument(
        "--competitors",
        required=True,
        help="Kommaseparerad lista på konkurrenter, t.ex. 'Pasta Basta, La Famiglia'",
    )
    parser.add_argument("--language", default="sv", help="Föredraget språk för beskrivningar (default: sv)")
    parser.add_argument("--menu-type", default="dinner", help="Typ av meny att skrapa (default: dinner)")
    parser.add_argument("--output", default=None, help="Sökväg för output-fil (default: <Prospektnamn>.xlsx)")
    parser.add_argument("--max-workers", type=int, default=10, help="Max antal parallella scrapningar (default: 10)")
    args = parser.parse_args()

    prospect_name = args.name.strip()
    address = args.address.strip()
    city = extract_city(address)
    competitor_names = [c.strip() for c in args.competitors.split(",") if c.strip()]
    language = args.language
    menu_type = args.menu_type

    output_file = args.output if args.output else f"{prospect_name}.xlsx"

    all_restaurants = (
        [(prospect_name, f"{prospect_name} restaurang {address} hemsida")]
        + [(name, f"{name} restaurang {city} hemsida") for name in competitor_names]
    )

    def scrape_one(name: str, query: str) -> tuple[str, list[tuple[str, str, str, str]]]:
        log.info("=== Startar: %s ===", name)
        try:
            url = find_restaurant_url(query, name)
            if not url:
                log.warning("Hittade ingen URL för '%s' — hoppar över.", name)
                return name, []
            rows = scrape_menu(url, language=language, menu_type=menu_type)
            if not rows:
                log.warning("Ingen meny hittades för '%s'.", name)
            return name, rows
        except Exception as exc:
            log.error("Fel vid bearbetning av '%s': %s", name, exc)
            return name, []

    from concurrent.futures import ThreadPoolExecutor, as_completed

    results: dict[str, list] = {}
    with ThreadPoolExecutor(max_workers=min(len(all_restaurants), args.max_workers)) as executor:
        futures = {
            executor.submit(scrape_one, name, query): name
            for name, query in all_restaurants
        }
        for future in as_completed(futures):
            name, rows = future.result()
            results[name] = rows
            log.info("=== Klar: %s (%d rätter) ===", name, len(rows))

    # Preserve original order
    sheets = [(name, results[name]) for name, _ in all_restaurants]

    # --- Spara ---
    if all(len(rows) == 0 for _, rows in sheets):
        log.error("Inga menyer hittades alls. Sparar ändå en tom fil.")

    write_workbook(sheets, output_file)
    print(f"\nKlar! Resultat: {output_file}")


if __name__ == "__main__":
    main()
