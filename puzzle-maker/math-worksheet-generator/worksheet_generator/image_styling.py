from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Protocol

from google import genai

from .logging_utils import log_event


ART_STYLES: dict[str, str] = {
    "cartoon": "Vibrant, thick lines, playful characters, high-energy animation feel.",
    "watercolor": "Soft edges, painted textures, gentle blends, pastel color washes.",
    "sketch": "Hand-drawn pencil and pen marks, clean linework, cross-hatching detail.",
    "flat": "Modern vector-like shapes, solid colors, clean geometry, minimal gradients.",
    "isometric": "Structured miniature-world perspective, playful technical organization, light 3D depth.",
    "cyberpunk": "Neon accents, futuristic city motifs, high-contrast darks, glowing synthwave atmosphere.",
    "origami": "Paper-fold textures, sharp creases, geometric craft feel, tactile paper surfaces.",
    "steampunk": "Brass, copper, gears, Victorian industrial motifs, sepia mechanical atmosphere.",
    "pixel-art": "Retro 8-bit aesthetic, blocky forms, limited palette, nostalgic game energy.",
    "oil-painting": "Rich brushstrokes, textured paint buildup, classical art mood, dramatic color.",
    "crayon": "Waxy hand-colored look, playful classroom feel, rough texture, childlike color energy.",
    "blueprint": "Technical drafting look with deep blue ground and crisp white linework.",
    "stained-glass": "Translucent jewel-tone panes, thick dark outlines, mosaic light-through-glass effect.",
    "pop-art": "Comic-book boldness, saturated tones, punchy graphic energy, dot-pattern accents.",
    "chalkboard": "Dusty chalkboard atmosphere with chalk-like marks and classroom visual language.",
}

STYLE_OPTIONS = [{"value": name, "label": name.replace("-", " ").title()} for name in ART_STYLES]

COLOR_MODE_OPTIONS = [
    {"value": "color", "label": "Full Color"},
    {"value": "black_and_white", "label": "Black And White"},
]

DEFAULT_IMAGE_STYLE = "watercolor"
DEFAULT_IMAGE_COLOR_MODE = "color"
DEFAULT_IMAGE_REFINEMENT_MODEL = "gemini-2.5-flash-lite"


@dataclass(frozen=True)
class WorksheetImageStylingPromptRequest:
    theme: str
    style_name: str
    color_mode: str
    ink_saver: bool
    additional_guidance: str | None
    title: str
    prompt_text: str
    learner_band_label: str
    reveal_mode_label: str


class WorksheetStylingPromptRefinementError(ValueError):
    pass


class WorksheetStylingPromptRefiner(Protocol):
    def refine(self, prompt: str) -> str:
        ...


class GeminiWorksheetStylingPromptRefiner:
    def __init__(
        self,
        api_key: str,
        *,
        model: str = DEFAULT_IMAGE_REFINEMENT_MODEL,
    ) -> None:
        self._model = model
        self._client = genai.Client(api_key=api_key)
        self._logger = logging.getLogger("worksheet_generator.image_styling")

    def refine(self, prompt: str) -> str:
        instruction = (
            "Tighten the following worksheet image-styling prompt.\n"
            "Keep all preservation rules intact.\n"
            "Do not weaken any constraints about preserving text, equations, labels, boxes, or layout.\n"
            "Return plain text only.\n\n"
            f"{prompt}"
        )
        log_event(self._logger, "worksheet_styling_prompt_refinement_requested", verbosity="normal", model=self._model)
        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=instruction,
            )
        except Exception as exc:
            raise WorksheetStylingPromptRefinementError(f"Gemini prompt refinement failed: {exc}") from exc
        refined = getattr(response, "text", "") or ""
        refined = refined.strip()
        if not refined:
            raise WorksheetStylingPromptRefinementError("Gemini returned an empty refined styling prompt.")
        log_event(self._logger, "worksheet_styling_prompt_refinement_completed", verbosity="normal", model=self._model)
        return refined


class NoOpWorksheetStylingPromptRefiner:
    def refine(self, prompt: str) -> str:
        return prompt


def build_stricter_worksheet_styling_prompt(prompt: str, *, verification_note: str | None = None) -> str:
    retry_note = (
        f"Previous verification failure: {verification_note}. "
        if verification_note
        else ""
    )
    return (
        f"{prompt} "
        "RETRY REQUIREMENTS: Preserve every semantic worksheet pixel exactly after compositing. "
        "Do not alter or obscure any text, numbers, equations, labels, or boxes. "
        "Leave every final answer box and student-answer blank empty. Do not prefill them with numbers, letters, symbols, or decorative marks. "
        "Reduce decoration density near text-heavy regions and margins that touch worksheet content. "
        f"{retry_note}"
    ).strip()


def build_worksheet_styling_prompt(request: WorksheetImageStylingPromptRequest) -> str:
    style_key = request.style_name.strip().lower()
    style_description = ART_STYLES.get(style_key, f"Artistic {request.style_name} styling.")
    color_description = "Full color" if request.color_mode == "color" else "Black and white"
    ink_saver_description = (
        "Use an ink-saving treatment with strong contrast and avoid large dense dark fills."
        if request.ink_saver
        else "Do not force an ink-saving treatment if richer color or texture helps the theme."
    )
    theme_text = request.theme.strip() or "general classroom math"
    additional_guidance = (request.additional_guidance or "").strip()
    additional_guidance_text = (
        f"Additional user guidance: '{additional_guidance}'. Apply this only when it does not conflict with the preservation rules or reduce worksheet legibility. "
        if additional_guidance
        else ""
    )

    return (
        f"Restyle this existing math worksheet image in a {request.style_name} style themed around '{theme_text}'. "
        f"{style_description} {color_description}. {ink_saver_description} "
        f"The worksheet title is '{request.title}'. The clue prompt is '{request.prompt_text}'. "
        f"The learner band is {request.learner_band_label} and the reveal mode is {request.reveal_mode_label}. "
        f"{additional_guidance_text}"
        "Decorate only the page background, border treatment, margins, and other empty whitespace around the worksheet content. "
        "Use the theme to add tasteful classroom-safe decorative elements that support the worksheet without overwhelming it. "
        "CRITICAL PRESERVATION RULES: "
        "1. Every visible text character must remain exactly the same. Do not rewrite, paraphrase, redraw, stylize, replace, or obscure any text. "
        "2. Every number, operator, equation, label, lookup entry, answer box, and final answer slot must remain exactly where it is. "
        "3. Do not move, resize, crop, rotate, warp, or relayout the worksheet. "
        "4. Do not cover the worksheet with characters, textures, stickers, shadows, or decorative objects that reduce legibility. "
        "5. Do not alter question content, problem numbering, title wording, prompt wording, letter-bank entries, or color-by-number labels. "
        "6. Leave all final answer boxes and student-answer blanks empty. Do not fill them with numbers, letters, symbols, shading, or decorations. "
        "7. Preserve the printability of the page and keep the worksheet easy to read for students. "
        "The desired outcome is a themed worksheet image with the original worksheet content perfectly preserved."
    )
