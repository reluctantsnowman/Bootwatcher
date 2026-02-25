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
    r = requests.get(url, headers={"User-Agent": "top5-footwear-bot/1.4"}, timeout=30)
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

    if "brooklynclothing.com" in collection_url:
        cad = parse_price_to_float(price_str)
        if cad is None:
            return f"{price_str} CAD (~USD unavailable)"
        if fx is None:
            return f"{price_str} CAD (~USD unavailable)"
        usd_per_cad, rate_date = fx
        usd = cad * usd_per_cad
        return f"{price_str} CAD (~${usd:,.2f} USD @ {rate_date})"

    return price_str


def build_report_text(dr_top5, bc_top5, fx) -> str:
    lines = []
    lines.append("Division Road + Brooklyn Clothing — Top 5 newest FOOTWEAR (new → old)")
    lines.append("")

    # Division Road section
    lines.append("=== Division Road ===")
    lines.append(DIVISIONROAD_URL)
    if dr_top5:
        latest_norm = norm(dr_top5[0][0])
        target_norm = norm(DIVISIONROAD_TARGET_TITLE)
        lines.append(f"Target still #1? {'YES' if latest_norm == target_norm else 'NO'}")
        lines.append("")
        for i, (title, url, price_raw) in enumerate(dr_top5, start=1):
            price_fmt = format_price(DIVISIONROAD_URL, price_raw, fx)
            lines.append(f"{i}. {title}" + (f" — {price_fmt}" if price_fmt else ""))
            lines.append(f"   {url}")
    else:
        lines.append("No footwear entries found.")
    lines.append("")

    # Brooklyn section
    lines.append("=== Brooklyn Clothing ===")
    lines.append(BROOKLYN_URL)
    lines.append("")
    if bc_top5:
        for i, (title, url, price_raw) in enumerate(bc_top5, start=1):
            price_fmt = format_price(BROOKLYN_URL, price_raw, fx)
            lines.append(f"{i}. {title}" + (f" — {price_fmt}" if price_fmt else ""))
            lines.append(f"   {url}")
    else:
        lines.append("No footwear entries found.")

    return "\n".join(lines)


def send_discord(webhook_url: str, message: str):
    """
    Discord message hard limit is ~2000 chars. Chunk safely.
    """
    max_len = 1900  # leave some slack
    parts = []
    while message:
        if len(message) <= max_len:
            parts.append(message)
            break
        cut = message.rfind("\n", 0, max_len)
        if cut == -1:
            cut = max_len
        parts.append(message[:cut])
        message = message[cut:].lstrip("\n")

    for idx, part in enumerate(parts, start=1):
        prefix = "" if len(parts) == 1 else f"Part {idx}/{len(parts)}\n"
        payload = {"content": prefix + part}
        resp = requests.post(webhook_url, json=payload, timeout=30)
        if resp.status_code not in (200, 204):
            raise RuntimeError(f"Discord webhook error {resp.status_code}: {resp.text}")


def main():
    # FX once per run
    fx = None
    try:
        fx = get_cad_to_usd_rate_latest()
    except Exception as e:
        print("WARNING: Could not fetch latest CAD→USD rate. USD conversion will be unavailable.")
        print(f"Reason: {e}")
        print()

    dr_top5 = extract_top_entries(DIVISIONROAD_URL, fetch_html(DIVISIONROAD_URL), 5)
    bc_top5 = extract_top_entries(BROOKLYN_URL, fetch_html(BROOKLYN_URL), 5)

    report = build_report_text(dr_top5, bc_top5, fx)

    # Always print for GitHub logs
    print(report)

    # If webhook secret exists, send to Discord
    webhook = (requests.utils.os.environ.get("DISCORD_WEBHOOK_URL") or "").strip()
    if webhook:
        send_discord(webhook, report)


if __name__ == "__main__":
    main()
