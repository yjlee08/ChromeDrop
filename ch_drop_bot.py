#!/usr/bin/env python3
"""
Chrome Hearts drop monitor -> Telegram alert.

The real store (chromehearts.com) runs on Salesforce Commerce Cloud, so each
product is a link like:
    /socks/ch-logo-socks/176354XXXXXX349.html
with a "$255" price and an "OUT OF STOCK" label when sold out.

What this does:
  - Every CHECK_INTERVAL seconds (plus jitter) it loads each watched category
    page, building the set of products currently listed and whether each is in
    stock. Categories are discovered from the site's top nav, with a configured
    fallback list.
  - It diffs against what it saw last time (saved in a configurable seen.json).
  - It pings you on Telegram when a NEW product appears or a sold-out one
    comes back in stock. Alerts are batched and throttled.

The site sits behind Akamai bot protection, so the fetch layer tries plain
`requests` first and transparently falls back to a headless Playwright browser
when it hits a 403 / JS challenge / empty parse. Strategy is configurable:
auto (default) | requests | playwright.

First run just records what's there (no spam). Alerts start next cycle.

Setup:
  pip install -r requirements.txt
  cp .env.example .env   # then fill in BOT_TOKEN and CHAT_ID
  python tg_setup.py     # find your CHAT_ID + send a test message
  python ch_drop_bot.py
"""

import logging
import logging.handlers
import os
import json
import random
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()  # pull BOT_TOKEN / CHAT_ID / tunables from a local .env if present

log = logging.getLogger("ch_drop_bot")


# ------------------------- CONFIG -------------------------
def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _env_list(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name)
    if not raw:
        return list(default)
    return [item.strip() for item in raw.split(",") if item.strip()]


BASE = "https://www.chromehearts.com"

# Secrets — never hardcode these; they come from the environment / .env.
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")

# Fetch strategy: auto | requests | playwright
FETCH_STRATEGY = os.getenv("FETCH_STRATEGY", "auto").strip().lower()

# Chrome Hearts is category-based (no single "all products" page). We try to
# discover categories from the top nav each sweep; this is the fallback list
# used when discovery fails or is disabled.
STORE_URLS = _env_list(
    "STORE_URLS",
    [
        f"{BASE}/socks",
        f"{BASE}/scents",
        f"{BASE}/baccarat",
        f"{BASE}/boxers-leggings",
        f"{BASE}/intimates",
    ],
)

# Scrape the nav for categories automatically (catches new categories).
DISCOVER_CATEGORIES = os.getenv("DISCOVER_CATEGORIES", "true").strip().lower() in (
    "1", "true", "yes", "on",
)

CHECK_INTERVAL = _env_int("CHECK_INTERVAL", 120)   # base seconds between sweeps
JITTER = _env_int("JITTER", 30)                    # random 0..JITTER extra sec/sweep
PER_URL_DELAY = _env_int("PER_URL_DELAY", 3)       # politeness pause between pages
MAX_FETCH_RETRIES = _env_int("MAX_FETCH_RETRIES", 4)  # per-URL retry attempts
BACKOFF_BASE = _env_int("BACKOFF_BASE", 2)         # exponential backoff base (sec)

# Optional: only alert when a title contains one of these (case-insensitive).
KEYWORDS = _env_list("KEYWORDS", [])

STATE_FILE = Path(os.getenv("STATE_FILE", "seen.json"))
LOG_FILE = os.getenv("LOG_FILE", "ch_drop_bot.log")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
        "image/webp,*/*;q=0.8"
    ),
}

# A product detail URL is /<category>/<handle>/<id>.html  (three path segments).
# This deliberately excludes /socks (category) and /terms.html (one segment).
PRODUCT_RE = re.compile(r"^/[^/]+/[^/]+/[^/]+\.html$")
PRICE_RE = re.compile(r"^\$\s?\d")
# A real category slug is plain lowercase words/hyphens — this rejects JS hrefs
# like "void(0);" and other non-path junk that shows up in nav anchors.
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

# Telegram rate limits: ~30 msgs/sec globally but ~1 msg/sec to a single chat.
TELEGRAM_MAX_ITEMS_PER_MSG = _env_int("TELEGRAM_MAX_ITEMS_PER_MSG", 10)
TELEGRAM_SEND_DELAY = float(os.getenv("TELEGRAM_SEND_DELAY", "1.2"))
TELEGRAM_MAX_MSG_CHARS = 3800  # stay under Telegram's 4096 hard limit

