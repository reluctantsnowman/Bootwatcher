import re
import requests
from bs4 import BeautifulSoup

# --- URLs (sorted: Date, new -> old) ---
DIVISIONROAD_URL = "https://divisionroadinc.com/collections/footwear/boots?sort_by=created-descending"
BROOKLYN_URL = "https://brooklynclothing.com/collections/boots?grid_list=grid-view&sort_by=created-descending"

# Optional: check whether Division Road #1 is still this exact title
DIVISIONROAD_TARGET_TITLE = "Stow Boot - 4497 - Leather - Tempesti Ambra Elbamatt Liscio"

# --- Keyword filters (boot-like footwear allowed; non-footwear excluded) ---
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
    """Normalize punctuation + whitespace for stable comparisons."""
    s = s.replace("\u2019", "'")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def is_footwear_title(title: str) -> bool:
    t = title.lower()
    if any(bad in t for bad in EXCLUDE_WORDS):
        return False
    return any(good in t for good in INCLUDE_WORDS)


def fetch_html(url: str) -> str:
    r = requests.get(url, headers={"User-Agent": "top5-footwear-bot/1.3"}, timeout=30)
    r.raise_for_status()
    return r.text


def make_absolute_url(collection_url: str, href: str) -> str:
    if href.startswith("http"):
        return href
    base = re.match(r"^(https?://[^/]+)", collection_url)
    return (base.group(1) if base else "") + href


def parse_price_to_float(price_str: str) -> float | None:
    """
    Convert strings like '$1,475.00' into 1475.00
    Returns None if parsing fails.
    """
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
    Returns: (usd_per_cad, date_string)
    """
    # Frankfurter supports latest rates with query params. :contentReference[oaicite:1]{index=1}
    url = "https://api.frankfurter.dev/v1/latest?from=CAD&to=USD"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    data = r.json()

    # Example shape:
    # {"amount":1.0,"base":"CAD","date":"2026-02-25","rates":{"USD":0.73}}
    usd_per_cad = float(data["rates"]["USD"])
    rate_date = str(data.get("date", "unknown"))
    return usd_per_cad, rate_date


def extract_top_entries(collection_url: str, html: str, n: int = 5):
    """
    Extract top N footwear entries from a Shopify-like collection page:
    - finds /products/ links
    - pulls title from link text OR nearby card heading/title (Brooklyn needs this)
    - best-effort extracts price from nearby text
    - filters to footwear-ish titles and excludes accessories
    """
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

                # Try headings
                h = card.find(["h1", "h2", "h3"])
                if h:
                    t = norm(h.get_text(" ", strip=True))
                    if t:
                        title = t
                        break

                # Try common "title-ish" nodes
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
    """
    Division Road: treat '$' as USD, display as-is.
    Brooklyn Clothing: treat '$' as CAD, convert to USD using fetched latest FX.
    """
    if not price_str:
        return None

    # Brooklyn: CAD -> USD
    if "brooklynclothing.com" in collection_url:
        cad = parse_price_to_float(price_str)
        if cad is None:
            return f"{price_str} CAD (~USD unavailable)"

        if fx is None:
            return f"{price_str} CAD (~USD unavailable)"

        usd_per_cad, rate_date = fx
        usd = cad * usd_per_cad
        return f"{price_str} CAD (~${usd:,.2f} USD @ {rate_date})"

    # Default (Division Road): show as-is (USD)
    return price_str


def print_section(name: str, url: str, top5, fx: tuple[float, str] | None, target_title: str | None = None):
    print("=" * 80)
    print(f"{name} — Top 5 newest FOOTWEAR entries (Date: new → old)")
    print(url)
    print()

    if not top5:
        print("No footwear entries found (page structure may have changed).")
        return

    if target_title:
        target_norm = norm(target_title)
        latest_norm = norm(top5[0][0])
        print("Target still #1?", "YES" if latest_norm == target_norm else "NO")
        print()

    for i, (title, prod_url, price_raw) in enumerate(top5, start=1):
        price_fmt = format_price(url, price_raw, fx)
        if price_fmt:
            print(f"{i}. {title} — {price_fmt}")
        else:
            print(f"{i}. {title}")
        print(f"   {prod_url}")


def main():
    # Fetch FX once per run (only needed for Brooklyn)
    fx = None
    try:
        fx = get_cad_to_usd_rate_latest()
    except Exception as e:
        print("WARNING: Could not fetch latest CAD→USD rate. USD conversion will be unavailable.")
        print(f"Reason: {e}")
        print()

    # Division Road
    dr_html = fetch_html(DIVISIONROAD_URL)
    dr_top5 = extract_top_entries(DIVISIONROAD_URL, dr_html, 5)

    # Brooklyn Clothing
    bc_html = fetch_html(BROOKLYN_URL)
    bc_top5 = extract_top_entries(BROOKLYN_URL, bc_html, 5)

    print_section("Division Road", DIVISIONROAD_URL, dr_top5, fx=fx, target_title=DIVISIONROAD_TARGET_TITLE)
    print_section("Brooklyn Clothing", BROOKLYN_URL, bc_top5, fx=fx, target_title=None)


if __name__ == "__main__":
    main()
