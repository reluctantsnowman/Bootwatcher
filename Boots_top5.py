import os
import json
import requests
from bs4 import BeautifulSoup
from datetime import datetime

STATE_FILE = "state_last_top5.json"
LOG_FILE = "logs/boots_watcher.log"
README_FILE = "README.md"

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
SAVE_STATE = os.getenv("SAVE_STATE") == "1"

IRON_HEART_UK_URL = "https://ironheart.co.uk/collections/wesco?sort_by=created-descending"


# --------------------------------------------------
# Logging
# --------------------------------------------------

def log(message: str):
    os.makedirs("logs", exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{timestamp}] {message}"
    print(line)

    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# --------------------------------------------------
# Load / Save State
# --------------------------------------------------

def load_previous_state():
    if not os.path.exists(STATE_FILE):
        return []

    with open(STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(boots):
    if not SAVE_STATE:
        return

    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(boots, f, indent=2)

    log("State saved.")


# --------------------------------------------------
# Scraper (Iron Heart UK - Wesco)
# --------------------------------------------------

def scrape_iron_heart_uk():
    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    response = requests.get(IRON_HEART_UK_URL, headers=headers, timeout=30)
    soup = BeautifulSoup(response.text, "lxml")

    products = soup.select(".product-item")
    boots = []

    for product in products[:5]:
        title_tag = product.select_one(".product-item__title")
        price_tag = product.select_one(".price-item")
        link_tag = product.select_one("a")

        if not title_tag or not link_tag:
            continue

        name = title_tag.get_text(strip=True)
        price = price_tag.get_text(strip=True) if price_tag else ""
        url = "https://ironheart.co.uk" + link_tag["href"]

        boots.append({
            "name": name,
            "price": price,
            "currency": "GBP",
            "url": url
        })

    return boots


# --------------------------------------------------
# Detect NEW in Top 3
# --------------------------------------------------

def detect_new_top3(current, previous):
    previous_urls = {boot["url"] for boot in previous}
    new_items = []

    for boot in current[:3]:
        if boot["url"] not in previous_urls:
            new_items.append(boot)

    return new_items


# --------------------------------------------------
# Discord Posting
# --------------------------------------------------

def post_to_discord(new_items):
    if not DISCORD_WEBHOOK_URL:
        log("No Discord webhook set.")
        return

    content_lines = ["**🆕 NEW Boots Detected (Top 3) 🆕**\n"]

    for boot in new_items:
        content_lines.append(
            f"**{boot['name']}**\n{boot['price']}\n{boot['url']}\n"
        )

    payload = {"content": "\n".join(content_lines)}

    response = requests.post(DISCORD_WEBHOOK_URL, json=payload)

    if response.status_code == 204:
        log("Posted NEW boots to Discord.")
    else:
        log(f"Discord error: {response.status_code} {response.text}")


# --------------------------------------------------
# README Update
# --------------------------------------------------

def update_readme_summary(run_ts_utc: str, boots: list[dict]):
    if not os.path.exists(README_FILE):
        return

    with open(README_FILE, "r", encoding="utf-8") as f:
        content = f.read()

    start_marker = "<!-- BOOTS_SUMMARY_START -->"
    end_marker = "<!-- BOOTS_SUMMARY_END -->"

    if start_marker not in content or end_marker not in content:
        return

    summary_lines = []
    summary_lines.append(f"**Last Run (UTC):** `{run_ts_utc}`\n")
    summary_lines.append("### Iron Heart UK - Wesco (Top 5)\n")
    summary_lines.append("| Rank | Name | Price | Link |")
    summary_lines.append("|------|------|-------|------|")

    for i, boot in enumerate(boots, start=1):
        summary_lines.append(
            f"| {i} | {boot['name']} | {boot['price']} | [View]({boot['url']}) |"
        )

    new_summary = "\n".join(summary_lines)

    before = content.split(start_marker)[0]
    after = content.split(end_marker)[1]

    updated_content = (
        before
        + start_marker
        + "\n"
        + new_summary
        + "\n"
        + end_marker
        + after
    )

    with open(README_FILE, "w", encoding="utf-8") as f:
        f.write(updated_content)

    log("README updated.")


# --------------------------------------------------
# Main
# --------------------------------------------------

def main():
    run_ts_utc = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    log("Boots watcher started.")

    previous_state = load_previous_state()
    current_boots = scrape_iron_heart_uk()

    if not current_boots:
        log("No boots scraped. Exiting.")
        return

    new_items = detect_new_top3(current_boots, previous_state)

    if new_items:
        log(f"NEW detected: {len(new_items)} item(s).")
        post_to_discord(new_items)
    else:
        log("No NEW in top 3.")

    save_state(current_boots)
    update_readme_summary(run_ts_utc, current_boots)

    log("Boots watcher completed.")


if __name__ == "__main__":
    main()
