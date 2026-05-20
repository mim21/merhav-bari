# Security Review — pipeline.py (Rounds 7–13)

Review this file: https://github.com/mim21/merhav-bari/blob/b7b20b9/pipeline.py

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

---

### Round 13 — ICS hardening, slug/UID collision fix, scroll overhaul (commit b7b20b9)

#### Fixed — real bugs:

**`_ics_escape` missing CR handling:**
Added `.replace('\r\n', '\n').replace('\r', '\n')` before the existing escapes.
Raw carriage returns in `description` or `title` could produce spurious ICS content lines.

**`URL:` property not ICS-escaped:**
Changed `vevent.append('URL:' + event_url)` → `vevent.append(_ics_fold('URL:' + _ics_escape(event_url)))`.
All other VEVENT text properties were already escaped; this was an inconsistency.

**Slug/UID collision (same title + same date):**
- `UID` now uses full `gs` (date + time) instead of `gs[:8]` (date only). Two events
  with the same title on the same date but different start times now get distinct UIDs —
  calendar apps no longer merge/overwrite them.
- `_event_slug` now appends start time: `event-{slug}-{YYYYMMDD}-{HHMM}` for timed
  events, `event-{slug}-{YYYYMMDD}` for all-day events.

**Empty slug from whitespace/special-char title:**
`re.sub(...).strip('-') or 'untitled'` — prevents `event--YYYYMMDD` from two whitespace-titled events on the same date colliding.

**`step_enrich()` crash on non-dict/non-list JSON:**
`data["events"] = events` raised `TypeError` if `data` was `null`, a number, or a string.
Fixed: `if not isinstance(data, dict): data = {}` before the assignment.

#### Fixed — good improvements:

**ICS line folding (`_ics_fold`):**
New helper folds lines longer than 75 UTF-8 octets per RFC 5545 (splitting on character
boundaries, not byte boundaries). Applied to every VEVENT property line and at the join
points in both `_make_cal_links` and `_make_full_cal`. Without this, strict ICS parsers
(and some Outlook versions) reject the entire VCALENDAR when one Hebrew description
exceeds 38 characters.

**ICS download button replaced with file link:**
`data:text/calendar;base64,{full_ics}` in the header replaced with
`<a href="calendar.ics" download="מרחב-בריא.ics">`. Always fresh, no page bloat.
Per-event Apple buttons keep their `data:` URIs (no file to link to for individual events).

**`webcal://` URL wrapped in `h()`:**
`href="{webcal_url}"` → `href="{h(webcal_url)}"` for parity with the other two header buttons.

**iPhone anchor scroll overhauled:**
Root cause: `scrollIntoView` fired at 50 ms while base64 images were still laying out,
giving a wrong scroll target; browser scroll restoration then overrode the fix on
back-forward navigation.
New script before `</body>`:
- `history.scrollRestoration = 'manual'` — prevents browser from restoring old position
- `decodeURIComponent(location.hash.slice(1))` — handles percent-encoded hashes
- `getBoundingClientRect().top + pageYOffset - 12` + `window.scrollTo` — more reliable
  than `scrollIntoView` in Mobile Safari
- Retries at `rAF×2`, 250 ms, 750 ms — catches post-image-load layout shifts
- Listens to `DOMContentLoaded`, `load`, `pageshow` (back-forward cache), `hashchange`
- `.card-img { min-height: 180px }` reserves layout space before images decode,
  reducing layout shift magnitude

#### Not fixed (skipped):
- SHA-256 digest suffix on slugs — adding start time already eliminates collisions
- `quote()` on slug in URL fragment — slug contains only `\w` + hyphens, no encoding needed
- LRM/RLM bidi marks in slug — theoretical edge case, not seen in practice
- Enricher sublink cross-contamination — documented; not a security risk

