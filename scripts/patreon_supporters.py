#!/usr/bin/env python3
"""
Regenerate supporters.json from the TessTrack Patreon campaign.

The TessTrack app (RemoteSupportersProvider) fetches the resulting
supporters.json from this site's GitHub Pages URL. The Patreon token never
touches the app binary — it lives only where this script runs.

Environment (GitHub Actions secrets, or a local .env you source yourself):

    PATREON_CLIENT_ID
    PATREON_CLIENT_SECRET
    PATREON_ACCESS_TOKEN     Creator's Access Token (from the client reg page)
    PATREON_REFRESH_TOKEN    Creator's Refresh Token
    PATREON_CAMPAIGN_ID      numeric id of your campaign

What it does:
  1. Try the members endpoint with PATREON_ACCESS_TOKEN as-is.
  2. Only if that 401s (token expired, ~monthly): exchange the refresh token
     for a fresh pair, retry, and write the new tokens to  patreon_tokens.env
     (KEY=VALUE lines) so the workflow can push them back into the repo
     secrets. Patreon rotates the refresh token on every use, so we refresh as
     rarely as possible.
  3. Page through /campaigns/{id}/members, keep active patrons, map each one's
     highest entitled tier to chill|sport|ludicrous.
  4. Write supporters.json:
         { "generated": "<iso8601 Z>", "supporters": [ {"name","tier"}, ... ] }
     sorted by tier rank then name. A patron with no name becomes "Anonymous".

Exits non-zero on any hard failure so the Action surfaces it.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

API = "https://www.patreon.com/api/oauth2/v2"
TOKEN_URL = "https://www.patreon.com/api/oauth2/token"

# Patreon tier title (lower-cased) -> our tier key. The generic "contains"
# match below already handles titles like "Chill 🚗 $3/mo"; only add an entry
# here if a real title does NOT contain its keyword.
# Campaign 16726725 tiers as of 2026-09-03: Free / Chill ($3) / Sport ($7) /
# Ludicrous ($15) — the three paid titles match by substring with no overrides.
TIER_OVERRIDES: dict[str, str] = {
    # "founding drivers": "ludicrous",
}
# lower number = higher tier; used both to pick a patron's top tier and to sort.
TIER_RANK = {"ludicrous": 0, "sport": 1, "chill": 2}
# Titles we intentionally don't list (free followers). Skipped without a warning.
IGNORED_TIER_TITLES = {"free"}


class AuthExpired(Exception):
    """Raised on HTTP 401 so main() can refresh and retry once."""


def die(msg: str, code: int = 1):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        die(f"missing required env var {name}")
    return value


def api_get(url: str, access_token: str) -> dict:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {access_token}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise AuthExpired()
        body = exc.read().decode("utf-8", "replace")[:500]
        die(f"GET {url} -> HTTP {exc.code}: {body}")
    except urllib.error.URLError as exc:
        die(f"GET {url} -> {exc.reason}")


def refresh_tokens(client_id: str, client_secret: str, refresh_token: str) -> dict:
    data = urllib.parse.urlencode(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        }
    ).encode()
    req = urllib.request.Request(
        TOKEN_URL,
        data=data,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            tok = json.load(resp)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:500]
        die(f"token refresh -> HTTP {exc.code}: {body}")
    except urllib.error.URLError as exc:
        die(f"token refresh -> {exc.reason}")
    if "access_token" not in tok:
        die(f"token refresh returned no access_token: {tok}")
    return tok


def map_tier(title: str) -> str | None:
    key = title.strip().lower()
    if key in TIER_OVERRIDES:
        return TIER_OVERRIDES[key]
    for name in TIER_RANK:
        if name in key:
            return name
    return None


def fetch_supporters(access_token: str, campaign_id: str) -> list[dict]:
    params = {
        "include": "currently_entitled_tiers",
        "fields[member]": "full_name,patron_status",
        "fields[tier]": "title",
        "page[count]": "200",
    }
    url = f"{API}/campaigns/{campaign_id}/members?" + urllib.parse.urlencode(params)

    tier_titles: dict[str, str] = {}
    people: list[dict] = []
    unmapped_titles: set[str] = set()

    while url:
        page = api_get(url, access_token)

        for inc in page.get("included", []):
            if inc.get("type") == "tier":
                tier_titles[inc["id"]] = inc.get("attributes", {}).get("title", "")

        for member in page.get("data", []):
            attrs = member.get("attributes", {})
            if attrs.get("patron_status") != "active_patron":
                continue
            tier_ids = [
                t["id"]
                for t in member.get("relationships", {})
                .get("currently_entitled_tiers", {})
                .get("data", [])
            ]
            mapped = []
            for tid in tier_ids:
                title = tier_titles.get(tid, "")
                key = map_tier(title)
                if key:
                    mapped.append(key)
                elif title and title.strip().lower() not in IGNORED_TIER_TITLES:
                    unmapped_titles.add(title)
            if not mapped:
                continue
            tier = min(mapped, key=lambda k: TIER_RANK[k])  # highest tier wins
            name = (attrs.get("full_name") or "").strip() or "Anonymous"
            people.append({"name": name, "tier": tier})

        url = page.get("links", {}).get("next")

    if unmapped_titles:
        print(
            "warning: these Patreon tier titles didn't map to chill/sport/ludicrous "
            f"and were skipped: {sorted(unmapped_titles)}",
            file=sys.stderr,
        )

    people.sort(key=lambda p: (TIER_RANK[p["tier"]], p["name"].casefold()))
    return people


def main() -> None:
    client_id = env("PATREON_CLIENT_ID")
    client_secret = env("PATREON_CLIENT_SECRET")
    access_token = env("PATREON_ACCESS_TOKEN")
    refresh_token = env("PATREON_REFRESH_TOKEN")
    campaign_id = env("PATREON_CAMPAIGN_ID")

    try:
        supporters = fetch_supporters(access_token, campaign_id)
    except AuthExpired:
        print("access token expired — refreshing", file=sys.stderr)
        tok = refresh_tokens(client_id, client_secret, refresh_token)
        access_token = tok["access_token"]
        new_refresh = tok.get("refresh_token", refresh_token)
        out_path = os.environ.get("PATREON_TOKENS_OUT", "patreon_tokens.env")
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(f"PATREON_ACCESS_TOKEN={access_token}\n")
            fh.write(f"PATREON_REFRESH_TOKEN={new_refresh}\n")
        supporters = fetch_supporters(access_token, campaign_id)

    payload = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "supporters": supporters,
    }
    with open("supporters.json", "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    counts: dict[str, int] = {}
    for person in supporters:
        counts[person["tier"]] = counts.get(person["tier"], 0) + 1
    print(f"wrote supporters.json — {len(supporters)} active patrons {counts or '{}'}")


if __name__ == "__main__":
    main()
