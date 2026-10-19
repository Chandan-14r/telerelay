"""
Flask Web Application for Telegram Media Forwarder.
Accessible on Laptop (localhost) and Mobile (via local Wi-Fi IP).
"""

import io
import os
import sys
import socket
import asyncio
import threading
from flask import Flask, render_template, request, jsonify
from telethon import TelegramClient
from telethon.tl.types import (
    MessageMediaPhoto,
    MessageMediaDocument,
    MessageMediaWebPage,
    DocumentAttributeFilename,
    DocumentAttributeAudio,
    DocumentAttributeVideo,
)

from telethon.sessions import StringSession
from config import API_ID, API_HASH, SESSION_NAME, STRING_SESSION
from fast_telethon import fast_upload

# Configure Windows console
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

app = Flask(__name__)

# Global async event loop and Telethon client in a dedicated worker thread
loop = asyncio.new_event_loop()
session_target = StringSession(STRING_SESSION) if STRING_SESSION else SESSION_NAME
client = TelegramClient(session_target, API_ID, API_HASH, loop=loop)


def start_loop():
    asyncio.set_event_loop(loop)
    loop.run_forever()


threading.Thread(target=start_loop, daemon=True).start()


def run_async(coro):
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result()


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def sizeof_fmt(num_bytes: int) -> str:
    if not num_bytes:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num_bytes) < 1024:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} PB"


def get_filename_for_message(message) -> str:
    media = message.media
    if isinstance(media, MessageMediaPhoto):
        return f"photo_{message.id}_{message.date:%Y%m%d_%H%M%S}.jpg"

    if isinstance(media, MessageMediaDocument):
        doc = media.document
        for attr in doc.attributes:
            if isinstance(attr, DocumentAttributeFilename):
                return attr.file_name
            if isinstance(attr, DocumentAttributeAudio):
                title = attr.title or ""
                performer = attr.performer or ""
                if title:
                    return f"{performer} - {title}".strip(" -") + ".m4a"
            if isinstance(attr, DocumentAttributeVideo):
                return f"video_{message.id}.mp4"
        return f"file_{message.id}"
    return f"media_{message.id}"


# ──────────────────────────────────────────────
# Telethon Async Functions
# ──────────────────────────────────────────────

async def _init_client():
    await client.connect()
    if not await client.is_user_authorized():
        return False
    await client.get_dialogs()
    return True


async def _get_chats():
    chats = []
    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        chat_type = "User"
        restricted = False

        if hasattr(entity, "megagroup") and entity.megagroup:
            chat_type = "Supergroup"
        elif hasattr(entity, "broadcast") and entity.broadcast:
            chat_type = "Channel"
        elif hasattr(entity, "title"):
            chat_type = "Group"

        if hasattr(entity, "noforwards") and entity.noforwards:
            restricted = True

        chats.append({
            "id": dialog.id,
            "title": dialog.title or dialog.name or "Unknown",
            "type": chat_type,
            "restricted": restricted
        })
    return chats


async def _get_messages(chat_id, limit=50, offset_id=0, search=""):
    entity = await client.get_entity(int(chat_id))
    messages_out = []
    lowest_id = offset_id

    iter_kwargs = {"limit": limit}
    if offset_id > 0:
        iter_kwargs["offset_id"] = offset_id

    async for m in client.iter_messages(entity, **iter_kwargs):
        lowest_id = m.id
        if not m.media or isinstance(m.media, MessageMediaWebPage):
            continue

        media_type = "file"
        size = 0
        if isinstance(m.media, MessageMediaPhoto):
            media_type = "photo"
        elif isinstance(m.media, MessageMediaDocument):
            doc = m.media.document
            size = doc.size or 0
            mime = doc.mime_type or ""
            if mime.startswith("audio") or any(isinstance(a, DocumentAttributeAudio) for a in doc.attributes):
                media_type = "audio"
            elif mime.startswith("video") or any(isinstance(a, DocumentAttributeVideo) for a in doc.attributes):
                media_type = "video"

        filename = get_filename_for_message(m)
        caption = m.text or ""

        # Optional client search filtering
        if search:
            q = search.lower()
            if q not in filename.lower() and q not in caption.lower():
                continue

        messages_out.append({
            "id": m.id,
            "filename": filename,
            "type": media_type,
            "size": sizeof_fmt(size),
            "date": m.date.strftime("%Y-%m-%d %H:%M") if m.date else ""
        })

    return {
        "messages": messages_out,
        "next_offset_id": lowest_id if lowest_id != offset_id else 0,
        "has_more": len(messages_out) > 0
    }


async def _forward_message_item(source_chat, message_ids=None, limit=0):
    src_entity = await client.get_entity(int(source_chat))
    dst_entity = await client.get_entity("me")  # Saved Messages

    if message_ids:
        msgs = await client.get_messages(src_entity, ids=message_ids)
        if not isinstance(msgs, list):
            msgs = [msgs]
    else:
        msgs = [m async for m in client.iter_messages(src_entity, limit=limit)]

    count = 0
    for message in msgs:
        if not message or not message.media:
            continue

        filename = get_filename_for_message(message)
        caption = message.text or ""

        # High-speed RAM stream (0 disk writes)
        buffer = io.BytesIO()
        buffer.name = filename
        await client.download_media(message, file=buffer)
        buffer.seek(0)

        attributes = []
        if isinstance(message.media, MessageMediaDocument) and message.media.document:
            attributes = message.media.document.attributes or []

        uploaded_file = await fast_upload(
            client,
            buffer,
            file_name=filename,
            workers=4,
        )

        await client.send_file(
            dst_entity,
            file=uploaded_file,
            caption=caption,
            attributes=attributes,
        )
        buffer.close()
        count += 1

    return count


# ──────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/status')
def status():
    try:
        is_logged_in = run_async(_init_client())
        if is_logged_in:
            me = run_async(client.get_me())
            return jsonify({"status": "connected", "user": me.first_name})
        return jsonify({"status": "not_authorized", "user": None})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route('/api/chats')
def chats():
    try:
        c = run_async(_get_chats())
        return jsonify(c)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/messages')
def messages():
    chat_id = request.args.get('chat_id')
    limit = int(request.args.get('limit', 50))
    offset_id = int(request.args.get('offset_id', 0))
    search = request.args.get('search', '').strip()
    if not chat_id:
        return jsonify({"error": "Missing chat_id"}), 400
    try:
        data = run_async(_get_messages(chat_id, limit=limit, offset_id=offset_id, search=search))
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/forward', methods=['POST'])
def forward():
    data = request.json or {}
    source_chat = data.get('source_chat')
    message_ids = data.get('message_ids')
    limit = int(data.get('limit', 0))

    if not source_chat:
        return jsonify({"error": "Missing source_chat"}), 400

    try:
        count = run_async(_forward_message_item(source_chat, message_ids=message_ids, limit=limit))
        return jsonify({"success": True, "count": count})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


if __name__ == '__main__':
    local_ip = get_local_ip()
    port = 5000

    print("=" * 60)
    print("🚀  TeleRelay App is Running!")
    print(f"💻  On this Laptop:   http://localhost:{port}")
    print(f"📱  On your Mobile:   http://{local_ip}:{port}")
    print("    (Make sure your phone and laptop are on the same Wi-Fi)")
    print("=" * 60)

    # Print terminal QR code so user can scan with mobile camera
    try:
        import qrcode
        qr = qrcode.QRCode()
        qr.add_data(f"http://{local_ip}:{port}")
        qr.print_ascii(invert=True)
    except Exception:
        pass

    app.run(host='0.0.0.0', port=port, debug=False)
