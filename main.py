"""
Telegram Restricted-Content Downloader

Downloads media (photos, videos, documents, audio, voice, stickers)
from chats/channels that have forwarding & saving disabled.

Uses your own Telegram user account via Telethon (MTProto API).
The "noforwards" restriction is enforced only by official clients —
the underlying API still allows the account holder to download media
from chats they belong to.
"""

import io
import os
import sys
import asyncio
import argparse
from datetime import datetime

# Fix Unicode output on Windows (cp1252 console can't print emojis)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from telethon import TelegramClient, errors
from telethon.tl.types import (
    MessageMediaPhoto,
    MessageMediaDocument,
    MessageMediaWebPage,
    DocumentAttributeFilename,
    DocumentAttributeAudio,
    DocumentAttributeVideo,
    DocumentAttributeSticker,
    InputMediaDocument,
    InputMediaPhoto,
    InputDocument,
    InputPhoto,
)

from config import API_ID, API_HASH, SESSION_NAME, DOWNLOAD_DIR, BOT_TOKEN
from fast_telethon import fast_upload, fast_download


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def ensure_dir(path: str):
    """Create directory if it doesn't exist."""
    os.makedirs(path, exist_ok=True)


def sanitize_filename(name: str) -> str:
    """Remove characters that are invalid in Windows filenames."""
    for ch in r'<>:"/\|?*':
        name = name.replace(ch, "_")
    return name.strip()


def get_filename_for_message(message) -> str:
    """Derive a sensible filename from a Telegram message."""
    media = message.media

    # Photo
    if isinstance(media, MessageMediaPhoto):
        return f"photo_{message.id}_{message.date:%Y%m%d_%H%M%S}.jpg"

    # Document (video, file, audio, sticker, etc.)
    if isinstance(media, MessageMediaDocument):
        doc = media.document
        # Try to find the original filename attribute
        for attr in doc.attributes:
            if isinstance(attr, DocumentAttributeFilename):
                return sanitize_filename(attr.file_name)

        # Determine extension from mime_type
        ext_map = {
            "video/mp4": ".mp4",
            "video/x-matroska": ".mkv",
            "audio/mpeg": ".mp3",
            "audio/ogg": ".ogg",
            "audio/mp4": ".m4a",
            "image/webp": ".webp",
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "application/pdf": ".pdf",
            "application/zip": ".zip",
            "application/x-rar-compressed": ".rar",
        }
        mime = doc.mime_type or ""
        ext = ext_map.get(mime, "")
        if not ext and "/" in mime:
            ext = "." + mime.split("/")[-1]

        # Prefix with a descriptive tag
        for attr in doc.attributes:
            if isinstance(attr, DocumentAttributeVideo):
                return f"video_{message.id}{ext}"
            if isinstance(attr, DocumentAttributeAudio):
                title = attr.title or ""
                performer = attr.performer or ""
                if title:
                    return sanitize_filename(f"{performer} - {title}".strip(" -")) + ext
                return f"audio_{message.id}{ext}"
            if isinstance(attr, DocumentAttributeSticker):
                return f"sticker_{message.id}{ext or '.webp'}"

        return f"file_{message.id}{ext}"

    return f"media_{message.id}"


