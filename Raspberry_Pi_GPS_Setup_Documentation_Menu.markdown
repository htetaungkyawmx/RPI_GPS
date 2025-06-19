# Raspberry Pi GPS Module Setup Documentation

## Menu
1. [Introduction](#1-introduction)
2. [Hardware Requirements](#2-hardware-requirements)
3. [Software Requirements](#3-software-requirements)
4. [Installation Steps](#4-installation-steps)
   - [4.1 Install Raspberry Pi OS](#41-install-raspberry-pi-os)
   - [4.2 Update System and Install Dependencies](#42-update-system-and-install-dependencies)
   - [4.3 Set Up Python Environment](#43-set-up-python-environment)
   - [4.4 Configure Files and Directories](#44-configure-files-and-directories)
   - [4.5 Configure gpsd](#45-configure-gpsd)
   - [4.6 Set Up Systemd Service](#46-set-up-systemd-service)
5. [Testing GPS Modules](#5-testing-gps-modules)
6. [Accessing Real-Time GPS Data](#6-accessing-real-time-gps-data)
7. [Troubleshooting](#7-troubleshooting)
8. [Customization](#8-customization)

## 1. Introduction
This guide provides a step-by-step process to set up a Raspberry Pi with GPS modules to collect and serve real-time GPS data using the `gps_data.py` script. The system automatically starts on boot, logs data to files, and provides access via WebSocket and HTTP interfaces.

## 2. Hardware Requirements
- **Raspberry Pi**: Model 4 (recommended), 3B+, or Zero 2 W.
- **GPS Modules**: USB-based (e.g., U-blox NEO) connected to `/dev/ttyACM0` or `/dev/ttyACM1`.
- **MicroSD Card**: 16GB+ (Class 10).
- **Power Supply**: 5V, 3A USB-C for Pi 4.
- **Internet**: Wi-Fi or Ethernet.
- **Optional**: Monitor, keyboard, mouse, or SSH access.

## 3. Software Requirements
- **OS**: Raspberry Pi OS (64-bit or 32-bit, Lite or Desktop).
- **System Packages**:
  ```bash
  sudo apt install -y python3 python3-pip python3-venv gpsd gpsd-clients libgps-dev
  ```
- **Python Libraries**:
  ```bash
  pip install gps3 websocket-client websockets aiohttp pytz
  ```
- **Tools**: `systemctl`, `stty`.

## 4. Installation Steps

### 4.1 Install Raspberry Pi OS
- **Download**: Use Raspberry Pi Imager to flash Raspberry Pi OS (64-bit) onto a MicroSD card.
- **Configure**: Enable SSH and Wi-Fi in Imager settings.
- **Boot and Log In**:
  ```bash
  ssh pi@<Raspberry_Pi_IP>
  ```
  - Default: user `pi`, password `raspberry`.

### 4.2 Update System and Install Dependencies
- **Update**:
  ```bash
  sudo apt update && sudo apt upgrade -y
  ```
- **Install Packages**:
  ```bash
  sudo apt install -y python3 python3-pip python3-venv gpsd gpsd-clients libgps-dev
  ```

### 4.3 Set Up Python Environment
- **Create Virtual Environment**:
  ```bash
  python3 -m venv /home/pi/gps_venv
  ```
- **Activate**:
  ```bash
  source /home/pi/gps_venv/bin/activate
  ```
- **Upgrade pip**:
  ```bash
  pip install --upgrade pip
  ```
- **Install Dependencies**:
  ```bash
  pip install gps3 websocket-client websockets aiohttp pytz
  ```
- **Save Requirements**:
  ```bash
  pip freeze > /home/pi/GPS/requirements.txt
  ```
- **Deactivate**:
  ```bash
  deactivate
  ```

### 4.4 Configure Files and Directories
- **Create GPS Directory**:
  ```bash
  sudo mkdir -p /home/pi/GPS
  sudo chown pi:pi /home/pi/GPS
  ```
- **Create `gps_data.py`**:
  ```bash
  sudo nano /home/pi/gps_data.py
  ```
  - Copy `gps_data.py` content (provided earlier).
  - Save and set permissions:
    ```bash
    sudo chmod +x /home/pi/gps_data.py
    sudo chown pi:pi /home/pi/gps_data.py
    ```
- **Create `requirements.txt`**:
  ```bash
  echo -e "gps3\nwebsocket-client\nwebsockets\naiohttp\npytz" > /home/pi/GPS/requirements.txt
  ```

### 4.5 Configure gpsd
- **Edit Configuration**:
  ```bash
  sudo nano /etc/default/gpsd
  ```
  - Content:
    ```bash
    START_DAEMON="true"
    GPSD_OPTIONS="-n -F /var/run/gpsd.sock -G 127.0.0.1 -b -D 3"
    DEVICES="/dev/ttyACM0 /dev/ttyACM1"
    USBAUTO="false"
    GPSD_SOCKET="/var/run/gpsd.sock"
    ```
- **Disable Socket**:
  ```bash
  sudo systemctl disable gpsd.socket
  sudo systemctl stop gpsd.socket
  ```
- **Enable Service**:
  ```bash
  sudo systemctl enable gpsd.service
  ```

### 4.6 Set Up Systemd Service
- **Create Service File**:
  ```bash
  sudo nano /etc/systemd/system/gps_data.service
  ```
  - Content:
    ```bash
    [Unit]
    Description=GPS Data Python Script Service
    After=network.target gpsd.service

    [Service]
    ExecStart=/home/pi/gps_venv/bin/python3 /home/pi/gps_data.py
    WorkingDirectory=/home/pi
    StandardOutput=inherit
    StandardError=inherit
    Restart=always
    User=pi

    [Install]
    WantedBy=multi-user.target
    ```
- **Set Permissions**:
  ```bash
  sudo chmod 644 /etc/systemd/system/gps_data.service
  ```
- **Enable and Start**:
  ```bash
  sudo systemctl daemon-reload
  sudo systemctl enable gps_data.service
  sudo systemctl start gps_data.service
  ```

## 5. Testing GPS Modules
- **Connect Modules**:
  ```bash
  ls -l /dev/ttyACM*
  sudo dmesg | grep ttyACM
  ```
- **Test gpsd**:
  ```bash
  cgps -s
  ```
- **Check Service**:
  ```bash
  sudo systemctl status gps_data.service
  ```
- **View Logs**:
  ```bash
  tail -f /home/pi/GPS/gps_websocket.log
  ```

## 6. Accessing Real-Time GPS Data
- **WebSocket**:
  ```bash
  sudo apt install -y npm
  npm install -g wscat
  wscat -c ws://<Raspberry_Pi_IP>:8766
  ```
  - Or use a browser-based client:
    ```html
    <!DOCTYPE html>
    <html>
    <body>
      <pre id="gps-data"></pre>
      <script>
        const ws = new WebSocket('ws://<Raspberry_Pi_IP>:8766');
        ws.onmessage = (event) => {
          document.getElementById('gps-data').textContent = JSON.stringify(JSON.parse(event.data), null, 2);
        };
      </script>
    </body>
    </html>
    ```
- **HTTP**:
  ```bash
  curl http://<Raspberry_Pi_IP>:8082/gps
  ```
- **Logs**:
  - Data: `/home/pi/GPS/gps_data_<timestamp>.txt`
  - Offline: `/home/pi/GPS/offline_gps_data.json`

## 7. Troubleshooting
- **Check Ports**:
  ```bash
  sudo netstat -tulnp | grep -E '2947|8766|8082'
  ```
- **Kill Conflicts**:
  ```bash
  sudo pkill -9 gpsd
  sudo pkill -f gps_data.py
  sudo rm -f /var/run/gpsd.sock
  ```
- **Restart Services**:
  ```bash
  sudo systemctl restart gpsd.service
  sudo systemctl restart gps_data.service
  ```
- **View Logs**:
  ```bash
  journalctl -u gps_data.service -b
  journalctl -u gpsd.service -b
  ```
- **Reboot**:
  ```bash
  sudo reboot
  ```

## 8. Customization
- **SHIP_ID**: Edit `SHIP_ID="SHIP456"` in `gps_data.py`.
- **Ports**: Change `WEBSOCKET_PORT=8766` or `HTTP_PORT=8082` in `gps_data.py`.
- **External WebSocket**: Update `EXTERNAL_WEBSOCKET_URL` in `gps_data.py`.
- **Baud Rates**: Adjust in `gps_data.py`:
  ```python
  baud_rate = '115200' if device == '/dev/ttyACM0' else '9600'
  ```