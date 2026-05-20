# Security Review — pipeline.py (Round 14)

Review this file: https://github.com/mim21/merhav-bari/blob/e14c796/pipeline.py

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
- `gcal_url` — all user fields through `quote()`, attribute through `h()`
- Per-event ICS: base64-encoded data URI, no untrusted bytes reach HTML unencoded
- `download` filename: `re.sub(r'[\\/:"*?<>|]', '', title[:50])` then `h()`
- `webcal://` and Google subscribe URLs built from hardcoded `SITE_URL` only
- `_event_slug` — retains only `\w` + hyphens; `or 'untitled'` guards empty title
- Scroll JS — `location.hash.slice(1)` → `getElementById()` is safe (no code execution)
- `step_enrich()` — non-dict/non-list JSON handled before `data["events"]` assignment

---

## Recent changes (Rounds 12–14) — focus of this review

### Round 12 — anchor scroll (commit 90d5de2)
- `scroll-margin-top: 12px` on `.card`
- JS before `</body>`: `history.scrollRestoration = 'manual'`, `decodeURIComponent(hash)`,
  `getBoundingClientRect` + `window.scrollTo`, retries at rAF×2 / 250ms / 750ms,
  listens to `DOMContentLoaded`, `load`, `pageshow`, `hashchange`
- `.card-img { min-height: 180px }` reserves layout space before images decode

### Round 13 — ICS hardening + slug/UID collision fix (commits 1349b22 → b7b20b9)
- `_ics_escape` — added `.replace('\r\n','\n').replace('\r','\n')` before TEXT escaping
- `_ics_fold(line)` — new helper, folds lines > 75 UTF-8 octets per RFC 5545, splits
  on character boundaries (backs off if `b[end] & 0xC0 == 0x80`)
- `URL:` VEVENT property — was `_ics_escape(event_url)`; corrected in Round 14
- UID — now uses full `gs` (date+time) instead of `gs[:8]` (date only)
- `_event_slug` — appends start time (`-HHMM`) for timed events; falls back to `'untitled'`
- ICS download button — replaced `data:` URI with `<a href="calendar.ics">`
- `webcal://` wrapped in `h()`
- **Bug introduced + fixed:** double `_ics_fold` — `_event_cal_data` was folding property
  lines, then `_make_cal_links` / `_make_full_cal` folded them again. Apple Calendar
  parsed only the first VEVENT and silently discarded the rest. Fixed by removing fold
  calls from `_event_cal_data`; folding happens exactly once at join time.

### Round 14 — ICS URI fix + control char stripping (commit cdfa3a4)
- `_ICS_CTRL_RE = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')` — C0 control
  characters stripped in `_ics_escape` before TEXT escaping
- `_ics_uri(url)` — new helper for URI-type ICS properties: strips CR/LF only, no
  backslash-escaping (TEXT escaping is invalid for URI values per RFC 5545)
- `URL:` VEVENT property now uses `_ics_uri(event_url)` instead of `_ics_escape`

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

---

## What to focus on in this review

**ICS / calendar:**
- `_ics_fold` backs off when `b[end] & 0xC0 == 0x80`. Could this produce a zero-length
  chunk (`end == i`) causing an infinite loop on a pathological input?
- Any remaining ICS injection paths after `_ics_escape` + `_ics_uri` + C0 stripping?
- Could a very long `UID` or `DTSTAMP` line (which are not escaped/folded) cause issues?

**Slug / HTML:**
- Are there Unicode `\w` characters retained by `_event_slug` that are unsafe in an
  HTML `id` attribute or URL fragment?
- Any remaining `events.json`-driven crash paths or HTML injection paths?

**Enricher:**
- Is sublink cross-contamination data accuracy only, or could scraped content from
  the wrong page cause XSS/injection in rendered HTML?

**General:**
- Any issues introduced by Rounds 12–14?
