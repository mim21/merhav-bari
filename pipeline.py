#!/usr/bin/env python3
# pip install playwright opencv-python-headless && playwright install chromium
"""
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
"""

import asyncio
import base64
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG  ← edit only this section
# ─────────────────────────────────────────────────────────────────────────────
CHAT_FILE   = Path(r"c:/PRIVATE/merhav-bari/WhatsApp Chat - מרחב בריא - פרסום מרחבים ואירועים 1/_chat.txt")
EVENTS_JSON = Path(__file__).parent / "events.json"
OUTPUT_HTML = Path(__file__).parent / "events.html"

TRIM_DAYS        = 60   # strip chat messages older than this
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
    "ינואר": 1, "פברואר": 2, "מרץ": 3, "אפריל": 4,
    "מאי": 5,   "יוני": 6,   "יולי": 7, "אוגוסט": 8,
    "ספטמבר": 9, "אוקטובר": 10, "נובמבר": 11, "דצמבר": 12,
}
_HE_MONTH_RE = re.compile("|".join(_HE_MONTH_NUM.keys()))
# Numeric date patterns: 17.5  /  17/5  /  17.5.26  /  17.5.2026
_NUM_DATE_RE = re.compile(r"\b(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?\b")


def _parse_event_dates(text):
    """Return list of dates mentioned as event dates in a message block."""
    today = date.today()
    found = []

    # Hebrew month: "17 במאי", "17 מאי 2026", "יום שישי 17 מאי"
    for m in _HE_MONTH_RE.finditer(text):
        month = _HE_MONTH_NUM[m.group(0)]
        # look for day number near the month name (up to 10 chars before)
        prefix = text[max(0, m.start() - 10): m.start()]
        day_m  = re.search(r"(\d{1,2})\s*$", prefix)
        if not day_m:
            continue
        day = int(day_m.group(1))
        # look for year after month name
        suffix = text[m.end(): m.end() + 10]
        year_m = re.search(r"(\d{4}|\d{2})", suffix)
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
    """Split lines into (header_line, [all_lines]) message groups."""
    ts_re = re.compile(r"^\[(\d{2}/\d{2}/\d{4}), ")
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
    print("\n── Step 1: Trim chat ──")
    ts_re   = re.compile(r"^\[(\d{2}/\d{2}/\d{4}), ")
    cutoff  = datetime.now() - timedelta(days=TRIM_DAYS)
    yesterday = date.today() - timedelta(days=1)

    lines = CHAT_FILE.read_text(encoding="utf-8").splitlines(keepends=True)

    # Pass A – remove by post date
    after_a = []
    keep = False
    for line in lines:
        m = ts_re.match(line)
        if m:
            keep = datetime.strptime(m.group(1), "%d/%m/%Y") >= cutoff
        if keep:
            after_a.append(line)
    removed_a = len(lines) - len(after_a)
    print(f"  Pass A (post date): removed {removed_a} lines, kept {len(after_a)}")

    # Pass B – remove messages whose event date is in the past
    groups  = _group_messages(after_a)
    after_b = []
    removed_b = 0
    for group in groups:
        text = "".join(group)
        dates = _parse_event_dates(text)
        # Only drop if ALL found dates are in the past (so multi-date posts like
        # "7.5 or 28.5" survive until the last date passes)
        if dates and all(d <= yesterday for d in dates):
            removed_b += len(group)
        else:
            after_b.extend(group)
    print(f"  Pass B (event date): removed {removed_b} lines, kept {len(after_b)}")

    CHAT_FILE.write_text("".join(after_b), encoding="utf-8")
    first = after_b[0][:12] if after_b else "nothing"
    print(f"  Final: {len(after_b)} lines from {first}")


