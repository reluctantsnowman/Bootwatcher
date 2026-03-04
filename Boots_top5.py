import os
import json
import re
import requests
from datetime import datetime
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse

# ==================================================
# GOAL (PRECISE)
# ==================================================
# Ensure per-site state is only updated when that site's scrape succeeds (do not overwrite
# prior state with empty results from failures), while preserving the existing state schema,
# keeping URL-based change detection, aggregating alerts, and saving state only after a
# successful run (and successful alert delivery when applicable).

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
        # User-provided: newest-first + in-stock only + size 10.5
        "collection": "/collections/boots?sort_by=created-descending&filter.v.availability=1&filter.v.option.size=10+1%2F2"
    },
    "iron_heart_uk": {
        "base": "https://ironheart.co.uk",
        # Better Iron Heart setup:
        # - Wesco collection
        # - in-stock only
        # - newest-first
        # - size 10.5
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

# Global exclusions (applies to all sites)
EXCLUDED_KEYWORDS = [
    "bag", "bags"
]

# Site-specific exclusions (do not change state schema; this is runtime filtering only)
SITE_EXCLUDED_KEYWORDS = {
    # Iron Heart UK Wesco collection includes non-boot items like boot dressings, laces, kilties.
    # Exclude those to focus alerts on actual footwear/collab drops.
    "iron_heart_uk": ["dressing", "dressings", "lace", "laces", "kiltie", "kilties"],
    # You can add more site-specific exclusions here if needed, without changing state schema.
}

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
# PRICE HELPERS
# ==================================================

def _shopify_price_to_usd_string(price_value, site_name: str) -> str:
    """
    Shopify products.json typically returns variant.price as a string like "765.00"
    in the shop's presentment currency. For Brooklyn Clothing this appears to be CAD.

    Goal:
    - For Brooklyn Clothing only: convert CAD -> USD when an FX rate is available.
    - Fail safe: if conversion cannot be performed, still return the original price.
    """
    if price_value is None:
        return ""

    try:
        raw = str(price_value).strip()
    except Exception:
        return ""

    if raw == "":
        return ""

    try:
        amount = float(raw)
    except Exception:
        return f"${raw}"

    if site_name != "brooklyn_clothing":
        return f"${amount:.2f}"

    try:
        fx = requests.get(
            "https://api.exchangerate.host/convert",
            params={"from": "CAD", "to": "USD", "amount": amount},
            headers=HEADERS,
            timeout=15
        )
        fx.raise_for_status()
        data = fx.json() if isinstance(fx, requests.Response) else {}
        result = data.get("result")
        if isinstance(result, (int, float)):
            return f"${float(result):.2f}"
    except Exception as e:
        log(f"{site_name}: CAD->USD conversion failed; using original currency amount. Error: {e}")

    return f"${amount:.2f}"

def _cents_to_usd_string(cents_value) -> str:
    if cents_value is None:
        return ""
    try:
        if isinstance(cents_value, int):
            return f"${cents_value / 100:.2f}"
        s = str(cents_value).strip()
        if s.isdigit():
            return f"${int(s) / 100:.2f}"
    except Exception:
        return ""
    return ""

# ==================================================
# URL HELPERS
# ==================================================

def _build_collection_products_json_url(base: str, collection: str, limit: int = 50) -> str:
    """
    Build a Shopify collection products.json URL while preserving any query params
    present on the collection URL (e.g., sort/filter params).
    """
    collection = collection or ""
    if "?" in collection:
        path_part, query_part = collection.split("?", 1)
    else:
        path_part, query_part = collection, ""

    path_part = (path_part or "").rstrip("/")
    url = f"{base}{path_part}/products.json"

    parsed = urlparse(url)
    existing_qs = dict(parse_qsl(parsed.query, keep_blank_values=True))
    incoming_qs = dict(parse_qsl(query_part, keep_blank_values=True)) if query_part else {}

    merged = {}
    merged.update(existing_qs)
    merged.update(incoming_qs)
    merged["limit"] = str(int(limit))

    new_query = urlencode(merged, doseq=True)
    rebuilt = parsed._replace(query=new_query)

    return urlunparse(rebuilt)

# ==================================================
# SHOPIFY SCRAPER (JSON + FALLBACK)
# ==================================================

def _site_exclusions(site_name: str):
    extras = SITE_EXCLUDED_KEYWORDS.get(site_name, [])
    return set(str(x).lower() for x in extras if isinstance(x, str))

def _is_footwear_product(site_name: str, title: str, product_type: str = "", tags_text: str = "") -> bool:
    title_lower = (title or "").lower()
    product_type_lower = (product_type or "").lower()
    tags_lower = (tags_text or "").lower()

    # Global exclusions
    if any(k in title_lower for k in EXCLUDED_KEYWORDS):
        return False

    # Site-specific exclusions
    site_ex = _site_exclusions(site_name)
    if site_ex and any(k in title_lower for k in site_ex):
        return False

    # Avoid obvious non-footwear categories when product_type indicates accessories/care.
    if "accessor" in product_type_lower or "care" in product_type_lower:
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
        return None

    handles = _extract_product_handles_from_collection_html(html)
    if not handles:
        log(f"{site_name}: fallback found 0 product handles in HTML")
        # This is still a successful fetch/parse; just no products found.
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
                price = _cents_to_usd_string(cents)

        if not _is_footwear_product(site_name, str(title), str(product_type), str(tags_text)):
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
    url = _build_collection_products_json_url(base, collection, limit=50)

    try:
        response = requests.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()
    except requests.exceptions.HTTPError as e:
        status = getattr(getattr(e, "response", None), "status_code", None)
        if status == 404:
            log(f"{site_name} request failed: {e} (will try HTML fallback)")
            return scrape_shopify_html_fallback(site_name, base, collection)
        log(f"{site_name} request failed: {e}")
        return None
    except Exception as e:
        log(f"{site_name} request failed: {e}")
        return None

    try:
        data = response.json()
    except Exception:
        log(f"{site_name} invalid JSON.")
        return None

    products = data.get("products", [])
    if not isinstance(products, list):
        log(f"{site_name} JSON schema unexpected: products is not a list.")
        return None

    boots = []
    seen = set()

    for product in products:
        if not isinstance(product, dict):
            continue

        title = product.get("title", "")
        handle = product.get("handle", "")
        product_type = product.get("product_type", "")
        tags_val = product.get("tags", [])
        if isinstance(tags_val, list):
            tags = " ".join(str(t) for t in tags_val)
        else:
            tags = str(tags_val)

        if not _is_footwear_product(site_name, str(title), str(product_type), str(tags)):
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
            v0 = variants[0] if isinstance(variants[0], dict) else {}
            price_val = v0.get("price", "")
            price = _shopify_price_to_usd_string(price_val, site_name)

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
    """
    Detect meaningful change using stable identifiers (URL).
    - Not position-based: ordering shifts do not alert.
    - Only alerts if a URL in current top 3 was not seen previously for that site.
    """
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
    """
    Idempotency strategy:
    - Message content is aggregated across sites.
    - Caller controls state persistence; if posting fails when there are new items,
      state is NOT saved, so reruns will retry rather than silently skipping.
    - Webhook failure never crashes the run.
    """
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
    site_success = {}
    any_site_success = False

    for site_name, config in SITES.items():
        boots = scrape_shopify_json(
            site_name,
            config["base"],
            config["collection"]
        )

        if boots is None:
            # Failed scrape: do not overwrite prior state for this site.
            site_success[site_name] = False
            log(f"{site_name}: scrape failed; preserving previous state for this site.")
            continue

        # Successful scrape (even if boots == [] because none matched filters).
        site_success[site_name] = True
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
        if not posted_ok:
            log("Discord post failed; will not save state to avoid losing retry capability.")

    # README should reflect only successfully scraped results (not stale/unknown).
    update_readme_summary(run_ts_utc, site_results)

    # Only persist state if the run is considered successful:
    # - at least one site succeeded (any_site_success)
    # - and if there were new items, Discord posting succeeded
    if posted_ok:
        for site_name, boots in site_results.items():
            # Update per-entity state only for successful sites.
            state[site_name] = boots
        save_state(state)
    else:
        log("State not saved due to unsuccessful alert delivery.")

    log("Boots watcher completed.")


if __name__ == "__main__":
    main()
