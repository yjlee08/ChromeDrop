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


def test_extract_product_urls_from_metadata():
    """Recover a product URL that only appears in a <link>/meta (JS-rendered)."""
    html = (
        '<link rel="alternate" hreflang="x-default" '
        'href="https://www.chromehearts.com/eyewear/hollyweird/21761920SE5101X.html" />'
        '<meta property="og:url" content="https://www.chromehearts.com/eyewear/hollyweird/21761920SE5101X.html" />'
        '<a href="/socks/other/1.html">noise from another category</a>'
    )
    found = ch_drop_bot.extract_product_urls(html, "eyewear")
    assert found == [f"{BASE}/eyewear/hollyweird/21761920SE5101X.html"]
    # Slug filter keeps out cross-category noise.
    assert all("eyewear" in u for u in found)


def test_title_from_url():
    assert ch_drop_bot._title_from_url(
        f"{BASE}/eyewear/hollyweird/21761920SE5101X.html") == "Hollyweird"
    assert ch_drop_bot._title_from_url(
        f"{BASE}/boxers-leggings/boxer-brief---long/1.html") == "Boxer Brief   Long"


def test_parse_cached_matches_parse_products_and_reuses(monkeypatch):
    """parse_cached must equal parse_products, and skip re-parsing identical HTML."""
    ch_drop_bot._page_cache.clear()
    url = f"{BASE}/socks"
    first = ch_drop_bot.parse_cached(url, CATEGORY_HTML)
    assert first == parse_products(CATEGORY_HTML)

    # A second call with identical HTML must NOT call parse_products again.
    calls = {"n": 0}
    real = ch_drop_bot.parse_products

    def counting(html):
        calls["n"] += 1
        return real(html)

    monkeypatch.setattr(ch_drop_bot, "parse_products", counting)
    second = ch_drop_bot.parse_cached(url, CATEGORY_HTML)
    assert second == first
    assert calls["n"] == 0  # served from cache

    # Changed HTML busts the cache and re-parses.
    ch_drop_bot.parse_cached(url, CATEGORY_HTML + "<!-- changed -->")
    assert calls["n"] == 1


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


def test_backoff_no_browser_on_persistent_empty_by_default(monkeypatch):
    """
    A page that only *parses empty* (not a hard block) must NOT launch Chromium
    every sweep — that was the source of the server-Mac heat. Default behavior
    returns the empty result and retries next sweep.
    """
    monkeypatch.setattr(ch_drop_bot, "fetch_via_requests",
                        lambda url, session=None: (200, "<html>empty</html>"))
    monkeypatch.setattr(ch_drop_bot.time, "sleep", lambda s: None)
    monkeypatch.setattr(ch_drop_bot, "FETCH_STRATEGY", "auto")
    monkeypatch.setattr(ch_drop_bot, "PLAYWRIGHT_ON_EMPTY", False)
    monkeypatch.setattr(ch_drop_bot, "MAX_FETCH_RETRIES", 2)

    def boom(url, timeout_ms=30_000):
        raise AssertionError("Playwright must not launch on a mere empty parse")

    monkeypatch.setattr(ch_drop_bot, "fetch_via_playwright", boom)
    html = ch_drop_bot.fetch_with_backoff("https://x/socks")
    assert "empty" in html  # returns the requests body, no browser


def test_backoff_last_resort_playwright_when_opted_in(monkeypatch):
    """With PLAYWRIGHT_ON_EMPTY on, persistent empty still falls back once."""
    calls = {"pw": 0}
    monkeypatch.setattr(ch_drop_bot, "fetch_via_requests",
                        lambda url, session=None: (200, "<html>empty</html>"))
    monkeypatch.setattr(ch_drop_bot.time, "sleep", lambda s: None)
    monkeypatch.setattr(ch_drop_bot, "FETCH_STRATEGY", "auto")
    monkeypatch.setattr(ch_drop_bot, "PLAYWRIGHT_ON_EMPTY", True)
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


def _run_check_once(monkeypatch, seen, current, sent, send_ok=True):
    """Drive check_once with a fixed `current` sweep and capture Telegram sends."""
    monkeypatch.setattr(ch_drop_bot, "get_current_products", lambda session=None: current)
    monkeypatch.setattr(ch_drop_bot, "save_seen", lambda s: None)

    def fake_send(hits):
        sent.extend(hits)
        return send_ok

    monkeypatch.setattr(ch_drop_bot, "send_batch", fake_send)
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


