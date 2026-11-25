
import asyncio
import logging
import math
import re
from urllib.parse import quote, unquote

from aiohttp import web
from aiohttp.client_exceptions import ClientConnectionError
from telethon.errors import AuthKeyUnregisteredError

from Adarsh.bot import multi_clients, work_loads
from Adarsh.vars import Var
from Adarsh.server.exceptions import FIleNotFound
from Adarsh.utils.custom_dl import ByteStreamer
from Adarsh.utils.youtube_dl import YouTubeStreamer, get_youtube_streamer


logger = logging.getLogger(__name__)

CHUNK_SIZE = 512 * 1024

# Track application start time for uptime
import time
APP_START_TIME = time.time()

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
    Optional query params:
        ?id=youtube_id_or_url (used as fallback if Telegram file not found)
        ?start=seconds (custom start time for video playback)
    """

    try:
        raw_file_id = (
            request.match_info.get("id")
            or request.match_info.get("file_id")
        )

        # Check for YouTube ID in query parameter (fallback)
        youtube_id_param = request.query.get("id")
        
        # Support custom start time via ?start=seconds query parameter
        start_seconds = request.query.get("start")
        start_bytes = 0
        if start_seconds:
            try:
                start_seconds = float(start_seconds)
                # We'll convert to bytes after getting file_size
                # For now, store the seconds value
            except ValueError:
                start_seconds = None
        
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
                return await stream_youtube_video(request, youtube_url, youtube_id, start_seconds=start_seconds)
            return web.Response(
                text="Invalid file ID",
                status=400
            )

        if not multi_clients:
            return web.Response(
                text="Telegram clients are unavailable",
                status=503
            )

        # Filter out non-Telethon clients (like "pyro" Pyrogram client) for streaming
        telethon_clients = {k: v for k, v in multi_clients.items() if isinstance(k, int)}
        
        # Debug logging
        logger.info("multi_clients keys: %s, telethon_clients: %s", list(multi_clients.keys()), list(telethon_clients.keys()))
        for idx, client in telethon_clients.items():
            logger.info("Client %s: connected=%s", idx, client.is_connected())
        
        # Clean up disconnected clients from work_loads to prevent stale entries
        connected_clients = {k: v for k, v in telethon_clients.items() if v.is_connected()}
        if len(connected_clients) != len(telethon_clients):
            disconnected = set(telethon_clients.keys()) - set(connected_clients.keys())
            logger.warning("Removing disconnected clients from work_loads: %s", disconnected)
            for idx in disconnected:
                work_loads.pop(idx, None)
        
        if not connected_clients:
            logger.warning("No Telethon clients available for streaming")
            if youtube_id_param and is_youtube_url(youtube_id_param):
                youtube_id = extract_youtube_id(youtube_id_param)
                youtube_url = f"https://www.youtube.com/watch?v={youtube_id}"
                return await stream_youtube_video(request, youtube_url, youtube_id, start_seconds=start_seconds)
            return web.Response(
                text="Telegram clients not ready, please retry in a moment",
                status=503,
                headers={"Retry-After": "30"}
            )

        # Select the client with the lowest current workload (only connected Telethon clients)
        index = min(
            connected_clients.keys(),
            key=lambda client_index: work_loads.get(client_index, 0)
        )

        streamer = get_streamer(index)

        # Try to retrieve Telegram file metadata
        try:
            file_properties = await streamer.get_file_properties(file_id)
        except AuthKeyUnregisteredError:
            logger.error("AuthKeyUnregisteredError - Telegram session expired, bot needs re-authentication")
            # Clean up expired session files so next retry creates fresh session
            try:
                from Adarsh.bot.clients import cleanup_session_files
                cleanup_session_files()
            except Exception:
                pass
            # Try YouTube fallback immediately
            if youtube_id_param and is_youtube_url(youtube_id_param):
                logger.info("Falling back to YouTube due to expired session: %s", youtube_id_param)
                youtube_id = extract_youtube_id(youtube_id_param)
                youtube_url = f"https://www.youtube.com/watch?v={youtube_id}"
                return await stream_youtube_video(request, youtube_url, youtube_id, start_seconds=start_seconds)
            return web.Response(
                text="Telegram session expired, please retry later",
                status=503,
                headers={"Retry-After": "30"}
            )
        except FIleNotFound:
            # Telegram file not found - try YouTube fallback
            if youtube_id_param and is_youtube_url(youtube_id_param):
                logger.info("Telegram file %s not found, falling back to YouTube: %s", file_id, youtube_id_param)
                youtube_id = extract_youtube_id(youtube_id_param)
                youtube_url = f"https://www.youtube.com/watch?v={youtube_id}"
                return await stream_youtube_video(request, youtube_url, youtube_id, start_seconds=start_seconds)
            raise  # Re-raise if no YouTube fallback

        file_size = int(file_properties.file_size)

        if file_size <= 0:
            return web.Response(
                text="Invalid file size",
                status=404
            )

        file_name = (
            unquote(request.match_info.get("file_name", "")).replace("+", " ")
            or file_properties.file_name
            or f"file_{file_id}"
        )

        mime_type = (
            file_properties.mime_type
            or "application/octet-stream"
        )

        # Determine the requested byte range.
        range_header = request.headers.get("Range")
        
        # If no Range header but start_seconds provided, calculate byte position
        # Note: This is approximate since we don't know exact bitrate
        # For better seeking, the video player should send Range headers
        if not range_header and start_seconds is not None:
            # Estimate bytes from seconds (rough approximation: assume ~1MB/s for 720p)
            # This is a fallback; proper seeking requires Range header from player
            estimated_bitrate = 1_000_000  # ~8 Mbps
            start = int(start_seconds * estimated_bitrate)
            start = min(max(start, 0), file_size - 1)
            end = file_size - 1
            logger.info("Using start_seconds=%.1f, estimated start byte=%d", start_seconds, start)
        else:
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
            file_id=file_properties,
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

        except (ConnectionResetError, asyncio.CancelledError, ClientConnectionError):
            logger.info(
                "Client disconnected while streaming file %s",
                file_id
            )
            return response

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


async def stream_youtube_video(request, youtube_url: str, video_id: str, start_seconds: float = None):
    """Stream a YouTube video using yt-dlp with full Range request support.

    IMPORTANT: Response headers are sent IMMEDIATELY (before the slow yt-dlp
    extraction) so Koyeb's load balancer doesn't return 502 while we wait
    for Invidious/yt-dlp to resolve the stream URL.
    """
    try:
        yt_streamer = await get_youtube_streamer()

        # Parse Range header from request for seeking support
        range_header = request.headers.get("Range")
        start_bytes = 0
        end_bytes = None
        
        if range_header:
            # Parse "bytes=start-end" format
            try:
                range_str = range_header.replace("bytes=", "")
                if "-" in range_str:
                    start_str, end_str = range_str.split("-", 1)
                    if start_str:
                        start_bytes = int(start_str)
                    if end_str:
                        end_bytes = int(end_str)
            except (ValueError, IndexError):
                logger.warning("Invalid Range header: %s", range_header)
        
        # Fallback to start_seconds query parameter if no Range header
        if start_bytes == 0 and start_seconds is not None:
            estimated_bitrate = 1_000_000  # ~8 Mbps
            start_bytes = int(start_seconds * estimated_bitrate)

        # Get stream URL first to determine content length (for Content-Range)
        stream_url = await yt_streamer.get_stream_url(youtube_url)
        if not stream_url:
            return web.Response(text="No stream URL found", status=500)

        # Prepare headers for upstream request to get content length
        import aiohttp
        headers_upstream = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://www.youtube.com/",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": "https://www.youtube.com",
        }
        if start_bytes or end_bytes:
            range_val = f"bytes={start_bytes}-"
            if end_bytes:
                range_val += str(end_bytes)
            headers_upstream["Range"] = range_val

        cookie_path = yt_streamer._prepare_cookies()
        cookie_jar = None
        if cookie_path:
            import http.cookiejar
            cookie_jar = http.cookiejar.MozillaCookieJar(cookie_path)
            cookie_jar.load(ignore_discard=True, ignore_expires=True)

        timeout = aiohttp.ClientTimeout(total=300, connect=30, sock_read=30)
        connector = aiohttp.TCPConnector(limit=10)
        
        # First, make a HEAD request to get content length
        content_length = None
        async with aiohttp.ClientSession(timeout=timeout, connector=connector, cookie_jar=cookie_jar) as session:
            async with session.head(stream_url, headers=headers_upstream) as resp:
                if resp.status in (200, 206):
                    cl = resp.headers.get("Content-Length")
                    if cl:
                        content_length = int(cl)
                    # Also check Content-Range for total size
                    cr = resp.headers.get("Content-Range")
                    if cr and "/" in cr:
                        try:
                            total_size = int(cr.split("/")[-1])
                            if content_length is None:
                                content_length = total_size
                        except (ValueError, IndexError):
                            pass

        # Now prepare response headers
        response_headers = {
            "Content-Type": "video/mp4",
            "Accept-Ranges": "bytes",
            "Content-Disposition": f'inline; filename="youtube_{video_id}.mp4"',
            "Cache-Control": "public, max-age=3600",
            "X-Content-Type-Options": "nosniff",
            # CORS headers for video element
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
            "Access-Control-Allow-Headers": "Range, Content-Type",
            "Access-Control-Expose-Headers": "Content-Length, Content-Range, Accept-Ranges",
        }

        # Determine response status and Content-Range
        if range_header and content_length:
            # 206 Partial Content
            response_status = 206
            end_pos = end_bytes if end_bytes is not None else (content_length - 1)
            response_headers["Content-Range"] = f"bytes {start_bytes}-{end_pos}/{content_length}"
            response_headers["Content-Length"] = str(end_pos - start_bytes + 1)
        else:
            # 200 OK (full content or unknown size)
            response_status = 200
            if content_length:
                response_headers["Content-Length"] = str(content_length)

        response = web.StreamResponse(status=response_status, headers=response_headers)
        await response.prepare(request)

        # Now stream the actual content
        try:
            stream_gen = yt_streamer.stream_video(
                youtube_url,
                start=start_bytes,
                end=end_bytes,
                chunk_size=512 * 1024
            )

            bytes_sent = 0
            first_chunk = True
            while True:
                try:
                    timeout = 120.0 if first_chunk else 30.0
                    chunk = await asyncio.wait_for(stream_gen.__anext__(), timeout=timeout)
                    first_chunk = False
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError:
                    if first_chunk:
                        logger.warning("YouTube stream URL extraction timeout for %s (120s), closing", video_id)
                    else:
                        logger.warning("YouTube stream chunk read timeout for %s (30s), closing", video_id)
                    break

                if not chunk:
                    continue

                await response.write(chunk)
                bytes_sent += len(chunk)

            logger.info("YouTube stream complete for %s: %s bytes sent", video_id, bytes_sent)

        except (ConnectionResetError, asyncio.CancelledError):
            logger.info("Client disconnected while streaming YouTube video %s", video_id)
            raise

        except Exception as e:
            logger.exception("YouTube streaming failed for %s: %s", video_id, e)
            try:
                await response.write(
                    f"YouTube streaming error: {type(e).__name__}: {e}".encode()
                )
            except Exception:
                pass

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
    uptime_seconds = int(time.time() - APP_START_TIME)
    hours, remainder = divmod(uptime_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    uptime_str = f"{hours}h {minutes}m {seconds}s"
    
    return web.json_response({
        "status": "healthy",
        "service": "filestreambot-pro",
        "clients_connected": len(multi_clients),
        "uptime": uptime_str,
        "uptime_seconds": uptime_seconds
    })


async def root_handler(request):
    """Root endpoint with basic info and uptime."""
    uptime_seconds = int(time.time() - APP_START_TIME)
    hours, remainder = divmod(uptime_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    uptime_str = f"{hours}h {minutes}m {seconds}s"
    
    return web.json_response({
        "service": "filestreambot-pro",
        "status": "running",
        "uptime": uptime_str,
        "endpoints": {
            "health": "/health",
            "ready": "/ready",
            "stream": "/watch/{message_id}/{filename}",
            "download": "/dl/{message_id}/{filename}",
            "youtube_fallback": "?id=youtube_id_or_url"
        }
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
    web.get("/", root_handler),
    web.get("/health", health_check),
    web.get("/ready", readiness_check),
    web.get("/watch/{id}/{file_name}", media_streamer),
    web.get("/dl/{id}/{file_name}", media_streamer),
    # Handle double-slash URLs (e.g., //watch/...)
    web.get("//watch/{id}/{file_name}", media_streamer),
    web.get("//dl/{id}/{file_name}", media_streamer),
    web.options("/watch/{id}/{file_name}", options_handler),
    web.options("/dl/{id}/{file_name}", options_handler),
    web.options("//watch/{id}/{file_name}", options_handler),
    web.options("//dl/{id}/{file_name}", options_handler),
]