def sizeof_fmt(num_bytes: int) -> str:
    """Human-readable file size."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num_bytes) < 1024:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} PB"


# ──────────────────────────────────────────────
# Progress callback
# ──────────────────────────────────────────────

def make_progress_cb(filename: str, total_size: int):
    """Return a callback that prints download progress."""
    def progress(received, total):
        total = total or total_size
        if total:
            pct = received * 100 / total
            bar_len = 30
            filled = int(bar_len * received / total)
            bar = "█" * filled + "░" * (bar_len - filled)
            print(f"\r  ⬇  {filename}  [{bar}] {pct:5.1f}%  ({sizeof_fmt(received)}/{sizeof_fmt(total)})", end="", flush=True)
        else:
            print(f"\r  ⬇  {filename}  {sizeof_fmt(received)} downloaded...", end="", flush=True)
    return progress


# ──────────────────────────────────────────────
# Core download logic
# ──────────────────────────────────────────────

async def download_message_media(client: TelegramClient, message, dest_dir: str) -> str | None:
    """
    Download the media attached to a single message.
    Returns the saved file path, or None if no media.
    """
    if not message.media:
        return None
    # Skip web-page previews (no actual file)
    if isinstance(message.media, MessageMediaWebPage):
        return None

    filename = get_filename_for_message(message)
    filepath = os.path.join(dest_dir, filename)

    # Avoid re-downloading
    if os.path.exists(filepath):
        print(f"  ⏭  Already exists: {filename}")
        return filepath

    # Figure out size for progress bar
    total_size = 0
    if isinstance(message.media, MessageMediaDocument):
        total_size = message.media.document.size or 0
    elif isinstance(message.media, MessageMediaPhoto):
        # Photo sizes aren't known until downloaded
        total_size = 0

    progress_cb = make_progress_cb(filename, total_size)

    path = await client.download_media(
        message,
        file=filepath,
        progress_callback=progress_cb,
    )
    print()  # newline after progress bar
    return path


async def list_chats(client: TelegramClient):
    """Print all dialogs the account belongs to, highlighting restricted ones."""
    print("\n📋  Your chats / channels / groups:\n")
    print(f"{'ID':<16} {'Restricted':^12} {'Type':<10} {'Title'}")
    print("─" * 72)

    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        chat_type = "User"
        restricted = ""

        if hasattr(entity, "megagroup") and entity.megagroup:
            chat_type = "Supergroup"
        elif hasattr(entity, "broadcast") and entity.broadcast:
            chat_type = "Channel"
        elif hasattr(entity, "gigagroup") and entity.gigagroup:
            chat_type = "Gigagroup"
        elif hasattr(entity, "title"):
            chat_type = "Group"

        if hasattr(entity, "noforwards") and entity.noforwards:
            restricted = "🔒 Yes"

        title = dialog.title or dialog.name or "?"
        print(f"{dialog.id:<16} {restricted:^12} {chat_type:<10} {title}")


async def download_from_chat(
    client: TelegramClient,
    chat_id: int | str,
    dest_dir: str,
    limit: int = 0,
    offset_id: int = 0,
    media_types: list[str] | None = None,
    min_id: int = 0,
    max_id: int = 0,
):
    """
    Download all media from a chat/channel.

    Args:
        chat_id:     numeric ID, @username, or invite link
        dest_dir:    folder to save files into
        limit:       max messages to scan (0 = all)
        offset_id:   start scanning from this message ID (newest → oldest)
        media_types: filter — e.g. ["photo", "video", "document", "audio"]
        min_id:      only messages with id > min_id
        max_id:      only messages with id < max_id
    """
    ensure_dir(dest_dir)

    try:
        entity = await client.get_entity(chat_id)
    except (ValueError, errors.UsernameNotOccupiedError):
        print(f"❌  Could not find chat: {chat_id}")
        return

    title = getattr(entity, "title", None) or getattr(entity, "first_name", str(chat_id))
    noforwards = getattr(entity, "noforwards", False)
    print(f"\n📂  Chat: {title}")
    print(f"    Forwarding restricted: {'Yes 🔒' if noforwards else 'No'}")
    print(f"    Saving to: {dest_dir}\n")

    count = 0
    skipped = 0
    iter_kwargs = {}
    if limit:
        iter_kwargs["limit"] = limit
    if offset_id:
        iter_kwargs["offset_id"] = offset_id
    if min_id:
        iter_kwargs["min_id"] = min_id
    if max_id:
        iter_kwargs["max_id"] = max_id

    async for message in client.iter_messages(entity, **iter_kwargs):
        if not message.media:
            continue
        if isinstance(message.media, MessageMediaWebPage):
            continue

        # Optional type filter
        if media_types:
            matched = False
            if "photo" in media_types and isinstance(message.media, MessageMediaPhoto):
                matched = True
            elif isinstance(message.media, MessageMediaDocument):
                doc = message.media.document
                mime = doc.mime_type or ""
                if "video" in media_types and mime.startswith("video"):
                    matched = True
                elif "audio" in media_types and mime.startswith("audio"):
                    matched = True
                elif "document" in media_types:
                    matched = True
            if not matched:
                skipped += 1
                continue

        path = await download_message_media(client, message, dest_dir)
        if path:
            count += 1

    print(f"\n✅  Done — {count} file(s) downloaded", end="")
    if skipped:
        print(f", {skipped} skipped (filtered out)", end="")
    print()


async def forward_messages(
    client: TelegramClient,
    source_chat: int | str,
    dest_chat: int | str,
    message_ids: list[int] | None = None,
    limit: int = 0,
):
    """
    Re-send messages from a restricted chat to another chat.

    Uses server-side media references (no download needed) — the file stays
    on Telegram's servers and is just re-sent to the destination instantly.
    Falls back to download+reupload only if the server-side approach fails.

    Args:
        source_chat:  numeric ID, @username, or invite link of source
        dest_chat:    numeric ID, @username, or invite link of destination
        message_ids:  specific message IDs to forward (None = scan all)
        limit:        max messages to scan when message_ids is None
    """
    try:
        src_entity = await client.get_entity(source_chat)
    except (ValueError, errors.UsernameNotOccupiedError):
        print(f"❌  Could not find source chat: {source_chat}")
        return

    try:
        dst_entity = await client.get_entity(dest_chat)
    except (ValueError, errors.UsernameNotOccupiedError):
        print(f"❌  Could not find destination chat: {dest_chat}")
        return

    src_title = getattr(src_entity, "title", None) or getattr(src_entity, "first_name", str(source_chat))
    dst_title = getattr(dst_entity, "title", None) or getattr(dst_entity, "first_name", str(dest_chat))
    noforwards = getattr(src_entity, "noforwards", False)
    print(f"\n🔀  Forwarding: {src_title} → {dst_title}")
    print(f"    Restricted: {'Yes 🔒' if noforwards else 'No'}")
    print(f"    Method: Server-side reference (no download)\n")

    count = 0
    failed = 0

    if message_ids:
        messages = await client.get_messages(src_entity, ids=message_ids)
        if not isinstance(messages, list):
            messages = [messages]
    else:
        iter_kwargs = {}
        if limit:
            iter_kwargs["limit"] = limit
        messages = [m async for m in client.iter_messages(src_entity, **iter_kwargs)]

    for message in messages:
        if message is None:
            continue

        caption = message.text or ""

        if message.media and not isinstance(message.media, MessageMediaWebPage):
            filename = get_filename_for_message(message)
            try:
                # Server-side: pass the Document/Photo TL object directly.
                # Telethon converts these to InputMediaDocument/InputMediaPhoto
                # internally — the file stays on Telegram's servers (no download).
                if isinstance(message.media, MessageMediaDocument):
                    file_ref = message.media.document
                elif isinstance(message.media, MessageMediaPhoto):
                    file_ref = message.media.photo
                else:
                    raise ValueError(f"Unsupported media type: {type(message.media)}")

                await client.send_file(
                    dst_entity,
                    file=file_ref,
                    caption=caption,
                )
                print(f"  ✅  [{message.id}] {filename}")
                count += 1
            except Exception as e:
                # Telegram blocks server-side reference on protected media.
                # High-speed parallel stream in RAM (0 files written to disk)
                print(f"  ⚡  Protected media [{message.id}]. Parallel RAM streaming (0 disk files)...")
                try:
                    buffer = io.BytesIO()
                    buffer.name = filename
                    
                    # 1. Download into RAM buffer with progress
                    total_size = 0
                    if isinstance(message.media, MessageMediaDocument) and message.media.document:
                        total_size = message.media.document.size or 0
                    
                    dl_progress = make_progress_cb(f"Fetch: {filename}", total_size)
                    await client.download_media(message, file=buffer, progress_callback=dl_progress)
                    print()
                    buffer.seek(0)

                    # Extract original attributes (title, performer, duration, video dims)
                    attributes = []
                    if isinstance(message.media, MessageMediaDocument) and message.media.document:
                        attributes = message.media.document.attributes or []

                    # 2. Fast parallel multi-worker upload straight from memory
                    up_progress = make_progress_cb(f"Send:  {filename}", total_size)
                    uploaded_file = await fast_upload(
                        client,
                        buffer,
                        file_name=filename,
                        progress_callback=up_progress,
                        workers=4,
                    )
                    print()

                    await client.send_file(
                        dst_entity,
                        file=uploaded_file,
                        caption=caption,
                        attributes=attributes,
                    )
                    buffer.close()
                    print(f"  ✅  [{message.id}] {filename} (transferred)")
                    count += 1
                except Exception as e2:
                    print(f"  ❌  [{message.id}] Failed: {e2}")
                    failed += 1
        elif caption:
            # Text-only message
            await client.send_message(dst_entity, caption)
            print(f"  ✅  [{message.id}] (text message)")
            count += 1

    print(f"\n✅  Done — {count} message(s) forwarded to {dst_title}", end="")
    if failed:
        print(f", {failed} failed", end="")
    print()


# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Telegram Restricted-Content Downloader & Forwarder",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # List all your chats (see which ones are restricted)
  python main.py list

  # Download all media from a channel by ID
  python main.py download -c -1001234567890

  # Download only videos, limit 50 messages
  python main.py download -c -1001234567890 -t video --limit 50

  # Download from a channel by username
  python main.py download -c @somechannel

  # Forward media from restricted chat to your Saved Messages
  python main.py forward -s -1001234567890 -d me

  # Forward specific message IDs
  python main.py forward -s -1001234567890 -d me -m 100 101 102
        """,
    )
    sub = parser.add_subparsers(dest="command")

    # ── list ──
    sub.add_parser("list", help="List all chats/channels/groups")

    # ── download ──
    dl = sub.add_parser("download", help="Download media from a chat")
    dl.add_argument("-c", "--chat", required=True, help="Chat ID, @username, or invite link")
    dl.add_argument("-o", "--output", default=None, help=f"Output directory (default: {DOWNLOAD_DIR}/<chat>)")
    dl.add_argument("--limit", type=int, default=0, help="Max messages to scan (0 = all)")
    dl.add_argument("--offset-id", type=int, default=0, help="Start from this message ID")
    dl.add_argument("--min-id", type=int, default=0, help="Only messages with ID > this")
    dl.add_argument("--max-id", type=int, default=0, help="Only messages with ID < this")
    dl.add_argument(
        "-t", "--type", nargs="+", default=None,
        choices=["photo", "video", "audio", "document"],
        help="Filter by media type",
    )

    # ── forward ──
    fw = sub.add_parser("forward", help="Re-send media from a restricted chat to another chat")
    fw.add_argument("-s", "--source", required=True, help="Source chat ID or @username")
    fw.add_argument("-d", "--dest", required=True, help="Destination chat ID or @username (use 'me' for Saved Messages)")
    fw.add_argument("-m", "--messages", nargs="+", type=int, default=None, help="Specific message IDs to forward")
    fw.add_argument("--limit", type=int, default=0, help="Max messages to scan (0 = all)")

    return parser


