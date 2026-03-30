from __future__ import annotations

from dataclasses import dataclass
from math import ceil, cos, radians, sin
import re
from textwrap import TextWrapper
from textwrap import dedent
from xml.sax.saxutils import escape

from .models import RevealMode, Worksheet
from .solution_phrase import solution_slot_count


BODY_FONT_SIZE = 18
BODY_LINE_HEIGHT = 26
SMALL_FONT_SIZE = 14
SMALL_LINE_HEIGHT = 20

SOLUTION_PALETTE = [
    "#f4c542",
    "#67a6d8",
    "#dd7c6b",
    "#6ba36f",
    "#8a6bb8",
    "#f29d52",
    "#5cb8a6",
    "#d96c9d",
    "#7d8cc4",
    "#9bb85d",
]

ARITHMETIC_PROMPT_RE = re.compile(r"^\s*(\d+)\s*([+\-x×*/])\s*(\d+)\s*=\s*\?\s*$")


def normalize_arithmetic_operator(operator: str) -> str:
    if operator in {"x", "×", "*"}:
        return "x"
    return operator


@dataclass(frozen=True)
class PreviewProblem:
    problem_id: str
    prompt: str
    answer: str
    reveal: str
    family: str
    slot: int | None = None
    metadata: dict[str, object] | None = None


@dataclass(frozen=True)
class RenderMetrics:
    worksheet_id: str
    learner_band: str
    reveal_mode: str
    solution: bool
    page_width: int
    page_height: int
    problem_count: int
    slot_count: int
    color_option_count: int
    content_bottom: int
    content_fits_page: bool
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class RenderedPage:
    title: str
    svg: str
    body: str
    metrics: RenderMetrics
    keep_together_segments: tuple[tuple[int, int], ...] = ()


@dataclass(frozen=True)
class ArithmeticLayoutMetrics:
    digit_count: int
    top_offset: int
    row_gap: int
    bar_gap: int
    workspace_height: int
    body_height: int


def sort_answer_value(value: str) -> tuple[int, str]:
    return (0, f"{int(value):08d}") if value.isdigit() else (1, value)


def estimate_text_width(text: str, *, font_size: int, mono: bool = False) -> int:
    if not text:
        return 0
    char_width = font_size * (0.62 if mono else 0.56)
    wide_bonus = sum(1 for char in text if char in "MWQ@#%&")
    return int(len(text) * char_width + wide_bonus * font_size * 0.12)


def answer_chip_width_for_card(*, answers: list[str], card_width: int, has_color_labels: bool) -> int:
    if not answers:
        return 0
    required = max(88, max(estimate_text_width(f"ans {answer}", font_size=11, mono=False) + 20 for answer in answers))
    max_width = max(88, card_width - (178 if has_color_labels else 144))
    return min(required, max_width)


def lookup_chip_width_for_rows(*, rows: list[tuple[str, str]], page_width: int) -> int:
    if not rows:
        return 150
    answer_width = max(estimate_text_width(answer, font_size=16, mono=True) for answer, _ in rows)
    token_width = max(estimate_text_width(token, font_size=18) for _, token in rows)
    required = max(150, answer_width + token_width + 67)
    max_width = max(150, page_width - 180)
    return min(required, max_width)


def parse_binary_arithmetic(problem: PreviewProblem) -> tuple[str, int, int] | None:
    metadata = problem.metadata or {}
    operator = metadata.get("operator")
    operands = metadata.get("operands")
    if isinstance(operator, str) and isinstance(operands, list) and len(operands) >= 2:
        try:
            return normalize_arithmetic_operator(operator), int(operands[0]), int(operands[1])
        except (TypeError, ValueError):
            pass
    match = ARITHMETIC_PROMPT_RE.fullmatch(problem.prompt.strip())
    if not match:
        return None
    left, operator, right = match.groups()
    return normalize_arithmetic_operator(operator), int(left), int(right)


def arithmetic_layout_kind(problem: PreviewProblem) -> tuple[str, int, int] | None:
    parsed = parse_binary_arithmetic(problem)
    if not parsed:
        return None
    operator, left, right = parsed
    max_digits = max(len(str(abs(left))), len(str(abs(right))))
    if operator == "+" and max_digits > 1:
        return "addition", left, right
    if operator == "-" and max_digits > 1:
        return "subtraction", left, right
    if operator == "x":
        return "multiplication", left, right
    if operator == "/" and max_digits > 1:
        return "division", left, right
    return None


def arithmetic_workspace_height(kind: str, left: int, right: int) -> int:
    max_digits = min(4, max(len(str(abs(left))), len(str(abs(right)))))
    blank_line_height = 24
    return max_digits * blank_line_height


