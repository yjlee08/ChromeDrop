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