# Markers that indicate an Akamai / JS bot-challenge interstitial rather than a
# real category page.
CHALLENGE_MARKERS = (
    "access denied",
    "reference #",
    "akamai",
    "enable javascript",
    "please verify you are a human",
    "bot detection",
    "_incapsula_",
    "challenge-platform",
)
# ----------------------------------------------------------


def setup_logging() -> None:
    """stdout + rotating file, structured with timestamps and levels."""
    if log.handlers:  # already configured (e.g. re-entrant / tests)
        return
    log.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S"
    )

    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    log.addHandler(stream)

    if LOG_FILE:
        try:
            fileh = logging.handlers.RotatingFileHandler(
                LOG_FILE, maxBytes=2_000_000, backupCount=5, encoding="utf-8"
            )
            fileh.setFormatter(fmt)
            log.addHandler(fileh)
        except OSError as e:
            log.warning("Could not open log file %s: %s", LOG_FILE, e)


# ------------------------- TELEGRAM -------------------------
def _post_telegram(text: str) -> bool:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(
            url,
            data={
                "chat_id": CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": "false",
            },
            timeout=20,
        )
        if not r.ok:
            log.error("Telegram error: %s %s", r.status_code, r.text)
            return False
        return True
    except requests.RequestException as e:
        log.error("Telegram request failed: %s", e)
        return False


def send_telegram(text: str) -> bool:
    """Send a single message (kept for simple/manual use and tests)."""
    return _post_telegram(text)


def format_hit(url: str, prod: dict, kind: str) -> str:
    """One product's alert block: title, price, direct URL, NEW vs RESTOCK."""
    tag = "🆕" if kind == "NEW" else "🔁"
    price = f" — {prod['price']}" if prod.get("price") else ""
    title = prod.get("title") or url.rsplit("/", 1)[-1]
    return f"{tag} <b>{kind}</b>: {title}{price}\n{url}"


def _chunk_hits(hits: list[tuple]) -> list[str]:
    """Group hit blocks into Telegram-sized messages (batching)."""
    messages: list[str] = []
    current: list[str] = []
    length = 0
    header = "🔔 <b>Chrome Hearts</b>\n\n"
    for url, prod, kind in hits:
        block = format_hit(url, prod, kind)
        block_len = len(block) + 2
        too_many = len(current) >= TELEGRAM_MAX_ITEMS_PER_MSG
        too_long = length + block_len + len(header) > TELEGRAM_MAX_MSG_CHARS
        if current and (too_many or too_long):
            messages.append(header + "\n\n".join(current))
            current, length = [], 0
        current.append(block)
        length += block_len
    if current:
        messages.append(header + "\n\n".join(current))
    return messages


def send_batch(hits: list[tuple]) -> None:
    """Batch hits into as few messages as possible and throttle sends."""
    messages = _chunk_hits(hits)
    for i, msg in enumerate(messages):
        _post_telegram(msg)
        if i + 1 < len(messages):
            time.sleep(TELEGRAM_SEND_DELAY)


# ------------------------- PARSING -------------------------
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


def discover_categories(html: str) -> list[str]:
    """
    Enumerate category URLs from a homepage's top nav.

    Category links are single-segment paths (e.g. /socks, /scents) — not product
    detail pages (three segments, .html) and not deep utility pages. Returns
    absolute URLs, de-duplicated, order preserved.
    """
    soup = BeautifulSoup(html, "html.parser")
    seen: dict[str, None] = {}
    skip = {
        "account", "login", "sign-in", "cart", "search", "wishlist", "checkout",
        "customer-service", "contact", "stores", "locations", "find-us",
        "book-appointment", "appointment", "magazine", "journal", "about",
        "faq", "help", "shipping", "returns", "careers", "press", "newsletter",
        "legal", "privacy", "terms", "gift-cards", "gift-card", "sitemap",
    }
    for a in soup.find_all("a", href=True):
        href = a["href"].split("?")[0].split("#")[0]
        abs_url = urljoin(BASE + "/", href)
        parsed = urlparse(abs_url)
        if parsed.netloc and parsed.netloc not in urlparse(BASE).netloc:
            continue
        segments = [s for s in parsed.path.split("/") if s]
        if len(segments) != 1:
            continue
        slug = segments[0]
        if slug.endswith(".html") or slug in skip or not SLUG_RE.match(slug):
            continue
        category_url = f"{BASE}/{slug}"
        seen.setdefault(category_url, None)
    return list(seen)


