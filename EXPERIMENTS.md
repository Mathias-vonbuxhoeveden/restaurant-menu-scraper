# Experimentlogg — meny-scraper

## Aggregerat resultat per iteration

| Iter | Datum | Ändring | Precision | Recall | F1 | Commit |
|------|-------|---------|-----------|--------|----|--------|
| 0 | 2026-05-18 | Baseline | 85.9% | 100.0% | 92.4% | df2d58e |
| 1 | 2026-05-18 | Exkludera sides/såser m eget pris + köttkvaliteter med ursprungsland i namn | 91.9% | 100.0% | 95.8% | 31c96ec |
| 2 | 2026-05-18 | Python-filter: ta bort rätter från "Kött för X"-kategorier + förfina prompt-regler | 100.0% | 100.0% | 100.0% | — |

---

## Detaljerade noteringar

### Iter 0 — Baseline (2026-05-18)
- Testfall: kommendoren (3 restauranger: Kommendören, Restaurant Aubergine, Ted)
- Tradition och Tranan testfall kan inte mätas (ground_truth filen heter ground_truth_tradition.xlsx resp. ground_truth_tranan.xlsx istf. ground_truth.xlsx)
- Kommendören: F1 100%, Restaurant Aubergine: F1 100%, Ted: F1 74.5%
- Ted: 13 hallucinerade rätter
  - 7 köttdelar: "Ryggbiff, USDA Prime, Nebraska, grain fed" etc. (sektion "Kött för en/2 person(er)")
  - 6 sides/såser: Pommes frites, Potatisgratäng, Burratasallad, Bearnaise, Cafe de Paris smör, Grönpepparsås (alla med eget pris)

### Iter 1 — Exkludera sides/såser + köttkvaliteter (2026-05-18)
- Ändring: La till explicit exkludering i `MENU_TYPES["dinner"]["extract"]`:
  - Tillbehör och sides med eget pris (pommes frites, potatisgratäng etc.)
  - Såser och smör med eget pris (bearnaise, café de paris smör etc.)
  - Rena köttkvaliteter listade med ursprungsland utan tillagningsmetod
- Resultat: F1 95.8% (+3.4 pp)
  - Sides/såser fixades (6 → 0 hallucinations)
  - Köttstyckena kvarstod som hallucinerade (7 st) men modellen kortade ned namnen till t.ex. "Ryggbiff"

### Iter 2 — Python-filter för "Kött för X"-kategorier (2026-05-18)
- Hypotes: kategorinamnen "Kött för en person" / "Kött för 2 personer" är konsistenta och pålitliga signaler
- Ändring: La till `_filter_items()` + `_MEAT_SELECTION_CATEGORY_RE` i scraper.py
  - Filtrerar deterministiskt bort alla rätter vars kategori matchar "kött för" (case-insensitive)
  - Appliceras i både `extract_with_claude()` och `parse_pdf()`
- Resultat: F1 100.0% (+4.2 pp) — alla 7 köttstycken eliminerade
- Prompt-regel om uppfödningstermer (grain fed/grass fed etc.) finns kvar som extra skydd

---

## Mönster och lärdomar

- **Prompt-regler är opålitliga för deterministisk filtrering**: Modellen kan kringgå regler genom att flytta termer från namn till beskrivning eller byta namn.
- **Python-filtrering på kategorinamn är mer tillförlitlig**: Modellen kopierar konsekvent sidans sektionsrubriker till category-fältet, vilket ger en stabil ankarpunkt.
- **"Kött för X"-sektioner är ett identifierat mönster**: Restauranger (särskilt grill-/köttkrogs-koncept) listar köttkvaliteter i beställningssektioner som inte bör extraheras som enskilda rätter.
- **Sides/såser med eget pris**: Prompt-regel effektiv för att exkludera sides och såser även när de har priser.
- **LLM-varians**: Haiku ger olika resultat per körning. --only Ted-körningar ger ibland bättre resultat än full run. Deterministisk Python-post-processing minskar variansen.

---

## Testade hypoteser som INTE fungerade

- **"rena köttkvaliteter... utan tillagningsmetod i namnet"**: Modellen kortade ned namnen till t.ex. "Ryggbiff" och inkluderade ändå itemet. Regeln uppfylldes formellt men inte i anda.
- **"köttvalssektioner märkta 'Kött för en person'"**: Inkonsistent — fungerade i --only Ted men inte i full run. Modellen ignorerade regeln i vissa körningar.
- **"uppfödningstermer (grain fed, grass fed, milk fed) i namn eller beskrivning"**: Modellen tog bort termerna från de extraherade fälten snarare än att exkludera rätterna.
- **"ursprungsland följt av cut-name"**: För bred — exkluderade "Grillad Argentinsk ryggbiff" (GT-rätt) eftersom "Argentinsk" tolkades som ursprungsland.
