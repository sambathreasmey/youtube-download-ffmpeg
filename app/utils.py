from datetime import datetime, timedelta
from sqlalchemy import select, func
from db import SessionLocal
from config import Config
from models import User, DownloadJob
from urllib.parse import urlparse, parse_qs

def get_or_create_user(email: str):
    db = SessionLocal()
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if not user:
        user = User(email=email)
        db.add(user)
        db.commit()
        db.refresh(user)
    db.close()
    return user

def can_download_today(user: User) -> bool:
    db = SessionLocal()
    # check trial
    now = datetime.utcnow()
    trial_expiry = (user.trial_start or now) + timedelta(days=Config.FREE_TRIAL_DAYS)
    limit = Config.FREE_TRIAL_DAILY_LIMIT
    if user.is_vip or now > trial_expiry:
        limit = Config.VIP_DAILY_LIMIT

    start_of_day = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    count_today = db.execute(
        select(func.count(DownloadJob.id)).where(
            DownloadJob.user_id == user.id,
            DownloadJob.created_at >= start_of_day
        )
    ).scalar_one()
    db.close()
    return count_today < limit

def get_clean_youtube_url(full_url):
    # Parse the URL into components
    parsed_url = urlparse(full_url)
    
    # Parse the query string into a dictionary
    query_params = parse_qs(parsed_url.query)
    
    # Get the 'v' parameter (the unique video ID)
    video_id = query_params.get("v")
    
    if video_id:
        # Reconstruct the URL using the base path and the video ID
        return f"{parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path}?v={video_id[0]}"
    
    # Return the original URL if no 'v' parameter is found
    return full_url