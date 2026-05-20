# Code Review Prompt — merhav-bari/pipeline.py

Paste this prompt to any AI (Claude, GPT-4, etc.) along with the full contents of `pipeline.py`:

---

You are a senior Python developer. Review the following script and give concrete, prioritized feedback.

The script is a WhatsApp chat event pipeline with 4 steps:
1. **Trim** — strips old lines from a WhatsApp `_chat.txt` export (by post date, then by parsed event date)
2. **Clean** — removes expired events from a JSON file
3. **Enrich** — opens each event's registration URL in a headless Chromium browser (Playwright, async) and extracts missing price / time / city / phone
4. **HTML** — renders events as a card grid (Hebrew, RTL) with base64-embedded images/video thumbnails

Focus your review on:

### 1. Correctness
- Are there edge cases in `_parse_event_dates()` that would cause false positives (removing messages that should be kept)?
- Can the WhatsApp timestamp regex `^\[(\d{2}/\d{2}/\d{4}), ` miss any valid message lines?
- Does the `_group_messages()` logic correctly handle continuation lines?
- In `step_enrich`, is the asyncio semaphore + lock pattern correct for the parallel Playwright workers?

### 2. Robustness
- What happens if `events.json` doesn't exist when the pipeline runs?
- What happens if `_chat.txt` is empty or missing?
- Can `cv2.VideoCapture` block the process on a corrupt mp4?
- Is the Playwright browser guaranteed to close even if a worker throws?

### 3. Performance
- Is embedding all images as base64 in the HTML a good idea for large chats (100+ images)?
- Is `CONCURRENCY=5` a good default for the Playwright enrichment step?
- Any obvious bottlenecks?

### 4. Security
- Are there any XSS risks in the HTML generation (user-controlled strings inserted into HTML)?
- Any other risks?

### 5. Code quality
- Is the file structure (one big file) appropriate, or would splitting it make sense?
- Any obvious bugs, dead code, or confusing patterns?

For each issue found: name it, explain why it's a problem, and suggest a specific fix.
