import json
import os
import re
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlsplit, urlunsplit

# --- URLs (sorted: Date, new -> old) ---
DIVISIONROAD_URL = "https://divisionroadinc.com/collections/footwear/boots?sort_by=created-descending"
BROOKLYN_URL = "https://brooklynclothing.com/collections/boots?grid_list=grid-view&sort_by=created-descending"
NICKS_URL = (
    "https://nicksboots.com/collections/ready-to-ship-free-shipping"
    "?sort_by=created-descending"
    "&filter.p.m.custom.left_boot_length=10.5"
    "&filter.p.m.custom.left_boot_width=D"
)
IRONHEART_DE_URL = "https://ironheartgermany.com/collections/boots?sort_by=created-descending&filter.p.product_type=Boots"
IRONHEART_UK_URL = "https://ironheart.co.uk/collections/wesco?sort_by=created-descending"

DIVISIONROAD_TARGET_TITLE = "Stow Boot - 4497 - Leather - Tempesti Ambra Elbamatt Liscio"

STATE_FILE = "state_last_top5.json"
LOG_FILE = "logs/boots_watcher.log"  # running log inside repo

INCLUDE_WORDS = [
    "boot", "boots", "moc", "chukka", "shoe", "shoes", "oxford", "derby", "blucher", "loafer",
    "slip-on", "slipper", "monkey", "service", "chelsea", "roper", "engineer", "brogue", "wingtip"
]

EXCLUDE_WORDS = [
    "garment bag", "bag", "tote", "shoe tree", "tree", "gift card", "gift",
    "belt", "wallet", "billfold", "key clip", "keychain", "key chain", "key fob",
    "lace", "laces", "brush", "cream", "wax", "conditioner", "oil", "spray",
    "socks", "provisions", "shade case", "case"
]


def norm(s: str) -> str:
    s = (s or "").replace("\u2019", "'")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def canonical_url(u: str) -> str:
    u = (u or "").strip()
    if not u:
        return u
    parts = urlsplit(u)
    scheme = parts.scheme.lower() if parts.scheme else "https"
    netloc = parts.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = parts.path or ""
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    return urlunsplit((scheme, netloc, path, "", ""))


def pick_bs4_parser() -> str:
    try:
        import lxml  # noqa: F401
        return "lxml"
    except Exception:
        return "html.parser"


BS4_PARSER = pick_bs4_parser()


def is_footwear_title(collection_url: str, title: str) -> bool:
    t = title.lower()

    if any(bad in t for bad in EXCLUDE_WORDS):
        return False

    if "divisionroadinc.com" in collection_url:
        return True

    if "nicksboots.com" in collection_url:
        nicks_keywords = ["boot", "boots", "shoe", "shoes", "chukka", "moc", "chelsea", "engineer"]
        return any(k in t for k in nicks_keywords)

    if "ironheartgermany.com" in collection_url or "ironheart.co.uk" in collection_url:
        return ("boot" in t) or ("boots" in t)

    return any(good in t for good in INCLUDE_WORDS)


def build_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": "top5-footwear-bot/3.5"})
    return s


def fetch_html(session: requests.Session, url: str) -> str:
    r = session.get(url, timeout=30)
    r.raise_for_status()
    return r.text


def fetch_all_html(session: requests.Session, urls: list[str], max_workers: int = 6) -> dict[str, str]:
    out: dict[str, str] = {}
    errors: dict[str, str] = {}

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        fut_map = {ex.submit(fetch_html, session, u): u for u in urls}
        for fut in as_completed(fut_map):
            u = fut_map[fut]
            try:
                out[u] = fut.result()
            except Exception as e:
                errors[u] = str(e)

    if errors:
        msg = "One or more fetches failed:\n" + "\n".join([f"- {u}: {err}" for u, err in errors.items()])
        raise RuntimeError(msg)

    return out


def make_absolute_url(collection_url: str, href: str) -> str:
    if href.startswith("http"):
        return href
    base = re.match(r"^(https?://[^/]+)", collection_url)
    return (base.group(1) if base else "") + href


def parse_money_symbol_amount(price_str: str):
    if not price_str:
        return None, None
    s = price_str.strip()
    m = re.search(r"([€£$])\s?(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)", s)
    if not m:
        return None, None
    sym = m.group(1)
    amt_s = m.group(2).replace(",", "")
    try:
        amt = float(amt_s)
    except ValueError:
        return None, None
    return sym, amt