def arithmetic_layout_metrics(kind: str, left: int, right: int) -> ArithmeticLayoutMetrics:
    digit_count = min(4, max(len(str(abs(left))), len(str(abs(right)))))
    top_offset = 14
    row_gap = 20 + digit_count
    bar_gap = 6
    workspace_height = arithmetic_workspace_height(kind, left, right)
    body_height = top_offset + row_gap + bar_gap + workspace_height + 10
    return ArithmeticLayoutMetrics(
        digit_count=digit_count,
        top_offset=top_offset,
        row_gap=row_gap,
        bar_gap=bar_gap,
        workspace_height=workspace_height,
        body_height=body_height,
    )


def arithmetic_body_height(kind: str, left: int, right: int) -> int:
    return arithmetic_layout_metrics(kind, left, right).body_height


def render_arithmetic_layout(kind: str, left: int, right: int, *, x: int, y: int, width: int) -> str:
    if kind == "division":
        return render_long_division(left, right, x=x, y=y, width=width)
    return render_vertical_operation(kind, left, right, x=x, y=y, width=width)


def render_vertical_operation(kind: str, left: int, right: int, *, x: int, y: int, width: int) -> str:
    right_x = x + width - 16
    digits = max(len(str(abs(left))), len(str(abs(right))))
    number_width = max(64, digits * 18)
    line_left = right_x - number_width - 6
    metrics = arithmetic_layout_metrics(kind, left, right)
    top_y = y + metrics.top_offset
    second_y = top_y + metrics.row_gap
    line_y = second_y + metrics.bar_gap
    operator = {
        "addition": "+",
        "subtraction": "-",
        "multiplication": "x",
    }[kind]
    parts = [
        f'<text class="mono" x="{right_x}" y="{top_y}" text-anchor="end" style="font-size: 24px;">{left}</text>',
        f'<text class="mono" x="{line_left}" y="{second_y}" style="font-size: 24px;">{operator}</text>',
        f'<text class="mono" x="{right_x}" y="{second_y}" text-anchor="end" style="font-size: 24px;">{right}</text>',
        f'<line x1="{line_left}" y1="{line_y}" x2="{right_x + 2}" y2="{line_y}" stroke="#8fa1af" stroke-width="2"/>',
    ]
    return "".join(parts)


def render_long_division(dividend: int, divisor: int, *, x: int, y: int, width: int) -> str:
    digits = max(len(str(abs(dividend))), len(str(abs(divisor))))
    work_width = max(96, digits * 20 + 48)
    bracket_x = x + 46
    metrics = arithmetic_layout_metrics("division", dividend, divisor)
    top_y = y + metrics.top_offset + 4
    bar_y = top_y - 18
    bracket_bottom_y = top_y + 8
    parts = [
        f'<text class="mono" x="{bracket_x - 10}" y="{top_y + 16}" text-anchor="end" style="font-size: 24px;">{divisor}</text>',
        f'<path d="M {bracket_x} {bar_y} h {work_width} M {bracket_x} {bar_y} v {bracket_bottom_y - bar_y}" fill="none" stroke="#8fa1af" stroke-width="2"/>',
        f'<text class="mono" x="{bracket_x + 14}" y="{top_y + 16}" style="font-size: 24px;">{dividend}</text>',
    ]
    return "".join(parts)


