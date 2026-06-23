#!/usr/bin/env python3
"""
run_eval.py — End-to-end eval pipeline
Laddar en payload, kör scraping lokalt, och utvärderar mot ground truth.

Usage:
    python run_eval.py --case tradition
    python run_eval.py --case tranan --only "Tranan"
    python run_eval.py --all
    python run_eval.py --case tradition --scraped tests/output/tradition_scraped.xlsx

    # Bakåtkompatibelt:
    python run_eval.py --payload eval-payload-tranan.json --ground-truth ground_truth.xlsx
"""

import argparse
import io
import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import redirect_stdout
from pathlib import Path

import openpyxl

from evaluate import (
    EvaluationResult,
    evaluate_exact,
    f1,
    name_similarity,
    print_aggregate_report,
    print_report,
    read_xlsx_all_sheets,
)
from pipeline import scrape_menu, write_workbook

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ─── ANSI colors ─────────────────────────────────────────────────────────────
BOLD  = ""
CYAN  = ""
DIM   = ""
RESET = ""
RED   = "\033[91m"

# ─── Navigation issue detection ───────────────────────────────────────────────
NAV_WARN_RECALL_THRESHOLD = 0.4


def detect_nav_issues(nav_log: str | None, n_scraped: int, result: EvaluationResult) -> list[str]:
    """
    Returns a list of human-readable warnings if the nav log or eval result
    suggests the scraper failed to navigate to the menu correctly.
    """
    issues = []

    if n_scraped == 0:
        issues.append("Inga rätter scrapad — scrapen nådde troligen inte menysidan")
    elif n_scraped < 5:
        issues.append(f"Endast {n_scraped} rätt(er) scrapad — suspekt lågt antal")

    if nav_log:
        if "Claude hittade ingen tydlig menylänk" in nav_log:
            issues.append("Ingen menylänk hittades — menyn kan ligga bakom fler klick")
        if "Claude hittade ingen relevant flik" in nav_log:
            issues.append("Ingen relevant flik/knapp hittades — menyval kan kräva ett extra klick-steg")
        if "Playwright" in nav_log and result.recall < NAV_WARN_RECALL_THRESHOLD and n_scraped > 0:
            issues.append(
                f"Låg recall ({result.recall:.0%}) trots Playwright-navigering — "
                "trolig navigeringsproblematik (t.ex. kräver fler klick)"
            )

    return issues


# ---------------------------------------------------------------------------
# Payload loading
# ---------------------------------------------------------------------------

def load_payload(path: str) -> dict:
    raw = Path(path).read_text(encoding="utf-8")
    return json.loads(raw)


def restaurants_from_payload(payload: dict) -> list[tuple[str, str, str, str]]:
    """Returns [(name, address, website_url, menu_type), …] for prospect + competitors."""
    menu_type = payload.get("menu_type", "dinner")
    result = [(payload["restaurant_name"], payload.get("address", ""), payload.get("website_url", ""), menu_type)]
    for c in payload.get("competitor_places", []):
        result.append((c["name"], c.get("address", ""), c.get("website_url", ""), menu_type))
    return result


# ---------------------------------------------------------------------------
# Scrape phase — runs scrape_menu() per restaurant, captures nav log
# ---------------------------------------------------------------------------

def _scrape_one(name: str, address: str, url: str, menu_type: str) -> tuple[str, list, str]:
    """Returns (name, rows, nav_log)."""
    if not url:
        return name, [], "(ingen URL)"

    stdout_buf = io.StringIO()
    log_buf    = io.StringIO()

    # Attach a temporary log handler so WARNING/INFO from scraper ends up in log_buf
    handler = logging.StreamHandler(log_buf)
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter("%(levelname)s  %(message)s"))
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)

    try:
        with redirect_stdout(stdout_buf):
            rows = scrape_menu(url, menu_type=menu_type, restaurant_name=name, restaurant_address=address)
    except Exception as exc:
        rows = []
        print(f"EXCEPTION: {exc}", file=stdout_buf)
    finally:
        root_logger.removeHandler(handler)

    nav_log = f"URL: {url}\n" + stdout_buf.getvalue() + log_buf.getvalue()
    return name, rows, nav_log


