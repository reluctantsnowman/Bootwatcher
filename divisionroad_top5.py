import re
import requests
from bs4 import BeautifulSoup

SORTED_URL = "https://divisionroadinc.com/collections/footwear/boots?sort_by=created-descending"

# Your specific target (exact match after normalization)
TARGET_TITLE = "Stow Boot - 4497 - Leather - Tempesti Ambra Elbamatt Liscio"


def norm(s: str) -> str:
    """Normalize punctuation + whitespace for stable comparisons."""
    s = s.replace("\u2019", "'")  # curly apostrophe -> straight
    s = re.sub(r"\s+", " ", s).strip()
    return s


def fetch_html() -> str:
    r = requests.get(
        SORTED_URL,
        headers={"User-Agent": "divisionroad-top5-bot/1.0"},
        timeout=30,
    )
    r.raise_for_status()
    return r.text


def extract_top_entries(html: str, n: int = 5):
    """
    Extract top N footwear entries from the collection page.

    Includes:
      - Boots (titles containing 'boot')
      - Boot-like items (moc, chukka, etc.) since you want 1/2/3.

    Also tries to extract a price if it appears near the product card.
    """
    soup = BeautifulSoup(html, "html.parser")
    seen = set()
    out = []

    # Grab product links. Product cards typically contain /products/ links.
    for a in soup.select("a[href*='/products/']"):
        href = (a.get("href") or "").strip()
        title = norm(a.get_text(" ", strip=True))

        # Basic sanity checks
        if not href or "/products/" not in href or not title or len(title) < 8:
            continue

        url = href if href.startswith("http") else "https://divisionroadinc.com" + href
        if url in seen:
            continue
        seen.add(url)

        # Try to find a price near the link (theme-dependent).
        price = None
        card = a.find_parent()
        for _ in range(6):  # climb a few levels to reach the product card container
            if not card:
                break
            text = card.get_text(" ", strip=True)
            # Common Shopify price patterns like "$765" or "$1,225"
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

    print("Division Road — Top 5 newest entries (Date: new → old)")
    print(SORTED_URL)
    print()

    if not top5:
        print("No entries found. Page structure may have changed.")
        return

    # 1) Target still #1?
    target_norm = norm(TARGET_TITLE)
    latest_title_norm = norm(top5[0][0])
    print("Target still #1?", "YES" if latest_title_norm == target_norm else "NO")
    print()

    # 2) Include price (if found)
    # 3) Include boot-like items (we are not filtering to 'boot' only anymore)
    for i, (title, url, price) in enumerate(top5, start=1):
        if price:
            print(f"{i}. {title} — {price}")
        else:
            print(f"{i}. {title}")
        print(f"   {url}")


if __name__ == "__main__":
    main()
