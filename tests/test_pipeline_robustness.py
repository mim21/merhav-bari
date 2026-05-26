"""
Adversarial test suite for pipeline.py — covers the JSON-input boundary functions
that have accumulated hardening across many review rounds.

Run with:
    python -m unittest tests.test_pipeline_robustness -v

Or for a quick smoke test:
    python tests/test_pipeline_robustness.py

No extra dependencies — pure stdlib unittest.
"""

import json
import os
import re
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pipeline  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Adversarial input sets reused across many tests
# ─────────────────────────────────────────────────────────────────────────────

BAD_VALUES_NON_STRING = [None, 0, 1, -1, 1.5, True, False, [], {}, [1, 2, 3],
                         {'nested': 'dict'}]
BAD_VALUES_NON_LIST = [None, '', 'string', 0, 1, True, {}, {'a': 1}]

HTML_INJECTION_STRINGS = [
    '<script>alert(1)</script>',
    '"><script>alert(1)</script>',
    "' onmouseover='alert(1)",
    '" onmouseover="alert(1)',
    '<img src=x onerror=alert(1)>',
    '</style><script>alert(1)</script>',
    'javascript:alert(1)',
    'data:text/html,<script>alert(1)</script>',
    'vbscript:alert(1)',
    'JAVASCRIPT:alert(1)',
    '  javascript:alert(1)  ',
]

ICS_INJECTION_STRINGS = [
    'normal\r\nBEGIN:VEVENT\r\nUID:fake@evil\r\nEND:VEVENT',
    'normal\nBEGIN:VEVENT\nEND:VEVENT',
    'normal\rinjected',
    'a; ATTENDEE=evil@example.com',
    'a, evil',
    'a\x00b',
    'a\x07b',
    'a\x1bb',
    'a\x7fb',
    'a\x0bb',
    'a\x0cb',
]

PATH_TRAVERSAL_FILENAMES = [
    '../etc/passwd',
    '..\\windows\\system32',
    '/etc/passwd',
    'image.jpg:hidden.exe',
    'foo/bar.jpg',
    'image.jpg\x00.txt',
]


# ─────────────────────────────────────────────────────────────────────────────
# Type guards
# ─────────────────────────────────────────────────────────────────────────────

class TestStr(unittest.TestCase):

    def test_strings_pass_through(self):
        for s in ['', 'hello', 'שלום', '🎉', ' ', '\n', '\x00', 'a' * 10000]:
            self.assertEqual(pipeline._str(s), s)

    def test_non_strings_become_empty(self):
        for v in BAD_VALUES_NON_STRING:
            self.assertEqual(pipeline._str(v), '')


class TestList(unittest.TestCase):

    def test_lists_pass_through(self):
        for L in [[], [1], [None], [{'a': 1}]]:
            self.assertEqual(pipeline._list(L), L)

    def test_non_lists_become_empty(self):
        for v in BAD_VALUES_NON_LIST:
            self.assertEqual(pipeline._list(v), [])


class TestEventsFromJson(unittest.TestCase):

    def test_list_input(self):
        self.assertEqual(pipeline._events_from_json([]), [])
        self.assertEqual(pipeline._events_from_json([{'a': 1}]), [{'a': 1}])

    def test_dict_with_events_key(self):
        self.assertEqual(pipeline._events_from_json({'events': []}), [])
        self.assertEqual(pipeline._events_from_json({'events': [{'a': 1}]}), [{'a': 1}])

    def test_dict_missing_events_key(self):
        self.assertEqual(pipeline._events_from_json({}), [])

    def test_dict_with_non_list_events(self):
        for bad in BAD_VALUES_NON_LIST:
            self.assertEqual(pipeline._events_from_json({'events': bad}), [])

    def test_garbage_top_level(self):
        for bad in [None, 0, 1, 1.5, 'string', True, False]:
            self.assertEqual(pipeline._events_from_json(bad), [])


# ─────────────────────────────────────────────────────────────────────────────
# URL safety
# ─────────────────────────────────────────────────────────────────────────────

