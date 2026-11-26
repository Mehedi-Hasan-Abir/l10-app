#!/usr/bin/env bash
# PostToolUse hook (Edit|Write|MultiEdit): format the file Claude just changed, so every AI edit lands in the
# project's style (ruff for backend Python, Prettier for frontend TS/TSX). Never blocks: always exits 0.
# Reads the hook payload (JSON) on stdin; uses node to parse it because jq isn't guaranteed to be installed.
root="$(cd "$(dirname "$0")/../.." && pwd)" # the repo root, wherever Claude Code was started
file=$(node -e 'let d="";process.stdin.on("data",c=>d+=c).on("end",()=>{try{const j=JSON.parse(d);process.stdout.write(j.tool_input?.file_path??j.tool_response?.filePath??"")}catch{}})')
file="${file//\\//}" # Windows paths → forward slashes

case "$file" in
  */backend/*.py)
    (cd "$root/backend" && uv run --quiet ruff format "$file" && uv run --quiet ruff check --fix --quiet "$file") ;;
  */frontend/src/*.ts | */frontend/src/*.tsx)
    case "$file" in */schema.d.ts) ;; *) (cd "$root/frontend" && npx --no-install prettier --write --log-level warn "$file") ;; esac ;;
esac
exit 0