# ------------------------- FETCH LAYER -------------------------
def looks_like_challenge(status: int | None, html: str) -> bool:
    """
    True when a response is a block/challenge rather than a real page.

    Pure decision helper (no I/O) so the fallback logic is unit-testable:
      - HTTP 403 / 429 / 503 are hard blocks.
      - A short body containing a known bot-challenge marker is a soft block.
    """
    if status in (403, 429, 503):
        return True
    if not html or not html.strip():
        return True
    low = html.lower()
    if any(marker in low for marker in CHALLENGE_MARKERS):
        # Genuine pages can mention these words in passing; only treat a small
        # interstitial (no real product markup) as a challenge.
        if len(html) < 40_000:
            return True
    return False


def fetch_via_requests(url: str, session: requests.Session | None = None):
    """Return (status_code, text). status_code is None on a transport error."""
    sess = session or requests
    try:
        r = sess.get(url, headers=HEADERS, timeout=25)
        return r.status_code, r.text
    except requests.RequestException as e:
        log.warning("requests fetch error for %s: %s", url, e)
        return None, ""


def fetch_via_playwright(url: str, timeout_ms: int = 30_000) -> str:
    """Render the page in headless Chromium and return its HTML."""
    from playwright.sync_api import sync_playwright  # lazy: optional dependency

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context(
                user_agent=HEADERS["User-Agent"],
                locale="en-US",
            )
            page = context.new_page()
            page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            page.wait_for_timeout(2000)  # let client-side product tiles render
            return page.content()
        finally:
            browser.close()


def _try_playwright(url: str) -> str | None:
    """Best-effort headless render; returns None on any Playwright error."""
    try:
        return fetch_via_playwright(url)
    except Exception as e:  # noqa: BLE001 - playwright surfaces many types
        log.error("Playwright fetch failed for %s: %s", url, e)
        return None


def fetch_html(url: str, strategy: str = None, session=None) -> str | None:
    """
    Fetch a category page's HTML using the configured strategy.

      requests   -> only plain requests
      playwright -> only headless browser
      auto       -> requests first; escalate to Playwright ONLY on a hard block
                    (403 / challenge interstitial). A merely-empty parse is left
                    for fetch_with_backoff to retry, because on this site plain
                    requests gets the full server-rendered page while headless
                    Chromium is often served a stripped/challenged one — so a
                    requests retry recovers more reliably than the browser does.

    Returns HTML (possibly empty), or None if nothing could be fetched.
    """
    strategy = (strategy or FETCH_STRATEGY or "auto").lower()

    if strategy == "playwright":
        return _try_playwright(url)

    status, html = fetch_via_requests(url, session=session)

    if strategy == "requests":
        return html or None

    # auto: only a genuine block warrants the (weaker, slower) browser fallback.
    if looks_like_challenge(status, html):
        log.info("requests blocked for %s (status=%s) — trying Playwright", url, status)
        pw = _try_playwright(url)
        return pw if pw else (html or None)
    return html


def fetch_with_backoff(url: str, session=None) -> str | None:
    """
    Fetch one URL, retrying transient empty results with exponential backoff.

    Order for `auto`: keep retrying plain requests (which is what actually works
    on this site); if every attempt still parses to zero products, make ONE
    last-resort Playwright attempt before giving up.
    """
    last_html = None
    for attempt in range(1, MAX_FETCH_RETRIES + 1):
        html = fetch_html(url, session=session)
        if html and parse_products(html):
            return html
        last_html = html or last_html
        if FETCH_STRATEGY == "requests" and html:
            # requests-only mode: trust the (possibly empty) result, no browser.
            return html
        if attempt < MAX_FETCH_RETRIES:
            delay = BACKOFF_BASE ** attempt + random.uniform(0, 1)
            log.warning("Fetch attempt %d/%d for %s parsed 0 products; retrying in %.1fs",
                        attempt, MAX_FETCH_RETRIES, url, delay)
            time.sleep(delay)

    # Persistent empty in auto mode: the page may be genuinely JS-rendered.
    if FETCH_STRATEGY == "auto":
        log.info("requests never parsed products for %s — last-resort Playwright", url)
        pw = _try_playwright(url)
        if pw and parse_products(pw):
            return pw
        last_html = pw or last_html

    log.error("Giving up on %s after %d attempts", url, MAX_FETCH_RETRIES)
    return last_html