class TestSafeUrl(unittest.TestCase):

    def test_valid_http_https(self):
        for u in ['http://example.com', 'https://example.com',
                  'https://example.com/path?q=1', 'HTTP://EXAMPLE.COM']:
            self.assertEqual(pipeline._safe_url(u), u.strip())

    def test_whitespace_stripped(self):
        self.assertEqual(pipeline._safe_url('  https://example.com  '),
                         'https://example.com')

    def test_dangerous_schemes_rejected(self):
        for bad in ['javascript:alert(1)', 'data:text/html,<script>',
                    'vbscript:msgbox', 'file:///etc/passwd', 'ftp://example.com']:
            self.assertEqual(pipeline._safe_url(bad), '')

    def test_empty_netloc_rejected(self):
        for bad in ['https://', 'http://', 'https:', 'http:']:
            self.assertEqual(pipeline._safe_url(bad), '')

    def test_non_strings(self):
        for v in BAD_VALUES_NON_STRING:
            self.assertEqual(pipeline._safe_url(v), '')

    def test_scheme_case_bypass_rejected(self):
        for v in ['JaVaScRiPt:alert(1)', 'JAVASCRIPT:alert(1)']:
            self.assertEqual(pipeline._safe_url(v), '')


# ─────────────────────────────────────────────────────────────────────────────
# File path safety
# ─────────────────────────────────────────────────────────────────────────────

class TestImgUri(unittest.TestCase):

    def setUp(self):
        import shutil
        self.tmp = tempfile.mkdtemp()
        self.chat_folder = Path(self.tmp)
        self.good_jpg = self.chat_folder / 'good.jpg'
        self.good_jpg.write_bytes(b'\xff\xd8\xff\xd9')

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_valid_image_returns_data_uri(self):
        result = pipeline._img_uri(self.chat_folder, 'good.jpg')
        self.assertIsNotNone(result)
        self.assertTrue(result.startswith('data:image/jpeg;base64,'))

    def test_non_string_filename_rejected(self):
        for v in BAD_VALUES_NON_STRING:
            self.assertIsNone(pipeline._img_uri(self.chat_folder, v))

    def test_path_traversal_rejected(self):
        for bad in PATH_TRAVERSAL_FILENAMES:
            self.assertIsNone(pipeline._img_uri(self.chat_folder, bad))

    def test_disallowed_extensions_rejected(self):
        for bad in ['file.txt', 'file.exe', 'file.php', 'file.svg', 'file.html']:
            self.assertIsNone(pipeline._img_uri(self.chat_folder, bad))

    def test_allowed_extensions_case_insensitive(self):
        for ext in ['jpg', 'JPG', 'jpeg', 'png', 'PNG']:
            f = self.chat_folder / f'test.{ext}'
            f.write_bytes(b'\xff\xd8\xff\xd9')
            try:
                self.assertIsNotNone(pipeline._img_uri(self.chat_folder, f'test.{ext}'))
            finally:
                f.unlink()

    def test_oversize_file_rejected(self):
        big = self.chat_folder / 'big.jpg'
        big.write_bytes(b'\x00' * 10_000_001)
        try:
            self.assertIsNone(pipeline._img_uri(self.chat_folder, 'big.jpg'))
        finally:
            big.unlink()

    def test_nonexistent_file_returns_none(self):
        self.assertIsNone(pipeline._img_uri(self.chat_folder, 'missing.jpg'))

    def test_empty_string_rejected(self):
        self.assertIsNone(pipeline._img_uri(self.chat_folder, ''))


# ─────────────────────────────────────────────────────────────────────────────
# Slug generation
# ─────────────────────────────────────────────────────────────────────────────

class TestEventSlug(unittest.TestCase):

    def test_normal_title(self):
        s = pipeline._event_slug({'title': 'Hello World', 'date_only': '2026-05-20'})
        self.assertEqual(s, 'event-Hello-World-20260520')

    def test_hebrew_title_preserved(self):
        # Hebrew \w chars must survive the slug regex
        s = pipeline._event_slug({'title': 'סדנה מגע', 'date_only': '2026-05-20'})
        self.assertIn('סדנה', s)
        self.assertIn('מגע', s)
        self.assertTrue(s.startswith('event-'))

    def test_time_suffix_included(self):
        s = pipeline._event_slug({'title': 'Test', 'date_only': '2026-05-20',
                                   'start_time_only': '18:30'})
        self.assertEqual(s, 'event-Test-20260520-1830')

    def test_empty_title_fallback(self):
        s = pipeline._event_slug({'title': '', 'date_only': '2026-05-20'})
        self.assertTrue(s.startswith('event-'))

    def test_punctuation_only_title_untitled(self):
        s = pipeline._event_slug({'title': '!@#$%', 'date_only': '2026-05-20'})
        self.assertIn('untitled', s)

    def test_non_string_title(self):
        for bad in BAD_VALUES_NON_STRING:
            s = pipeline._event_slug({'title': bad, 'date_only': '2026-05-20'})
            self.assertTrue(s.startswith('event-'))
            for ch in '<>"\'':
                self.assertNotIn(ch, s)

    def test_no_date(self):
        s = pipeline._event_slug({'title': 'Test'})
        self.assertEqual(s, 'event-Test')

    def test_safe_chars_only(self):
        safe = re.compile(r'^event-[\w\-]+$', re.UNICODE)
        for title in ['Hello', 'שלום', '🎉 party', 'a/b', 'a&b', 'a\nb',
                      '<script>alert(1)</script>', '../../../etc/passwd', 'a"b']:
            s = pipeline._event_slug({'title': title, 'date_only': '2026-05-20'})
            self.assertRegex(s, safe)

    def test_no_whitespace_or_quotes_in_slug(self):
        for title in HTML_INJECTION_STRINGS + ['a b', "a'b", 'a"b', 'a\nb']:
            s = pipeline._event_slug({'title': title, 'date_only': '2026-05-20'})
            for ch in ' "\'<>\n\r\t':
                self.assertNotIn(ch, s)


