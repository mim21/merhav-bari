#!/usr/bin/env python3
# pip install playwright opencv-python-headless && playwright install chromium
'''
מרחב בריא – Event Pipeline
WhatsApp chat → events.json → events.html

Steps (run automatically by this script):
  1. Trim   – A) remove messages with post date > TRIM_DAYS
              B) remove messages whose event date has passed
  2. Clean  – remove expired events from events.json
  3. Enrich – fill missing price / time / city / phone via Playwright
  4. HTML   – render events.json → events.html

NOTE: Event extraction is done by Claude Code (no API key needed):
  → Ask Claude Code: "read _chat.txt and update events.json"
  → Then run:  python pipeline.py
'''

import asyncio
import base64
import hashlib
import json
import math
import os
import re
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from html import escape as h
from pathlib import Path
import urllib.request
from urllib.parse import urlparse, quote

sys.stdout.reconfigure(encoding='utf-8')

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG  ← edit only this section
# ─────────────────────────────────────────────────────────────────────────────
CHAT_FILE   = Path(os.environ.get('MERHAV_CHAT_FILE',
                   r'c:/PRIVATE/merhav-bari/WhatsApp_Chat_מרחב_בריא_פרסום_מרחבים_ואירועים/_chat.txt'))
EVENTS_JSON = Path(os.environ.get('MERHAV_EVENTS_JSON', Path(__file__).parent / 'events.json'))
OUTPUT_HTML = Path(os.environ.get('MERHAV_OUTPUT_HTML', Path(__file__).parent / 'index.html'))
OUTPUT_CAL  = Path(os.environ.get('MERHAV_OUTPUT_CAL',  Path(__file__).parent / 'calendar.ics'))
SITE_URL    = 'https://mim21.github.io/merhav-bari'

TRIM_DAYS        = 120   # strip chat messages older than this
SHOW_DAYS_AGO    = 1    # show events from N days ago (1 = from yesterday)

WAIT_MS          = 3000
TIMEOUT_MS       = 15000
CONCURRENCY      = 5
MAX_URLS         = 3
MAX_SUBLINKS     = 3
PRICE_MIN        = 20
PRICE_MAX        = 5000

# ─────────────────────────────────────────────────────────────────────────────
# STEP 1 – TRIM CHAT
# Two passes on _chat.txt:
#   A) Remove messages with post date older than TRIM_DAYS
#   B) Remove messages whose event date is yesterday or earlier
# ─────────────────────────────────────────────────────────────────────────────

# Detect Hebrew month names in event date text
_HE_MONTH_NUM = {
    'ינואר': 1, 'פברואר': 2, 'מרץ': 3, 'אפריל': 4,
    'מאי': 5,   'יוני': 6,   'יולי': 7, 'אוגוסט': 8,
    'ספטמבר': 9, 'אוקטובר': 10, 'נובמבר': 11, 'דצמבר': 12,
}
_HE_MONTH_RE = re.compile('|'.join(_HE_MONTH_NUM.keys()))
# Numeric date patterns: 17.5  /  17/5  /  17.5.26  /  17.5.2026
_NUM_DATE_RE = re.compile(r'\b(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?\b')


def _parse_event_dates(text):
    '''Return list of dates mentioned as event dates in a message block.'''
    today = date.today()
    found = []

    # Hebrew month: "17 במאי", "17 מאי 2026", "יום שישי 17 מאי"
    for m in _HE_MONTH_RE.finditer(text):
        month = _HE_MONTH_NUM[m.group(0)]
        # look for day number near the month name (up to 10 chars before)
        prefix = text[max(0, m.start() - 10): m.start()]
        day_m  = re.search(r'(\d{1,2})\s*$', prefix)
        if not day_m:
            continue
        day = int(day_m.group(1))
        # look for year after month name
        suffix = text[m.end(): m.end() + 10]
        year_m = re.search(r'(\d{4}|\d{2})', suffix)
        if year_m:
            y = int(year_m.group(1))
            year = 2000 + y if y < 100 else y
        else:
            year = today.year
        try:
            found.append(date(year, month, day))
        except ValueError:
            pass

    # Numeric: DD.MM or DD/MM or DD.MM.YY(YY)
    for m in _NUM_DATE_RE.finditer(text):
        day, month = int(m.group(1)), int(m.group(2))
        if not (1 <= day <= 31 and 1 <= month <= 12):
            continue
        if year_str := m.group(3):
            y = int(year_str)
            year = 2000 + y if y < 100 else y
        else:
            year = today.year
            # if the date has clearly passed this year by a lot, it's not an upcoming event
        try:
            found.append(date(year, month, day))
        except ValueError:
            pass

    return found


def _group_messages(lines):
    '''Split lines into (header_line, [all_lines]) message groups.'''
    ts_re = re.compile(r'^\[(\d{2}/\d{2}/\d{4}), ')
    groups = []  # list of [line, line, ...]
    current = []
    for line in lines:
        if ts_re.match(line) and current:
            groups.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        groups.append(current)
    return groups


def step_trim():
    print('\n── Step 1: Trim chat ──')
    ts_re   = re.compile(r'^\[(\d{2}/\d{2}/\d{4}), ')
    cutoff  = datetime.now() - timedelta(days=TRIM_DAYS)
    yesterday = date.today() - timedelta(days=1)

    lines = CHAT_FILE.read_text(encoding='utf-8').splitlines(keepends=True)

    # Pass A – remove by post date
    after_a = []
    keep = False
    for line in lines:
        m = ts_re.match(line)
        if m:
            keep = datetime.strptime(m.group(1), '%d/%m/%Y') >= cutoff
        if keep:
            after_a.append(line)
    removed_a = len(lines) - len(after_a)
    print(f"  Pass A (post date): removed {removed_a} lines, kept {len(after_a)}")

    # Pass B – remove messages whose event date is in the past
    groups  = _group_messages(after_a)
    after_b = []
    removed_b = 0
    for group in groups:
        text = ''.join(group)
        dates = _parse_event_dates(text)
        # Only drop if ALL found dates are in the past (so multi-date posts like
        # "7.5 or 28.5" survive until the last date passes)
        if dates and all(d <= yesterday for d in dates):
            removed_b += len(group)
        else:
            after_b.extend(group)
    print(f"  Pass B (event date): removed {removed_b} lines, kept {len(after_b)}")

    tmp = CHAT_FILE.with_suffix('.tmp')
    tmp.write_text(''.join(after_b), encoding='utf-8')
    os.replace(tmp, CHAT_FILE)
    first = after_b[0][:12] if after_b else 'nothing'
    print(f"  Final: {len(after_b)} lines from {first}")


