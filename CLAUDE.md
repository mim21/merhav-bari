# Claude Code – מרחב בריא

See README.md for full project docs and event schema.

## Chat file location
`c:/PRIVATE/merhav-bari/WhatsApp_Chat_מרחב_בריא_פרסום_מרחבים_ואירועים/_chat.txt`

## How to run
1. Read `_chat.txt` and write all upcoming events to `events.json` (follow schema in README.md)
2. Run `python pipeline.py` or `run.bat`

## Extracting events — accuracy rules (MANDATORY)

For EVERY event, you must check BOTH the WhatsApp post AND the registration website.
The post alone is never enough. Known gaps the post often omits:

- **end_time_only** — the post usually only mentions start time. The website always has both.
- **Couple vs. per-person pricing** — posts often mention only the individual price.
  The website may also have a couple (לצמד/לזוג) price. Capture BOTH in `price_details`.
- **Early-bird tiers with cutoff dates** — use `price_details` with the exact dates
  so the pipeline can auto-mark expired tiers.
- **Location name** — the post may say just a city; the website often has the venue name.

Never write a field based only on the post if the registration_link is available —
always fetch the website first and cross-check.

**Failure mode that happened:** Soft Play Party post said `200₪ לאדם` and `20:00`.
Website also had `400₪ לצמד` and end time `00:00`. Both were missed because the website
was never checked during extraction.

## price_details vs price_note
- `price_details` (array): multiple tiers → replaces price display entirely. Include cutoff dates so pipeline can mark expired ones.
- `price_note` (single string): auto-detected by pipeline from website — never set manually.

## When compacting
Focus on: event schema, line_reference values, pipeline step order, any errors encountered.
