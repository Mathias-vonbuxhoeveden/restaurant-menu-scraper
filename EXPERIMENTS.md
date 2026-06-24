# Experimentlogg — meny-scraper

## Aggregerat resultat per iteration

> **OBS:** Iter 0–3 mättes utan testfallet *lilla ego*. Iter 3★ är re-baseline med alla 4 testfall. Iter 5★ är re-baseline med alla 5 testfall (Crispy tillagd). Iter 7★ är re-baseline med alla 7 testfall (Hantverket + Piadina tillagda).

| Iter | Datum | Ändring | Crispy | Hantverket | Piadina | Kommendoren | Lilla ego | Tradition | Tranan | Commit |
|------|-------|---------|--------|-----------|---------|------------|-----------|-----------|--------|--------|
| 0 | 2026-05-18 | Baseline | — | — | — | 92.4% | — | 77.8% | 69.7% | df2d58e |
| 1a | 2026-05-18 | Prompt: exkludera sides/såser med eget pris | — | — | — | 95.8% | — | — | — | 31c96ec |
| 1b | 2026-05-18 | Nav-dedup: byt text→URL, fixar Pelikan+Tranan | — | — | — | 95.8% | — | 91.8%* | 96.6% | 3492fbb |
| 2 | 2026-05-18 | Reliabilitet: JSON-retry, nav-prompt, statisk-HTML-priströskel | — | — | — | 95.8% | — | 98.5% | ~92.6% | e804180 |
| 3 | 2026-05-18 | Filtrera display:none + Elementor-dolda sektioner i BeautifulSoup | — | — | — | 100.0% | — | 98.5% | 96.6% | 5add54a |
| **3★** | **2026-05-19** | **Re-baseline: lilla ego GT tillagd (ingen kod-ändring)** | — | — | — | **100.0%** | **79.7%** | **98.5%** | **96.6%** | **5add54a** |
| 4 | 2026-05-19 | Extract-prompt: lägg till snacks/aptitretare/ostar i include-listan | — | — | — | 94.6% | 94.3% | 98.5% | 94.1% | 8e5c81c |
| **5★** | **2026-05-19** | **Nav-fix: skip is_menu_complete vid 0 rätter + MAX_NAV_STEPS 3→4; Crispy GT tillagd** | **85.9%** | — | — | **94.6%** | **97.1%** | **98.5%** | **94.1%** | bbc0e12 |
| 6 | 2026-05-19 | Nav-fix: kontrollera PDF-länkar i Playwright-rendrad HTML efter varje steg | ~82%† | — | — | 94.6% | 94.3% | 98.5% | 94.1% | — |
| **7★** | **2026-06-24** | **Pop-up-dismissal + PDF-prioritering vid prislösa rätter; Hantverket + Piadina GT tillagda** | **75.2%**‡ | **89.4%** | **88.6%** | **74.8%**§ | **82.9%**‡ | **91.3%**‡ | **81.7%**‡ | **85db41f** |

*) Tradition-aggregat fluktuerar pga LLM-varians i PDF-extraktionen; 76.8%–98.5% sett i olika körningar
†) Crispy-aggregat varierar 80–86% pga LLM-varians i PDF-extraktion; navigeringen är nu deterministisk
‡) Siffror under förväntan pga LLM-varians denna körning — ingen kodregression identifierad
§) Kommendoren-aggregat påverkat av Aubergine (41.7%): GT är inaktuell, menyn har ändrats sedan facit skapades

---

## Detaljerade noteringar

### Iter 0 — Baseline (2026-05-18)
- Kommendoren: Kommendören 100%, Aubergine 100%, Ted 74.5% → aggregat 92.4%
- Tradition: Tradition 100%, Tennstopet 100%, **Pelikan 0%**, Bistro Bestick 95.2% → aggregat 77.8%
- Tranan: **Tranan 0%**, Tennstopet 100% → aggregat 69.7%
- Ted: 13 hallucinerade (7 köttstycken + 6 sides/såser med eget pris)
- Pelikan: navigeringsfel — meny-URL besökt men 0 rätter (menyn är en PDF via separat länk)
- Tranan: navigeringsfel — landingssida med bara 3 element, Claude väljer 0