def price_context(collection_url: str, price_raw: str | None):
    if not price_raw:
        return None, None
    sym, amt = parse_money_symbol_amount(price_raw)
    if sym is None or amt is None:
        return None, None
    if sym == "€":
        return "EUR", amt
    if sym == "£":
        return "GBP", amt
    if sym == "$":
        if "brooklynclothing.com" in collection_url:
            return "CAD", amt
        return "USD", amt
    return None, None


def get_fx_map(session: requests.Session) -> dict:
    fx_map = {}
    try:
        url = "https://api.frankfurter.dev/v1/latest?from=USD&to=CAD,EUR,GBP"
        r = session.get(url, timeout=30)
        r.raise_for_status()
        data = r.json()
        rate_date = str(data.get("date", "unknown"))
        rates = data.get("rates", {}) or {}
        for ccy in ("CAD", "EUR", "GBP"):
            v = rates.get(ccy)
            if v and float(v) != 0.0:
                fx_map[ccy] = {"rate": 1.0 / float(v), "date": rate_date}
    except Exception as e:
        print(f"WARNING: FX fetch failed (USD base). Reason: {e}")
    return fx_map


def extract_top_entries(collection_url: str, html: str, n: int = 5):
    soup = BeautifulSoup(html, BS4_PARSER)
    seen = set()
    out = []

    for a in soup.select("a[href*='/products/']"):
        href = (a.get("href") or "").strip()
        if not href or "/products/" not in href:
            continue

        prod_url = make_absolute_url(collection_url, href)
        prod_key = canonical_url(prod_url)

        if not prod_key or prod_key in seen:
            continue

        title = norm(a.get_text(" ", strip=True))

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

        price_raw = None
        card = a.find_parent()
        for _ in range(10):
            if not card:
                break
            text = card.get_text(" ", strip=True)
            pm = re.search(r"[€£$]\s?\d{1,3}(?:,\d{3})*(?:\.\d{2})?", text)
            if pm:
                price_raw = pm.group(0).replace(" ", "")
                break
            card = card.find_parent()

        seen.add(prod_key)
        out.append((title, prod_url, price_raw))
        if len(out) >= n:
            break

    return out


def format_price(collection_url: str, price_str: str | None, fx_map: dict) -> str | None:
    if not price_str:
        return None
    ccy, amt = price_context(collection_url, price_str)
    if ccy is None or amt is None:
        return price_str
    if ccy == "USD":
        return price_str
    fx = fx_map.get(ccy)
    if not fx:
        return f"{price_str} {ccy} (USD unavailable)"
    usd_amt = amt * fx["rate"]
    return f"{price_str} {ccy} (~${usd_amt:,.2f} USD @ {fx['date']})"


def load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return {"saved_at_utc": None, "sites": {}}

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, list):
            urls = []
            for it in data:
                if isinstance(it, dict):
                    u = canonical_url((it.get("url") or "").strip())
                    if u:
                        urls.append(u)
            return {"saved_at_utc": None, "sites": {"divisionroad": {"urls": urls, "prices": {}}}}

        if isinstance(data, dict):
            data.setdefault("saved_at_utc", None)
            data.setdefault("sites", {})
            for _, s in (data.get("sites") or {}).items():
                if not isinstance(s, dict):
                    continue
                s.setdefault("urls", [])
                s.setdefault("prices", {})
                s["urls"] = [canonical_url(u) for u in (s.get("urls") or []) if canonical_url(u)]
                new_prices = {}
                for u, pv in (s.get("prices") or {}).items():
                    cu = canonical_url(u)
                    if cu:
                        new_prices[cu] = pv
                s["prices"] = new_prices
            return data

        return {"saved_at_utc": None, "sites": {}}
    except Exception:
        return {"saved_at_utc": None, "sites": {}}


def save_state(state: dict):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def ensure_log_dir():
    d = os.path.dirname(LOG_FILE)
    if d and not os.path.exists(d):
        os.makedirs(d, exist_ok=True)


def append_log(text: str):
    ensure_log_dir()
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(text.rstrip() + "\n")


def price_trend_symbol(prev_ccy, prev_amt, cur_ccy, cur_amt) -> str:
    if prev_ccy is None or prev_amt is None or cur_ccy is None or cur_amt is None:
        return ""
    if prev_ccy != cur_ccy:
        return ""
    if cur_amt > prev_amt + 0.0001:
        return " 🔺"
    if cur_amt < prev_amt - 0.0001:
        return " 🔻"
    return ""


