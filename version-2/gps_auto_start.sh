#!/bin/bash

# gps_auto_start.sh
# Script to start GPS system on reboot, auto-detect GPS devices, and log satellite info

# Configuration
VENV_PATH="/home/mdt/gps_venv"
GPS_DIR="/home/mdt/GPS"
LOG_FILE="/home/mdt/gps_auto_start.log"
OUTPUT_DIR="/home/mdt/Desktop/GPS"
GPS_PY="/home/mdt/gps_websocket.py"

# Function to log messages
log_message() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" >> "${LOG_FILE}"
    echo "$1"
}

# Create log and output directories
mkdir -p "${OUTPUT_DIR}" "${GPS_DIR}"
touch "${LOG_FILE}"
log_message "Starting GPS auto-start script"

# Activate virtual environment
if [ -d "${VENV_PATH}" ]; then
    source "${VENV_PATH}/bin/activate"
    log_message "Activated virtual environment at ${VENV_PATH}"
else
    log_message "ERROR: Virtual environment not found at ${VENV_PATH}"
    exit 1
fi

# Check Python package dependencies
for pkg in gps3 websocket_client websockets aiohttp pytz; do
    python3 -c "import $pkg" 2>/dev/null || {
        log_message "Installing missing package: $pkg"
        pip install "$pkg" || {
            log_message "ERROR: Failed to install $pkg"
            exit 1
        }
    }
done

# Detect GPS devices
GPS_DEVICES=$(ls /dev/ttyACM* /dev/ttyUSB* 2>/dev/null)
if [ -z "${GPS_DEVICES}" ]; then
    log_message "ERROR: No GPS devices detected"
    exit 1
fi
GPS_DEVICES_ARRAY=(${GPS_DEVICES})
log_message "Detected GPS devices: ${GPS_DEVICES}"

# Set baud rate for each device
for device in "${GPS_DEVICES_ARRAY[@]}"; do
    sudo stty -F "${device}" 9600
    if [ $? -eq 0 ]; then
        log_message "Set baud rate to 9600 for ${device}"
    else
        log_message "WARNING: Failed to set baud rate for ${device}"
    fi
done

# Stop any existing gpsd instances
sudo killall gpsd 2>/dev/null
sleep 1
log_message "Stopped any existing gpsd instances"

# Start gpsd with detected devices
sudo gpsd -n -F /var/run/gpsd.sock -G 127.0.0.1 -b ${GPS_DEVICES}
if [ $? -eq 0 ]; then
    log_message "Started gpsd with devices: ${GPS_DEVICES}"
else
    log_message "ERROR: Failed to start gpsd"
    exit 1
fi

# Start GPS data processing and WebSocket server
if [ -f "${GPS_PY}" ]; then
    python3 "${GPS_PY}" >> "${LOG_FILE}" 2>&1 &
    GPS_PID=$!
    log_message "Started GPS and WebSocket server (PID: ${GPS_PID})"
    sleep 5  # Increased sleep to allow script to initialize
    if ! ps -p "${GPS_PID}" > /dev/null; then
        log_message "ERROR: GPS and WebSocket server failed to start. Check ${LOG_FILE} for details."
        cat "${LOG_FILE}" | tail -n 20 >> "${LOG_FILE}.error"
        exit 1
    fi
else
    log_message "ERROR: GPS script not found at ${GPS_PY}"
    exit 1
fi

log_message "GPS system started successfully"
