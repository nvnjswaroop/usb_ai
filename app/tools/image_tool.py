"""
Image Tool - handles image input for vision models
Supports: LLaVA, Gemma 3/4, any multimodal GGUF with mmproj
"""
import base64
import struct
import time
from pathlib import Path
from typing import Optional


SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}


def image_to_base64(path: str) -> str:
    """Convert image file to base64 data URL."""
    p = Path(path)
    ext = p.suffix.lower()
    mime = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".gif": "image/gif",
        ".webp": "image/webp", ".bmp": "image/bmp",
    }.get(ext, "image/jpeg")
    data = p.read_bytes()
    b64 = base64.b64encode(data).decode("utf-8")
    return f"data:{mime};base64,{b64}"


class ImageTool:
    def __init__(self, output_dir: Path):
        self.output_dir = output_dir

    def save_upload(self, data: bytes, filename: str) -> dict:
        """Save uploaded image to output dir."""
        p = Path(filename)
        if p.suffix.lower() not in SUPPORTED_EXTENSIONS:
            return {"status": "error", "message": f"Unsupported format: {p.suffix}"}
        dest = self.output_dir / f"img_{int(time.time())}{p.suffix.lower()}"
        dest.write_bytes(data)
        return {
            "status":   "ok",
            "path":     str(dest),
            "filename": dest.name,
            "size_kb":  round(len(data) / 1024, 1),
        }

    def read_for_llm(self, path: str) -> dict:
        """Read image and return base64 ready for LLM."""
        p = Path(path)
        if not p.exists():
            return {"status": "error", "message": f"Image not found: {path}"}
        if p.suffix.lower() not in SUPPORTED_EXTENSIONS:
            return {"status": "error", "message": f"Unsupported format: {p.suffix}"}
        size = p.stat().st_size
        if size > 10 * 1024 * 1024:  # 10MB limit
            return {"status": "error", "message": "Image too large (max 10MB)"}
        try:
            b64_url = image_to_base64(str(p))
            return {
                "status":   "ok",
                "path":     str(p),
                "filename": p.name,
                "size_kb":  round(size / 1024, 1),
                "base64_url": b64_url,
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}
