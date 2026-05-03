import os
from datetime import timedelta

class Config:
    DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg2://ytuser:ytpass@localhost:5432/ytdb")
    RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
    DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", "/downloads")
    FFMPEG_CONTAINER = os.getenv("FFMPEG_CONTAINER", "ffmpeg-helper")

    FREE_TRIAL_DAYS = 7
    FREE_TRIAL_DAILY_LIMIT = 10
    VIP_DAILY_LIMIT = 250