# ─────────────────────────────────────────────────────────────────────────────
# Date formatting
# ─────────────────────────────────────────────────────────────────────────────

class TestFormatDate(unittest.TestCase):

    def test_iso_date_contains_year(self):
        s = pipeline._format_date({'date_only': '2026-05-20'})
        self.assertIn('2026', s)

    def test_event_start_fallback(self):
        s = pipeline._format_date({'event_start': '2026-05-20T18:00:00'})
        self.assertIn('2026', s)

    def test_missing_returns_raw_text(self):
        self.assertEqual(pipeline._format_date({'raw_date_text': 'next Tuesday'}),
                         'next Tuesday')

    def test_missing_everything_returns_empty(self):
        self.assertEqual(pipeline._format_date({}), '')

    def test_invalid_date_does_not_crash(self):
        for bad in ['not a date', '2026-99-99', 'garbage']:
            try:
                result = pipeline._format_date({'date_only': bad})
                self.assertIsInstance(result, str)
            except Exception as e:
                self.fail(f'_format_date crashed on {bad!r}: {e}')

    def test_non_string_date_does_not_crash(self):
        for bad in [None, 0, [], {}]:
            try:
                result = pipeline._format_date({'date_only': bad})
                self.assertIsInstance(result, str)
            except Exception as e:
                self.fail(f'_format_date crashed on date_only={bad!r}: {e}')


# ─────────────────────────────────────────────────────────────────────────────
# ICS escaping
# ─────────────────────────────────────────────────────────────────────────────

class TestIcsEscape(unittest.TestCase):

    def test_backslash_escaped(self):
        self.assertEqual(pipeline._ics_escape('a\\b'), 'a\\\\b')

    def test_newline_escaped(self):
        self.assertEqual(pipeline._ics_escape('a\nb'), 'a\\nb')

    def test_crlf_normalized_then_escaped(self):
        self.assertEqual(pipeline._ics_escape('a\r\nb'), 'a\\nb')

    def test_cr_alone_normalized(self):
        self.assertEqual(pipeline._ics_escape('a\rb'), 'a\\nb')

    def test_comma_escaped(self):
        self.assertEqual(pipeline._ics_escape('a,b'), 'a\\,b')

    def test_semicolon_escaped(self):
        self.assertEqual(pipeline._ics_escape('a;b'), 'a\\;b')

    def test_c0_controls_stripped(self):
        for ch in ['\x00', '\x01', '\x07', '\x0b', '\x0c', '\x0e', '\x1b', '\x1f', '\x7f']:
            result = pipeline._ics_escape(f'a{ch}b')
            self.assertEqual(result, 'ab', msg=f'Failed to strip 0x{ord(ch):02x}')

    def test_tab_preserved(self):
        self.assertEqual(pipeline._ics_escape('a\tb'), 'a\tb')

    def test_crlf_injection_neutralized(self):
        attack = 'normal\r\nBEGIN:VEVENT\r\nUID:fake\r\nEND:VEVENT'
        escaped = pipeline._ics_escape(attack)
        self.assertNotIn('\r', escaped)
        self.assertNotIn('\n', escaped)
        self.assertIn('\\n', escaped)

    def test_non_string_input(self):
        for bad in BAD_VALUES_NON_STRING:
            try:
                result = pipeline._ics_escape(bad)
                self.assertEqual(result, '')
            except Exception as e:
                self.fail(f'_ics_escape crashed on {bad!r}: {e}')

    def test_unicode_preserved(self):
        for s in ['שלום', '🎉', 'café', 'müller']:
            self.assertEqual(pipeline._ics_escape(s), s)

    def test_double_backslash_then_comma(self):
        self.assertEqual(pipeline._ics_escape('\\,'), '\\\\\\,')


