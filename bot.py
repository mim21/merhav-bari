'''
Telegram bot (local mode): receive WhatsApp ZIP → save _chat.txt →
run claude CLI extraction → publish events to GitHub Pages.

Commands:
  /start  — help
  /run    — auto-detect latest ZIP/folder and run extraction

Usage:
  Set env vars (see .env.example), then:  python bot.py

Install:
  pip install -r requirements.txt
'''
import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import datetime
from pathlib import Path

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

# ── Config (from env vars) ────────────────────────────────────────────────────
BOT_TOKEN       = os.environ['TELEGRAM_BOT_TOKEN']
SITE_URL        = os.environ.get('SITE_URL', 'https://mim21.github.io/merhav-bari')
MERHAV_BARI_DIR = Path(os.environ.get('MERHAV_BARI_DIR', str(Path(__file__).parent)))

# Comma-separated Telegram user IDs allowed to use the bot (empty = no restriction)
_raw_ids = os.environ.get('ALLOWED_USER_IDS', '')
ALLOWED_USER_IDS: set[int] = {int(x) for x in _raw_ids.split(',') if x.strip().isdigit()}

CHAT_FOLDER_NAME = 'WhatsApp_Chat_מרחב_בריא_פרסום_מרחבים_ואירועים'
MAX_ZIP_SIZE     = 50 * 1024 * 1024   # 50 MB
MAX_CHAT_SIZE    = 10 * 1024 * 1024   # 10 MB

_job_lock: asyncio.Lock | None = None   # created in main() after event loop starts


# ── Auth helpers ──────────────────────────────────────────────────────────────

def _is_allowed(update: Update) -> bool:
    if not ALLOWED_USER_IDS:
        return True   # no restriction (dev mode)
    return update.effective_user.id in ALLOWED_USER_IDS


def _safe_error(ex: Exception) -> str:
    msg = str(ex)
    msg = msg.replace(BOT_TOKEN, '***')
    return msg[:200]


# ── Save chat to disk ─────────────────────────────────────────────────────────

def _save_chat(chat_bytes: bytes) -> Path:
    dest_chat = MERHAV_BARI_DIR / CHAT_FOLDER_NAME / '_chat.txt'
    dest_chat.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest_chat.with_suffix('.tmp')
    tmp.write_bytes(chat_bytes)
    os.replace(tmp, dest_chat)
    log.info('Saved chat to %s', dest_chat)
    return dest_chat


# ── Claude CLI extraction ─────────────────────────────────────────────────────

def _run_extraction(chat_file: Path) -> str:
    '''
    Run claude CLI in non-interactive mode to extract events and publish.
    Returns summary string like "7 events published".
    Raises RuntimeError on any detectable failure.
    '''
    claude_bin = shutil.which('claude') or 'claude'
    done_file  = MERHAV_BARI_DIR / '_EXTRACT_DONE.json'
    events_json = MERHAV_BARI_DIR / 'events.json'

    # Read previous event count so we can detect silent empty-result failures
    prev_count = 0
    try:
        prev = json.loads(events_json.read_text(encoding='utf-8'))
        if isinstance(prev, list):
            prev_count = len(prev)
        elif isinstance(prev, dict) and isinstance(prev.get('events'), list):
            prev_count = len(prev['events'])
    except Exception:
        pass

    # Remove stale done file — presence after the run proves this run completed
    done_file.unlink(missing_ok=True)
    start_ts = time.time()

    prompt = (
        f'Read {chat_file} following all rules in CLAUDE.md — '
        f'extract all upcoming events, write events.json to {MERHAV_BARI_DIR}/, '
        f'then run: python {MERHAV_BARI_DIR / "pipeline.py"} --push. '
        f'After pipeline succeeds write {done_file} '
        f'with {{"events": COUNT, "timestamp": ISO_UTC}}. '
        f'Follow CLAUDE.md strictly: check every registration_link for end_time, '
        f'couple pricing, early-bird tiers, _image_filename, image_url.'
    )
    result = subprocess.run(
        [claude_bin, '-p', prompt, '--permission-mode', 'acceptEdits'],
        cwd=str(MERHAV_BARI_DIR),
        capture_output=True,
        text=True,
        encoding='utf-8',
    )
    if result.returncode != 0:
        raise RuntimeError(f'claude exited {result.returncode}')

    # Detect auth/rate-limit failures that claude reports but exits 0 on
    combined = (result.stdout or '') + (result.stderr or '')
    for kw in ('invalid api key', 'unauthorized', 'rate limit', 'please run /login',
                'not authenticated', 'authentication required', 'auth failed',
                'credit balance', 'insufficient credits', 'overloaded',
                'context length', 'prompt is too long', 'session expired'):
        if kw in combined.lower():
            raise RuntimeError(f'claude auth/rate-limit failure (keyword: "{kw}")')

    # Verify claude actually completed — done file must be fresh from this run
    if not done_file.exists() or done_file.stat().st_mtime < start_ts:
        raise RuntimeError('claude did not signal completion (_EXTRACT_DONE.json missing or stale)')

    try:
        done  = json.loads(done_file.read_text(encoding='utf-8'))
        count = int(done['events'])
    except Exception as ex:
        raise RuntimeError(f'_EXTRACT_DONE.json malformed: {ex}')

    # Verify actual events.json count — pipeline's step_clean may drop events
    # regardless of what claude wrote in the done file
    actual_count = count
    try:
        post = json.loads(events_json.read_text(encoding='utf-8'))
        if isinstance(post, list):
            actual_count = len(post)
        elif isinstance(post, dict) and isinstance(post.get('events'), list):
            actual_count = len(post['events'])
    except Exception:
        pass
    if actual_count != count:
        log.warning('Done-file reported %s events but events.json has %s', count, actual_count)

    # Guard: refuse if pipeline left 0 events when we had data before
    if prev_count > 0 and actual_count == 0:
        raise RuntimeError(
            f'pipeline left 0 events but previous run had {prev_count} — aborting publish'
        )

    return f'{actual_count} events published'


