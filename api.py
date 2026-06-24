#!/usr/bin/env python3
"""
Restaurant Menu Scraper — FastAPI
----------------------------------
POST /api/scrape   → kör scraping i bakgrunden, skickar JSON-callback när klart
GET  /health       → hälsokontroll
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from fastapi import BackgroundTasks, FastAPI
from pydantic import BaseModel

from pipeline import scrape_menu

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

app = FastAPI()


# ---------------------------------------------------------------------------
# Modeller
# ---------------------------------------------------------------------------

class CompetitorPlace(BaseModel):
    name: str
    place_id: str
    address: str = ""
    website_url: str = ""


class ScrapeRequest(BaseModel):
    prospect_id: str
    slug: str = ""
    restaurant_name: str
    address: str = ""
    email: str = ""
    menu_type: str = "dinner"
    google_place_id: str = ""
    website_url: str = ""
    competitor_places: list[CompetitorPlace] = []
    callback_url: str = ""


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/scrape", status_code=202)
def scrape(req: ScrapeRequest, background_tasks: BackgroundTasks):
    background_tasks.add_task(_process, req)
    return {"message": "Scraping started", "prospect_id": req.prospect_id}


# ---------------------------------------------------------------------------
# Bakgrundshantering
# ---------------------------------------------------------------------------

def _scrape_one(name: str, address: str, place_id: str, website_url: str, menu_type: str) -> dict:
    log.info("=== Startar: %s ===", name)
    items = []
    if website_url:
        try:
            rows = scrape_menu(website_url, menu_type=menu_type, restaurant_name=name, restaurant_address=address)
            items = [
                {
                    "name": row[0],
                    "price": _parse_price(row[1]),
                    "category": row[2],
                    "description": row[3],
                }
                for row in rows
            ]
        except Exception as exc:
            log.error("Scraping misslyckades för '%s': %s", name, exc)
    else:
        log.warning("Ingen website_url för '%s' — hoppar över", name)

    log.info("=== Klar: %s (%d rätter) ===", name, len(items))
    return {"google_place_id": place_id, "name": name, "items": items}


def _parse_price(price_str: str) -> int | str:
    try:
        return int(price_str)
    except (ValueError, TypeError):
        return price_str or ""


def _process(req: ScrapeRequest) -> None:
    all_restaurants = [
        (req.restaurant_name, req.address, req.google_place_id, req.website_url),
        *((c.name, c.address, c.place_id, c.website_url) for c in req.competitor_places),
    ]

    results: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=len(all_restaurants)) as executor:
        futures = {
            executor.submit(_scrape_one, name, address, place_id, website_url, req.menu_type): name
            for name, address, place_id, website_url in all_restaurants
        }
        for future in as_completed(futures):
            result = future.result()
            results[result["name"]] = result

    ordered = [results[name] for name, _, _, _ in all_restaurants if name in results]

    payload = {
        "prospect_id": req.prospect_id,
        "menu_type": req.menu_type,
        "google_place_id": req.google_place_id,
        "competitor_places": [
            {"name": c.name, "place_id": c.place_id}
            for c in req.competitor_places
        ],
        "restaurants": ordered,
    }

    if req.callback_url:
        _send_callback(req.callback_url, payload, req.prospect_id)
    else:
        log.warning("[%s] Ingen callback_url — resultatet loggas bara", req.prospect_id)


def _send_callback(url: str, payload: dict, prospect_id: str) -> None:
    try:
        resp = requests.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        log.info("[%s] Callback skickad (HTTP %d)", prospect_id, resp.status_code)
    except Exception as exc:
        log.error("[%s] Callback misslyckades: %s", prospect_id, exc)
