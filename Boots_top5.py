import os
import re
import requests
from bs4 import BeautifulSoup

# --- URLs (sorted: Date, new -> old) ---
DIVISIONROAD_URL = "https://divisionroadinc.com/collections/footwear/boots?sort_by=created-descending"
BROOKLYN_URL = "https://brooklynclothing.com/collections/boots?grid_list=grid-view&sort_by=created-descending"

DIVISIONROAD_TARGET_TITLE = "Stow Boot - 4497 - Leather - Tempesti Ambra Elbamatt Liscio"

INCLUDE_WORDS = [
    "boot", "moc", "chukka", "shoe", "oxford", "derby", "blucher", "loafer",
    "slip-on", "slipper", "monkey", "service", "chelsea", "roper", "engineer",
    "brogue", "wingtip"
]

EXCLUDE_WORDS = [
    "garment bag", "bag", "tote", "shoe tree", "tree", "gift card", "gift",
    "belt", "wallet", "keychain", "lace", "laces", "brush", "cream", "wax",
    "conditioner", "oil", "spray", "socks", "provisions"
]


def norm(s: str) -> str:
    s = s.replace("\u2019", "'")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def is_footwear_title(title: str) -> bool:
    t = title.lower()
    if any(bad in t for bad in EXCLUDE_WORDS):
        return False
    return any(good in t for good in INCLUDE_WORDS)


def fetch_html(url: str) -> str:
    r = requests.get(url, headers={"User-Agent": "top5-footwear-bot/1.5"}, timeout=30)
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

        if not is_footwear_title(title):
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
            return f"{price_str} CAD (~USD unavailable)"
        if fx is None:
            return f"{price_str} CAD (~USD unavailable)"
        usd_per_cad, rate_date = fx
        usd = cad * usd_per_cad
        return f"{price_str} CAD (~${usd:,.2f} USD @ {rate_date})"

    # Division Road is USD
    return price_str


def build_embed_payload(dr_top5, bc_top5, fx):
    """
    Build a Discord embed payload (much nicer formatting than plain text).
    """
    # Division Road block
    if dr_top5:
        latest_norm = norm(dr_top5[0][0])
        target_norm = norm(DIVISIONROAD_TARGET_TITLE)
        target_line = f"**Target still #1?** {'✅ YES' if latest_norm == target_norm else '🚨 NO'}\n"

        dr_lines = [target_line]
        for i, (title, url, price_raw) in enumerate(dr_top5, start=1):
            price_fmt = format_price(DIVISIONROAD_URL, price_raw, fx)
            price_part = f" — {price_fmt}" if price_fmt else ""
            dr_lines.append(f"**{i}. {title}**{price_part}\n<{url}>")

        dr_value = "\n\n".join(dr_lines)
    else:
        dr_value = "_No footwear entries found._"

    # Brooklyn block
    if fx:
        usd_per_cad, rate_date = fx
        fx_line = f"**CAD→USD:** 1 CAD = **{usd_per_cad:.4f} USD** (as of {rate_date})\n\n"
    else:
        fx_line = "**CAD→USD:** _unavailable (FX fetch failed)_\n\n"

    if bc_top5:
        bc_lines = [fx_line.strip()]
        for i, (title, url, price_raw) in enumerate(bc_top5, start=1):
            price_fmt = format_price(BROOKLYN_URL, price_raw, fx)
            price_part = f" — {price_fmt}" if price_fmt else ""
            bc_lines.append(f"**{i}. {title}**{price_part}\n<{url}>")
        bc_value = "\n\n".join(bc_lines)
    else:
        bc_value = fx_line + "_No footwear entries found._"

    payload = {
        "content": None,
        "embeds": [
            {
                "title": "🧾 Boots Watch — Top 5 Newest (new → old)",
                "description": "Sources are sorted by **Date: new → old**.",
                "fields": [
                    {"name": "🏷️ Division Road", "value": f"<{DIVISIONROAD_URL}>\n\n{dr_value}", "inline": False},
                    {"name": "🏷️ Brooklyn Clothing", "value": f"<{BROOKLYN_URL}>\n\n{bc_value}", "inline": False},
                ],
            }
        ],
        "allowed_mentions": {"parse": []},
    }
    return payload


def send_discord_embed(webhook_url: str, payload: dict):
    """
    Send one Discord webhook message with an embed.
    """
    resp = requests.post(webhook_url, json=payload, timeout=30)
    if resp.status_code not in (200, 204):
        raise RuntimeError(f"Discord webhook error {resp.status_code}: {resp.text}")


def main():
    # Fetch FX once per run
    fx = None
    try:
        fx = get_cad_to_usd_rate_latest()
    except Exception as e:
        print("WARNING: Could not fetch latest CAD→USD rate. USD conversion will be unavailable.")
        print(f"Reason: {e}")
        print()

    # Scrape both pages
    dr_top5 = extract_top_entries(DIVISIONROAD_URL, fetch_html(DIVISIONROAD_URL), 5)
    bc_top5 = extract_top_entries(BROOKLYN_URL, fetch_html(BROOKLYN_URL), 5)

    # Print a plain log summary in GitHub (optional but helpful)
    print("Division Road top 5:", len(dr_top5))
    print("Brooklyn Clothing top 5:", len(bc_top5))
    print("FX available:", "YES" if fx else "NO")

    # Build and send embed to Discord if webhook is present
    webhook = (os.environ.get("DISCORD_WEBHOOK_URL") or "").strip()
    if webhook:
        payload = build_embed_payload(dr_top5, bc_top5, fx)
        send_discord_embed(webhook, payload)
    else:
        print("DISCORD_WEBHOOK_URL not set; skipping Discord send.")


if __name__ == "__main__":
    main()
