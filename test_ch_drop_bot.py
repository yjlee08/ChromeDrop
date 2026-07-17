"""Tests for ch_drop_bot.

The parser tests pin down parse_products() behavior on realistic Salesforce
Commerce Cloud markup: new drops, restocks, sold-out items, and nav/footer
noise. parse_products() is the source of truth — these must stay green.
"""

import ch_drop_bot
from ch_drop_bot import parse_products


BASE = "https://www.chromehearts.com"


# A realistic-ish category page: a header nav, three product tiles (each with a
# color-swatch link, a title link and a price link sharing one product path),
# one of them marked OUT OF STOCK, plus footer/legal noise that must be ignored.
CATEGORY_HTML = """
<!doctype html>
<html>
<head><title>Socks</title></head>
<body>
  <header>
    <nav>
      <a href="/socks">Socks</a>
      <a href="/scents">Scents</a>
      <a href="/baccarat">Baccarat</a>
      <a href="https://www.chromehearts.com/boxers-leggings">Boxers &amp; Leggings</a>
    </nav>
  </header>

  <main>
    <div class="tile">
      <a href="/socks/ch-logo-socks/176354000001349.html"><img></a>
      <a href="/socks/ch-logo-socks/176354000001349.html">CH Logo Socks</a>
      <a href="/socks/ch-logo-socks/176354000001349.html">$255</a>
    </div>

    <div class="tile">
      <a href="/socks/cemetery-socks/176354000002349.html?dwvar=black"><img></a>
      <a href="/socks/cemetery-socks/176354000002349.html">Cemetery Cross Socks</a>
      <a href="/socks/cemetery-socks/176354000002349.html">$260</a>
      <span class="badge">OUT OF STOCK</span>
    </div>

    <div class="tile">
      <a href="https://www.chromehearts.com/socks/dagger-socks/176354000003349.html"><img></a>
      <a href="/socks/dagger-socks/176354000003349.html">Dagger Socks</a>
      <a href="/socks/dagger-socks/176354000003349.html">$275</a>
    </div>
  </main>

  <footer>
    <a href="/terms.html">Terms</a>
    <a href="/privacy.html">Privacy</a>
    <a href="/customer-service/contact-us">Contact</a>
    <p>All items subject to availability.</p>
  </footer>
</body>
</html>
"""


def test_parses_expected_products_only():
    products = parse_products(CATEGORY_HTML)
    assert set(products) == {
        f"{BASE}/socks/ch-logo-socks/176354000001349.html",
        f"{BASE}/socks/cemetery-socks/176354000002349.html",
        f"{BASE}/socks/dagger-socks/176354000003349.html",
    }


def test_titles_and_prices():
    products = parse_products(CATEGORY_HTML)
    logo = products[f"{BASE}/socks/ch-logo-socks/176354000001349.html"]
    assert logo["title"] == "CH Logo Socks"
    assert logo["price"] == "$255"


def test_in_stock_product_available():
    products = parse_products(CATEGORY_HTML)
    logo = products[f"{BASE}/socks/ch-logo-socks/176354000001349.html"]
    assert logo["available"] is True


def test_sold_out_product_unavailable():
    products = parse_products(CATEGORY_HTML)
    cemetery = products[f"{BASE}/socks/cemetery-socks/176354000002349.html"]
    assert cemetery["available"] is False


def test_out_of_stock_does_not_bleed_to_neighbor():
    """The tile after the sold-out one must still read as available."""
    products = parse_products(CATEGORY_HTML)
    dagger = products[f"{BASE}/socks/dagger-socks/176354000003349.html"]
    assert dagger["available"] is True


def test_nav_and_footer_noise_excluded():
    products = parse_products(CATEGORY_HTML)
    # Category links (one path segment) and legal pages must not appear.
    for url in products:
        assert "/terms.html" not in url
        assert "/privacy.html" not in url
        assert url not in {f"{BASE}/socks", f"{BASE}/scents"}


def test_query_string_variants_group_to_one_product():
    """Color-swatch links carry ?dwvar=... but must dedupe to one product."""
    products = parse_products(CATEGORY_HTML)
    cemetery_urls = [u for u in products if "cemetery-socks" in u]
    assert cemetery_urls == [f"{BASE}/socks/cemetery-socks/176354000002349.html"]


def test_empty_page_yields_no_products():
    assert parse_products("<html><body>nothing here</body></html>") == {}


