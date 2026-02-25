import re
import requests
from bs4 import BeautifulSoup

SORTED_URL = "https://divisionroadinc.com/collections/footwear/boots?sort_by=created-descending"
TARGET_TITLE = "Stow Boot - 4497 - Leather - Tempesti Ambra Elbamatt Liscio"


def norm(s: str) -> str:
    s = s.replace("\u2019", "'")
    s = re.sub(r"\s+", " ", s).strip()
    return s


# Words that mean "this is footwear" (boot-like included)
INCLUDE_WORDS = [
    "boot", "moc", "chukka", "shoe", "oxford", "derby", "blucher", "loafer",
    "slip-on", "slipper", "monkey", "service", "chelsea", "roper", "engineer",
    "brogue", "wingtip"
]

# Words that mean "this is NOT footwear"
EXCLUDE_WORDS = [
    "garment bag", "bag", "tote", "shoe tree", "tree", "gift card", "gift",
    "belt", "wallet", "keychain", "lace", "laces", "brush", "cream", "wax",
    "conditioner", "oil", "spray", "socks", "bag", "tote", "provisions"
]


def is_footwear_title(title: str) -> bool:
    t = title.lower()

    # Exclude obvious non-footwear first
    for bad in EXCLUDE_WORDS:
        if bad in t:
            return False

    # Include footwear keywords
    return any(word in t for word in INCLUDE_WORDS)


def fetch_html() -> str:
    r = requests.get(SORTED_URL, headers={"User-Agent": "divisionroad-top5-bot/1.0"}, timeout=30)
    r.raise_for_status()
    return r.text


def extract_top_entries(html: str, n: int = 5):
    soup = BeautifulSoup(html, "html.parser")
    seen = set()
    out = []

    for a in soup.select("a[href*='/products/']"):
        href = (a.get("href") or "").strip()
        title = norm(a.get_text(" ", strip=True))

        if not href or "/products/" not in href or not title or len(title) < 4:
            continue

        # Only keep footwear / boot-like titles
        if not is_footwear_title(title):
            continue

        url = href if href.startswith("http") else "https://divisionroadinc.com" + href
        if url in seen:
            continue
        seen.add(url)

        # Try to find a price near the link (theme-dependent)
        price = None
        card = a.find_parent()
        for _ in range(7):
            if not card:
                break
            text = card.get_text(" ", strip=True)
            m = re.search(r"\$\s?\d{1,3}(?:,\d{3})*(?:\.\d{2})?", text)
            if m:
                price = m.group(0).replace(" ", "")
                break
            card = card.find_parent()

        out.append((title, url, price))
        if len(out) >= n:
            break

    return out


def main():
    html = fetch_html()
    top5 = extract_top_entries(html, 5)

    print("Division Road — Top 5 newest FOOTWEAR entries (Date: new → old)")
    print(SORTED_URL)
    print()

    if not top5:
        print("No footwear entries found. Page structure may have changed.")
        return

    # Target still #1?
    target_norm = norm(TARGET_TITLE)
    latest_title_norm = norm(top5[0][0])
    print("Target still #1?", "YES" if latest_title_norm == target_norm else "NO")
    print()

    for i, (title, url, price) in enumerate(top5, start=1):
        if price:
            print(f"{i}. {title} — {price}")
        else:
            print(f"{i}. {title}")
        print(f"   {url}")


if __name__ == "__main__":
    main()
