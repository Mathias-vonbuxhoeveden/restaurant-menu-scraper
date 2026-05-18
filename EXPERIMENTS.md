# Experimentlogg — meny-scraper

## Aggregerat resultat per iteration

| Iter | Datum | Ändring | Precision | Recall | F1 | Commit |
|------|-------|---------|-----------|--------|----|--------|
| 0 | 2026-05-18 | Baseline | 85.9% | 100.0% | 92.4% | df2d58e |
| 1 | 2026-05-18 | Prompt: exkludera sides/såser med eget pris | 91.9% | 100.0% | 95.8% | 31c96ec |

---

## Detaljerade noteringar

### Iter 0 — Baseline (2026-05-18)
- Testfall: kommendoren (3 restauranger: Kommendören, Restaurant Aubergine, Ted)
- **OBS:** Tradition och Tranan kan inte mätas — ground_truth-filerna heter `ground_truth_tradition.xlsx` / `ground_truth_tranan.xlsx` istf. `ground_truth.xlsx`
- Kommendören: F1 100%, Restaurant Aubergine: F1 100%, Ted: F1 74.5%
- Ted: 13 hallucinerade rätter
  - 7 köttstycken med ursprung/uppfödning: "Ryggbiff, USDA Prime, Nebraska, grain fed" m.fl. (sektion "Kött för en/2 person(er)")
  - 6 sides/såser med eget pris: Pommes frites, Potatisgratäng, Burratasallad, Bearnaise, Cafe de Paris smör, Grönpepparsås

### Iter 1 — Prompt: exkludera sides/såser (2026-05-18)
- Ändring: utökade exkluderingslistan i `MENU_TYPES["dinner"]["extract"]` med:
  - Tillbehör och sides **även om de har eget pris**
  - Såser och smör **även om de har eget pris**
  - Rena köttkvaliteter med ursprungsland/uppfödningsmetod utan tillagningsmetod i namnet
- Resultat: **F1 95.8%** (+3.4 pp)
  - Sides/såser fixades helt (6 → 0 hallucinationer)
  - Köttstyckena kvarstod som hallucinerade (7 st) — modellen kortade ned namnen ("Ryggbiff" istf. "Ryggbiff, USDA Prime, Nebraska, grain fed") men inkluderade ändå posterna

---

## Mönster och lärdomar

- **Prompt-regler funkar bra för semantiskt tydliga uteslutningar** (bearnaise är en sås, inte en rätt) — iter 1 fixade sides/såser stabilt.
- **Prompt-regler är opålitliga för syntaktiska mönster** — modellen kringgår "exkludera om namn innehåller X" genom att flytta X till `description` eller byta namn, och ändå inkludera posten.
- **LLM-varians är real** — `--only Ted` ger ibland färre hallucinationer än full run, beroende på stokastisk sampling. Mät alltid med `--all` / `--case` för konsekventa siffror.

---

## Testade hypoteser som INTE fungerade

- **Prompt: "rena köttkvaliteter listade med ursprungsland utan tillagningsmetod"** — Modellen kortade ned namnen syntaktiskt ("Ryggbiff") men inkluderade ändå posten. Regeln uppfylldes formellt men inte i anda.
- **Prompt: "poster vars namn innehåller grain fed / grass fed / USDA Prime"** — Modellen tog bort termerna från extraherade fält snarare än att exkludera posten.
- **Prompt: "ursprungsort följt av cut-name"** — För bred; exkluderade "Grillad Argentinsk ryggbiff" (GT-rätt) eftersom "Argentinsk" tolkades som ursprungsland.
- **Python-filter på `category` ≈ "kött för"** — Ger 100% på eval men är ren overfitting mot Teds specifika sektionsrubriker. Reverted.
