#!/bin/bash
set -e

# Ensure current directory is in PYTHONPATH for the web app
export PYTHONPATH=$PYTHONPATH:$(pwd)

# Dispatcher for the word search tools
# Usage:
#   docker run word-search-generator orchestrate ...
#   docker run word-search-generator generator ...
#   docker run word-search-generator style ...
#   docker run word-search-generator web

case "$1" in
    orchestrate)
        shift
        exec python orchestrate.py "$@"
        ;;
    generator)
        shift
        exec python cli.py "$@"
        ;;
    style)
        shift
        exec python style.py "$@"
        ;;
    web)
        shift
        exec uvicorn web.app:app --host 0.0.0.0 --port 8000
        ;;
    *)
        echo "Usage: docker run word-search-generator {orchestrate|generator|style|web} [args...]"
        exit 1
        ;;
esac
