import os
import uuid
import threading
import shutil
import re
import time
from flask import Flask, render_template, request, jsonify, send_file
from yt_dlp import YoutubeDL

app = Flask(__name__)

# ======================================
# CONFIG
# ======================================

DOWNLOAD_FOLDER = os.path.join(os.getcwd(), "downloads")
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

FILE_EXPIRY_SECONDS = 600
MAX_CONCURRENT_PER_IP = 1

download_state = {}
active_ip_downloads = {}

# ======================================
# CHECK FFMPEG
# ======================================

if not shutil.which("ffmpeg"):
    print("WARNING: FFmpeg not found")

# ======================================
# SAFE FILENAME
# ======================================

def sanitize_filename(name):
    name = re.sub(r'[<>:"/\\|?*]', '', name)
    name = name.encode("ascii", "ignore").decode()
    name = re.sub(r"\s+", "_", name)
    name = re.sub(r"_+", "_", name)
    return name[:40]

# ======================================
# URL VALIDATION
# ======================================

def valid_url(url):
    return isinstance(url, str) and url.startswith(("http://", "https://"))

# ======================================
# BASE YTDLP OPTIONS
# ======================================

def base_ydl_opts():

    return {

        "quiet": True,
        "nocheckcertificate": True,
        "ignoreerrors": True,
        "noplaylist": True,

        "retries": 5,
        "fragment_retries": 5,
        "socket_timeout": 30,

        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9"
        },

        "extractor_args": {
            "youtube": {
                "player_client": ["android", "web"]
            }
        }
    }

# ======================================
# FORMAT EXTRACTION
# ======================================

def get_available_formats(url):

    opts = base_ydl_opts()
    opts["skip_download"] = True

    try:

        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)

    except Exception as e:

        print("Extraction error:", e)
        return [{"height": "Best", "size": "Auto"}]

    if not info:
        return [{"height": "Best", "size": "Auto"}]

    if info.get("_type") == "playlist" and info.get("entries"):
        info = info["entries"][0]

    formats = []
    seen = set()

    for f in info.get("formats", []):

        height = f.get("height")

        if height and height not in seen:

            seen.add(height)

            size = f.get("filesize") or f.get("filesize_approx")

            if size:
                size_text = f"{round(size/1024/1024,2)} MB"
            else:
                size_text = "Unknown"

            formats.append({
                "height": height,
                "size": size_text
            })

    formats = sorted(formats, key=lambda x: x["height"])

    if not formats:
        formats.append({"height": "Best", "size": "Auto"})

    return formats

# ======================================
# ROUTE: GET FORMATS
# ======================================

@app.route("/get_formats", methods=["POST"])
def formats():

    data = request.get_json() or {}
    url = data.get("url")

    if not valid_url(url):
        return jsonify({"error": "Invalid URL"})

    try:

        formats = get_available_formats(url)

        return jsonify({
            "formats": formats,
            "single_option": len(formats) <= 1
        })

    except Exception as e:

        print("Format error:", e)
        return jsonify({"error": "Could not fetch formats"})

# ======================================
# DOWNLOAD WORKER
# ======================================

