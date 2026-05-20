# Security Review — pipeline.py (Rounds 7–12)

Review this file: https://github.com/mim21/merhav-bari/blob/90d5de2/pipeline.py

You are reviewing `pipeline.py` in the **merhav-bari** project.
This is a WhatsApp chat → JSON → HTML event pipeline that publishes to GitHub Pages at
`https://mim21.github.io/merhav-bari/`.

The file reads `events.json` **written by an AI agent** (not sanitized) and generates a
self-contained `index.html` with base64-embedded images, plus a `calendar.ics` file.
Treat `events.json` as fully untrusted input.

---

## Baseline — what was reviewed in Rounds 1–6 (already approved)

- `_events_from_json(data)` — safe extraction from any JSON shape
- `_str(v)`, `_list(v)` — applied at every JSON boundary to prevent TypeErrors
- `_find_image` — window narrowed to forward-only 0–3 lines
- `_format_date` — falls back gracefully when `date_only` is null
- `_safe_url()` — validates http/https scheme + netloc before use
- Atomic writes via `.tmp` + `os.replace()`
- Phone contact `name` coerced with `_str()`

---

## Changes in Rounds 7–11 (this review scope)

### Round 7 — per-event calendar links (`_make_cal_links`)

Each event card gained two calendar buttons:
- **📅 Google** — opens `calendar.google.com/calendar/render?action=TEMPLATE&...`
- **📅 Apple** — `data:text/calendar;base64,...` `.ics` download

**Implementation:**
- `gcal_url` built with `urllib.parse.quote()` on all user-supplied fields; HTML attribute wrapped with `h()` (`html.escape`).
- ICS content escaped by `_esc()` (nested helper at the time): escapes `\`, `\n`, `,`, `;` per RFC 5545. Then base64-encoded into the data URI — no untrusted bytes reach the HTML attribute unencoded.
- ICS `UID` is deterministic `md5(title + date_prefix)` — re-importing the same event does not create duplicates.
- `download` filename sanitized: `re.sub(r'[\\/:"*?<>|]', '', title[:50])` then `h()`.

---

### Round 8 — data accuracy fixes

Correctness bugs found by cross-checking `events.json` against WhatsApp posts and registration websites.

#### Fixed:
- **`price_unit` over-set**: enricher was setting `price_unit: "couple"` even when the event had both per-person and per-couple pricing. Fix: use `price_details` array for multi-tier; leave `price_unit` null.
- **`event_type` misclassified**: Spring Nights was `cuddle_party`; corrected to `workshop` based on event description.

#### Not fixed (documented):
- **`_needs_enrich` gap**: only triggers on null `price_text` / `start_time_only` / `city`. If all three are set from the chat post, the enricher never visits the website — silently missing end time, couple pricing, etc.
- **Enricher sublink cross-contamination**: enricher follows up to 3 sublinks per event. If a sublink from event A's registration page links to event B's page, event B's prices may be written to event A's object. Not yet fixed.

---

### Round 9 — midnight crossover fix + full-calendar subscription

#### Midnight crossover fix:
**Problem:** Events ending at `00:00` (e.g. 20:00–00:00) produced `DTEND = same day T000000`,
which is before `DTSTART`. Apple Calendar rejected or misrendered these.

**Fix:** Extracted `_event_cal_data(event)` shared helper. When `end_t <= start_t`,
`DTEND` date = `start_date + timedelta(days=1)`. Same fix applied to the fallback +2h
calculation when `end_time_only` is missing.

#### Unified icon:
All calendar buttons changed from 🍎 to 📅.

#### Full-calendar subscription (`_make_full_cal`):
Builds a single `VCALENDAR` with all events and writes `calendar.ics` to disk
(published to GitHub Pages). Returns three HTML buttons in the page header:
- **📅 Apple – הרשם** → `webcal://mim21.github.io/merhav-bari/calendar.ics`
  (live subscription, Apple re-fetches ~every 1h automatically)
- **📅 Google – הרשם** → `https://calendar.google.com/calendar/r/settings/addbyurl?url=…`
  (live subscription, Google re-fetches ~every 12–24h automatically)
- **⬇ הורד ICS** → `data:text/calendar;base64,...` one-time snapshot download

`_ics_escape()` promoted to **module-level** function (was a nested `_esc` inside
`_make_cal_links`). `SITE_URL` and `OUTPUT_CAL` added to the config section.

**Security surface:**
- All event fields pass through `_ics_escape()`.
- Full ICS is base64-encoded into the data URI.
- `webcal://` and Google subscribe URLs are built from the hardcoded `SITE_URL` constant
  only — no user input reaches them.

---

### Round 10 — stable event anchor IDs + back-link in calendar events

#### Stable slugs:
**Problem:** Card IDs were positional (`event-0`, `event-1`, …) — inserting an event with
an earlier date shifts all subsequent IDs, breaking any saved calendar deep-links.

**Fix:** `_event_slug(event)` generates a stable ID from title + date:
```python
slug = re.sub(r'[^\w]+', '-', title, flags=re.UNICODE).strip('-')
return f'event-{slug}-{YYYYMMDD}'
# e.g. event-Soft-Play-Party-אבישג-ואוקאס-20260529
```
Each card `<div>` gets `id="{slug}"`.

#### Back-link in calendar events:
`_event_cal_data(event, event_url='')` now accepts an optional back-link URL:
- Added to VEVENT as `URL:{event_url}` (RFC 5545 — shown as a clickable link in
  Apple Calendar and Google Calendar).
