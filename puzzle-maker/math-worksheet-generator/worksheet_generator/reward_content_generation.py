from __future__ import annotations

from dataclasses import dataclass
import logging
import re
from typing import Protocol

from google import genai
from pydantic import BaseModel, Field, ValidationError

from .logging_utils import log_event
from .models import ApprovalState, LearnerBand, RewardContentCandidate


@dataclass
class RewardContentGenerationRequest:
    theme: str
    learner_band: LearnerBand
    preferred_style: str | None = None
    language: str = "en"
    reading_level_expectations: str | None = None
    solution_length_guidance: str | None = None
    reveal_mode: str | None = None
    color_picture_source: str | None = None
    color_picture_preset: str | None = None
    tone_constraints: tuple[str, ...] = ("short", "clear", "classroom-appropriate")


class RewardContentGenerator(Protocol):
    def generate(self, request: RewardContentGenerationRequest) -> RewardContentCandidate:
        ...

    def generate_for_solution(self, request: RewardContentGenerationRequest, solution_phrase: str) -> RewardContentCandidate:
        ...


class GeminiGenerationError(ValueError):
    pass


class GeminiRewardContentPayload(BaseModel):
    prompt_text: str = Field(description="A short classroom-safe riddle, pun, or thematic question.")
    solution_phrase: str = Field(
        description="A concise answer phrase for the worksheet reveal using letters and spaces only, with no digits or punctuation."
    )
    style: str = Field(description="The style used for the generated reward content.")
    reasoning_note: str | None = Field(default=None, description="A short note about age-appropriate phrasing.")


class GeminiRewardPromptOnlyPayload(BaseModel):
    prompt_text: str = Field(description="A short classroom-safe riddle, pun, or thematic question.")
    reasoning_note: str | None = Field(default=None, description="A short note about age-appropriate phrasing.")


