# Chrome Hearts drop monitor — 24/7 container.
# Based on the official Playwright image so headless Chromium + all system
# libraries are already present for the fallback fetch path.
FROM mcr.microsoft.com/playwright/python:v1.61.0-noble

WORKDIR /app

# Install Python deps first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The base image ships browsers, but ensure the matching Chromium is present.
RUN python -m playwright install chromium

COPY ch_drop_bot.py .

# Persist state + logs on a mounted volume.
ENV STATE_FILE=/data/seen.json \
    LOG_FILE=/data/ch_drop_bot.log \
    PYTHONUNBUFFERED=1
VOLUME ["/data"]

# BOT_TOKEN and CHAT_ID must be supplied at runtime, e.g.:
#   docker run --env-file .env -v ch_data:/data chdrop
CMD ["python", "ch_drop_bot.py"]
