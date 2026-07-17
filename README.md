# Chrome Hearts Drop Monitor → Telegram Alerts

Polls the [Chrome Hearts](https://www.chromehearts.com) online store and sends a
Telegram alert whenever a **new product drops** or a **sold-out item comes back
in stock**. Built to run 24/7.

The store runs on Salesforce Commerce Cloud (Demandware) — there is no
`/products.json`, no single "all products" page, and it sits behind Akamai bot
protection. This bot handles all three: it walks category pages, parses product
tiles robustly from the anchors themselves, and falls back to a headless browser
when plain HTTP requests get challenged.

## How it works

1. Each sweep, it discovers category URLs from the site's top nav (with a
   configured fallback list) so new categories are picked up automatically.
2. For each category it fetches the HTML — trying `requests` first and falling
   back to headless Playwright Chromium on a 403 / JS challenge / empty parse.
3. `parse_products()` turns each page into `{url: {title, price, available}}`,
   grouping a product's swatch/title/price links by their shared
   `/cat/handle/id.html` path and reading stock status per-product.
4. It diffs against `seen.json` and sends batched, throttled Telegram alerts for
   NEW products and RESTOCKs. The **first run only seeds state** (no spam).

## Setup

Requires Python 3.11+.

```bash
git clone https://github.com/yjlee08/ChromeDrop.git
cd ChromeDrop

python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium # only needed for the fallback path

cp .env.example .env                  # then edit .env
```

### Telegram credentials

- **BOT_TOKEN** — message [@BotFather](https://t.me/BotFather), `/newbot`, copy the token.
- **CHAT_ID** — message your new bot once, then open
  `https://api.telegram.org/bot<BOT_TOKEN>/getUpdates` and read `chat.id`.

Put both in `.env`. `.env` is gitignored — never commit real secrets.

## Run

```bash
python ch_drop_bot.py
```

First run seeds `seen.json` silently; alerts start on the next sweep.

## Configuration

All settings come from the environment / `.env` (see `.env.example`):

| Variable | Default | Meaning |
|---|---|---|
| `BOT_TOKEN` | — | Telegram bot token (required) |
| `CHAT_ID` | — | Telegram chat id (required) |
| `FETCH_STRATEGY` | `auto` | `auto` \| `requests` \| `playwright` |
| `DISCOVER_CATEGORIES` | `true` | Scrape nav for categories automatically |
| `STORE_URLS` | socks,scents,… | Comma-separated fallback category list |
| `CHECK_INTERVAL` | `120` | Base seconds between sweeps (keep ≥ 60) |
| `JITTER` | `30` | Random 0–JITTER extra seconds per sweep |
| `PER_URL_DELAY` | `3` | Politeness pause between category pages |
| `MAX_FETCH_RETRIES` | `4` | Per-URL retries with exponential backoff |
| `KEYWORDS` | _(empty)_ | Only alert when a title matches one of these |
| `STATE_FILE` | `seen.json` | Path to the dedupe state file |
| `LOG_FILE` | `ch_drop_bot.log` | Rotating log file (also logs to stdout) |
| `TELEGRAM_MAX_ITEMS_PER_MSG` | `10` | Items batched per Telegram message |
| `TELEGRAM_SEND_DELAY` | `1.2` | Seconds between throttled messages |

**Be polite:** keep `CHECK_INTERVAL` ≥ 60s. The bot already jitters intervals,
delays between pages, and backs off on errors — don't hammer the site.

## Tests

```bash
pytest
```

Covers the parser (new drop, restock, sold-out, query-string dedupe, nav/footer
noise) and the fetch-fallback decision logic with mocked requests/Playwright.

## Deploy 24/7

### systemd (Linux host)

```bash
sudo useradd -r -s /usr/sbin/nologin chdrop
sudo mkdir -p /opt/ChromeDrop && sudo chown chdrop /opt/ChromeDrop
# copy the repo to /opt/ChromeDrop, create the venv + .env there, then:
sudo cp ch-drop-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ch-drop-bot
journalctl -u ch-drop-bot -f
```

The unit restarts on failure and reads secrets from `/opt/ChromeDrop/.env`.

### Docker

```bash
docker build -t chdrop .
docker run -d --name chdrop --restart unless-stopped \
  --env-file .env -v ch_data:/data chdrop
```

State and logs persist in the `ch_data` volume (`/data/seen.json`,
`/data/ch_drop_bot.log`). The image is based on the official Playwright image so
the headless-browser fallback works out of the box.

## Notes

- No database — state is a single JSON file.
- If the site markup changes and `0 products parsed` shows up in the logs,
  `parse_products()` is where to look; it's covered by tests to catch regressions.
