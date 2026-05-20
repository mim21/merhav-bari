# מרחב בריא – Event Pipeline

Converts a WhatsApp group chat export into a styled HTML event listing page, published to GitHub Pages.

**Live site:** https://mim21.github.io/merhav-bari/

## What it does

1. Reads a WhatsApp chat export (`_chat.txt`)
2. An AI agent extracts upcoming events into `events.json`
3. `pipeline.py` enriches each event (price, time, location) by scraping the event's website
4. Generates a self-contained `index.html` with event cards
5. Publishes to GitHub Pages

## Setup

**Requirements:**
```
pip install playwright opencv-python-headless
playwright install chromium
```

**Chat file location** (not in repo — provided locally):
```
WhatsApp Chat - מרחב בריא - פרסום מרחבים ואירועים 1/_chat.txt
```

## Usage

### Step 1 — Extract events from chat
Ask an AI agent to read `_chat.txt` and produce `events.json` following the schema below.

### Step 2 — Run the pipeline
```
run.bat          # Windows: runs pipeline, opens browser, pushes to GitHub Pages
python pipeline.py  # or run directly
```

The pipeline automatically:
- Removes chat messages older than 60 days
- Removes events whose date has passed
- Enriches missing price/time/city by scraping each event's website (Playwright)
- Generates `index.html`

## events.json schema

Each event in the array:

```json
{
  "title": "string",
  "event_type": "concert|lecture|meetup|party|workshop|screening|exhibition|class|sale|cuddle_party|other",
  "status": "scheduled|updated|postponed|canceled|tentative",
  "date_only": "YYYY-MM-DD or null",
  "end_date_only": "YYYY-MM-DD or null  (multi-day events only)",
  "start_time_only": "HH:MM or null",
  "end_time_only": "HH:MM or null",
  "raw_date_text": "original date text from the post",
  "location_name": "venue name or null",
  "city": "city name in Hebrew or null",
  "price_text": "e.g. 150₪ or null  (lowest/summary price)",
  "price_unit": "couple|person|null",
  "price_note": "e.g. 'הרשמה מוקדמת / רגילה' or 'יחיד / זוג' or null",
  "price_details": ["one string per price tier — used when multiple tiers exist"],
  "description": "1-3 sentences in Hebrew",
  "registration_link": "URL or null",
  "contact_info": {
    "phone": [{"number": "05X-XXX-XXXX", "name": "name or null"}],
    "telegram": [],
    "instagram": [],
    "other": []
  },
  "source_messages": [{
    "line_reference": 123,
    "source_excerpt": "first line of the WhatsApp message",
    "source_message_timestamp": "YYYY-MM-DD"
  }],
  "confidence": 0.95
}
```

## Key rules for AI agents extracting events

### Times
- Verify start and end time from both the WhatsApp post AND the event website
- Never confuse "recommended arrival time" with event start time
- If end time < start time (e.g. 20:00–01:00), it crosses midnight — this is valid
- If uncertain, set to null rather than guess

### Prices
- Use `price_details` (array) when multiple tiers exist (e.g. early bird + regular, or individual + couple)
- Include cutoff dates in tier strings: `"180₪ – מכירה מוקדמת (עד 10.5)"` — the pipeline auto-marks expired tiers with strikethrough
- Use `price_note` only for a single simple qualifier on one price
- Always verify prices from the event website — never guess

### Images / videos
- `line_reference` must point to the exact line containing `<attached: FILENAME.jpg>` in `_chat.txt`
- The pipeline searches ±40 lines around `line_reference` to find the image file
- Videos (.mp4): set `line_reference` to the mp4 line — the pipeline extracts the first frame as thumbnail
- No media → `line_reference: null`
- Two events in one message → only the first gets `line_reference`, the second gets `null`

### Phone numbers
- Store as objects: `{"number": "05X-XXX-XXXX", "name": "name or null"}`
- `phone: null` = unknown, pipeline will try to scrape
- `phone: []` = confirmed no phone (prevents scraping)
- Only include numbers explicitly published in the post or on the event website

## File structure

```
pipeline.py        — full pipeline logic
CLAUDE.md          — instructions for Claude Code specifically
README.md          — this file
run.bat            — Windows runner + GitHub Pages publish
.gitignore         — excludes WhatsApp chat, generated files
```

Generated locally (not in repo):
```
events.json        — extracted events (regenerated each run)
index.html         — output HTML (pushed to GitHub Pages via run.bat)
WhatsApp Chat .../  — chat export folder
```
