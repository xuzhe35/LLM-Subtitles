#!/bin/zsh

SCRIPT_DIR="${0:A:h}"
cd "$SCRIPT_DIR" || exit 1

if [[ ! -f ".venv/bin/activate" ]]; then
    echo "Virtual environment not found: $SCRIPT_DIR/.venv"
    echo "Create it and install dependencies before starting the application."
    echo
    read "?Press Return to close..."
    exit 1
fi

source ".venv/bin/activate"
python main.py
exit_code=$?

if (( exit_code != 0 )); then
    echo
    echo "LLM Subtitles exited with error code $exit_code."
    read "?Press Return to close..."
fi

exit $exit_code
