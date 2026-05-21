# tests/

Robustness and security tests for `pipeline.py`.

## Run

```
python -m unittest tests.test_pipeline_robustness -v
```

## Coverage (90 tests)

| Class | What it checks |
|---|---|
| `TestStr` / `TestList` | Boundary coercions — non-strings become `''`, non-lists become `[]` |
| `TestEventsFromJson` | Top-level list, dict with/without `events` key, garbage input |
| `TestSafeUrl` | http/https allowed; `javascript:`, `data:`, `vbscript:` blocked; case-bypass blocked |
| `TestImgUri` | Extension allowlist, path traversal guard, size cap, nonexistent file |
| `TestEventSlug` | Hebrew titles, punctuation-only → `untitled`, time suffix, no whitespace/quotes |
| `TestFormatDate` | ISO dates, `event_start` fallback, invalid/non-string input |
| `TestIcsEscape` | CRLF injection, C0 control stripping, backslash/comma/semicolon escaping, Unicode pass-through |
| `TestRenderPriceTier` | Expired tier strikethrough, HTML escaping, non-string input |
| `TestEventCalData` | Valid/invalid dates, midnight crossover, UID stability/uniqueness, URL percent-encoding, ICS/HTML injection |
| `TestMakeFullCal` | ICS structure (`BEGIN:VCALENDAR` / `END:VCALENDAR`), adversarial titles, subscribe URLs use hardcoded `SITE_URL` |
| `TestMakeCalLinks` | Both buttons present, download filename safe chars, HTML attribute injection blocked |
| `TestFindImage` | Forward-only 0–3 line window, no backwards search |
| `TestCollectUrls` | `_str()` on `source_excerpt`, dangerous schemes excluded, max-URL limit |
| `TestEndToEndSmoke` | Adversarial event dicts fed into `_render_event` do not crash |

## Design notes

- Tests import only from `pipeline` (no mocking of external calls — `step_enrich` is not tested here).
- HTML injection assertions use `html.parser` structural parsing, not substring search.
- ICS injection assertions count `\r\n`-split lines; they do not use substring search for injected property names.
- The test file is self-contained: no fixtures, no temp files (except `TestImgUri` which creates a 1×1 JPEG in memory via `tempfile`).