# ─────────────────────────────────────────────────────────────────────────────
# Price tier rendering
# ─────────────────────────────────────────────────────────────────────────────

class TestRenderPriceTier(unittest.TestCase):

    def test_normal_tier(self):
        s = pipeline._render_price_tier('180₪')
        self.assertIn('180₪', s)
        self.assertIn("class='price-tier'", s)

    def test_html_escaping(self):
        from html.parser import HTMLParser

        class StructureChecker(HTMLParser):
            def __init__(self):
                super().__init__()
                self.tags = []
                self.bad_attrs = []

            def handle_starttag(self, tag, attrs):
                self.tags.append(tag)
                for name, val in attrs:
                    if name != 'class':
                        self.bad_attrs.append((tag, name, val))

        for attack in HTML_INJECTION_STRINGS:
            result = pipeline._render_price_tier(attack)
            c = StructureChecker()
            c.feed(result)
            for tag in c.tags:
                self.assertIn(tag, {'div', 's', 'span'},
                              msg=f'Attack {attack!r} opened unexpected <{tag}>')
            self.assertEqual(c.bad_attrs, [],
                             msg=f'Attack {attack!r} injected attrs: {c.bad_attrs}')

    def test_non_string_input_does_not_crash(self):
        for bad in BAD_VALUES_NON_STRING:
            try:
                result = pipeline._render_price_tier(bad)
                self.assertIsInstance(result, str)
            except Exception as e:
                self.fail(f'_render_price_tier crashed on {bad!r}: {e}')

    def test_expired_tier_marked(self):
        if date.today() > date(date.today().year, 1, 2):
            result = pipeline._render_price_tier('180₪ עד 01/01')
            self.assertIn('expired', result)
            self.assertIn('<s>', result)


# ─────────────────────────────────────────────────────────────────────────────
# Calendar VEVENT building
# ─────────────────────────────────────────────────────────────────────────────

class TestEventCalData(unittest.TestCase):

    def _e(self, **kw):
        e = {'title': 'Test', 'date_only': '2026-05-20'}
        e.update(kw)
        return e

    @staticmethod
    def _structural(vevent_lines, prefix):
        return sum(1 for L in vevent_lines if L == prefix or L.startswith(prefix + ':'))

    def test_minimal_event(self):
        gs, ge, timed, gcal_url, vevent = pipeline._event_cal_data(self._e())
        self.assertFalse(timed)
        self.assertEqual(gs, '20260520')
        self.assertEqual(ge, '20260521')
        self.assertIn('BEGIN:VEVENT', vevent)
        self.assertIn('END:VEVENT', vevent)

    def test_timed_event(self):
        gs, ge, timed, _, _ = pipeline._event_cal_data(
            self._e(start_time_only='18:00', end_time_only='20:00'))
        self.assertTrue(timed)
        self.assertEqual(gs, '20260520T180000')
        self.assertEqual(ge, '20260520T200000')

    def test_midnight_crossover(self):
        gs, ge, _, _, _ = pipeline._event_cal_data(
            self._e(start_time_only='20:00', end_time_only='00:00'))
        self.assertEqual(ge, '20260521T000000')

    def test_no_end_time_adds_2_hours(self):
        _, ge, _, _, _ = pipeline._event_cal_data(self._e(start_time_only='18:00'))
        self.assertEqual(ge, '20260520T200000')

    def test_no_date_returns_none(self):
        self.assertIsNone(pipeline._event_cal_data({'title': 'No date'}))

    def test_invalid_date_returns_none(self):
        self.assertIsNone(pipeline._event_cal_data(self._e(date_only='garbage')))

    def test_non_string_date_returns_none(self):
        for bad in [None, 0, [], {}]:
            self.assertIsNone(pipeline._event_cal_data(self._e(date_only=bad)))

    def test_ics_injection_blocked(self):
        for attack in ICS_INJECTION_STRINGS:
            gs, ge, timed, gcal_url, vevent = pipeline._event_cal_data(
                self._e(title=attack))
            # Structural line count — not substring count
            self.assertEqual(self._structural(vevent, 'BEGIN:VEVENT'), 1,
                             msg=f'Title {attack!r} broke structure')
            self.assertEqual(self._structural(vevent, 'END:VEVENT'), 1)
            for line in vevent:
                self.assertNotIn('\r', line)
                self.assertNotIn('\n', line)

    def test_description_injection_blocked(self):
        for attack in ICS_INJECTION_STRINGS:
            _, _, _, _, vevent = pipeline._event_cal_data(self._e(description=attack))
            self.assertEqual(self._structural(vevent, 'BEGIN:VEVENT'), 1)
            for line in vevent:
                self.assertNotIn('\r', line)
                self.assertNotIn('\n', line)

    def test_location_injection_blocked(self):
        for attack in ICS_INJECTION_STRINGS:
            _, _, _, _, vevent = pipeline._event_cal_data(self._e(location_name=attack))
            self.assertEqual(self._structural(vevent, 'BEGIN:VEVENT'), 1)
            for line in vevent:
                self.assertNotIn('\r', line)
                self.assertNotIn('\n', line)

    def test_uid_different_for_different_times(self):
        _, _, _, _, v1 = pipeline._event_cal_data(self._e(start_time_only='18:00'))
        _, _, _, _, v2 = pipeline._event_cal_data(self._e(start_time_only='19:00'))
        uid1 = next(L for L in v1 if L.startswith('UID:'))
        uid2 = next(L for L in v2 if L.startswith('UID:'))
        self.assertNotEqual(uid1, uid2)

    def test_uid_stable_across_calls(self):
        e = self._e(start_time_only='18:00')
        _, _, _, _, v1 = pipeline._event_cal_data(e)
        _, _, _, _, v2 = pipeline._event_cal_data(e)
        self.assertEqual(next(L for L in v1 if L.startswith('UID:')),
                         next(L for L in v2 if L.startswith('UID:')))

    def test_url_percent_encoded(self):
        url = 'https://example.com/#event-שלום-20260520'
        _, _, _, _, vevent = pipeline._event_cal_data(self._e(), event_url=url)
        url_line = next((L for L in vevent if L.startswith('URL:')), None)
        self.assertIsNotNone(url_line)
        self.assertNotIn('שלום', url_line)
        self.assertIn('%', url_line)


