from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import cairosvg
from PIL import Image
from pypdf import PdfReader, PdfWriter

from .models import RenderedWorksheet, Worksheet
from .rendering import RenderMetrics, RenderedPage, WorksheetRenderer, svg_document


@dataclass(frozen=True)
class ExportedVariant:
    variant_name: str
    rendered_page: RenderedPage
    outputs: tuple[RenderedWorksheet, ...]


class WorksheetExportService:
    PDF_CONTINUATION_TOP_MARGIN = 56
    PDF_BOTTOM_MARGIN = 56
    PDF_CONTINUATION_BLEED = 2

    def __init__(self, renderer: WorksheetRenderer | None = None) -> None:
        self._renderer = renderer or WorksheetRenderer()

    def export_variant(
        self,
        worksheet: Worksheet,
        output_dir: Path,
        stem: str,
        *,
        solution: bool = False,
    ) -> ExportedVariant:
        output_dir.mkdir(parents=True, exist_ok=True)
        rendered_page = self._renderer.render(worksheet, solution=solution)
        export_height = self._export_height(rendered_page)
        export_svg = svg_document(
            title=rendered_page.title,
            body=rendered_page.body,
            page_width=rendered_page.metrics.page_width,
            page_height=export_height,
        )
        svg_path = output_dir / f"{stem}.svg"
        png_path = output_dir / f"{stem}.png"
        pdf_path = output_dir / f"{stem}.pdf"

        svg_path.write_text(export_svg, encoding="utf-8")
        cairosvg.svg2png(bytestring=export_svg.encode("utf-8"), write_to=str(png_path))
        self._write_pdf(rendered_page, pdf_path, export_height)

        return ExportedVariant(
            variant_name="solution" if solution else "preview",
            rendered_page=rendered_page,
            outputs=(
                RenderedWorksheet(worksheet_id=worksheet.worksheet_id, output_format="svg", output_path=str(svg_path)),
                RenderedWorksheet(worksheet_id=worksheet.worksheet_id, output_format="png", output_path=str(png_path)),
                RenderedWorksheet(worksheet_id=worksheet.worksheet_id, output_format="pdf", output_path=str(pdf_path)),
            ),
        )

    def export_semantic_foreground_variant(
        self,
        worksheet: Worksheet,
        output_dir: Path,
        stem: str,
        *,
        solution: bool = False,
    ) -> ExportedVariant:
        output_dir.mkdir(parents=True, exist_ok=True)
        rendered_page = self._renderer.render(worksheet, solution=solution, semantic_foreground=True)
        export_height = self._export_height(rendered_page)
        export_svg = svg_document(
            title=rendered_page.title,
            body=rendered_page.body,
            page_width=rendered_page.metrics.page_width,
            page_height=export_height,
            semantic_foreground=True,
        )
        svg_path = output_dir / f"{stem}.svg"
        png_path = output_dir / f"{stem}.png"

        svg_path.write_text(export_svg, encoding="utf-8")
        cairosvg.svg2png(bytestring=export_svg.encode("utf-8"), write_to=str(png_path))

        return ExportedVariant(
            variant_name="semantic_foreground_solution" if solution else "semantic_foreground_preview",
            rendered_page=rendered_page,
            outputs=(
                RenderedWorksheet(worksheet_id=worksheet.worksheet_id, output_format="svg", output_path=str(svg_path)),
                RenderedWorksheet(worksheet_id=worksheet.worksheet_id, output_format="png", output_path=str(png_path)),
            ),
        )

    def _export_height(self, rendered_page: RenderedPage) -> int:
        return max(rendered_page.metrics.page_height, rendered_page.metrics.content_bottom + 36)

    def _write_pdf(self, rendered_page: RenderedPage, pdf_path: Path, export_height: int) -> None:
        page_width = rendered_page.metrics.page_width
        page_height = rendered_page.metrics.page_height
        if export_height <= page_height:
            cairosvg.svg2pdf(bytestring=rendered_page.svg.encode("utf-8"), write_to=str(pdf_path))
            return

        writer = PdfWriter()
        offsets = self._page_offsets(rendered_page, export_height)
        page_windows = tuple(zip(offsets, offsets[1:] + (export_height,)))
        for index, (start, end) in enumerate(page_windows):
            clipped_svg = self._page_svg(
                rendered_page=rendered_page,
                page_index=index,
                start=start,
                end=end,
                page_width=page_width,
                page_height=page_height,
            )
            buffer = BytesIO()
            cairosvg.svg2pdf(bytestring=clipped_svg.encode("utf-8"), write_to=buffer)
            reader = PdfReader(buffer)
            writer.add_page(reader.pages[0])

        with pdf_path.open("wb") as handle:
            writer.write(handle)

    def _page_offsets(self, rendered_page: RenderedPage, export_height: int) -> tuple[int, ...]:
        page_height = rendered_page.metrics.page_height
        first_page_limit = page_height - self.PDF_BOTTOM_MARGIN
        continuation_capacity = page_height - self.PDF_CONTINUATION_TOP_MARGIN - self.PDF_BOTTOM_MARGIN
        segments = sorted(rendered_page.keep_together_segments)
        if not segments:
            return tuple(range(0, export_height, page_height))

        offsets = [0]
        current_offset = 0
        current_limit = first_page_limit

        for top, bottom in segments:
            if bottom <= current_limit:
                continue

            next_offset = max(0, top)
            if next_offset <= current_offset:
                next_offset = current_limit
            offsets.append(next_offset)
            current_offset = next_offset
            current_limit = current_offset + continuation_capacity

        return tuple(offsets)

    def _page_translate_y(self, offset: int) -> int:
        if offset <= 0:
            return 0
        return self.PDF_CONTINUATION_TOP_MARGIN - offset

    def _page_svg(
        self,
        *,
        rendered_page: RenderedPage,
        page_index: int,
        start: int,
        end: int,
        page_width: int,
        page_height: int,
    ) -> str:
        top_margin = 0 if start <= 0 else self.PDF_CONTINUATION_TOP_MARGIN
        content_y = top_margin if start <= 0 else top_margin + self.PDF_CONTINUATION_BLEED
        available_height = page_height - content_y - self.PDF_BOTTOM_MARGIN
        visible_height = min(max(0, end - start), available_height)
        if end < rendered_page.metrics.content_bottom:
            visible_height = max(0, visible_height - 20)
        body = (
            f'<rect x="0" y="0" width="{page_width}" height="{content_y}" fill="#ffffff"/>'
            f'<svg x="0" y="{content_y}" width="{page_width}" height="{visible_height}" '
            f'viewBox="0 {start} {page_width} {visible_height}" overflow="hidden">{rendered_page.body}</svg>'
            f'<rect x="0" y="{content_y + visible_height}" width="{page_width}" '
            f'height="{page_height - (content_y + visible_height)}" fill="#ffffff"/>'
        )
        return svg_document(
            title=rendered_page.title,
            body=body,
            page_width=page_width,
            page_height=page_height,
        )

    def export_preview_and_solution(
        self,
        worksheet: Worksheet,
        output_dir: Path,
        *,
        preview_stem: str,
        solution_stem: str,
    ) -> tuple[ExportedVariant, ExportedVariant]:
        preview = self.export_variant(worksheet, output_dir, preview_stem, solution=False)
        solution = self.export_variant(worksheet, output_dir, solution_stem, solution=True)
        return preview, solution

    def composite_styled_variant(
        self,
        *,
        styled_image_bytes: bytes,
        foreground_png_bytes: bytes,
        output_path: Path,
    ) -> Path:
        background = Image.open(BytesIO(styled_image_bytes)).convert("RGBA")
        foreground = Image.open(BytesIO(foreground_png_bytes)).convert("RGBA")
        if background.size != foreground.size:
            raise ValueError(f"foreground size {foreground.size} does not match background size {background.size}")
        composited = Image.alpha_composite(background, foreground)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        composited.save(output_path, format="PNG")
        return output_path


def metrics_to_dict(metrics: RenderMetrics) -> dict[str, object]:
    return {
        "worksheet_id": metrics.worksheet_id,
        "learner_band": metrics.learner_band,
        "reveal_mode": metrics.reveal_mode,
        "solution": metrics.solution,
        "page_width": metrics.page_width,
        "page_height": metrics.page_height,
        "problem_count": metrics.problem_count,
        "slot_count": metrics.slot_count,
        "color_option_count": metrics.color_option_count,
        "content_bottom": metrics.content_bottom,
        "content_fits_page": metrics.content_fits_page,
        "warnings": list(metrics.warnings),
    }
