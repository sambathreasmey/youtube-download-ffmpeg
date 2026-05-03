from flask import (
    Flask, render_template, request, jsonify,
    send_from_directory, url_for, redirect, session, flash
)
from db import Base, engine, SessionLocal
from utils import can_download_today, get_clean_youtube_url  # we'll adjust usage
from config import Config
from models import DownloadJob, User
import yt_dlp
import pika
import json
import os
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps

app = Flask(__name__)
app.secret_key = "super-secret-change-me"  # 🔴 change in prod
Base.metadata.create_all(bind=engine)


def get_rabbit_channel():
    params = pika.URLParameters(Config.RABBITMQ_URL)
    connection = pika.BlockingConnection(params)
    channel = connection.channel()
    channel.queue_declare(queue="yt_jobs", durable=True)
    return connection, channel


def get_db_user(user_id: int):
    db = SessionLocal()
    user = db.get(User, user_id)
    db.close()
    return user


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return wrapper


@app.route("/", methods=["GET"])
def index():
    # pass user to template to show “Hi …”
    user = None
    if "user_id" in session:
      user = get_db_user(session["user_id"])
    return render_template("index.html", user=user)


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if not email or not password:
            flash("Email and password required", "danger")
            return redirect(url_for("register"))

        db = SessionLocal()
        existing = db.query(User).filter_by(email=email).first()
        if existing:
            db.close()
            flash("Email already registered", "danger")
            return redirect(url_for("register"))

        user = User(
            email=email,
            password_hash=generate_password_hash(password)
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        db.close()

        # log in after register
        session["user_id"] = user.id
        return redirect(url_for("index"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        db = SessionLocal()
        user = db.query(User).filter_by(email=email).first()
        db.close()

        if not user or not user.password_hash or not check_password_hash(user.password_hash, password):
            flash("Invalid email or password", "danger")
            return redirect(url_for("login"))

        session["user_id"] = user.id
        return redirect(url_for("index"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.pop("user_id", None)
    return redirect(url_for("index"))


@app.route("/formats", methods=["POST"])
def formats():
    url = request.form.get("url")
    url = get_clean_youtube_url(url)
    if not url:
        return jsonify({"error": "URL required"}), 400

    ydl_opts = {"skip_download": True, "quiet": True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    formats = []

    for f in info.get("formats", []):
        vcodec = f.get("vcodec")
        acodec = f.get("acodec")

        # 🎥 Video (H.264 + VP9 + AV1)
        if vcodec and vcodec != "none" and vcodec != "none":
            if vcodec.startswith(("avc1", "vp09", "av01")):
                formats.append({
                    "itag": f.get("format_id"),
                    "resolution": f.get("resolution") or f"{f.get('width','')}x{f.get('height','')}",
                    "quality": f.get("format_note"),
                    "codec": vcodec,
                    "size": f.get("filesize") or f.get("filesize_approx"),
                    "fps": f.get("fps"),
                    "best_for": "video"
                })

        # 🔊 AAC audio only
        elif vcodec == "none" and acodec and acodec.startswith("mp4a"):
            formats.append({
                "itag": f.get("format_id"),
                "quality": f.get("format_note"),
                "codec": acodec,
                "size": f.get("filesize") or f.get("filesize_approx"),
                "abr": f.get("abr"),
                "best_for": "audio"
            })

    # ✅ Optional: sort (best quality first)
    def sort_key(x):
        if x["best_for"] == "video":
            res = x.get("resolution") or ""
            try:
                height = int(res.split("x")[-1])
            except:
                height = 0
            return (1, height, x.get("fps") or 0)

        else:
            return (0, x.get("abr") or 0, 0)
    formats.sort(key=sort_key, reverse=True)

    return jsonify({"formats": formats})


@app.route("/download", methods=["POST"])
@login_required
def download():
    url = request.form.get("url")
    quality = request.form.get("quality")   # ✅ NEW
    audio_only = request.form.get("audio_only") == "true"
    user_id = session["user_id"]

    db = SessionLocal()
    user = db.get(User, user_id)

    # quota check
    if not can_download_today(user):
        db.close()
        return jsonify({"error": "Daily limit reached"}), 403

    job = DownloadJob(
        user_id=user.id,
        youtube_url=url,
        itag=None,  # ❌ no more itag usage
        status="queued"
    )

    db.add(job)
    db.commit()
    db.refresh(job)

    conn, ch = get_rabbit_channel()

    payload = {
        "job_id": job.id,
        "url": url,
        "quality": quality,   # ✅ send quality instead
        "audio_only": audio_only,
        "user_id": user.id
    }

    ch.basic_publish(
        exchange="",
        routing_key="yt_jobs",
        body=json.dumps(payload),
        properties=pika.BasicProperties(delivery_mode=2)
    )

    conn.close()
    db.close()

    return jsonify({"job_id": job.id})


@app.route("/progress/<int:job_id>")
@login_required
def progress(job_id):
    db = SessionLocal()
    job = db.get(DownloadJob, job_id)
    if not job or job.user_id != session["user_id"]:
        db.close()
        return jsonify({"error": "not found"}), 404

    download_url = None
    if job.output_file:
        download_url = url_for("files", filename=job.output_file)

    data = {
        "status": job.status,
        "progress": job.progress,
        "output_file": job.output_file,
        "download_url": download_url,
        "error_message": job.error_message,
    }
    db.close()
    return jsonify(data)


@app.route("/files/<path:filename>")
@login_required
def files(filename):
    return send_from_directory(Config.DOWNLOAD_DIR, filename, as_attachment=True)


@app.route("/admin/jobs")
@login_required
def admin_jobs():
    # optionally check is_vip or is_admin here
    db = SessionLocal()
    jobs = db.query(DownloadJob).order_by(DownloadJob.created_at.desc()).limit(100).all()
    out = []
    for j in jobs:
        out.append({
            "id": j.id,
            "user_id": j.user_id,
            "url": j.youtube_url,
            "status": j.status,
            "progress": j.progress,
            "created_at": j.created_at.isoformat()
        })
    db.close()
    return jsonify(out)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