# ─────────────────────────────────────────────────────────────────────────────
# Full calendar
# ─────────────────────────────────────────────────────────────────────────────

class TestMakeFullCal(unittest.TestCase):

    def test_empty_event_list(self):
        html, ics = pipeline._make_full_cal([])
        self.assertIn('BEGIN:VCALENDAR', ics)
        self.assertIn('END:VCALENDAR', ics)
        self.assertNotIn('BEGIN:VEVENT', ics)

    def test_mixed_valid_invalid(self):
        events = [
            {'title': 'Valid', 'date_only': '2026-05-20'},
            {'title': 'No date'},
            {'title': 'Bad date', 'date_only': 'garbage'},
            {'title': 'Another valid', 'date_only': '2026-05-21'},
        ]
        _, ics = pipeline._make_full_cal(events)
        lines = ics.split('\r\n')
        self.assertEqual(sum(1 for L in lines if L == 'BEGIN:VEVENT'), 2)
        self.assertEqual(sum(1 for L in lines if L == 'END:VEVENT'), 2)

    def test_ics_well_formed(self):
        _, ics = pipeline._make_full_cal([{'title': 'X', 'date_only': '2026-05-20'}])
        self.assertEqual(ics.count('BEGIN:VCALENDAR'), ics.count('END:VCALENDAR'))
        self.assertEqual(ics.count('BEGIN:VEVENT'), ics.count('END:VEVENT'))
        self.assertIn('\r\n', ics)

    def test_adversarial_title_does_not_break_calendar(self):
        for attack in ICS_INJECTION_STRINGS:
            events = [{'title': attack, 'date_only': '2026-05-20'},
                      {'title': 'Normal', 'date_only': '2026-05-21'}]
            _, ics = pipeline._make_full_cal(events)
            lines = ics.split('\r\n')
            self.assertEqual(sum(1 for L in lines if L == 'BEGIN:VEVENT'), 2,
                             msg=f'Attack {attack!r} broke VEVENT structure')
            self.assertEqual(sum(1 for L in lines if L == 'END:VEVENT'), 2)

    def test_subscribe_urls_use_hardcoded_site(self):
        html, _ = pipeline._make_full_cal([])
        self.assertIn('webcal://mim21.github.io/merhav-bari/calendar.ics', html)
        self.assertIn('calendar.google.com/calendar/r?cid=', html)

    def test_download_link_is_file_not_data_uri(self):
        html, _ = pipeline._make_full_cal([])
        self.assertIn('href="calendar.ics"', html)
        self.assertNotIn('href="data:text/calendar', html)


