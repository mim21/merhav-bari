'''
Adversarial test suite for bot.py — the Telegram bot's input-handling functions.

Tests the parts of bot.py that don't require a running Telegram connection:
- _extract_chat_from_zip: zip-slip protection, size caps, file selection
- _is_allowed: user authorization
- _safe_error: token redaction in error messages

Run with:
    python -m unittest tests.test_bot_robustness -v

No pytest or telegram dependency required — telegram imports are stubbed.
'''

import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

_telegram_stub = MagicMock()
_telegram_ext_stub = MagicMock()
_telegram_ext_stub.filters = MagicMock()
_telegram_ext_stub.filters.Document = MagicMock()
sys.modules['telegram'] = _telegram_stub
sys.modules['telegram.ext'] = _telegram_ext_stub

os.environ.setdefault('TELEGRAM_BOT_TOKEN', 'test-token-1234567890')

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import bot  # noqa: E402


def _make_zip_with_entries(entries):
    fd, zpath = tempfile.mkstemp(suffix='.zip')
    os.close(fd)
    with zipfile.ZipFile(zpath, 'w') as z:
        for name, content in entries:
            if isinstance(content, str):
                content = content.encode('utf-8')
            z.writestr(name, content)
    return Path(zpath)


class TestZipExtraction(unittest.TestCase):

    def setUp(self):
        self._cleanup = []

    def tearDown(self):
        for p in self._cleanup:
            try:
                p.unlink()
            except FileNotFoundError:
                pass

    def _make_zip(self, entries):
        p = _make_zip_with_entries(entries)
        self._cleanup.append(p)
        return p

    def test_legitimate_chat_extracted(self):
        z = self._make_zip([
            ('_chat.txt', 'hello world'),
            ('photo.jpg', b'\xff\xd8\xff\xd9'),
        ])
        result = bot._extract_chat_from_zip(z)
        self.assertIsNotNone(result)
        self.assertEqual(result.name, '_chat.txt')
        self.assertEqual(result.read_text(encoding='utf-8'), 'hello world')

    def test_chat_in_subfolder_extracted(self):
        z = self._make_zip([
            ('WhatsApp Chat With Group/_chat.txt', 'in subfolder'),
        ])
        result = bot._extract_chat_from_zip(z)
        self.assertIsNotNone(result)
        self.assertEqual(result.read_text(encoding='utf-8'), 'in subfolder')

    def test_no_chat_file_returns_none(self):
        z = self._make_zip([
            ('photo.jpg', b'\xff\xd8\xff\xd9'),
            ('readme.txt', 'not a chat'),
        ])
        self.assertIsNone(bot._extract_chat_from_zip(z))

    def test_oversized_zip_rejected(self):
        original_max = bot.MAX_ZIP_SIZE
        bot.MAX_ZIP_SIZE = 100
        try:
            z = self._make_zip([('_chat.txt', 'x' * 500)])
            with self.assertRaises(ValueError) as ctx:
                bot._extract_chat_from_zip(z)
            self.assertIn('too large', str(ctx.exception).lower())
        finally:
            bot.MAX_ZIP_SIZE = original_max

    def test_oversized_chat_inside_zip_rejected(self):
        original_max = bot.MAX_CHAT_SIZE
        bot.MAX_CHAT_SIZE = 100
        try:
            z = self._make_zip([('_chat.txt', 'x' * 1000)])
            with self.assertRaises(ValueError) as ctx:
                bot._extract_chat_from_zip(z)
            self.assertIn('too large', str(ctx.exception).lower())
        finally:
            bot.MAX_CHAT_SIZE = original_max

    def test_zip_slip_traversal_blocked(self):
        attacks = [
            '../evil_chat.txt',
            '../../evil_chat.txt',
            '../../../etc/passwd_chat.txt',
            'subdir/../../escaped_chat.txt',
        ]
        for attack in attacks:
            z = self._make_zip([(attack, 'should not extract here')])
            try:
                result = bot._extract_chat_from_zip(z)
                self.assertIsNone(result, msg=f'ZIP slip attack {attack!r} was not blocked')
            finally:
                for parent in [Path(tempfile.gettempdir()), Path.cwd()]:
                    for evil in ['evil_chat.txt', 'escaped_chat.txt']:
                        try:
                            (parent / evil).unlink()
                        except (FileNotFoundError, IsADirectoryError):
                            pass

    def test_absolute_path_zip_entry(self):
        z = self._make_zip([
            ('/tmp/evil_absolute_chat.txt', 'should not write to absolute path'),
        ])
        result = bot._extract_chat_from_zip(z)
        if result is not None:
            self.assertFalse(
                Path('/tmp/evil_absolute_chat.txt').exists(),
                msg='Absolute path ZIP entry wrote outside tmp_dir',
            )

    def test_first_matching_chat_returned(self):
        z = self._make_zip([
            ('first_chat.txt', 'first match'),
            ('photos/IMG.jpg', b'\xff\xd8\xff\xd9'),
            ('second_chat.txt', 'second match'),
        ])
        result = bot._extract_chat_from_zip(z)
        self.assertIsNotNone(result)
        self.assertIn(result.read_text(encoding='utf-8'), ['first match', 'second match'])


