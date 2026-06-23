# Meny-scraper — optimeringsloop

## Projektstruktur
```
scraper.py                           ← primär fil att ändra (scraping-logik)
api.py                               ← får ändras (produktions-API)
pipeline.py                          ← får ändras (orkestrering)
run_eval.py                          ← får ändras om eval-kedjan måste spegla api.py
evaluate.py                          ← rör aldrig (eval-logik)
tests/cases/<case>/payload.json      ← rör aldrig
tests/cases/<case>/ground_truth.xlsx ← rör aldrig
tests/output/<case>_scraped.xlsx     ← genereras vid körning
EXPERIMENTS.md                       ← uppdatera efter varje iteration
eval_report.json                     ← skrivs över vid varje körning
```

## Köra eval

Alla testfall:
```bash
python run_eval.py --all
```

Ett specifikt testfall:
```bash
python run_eval.py --case <namn>
```

En specifik restaurang inom ett testfall:
```bash
python run_eval.py --case <namn> --only "Restaurangnamn"
```

## Workflow per iteration

1. Läs `EXPERIMENTS.md` — förstå vad som testats och vad som inte funkat
2. Kör `python run_eval.py --all` och läs output noggrant
3. Identifiera vilket testfall/restaurang som drar ner F1 mest
4. Läs nav-loggen för den restaurangen — vad gick snett?
5. Bilda EN hypotes om rotorsaken
6. Gör EN avgränsad ändring i `scraper.py`
7. Kör eval igen (kan köra `--case <namn>` för snabbhet, kör `--all` innan commit)
8. Jämför aggregerat F1 mot föregående iteration
9. Om förbättring: `git commit -m "iter N: <vad> → F1 X.X%"`
10. Om försämring: `git checkout scraper.py` och prova något annat
11. Uppdatera `EXPERIMENTS.md`

## Vad du ska titta efter i eval-output

**Navigeringsproblem (⚠ NAVIGERINGSVARNING):**
- Inga rätter scrapad → scrapen nådde aldrig menysidan
- Låg recall trots Playwright → fler klick krävs
- Dessa är högt prioriterade — ingen mängd prompt-tuning hjälper om sidan inte nås

**Extraktionsproblem (når sidan men missar rätter):**
- Saknas (✗) → recall-problem, rätter finns på sidan men extraheras inte
- Hallucinerade (?) → precision-problem, rätter hittas på som inte finns
- Fel pris/kategori (~) → extraktionslogiken tolkar fel

**Per restaurang F1** — vilken drar ner mest? Börja där.

## Prioriteringsordning
1. Navigeringsproblem först (0 rätter scrapad är alltid fel)
2. Låg recall (saknade rätter) — vanligen viktigare än precision
3. Hallucinerade rätter
4. Fel pris/kategori

## Regler
- Ändra ALDRIG `evaluate.py`, `run_eval.py`, `pipeline.py` eller filer i `tests/`
- Gör EN ändring åt gången
- Committa BARA om aggregerat F1 förbättras (kör `--all` för att verifiera)
- Stoppa och sammanfatta om du inte förbättrat på 5 iterationer i rad
- Prova aldrig något som redan finns under "Testade hypoteser som INTE fungerade" i EXPERIMENTS.md
