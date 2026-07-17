# Deploy the monitor 24/7 on a free Google Cloud e2-micro VM

This runs the bot on an always-on Linux VM so it works even when your Mac is
off. **You** do the 3 interactive steps (they need your login/card); the VM's
startup script does everything else automatically.

> ⚠️ **Datacenter-IP caveat:** the store uses Akamai bot protection, which
> challenges cloud IPs more than home internet. Plain `requests` may hit more
> 403s from a GCP VM than from your Mac. The startup script installs the
> Playwright fallback + swap to cope, but if the logs show constant 403s
> (see step 5), hosting at home (Raspberry Pi / old laptop) is more reliable.

---

## Step 1 — Create the account (one-time, ~5 min)
1. Go to <https://console.cloud.google.com> and sign in with your Google account.
2. Accept the free trial / set up **Billing** (a card is required for identity
   verification). The **e2-micro** below stays within the **Always Free** tier,
   so you should not be charged — but keep an eye on billing to be safe.
3. Create (or accept) a **Project**.

## Step 2 — Prepare the startup script (~2 min)
1. Open [`gcp-startup.sh`](gcp-startup.sh) in this repo.
2. Replace the two placeholders at the top with your real values:
   - `__PASTE_YOUR_BOT_TOKEN__`  → your Telegram bot token
   - `__PASTE_YOUR_CHAT_ID__`    → `2140967909`
3. Copy the whole edited file to your clipboard.

## Step 3 — Create the VM (~3 min)
1. In the console, go to **Compute Engine → VM instances → Create instance**
   (enable the Compute Engine API if prompted).
2. Set these to stay in the **Always Free** tier:
   - **Name:** `chdrop`
   - **Region:** `us-central1` (or `us-west1` / `us-east1` — must be one of these)
   - **Machine type:** `e2-micro`
   - **Boot disk:** Debian 12, standard persistent disk ≤ 30 GB
3. Expand **Advanced options → Management → Automation**.
4. Paste your edited `gcp-startup.sh` into the **Startup script** box.
5. Click **Create**.

## Step 4 — Wait
First boot installs Python, Chromium, and the service — about **5–10 minutes**.
When it's done you'll get a Telegram message on the next real drop/restock.
(The first sweep seeds silently, exactly like it did on your Mac.)

## Step 5 — Verify / manage (SSH)
Click **SSH** next to the VM in the console, then:

```bash
# Is the service running?
sudo systemctl status ch-drop-bot

# Live logs (watch for "Seeded", "No changes", or repeated 403s):
sudo journalctl -u ch-drop-bot -f

# The app's own log file:
sudo tail -f /opt/ChromeDrop/ch_drop_bot.log
```

**If you see repeated 403 / challenge lines**, the Akamai datacenter block is
in effect. Try `sudo nano /opt/ChromeDrop/.env`, ensure `FETCH_STRATEGY=auto`,
then `sudo systemctl restart ch-drop-bot`. If it persists, switch to home
hosting — ask me for the Raspberry Pi / old-laptop guide.

## Managing later
```bash
sudo systemctl restart ch-drop-bot     # after editing .env (e.g. rotated token)
sudo systemctl stop ch-drop-bot        # pause alerts
sudo systemctl disable --now ch-drop-bot   # stop permanently
```

## Cost
`e2-micro` in an eligible US region is **Always Free**. Outbound traffic is tiny
(Telegram messages). Deleting the VM when you're done avoids any disk charges.