class WorksheetRenderer:
    def render(self, worksheet: Worksheet, *, solution: bool = False, semantic_foreground: bool = False) -> RenderedPage:
        profile_title = skill_profile_title(worksheet.spec.skill_profile)
        if worksheet.spec.reveal_mode == RevealMode.LETTER_BANK:
            title = f"{profile_title} Worksheet Solution" if solution else f"{profile_title} Worksheet"
            body, content_bottom, keep_together_segments = self._render_letter_bank(
                worksheet,
                title,
                solution=solution,
                semantic_foreground=semantic_foreground,
            )
        else:
            title = f"{profile_title} Worksheet Solution" if solution else f"{profile_title} Worksheet"
            body, content_bottom, keep_together_segments = self._render_color_by_number(
                worksheet,
                title,
                solution=solution,
                semantic_foreground=semantic_foreground,
            )

        metrics = self._build_metrics(worksheet, solution=solution, content_bottom=content_bottom)
        svg = svg_document(
            title=title,
            body=body,
            page_width=worksheet.spec.layout.page_width,
            page_height=worksheet.spec.layout.page_height,
            semantic_foreground=semantic_foreground,
        )
        return RenderedPage(
            title=title,
            svg=svg,
            body=body,
            metrics=metrics,
            keep_together_segments=tuple(keep_together_segments),
        )

    def _build_metrics(self, worksheet: Worksheet, *, solution: bool, content_bottom: int) -> RenderMetrics:
        slot_count = 0 if worksheet.spec.reveal_mode == RevealMode.COLOR_BY_NUMBER else solution_slot_count(worksheet.reward_content.solution_phrase)
        color_option_count = len(color_key_entries(worksheet)) if worksheet.spec.reveal_mode == RevealMode.COLOR_BY_NUMBER else 0
        fits_page = content_bottom <= worksheet.spec.layout.page_height
        warnings: list[str] = []
        if not fits_page:
            warnings.append("rendered content exceeds configured page height")

        return RenderMetrics(
            worksheet_id=worksheet.worksheet_id,
            learner_band=worksheet.spec.learner_band.value,
            reveal_mode=worksheet.spec.reveal_mode.value,
            solution=solution,
            page_width=worksheet.spec.layout.page_width,
            page_height=worksheet.spec.layout.page_height,
            problem_count=len(worksheet.problems),
            slot_count=slot_count,
            color_option_count=color_option_count,
            content_bottom=content_bottom,
            content_fits_page=fits_page,
            warnings=tuple(warnings),
        )

    def _render_letter_bank(
        self,
        worksheet: Worksheet,
        title: str,
        *,
        solution: bool,
        semantic_foreground: bool,
    ) -> tuple[str, int, list[tuple[int, int]]]:
        prompt = worksheet.reward_content.prompt_text
        answer = worksheet.reward_content.solution_phrase
        problems = preview_problems(worksheet)
        body_parts: list[str] = []
        keep_together_segments: list[tuple[int, int]] = []

        section, current_y, segment = render_header_with_note(
            page_width=worksheet.spec.layout.page_width,
            title=title,
            prompt=prompt,
            note=worksheet_prompt_note(problems),
            show_title=not semantic_foreground,
        )
        body_parts.append(section)
        keep_together_segments.append(segment)

        answer_colors: dict[str, str] = {}
        problem_colors: dict[str, str] = {}
        slot_colors: dict[int, str] = {}
        if solution:
            answer_colors, problem_colors, slot_colors = build_letter_bank_solution_colors(worksheet)

        section, current_y, row_segments = render_problem_cards(
            page_width=worksheet.spec.layout.page_width,
            problems=problems,
            start_y=current_y + 28,
            column_count=worksheet.spec.layout.problem_columns,
            show_answers=solution,
            answer_colors_by_problem_id=problem_colors,
        )
        body_parts.append(section)
        keep_together_segments.extend(row_segments)

        section, current_y, segment = render_letter_lookup(
            page_width=worksheet.spec.layout.page_width,
            rows=letter_lookup_rows(worksheet),
            start_y=current_y + 26,
            highlighted_answers=answer_colors if solution else None,
        )
        body_parts.append(section)
        keep_together_segments.append(segment)

        section, current_y, segment = render_answer_slots(
            page_width=worksheet.spec.layout.page_width,
            answer=answer,
            start_y=current_y + 24,
            filled_letters=solution,
            show_slot_labels=worksheet.spec.layout.show_slot_labels,
            slot_fill_colors=slot_colors if solution else None,
        )
        body_parts.append(section)
        keep_together_segments.append(segment)

        return "".join(body_parts), current_y, keep_together_segments

    def _render_color_by_number(
        self,
        worksheet: Worksheet,
        title: str,
        *,
        solution: bool,
        semantic_foreground: bool,
    ) -> tuple[str, int, list[tuple[int, int]]]:
        prompt = worksheet.reward_content.prompt_text
        problems = preview_problems(worksheet)
        color_map = color_key_entries(worksheet)
        body_parts: list[str] = []
        keep_together_segments: list[tuple[int, int]] = []

        section, current_y, segment = render_header_with_note(
            page_width=worksheet.spec.layout.page_width,
            title=title,
            prompt=prompt,
            note=worksheet_prompt_note(problems),
            show_title=not semantic_foreground,
        )
        body_parts.append(section)
        keep_together_segments.append(segment)

        section, current_y, row_segments = render_problem_cards(
            page_width=worksheet.spec.layout.page_width,
            problems=problems,
            start_y=current_y + 28,
            column_count=worksheet.spec.layout.problem_columns,
            show_answers=solution,
            swatch_by_problem_id={problem_id: swatch for _, _, swatch, problem_id in color_map},
            swatch_label_by_problem_id={problem_id: label for _, label, _, problem_id in color_map},
        )
        body_parts.append(section)
        keep_together_segments.extend(row_segments)

        section, current_y, segment = render_color_visuals(
            worksheet=worksheet,
            color_map=color_map,
            problems=problems,
            start_y=current_y + 28,
            max_color_options=worksheet.spec.layout.max_color_options,
            solved=solution,
        )
        body_parts.append(section)
        keep_together_segments.append(segment)

        return "".join(body_parts), current_y, keep_together_segments


