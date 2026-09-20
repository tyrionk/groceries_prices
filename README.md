# Groceries price tracker

Compares your 7 regular products across Albert Heijn (baseline), Jumbo and Lidl.

## Setup

```bash
pip install -r requirements.txt
python scraper.py
```

This must run from a machine/network that can reach `ah.nl`, `jumbo.com`
and `lidl.nl` directly — it will not work from a sandboxed environment
that blocks those domains at the network level.

## Output

- `prices.json` — raw scraped data
- `prices.csv` — spreadsheet-friendly table
- `index.html` — a simple comparison table, AH used as baseline, with
  Jumbo/Lidl shown as % difference (negative = cheaper than AH)

## Keeping it current

- Product pages occasionally get retired/renumbered — if a price stops
  being found for an item, check `products.json` and update that URL.
- Run `python scraper.py` again any time you want fresh prices (e.g. via
  a daily cron job) — it overwrites the three output files each run.