def test_new_category_appearing_later_alerts(monkeypatch):
    """
    A brand-new category dropping after startup (e.g. a fresh /eyewear) must
    ALERT — that's the whole point. (Only the very first run seeds silently.)
    """
    seen = {f"{BASE}/socks/a/1.html": {"title": "A", "price": "$1", "available": True}}
    current = {
        f"{BASE}/socks/a/1.html": {"title": "A", "price": "$1", "available": True},
        f"{BASE}/eyewear/hollyweird/9.html": {"title": "Hollyweird", "price": None, "available": True},
    }
    sent = []
    _run_check_once(monkeypatch, seen, current, sent)
    assert len(sent) == 1
    assert sent[0][2] == "NEW"
    assert "eyewear" in sent[0][0]


def test_flood_cap_sends_summary(monkeypatch):
    """A huge batch is summarized (one message), not flooded, and all recorded."""
    seen = {f"{BASE}/socks/a/1.html": {"title": "A", "price": "$1", "available": True}}
    current = dict(seen)
    for i in range(40):
        current[f"{BASE}/eyewear/item-{i}/{i}.html"] = {
            "title": f"E{i}", "price": None, "available": True}
    posted = []
    monkeypatch.setattr(ch_drop_bot, "MAX_ALERTS_PER_SWEEP", 25)
    monkeypatch.setattr(ch_drop_bot, "save_seen", lambda s: None)
    monkeypatch.setattr(ch_drop_bot, "get_current_products", lambda session=None: current)
    monkeypatch.setattr(ch_drop_bot, "send_batch",
                        lambda hits: (_ for _ in ()).throw(AssertionError("should summarize")))
    monkeypatch.setattr(ch_drop_bot, "_post_telegram_with_retry",
                        lambda text: posted.append(text) or True)
    result = ch_drop_bot.check_once(seen)
    assert len(posted) == 1 and "new items" in posted[0]
    assert f"{BASE}/eyewear/item-0/0.html" in result  # all recorded, won't re-fire


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


def test_failed_send_does_not_record_alert(monkeypatch):
    """Gap #1: a failed Telegram send must NOT mark the item seen (so it retries)."""
    seen = {f"{BASE}/socks/a/1.html": {"title": "A", "price": "$1", "available": True}}
    new_url = f"{BASE}/socks/new/3.html"
    current = {
        f"{BASE}/socks/a/1.html": {"title": "A", "price": "$1", "available": True},
        new_url: {"title": "New Drop", "price": "$3", "available": True},
    }
    sent = []
    result = _run_check_once(monkeypatch, seen, current, sent, send_ok=False)
    assert len(sent) == 1                 # send was attempted
    assert new_url not in result          # but NOT recorded -> will re-fire


def test_failed_then_successful_send_refires_and_records(monkeypatch):
    """After a failed send, the next sweep re-alerts and (on success) records it."""
    new_url = f"{BASE}/socks/new/3.html"
    seen = {f"{BASE}/socks/a/1.html": {"title": "A", "price": "$1", "available": True}}
    current = {
        f"{BASE}/socks/a/1.html": {"title": "A", "price": "$1", "available": True},
        new_url: {"title": "New Drop", "price": "$3", "available": True},
    }
    # Sweep 1: send fails -> not recorded.
    sent1 = []
    seen = _run_check_once(monkeypatch, seen, current, sent1, send_ok=False)
    assert new_url not in seen
    # Sweep 2: send succeeds -> alert re-fires and is now recorded.
    sent2 = []
    seen = _run_check_once(monkeypatch, seen, current, sent2, send_ok=True)
    assert len(sent2) == 1 and sent2[0][2] == "NEW"
    assert new_url in seen
    # Sweep 3: already recorded -> no duplicate alert.
    sent3 = []
    seen = _run_check_once(monkeypatch, seen, current, sent3, send_ok=True)
    assert sent3 == []


