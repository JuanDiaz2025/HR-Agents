"""Getting a video onto disk and turning it into model inputs.

Two inputs come out of every video: a timestamped transcript (what was said) and
a handful of evenly spaced frames (what was shown). Frames are sampled rather
than streamed — a screening video is a person talking to a camera, so six stills
carry nearly all of the visual signal at a fraction of the token cost.
"""

from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
import urllib.request
from dataclasses import dataclass
from pathlib import Path

DRIVE_ID_PATTERNS = (
    re.compile(r"/file/d/([A-Za-z0-9_-]{20,})"),
    re.compile(r"[?&]id=([A-Za-z0-9_-]{20,})"),
    re.compile(r"/open\?id=([A-Za-z0-9_-]{20,})"),
)


class MediaError(RuntimeError):
    """A video could not be fetched or decoded."""


@dataclass(frozen=True)
class Frame:
    timestamp_seconds: float
    jpeg: bytes

    @property
    def label(self) -> str:
        total = int(self.timestamp_seconds)
        return f"{total // 60:02d}:{total % 60:02d}"

    def as_image_block(self) -> dict:
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": base64.standard_b64encode(self.jpeg).decode("ascii"),
            },
        }


def require_ffmpeg() -> None:
    for binary in ("ffmpeg", "ffprobe"):
        if shutil.which(binary) is None:
            raise MediaError(
                f"`{binary}` not found on PATH. Install ffmpeg — it is required to "
                "sample frames and extract audio."
            )


def drive_file_id(link: str) -> str | None:
    """Extract a Google Drive file id from a share link, if it is one."""
    for pattern in DRIVE_ID_PATTERNS:
        match = pattern.search(link)
        if match:
            return match.group(1)
    return None


def fetch_video(link: str, dest_dir: Path, drive_service=None) -> Path:
    """Resolve `link` to a local file: a path, an HTTP URL, or a Drive link."""
    dest_dir.mkdir(parents=True, exist_ok=True)

    local = Path(link)
    if local.exists():
        return local

    file_id = drive_file_id(link)
    if file_id:
        if drive_service is None:
            raise MediaError(
                f"{link} is a Google Drive link but no Drive client was configured. "
                "Install the `sheets` extra and set GOOGLE_APPLICATION_CREDENTIALS."
            )
        return _download_from_drive(drive_service, file_id, dest_dir)

    if link.startswith(("http://", "https://")):
        dest = dest_dir / (Path(link.split("?")[0]).name or "video.mp4")
        try:
            with urllib.request.urlopen(link, timeout=120) as response, dest.open("wb") as out:
                shutil.copyfileobj(response, out)
        except OSError as exc:
            raise MediaError(f"Could not download {link}: {exc}") from exc
        return dest

    raise MediaError(f"Unrecognized video link: {link!r}")


def _download_from_drive(drive_service, file_id: str, dest_dir: Path) -> Path:
    from googleapiclient.http import MediaIoBaseDownload  # imported lazily

    metadata = drive_service.files().get(fileId=file_id, fields="name").execute()
    dest = dest_dir / f"{file_id}-{metadata.get('name', 'video.mp4')}"
    request = drive_service.files().get_media(fileId=file_id)
    with dest.open("wb") as handle:
        downloader = MediaIoBaseDownload(handle, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
    return dest


def probe_duration(path: Path) -> float:
    require_ffmpeg()
    proc = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "json", str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise MediaError(f"ffprobe failed on {path.name}: {proc.stderr.strip()}")
    try:
        return float(json.loads(proc.stdout)["format"]["duration"])
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        raise MediaError(f"Could not read duration of {path.name}") from exc


def extract_audio(path: Path, dest_dir: Path) -> Path:
    """16 kHz mono WAV — what speech-to-text models expect."""
    require_ffmpeg()
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{path.stem}.wav"
    proc = subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error", "-i", str(path),
            "-vn", "-ac", "1", "-ar", "16000", "-f", "wav", str(dest),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise MediaError(f"Audio extraction failed for {path.name}: {proc.stderr.strip()}")
    return dest


def sample_frames(path: Path, count: int, duration: float | None = None) -> list[Frame]:
    """Grab `count` frames spread evenly across the video, skipping the edges.

    The first and last moments of a self-recorded video are usually the person
    reaching for the record button, so sampling is inset from both ends.
    """
    require_ffmpeg()
    if count <= 0:
        return []
    if duration is None:
        duration = probe_duration(path)
    if duration <= 0:
        raise MediaError(f"{path.name} has no measurable duration.")

    step = duration / (count + 1)
    frames: list[Frame] = []
    for index in range(1, count + 1):
        offset = step * index
        proc = subprocess.run(
            [
                "ffmpeg", "-loglevel", "error", "-ss", f"{offset:.3f}", "-i", str(path),
                "-frames:v", "1", "-q:v", "4", "-f", "image2pipe", "-vcodec", "mjpeg", "-",
            ],
            capture_output=True,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout:
            frames.append(Frame(timestamp_seconds=offset, jpeg=proc.stdout))
    if not frames:
        raise MediaError(f"Could not extract any frames from {path.name}.")
    return frames
