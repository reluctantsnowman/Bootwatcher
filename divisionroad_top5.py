import re
import requests
from bs4 import BeautifulSoup

SORTED_URL = "https://divisionroadinc.com/collections/footwear/boots?sort_by=created-descending"

def norm(s: str) -> str:
    s = s.replace("\u2019", "'")
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

def extract_top_boots(html: str, n: int = 5):
    soup = BeautifulSoup(html, "html.parser")
    seen = set()
    out = []

    for a in soup.select("a[href*='/products/']"):
        href = (a.get("href") or "").strip()
        title = norm(a.get_text(" ", strip=True))

        # Basic sanity checks
        if not href or "/products/" not in href or not title or len(title) < 8:
            continue

        # Only include actual boots (exclude mocs etc.)
        if "boot" not in title.lower():
            continue

        url = href if href.startswith("http") else "https://divisionroadinc.com" + href
        if url in seen:
            continue
        seen.add(url)

        out.append((title, url))
        if len(out) >= n:
            break

    return out

def main():
    html = fetch_html()
    top5 = extract_top_boots(html, 5)

    print("Division Road — Top 5 newest BOOTS (Date: new → old)")
    print(SORTED_URL)
    print()

    if not top5:
        print("No boots found. Page structure may have changed.")
        return

    for i, (title, url) in enumerate(top5, start=1):
        print(f"{i}. {title}")
        print(f"   {url}")

if __name__ == "__main__":
    main()
