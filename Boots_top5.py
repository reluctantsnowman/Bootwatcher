import json
import os
import re
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

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
    "slip-on", "slipper", "monkey", "service", "chelsea", "roper", "engineer", "brogue", "wingtip"
]

# “Exclude” words for all sites (filters obvious non-footwear)
EXCLUDE_WORDS = [
    "garment bag", "bag", "tote", "shoe tree", "tree", "gift card", "gift",
    "belt", "wallet", "billfold", "key clip", "keychain", "key chain", "key fob",
    "lace", "laces", "brush", "cream", "wax", "conditioner", "oil", "spray",
    "socks", "provisions", "shade case", "case"
]


def norm(s: str) -> str:
    s = (s or "").replace("\u2019", "'")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def pick_bs4_parser() -> str:
    # Use lxml if installed (faster), else fallback
    try:
        import lxml  # noqa: F401
        return "lxml"
    except Exception:
        return "html.parser"


BS4_PARSER = pick_bs4_parser()


def is_footwear_title(collection_url: str, title: str) -> bool:
    """
    Division Road: accept anything in Boots collection unless excluded.
    Brooklyn: require INCLUDE_WORDS (plus not excluded).
    Nick's: require footwear keywords because RTS can include accessories.
    Iron Heart DE/UK: require boot/boots keyword (keeps out random non-boot items).
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


def build_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": "top5-footwear-bot/3.0"})
    return s


def fetch_html(session: requests.Session, url: str) -> str:
    r = session.get(url, timeout=30)
    r.raise_for_status()
    return r.text


def fetch_all_html(session: requests.Session, urls: list[str], max_workers: int = 6) -> dict[str, str]:
    """
    Fetch multiple URLs concurrently.
    Returns {url: html}. Raises on complete failure of a URL.
    """
    out: dict[str, str] = {}
    errors: dict[str, str] = {}

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        fut_map = {ex.submit(fetch_html, session, u): u for u in urls}
        for fut in as_completed(fut_map):
            u = fut_map[fut]
            try:
                out[u] = fut.result()
            except Exception as e:
                errors[u] = str(e)

    if errors:
        # Fail fast with useful info
        msg = "One or more fetches failed:\n" + "\n".join([f"- {u}: {err}" for u, err in errors.items()])
        raise RuntimeError(msg)

    return out


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
    m = re.search(r"([€£$])\s?(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)", s)
    if not m:
        return None, None
    sym = m.group(1)
    amt_s = m.group(2).replace(",", "")
    try:
        amt = float(amt_s)
    except ValueError:
        return None, None

    if sym == "€":
        return "EUR", amt
    if sym == "£":
        return "GBP", amt
    if sym == "$":
        return "USD", amt  # Brooklyn handled separately (CAD)
    return None, None


def get_fx_map(session: requests.Session) -> dict:
    """
    One FX request total:
    Get USD->(CAD,EUR,GBP), then invert to get (CAD/EUR/GBP)->USD.
    """
    fx_map = {}
    try:
        url = "https://api.frankfurter.dev/v1/latest?from=USD&to=CAD,EUR,GBP"
        r = session.get(url, timeout=30)
        r.raise_for_status()
        data = r.json()
        rate_date = str(data.get("date", "unknown"))
        rates = data.get("rates", {}) or {}

        # rates["CAD"] = CAD per 1 USD  => USD per 1 CAD = 1 / rates["CAD"]
        for ccy in ("CAD", "EUR", "GBP"):
            v = rates.get(ccy)
            if v and float(v) != 0.0:
                fx_map[ccy] = {"rate": 1.0 / float(v), "date": rate_date}
    except Exception as e:
        print(f"WARNING: FX fetch failed (USD base). Reason: {e}")

    return fx_map


def extract_top_entries(collection_url: str, html: str, n: int = 5):
    soup = BeautifulSoup(html, BS4_PARSER)
    seen = set()
    out = []

    for a in soup.select("a[href*='/products/']"):
        href = (a.get("href") or "").strip()
        if not href or "/products/" not in href:
            continue

        prod_url = make_absolute_url(collection_url, href)
        if prod_url in seen:
            continue

        # 1) Try link text
        title = norm(a.get_text(" ", strip=True))

        # 2) If empty (image links), search parent card
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
    Converts non-USD currencies to USD using latest FX.
    Brooklyn "$" is treated as CAD and converted to USD.
    """
    if not price_str:
        return None

    ccy, amt = parse_money(price_str)
    if ccy is None or amt is None:
        return price_str

    # Brooklyn: treat $ as CAD
    if "brooklynclothing.com" in collection_url and ccy == "USD":
        ccy = "CAD"

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
    """
    Supports both:
    - NEW format: {"saved_at_utc": "...", "sites": {"divisionroad": {"urls": [...]}, ...}}
    - LEGACY format: [{"title": "...", "url": "..."}, ...]  (assumed Division Road)
    """
    if not os.path.exists(STATE_FILE):
        return {"saved_at_utc": None, "sites": {}}

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Legacy: list of {title,url}
        if isinstance(data, list):
            urls = []
            for it in data:
                if isinstance(it, dict):
                    u = (it.get("url") or "").strip()
                    if u:
                        urls.append(u)

            return {
                "saved_at_utc": None,
                "sites": {
                    "divisionroad": {"urls": urls}
                },
            }

        # New format: dict
        if isinstance(data, dict):
            data.setdefault("saved_at_utc", None)
            data.setdefault("sites", {})
            return data

        return {"saved_at_utc": None, "sites": {}}

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
                "is_new": (url not in prev_urls) if prev_urls else False,  # no baseline => no NEW spam
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
    header_lines = ["Sorted by **Date: new → old**."]
    for s in all_sites:
        header_lines.append(f"{s['name']}: <{s['url']}>")
    header_lines.append(f"\nBaseline from: **{prev_saved_at_utc} UTC**" if prev_saved_at_utc else "\nBaseline: **none yet**")

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


