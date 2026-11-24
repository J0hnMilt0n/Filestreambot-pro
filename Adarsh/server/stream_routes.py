
import asyncio
import logging
import math
import re
from urllib.parse import quote, unquote

from aiohttp import web

from Adarsh.bot import multi_clients, work_loads
from Adarsh.vars import Var
from Adarsh.server.exceptions import FIleNotFound
from Adarsh.utils.custom_dl import ByteStreamer
from Adarsh.utils.youtube_dl import YouTubeStreamer, get_youtube_streamer


logger = logging.getLogger(__name__)

CHUNK_SIZE = 512 * 1024

# One streamer instance per Telegram client.
streamers = {}


def get_streamer(index):
    if index not in streamers:
        streamers[index] = ByteStreamer(multi_clients[index])

    return streamers[index]


def parse_range_header(range_header, file_size):
    """
    Parse a single HTTP byte range.

    Supports:
      bytes=0-1023
      bytes=1024-
      bytes=-1024
    """

    if not range_header:
        return 0, file_size - 1

    if not range_header.startswith("bytes="):
        raise ValueError("Invalid range unit")

    range_value = range_header.replace("bytes=", "", 1).strip()

    # Multiple ranges are not supported.
    if "," in range_value:
        raise ValueError("Multiple ranges are not supported")

    start_text, end_text = range_value.split("-", 1)

    if not start_text:
        # Suffix range: last N bytes.
        suffix_length = int(end_text)

        if suffix_length <= 0:
            raise ValueError("Invalid suffix range")

        start = max(file_size - suffix_length, 0)
        end = file_size - 1

    else:
        start = int(start_text)
        end = int(end_text) if end_text else file_size - 1

    if start < 0 or end < start or start >= file_size:
        raise ValueError("Requested range is not satisfiable")

    end = min(end, file_size - 1)

    return start, end


# YouTube URL patterns
YOUTUBE_PATTERNS = [
    r'(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/|youtube\.com/v/)([a-zA-Z0-9_-]{11})',
    r'^([a-zA-Z0-9_-]{11})$'  # Direct video ID
]

def is_youtube_url(text: str) -> bool:
    """Check if text is a YouTube URL or video ID."""
    for pattern in YOUTUBE_PATTERNS:
        if re.match(pattern, text):
            return True
    return False

def extract_youtube_id(text: str) -> str:
    """Extract YouTube video ID from URL or return ID if already an ID."""
    for pattern in YOUTUBE_PATTERNS:
        match = re.match(pattern, text)
        if match:
            return match.group(1)
    return text