def watched_categories(session=None) -> list[str]:
    """Discover categories from the nav; fall back to the configured list."""
    if not DISCOVER_CATEGORIES:
        return list(STORE_URLS)
    home = fetch_html(BASE, session=session)
    if home:
        found = discover_categories(home)
        if found:
            # Union with configured URLs so a nav miss never drops a category.
            merged = list(dict.fromkeys(found + list(STORE_URLS)))
            log.info("Discovered %d categories from nav (%d configured fallback)",
                     len(found), len(STORE_URLS))
            return merged
    log.warning("Category discovery failed; using configured STORE_URLS")
    return list(STORE_URLS)


def get_current_products(session=None) -> dict:
    """Sweep every watched category page into one combined dict."""
    combined = {}
    urls = watched_categories(session=session)
    # Shuffle order each sweep so no single category is perpetually fetched last
    # (and thus perpetually starved by cumulative Akamai throttling). Categories
    # that come back empty this sweep are simply skipped; they seed silently the
    # first time they are reachable (see check_once's per-category guard), so a
    # transient throttle never turns into a flood of false "NEW" alerts.
    random.shuffle(urls)
    for url in urls:
        html = fetch_with_backoff(url, session=session)
        found = parse_products(html) if html else {}
        if not found:
            log.info("0 products parsed from %s (will retry next sweep).", url)
        combined.update(found)
        time.sleep(PER_URL_DELAY + random.uniform(0, 1))  # politeness
    return combined


# ------------------------- STATE -------------------------
def load_seen() -> dict:
    return json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}


def save_seen(seen: dict) -> None:
    STATE_FILE.write_text(json.dumps(seen, indent=2))


def passes_keywords(title: str) -> bool:
    return not KEYWORDS or any(k.lower() in title.lower() for k in KEYWORDS)


def category_of(url: str) -> str:
    """First path segment of a product URL, e.g. .../socks/handle/id.html -> socks."""
    path = urlparse(url).path.strip("/")
    return path.split("/", 1)[0] if path else ""


# ------------------------- CORE LOOP -------------------------
def check_once(seen: dict, session=None) -> dict:
    current = get_current_products(session=session)
    if not current:
        log.warning("No products fetched this sweep; keeping previous state.")
        return seen

    # Categories we've already recorded. A category appearing for the FIRST time
    # (initial run, or one that was throttled to empty on earlier sweeps) is
    # seeded silently — otherwise its whole existing catalog would fire as false
    # "NEW" alerts. Genuine NEW/RESTOCK detection kicks in once a category is
    # seeded. State only ever accumulates, so a later transient empty is safe.
    seeded_categories = {category_of(u) for u in seen}

    hits = []
    seeded_now = 0
    for url, prod in current.items():
        was = seen.get(url)
        is_new = was is None
        restocked = was is not None and not was.get("available") and prod["available"]
        category_known = category_of(url) in seeded_categories

        if is_new and not category_known:
            seeded_now += 1  # first sighting of this category -> seed, don't alert
        elif (is_new or restocked) and prod["available"] and passes_keywords(prod["title"]):
            hits.append((url, prod, "NEW" if is_new else "RESTOCK"))
        seen[url] = prod

    save_seen(seen)

    if seeded_now:
        log.info("Seeded %d products from new categories (silent).", seeded_now)

    if hits:
        send_batch(hits)
        for url, prod, kind in hits:
            log.info("Alerted: %s %s", kind, prod["title"])
    elif not seeded_now:
        log.info("No changes (%d products live).", len(current))
    return seen


def main():
    setup_logging()
    if not BOT_TOKEN or not CHAT_ID:
        raise SystemExit(
            "Set BOT_TOKEN and CHAT_ID (env or .env). See .env.example."
        )
    session = requests.Session()
    seen = load_seen()
    log.info("Monitor started (strategy=%s, discover=%s, state=%s).",
             FETCH_STRATEGY, DISCOVER_CATEGORIES, STATE_FILE)
    while True:
        try:
            seen = check_once(seen, session=session)
        except Exception as e:  # noqa: BLE001 - never let one bad sweep kill the loop
            log.exception("Sweep failed: %s", e)
        sleep_for = CHECK_INTERVAL + random.uniform(0, JITTER)
        log.info("Sleeping %.0fs until next sweep.", sleep_for)
        time.sleep(sleep_for)


if __name__ == "__main__":
    main()