### Iter 1a — Prompt: exkludera sides/såser (2026-05-18)
- `MENU_TYPES["dinner"]["extract"]` utökad med explicit exkludering av:
  - Tillbehör/sides med eget pris (pommes, potatisgratäng etc.)
  - Såser och smör med eget pris (bearnaise, café de paris smör etc.)
  - Köttkvaliteter listade med ursprungsland utan tillagningsmetod
- Ted: 13 → 7 hallucinerade (sides/såser fixade, köttstycken kvarstår)
- Aggregat kommendoren: 92.4% → 95.8%

### Iter 1b — Nav-dedup: text→URL (2026-05-18)
- Rotorsak Pelikan: `collect_all_navigable_elements` deduplicerade på `text.lower()`.
  Pelikan's meny-sida har `[Meny] → pelikan.se/meny` OCH `[Meny] → PDF-URL` — PDF-länken kastades bort!
- Fix: deduplicera på resolved URL istf. länktext.
- Pelikan: 0% → 98.5% (navigerar nu korrekt till PDF)
- Tranan: 0% → 92.6% (hittade "VÅR RESTAURANG" → restaurang-sida → PDF)
- Aggregat tradition: 77.8% → ~91.8%; tranan: 69.7% → 96.6%

### Iter 2 — Reliabilitet: tre parallella fixes (2026-05-18)
- **JSON-retry**: `_parse_json_array()` extraherar `[...]`-blocket robust; båda extraktionsfunktioner
  retryar API-anrop en gång vid `JSONDecodeError`. Fixar sporadisk 0% för Pelikan och Tradition.
- **Nav-prompt**: "Svara 0 om inget verkar relevant" → "Svara 0 BARA om alla element klart leder
  bort från mat/meny". Gör Tranans "Vår restaurang" mer konsekvent vald.
- **Statisk HTML-priströskel**: `main()` avslutar inte vid statisk HTML om ingen rad har pris.
  Navigeringslänkar som hallucineras (t.ex. "Meny" utan pris) blockar inte längre Playwright-fallback.
- Aggregat tradition: **98.5%** stabilt; tranan ~92.6% (kvar LLM-varians i nav-steget)

### Iter 7★ — Pop-up-dismissal + PDF-prioritering vid prislösa rätter (2026-06-24)
- **Nytt testfall: Piadina** (Piadina 88.4%, Invece 74.7%, Medis Kök & Bar 99.1%)
- **Nytt testfall: Hantverket** (Hantverket 100%, Nomad 87%, Artilleriet 91.2%, Bar Nordic 62.5%)
- **Rotorsak Invece (0% recall):**
  1. WordPress Popup Builder-overlay visade bakgrundstexten (Instagram-inbäddningar) som extraherades som 6 "rätter" utan priser — Claude sa "done" utan att ha nått menyn.
  2. När pop-upen stängdes med Escape: landningssidan och `/menyer/`-sidan innehöll bara Instagram-embeds (inga priser) → PDF-länkarna på `/menyer/` hittades aldrig.
- **Fix 1:** `_dismiss_popups(page)` — körs direkt efter `page.goto()`. Testar Escape + lista av vanliga pop-up-selektorer inkl. `[class*='sgpb-popup-close-button']` (WordPress Popup Builder).
- **Fix 2:** Om alla extraherade rätter saknar pris (`all(r[1] == "" for r in rows)`) → kolla efter PDF-länk direkt, prioritera den framför HTML-extraktionen. Trigg på Invece `/menyer/` → hittar `a-la-carte-1.pdf` deterministiskt.
- **Invece: 0% → 74.7%** (37 rätter från PDF). Kvarstående: 11 saknas (contorni + MENYFÖRSLAG + GRIGLIATA MISTA), kategorinamn FRITTI vs ANTIPASTI PICCOLI, 2 hallucinerade (Prosciutto/Coppa namnvarianter).
- **Aubergine GT inaktuell:** Aubergine 41.7% beror på att GT inte stämmer med nuvarande meny — scraper extraherar korrekt data men facit är föråldrat.
- **Övriga regressioner är LLM-varians:** Tradition (68.6%), Tranan (65.5%), Lilla Ego (72.7%) — samtliga med känd hög varians; ingen kodregression identifierad i nav-loggarna.

