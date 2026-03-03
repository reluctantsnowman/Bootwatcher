import os
import json
import requests
from bs4 import BeautifulSoup
from datetime import datetime

# ==================================================
# CONFIG
# ==================================================

STATE_FILE = "state_last_top5.json"
LOG_FILE = "logs/boots_watcher.log"
README_FILE = "README.md"

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
SAVE_STATE = os.getenv("SAVE_STATE") == "1"

DIVISION_ROAD_URL = "https://divisionroadinc.com/collections/footwear/boots?sort_by=created-descending"
BROOKLYN_URL = "https://brooklynclothing.com/collections/boots?sort_by=created-descending"
NICKS_URL = "https://nicksboots.com/collections/ready-to-ship-free-shipping?sort_by=created-descending"
IRON_HEART_GERMANY_URL = "https://ironheartgermany.com/collections/boots?sort_by=created-descending"
IRON_HEART_UK_URL = "https://ironheart.co.uk/collections/wesco?sort_by=created-descending"

HEADERS = {"User-Agent": "Mozilla/5.0"}

# ==================================================
# LOGGING
# ==================================================

def log(message: str):
    os.makedirs("logs", exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{timestamp}] {message}"
    print(line)

    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")

# ==================================================
# STATE HANDLING (FIXED + SAFE)
# ==================================================

def load_previous_state():
    if not os.path.exists(STATE_FILE):
        log("State file does not exist. Starting fresh.")
        return {}

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            content = f.read().strip()

            if not content:
                log("State file is empty. Resetting state.")
                return {}

            return json.loads(content)

    except json.JSONDecodeError:
        log("State file is corrupted. Resetting state.")
        return {}

    except Exception as e:
        log(f"Unexpected error loading state: {e}")
        return {}


def save_state(state: dict):
    if not SAVE_STATE:
        log("SAVE_STATE disabled. Skipping state save.")
        return

    try:
        temp_file = STATE_FILE + ".tmp"

        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)

        os.replace(temp_file, STATE_FILE)  # atomic write
        log("State saved.")

    except Exception as e:
        log(f"Failed to save state: {e}")

# ==================================================
# GENERIC SHOPIFY SCRAPER
# ==================================================

def scrape_shopify_collection(base_url: str, base_domain: str):
    try:
        response = requests.get(base_url, headers=HEADERS, timeout=30)
        response.raise_for_status()
    except Exception as e:
        log(f"Request failed for {base_url}: {e}")
        return []

    soup = BeautifulSoup(response.text, "lxml")
    products = soup.select(".product-item, .grid-product")

    boots = []

    for product in products[:5]:
        title_tag = product.select_one(".product-item__title, .grid-product__title")
        price_tag = product.select_one(".price-item, .grid-product__price")
        link_tag = product.select_one("a")

        if not title_tag or not link_tag:
            continue

        name = title_tag.get_text(strip=True)
        price = price_tag.get_text(strip=True) if price_tag else ""
        url = link_tag.get("href")

        if url and not url.startswith("http"):
            url = base_domain + url

        boots.append({
            "name": name,
            "price": price,
            "url": url
        })

    return boots

# ==================================================
# SITE SCRAPERS
# ==================================================

def scrape_division_road():
    return scrape_shopify_collection(
        DIVISION_ROAD_URL,
        "https://divisionroadinc.com"
    )

def scrape_brooklyn_clothing():
    return scrape_shopify_collection(
        BROOKLYN_URL,
        "https://brooklynclothing.com"
    )

def scrape_nicks():
    return scrape_shopify_collection(
        NICKS_URL,
        "https://nicksboots.com"
    )

def scrape_iron_heart_germany():
    return scrape_shopify_collection(
        IRON_HEART_GERMANY_URL,
        "https://ironheartgermany.com"
    )

def scrape_iron_heart_uk():
    return scrape_shopify_collection(
        IRON_HEART_UK_URL,
        "https://ironheart.co.uk"
    )

# ==================================================
# NEW DETECTION
# ==================================================

def detect_new_top3(site_name: str, current: list, state: dict):
    previous = state.get(site_name, [])
    previous_urls = {boot["url"] for boot in previous[:3]}

    new_items = []

    for boot in current[:3]:
        if boot["url"] not in previous_urls:
            new_items.append(boot)

    return new_items

# ==================================================
# DISCORD ALERT
# ==================================================

def post_to_discord(site_new_map: dict):
    if not DISCORD_WEBHOOK_URL:
        log("No Discord webhook set.")
        return

    lines = ["**🆕 NEW Boots Detected (Top 3) 🆕**\n"]

    for site, boots in site_new_map.items():
        lines.append(f"\n__{site.replace('_',' ').upper()}__\n")

        for boot in boots:
            lines.append(
                f"**{boot['name']}**\n{boot['price']}\n{boot['url']}\n"
            )

    payload = {"content": "\n".join(lines)}

    try:
        response = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=15)

        if response.status_code in (200, 204):
            log("Posted NEW boots to Discord.")
        else:
            log(f"Discord error: {response.status_code} {response.text}")

    except Exception as e:
        log(f"Discord post failed: {e}")

# ==================================================
# README UPDATE
# ==================================================

def update_readme_summary(run_ts_utc: str, boots: list):
    if not os.path.exists(README_FILE):
        return

    with open(README_FILE, "r", encoding="utf-8") as f:
        content = f.read()

    start_marker = "<!-- BOOTS_SUMMARY_START -->"
    end_marker = "<!-- BOOTS_SUMMARY_END -->"

    if start_marker not in content or end_marker not in content:
        return

    lines = []
    lines.append(f"**Last Run (UTC):** `{run_ts_utc}`\n")
    lines.append("### Iron Heart UK - Wesco (Top 5)\n")
    lines.append("| Rank | Name | Price | Link |")
    lines.append("|------|------|-------|------|")

    for i, boot in enumerate(boots, start=1):
        lines.append(
            f"| {i} | {boot['name']} | {boot['price']} | [View]({boot['url']}) |"
        )

    new_summary = "\n".join(lines)

    before = content.split(start_marker)[0]
    after = content.split(end_marker)[1]

    updated = (
        before
        + start_marker
        + "\n"
        + new_summary
        + "\n"
        + end_marker
        + after
    )

    with open(README_FILE, "w", encoding="utf-8") as f:
        f.write(updated)

    log("README updated.")

# ==================================================
# MAIN
# ==================================================

def main():
    run_ts_utc = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    log("Boots watcher started.")

    state = load_previous_state()

    site_results = {
        "division_road": scrape_division_road(),
        "brooklyn_clothing": scrape_brooklyn_clothing(),
        "nicks_ready_to_ship": scrape_nicks(),
        "iron_heart_germany": scrape_iron_heart_germany(),
        "iron_heart_uk": scrape_iron_heart_uk(),
    }

    site_results = {k: v for k, v in site_results.items() if v}

    if not site_results:
        log("No sites scraped successfully. Exiting.")
        return

    site_new_map = {}

    for site_name, boots in site_results.items():
        new_items = detect_new_top3(site_name, boots, state)

        if new_items:
            log(f"{site_name}: {len(new_items)} NEW item(s).")
            site_new_map[site_name] = new_items
        else:
            log(f"{site_name}: No NEW in top 3.")

    if site_new_map:
        post_to_discord(site_new_map)
    else:
        log("No NEW items across any site.")

    for site_name, boots in site_results.items():
        state[site_name] = boots

    save_state(state)

    if "iron_heart_uk" in site_results:
        update_readme_summary(run_ts_utc, site_results["iron_heart_uk"])

    log("Boots watcher completed.")


if __name__ == "__main__":
    main()