# ─────────────────────────────────────────────────────────────────────────────
# STEP 2 – CLEAN OLD EVENTS
# ─────────────────────────────────────────────────────────────────────────────
def step_clean():
    print("\n── Step 2: Clean old events ──")
    today         = date.today()
    cutoff_post   = today - timedelta(days=60)
    cutoff_event  = today - timedelta(days=1)

    with open(EVENTS_JSON, encoding="utf-8") as f:
        data = json.load(f)
    is_list = isinstance(data, list)
    events  = data if is_list else data["events"]
    print(f"  Loaded {len(events)} events")

    def get_event_date(e):
        for field in ("date_only", "event_start"):
            v = e.get(field)
            if v:
                try: return date.fromisoformat(str(v)[:10])
                except: pass
        return None

    def get_post_date(e):
        for msg in (e.get("source_messages") or []):
            ts = msg.get("source_message_timestamp", "") if isinstance(msg, dict) else ""
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
        title = (e.get("title") or "?")[:55]
        print(f"  Removed [{reason}]: {title}")
    print(f"  Keeping {len(kept)} events")

    out = kept if is_list else {"events": kept}
    with open(EVENTS_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)


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
SKIP_DOMAINS = {"chat.whatsapp.com", "wa.me", "instagram.com", "twitter.com", "t.me", "bit.ly"}
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
        host = urlparse(url).netloc.lower().lstrip("www.")
        return any(d in host for d in SKIP_DOMAINS)
    except: return False


def _collect_urls(event):
    urls, seen = [], set()
    link = event.get("registration_link") or ""
    if link and not _should_skip(link):
        urls.append(link); seen.add(link)
    for msg in (event.get("source_messages") or []):
        if isinstance(msg, dict):
            for u in URL_RE.findall(msg.get("source_excerpt") or ""):
                u = u.rstrip(".,)")
                if u not in seen and not _should_skip(u):
                    urls.append(u); seen.add(u)
    return urls[:MAX_URLS]


def _price_unit(text, pos):
    w = text[max(0, pos - 60): pos + 60]
    if COUPLE_RE.search(w): return "couple"
    if PERSON_RE.search(w): return "person"
    return None


def _best_price(text):
    candidates = []
    for m in PRICE_RE.finditer(text):
        try:
            val = float(m.group(1).replace(",", ""))
            candidates.append((val, _price_unit(text, m.start())))
        except: pass
    for m in PRICE_KW.finditer(text):
        try:
            val = float(m.group(1).replace(",", ""))
            candidates.append((val, _price_unit(text, m.start())))
        except: pass
    plausible = [(v, u) for v, u in candidates if PRICE_MIN <= v <= PRICE_MAX]
    if not plausible: return None, None
    vals = sorted(set(int(v) for v, u in plausible))
    units = [u for v, u in plausible if u]
    unit = "couple" if "couple" in units else "person" if "person" in units else None
    if len(vals) == 1: return f"{vals[0]}₪", unit
    return f"{vals[0]}₪–{vals[-1]}₪", unit


def _best_times(text):
    times = [f"{m.group(1)}:{m.group(2)}" for m in TIME_RE.finditer(text) if 7 <= int(m.group(1)) <= 23]
    if len(times) >= 2: return times[0], times[-1]
    if times: return times[0], None
    return None, None


def _enrich_from_text(event, text):
    changed = False
    if not event.get("price_text"):
        p, unit = _best_price(text)
        if p:
            event["price_text"] = p
            if unit and not event.get("price_unit"):
                event["price_unit"] = unit
            print(f"    price  → {p}")
            changed = True
    if "–" in (event.get("price_text") or "") and not event.get("price_note"):
        if INDIV_RE.search(text) and COUPLE_PRICE_RE.search(text):
            event["price_note"] = "יחיד / זוג"
            print(f"    price_note → יחיד / זוג")
            changed = True
        elif EARLY_BIRD_RE.search(text):
            event["price_note"] = "הרשמה מוקדמת / רגילה"
            print(f"    price_note → הרשמה מוקדמת / רגילה")
            changed = True
    if not event.get("start_time_only"):
        s, e = _best_times(text)
        if s:
            event["start_time_only"] = s
            if e and not event.get("end_time_only"):
                event["end_time_only"] = e
            print(f"    time   → {s}" + (f"–{e}" if e else ""))
            changed = True
    if not event.get("city"):
        m = CITY_RE.search(text)
        if m:
            event["city"] = m.group(1)
            print(f"    city   → {m.group(1)}")
            changed = True
    ci = event.setdefault("contact_info", {"phone": [], "email": [], "telegram": [], "instagram": [], "other": []})
    if isinstance(ci, dict) and not ci.get("phone"):
        phones = list(set(PHONE_RE.findall(text)))
        if phones:
            ci["phone"] = phones
            print(f"    phone  → {phones}")
            changed = True
    return changed


