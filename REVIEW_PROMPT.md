# Security Review Round 10 — pipeline.py

Review this file: https://github.com/mim21/merhav-bari/blob/d177ba6/pipeline.py

You are reviewing `pipeline.py` in the merhav-bari project.
This is a WhatsApp chat → JSON → HTML event pipeline that publishes to GitHub Pages.

The file reads `events.json` (written by an AI agent, not sanitized) and generates a
self-contained `index.html` with base64-embedded images. Treat `events.json` as
untrusted input.

---

## What was fixed in the previous rounds (up to commit d177ba6)

### Rounds 1–6 — robustness against malformed events.json
- `_events_from_json(data)` helper — safely extracts events list from any JSON shape
- `_str(v)`, `_list(v)` helpers — applied at every JSON boundary to avoid TypeErrors
- `_find_image` window narrowed to forward-only 0–3 lines (was ±40, could attach wrong photo)
- `_format_date` falls back to `event_start` when `date_only` is null
- Phone contact `name` coerced with `_str()`
- `_safe_url()` validates http/https scheme + netloc before use
- Atomic file writes via `.tmp` + `os.replace()`

### Round 7 — `_make_cal_links(event)` added (commit 61feb68)
**New feature:** Each event card has a `📅 Google` link and a `🍎 Apple` (.ics download).
**Security surface:**
- `gcal_url` built with `quote()` on all user-supplied fields, then `h()` for the HTML attribute.
- ICS uses `_esc()` to escape `,`, `;`, `\n`, `\\` per RFC 5545, then base64-encoded into a
  `data:text/calendar;base64,...` URI — no untrusted bytes reach the HTML attribute unencoded.
- ICS `UID` is deterministic `md5(title + date_prefix)` — re-importing the same event does not
  create duplicates in the calendar app.
- `download` filename sanitized with `re.sub(r'[\\/:"*?<>|]', '', title[:50])` then `h()`.

### Rounds 9–11 — calendar features (commits fd05244 → 6dc1c6a)

#### 1. Apple Calendar midnight crossover bug
**Problem:** `_make_cal_links` set `DTEND` to the same day for events ending at `00:00`
(e.g. Soft Play Party 20:00–00:00 → DTEND was `20260529T000000`, before DTSTART).
Apple Calendar rejected or misrendered the event.
**Fix:** Extracted `_event_cal_data(event)` helper shared by per-event and full-calendar
generation. When `end_t <= start_t` (midnight crossover), `DTEND` date is `start_date + 1 day`.
Same fix applied when the fallback +2h calculation crosses midnight.

#### 2. Unified calendar icon (🍎 → 📅)
All calendar buttons now use 📅 for consistency.

#### 3. Full-calendar subscription — `calendar.ics` + three header buttons
**New:** `_make_full_cal(events)` builds a single VCALENDAR with all events and:
- Writes `calendar.ics` to disk (committed to GitHub Pages via `run.bat`).
- Returns three buttons rendered in the page header:
  - **📅 Apple – הרשם** — `webcal://mim21.github.io/merhav-bari/calendar.ics`
    Opens Apple Calendar subscription dialog; Apple re-fetches ~every 1h automatically.
  - **📅 Google – הרשם** — `https://calendar.google.com/calendar/r/settings/addbyurl?url=…`
    Opens Google's "Add calendar by URL" page; Google re-fetches ~every 12–24h automatically.
  - **⬇ הורד ICS** — data URI download, one-time snapshot for Outlook and other apps.
- `_ics_escape` promoted to module-level function (was nested `_esc`), reused by both
  per-event and full-calendar generation.
- `SITE_URL` and `OUTPUT_CAL` added to the config section.
**Security surface:** All event fields pass through `_ics_escape()`. Full ICS is
base64-encoded into the data URI. The `webcal://` and Google subscribe URLs contain
only the hardcoded `SITE_URL` constant — no user input reaches them.

#### 4. Stable event anchor IDs + back-link in calendar events (commits 1181dd3 → 6dc1c6a)
**Problem:** Per-event calendar links had no way to navigate back to the specific event
card on the webpage. Also, card IDs were positional (`event-0`, `event-1`, …) — adding
a new event with an earlier date would shift all subsequent IDs and break saved links.
**Fix:**
- `_event_slug(event)` produces a stable ID from title + date:
  `event-{slugified-title}-{YYYYMMDD}` (e.g. `event-Soft-Play-Party-אבישג-ואוקאס-20260529`).
  Hebrew characters are kept as-is; non-word characters become `-`.
- Each card `<div>` gets `id="{slug}"`.
- `_event_cal_data(event, event_url='')` accepts an optional back-link URL.
  - Added to VEVENT as `URL:{event_url}` (RFC 5545 — shown as a clickable link in
    Apple Calendar and Google Calendar).
  - Appended to the Google Calendar template `&details=` parameter.
- `_make_cal_links(event, event_url='')` and `_make_full_cal` both pass the slug URL.
**Security surface:** `event_url` is constructed entirely from the hardcoded `SITE_URL`
constant + the output of `_event_slug()`. `_event_slug` only retains `\w` characters
(Unicode word chars) and hyphens — no user-supplied bytes reach the `URL:` ICS property
or the Google Calendar URL unescaped.

