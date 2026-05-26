# AI Review Prompt — merhav-bari

Use this prompt with Claude Opus / GPT thinking. The repo is public — fetch each URL before answering.

---

Please do a security and engineering review of the following Telegram bot project.

**First, fetch and read the files below before answering. Do not answer based on memory or previous versions — verify every claim against the actual fetched code, with line numbers.**

**Commit:** https://github.com/mim21/merhav-bari/tree/d8692b9

| File | URL |
|------|-----|
| `bot.py` | https://raw.githubusercontent.com/mim21/merhav-bari/d8692b9/bot.py |
| `pipeline.py` | https://raw.githubusercontent.com/mim21/merhav-bari/d8692b9/pipeline.py |
| `tests/test_bot_robustness.py` | https://raw.githubusercontent.com/mim21/merhav-bari/d8692b9/tests/test_bot_robustness.py |
| `tests/test_pipeline_robustness.py` | https://raw.githubusercontent.com/mim21/merhav-bari/d8692b9/tests/test_pipeline_robustness.py |

**Project description:**

The bot runs locally on Windows (`python bot.py`). Two trigger paths:
1. **ZIP upload**: user sends a WhatsApp ZIP export via Telegram → bot extracts `_chat.txt`, saves it, calls `claude -p` (Claude Code CLI) to read the chat and write `events.json` → runs `pipeline.py --push` to generate a static HTML page and push to GitHub Pages.
2. **`/run` command**: bot auto-detects the latest `_chat.txt` from existing ZIPs or unzipped folder on disk (by comparing last message timestamps), then runs the same `claude -p` pipeline.

`python bot.py --run` is the CLI equivalent of `/run` — usable from terminal without the bot.

**Architecture notes:**
- `bot.py` and `pipeline.py` both live in the same repo (`merhav-bari`). `bot.py` triggers the pipeline; `pipeline.py` generates HTML and pushes to GitHub Pages.
- `claude -p` runs with `cwd=MERHAV_BARI_DIR` directly in the real repo using `--permission-mode acceptEdits`. No isolated workspace. Deliberate tradeoff for local personal use.
- `claude -p` is asked to write `events.json` and run `pipeline.py --push` itself.

---

## What was already fixed (do not re-report these)

Findings marked **[deleted]** applied only to files that have since been removed.