async def media_streamer(request):
    """
    Stream a Telegram file using Telethon with YouTube fallback.

    Expected route parameters:
        /watch/{message_id}/{file_name}
        /dl/{message_id}/{file_name}

    The message_id is the ID of the file in BIN_CHANNEL.
    Optional query param: ?id=youtube_id_or_url (used as fallback if Telegram file not found)
    """

    try:
        raw_file_id = (
            request.match_info.get("id")
            or request.match_info.get("file_id")
        )

        # Check for YouTube ID in query parameter (fallback)
        youtube_id_param = request.query.get("id")
        
        if not raw_file_id:
            return web.Response(
                text="Missing file ID",
                status=400
            )

        # First, try Telegram file streaming
        try:
            file_id = int(raw_file_id)
        except (TypeError, ValueError):
            # If path ID is not an integer, check if it's a YouTube URL/ID
            if is_youtube_url(raw_file_id):
                youtube_id = extract_youtube_id(raw_file_id)
                youtube_url = f"https://www.youtube.com/watch?v={youtube_id}"
                return await stream_youtube_video(request, youtube_url, youtube_id)
            return web.Response(
                text="Invalid file ID",
                status=400
            )

        if not multi_clients:
            return web.Response(
                text="Telegram clients are unavailable",
                status=503
            )

        # Select the client with the lowest current workload.
        index = min(
            multi_clients.keys(),
            key=lambda client_index: work_loads.get(client_index, 0)
        )

        streamer = get_streamer(index)

        # Try to retrieve Telegram file metadata
        try:
            file_properties = await streamer.get_file_properties(file_id)
        except FIleNotFound:
            # Telegram file not found - try YouTube fallback
            if youtube_id_param and is_youtube_url(youtube_id_param):
                logger.info("Telegram file %s not found, falling back to YouTube: %s", file_id, youtube_id_param)
                youtube_id = extract_youtube_id(youtube_id_param)
                youtube_url = f"https://www.youtube.com/watch?v={youtube_id}"
                return await stream_youtube_video(request, youtube_url, youtube_id)
            raise  # Re-raise if no YouTube fallback

        file_size = int(file_properties.file_size)

        if file_size <= 0:
            return web.Response(
                text="Invalid file size",
                status=404
            )

        file_name = (
            request.match_info.get("file_name")
            or file_properties.file_name
            or f"file_{file_id}"
        )

        mime_type = (
            file_properties.mime_type
            or "application/octet-stream"
        )

        # Determine the requested byte range.
        range_header = request.headers.get("Range")

        try:
            start, end = parse_range_header(
                range_header,
                file_size
            )
        except (ValueError, TypeError):
            return web.Response(
                status=416,
                headers={
                    "Content-Range": f"bytes */{file_size}",
                    "Accept-Ranges": "bytes"
                }
            )

        content_length = end - start + 1

        # Calculate Telegram chunk boundaries.
        offset = (start // CHUNK_SIZE) * CHUNK_SIZE

        first_part_cut = start - offset

        last_part_cut = (end % CHUNK_SIZE) + 1

        part_count = (
            (end // CHUNK_SIZE)
            - (start // CHUNK_SIZE)
            + 1
        )

        status = 206 if range_header else 200

        headers = {
            "Content-Type": mime_type,
            "Content-Length": str(content_length),
            "Accept-Ranges": "bytes",
            "Content-Disposition": (
                f'inline; filename="{quote(file_name)}"'
            ),
            "Cache-Control": "public, max-age=86400",
            "X-Content-Type-Options": "nosniff",
            # CORS headers for video element
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
            "Access-Control-Allow-Headers": "Range, Content-Type",
            "Access-Control-Expose-Headers": "Content-Length, Content-Range, Accept-Ranges",
        }

        if status == 206:
            headers["Content-Range"] = (
                f"bytes {start}-{end}/{file_size}"
            )

        response = web.StreamResponse(
            status=status,
            headers=headers
        )

        await response.prepare(request)

        # Ask ByteStreamer for the requested Telegram chunks.
        body = streamer.yield_file(
            file_properties=file_properties,
            index=index,
            offset=offset,
            first_part_cut=first_part_cut,
            last_part_cut=last_part_cut,
            part_count=part_count,
            chunk_size=CHUNK_SIZE
        )

        bytes_sent = 0

        try:
            async for chunk in body:
                if not chunk:
                    continue

                # Do not send more bytes than the HTTP range requested.
                remaining = content_length - bytes_sent

                if remaining <= 0:
                    break

                if len(chunk) > remaining:
                    chunk = chunk[:remaining]

                await response.write(chunk)

                bytes_sent += len(chunk)

            if bytes_sent != content_length:
                logger.warning(
                    "Incomplete stream for file %s: sent %s of %s bytes",
                    file_id,
                    bytes_sent,
                    content_length
                )

        except (ConnectionResetError, asyncio.CancelledError):
            logger.info(
                "Client disconnected while streaming file %s",
                file_id
            )
            raise

        finally:
            await body.aclose()

        await response.write_eof()

        return response

    except FIleNotFound:
        return web.Response(
            text="File not found",
            status=404
        )

    except asyncio.CancelledError:
        raise

    except Exception as e:
        logger.exception("File streaming failed: %s", e)

        if not request.transport or request.transport.is_closing():
            return web.Response(status=500)

        # Return actual error for debugging (remove in production)
        return web.Response(
            text=f"Internal streaming error: {type(e).__name__}: {e}",
            status=500
        )


async def stream_youtube_video(request, youtube_url: str, video_id: str):
    """Stream a YouTube video using yt-dlp."""
    try:
        yt_streamer = await get_youtube_streamer()
        
        # Get video info for metadata
        try:
            info = await yt_streamer.get_video_info(youtube_url)
            file_name = info.get("title", f"youtube_{video_id}") + ".mp4"
            mime_type = "video/mp4"
        except Exception:
            file_name = f"youtube_{video_id}.mp4"
            mime_type = "video/mp4"
        
        # Determine the requested byte range
        range_header = request.headers.get("Range")
        
        # For YouTube, we don't know exact file size, so we stream without Content-Length
        # unless we can get it from yt-dlp
        file_size = None
        try:
            info = await yt_streamer.get_video_info(youtube_url)
            for fmt in info.get("formats", []):
                if fmt.get("ext") == "mp4" and fmt.get("filesize"):
                    file_size = fmt["filesize"]
                    break
        except Exception:
            pass
        
        start = 0
        end = file_size - 1 if file_size else None
        
        if range_header and file_size:
            try:
                start, end = parse_range_header(range_header, file_size)
            except (ValueError, TypeError):
                return web.Response(
                    status=416,
                    headers={
                        "Content-Range": f"bytes */{file_size}",
                        "Accept-Ranges": "bytes"
                    }
                )
        
        content_length = (end - start + 1) if file_size and end else None
        
        status = 206 if range_header else 200
        
        headers = {
            "Content-Type": mime_type,
            "Accept-Ranges": "bytes",
            "Content-Disposition": f'inline; filename="{quote(file_name)}"',
            "Cache-Control": "public, max-age=3600",
            "X-Content-Type-Options": "nosniff",
            # CORS headers for video element
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
            "Access-Control-Allow-Headers": "Range, Content-Type",
            "Access-Control-Expose-Headers": "Content-Length, Content-Range, Accept-Ranges",
        }
        
        if content_length:
            headers["Content-Length"] = str(content_length)
        
        if status == 206 and file_size:
            headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
        
        response = web.StreamResponse(
            status=status,
            headers=headers
        )
        
        await response.prepare(request)
        
        # Stream video using yt-dlp + ffmpeg
        chunk_size = 512 * 1024
        bytes_sent = 0
        
        try:
            async for chunk in yt_streamer.stream_video(
                youtube_url,
                start=start,
                end=end,
                chunk_size=chunk_size
            ):
                if not chunk:
                    continue
                
                if content_length:
                    remaining = content_length - bytes_sent
                    if remaining <= 0:
                        break
                    if len(chunk) > remaining:
                        chunk = chunk[:remaining]
                
                await response.write(chunk)
                bytes_sent += len(chunk)
        
        except (ConnectionResetError, asyncio.CancelledError):
            logger.info("Client disconnected while streaming YouTube video %s", video_id)
            raise
        
        except Exception as e:
            logger.exception("YouTube streaming failed for %s: %s", video_id, e)
            raise
        
        finally:
            await response.write_eof()
        
        return response
        
    except Exception as e:
        logger.exception("YouTube stream error: %s", e)
        if not request.transport or request.transport.is_closing():
            return web.Response(status=500)
        return web.Response(
            text=f"YouTube streaming error: {type(e).__name__}: {e}",
            status=500
        )


async def health_check(request):
    """Health check endpoint for container orchestration."""
    return web.json_response({
        "status": "healthy",
        "service": "filestreambot-pro",
        "clients_connected": len(multi_clients)
    })


async def readiness_check(request):
    """Readiness check endpoint for container orchestration."""
    if not multi_clients:
        return web.json_response({
            "status": "not_ready",
            "reason": "No Telegram clients connected"
        }, status=503)
    return web.json_response({
        "status": "ready",
        "service": "filestreambot-pro",
        "clients_connected": len(multi_clients)
    })


async def options_handler(request):
    """Handle CORS preflight requests."""
    return web.Response(
        status=200,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
            "Access-Control-Allow-Headers": "Range, Content-Type",
            "Access-Control-Max-Age": "86400",
        }
    )


routes = [
    web.get("/health", health_check),
    web.get("/ready", readiness_check),
    web.get("/watch/{id}/{file_name}", media_streamer),
    web.get("/dl/{id}/{file_name}", media_streamer),
    web.options("/watch/{id}/{file_name}", options_handler),
    web.options("/dl/{id}/{file_name}", options_handler),
]