
import math
import asyncio
import logging

from typing import Dict, Union

from Adarsh.vars import Var
from Adarsh.bot import work_loads

from pyrogram import Client, utils, raw
from pyrogram.session import Session, Auth
from pyrogram.errors import (
    AuthBytesInvalid,
    FloodWait,
    RPCError,
    Timeout
)

from .file_properties import get_file_ids

from Adarsh.server.exceptions import FIleNotFound

from pyrogram.file_id import (
    FileId,
    FileType,
    ThumbnailSource
)


class ByteStreamer:

    def __init__(self, client: Client):

        self.clean_timer = 30 * 60
        self.client: Client = client

        self.cached_file_ids: Dict[int, FileId] = {}

        asyncio.create_task(self.clean_cache())

    # --------------------------------------------
    # FILE PROPERTIES
    # --------------------------------------------

    async def get_file_properties(self, id: int) -> FileId:

        if id not in self.cached_file_ids:
            await self.generate_file_properties(id)

            logging.debug(
                f"Cached file properties for message ID {id}"
            )

        return self.cached_file_ids[id]

    async def generate_file_properties(self, id: int) -> FileId:

        file_id = await get_file_ids(
            self.client,
            Var.BIN_CHANNEL,
            id
        )

        if not file_id:
            logging.error(
                f"Message with ID {id} not found"
            )
            raise FIleNotFound

        self.cached_file_ids[id] = file_id

        logging.debug(
            f"Generated file properties for message ID {id}"
        )

        return file_id

    # --------------------------------------------
    # MEDIA SESSION
    # --------------------------------------------

    async def generate_media_session(
        self,
        client: Client,
        file_id: FileId
    ) -> Session:

        media_session = client.media_sessions.get(
            file_id.dc_id
        )

        if media_session is not None:
            if media_session.is_connected:
                return media_session

            logging.warning(
                f"Media session disconnected for DC {file_id.dc_id}"
            )

            try:
                await media_session.stop()
            except Exception:
                logging.exception("Error stopping old media session")

            client.media_sessions.pop(file_id.dc_id, None)

        if file_id.dc_id != await client.storage.dc_id():

            media_session = Session(
                client,
                file_id.dc_id,
                await Auth(
                    client,
                    file_id.dc_id,
                    await client.storage.test_mode()
                ).create(),
                await client.storage.test_mode(),
                is_media=True
            )

            await media_session.start()

            try:

                for attempt in range(6):

                    exported_auth = await client.invoke(
                        raw.functions.auth.ExportAuthorization(
                            dc_id=file_id.dc_id
                        )
                    )

                    try:

                        await media_session.send(
                            raw.functions.auth.ImportAuthorization(
                                id=exported_auth.id,
                                bytes=exported_auth.bytes
                            )
                        )

                        break

                    except AuthBytesInvalid:

                        logging.warning(
                            f"Invalid authorization bytes for DC "
                            f"{file_id.dc_id}, attempt {attempt + 1}"
                        )

                        await asyncio.sleep(1)

                else:

                    raise AuthBytesInvalid

            except Exception:

                await media_session.stop()
                raise

        else:

            media_session = Session(
                client,
                file_id.dc_id,
                await client.storage.auth_key(),
                await client.storage.test_mode(),
                is_media=True
            )

            await media_session.start()

        client.media_sessions[file_id.dc_id] = media_session

        logging.info(
            f"Created media session for DC {file_id.dc_id}"
        )

        return media_session

    # --------------------------------------------
    # FILE LOCATION
    # --------------------------------------------

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

            else:

                if file_id.chat_access_hash == 0:

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

    # --------------------------------------------
    # STREAM FILE
    # --------------------------------------------

    async def yield_file(
        self,
        file_id: FileId,
        index: int,
        offset: int,
        first_part_cut: int,
        last_part_cut: int,
        part_count: int,
        chunk_size: int
    ) -> Union[str, None]:

        work_loads[index] += 1

        current_part = 1

        try:

            logging.info(
                f"Starting file streaming with client {index}"
            )

            media_session = await self.generate_media_session(
                self.client,
                file_id
            )

            location = await self.get_location(file_id)

            while current_part <= part_count:

                retries = 0
                max_retries = 3

                while True:

                    try:

                        r = await media_session.send(
                            raw.functions.upload.GetFile(
                                location=location,
                                offset=offset,
                                limit=chunk_size
                            )
                        )

                        break

                    except FloodWait as e:

                        wait_time = e.value

                        logging.warning(
                            f"Telegram FloodWait: {wait_time}s"
                        )

                        await asyncio.sleep(wait_time)

                    except (Timeout, asyncio.TimeoutError) as e:

                        retries += 1

                        logging.warning(
                            f"Telegram timeout: attempt "
                            f"{retries}/{max_retries}"
                        )

                        if retries >= max_retries:
                            raise

                        await asyncio.sleep(2 ** retries)

                    except RPCError as e:

                        logging.error(
                            f"Telegram RPC error: {e}"
                        )

                        raise

                if not isinstance(r, raw.types.upload.File):

                    logging.error(
                        "Telegram returned an unexpected file response"
                    )

                    raise RuntimeError(
                        "Invalid Telegram file response"
                    )

                chunk = r.bytes

                if not chunk:
                    break

                if current_part == 1 and part_count == 1:

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
                "File streaming cancelled by client"
            )

            raise

        except Exception:

            logging.exception(
                "File streaming failed"
            )

            raise

        finally:

            logging.info(
                f"Finished streaming after {current_part - 1} parts"
            )

            work_loads[index] = max(
                0,
                work_loads[index] - 1
            )

    # --------------------------------------------
    # CACHE CLEANER
    # --------------------------------------------

    async def clean_cache(self):

        while True:

            await asyncio.sleep(self.clean_timer)

            self.cached_file_ids.clear()

            logging.debug(
                "Cleaned file properties cache"
            )
