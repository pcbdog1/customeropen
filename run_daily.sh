#!/bin/zsh
set -eu

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"
mkdir -p logs
LOG_FILE="logs/daily_loop_$(date +%Y%m%d).log"

{
  echo "===== customeropen START $(date '+%Y-%m-%d %H:%M:%S %Z') ====="
  .venv/bin/python scheduler_gate.py
  exit_code=$?
  echo "===== customeropen END $(date '+%Y-%m-%d %H:%M:%S %Z') exit_code=${exit_code} ====="
  exit "${exit_code}"
} >> "${LOG_FILE}" 2>&1