def scrape_phase(
    restaurants: list[tuple[str, str, str, str]],
    output_xlsx: str,
    only: str | None,
) -> tuple[dict[str, list], dict[str, str]]:
    """
    Runs scraping in parallel.
    Returns:
        scraped_rows:  {name: [(Rätt, Pris, Kategori, Beskrivning), ...]}
        nav_logs:      {name: nav_log_string}
    """
    if only:
        needle = only.lower()
        restaurants = [(n, a, u, m) for n, a, u, m in restaurants if needle in n.lower()]
        if not restaurants:
            log.error("--only '%s' matchade ingen restaurang i payloaden.", only)
            sys.exit(1)

    log.info("Skrapar %d restauranger: %s", len(restaurants), [n for n, _, _, _ in restaurants])

    scraped_rows: dict[str, list] = {}
    nav_logs:     dict[str, str]  = {}

    for name, address, url, menu_type in restaurants:
        name, rows, nav_log = _scrape_one(name, address, url, menu_type)
        scraped_rows[name] = rows
        nav_logs[name]     = nav_log

    # Save scraped data to Excel (preserving payload order)
    sheets = [(name, scraped_rows[name]) for name, _, _, _ in restaurants if name in scraped_rows]
    write_workbook(sheets, output_xlsx)
    log.info("Scrapad data sparad: %s", output_xlsx)

    return scraped_rows, nav_logs


# ---------------------------------------------------------------------------
# GT reading — handles title row + real header row format
# ---------------------------------------------------------------------------

def read_gt_xlsx(path: str) -> dict[str, list[dict]]:
    """
    Reads a ground-truth workbook where each sheet has:
      Row 1: title (e.g. "TRANAN MENU")
      Row 2: actual headers ("Dish Name", "Price (SEK)", "Section", "Description")
      Row 3+: ▼ section markers (skipped) and data rows
    Returns {sheet_name: [{"Name", "Price", "Category", "Description"}, ...]}
    """
    wb = openpyxl.load_workbook(path)
    result: dict[str, list[dict]] = {}

    for ws in wb.worksheets:
        rows_raw = list(ws.iter_rows(values_only=True))
        # Find the header row — first row that contains "Dish Name" or "Name"
        header_idx = None
        for i, row in enumerate(rows_raw):
            cells = [str(c).strip() if c is not None else "" for c in row]
            if any(c in ("Dish Name", "Name", "name") for c in cells):
                header_idx = i
                break

        if header_idx is None:
            result[ws.title] = []
            continue

        headers = [str(c).strip() if c is not None else "" for c in rows_raw[header_idx]]

        data: list[dict] = []
        for row in rows_raw[header_idx + 1:]:
            cells = [str(c).strip() if c is not None else "" for c in row]
            if not any(cells):
                continue
            first = cells[0]
            if first.startswith("▼") or not first:
                continue  # section marker or empty
            raw = dict(zip(headers, cells))
            # Normalise to standard keys
            data.append({
                "Name":        raw.get("Dish Name") or raw.get("Name") or "",
                "Price":       raw.get("Price (SEK)") or raw.get("Price") or "",
                "Category":    raw.get("Section") or raw.get("Category") or "",
                "Description": raw.get("Description") or "",
            })

        result[ws.title] = data

    return result


# ---------------------------------------------------------------------------
# Sheet-name matching — fuzzy, since GT names often differ from payload names
# ---------------------------------------------------------------------------

GT_MATCH_THRESHOLD = 0.5


def _sheet_similarity(gt: str, scraped: str) -> float:
    """
    Compare GT sheet name against scraped sheet name.
    Also tries matching GT against just the first word(s) of scraped,
    to handle cases like "Tennsdopet" vs "Tennstopet Restaurang - Bar & Pub".
    """
    g, s = gt.lower().strip(), scraped.lower().strip()
    full_score = name_similarity(g, s)
    # Also compare GT against the first N words of scraped (where N = word count of GT)
    gt_words  = g.split()
    src_words = s.split()
    prefix = " ".join(src_words[:max(len(gt_words), 1)])
    prefix_score = name_similarity(g, prefix)
    return max(full_score, prefix_score)


