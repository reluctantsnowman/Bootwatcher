import json
import os
import re
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone

# --- URLs (sorted: Date, new -> old) ---
DIVISIONROAD_URL = "https://divisionroadinc.com/collections/footwear/boots?sort_by=created-descending"
BROOKLYN_URL = "https://brooklynclothing.com/collections/boots?grid_list=grid-view&sort_by=created-descending"
NICKS_URL = (
    "https://nicksboots.com/collections/ready-to-ship-free-shipping"
    "?sort_by=created-descending"
    "&filter.p.m.custom.left_boot_length=10.5"
    "&filter.p.m.custom.left_boot_width=D"
)

IRONHEART_DE_URL = "https://ironheartgermany.com/collections/boots?sort_by=created-descending&filter.p.product_type=Boots"
IRONHEART_UK_URL = "https://ironheart.co.uk/collections/wesco?sort_by=created-descending"

DIVISIONROAD_TARGET_TITLE = "Stow Boot - 4497 - Leather - Tempesti Ambra Elbamatt Liscio"

STATE_FILE = "state_last_top5.json"

# “Include” words for noisier collections (Brooklyn)
INCLUDE_WORDS = [
    "boot", "boots", "moc", "chukka", "shoe", "shoes", "oxford", "derby", "blucher", "loafer",
    "slip-on", "slipper", "monkey", "service", "chelsea", "roper", "engineer",
    "brogue", "wingtip"
]

# “Exclude” words for all sites (filters obvious non-footwear)
EXCLUDE_WORDS = [
    "garment bag", "bag", "tote", "shoe tree", "tree", "gift card", "gift",
    "belt", "wallet", "billfold", "key clip", "keychain", "key chain", "key fob",
    "lace", "laces", "brush", "cream", "wax", "conditioner", "oil", "spray",
    "socks", "provisions", "shade case", "case"
]


def norm(s: str) -> str:
    s = s.replace("\u2019", "'")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def is_footwear_title(collection_url: str, title: str) -> bool:
    """
    Division Road: accept anything in Boots collection unless excluded.
    Brooklyn: require INCLUDE_WORDS (plus not excluded).
    Nick's: require footwear keywords because RTS can include accessories.
    Iron Heart DE/UK: require boot/boots keywords (collections are mostly boots, but this avoids accessories).
    """
    t = title.lower()

    if any(bad in t for bad in EXCLUDE_WORDS):
        return False

    if "divisionroadinc.com" in collection_url:
        return True

    if "nicksboots.com" in collection_url:
        nicks_keywords = ["boot", "boots", "shoe", "shoes", "chukka", "moc", "chelsea", "engineer"]
        return any(k in t for k in nicks_keywords)

    if "ironheartgermany.com" in collection_url or "ironheart.co.uk" in collection_url:
        return ("boot" in t) or ("boots" in t)

    # Brooklyn (and anything else): require general footwear keywords
    return any(good in t for good in INCLUDE_WORDS)


def fetch_html(url: str) -> str:
    r = requests.get(url, headers={"User-Agent": "top5-footwear-bot/2.3"}, timeout=30)
    r.raise_for_status()
    return r.text


def make_absolute_url(collection_url: str, href: str) -> str:
    if href.startswith("http"):
        return href
    base = re.match(r"^(https?://[^/]+)", collection_url)
    return (base.group(1) if base else "") + href


def parse_money(price_str: str):
    """
    Parse a price string like '$594', '$1,475.00', '€399.00', '£725'
    Returns: (currency_code, amount_float) or (None, None)
    """
    if not price_str:
        return None, None
    s = price_str.strip()

    # Capture symbol + number
    m = re.search(r"([€£$])\s?(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)", s)
    if not m:
        return None, None

    sym = m.group(1)
    amt_s = m.group(2).replace(",", "")
    try:
        amt = float(amt_s)
    except ValueError:
        return None, None

    # Currency code by symbol (site-specific $ handled later)
    if sym == "€":
        return "EUR", amt
    if sym == "£":
        return "GBP", amt
    if sym == "$":
        return "USD", amt  # may be CAD for Brooklyn, handled later
    return None, None


