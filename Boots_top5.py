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

DIVISIONROAD_TARGET_TITLE = "Stow Boot - 4497 - Leather - Tempesti Ambra Elbamatt Liscio"

STATE_FILE = "state_last_top5.json"

# “Include” words for Brooklyn (keeps it footwear-ish)
INCLUDE_WORDS = [
    "boot", "moc", "chukka", "shoe", "oxford", "derby", "blucher", "loafer",
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
    Nick's: require footwear keywords because their RTS collection includes accessories.
    """
    t = title.lower()

    if any(bad in t for bad in EXCLUDE_WORDS):
        return False

    if "divisionroadinc.com" in collection_url:
        return True

    if "nicksboots.com" in collection_url:
        nicks_keywords = ["boot", "boots", "shoe", "shoes", "chukka", "moc", "chelsea", "engineer"]
        return any(k in t for k in nicks_keywords)

    return any(good in t for good in INCLUDE_WORDS)


def fetch_html(url: str) -> str:
    r = requests.get(url, headers={"User-Agent": "top5-footwear-bot/2.2"}, timeout=30)
    r.raise_for_status()
    return r.text


def make_absolute_url(collection_url: str, href: str) -> str:
    if href.startswith("http"):
        return href
    base = re.match(r"^(https?://[^/]+)", collection_url)
    return (base.group(1) if base else "") + href


def parse_price_to_float(price_str: str) -> float | None:
    if not price_str:
        return None
    s = price_str.replace("$", "").replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def get_cad_to_usd_rate_latest() -> tuple[float, str]:
    """
    Fetch latest CAD->USD FX from Frankfurter (no key).
    """
    url = "https://api.frankfurter.dev/v1/latest?from=CAD&to=USD"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    data = r.json()
    usd_per_cad = float(data["rates"]["USD"])
    rate_date = str(data.get("date", "unknown"))
    return usd_per_cad, rate_date


def extract_top_entries(collection_url: str, html: str, n: int = 5):
    soup = BeautifulSoup(html, "html.parser")
    seen = set()
    out = []

    for a in soup.select("a[href*='/products/']"):
        href = (a.get("href") or "").strip()
        if not href or "/products/" not in href:
            continue

        prod_url = make_absolute_url(collection_url, href)
        if prod_url in seen:
            continue

        # 1) Try link text first
        title = norm(a.get_text(" ", strip=True))

        # 2) If empty (Brooklyn image links), search within the product card
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

        # Best-effort price capture near the link/card
        price = None
        card = a.find_parent()
        for _ in range(10):
            if not card:
                break
            text = card.get_text(" ", strip=True)
            pm = re.search(r"\$\s?\d{1,3}(?:,\d{3})*(?:\.\d{2})?", text)
            if pm:
                price = pm.group(0).replace(" ", "")
                break
            card = card.find_parent()

        seen.add(prod_url)
        out.append((title, prod_url, price))
        if len(out) >= n:
            break

    return out


def format_price(collection_url: str, price_str: str | None, fx: tuple[float, str] | None) -> str | None:
    if not price_str:
        return None

    # Brooklyn uses CAD (they display "$" but it's CAD)
    if "brooklynclothing.com" in collection_url:
        cad = parse_price_to_float(price_str)
        if cad is None:
            return f"{price_str} CAD"
        if fx is None:
            return f"{price_str} CAD (USD unavailable)"
        usd_per_cad, rate_date = fx
        usd = cad * usd_per_cad
        return f"{price_str} CAD (~${usd:,.2f} USD @ {rate_date})"

    # Division Road + Nick's are USD
    return price_str


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
    """
    Mark items as NEW if their URL wasn't in last state's urls for this site.
    Returns list of dicts: {title, url, price_raw, is_new}
    """
    prev_urls = set((prev_state.get("sites", {}).get(site_key, {}) or {}).get("urls", []) or [])
    out = []
    for title, url, price_raw in items:
        out.append(
            {
                "title": title,
                "url": url,
                "price_raw": price_raw,
                "is_new": (url not in prev_urls) if prev_urls else False,  # no state => don't spam NEW
            }
        )
    return out


def print_top5(name: str, items_dicts, collection_url: str, fx):
    print(f"\n=== {name} ===")
    for i, it in enumerate(items_dicts, start=1):
        price_fmt = format_price(collection_url, it["price_raw"], fx)
        new_tag = " 🆕 NEW" if it["is_new"] else ""
        if price_fmt:
            print(f"{i}. {it['title']}{new_tag} — {price_fmt}")
        else:
            print(f"{i}. {it['title']}{new_tag}")
        print(f"   {it['url']}")


def _truncate(s: str, max_len: int) -> str:
    s = s or ""
    return s if len(s) <= max_len else s[: max_len - 1] + "…"


def build_discord_payload(dr_items, bc_items, n_items, fx, prev_saved_at_utc: str | None):
    embeds = [
        {
            "title": "🧾 Boots Watch — Top 5 Newest",
            "description": (
                "Sorted by **Date: new → old**.\n"
                f"Division Road: <{DIVISIONROAD_URL}>\n"
                f"Brooklyn Clothing: <{BROOKLYN_URL}>\n"
                f"Nick’s (10.5D RTS): <{NICKS_URL}>\n"
                + (f"\nBaseline from: **{prev_saved_at_utc} UTC**" if prev_saved_at_utc else "\nBaseline: **none yet**")
            ),
        }
    ]

    def site_embed(title, desc, items, collection_url):
        emb = {"title": title, "description": desc, "fields": []}
        for i, it in enumerate(items, start=1):
            price_fmt = format_price(collection_url, it["price_raw"], fx)
            new_tag = "🆕 NEW — " if it["is_new"] else ""
            field_name = _truncate(f"{i}. {it['title']}", 256)
            value_lines = []
            if price_fmt:
                value_lines.append(f"{new_tag}{price_fmt}")
            else:
                if it["is_new"]:
                    value_lines.append("🆕 NEW")
            value_lines.append(f"<{it['url']}>")
            emb["fields"].append({"name": field_name, "value": _truncate("\n".join(value_lines), 1024), "inline": False})
        if not items:
            emb["fields"].append({"name": "No entries found", "value": "—", "inline": False})
        return emb

    # Division Road target indicator
    if dr_items:
        latest_norm = norm(dr_items[0]["title"])
        target_norm = norm(DIVISIONROAD_TARGET_TITLE)
        dr_desc = f"Target still #1? {'✅ YES' if latest_norm == target_norm else '🚨 NO'}"
    else:
        dr_desc = "No entries found."

    if fx:
        usd_per_cad, rate_date = fx
        bc_desc = f"CAD→USD: 1 CAD = **{usd_per_cad:.4f} USD** (as of {rate_date})"
    else:
        bc_desc = "CAD→USD: **unavailable** (FX fetch failed)"

    embeds.append(site_embed("🏷️ Division Road — Top 5", dr_desc, dr_items, DIVISIONROAD_URL))
    embeds.append(site_embed("🏷️ Brooklyn Clothing — Top 5", bc_desc, bc_items, BROOKLYN_URL))
    embeds.append(site_embed("🏷️ Nick’s Ready-to-Ship (10.5D) — Top 5", "Filtered to **10.5 D**.", n_items, NICKS_URL))

    return {"content": None, "embeds": embeds, "allowed_mentions": {"parse": []}}


def send_discord_embed(webhook_url: str, payload: dict):
    resp = requests.post(webhook_url, json=payload, timeout=30)
    if resp.status_code not in (200, 204):
        raise RuntimeError(f"Discord webhook error {resp.status_code}: {resp.text}")


def main():
    # Load baseline state (previous run)
    prev_state = load_state()
    prev_saved_at = prev_state.get("saved_at_utc")

    # FX once per run (Brooklyn CAD→USD)
    fx = None
    try:
        fx = get_cad_to_usd_rate_latest()
    except Exception as e:
        print("WARNING: Could not fetch latest CAD→USD rate. USD conversion will be unavailable.")
        print(f"Reason: {e}\n")

    # Scrape
    dr_top5_raw = extract_top_entries(DIVISIONROAD_URL, fetch_html(DIVISIONROAD_URL), 5)
    bc_top5_raw = extract_top_entries(BROOKLYN_URL, fetch_html(BROOKLYN_URL), 5)
    n_top5_raw = extract_top_entries(NICKS_URL, fetch_html(NICKS_URL), 5)

    # Mark NEW vs baseline
    dr_items = compute_new_flags("divisionroad", dr_top5_raw, prev_state)
    bc_items = compute_new_flags("brooklyn", bc_top5_raw, prev_state)
    n_items = compute_new_flags("nicks", n_top5_raw, prev_state)

    print("Baseline saved_at_utc:", prev_saved_at)
    print("Division Road top 5:", len(dr_items))
    print("Brooklyn Clothing top 5:", len(bc_items))
    print("Nick's top 5:", len(n_items))
    print("FX available:", "YES" if fx else "NO")

    # Manual / GitHub preview mode: PRINT lists (no Discord)
    mode = (os.environ.get("OUTPUT_MODE") or "").lower()
    if mode == "github":
        print_top5("Division Road", dr_items, DIVISIONROAD_URL, fx)
        print_top5("Brooklyn Clothing", bc_items, BROOKLYN_URL, fx)
        print_top5("Nick's (10.5D RTS)", n_items, NICKS_URL, fx)
    else:
        # Scheduled mode: send to Discord if webhook set
        webhook = (os.environ.get("DISCORD_WEBHOOK_URL") or "").strip()
        if webhook:
            payload = build_discord_payload(dr_items, bc_items, n_items, fx, prev_saved_at)
            send_discord_embed(webhook, payload)
            print("Discord message sent successfully.")
        else:
            print("DISCORD_WEBHOOK_URL not set; skipping Discord send.")

    # Save new baseline if requested
    save_flag = (os.environ.get("SAVE_STATE") or "").strip() in ("1", "true", "TRUE", "yes", "YES")
    if save_flag:
        new_state = {
            "saved_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "sites": {
                "divisionroad": {"urls": [it["url"] for it in dr_items]},
                "brooklyn": {"urls": [it["url"] for it in bc_items]},
                "nicks": {"urls": [it["url"] for it in n_items]},
            },
        }
        save_state(new_state)
        print(f"State saved to {STATE_FILE}")
    else:
        print(f"State NOT saved (set SAVE_STATE=1 to update {STATE_FILE}).")


if __name__ == "__main__":
    main()
