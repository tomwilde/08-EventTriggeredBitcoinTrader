#!/usr/bin/env bash
set -euo pipefail

echo "==> Streamlit skill..."
npx skills add streamlit/agent-skills -s developing-with-streamlit -a codex -g -y

echo "==> pytest skill (LambdaTest public repo)..."
npx skills add https://github.com/LambdaTest/agent-skills --skill pytest-skill -a codex -g -y

echo "==> modern-python (Trail of Bits)..."
npx skills add https://github.com/trailofbits/skills --skill modern-python -a codex -g -y

echo "==> Done."