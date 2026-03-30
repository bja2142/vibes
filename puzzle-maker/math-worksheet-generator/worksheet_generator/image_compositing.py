from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image


class WorksheetForegroundCompositingError(ValueError):
    pass


def normalize_background_to_reference_size(*, background_bytes: bytes, reference_bytes: bytes) -> bytes:
    if not background_bytes:
        raise WorksheetForegroundCompositingError("background image bytes must not be empty")
    if not reference_bytes:
        raise WorksheetForegroundCompositingError("reference image bytes must not be empty")

    background = Image.open(BytesIO(background_bytes)).convert("RGBA")
    reference = Image.open(BytesIO(reference_bytes)).convert("RGBA")
    if background.size != reference.size:
        background = background.resize(reference.size, Image.Resampling.LANCZOS)
    buffer = BytesIO()
    background.save(buffer, format="PNG")
    return buffer.getvalue()


def composite_foreground_over_background(*, background_bytes: bytes, foreground_bytes: bytes) -> bytes:
    if not background_bytes:
        raise WorksheetForegroundCompositingError("background image bytes must not be empty")
    if not foreground_bytes:
        raise WorksheetForegroundCompositingError("foreground image bytes must not be empty")

    background = Image.open(BytesIO(background_bytes)).convert("RGBA")
    foreground = Image.open(BytesIO(foreground_bytes)).convert("RGBA")
    if background.size != foreground.size:
        raise WorksheetForegroundCompositingError(
            f"foreground size {foreground.size} does not match background size {background.size}"
        )
    composited = Image.alpha_composite(background, foreground)
    buffer = BytesIO()
    composited.save(buffer, format="PNG")
    return buffer.getvalue()


def write_composited_png(*, background_bytes: bytes, foreground_bytes: bytes, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(
        composite_foreground_over_background(
            background_bytes=background_bytes,
            foreground_bytes=foreground_bytes,
        )
    )
    return output_path


def tint_foreground(*, foreground_bytes: bytes, color_hex: str) -> bytes:
    if not foreground_bytes:
        raise WorksheetForegroundCompositingError("foreground image bytes must not be empty")
    foreground = Image.open(BytesIO(foreground_bytes)).convert("RGBA")
    tinted = Image.new("RGBA", foreground.size, (0, 0, 0, 0))
    color = Image.new("RGBA", foreground.size, color_hex)
    tinted = Image.composite(color, tinted, foreground.getchannel("A"))
    buffer = BytesIO()
    tinted.save(buffer, format="PNG")
    return buffer.getvalue()
