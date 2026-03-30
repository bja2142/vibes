from __future__ import annotations

from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent


def run_step(*args: str) -> None:
    print(f"+ {' '.join(args)}")
    subprocess.run(args, cwd=ROOT, check=True)


def main() -> None:
    run_step(sys.executable, "-m", "compileall", "worksheet_generator", "scripts", "tests")
    run_step(sys.executable, "-m", "pytest", "-q")


if __name__ == "__main__":
    main()
