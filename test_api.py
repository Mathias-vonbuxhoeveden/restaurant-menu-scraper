"""Regression tests for api.py — exercises _process without real scraping."""

from unittest.mock import MagicMock, patch

from api import CompetitorPlace, ScrapeRequest, _process


def _fake_scrape_one(name, address, place_id, website_url, menu_type):
    return {"google_place_id": place_id, "name": name, "items": []}


def _make_request(**kwargs):
    defaults = dict(
        prospect_id="test-123",
        restaurant_name="Hantverket",
        address="Kungsgatan 1",
        google_place_id="place_A",
        website_url="https://hantverket.se",
        menu_type="dinner",
        callback_url="https://example.com/callback",
        competitor_places=[
            CompetitorPlace(name="Nomad", place_id="place_B", address="Addr B", website_url="https://nomad.se"),
            CompetitorPlace(name="Artilleriet", place_id="place_C", address="Addr C", website_url="https://artilleriet.se"),
        ],
    )
    defaults.update(kwargs)
    return ScrapeRequest(**defaults)


@patch("api._send_callback")
@patch("api._scrape_one", side_effect=_fake_scrape_one)
def test_process_does_not_crash_with_competitors(mock_scrape, mock_callback):
    req = _make_request()
    _process(req)
    mock_callback.assert_called_once()
    payload = mock_callback.call_args[0][1]
    names = [r["name"] for r in payload["restaurants"]]
    assert names == ["Hantverket", "Nomad", "Artilleriet"]


@patch("api._send_callback")
@patch("api._scrape_one", side_effect=_fake_scrape_one)
def test_process_no_competitors(mock_scrape, mock_callback):
    req = _make_request(competitor_places=[])
    _process(req)
    payload = mock_callback.call_args[0][1]
    assert len(payload["restaurants"]) == 1
    assert payload["restaurants"][0]["name"] == "Hantverket"
