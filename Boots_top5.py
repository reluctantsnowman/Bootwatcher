import os
import json
import re
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse
from bs4 import BeautifulSoup

# ==================================================
# GOAL (PRECISE)
# ==================================================
# Ensure per-site state is only updated when that site's scrape succeeds while preserving
# state schema, URL-based change detection, alert aggregation, and safe state persistence.
# Additionally, Division Road must fall back to HTML scraping if Shopify JSON is blocked.
# All log timestamps must be US Eastern Time.

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
    },
    "bakers_exclusive": {
    "base": "https://bakershoe.com",
    "collection": "/collections/bakers-exclusive?sort_by=created-descending"
    }
}

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "text/html"
}

FOOTWEAR_KEYWORDS = [
    "boot", "boots", "engineer", "service",
    "oxford", "derby", "wesco", "viberg"
]

EXCLUDED_KEYWORDS = ["bag", "bags"]

SITE_EXCLUDED_KEYWORDS = {
    "iron_heart_uk": ["dressing", "dressings", "lace", "laces", "kiltie", "kilties"]
}

CAD_TO_USD_RATE = None

# ==================================================
# LOGGING
# ==================================================

def log(message: str):
    os.makedirs("logs", exist_ok=True)
    now_est = datetime.now(EASTERN_TZ)
    ts = now_est.strftime("%Y-%m-%d %H:%M:%S %Z")
    line = f"[{ts}] {message}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")

# ==================================================
# STATE
# ==================================================

def load_previous_state():
    if not os.path.exists(STATE_FILE):
        log("State file does not exist. Starting fresh.")
        return {}

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        log("State file corrupted. Resetting.")
        return {}

