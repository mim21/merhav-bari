# Engineering Review — pipeline.py (Round 17)

Review this file: https://github.com/mim21/merhav-bari/blob/HEAD/pipeline.py

You are reviewing `pipeline.py` in the **merhav-bari** project.
This is a WhatsApp chat → JSON → HTML event pipeline that publishes to GitHub Pages at
`https://mim21.github.io/merhav-bari/`.

The file reads `events.json` **written by an AI agent** (not sanitized) and generates a
self-contained `index.html` with base64-embedded images, plus a `calendar.ics` file.
Treat `events.json` as fully untrusted input.

---

## Confirmed safe — do not re-raise (Rounds 1–17)

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
- `_event_slug` — retains only `\w` + hyphens; `or 'untitled'` guards empty result (Round 17)
- `step_enrich()` — non-dict/non-list JSON handled before `data["events"]` assignment
- `_ics_escape` — CR normalization + C0 control char stripping before TEXT escaping (Rounds 16–17)
- `URL:` VEVENT — `quote()` applied so non-ASCII fragment chars are percent-encoded (Round 16)
- Google Calendar subscribe URL — uses `webcal://` in `cid=` parameter (Rounds 15–16)
- `_collect_urls` — `_str()` applied to `source_excerpt` before regex (Round 17)
- Full-calendar download button — `href="calendar.ics"` file link, not data URI (Round 17)
- Copy-URL button — hardcoded `SITE_URL + '/calendar.ics'`, clipboard JS only (Round 17)
- `UID` in VEVENT — uses full `gs` (datetime), not `gs[:8]` (date only) (Round 17)

---

## History of notable changes

| Round | Change |
|---|---|
| 12–14 | Anchor scroll JS, ICS line folding, C0 stripping, `_ics_uri` — **reverted** (double-fold bug broke Apple Calendar) |
| 15 | Google Calendar subscribe URL: `addbyurl?url=` → `cid=` |
| 16 | `_ics_escape` CR normalization; `URL:` percent-encoding; `step_enrich` non-dict guard; `cid=webcal://`; page version footer |
| 17 | C0 stripping in `_ics_escape`; UID full datetime; `or 'untitled'` in slug; `_str()` on `source_excerpt`; download button → `calendar.ics`; copy-URL button added |

---

## What was NOT fixed and why

| Issue | Reason not fixed |
|---|---|
| `_needs_enrich` gap — misses `end_time_only` when `price_text + city` already set | Performance trade-off |
| Enricher sublink cross-contamination | Data accuracy only; no XSS path. Could restrict to canonical URL only in future |
| ICS line folding (RFC 5545, 75 byte limit) | Reverted twice — double-fold bug corrupted Apple Calendar; audience (iOS/Android) tolerates unfolded lines |
| Slug/UID SHA digest + start time | Complexity > benefit for current data (events rarely share title + date) |
| Google Calendar subscribe on iPhone | Google Calendar iOS app does not support URL-based subscriptions at all — product limitation. Workaround: "📋 העתק URL" copy button added |
| Adversarial test suite | Valid long-term; out of scope |

---

## What to focus on in this review

**ICS correctness:**
- `_ics_escape` now strips C0 controls. Any remaining ICS injection paths?
- `UID` now uses full datetime `gs`. Could two events still collide (same title + same
  datetime but different location)?

**HTML / security:**
- Copy-URL button: `onclick` embeds hardcoded `SITE_URL + '/calendar.ics'` via Python
  f-string. Is there any injection surface in this pattern?
- Any new injection paths introduced in Round 17?

**Enricher:**
- Is sublink cross-contamination data accuracy only, or could scraped content
  from the wrong page cause injection in rendered HTML?

**General:**
- Anything introduced by Rounds 16–17 worth flagging?
