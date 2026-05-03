import os
import json
import subprocess
from pathlib import Path
import re
import logging
import shutil

import pika
import yt_dlp

from db import SessionLocal
from config import Config
from models import DownloadJob
from utils import get_clean_youtube_url

# =========================
# LOGGING
# =========================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger("worker")

# =========================
# CONFIG
# =========================
DOWNLOAD_DIR = Config.DOWNLOAD_DIR
FFMPEG_CONTAINER = Config.FFMPEG_CONTAINER
COOKIES_PATH = getattr(Config, "COOKIES_PATH", None)

# =========================
# UTIL
# =========================

def quality_to_format(q: str | None) -> str | None:
    if not q:
        return None

    # FIX: Appended [ext=mp4] to force Apple-compatible video tracks
    mapping = {
        "2160p": "bestvideo[height<=2160][ext=mp4]",
        "1440p": "bestvideo[height<=1440][ext=mp4]",
        "1080p": "bestvideo[height<=1080][ext=mp4]",
        "720p": "bestvideo[height<=720][ext=mp4]",
        "480p": "bestvideo[height<=480][ext=mp4]",
        "360p": "bestvideo[height<=360][ext=mp4]",
    }
    return mapping.get(q, None)


def safe_filename(name: str, max_length: int = 150) -> str:
    name = re.sub(r'[\\/*?:"<>|]', "", name)
    return name.strip()[:max_length]


def update_job(job_id: int, **kwargs):
    with SessionLocal() as db:
        job = db.get(DownloadJob, job_id)
        if not job:
            return
        
        for k, v in kwargs.items():
            setattr(job, k, v)

        db.add(job)
        db.commit()


def file_nonempty(path: str) -> bool:
    try:
        return os.path.getsize(path) > 0
    except OSError:
        return False


# =========================
# FFMPEG
# =========================
def run_ffmpeg_merge(video_path: str, audio_path: str, output_path: str):
    subprocess.check_call([
        "docker", "exec", FFMPEG_CONTAINER,
        "ffmpeg", "-y",
        "-i", video_path,
        "-i", audio_path,
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", "copy",
        "-c:a", "aac",
        output_path
    ])


def run_ffmpeg_mp3(input_path: str, output_path: str):
    subprocess.check_call([
        "docker", "exec", FFMPEG_CONTAINER,
        "ffmpeg", "-y",
        "-i", input_path,
        "-vn",
        "-acodec", "libmp3lame",
        "-q:a", "2",
        output_path
    ])


# =========================
# YT-DLP OPTIONS
# =========================
def get_ytdl_opts(outtmpl=None, fmt=None):
    opts = {
        "quiet": True,
        "no_warnings": True,
        "nocheckcertificate": True,
        "socket_timeout": 20,
        "allow_unplayable_formats": True,
        "http_headers": {
            "User-Agent": "Mozilla/5.0 Chrome/120 Safari/537.36"
        }
    }

    if outtmpl:
        opts["outtmpl"] = outtmpl
    if fmt:
        opts["format"] = fmt

    if COOKIES_PATH and os.path.exists(COOKIES_PATH):
        opts["cookiefile"] = COOKIES_PATH

    return opts


# =========================
# DOWNLOAD CORE
# =========================
def download_video_audio(url: str, tmp_dir: str, fmt: str | None):
    video_tmpl = os.path.join(tmp_dir, "video.%(ext)s")
    audio_tmpl = os.path.join(tmp_dir, "audio.%(ext)s")

    def build_format_chain(user_fmt):
        if user_fmt:
            return [
                user_fmt,               
                "bestvideo[ext=mp4]",   # Fallback 1: Best mp4 video
                "bestvideo",            # Fallback 2: Best overall video
                "best"                  # Fallback 3: Legacy format
            ]

        return [
            "bestvideo[ext=mp4]",
            "bestvideo",
            "best"
        ]

    last_error = None

    try:
        with yt_dlp.YoutubeDL(get_ytdl_opts()) as probe:
            probe.extract_info(url, download=False)
    except Exception as e:
        log.warning(f"Probe failed (ignored): {e}")

    # 1. Download ONLY Video
    for f in build_format_chain(fmt):
        try:
            log.info(f"Trying format: {f}")
            with yt_dlp.YoutubeDL(get_ytdl_opts(video_tmpl, f)) as ydl:
                ydl.download([url])
            last_error = None
            break
        except Exception as e:
            log.warning(f"Format failed: {f} -> {e}")
            last_error = e

    if last_error:
        log.warning("All formats failed → fallback BEST")
        with yt_dlp.YoutubeDL(get_ytdl_opts(video_tmpl, "best")) as ydl:
            ydl.download([url])

    # 2. Download ONLY Audio
    log.info("Downloading Audio stream...")
    # FIX: Request M4A audio specifically to ensure perfect MP4 merging for iOS
    with yt_dlp.YoutubeDL(get_ytdl_opts(audio_tmpl, "bestaudio[ext=m4a]/bestaudio")) as ydl:
        ydl.download([url])

    video = list(Path(tmp_dir).glob("video.*"))
    audio = list(Path(tmp_dir).glob("audio.*"))

    if not video:
        raise RuntimeError("Missing video file")
    if not audio:
        raise RuntimeError("Missing audio file")

    return str(video[0]), str(audio[0])


