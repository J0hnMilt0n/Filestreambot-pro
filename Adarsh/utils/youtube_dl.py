# (c) adarsh-goel

import asyncio
import logging
import os
import shlex
from typing import Optional, AsyncGenerator

import aiohttp

logger = logging.getLogger(__name__)


class YouTubeStreamer:
    """Stream YouTube videos using yt-dlp."""

    def __init__(self):
        self.yt_dlp_path = "yt-dlp"
        self._ffmpeg_checked = False
        self._has_ffmpeg = False
        # Cookie file for YouTube authentication (optional)
        self.cookie_file = os.getenv("YOUTUBE_COOKIES_FILE", "/tmp/youtube_cookies.txt")
        # Cookie content from env var (alternative to file)
        self.cookie_content = os.getenv("YOUTUBE_COOKIES")
        self._cookie_temp_file = None

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
        return self._has_ffmpeg

    def _get_info_cmd(self):
        """Base command for getting video info (no format restrictions)."""
        cmd = [
            self.yt_dlp_path,
            "--no-check-certificates",
            "--no-warnings",
            "--ignore-errors",
            "--extractor-retries", "3",
            "--socket-timeout", "30",
            # Use default player client for info (more compatible)
            "--age-limit", "99",
            # NOTE: Removed --extractor-args youtube:skip=hls,dash
            # HLS/DASH are often the ONLY available formats.
            "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "--referer", "https://www.youtube.com/",
            "--add-header", "Accept-Language:en-US,en;q=0.9",
        ]
        
        # Add cookies from env var or file
        cookie_path = self._prepare_cookies()
        if cookie_path:
            cmd.extend(["--cookies", cookie_path])
        
        return cmd

    async def get_video_info(self, url: str) -> dict:
        """Get video metadata without downloading. Tries multiple player clients + Invidious fallback."""
        # Try multiple player clients in order of preference
        player_clients = [
            "android_embedded",
            "tv_embedded", 
            "web_embedded",
            "web",
            "default",  # No extractor args
        ]
        
        last_error = None
        
        for player_client in player_clients:
            cmd = self._get_info_cmd()
            
            # Set player client
            if player_client == "default":
                # Remove extractor-args
                try:
                    idx = cmd.index("--extractor-args")
                    cmd.pop(idx)
                    cmd.pop(idx)
                except ValueError:
                    pass
            else:
                # Replace or add extractor-args
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
                stdout, stderr = await proc.communicate()
                
                if proc.returncode == 0:
                    import json
                    info = json.loads(stdout.decode())
                    logger.info("Got video info with player_client: %s", player_client)
                    self._cleanup_cookies()
                    return info
                
                last_error = stderr.decode()
                logger.warning("Video info failed (client: %s): %s", player_client, last_error[:200])
                
            except Exception as e:
                last_error = str(e)
                logger.warning("Video info exception (client: %s): %s", player_client, e)
        
        # Last resort: try without any special args
        logger.info("All player clients failed for video info, trying basic command...")
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
            stdout, stderr = await proc.communicate()
            
            if proc.returncode == 0:
                import json
                info = json.loads(stdout.decode())
                logger.info("Got video info with basic command")
                self._cleanup_cookies()
                return info
            
            last_error = stderr.decode()
        except Exception as e:
            last_error = str(e)
        
        # Fallback: Try Invidious instances (bypasses YouTube IP blocking)
        logger.info("Trying Invidious fallback for video info...")
        invidious_instances = [
            "https://yewtu.be",
            "https://invidious.snopyta.org",
            "https://invidious.nerdvpn.de",
            "https://yewtu.be",
        ]
        
        video_id = url.split("v=")[-1].split("&")[0]
        for instance in invidious_instances:
            try:
                invidious_url = f"{instance}/api/v1/videos/{video_id}"
                import aiohttp
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
                            self._cleanup_cookies()
                            return info
            except Exception as e:
                logger.warning("Invidious %s failed: %s", instance, e)
                continue
        
        self._cleanup_cookies()
        logger.error("All video info attempts failed: %s", last_error)
        # Return minimal info so streaming can still attempt
        return {"id": video_id, "title": "YouTube Video"}

    async def get_stream_url(self, url: str, quality: str = "best[ext=mp4]/best") -> str:
        """Get direct stream URL for a YouTube video with fallback formats + Invidious."""
        # Try multiple player clients in order of preference
        player_clients = [
            "android_embedded",
            "tv_embedded", 
            "web_embedded",
            "web",
            "default",  # No extractor args
        ]
        
        # Format selectors to try
        formats = [
            quality,
            "best[ext=mp4]/best",
            "best[protocol^=http]/best",
            "worst[ext=mp4]/worst",
            "best",
        ]
        
        last_error = None
        
        for player_client in player_clients:
            for fmt in formats:
                cmd = self._get_base_cmd()
                
                # Set player client
                if player_client == "default":
                    # Remove extractor-args
                    try:
                        idx = cmd.index("--extractor-args")
                        cmd.pop(idx)
                        cmd.pop(idx)
                    except ValueError:
                        pass
                else:
                    # Replace or add extractor-args
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
                    stdout, stderr = await proc.communicate()
                    
                    if proc.returncode == 0:
                        urls = stdout.decode().strip().split('\n')
                        if urls and urls[0]:
                            logger.info("Got stream URL with format: %s (player_client: %s)", fmt, player_client)
                            self._cleanup_cookies()
                            return urls[0]
                    
                    last_error = stderr.decode()
                    logger.warning("Format %s (client: %s) failed: %s", fmt, player_client, last_error[:200])
                    
                except Exception as e:
                    last_error = str(e)
                    logger.warning("Format %s (client: %s) exception: %s", fmt, player_client, e)
        
        # Last resort: try to list formats and pick any available
        logger.info("All format attempts failed, trying to list available formats...")
        try:
            list_cmd = self._get_base_cmd() + ["--list-formats", url]
            proc = await asyncio.create_subprocess_exec(
                *list_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            logger.info("Available formats for %s:\n%s", url, stdout.decode()[:2000])
        except Exception as e:
            logger.warning("Failed to list formats: %s", e)
        
        # Fallback: Try Invidious instances for direct stream URL
        logger.info("Trying Invidious fallback for stream URL...")
        video_id = url.split("v=")[-1].split("&")[0]
        invidious_instances = [
            "https://yewtu.be",
            "https://invidious.snopyta.org",
            "https://invidious.nerdvpn.de",
        ]
        
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
            
            timeout = aiohttp.ClientTimeout(total=300, connect=30)
            
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
                            async for chunk in resp2.content.iter_chunked(chunk_size):
                                if not chunk:
                                    continue
                                yield chunk
                            return
                    elif resp.status not in (200, 206):
                        # Log response headers for debugging
                        logger.error("Stream request failed: %s, headers: %s", resp.status, dict(resp.headers))
                        raise Exception(f"Stream request failed: {resp.status}")
                    
                    async for chunk in resp.content.iter_chunked(chunk_size):
                        if not chunk:
                            continue
                        yield chunk
        finally:
            self._cleanup_cookies()


async def get_youtube_streamer() -> YouTubeStreamer:
    """Get or create YouTube streamer instance."""
    return YouTubeStreamer()