async def main():
    if API_ID == 0 or not API_HASH:
        print("❌  Please set your API credentials!")
        print("    Edit config.py or set environment variables:")
        print("      TELEGRAM_API_ID=<your_api_id>")
        print("      TELEGRAM_API_HASH=<your_api_hash>")
        print()
        print("    Get them at https://my.telegram.org")
        sys.exit(1)

    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    client = TelegramClient(SESSION_NAME, API_ID, API_HASH)

    if BOT_TOKEN:
        await client.start(bot_token=BOT_TOKEN)
        me = await client.get_me()
        print(f"✅  Logged in as bot: @{me.username}")
        print("    ℹ️  Bot must be added to target chats to access messages.")
    else:
        await client.start()
        me = await client.get_me()
        print(f"✅  Logged in as: {me.first_name}")

    # Pre-load all dialogs to populate entity cache
    # (needed so numeric IDs can be resolved after a fresh session)
    print("📡  Loading chats...")
    await client.get_dialogs()
    print("    Done.\n")

    if args.command == "list":
        await list_chats(client)

    elif args.command == "download":
        chat = args.chat
        # Try to parse as int (chat IDs are numeric)
        try:
            chat = int(chat)
        except ValueError:
            pass

        # Determine output directory
        dest_dir = args.output
        if not dest_dir:
            safe_name = sanitize_filename(str(chat).lstrip("@"))
            dest_dir = os.path.join(DOWNLOAD_DIR, safe_name)

        await download_from_chat(
            client,
            chat_id=chat,
            dest_dir=dest_dir,
            limit=args.limit,
            offset_id=args.offset_id,
            media_types=args.type,
            min_id=args.min_id,
            max_id=args.max_id,
        )

    elif args.command == "forward":
        src = args.source
        dst = args.dest
        try:
            src = int(src)
        except ValueError:
            pass
        try:
            dst = int(dst)
        except ValueError:
            pass

        await forward_messages(
            client,
            source_chat=src,
            dest_chat=dst,
            message_ids=args.messages,
            limit=args.limit,
        )

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