# ─────────────────────────────────────────────────────────────────────────────
# Per-event calendar buttons
# ─────────────────────────────────────────────────────────────────────────────

class TestMakeCalLinks(unittest.TestCase):

    def test_no_date_returns_empty(self):
        self.assertEqual(pipeline._make_cal_links({'title': 'No date'}), '')

    def test_minimal_event_has_both_buttons(self):
        html = pipeline._make_cal_links({'title': 'Test', 'date_only': '2026-05-20'})
        self.assertIn('Google', html)
        self.assertIn('Apple', html)
        self.assertIn('data:text/calendar;base64,', html)

    def test_html_attribute_injection_blocked(self):
        from html.parser import HTMLParser
        EXPECTED = {'class', 'href', 'target', 'rel', 'download'}

        for attack in HTML_INJECTION_STRINGS:
            html_out = pipeline._make_cal_links(
                {'title': attack, 'date_only': '2026-05-20'})

            class Checker(HTMLParser):
                def __init__(self):
                    super().__init__()
                    self.bad = []
                def handle_starttag(self, tag, attrs):
                    for name, val in attrs:
                        if name.lower() not in EXPECTED:
                            self.bad.append((tag, name, val))

            c = Checker()
            c.feed(html_out)
            self.assertEqual(c.bad, [],
                             msg=f'Attack {attack!r} injected attrs: {c.bad}')

    def test_download_filename_no_forbidden_chars(self):
        for attack in ['../../../etc/passwd', 'evil\\.exe', 'foo:bar', 'a*b', 'a?b']:
            html = pipeline._make_cal_links(
                {'title': attack, 'date_only': '2026-05-20'})
            m = re.search(r'download="([^"]*)"', html)
            if m:
                fn = m.group(1)
                for ch in ['\\', '/', ':', '"', '*', '?', '<', '>', '|']:
                    self.assertNotIn(ch, fn,
                                     msg=f'Filename for {attack!r} has {ch!r}: {fn!r}')


# ─────────────────────────────────────────────────────────────────────────────
# Image line lookup
# ─────────────────────────────────────────────────────────────────────────────

class TestFindImage(unittest.TestCase):

    def test_finds_at_exact_line(self):
        self.assertEqual(
            pipeline._find_image({'source_messages': [{'line_reference': 100}]},
                                 {100: 'photo.jpg'}),
            'photo.jpg')

    def test_finds_within_50_lines_forward(self):
        # Implementation searches up to +50 lines to handle long WhatsApp event posts
        self.assertEqual(
            pipeline._find_image({'source_messages': [{'line_reference': 100}]},
                                 {149: 'photo.jpg'}),
            'photo.jpg')

    def test_does_not_find_backwards(self):
        self.assertIsNone(
            pipeline._find_image({'source_messages': [{'line_reference': 100}]},
                                 {99: 'photo.jpg'}))

    def test_does_not_find_beyond_50_lines(self):
        self.assertIsNone(
            pipeline._find_image({'source_messages': [{'line_reference': 100}]},
                                 {150: 'photo.jpg'}))

    def test_non_list_source_messages(self):
        for bad in BAD_VALUES_NON_LIST:
            try:
                result = pipeline._find_image({'source_messages': bad}, {100: 'x.jpg'})
                self.assertIsNone(result)
            except Exception as e:
                self.fail(f'_find_image crashed on source_messages={bad!r}: {e}')

    def test_non_dict_message_skipped(self):
        self.assertIsNone(
            pipeline._find_image({'source_messages': ['string', 123, None]},
                                 {100: 'photo.jpg'}))


# ─────────────────────────────────────────────────────────────────────────────
# URL collection
# ─────────────────────────────────────────────────────────────────────────────