def get_fx_rate_latest(from_ccy: str, to_ccy: str = "USD") -> tuple[float, str]:
    """
    Fetch latest FX rate (no key) from Frankfurter.
    Returns: (to_per_from, date)
    """
    url = f"https://api.frankfurter.dev/v1/latest?from={from_ccy}&to={to_ccy}"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    data = r.json()
    rate = float(data["rates"][to_ccy])
    rate_date = str(data.get("date", "unknown"))
    return rate, rate_date


def extract_top_entries(collection_url: str, html: str, n: int = 5):
    soup = BeautifulSoup(html, "html.parser")
    seen = set()
    out = []

    # Find product links
    for a in soup.select("a[href*='/products/']"):
        href = (a.get("href") or "").strip()
        if not href or "/products/" not in href:
            continue

        prod_url = make_absolute_url(collection_url, href)
        if prod_url in seen:
            continue

        # 1) Try link text first
        title = norm(a.get_text(" ", strip=True))

        # 2) If empty (common on image links), search within the product card
        if not title:
            card = a.find_parent()
            for _ in range(10):
                if not card:
                    break

                h = card.find(["h1", "h2", "h3"])
                if h:
                    t = norm(h.get_text(" ", strip=True))
                    if t:
                        title = t
                        break

                tnode = card.select_one(
                    ".product-title, .card__heading, .grid-product__title, "
                    ".productitem--title, [class*='title']"
                )
                if tnode:
                    t = norm(tnode.get_text(" ", strip=True))
                    if t:
                        title = t
                        break

                card = card.find_parent()

        if not title or len(title) < 4:
            continue
        if not is_footwear_title(collection_url, title):
            continue

        # Best-effort price capture near the link/card (supports $, €, £)
        price_raw = None
        card = a.find_parent()
        for _ in range(10):
            if not card:
                break
            text = card.get_text(" ", strip=True)
            pm = re.search(r"[€£$]\s?\d{1,3}(?:,\d{3})*(?:\.\d{2})?", text)
            if pm:
                price_raw = pm.group(0).replace(" ", "")
                break
            card = card.find_parent()

        seen.add(prod_url)
        out.append((title, prod_url, price_raw))
        if len(out) >= n:
            break

    return out


def format_price(collection_url: str, price_str: str | None, fx_map: dict) -> str | None:
    """
    Returns a display string. Converts non-USD currencies to USD using latest FX.
    Brooklyn "$" is treated as CAD and converted to USD.
    """
    if not price_str:
        return None

    ccy, amt = parse_money(price_str)
    if ccy is None or amt is None:
        return price_str

    # Brooklyn: treat $ as CAD, not USD
    if "brooklynclothing.com" in collection_url and ccy == "USD":
        ccy = "CAD"

    # If already USD, return as-is
    if ccy == "USD":
        return price_str

    fx = fx_map.get(ccy)
    if not fx:
        return f"{price_str} {ccy} (USD unavailable)"

    usd_per = fx["rate"]
    rate_date = fx["date"]
    usd_amt = amt * usd_per
    return f"{price_str} {ccy} (~${usd_amt:,.2f} USD @ {rate_date})"


def load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return {"saved_at_utc": None, "sites": {}}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"saved_at_utc": None, "sites": {}}
        data.setdefault("sites", {})
        data.setdefault("saved_at_utc", None)
        return data
    except Exception:
        return {"saved_at_utc": None, "sites": {}}


def save_state(state: dict):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def compute_new_flags(site_key: str, items, prev_state: dict):
    prev_urls = set((prev_state.get("sites", {}).get(site_key, {}) or {}).get("urls", []) or [])
    out = []
    for title, url, price_raw in items:
        out.append(
            {
                "title": title,
                "url": url,
                "price_raw": price_raw,
                "is_new": (url not in prev_urls) if prev_urls else False,  # first baseline -> don't spam NEW
            }
        )
    return out


