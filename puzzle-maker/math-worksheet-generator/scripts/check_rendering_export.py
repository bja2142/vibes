from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worksheet_generator.exporter import WorksheetExportService
from worksheet_generator.sample_data import build_color_by_number_sample, build_letter_bank_sample, build_pre_algebra_sample


def main() -> None:
    exporter = WorksheetExportService()
    fixtures = [
        ("letter_bank", build_letter_bank_sample()),
        ("color_by_number", build_color_by_number_sample()),
        ("pre_algebra", build_pre_algebra_sample()),
    ]

    output_dir = Path("/tmp/render-checks")
    for name, worksheet in fixtures:
        preview = exporter.export_variant(worksheet, output_dir, f"{name}-preview-check", solution=False)
        solution = exporter.export_variant(worksheet, output_dir, f"{name}-solution-check", solution=True)
        print(
            f"{name}: preview_fit={str(preview.rendered_page.metrics.content_fits_page).lower()} "
            f"solution_fit={str(solution.rendered_page.metrics.content_fits_page).lower()} "
            f"preview_bottom={preview.rendered_page.metrics.content_bottom} "
            f"solution_bottom={solution.rendered_page.metrics.content_bottom}"
        )


if __name__ == "__main__":
    main()