### Round 8 — data accuracy fixes (commit bde56df)
These are correctness bugs found by manually cross-checking events.json against the
WhatsApp posts and registration websites.

#### 1. `_needs_enrich` skips events that look complete but have missing fields
**Problem:** `_needs_enrich` only triggers if `price_text`, `start_time_only`, or `city`
are null. Soft Play Party had all three from the chat post, so the enricher never visited
the website. The site had `400₪ לצמד` (couple price) and end time `00:00` — both missed.
**Not yet fixed in pipeline code.** Documented for this review round.
**Suggested fix:** Also return True if `end_time_only` is null and `registration_link` is set.

#### 2. Enricher assigned prices from wrong sublinks
**Problem:** The enricher follows up to 3 sublinks per event. With parallel workers,
prices from one event's sublinks were sometimes written to a different event's object
(race condition in shared output, though each event has its own worker — the prices
were scraped from sublinks that happened to point to another event's registration page).
**Example:** falling2balance.com's sublinks included benoam.wixsite.com links, causing
benoam's prices to appear on the ליפול לאיזון event.
**Not yet fixed.** Documented for this review round.

#### 3. `price_unit: "couple"` set when event accepts both singles and couples
**Problem:** When the enricher found `COUPLE_RE` in scraped text, it set `price_unit: "couple"`
even when the event had both per-person and per-couple pricing (Spring Nights, Soft Play Party).
This caused the card to display "לזוג" after a per-person price.
**Fix:** Use `price_details` for multi-tier pricing; leave `price_unit` null.

#### 4. `event_type` misclassified — "cuddle_party" vs "workshop"
**Problem:** Spring Nights was classified as `cuddle_party` (displays as 🫂 כרבולים).
The WhatsApp post and website clearly describe it as a tantric workshop (מקדש טנטרי).
**Fix:** Changed to `event_type: "workshop"`.

---

## What was NOT fixed and why

### Browser-side JS text cap in `_fetch_text`
Current `[:200_000]` post-hoc slice is sufficient. Added complexity not worth it.

### `_needs_enrich` dead local variable
`price_text` computed but first branch re-fetches. Micro-optimization, no behavior impact.

### Abort `step_html` if `line_to_image` is empty
Optional degraded-publish check. Too complex for a rare edge case.

### `_parse_event_dates` year inference near year boundary
Pre-existing issue (late-December posts may trim early). Not a security concern.

### `style="background:{card_bg}"` without `h()`
`card_bg` only comes from `STATUS_STYLES` hardcoded literals. No user input reaches it.

---

## Critical accuracy requirement (Claude Code instruction)

When extracting events from the WhatsApp chat AND when enriching from websites,
Claude Code MUST cross-check every field against BOTH the original post AND the
registration website. Specific rules:

- **end_time_only**: posts rarely mention it — ALWAYS check the website.
- **Couple vs. per-person pricing**: posts often mention only the individual price.
  The website may also have a couple (לצמד/לזוג) price. Capture BOTH in `price_details`.
- **Early-bird tiers**: use `price_details` with exact cutoff dates so the pipeline
  can auto-mark expired tiers.
- **price_unit**: leave null if both per-person and per-couple prices exist — use `price_details`.
- **event_type**: verify against the event description, not just the event name.
- **Never guess or infer** any field. If you can't verify, leave it null.

**Known failure mode:** If an event already has `price_text + start_time_only + city`
set from the chat post, `_needs_enrich` returns False and the enricher never visits the
website. Additional data on the site (end time, couple pricing) is then silently missed.

---

## What to focus on in this review

- `_ics_escape` is now a module-level function (was a nested `_esc`). Any new exposure?
- `_event_cal_data` is shared by `_make_cal_links` and `_make_full_cal`. If it returns
  corrupt data for one malformed event, does it affect other events in the full calendar?
- `_make_full_cal` embeds ALL events into one data URI. Could a very large event list
  produce a data URI too large for browsers to handle (href length limit)?
- Is the ICS `_ics_escape` complete per RFC 5545? Any missing special characters?
- Could crafted `title`/`description` in `events.json` break out of the
  `data:text/calendar;base64,...` URI or the `download` attribute?
- Should `_needs_enrich` also trigger when `end_time_only` is null (and
  `registration_link` is set)? What are the performance implications?
- Is the enricher sublink race condition a real problem (parallel workers writing to
  different event objects)? Can prices leak between events?
- Are there remaining `events.json`-driven crash paths or HTML injection paths?
- Any issues introduced by the round-9 changes themselves?
- `_event_slug` uses `re.sub(r'[^\w]+', '-', title)` — could two different events
  produce the same slug (collision)? What happens if `title` is empty or all special chars?
- The `URL:` ICS property is built from `SITE_URL + '#' + slug`. `SITE_URL` is a hardcoded
  constant. Does `_event_slug` fully sanitize `title` for use in a URL fragment?
- The back-link URL is also appended to the Google Calendar `&details=` parameter.
  Is `quote()` applied correctly so no URL injection is possible?
