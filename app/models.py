from sqlalchemy import Column, Integer, String, DateTime, Boolean, ForeignKey, func
from sqlalchemy.orm import relationship
from db import Base

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=True)   # 🔴 added
    is_vip = Column(Boolean, default=False)
    trial_start = Column(DateTime, server_default=func.now())

    downloads = relationship("DownloadJob", back_populates="user")


class DownloadJob(Base):
    __tablename__ = "download_jobs"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    youtube_url = Column(String)
    itag = Column(String)
    status = Column(String, default="queued")
    progress = Column(Integer, default=0)
    output_file = Column(String)
    created_at = Column(DateTime, server_default=func.now())
    error_message = Column(String, nullable=True)

    user = relationship("User", back_populates="downloads")