def test_send_batch_returns_false_when_a_message_fails(monkeypatch):
    monkeypatch.setattr(ch_drop_bot, "TELEGRAM_SEND_RETRIES", 1)
    monkeypatch.setattr(ch_drop_bot.time, "sleep", lambda s: None)
    monkeypatch.setattr(ch_drop_bot, "_post_telegram", lambda text: False)
    hit = (f"{BASE}/socks/a/1.html",
           {"title": "A", "price": "$1", "available": True}, "NEW")
    assert ch_drop_bot.send_batch([hit]) is False


def test_send_batch_retries_then_succeeds(monkeypatch):
    attempts = {"n": 0}
    monkeypatch.setattr(ch_drop_bot, "TELEGRAM_SEND_RETRIES", 3)
    monkeypatch.setattr(ch_drop_bot.time, "sleep", lambda s: None)

    def flaky(text):
        attempts["n"] += 1
        return attempts["n"] >= 2  # fail once, then succeed

    monkeypatch.setattr(ch_drop_bot, "_post_telegram", flaky)
    hit = (f"{BASE}/socks/a/1.html",
           {"title": "A", "price": "$1", "available": True}, "NEW")
    assert ch_drop_bot.send_batch([hit]) is True
    assert attempts["n"] == 2


def test_parse_window():
    assert ch_drop_bot._parse_window("14:00-22:00") == (840, 1320)
    assert ch_drop_bot._parse_window("") is None
    assert ch_drop_bot._parse_window("garbage") is None


def test_within_slow_window_daytime(monkeypatch):
    # slow window 14:00-22:00 = minutes 840..1320
    monkeypatch.setattr(ch_drop_bot, "SLOW_WINDOW", (840, 1320))
    assert ch_drop_bot.within_slow_window(13 * 60 + 59) is False  # 13:59 -> fast
    assert ch_drop_bot.within_slow_window(14 * 60) is True         # 14:00 -> slow
    assert ch_drop_bot.within_slow_window(21 * 60 + 59) is True    # 21:59 -> slow
    assert ch_drop_bot.within_slow_window(22 * 60) is False        # 22:00 -> fast
    assert ch_drop_bot.within_slow_window(6 * 60) is False         # 06:00 -> fast (drop time)


def test_within_slow_window_wraps_midnight(monkeypatch):
    monkeypatch.setattr(ch_drop_bot, "SLOW_WINDOW", (22 * 60, 2 * 60))
    assert ch_drop_bot.within_slow_window(23 * 60) is True
    assert ch_drop_bot.within_slow_window(1 * 60) is True
    assert ch_drop_bot.within_slow_window(12 * 60) is False


def test_no_window_means_never_slow(monkeypatch):
    monkeypatch.setattr(ch_drop_bot, "SLOW_WINDOW", None)
    assert ch_drop_bot.within_slow_window(15 * 60) is False


def test_current_interval_switches(monkeypatch):
    monkeypatch.setattr(ch_drop_bot, "SLOW_WINDOW", (840, 1320))  # 14:00-22:00
    monkeypatch.setattr(ch_drop_bot, "CHECK_INTERVAL", 120)
    monkeypatch.setattr(ch_drop_bot, "SLOW_INTERVAL", 600)
    assert ch_drop_bot.current_interval(6 * 60) == 120    # 6am -> fast (2 min)
    assert ch_drop_bot.current_interval(15 * 60) == 600   # 3pm -> slow (10 min)


def test_handle_command_status():
    reply = ch_drop_bot.handle_command("/status")
    assert reply is not None and "running" in reply.lower()


def test_handle_command_status_with_botname_suffix():
    # Telegram appends @botname in groups; the handler must still match.
    assert ch_drop_bot.handle_command("/status@yjchromedropbot") is not None


def test_handle_command_ping():
    assert "pong" in ch_drop_bot.handle_command("/ping").lower()


def test_handle_command_unknown_and_empty():
    assert ch_drop_bot.handle_command("hello there") is None
    assert ch_drop_bot.handle_command("") is None
    assert ch_drop_bot.handle_command("/unknown") is None


def test_status_reply_reflects_tracked_count(monkeypatch):
    monkeypatch.setattr(ch_drop_bot, "_TRACKED_COUNT", 42)
    monkeypatch.setattr(ch_drop_bot, "_LAST_SWEEP_AT", None)
    reply = ch_drop_bot.build_status_reply()
    assert "42 products" in reply
    assert "not yet" in reply  # no sweep recorded yet


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
