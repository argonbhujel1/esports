"""Upload: Cloudinary if OK, else local project uploads/ folder"""
import os
import secrets
from pathlib import Path
from werkzeug.utils import secure_filename

ALLOWED_EXT = {"png", "jpg", "jpeg", "webp", "gif", "ico"}

def _project_uploads():
    return Path(__file__).resolve().parent.parent / "uploads"

def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXT

def upload_file(file_storage, folder="esports", local_base=None):
    if not file_storage or not file_storage.filename:
        return None
    if not allowed_file(file_storage.filename):
        raise ValueError("Invalid file type. Use png/jpg/webp/gif/ico")

    cloud_name = os.getenv("CLOUDINARY_CLOUD_NAME", "").strip()
    api_key = os.getenv("CLOUDINARY_API_KEY", "").strip()
    api_secret = os.getenv("CLOUDINARY_API_SECRET", "").strip()

    if cloud_name and api_key and api_secret:
        try:
            import cloudinary
            import cloudinary.uploader
            cloudinary.config(cloud_name=cloud_name, api_key=api_key, api_secret=api_secret, secure=True)
            result = cloudinary.uploader.upload(file_storage, folder=folder, resource_type="image")
            url = result.get("secure_url")
            if url:
                return url
        except Exception:
            pass

    # Local: project/uploads/{sub}/file
    base = Path(local_base) if local_base else _project_uploads()
    sub = folder.replace("esports/", "").replace("esports", "misc") or "misc"
    dest_dir = base / sub
    dest_dir.mkdir(parents=True, exist_ok=True)
    ext = secure_filename(file_storage.filename).rsplit(".", 1)[-1].lower()
    name = f"{secrets.token_hex(8)}.{ext}"
    path = dest_dir / name
    file_storage.seek(0)
    file_storage.save(str(path))
    # Served via /media/<path> on every app
    return f"/media/{sub}/{name}"
