# (c) adarsh-goel

import asyncio
import logging
import os
import shlex
from typing import Optional, AsyncGenerator, Dict, Any

import aiohttp

logger = logging.getLogger(__name__)


class YouTubeStreamer:
    """Stream YouTube videos using yt-dlp with Invidious fallback."""

    def __init__(self):
        self.yt_dlp_path = "yt-dlp"
        self._ffmpeg_checked = False
        self._has_ffmpeg = False
        # Cookie file for YouTube authentication (optional)
        self.cookie_file = os.getenv("YOUTUBE_COOKIES_FILE", "/tmp/youtube_cookies.txt")
        # Cookie content from env var (alternative to file)
        self.cookie_content = os.getenv("YOUTUBE_COOKIES")
        self._cookie_temp_file = None
        # Cache for video info to avoid duplicate calls
        self._info_cache: Dict[str, Dict[str, Any]] = {}

    def _prepare_cookies(self) -> Optional[str]:
        """Prepare cookie file from env var or existing file. Returns path or None."""
        # Priority 1: Cookie content from env var (write to temp file)
        if self.cookie_content:
            import tempfile
            self._cookie_temp_file = tempfile.NamedTemporaryFile(
                mode='w', suffix='.txt', delete=False, prefix='yt_cookies_'
            )
            self._cookie_temp_file.write(self.cookie_content)
            self._cookie_temp_file.close()
            logger.info("Using YouTube cookies from YOUTUBE_COOKIES env var")
            return self._cookie_temp_file.name
        
        # Priority 2: Cookie file from env var or default
        if os.path.exists(self.cookie_file):
            logger.info("Using YouTube cookies from %s", self.cookie_file)
            return self.cookie_file
        
        return None

    def _cleanup_cookies(self):
        """Clean up temporary cookie file."""
        if self._cookie_temp_file and os.path.exists(self._cookie_temp_file.name):
            try:
                os.unlink(self._cookie_temp_file.name)
            except Exception:
                pass
            self._cookie_temp_file = None

    def _get_base_cmd(self):
        """Base yt-dlp command with anti-bot options."""
        cmd = [
            self.yt_dlp_path,
            "--no-check-certificates",
            "--no-warnings",
            "--ignore-errors",
            "--extractor-retries", "3",
            "--socket-timeout", "30",
            "--extractor-args", "youtube:player_client=android_embedded",
            # Age-gate bypass
            "--age-limit", "99",
            # NOTE: Removed --extractor-args youtube:skip=hls,dash
            # HLS/DASH are often the ONLY available formats. Skipping them causes
            # "Requested format is not available" for many videos.
            "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "--referer", "https://www.youtube.com/",
            "--add-header", "Accept-Language:en-US,en;q=0.9",
        ]
        
        # Add cookies from env var or file
        cookie_path = self._prepare_cookies()
        if cookie_path:
            cmd.extend(["--cookies", cookie_path])
        
        return cmd

    async def _check_ffmpeg(self) -> bool:
        """Check if ffmpeg is available."""
        if self._ffmpeg_checked:
            return self._has_ffmpeg
        
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await proc.communicate()
            self._has_ffmpeg = proc.returncode == 0
        except Exception:
            self._has_ffmpeg = False
        
        self._ffmpeg_checked = True
        logger.info("ffmpeg available: %s", self._has_ffmpeg)

    def _extract_video_id(self, url: str) -> str:
        """Extract video ID from YouTube URL."""
        if "v=" in url:
            return url.split("v=")[-1].split("&")[0]
        elif "youtu.be/" in url:
            return url.split("youtu.be/")[-1].split("?")[0]
        return url

    async def _get_invidious_info(self, video_id: str) -> Optional[Dict[str, Any]]:
        """Get video info from Invidious instances (fast, no yt-dlp)."""
        invidious_instances = [
            "https://yewtu.be",
            "https://invidious.snopyta.org",
            "https://invidious.nerdvpn.de",
        ]
        
        for instance in invidious_instances:
            try:
                invidious_url = f"{instance}/api/v1/videos/{video_id}"
                async with aiohttp.ClientSession() as session:
                    async with session.get(invidious_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        if resp.status == 200:
                            import json
                            data = await resp.json()
                            # Convert Invidious format to yt-dlp-like format
                            info = {
                                "id": video_id,
                                "title": data.get("title", "YouTube Video"),
                                "duration": data.get("length_seconds", 0),
                                "formats": [],
                            }
                            # Add format info from Invidious
                            for fmt in data.get("adaptiveFormats", []):
                                info["formats"].append({
                                    "url": fmt.get("url"),
                                    "ext": fmt.get("mimeType", "").split("/")[-1].split(";")[0],
                                    "filesize": fmt.get("contentLength"),
                                    "format_id": fmt.get("itag"),
                                    "height": fmt.get("height"),
                                    "width": fmt.get("width"),
                                    "fps": fmt.get("fps"),
                                    "vcodec": fmt.get("mimeType", "").split("/")[0] if "video" in fmt.get("mimeType", "") else "none",
                                    "acodec": fmt.get("mimeType", "").split("/")[0] if "audio" in fmt.get("mimeType", "") else "none",
                                })
                            logger.info("Got video info from Invidious: %s", instance)
                            return info
            except Exception as e:
                logger.warning("Invidious %s failed: %s", instance, e)
                continue
        return None

    async def _get_invidious_stream_url(self, video_id: str) -> Optional[str]:
        """Get direct stream URL from Invidious (fast, no yt-dlp)."""
        invidious_instances = [
            "https://yewtu.be",
            "https://invidious.snopyta.org",
            "https://invidious.nerdvpn.de",
        ]
        
        for instance in invidious_instances:
            try:
                invidious_url = f"{instance}/api/v1/videos/{video_id}"
                async with aiohttp.ClientSession() as session:
                    async with session.get(invidious_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        if resp.status == 200:
                            import json
                            data = await resp.json()
                            # Find best mp4 format
                            best_url = None
                            for fmt in data.get("adaptiveFormats", []):
                                if fmt.get("mimeType", "").startswith("video/") and fmt.get("url"):
                                    best_url = fmt["url"]
                                    break
                            if not best_url:
                                for fmt in data.get("adaptiveFormats", []):
                                    if fmt.get("url"):
                                        best_url = fmt["url"]
                                        break
                            if best_url:
                                logger.info("Got stream URL from Invidious: %s", instance)
                                return best_url
            except Exception as e:
                logger.warning("Invidious stream %s failed: %s", instance, e)
                continue
        return None

    async def get_video_info(self, url: str) -> dict:
        """Get video metadata. Try Invidious FIRST (fast), then yt-dlp as fallback."""
        video_id = self._extract_video_id(url)
        
        # Check cache first
        if video_id in self._info_cache:
            logger.info("Using cached video info for %s", video_id)
            return self._info_cache[video_id]
        
        # Try Invidious FIRST (fast, no yt-dlp subprocess)
        logger.info("Trying Invidious for video info: %s", video_id)
        info = await self._get_invidious_info(video_id)
        if info and info.get("formats"):
            self._info_cache[video_id] = info
            self._cleanup_cookies()
            return info
        
        # Fallback to yt-dlp with limited attempts
        logger.info("Invidious failed, trying yt-dlp for video info...")
        player_clients = ["android_embedded", "tv_embedded", "web_embedded", "web", "default"]
        
        for player_client in player_clients:
            cmd = self._get_base_cmd()
            
            if player_client == "default":
                try:
                    idx = cmd.index("--extractor-args")
                    cmd.pop(idx)
                    cmd.pop(idx)
                except ValueError:
                    pass
            else:
                try:
                    idx = cmd.index("--extractor-args")
                    cmd[idx+1] = f"youtube:player_client={player_client}"
                except ValueError:
                    cmd.extend(["--extractor-args", f"youtube:player_client={player_client}"])
            
            cmd.extend(["--dump-json", "--no-download", "--skip-download", url])
            
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                try:
                    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30.0)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.communicate()
                    logger.warning("Video info timeout (client: %s)", player_client)
                    continue
                
                if proc.returncode == 0:
                    stdout_str = stdout.decode().strip()
                    if stdout_str:
                        import json
                        info = json.loads(stdout_str)
                        logger.info("Got video info with player_client: %s", player_client)
                        self._info_cache[video_id] = info
                        self._cleanup_cookies()
                        return info
                
                logger.warning("Video info failed (client: %s): %s", player_client, stderr.decode()[:200])
                
            except Exception as e:
                logger.warning("Video info exception (client: %s): %s", player_client, e)
        
        # Last resort: basic command
        logger.info("All player clients failed, trying basic command...")
        try:
            basic_cmd = [
                self.yt_dlp_path,
                "--no-check-certificates",
                "--no-warnings",
                "--ignore-errors",
                "--dump-json",
                "--no-download",
                "--skip-download",
                url
            ]
            cookie_path = self._prepare_cookies()
            if cookie_path:
                basic_cmd.extend(["--cookies", cookie_path])
            
            proc = await asyncio.create_subprocess_exec(
                *basic_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                logger.warning("Basic video info timeout")
            else:
                if proc.returncode == 0:
                    stdout_str = stdout.decode().strip()
                    if stdout_str:
                        import json
                        info = json.loads(stdout_str)
                        logger.info("Got video info with basic command")
                        self._info_cache[video_id] = info
                        self._cleanup_cookies()
                        return info
        except Exception as e:
            logger.warning("Basic video info exception: %s", e)
        
        self._cleanup_cookies()
        logger.error("All video info attempts failed for %s", video_id)
        # Return minimal info so streaming can still attempt
        minimal_info = {"id": video_id, "title": "YouTube Video", "formats": []}
        self._info_cache[video_id] = minimal_info
        return minimal_info

    async def get_stream_url(self, url: str, quality: str = "best[ext=mp4]/best") -> str:
        """Get direct stream URL. Try Invidious FIRST (fast), then yt-dlp as fallback."""
        video_id = self._extract_video_id(url)
        
        # Try Invidious FIRST (fast, no yt-dlp subprocess)
        logger.info("Trying Invidious for stream URL: %s", video_id)
        stream_url = await self._get_invidious_stream_url(video_id)
        if stream_url:
            self._cleanup_cookies()
            return stream_url
        
        # Fallback to yt-dlp with limited attempts
        logger.info("Invidious failed, trying yt-dlp for stream URL...")
        player_clients = ["android_embedded", "default"]
        formats = [quality, "best[ext=mp4]/best", "best[protocol^=http]/best", "best"]
        
        for player_client in player_clients:
            for fmt in formats:
                cmd = self._get_base_cmd()
                
                if player_client == "default":
                    try:
                        idx = cmd.index("--extractor-args")
                        cmd.pop(idx)
                        cmd.pop(idx)
                    except ValueError:
                        pass
                else:
                    try:
                        idx = cmd.index("--extractor-args")
                        cmd[idx+1] = f"youtube:player_client={player_client}"
                    except ValueError:
                        cmd.extend(["--extractor-args", f"youtube:player_client={player_client}"])
                
                cmd.extend(["-g", "-f", fmt, url])
                
                try:
                    proc = await asyncio.create_subprocess_exec(
                        *cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE
                    )
                    try:
                        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30.0)
                    except asyncio.TimeoutError:
                        proc.kill()
                        await proc.communicate()
                        logger.warning("Format %s (client: %s) timeout", fmt, player_client)
                        continue
                    
                    if proc.returncode == 0:
                        urls = stdout.decode().strip().split('\n')
                        if urls and urls[0]:
                            logger.info("Got stream URL with format: %s (player_client: %s)", fmt, player_client)
                            self._cleanup_cookies()
                            return urls[0]
                    
                    logger.warning("Format %s (client: %s) failed: %s", fmt, player_client, stderr.decode()[:200])
                    
                except Exception as e:
                    logger.warning("Format %s (client: %s) exception: %s", fmt, player_client, e)
        
        self._cleanup_cookies()
        raise Exception(f"All stream URL attempts failed for {video_id}")
        
        for instance in invidious_instances:
            try:
                invidious_url = f"{instance}/api/v1/videos/{video_id}"
                import aiohttp
                async with aiohttp.ClientSession() as session:
                    async with session.get(invidious_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        if resp.status == 200:
                            import json
                            data = await resp.json()
                            # Find best mp4 format
                            best_url = None
                            for fmt in data.get("adaptiveFormats", []):
                                if fmt.get("mimeType", "").startswith("video/") and fmt.get("url"):
                                    best_url = fmt["url"]
                                    break
                            if not best_url:
                                for fmt in data.get("adaptiveFormats", []):
                                    if fmt.get("url"):
                                        best_url = fmt["url"]
                                        break
                            if best_url:
                                logger.info("Got stream URL from Invidious: %s", instance)
                                self._cleanup_cookies()
                                return best_url
            except Exception as e:
                logger.warning("Invidious stream %s failed: %s", instance, e)
                continue
        
        self._cleanup_cookies()
        raise Exception(f"All format attempts failed. Last error: {last_error}")

    async def stream_video(
        self,
        url: str,
        start: int = 0,
        end: Optional[int] = None,
        chunk_size: int = 512 * 1024,
        quality: str = "best[ext=mp4]/best"
    ) -> AsyncGenerator[bytes, None]:
        """Stream video content by proxying direct URL from yt-dlp."""
        
        try:
            # Get direct stream URL
            stream_url = await self.get_stream_url(url, quality)
            if not stream_url:
                raise Exception("No stream URL found")
            
            logger.info("Streaming YouTube video via direct URL: %s...", stream_url[:50])
            
            # Use aiohttp to proxy the stream with same headers/cookies as yt-dlp
            import aiohttp
            
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Referer": "https://www.youtube.com/",
                "Accept-Language": "en-US,en;q=0.9",
                "Origin": "https://www.youtube.com",
            }
            if start or end:
                range_val = f"bytes={start}-"
                if end:
                    range_val += str(end)
                headers["Range"] = range_val
            
            # Prepare cookies for the streaming request
            cookie_path = self._prepare_cookies()
            cookie_jar = None
            if cookie_path:
                import http.cookiejar
                cookie_jar = http.cookiejar.MozillaCookieJar(cookie_path)
                cookie_jar.load(ignore_discard=True, ignore_expires=True)
            
            timeout = aiohttp.ClientTimeout(total=300, connect=30, sock_read=30)
            
            connector = aiohttp.TCPConnector(limit=10)
            async with aiohttp.ClientSession(
                timeout=timeout,
                connector=connector,
                cookie_jar=cookie_jar
            ) as session:
                async with session.get(stream_url, headers=headers) as resp:
                    if resp.status == 403:
                        # Try to get a fresh URL (URLs expire)
                        logger.warning("Got 403, stream URL may have expired, retrying...")
                        self._cleanup_cookies()
                        # Recursive retry once with fresh URL
                        stream_url = await self.get_stream_url(url, quality)
                        if not stream_url:
                            raise Exception("No stream URL found on retry")
                        logger.info("Retrying with fresh URL: %s...", stream_url[:50])
                        async with session.get(stream_url, headers=headers) as resp2:
                            if resp2.status not in (200, 206):
                                logger.error("Retry failed: %s", resp2.status)
                                raise Exception(f"Stream request failed: {resp2.status}")
                            # Add timeout for chunk reading
                            async for chunk in self._iter_chunks_with_timeout(resp2.content, chunk_size, 30.0):
                                if not chunk:
                                    continue
                                yield chunk
                            return
                    elif resp.status not in (200, 206):
                        # Log response headers for debugging
                        logger.error("Stream request failed: %s, headers: %s", resp.status, dict(resp.headers))
                        raise Exception(f"Stream request failed: {resp.status}")
                    
                    # Add timeout for chunk reading
                    async for chunk in self._iter_chunks_with_timeout(resp.content, chunk_size, 30.0):
                        if not chunk:
                            continue
                        yield chunk
        finally:
            self._cleanup_cookies()

    async def _iter_chunks_with_timeout(self, content, chunk_size: int, timeout: float):
        """Iterate chunks with per-chunk timeout to prevent infinite hanging."""
        while True:
            try:
                chunk = await asyncio.wait_for(content.read(chunk_size), timeout=timeout)
            except asyncio.TimeoutError:
                logger.warning("Chunk read timeout, stopping stream")
                break
            if not chunk:
                break
            yield chunk


async def get_youtube_streamer() -> YouTubeStreamer:
    """Get or create YouTube streamer instance."""
    return YouTubeStreamer()