def download_worker(download_id, url, height, user_ip):

    def progress_hook(d):

        if d["status"] == "downloading":

            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            downloaded = d.get("downloaded_bytes", 0)

            if total and download_id in download_state:

                percent = int(downloaded * 90 / total)

                download_state[download_id]["progress"] = percent
                download_state[download_id]["status"] = "downloading"

    try:

        download_state[download_id]["status"] = "processing"

        opts = base_ydl_opts()
        opts["skip_download"] = True

        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)

        if not info:
            download_state[download_id]["status"] = "error"
            return

        if info.get("_type") == "playlist" and info.get("entries"):
            info = info["entries"][0]

        raw_title = info.get("title", "video")
        safe_title = sanitize_filename(raw_title)

        final_filename = f"{safe_title}_{download_id}.mp4"
        final_path = os.path.join(DOWNLOAD_FOLDER, final_filename)

        if height and height != "Best":

            format_string = (
                f"bestvideo[height<={height}]+bestaudio/"
                f"best[height<={height}]/best"
            )

        else:

            format_string = "bestvideo+bestaudio/best"

        ydl_opts = base_ydl_opts()

        ydl_opts.update({

            "format": format_string,
            "outtmpl": final_path,
            "merge_output_format": "mp4",
            "progress_hooks": [progress_hook]

        })

        with YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        if os.path.exists(final_path):

            download_state[download_id]["progress"] = 100
            download_state[download_id]["status"] = "ready"
            download_state[download_id]["filename"] = final_filename

        else:

            download_state[download_id]["status"] = "error"

    except Exception as e:

        print("Download error:", e)

        if download_id in download_state:
            download_state[download_id]["status"] = "error"

    finally:

        if user_ip in active_ip_downloads:

            active_ip_downloads[user_ip] -= 1

            if active_ip_downloads[user_ip] <= 0:
                active_ip_downloads.pop(user_ip)

# ======================================
# START DOWNLOAD
# ======================================

@app.route("/download", methods=["POST"])
def download():

    user_ip = request.remote_addr
    current = active_ip_downloads.get(user_ip, 0)

    if current >= MAX_CONCURRENT_PER_IP:
        return jsonify({"error": "Please wait until your current download finishes."})

    data = request.get_json() or {}

    url = data.get("url")
    height = data.get("height")

    if not valid_url(url):
        return jsonify({"error": "Invalid URL"})

    active_ip_downloads[user_ip] = current + 1

    download_id = uuid.uuid4().hex[:8]

    download_state[download_id] = {
        "progress": 0,
        "status": "starting",
        "filename": None,
        "created_at": time.time()
    }

    thread = threading.Thread(
        target=download_worker,
        args=(download_id, url, height, user_ip),
        daemon=True
    )

    thread.start()

    return jsonify({"download_id": download_id})

# ======================================
# PROGRESS
# ======================================

@app.route("/progress/<download_id>")
def progress(download_id):

    data = download_state.get(download_id)

    if not data:
        return jsonify({"progress": 0, "status": "invalid"})

    return jsonify({
        "progress": data.get("progress", 0),
        "status": data.get("status", "unknown")
    })

# ======================================
# DOWNLOAD FILE
# ======================================

@app.route("/download_file/<download_id>")
def download_file(download_id):

    data = download_state.get(download_id)

    if not data:
        return "Invalid download ID", 404

    if data.get("status") != "ready":
        return "File not ready yet.", 404

    filename = data.get("filename")
    file_path = os.path.join(DOWNLOAD_FOLDER, filename)

    if os.path.exists(file_path):
        return send_file(file_path, as_attachment=True)

    return "File missing", 404

# ======================================
# CLEANUP
# ======================================

def cleanup_worker():

    while True:

        now = time.time()

        for download_id in list(download_state.keys()):

            data = download_state.get(download_id)

            if not data:
                continue

            if now - data.get("created_at", now) > FILE_EXPIRY_SECONDS:

                filename = data.get("filename")

                if filename:

                    file_path = os.path.join(DOWNLOAD_FOLDER, filename)

                    if os.path.exists(file_path):

                        try:
                            os.remove(file_path)
                        except:
                            pass

                download_state.pop(download_id, None)

        time.sleep(60)

# ======================================
# PAGES
# ======================================

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/privacy")
def privacy():
    return render_template("privacy.html")

@app.route("/terms")
def terms():
    return render_template("terms.html")

@app.route("/contact")
def contact():
    return render_template("contact.html")

@app.route("/about")
def about():
    return render_template("about.html")

# ======================================
# RUN
# ======================================

if __name__ == "__main__":

    cleanup_thread = threading.Thread(target=cleanup_worker, daemon=True)
    cleanup_thread.start()

    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

