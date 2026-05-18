# Experimentlogg — meny-scraper

## Aggregerat resultat per iteration

| Iter | Datum | Ändring | Kommendoren | Tradition | Tranan | Commit |
|------|-------|---------|------------|-----------|--------|--------|
| 0 | 2026-05-18 | Baseline | 92.4% | 77.8% | 69.7% | df2d58e |
| 1a | 2026-05-18 | Prompt: exkludera sides/såser med eget pris | 95.8% | — | — | 31c96ec |
| 1b | 2026-05-18 | Nav-dedup: byt text→URL, fixar Pelikan+Tranan | 95.8% | 91.8%* | 96.6% | 3492fbb |
| 2 | 2026-05-18 | Reliabilitet: JSON-retry, nav-prompt, statisk-HTML-priströskel | 95.8% | 98.5% | ~92.6% | e804180 |
| 3 | 2026-05-18 | Filtrera display:none + Elementor-dolda sektioner i BeautifulSoup | 100.0% | 98.5% | 96.6% | — |

*) Tradition-aggregat fluktuerar pga LLM-varians i PDF-extraktionen; 76.8%–98.5% sett i olika körningar

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

### Iter 3 — Filtrera dolda HTML-element (2026-05-18)
- Rotorsak Ted: Teds startsida innehåller en dold "Kött Bonanza"-helgmeny (`display:none` via Elementor-klasser). BeautifulSoup ignorerar CSS och extraherar texten ändå.
- Fix: i `parse_menu_from_html`, ta bort element med inline `style="display:none"` OCH element med alla tre Elementor-klasserna `elementor-hidden-desktop + tablet + mobile` (= dold på alla skärmstorlekar).
- Ted: 5–7 hallucinerade → **0**. Kommendoren-aggregat: 95.8% → **100.0%**.
- Tradition/Tranan opåverkade.

### Iter 2 — Reliabilitet: tre parallella fixes (2026-05-18)
- **JSON-retry**: `_parse_json_array()` extraherar `[...]`-blocket robust; båda extraktionsfunktioner
  retryar API-anrop en gång vid `JSONDecodeError`. Fixar sporadisk 0% för Pelikan och Tradition.
- **Nav-prompt**: "Svara 0 om inget verkar relevant" → "Svara 0 BARA om alla element klart leder
  bort från mat/meny". Gör Tranans "Vår restaurang" mer konsekvent vald.
- **Statisk HTML-priströskel**: `main()` avslutar inte vid statisk HTML om ingen rad har pris.
  Navigeringslänkar som hallucineras (t.ex. "Meny" utan pris) blockar inte längre Playwright-fallback.
- Aggregat tradition: **98.5%** stabilt; tranan ~92.6% (kvar LLM-varians i nav-steget)

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