async def _fetch_text(page, url):
    is_fb = "facebook.com" in url
    try:
        try: await page.evaluate("window.stop()")
        except: pass
        await page.goto(url, timeout=TIMEOUT_MS, wait_until="domcontentloaded")
        await page.wait_for_timeout(WAIT_MS)
        if is_fb:
            try:
                close = await page.query_selector('[aria-label="Close"]')
                if close: await close.click()
                else: await page.keyboard.press("Escape")
                await page.wait_for_timeout(1500)
            except: pass
        text = await page.inner_text("body") or ""
        if len(text.strip()) < 50:
            await page.wait_for_timeout(3000)
            text = await page.inner_text("body") or ""
        return text
    except Exception as ex:
        print(f"      error: {ex}")
        return ""


async def _get_sublinks(page, base_url):
    try:
        from urllib.parse import urlparse
        found, seen = [], set()
        for a in await page.query_selector_all("a[href]"):
            href = await a.get_attribute("href") or ""
            text = (await a.inner_text() or "").strip()
            if href.startswith("/"):
                p = urlparse(base_url)
                href = f"{p.scheme}://{p.netloc}{href}"
            if not href.startswith("http") or _should_skip(href) or href == base_url: continue
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
        if event.get("price_text") and event.get("start_time_only") and event.get("city"):
            break
    return changed


