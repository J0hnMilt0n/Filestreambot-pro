
# (c) adarsh-goel

import asyncio
import logging

from typing import Dict

from telethon import TelegramClient
from telethon.errors import FloodWaitError, RPCError

from Adarsh.bot import work_loads
from Adarsh.server.exceptions import FIleNotFound
from Adarsh.vars import Var

from .file_properties import (
    FileProperties,
    get_file_ids
)


class ByteStreamer:

    def __init__(self, client: TelegramClient):

        self.clean_timer = 30 * 60

        self.client = client

        self.cached_file_ids: Dict[int, FileProperties] = {}

        self._cache_lock = asyncio.Lock()

        asyncio.create_task(self.clean_cache())

    async def get_file_properties(
        self,
        id: int
    ) -> FileProperties:

        if id not in self.cached_file_ids:

            async with self._cache_lock:

                if id not in self.cached_file_ids:

                    await self.generate_file_properties(id)

        return self.cached_file_ids[id]

    async def generate_file_properties(
        self,
        id: int
    ) -> FileProperties:

        file_properties = await get_file_ids(
            self.client,
            Var.BIN_CHANNEL,
            id
        )

        if not file_properties:

            logging.error(
                "File not found. Message ID: %s",
                id
            )

            raise FIleNotFound

        self.cached_file_ids[id] = file_properties

        logging.debug(
            "Cached file properties for message ID %s",
            id
        )

        return file_properties

    async def get_chunk(
        self,
        file_properties: FileProperties,
        offset: int,
        chunk_size: int,
        retries: int = 5
    ):

        for attempt in range(retries):

            stream = None

            try:

                stream = self.client.iter_download(
                    file_properties.message,
                    offset=offset,
                    limit=1,
                    chunk_size=chunk_size,
                    request_size=chunk_size,
                    file_size=file_properties.file_size
                )

                chunk = await stream.__anext__()

                if not chunk:
                    return b""

                return bytes(chunk)

            except StopAsyncIteration:
                return b""

            except FloodWaitError as e:

                delay = max(
                    int(getattr(e, "seconds", 5)),
                    1
                )

                logging.warning(
                    "Telegram FloodWait: %s seconds",
                    delay
                )

                await asyncio.sleep(delay)

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

            except asyncio.CancelledError:
                raise

            except Exception:

                if attempt == retries - 1:
                    raise

                delay = min(2 ** attempt, 15)

                logging.exception(
                    "Telegram download failed. Retrying in %s seconds",
                    delay
                )

                await asyncio.sleep(delay)

            finally:

                if stream is not None:

                    try:
                        await stream.close()
                    except Exception:
                        pass

        raise asyncio.TimeoutError(
            "Telegram file request failed"
        )

    async def yield_file(
        self,
        file_id: FileProperties,
        index: int,
        offset: int,
        first_part_cut: int,
        last_part_cut: int,
        part_count: int,
        chunk_size: int
    ):

        work_loads[index] += 1

        current_part = 0

        try:

            while current_part < part_count:

                chunk_offset = (
                    offset + current_part * chunk_size
                )

                chunk = await self.get_chunk(
                    file_id,
                    chunk_offset,
                    chunk_size
                )

                if not chunk:

                    logging.warning(
                        "Empty Telegram chunk at offset %s",
                        chunk_offset
                    )

                    break

                current_part += 1

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
                current_part
            )

            work_loads[index] = max(
                0,
                work_loads.get(index, 0) - 1
            )

    async def clean_cache(self):

        while True:

            await asyncio.sleep(self.clean_timer)

            async with self._cache_lock:

                self.cached_file_ids.clear()

            logging.debug(
                "Cleaned file properties cache"
            )
