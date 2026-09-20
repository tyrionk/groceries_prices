#!/usr/bin/env python3
"""
Scrapes current prices for a fixed list of grocery products from
Albert Heijn, Jumbo and Lidl, and writes a comparison table (CSV + HTML)
using AH as the price baseline.

Run:
    pip install -r requirements.txt
    python scraper.py

Notes:
- This must be run from a machine that can reach ah.nl, jumbo.com and
  lidl.nl directly (it will NOT work from a sandboxed/proxied environment
  that blocks those domains).
- ah.nl currently 403s every plain requests.get() here, including from
  GitHub Actions runners - this looks like bot detection on datacenter
  IPs specifically (Jumbo and Lidl scrape fine with the same code).
  Adding more browser-like headers did not help and made Jumbo/Lidl
  worse, so headers are kept minimal. A real fix likely needs a
  headless browser (Playwright) or running from a residential IP.
- Site markup changes over time. If a product's price stops being found,
  open PRICE_EXTRACTION below and add/adjust a pattern, or update the
  product URL in products.json (product pages get retired/renumbered).
"""
import json
import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.8",
}

ROOT = Path(__file__).parent
PRODUCTS_FILE = ROOT / "products.json"
OUTPUT_JSON = ROOT / "prices.json"
OUTPUT_CSV = ROOT / "prices.csv"
OUTPUT_HTML = ROOT / "index.html"

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def fetch(url: str) -> str | None:
    try:
        resp = SESSION.get(url, timeout=20)
        resp.raise_for_status()
        return resp.text
    except requests.RequestException as e:
        print(f"  ! request failed for {url}: {e}", file=sys.stderr)
        return None