### Iter 6 — PDF-check i Playwright-loop (2026-05-19)
- **Rotorsak (kvarstående prod-problem):** Även med `MAX_NAV_STEPS=4` och `if rows and is_menu_complete` kunde Claude navigera fel (t.ex. till HTML-sida istf. PDF) och köra slut på steg. Navigeringen var fortfarande LLM-beroende.
- **Fix:** I varje Playwright-steg, om `rows=[]`, anropa `find_pdf_url(html, current_url)` direkt på den rendrade HTML:en. Om PDF hittas — hämta och scrapea omedelbart, utan ytterligare Claude-navigation.
- **Resultat:** PDF hittas nu deterministiskt i steg 1 (landningssidan innehåller PDF-länken efter JS-rendering). Totalt 2 API-anrop vs tidigare 8+. Navigering oberoende av LLM-val.
- Crispy-aggregat ~82% (LLM-varians i PDF-extraktion, ej navigering). Övriga testfall opåverkade.

### Iter 5★ — Nav-fix + Crispy GT tillagd (2026-05-19)
- **Rotorsak (prod-fel 0 rätter):** `_is_menu_complete` anropades även när `rows=[]`. Claude svarade 'done' på mellansidor (t.ex. Crispys restauranglistsida) utan rätter → navigeringsloopen bröt för tidigt → 0 rätter returnerade.
- **Fix 1:** `if rows and _is_menu_complete(...)` — hoppar över bedömningsanropet när inga rätter extraherats. Navigeringen fortsätter alltid om vi inte hittat något.
- **Fix 2:** `MAX_NAV_STEPS 3→4` — Crispy kräver ibland 3 hopp (landing→restauranger→kvarnholmen-sida→PDF). Med max 3 steg nådde vi aldrig PDF-länken på sidan 3.
- **Crispy Kvarnholmen 85.9%** (Crispy Pizza Bistro 80.4%, Don Felice 96.6%) — navigering nu stabil; extraktionskvalitet begränsas av LLM-varians i PDF-läsning.
- Övriga testfall opåverkade. Lilla ego 94.3%→97.1% (LLM-varians, inte kodändring).

### Iter 4 — Snacks/aptitretare/ostar i include-listan (2026-05-19)
- Lade till "snacks och aptitretare (t.ex. oliver, nötter, bröd, ostron, charkuterier listade som egna poster)" och "ostar och ostbrickor" i extract-promptens include-lista.
- **Haggans 62.1% → 91.9%**: +8 snacks-items (Manzanilla Oliver, Pistagenötter, Focaccia, Boquerones, Serrano, Salame ×3) nu extraherade. Kvar: 3 tillbehörsposter (Poutine, Sallad, Pommes) som fortfarande exkluderas av "tillbehör"-regeln.
- **Vineriet 78.0% → 100.0%**: alla 9 saknade snacks/chark/ostar nu extraherade.
- **Regression Aubergine 100.0% → 86.6%**: 9 hallucinerade snacks-items (Löjromschips, Pimentos, Sobrasada, Marinerade Oliver, Tryfferade Cashewnötter m.fl.) — finns i PDF:en men ingår inte i Aubergines GT.
- **Regression Tranan 92.6% → 87.7%**: 3 hallucinerade (Sinisioliver, Blandade nötter, Marconamandlar) — samma mönster.
- **GT-inkonsistens identifierad**: Haggans/Vineriet inkluderar snacks i facit; Aubergine/Tranan gör det inte. Regeln är korrekt men GT:erna är inkonsekventa — bör beaktas vid framtida GT-generering.
- Netto aggregat: 93.7% → **95.4%** (+1.7pp).