class GeminiRewardContentGenerator:
    def __init__(
        self,
        api_key: str,
        *,
        model: str = "gemini-2.5-flash-lite",
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._client = genai.Client(api_key=api_key)
        self._logger = logging.getLogger("worksheet_generator.gemini")

    def generate(self, request: RewardContentGenerationRequest) -> RewardContentCandidate:
        prompt = self._build_prompt(request)
        log_event(
            self._logger,
            "gemini_request_started",
            theme=request.theme,
            learner_band=request.learner_band.value,
            model=self._model,
        )
        log_event(self._logger, "gemini_request_prompt", verbosity="normal", prompt=prompt)
        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=prompt,
                config={
                    "response_mime_type": "application/json",
                    "response_schema": GeminiRewardContentPayload,
                },
            )
        except Exception as exc:
            raise GeminiGenerationError(f"Gemini SDK request failed: {exc}") from exc
        log_event(
            self._logger,
            "gemini_response_received",
            verbosity="normal",
            response_text=getattr(response, "text", ""),
        )

        candidate_payload = self._parse_candidate_payload(response)
        style = str(candidate_payload.style or request.preferred_style or "riddle").strip().lower()
        prompt_text = candidate_payload.prompt_text.strip()
        solution_phrase = self._normalize_solution_phrase(candidate_payload.solution_phrase)

        if not prompt_text or not solution_phrase:
            raise GeminiGenerationError("Gemini response did not include both prompt_text and solution_phrase")

        review_notes = self._build_review_notes(
            request,
            model_note=f"Gemini model {self._model} generated structured reward content.",
            reasoning_note=candidate_payload.reasoning_note,
        )

        log_event(
            self._logger,
            "gemini_candidate_parsed",
            verbosity="verbose",
            prompt_text=prompt_text,
            solution_phrase=solution_phrase,
            style=style,
        )

        return RewardContentCandidate(
            prompt_text=prompt_text,
            solution_phrase=solution_phrase,
            theme=request.theme,
            source="gemini_api",
            approval_state=ApprovalState.PENDING,
            style=style,
            language=request.language,
            review_notes=review_notes,
        )

    def generate_for_solution(self, request: RewardContentGenerationRequest, solution_phrase: str) -> RewardContentCandidate:
        normalized_solution_phrase = self._normalize_solution_phrase(solution_phrase)
        prompt = self._build_prompt_for_solution(request, normalized_solution_phrase)
        log_event(
            self._logger,
            "gemini_solution_prompt_request_started",
            theme=request.theme,
            learner_band=request.learner_band.value,
            model=self._model,
        )
        log_event(self._logger, "gemini_solution_prompt_request", verbosity="normal", prompt=prompt)
        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=prompt,
                config={
                    "response_mime_type": "application/json",
                    "response_schema": GeminiRewardPromptOnlyPayload,
                },
            )
        except Exception as exc:
            raise GeminiGenerationError(f"Gemini SDK request failed: {exc}") from exc
        log_event(
            self._logger,
            "gemini_solution_prompt_response_received",
            verbosity="normal",
            response_text=getattr(response, "text", ""),
        )

        candidate_payload = self._parse_prompt_only_payload(response)
        prompt_text = candidate_payload.prompt_text.strip()
        if not prompt_text:
            raise GeminiGenerationError("Gemini response did not include prompt_text for the requested solution phrase")
        if normalized_solution_phrase.lower() in prompt_text.lower():
            raise GeminiGenerationError("Gemini returned a clue that included the requested solution phrase directly")

        style = str(request.preferred_style or "riddle").strip().lower()
        review_notes = self._build_review_notes(
            request,
            model_note=f"Gemini model {self._model} generated a clue for a user-provided solution phrase.",
            reasoning_note=candidate_payload.reasoning_note,
            extra_notes=[f"Requested solution phrase: {normalized_solution_phrase}."],
        )
        return RewardContentCandidate(
            prompt_text=prompt_text,
            solution_phrase=normalized_solution_phrase,
            theme=request.theme,
            source="gemini_api",
            approval_state=ApprovalState.PENDING,
            style=style,
            language=request.language,
            review_notes=review_notes,
        )

    def _parse_candidate_payload(self, response: object) -> GeminiRewardContentPayload:
        parsed = getattr(response, "parsed", None)
        if isinstance(parsed, GeminiRewardContentPayload):
            return parsed
        if isinstance(parsed, dict):
            return GeminiRewardContentPayload.model_validate(parsed)
        text = getattr(response, "text", "")
        if not text:
            raise GeminiGenerationError("Gemini returned an empty candidate payload")
        try:
            return GeminiRewardContentPayload.model_validate_json(text)
        except ValidationError as exc:
            raise GeminiGenerationError(f"Gemini returned invalid structured output: {exc}") from exc

    def _parse_prompt_only_payload(self, response: object) -> GeminiRewardPromptOnlyPayload:
        parsed = getattr(response, "parsed", None)
        if isinstance(parsed, GeminiRewardPromptOnlyPayload):
            return parsed
        if isinstance(parsed, dict):
            return GeminiRewardPromptOnlyPayload.model_validate(parsed)
        text = getattr(response, "text", "")
        if not text:
            raise GeminiGenerationError("Gemini returned an empty candidate payload")
        try:
            return GeminiRewardPromptOnlyPayload.model_validate_json(text)
        except ValidationError as exc:
            raise GeminiGenerationError(f"Gemini returned invalid structured output: {exc}") from exc

    def _build_prompt(self, request: RewardContentGenerationRequest) -> str:
        learner_band = request.learner_band.value.replace("_", " ")
        preferred_style = request.preferred_style or "riddle"
        expectations = request.reading_level_expectations or (
            "Use short, clear, classroom-appropriate language with age-appropriate vocabulary."
        )
        solution_length_guidance = request.solution_length_guidance or "single concise word or short phrase"
        color_picture_guidance = ""
        if request.reveal_mode == "color_by_number":
            if request.color_picture_source == "gemini":
                solution_length_guidance = (
                    "Choose one simple classroom-safe object or symbol word that can be drawn clearly as square pixel art."
                )
                color_picture_guidance = (
                    "This worksheet will use Gemini to generate a square color-by-number picture.\n"
                    "Start by choosing a simple concrete picture subject that can be recognized on a square grid.\n"
                    "The solution_phrase must be that same simple drawable subject, ideally one word.\n"
                    "Choose the solution_phrase from this supported drawable subject list only:\n"
                    "SMILE, HEART, STAR, MOON, SUN, FLOWER, APPLE, TREE, EVERGREEN, CAT, CLOWN FISH, BLUE TANG, BUTTERFLY, ROCKET.\n"
                    "Then think about characters, objects, places, actions, or situations closely related to that subject.\n"
                    "Write prompt_text so it clearly points to that exact same subject through those related clues.\n"
                    "Do not say the subject word itself inside prompt_text.\n"
                    "Do not invent a different subject outside this supported list.\n"
                    "Avoid abstract phrases, slogans, long answers, or answers that cannot be pictured easily.\n"
                )
            else:
                preset_subject = _preset_subject_word(request.color_picture_preset)
                solution_length_guidance = f"Use the single word {preset_subject} as the solution phrase."
                color_picture_guidance = (
                    "This worksheet will use a preset color-by-number picture.\n"
                    f"The preset picture subject is {preset_subject}.\n"
                    f"The solution_phrase must be exactly {preset_subject}.\n"
                    "Think about characters, objects, places, actions, or situations closely related to that subject.\n"
                    "Write prompt_text as a short classroom-safe riddle or question within the chosen theme using those related clues.\n"
                    "Do not say the preset subject word itself inside prompt_text.\n"
                    f"The answer to the prompt_text must clearly and unambiguously be {preset_subject}.\n"
                    "Do not choose a different answer word.\n"
                )
        style_guidance = self._style_guidance(preferred_style)
        return (
            "Create one short classroom-safe worksheet reward clue.\n"
            f"Theme: {request.theme}\n"
            f"Target learner band: {learner_band}\n"
            f"Preferred style: {preferred_style}\n"
            f"Language: {request.language}\n"
            f"Reading-level expectations: {expectations}\n"
            f"Solution length guidance: {solution_length_guidance}\n"
            f"Tone constraints: {', '.join(request.tone_constraints)}\n"
            "Return only JSON matching the provided schema.\n"
            "The prompt_text should be a short riddle, pun, or thematic question.\n"
            "The solution_phrase should be specific, classroom-appropriate, concise, and usable as a reveal answer.\n"
            f"{style_guidance}"
            "Before writing prompt_text, think about characters, objects, places, actions, or topics closely related to the chosen answer.\n"
            "Build the clue from those related ideas instead of stating the answer directly.\n"
            "Do not include the exact solution_phrase inside prompt_text.\n"
            "Avoid using the exact theme word inside prompt_text when a related clue can imply it instead.\n"
            f"{color_picture_guidance}"
            "CRITICAL: solution_phrase must contain letters and spaces only.\n"
            "Do not use digits, punctuation, apostrophes, hyphens, quotation marks, emoji, symbols, or abbreviations with numbers.\n"
            "If the natural phrase would contain digits or punctuation, rewrite it into plain words using letters and spaces only.\n"
            "Prefer a reveal answer with 4 to 20 alphabetic characters total, not counting spaces.\n"
            "Valid examples: MATH RULES, NUMBER SENSE, FRACTION FUN.\n"
            "Invalid examples: MATH!, 3D SHAPES, X-O FACTOR, PI DAY 2026.\n"
        )

    def _build_prompt_for_solution(self, request: RewardContentGenerationRequest, solution_phrase: str) -> str:
        learner_band = request.learner_band.value.replace("_", " ")
        preferred_style = request.preferred_style or "riddle"
        expectations = request.reading_level_expectations or (
            "Use short, clear, classroom-appropriate language with age-appropriate vocabulary."
        )
        style_guidance = self._style_guidance(preferred_style)
        return (
            "Create one short classroom-safe worksheet reward clue.\n"
            f"Theme: {request.theme}\n"
            f"Target learner band: {learner_band}\n"
            f"Preferred style: {preferred_style}\n"
            f"Language: {request.language}\n"
            f"Reading-level expectations: {expectations}\n"
            f"Fixed solution_phrase: {solution_phrase}\n"
            f"Tone constraints: {', '.join(request.tone_constraints)}\n"
            "Return only JSON matching the provided schema.\n"
            "The solution_phrase is already chosen and must remain exactly the fixed solution_phrase.\n"
            "Do not invent a different answer.\n"
            f"{style_guidance}"
            "Before writing prompt_text, think about characters, objects, places, actions, or topics closely related to the fixed solution_phrase and theme.\n"
            "Build the clue from those related ideas instead of stating the answer directly.\n"
            "The prompt_text should be a short riddle, pun, or thematic question that clearly leads to the fixed solution_phrase.\n"
            "Do not include the exact solution_phrase inside prompt_text.\n"
            "Do not include any individual word from the solution_phrase inside prompt_text.\n"
            "Do not use a near-synonym, direct category label, obvious compound extension, or a signature part, tool, place, or accessory that makes the answer immediately obvious.\n"
            "For example, avoid clues that simply point to the answer's most iconic equipment, body part, habitat, or container when that would feel like a giveaway.\n"
            "Prefer a broader situational clue, behavior, role, or scene that requires one extra step of inference.\n"
            "Avoid using the exact theme word inside prompt_text when a related clue can imply it instead.\n"
            "Keep the clue varied and specific rather than repeating generic classroom wording.\n"
        )

    def _normalize_solution_phrase(self, solution_phrase: str) -> str:
        normalized = " ".join(solution_phrase.strip().split())
        if not normalized:
            raise GeminiGenerationError("Gemini response did not include both prompt_text and solution_phrase")
        if not re.fullmatch(r"[A-Za-z ]+", normalized):
            raise GeminiGenerationError(
                "Gemini returned an invalid solution phrase. The reveal answer must contain letters and spaces only."
            )
        if not re.search(r"[A-Za-z]", normalized):
            raise GeminiGenerationError("Gemini returned an invalid solution phrase with no letters.")
        return normalized

    def _build_review_notes(
        self,
        request: RewardContentGenerationRequest,
        *,
        model_note: str,
        reasoning_note: str | None = None,
        extra_notes: list[str] | None = None,
    ) -> list[str]:
        review_notes = [
            model_note,
            f"Theme: {request.theme}",
            f"Tone constraints: {', '.join(request.tone_constraints)}.",
        ]
        if extra_notes:
            review_notes.extend(extra_notes)
        if reasoning_note:
            review_notes.append(reasoning_note.strip())
        return review_notes

    def _style_guidance(self, preferred_style: str) -> str:
        style = preferred_style.strip().lower()
        if style == "pun":
            return (
                "STYLE REQUIREMENT: The prompt_text must be a pun or joke setup, not a plain descriptive riddle.\n"
                "Use playful wordplay, a joke-question format, or a light humorous twist connected to the related ideas.\n"
                "Do not fall back to a generic riddle, definition clue, or straightforward thematic question.\n"
            )
        if style == "question":
            return (
                "STYLE REQUIREMENT: The prompt_text must be a direct thematic question, not a pun and not a riddle with hidden metaphor.\n"
                "Use clear classroom wording that asks about the answer through related ideas.\n"
            )
        return (
            "STYLE REQUIREMENT: The prompt_text must read like a riddle with indirect clues and one clear answer.\n"
            "Do not write it as a joke setup or a plain factual classroom question.\n"
        )