def _run_extraction_with_backup(chat_file: Path) -> str:
    '''Wrap _run_extraction with atomic events.json backup/restore on failure.'''
    events_json = MERHAV_BARI_DIR / 'events.json'
    backup      = MERHAV_BARI_DIR / 'events.json.bak'
    backed_up   = False
    if events_json.exists():
        shutil.copy2(events_json, backup)
        backed_up = True
    try:
        result = _run_extraction(chat_file)
        if backed_up:
            backup.unlink(missing_ok=True)
        return result
    except Exception:
        if backed_up and backup.exists():
            os.replace(backup, events_json)
            log.info('Restored events.json from backup after extraction failure')
        raise


# ── Latest-chat detection ─────────────────────────────────────────────────────

def _last_msg_date(chat_path: Path) -> datetime | None:
    last: datetime | None = None
    with chat_path.open(encoding='utf-8', errors='replace') as f:
        for line in f:
            if line.startswith('[') and ',' in line[:20]:
                try:
                    last = datetime.strptime(line[1:11], '%d/%m/%Y')
                except ValueError:
                    pass
    return last


_MEDIA_EXTS = ('.jpg', '.jpeg', '.png', '.mp4')


def _find_latest_chat() -> Path:
    '''
    Compare all *.zip archives in MERHAV_BARI_DIR against the existing
    unzipped _chat.txt. Returns the path to _chat.txt from the newest source,
    extracting from ZIP first if needed.
    '''
    dest_chat = MERHAV_BARI_DIR / CHAT_FOLDER_NAME / '_chat.txt'
    candidates: list[tuple[datetime | None, Path, Path | None]] = []
    tmp_dirs: list[Path] = []

    if dest_chat.exists():
        candidates.append((_last_msg_date(dest_chat), dest_chat, None))

    for zp in sorted(MERHAV_BARI_DIR.glob('*.zip')):
        try:
            with zipfile.ZipFile(zp) as z:
                entry = next((n for n in z.namelist() if n.endswith('_chat.txt')), None)
                if not entry:
                    continue
                tmp_dir = Path(tempfile.mkdtemp(prefix='chat_peek_'))
                tmp_dirs.append(tmp_dir)
                z.extract(entry, tmp_dir)
                candidates.append((_last_msg_date(tmp_dir / entry), tmp_dir / entry, zp))
        except Exception as ex:
            log.warning('Could not peek into %s: %s', zp.name, ex)

    if not candidates:
        raise FileNotFoundError('No _chat.txt found in folder or ZIPs')

    best_date, best_chat, best_zip = max(
        candidates, key=lambda c: c[0] or datetime.min
    )
    if best_date is None:
        log.warning('No parseable WhatsApp timestamp in any candidate — using first available')
    log.info('Latest chat: %s (last msg %s)', best_zip.name if best_zip else 'unzipped folder', best_date)

    try:
        if best_zip:
            dest_chat.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest_chat.with_suffix('.tmp')
            shutil.copy2(best_chat, tmp)
            os.replace(tmp, dest_chat)
            # Extract new media (photos + videos) from the same ZIP
            chat_folder = MERHAV_BARI_DIR / CHAT_FOLDER_NAME
            with zipfile.ZipFile(best_zip) as z:
                for entry in z.namelist():
                    name = Path(entry).name
                    if not name.lower().endswith(_MEDIA_EXTS):
                        continue
                    dest = chat_folder / name
                    if not dest.exists():
                        z.extract(entry, chat_folder.parent)
    finally:
        for td in tmp_dirs:
            shutil.rmtree(td, ignore_errors=True)

    return dest_chat


# ── ZIP extraction helpers ────────────────────────────────────────────────────

