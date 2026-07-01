#!/usr/bin/env python3
"""
Restaurant Menu Scraper — FastAPI
----------------------------------
POST /api/scrape   → kör scraping i bakgrunden, skickar JSON-callback när klart
GET  /health       → hälsokontroll
"""

from __future__ import annotations

import logging

import requests
from fastapi import BackgroundTasks, FastAPI
from pydantic import BaseModel

from pipeline import scrape_restaurants_concurrent

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

def _parse_price(price_str: str) -> int | str:
    try:
        return int(price_str)
    except (ValueError, TypeError):
        return price_str or ""


def _process(req: ScrapeRequest) -> None:
    all_restaurants = [
        (req.restaurant_name, req.address, req.website_url, req.menu_type),
        *((c.name, c.address, c.website_url, req.menu_type) for c in req.competitor_places),
    ]
    place_id_by_name = {req.restaurant_name: req.google_place_id}
    place_id_by_name.update({c.name: c.place_id for c in req.competitor_places})

    scraped = scrape_restaurants_concurrent(all_restaurants)

    ordered = []
    for name, _, _, _ in all_restaurants:
        rows = scraped.get(name, {}).get("rows", [])
        items = [
            {
                "name": row[0],
                "price": _parse_price(row[1]),
                "category": row[2],
                "description": row[3],
            }
            for row in rows
        ]
        ordered.append({"google_place_id": place_id_by_name.get(name, ""), "name": name, "items": items})

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
