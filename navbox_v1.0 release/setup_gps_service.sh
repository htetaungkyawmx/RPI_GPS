#!/bin/bash

# Exit on any error
set -e

# Define variables
GPS_DIR="/home/mdt/GPS"
VENV_DIR="/home/mdt/gps_venv"
SCRIPT_DIR="/home/mdt"
SCRIPT_NAME="gps_data.py"
SERVICE_NAME="gps_data.service"
USER="mdt"
SYSTEMD_SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}"
PYTHON_VERSION="python3"
PIP_VERSION="pip3"

# Ensure script is run as root
if [ "$(id -u)" != "0" ]; then
    echo "This script must be run as root. Please use sudo."
    exit 1
fi

# Update system packages
echo "Updating system packages..."
apt update && apt upgrade -y

# Install required system packages
echo "Installing system dependencies..."
apt install -y python3 python3-pip python3-venv gpsd gpsd-clients libgps-dev

# Create GPS directory if it doesn't exist
echo "Creating GPS directory: ${GPS_DIR}"
mkdir -p "${GPS_DIR}"
chown ${USER}:${USER} "${GPS_DIR}"

# Create script directory if it doesn't exist
echo "Creating script directory: ${SCRIPT_DIR}"
mkdir -p "${SCRIPT_DIR}"
chown ${USER}:${USER} "${SCRIPT_DIR}"

# Create Python virtual environment
echo "Creating Python virtual environment in ${VENV_DIR}"
if [ ! -d "${VENV_DIR}" ]; then
    ${PYTHON_VERSION} -m venv "${VENV_DIR}"
fi
chown -R ${USER}:${USER} "${VENV_DIR}"

# Activate virtual environment and install Python dependencies
echo "Installing Python dependencies..."
source "${VENV_DIR}/bin/activate"
${PIP_VERSION} install --upgrade pip
${PIP_VERSION} install gps python-dateutil pytz websockets aiohttp

# Deactivate virtual environment
deactivate

# Ensure gps_data.py exists (assuming it's already provided)
if [ ! -f "${SCRIPT_DIR}/${SCRIPT_NAME}" ]; then
    echo "Error: ${SCRIPT_NAME} not found in ${SCRIPT_DIR}. Please place the script there."
    exit 1
fi
chown ${USER}:${USER} "${SCRIPT_DIR}/${SCRIPT_NAME}"
chmod +x "${SCRIPT_DIR}/${SCRIPT_NAME}"

# Create systemd service file
echo "Creating systemd service file: ${SYSTEMD_SERVICE_FILE}"
cat > "${SYSTEMD_SERVICE_FILE}" <<EOL
[Unit]
Description=GPS Data Python Script Service
After=network.target

[Service]
ExecStart=${VENV_DIR}/bin/python3 ${SCRIPT_DIR}/${SCRIPT_NAME}
WorkingDirectory=${SCRIPT_DIR}
StandardOutput=inherit
StandardError=inherit
Restart=always
User=${USER}

[Install]
WantedBy=multi-user.target
EOL

# Set permissions for systemd service file
chmod 644 "${SYSTEMD_SERVICE_FILE}"

# Reload systemd, enable and start the service
echo "Reloading systemd daemon..."
systemctl daemon-reload
echo "Enabling ${SERVICE_NAME}..."
systemctl enable "${SERVICE_NAME}"
echo "Starting ${SERVICE_NAME}..."
systemctl start "${SERVICE_NAME}"

# Check service status
echo "Checking service status..."
systemctl status "${SERVICE_NAME}" --no-pager

echo "Setup completed successfully!"