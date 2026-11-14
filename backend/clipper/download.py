from __future__ import annotations

import argparse
import ipaddress
import json
import os
import socket
from pathlib import Path
from urllib.parse import urlparse

from .config import configure_tools
from .domain import URLInput


def public_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
        return False
    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443)
        return bool(addresses) and all(ipaddress.ip_address(address[4][0]).is_global for address in addresses)
    except (OSError, ValueError):
        return False


def acquire(url: str, directory: Path) -> None:
    os.environ["YTDLP_NO_PLUGINS"] = "1"
    from yt_dlp import YoutubeDL
    validated = URLInput(url=url)
    if not public_url(validated.url):
        raise ValueError("The source must resolve to a public internet address.")
    def match(info, *, incomplete):
        if info.get("is_live") or info.get("live_status") in {"is_live", "is_upcoming", "post_live"}:
            return "Only completed recordings are supported."
        if info.get("duration", 0) > 21600:
            return "Recording exceeds six hours."
        return None
    options = {
        "noplaylist": True, "ignoreerrors": False, "quiet": True, "no_warnings": True,
        "cachedir": False, "socket_timeout": 20, "retries": 3, "fragment_retries": 3,
        "outtmpl": str(directory / "source.%(ext)s"), "restrictfilenames": True,
        "format": "bv*[height<=1080]+ba/b[height<=1080]", "merge_output_format": "mkv",
        "max_filesize": 30 * 1024**3, "match_filter": match,
        "js_runtimes": {"node": {}},
    }
    with YoutubeDL(options) as downloader:
        metadata = downloader.extract_info(validated.url, download=False)
        if not metadata or metadata.get("_type") in {"playlist", "multi_video"}:
            raise ValueError("No single completed recording was found.")
        formats = metadata.get("formats", [])
        selected_urls = [item["url"] for item in formats if item.get("url")]
        if not selected_urls or any(not public_url(link) for link in selected_urls):
            raise ValueError("Recording media points to an unsupported network address.")
        result = downloader.download([validated.url])
        if result:
            raise RuntimeError("Download failed. Import an authorized local recording instead.")
    candidates = [path for path in directory.glob("source.*") if path.suffix in {".mkv", ".mp4", ".webm"}]
    if len(candidates) != 1:
        raise RuntimeError("The recording download did not produce one complete media file.")
    (directory / "download.json").write_text(json.dumps({"path": str(candidates[0].resolve())}), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("directory", type=Path)
    arguments = parser.parse_args()
    configure_tools()
    acquire(arguments.url, arguments.directory)


if __name__ == "__main__":
    main()