class TestCollectUrls(unittest.TestCase):

    def test_registration_link_included(self):
        self.assertIn('https://example.com',
                      pipeline._collect_urls({'registration_link': 'https://example.com'}))

    def test_dangerous_scheme_excluded(self):
        self.assertEqual(
            pipeline._collect_urls({'registration_link': 'javascript:alert(1)'}), [])

    def test_urls_from_source_excerpt(self):
        event = {'source_messages': [
            {'source_excerpt': 'see https://example.org/event for details'}]}
        self.assertIn('https://example.org/event', pipeline._collect_urls(event))

    def test_non_list_source_messages(self):
        for bad in BAD_VALUES_NON_LIST:
            try:
                result = pipeline._collect_urls({'source_messages': bad})
                self.assertIsInstance(result, list)
            except Exception as e:
                self.fail(f'_collect_urls crashed on source_messages={bad!r}: {e}')

    def test_non_string_source_excerpt_does_not_crash(self):
        for bad in BAD_VALUES_NON_STRING:
            try:
                result = pipeline._collect_urls(
                    {'source_messages': [{'source_excerpt': bad}]})
                self.assertIsInstance(result, list)
            except Exception as e:
                self.fail(f'_collect_urls crashed on source_excerpt={bad!r}: {e}')

    def test_max_urls_limit(self):
        event = {
            'registration_link': 'https://a.com',
            'source_messages': [
                {'source_excerpt': 'https://b.com https://c.com https://d.com'},
                {'source_excerpt': 'https://e.com https://f.com'},
            ]
        }
        self.assertLessEqual(len(pipeline._collect_urls(event)), pipeline.MAX_URLS)


# ─────────────────────────────────────────────────────────────────────────────
# End-to-end smoke test
# ─────────────────────────────────────────────────────────────────────────────

class TestEndToEndSmoke(unittest.TestCase):

    def test_adversarial_events_do_not_crash(self):
        events = [
            {'title': '<script>alert(1)</script>', 'date_only': '2026-05-20',
             'description': '"; DROP TABLE events;--',
             'location_name': '\r\nBEGIN:VEVENT\r\nUID:fake'},
            {'title': None, 'date_only': 12345},
            {'title': 'שלום', 'date_only': '2026-05-21',
             'start_time_only': '20:00', 'end_time_only': '00:00'},
            {'title': '', 'event_start': '2026-05-22T18:00:00',
             'price_details': [None, 123, {'nested': 'object'}]},
            {},
        ]
        try:
            html, ics = pipeline._make_full_cal(events)
        except Exception as e:
            self.fail(f'_make_full_cal crashed: {e}')

        self.assertNotIn('<script>alert(1)</script>', html)
        lines = ics.split('\r\n')
        begin = sum(1 for L in lines if L == 'BEGIN:VEVENT')
        end = sum(1 for L in lines if L == 'END:VEVENT')
        self.assertEqual(begin, end, msg=f'VEVENT mismatch: {begin} BEGIN vs {end} END')


# ─────────────────────────────────────────────────────────────────────────────
# step_clean — regression guard for fix #21 (dedup key includes start_time_only)
# ─────────────────────────────────────────────────────────────────────────────

class TestStepCleanDedup(unittest.TestCase):
    '''Regression guard: same-title/same-date/different-time must NOT be deduped.'''

    def _run_step_clean(self, events):
        tmp = tempfile.NamedTemporaryFile(
            mode='w', encoding='utf-8', suffix='.json', delete=False
        )
        try:
            json.dump(events, tmp, ensure_ascii=False)
            tmp.close()
            events_path = Path(tmp.name)
            with patch.object(pipeline, 'EVENTS_JSON', events_path):
                pipeline.step_clean()
            with open(events_path, encoding='utf-8') as f:
                return json.load(f)
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

    def test_same_title_same_date_different_time_kept(self):
        # Two sessions of the same workshop on same day at different times —
        # must NOT be deduplicated (regression guard for fix #21)
        events = [
            {'title': 'סדנה', 'date_only': '2026-12-01', 'start_time_only': '10:00'},
            {'title': 'סדנה', 'date_only': '2026-12-01', 'start_time_only': '18:00'},
        ]
        result = self._run_step_clean(events)
        kept = result if isinstance(result, list) else result.get('events', [])
        self.assertEqual(len(kept), 2, 'Different-time sessions were incorrectly deduplicated')

    def test_exact_duplicate_removed(self):
        events = [
            {'title': 'סדנה', 'date_only': '2026-12-01', 'start_time_only': '10:00'},
            {'title': 'סדנה', 'date_only': '2026-12-01', 'start_time_only': '10:00'},
        ]
        result = self._run_step_clean(events)
        kept = result if isinstance(result, list) else result.get('events', [])
        self.assertEqual(len(kept), 1, 'True duplicate was not removed')

    def test_all_past_events_raises(self):
        # step_clean should refuse to produce an empty output from non-empty input
        events = [
            {'title': 'עבר', 'date_only': '2000-01-01', 'start_time_only': '10:00'},
        ]
        with self.assertRaises(RuntimeError):
            self._run_step_clean(events)


# ─────────────────────────────────────────────────────────────────────────────
# step_validate — fix #22 regression guard
# ─────────────────────────────────────────────────────────────────────────────

