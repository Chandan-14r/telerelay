"""
Fast download/upload implementation for Telethon using parallel DC connections.
Bypasses single-connection throttling and achieves maximum network speed.
"""

import asyncio
import math
import os
import inspect
from telethon import utils, helpers
from telethon.crypto import AuthKey
from telethon.network import MTProtoSender
from telethon.tl.alltlobjects import LAYER
from telethon.tl.functions import (
    InitConnectionRequest,
    InvokeWithLayerRequest,
)
from telethon.tl.functions.auth import (
    ExportAuthorizationRequest,
    ImportAuthorizationRequest,
)
from telethon.tl.functions.upload import (
    GetFileRequest,
    SaveFilePartRequest,
    SaveBigFilePartRequest,
)
from telethon.tl.types import (
    InputFile,
    InputFileBig,
    InputDocumentFileLocation,
    InputPhotoFileLocation,
    InputPeerPhotoFileLocation,
)

# Standard chunk size for high-speed transfers: 512 KB
CHUNK_SIZE = 512 * 1024


class ParallelTransferrer:
    def __init__(self, client, dc_id=None):
        self.client = client
        self.loop = client.loop
        self.dc_id = dc_id or client.session.dc_id
        self.senders = []

    async def _init_sender(self):
        dc = await self.client._get_dc(self.dc_id)
        sender = MTProtoSender(self.client.session.auth_key, loggers=self.client._log)
        await sender.connect(self.client._connection(dc.ip_address, dc.port, dc.id, loggers=self.client._log, loop=self.loop))
        if not sender.is_connected():
            return None

        if self.dc_id != self.client.session.dc_id:
            auth = await self.client(ExportAuthorizationRequest(self.dc_id))
            req = ImportAuthorizationRequest(id=auth.id, bytes=auth.bytes)
        else:
            req = InitConnectionRequest(
                api_id=self.client.api_id,
                device_model=self.client.device_model,
                system_version=self.client.system_version,
                app_version=self.client.app_version,
                system_lang_code=self.client.system_lang_code,
                lang_pack="",
                lang_code=self.client.lang_code,
                query=self.client._init_query,
            )

        await sender.send(InvokeWithLayerRequest(LAYER, req))
        return sender

    async def init_senders(self, count=4):
        tasks = [self._init_sender() for _ in range(count)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        self.senders = [s for s in results if s and not isinstance(s, Exception)]
        if not self.senders:
            self.senders = [self.client._sender]

    async def close(self):
        for sender in self.senders:
            if sender != self.client._sender:
                await sender.disconnect()


async def fast_download(client, location, file_size, output_buffer, progress_callback=None, workers=4):
    """
    Downloads file parts in parallel across multiple worker connections.
    """
    transferrer = ParallelTransferrer(client, location.dc_id if hasattr(location, 'dc_id') else None)
    await transferrer.init_senders(count=workers)
    
    total_parts = math.ceil(file_size / CHUNK_SIZE)
    queue = asyncio.Queue()
    for i in range(total_parts):
        queue.put_nowait(i)

    downloaded_parts = {}
    downloaded_bytes = 0

    async def worker(sender):
        nonlocal downloaded_bytes
        while not queue.empty():
            part_index = await queue.get()
            offset = part_index * CHUNK_SIZE
            limit = min(CHUNK_SIZE, file_size - offset)
            
            req = GetFileRequest(
                location=location,
                offset=offset,
                limit=limit,
                precise=True,
            )
            try:
                res = await sender.send(req)
                downloaded_parts[part_index] = res.bytes
                downloaded_bytes += len(res.bytes)
                if progress_callback:
                    res_cb = progress_callback(downloaded_bytes, file_size)
                    if inspect.isawaitable(res_cb):
                        await res_cb
            except Exception as e:
                await queue.put(part_index)
                break
            finally:
                queue.task_done()

    tasks = [asyncio.create_task(worker(s)) for s in transferrer.senders]
    await asyncio.gather(*tasks, return_exceptions=True)
    await transferrer.close()

    for i in range(total_parts):
        if i in downloaded_parts:
            output_buffer.write(downloaded_parts[i])
        else:
            raise RuntimeError(f"Missing chunk {i} during fast download")

    output_buffer.seek(0)
    return output_buffer


async def fast_upload(client, file_buffer, file_name, progress_callback=None, workers=4):
    """
    Uploads file parts in parallel across multiple worker connections.
    """
    file_buffer.seek(0, os.SEEK_END)
    file_size = file_buffer.tell()
    file_buffer.seek(0)

    file_id = helpers.generate_random_long()
    is_big = file_size > 10 * 1024 * 1024  # > 10MB uses big file API
    total_parts = math.ceil(file_size / CHUNK_SIZE)

    transferrer = ParallelTransferrer(client)
    await transferrer.init_senders(count=workers)

    queue = asyncio.Queue()
    for i in range(total_parts):
        queue.put_nowait(i)

    uploaded_bytes = 0

    async def worker(sender):
        nonlocal uploaded_bytes
        while not queue.empty():
            part_index = await queue.get()
            offset = part_index * CHUNK_SIZE
            file_buffer.seek(offset)
            chunk = file_buffer.read(CHUNK_SIZE)

            if is_big:
                req = SaveBigFilePartRequest(file_id, part_index, total_parts, chunk)
            else:
                req = SaveFilePartRequest(file_id, part_index, chunk)

            try:
                await sender.send(req)
                uploaded_bytes += len(chunk)
                if progress_callback:
                    res_cb = progress_callback(uploaded_bytes, file_size)
                    if inspect.isawaitable(res_cb):
                        await res_cb
            except Exception:
                await queue.put(part_index)
                break
            finally:
                queue.task_done()

    tasks = [asyncio.create_task(worker(s)) for s in transferrer.senders]
    await asyncio.gather(*tasks, return_exceptions=True)
    await transferrer.close()

    if is_big:
        return InputFileBig(id=file_id, parts=total_parts, name=file_name)
    else:
        return InputFile(id=file_id, parts=total_parts, name=file_name, md5_checksum="")