- Also appended to the Google Calendar template `&details=` parameter (Google's
  quick-add template has no dedicated URL field).

`_make_cal_links(event, event_url='')` and `_make_full_cal` both compute and pass
`f'{SITE_URL}/#{_event_slug(event)}'`.

**Security surface:**
- `event_url` is constructed entirely from the hardcoded `SITE_URL` constant +
  `_event_slug()` output — no user input flows in.
- `_event_slug` retains only `\w` Unicode chars and hyphens; all other characters
  become `-`. No user-supplied bytes reach the `URL:` ICS property or the
  Google Calendar `&details=` URL without this transformation.

---

### Round 12 — anchor scroll fix (commit 90d5de2)

**Problem:** On mobile browsers, navigating to a deep link (`#event-slug`) positioned the
event card in the center of the viewport instead of the top, because the browser's default
fragment scroll fires before the page fully settles.

**Fix:**
- CSS `scroll-margin-top: 12px` added to `.card` — standard property that reserves
  12 px of space above the element when it is the anchor target.
- Inline JS added before `</body>`:
  ```javascript
  if (location.hash) {
    var el = document.getElementById(location.hash.slice(1));
    if (el) setTimeout(function() { el.scrollIntoView({block: 'start', behavior: 'instant'}); }, 50);
  }
  ```
  The 50 ms delay lets the page finish rendering before the explicit `scrollIntoView`
  overrides whatever position the browser defaulted to.

**Security surface:**
- `location.hash.slice(1)` is passed to `getElementById()`. `getElementById` does not
  execute code; it only does a DOM lookup. An attacker-controlled hash value cannot cause
  XSS — at worst it finds no element and silently does nothing.
- No user-supplied data from `events.json` flows into this script.

**Status: NOT fully working — still broken on iPhone (Mobile Safari).**
The card still does not scroll to the top of the viewport after the fix.
Suspected causes (not yet investigated):
- Mobile Safari may ignore `scrollIntoView` on initial page load if the call happens
  while the browser is still processing the native fragment scroll.
- 50 ms may be too short — Safari renders and lays out large base64-embedded images
  lazily; the card's final position may not be known yet at 50 ms.
- `behavior: 'instant'` may be silently ignored in some Safari versions.
- The `webcal://` / calendar app → browser handoff may restore scroll position from
  a previous visit before the JS fires, overriding the `scrollIntoView` call.
- `scroll-margin-top` browser support on Mobile Safari (check if a polyfill is needed).

---

## What was NOT fixed and why

| Issue | Reason not fixed |
|---|---|
| `_needs_enrich` gap (misses `end_time_only` when `price_text + city` are already set) | Would force website visit for most events; performance trade-off |
| Enricher sublink cross-contamination | Complex to fix; no security risk (enricher only overwrites null fields) |
| `[:200_000]` post-hoc text slice in `_fetch_text` | Sufficient; added complexity not worth it |
| `_needs_enrich` dead local variable | Micro-optimization, no behavior impact |
| `_parse_event_dates` year inference near year boundary | Pre-existing; not a security concern |
| `style="background:{card_bg}"` without `h()` | `card_bg` only ever comes from `STATUS_STYLES` hardcoded dict |

---

## What to focus on in this review

**Calendar / ICS security:**
- Is `_ics_escape()` complete per RFC 5545? Are there special characters that should
  be escaped but aren't (e.g. COLON, line folding for long values)?
- Could crafted `title` or `description` in `events.json` break out of the
  `data:text/calendar;base64,...` URI or corrupt the `download` attribute?
- `_event_cal_data` is shared by `_make_cal_links` and `_make_full_cal`. If it
  produces corrupt output for one malformed event, does it affect other events in
  the full calendar?
- `_make_full_cal` embeds ALL events into one data URI. Could a large event list
  produce a data URI that exceeds browser `href` length limits?

**Slug / back-link security:**
- `_event_slug` uses `re.sub(r'[^\w]+', '-', title, flags=re.UNICODE)`. Could two
  different events produce the same slug (collision)?
- What happens if `title` is empty, all-whitespace, or consists entirely of
  special characters?
- Are there Unicode `\w` characters (retained by the slug regex) that are unsafe in
  a URL fragment without percent-encoding?
- The back-link URL is appended to `&details=` via `quote()`. Is this applied
  correctly so no URL injection is possible?

**Enricher:**
- Is the sublink cross-contamination (Round 8) a real security risk, or only a data
  accuracy issue? Could scraped content from the wrong page cause XSS or injection?
- Are there remaining `events.json`-driven crash paths or HTML injection paths not
  covered by the existing `_str()` / `h()` / `_safe_url()` guards?

**Anchor scroll (Round 12 — still broken on iPhone / Mobile Safari):**
- The current fix (`scroll-margin-top: 12px` + 50 ms `setTimeout` + `scrollIntoView({block:'start', behavior:'instant'})`)
  does NOT reliably scroll to the card top on iPhone. Why not, and what is the correct fix?
- Specifically: does Mobile Safari suppress `scrollIntoView` on initial load? Is 50 ms
  too short given large base64 images in the page? Does `behavior: 'instant'` work in
  all Safari versions? Could the calendar-app → browser handoff restore a prior scroll
  position after the JS fires?
- What is the most reliable cross-browser approach for this pattern (deep-link from
  external app → specific card at top of viewport on mobile)?
- `location.hash.slice(1)` → `getElementById()`: safe against DOM-clobbering?

**General:**
- Any other issues introduced by the Round 9–12 changes?