def _match_gt_to_scraped(gt_names: list[str], scraped_names: list[str]) -> dict[str, str | None]:
    """
    Returns {gt_name: scraped_name | None}.
    Uses _sheet_similarity; picks the best match above threshold.
    """
    mapping: dict[str, str | None] = {}
    used: set[str] = set()

    for gt in gt_names:
        best_name, best_score = None, 0.0
        for scraped in scraped_names:
            if scraped in used:
                continue
            score = _sheet_similarity(gt, scraped)
            if score > best_score:
                best_score, best_name = score, scraped
        if best_name and best_score >= GT_MATCH_THRESHOLD:
            mapping[gt] = best_name
            used.add(best_name)
        else:
            mapping[gt] = None

    return mapping


# ---------------------------------------------------------------------------
# Evaluate phase
# ---------------------------------------------------------------------------

def evaluate_phase(
    scraped_xlsx: str,
    gt_xlsx: str,
    nav_logs: dict[str, str],
    only: str | None,
) -> None:
    scraped_sheets  = read_xlsx_all_sheets(scraped_xlsx)
    gt_sheets_clean = read_gt_xlsx(gt_xlsx)

    # Filter GT sheets based on --only
    if only:
        needle = only.lower()
        gt_sheets_clean = {k: v for k, v in gt_sheets_clean.items() if needle in k.lower()}

    name_mapping = _match_gt_to_scraped(list(gt_sheets_clean), list(scraped_sheets))

    print(f"\n{BOLD}{CYAN}{'─'*58}{RESET}")
    print(f"{BOLD}{CYAN}  SHEET-MATCHNING (GT → Scraped){RESET}")
    print(f"{BOLD}{CYAN}{'─'*58}{RESET}")
    for gt_name, scraped_name in name_mapping.items():
        if scraped_name:
            print(f"  {gt_name!r:40s} → {scraped_name!r}")
        else:
            print(f"  {gt_name!r:40s} → \033[91m(ingen match)\033[0m")

    results:              dict[str, EvaluationResult] = {}
    missing_restaurants:  list[str]                   = []
    nav_issues:           dict[str, list[str]]         = {}

    for gt_name, gt_rows in gt_sheets_clean.items():
        scraped_name = name_mapping.get(gt_name)

        # ── Nav log for this restaurant ──────────────────────────────────
        nav_key = scraped_name or gt_name
        nav_log = nav_logs.get(nav_key) or next(
            (v for k, v in nav_logs.items() if name_similarity(k.lower(), nav_key.lower()) >= GT_MATCH_THRESHOLD),
            None,
        )

        if nav_log:
            print(f"\n{BOLD}{CYAN}NAV-LOG: {nav_key}{RESET}")
            for line in nav_log.strip().splitlines():
                print(f"  {DIM}{line}{RESET}")

        # ── Evaluation ──────────────────────────────────────────────────
        if scraped_name is None:
            missing_restaurants.append(gt_name)
            results[gt_name] = evaluate_exact([], gt_rows)
        else:
            results[gt_name] = evaluate_exact(scraped_sheets[scraped_name], gt_rows)

        # ── Navigation issue detection ───────────────────────────────────
        n_scraped = len(scraped_sheets.get(scraped_name, [])) if scraped_name else 0
        issues = detect_nav_issues(nav_log, n_scraped, results[gt_name])
        if issues:
            nav_issues[gt_name] = issues
            bar = "─" * 58
            print(f"\n{RED}{bar}{RESET}")
            print(f"{RED}  ⚠  NAVIGERINGSVARNING — {gt_name}{RESET}")
            print(f"{RED}{bar}{RESET}")
            for issue in issues:
                print(f"{RED}  → {issue}{RESET}")

    for name, result in results.items():
        print_report(result, name)

    print_aggregate_report(results, missing_restaurants)

    if nav_issues:
        bar = "=" * 58
        print(f"\n{BOLD}{RED}{bar}{RESET}")
        print(f"{BOLD}{RED}  ⚠  NAVIGERINGSPROBLEM — SAMMANFATTNING{RESET}")
        print(f"{BOLD}{RED}{bar}{RESET}")
        for name, issues in nav_issues.items():
            print(f"\n  {RED}{name}{RESET}")
            for issue in issues:
                print(f"    → {issue}")
        print()

    # Save JSON report
    report_path = "eval_report.json"
    report = {
        "restaurants": {
            name: {
                "matched":              [m.model_dump() for m in res.matched],
                "wrong_fields":         [w.model_dump() for w in res.wrong_fields],
                "missing":              res.missing,
                "hallucinated":         res.hallucinated,
                "precision":            res.precision,
                "recall":               res.recall,
                "f1":                   f1(res.precision, res.recall),
                "nav_issues":           nav_issues.get(name, []),
            }
            for name, res in results.items()
        },
        "missing_restaurants": missing_restaurants,
    }
    Path(report_path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"JSON-rapport sparad: {report_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

CASES_DIR = Path("tests/cases")
OUTPUT_DIR = Path("tests/output")


def _run_case(
    case_name: str,
    scraped_override: str | None,
    output_override: str | None,
    only: str | None,
) -> None:
    case_dir = CASES_DIR / case_name
    payload_path     = case_dir / "payload.json"
    ground_truth_path = case_dir / "ground_truth.xlsx"

    if not payload_path.exists():
        log.error("Hittade ingen payload: %s", payload_path)
        sys.exit(1)
    if not ground_truth_path.exists():
        log.error("Hittade ingen ground truth: %s", ground_truth_path)
        sys.exit(1)

    payload = load_payload(str(payload_path))

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    scraped_xlsx = scraped_override or output_override or str(OUTPUT_DIR / f"{case_name}_scraped.xlsx")

    if scraped_override:
        log.info("Hoppar över scraping — använder: %s", scraped_override)
        nav_logs: dict[str, str] = {}
    else:
        restaurants = restaurants_from_payload(payload)
        _, nav_logs = scrape_phase(restaurants, scraped_xlsx, only)

    evaluate_phase(scraped_xlsx, str(ground_truth_path), nav_logs, only)


def main() -> None:
    parser = argparse.ArgumentParser(description="End-to-end eval: payload → scraping → jämförelse mot ground truth")

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--case",    metavar="NAMN", help="Kör ett testfall från tests/cases/<NAMN>/")
    mode.add_argument("--all",     action="store_true", help="Kör alla testfall i tests/cases/")
    mode.add_argument("--payload", metavar="FIL",  help="(Bakåtkompatibelt) Payload-JSON direkt")

    parser.add_argument("--ground-truth", default=None, help="(Bakåtkompatibelt) Ground truth Excel direkt")
    parser.add_argument("--scraped",      default=None, help="Befintlig scrapad Excel — hoppar över scraping-steget")
    parser.add_argument("--output",       default=None, help="Sökväg för scrapad Excel")
    parser.add_argument("--only",         default=None, help="Testa bara en restaurang (delsträcksökning på namn)")
    args = parser.parse_args()

    if args.all:
        if not CASES_DIR.exists():
            log.error("Katalogen %s saknas.", CASES_DIR)
            sys.exit(1)
        cases = sorted(p.name for p in CASES_DIR.iterdir() if p.is_dir())
        if not cases:
            log.error("Inga testfall hittade i %s", CASES_DIR)
            sys.exit(1)
        log.info("Kör %d testfall: %s", len(cases), cases)
        for case_name in cases:
            print(f"\n{'='*58}")
            print(f"  TESTFALL: {case_name.upper()}")
            print(f"{'='*58}")
            _run_case(case_name, scraped_override=None, output_override=None, only=args.only)

    elif args.case:
        _run_case(args.case, scraped_override=args.scraped, output_override=args.output, only=args.only)

    else:
        # Bakåtkompatibelt läge
        if not args.payload or not args.ground_truth:
            parser.error("Ange --case <namn>, --all, eller --payload + --ground-truth")

        payload = load_payload(args.payload)
        slug    = payload.get("slug") or payload.get("restaurant_name", "output").replace(" ", "_").lower()
        scraped_xlsx = args.scraped or args.output or f"{slug}_scraped.xlsx"

        if args.scraped:
            log.info("Hoppar över scraping — använder: %s", args.scraped)
            nav_logs: dict[str, str] = {}
        else:
            restaurants = restaurants_from_payload(payload)
            _, nav_logs = scrape_phase(restaurants, scraped_xlsx, args.only)

        evaluate_phase(scraped_xlsx, args.ground_truth, nav_logs, args.only)


if __name__ == "__main__":
    main()