def preview_problems(worksheet: Worksheet) -> list[PreviewProblem]:
    solved_problems = worksheet.solved_problem_map()
    assignments = worksheet.assignment_map()
    if worksheet.spec.reveal_mode == RevealMode.COLOR_BY_NUMBER:
        ordered_problems = list(worksheet.problems)
    else:
        problem_lookup = worksheet.problem_map()
        ordered_problems = [
            problem_lookup[assignment.problem_id]
            for assignment in worksheet.slot_assignments()
        ]
    return [
        PreviewProblem(
            problem_id=problem.problem_id,
            prompt=problem.prompt,
            answer=solved_problems[problem.problem_id].normalized_answer,
            reveal=assignments[problem.problem_id].reveal_token,
            family=problem.family,
            slot=assignments[problem.problem_id].answer_slot_index,
            metadata=problem.metadata,
        )
        for problem in ordered_problems
    ]


def worksheet_prompt_note(problems: list[PreviewProblem]) -> str | None:
    if any((problem.metadata or {}).get("template") == "quadratic_smaller_root" for problem in problems):
        return "Note: If a problem has two roots, give the smaller integer root."
    return None


def letter_lookup_rows(worksheet: Worksheet) -> list[tuple[str, str]]:
    rows = sorted(
        {(assignment.answer_value, assignment.reveal_token.upper()) for assignment in worksheet.letter_assignments},
        key=lambda item: sort_answer_value(item[0]),
    )
    return list(rows)


def color_key_entries(worksheet: Worksheet) -> list[tuple[str, str, str, str]]:
    palette = worksheet.spec.layout.color_palette
    if len(palette) > worksheet.spec.layout.max_color_options:
        raise ValueError("color palette exceeds supported max_color_options")

    unique_entries: dict[str, tuple[str, str, str, str]] = {}
    for assignment in worksheet.letter_assignments:
        if assignment.answer_value not in unique_entries:
            unique_entries[assignment.answer_value] = (
                assignment.answer_value,
                assignment.reveal_token,
                palette.get(assignment.reveal_token, "#d9e2ea"),
                assignment.problem_id,
            )

    return sorted(unique_entries.values(), key=lambda item: sort_answer_value(item[0]))


def build_letter_bank_solution_colors(worksheet: Worksheet) -> tuple[dict[str, str], dict[str, str], dict[int, str]]:
    token_colors: dict[tuple[str, str], str] = {}
    answer_colors: dict[str, str] = {}
    problem_colors: dict[str, str] = {}
    slot_colors: dict[int, str] = {}

    for assignment in worksheet.slot_assignments():
        token_key = (assignment.reveal_token, assignment.answer_value)
        if token_key not in token_colors:
            token_colors[token_key] = SOLUTION_PALETTE[len(token_colors) % len(SOLUTION_PALETTE)]
        color = token_colors[token_key]
        answer_colors[assignment.answer_value] = color
        problem_colors[assignment.problem_id] = color
        if assignment.answer_slot_index is not None:
            slot_colors[assignment.answer_slot_index] = color

    return answer_colors, problem_colors, slot_colors


def wrap_text(text: str, *, max_width: int, font_size: int) -> list[str]:
    approx_char_width = max(font_size * 0.58, 1)
    width = max(8, int(max_width / approx_char_width))
    wrapper = TextWrapper(width=width, break_long_words=False, break_on_hyphens=False)
    return wrapper.wrap(text) or [text]


def render_text_block(x: int, y: int, lines: list[str], class_name: str, line_height: int) -> str:
    tspans = []
    for index, line in enumerate(lines):
        dy = 0 if index == 0 else line_height
        tspans.append(f'<tspan x="{x}" dy="{dy}">{escape(line)}</tspan>')
    return f'<text class="{class_name}" x="{x}" y="{y}">{"".join(tspans)}</text>'


def svg_document(*, title: str, body: str, page_width: int, page_height: int, semantic_foreground: bool = False) -> str:
    foreground_css = (
        """
            .page { fill: none; }
            .card { fill: none; }
            .chip { fill: none; }
            .slot { fill: none; }
            rect[fill="#ffffff"],
            rect[fill="#fbfcfd"],
            rect[fill="#eef4f7"] { fill: none !important; }
        """
        if semantic_foreground
        else ""
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{page_width}" '
        f'height="{page_height}" viewBox="0 0 {page_width} {page_height}">'
        f"<title>{escape(title)}</title>"
        f"""
        <style>
            .page {{ fill: #ffffff; }}
            .title {{ font: 700 34px 'DejaVu Sans', sans-serif; fill: #101418; }}
            .subtitle {{ font: 400 18px 'DejaVu Sans', sans-serif; fill: #44515c; }}
            .section {{ font: 700 20px 'DejaVu Sans', sans-serif; fill: #101418; }}
            .body {{ font: 400 18px 'DejaVu Sans', sans-serif; fill: #1d2b36; }}
            .small {{ font: 400 14px 'DejaVu Sans', sans-serif; fill: #5c6b77; }}
            .tiny {{ font: 400 11px 'DejaVu Sans', sans-serif; fill: #5c6b77; }}
            .mono {{ font: 700 16px 'DejaVu Sans Mono', monospace; fill: #162029; }}
            .card {{ fill: #fbfcfd; stroke: #d2dbe2; stroke-width: 2; rx: 18; }}
            .line {{ stroke: #d2dbe2; stroke-width: 2; }}
            .slot {{ fill: #ffffff; stroke: #8fa1af; stroke-width: 2; rx: 12; }}
            .chip {{ fill: #eef4f7; stroke: #d2dbe2; stroke-width: 1.5; rx: 10; }}
            {foreground_css}
        </style>
        <rect class="page" width="100%" height="100%"/>
        {body}
        </svg>
        """
    )