def save_state(state):
    if not SAVE_STATE:
        log("SAVE_STATE disabled.")
        return

    try:
        temp = STATE_FILE + ".tmp"
        with open(temp, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        os.replace(temp, STATE_FILE)
        log("State saved.")
    except Exception as e:
        log(f"Failed to save state: {e}")

# ==================================================
# SIZE MATCHING
# ==================================================

def _variant_matches_target_size(variant):

    parts = [
        str(variant.get("title", "")),
        str(variant.get("option1", "")),
        str(variant.get("option2", "")),
        str(variant.get("option3", ""))
    ]

    text = " ".join(parts).lower()

    text = text.replace("½", "0.5")
    text = text.replace("1/2", "0.5")

    return bool(re.search(r"\b(10\.5|11)\b", text))

def _nicks_product_title_matches_target_size(product_title):

    t = (product_title or "").lower()
    t = t.replace("½", "0.5").replace("1/2", "0.5")

    return bool(re.search(r"\b(10\.5|11)\s*d\b", t))

# ==================================================
# FX
# ==================================================

def get_cad_to_usd_rate():
    global CAD_TO_USD_RATE

    if CAD_TO_USD_RATE:
        return CAD_TO_USD_RATE

    try:
        r = requests.get("https://open.er-api.com/v6/latest/CAD", timeout=15)
        CAD_TO_USD_RATE = r.json()["rates"]["USD"]
    except Exception:
        CAD_TO_USD_RATE = None

    return CAD_TO_USD_RATE

# ==================================================
# PRICE
# ==================================================

def _shopify_price_to_usd_string(price_value, site_name):

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
# PRODUCT FILTER
# ==================================================

def _is_footwear_product(site_name, title, product_type="", tags_text=""):

    title = (title or "").lower()
    product_type = (product_type or "").lower()

    if isinstance(tags_text, list):
        tags_text = " ".join(tags_text)

    tags_text = (tags_text or "").lower()

    if any(k in title for k in EXCLUDED_KEYWORDS):
        return False

    site_ex = SITE_EXCLUDED_KEYWORDS.get(site_name, [])
    if any(k in title for k in site_ex):
        return False

    return (
        any(k in title for k in FOOTWEAR_KEYWORDS)
        or "boot" in product_type
        or "footwear" in product_type
        or "boot" in tags_text
    )

# ==================================================
# URL BUILDER
# ==================================================

def _build_collection_products_json_url(base, collection, limit=250, page=1):

    if "?" in collection:
        path, query = collection.split("?", 1)
    else:
        path, query = collection, ""

    url = f"{base}{path.rstrip('/')}/products.json"

    parsed = urlparse(url)
    merged = dict(parse_qsl(parsed.query))
    merged.update(dict(parse_qsl(query)))
    merged["limit"] = str(limit)
    merged["page"] = str(page)

    return urlunparse(parsed._replace(query=urlencode(merged)))

# ==================================================
# DIVISION ROAD HTML FALLBACK
# ==================================================

def scrape_division_road_html(base, collection):

    url = base + collection

    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
    except Exception as e:
        log(f"division_road HTML request failed: {e}")
        return None

    soup = BeautifulSoup(r.text, "lxml")

    boots = []
    seen = set()

    cards = soup.select('a[href*="/products/"]')

    for c in cards:

        name = c.get_text(strip=True)
        link = c.get("href")

        if not link or not name:
            continue

        full_url = base + link

        if full_url in seen:
            continue

        if not _is_footwear_product("division_road", name):
            continue

        seen.add(full_url)

        boots.append({
            "name": name,
            "price": "",
            "url": full_url
        })

        if len(boots) == 5:
            break

    log(f"division_road HTML fallback returning {len(boots)} boots")
    return boots

# ==================================================
# SHOPIFY SCRAPER
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
        except Exception as e:

            if site_name == "division_road":
                log("division_road JSON blocked; using HTML fallback.")
                return scrape_division_road_html(base, collection)

            log(f"{site_name} request failed: {e}")
            return None

        data = r.json()
        products = data.get("products", [])

        if not products:
            break

        for product in products:

            title = product.get("title", "")
            handle = product.get("handle", "")
            product_type = product.get("product_type", "")
            tags = product.get("tags", [])

            if not _is_footwear_product(site_name, title, product_type, tags):
                continue

            url = f"{base}/products/{handle}"

            if url in seen:
                continue

            variants = product.get("variants", [])

            qualifying = None

            for v in variants:
                if v.get("available") and _variant_matches_target_size(v):
                    qualifying = v
                    break

            if not qualifying and site_name == "nicks_ready_to_ship":
                if _nicks_product_title_matches_target_size(title):
                    for v in variants:
                        if v.get("available"):
                            qualifying = v
                            break

            if not qualifying:
                continue

            seen.add(url)

            boots.append({
                "name": title,
                "price": _shopify_price_to_usd_string(
                    qualifying.get("price"), site_name
                ),
                "url": url
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

    seen = {b["url"] for b in state.get(site_name, [])}

    new = []

    for boot in current[:3]:
        if boot["url"] not in seen:
            new.append(boot)

    return new

# ==================================================
# README UPDATE
# ==================================================

def update_readme(site_results, run_ts):

    lines = []
    lines.append("# Boots Watcher\n")
    lines.append(f"Last updated: {run_ts}\n")

    for site, boots in site_results.items():

        lines.append(f"\n## {site.replace('_',' ').title()} (Top 5)\n")
        lines.append("| Rank | Name | Price | Link |\n")
        lines.append("|---|---|---|---|\n")

        for i, b in enumerate(boots, start=1):

            name = b.get("name","")
            price = b.get("price","")
            url = b.get("url","")

            lines.append(
                f"| {i} | {name} | {price} | [View]({url}) |\n"
            )

    try:
        with open(README_FILE, "w", encoding="utf-8") as f:
            f.writelines(lines)
        log("README updated.")
    except Exception as e:
        log(f"README update failed: {e}")

# ==================================================
# DISCORD
# ==================================================

def post_to_discord(site_new_map):

    if not DISCORD_WEBHOOK_URL:
        log("No Discord webhook set.")
        return False

    embeds = []

    for site, boots in site_new_map.items():

        site_title = site.replace("_", " ").title()

        lines = []

        for i, b in enumerate(boots, start=1):

            name = b["name"]
            price = b["price"]
            url = b["url"]

            lines.append(f"{i}️⃣ [{name}]({url}) — {price}")

        embed = {
            "title": site_title,
            "description": "\n".join(lines),
            "color": 16753920  # boot leather orange
        }

        embeds.append(embed)

    payload = {
        "content": "👢 **Boot Watcher — New Drops Detected**",
        "embeds": embeds
    }

    try:

        r = requests.post(
            DISCORD_WEBHOOK_URL,
            json=payload,
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
# MAIN
# ==================================================

def main():

    now_est = datetime.now(EASTERN_TZ)
    run_ts_est = now_est.strftime("%Y-%m-%d %H:%M:%S %Z")

    log("Boots watcher started.")

    state = load_previous_state()

    site_results = {}
    any_success = False

    for site_name, config in SITES.items():

        boots = scrape_shopify_json(
            site_name,
            config["base"],
            config["collection"]
        )

        if boots is None:
            log(f"{site_name}: scrape failed; preserving previous state.")
            continue

        any_success = True
        site_results[site_name] = boots

    if not any_success:
        log("No sites scraped successfully.")
        return

    update_readme(site_results, run_ts_est)

    site_new_map = {}

    for site, boots in site_results.items():

        new_items = detect_new_top3(site, boots, state)

        if new_items:
            site_new_map[site] = new_items

    posted_ok = True

    if site_new_map:
        posted_ok = post_to_discord(site_new_map)

    if posted_ok:
        for site, boots in site_results.items():
            state[site] = boots
        save_state(state)

    log("Boots watcher completed.")

if __name__ == "__main__":
    main()