| # | Finding | How fixed |
|---|---------|-----------|
| 1 | **Prompt injection via `claude -p` running in the real repo** | Previously isolated, then deliberately reverted — see "What was NOT fixed". |
| 2 | **Zip-slip: `startswith` broken on Windows** | `os.path.commonpath()` + `try/except ValueError` for cross-drive paths. |
| 3 | **`step_push()` silently swallowed git errors** | Raises `RuntimeError` on commit/push failure. |
| 4 | **`asyncio.get_event_loop()` deprecated** | Changed to `asyncio.get_running_loop()`. |
| 5 | **[deleted] GitHub token in git remote URL** | File deleted. |
| 6 | **No Telegram user authorization** | `ALLOWED_USER_IDS` env var; startup warning when empty. |
| 7 | **[deleted] SSRF in `extract.py`** | File deleted. |
| 8 | **[deleted] Agentic loop ran indefinitely** | File deleted. |
| 9 | **`sys.exit(1)` inside `step_validate()`** | Replaced with `raise ValidationError(...)`. |
| 10 | **`pipeline.py` ignored skip env vars** | Both flags checked at top of `__main__`. |
| 11 | **No CSP on generated HTML** | `<meta http-equiv="Content-Security-Policy">` with strict directives. |
| 12 | **[deleted] Untrusted website content fed raw to Claude** | File deleted. |
| 13 | **Empty-events guard missing** | `_run_extraction` now reads `prev_count` before run; raises if `prev > 0` and new `= 0`. |
| 14 | **Auth failures invisible — stdout not captured** | `capture_output=True`; stdout+stderr scanned for auth/rate-limit keywords. |
| 15 | **`_EXTRACT_DONE.json` freshness not checked** | Deleted before run; mtime checked after — stale file raises `RuntimeError`. |
| 16 | **`events.json` not protected on extraction failure** | `_run_extraction_with_backup()` backs up `events.json` before run; restores atomically on any exception. |
| 17 | **[deleted] `bot_server.py` push fell through on errors** | File deleted. |
| 18 | **[deleted] `extract.py` SSRF IPv4-only** | File deleted. |
| 19 | **[deleted] `extract.py` relative redirects broken** | File deleted. |
| 20 | **Date comparison used `DD/MM/YYYY` string sort** | `_last_msg_date()` now returns `datetime`; `max()` uses `datetime.min` fallback. |
| 21 | **Dedup dropped same-title-same-date-different-time events** | `start_time_only` in dedup key — confirmed present at `pipeline.py` line 240. |
| 22 | **`step_validate` crashed on malformed JSON** | `try/except json.JSONDecodeError` — confirmed present at `pipeline.py` line 1012. |
| 23 | **`step_push` failed on drifted local repo** | `git pull --rebase --autostash` — confirmed present at `pipeline.py` line 1080. |
| 24 | **[deleted] `bot_server.py` server blockers** | File deleted. |
| 25 | **Temp dirs from `_find_latest_chat` never cleaned up** | `finally` block with `shutil.rmtree` for all peek dirs. |
| 26 | **`.mp4` files not extracted from ZIP** | Added `.mp4` to `_MEDIA_EXTS` in `_find_latest_chat`. |
| 27 | **`prev_count` broke on dict-shape `events.json`** | Handles both list and `{"events": [...]}` dict shapes. |
| 28 | **Empty-events guard trusted claude's done-file count, not actual file** | After done-file parse, re-reads `events.json` for `actual_count` (pipeline's `step_clean` may reduce independently). Guard and return now use `actual_count`. |
| 29 | **`step_clean` silently published empty site** | Raises `RuntimeError` when all events are removed from non-empty input. |
| 30 | **Auth keyword list incomplete** | Added `'credit balance'`, `'insufficient credits'`, `'overloaded'`, `'context length'`, `'prompt is too long'`, `'session expired'`. |
| 31 | **`_find_latest_chat` silent when all dates are `None`** | `log.warning` when `best_date is None`. |
| 32 | **`test_zip_slip_traversal_blocked` passed vacuously** | Attack entries now end with `_chat.txt` so they reach the `commonpath` guard; asserts escape file doesn't exist. |
| 33 | **`test_first_matching_chat_returned` too permissive** | Changed `assertIn(..., ['first match', 'second match'])` → `assertEqual(..., 'first match')`. |
| 34 | **`TestFindImage` range test stale** | Updated to match actual +50 line search window. |
| 35 | **No tests for `_run_extraction` guards** | `TestRunExtractionGuards`: stale done-file, empty-events guard, auth keyword scan. |
| 36 | **No regression test for dedup fix #21** | `TestStepCleanDedup`: same-title/same-date/different-time kept; true duplicate removed; all-past raises. |

## What was NOT fixed and why