def render_header_with_note(
    *,
    page_width: int,
    title: str,
    prompt: str,
    note: str | None,
    start_y: int = 76,
    show_title: bool = True,
) -> tuple[str, int, tuple[int, int]]:
    prompt_lines = wrap_text(prompt, max_width=page_width - 180, font_size=BODY_FONT_SIZE)
    note_lines = wrap_text(note, max_width=page_width - 180, font_size=SMALL_FONT_SIZE) if note else []
    card_y = start_y + 46
    prompt_y = card_y + 58
    note_y = prompt_y + len(prompt_lines) * 22 + 10 if note_lines else prompt_y
    card_height = 38 + len(prompt_lines) * 22 + (len(note_lines) * 18 + 14 if note_lines else 0) + 18
    card_width = page_width - 120

    title_markup = f'<text class="title" x="70" y="{start_y}">{escape(title)}</text>' if show_title else ""
    body = dedent(
        f"""
        {title_markup}
        <rect class="card" x="60" y="{card_y}" width="{card_width}" height="{card_height}"/>
        <text class="section" x="90" y="{card_y + 32}">Prompt</text>
        """
    )
    body += render_text_block(90, prompt_y, prompt_lines, "body", 22)
    if note_lines:
        body += render_text_block(90, note_y, note_lines, "small", 18)
    return body, card_y + card_height, (start_y, card_y + card_height)


