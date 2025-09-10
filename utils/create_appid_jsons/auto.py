#!/usr/bin/env python3
import json
import argparse
import os
import requests
import time

SEARCH_URL = "https://store.steampowered.com/api/storesearch/"

def fetch_appid_interactive(title):
    """Search Steam for a game title, let user pick if multiple results exist."""
    try:
        resp = requests.get(SEARCH_URL, params={"term": title, "cc": "us"}, timeout=10)
        data = resp.json()
        items = data.get("items", [])

        if not items:
            print(f"❌ No AppID found for: {title}")
            return None

        if len(items) == 1:
            return items[0]["id"]

        # Multiple matches, prompt user
        print(f"\nMultiple matches found for '{title}':")
        for idx, item in enumerate(items, 1):
            print(f"  {idx}. {item['name']} (AppID: {item['id']})")

        while True:
            choice = input(f"Select the correct match [1-{len(items)}] or 's' to skip: ")
            if choice.lower() == "s":
                print(f"⚠️ Skipped {title}")
                return None
            try:
                choice = int(choice)
                if 1 <= choice <= len(items):
                    return items[choice - 1]["id"]
            except ValueError:
                pass
            print("Invalid choice, try again.")
    except Exception as e:
        print(f"⚠️ Error fetching {title}: {e}")
    return None


def generate_wishlist(input_file, output_file, delay=0.5):
    wishlist = {}

    with open(input_file, "r", encoding="utf-8") as f:
        for line in f:
            title = line.strip()
            if not title or title.startswith("#"):
                continue

            appid = fetch_appid_interactive(title)
            if not appid:
                continue

            wishlist[str(appid)] = {
                "appid": str(appid),
                "title": title,
                "image_url": f"https://cdn.akamai.steamstatic.com/steam/apps/{appid}/header_292x136.jpg",
                "image_path": None,
                "rating": 1500.0,
                "wins": 0,
                "losses": 0,
                "played": 0
            }

            print(f"✅ Added {title} ({appid})")
            time.sleep(delay)  # avoid hammering Steam API

    with open(output_file, "w", encoding="utf-8") as out:
        json.dump(wishlist, out, indent=2, ensure_ascii=False)

    print(f"\n🎉 Wishlist JSON created: {output_file} with {len(wishlist)} games")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate wishlist.json from game titles by fetching appid")
    parser.add_argument("input", help="Input text file with lines: title")
    parser.add_argument("-o", "--output", default="wishlist.json", help="Output JSON file (default: wishlist.json)")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"❌ Input file not found: {args.input}")
        exit(1)

    generate_wishlist(args.input, args.output)
