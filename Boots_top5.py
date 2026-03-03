import os
import json
import requests
from datetime import datetime

# ==================================================
# CONFIG
# ==================================================

STATE_FILE = "state_last_top5.json"
LOG_FILE = "logs/boots_watcher.log"
README_FILE = "README.md"

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
SAVE_STATE = os.getenv("SAVE_STATE") == "1"

SITES = {
    "division_road": {
        "base": "https://divisionroadinc.com",
        "collection": "/collections/footwear/boots"
    },
    "brooklyn_clothing": {
        "base": "https://brooklynclothing.com",
        "collection": "/collections/boots"
    },
    "nicks_ready_to_ship": {
        "base": "https://nicksboots.com",
        "collection": "/collections/ready-to-ship-free-shipping"
    },
    "iron_heart_germany": {
        "base": "https://ironheartgermany.com",
        "collection": "/collections/boots"
    },
    "iron_heart_uk": {
        "base": "https://ironheart.co.uk",
        "collection": "/collections/wesco"
    }
}

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

FOOTWEAR_KEYWORDS = [
    "boot", "boots", "engineer", "service",
    "oxford", "derby", "wesco", "viberg"
]

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
# STATE HANDLING
# ==================================================

def load_previous_state():
    if not os.path.exists(STATE_FILE):
        log("State file does not exist. Starting fresh.")
        return {}

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if not content:
                log("State file empty. Resetting.")
                return {}
            return json.loads(content)
    except Exception:
        log("State file corrupted. Resetting.")
        return {}

def save_state(state: dict):
    if not SAVE_STATE:
        log("SAVE_STATE disabled.")
        return

    try:
        temp_file = STATE_FILE + ".tmp"
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        os.replace(temp_file, STATE_FILE)
        log("State saved.")
    except Exception as e:
        log(f"Failed to save state: {e}")

# ==================================================
# SHOPIFY JSON SCRAPER (STRICT FOOTWEAR FILTER)
# ==================================================

def scrape_shopify_json(site_name: str, base: str, collection: str):
    url = f"{base}{collection}/products.json?limit=50"

    try:
        response = requests.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()
    except Exception as e:
        log(f"{site_name} request failed: {e}")
        return []

    try:
        data = response.json()
    except Exception:
        log(f"{site_name} invalid JSON.")
        return []

    products = data.get("products", [])
    boots = []
    seen = set()

    for product in products:
        title = product.get("title", "")
        handle = product.get("handle", "")
        product_type = product.get("product_type", "").lower()
        tags = " ".join(product.get("tags", [])).lower()

        title_lower = title.lower()

        # STRICT FOOTWEAR FILTER
        is_footwear = (
            any(k in title_lower for k in FOOTWEAR_KEYWORDS) or
            "boot" in product_type or
            "footwear" in product_type or
            "shoe" in product_type or
            "boot" in tags
        )

        if not is_footwear:
            continue

        product_url = f"{base}/products/{handle}"

        if product_url in seen:
            continue
        seen.add(product_url)

        variants = product.get("variants", [])
        price = ""
        if variants:
            price = f"${variants[0].get('price', '')}"

        boots.append({
            "name": title,
            "price": price,
            "url": product_url
        })

        if len(boots) == 5:
            break

    log(f"{site_name}: returning {len(boots)} filtered boots")
    return boots

# ==================================================
# NEW DETECTION
# ==================================================

def detect_new_top3(site_name: str, current: list, state: dict):
    previous = state.get(site_name, [])
    previous_urls = {boot["url"] for boot in previous[:3]}
    return [boot for boot in current[:3] if boot["url"] not in previous_urls]

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
            lines.append(f"**{boot['name']}**\n{boot['price']}\n{boot['url']}\n")

    try:
        response = requests.post(
            DISCORD_WEBHOOK_URL,
            json={"content": "\n".join(lines)},
            timeout=15
        )
        if response.status_code in (200, 204):
            log("Posted NEW boots to Discord.")
        else:
            log(f"Discord error: {response.status_code}")
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

    lines = [
        f"**Last Run (UTC):** `{run_ts_utc}`\n",
        "### Iron Heart UK - Wesco (Top 5)\n",
        "| Rank | Name | Price | Link |",
        "|------|------|-------|------|",
    ]

    for i, boot in enumerate(boots, start=1):
        lines.append(
            f"| {i} | {boot['name']} | {boot['price']} | [View]({boot['url']}) |"
        )

    new_summary = "\n".join(lines)

    before = content.split(start_marker)[0]
    after = content.split(end_marker)[1]

    updated = before + start_marker + "\n" + new_summary + "\n" + end_marker + after

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
    site_results = {}

    for site_name, config in SITES.items():
        boots = scrape_shopify_json(
            site_name,
            config["base"],
            config["collection"]
        )
        if boots:
            site_results[site_name] = boots

    if not site_results:
        log("No sites scraped successfully. Exiting.")
        return

    site_new_map = {}

    for site_name, boots in site_results.items():
        new_items = detect_new_top3(site_name, boots, state)
        if new_items:
            site_new_map[site_name] = new_items

    if site_new_map:
        post_to_discord(site_new_map)

    for site_name, boots in site_results.items():
        state[site_name] = boots

    save_state(state)

    if "iron_heart_uk" in site_results:
        update_readme_summary(run_ts_utc, site_results["iron_heart_uk"])

    log("Boots watcher completed.")


if __name__ == "__main__":
    main()