async def step_enrich():
    print("\n── Step 3: Enrich with Playwright ──")
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("  playwright not installed — skipping (run: pip install playwright && playwright install chromium)")
        return

    with open(EVENTS_JSON, encoding="utf-8") as f:
        data = json.load(f)
    is_list = isinstance(data, list)
    events  = data if is_list else data["events"]

    def _needs_enrich(e):
        if any(not e.get(k) for k in ["price_text", "start_time_only", "city"]):
            return True
        if "–" in (e.get("price_text") or "") and not e.get("price_note"):
            return True
        return False
    to_enrich = [(i, e) for i, e in enumerate(events, 1) if _needs_enrich(e)]
    print(f"  Enriching {len(to_enrich)}/{len(events)} events ({CONCURRENCY} parallel pages)")

    sem, lock = asyncio.Semaphore(CONCURRENCY), asyncio.Lock()
    total = [0]

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(locale="he-IL", extra_http_headers={"Accept-Language": "he,en;q=0.9"})

        async def worker(idx, event):
            async with sem:
                page = await ctx.new_page()
                try:
                    label = f"[{idx:02d}/{len(events)}] {(event.get('title') or '')[:40]}"
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
    with open(EVENTS_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ─────────────────────────────────────────────────────────────────────────────
# STEP 4 – GENERATE HTML
# ─────────────────────────────────────────────────────────────────────────────
ATTACH_RE = re.compile(r"<attached:\s*([\w\-\.]+\.(?:jpg|jpeg|png|mp4))\s*>", re.IGNORECASE)

HE_MONTHS = ["", "ינואר", "פברואר", "מרץ", "אפריל", "מאי", "יוני",
             "יולי", "אוגוסט", "ספטמבר", "אוקטובר", "נובמבר", "דצמבר"]
HE_DAYS   = ["שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון"]

TYPE_LABELS = {
    "concert":      ("🎵", "קונצרט"),
    "lecture":      ("🎤", "הרצאה"),
    "meetup":       ("🤝", "מפגש"),
    "party":        ("🎉", "מסיבה"),
    "workshop":     ("🛠", "סדנה"),
    "screening":    ("🎬", "הקרנה"),
    "exhibition":   ("🖼", "תערוכה"),
    "class":        ("📚", "שיעור"),
    "sale":         ("🏷", "מכירה"),
    "cuddle_party": ("🫂", "כרבולים"),
    "other":        ("📅", "אירוע"),
}
STATUS_STYLES = {
    "scheduled": ("",          "white"),
    "updated":   ("🔄 עודכן",  "#e8f4fd"),
    "postponed": ("⏸ נדחה",   "#fff3cd"),
    "canceled":  ("❌ בוטל",   "#fde8e8"),
    "tentative": ("❓ מותנה",  "#f9f9f9"),
}


def _mp4_thumbnail(path):
    try:
        import cv2
        cap = cv2.VideoCapture(str(path))
        ok, frame = cap.read()
        cap.release()
        if not ok: return None
        ok2, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok2: return None
        return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode("ascii")
    except Exception: return None


def _img_uri(chat_folder, filename):
    path = chat_folder / filename
    if not path.exists(): return None
    try:
        ext = path.suffix.lower().lstrip(".")
        if ext == "mp4": return _mp4_thumbnail(path)
        data = base64.b64encode(path.read_bytes()).decode("ascii")
        mime = "jpeg" if ext in ("jpg", "jpeg") else ext
        return f"data:image/{mime};base64,{data}"
    except: return None


def _find_image(event, line_to_image):
    for msg in (event.get("source_messages") or []):
        if not isinstance(msg, dict): continue
        ref = msg.get("line_reference")
        if ref is None: continue
        try: base = int(str(ref).strip())
        except: continue
        for offset in sorted(range(-40, 31), key=abs):
            img = line_to_image.get(base + offset)
            if img: return img
    return None


def _format_date(event):
    d = event.get("date_only")
    if not d: return event.get("raw_date_text") or ""
    try:
        dt = date.fromisoformat(str(d))
        end = event.get("end_date_only")
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


def _make_card(event, chat_folder, line_to_image):
    img_tag = ""
    img_file = event.get("_image_filename") or _find_image(event, line_to_image)
    if img_file:
        uri = _img_uri(chat_folder, img_file)
        if uri:
            img_tag = f'<div class="card-img"><img src="{uri}" alt="" loading="lazy"/></div>'

    etype = event.get("event_type", "other")
    icon, label = TYPE_LABELS.get(etype, ("📅", etype))
    status = event.get("status", "scheduled")
    status_label, card_bg = STATUS_STYLES.get(status, ("", "white"))
    status_html = f'<div class="status-banner">{status_label}</div>' if status_label else ""

    title    = event.get("title") or event.get("normalized_title") or "אירוע"
    date_str = _format_date(event)
    s, e     = event.get("start_time_only"), event.get("end_time_only")
    time_str = f"{s} – {e}" if s and e else s or ""

    loc_name    = event.get("location_name") or ""
    loc_private = "יימסר לנרשמים" in loc_name or "לנרשמים" in loc_name
    if loc_private:
        loc_clean = loc_name.split("(")[0].strip() if "(" in loc_name else ""
        loc_parts = [p for p in [loc_clean, event.get("city")] if p]
    else:
        loc_parts = [p for p in [loc_name, event.get("city")] if p]
    location = " · ".join(loc_parts)

    price_raw     = event.get("price_text") or ""
    price_unit    = event.get("price_unit") or ""
    unit_label    = " לזוג" if price_unit == "couple" else " לאדם" if price_unit == "person" else ""
    price         = f"{price_raw}{unit_label}" if price_raw else ""
    price_note    = event.get("price_note") or ""
    price_details = event.get("price_details") or []  # list of strings, one per tier

    desc = event.get("description") or ""
    link = event.get("registration_link") or ""
    link_html = f'<a class="reg-link" href="{link}" target="_blank">להרשמה ←</a>' if link else ""

    contacts = []
    ci = event.get("contact_info") or {}
    if isinstance(ci, dict):
        for p in ci.get("phone", []):
            digits = re.sub(r'\D', '', p)
            if digits.startswith("0"): digits = "972" + digits[1:]
            wa_url = f"https://wa.me/{digits}"
            contacts.append(f'<a href="{wa_url}" target="_blank" class="contact-wa">💬 {p}</a>')
        for t in ci.get("telegram", []): contacts.append(f'<span class="contact">✈️ {t}</span>')
        for i in ci.get("instagram", []): contacts.append(f'<span class="contact">📷 {i}</span>')
    contact_html = "".join(contacts)

    conf = event.get("confidence", 0)
    conf_color = "#28a745" if conf >= 0.8 else "#ffc107" if conf >= 0.5 else "#dc3545"
    dots = "●" * round(conf * 5) + "○" * (5 - round(conf * 5))

    return f"""<div class="card" style="background:{card_bg}">
  {status_html}
  {img_tag}
  <div class="card-body">
    <div class="card-header-row">
      <span class="badge">{icon} {label}</span>
      <span class="confidence" style="color:{conf_color}" title="{int(conf*100)}%">{dots}</span>
    </div>
    <h2 class="card-title">{title}</h2>
    {"<div class='card-date'>📅 " + date_str + "</div>" if date_str else ""}
    {"<div class='card-time'>🕐 " + time_str + "</div>" if time_str else ""}
    {"<div class='card-location'>📍 " + location + "</div>" if location else ""}
    {"<div class='card-location-note'>🔒 מיקום מדויק יישלח לנרשמים</div>" if loc_private else ""}
    {"<div class='card-price'>💰 " + "".join(f"<div class='price-tier'>{t}</div>" for t in price_details) + "</div>" if price_details else ("<div class='card-price'>💰 " + price + ("  <span class='price-note'>(" + price_note + ")</span>" if price_note else "") + "</div>" if price else "")}
    {"<div class='card-desc'>" + desc + "</div>" if desc else ""}
    <div class="card-footer">{link_html}{contact_html}</div>
  </div>
</div>"""


def step_html():
    print("\n── Step 4: Generate HTML ──")
    chat_folder = CHAT_FILE.parent

    # Build line → image filename map
    line_to_image = {}
    try:
        for i, line in enumerate(CHAT_FILE.read_text(encoding="utf-8").splitlines(), start=1):
            m = ATTACH_RE.search(line)
            if m: line_to_image[i] = m.group(1)
    except Exception as ex:
        print(f"  Warning: could not read chat for image map: {ex}")

    with open(EVENTS_JSON, encoding="utf-8") as f:
        all_events = json.load(f)
    if isinstance(all_events, dict): all_events = all_events["events"]

    show_from = (date.today() - timedelta(days=SHOW_DAYS_AGO)).isoformat()
    events = [e for e in all_events if (e.get("date_only") or e.get("event_start") or "") >= show_from]
    events.sort(key=lambda e: (e.get("date_only") or e.get("event_start") or ""))
    print(f"  Showing {len(events)} events (from {show_from})")

    cards_html = "\n".join(_make_card(e, chat_folder, line_to_image) for e in events)

    html = f"""<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
  <meta charset="UTF-8"/>
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
    .card-desc   {{ color: #374151; font-size: 0.85rem; line-height: 1.5; margin-top: 4px; flex: 1; }}
    .card-footer {{ margin-top: 10px; display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }}
    .reg-link {{ display: inline-block; padding: 6px 14px; background: #4361ee; color: white; border-radius: 8px; font-size: 0.8rem; font-weight: 600; text-decoration: none; }}
    .reg-link:hover {{ background: #3451d1; }}
    .contact {{ font-size: 0.78rem; color: #6b7280; }}
    .contact-wa {{ font-size: 0.78rem; color: #25d366; text-decoration: none; font-weight: 500; }}
    .contact-wa:hover {{ text-decoration: underline; }}
    .card-location-note {{ font-size: 0.78rem; color: #9061d4; font-style: italic; }}
    footer {{ text-align: center; margin-top: 40px; color: #9ca3af; font-size: 0.8rem; }}
  </style>
</head>
<body>
  <header>
    <h1>🌿 אירועים קרובים</h1>
    <p>מרחב בריא – פרסום מרחבים ואירועים &nbsp;|&nbsp; {len(events)} אירועים</p>
  </header>
  <div class="grid">
    {cards_html}
  </div>
  <footer>נוצר מייצוא WhatsApp · {datetime.now().strftime("%d/%m/%Y %H:%M")}</footer>
</body>
</html>"""

    OUTPUT_HTML.write_text(html, encoding="utf-8")
    print(f"  Written: {OUTPUT_HTML}")
    for e in events:
        print(f"    {e.get('date_only','?')}  {(e.get('title') or '')[:55]}")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    step_trim()
    step_clean()
    asyncio.run(step_enrich())
    step_html()
    print("\nDone.")
