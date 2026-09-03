# Patreon supporters feed

`patreon_supporters.py` regenerates `../supporters.json` from the TessTrack
Patreon campaign. The TessTrack app (`RemoteSupportersProvider`) fetches that
file from `https://bill209.github.io/tesstrack-docs/supporters.json` and shows
it on the in-app Supporters screen, grouped by tier. If the fetch fails the app
falls back to a hand-kept list baked into the binary, so a missing or stale
feed never breaks the screen.

## How it runs in CI

`.github/workflows/refresh-supporters.yml` runs this daily (and on demand via
**Actions -> Refresh Patreon supporters -> Run workflow**) and commits
`supporters.json` when it changes. Scheduled runs only fire from the default
branch, so the workflow has to be on `main` to start.

### Required repo secrets

Settings -> Secrets and variables -> Actions:

| Secret | Where it comes from |
| --- | --- |
| `PATREON_CLIENT_ID` | patreon.com/portal/registration/register-clients |
| `PATREON_CLIENT_SECRET` | same page |
| `PATREON_ACCESS_TOKEN` | "Creator's Access Token" on that page |
| `PATREON_REFRESH_TOKEN` | "Creator's Refresh Token" on that page |
| `PATREON_CAMPAIGN_ID` | `16726725` (from `GET /api/oauth2/v2/campaigns` -> `data[].id`) |
| `GH_PAT` | fine-grained PAT for this repo: Contents read/write + Secrets read/write |

The client must have the `identity`, `campaigns`, and `campaigns.members`
scopes. The script uses `PATREON_ACCESS_TOKEN` directly and only falls back to
the refresh-token exchange when it gets a 401 (~monthly); when it does, it
writes the rotated tokens back into the secrets via `GH_PAT`.

## Running it locally

```sh
cd scripts
cp .env.example .env      # then fill in the five PATREON_* values
set -a; . ./.env; set +a
python3 patreon_supporters.py
cd .. && git add supporters.json && git commit -m "refresh supporters"
```

`.env` and `patreon_tokens.env` are gitignored. If the local run triggers a
token refresh it writes `patreon_tokens.env` next to the script — copy those
two values back into your `.env` (and into the repo secrets) so the next run
starts from the current tokens.

## Tier mapping

`map_tier()` lower-cases each Patreon tier title and matches on the substrings
`ludicrous` / `sport` / `chill`, so titles like `Chill 🚗 $3/mo` still map. The
campaign's current paid tiers are exactly `Chill` / `Sport` / `Ludicrous`, so no
overrides are needed; the `Free` tier is in `IGNORED_TIER_TITLES` and skipped
silently. If a new paid title doesn't contain its keyword, add an explicit entry
to `TIER_OVERRIDES` at the top of the script. Any other unmapped tier is logged
and its patrons skipped.
