import os
import json
import re
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse

# ==================================================
# GOAL (PRECISE)
# ==================================================
# Ensure per-site state is only updated when that site's scrape succeeds (do not overwrite
# prior state with empty results from failures), while preserving the existing state schema,
# keeping URL-based change detection, aggregating alerts, and saving state only after a
# successful run (and successful alert delivery when applicable).
# Additionally, log timestamps must be in US Eastern Time (EST/EDT).
# Nick's Boots collection now uses pagination to ensure items beyond the first Shopify page are detected.

# ==================================================
# CONFIG
# ==================================================

STATE_FILE = "state_last_top5.json"
LOG_FILE = "logs/boots_watcher.log"
README_FILE = "README.md"

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
SAVE_STATE = os.getenv("SAVE_STATE") == "1"

EASTERN_TZ = ZoneInfo("America/New_York")

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
        "collection": "/collections/in-stock-boots?filter.p.m.custom.left_boot_length=10.5&filter.p.m.custom.left_boot_width=D&sort_by=null"
    },
    "iron_heart_germany": {
        "base": "https://ironheartgermany.com",
        "collection": "/collections/boots?sort_by=created-descending&filter.v.availability=1&filter.v.option.size=10+1%2F2"
    },
    "iron_heart_uk": {
        "base": "https://ironheart.co.uk",
        "collection": "/collections/wesco?filter.v.availability=1&sort_by=created-descending&filter.v.option.size=10+1%2F2"
    }
}

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

FOOTWEAR_KEYWORDS = [
    "boot", "boots", "engineer", "service",
    "oxford", "derby", "wesco", "viberg"
]

EXCLUDED_KEYWORDS = [
    "bag", "bags"
]

SITE_EXCLUDED_KEYWORDS = {
    "iron_heart_uk": ["dressing", "dressings", "lace", "laces", "kiltie", "kilties"],
}

TARGET_SIZE_PATTERNS = [
    r"\b10\.5\b",
    r"\b10½\b",
    r"\b11\b",
    r"\b11d\b"
]

CAD_TO_USD_RATE = None

# ==================================================
# LOGGING
# ==================================================

def log(message: str):
    os.makedirs("logs", exist_ok=True)
    now_est = datetime.now(EASTERN_TZ)
    timestamp = now_est.strftime("%Y-%m-%d %H:%M:%S %Z")
    line = f"[{timestamp}] {message}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")

# ==================================================
# STATE
# ==================================================

def _is_valid_boot_item(item) -> bool:
    if not isinstance(item, dict):
        return False
    url = item.get("url")
    if not isinstance(url, str) or not url.strip():
        return False
    return True


def load_previous_state():
    if not os.path.exists(STATE_FILE):
        log("State file does not exist. Starting fresh.")
        return {}

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        log("State file corrupted. Resetting.")
        return {}

    if not isinstance(data, dict):
        return {}

    return data


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
# SIZE
# ==================================================

def _variant_matches_target_size(variant: dict) -> bool:
    text = " ".join([
        str(variant.get("title", "")),
        str(variant.get("option1", "")),
        str(variant.get("option2", "")),
        str(variant.get("option3", ""))
    ]).lower()

    for pattern in TARGET_SIZE_PATTERNS:
        if re.search(pattern, text):
            return True

    return False

# ==================================================
# FX
# ==================================================

def get_cad_to_usd_rate():
    global CAD_TO_USD_RATE

    if CAD_TO_USD_RATE is not None:
        return CAD_TO_USD_RATE

    try:
        r = requests.get(
            "https://open.er-api.com/v6/latest/CAD",
            headers=HEADERS,
            timeout=15
        )
        r.raise_for_status()
        data = r.json()
        CAD_TO_USD_RATE = data.get("rates", {}).get("USD")
    except Exception:
        CAD_TO_USD_RATE = None

    return CAD_TO_USD_RATE

# ==================================================
# PRICE
# ==================================================

def _shopify_price_to_usd_string(price_value, site_name: str):
    try:
        amount = float(str(price_value))
    except Exception:
        return ""

    if site_name == "brooklyn_clothing":
        rate = get_cad_to_usd_rate()
        if rate:
            amount *= rate

    return f"${amount:.2f}"

# ==================================================
# URL
# ==================================================

def _build_collection_products_json_url(base, collection, limit=250, page=1):

    if "?" in collection:
        path_part, query_part = collection.split("?", 1)
    else:
        path_part, query_part = collection, ""

    url = f"{base}{path_part.rstrip('/')}/products.json"

    parsed = urlparse(url)

    existing = dict(parse_qsl(parsed.query))
    incoming = dict(parse_qsl(query_part)) if query_part else {}

    merged = {}
    merged.update(existing)
    merged.update(incoming)

    merged["limit"] = str(limit)
    merged["page"] = str(page)

    new_query = urlencode(merged)

    return urlunparse(parsed._replace(query=new_query))

# ==================================================
# FILTER
# ==================================================

def _is_footwear_product(site_name, title, product_type="", tags_text=""):

    title = (title or "").lower()
    product_type = (product_type or "").lower()

    if isinstance(tags_text, list):
        tags_text = " ".join(tags_text)

    tags_text = (tags_text or "").lower()

    if any(x in title for x in EXCLUDED_KEYWORDS):
        return False

    site_ex = SITE_EXCLUDED_KEYWORDS.get(site_name, [])
    if any(x in title for x in site_ex):
        return False

    return (
        any(k in title for k in FOOTWEAR_KEYWORDS)
        or "boot" in product_type
        or "footwear" in product_type
        or "boot" in tags_text
    )
