import os
import re
import requests
from bs4 import BeautifulSoup

# --- URLs (sorted: Date, new -> old) ---
DIVISIONROAD_URL = "https://divisionroadinc.com/collections/footwear/boots?sort_by=created-descending"
BROOKLYN_URL = "https://brooklynclothing.com/collections/boots?grid_list=grid-view&sort_by=created-descending"

# Nick's: Ready-to-ship + Free shipping, filtered to 10.5 D
NICKS_URL = (
    "https://nicksboots.com/collections/ready-to-ship-free-shipping"
    "?sort_by=created-descending"
    "&filter.p.m.custom.left_boot_length=10.5"
    "&filter.p.m.custom.left_boot_width=D"
)

DIVISIONROAD_TARGET_TITLE = "Stow Boot - 4497 - Leather - Tempesti Ambra Elbamatt Liscio"

# “Include” words for Brooklyn (keeps it footwear-ish)
INCLUDE_WORDS = [
    "boot", "moc", "chukka", "shoe", "oxford", "derby", "blucher", "loafer",
    "slip-on", "slipper", "monkey", "service", "chelsea", "roper", "engineer",
    "brogue", "wingtip"
]

# “Exclude” words for all sites (filters obvious non-footwear)
EXCLUDE_WORDS = [
    "garment bag", "bag", "tote", "shoe tree", "tree", "gift card", "gift",
    "belt", "wallet", "keychain", "lace", "laces", "brush", "cream", "wax",
    "conditioner", "oil", "spray", "socks", "provisions"
]


def norm(s: str) -> str:
    s = s.replace("\u2019", "'")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def is_footwear_title(collection_url: str, title: str) -> bool:
    """
    Division Road + Nick's: accept anything in the curated boot collection unless excluded.
    Brooklyn: require INCLUDE_WORDS (plus not excluded) to avoid accessories/noise.
    """
    t = title.lower()

    if any(bad in t for bad in EXCLUDE_WORDS):
        return False

    if "divisionroadinc.com" in collection_url:
        return True

    if "nicksboots.com" in collection_url:
        return True

    # Brooklyn (and any other site): require a footwear keyword
    return any(good in t for good in INCLUDE_WORDS)


def fetch_html(url: str) -> str:
    r = requests.get(url, headers={"User-Agent": "top5-footwear-bot/1.8"}, timeout=30)
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

        # 2) If empty (common on Brooklyn: image links), search within the product card
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


def _truncate(s: str, max_len: int) -> str:
    s = s or ""
    return s if len(s) <= max_len else s[: max_len - 1] + "…"


def build_discord_payload(dr_top5, bc_top5, nicks_top5, fx):
    # Header embed
    embeds = [
        {
            "title": "🧾 Boots Watch — Top 5 Newest",
            "description": (
                "Sorted by **Date: new → old**.\n"
                f"Division Road: <{DIVISIONROAD_URL}>\n"
                f"Brooklyn Clothing: <{BROOKLYN_URL}>\n"
                f"Nick’s (10.5D RTS): <{NICKS_URL}>"
            ),
        }
    ]

    # --- Division Road embed ---
    if dr_top5:
        latest_norm = norm(dr_top5[0][0])
        target_norm = norm(DIVISIONROAD_TARGET_TITLE)
        dr_desc = f"Target still #1? {'✅ YES' if latest_norm == target_norm else '🚨 NO'}"
    else:
        dr_desc = "No entries found."

    dr_embed = {"title": "🏷️ Division Road — Top 5", "description": dr_desc, "fields": []}
    for i, (title, url, price_raw) in enumerate(dr_top5, start=1):
        price_fmt = format_price(DIVISIONROAD_URL, price_raw, fx)
        name = _truncate(f"{i}. {title}", 256)
        value = f"{price_fmt}\n<{url}>" if price_fmt else f"<{url}>"
        dr_embed["fields"].append({"name": name, "value": _truncate(value, 1024), "inline": False})
    embeds.append(dr_embed)

    # --- Brooklyn embed ---
    if fx:
        usd_per_cad, rate_date = fx
        bc_desc = f"CAD→USD: 1 CAD = **{usd_per_cad:.4f} USD** (as of {rate_date})"
    else:
        bc_desc = "CAD→USD: **unavailable** (FX fetch failed)"

    bc_embed = {"title": "🏷️ Brooklyn Clothing — Top 5", "description": bc_desc, "fields": []}
    for i, (title, url, price_raw) in enumerate(bc_top5, start=1):
        price_fmt = format_price(BROOKLYN_URL, price_raw, fx)
        name = _truncate(f"{i}. {title}", 256)
        value = f"{price_fmt}\n<{url}>" if price_fmt else f"<{url}>"
        bc_embed["fields"].append({"name": name, "value": _truncate(value, 1024), "inline": False})
    if not bc_top5:
        bc_embed["fields"].append({"name": "No entries found", "value": "—", "inline": False})
    embeds.append(bc_embed)

    # --- Nick's embed ---
    nicks_embed = {
        "title": "🏷️ Nick’s Ready-to-Ship (10.5D) — Top 5",
        "description": "Filtered to **10.5 D** via collection filters.",
        "fields": [],
    }
    for i, (title, url, price_raw) in enumerate(nicks_top5, start=1):
        price_fmt = format_price(NICKS_URL, price_raw, fx)
        name = _truncate(f"{i}. {title}", 256)
        value = f"{price_fmt}\n<{url}>" if price_fmt else f"<{url}>"
        nicks_embed["fields"].append({"name": name, "value": _truncate(value, 1024), "inline": False})
    if not nicks_top5:
        nicks_embed["fields"].append({"name": "No entries found", "value": "—", "inline": False})
    embeds.append(nicks_embed)

    return {"content": None, "embeds": embeds, "allowed_mentions": {"parse": []}}


def send_discord_embed(webhook_url: str, payload: dict):
    resp = requests.post(webhook_url, json=payload, timeout=30)
    if resp.status_code not in (200, 204):
        raise RuntimeError(f"Discord webhook error {resp.status_code}: {resp.text}")


def main():
    # Fetch FX once per run (only needed for Brooklyn CAD→USD)
    fx = None
    try:
        fx = get_cad_to_usd_rate_latest()
    except Exception as e:
        print("WARNING: Could not fetch latest CAD→USD rate. USD conversion will be unavailable.")
        print(f"Reason: {e}\n")

    dr_top5 = extract_top_entries(DIVISIONROAD_URL, fetch_html(DIVISIONROAD_URL), 5)
    bc_top5 = extract_top_entries(BROOKLYN_URL, fetch_html(BROOKLYN_URL), 5)
    nicks_top5 = extract_top_entries(NICKS_URL, fetch_html(NICKS_URL), 5)

    print("Division Road top 5:", len(dr_top5))
    print("Brooklyn Clothing top 5:", len(bc_top5))
    print("Nick's top 5:", len(nicks_top5))
    print("FX available:", "YES" if fx else "NO")

    webhook = (os.environ.get("DISCORD_WEBHOOK_URL") or "").strip()
    if not webhook:
        print("DISCORD_WEBHOOK_URL not set; skipping Discord send.")
        return

    payload = build_discord_payload(dr_top5, bc_top5, nicks_top5, fx)
    send_discord_embed(webhook, payload)
    print("Discord message sent successfully.")


if __name__ == "__main__":
    main()
