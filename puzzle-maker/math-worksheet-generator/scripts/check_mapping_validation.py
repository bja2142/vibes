from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worksheet_generator.sample_data import build_mapping_validation_demo_report


def main() -> None:
    report = build_mapping_validation_demo_report()
    print(f"attempts_used={report['attempts_used']}")
    print(f"is_valid={report['is_valid']}")
    print(f"reconstructed_answer={report['reconstructed_answer']}")
    print(f"distinct_letter_answer_map={report['distinct_letter_answer_map']}")
    print(f"distractor_count={len(report['distractors'])}")
    print(f"warnings={report['warnings']}")


if __name__ == "__main__":
    main()
