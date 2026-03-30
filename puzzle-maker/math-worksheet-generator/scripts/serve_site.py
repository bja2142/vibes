from __future__ import annotations

from pathlib import Path
import argparse
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worksheet_generator.webapp import create_app
from worksheet_generator.logging_utils import configure_application_logging, log_event


import logging

def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the worksheet web application.")
    parser.add_argument("--port", type=int, default=9595, help="Port to bind the HTTP server to.")
    args = parser.parse_args()

    configure_application_logging()
    logger = logging.getLogger("worksheet_generator.server")
    app = create_app()
    log_event(logger, "server_starting", port=args.port)
    print(f"Serving worksheet web app at http://0.0.0.0:{args.port}")
    app.run(host="0.0.0.0", port=args.port)


if __name__ == "__main__":
    main()
