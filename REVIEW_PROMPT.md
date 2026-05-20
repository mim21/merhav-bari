# Security Review — pipeline.py (Round 15)

Review this file: https://github.com/mim21/merhav-bari/blob/6d85a09/pipeline.py

You are reviewing `pipeline.py` in the **merhav-bari** project.
This is a WhatsApp chat → JSON → HTML event pipeline that publishes to GitHub Pages at
`https://mim21.github.io/merhav-bari/`.

The file reads `events.json` **written by an AI agent** (not sanitized) and generates a
self-contained `index.html` with base64-embedded images, plus a `calendar.ics` file.
Treat `events.json` as fully untrusted input.

---

## Already reviewed and approved (Rounds 1–11)

The following are confirmed safe — do not re-raise them:

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
- `step_enrich()` — non-dict/non-list JSON handled before `data["events"]` assignment

---

## Round 12–14 note — changes were reverted

Rounds 12–14 introduced anchor scroll JS, ICS line folding (`_ics_fold`), C0 control
char stripping, and `_ics_uri`. All of these changes were **reverted** after debugging
a double-fold bug that caused Apple Calendar to show only 1 event. Current code is back
to the pre-Round-12 state (stable slugs and `URL:` VEVENT property are still present).

---

## Round 15 — Google Calendar subscribe URL fix (commit 6d85a09)

**Problem:** The "📅 Google – הרשם" subscription button used:
```
https://calendar.google.com/calendar/r/settings/addbyurl?url=<ics_url>
```
This is a desktop web settings page. On iPhone, the Google Calendar app opens but
shows no subscription prompt — events never appear.

**Fix:** Changed to the `cid=` format:
```
https://calendar.google.com/calendar/r?cid=<ics_url>
```
The `cid=` parameter is the canonical deep-link format recognized by both the Google
Calendar web interface and the iOS/Android apps as a "subscribe to this calendar" action.

---

## What was NOT fixed and why

| Issue | Reason not fixed |
|---|---|
| `_needs_enrich` gap — misses `end_time_only` when `price_text + city` already set | Would force website visit for most events; performance trade-off |
| Enricher sublink cross-contamination | Not a security risk; enricher only overwrites null fields |
| `[:200_000]` post-hoc slice in `_fetch_text` | Sufficient mitigation |
| `_parse_event_dates` year boundary inference | Pre-existing; not security-relevant |
| `style="background:{card_bg}"` without `h()` | `card_bg` is from `STATUS_STYLES` hardcoded dict only |
| Slug/UID digest (sha256 of all identity fields) | Start time already distinguishes same-title/date events |
| `quote()` on URL fragment slug | Slug is `\w` + hyphens only — no encoding needed |
| Adversarial test suite | Valid long-term; out of scope |
| ICS line folding (RFC 5545, 75 byte limit) | Reverted — double-fold bug corrupted Apple Calendar |

---

## What to focus on in this review

**Google Calendar subscribe URL (Round 15):**
- Is `https://calendar.google.com/calendar/r?cid=<url>` the correct canonical format
  for triggering a Google Calendar subscription on iOS? Any known issues with this format?
- The `<url>` value is `quote(SITE_URL + '/calendar.ics')` where `SITE_URL` is a
  hardcoded `https://` string — is there any injection surface here?

**ICS / calendar (pre-Round-12 state):**
- `_ics_escape` does TEXT escaping (`\`, `\n`, `,`, `;`) but no C0 control char stripping
  and no line folding. Is this a correctness or security risk for the current usage?
- `URL:` VEVENT property uses `_ics_escape(event_url)` — TEXT escaping is incorrect for
  URI values per RFC 5545 (URIs should not have backslash-escaping). Is this a real
  problem in practice, or do calendar clients tolerate it?

**Slug / HTML:**
- Are there Unicode `\w` characters retained by `_event_slug` that are unsafe in an
  HTML `id` attribute or URL fragment?
- Any remaining `events.json`-driven crash paths or HTML injection paths?

**Enricher:**
- Is sublink cross-contamination data accuracy only, or could scraped content from
  the wrong page cause XSS/injection in rendered HTML?