def print_top5(name: str, items_dicts, collection_url: str, fx_map: dict):
    print(f"\n=== {name} ===")
    for i, it in enumerate(items_dicts, start=1):
        price_fmt = format_price(collection_url, it["price_raw"], fx_map)
        new_tag = " 🆕 NEW" if it["is_new"] else ""
        if price_fmt:
            print(f"{i}. {it['title']}{new_tag} — {price_fmt}")
        else:
            print(f"{i}. {it['title']}{new_tag}")
        print(f"   {it['url']}")


def _truncate(s: str, max_len: int) -> str:
    s = s or ""
    return s if len(s) <= max_len else s[: max_len - 1] + "…"


def build_discord_payload(all_sites, fx_map: dict, prev_saved_at_utc: str | None):
    """
    all_sites: list of dicts [{key, name, url, items, desc(optional)}]
    Creates 1 header embed + 1 embed per site (safe vs Discord length limits).
    """
    header_lines = ["Sorted by **Date: new → old**."]
    for s in all_sites:
        header_lines.append(f"{s['name']}: <{s['url']}>")
    if prev_saved_at_utc:
        header_lines.append(f"\nBaseline from: **{prev_saved_at_utc} UTC**")
    else:
        header_lines.append("\nBaseline: **none yet**")

    embeds = [{"title": "🧾 Boots Watch — Top 5 Newest", "description": "\n".join(header_lines)}]

    for s in all_sites:
        emb = {
            "title": f"🏷️ {s['name']} — Top 5",
            "description": s.get("desc", ""),
            "fields": [],
        }
        for i, it in enumerate(s["items"], start=1):
            price_fmt = format_price(s["url"], it["price_raw"], fx_map)
            new_tag = "🆕 NEW — " if it["is_new"] else ""
            field_name = _truncate(f"{i}. {it['title']}", 256)
            value_lines = []
            if price_fmt:
                value_lines.append(f"{new_tag}{price_fmt}" if new_tag else price_fmt)
            else:
                if it["is_new"]:
                    value_lines.append("🆕 NEW")
            value_lines.append(f"<{it['url']}>")
            emb["fields"].append({"name": field_name, "value": _truncate("\n".join(value_lines), 1024), "inline": False})
        if not s["items"]:
            emb["fields"].append({"name": "No entries found", "value": "—", "inline": False})

        embeds.append(emb)

    return {"content": None, "embeds": embeds, "allowed_mentions": {"parse": []}}


def send_discord_embed(webhook_url: str, payload: dict):
    resp = requests.post(webhook_url, json=payload, timeout=30)
    if resp.status_code not in (200, 204):
        raise RuntimeError(f"Discord webhook error {resp.status_code}: {resp.text}")


