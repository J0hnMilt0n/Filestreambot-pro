
import asyncio
import logging

from typing import Dict, Union

from pyrogram import Client, utils, raw
from pyrogram.session import Session, Auth
from pyrogram.errors import (
    AuthBytesInvalid,
    FloodWait,
    RPCError
)

from pyrogram.file_id import (
    FileId,
    FileType,
    ThumbnailSource
)

from Adarsh.vars import Var
from Adarsh.bot import work_loads
from Adarsh.server.exceptions import FIleNotFound

from .file_properties import get_file_ids


class ByteStreamer:

    def __init__(self, client: Client):
        self.clean_timer = 30 * 60

        self.client: Client = client

        self.cached_file_ids: Dict[int, FileId] = {}

        self._cache_lock = asyncio.Lock()
        self._session_locks = {}

        asyncio.create_task(self.clean_cache())

    async def get_file_properties(self, id: int) -> FileId:

        if id not in self.cached_file_ids:
            async with self._cache_lock:
                if id not in self.cached_file_ids:
                    await self.generate_file_properties(id)

        return self.cached_file_ids[id]

    async def generate_file_properties(self, id: int) -> FileId:

        file_id = await get_file_ids(
            self.client,
            Var.BIN_CHANNEL,
            id
        )

        if not file_id:
            logging.error(
                "File not found. Message ID: %s",
                id
            )
            raise FIleNotFound

        self.cached_file_ids[id] = file_id

        logging.debug(
            "Cached file properties for message ID %s",
            id
        )

        return file_id

    async def generate_media_session(
        self,
        client: Client,
        file_id: FileId
    ) -> Session:

        dc_id = file_id.dc_id

        media_session = client.media_sessions.get(dc_id)

        if media_session is not None:
            return media_session

        if dc_id not in self._session_locks:
            self._session_locks[dc_id] = asyncio.Lock()

        async with self._session_locks[dc_id]:

            media_session = client.media_sessions.get(dc_id)

            if media_session is not None:
                return media_session

            test_mode = await client.storage.test_mode()

            if dc_id != await client.storage.dc_id():

                auth_key = await Auth(
                    client,
                    dc_id,
                    test_mode
                ).create()

            else:
                auth_key = await client.storage.auth_key()

            media_session = Session(
                client,
                dc_id,
                auth_key,
                test_mode,
                is_media=True
            )

            try:

                await media_session.start()

                if dc_id != await client.storage.dc_id():

                    authorized = False

                    for attempt in range(5):

                        exported_auth = await client.invoke(
                            raw.functions.auth.ExportAuthorization(
                                dc_id=dc_id
                            )
                        )

                        try:

                            await media_session.send(
                                raw.functions.auth.ImportAuthorization(
                                    id=exported_auth.id,
                                    bytes=exported_auth.bytes
                                )
                            )

                            authorized = True
                            break

                        except AuthBytesInvalid:

                            logging.warning(
                                "Invalid authorization bytes for DC %s",
                                dc_id
                            )

                            await asyncio.sleep(
                                min(2 ** attempt, 10)
                            )

                    if not authorized:
                        raise AuthBytesInvalid

                client.media_sessions[dc_id] = media_session

                logging.info(
                    "Media session created for DC %s",
                    dc_id
                )

                return media_session

            except Exception:

                try:
                    await media_session.stop()
                except Exception:
                    logging.exception(
                        "Failed to stop media session"
                    )

                raise

    @staticmethod
    async def get_location(
        file_id: FileId
    ) -> Union[
        raw.types.InputPhotoFileLocation,
        raw.types.InputDocumentFileLocation,
        raw.types.InputPeerPhotoFileLocation
    ]:

        file_type = file_id.file_type

        if file_type == FileType.CHAT_PHOTO:

            if file_id.chat_id > 0:

                peer = raw.types.InputPeerUser(
                    user_id=file_id.chat_id,
                    access_hash=file_id.chat_access_hash
                )

            elif file_id.chat_access_hash == 0:

                peer = raw.types.InputPeerChat(
                    chat_id=-file_id.chat_id
                )

            else:

                peer = raw.types.InputPeerChannel(
                    channel_id=utils.get_channel_id(
                        file_id.chat_id
                    ),
                    access_hash=file_id.chat_access_hash
                )

            location = raw.types.InputPeerPhotoFileLocation(
                peer=peer,
                volume_id=file_id.volume_id,
                local_id=file_id.local_id,
                big=(
                    file_id.thumbnail_source
                    == ThumbnailSource.CHAT_PHOTO_BIG
                )
            )

        elif file_type == FileType.PHOTO:

            location = raw.types.InputPhotoFileLocation(
                id=file_id.media_id,
                access_hash=file_id.access_hash,
                file_reference=file_id.file_reference,
                thumb_size=file_id.thumbnail_size
            )

        else:

            location = raw.types.InputDocumentFileLocation(
                id=file_id.media_id,
                access_hash=file_id.access_hash,
                file_reference=file_id.file_reference,
                thumb_size=file_id.thumbnail_size
            )

        return location

    async def get_file_with_retry(
        self,
        media_session,
        location,
        offset,
        chunk_size,
        retries=5
    ):

        for attempt in range(retries):

            try:

                return await media_session.send(
                    raw.functions.upload.GetFile(
                        location=location,
                        offset=offset,
                        limit=chunk_size
                    )
                )

            except FloodWait as e:

                logging.warning(
                    "Telegram FloodWait: %s seconds",
                    e.value
                )

                await asyncio.sleep(e.value)

            except RPCError as e:

                if attempt == retries - 1:
                    raise

                delay = min(2 ** attempt, 15)

                logging.warning(
                    "Telegram RPC error: %s. Retrying in %s seconds",
                    e,
                    delay
                )

                await asyncio.sleep(delay)

            except asyncio.TimeoutError:

                if attempt == retries - 1:
                    raise

                delay = min(2 ** attempt, 15)

                logging.warning(
                    "Telegram timeout. Retrying in %s seconds",
                    delay
                )

                await asyncio.sleep(delay)

        raise asyncio.TimeoutError(
            "Telegram file request failed"
        )

    async def yield_file(
        self,
        file_id: FileId,
        index: int,
        offset: int,
        first_part_cut: int,
        last_part_cut: int,
        part_count: int,
        chunk_size: int
    ):

        work_loads[index] += 1

        current_part = 1

        try:

            client = self.client

            media_session = await self.generate_media_session(
                client,
                file_id
            )

            location = await self.get_location(file_id)

            while current_part <= part_count:

                r = await self.get_file_with_retry(
                    media_session,
                    location,
                    offset,
                    chunk_size
                )

                if not isinstance(r, raw.types.upload.File):

                    logging.error(
                        "Unexpected Telegram file response"
                    )

                    break

                chunk = r.bytes

                if not chunk:
                    break

                if part_count == 1:

                    yield chunk[
                        first_part_cut:last_part_cut
                    ]

                elif current_part == 1:

                    yield chunk[first_part_cut:]

                elif current_part == part_count:

                    yield chunk[:last_part_cut]

                else:

                    yield chunk

                current_part += 1
                offset += chunk_size

        except asyncio.CancelledError:

            logging.info(
                "File streaming cancelled"
            )

            raise

        except Exception:

            logging.exception(
                "File streaming failed"
            )

            raise

        finally:

            logging.debug(
                "Finished streaming after %s parts",
                current_part - 1
            )

            work_loads[index] = max(
                0,
                work_loads[index] - 1
            )

    async def clean_cache(self):

        while True:

            await asyncio.sleep(self.clean_timer)

            async with self._cache_lock:
                self.cached_file_ids.clear()

            logging.debug(
                "Cleaned file properties cache"
            )