#### Bug introduced by Round 13 — double ICS fold (fixed in commit 1349b22):
`_event_cal_data` was already calling `_ics_fold()` on each property line before
returning the `vevent` list. Then both `_make_cal_links` and `_make_full_cal` called
`_ics_fold()` again on every line at join time. A folded line containing embedded
`\r\n ` sequences was passed through `_ics_fold` a second time, corrupting the byte
boundary calculations. Result: Apple Calendar parsed only the first VEVENT and silently
discarded the rest of the VCALENDAR.
**Fix:** Removed `_ics_fold` calls from inside `_event_cal_data`. Folding now happens
exactly once — at the join points in `_make_cal_links` and `_make_full_cal`.

**Status: Apple Calendar subscription still shows only 1 event after the fix.**
The `calendar.ics` file on disk is confirmed correct (10 `BEGIN:VEVENT` blocks).
Apple Calendar's live subscription may be showing a cached version — it re-fetches
only ~every 1h. Manual refresh: Calendar → right-click subscription → Refresh.
If the problem persists after a forced refresh, the remaining suspected causes are:
- `_ics_fold` still produces lines that confuse Apple's ICS parser (e.g. folding
  inside a multi-byte Hebrew character boundary despite the guard).
- A `DTSTAMP` line or `UID` line that is too long and gets folded unexpectedly.
- Apple Calendar rejecting the `URL:` property on VEVENT (non-standard extension
  in some older iOS versions — try removing it and testing).
- The `X-WR-CALNAME` or `X-WR-TIMEZONE` header lines containing characters that
  confuse the parser before the first VEVENT.
- Encoding issue: `calendar.ics` is written as UTF-8 but lacks a BOM or
  `CHARSET` declaration; some Apple Calendar versions expect a BOM for UTF-8 ICS.

---

## What was NOT fixed and why

| Issue | Reason not fixed |
|---|---|
| `_needs_enrich` gap (misses `end_time_only` when `price_text + city` are already set) | Would force website visit for most events; performance trade-off |
| Enricher sublink cross-contamination | Complex to fix; not a security risk (enricher only overwrites null fields) |
| `[:200_000]` post-hoc text slice in `_fetch_text` | Sufficient; added complexity not worth it |
| `_needs_enrich` dead local variable | Micro-optimization, no behavior impact |
| `_parse_event_dates` year inference near year boundary | Pre-existing; not a security concern |
| `style="background:{card_bg}"` without `h()` | `card_bg` only ever comes from `STATUS_STYLES` hardcoded dict |

---

## What to focus on in this review

**ICS / calendar — Apple Calendar subscription shows only 1 event (unresolved):**
- `calendar.ics` on disk has all 10 events confirmed. Apple Calendar subscription
  still shows only 1 after forced refresh. Why?
- Could `_ics_fold` still be producing malformed lines (e.g. a guard condition miss
  on multi-byte UTF-8 sequences, leaving a continuation byte at the split point)?
- Could Apple Calendar be rejecting the non-standard `URL:` VEVENT property and
  stopping parse at the first event that has one?
- Could `X-WR-CALNAME:מרחב בריא – אירועים` (Hebrew + em-dash in a header line)
  confuse the parser before the first VEVENT is reached?
- Is a UTF-8 BOM needed in `calendar.ics` for Apple Calendar to correctly parse Hebrew?
- Are there remaining ICS injection paths not covered by `_ics_escape` + `_ics_fold`?
- Could a crafted `title` or `description` corrupt the `download` attribute on the per-event Apple button?

**Slug / URL:**
- Are there Unicode `\w` characters retained by `_event_slug` that are unsafe in an HTML `id` attribute or URL fragment?
- Slug collision is now `title + date + start_time`. Can two events still collide (e.g. all-day events with same title and date)?

**Enricher:**
- Is the sublink cross-contamination a real security risk, or data accuracy only? Could scraped content from the wrong page cause XSS or injection in the rendered HTML?
- Any remaining `events.json`-driven crash paths not covered by `_str()` / `h()` / `_safe_url()`?

**General:**
- Any issues introduced by the Round 13 changes?