# =========================
# WORKER
# =========================
def process_job(ch, method, properties, body):
    job_id = None
    tmp_dir = None

    try:
        log.info("========== NEW JOB RECEIVED ==========")

        payload = json.loads(body)
        job_id = payload["job_id"]
        url = get_clean_youtube_url(payload["url"])
        quality = payload.get("quality")
        fmt = quality_to_format(quality)
        audio_only = payload.get("audio_only", False)

        log.info(f"Job ID: {job_id}")
        log.info(f"URL: {url}")
        log.info(f"Requested Quality: {quality} | Resolved Format: {fmt}")

        tmp_dir = os.path.join(DOWNLOAD_DIR, f"tmp-{job_id}")
        os.makedirs(tmp_dir, exist_ok=True)

        update_job(job_id, status="downloading", progress=10)

        # Extract metadata
        with yt_dlp.YoutubeDL(get_ytdl_opts()) as ydl:
            info = ydl.extract_info(url, download=False)
            title = safe_filename(info.get("title", f"file-{job_id}"))

        # Audio Only
        if audio_only:
            audio_file = os.path.join(tmp_dir, "audio.m4a")
            with yt_dlp.YoutubeDL(get_ytdl_opts(audio_file, "bestaudio[ext=m4a]/bestaudio")) as ydl:
                ydl.download([url])

            output = os.path.join(DOWNLOAD_DIR, f"{title}.mp3")
            update_job(job_id, status="converting", progress=70)
            run_ffmpeg_mp3(audio_file, output)
            final = f"{title}.mp3"

        # Video + Audio
        else:
            video_file, audio_file = download_video_audio(url, tmp_dir, fmt)

            if not file_nonempty(video_file):
                raise RuntimeError("Video file is empty")
            if not file_nonempty(audio_file):
                raise RuntimeError("Audio file is empty")

            # FIX: Swapped back to .mp4
            output = os.path.join(DOWNLOAD_DIR, f"{title}.mp4")
            
            update_job(job_id, status="merging", progress=80)
            run_ffmpeg_merge(video_file, audio_file, output)
            final = f"{title}.mp4"

        # Mark as done
        update_job(job_id, status="done", progress=100, output_file=final)
        ch.basic_ack(delivery_tag=method.delivery_tag)
        log.info(f"Job {job_id} DONE")

    except Exception as e:
        log.exception(f"Worker error on job {job_id}")
        if job_id:
            update_job(job_id, status="failed", error=str(e))
        
        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

    finally:
        if tmp_dir and os.path.exists(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)


# =========================
# MAIN
# =========================
def main():
    log.info("Worker starting...")

    params = pika.URLParameters(Config.RABBITMQ_URL)
    params.heartbeat = 1800  
    params.blocked_connection_timeout = 1800 

    connection = pika.BlockingConnection(params)
    channel = connection.channel()

    channel.queue_declare(queue="yt_jobs", durable=True)
    channel.basic_qos(prefetch_count=1)

    channel.basic_consume(
        queue="yt_jobs",
        on_message_callback=process_job
    )

    log.info("Waiting for jobs...")
    try:
        channel.start_consuming()
    except KeyboardInterrupt:
        log.info("Worker shutting down...")
        channel.stop_consuming()
    finally:
        connection.close()


if __name__ == "__main__":
    main()