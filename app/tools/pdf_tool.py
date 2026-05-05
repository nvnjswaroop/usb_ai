"""
PDF Tool - text extraction with OCR fallback for scanned PDFs
Priority: pymupdf > pypdf > PyPDF2
OCR: pytesseract (if installed) for scanned pages
"""
import sys
from pathlib import Path


class PDFTool:
    def extract_text(self, path: str) -> dict:
        p = Path(path.strip())

        # Resolve relative paths
        if not p.is_absolute():
            for base in [Path.home(), Path.home() / "Documents",
                         Path.home() / "Desktop", Path("C:/")]:
                c = base / p
                if c.exists():
                    p = c
                    break

        if not p.exists():
            return {"status": "error",
                    "message": f"File not found: {path}\n\nUse full path e.g. C:\\Users\\JYOTHI\\Documents\\file.pdf"}
        if not p.is_file():
            return {"status": "error", "message": f"Not a file: {path}"}
        if p.suffix.lower() != ".pdf":
            return {"status": "error", "message": f"Expected a .pdf file, got {p.suffix}"}

        # Try pymupdf first (best quality)
        result = self._try_pymupdf(p)
        if result:
            return result

        # Try pypdf / PyPDF2
        result = self._try_pypdf(p)
        if result:
            return result

        return {"status": "error",
                "message": "Could not read PDF. Install pymupdf: pip install pymupdf"}

    def _try_pymupdf(self, p: Path) -> dict | None:
        try:
            import fitz  # pymupdf
        except ImportError:
            return None

        try:
            doc = fitz.open(str(p))
            num_pages = len(doc)
            text_pages = []
            scanned_pages = 0

            for i, page in enumerate(doc):
                text = page.get_text().strip()
                if text:
                    text_pages.append(f"--- Page {i+1} ---\n{text}")
                else:
                    # Scanned page — try OCR if pytesseract available
                    ocr_text = self._ocr_page_fitz(page, i+1)
                    if ocr_text:
                        text_pages.append(f"--- Page {i+1} (OCR) ---\n{ocr_text}")
                    else:
                        scanned_pages += 1
                        text_pages.append(f"--- Page {i+1} --- [scanned image — no text extractable]")

            doc.close()
            full_text = "\n\n".join(text_pages)
            truncated = False
            if len(full_text) > 14000:
                full_text = full_text[:14000]
                truncated = True

            note = ""
            if scanned_pages > 0:
                note = f"\n\n⚠️ {scanned_pages} page(s) are scanned images. Install pytesseract + tesseract for OCR."

            return {
                "status":    "ok",
                "path":      str(p),
                "filename":  p.name,
                "pages":     num_pages,
                "chars":     len(full_text),
                "truncated": truncated,
                "scanned":   scanned_pages,
                "content":   full_text + note,
            }
        except Exception as e:
            return None

    def _ocr_page_fitz(self, page, page_num: int) -> str:
        """Try OCR on a single fitz page using pytesseract."""
        try:
            import pytesseract
            from PIL import Image
            import io
            # Render page to image at 200 DPI
            mat  = __import__('fitz').Matrix(2, 2)
            pix  = page.get_pixmap(matrix=mat)
            img  = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            text = pytesseract.image_to_string(img)
            return text.strip()
        except Exception:
            return ""

    def _try_pypdf(self, p: Path) -> dict | None:
        reader_cls = None
        for lib in ["pypdf", "PyPDF2"]:
            try:
                mod = __import__(lib)
                reader_cls = mod.PdfReader
                break
            except ImportError:
                continue
        if reader_cls is None:
            return None

        try:
            text_pages = []
            with open(p, "rb") as f:
                pdf = reader_cls(f)
                num_pages = len(pdf.pages)
                scanned   = 0
                for i, page in enumerate(pdf.pages):
                    try:
                        text = (page.extract_text() or "").strip()
                        if text:
                            text_pages.append(f"--- Page {i+1} ---\n{text}")
                        else:
                            scanned += 1
                            text_pages.append(f"--- Page {i+1} --- [scanned image]")
                    except Exception as e:
                        text_pages.append(f"--- Page {i+1} --- [error: {e}]")

            full_text = "\n\n".join(text_pages)
            truncated = False
            if len(full_text) > 14000:
                full_text = full_text[:14000]
                truncated = True

            if not any(t for t in text_pages if "[scanned" not in t and "[error" not in t):
                return {"status": "error",
                        "message": "This PDF contains only scanned images. "
                                   "Install pymupdf + pytesseract for OCR support:\n"
                                   "  pip install pymupdf pytesseract\n"
                                   "  Also install Tesseract: https://github.com/tesseract-ocr/tesseract"}

            return {
                "status":    "ok",
                "path":      str(p),
                "filename":  p.name,
                "pages":     num_pages,
                "chars":     len(full_text),
                "truncated": truncated,
                "scanned":   scanned,
                "content":   full_text,
            }
        except Exception as e:
            return {"status": "error", "message": f"Failed to read PDF: {e}"}