def _preset_subject_word(preset_name: str | None) -> str:
    mapping = {
        "heart": "HEART",
        "star": "STAR",
        "moon": "MOON",
        "smile": "SMILE",
        "sun": "SUN",
        "flower": "FLOWER",
        "apple": "APPLE",
        "tree": "TREE",
        "christmas_tree": "EVERGREEN",
        "cat": "CAT",
        "clown_fish": "CLOWN FISH",
        "blue_tang": "BLUE TANG",
        "butterfly": "BUTTERFLY",
        "rocket": "ROCKET",
    }
    return mapping.get((preset_name or "").strip().lower(), "STAR")

class StubGeminiRewardContentGenerator:
    def generate(self, request: RewardContentGenerationRequest) -> RewardContentCandidate:
        style = (request.preferred_style or "riddle").strip().lower()
        theme = request.theme.strip()
        theme_phrase = theme if theme else "math"

        if style == "pun":
            prompt_text = f"Why did the {theme_phrase} bring a pencil to math class?"
            solution_phrase = f"{theme_phrase.title()} Time"
        elif style == "question":
            prompt_text = f"Which classroom idea matches the theme {theme_phrase}?"
            solution_phrase = theme_phrase.title()
        else:
            prompt_text = f"What classroom clue points to {theme_phrase}?"
            solution_phrase = theme_phrase.title()

        return RewardContentCandidate(
            prompt_text=prompt_text,
            solution_phrase=solution_phrase,
            theme=theme,
            source="gemini_stub",
            approval_state=ApprovalState.PENDING,
            style=style,
            language=request.language,
            review_notes=[
                f"Structured generation request for learner band {request.learner_band.value}.",
                f"Tone constraints: {', '.join(request.tone_constraints)}.",
            ],
        )

    def generate_for_solution(self, request: RewardContentGenerationRequest, solution_phrase: str) -> RewardContentCandidate:
        theme = request.theme.strip() or "math"
        style = (request.preferred_style or "riddle").strip().lower()
        normalized_solution = " ".join(solution_phrase.strip().split())
        if style == "question":
            prompt_text = f"Which classroom clue fits the topic {theme}?"
        elif style == "pun":
            prompt_text = f"What classroom hint would make someone think about {theme}?"
        else:
            prompt_text = f"What classroom clue points to this idea from {theme}?"

        return RewardContentCandidate(
            prompt_text=prompt_text,
            solution_phrase=normalized_solution,
            theme=theme,
            source="gemini_stub",
            approval_state=ApprovalState.PENDING,
            style=style,
            language=request.language,
            review_notes=[
                f"Structured fixed-solution generation request for learner band {request.learner_band.value}.",
                f"Tone constraints: {', '.join(request.tone_constraints)}.",
            ],
        )