def main():
    prev_state = load_state()
    prev_saved_at = prev_state.get("saved_at_utc")

    # FX map (only fetch what we need)
    fx_map = {}
    # CAD->USD, EUR->USD, GBP->USD
    for ccy in ("CAD", "EUR", "GBP"):
        try:
            rate, date = get_fx_rate_latest(ccy, "USD")
            fx_map[ccy] = {"rate": rate, "date": date}
        except Exception as e:
            print(f"WARNING: Could not fetch latest {ccy}→USD rate. Reason: {e}")

    # Scrape raw top5
    dr_raw = extract_top_entries(DIVISIONROAD_URL, fetch_html(DIVISIONROAD_URL), 5)
    bc_raw = extract_top_entries(BROOKLYN_URL, fetch_html(BROOKLYN_URL), 5)
    n_raw = extract_top_entries(NICKS_URL, fetch_html(NICKS_URL), 5)
    ih_de_raw = extract_top_entries(IRONHEART_DE_URL, fetch_html(IRONHEART_DE_URL), 5)
    ih_uk_raw = extract_top_entries(IRONHEART_UK_URL, fetch_html(IRONHEART_UK_URL), 5)

    # Mark NEW
    dr_items = compute_new_flags("divisionroad", dr_raw, prev_state)
    bc_items = compute_new_flags("brooklyn", bc_raw, prev_state)
    n_items = compute_new_flags("nicks", n_raw, prev_state)
    ih_de_items = compute_new_flags("ironheart_de", ih_de_raw, prev_state)
    ih_uk_items = compute_new_flags("ironheart_uk", ih_uk_raw, prev_state)

    # Division Road target indicator (for Discord desc)
    if dr_items:
        latest_norm = norm(dr_items[0]["title"])
        target_norm = norm(DIVISIONROAD_TARGET_TITLE)
        dr_desc = f"Target still #1? {'✅ YES' if latest_norm == target_norm else '🚨 NO'}"
    else:
        dr_desc = "No entries found."

    # Brooklyn FX line (for Discord desc)
    if fx_map.get("CAD"):
        cad_rate = fx_map["CAD"]["rate"]
        cad_date = fx_map["CAD"]["date"]
        bc_desc = f"CAD→USD: 1 CAD = **{cad_rate:.4f} USD** (as of {cad_date})"
    else:
        bc_desc = "CAD→USD: **unavailable** (FX fetch failed)"

    print("Baseline saved_at_utc:", prev_saved_at)
    print("Division Road top 5:", len(dr_items))
    print("Brooklyn Clothing top 5:", len(bc_items))
    print("Nick's top 5:", len(n_items))
    print("Iron Heart Germany top 5:", len(ih_de_items))
    print("Iron Heart UK top 5:", len(ih_uk_items))
    print("FX available:", ",".join(sorted(fx_map.keys())) if fx_map else "NO")

    # Manual GitHub preview mode: PRINT (no Discord)
    mode = (os.environ.get("OUTPUT_MODE") or "").lower()
    if mode == "github":
        print_top5("Division Road", dr_items, DIVISIONROAD_URL, fx_map)
        print_top5("Brooklyn Clothing", bc_items, BROOKLYN_URL, fx_map)
        print_top5("Nick's (10.5D RTS)", n_items, NICKS_URL, fx_map)
        print_top5("Iron Heart Germany", ih_de_items, IRONHEART_DE_URL, fx_map)
        print_top5("Iron Heart UK (Wesco)", ih_uk_items, IRONHEART_UK_URL, fx_map)

    else:
        # Scheduled mode: Discord if webhook set
        webhook = (os.environ.get("DISCORD_WEBHOOK_URL") or "").strip()
        if webhook:
            sites = [
                {"key": "divisionroad", "name": "Division Road", "url": DIVISIONROAD_URL, "items": dr_items, "desc": dr_desc},
                {"key": "brooklyn", "name": "Brooklyn Clothing", "url": BROOKLYN_URL, "items": bc_items, "desc": bc_desc},
                {"key": "nicks", "name": "Nick’s Ready-to-Ship (10.5D)", "url": NICKS_URL, "items": n_items, "desc": "Filtered to **10.5 D**."},
                {"key": "ironheart_de", "name": "Iron Heart Germany", "url": IRONHEART_DE_URL, "items": ih_de_items, "desc": "Prices shown in EUR with USD conversion when available."},
                {"key": "ironheart_uk", "name": "Iron Heart UK (Wesco)", "url": IRONHEART_UK_URL, "items": ih_uk_items, "desc": "Prices shown in GBP with USD conversion when available."},
            ]
            payload = build_discord_payload(sites, fx_map, prev_saved_at)
            send_discord_embed(webhook, payload)
            print("Discord message sent successfully.")
        else:
            print("DISCORD_WEBHOOK_URL not set; skipping Discord send.")

    # Save baseline if requested
    save_flag = (os.environ.get("SAVE_STATE") or "").strip().lower() in ("1", "true", "yes")
    if save_flag:
        new_state = {
            "saved_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "sites": {
                "divisionroad": {"urls": [it["url"] for it in dr_items]},
                "brooklyn": {"urls": [it["url"] for it in bc_items]},
                "nicks": {"urls": [it["url"] for it in n_items]},
                "ironheart_de": {"urls": [it["url"] for it in ih_de_items]},
                "ironheart_uk": {"urls": [it["url"] for it in ih_uk_items]},
            },
        }
        save_state(new_state)
        print(f"State saved to {STATE_FILE}")
    else:
        print(f"State NOT saved (set SAVE_STATE=1 to update {STATE_FILE}).")


if __name__ == "__main__":
    main()