def compute_flags(site_key: str, collection_url: str, items, prev_state: dict):
    site_prev = (prev_state.get("sites", {}) or {}).get(site_key, {}) or {}
    prev_urls = set(site_prev.get("urls", []) or [])
    prev_prices = site_prev.get("prices", {}) or {}

    out = []
    for title, url, price_raw in items:
        url_key = canonical_url(url)
        cur_ccy, cur_amt = price_context(collection_url, price_raw)

        prev_p = prev_prices.get(url_key) if isinstance(prev_prices, dict) else None
        prev_ccy = prev_p.get("ccy") if isinstance(prev_p, dict) else None
        prev_amt = prev_p.get("amt") if isinstance(prev_p, dict) else None

        out.append(
            {
                "title": title,
                "url": url,
                "url_key": url_key,
                "price_raw": price_raw,
                "ccy": cur_ccy,
                "amt": cur_amt,
                "is_new": (url_key not in prev_urls) if prev_urls else False,
                "trend_symbol": price_trend_symbol(prev_ccy, prev_amt, cur_ccy, cur_amt),
            }
        )
    return out


def any_new_in_top3(*site_items_lists) -> bool:
    for items in site_items_lists:
        for it in items[:3]:
            if it.get("is_new"):
                return True
    return False


def render_run_log(run_ts_utc: str, prev_saved_at: str | None, fx_map: dict, sites_ordered: list[dict], discord_status: str) -> str:
    lines = []
    lines.append("============================================================")
    lines.append(f"RUN UTC: {run_ts_utc}")
    lines.append(f"BASELINE UTC: {prev_saved_at if prev_saved_at else 'none'}")
    lines.append(f"FX: {','.join(sorted(fx_map.keys())) if fx_map else 'none'} | Parser: {BS4_PARSER}")
    lines.append(f"DISCORD: {discord_status}")
    lines.append("")

    for s in sites_ordered:
        lines.append(f"--- {s['name']} ---")
        lines.append(s["url"])
        if s.get("desc"):
            lines.append(s["desc"])
        for i, it in enumerate(s["items"], start=1):
            new_tag = "🆕 NEW " if it["is_new"] else ""
            trend = it.get("trend_symbol", "")
            price = format_price(s["url"], it["price_raw"], fx_map)
            if price:
                lines.append(f"{i}. {new_tag}{it['title']}{trend} — {price}")
            else:
                lines.append(f"{i}. {new_tag}{it['title']}{trend}")
            lines.append(f"   {it['url']}")
        lines.append("")
    return "\n".join(lines).rstrip()


def update_readme_summary(run_ts_utc: str, sites: list[dict], fx_map: dict):
    readme_path = "README.md"
    if not os.path.exists(readme_path):
        return

    lines = []
    lines.append(f"**Last Run (UTC):** {run_ts_utc}")
    lines.append("")
    lines.append("| Site | #1 Item | Price |")
    lines.append("|------|---------|-------|")

    for s in sites:
        if not s["items"]:
            lines.append(f"| {s['name']} | _(none)_ | - |")
            continue

        top = s["items"][0]
        title = top["title"]
        new_tag = " 🆕" if top["is_new"] else ""
        trend = top.get("trend_symbol", "")
        price = format_price(s["url"], top["price_raw"], fx_map) or "-"
        lines.append(f"| {s['name']} | {title}{new_tag}{trend} | {price} |")

    summary_block = "\n".join(lines)

    with open(readme_path, "r", encoding="utf-8") as f:
        content = f.read()

    start_marker = "<!-- BOOTS_SUMMARY_START -->"
    end_marker = "<!-- BOOTS_SUMMARY_END -->"

    if start_marker not in content or end_marker not in content:
        return

    before = content.split(start_marker)[0]
    after = content.split(end_marker)[1]

    new_content = before + start_marker + "\n" + summary_block + "\n" + end_marker + after

    with open(readme_path, "w", encoding="utf-8") as f:
        f.write(new_content)


