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
- **Location name / full address** — the post may say just a city; the website often has the venue name AND full street address. Put the full address (e.g. `רחוב השלום 5, תל אביב`) into `location_name` — the card will display it as-is. Never truncate to just the venue name when you have more detail.

Never write a field based only on the post if the registration_link is available —
always fetch the website first and cross-check.

**Failure mode that happened:** Soft Play Party post said `200₪ לאדם` and `20:00`.
Website also had `400₪ לצמד` and end time `00:00`. Both were missed because the website
was never checked during extraction.

## Conflict rules — when post and website disagree

Prefer the **website** for: price, price tiers, couple/person pricing, start/end time, venue name, status (canceled/postponed).

Prefer the **WhatsApp post** when: the website is generic/outdated/unreachable, or the post contains explicit update language (עודכן, שינוי, נדחה, בוטל).

If both sources are valid but conflict: use the more specific value, note the conflict in `source_excerpt`, lower `confidence` to ≤0.6.

## Mandatory second pass before saving events.json

After drafting all events, re-open every `registration_link` and verify:
- `date_only` / `end_date_only`
- `start_time_only` / `end_time_only`
- `price_text` / `price_details` / `price_unit`
- `location_name` / `city`
- `contact_info.phone`

Ask before saving: Did I check every link? Did I capture end time? Did I capture couple price? Did I capture early-bird cutoff dates? Did I capture _image_filename from the WhatsApp post? Did I capture image_url?

## _image_filename (WhatsApp photo — preferred over image_url)

For every event, look for a `<attached: FILENAME.jpg>` line in the WhatsApp post.
It appears at the end of the message, after the text. Capture just the filename (e.g. `00001098-PHOTO-2026-05-09-21-08-42.jpg`) and store it in `_image_filename`.
The pipeline embeds it as a data URI — it takes priority over `image_url`.

## image_url (website fallback)

For every event that has a `registration_link`, fetch the page and extract the event poster image URL:
- Prefer `og:image` or `twitter:image` meta tag content
- **Google Forms** (`docs.google.com/forms/...`) never have og:image — look for the `<img>` tag inside the form header/banner section. The URL is typically `https://lh3.googleusercontent.com/...` or similar. Use that.
- If not found via meta tags, look for the largest/most prominent `<img>` on the page (wixstatic, squarespace, lh3.googleusercontent, etc.)
- Store as `image_url` field in the event object (must start with `https://`)
- If no suitable image found, omit the field (do not set to null)

## Time rules

- Use 24-hour `HH:MM`.
- Do not confuse arrival time / התכנסות / doors-open with event start time.
- If the website shows a range like `20:00–00:00`: `start_time_only: "20:00"`, `end_time_only: "00:00"`.
- If no end time is explicitly stated: `end_time_only: null` — never invent a duration.

## price_details vs price_note
- `price_details` (array): multiple tiers → replaces price display entirely. Include cutoff dates so pipeline can mark expired ones.
- `price_note` (single string): auto-detected by pipeline from website — never set manually.

## When compacting
Focus on: event schema, line_reference values, pipeline step order, any errors encountered.