# --------------------------------------------------------------------------
# Fetch-fallback decision logic (mocked responses)
# --------------------------------------------------------------------------

CHALLENGE_HTML = (
    "<html><body><h1>Access Denied</h1>"
    "<p>Reference #18.abcd. You don't have permission.</p></body></html>"
)


def test_looks_like_challenge_on_403():
    assert ch_drop_bot.looks_like_challenge(403, "<html>whatever</html>") is True


def test_looks_like_challenge_on_rate_limit_and_unavailable():
    assert ch_drop_bot.looks_like_challenge(429, "x") is True
    assert ch_drop_bot.looks_like_challenge(503, "x") is True


def test_looks_like_challenge_on_empty_body():
    assert ch_drop_bot.looks_like_challenge(200, "") is True
    assert ch_drop_bot.looks_like_challenge(200, "   ") is True


def test_looks_like_challenge_on_small_interstitial():
    assert ch_drop_bot.looks_like_challenge(200, CHALLENGE_HTML) is True


def test_real_page_is_not_a_challenge():
    assert ch_drop_bot.looks_like_challenge(200, CATEGORY_HTML) is False


def test_large_page_mentioning_marker_is_not_a_challenge():
    # A genuine page may say "enable javascript" in a footer; if it's large and
    # rich, don't misclassify it as a block.
    big = CATEGORY_HTML + ("<p>please enable javascript for maps</p>" + "x" * 50_000)
    assert ch_drop_bot.looks_like_challenge(200, big) is False


def test_auto_falls_back_to_playwright_on_block(monkeypatch):
    calls = {"pw": 0}
    monkeypatch.setattr(ch_drop_bot, "fetch_via_requests",
                        lambda url, session=None: (403, CHALLENGE_HTML))

    def fake_pw(url, timeout_ms=30_000):
        calls["pw"] += 1
        return CATEGORY_HTML

    monkeypatch.setattr(ch_drop_bot, "fetch_via_playwright", fake_pw)
    html = ch_drop_bot.fetch_html("https://x/socks", strategy="auto")
    assert html == CATEGORY_HTML
    assert calls["pw"] == 1


def test_auto_empty_parse_does_not_immediately_use_playwright(monkeypatch):
    """
    200 OK but no products parsed is NOT a hard block: fetch_html returns the
    requests body and leaves retrying to fetch_with_backoff (requests is the
    reliable path on this site; headless Chromium gets challenged).
    """
    monkeypatch.setattr(ch_drop_bot, "fetch_via_requests",
                        lambda url, session=None: (200, "<html><body>no tiles</body></html>"))

    def boom(url, timeout_ms=30_000):
        raise AssertionError("Playwright must not run on a mere empty parse")

    monkeypatch.setattr(ch_drop_bot, "fetch_via_playwright", boom)
    html = ch_drop_bot.fetch_html("https://x/socks", strategy="auto")
    assert "no tiles" in html


def test_backoff_retries_requests_then_recovers(monkeypatch):
    """A transient empty parse is recovered by retrying requests, not Playwright."""
    responses = iter([(200, "<html>empty</html>"), (200, CATEGORY_HTML)])
    monkeypatch.setattr(ch_drop_bot, "fetch_via_requests",
                        lambda url, session=None: next(responses))
    monkeypatch.setattr(ch_drop_bot.time, "sleep", lambda s: None)
    monkeypatch.setattr(ch_drop_bot, "FETCH_STRATEGY", "auto")
    monkeypatch.setattr(ch_drop_bot, "MAX_FETCH_RETRIES", 3)

    def boom(url, timeout_ms=30_000):
        raise AssertionError("Playwright must not run while requests still recovers")

    monkeypatch.setattr(ch_drop_bot, "fetch_via_playwright", boom)
    html = ch_drop_bot.fetch_with_backoff("https://x/socks")
    assert html == CATEGORY_HTML


def test_backoff_last_resort_playwright_on_persistent_empty(monkeypatch):
    """When requests never yields products, fall back to Playwright once."""
    calls = {"pw": 0}
    monkeypatch.setattr(ch_drop_bot, "fetch_via_requests",
                        lambda url, session=None: (200, "<html>empty</html>"))
    monkeypatch.setattr(ch_drop_bot.time, "sleep", lambda s: None)
    monkeypatch.setattr(ch_drop_bot, "FETCH_STRATEGY", "auto")
    monkeypatch.setattr(ch_drop_bot, "MAX_FETCH_RETRIES", 2)

    def fake_pw(url, timeout_ms=30_000):
        calls["pw"] += 1
        return CATEGORY_HTML

    monkeypatch.setattr(ch_drop_bot, "fetch_via_playwright", fake_pw)
    html = ch_drop_bot.fetch_with_backoff("https://x/socks")
    assert html == CATEGORY_HTML
    assert calls["pw"] == 1