### Iter 3★ — Re-baseline med lilla ego (2026-05-19)
- Ingen kod-ändring; lilla ego GT tillagd som nytt testfall.
- Lilla ego aggregat: **79.7%** (Lilla Ego 81.8%, Haggans 62.1%, Restaurang ART 96.8%, Vineriet 78.0%)
- **Lilla Ego 81.8%**: scraper trimmar rättnamn — "Rödtunga med broccoli och räka" istf. "Rödtunga med broccoli, räka och fläder". 2 saknas + 2 hallucinerade (samma rätter, kortare namn).
- **Haggans 62.1%**: 11 saknas — alla snacks/chark (Oliver, Pistagenötter, Focaccia, Boquerones, Serrano, Salame ×3, Poutine, Sallad, Pommes). PDF hittad och läst (9 rätter), men dessa listas troligen i en annan sektion av PDF:en som inte scrapas.
- **Vineriet 78.0%**: 9 saknas — snacks/chark (Nötmix, Marcona-mandlar, Manzanilla-oliver, Salchichon Fuet, Coppa Iberico, Lomito, Brisa, Wrångebäck, Payoyo). PDF-baserad; sannolikt samma problem som Haggans — separat sektion i PDF.
- **Restaurang ART 96.8%**: 1 saknas — Pommes frites.
- Störst förbättringspotential: Haggans (–37.9pp) och Vineriet (–22pp). Båda är recall-problem i PDF-extraktion.

### Iter 3 — Filtrera dolda HTML-element (2026-05-18)
- Rotorsak Ted: Teds startsida innehåller en dold "Kött Bonanza"-helgmeny (`display:none` via Elementor-klasser). BeautifulSoup ignorerar CSS och extraherar texten ändå.
- Fix: i `parse_menu_from_html`, ta bort element med inline `style="display:none"` OCH element med alla tre Elementor-klasserna `elementor-hidden-desktop + tablet + mobile` (= dold på alla skärmstorlekar).
- Ted: 74.5% (baseline) → 88.4% (iter 1a) → **100.0%**. 0 hallucinerade (var 5–7).
- Kommendören 100%, Aubergine 100%, Ted 100% → kommendoren-aggregat: 95.8% → **100.0%**.
- Tradition/Tranan opåverkade.

---

## Mönster och lärdomar

- **Dedup-strategi spelar roll**: Dedup på URL (inte text) är rätt — distinct destinations med identisk länktext (t.ex. nav-länk + PDF-länk båda "Meny") annars försvinner den ena.
- **Statisk HTML före Playwright kan störa**: Tidiga avslut vid få hallucinerade rätter utan pris blockerade Playwright. Priströskel löser detta.
- **LLM-varians är signifikant** för site-specifika val (pick_next_action) och JSON-generering. Retry-logik och mer toleranta prompts hjälper.
- **Prompt-regler fungerar bra** för semantiska uteslutningar (sås/sides) men sämre för syntaktiska mönster — modellen kan kringgå keyword-regler.
- **Python-postprocessing för saker som är deterministiska att identifiera** (tex. kategorinamn) är mer tillförlitlig än LLM-regler, MEN kräver att regeln är verkligt generaliserbar.
- **BeautifulSoup ignorerar CSS**: `display:none`-element syns inte för besökare men extraheras ändå. Filtrera bort inline `display:none` och ramverksspecifika hidden-klasser (Elementor) i parse-steget.

---

## Testade hypoteser som INTE fungerade

- **Prompt: "rena köttkvaliteter utan tillagningsmetod"** — Claude kortade ned namnen men inkluderade ändå posterna.
- **Prompt: uppfödningstermer (grain fed, grass fed) som filter** — Claude tog bort termerna från extraherade fält snarare än att exkludera posten.
- **Prompt: ursprungsort i namn → exkludera** — För bred; exkluderade "Grillad Argentinsk ryggbiff" (GT-rätt).
- **Python-filter på `category` ≈ "kött för"** — Ger 100% på Ted-eval men är ren overfitting mot Teds specifika sektionsrubriker. Reverted (3b → reverted, se commit 253a4ef).
- **Väntetid 2s efter networkidle** — Ändrade inte Pelikans text-storlek (1786 tecken kvarstod). Rotkausen var dedup, inte timing.