class TestIsAllowed(unittest.TestCase):

    def _make_update(self, user_id):
        return SimpleNamespace(effective_user=SimpleNamespace(id=user_id))

    def test_empty_allowlist_permits_all(self):
        original = bot.ALLOWED_USER_IDS
        bot.ALLOWED_USER_IDS = set()
        try:
            for uid in [12345, 0, -1, 9999999999]:
                self.assertTrue(bot._is_allowed(self._make_update(uid)))
        finally:
            bot.ALLOWED_USER_IDS = original

    def test_allowlist_permits_listed_users(self):
        original = bot.ALLOWED_USER_IDS
        bot.ALLOWED_USER_IDS = {12345, 67890}
        try:
            self.assertTrue(bot._is_allowed(self._make_update(12345)))
            self.assertTrue(bot._is_allowed(self._make_update(67890)))
        finally:
            bot.ALLOWED_USER_IDS = original

    def test_allowlist_rejects_unlisted_users(self):
        original = bot.ALLOWED_USER_IDS
        bot.ALLOWED_USER_IDS = {12345}
        try:
            for uid in [99999, 0, -1, 12346]:
                self.assertFalse(bot._is_allowed(self._make_update(uid)))
        finally:
            bot.ALLOWED_USER_IDS = original


class TestSafeError(unittest.TestCase):

    def test_redacts_bot_token(self):
        ex = Exception(f'Something went wrong: {bot.BOT_TOKEN}')
        result = bot._safe_error(ex)
        self.assertNotIn(bot.BOT_TOKEN, result)
        self.assertIn('***', result)

    def test_truncates_to_200_chars(self):
        ex = Exception('x' * 1000)
        result = bot._safe_error(ex)
        self.assertEqual(len(result), 200)

    def test_handles_token_appearing_multiple_times(self):
        msg = f'{bot.BOT_TOKEN} and again {bot.BOT_TOKEN}'
        ex = Exception(msg)
        result = bot._safe_error(ex)
        self.assertNotIn(bot.BOT_TOKEN, result)
        self.assertEqual(result.count('***'), 2)

    def test_exception_with_no_message(self):
        ex = Exception()
        result = bot._safe_error(ex)
        self.assertIsInstance(result, str)
        self.assertLessEqual(len(result), 200)


class TestConfig(unittest.TestCase):

    def test_max_zip_size_is_reasonable(self):
        self.assertGreater(bot.MAX_ZIP_SIZE, 1 * 1024 * 1024)
        self.assertLess(bot.MAX_ZIP_SIZE, 500 * 1024 * 1024)

    def test_max_chat_size_smaller_than_zip(self):
        self.assertLessEqual(bot.MAX_CHAT_SIZE, bot.MAX_ZIP_SIZE)

    def test_chat_folder_name_safe(self):
        self.assertNotIn('/', bot.CHAT_FOLDER_NAME)
        self.assertNotIn('\\', bot.CHAT_FOLDER_NAME)
        self.assertNotIn('..', bot.CHAT_FOLDER_NAME)
        self.assertNotIn('\x00', bot.CHAT_FOLDER_NAME)


if __name__ == '__main__':
    unittest.main(verbosity=2)