def send_discord_embed(session: requests.Session, webhook_url: str, payload: dict):
    resp = session.post(webhook_url, json=payload, timeout=30)
    if resp.status_code not in (200, 204):
        raise RuntimeError(f"Discord webhook error {resp.status_code}: {resp.text}")


def main():
    prev_state = load_state()
    prev_saved_at = prev_state.get("saved_at_utc")

    urls = [DIVISIONROAD_URL, BROOKLYN_URL, NICKS_URL, IRONHEART_DE_URL, IRONHEART_UK_URL]

    session = build_session()

    # 1) FX (single call)
    fx_map = get_fx_map(session)

    # 2) Fetch all pages concurrently
    html_map = fetch_all_html(session, urls, max_workers=6)

    # 3) Parse
    dr_raw = extract_top_entries(DIVISIONROAD_URL, html_map[DIVISIONROAD_URL], 5)
    bc_raw = extract_top_entries(BROOKLYN_URL, html_map[BROOKLYN_URL], 5)
    n_raw = extract_top_entries(NICKS_URL, html_map[NICKS_URL], 5)
    ih_de_raw = extract_top_entries(IRONHEART_DE_URL, html_map[IRONHEART_DE_URL], 5)
    ih_uk_raw = extract_top_entries(IRONHEART_UK_URL, html_map[IRONHEART_UK_URL], 5)

    # 4) NEW flags vs baseline
    dr_items = compute_new_flags("divisionroad", dr_raw, prev_state)
    bc_items = compute_new_flags("brooklyn", bc_raw, prev_state)
    n_items = compute_new_flags("nicks", n_raw, prev_state)
    ih_de_items = compute_new_flags("ironheart_de", ih_de_raw, prev_state)
    ih_uk_items = compute_new_flags("ironheart_uk", ih_uk_raw, prev_state)

    # Descriptions for Discord
    if dr_items:
        latest_norm = norm(dr_items[0]["title"])
        target_norm = norm(DIVISIONROAD_TARGET_TITLE)
        dr_desc = f"Target still #1? {'✅ YES' if latest_norm == target_norm else '🚨 NO'}"
    else:
        dr_desc = "No entries found."

    if fx_map.get("CAD"):
        bc_desc = f"CAD→USD: 1 CAD = **{fx_map['CAD']['rate']:.4f} USD** (as of {fx_map['CAD']['date']})"
    else:
        bc_desc = "CAD→USD: **unavailable** (FX fetch failed)"

    print("Baseline saved_at_utc:", prev_saved_at)
    print("Division Road top 5:", len(dr_items))
    print("Brooklyn Clothing top 5:", len(bc_items))
    print("Nick's top 5:", len(n_items))
    print("Iron Heart Germany top 5:", len(ih_de_items))
    print("Iron Heart UK top 5:", len(ih_uk_items))
    print("FX available:", ",".join(sorted(fx_map.keys())) if fx_map else "NO")
    print("BS4 parser:", BS4_PARSER)

    # Manual mode: print only
    mode = (os.environ.get("OUTPUT_MODE") or "").lower()
    if mode == "github":
        print_top5("Division Road", dr_items, DIVISIONROAD_URL, fx_map)
        print_top5("Brooklyn Clothing", bc_items, BROOKLYN_URL, fx_map)
        print_top5("Nick's (10.5D RTS)", n_items, NICKS_URL, fx_map)
        print_top5("Iron Heart Germany", ih_de_items, IRONHEART_DE_URL, fx_map)
        print_top5("Iron Heart UK (Wesco)", ih_uk_items, IRONHEART_UK_URL, fx_map)
    else:
        webhook = (os.environ.get("DISCORD_WEBHOOK_URL") or "").strip()
        if webhook:
            sites = [
                {"key": "divisionroad", "name": "Division Road", "url": DIVISIONROAD_URL, "items": dr_items, "desc": dr_desc},
                {"key": "brooklyn", "name": "Brooklyn Clothing", "url": BROOKLYN_URL, "items": bc_items, "desc": bc_desc},
                {"key": "nicks", "name": "Nick’s Ready-to-Ship (10.5D)", "url": NICKS_URL, "items": n_items, "desc": "Filtered to **10.5 D**."},
                {"key": "ironheart_de", "name": "Iron Heart Germany", "url": IRONHEART_DE_URL, "items": ih_de_items, "desc": "Prices in EUR with USD conversion when available."},
                {"key": "ironheart_uk", "name": "Iron Heart UK (Wesco)", "url": IRONHEART_UK_URL, "items": ih_uk_items, "desc": "Prices in GBP with USD conversion when available."},
            ]
            payload = build_discord_payload(sites, fx_map, prev_saved_at)
            send_discord_embed(session, webhook, payload)
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
