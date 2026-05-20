# Claude Code – מרחב בריא

See README.md for full project docs and event schema.

## Chat file location
`c:/PRIVATE/merhav-bari/WhatsApp Chat - מרחב בריא - פרסום מרחבים ואירועים 1/_chat.txt`
(folder name has " 1" at the end — critical)

## How to run
1. Read `_chat.txt` and write all upcoming events to `events.json` (follow schema in README.md)
2. Run `python pipeline.py` or `run.bat`

## price_details vs price_note
- `price_details` (array): multiple tiers → replaces price display entirely. Include cutoff dates so pipeline can mark expired ones.
- `price_note` (single string): auto-detected by pipeline from website — never set manually.

## When compacting
Focus on: event schema, line_reference values, pipeline step order, any errors encountered.
