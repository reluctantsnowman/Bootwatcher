import json
import os
import re
import requests
from bs4 import BeautifulSoup

# Force sort: Date, new -> old (Shopify convention)
SORTED_URL = "https://divisionroadinc.com/collections/footwear/boots?sort_by=created-descending"

# Persist last seen list so we can say "Changed since last run?"
STATE_PATH = "state_last_top5.json"

# Used only for a simple "is target #1?" check
TARGET_FRAGMENT = "Stow Boot - 4497"


def norm(s: str) -> str:
    """Normalize punctuation and whitespace for stable comparisons."""
    s = s.replace("\u2019", "'")  # curly apostrophe -> straight
    s = re.sub(r"\s+", " ", s).strip()
    return s


def fetch_html() -> str:
    """Fetch the boots collection page (sorted newest first)."""
    r = requests.get(
        SORTED_URL,
        headers={"User-Agent": "divisionroad-top5-bot/1.0"},
        timeout=30,
    )
    r.raise_for_status()
    return r.text


def extract_top_boots(html: str, n: int = 5):
    """
    Extract the top N *boot* products from the collection page.
    Filters out non-boots (e.g., 'Moc') by requiring 'boot' in the title.
    """
    soup = BeautifulSoup(html, "html.parser")
    seen = set()
    out = []

    for a in soup.select("a[href*='/products/']"):
        href = (a.get("href") or "").strip()
        title = norm(a.get_text(" ", strip=True))

        # Must look like a product card link
        if not href or "/products/" not in href or not title or len(title) < 8:
            continue

        # Only include actual boots (exclude mocs, etc.)
        if "boot" not in title.lower():
            continue

        url = href if href.startswith("http") else "https://divisionroadinc.com" + href

        # De-dupe (product cards often have multiple links)
        if url in seen:
            continue
        seen.add(url)

        out.append({"title": title, "url": url})
        if len(out) >= n:
            break

    if len(out) < n:
        raise RuntimeError(
            f"Only found {len(out)} boot products. "
            "The page may have fewer boots or the page structure changed."
        )

    return out


def load_state():
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None


def save_state(top_list):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(top_list, f, ensure_ascii=False, indent=2)


def send_discord(message: str):
    webhook = os.environ["DISCORD_WEBHOOK_URL"]
    resp = requests.post(webhook, json={"content": message}, timeout=30)
    if resp.status_code not in (200, 204):
        raise RuntimeError(f"Discord webhook error {resp.status_code}: {resp.text}")


def main():
    html = fetch_html()
    top5 = extract_top_boots(html, 5)

    prev = load_state()
    changed = (prev != top5)

    latest_title = top5[0]["title"]
    target_is_latest = TARGET_FRAGMENT.lower() in latest_title.lower()

    lines = []
    lines.append("**Division Road — Top 5 newest BOOTS (Date: new → old)**")
    lines.append(SORTED_URL)
    lines.append("")
    lines.append(f"Changed since last run? **{'YES' if changed else 'NO'}**")
    lines.append(f"Target still #1? **{'YES' if target_is_latest else 'NO'}**")
    lines.append("")
    lines.append("**Top 5:**")

    for i, p in enumerate(top5, start=1):
        lines.append(f"{i}. {p['title']}")
        lines.append(f"   {p['url']}")

    send_discord("\n".join(lines))
    save_state(top5)


if __name__ == "__main__":
    main()