def _extract_chat_from_zip(zip_path: Path) -> Path | None:
    if zip_path.stat().st_size > MAX_ZIP_SIZE:
        raise ValueError('ZIP file too large (max 50 MB)')
    tmp_dir = Path(tempfile.mkdtemp(prefix='chat_'))
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            if not info.filename.endswith('_chat.txt'):
                continue
            # Zip-slip protection — commonpath handles Windows case-insensitivity and drive differences
            target = (tmp_dir / info.filename).resolve()
            try:
                if os.path.commonpath([str(target), str(tmp_dir.resolve())]) != str(tmp_dir.resolve()):
                    log.warning('Skipping suspicious ZIP entry: %s', info.filename)
                    continue
            except ValueError:   # different drives on Windows
                continue
            if info.file_size > MAX_CHAT_SIZE:
                raise ValueError('_chat.txt too large inside ZIP (max 10 MB)')
            z.extract(info, tmp_dir)
            return target
    return None


# ── Telegram handlers ─────────────────────────────────────────────────────────

async def cmd_start(update: Update, _: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        return
    await update.message.reply_text(
        '🌿 *מרחב בריא – בוט עדכון אירועים*\n\n'
        'שלח לי קובץ ZIP של ייצוא הווטסאפ ואני אעדכן את האתר.\n\n'
        f'אתר: {SITE_URL}',
        parse_mode='Markdown',
    )


async def cmd_run(update: Update, _: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        await update.message.reply_text('⛔ אין הרשאה.')
        return

    if _job_lock and _job_lock.locked():
        await update.message.reply_text('⏳ עיבוד אחר כבר פועל. נסה שוב בעוד כמה דקות.')
        return

    async with _job_lock:
        status = await update.message.reply_text('🔍 מחפש את הצ\'אט העדכני ביותר...')
        try:
            loop = asyncio.get_running_loop()
            chat_file = await loop.run_in_executor(None, _find_latest_chat)
            await status.edit_text('🤖 מעבד אירועים עם Claude... זה לוקח כ-5 דקות.')
            summary = await loop.run_in_executor(
                None, _run_extraction_with_backup, chat_file
            )
            await status.edit_text(
                f'✅ *{summary}*\n\n[פתח את האתר]({SITE_URL})',
                parse_mode='Markdown',
            )
        except Exception as ex:
            log.exception('cmd_run failed')
            await status.edit_text(f'❌ שגיאה: {_safe_error(ex)}')


async def handle_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        await update.message.reply_text('⛔ אין הרשאה.')
        return

    doc = update.message.document
    fname = doc.file_name or ''

    if not (fname.endswith('.zip') or fname.endswith('.txt')):
        await update.message.reply_text('⚠️ שלח קובץ ZIP של ייצוא הווטסאפ.')
        return

    if _job_lock and _job_lock.locked():
        await update.message.reply_text('⏳ עיבוד אחר כבר פועל. נסה שוב בעוד כמה דקות.')
        return

    async with _job_lock:
        status = await update.message.reply_text('⏳ מוריד קובץ...')
        try:
            tg_file = await doc.get_file()
            tmp = tempfile.gettempdir()

            if fname.endswith('.zip'):
                zip_path = Path(tmp) / f'upload_{doc.file_id}.zip'
                await tg_file.download_to_drive(str(zip_path))
                await status.edit_text('📦 מחלץ _chat.txt מה-ZIP...')
                chat_file = _extract_chat_from_zip(zip_path)
                if not chat_file:
                    await status.edit_text('❌ לא מצאתי _chat.txt בקובץ ZIP.')
                    return
            else:
                chat_file = Path(tmp) / f'upload_{doc.file_id}_chat.txt'
                await tg_file.download_to_drive(str(chat_file))

            loop = asyncio.get_running_loop()

            await status.edit_text('💾 שומר קובץ...')
            saved_chat = await loop.run_in_executor(
                None, _save_chat, chat_file.read_bytes()
            )

            await status.edit_text(
                '🤖 מעבד אירועים עם Claude... זה לוקח כ-5 דקות.'
            )
            summary = await loop.run_in_executor(None, _run_extraction_with_backup, saved_chat)

            await status.edit_text(
                f'✅ *{summary}*\n\n'
                f'[פתח את האתר]({SITE_URL})',
                parse_mode='Markdown',
            )

        except Exception as ex:
            log.exception('Processing failed')
            await status.edit_text(f'❌ שגיאה: {_safe_error(ex)}')


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    global _job_lock
    asyncio.set_event_loop(asyncio.new_event_loop())
    _job_lock = asyncio.Lock()

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler('start', cmd_start))
    app.add_handler(CommandHandler('run', cmd_run))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    if not ALLOWED_USER_IDS:
        log.warning('ALLOWED_USER_IDS not set — bot accepts commands from ANY Telegram user')
    log.info('Starting polling...')
    app.run_polling()


if __name__ == '__main__':
    if '--run' in sys.argv:
        # CLI mode: auto-detect latest chat and run extraction (no bot needed)
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        logging.basicConfig(level=logging.INFO, format='%(message)s')
        try:
            print('Detecting latest chat source...')
            chat_file = _find_latest_chat()
            print(f'Running extraction from {MERHAV_BARI_DIR} ...')
            summary = _run_extraction_with_backup(chat_file)
            print(summary)
        except Exception as ex:
            print(f'Error: {ex}', file=sys.stderr)
            sys.exit(1)
    else:
        main()