| Finding | Decision |
|---------|----------|
| **Prompt injection / real-repo execution** (finding #1, reverted) — `claude -p` runs in `MERHAV_BARI_DIR` with `--permission-mode acceptEdits`. | Accepted for local personal use. `acceptEdits` auto-approves Edit/Write/MultiEdit; Bash and network tools still prompt. Single trusted user. |
| **`_chat.txt` saved before extraction succeeds** — if extraction fails, new chat is in repo but events.json is restored from backup. | Accepted. The events.json backup (finding #16) covers the data-loss case. Chat being "ahead" is harmless — next run re-processes it. |
| **`ALLOWED_USER_IDS` fail-open** (empty = allow all) | Intentional dev-mode default. Startup warning added (finding #6). |
| **`style-src 'unsafe-inline'` in CSP** | Required for inline `<style>`. Hashed CSP is over-engineering for personal use. |
| **Job-lock TOCTOU** — both `.locked()` checks happen before `async with` | UX glitch only (second caller waits silently). Personal bot, single user. |
| **CSP `frame-ancestors 'none'` in `<meta>` is a no-op** | GitHub Pages doesn't allow custom headers. Accepted. |
| **`_safe_error()` only redacts `BOT_TOKEN`** | Local Windows bot with one trusted user. Paths in error messages are fine. |
| **CLAUDE.md poisoning** — a prior injection could modify `CLAUDE.md` so future runs inherit attacker instructions | Accepted for personal use. CLAUDE.md is in a private repo edited only by the owner. |
| **`git pull` failure in `step_push` is non-fatal** — prints warning, continues | If pull fails, push will fail too (non-fast-forward). Error surfaces at the push step. |

---

## Evaluation tracks

This prompt is split into four independent review tracks. Each reviewer should focus on their track only.

---

### Track A — Engineering & correctness

Fetch and read `bot.py`. Verify the following claims against the actual code (cite line numbers):

1. **`capture_output=True`**: confirm it is present in the `subprocess.run` call inside `_run_extraction`. If absent, stdout/stderr are invisible to the bot — auth failures go undetected.
2. **Auth keyword scan**: confirm the loop over keywords runs after the subprocess call. The keywords now include `'credit balance'`, `'insufficient credits'`, `'overloaded'`, `'context length'`, `'prompt is too long'`, `'session expired'` in addition to the originals. Are there any remaining Claude failure messages that exit 0 but are not caught?
3. **Done-file freshness**: confirm `done_file.unlink(missing_ok=True)` happens before the subprocess, and `done_file.stat().st_mtime < start_ts` is checked after. Is `start_ts = time.time()` called at the right place (before or after `unlink`)?
4. **Empty-events guard — two-stage**: `prev_count` is now read before the subprocess; after the done-file parse, `events.json` is re-read to get `actual_count` (because `step_clean` may drop events independently). Verify both reads exist and the guard uses `actual_count`. Does this correctly handle the first run when `events.json` doesn't exist yet?
5. **events.json backup/restore**: confirm `_run_extraction_with_backup` copies `events.json` to `events.json.bak` before the run and restores it atomically with `os.replace` on any exception. Is the backup deleted on success?
6. **Date comparison**: confirm `_last_msg_date` returns `datetime | None`, `_find_latest_chat` uses `datetime.min` as fallback in `max()`, and logs a warning when `best_date is None`. What happens if all candidates return `None`?
7. **Temp dir cleanup**: confirm all peek `tmp_dirs` are cleaned up in a `finally` block, including on exception. Does cleanup also cover the winner's temp dir?
8. **ZIP extraction path**: `_extract_chat_from_zip` computes `target = (tmp_dir / info.filename).resolve()` then calls `z.extract(info, tmp_dir)`. The `commonpath` check uses `target` but extraction writes to the actual path returned by `z.extract`. Are these guaranteed to be the same on Windows with mixed-case filenames?

---

### Track B — Bug hunting

Fetch both files. Look for logic bugs, silent failures, and edge cases — especially ones that could corrupt state or give false success signals.

1. **`_run_extraction` exit-0 silent failure modes**: what other ways can `claude -p` exit 0 without completing the pipeline? (e.g. chat has no upcoming events, CLAUDE.md instruction conflict, claude decides nothing to do) — does the done-file check catch all of these?
2. **`_find_latest_chat` with only candidates that have `None` dates**: if every chat source has no parseable date (empty file, corrupt encoding), all dates are `None`, `datetime.min` is used for all, and `max()` picks whichever is first. A warning is now logged. Is this still the right fallback behavior, or should it raise?
3. **`_extract_chat_from_zip` temp dir leak**: the `tmp_dir` for the uploaded ZIP is created with `mkdtemp` and never deleted. (Different from `_find_latest_chat`'s peek dirs.) Is this a problem?
4. **`events.json.bak` collision**: if two extractions run back-to-back (one fails mid-flight while another starts), could `events.json.bak` be overwritten by the second run before the first restores it? Can `_job_lock` prevent this entirely?
5. **`step_clean` all-events guard**: `step_clean` now raises `RuntimeError` when it would drop ALL events from non-empty input (`if events and not kept`). Verify the guard is present and check: does it also prevent publishing when events is populated from `events.json` but ALL entries are past-dated?
6. **`_EXTRACT_DONE.json` integer cast**: `count = int(done['events'])` — what if `claude -p` writes `{"events": "7"}` (string) or `{"events": null}`? Does `int("7")` handle the string case correctly?
7. **Race between `done_file.stat().st_mtime` and filesystem clock**: on some Windows filesystems, mtime resolution is 2 seconds. Could `start_ts` and mtime be equal even if the file was written before the run started?

---

### Track C — Security

Fetch both files. Focus on what an adversary could do with a malicious WhatsApp message that ends up in `_chat.txt`.

1. **`acceptEdits` scope on Windows**: Claude Code docs say `--permission-mode acceptEdits` auto-approves Edit/Write/MultiEdit tool calls. On Windows, does it also auto-approve PowerShell or Bash commands run via the tool? What is the actual blast radius in a `-p` non-interactive subprocess?
2. **Prompt injection via chat content**: the prompt passed to `claude -p` includes the full path to `_chat.txt` and tells Claude to "follow all rules in CLAUDE.md". Can a WhatsApp message containing `[SYSTEM]` or `---END OF INSTRUCTIONS---` style text cause Claude to ignore CLAUDE.md and follow attacker instructions instead?
3. **CLAUDE.md as persistence point**: if a prior injection modified `CLAUDE.md`, every future `claude -p` run inherits attacker-controlled instructions. What mitigations exist? (Hash check? Read-only file attribute?) Is there any defense in depth?
4. **`_safe_error` leakage**: does any exception path send file paths, tokens, or Windows paths to the Telegram user? Check `_run_extraction` `RuntimeError` messages specifically — some include `MERHAV_BARI_DIR` in the done-file path shown to the user.
5. **HTML XSS**: verify all event fields written into `index.html` pass through `h()` (`html.escape`). Pay attention to `registration_link`, `image_url`, `_image_filename`, and any field used inside an `href` or `src` attribute. Does `_safe_url` enforce `http`/`https` scheme?
6. **CSP effectiveness**: the CSP is in a `<meta>` tag. Is it placed before any inline `<style>` block or any resource load? Does `script-src 'none'` fully prevent XSS given that no JS is intentionally used?

---

### Track D — Test suite review

Fetch `tests/test_bot_robustness.py` and `tests/test_pipeline_robustness.py`. Evaluate coverage, correctness, and what's missing.

1. **`_run_extraction` guard tests**: `TestRunExtractionGuards` now tests stale done-file detection, empty-events guard, and auth keyword scan using `subprocess.run` mocks. Do these tests correctly simulate the failure modes? Are there gaps (e.g. `events.json` restore after exception)?
2. **ZIP slip test**: `test_zip_slip_traversal_blocked` now uses attack entries ending with `_chat.txt` (so they reach the `commonpath` check) and asserts the escaped file doesn't exist. Is the test hermetic — could a previously-escaped `_chat.txt` in `tempfile.gettempdir()` cause a false negative?
3. **`step_clean` dedup tests**: `TestStepCleanDedup` covers same-title/same-date/different-time kept, exact duplicate removed, and all-past raises. Is the test patching of `EVENTS_JSON` robust? Could `step_clean` print output affect test isolation?
4. **Missing negative tests**: are there tests for `_extract_chat_from_zip` with a ZIP that contains a `_chat.txt` entry using Windows-style backslash paths (`..\_chat.txt`) or null bytes in the filename?
5. **`test_pipeline_robustness.py` coverage**: are there tests for `step_validate`, `step_push` error propagation, or the `step_enrich` enrichment logic? What's the most valuable missing test for `pipeline.py`?
6. **Test isolation**: the bot tests stub `telegram` and `telegram.ext` modules at import time. Is the stub complete enough that `bot.py` imports cleanly? Could a future `bot.py` change (e.g. using a new `telegram` submodule) silently break the stub without the test failing at import?
7. **Improvement suggestions**: propose up to 3 specific new test cases (with sketch implementations) that would catch bugs not yet covered — e.g. `events.json` restore after a failed extraction, `_find_latest_chat` with all-None dates, or a two-run sequence verifying the backup is deleted on success.

---

**Context:** Personal-use bot for a small wellness community WhatsApp group (~1 trusted user sends ZIPs). Not a public service. The generated HTML is public (GitHub Pages). The Telegram token and GitHub token must be protected.