# ==================================================
# SCRAPER
# ==================================================

def scrape_shopify_json(site_name, base, collection):

    boots = []
    seen = set()

    page = 1
    max_pages = 5 if site_name == "nicks_ready_to_ship" else 1

    while page <= max_pages:

        url = _build_collection_products_json_url(base, collection, page=page)

        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            log(f"{site_name} request failed: {e}")
            return None

        products = data.get("products", [])

        if not products:
            break

        for product in products:

            title = product.get("title", "")
            handle = product.get("handle", "")
            product_type = product.get("product_type", "")
            tags = product.get("tags", "")

            if not _is_footwear_product(site_name, title, product_type, tags):
                continue

            product_url = f"{base}/products/{handle}"

            if product_url in seen:
                continue

            variants = product.get("variants", [])

            qualifying_variant = None

            for v in variants:

                if not v.get("available"):
                    continue

                if _variant_matches_target_size(v):
                    qualifying_variant = v
                    break

            if not qualifying_variant:
                continue

            seen.add(product_url)

            price = _shopify_price_to_usd_string(
                qualifying_variant.get("price"),
                site_name
            )

            boots.append({
                "name": title,
                "price": price,
                "url": product_url
            })

            if len(boots) == 5:
                log(f"{site_name}: returning {len(boots)} boots")
                return boots

        page += 1

    log(f"{site_name}: returning {len(boots)} boots")
    return boots

# ==================================================
# NEW DETECTION
# ==================================================

def detect_new_top3(site_name, current, state):

    seen = {b["url"] for b in state.get(site_name, []) if _is_valid_boot_item(b)}

    new = []

    for boot in current[:3]:
        if boot["url"] not in seen:
            new.append(boot)

    return new

# ==================================================
# DISCORD
# ==================================================

def post_to_discord(site_new_map):

    if not DISCORD_WEBHOOK_URL:
        log("No Discord webhook set.")
        return False

    lines = ["**🆕 NEW Boots Detected (Top 3) 🆕**\n"]

    for site, boots in site_new_map.items():

        lines.append(f"\n__{site.replace('_',' ').upper()}__\n")

        for boot in boots:
            lines.append(
                f"**{boot['name']}**\n{boot['price']}\n{boot['url']}\n"
            )

    try:

        r = requests.post(
            DISCORD_WEBHOOK_URL,
            json={"content": "\n".join(lines)},
            timeout=15
        )

        if r.status_code in (200, 204):
            log("Posted NEW boots to Discord.")
            return True

        log(f"Discord error {r.status_code}")
        return False

    except Exception as e:
        log(f"Discord post failed: {e}")
        return False

# ==================================================
# README
# ==================================================

def update_readme_summary(run_ts_est, site_results):

    if not os.path.exists(README_FILE):
        return

    with open(README_FILE, "r", encoding="utf-8") as f:
        content = f.read()

    start = "<!-- BOOTS_SUMMARY_START -->"
    end = "<!-- BOOTS_SUMMARY_END -->"

    if start not in content or end not in content:
        return

    lines = [f"**Last Run (Eastern):** `{run_ts_est}`\n"]

    for site, boots in site_results.items():

        lines.append(f"\n## {site.replace('_',' ').title()} (Top 5)\n")
        lines.append("| Rank | Name | Price | Link |")
        lines.append("|------|------|-------|------|")

        if boots:
            for i, b in enumerate(boots, start=1):
                lines.append(
                    f"| {i} | {b['name']} | {b['price']} | [View]({b['url']}) |"
                )
        else:
            lines.append("| - | No boots found | - | - |")

    summary = "\n".join(lines)

    updated = content.split(start)[0] + start + "\n" + summary + "\n" + end + content.split(end)[1]

    with open(README_FILE, "w", encoding="utf-8") as f:
        f.write(updated)

# ==================================================
# MAIN
# ==================================================

def main():

    now_est = datetime.now(EASTERN_TZ)
    run_ts_est = now_est.strftime("%Y-%m-%d %H:%M:%S %Z")

    log("Boots watcher started.")

    state = load_previous_state()

    site_results = {}
    any_site_success = False

    for site_name, config in SITES.items():

        boots = scrape_shopify_json(
            site_name,
            config["base"],
            config["collection"]
        )

        if boots is None:
            log(f"{site_name}: scrape failed; preserving previous state.")
            continue

        any_site_success = True
        site_results[site_name] = boots

    if not any_site_success:
        log("No sites scraped successfully. Exiting.")
        return

    site_new_map = {}

    for site_name, boots in site_results.items():

        new_items = detect_new_top3(site_name, boots, state)

        if new_items:
            site_new_map[site_name] = new_items

    posted_ok = True

    if site_new_map:
        posted_ok = post_to_discord(site_new_map)

    update_readme_summary(run_ts_est, site_results)

    if posted_ok:
        for site_name, boots in site_results.items():
            state[site_name] = boots

        save_state(state)

    log("Boots watcher completed.")

if __name__ == "__main__":
    main()