def test_auto_uses_requests_when_ok(monkeypatch):
    """A good requests response must NOT trigger the browser fallback."""
    monkeypatch.setattr(ch_drop_bot, "fetch_via_requests",
                        lambda url, session=None: (200, CATEGORY_HTML))

    def boom(url, timeout_ms=30_000):
        raise AssertionError("Playwright should not be called on a good response")

    monkeypatch.setattr(ch_drop_bot, "fetch_via_playwright", boom)
    html = ch_drop_bot.fetch_html("https://x/socks", strategy="auto")
    assert html == CATEGORY_HTML


def test_requests_strategy_never_falls_back(monkeypatch):
    """strategy=requests returns the (possibly empty) result, no browser."""
    monkeypatch.setattr(ch_drop_bot, "fetch_via_requests",
                        lambda url, session=None: (403, CHALLENGE_HTML))

    def boom(url, timeout_ms=30_000):
        raise AssertionError("Playwright must not run in requests-only mode")

    monkeypatch.setattr(ch_drop_bot, "fetch_via_playwright", boom)
    # 403 body is non-empty text, so it is returned as-is (not None).
    assert ch_drop_bot.fetch_html("https://x/socks", strategy="requests") == CHALLENGE_HTML


def test_playwright_strategy_ignores_requests(monkeypatch):
    def boom(url, session=None):
        raise AssertionError("requests must not run in playwright-only mode")

    monkeypatch.setattr(ch_drop_bot, "fetch_via_requests", boom)
    monkeypatch.setattr(ch_drop_bot, "fetch_via_playwright",
                        lambda url, timeout_ms=30_000: CATEGORY_HTML)
    assert ch_drop_bot.fetch_html("https://x/socks", strategy="playwright") == CATEGORY_HTML


# --------------------------------------------------------------------------
# Category discovery
# --------------------------------------------------------------------------

def test_discover_categories_from_nav():
    cats = ch_drop_bot.discover_categories(CATEGORY_HTML)
    assert f"{BASE}/socks" in cats
    assert f"{BASE}/scents" in cats
    assert f"{BASE}/baccarat" in cats
    assert f"{BASE}/boxers-leggings" in cats


def test_discover_categories_excludes_products_and_utility():
    cats = ch_drop_bot.discover_categories(CATEGORY_HTML)
    # Product detail pages (three segments, .html) are not categories.
    assert not any(".html" in c for c in cats)
    # Utility/legal single-segment links are filtered out.
    assert f"{BASE}/terms" not in cats
    assert f"{BASE}/customer-service" not in cats


def test_discover_categories_rejects_js_hrefs_and_utility_pages():
    """JS hrefs like void(0); and utility slugs like /contact are not categories."""
    html = """
    <nav>
      <a href="void(0);">Menu</a>
      <a href="/contact">Contact</a>
      <a href="/locations.html">Stores</a>
      <a href="/socks">Socks</a>
      <a href="/scents">Scents</a>
    </nav>
    """
    cats = ch_drop_bot.discover_categories(html)
    assert f"{BASE}/socks" in cats
    assert f"{BASE}/scents" in cats
    assert f"{BASE}/void(0);" not in cats
    assert not any("void" in c for c in cats)
    assert f"{BASE}/contact" not in cats
    assert not any(".html" in c for c in cats)


# --------------------------------------------------------------------------
# Telegram batching / throttling
# --------------------------------------------------------------------------

def test_format_hit_contains_all_fields():
    prod = {"title": "CH Logo Socks", "price": "$255", "available": True}
    block = ch_drop_bot.format_hit(f"{BASE}/socks/ch-logo-socks/1.html", prod, "NEW")
    assert "CH Logo Socks" in block
    assert "$255" in block
    assert f"{BASE}/socks/ch-logo-socks/1.html" in block
    assert "NEW" in block