def render_problem_cards(
    *,
    page_width: int,
    problems: list[PreviewProblem],
    start_y: int,
    column_count: int,
    show_answers: bool = False,
    answer_colors_by_problem_id: dict[str, str] | None = None,
    swatch_by_problem_id: dict[str, str] | None = None,
    swatch_label_by_problem_id: dict[str, str] | None = None,
) -> tuple[str, int, list[tuple[int, int]]]:
    parts: list[str] = [f'<text class="section" x="70" y="{start_y}">Questions</text>']
    row_segments: list[tuple[int, int]] = []
    card_top = start_y + 18
    gap_x = 40
    gap_y = 12
    base_x = 60
    usable_width = page_width - 120
    has_diagrams = any(problem.metadata and problem.metadata.get("diagram_kind") for problem in problems)
    has_color_labels = bool(swatch_label_by_problem_id)
    if has_diagrams or has_color_labels:
        column_count = min(column_count, 2)
    column_count = max(1, column_count)
    card_width = int((usable_width - gap_x * (column_count - 1)) / column_count)
    rows_per_column = ceil(len(problems) / column_count)

    any_color_labels = bool(swatch_by_problem_id)
    answers = [problem.answer for problem in problems] if show_answers else []
    answer_chip_width = answer_chip_width_for_card(
        answers=answers,
        card_width=card_width,
        has_color_labels=any_color_labels,
    ) if show_answers else 0
    prompt_wrap_width = max(card_width - 70 - answer_chip_width, 120)
    arithmetic_layouts = [arithmetic_layout_kind(problem) for problem in problems]
    wrapped_prompts = [
        None if arithmetic_layouts[index] else wrap_text(problem_text_body(problem.prompt), max_width=prompt_wrap_width, font_size=BODY_FONT_SIZE)
        for index, problem in enumerate(problems)
    ]
    body_heights = [
        arithmetic_body_height(layout[0], layout[1], layout[2]) if layout else max(len(lines or []), 1) * 20
        for layout, lines in zip(arithmetic_layouts, wrapped_prompts)
    ]
    color_label_height = 22 if swatch_label_by_problem_id else 0
    card_heights = [
        44
        + color_label_height
        + body_heights[index]
        + (BODY_LINE_HEIGHT if "equation" in problems[index].family else 0)
        + (136 if problems[index].metadata and problems[index].metadata.get("diagram_kind") else 0)
        for index in range(len(problems))
    ]
    row_heights = [0] * rows_per_column
    for index, height in enumerate(card_heights):
        row = index % rows_per_column
        row_heights[row] = max(row_heights[row], height)
    row_offsets: list[int] = []
    current_offset = 0
    for row_height in row_heights:
        row_offsets.append(current_offset)
        current_offset += row_height + gap_y

    for index, problem in enumerate(problems):
        prompt_lines = wrapped_prompts[index]
        arithmetic_layout = arithmetic_layouts[index]
        column = index // rows_per_column
        row = index % rows_per_column
        x = base_x + column * (card_width + gap_x)
        card_height = card_heights[index]
        y = card_top + row_offsets[row]
        instruction = problem_instruction(problem)
        parts.append(
            dedent(
                f"""
                <rect class="card" x="{x}" y="{y}" width="{card_width}" height="{card_height}"/>
                <text class="mono" x="{x + 18}" y="{y + 24}">Q{index + 1:02d} - {escape(instruction)}:</text>
                """
            )
        )
        has_color_label = bool(swatch_by_problem_id and problem.problem_id in swatch_by_problem_id)
        if has_color_label:
            swatch = swatch_by_problem_id[problem.problem_id]
            parts.append(
                f'<rect x="{x + card_width - 54}" y="{y + 8}" width="24" height="24" fill="{swatch}" stroke="#94a4b1" stroke-width="1.5" rx="6"/>'
            )
        if show_answers:
            answer_color = (answer_colors_by_problem_id or {}).get(problem.problem_id, "#eef4f7")
            chip_width = answer_chip_width_for_card(
                answers=[problem.answer],
                card_width=card_width,
                has_color_labels=has_color_label,
            )
            chip_x = x + card_width - (chip_width + (64 if has_color_label else 30))
            parts.append(
                dedent(
                    f"""
                    <rect x="{chip_x}" y="{y + 8}" width="{chip_width}" height="24" fill="{answer_color}" fill-opacity="0.28" stroke="{answer_color}" stroke-width="2" rx="10"/>
                    <text class="tiny" x="{chip_x + 10}" y="{y + 24}">ans {escape(problem.answer)}</text>
                    """
                )
            )
        if swatch_label_by_problem_id and problem.problem_id in swatch_label_by_problem_id:
            color_label = swatch_label_by_problem_id[problem.problem_id]
            parts.append(
                f'<text class="small" x="{x + 18}" y="{y + 48}">Color: {escape(color_label)}</text>'
            )
        prompt_y = y + 48 + color_label_height
        if arithmetic_layout:
            parts.append(
                render_arithmetic_layout(
                    arithmetic_layout[0],
                    arithmetic_layout[1],
                    arithmetic_layout[2],
                    x=x + 18,
                    y=prompt_y,
                    width=card_width - 36,
                )
            )
            body_bottom = prompt_y + arithmetic_body_height(arithmetic_layout[0], arithmetic_layout[1], arithmetic_layout[2])
        else:
            parts.append(render_text_block(x + 18, prompt_y, prompt_lines or [problem_text_body(problem.prompt)], "body", 20))
            body_bottom = prompt_y + max(len(prompt_lines or []), 1) * 20
        if problem.metadata and problem.metadata.get("diagram_kind"):
            diagram_y = body_bottom + 8
            parts.append(render_problem_diagram(problem.metadata, x + 20, diagram_y, card_width - 40))
        row_bottom = y + card_height
        if len(row_segments) <= row:
            row_segments.append((y, row_bottom))
        else:
            existing_top, existing_bottom = row_segments[row]
            row_segments[row] = (min(existing_top, y), max(existing_bottom, row_bottom))

    row_count = min(len(problems), rows_per_column)
    end_y = card_top + sum(row_heights[:row_count]) + max(0, row_count - 1) * gap_y
    return "".join(parts), end_y, row_segments


