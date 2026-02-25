import re
import requests
from bs4 import BeautifulSoup

# --- URLs (sorted: new -> old) ---
DIVISIONROAD_URL = "https://divisionroadinc.com/collections/footwear/boots?sort_by=created-descending"
BROOKLYN_URL = "https://brooklynclothing.com/collections/boots?grid_list=grid-view&sort_by=created-descending"

# Optional: target check for Division Road (#1 item)
DIVISIONROAD_TARGET_TITLE = "Stow Boot - 4497 - Leather - Tempesti Ambra Elbamatt Liscio"

# ---- Keyword filters (tune these if needed) ----
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
    r = requests.get(url, headers={"User-Agent": "top5-footwear-bot/1.0"}, timeout=30)
    r.raise_for_status()
    return r.text


def extract_top_entries(url: str, html: str, n: int = 5):
    """
    Generic extractor for Shopify-like collection pages:
    - finds /products/ links
    - gets title from link text
    - best-effort extracts $ price from nearby card text
    - filters to footwear-ish items
    """
    soup = BeautifulSoup(html, "html.parser")
    seen = set()
    out = []

    for a in soup.select("a[href*='/products/']"):
        href = (a.get("href") or "").strip()
        title = norm(a.get_text(" ", strip=True))

        if not href or "/products/" not in href or not title or len(title) < 4:
            continue

        if not is_footwear_title(title):
            continue

        # Make absolute URL
        if href.startswith("http"):
            prod_url = href
        else:
            # base domain from the collection URL
            m = re.match(r"^(https?://[^/]+)", url)
            base = m.group(1) if m else ""
            prod_url = base + href

        if prod_url in seen:
            continue
        seen.add(prod_url)

        # Best-effort price capture (theme-dependent)
        price = None
        card = a.find_parent()
        for _ in range(8):
            if not card:
                break
            text = card.get_text(" ", strip=True)
            m = re.search(r"\$\s?\d{1,3}(?:,\d{3})*(?:\.\d{2})?", text)
            if m:
                price = m.group(0).replace(" ", "")
                break
            card = card.find_parent()

        out.append((title, prod_url, price))
        if len(out) >= n:
            break

    return out


def print_section(name: str, url: str, top5, target_title: str | None = None):
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

    for i, (title, prod_url, price) in enumerate(top5, start=1):
        if price:
            print(f"{i}. {title} — {price}")
        else:
            print(f"{i}. {title}")
        print(f"   {prod_url}")


def main():
    # Division Road
    dr_html = fetch_html(DIVISIONROAD_URL)
    dr_top5 = extract_top_entries(DIVISIONROAD_URL, dr_html, 5)

    # Brooklyn Clothing
    bc_html = fetch_html(BROOKLYN_URL)
    bc_top5 = extract_top_entries(BROOKLYN_URL, bc_html, 5)

    print_section("Division Road", DIVISIONROAD_URL, dr_top5, target_title=DIVISIONROAD_TARGET_TITLE)
    print_section("Brooklyn Clothing", BROOKLYN_URL, bc_top5, target_title=None)


if __name__ == "__main__":
    main()