def test_chunk_hits_batches_many_into_few_messages(monkeypatch):
    monkeypatch.setattr(ch_drop_bot, "TELEGRAM_MAX_ITEMS_PER_MSG", 10)
    hits = [
        (f"{BASE}/socks/item-{i}/{i}.html",
         {"title": f"Item {i}", "price": "$100", "available": True},
         "NEW")
        for i in range(25)
    ]
    messages = ch_drop_bot._chunk_hits(hits)
    assert len(messages) == 3  # 10 + 10 + 5
    # Every item is represented exactly once across all messages.
    joined = "\n".join(messages)
    for i in range(25):
        assert f"Item {i}" in joined


def test_category_of():
    assert ch_drop_bot.category_of(f"{BASE}/socks/ch-logo-socks/1.html") == "socks"
    assert ch_drop_bot.category_of(f"{BASE}/scents/22-edp/2.html") == "scents"


def _run_check_once(monkeypatch, seen, current, sent):
    """Drive check_once with a fixed `current` sweep and capture Telegram sends."""
    monkeypatch.setattr(ch_drop_bot, "get_current_products", lambda session=None: current)
    monkeypatch.setattr(ch_drop_bot, "save_seen", lambda s: None)
    monkeypatch.setattr(ch_drop_bot, "send_batch", lambda hits: sent.extend(hits))
    return ch_drop_bot.check_once(seen)


def test_first_run_seeds_silently(monkeypatch):
    """Empty state: everything is seeded, nothing is alerted."""
    current = {
        f"{BASE}/socks/a/1.html": {"title": "A", "price": "$1", "available": True},
        f"{BASE}/socks/b/2.html": {"title": "B", "price": "$2", "available": True},
    }
    sent = []
    seen = _run_check_once(monkeypatch, {}, current, sent)
    assert sent == []
    assert set(seen) == set(current)


def test_new_category_appearing_later_seeds_silently(monkeypatch):
    """
    A category first seen on a LATER sweep (e.g. scents was throttled during the
    initial seed) must seed silently, not flood 21 false NEW alerts.
    """
    seen = {f"{BASE}/socks/a/1.html": {"title": "A", "price": "$1", "available": True}}
    current = {
        f"{BASE}/socks/a/1.html": {"title": "A", "price": "$1", "available": True},
        f"{BASE}/scents/x/9.html": {"title": "Scent X", "price": "$9", "available": True},
        f"{BASE}/scents/y/8.html": {"title": "Scent Y", "price": "$8", "available": True},
    }
    sent = []
    _run_check_once(monkeypatch, seen, current, sent)
    assert sent == []  # scents is a brand-new category -> silent seed


def test_new_product_in_seeded_category_alerts(monkeypatch):
    """Once a category is seeded, a genuinely new product alerts as NEW."""
    seen = {f"{BASE}/socks/a/1.html": {"title": "A", "price": "$1", "available": True}}
    current = {
        f"{BASE}/socks/a/1.html": {"title": "A", "price": "$1", "available": True},
        f"{BASE}/socks/new/3.html": {"title": "New Drop", "price": "$3", "available": True},
    }
    sent = []
    _run_check_once(monkeypatch, seen, current, sent)
    assert len(sent) == 1
    assert sent[0][2] == "NEW"
    assert sent[0][1]["title"] == "New Drop"


def test_restock_in_seeded_category_alerts(monkeypatch):
    """A sold-out item coming back in a seeded category alerts as RESTOCK."""
    seen = {f"{BASE}/socks/a/1.html": {"title": "A", "price": "$1", "available": False}}
    current = {f"{BASE}/socks/a/1.html": {"title": "A", "price": "$1", "available": True}}
    sent = []
    _run_check_once(monkeypatch, seen, current, sent)
    assert len(sent) == 1
    assert sent[0][2] == "RESTOCK"


def test_send_batch_throttles(monkeypatch):
    posted = []
    sleeps = []
    monkeypatch.setattr(ch_drop_bot, "_post_telegram", lambda text: posted.append(text) or True)
    monkeypatch.setattr(ch_drop_bot.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(ch_drop_bot, "TELEGRAM_MAX_ITEMS_PER_MSG", 2)
    hits = [
        (f"{BASE}/socks/item-{i}/{i}.html",
         {"title": f"Item {i}", "price": "$100", "available": True}, "NEW")
        for i in range(5)
    ]
    ch_drop_bot.send_batch(hits)
    assert len(posted) == 3          # 2 + 2 + 1
    assert len(sleeps) == 2          # throttle between the 3 messages
