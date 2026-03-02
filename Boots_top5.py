import os
import json
import requests
from datetime import datetime

STATE_FILE = "state_last_top5.json"
LOG_FILE = "logs/boots_watcher.log"
README_FILE = "README.md"

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
SAVE_STATE = os.getenv("SAVE_STATE") == "1"


# --------------------------------------------------
# Logging
# --------------------------------------------------

def log(message: str):
    os.makedirs("logs", exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{timestamp}] {message}"
    print(line)

    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# --------------------------------------------------
# Fake Top 5 Data (Replace With Your Real Scraper)
# --------------------------------------------------

def get_top_5_boots():
    """
    Replace this with your real scraping logic.
    Must return a list of dicts:
    [
        {"name": "...", "price": "...", "currency": "...", "url": "..."},
        ...
    ]
    """

    return [
        {"name": "Viberg Service Boot", "price": "725", "currency": "USD", "url": "https://example.com/1"},
        {"name": "Alden Indy", "price": "699", "currency": "USD", "url": "https://example.com/2"},
        {"name": "White's MP", "price": "649", "currency": "USD", "url": "https://example.com/3"},
        {"name": "Tricker's Stow", "price": "595", "currency": "USD", "url": "https://example.com/4"},
        {"name": "Grant Stone Diesel", "price": "380", "currency": "USD", "url": "https://example.com/5"},
    ]


# --------------------------------------------------
# Discord Posting
# --------------------------------------------------

def post_to_discord(boots):
    if not DISCORD_WEBHOOK_URL:
        log("No Discord webhook set.")
        return

    content_lines = ["**🔥 Top 5 Boots Update 🔥**\n"]

    for i, boot in enumerate(boots, start=1):
        content_lines.append(
            f"{i}. **{boot['name']}** — {boot['price']} {boot['currency']}\n{boot['url']}\n"
        )

    payload = {"content": "\n".join(content_lines)}

    response = requests.post(DISCORD_WEBHOOK_URL, json=payload)

    if response.status_code == 204:
        log("Posted update to Discord.")
    else:
        log(f"Failed to post to Discord: {response.status_code}")


# --------------------------------------------------
# State Saving
# --------------------------------------------------

def save_state(boots):
    if not SAVE_STATE:
        return

    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(boots, f, indent=2)

    log("State saved.")


# --------------------------------------------------
# README Update (Marker Safe)
# --------------------------------------------------

def update_readme_summary(run_ts_utc: str, boots: list[dict]):
    if not os.path.exists(README_FILE):
        log("README not found. Skipping update.")
        return

    with open(README_FILE, "r", encoding="utf-8") as f:
        content = f.read()

    start_marker = "<!-- BOOTS_SUMMARY_START -->"
    end_marker = "<!-- BOOTS_SUMMARY_END -->"

    if start_marker not in content or end_marker not in content:
        log("README markers missing. Skipping update.")
        return

    summary_lines = []
    summary_lines.append(f"**Last Run (UTC):** `{run_ts_utc}`\n")
    summary_lines.append("### Top 5 Boots\n")
    summary_lines.append("| Rank | Name | Price | Link |")
    summary_lines.append("|------|------|-------|------|")

    for i, boot in enumerate(boots, start=1):
        price_display = f"{boot['price']} {boot['currency']}".strip()
        summary_lines.append(
            f"| {i} | {boot['name']} | {price_display} | [View]({boot['url']}) |"
        )

    summary_lines.append(
        f"\n_Auto-updated at {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC_"
    )

    new_summary = "\n".join(summary_lines)

    before = content.split(start_marker)[0]
    after = content.split(end_marker)[1]

    updated_content = (
        before
        + start_marker
        + "\n"
        + new_summary
        + "\n"
        + end_marker
        + after
    )

    with open(README_FILE, "w", encoding="utf-8") as f:
        f.write(updated_content)

    log("README updated successfully.")


# --------------------------------------------------
# Main
# --------------------------------------------------

def main():
    run_ts_utc = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    log("Boots watcher started.")

    boots = get_top_5_boots()

    post_to_discord(boots)
    save_state(boots)
    update_readme_summary(run_ts_utc, boots)

    log("Boots watcher completed.")


if __name__ == "__main__":
    main()