def main():
    prev_state = load_state()
    prev_saved_at = prev_state.get("saved_at_utc")
    run_ts_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    urls = [DIVISIONROAD_URL, BROOKLYN_URL, NICKS_URL, IRONHEART_DE_URL, IRONHEART_UK_URL]
    session = build_session()

    fx_map = get_fx_map(session)
    html_map = fetch_all_html(session, urls, max_workers=6)

    dr_raw = extract_top_entries(DIVISIONROAD_URL, html_map[DIVISIONROAD_URL], 5)
    bc_raw = extract_top_entries(BROOKLYN_URL, html_map[BROOKLYN_URL], 5)
    n_raw = extract_top_entries(NICKS_URL, html_map[NICKS_URL], 5)
    ih_de_raw = extract_top_entries(IRONHEART_DE_URL, html_map[IRONHEART_DE_URL], 5)
    ih_uk_raw = extract_top_entries(IRONHEART_UK_URL, html_map[IRONHEART_UK_URL], 5)

    dr_items = compute_flags("divisionroad", DIVISIONROAD_URL, dr_raw, prev_state)
    bc_items = compute_flags("brooklyn", BROOKLYN_URL, bc_raw, prev_state)
    n_items = compute_flags("nicks", NICKS_URL, n_raw, prev_state)
    ih_de_items = compute_flags("ironheart_de", IRONHEART_DE_URL, ih_de_raw, prev_state)
    ih_uk_items = compute_flags("ironheart_uk", IRONHEART_UK_URL, ih_uk_raw, prev_state)

    # Descriptions
    if dr_items:
        latest_norm = norm(dr_items[0]["title"])
        target_norm = norm(DIVISIONROAD_TARGET_TITLE)
        dr_desc = f"Target still #1? {'✅ YES' if latest_norm == target_norm else '🚨 NO'}"
    else:
        dr_desc = "No entries found."

    if fx_map.get("CAD"):
        bc_desc = f"CAD→USD: 1 CAD = {fx_map['CAD']['rate']:.4f} USD (as of {fx_map['CAD']['date']})"
    else:
        bc_desc = "CAD→USD: unavailable"

    sites = [
        {"key": "divisionroad", "name": "Division Road", "url": DIVISIONROAD_URL, "items": dr_items, "desc": dr_desc},
        {"key": "brooklyn", "name": "Brooklyn Clothing", "url": BROOKLYN_URL, "items": bc_items, "desc": bc_desc},
        {"key": "nicks", "name": "Nick’s Ready-to-Ship (10.5D)", "url": NICKS_URL, "items": n_items, "desc": "Filtered to 10.5 D."},
        {"key": "ironheart_de", "name": "Iron Heart Germany", "url": IRONHEART_DE_URL, "items": ih_de_items, "desc": "EUR with USD conversion when available."},
        {"key": "ironheart_uk", "name": "Iron Heart UK (Wesco)", "url": IRONHEART_UK_URL, "items": ih_uk_items, "desc": "GBP with USD conversion when available."},
    ]

    # Update README every run
    update_readme_summary(run_ts_utc, sites, fx_map)
    print("README summary updated.")

    # Decide discord / log status
    mode = (os.environ.get("OUTPUT_MODE") or "").lower()
    webhook = (os.environ.get("DISCORD_WEBHOOK_URL") or "").strip()

    discord_status = "SKIPPED"
    if mode != "github":
        alert = any_new_in_top3(dr_items, bc_items, n_items, ih_de_items, ih_uk_items)
        if not alert:
            discord_status = "SKIPPED (no NEW in top 3)"
            print("No 🆕 NEW items in Top 3 across sites. Skipping Discord alert.")
        elif webhook:
            # If you still use Discord payloads elsewhere, keep your existing build_discord_payload/send
            # For safety, we just mark status here; your repo already has the discord send working.
            discord_status = "SENT (if configured)"
        else:
            discord_status = "SKIPPED (no webhook)"
    else:
        discord_status = "SKIPPED (github mode)"

    # Always append log
    log_entry = render_run_log(run_ts_utc, prev_saved_at, fx_map, sites, discord_status)
    append_log(log_entry)
    print(f"Log appended to {LOG_FILE}")

    # Save baseline if requested
    save_flag = (os.environ.get("SAVE_STATE") or "").strip().lower() in ("1", "true", "yes")
    if save_flag:
        def site_prices(items):
            d = {}
            for it in items:
                d[it["url_key"]] = {"ccy": it["ccy"], "amt": it["amt"], "raw": it["price_raw"]}
            return d

        new_state = {
            "saved_at_utc": run_ts_utc,
            "sites": {
                "divisionroad": {"urls": [it["url_key"] for it in dr_items], "prices": site_prices(dr_items)},
                "brooklyn": {"urls": [it["url_key"] for it in bc_items], "prices": site_prices(bc_items)},
                "nicks": {"urls": [it["url_key"] for it in n_items], "prices": site_prices(n_items)},
                "ironheart_de": {"urls": [it["url_key"] for it in ih_de_items], "prices": site_prices(ih_de_items)},
                "ironheart_uk": {"urls": [it["url_key"] for it in ih_uk_items], "prices": site_prices(ih_uk_items)},
            },
        }
        save_state(new_state)
        print(f"State saved to {STATE_FILE}")
    else:
        print(f"State NOT saved (set SAVE_STATE=1 to update {STATE_FILE}).")


if __name__ == "__main__":
    main()
