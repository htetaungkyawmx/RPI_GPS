#!/bin/bash

# Configuration
GPS_PY="/home/mdt/GPS/gps_websocket_offline.py"
VENV_PATH="/home/mdt/gps_venv"
LOG_FILE="/home/mdt/gps_auto_start.log"

# Ensure log file exists
touch "$LOG_FILE"

# Log start time
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting GPS auto-start script" >> "$LOG_FILE"

# Activate virtual environment
if [ -f "$VENV_PATH/bin/activate" ]; then
    source "$VENV_PATH/bin/activate"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Virtual environment activated" >> "$LOG_FILE"
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: Virtual environment not found at $VENV_PATH" >> "$LOG_FILE"
    exit 1
fi

# Run the Python script
if [ -f "$GPS_PY" ]; then
    python3 "$GPS_PY" >> "$LOG_FILE" 2>&1 &
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Started $GPS_PY with PID $!" >> "$LOG_FILE"
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: Python script not found at $GPS_PY" >> "$LOG_FILE"
    exit 1
fi

exit 0
