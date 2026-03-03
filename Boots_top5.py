import os
import json
import re
import requests
from datetime import datetime
from urllib.parse import urlparse

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

EXCLUDED_KEYWORDS = [
    "bag", "bags"
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

def _is_valid_boot_item(item) -> bool:
    if not isinstance(item, dict):
        return False
    url = item.get("url")
    name = item.get("name")
    price = item.get("price")
    if not isinstance(url, str) or not url.strip():
        return False
    if name is not None and not isinstance(name, str):
        return False
    if price is not None and not isinstance(price, str):
        return False
    return True

def _normalize_site_state(site_name: str, site_value):
    if not isinstance(site_value, list):
        log(f"State schema invalid for {site_name}: expected list. Resetting site state.")
        return []

    cleaned = []
    seen_urls = set()
    for item in site_value:
        if not _is_valid_boot_item(item):
            continue
        url = item["url"].strip()
        if url in seen_urls:
            continue
        seen_urls.add(url)
        cleaned.append({
            "name": item.get("name", ""),
            "price": item.get("price", ""),
            "url": url
        })

    return cleaned

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
            data = json.loads(content)
    except Exception:
        log("State file corrupted. Resetting.")
        return {}

    if not isinstance(data, dict):
        log("State schema invalid: expected object at root. Resetting.")
        return {}

    normalized = {}
    for site_name, site_value in data.items():
        normalized[site_name] = _normalize_site_state(str(site_name), site_value)

    return normalized

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
# SHOPIFY SCRAPER (JSON + FALLBACK)
# ==================================================

def _is_footwear_product(title: str, product_type: str = "", tags_text: str = "") -> bool:
    title_lower = (title or "").lower()
    product_type_lower = (product_type or "").lower()
    tags_lower = (tags_text or "").lower()

    if any(k in title_lower for k in EXCLUDED_KEYWORDS):
        return False

    return (
        any(k in title_lower for k in FOOTWEAR_KEYWORDS) or
        "boot" in product_type_lower or
        "footwear" in product_type_lower or
        "shoe" in product_type_lower or
        "boot" in tags_lower
    )

def _extract_product_handles_from_collection_html(html: str):
    matches = re.findall(r'href="([^"]*?/products/[^"?&#]+)"', html, flags=re.IGNORECASE)
    seen = set()
    handles = []
    for href in matches:
        parsed = urlparse(href)
        path = parsed.path if parsed.path else href
        if "/products/" not in path:
            continue
        handle = path.split("/products/", 1)[1].strip("/").split("/", 1)[0]
        if not handle:
            continue
        if handle in seen:
            continue
        seen.add(handle)
        handles.append(handle)
    return handles

def _fetch_product_js(base: str, handle: str):
    url = f"{base}/products/{handle}.js"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None

def scrape_shopify_html_fallback(site_name: str, base: str, collection: str):
    collection_url = f"{base}{collection}"
    log(f"{site_name}: using HTML fallback for collection {collection_url}")

    try:
        resp = requests.get(collection_url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        html = resp.text or ""
    except Exception as e:
        log(f"{site_name} fallback HTML request failed: {e}")
        return []

    handles = _extract_product_handles_from_collection_html(html)
    if not handles:
        log(f"{site_name}: fallback found 0 product handles in HTML")
        return []

    boots = []
    seen_urls = set()

    for handle in handles[:30]:
        product_url = f"{base}/products/{handle}"
        if product_url in seen_urls:
            continue

        pdata = _fetch_product_js(base, handle)
        title = handle
        price = ""
        product_type = ""
        tags_text = ""

        if isinstance(pdata, dict):
            title = pdata.get("title") or title
            product_type = pdata.get("type") or ""
            tags_val = pdata.get("tags")
            if isinstance(tags_val, list):
                tags_text = " ".join(str(t) for t in tags_val)
            elif isinstance(tags_val, str):
                tags_text = tags_val

            variants = pdata.get("variants", [])
            if isinstance(variants, list) and variants:
                v0 = variants[0] if isinstance(variants[0], dict) else {}
                cents = v0.get("price")
                if isinstance(cents, int):
                    price = f"${cents / 100:.2f}"
                elif isinstance(cents, str) and cents.isdigit():
                    price = f"${int(cents) / 100:.2f}"

        if not _is_footwear_product(str(title), str(product_type), str(tags_text)):
            continue

        seen_urls.add(product_url)
        boots.append({
            "name": title,
            "price": price,
            "url": product_url
        })

        if len(boots) == 5:
            break

    log(f"{site_name}: fallback returning {len(boots)} boots")
    return boots

def scrape_shopify_json(site_name: str, base: str, collection: str):
    url = f"{base}{collection}/products.json?limit=50"

    try:
        response = requests.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()
    except requests.exceptions.HTTPError as e:
        status = getattr(getattr(e, "response", None), "status_code", None)
        if status == 404:
            log(f"{site_name} request failed: {e} (will try HTML fallback)")
            return scrape_shopify_html_fallback(site_name, base, collection)
        log(f"{site_name} request failed: {e}")
        return []
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

        if not _is_footwear_product(str(title), str(product_type), str(tags)):
            continue

        if not handle:
            continue

        product_url = f"{base}/products/{handle}"

        if product_url in seen:
            continue
        seen.add(product_url)

        variants = product.get("variants", [])
        price = ""
        if variants and isinstance(variants, list):
            price_val = variants[0].get("price", "")
            price = f"${price_val}" if price_val != "" else ""

        boots.append({
            "name": title,
            "price": price,
            "url": product_url
        })

        if len(boots) == 5:
            break

    log(f"{site_name}: returning {len(boots)} boots")
    return boots

# ==================================================
# NEW DETECTION
# ==================================================

def _previous_seen_urls(site_name: str, state: dict):
    prev = state.get(site_name, [])
    if not isinstance(prev, list):
        return set()
    urls = set()
    for item in prev:
        if _is_valid_boot_item(item):
            urls.add(item["url"].strip())
    return urls

def detect_new_top3(site_name: str, current: list, state: dict):
    seen_urls = _previous_seen_urls(site_name, state)

    new_items = []
    for boot in (current[:3] if isinstance(current, list) else []):
        if not _is_valid_boot_item(boot):
            continue
        url = boot["url"].strip()
        if url and url not in seen_urls:
            new_items.append(boot)

    return new_items

# ==================================================
# DISCORD
# ==================================================

def post_to_discord(site_new_map: dict) -> bool:
    if not DISCORD_WEBHOOK_URL:
        log("No Discord webhook set.")
        return False

    lines = ["**🆕 NEW Boots Detected (Top 3) 🆕**\n"]

    for site, boots in site_new_map.items():
        lines.append(f"\n__{site.replace('_',' ').upper()}__\n")
        for boot in boots:
            lines.append(f"**{boot.get('name','')}**\n{boot.get('price','')}\n{boot.get('url','')}\n")

    try:
        response = requests.post(
            DISCORD_WEBHOOK_URL,
            json={"content": "\n".join(lines)},
            timeout=15
        )
        if response.status_code in (200, 204):
            log("Posted NEW boots to Discord.")
            return True
        log(f"Discord error: {response.status_code}")
        return False
    except Exception as e:
        log(f"Discord post failed: {e}")
        return False

# ==================================================
# README UPDATE (ALL SITES)
# ==================================================

def update_readme_summary(run_ts_utc: str, site_results: dict):
    if not os.path.exists(README_FILE):
        log("README not found. Skipping.")
        return

    with open(README_FILE, "r", encoding="utf-8") as f:
        content = f.read()

    start_marker = "<!-- BOOTS_SUMMARY_START -->"
    end_marker = "<!-- BOOTS_SUMMARY_END -->"

    if start_marker not in content or end_marker not in content:
        log("Markers missing in README. Skipping.")
        return

    lines = [
        f"**Last Run (UTC):** `{run_ts_utc}`\n"
    ]

    for site_name, boots in site_results.items():
        lines.append(f"\n## {site_name.replace('_',' ').title()} (Top 5)\n")
        lines.append("| Rank | Name | Price | Link |")
        lines.append("|------|------|-------|------|")

        if boots:
            for i, boot in enumerate(boots, start=1):
                lines.append(
                    f"| {i} | {boot.get('name','')} | {boot.get('price','')} | [View]({boot.get('url','')}) |"
                )
        else:
            lines.append("| - | No boots found | - | - |")

    new_summary = "\n".join(lines)

    before = content.split(start_marker)[0]
    after = content.split(end_marker)[1]

    updated = before + start_marker + "\n" + new_summary + "\n" + end_marker + after

    with open(README_FILE, "w", encoding="utf-8") as f:
        f.write(updated)

    log("README updated with all sites.")

# ==================================================
# MAIN
# ==================================================

def main():
    run_ts_utc = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
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
        site_results[site_name] = boots
        if boots is not None:
            any_site_success = True

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
        if not posted_ok:
            log("Discord post failed; will not save state to avoid losing retry capability.")

    update_readme_summary(run_ts_utc, site_results)

    if posted_ok:
        for site_name, boots in site_results.items():
            state[site_name] = boots
        save_state(state)
    else:
        log("State not saved due to unsuccessful alert delivery.")

    log("Boots watcher completed.")


if __name__ == "__main__":
    main()
