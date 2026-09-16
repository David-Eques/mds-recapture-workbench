"""Bounded local OCR for uploaded PDF and image documents.

Tesseract does not read PDFs directly, so PDF pages are rasterized with Poppler first. All work is
performed in a private temporary directory and removed on both success and failure. Subprocess output
is parsed in memory; source bytes and OCR text are never logged.
"""

from __future__ import annotations

import csv
import io
import os
import re
import shutil
import signal
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

MAX_DOCUMENT_BYTES = 20 * 1024 * 1024
MAX_DOCUMENT_PAGES = 50
MAX_IMAGE_PIXELS = 50_000_000


class OcrError(RuntimeError):
    """The uploaded document could not be safely converted to OCR text."""


class OcrLimitError(OcrError):
    """The document exceeds an explicit resource limit."""


class UnsupportedDocumentError(OcrError):
    """The document's inspected content type is unsupported."""


class DocumentToolUnavailableError(OcrError):
    """A required local executable is missing."""


class DocumentProcessingTimeout(OcrError):
    """A document subprocess exceeded its deadline."""


@dataclass(frozen=True)
class OcrLine:
    text: str
    bbox: list[float] | None
    confidence: float | None


@dataclass(frozen=True)
class OcrPage:
    number: int
    text: str
    confidence: float | None
    lines: list[OcrLine] = field(default_factory=list)


@dataclass(frozen=True)
class OcrDocument:
    filename: str
    sha256: str
    pages: list[OcrPage]
    provider: str
    provider_version: str


def _tool(name: str, env_name: str) -> str:
    configured = os.environ.get(env_name, name)
    resolved = shutil.which(configured)
    if resolved is None:
        raise DocumentToolUnavailableError("a required document processing tool is unavailable")
    return resolved


def _run(args: list[str], *, timeout: int, text: bool = False) -> subprocess.CompletedProcess:
    try:
        proc = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=text,
            start_new_session=True,
        )
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, OSError):
            proc.kill()
        proc.communicate()
        raise DocumentProcessingTimeout("document processing timed out") from exc
    except (FileNotFoundError, OSError) as exc:
        raise DocumentToolUnavailableError("a required document processing tool is unavailable") from exc
    if proc.returncode != 0:
        raise OcrError(f"document processing failed (exit={proc.returncode})")
    return subprocess.CompletedProcess(args, proc.returncode, stdout, stderr)


def _mime_from_content(data: bytes, declared: str | None, filename: str) -> str:
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"II*\x00", b"MM\x00*")):
        return "image/tiff"
    suffix = Path(filename).suffix.lower()
    if (declared or "").startswith("text/") or suffix in {".txt", ".md"}:
        return "text/plain"
    raise UnsupportedDocumentError("unsupported document type; use PDF, PNG, JPEG, TIFF, TXT, or Markdown")