def render_problem_diagram(metadata: dict[str, object], x: int, y: int, width: int) -> str:
    diagram_kind = str(metadata.get("diagram_kind", ""))
    if diagram_kind == "rectangle":
        rect_x = x + max(8, (width - 180) // 2)
        rect_y = y + 18
        center_x = rect_x + 90
        center_y = rect_y + 31
        return dedent(
            f"""
            <rect x="{rect_x}" y="{rect_y}" width="180" height="62" fill="#ffffff" stroke="#8fa1af" stroke-width="2" rx="8"/>
            <text class="small" x="{center_x}" y="{rect_y - 12}" text-anchor="middle">{escape(str(metadata.get("known_label", "")))}</text>
            <text class="small" x="{rect_x - 12}" y="{center_y + 5}" text-anchor="middle">{escape(str(metadata.get("missing_label", "")))}</text>
            <text class="small" x="{center_x}" y="{center_y + 5}" text-anchor="middle">{escape(str(metadata.get("interior_label", "")))}</text>
            """
        )
    if diagram_kind == "right_triangle":
        base_x = x + max(8, (width - 220) // 2)
        base_y = y + 98
        hyp_center_x = base_x + 74
        hyp_center_y = base_y - 52
        left_angle_degrees = 28
        theta_radius = 22
        theta_x = base_x + theta_radius * cos(radians(left_angle_degrees / 2)) + 22
        theta_y = base_y - theta_radius * sin(radians(left_angle_degrees / 2))
        return dedent(
            f"""
            <path d="M {base_x} {base_y} L {base_x + 150} {base_y} L {base_x + 150} {base_y - 80} Z" fill="#ffffff" stroke="#8fa1af" stroke-width="2"/>
            <path d="M {base_x + 132} {base_y} L {base_x + 132} {base_y - 18} L {base_x + 150} {base_y - 18}" fill="none" stroke="#8fa1af" stroke-width="2"/>
            <text class="small" x="{base_x + 75}" y="{base_y + 18}" text-anchor="middle">{escape(str(metadata.get("base_label", "")))}</text>
            <text class="small" x="{base_x + 176}" y="{base_y - 38}" text-anchor="middle">{escape(str(metadata.get("vertical_label", "")))}</text>
            <text class="small" x="{hyp_center_x}" y="{hyp_center_y}" text-anchor="middle" transform="rotate(-28 {hyp_center_x} {hyp_center_y})">{escape(str(metadata.get("hypotenuse_label", "")))}</text>
            <text class="body" x="{theta_x}" y="{theta_y}" text-anchor="middle" style="font-size: 14px; font-weight: 700;">{escape(str(metadata.get("angle_label", "")))}</text>
            """
        )
    return ""


def render_letter_lookup(
    *,
    page_width: int,
    rows: list[tuple[str, str]],
    start_y: int,
    highlighted_answers: dict[str, str] | None = None,
) -> tuple[str, int, tuple[int, int]]:
    chip_gap = 30
    chip_width = lookup_chip_width_for_rows(rows=rows, page_width=page_width)
    chip_columns = max(1, int((page_width - 180 + chip_gap) / (chip_width + chip_gap)))
    chip_rows = ceil(len(rows) / chip_columns)
    card_height = 30 + chip_rows * 42 + 16
    parts = [
        dedent(
            f"""
            <text class="section" x="70" y="{start_y}">Letter Lookup</text>
            <rect class="card" x="60" y="{start_y + 18}" width="{page_width - 120}" height="{card_height}"/>
            """
        )
    ]
    chip_x = 90
    chip_y = start_y + 42

    for index, (answer, letter) in enumerate(rows):
        x = chip_x + (index % chip_columns) * (chip_width + chip_gap)
        y = chip_y + (index // chip_columns) * 42
        parts.append(
            dedent(
                f"""
                <rect class="chip" x="{x}" y="{y}" width="{chip_width}" height="34"/>
                <text class="mono" x="{x + 16}" y="{y + 23}">{answer}</text>
                <text class="body" x="{x + chip_width - 16}" y="{y + 23}" text-anchor="end">{escape(letter)}</text>
                """
            )
        )
        if highlighted_answers and answer in highlighted_answers:
            parts.append(
                f'<ellipse cx="{x + 75}" cy="{y + 17}" rx="80" ry="23" fill="none" stroke="{highlighted_answers[answer]}" stroke-width="3"/>'
            )

    bottom = start_y + 18 + card_height
    return "".join(parts), bottom, (start_y, bottom)


def render_answer_slots(
    *,
    page_width: int,
    answer: str,
    start_y: int,
    filled_letters: bool = False,
    show_slot_labels: bool = True,
    slot_fill_colors: dict[int, str] | None = None,
) -> tuple[str, int, tuple[int, int]]:
    parts = [
        dedent(
            f"""
            <text class="section" x="70" y="{start_y}">Final Answer</text>
            """
        )
    ]
    left_x = 70
    x = left_x
    y = start_y + 26
    slot_width = 58
    slot_height = 62
    max_x = page_width - 70
    slot_number = 1
    slot_index = 0

    for character in answer:
        if character == " ":
            if x + 30 > max_x:
                x = left_x
                y += 92
            x += 24
            continue
        if not character.isalpha():
            continue
        if x + slot_width > max_x:
            x = left_x
            y += 92
        slot_color = (slot_fill_colors or {}).get(slot_index)
        if slot_color:
            parts.append(
                f'<rect x="{x}" y="{y}" width="{slot_width}" height="{slot_height}" fill="{slot_color}" fill-opacity="0.28" stroke="{slot_color}" stroke-width="2" rx="12"/>'
            )
        else:
            parts.append(f'<rect class="slot" x="{x}" y="{y}" width="{slot_width}" height="{slot_height}"/>')
        if filled_letters:
            display_character = character.upper()
            parts.append(
                f'<text class="section" x="{x + (slot_width / 2)}" y="{y + 44}" text-anchor="middle" '
                f'style="font-size: 24px;">{escape(display_character)}</text>'
            )
        if show_slot_labels:
            parts.append(
                f'<text class="tiny" x="{x + (slot_width / 2)}" y="{y + slot_height + 13}" text-anchor="middle">{slot_number}</text>'
            )
        x += slot_width + 12
        slot_number += 1
        slot_index += 1

    bottom = y + slot_height + (18 if show_slot_labels else 8)
    return "".join(parts), bottom, (start_y, bottom)


def render_color_grid(
    *,
    worksheet: Worksheet,
    color_map: list[tuple[str, str, str, str]],
    problems: list[PreviewProblem],
    start_y: int,
    page_width: int,
    solved: bool = False,
) -> tuple[str, int, tuple[int, int]]:
    grid_cells = worksheet.spec.layout.color_grid_cells
    grid_size = worksheet.spec.layout.color_grid_size or len(grid_cells)
    if not grid_cells or grid_size <= 0:
        raise ValueError("color-by-number worksheet is missing square grid data")
    swatches = {label: swatch for _, label, swatch, _ in color_map}
    answers_by_label = {problem.reveal: problem.answer for problem in problems}
    tile_size = max(8, min(28, int((page_width - 180) / grid_size)))
    grid_width = grid_size * tile_size
    card_width = grid_width + 40
    card_x = max(60, int((page_width - card_width) / 2))
    grid_x = card_x + 20
    grid_y = start_y + 46
    grid_height = grid_size * tile_size
    card_height = grid_height + 62
    heading = worksheet.spec.layout.color_grid_name or "Reveal Grid"
    parts = [
        dedent(
            f"""
            <text class="section" x="70" y="{start_y}">{escape(heading.title())}</text>
            <rect class="card" x="{card_x}" y="{start_y + 18}" width="{card_width}" height="{card_height}"/>
            """
        )
    ]
    font_size = max(4, int(tile_size * 0.52) - 2)
    text_y_adjust = max(4, int(tile_size * 0.34))
    for row_index, row in enumerate(grid_cells):
        for column_index, label in enumerate(row):
            x = grid_x + column_index * tile_size
            y = grid_y + row_index * tile_size
            fill = swatches.get(label, "#d9e2ea") if solved else "#ffffff"
            fill_opacity = "0.92" if solved else "1.0"
            parts.append(
                f'<rect x="{x}" y="{y}" width="{tile_size}" height="{tile_size}" fill="{fill}" fill-opacity="{fill_opacity}" stroke="#9caab5" stroke-width="1"/>'
            )
            if not solved:
                answer = answers_by_label.get(label, "")
                parts.append(
                    f'<text class="mono" x="{x + (tile_size / 2)}" y="{y + (tile_size / 2) + text_y_adjust}" text-anchor="middle" style="font-size: {font_size}px;">{escape(answer)}</text>'
                )

    bottom = start_y + 18 + card_height
    return "".join(parts), bottom, (start_y, bottom)


def problem_instruction(problem: PreviewProblem) -> str:
    if "equation" in problem.family:
        return "Solve for x"
    normalized = " ".join(problem.prompt.split())
    if ":" in normalized:
        prefix = normalized.split(":", 1)[0].strip()
        if prefix:
            return prefix
    return "Solve"


def problem_text_body(prompt: str) -> str:
    normalized = " ".join(prompt.split())
    if ":" in normalized:
        _, remainder = normalized.split(":", 1)
        body = remainder.strip()
        if body:
            normalized = body
    repeated_note = "Give the smaller integer root."
    if repeated_note in normalized:
        normalized = normalized.replace(repeated_note, "").strip()
        normalized = normalized.rstrip(". ").strip()
    return normalized


def skill_profile_title(skill_profile: str) -> str:
    titles = {
        "mixed_operations": "Mixed Operations",
        "mixed_skills": "Mixed Skills",
        "subtraction_and_addition": "Addition and Subtraction",
        "addition": "Addition",
        "subtraction": "Subtraction",
        "multiplication": "Multiplication",
        "division": "Division",
        "multiplication_focus": "Multiplication",
        "division_focus": "Division",
        "algebra": "Algebra",
        "geometry": "Geometry and Trigonometry",
        "trigonometry": "Trigonometry",
        "pre_algebra_equations": "Pre-Algebra",
        "algebraic_equations": "Algebra",
    }
    return titles.get(skill_profile, skill_profile.replace("_", " ").title())


def render_color_visuals(
    *,
    worksheet: Worksheet,
    color_map: list[tuple[str, str, str, str]],
    problems: list[PreviewProblem],
    start_y: int,
    max_color_options: int,
    solved: bool = False,
) -> tuple[str, int, tuple[int, int]]:
    grid_section, grid_bottom, segment = render_color_grid(
        worksheet=worksheet,
        color_map=color_map,
        problems=problems,
        start_y=start_y,
        page_width=worksheet.spec.layout.page_width,
        solved=solved,
    )
    return grid_section, grid_bottom, segment
