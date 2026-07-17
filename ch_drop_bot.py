#!/usr/bin/env python3
"""
Chrome Hearts drop monitor -> Telegram alert.

The real store (chromehearts.com) runs on Salesforce Commerce Cloud, so each
product is a link like:
    /socks/ch-logo-socks/176354XXXXXX349.html
with a "$255" price and an "OUT OF STOCK" label when sold out.

What this does:
  - Every CHECK_INTERVAL seconds it loads each category page in STORE_URLS.
  - It builds the set of products currently listed and whether each is in stock.
  - It diffs against what it saw last time (saved in seen.json).
  - It pings you on Telegram when a NEW product appears or a sold-out one
    comes back in stock.

First run just records what's there (no spam). Alerts start next cycle.

Setup:
  pip install requests beautifulsoup4
  Fill in BOT_TOKEN and CHAT_ID (see notes at the bottom), then:
  python ch_drop_bot.py
"""

import json
import random
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ------------------------- CONFIG -------------------------
BOT_TOKEN = "PASTE_YOUR_BOT_TOKEN"        # from @BotFather
CHAT_ID = "PASTE_YOUR_CHAT_ID"            # your numeric Telegram id

# Chrome Hearts is category-based (no single "all products" page), so list every
# category you want watched. Add/remove freely.
STORE_URLS = [
    "https://www.chromehearts.com/socks",
    # "https://www.chromehearts.com/scents",
    # "https://www.chromehearts.com/baccarat",
    # "https://www.chromehearts.com/boxers-leggings",
]

CHECK_INTERVAL = 120                     # seconds between full sweeps (keep >=60)
JITTER = 30                              # random 0..JITTER extra seconds per sweep

# Optional: only alert when a title contains one of these (case-insensitive).
# Leave [] to be alerted about every new product.
KEYWORDS = []                            # e.g. ["ring", "hoodie"]

STATE_FILE = Path("seen.json")
BASE = "https://www.chromehearts.com"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# A product detail URL is /<category>/<handle>/<id>.html  (three path segments).
# This deliberately excludes /socks (category) and /terms.html (one segment).
PRODUCT_RE = re.compile(r"^/[^/]+/[^/]+/[^/]+\.html$")
PRICE_RE = re.compile(r"^\$\s?\d")
# ----------------------------------------------------------


def send_telegram(text: str) -> None:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(
            url,
            data={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=20,
        )
        if not r.ok:
            print("Telegram error:", r.status_code, r.text)
    except requests.RequestException as e:
        print("Telegram request failed:", e)


def parse_products(html: str) -> dict:
    """
    Turn a category page into {product_url: {title, price, available}}.

    Works off the anchors themselves (stable), not fragile CSS class names:
    all the links for one product share the same /cat/handle/id.html path, so we
    group by that. Availability is decided per product using a text *window* in
    document order (from this product's first link up to the next product's),
    so an 'OUT OF STOCK' label can never bleed across into a neighbour.
    """
    soup = BeautifulSoup(html, "html.parser")
    groups: dict[str, list] = {}

    for a in soup.find_all("a", href=True):
        path = a["href"].split("?")[0]
        if path.startswith(BASE):
            path = path[len(BASE):]
        if not PRODUCT_RE.match(path):
            continue
        groups.setdefault(BASE + path, []).append(a)

    # Order products by where they first appear, then slice the HTML into
    # non-overlapping windows so each product owns only its own text.
    low = html.lower()
    positions = []
    for url in groups:
        path = url[len(BASE):]
        idx = html.find(path)
        positions.append((idx if idx != -1 else len(html), url))
    positions.sort()

    products = {}
    for i, (start, url) in enumerate(positions):
        end = positions[i + 1][0] if i + 1 < len(positions) else len(html)
        window = low[start:end]

        texts = [a.get_text(" ", strip=True) for a in groups[url]]
        price = next((t for t in texts if PRICE_RE.match(t)), None)
        titles = [t for t in texts if t and not PRICE_RE.match(t)
                  and "out of stock" not in t.lower()]
        title = min(titles, key=len) if titles else url.rsplit("/", 1)[-1]

        available = "out of stock" not in window

        products[url] = {"title": title, "price": price, "available": available}
    return products


def get_current_products() -> dict:
    """Sweep every configured category page into one combined dict."""
    combined = {}
    for url in STORE_URLS:
        try:
            r = requests.get(url, headers=HEADERS, timeout=25)
            r.raise_for_status()
        except requests.RequestException as e:
            print(f"Fetch failed for {url} (retry next sweep):", e)
            continue
        found = parse_products(r.text)
        if not found:
            print(f"  0 products parsed from {url} — markup may have changed.")
        combined.update(found)
    return combined


def load_seen() -> dict:
    return json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}


def save_seen(seen: dict) -> None:
    STATE_FILE.write_text(json.dumps(seen, indent=2))


def passes_keywords(title: str) -> bool:
    return not KEYWORDS or any(k.lower() in title.lower() for k in KEYWORDS)


def check_once(seen: dict) -> dict:
    current = get_current_products()
    if not current:
        return seen

    first_run = len(seen) == 0
    hits = []
    for url, prod in current.items():
        was = seen.get(url)
        is_new = was is None
        restocked = was is not None and not was.get("available") and prod["available"]
        if (is_new or restocked) and prod["available"] and passes_keywords(prod["title"]):
            hits.append((url, prod, "NEW" if is_new else "RESTOCK"))
        seen[url] = prod

    save_seen(seen)

    if first_run:
        print(f"Seeded {len(current)} products. Watching for changes...")
        return seen

    for url, prod, kind in hits:
        price = f" — {prod['price']}" if prod.get("price") else ""
        send_telegram(f"🔔 <b>Chrome Hearts {kind}</b>\n{prod['title']}{price}\n{url}")
        print("Alerted:", kind, prod["title"])

    if not hits:
        print(f"No changes ({len(current)} products live).")
    return seen


def main():
    if "PASTE_YOUR" in BOT_TOKEN or "PASTE_YOUR" in CHAT_ID:
        raise SystemExit("Set BOT_TOKEN and CHAT_ID first.")
    seen = load_seen()
    print("Monitor started.")
    while True:
        seen = check_once(seen)
        time.sleep(CHECK_INTERVAL + random.uniform(0, JITTER))


if __name__ == "__main__":
    main()