def _decode_text(data: bytes) -> str:
    if b"\x00" in data:
        raise UnsupportedDocumentError("text document contains binary content")
    for encoding in ("utf-8-sig", "utf-8", "windows-1252"):
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        controls = sum(1 for char in text if ord(char) < 32 and char not in "\n\r\t\f")
        if controls > max(2, len(text) // 100):
            raise UnsupportedDocumentError("text document contains binary content")
        return text
    raise OcrError("text document is not valid UTF-8 or Windows-1252")


def _parse_page_count(output: str) -> int:
    match = re.search(r"^Pages:\s+(\d+)\s*$", output, re.MULTILINE)
    if match is None:
        raise OcrError("could not determine PDF page count")
    return int(match.group(1))


def _image_pages(data: bytes, tmp: Path, max_pages: int) -> list[Path]:
    """Validate image dimensions and split multi-frame images into bounded PNG pages."""
    try:
        from PIL import Image, UnidentifiedImageError

        image = Image.open(io.BytesIO(data))
        frame_count = int(getattr(image, "n_frames", 1))
        if frame_count > max_pages:
            raise OcrLimitError(f"image has {frame_count} frames; limit is {max_pages}")
        pages: list[Path] = []
        for index in range(frame_count):
            image.seek(index)
            width, height = image.size
            if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
                raise OcrLimitError("image dimensions exceed the processing limit")
            output = tmp / f"image-{index + 1:04d}.png"
            image.convert("RGB").save(output, format="PNG")
            pages.append(output)
        return pages
    except UnidentifiedImageError as exc:
        raise OcrError("image content could not be decoded") from exc
    except OSError as exc:
        raise OcrError("image content could not be decoded") from exc


def _validate_raster_dimensions(path: Path) -> None:
    try:
        from PIL import Image, UnidentifiedImageError

        with Image.open(path) as image:
            width, height = image.size
            if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
                raise OcrLimitError("rasterized page dimensions exceed the processing limit")
    except UnidentifiedImageError as exc:
        raise OcrError("rasterized page could not be decoded") from exc
    except OSError as exc:
        raise OcrError("rasterized page could not be decoded") from exc


def _float(value: str | None) -> float | None:
    try:
        parsed = float(value or "")
    except ValueError:
        return None
    return None if parsed < 0 else parsed


def _tsv_page(tsv: str, page_number: int) -> OcrPage:
    grouped: dict[tuple[str, str, str, str], list[dict[str, str]]] = {}
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t"):
        word = (row.get("text") or "").strip()
        if not word or row.get("level") != "5":
            continue
        key = (
            str(row.get("page_num", "")),
            str(row.get("block_num", "")),
            str(row.get("par_num", "")),
            str(row.get("line_num", "")),
        )
        grouped.setdefault(key, []).append(row)

    lines: list[OcrLine] = []
    for rows in grouped.values():
        text = " ".join((row.get("text") or "").strip() for row in rows if (row.get("text") or "").strip())
        if not text:
            continue
        boxes: list[tuple[float, float, float, float]] = []
        confidences: list[float] = []
        for row in rows:
            try:
                boxes.append(
                    (
                        float(row.get("left") or 0),
                        float(row.get("top") or 0),
                        float(row.get("width") or 0),
                        float(row.get("height") or 0),
                    )
                )
            except ValueError:
                pass
            confidence = _float(row.get("conf"))
            if confidence is not None:
                confidences.append(confidence / 100.0)
        if boxes:
            left = min(box[0] for box in boxes)
            top = min(box[1] for box in boxes)
            right = max(box[0] + box[2] for box in boxes)
            bottom = max(box[1] + box[3] for box in boxes)
            bbox: list[float] | None = [left, top, right - left, bottom - top]
        else:
            bbox = None
        lines.append(
            OcrLine(
                text=text,
                bbox=bbox,
                confidence=(sum(confidences) / len(confidences) if confidences else None),
            )
        )
    text = "\n".join(line.text for line in lines)
    confidences = [line.confidence for line in lines if line.confidence is not None]
    return OcrPage(
        number=page_number,
        text=text,
        confidence=(sum(confidences) / len(confidences) if confidences else None),
        lines=lines,
    )


class TesseractOcr:
    """Tesseract/Poppler adapter with size, page, and execution-time bounds."""

    def __init__(
        self,
        *,
        language: str | None = None,
        dpi: int | None = None,
        ocr_timeout_seconds: int | None = None,
        raster_timeout_seconds: int | None = None,
        max_pages: int = MAX_DOCUMENT_PAGES,
    ):
        self.language = language or os.environ.get("OCR_LANGUAGE", "eng")
        self.dpi = dpi or int(os.environ.get("OCR_DPI", "200"))
        self.ocr_timeout_seconds = ocr_timeout_seconds or int(os.environ.get("OCR_TIMEOUT_SECONDS", "30"))
        self.raster_timeout_seconds = raster_timeout_seconds or int(
            os.environ.get("RASTER_TIMEOUT_SECONDS", "60")
        )
        self.max_pages = max_pages

    def version(self) -> str:
        proc = _run([_tool("tesseract", "TESSERACT_BINARY"), "--version"], timeout=5, text=True)
        first = (proc.stdout or "").splitlines()[0] if proc.stdout else "tesseract unknown"
        return first.replace("tesseract ", "", 1).strip()

    def extract(
        self,
        data: bytes,
        *,
        filename: str,
        content_type: str | None = None,
        max_pages: int | None = None,
    ) -> OcrDocument:
        import hashlib

        if not data:
            raise OcrError("document is empty")
        if len(data) > MAX_DOCUMENT_BYTES:
            raise OcrLimitError("document exceeds the 20 MB limit")
        mime = _mime_from_content(data, content_type, filename)
        page_limit = min(self.max_pages, max_pages if max_pages is not None else self.max_pages)
        if page_limit < 1:
            raise OcrLimitError("clinical documents exceed the 50-page request limit")
        digest = hashlib.sha256(data).hexdigest()
        if mime == "text/plain":
            text = _decode_text(data).strip()
            return OcrDocument(
                filename=filename,
                sha256=digest,
                pages=[OcrPage(number=1, text=text, confidence=None)],
                provider="native_text",
                provider_version="1",
            )

        tesseract = _tool("tesseract", "TESSERACT_BINARY")
        with tempfile.TemporaryDirectory(prefix="mds-ocr-") as tmp_name:
            tmp = Path(tmp_name)
            tmp.chmod(0o700)
            suffix = {
                "application/pdf": ".pdf",
                "image/png": ".png",
                "image/jpeg": ".jpg",
                "image/tiff": ".tiff",
            }[mime]
            source = tmp / f"source{suffix}"
            source.write_bytes(data)

            images: list[Path]
            if mime == "application/pdf":
                pdfinfo = _tool("pdfinfo", "PDFINFO_BINARY")
                info = _run([pdfinfo, str(source)], timeout=10, text=True)
                page_count = _parse_page_count(info.stdout or "")
                if page_count > page_limit:
                    raise OcrLimitError(f"PDF has {page_count} pages; remaining limit is {page_limit}")
                prefix = tmp / "page"
                _run(
                    [
                        _tool("pdftoppm", "PDFTOPPM_BINARY"),
                        "-r",
                        str(self.dpi),
                        "-png",
                        str(source),
                        str(prefix),
                    ],
                    timeout=self.raster_timeout_seconds,
                )
                images = sorted(tmp.glob("page-*.png"))
                if len(images) != page_count:
                    raise OcrError("PDF rasterization returned an unexpected page count")
                for image in images:
                    _validate_raster_dimensions(image)
            else:
                images = _image_pages(data, tmp, page_limit)

            pages: list[OcrPage] = []
            for index, image in enumerate(images, start=1):
                proc = _run(
                    [
                        tesseract,
                        str(image),
                        "stdout",
                        "-l",
                        self.language,
                        "--psm",
                        "6",
                        "tsv",
                    ],
                    timeout=self.ocr_timeout_seconds,
                    text=True,
                )
                pages.append(_tsv_page(proc.stdout or "", index))

        return OcrDocument(
            filename=filename,
            sha256=digest,
            pages=pages,
            provider="Tesseract",
            provider_version=self.version(),
        )