def _events_from_json(data):
    '''Safely extract the events list from any JSON shape.'''
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        events = data.get('events', [])
        return events if isinstance(events, list) else []
    return []


# ─────────────────────────────────────────────────────────────────────────────
# STEP 2 – CLEAN OLD EVENTS
# ─────────────────────────────────────────────────────────────────────────────
def step_clean():
    print('\n── Step 2: Clean old events ──')
    today         = date.today()
    cutoff_post   = today - timedelta(days=60)
    cutoff_event  = today - timedelta(days=1)

    with open(EVENTS_JSON, encoding='utf-8') as f:
        data = json.load(f)
    is_list = isinstance(data, list)
    events  = [e for e in _events_from_json(data) if isinstance(e, dict)]
    print(f"  Loaded {len(events)} events")

    def get_event_date(e):
        for field in ('date_only', 'event_start'):
            v = e.get(field)
            if v:
                try: return date.fromisoformat(str(v)[:10])
                except: pass
        return None

    def get_post_date(e):
        msgs = e.get('source_messages')
        for msg in (msgs if isinstance(msgs, list) else []):
            ts = msg.get('source_message_timestamp', '') if isinstance(msg, dict) else ''
            try: return date.fromisoformat(ts[:10])
            except: pass
        return None

    kept, removed = [], []
    for e in events:
        post_d  = get_post_date(e)
        event_d = get_event_date(e)
        if post_d and post_d < cutoff_post:
            removed.append((e, f"post {post_d}"))
        elif event_d and event_d <= cutoff_event:
            removed.append((e, f"event {event_d}"))
        else:
            kept.append(e)

    for e, reason in removed:
        title = (_str(e.get('title')) or '?')[:55]
        print(f"  Removed [{reason}]: {title}")

    # Deduplication by (title, date)
    seen_keys, unique = set(), []
    for e in kept:
        key = (
            _str(e.get('title')).strip().lower(),
            _str(e.get('date_only') or e.get('event_start') or '')[:10],
            _str(e.get('start_time_only')),
        )
        if key in seen_keys:
            print(f"  Duplicate removed: {(_str(e.get('title')) or '?')[:55]}")
        else:
            seen_keys.add(key)
            unique.append(e)
    if len(unique) < len(kept):
        print(f"  Removed {len(kept) - len(unique)} duplicate(s)")
    kept = unique
    if events and not kept:
        raise RuntimeError('step_clean would drop all events — refusing to publish empty site')
    print(f"  Keeping {len(kept)} events")

    out = kept if is_list else {'events': kept}
    tmp = EVENTS_JSON.with_suffix('.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    os.replace(tmp, EVENTS_JSON)


# ─────────────────────────────────────────────────────────────────────────────
# STEP 3 – ENRICH WITH PLAYWRIGHT
# ─────────────────────────────────────────────────────────────────────────────
URL_RE   = re.compile(r'https?://[^\s\]\)\'"<>]+', re.IGNORECASE)
PRICE_RE = re.compile(r'(\d[\d,\.]*)\s*(?:₪|ש[״"]?ח|nis|ils)', re.IGNORECASE | re.UNICODE)
PRICE_KW = re.compile(
    r'(?:מחיר|עלות|כרטיס|כניסה|תשלום|עולה|עלה|עלות)[^\d₪\n]{0,30}?(\d[\d,\.]+)',
    re.IGNORECASE | re.UNICODE)
TIME_RE  = re.compile(r'\b([01]?\d|2[0-3]):([0-5]\d)\b')
PHONE_RE = re.compile(
    r'(?:05\d[-.\s]?\d{3}[-.\s]?\d{4}|\+972[-.\s]?\d[-.\s]?\d{3}[-.\s]?\d{4}|'
    r'972[-.\s]?\d[-.\s]?\d{3}[-.\s]?\d{4})')
CITY_RE  = re.compile(
    r'\b(תל[-\s]?אביב|ירושלים|חיפה|באר[-\s]?שבע|פרדס[-\s]?חנה|קיסריה|'
    r'חדרה|נתניה|רמת[-\s]?גן|פתח[-\s]?תקווה|ראשון[-\s]?לציון|'
    r'בנימינה|זכרון[-\s]?יעקב|כרכור|אשדוד|אשקלון|רחובות|הרצליה|'
    r'מודיעין|כפר[-\s]?סבא|רעננה|הוד[-\s]?השרון)\b',
    re.IGNORECASE | re.UNICODE)
COUPLE_RE     = re.compile(r'לזוג|לזוגות|per\s+couple|couple|זוג', re.IGNORECASE | re.UNICODE)
PERSON_RE     = re.compile(r'לאדם|לאיש|לאישה|לאנשים|לנפש|per\s+person|pp\b', re.IGNORECASE | re.UNICODE)
EARLY_BIRD_RE = re.compile(r'מוקדמת|early.?bird|הנחה.{0,15}מוקד|הרשמה.{0,15}מוקד|מחיר.{0,15}מוקד', re.IGNORECASE | re.UNICODE)
INDIV_RE    = re.compile(r'ליחיד|לאדם\s+אחד|per\s+person\b', re.IGNORECASE | re.UNICODE)
COUPLE_PRICE_RE = re.compile(r'לצמד|לזוג\s*\d|per\s+couple\b', re.IGNORECASE | re.UNICODE)
SKIP_DOMAINS = {'chat.whatsapp.com', 'wa.me', 'instagram.com', 'twitter.com', 't.me', 'bit.ly'}
REGISTER_LINK_RE = re.compile(
    r'טופס|הרשמה|registration|register|ticket|כרטיס|booking|book|purchase|רכישה|'
    r'payment|תשלום|sign.?up|מחיר|price|פרטים|details', re.IGNORECASE)
FOLLOW_DOMAINS = re.compile(
    r'docs\.google\.com/forms|forms\.gle|tazman\.co\.il|ravpage|wixsite|canva\.site|'
    r'morning-sale\.page|yannivgold|starkopal|waterembrace|lovetao|playful-touch|'
    r'avigayil|ruthiforer|falling2balance|covet\.co\.il|efrat-brodet', re.IGNORECASE)


def _should_skip(url):
    try:
        from urllib.parse import urlparse
        host = urlparse(url).netloc.lower().removeprefix('www.')
        return any(d in host for d in SKIP_DOMAINS)
    except: return False


def _collect_urls(event):
    urls, seen = [], set()
    link = _safe_url(event.get('registration_link') or '')
    if link and not _should_skip(link):
        urls.append(link); seen.add(link)
    msgs = event.get('source_messages')
    for msg in (msgs if isinstance(msgs, list) else []):
        if isinstance(msg, dict):
            for u in URL_RE.findall(_str(msg.get('source_excerpt'))):
                u = u.rstrip('.,)')
                if u not in seen and not _should_skip(u):
                    urls.append(u); seen.add(u)
    return urls[:MAX_URLS]


def _price_unit(text, pos):
    w = text[max(0, pos - 60): pos + 60]
    if COUPLE_RE.search(w): return 'couple'
    if PERSON_RE.search(w): return 'person'
    return None


def _best_price(text):
    candidates = []
    for m in PRICE_RE.finditer(text):
        try:
            val = float(m.group(1).replace(',', ''))
            candidates.append((val, _price_unit(text, m.start())))
        except: pass
    for m in PRICE_KW.finditer(text):
        try:
            val = float(m.group(1).replace(',', ''))
            candidates.append((val, _price_unit(text, m.start())))
        except: pass
    plausible = [(v, u) for v, u in candidates if PRICE_MIN <= v <= PRICE_MAX]
    if not plausible: return None, None
    vals = sorted(set(int(v) for v, u in plausible))
    units = [u for v, u in plausible if u]
    unit = 'couple' if 'couple' in units else 'person' if 'person' in units else None
    if len(vals) == 1: return f"{vals[0]}₪", unit
    return f"{vals[0]}₪–{vals[-1]}₪", unit


def _best_times(text):
    times = [f"{m.group(1)}:{m.group(2)}" for m in TIME_RE.finditer(text) if 7 <= int(m.group(1)) <= 23]
    if len(times) >= 2: return times[0], times[-1]
    if times: return times[0], None
    return None, None


def _enrich_from_text(event, text):
    changed = False
    if not event.get('price_text'):
        p, unit = _best_price(text)
        if p:
            event['price_text'] = p
            if unit and not event.get('price_unit'):
                event['price_unit'] = unit
            print(f"    price  → {p}")
            changed = True
    if '–' in _str(event.get('price_text')) and not event.get('price_note') and not event.get('price_details'):
        if INDIV_RE.search(text) and COUPLE_PRICE_RE.search(text):
            event['price_note'] = 'יחיד / זוג'
            print(f"    price_note → יחיד / זוג")
            changed = True
        elif EARLY_BIRD_RE.search(text):
            event['price_note'] = 'הרשמה מוקדמת / רגילה'
            print(f"    price_note → הרשמה מוקדמת / רגילה")
            changed = True
    if not event.get('start_time_only'):
        s, e = _best_times(text)
        if s:
            event['start_time_only'] = s
            if e and not event.get('end_time_only') and e > s:
                event['end_time_only'] = e
            print(f"    time   → {s}" + (f"–{e}" if e else ''))
            changed = True
    if not event.get('city'):
        m = CITY_RE.search(text)
        if m:
            event['city'] = m.group(1)
            print(f"    city   → {m.group(1)}")
            changed = True
    ci = event.setdefault('contact_info', {'phone': [], 'email': [], 'telegram': [], 'instagram': [], 'other': []})
    if isinstance(ci, dict) and ci.get('phone') is None:
        phones = list(set(PHONE_RE.findall(text)))
        if phones:
            ci['phone'] = [{'number': p, 'name': None} for p in phones]
            print(f"    phone  → {phones}")
            changed = True
    return changed


async def _fetch_text(page, url):
    is_fb = 'facebook.com' in url
    try:
        try: await page.evaluate('window.stop()')
        except: pass
        await page.goto(url, timeout=TIMEOUT_MS, wait_until='domcontentloaded')
        await page.wait_for_timeout(WAIT_MS)
        if is_fb:
            try:
                close = await page.query_selector('[aria-label="Close"]')
                if close: await close.click()
                else: await page.keyboard.press('Escape')
                await page.wait_for_timeout(1500)
            except: pass
        text = (await page.inner_text('body') or '')[:200_000]
        if len(text.strip()) < 50:
            await page.wait_for_timeout(3000)
            text = (await page.inner_text('body') or '')[:200_000]
        return text
    except Exception as ex:
        print(f"      error: {ex}")
        return ''


async def _get_sublinks(page, base_url):
    try:
        from urllib.parse import urlparse
        found, seen = [], set()
        for a in await page.query_selector_all('a[href]'):
            href = await a.get_attribute('href') or ''
            text = (await a.inner_text() or '').strip()
            if href.startswith('/'):
                p = urlparse(base_url)
                href = f"{p.scheme}://{p.netloc}{href}"
            href = _safe_url(href)
            if not href or _should_skip(href) or href == base_url: continue
            if REGISTER_LINK_RE.search(text) or REGISTER_LINK_RE.search(href) or FOLLOW_DOMAINS.search(href):
                if href not in seen:
                    seen.add(href); found.append(href)
        return found[:MAX_SUBLINKS]
    except: return []


async def _enrich_event(page, event, label):
    urls = _collect_urls(event)
    if not urls: return False
    changed, visited = False, set()
    for url in urls:
        if url in visited: continue
        visited.add(url)
        print(f"  {label} → {url[:70]}")
        text = await _fetch_text(page, url)
        if text: changed |= _enrich_from_text(event, text)
        for sub in await _get_sublinks(page, url):
            if sub in visited: continue
            visited.add(sub)
            print(f"    ↳ {sub[:70]}")
            subtext = await _fetch_text(page, sub)
            if subtext: changed |= _enrich_from_text(event, subtext)
        if event.get('price_text') and event.get('start_time_only') and event.get('city'):
            break
    return changed


async def step_enrich(force=False):
    print('\n── Step 3: Enrich with Playwright ──')
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print('  playwright not installed — skipping (run: pip install playwright && playwright install chromium)')
        return

    with open(EVENTS_JSON, encoding='utf-8') as f:
        data = json.load(f)
    is_list = isinstance(data, list)
    events  = [e for e in _events_from_json(data) if isinstance(e, dict)]
    if not is_list:
        if not isinstance(data, dict):
            data = {}
        data['events'] = events

    def _needs_enrich(e):
        if force:
            return True
        price_text = _str(e.get('price_text'))
        if any(not _str(e.get(k)) for k in ['price_text', 'start_time_only', 'city']):
            return True
        if '–' in price_text and not _str(e.get('price_note')):
            return True
        return False
    to_enrich = [(i, e) for i, e in enumerate(events, 1) if _needs_enrich(e)]
    print(f"  Enriching {len(to_enrich)}/{len(events)} events ({CONCURRENCY} parallel pages)")

    sem, lock = asyncio.Semaphore(CONCURRENCY), asyncio.Lock()
    total = [0]

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(locale='he-IL', extra_http_headers={'Accept-Language': 'he,en;q=0.9'})

        async def worker(idx, event):
            async with sem:
                page = await ctx.new_page()
                try:
                    label = f"[{idx:02d}/{len(events)}] {(_str(event.get('title')) or '')[:40]}"
                    changed = await _enrich_event(page, event, label)
                    if changed:
                        async with lock: total[0] += 1
                except Exception as ex:
                    print(f"  [{idx:02d}] FAILED: {ex}")
                finally:
                    await page.close()

        await asyncio.gather(*[worker(i, e) for i, e in to_enrich])
        await browser.close()

    print(f"  Enriched {total[0]}/{len(events)} events")
    tmp = EVENTS_JSON.with_suffix('.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, EVENTS_JSON)


# ─────────────────────────────────────────────────────────────────────────────
# STEP 4 – GENERATE HTML
# ─────────────────────────────────────────────────────────────────────────────
ATTACH_RE = re.compile(r'<attached:\s*([\w\-\.]+\.(?:jpg|jpeg|png|mp4))\s*>', re.IGNORECASE)
_LINE_TO_IMAGE: dict[int, str] = {}  # pre-built before trim; keyed by original line number
ALLOWED_MEDIA_EXTS = {'.jpg', '.jpeg', '.png', '.mp4'}

HE_MONTHS = ['', 'ינואר', 'פברואר', 'מרץ', 'אפריל', 'מאי', 'יוני',
             'יולי', 'אוגוסט', 'ספטמבר', 'אוקטובר', 'נובמבר', 'דצמבר']
HE_DAYS   = ['שני', 'שלישי', 'רביעי', 'חמישי', 'שישי', 'שבת', 'ראשון']

TYPE_LABELS = {
    'concert':      ('🎵', 'קונצרט'),
    'lecture':      ('🎤', 'הרצאה'),
    'meetup':       ('🤝', 'מפגש'),
    'party':        ('🎉', 'מסיבה'),
    'workshop':     ('🛠', 'סדנה'),
    'screening':    ('🎬', 'הקרנה'),
    'exhibition':   ('🖼', 'תערוכה'),
    'class':        ('📚', 'שיעור'),
    'sale':         ('🏷', 'מכירה'),
    'cuddle_party': ('🫂', 'כרבולים'),
    'other':        ('📅', 'אירוע'),
}
STATUS_STYLES = {
    'scheduled': ('',          'white'),
    'updated':   ('🔄 עודכן',  '#e8f4fd'),
    'postponed': ('⏸ נדחה',   '#fff3cd'),
    'canceled':  ('❌ בוטל',   '#fde8e8'),
    'tentative': ('❓ מותנה',  '#f9f9f9'),
}


def _mp4_thumbnail(path):
    try:
        import cv2
        cap = cv2.VideoCapture(str(path))
        ok, frame = cap.read()
        cap.release()
        if not ok: return None
        ok2, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok2: return None
        return 'data:image/jpeg;base64,' + base64.b64encode(buf.tobytes()).decode('ascii')
    except Exception: return None


def _safe_url(url):
    if not isinstance(url, str) or not url: return ''
    try:
        p = urlparse(url.strip())
        if p.scheme.lower() not in ('http', 'https') or not p.netloc: return ''
        return url.strip()
    except: return ''


def _img_uri(chat_folder, filename):
    if not isinstance(filename, str):
        return None
    # Only allow plain filenames matching WhatsApp attachment naming
    if not re.fullmatch(r'[\w\-.]+\.(?:jpg|jpeg|png|mp4)', filename, re.IGNORECASE):
        return None
    try:
        path = (chat_folder / filename).resolve()
        chat_root = chat_folder.resolve()
        if os.path.commonpath([str(path), str(chat_root)]) != str(chat_root):
            return None
        if path.suffix.lower() not in ALLOWED_MEDIA_EXTS:
            return None
        if not path.is_file(): return None
        if path.stat().st_size > 10_000_000: return None  # 10 MB cap
        if path.suffix.lower() == '.mp4': return _mp4_thumbnail(path)
        data = base64.b64encode(path.read_bytes()).decode('ascii')
        ext = path.suffix.lower().lstrip('.')
        mime = 'jpeg' if ext in ('jpg', 'jpeg') else ext
        return f"data:image/{mime};base64,{data}"
    except: return None


def _img_uri_remote(url):
    '''Fetch a remote HTTPS image at build time and return a data URI.
    Inlining avoids client-side requests to attacker-controlled hosts.'''
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=15) as resp:
            ct = (resp.headers.get_content_type() or '').split(';')[0].strip()
            if not ct.startswith('image/'):
                return None
            data = resp.read(10_000_001)
            if len(data) > 10_000_000:
                return None
            return f'data:{ct};base64,{base64.b64encode(data).decode("ascii")}'
    except Exception:
        return None


def _find_image(event, line_to_image):
    msgs = event.get('source_messages')
    for msg in (msgs if isinstance(msgs, list) else []):
        if not isinstance(msg, dict): continue
        ref = msg.get('line_reference')
        if ref is None: continue
        try: base = int(str(ref).strip())
        except: continue
        for offset in range(0, 50):  # attachment can be many lines after a long event post
            img = line_to_image.get(base + offset)
            if img: return img
    return None


def _format_date(event):
    d = event.get('date_only') or event.get('event_start')
    if not d: return _str(event.get('raw_date_text'))
    try:
        dt = date.fromisoformat(str(d)[:10])
        end = event.get('end_date_only')
        if end:
            try:
                dt_end = date.fromisoformat(str(end))
                if dt_end != dt:
                    d1 = HE_DAYS[dt.weekday()]
                    d2 = HE_DAYS[dt_end.weekday()]
                    if dt.month == dt_end.month and dt.year == dt_end.year:
                        return f"יום {d1}–{d2}, {dt.day}–{dt_end.day} ב{HE_MONTHS[dt.month]} {dt.year}"
                    else:
                        return f"יום {d1} {dt.day} ב{HE_MONTHS[dt.month]} – יום {d2} {dt_end.day} ב{HE_MONTHS[dt_end.month]} {dt_end.year}"
            except: pass
        return f"יום {HE_DAYS[dt.weekday()]}, {dt.day} ב{HE_MONTHS[dt.month]} {dt.year}"
    except: return str(d)


_TIER_DATE_RE = re.compile(r'עד\s+(\d{1,2})[./](\d{1,2})')


def _str(v):
    return v if isinstance(v, str) else ''


def _list(v):
    return v if isinstance(v, list) else []


def _render_price_tier(tier_text):
    tier_text = _str(tier_text)
    today = date.today()
    m = _TIER_DATE_RE.search(tier_text)
    escaped = h(tier_text)
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        try:
            tier_date = date(today.year, month, day)
            if tier_date < today:
                return f"<div class='price-tier expired'><s>{escaped}</s> <span class='expired-label'>פג תוקף</span></div>"
        except ValueError:
            pass
    return f"<div class='price-tier'>{escaped}</div>"


def _git_short_hash():
    try:
        kwargs = {'cwd': Path(__file__).parent, 'stderr': subprocess.DEVNULL, 'text': True}
        if sys.platform == 'win32':
            kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
        return subprocess.check_output(['git', 'rev-parse', '--short', 'HEAD'], **kwargs).strip()
    except Exception:
        return ''


_ICS_CTRL_RE = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')

def _ics_escape(s):
    s = _ICS_CTRL_RE.sub('', _str(s).replace('\r\n', '\n').replace('\r', '\n'))
    return s.replace('\\', '\\\\').replace('\n', '\\n').replace(',', '\\,').replace(';', '\\;')


def _event_slug(event):
    title = _str(event.get('title')) or 'event'
    d = _str(event.get('date_only') or event.get('event_start') or '')[:10].replace('-', '')
    t = _str(event.get('start_time_only')).replace(':', '')
    slug = re.sub(r'[^\w]+', '-', title, flags=re.UNICODE).strip('-') or 'untitled'
    suffix = f'-{d}-{t}' if d and t else f'-{d}' if d else ''
    return f'event-{slug}{suffix}'


def _event_cal_data(event, event_url=''):
    '''Return (gs, ge, timed, gcal_url, vevent_lines) or None if event has no date.'''
    title     = _str(event.get('title')) or 'אירוע'
    desc      = _str(event.get('description'))
    loc_parts = [p for p in [_str(event.get('location_name')), _str(event.get('city'))] if p]
    location  = ' · '.join(loc_parts)

    d_raw = event.get('date_only') or event.get('event_start')
    if not d_raw:
        return None
    try:
        start_date = date.fromisoformat(str(d_raw)[:10])
    except ValueError:
        return None

    start_t = _str(event.get('start_time_only'))
    end_t   = _str(event.get('end_time_only'))
    end_d   = _str(event.get('end_date_only'))
    timed   = bool(re.match(r'^\d{2}:\d{2}$', start_t))

    if timed:
        gs = start_date.strftime('%Y%m%d') + 'T' + start_t.replace(':', '') + '00'
        if re.match(r'^\d{2}:\d{2}$', end_t):
            # end <= start means midnight crossover (e.g. 20:00–00:00 → ends next day)
            end_date = start_date + timedelta(days=1) if end_t <= start_t else start_date
            ge = end_date.strftime('%Y%m%d') + 'T' + end_t.replace(':', '') + '00'
        else:
            try:
                total = int(start_t[:2]) * 60 + int(start_t[3:]) + 120
                h2, m2 = divmod(total, 60)
                end_date = start_date + timedelta(days=1) if h2 >= 24 else start_date
                ge = end_date.strftime('%Y%m%d') + f'T{h2 % 24:02d}{m2:02d}00'
            except Exception:
                ge = gs
    else:
        gs = start_date.strftime('%Y%m%d')
        try:
            ge = (date.fromisoformat(end_d) + timedelta(days=1)).strftime('%Y%m%d') if end_d else (start_date + timedelta(days=1)).strftime('%Y%m%d')
        except Exception:
            ge = (start_date + timedelta(days=1)).strftime('%Y%m%d')

    details_str = (desc[:400] + '\n' + event_url if desc else event_url) if event_url else desc
    gcal_url = (
        'https://calendar.google.com/calendar/render?action=TEMPLATE'
        '&text=' + quote(title)
        + '&dates=' + gs + '/' + ge
        + ('&details=' + quote(details_str) if details_str else '')
        + ('&location=' + quote(location) if location else '')
    )

    uid   = hashlib.md5('|'.join([title, gs, location]).encode('utf-8')).hexdigest() + '@merhav-bari'
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    vevent = [
        'BEGIN:VEVENT', 'UID:' + uid, 'DTSTAMP:' + stamp,
        ('DTSTART:' + gs) if timed else ('DTSTART;VALUE=DATE:' + gs),
        ('DTEND:'   + ge) if timed else ('DTEND;VALUE=DATE:'   + ge),
        'SUMMARY:' + _ics_escape(title),
    ]
    if desc:
        vevent.append('DESCRIPTION:' + _ics_escape(desc[:500]))
    if location:
        vevent.append('LOCATION:' + _ics_escape(location))
    if event_url:
        vevent.append('URL:' + quote(event_url, safe=':/?=&#-._~@'))
    vevent.append('END:VEVENT')

    return gs, ge, timed, gcal_url, vevent


def _make_cal_links(event, event_url=''):
    result = _event_cal_data(event, event_url)
    if not result:
        return ''
    _, _, _, gcal_url, vevent = result
    title = _str(event.get('title')) or 'אירוע'

    cal_lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//merhav-bari//pipeline//HE'] + vevent + ['END:VCALENDAR']
    ics_b64 = base64.b64encode(('\r\n'.join(cal_lines) + '\r\n').encode('utf-8')).decode('ascii')
    safe_fn = h(re.sub(r'[\\/:"*?<>|]', '', title)[:50])

    return (
        f'<a class="cal-link gcal" href="{h(gcal_url)}" target="_blank" rel="noopener noreferrer">📅 Google</a>'
        f'<a class="cal-link apple" href="data:text/calendar;base64,{ics_b64}" download="{safe_fn}.ics">📅 Apple</a>'
    )


def _make_full_cal(events):
    '''Returns (html_buttons, ics_content). Caller must write ics_content to OUTPUT_CAL.'''
    lines = [
        'BEGIN:VCALENDAR', 'VERSION:2.0',
        'PRODID:-//merhav-bari//pipeline//HE',
        'X-WR-CALNAME:מרחב בריא – אירועים',
        'X-WR-TIMEZONE:Asia/Jerusalem',
    ]
    for event in events:
        result = _event_cal_data(event, f'{SITE_URL}/#{_event_slug(event)}')
        if result:
            lines.extend(result[4])
    lines.append('END:VCALENDAR')
    ics_content = '\r\n'.join(lines) + '\r\n'

    webcal_url = SITE_URL.replace('https://', 'webcal://') + '/calendar.ics'
    gcal_url   = 'https://calendar.google.com/calendar/r?cid=' + quote(webcal_url)

    apple_sub  = f'<a class="cal-link full-cal-apple" href="{webcal_url}">📅 Apple – הרשם</a>'
    google_sub = f'<a class="cal-link full-cal-gcal" href="{h(gcal_url)}" target="_blank" rel="noopener noreferrer">📅 Google – הרשם</a>'
    download   = f'<a class="cal-link full-cal-dl" href="calendar.ics" download="מרחב-בריא.ics">⬇ הורד ICS</a>'
    return apple_sub + google_sub + download, ics_content


def _make_card(event, chat_folder, line_to_image):
    img_tag = ''
    img_file = event.get('_image_filename') or _find_image(event, line_to_image)
    if img_file:
        uri = _img_uri(chat_folder, img_file)
        if uri:
            img_tag = f'<div class="card-img"><img src="{uri}" alt="" loading="lazy"/></div>'
    if not img_tag:
        image_url = _safe_url(_str(event.get('image_url') or ''))
        if image_url:
            uri = _img_uri_remote(image_url)
            if uri:
                img_tag = f'<div class="card-img"><img src="{uri}" alt="" loading="lazy"/></div>'

    etype = _str(event.get('event_type')) or 'other'
    icon, label = TYPE_LABELS.get(etype, ('📅', h(etype)))
    status = _str(event.get('status')) or 'scheduled'
    status_label, card_bg = STATUS_STYLES.get(status, ('', 'white'))
    status_html = f'<div class="status-banner">{status_label}</div>' if status_label else ''

    title    = h(_str(event.get('title')) or _str(event.get('normalized_title')) or 'אירוע')
    date_str = h(_format_date(event))
    s, e     = _str(event.get('start_time_only')), _str(event.get('end_time_only'))
    time_str = h(f"{s} – {e}" if s and e else s)

    loc_name    = _str(event.get('location_name'))
    loc_private = 'יימסר לנרשמים' in loc_name or 'לנרשמים' in loc_name
    if loc_private:
        loc_clean = loc_name.split('(')[0].strip() if '(' in loc_name else ''
        loc_parts = [p for p in [loc_clean, _str(event.get('city'))] if p]
    else:
        loc_parts = [p for p in [loc_name, _str(event.get('city'))] if p]
    location = h(' · '.join(loc_parts))

    price_raw     = _str(event.get('price_text'))
    price_unit    = event.get('price_unit') or ''
    unit_label    = ' לזוג' if price_unit == 'couple' else ' לאדם' if price_unit == 'person' else ''
    price         = h(f"{price_raw}{unit_label}") if price_raw else ''
    price_note    = h(_str(event.get('price_note')))
    price_details = _list(event.get('price_details'))

    desc = h(_str(event.get('description')))
    safe_link = h(_safe_url(event.get('registration_link') or ''))
    link_html = f'<a class="reg-link" href="{safe_link}" target="_blank" rel="noopener noreferrer">להרשמה ←</a>' if safe_link else ''

    contacts = []
    ci = event.get('contact_info') or {}
    if isinstance(ci, dict):
        wa_svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="14" height="14" fill="#25d366" style="vertical-align:middle;margin-left:3px"><path d="M16 0C7.163 0 0 7.163 0 16c0 2.833.742 5.488 2.042 7.788L0 32l8.418-2.01A15.938 15.938 0 0016 32c8.837 0 16-7.163 16-16S24.837 0 16 0zm0 29.333a13.27 13.27 0 01-6.784-1.857l-.486-.29-5.001 1.194 1.227-4.865-.317-.5A13.267 13.267 0 012.667 16C2.667 8.636 8.636 2.667 16 2.667S29.333 8.636 29.333 16 23.364 29.333 16 29.333zm7.27-9.778c-.398-.199-2.354-1.162-2.718-1.294-.364-.133-.629-.199-.894.199-.265.398-1.028 1.294-1.26 1.56-.232.265-.464.298-.862.1-.398-.2-1.681-.62-3.203-1.976-1.184-1.056-1.983-2.36-2.215-2.758-.232-.398-.025-.613.174-.811.179-.178.398-.464.597-.696.199-.232.265-.398.398-.663.133-.265.066-.497-.033-.696-.1-.199-.894-2.155-1.225-2.95-.322-.775-.649-.67-.894-.682l-.762-.013c-.265 0-.696.1-1.061.497-.364.398-1.393 1.361-1.393 3.317s1.426 3.847 1.625 4.112c.199.265 2.807 4.285 6.802 6.01.951.41 1.693.655 2.271.839.954.304 1.823.261 2.51.158.766-.114 2.354-.962 2.686-1.891.332-.929.332-1.725.232-1.891-.099-.166-.364-.265-.762-.464z"/></svg>'
        for p in _list(ci.get('phone')):
            num  = p.get('number', p) if isinstance(p, dict) else p
            if not isinstance(num, str): continue
            name = _str(p.get('name')) if isinstance(p, dict) else ''
            digits = re.sub(r'\D', '', num)
            if digits.startswith('972'): digits = '0' + digits[3:]
            if len(digits) != 10 or not digits.startswith('05'): continue
            wa_digits = '972' + digits[1:]
            display_num = f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"
            wa_url = f"https://wa.me/{wa_digits}"
            contact_label = h(f"{name} {display_num}" if name else display_num)
            contacts.append(f'<a href="{wa_url}" target="_blank" rel="noopener noreferrer" class="contact-wa">&#x202A;{wa_svg} {contact_label}&#x202C;</a>')
        for t in _list(ci.get('telegram')): contacts.append(f'<span class="contact">✈️ {h(_str(t))}</span>')
        for i in _list(ci.get('instagram')): contacts.append(f'<span class="contact">📷 {h(_str(i))}</span>')
    contact_html = ''.join(contacts)

    try:
        conf = float(event.get('confidence', 0))
        if not math.isfinite(conf):
            conf = 0.0
        conf = max(0.0, min(1.0, conf))
    except (TypeError, ValueError):
        conf = 0.0
    conf_color = '#28a745' if conf >= 0.8 else '#ffc107' if conf >= 0.5 else '#dc3545'
    dot_count = round(conf * 5)
    dots = '●' * dot_count + '○' * (5 - dot_count)
    slug = _event_slug(event)
    event_url = f'{SITE_URL}/#{slug}'
    cal_html = _make_cal_links(event, event_url)

    return f"""<div class="card" id="{slug}" style="background:{card_bg}">
  {status_html}
  {img_tag}
  <div class="card-body">
    <div class="card-header-row">
      <span class="badge">{icon} {label}</span>
      <span class="confidence" style="color:{conf_color}" title="{dot_count * 20}%">{dots}</span>
    </div>
    <h2 class="card-title">{title}</h2>
    {"<div class='card-date'>📅 " + date_str + '</div>' if date_str else ''}
    {"<div class='card-time'>🕐 " + time_str + '</div>' if time_str else ''}
    {"<div class='card-location'>📍 " + location + (" <span class='location-private'>(מיקום מדויק יימסר לנרשמים)</span>" if loc_private else '') + '</div>' if location else ''}
    {"<div class='card-price'>💰 " + ''.join(_render_price_tier(t) for t in price_details) + '</div>' if price_details else ("<div class='card-price'>💰 " + price + ("  <span class='price-note'>(" + price_note + ')</span>' if price_note else '') + '</div>' if price else '')}
    {"<div class='card-desc'>" + desc + '</div>' if desc else ''}
    <div class="card-footer">{link_html}{contact_html}</div>
    {'<div class="cal-links">' + cal_html + '</div>' if cal_html else ''}
  </div>
</div>"""


def step_html():
    print('\n── Step 4: Generate HTML ──')
    chat_folder = CHAT_FILE.parent

    line_to_image = _LINE_TO_IMAGE  # pre-built from full chat before Step 1 trimmed it

    with open(EVENTS_JSON, encoding='utf-8') as f:
        all_events = json.load(f)
    all_events = [e for e in _events_from_json(all_events) if isinstance(e, dict)]

    show_from = (date.today() - timedelta(days=SHOW_DAYS_AGO)).isoformat()
    events = [e for e in all_events if str(e.get('date_only') or e.get('event_start') or '') >= show_from]
    events.sort(key=lambda e: str(e.get('date_only') or e.get('event_start') or ''))
    print(f"  Showing {len(events)} events (from {show_from})")

    cards_html              = '\n'.join(_make_card(e, chat_folder, line_to_image) for e in events)
    full_cal_html, ics_content = _make_full_cal(events)
    OUTPUT_CAL.write_text(ics_content, encoding='utf-8')

    _ver = _git_short_hash()
    _ver_str = f' · {_ver}' if _ver else ''

    html = f"""<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
  <meta charset="UTF-8"/>
  <meta http-equiv="Content-Security-Policy" content="default-src 'self'; script-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; connect-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>אירועים קרובים – מרחב בריא</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: 'Segoe UI', Arial, sans-serif;
      background: linear-gradient(135deg, #f5f7fa 0%, #e8ecf0 100%);
      min-height: 100vh; padding: 24px 16px; direction: rtl;
    }}
    header {{ text-align: center; margin-bottom: 32px; }}
    header h1 {{ font-size: 2rem; color: #2c3e50; margin-bottom: 4px; }}
    header p {{ color: #6c757d; font-size: 0.95rem; }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
      gap: 20px; max-width: 1400px; margin: 0 auto;
    }}
    .card {{
      border-radius: 16px; box-shadow: 0 4px 16px rgba(0,0,0,0.08);
      overflow: hidden; display: flex; flex-direction: column;
      transition: transform 0.2s, box-shadow 0.2s;
    }}
    .card:hover {{ transform: translateY(-4px); box-shadow: 0 8px 24px rgba(0,0,0,0.14); }}
    .status-banner {{ background: #e74c3c; color: white; font-size: 0.8rem; font-weight: 700; padding: 4px 12px; text-align: center; }}
    .card-img {{ background: #f0f0f0; }}
    .card-img img {{ width: 100%; max-height: 380px; object-fit: contain; display: block; }}
    .card-body {{ padding: 16px; flex: 1; display: flex; flex-direction: column; gap: 6px; }}
    .card-header-row {{ display: flex; justify-content: space-between; align-items: center; }}
    .badge {{ font-size: 0.75rem; padding: 3px 10px; border-radius: 999px; background: #eef2ff; color: #4361ee; font-weight: 600; }}
    .confidence {{ font-size: 0.7rem; letter-spacing: 1px; }}
    .card-title {{ font-size: 1.1rem; font-weight: 700; color: #1a202c; margin: 4px 0; line-height: 1.3; }}
    .card-date   {{ color: #2d6a4f; font-size: 0.9rem; font-weight: 600; }}
    .card-time   {{ color: #457b9d; font-size: 0.85rem; }}
    .card-location {{ color: #6b7280; font-size: 0.85rem; }}
    .card-price  {{ color: #b45309; font-size: 0.85rem; font-weight: 600; }}
    .price-note  {{ font-size: 0.78rem; font-weight: 400; color: #92400e; }}
    .price-tier  {{ font-size: 0.8rem; color: #92400e; margin-top: 2px; }}
    .price-tier.expired {{ color: #aaa; }}
    .expired-label {{ font-size: 0.72rem; color: #aaa; font-weight: 400; margin-right: 4px; }}
    .card-desc   {{ color: #374151; font-size: 0.85rem; line-height: 1.5; margin-top: 4px; flex: 1; }}
    .card-footer {{ margin-top: 10px; display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }}
    .reg-link {{ display: inline-block; padding: 6px 14px; background: #4361ee; color: white; border-radius: 8px; font-size: 0.8rem; font-weight: 600; text-decoration: none; }}
    .reg-link:hover {{ background: #3451d1; }}
    .contact {{ font-size: 0.78rem; color: #6b7280; }}
    .contact-wa {{ font-size: 0.78rem; color: #25d366; text-decoration: none; font-weight: 500; }}
    .contact-wa:hover {{ text-decoration: underline; }}
    .location-private {{ font-size: 0.78rem; color: #9061d4; font-style: italic; }}
    .cal-links {{ margin-top: 6px; display: flex; gap: 6px; flex-wrap: wrap; }}
    .cal-link {{ display: inline-block; padding: 4px 10px; border-radius: 6px; font-size: 0.75rem; font-weight: 600; text-decoration: none; }}
    .cal-link.gcal {{ background: #eef2ff; color: #4361ee; }}
    .cal-link.gcal:hover {{ background: #dce4ff; }}
    .cal-link.apple {{ background: #f0f0f0; color: #333; }}
    .cal-link.apple:hover {{ background: #e0e0e0; }}
    .cal-link.full-cal-apple {{ background: #2d6a4f; color: white; padding: 8px 20px; border-radius: 8px; font-size: 0.85rem; font-weight: 600; text-decoration: none; }}
    .cal-link.full-cal-apple:hover {{ background: #1b4332; }}
    .cal-link.full-cal-gcal {{ background: #4361ee; color: white; padding: 8px 20px; border-radius: 8px; font-size: 0.85rem; font-weight: 600; text-decoration: none; }}
    .cal-link.full-cal-gcal:hover {{ background: #3451d1; }}
    .cal-link.full-cal-dl {{ background: #6b7280; color: white; padding: 8px 20px; border-radius: 8px; font-size: 0.85rem; font-weight: 600; text-decoration: none; }}
    .cal-link.full-cal-dl:hover {{ background: #4b5563; }}
.header-actions {{ margin-top: 14px; display: flex; gap: 10px; justify-content: center; flex-wrap: wrap; }}
    footer {{ text-align: center; margin-top: 40px; color: #9ca3af; font-size: 0.8rem; }}
  </style>
</head>
<body>
  <header>
    <h1>🌿 אירועים קרובים</h1>
    <p>מרחב בריא – פרסום מרחבים ואירועים &nbsp;|&nbsp; {len(events)} אירועים</p>
    <div class="header-actions">{full_cal_html}</div>
  </header>
  <div class="grid">
    {cards_html}
  </div>
  <footer>נוצר מייצוא WhatsApp · {datetime.now().strftime('%d/%m/%Y %H:%M')}{_ver_str}</footer>
</body>
</html>"""

    OUTPUT_HTML.write_text(html, encoding='utf-8')
    print(f"  Written: {OUTPUT_HTML}")
    for e in events:
        print(f"    {_str(e.get('date_only')) or '?'}  {(_str(e.get('title')) or '')[:55]}")


# ─────────────────────────────────────────────────────────────────────────────
# STEP 2b – VALIDATE SCHEMA
# ─────────────────────────────────────────────────────────────────────────────
class ValidationError(Exception):
    pass

_VALID_STATUSES = {'scheduled', 'updated', 'postponed', 'canceled', 'tentative'}


def step_validate():
    print('\n── Step 2b: Validate schema ──')
    try:
        with open(EVENTS_JSON, encoding='utf-8') as f:
            data = json.load(f)
    except json.JSONDecodeError as ex:
        raise ValidationError(f'events.json is not valid JSON: {ex}')
    events = [e for e in _events_from_json(data) if isinstance(e, dict)]
    errors = 0
    for i, e in enumerate(events, 1):
        title = _str(e.get('title')) or f'[event {i}]'
        d = e.get('date_only') or e.get('event_start')
        if not d:
            print(f'  [{i}] "{title}": missing date_only')
            errors += 1
        else:
            try:
                date.fromisoformat(str(d)[:10])
            except ValueError:
                print(f'  [{i}] "{title}": invalid date: {d}')
                errors += 1
        etype = e.get('event_type')
        if etype and etype not in TYPE_LABELS:
            print(f'  [{i}] "{title}": unknown event_type "{etype}"')
            errors += 1
        status = e.get('status')
        if status and status not in _VALID_STATUSES:
            print(f'  [{i}] "{title}": unknown status "{status}"')
            errors += 1
        conf = e.get('confidence')
        if conf is not None:
            try:
                if not 0.0 <= float(conf) <= 1.0:
                    print(f'  [{i}] "{title}": confidence out of range: {conf}')
                    errors += 1
            except (TypeError, ValueError):
                print(f'  [{i}] "{title}": invalid confidence: {conf}')
                errors += 1
    if errors:
        print(f'  {errors} error(s) — fix events.json before continuing')
        raise ValidationError(f'{errors} validation error(s) in events.json')
    print(f'  {len(events)} events valid')


# ─────────────────────────────────────────────────────────────────────────────
# POST-ENRICH REPORT
# ─────────────────────────────────────────────────────────────────────────────
def step_report_missing():
    print('\n── Missing fields after enrichment ──')
    with open(EVENTS_JSON, encoding='utf-8') as f:
        data = json.load(f)
    events = [e for e in _events_from_json(data) if isinstance(e, dict)]
    fields = ['price_text', 'start_time_only', 'city']
    any_missing = False
    for e in events:
        missing = [f for f in fields if not _str(e.get(f))]
        if missing:
            title = (_str(e.get('title')) or '?')[:50]
            d = _str(e.get('date_only') or '')[:10]
            print(f'  ⚠  {d}  {title}: missing {", ".join(missing)}')
            any_missing = True
    if not any_missing:
        print('  All events have price, time and city.')


# ─────────────────────────────────────────────────────────────────────────────
# STEP 5 – GIT PUSH
# ─────────────────────────────────────────────────────────────────────────────
def step_push():
    print('\n── Step 5: Push to GitHub ──')
    cwd = Path(__file__).parent
    try:
        subprocess.run(
            ['git', '-C', str(cwd), 'pull', '--rebase', '--autostash'],
            check=True, capture_output=True, text=True, timeout=30
        )
    except subprocess.CalledProcessError as ex:
        print(f'  Warning: pull --rebase skipped ({ex.stderr.strip()[:100]})')
    subprocess.run(
        ['git', '-C', str(cwd), 'add', '-f', 'index.html', 'calendar.ics', 'events.json'],
        check=True
    )
    result = subprocess.run(
        ['git', '-C', str(cwd), 'commit', '-m', 'Update events'],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        if 'nothing to commit' in result.stdout + result.stderr:
            print('  Nothing to commit.')
            return
        raise RuntimeError(f'git commit failed: {result.stderr.strip()}')
    subprocess.run(['git', '-C', str(cwd), 'push'], check=True)
    print(f'  Published → {SITE_URL}')


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='מרחב בריא event pipeline')
    ap.add_argument('--force', action='store_true', help='Re-enrich all events, not just incomplete ones')
    ap.add_argument('--push',  action='store_true', help='Git-push after generating HTML')
    args = ap.parse_args()

    # Index photos before Step 1 overwrites _chat.txt with the trimmed version
    try:
        for i, line in enumerate(CHAT_FILE.read_text(encoding='utf-8').splitlines(), start=1):
            m = ATTACH_RE.search(line)
            if m: _LINE_TO_IMAGE[i] = m.group(1)
    except Exception as ex:
        print(f'Warning: could not index chat photos: {ex}')

    if os.environ.get('MERHAV_SKIP_TRIM', '') == '1':
        print('\n── Step 1: Trim skipped (MERHAV_SKIP_TRIM=1) ──')
    else:
        step_trim()
    step_clean()
    try:
        step_validate()
    except ValidationError as ex:
        print(f'\nAborted: {ex}')
        sys.exit(1)
    skip_enrich = os.environ.get('MERHAV_SKIP_ENRICH', '') == '1'
    if skip_enrich:
        print('\n── Step 3: Enrich skipped (MERHAV_SKIP_ENRICH=1) ──')
    else:
        asyncio.run(step_enrich(force=args.force))
    step_report_missing()
    step_html()
    if args.push:
        step_push()
    print('\nDone.')