class TestStepValidate(unittest.TestCase):

    def _write_events(self, content):
        tmp = tempfile.NamedTemporaryFile(
            mode='w', encoding='utf-8', suffix='.json', delete=False
        )
        tmp.write(content)
        tmp.close()
        return Path(tmp.name)

    def tearDown(self):
        pass

    def test_malformed_json_raises_validation_error(self):
        p = self._write_events('{not valid json at all')
        try:
            with patch.object(pipeline, 'EVENTS_JSON', p):
                with self.assertRaises(pipeline.ValidationError) as ctx:
                    pipeline.step_validate()
            self.assertIn('not valid JSON', str(ctx.exception))
        finally:
            os.unlink(p)

    def test_valid_events_pass(self):
        p = self._write_events(
            json.dumps([{'title': 'Test', 'date_only': '2026-12-01'}])
        )
        try:
            with patch.object(pipeline, 'EVENTS_JSON', p):
                pipeline.step_validate()   # must not raise
        finally:
            os.unlink(p)

    def test_unknown_event_type_raises(self):
        p = self._write_events(
            json.dumps([{'title': 'X', 'date_only': '2026-12-01',
                         'event_type': 'totally_made_up'}])
        )
        try:
            with patch.object(pipeline, 'EVENTS_JSON', p):
                with self.assertRaises(pipeline.ValidationError):
                    pipeline.step_validate()
        finally:
            os.unlink(p)

    def test_out_of_range_confidence_raises(self):
        p = self._write_events(
            json.dumps([{'title': 'X', 'date_only': '2026-12-01', 'confidence': 1.5}])
        )
        try:
            with patch.object(pipeline, 'EVENTS_JSON', p):
                with self.assertRaises(pipeline.ValidationError):
                    pipeline.step_validate()
        finally:
            os.unlink(p)

    def test_missing_date_raises(self):
        p = self._write_events(json.dumps([{'title': 'X'}]))
        try:
            with patch.object(pipeline, 'EVENTS_JSON', p):
                with self.assertRaises(pipeline.ValidationError):
                    pipeline.step_validate()
        finally:
            os.unlink(p)


# ─────────────────────────────────────────────────────────────────────────────
# _img_uri_remote — inline remote images at build time
# ─────────────────────────────────────────────────────────────────────────────

class TestImgUriRemote(unittest.TestCase):

    def _mock_resp(self, content_type='image/jpeg', body=b'\xff\xd8\xff\xd9', status=200):
        resp = MagicMock()
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        resp.headers.get_content_type.return_value = content_type
        resp.read.return_value = body
        return resp

    def test_valid_jpeg_returns_data_uri(self):
        resp = self._mock_resp(body=b'\xff\xd8\xff\xd9' * 10)
        with patch('urllib.request.urlopen', return_value=resp):
            with patch('urllib.request.Request', return_value=MagicMock()):
                result = pipeline._img_uri_remote('https://example.com/img.jpg')
        self.assertIsNotNone(result)
        self.assertTrue(result.startswith('data:image/jpeg;base64,'))

    def test_non_image_content_type_returns_none(self):
        resp = self._mock_resp(content_type='text/html', body=b'<html>')
        with patch('urllib.request.urlopen', return_value=resp):
            with patch('urllib.request.Request', return_value=MagicMock()):
                result = pipeline._img_uri_remote('https://example.com/page.html')
        self.assertIsNone(result)

    def test_oversized_image_returns_none(self):
        resp = self._mock_resp(body=b'\x00' * 10_000_001)
        with patch('urllib.request.urlopen', return_value=resp):
            with patch('urllib.request.Request', return_value=MagicMock()):
                result = pipeline._img_uri_remote('https://example.com/big.jpg')
        self.assertIsNone(result)

    def test_network_error_returns_none(self):
        with patch('urllib.request.urlopen', side_effect=OSError('connection refused')):
            with patch('urllib.request.Request', return_value=MagicMock()):
                result = pipeline._img_uri_remote('https://example.com/img.jpg')
        self.assertIsNone(result)

    def test_result_does_not_embed_url(self):
        body = b'\xff\xd8\xff\xd9'
        resp = self._mock_resp(body=body)
        with patch('urllib.request.urlopen', return_value=resp):
            with patch('urllib.request.Request', return_value=MagicMock()):
                result = pipeline._img_uri_remote('https://attacker.com/track.gif')
        # The returned data URI must not contain the original URL
        if result is not None:
            self.assertNotIn('attacker.com', result)


if __name__ == '__main__':
    unittest.main(verbosity=2)
