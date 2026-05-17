# מרחב בריא – Event Pipeline

WhatsApp chat export → events.json → events.html

## Project structure
- `pipeline.py` — all logic: trim chat → clean old events → enrich via Playwright → generate HTML
- `events.json` — extracted events (written by Claude Code manually)
- `events.html` — final output (opened in browser)
- `run.bat` — runs pipeline.py then opens events.html

## Chat file location
`c:/PRIVATE/merhav-bari/WhatsApp Chat - מרחב בריא - פרסום מרחבים ואירועים 1/_chat.txt`
(folder name has " 1" at the end — critical)

## How to run
1. Ask Claude Code: "read _chat.txt and update merhav-bari/events.json with all events"
2. Run `run.bat` (or `python pipeline.py`)

## Event JSON schema (events.json)
```json
{
  "title": "string",
  "event_type": "concert|lecture|meetup|party|workshop|screening|exhibition|class|sale|cuddle_party|other",
  "status": "scheduled|updated|postponed|canceled|tentative",
  "date_only": "YYYY-MM-DD or null",
  "end_date_only": "YYYY-MM-DD or null (for multi-day events)",
  "start_time_only": "HH:MM or null",
  "end_time_only": "HH:MM or null",
  "raw_date_text": "original text",
  "location_name": "string or null",
  "city": "string in Hebrew or null",
  "price_text": "e.g. 150₪ or null (summary / lowest price)",
  "price_unit": "couple|person|mixed|null",
  "price_note": "e.g. 'הרשמה מוקדמת / רגילה' or 'יחיד / זוג' or null — auto-detected by pipeline from website",
  "price_details": ["line per tier, e.g. 'הרשמה מוקדמת (עד 21/5): 290₪ ליחיד | 550₪ לזוג'"],
  "description": "1-3 sentences in Hebrew",
  "registration_link": "URL or null",
  "contact_info": { "phone": [], "telegram": [], "instagram": [], "other": [] },
  "source_messages": [{ "line_reference": 123, "source_excerpt": "...", "source_message_timestamp": "YYYY-MM-DD" }],
  "confidence": 0.0
}
```

## CRITICAL: image line_reference rule
`line_reference` must point to the EXACT line of `<attached: FILENAME.jpg>` in _chat.txt.
The HTML generator searches ±40 lines from line_reference to find the image.
- No image/video → set line_reference: null
- Videos (.mp4) are supported — their first frame is used as thumbnail
- Two events sharing one message → only first gets line_reference, second gets null

## price_details vs price_note
- Use `price_details` (array of strings) when there are multiple tiers (e.g. early bird + individual/couple).  
  When present, it replaces `price_text`/`price_note` in the HTML card entirely.
- Use `price_note` (single string) for a simple qualifier on a single price (e.g. "יחיד / זוג").  
  `price_note` is auto-detected by the pipeline from the event website — don't guess it manually.

## When compacting
Focus on: event schema, line_reference values, pipeline step order, and any errors encountered.