def price_from_jsonld(html: str) -> float | None:
    """Look for schema.org Product/Offer price in <script type=application/ld+json>.

    Only trusts a price that hangs off an object explicitly typed Product
    (not any stray "price" key anywhere in the page's JSON-LD graph, which
    picked up unrelated numbers - e.g. from a recommended-products carousel
    or an unrelated schema block - on first attempts against real sites).
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        candidates = data if isinstance(data, list) else [data]
        for item in candidates:
            price = _find_product_price(item)
            if price is not None:
                return price
    return None


def _is_product_type(type_field) -> bool:
    types = type_field if isinstance(type_field, list) else [type_field]
    return any(isinstance(t, str) and t.rstrip("/").split("/")[-1] == "Product" for t in types)


def _find_product_price(obj) -> float | None:
    if isinstance(obj, dict):
        if _is_product_type(obj.get("@type")):
            price = _extract_offer_price(obj.get("offers"))
            if price is not None:
                return price
        for v in obj.values():
            price = _find_product_price(v)
            if price is not None:
                return price
    elif isinstance(obj, list):
        for v in obj:
            price = _find_product_price(v)
            if price is not None:
                return price
    return None


def _extract_offer_price(offers) -> float | None:
    if isinstance(offers, list):
        for o in offers:
            price = _extract_offer_price(o)
            if price is not None:
                return price
        return None
    if isinstance(offers, dict):
        currency = offers.get("priceCurrency")
        if currency and currency != "EUR":
            return None
        if "price" in offers:
            try:
                return float(str(offers["price"]).replace(",", "."))
            except ValueError:
                return None
    return None


def price_from_regex(html: str) -> float | None:
    """Fallback when no typed Product JSON-LD is found: an explicit euro
    amount printed on the page (e.g. "€ 1,79"). Deliberately does NOT match
    bare `"price": N` JSON fields - those turned out to belong to unrelated
    objects (other products, recommendations) on real pages and produced
    nonsense values like "€506" for a loaf of bread."""
    m = re.search(r"€\s?(\d{1,2}[,.]\d{2})", html)
    if m:
        return float(m.group(1).replace(",", "."))
    return None


# Grocery items in this list plausibly cost between 10 cents and 30 euros.
# A price outside that range is almost certainly a mis-extracted number
# (a product ID, weight, rating, unrelated offer, etc.), so it's rejected
# rather than shown as if it were real - as happened on the first two
# scraper runs against the live sites.
PLAUSIBLE_RANGE = (0.10, 30.0)


def extract_price(html: str) -> float | None:
    price = price_from_jsonld(html) or price_from_regex(html)
    if price is None:
        return None
    if not (PLAUSIBLE_RANGE[0] <= price <= PLAUSIBLE_RANGE[1]):
        print(f"    ! extracted price {price} outside plausible range, discarding")
        return None
    return price


def main():
    products = json.loads(PRODUCTS_FILE.read_text())["products"]
    results = []

    for product in products:
        name = product["name"]
        print(f"Scraping: {name}")
        row = {"name": name}
        for store in ("ah", "jumbo", "lidl"):
            entry = product[store]
            print(f"  {store}: {entry['url']}")
            html = fetch(entry["url"])
            price = extract_price(html) if html else None
            row[store] = price
            row[f"{store}_label"] = entry["label"]
            if price is None:
                print(f"    ! could not extract price for {store}")
            time.sleep(1)  # be polite between requests
        results.append(row)

    OUTPUT_JSON.write_text(json.dumps(results, indent=2))
    write_csv(results)
    write_html(results)
    print(f"\nDone. Wrote {OUTPUT_JSON.name}, {OUTPUT_CSV.name}, {OUTPUT_HTML.name}")


def write_csv(results):
    import csv

    with OUTPUT_CSV.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Product", "AH (baseline)", "Jumbo", "Jumbo vs AH %", "Lidl", "Lidl vs AH %"])
        for row in results:
            ah = row.get("ah")
            jumbo = row.get("jumbo")
            lidl = row.get("lidl")
            writer.writerow([
                row["name"],
                ah if ah is not None else "n/a",
                jumbo if jumbo is not None else "n/a",
                pct_diff(ah, jumbo),
                lidl if lidl is not None else "n/a",
                pct_diff(ah, lidl),
            ])


def pct_diff(baseline, value):
    if baseline is None or value is None:
        return "n/a"
    return round((value - baseline) / baseline * 100, 1)


def write_html(results):
    rows_html = []
    for row in results:
        ah = row.get("ah")
        jumbo = row.get("jumbo")
        lidl = row.get("lidl")
        rows_html.append(f"""
        <tr>
          <td>{row['name']}</td>
          <td>{fmt(ah)}</td>
          <td>{fmt(jumbo)} <span class="diff">{fmt_diff(pct_diff(ah, jumbo))}</span></td>
          <td>{fmt(lidl)} <span class="diff">{fmt_diff(pct_diff(ah, lidl))}</span></td>
        </tr>""")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Grocery price comparison</title>
<style>
  body {{ font-family: system-ui, sans-serif; margin: 2rem; background: #fafafa; color: #222; }}
  table {{ border-collapse: collapse; width: 100%; max-width: 800px; }}
  th, td {{ padding: 0.6rem 1rem; border-bottom: 1px solid #ddd; text-align: left; }}
  th {{ background: #eee; }}
  .diff {{ font-size: 0.85em; color: #666; }}
  caption {{ text-align: left; margin-bottom: 1rem; color: #555; }}
</style>
</head>
<body>
  <h1>Grocery price comparison</h1>
  <table>
    <caption>Prices in EUR. AH is the baseline; % shows how Jumbo/Lidl compare to AH (negative = cheaper).</caption>
    <thead>
      <tr><th>Product</th><th>AH</th><th>Jumbo</th><th>Lidl</th></tr>
    </thead>
    <tbody>{''.join(rows_html)}
    </tbody>
  </table>
</body>
</html>"""
    OUTPUT_HTML.write_text(html)


def fmt(value):
    return f"€{value:.2f}" if value is not None else "n/a"


def fmt_diff(value):
    if value == "n/a":
        return ""
    sign = "+" if value > 0 else ""
    return f"({sign}{value}%)"


if __name__ == "__main__":
    main()
