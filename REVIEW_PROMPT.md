# Engineering Review — pipeline.py (Round 16)

Review this file: https://github.com/mim21/merhav-bari/blob/HEAD/pipeline.py

You are reviewing `pipeline.py` in the **merhav-bari** project.
This is a WhatsApp chat → JSON → HTML event pipeline that publishes to GitHub Pages at
`https://mim21.github.io/merhav-bari/`.

The file reads `events.json` **written by an AI agent** (not sanitized) and generates a
self-contained `index.html` with base64-embedded images, plus a `calendar.ics` file.
Treat `events.json` as fully untrusted input.

---

## Confirmed safe — do not re-raise (Rounds 1–15)

- `_events_from_json`, `_str`, `_list` — safe extraction at every JSON boundary
- `_safe_url()` — http/https scheme + netloc validation
- `_find_image` — forward-only 0–3 line window
- `_img_uri` — path traversal guard, size cap, allowlist of extensions
- Atomic writes via `.tmp` + `os.replace()`
- All HTML fields go through `h()` (`html.escape`)
- `registration_link` goes through `_safe_url()` + `h()`
- WhatsApp contact links (`wa.me/...`) are digit-derived, not user-input URLs
- `gcal_url` (per-event) — all user fields through `quote()`, attribute through `h()`
- Per-event ICS: base64-encoded data URI, no untrusted bytes reach HTML unencoded
- `download` filename: `re.sub(r'[\\/:"*?<>|]', '', title[:50])` then `h()`
- `webcal://` and Google subscribe URLs built from hardcoded `SITE_URL` only
- `_event_slug` — retains only `\w` + hyphens; `or 'untitled'` guards empty title
- `step_enrich()` — non-dict/non-list JSON handled before `data["events"]` assignment (Round 16)
- `_ics_escape` — CR normalization before TEXT escaping (Round 16)
- `URL:` VEVENT — `quote()` applied so non-ASCII fragment chars are percent-encoded (Round 16)
- Google Calendar subscribe URL — uses `webcal://` in `cid=` parameter (Rounds 15–16)

---

## History of notable changes

| Round | Change |
|---|---|
| 12–14 | Anchor scroll JS, ICS line folding, C0 stripping, `_ics_uri` — **reverted** (double-fold bug broke Apple Calendar) |
| 15 | Google Calendar subscribe URL: `addbyurl?url=` → `cid=` |
| 16 | `_ics_escape` CR normalization; `URL:` VEVENT percent-encoding; `step_enrich` non-dict guard; `cid=` `webcal://` fix |

---

## What was NOT fixed and why

| Issue | Reason not fixed |
|---|---|
| `_needs_enrich` gap — misses `end_time_only` when `price_text + city` already set | Performance trade-off |
| Enricher sublink cross-contamination | Data accuracy only; no XSS path |
| `[:200_000]` post-hoc slice in `_fetch_text` | Sufficient mitigation |
| `_parse_event_dates` year boundary inference | Not security-relevant |
| `style="background:{card_bg}"` without `h()` | `card_bg` is from `STATUS_STYLES` hardcoded dict only |
| Slug/UID uses date only, not time | Same-title same-date events are rare; start time added to slug in Round 13 and reverted |
| ICS line folding (RFC 5545, 75 byte limit) | Reverted — double-fold bug corrupted Apple Calendar |
| Google Calendar subscribe on iPhone | Google Calendar iOS app does not support URL-based subscriptions at all — product limitation, not fixable in code |
| Adversarial test suite | Valid long-term; out of scope |

---

## What to focus on in this review

**ICS correctness:**
- `_ics_escape` now normalizes CR, but still has no C0 control char stripping
  (`\x00–\x08`, `\x0b`, `\x0c`, `\x0e–\x1f`, `\x7f`). Is this a real risk?
- ICS line folding is still absent. Long Hebrew SUMMARY/DESCRIPTION lines can exceed
  75 UTF-8 octets. Are any known calendar clients actually rejecting these?
- `UID` still uses `md5(title + gs[:8])` — date only, not datetime. Could this cause
  calendar client merge/overwrite issues in practice?

**HTML / security:**
- Any new injection paths introduced in Round 16?
- Are there Unicode `\w` characters in `_event_slug` that are unsafe in HTML `id`
  attributes or URL fragments even after percent-encoding in `URL:` VEVENT?

**Enricher:**
- Is sublink cross-contamination data accuracy only, or could scraped content
  from the wrong page cause injection in rendered HTML?

**Google Calendar on iPhone:**
- The "📅 Google – הרשם" button now uses `https://calendar.google.com/calendar/r?cid=webcal://...`
  On iPhone, this opens Safari (the Google Calendar web app) instead of the native Google Calendar app.
  Is there a URL scheme or deep-link format that triggers the Google Calendar iOS app directly?
  Is there a `googlegcal://` or `x-apple-calevent://` equivalent for Google Calendar?
  Should the button be removed or replaced with guidance for iPhone users?

**General:**
- Anything introduced by Round 16 (CR normalization, percent-encoding, non-dict guard)?
