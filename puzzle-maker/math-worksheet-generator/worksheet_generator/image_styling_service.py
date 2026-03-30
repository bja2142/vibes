from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import base64
import json
import logging
from typing import Protocol

from google import genai
from google.genai import types

from .logging_utils import log_event


class WorksheetImageStylingError(ValueError):
    def __init__(self, message: str, *, response_json: str | None = None) -> None:
        super().__init__(message)
        self.response_json = response_json


@dataclass(frozen=True)
class WorksheetImageStylingRequest:
    prompt: str
    source_image_bytes: bytes
    source_mime_type: str = "image/png"


@dataclass(frozen=True)
class StyledWorksheetImageArtifact:
    image_bytes: bytes
    mime_type: str
    model: str
    prompt: str
    response_id: str | None = None
    response_text: str | None = None
    raw_response_json: str | None = None

    @property
    def filename_suffix(self) -> str:
        return _mime_type_to_suffix(self.mime_type)


class WorksheetImageStyler(Protocol):
    def style_image(self, request: WorksheetImageStylingRequest) -> StyledWorksheetImageArtifact:
        ...


class GeminiWorksheetImageStylingService:
    def __init__(
        self,
        api_key: str,
        *,
        model: str = "gemini-3.1-flash-image-preview",
    ) -> None:
        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._logger = logging.getLogger("worksheet_generator.image_styling_service")

    def style_image(self, request: WorksheetImageStylingRequest) -> StyledWorksheetImageArtifact:
        if not request.source_image_bytes:
            raise WorksheetImageStylingError("worksheet image styling requires non-empty source image bytes")
        if not request.prompt.strip():
            raise WorksheetImageStylingError("worksheet image styling requires a non-empty prompt")

        image_part = types.Part.from_bytes(data=request.source_image_bytes, mime_type=request.source_mime_type)
        log_event(
            self._logger,
            "worksheet_image_styling_requested",
            verbosity="normal",
            model=self._model,
            mime_type=request.source_mime_type,
            prompt=request.prompt,
            source_bytes=len(request.source_image_bytes),
        )
        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=[image_part, request.prompt],
                config=types.GenerateContentConfig(response_modalities=["IMAGE"]),
            )
        except Exception as exc:
            raise WorksheetImageStylingError(f"Gemini image styling request failed: {exc}") from exc

        response_json = _serialize_gemini_response(response)
        try:
            artifact = _parse_styled_image_response(
                response=response,
                model=self._model,
                prompt=request.prompt,
                response_json=response_json,
            )
        except WorksheetImageStylingError as exc:
            log_event(
                self._logger,
                "worksheet_image_styling_failed",
                verbosity="minimal",
                model=self._model,
                error=str(exc),
                response_json=response_json,
            )
            raise
        log_event(
            self._logger,
            "worksheet_image_styling_succeeded",
            verbosity="normal",
            model=self._model,
            mime_type=artifact.mime_type,
            response_id=artifact.response_id,
            image_bytes=len(artifact.image_bytes),
        )
        return artifact


def write_styled_image_artifact(artifact: StyledWorksheetImageArtifact, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(artifact.image_bytes)
    return output_path


def write_styling_debug_metadata(artifact: StyledWorksheetImageArtifact, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "model": artifact.model,
        "mime_type": artifact.mime_type,
        "response_id": artifact.response_id,
        "response_text": artifact.response_text,
        "raw_response_json": artifact.raw_response_json,
        "prompt": artifact.prompt,
    }
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return output_path


def _parse_styled_image_response(*, response: object, model: str, prompt: str, response_json: str | None = None) -> StyledWorksheetImageArtifact:
    response_id = getattr(response, "response_id", None)
    response_text = getattr(response, "text", None)
    candidates = getattr(response, "candidates", None) or []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            inline_data = getattr(part, "inline_data", None)
            if inline_data is None:
                continue
            data = getattr(inline_data, "data", None)
            mime_type = getattr(inline_data, "mime_type", None) or "image/png"
            if isinstance(data, bytes) and data:
                return StyledWorksheetImageArtifact(
                    image_bytes=data,
                    mime_type=str(mime_type),
                    model=model,
                    prompt=prompt,
                    response_id=str(response_id) if response_id is not None else None,
                    response_text=str(response_text) if response_text else None,
                    raw_response_json=response_json,
                )
    raise WorksheetImageStylingError(
        "Gemini image styling did not return an inline image artifact",
        response_json=response_json,
    )


def _mime_type_to_suffix(mime_type: str) -> str:
    normalized = mime_type.strip().lower()
    return {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
    }.get(normalized, ".bin")


def _serialize_gemini_response(response: object) -> str:
    payload = _jsonable_value(response)
    return json.dumps(payload, sort_keys=True, default=str)


def _jsonable_value(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return {"__type__": "bytes", "base64": base64.b64encode(value).decode("ascii")}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable_value(item) for item in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _jsonable_value(model_dump(mode="json"))
        except TypeError:
            return _jsonable_value(model_dump())
    to_json_dict = getattr(value, "to_json_dict", None)
    if callable(to_json_dict):
        return _jsonable_value(to_json_dict())
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return _jsonable_value(to_dict())
    if hasattr(value, "__dict__"):
        return {
            key: _jsonable_value(item)
            for key, item in vars(value).items()
            if not key.startswith("_")
        }
    return repr(value)
