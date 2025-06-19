# Raspberry Pi GPS Module Setup Documentation

## Document Information
- **Author**: Htet Aung Kyaw
- **Date**: June 19, 2025
- **Purpose**: Guide for setting up a Raspberry Pi to interface with GPS modules, auto-run Python scripts on boot, and retrieve real-time GPS data via WebSocket and HTTP.
- **Target Audience**: Users with basic Linux and Raspberry Pi knowledge.

## Introduction
This document outlines the process to configure a Raspberry Pi to work with USB-based GPS modules, automatically execute Python and shell scripts on startup, and provide real-time GPS data accessible locally and remotely. The setup leverages `gpsd` for GPS communication, a Python script (`gps_websocket.py`) for data processing, and systemd for service management. The system logs data to files and supports offline data storage, ensuring robust operation.

## Hardware Requirements
| Component | Specification | Notes |
|-----------|---------------|-------|
| Raspberry Pi | Model 4 or later (3B+, Zero 2 W compatible) | Pi 4 recommended for performance. |
| GPS Modules | USB/UART-based (e.g., U-blox NEO series) | Must output NMEA sentences, appear as `/dev/ttyACM*` or `/dev/ttyUSB*`. |
| MicroSD Card | 16GB+, Class 10 | For Raspberry Pi OS. |
| Power Supply | 5V, 3A USB-C (Pi 4) | Ensure stable power. |
| Internet Connection | Wi-Fi or Ethernet | Required for setup and updates. |
| Optional Peripherals | Monitor, keyboard, mouse | For direct setup; SSH for headless. |

## Software Requirements
- **Operating System**: Raspberry Pi OS (64-bit or 32-bit, Lite or Desktop, latest version as of June 2025).
- **System Packages**:
  - `gpsd`: GPS daemon.
  - `gpsd-clients`: Tools like `cgps`.
  - `libgps-dev`: GPS development library.
  - `python3`, `python3-pip`, `python3-venv`: Python environment.
- **Python Libraries**:
  - `gps3`: GPS data interface.
  - `websocket-client`, `websockets`: WebSocket communication.
  - `aiohttp`: HTTP server.
  - `pytz`: Timezone handling.
- **Tools**: `systemctl`, `stty`.

## Installation and Setup Instructions

### 1. Install Raspberry Pi OS
1. **Download Raspberry Pi Imager**:
   - Available for Windows, macOS, Linux from the Raspberry Pi website.
2. **Flash OS**:
   - Select **Raspberry Pi OS (64-bit)** or **Lite** for headless setups.
   - Configure SSH and Wi-Fi in Imager’s advanced settings for headless access.
   - Write to MicroSD card.
3. **Boot Raspberry Pi**:
   - Insert MicroSD card, connect power, and boot.
   - Default login: user `pi`, password `raspberry`.
   - Change password:
     ```bash
     passwd
     ```
4. **Enable SSH (Headless)**:
   ```bash
   sudo raspi-config
   ```
   - Navigate to `Interface Options` > `SSH` > Enable.
   - Find Pi’s IP address and connect:
     ```bash
     ssh pi@<Raspberry_Pi_IP>
     ```

### 2. Update System and Install Dependencies
1. **Update System**:
   ```bash
   sudo apt update && sudo apt upgrade -y
   ```
2. **Install System Packages**:
   ```bash
   sudo apt install -y python3 python3-pip python3-venv gpsd gpsd-clients libgps-dev
   ```

### 3. Set Up Python Environment
1. **Create Virtual Environment**:
   ```bash
   python3 -m venv /home/pi/gps_venv
   ```
2. **Activate Environment**:
   ```bash
   source /home/pi/gps_venv/bin/activate
   ```
3. **Upgrade pip**:
   ```bash
   pip install --upgrade pip
   ```
4. **Install Python Libraries**:
   ```bash
   pip install gps3 websocket-client websockets aiohttp pytz
   ```
5. **Save Requirements**:
   ```bash
   pip freeze > /home/pi/GPS/requirements.txt
   ```
6. **Deactivate Environment**:
   ```bash
   deactivate
   ```

### 4. Create Directories and Scripts
1. **Create GPS Directory**:
   ```bash
   sudo mkdir -p /home/pi/GPS
   sudo chown pi:pi /home/pi/GPS
   ```
2. **Create `gps_websocket.py`**:
   ```bash
   sudo nano /home/pi/gps_websocket.py
   ```
   - Copy the `gps_websocket.py` content (provided previously) into the file.
   - Key features:
     - Interfaces with GPS modules via `gpsd`.
     - Logs data to `/home/pi/GPS/gps_data_<timestamp>.txt`.
     - Stores offline data in `/home/pi/GPS/offline_gps_data.json`.
     - Serves data via WebSocket (`ws://<IP>:8765`) and HTTP (`http://<IP>:8082/gps`).
   - Save and set permissions:
     ```bash
     sudo chmod +x /home/pi/gps_websocket.py
     sudo chown pi:pi /home/pi/gps_websocket.py
     ```
3. **Create `requirements.txt`**:
   ```bash
   echo -e "gps3\nwebsocket-client\nwebsockets\naiohttp\npytz" > /home/pi/GPS/requirements.txt
   ```

### 5. Configure gpsd
1. **Edit Configuration**:
   ```bash
   sudo nano /etc/default/gpsd
   ```
   - Replace with:
     ```bash
     START_DAEMON="true"
     GPSD_OPTIONS="-n -F /var/run/gpsd.sock -G 127.0.0.1 -b -D 3"
     DEVICES="/dev/ttyACM0 /dev/ttyACM1"
     USBAUTO="false"
     GPSD_SOCKET="/var/run/gpsd.sock"
     ```
   - Save and exit.
2. **Disable gpsd Socket**:
   ```bash
   sudo systemctl disable gpsd.socket
   sudo systemctl stop gpsd.socket
   ```
3. **Enable gpsd Service**:
   ```bash
   sudo systemctl enable gpsd.service
   ```

### 6. Set Up Systemd Service
1. **Create Service File**:
   ```bash
   sudo nano /etc/systemd/system/gps_data.service
   ```
   - Add:
     ```bash
     [Unit]
     Description=GPS Data Python Script Service
     After=network.target gpsd.service

     [Service]
     ExecStart=/home/pi/gps_venv/bin/python3 /home/pi/gps_websocket.py
     WorkingDirectory=/home/pi
     StandardOutput=inherit
     StandardError=inherit
     Restart=always
     User=pi

     [Install]
     WantedBy=multi-user.target
     ```
   - Save and exit.
2. **Set Permissions**:
   ```bash
   sudo chmod 644 /etc/systemd/system/gps_data.service
   ```
3. **Enable and Start Service**:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable gps_data.service
   sudo systemctl start gps_data.service
   ```

### 7. Connect and Test GPS Modules
1. **Connect Modules**:
   - Plug GPS modules into USB ports.
   - Verify detection:
     ```bash
     ls -l /dev/ttyACM*
     ```
   - Check kernel logs:
     ```bash
     sudo dmesg | grep ttyACM
     ```
2. **Test gpsd**:
   ```bash
   cgps -s
   ```
   - Exit with `Ctrl+C`.
3. **Check Service**:
   ```bash
   sudo systemctl status gps_data.service
   ```
4. **View Logs**:
   ```bash
   tail -f /home/pi/GPS/gps_websocket.log
   ```

### 8. Access Real-Time GPS Data
1. **WebSocket**:
   - Install `wscat`:
     ```bash
     sudo apt install -y npm
     npm install -g wscat
     wscat -c ws://<Raspberry_Pi_IP>:8765
     ```
   - Browser-based client:
     ```html
     <!DOCTYPE html>
     <html>
     <body>
       <pre id="gps-data"></pre>
       <script>
         const ws = new WebSocket('ws://<Raspberry_Pi_IP>:8765');
         ws.onmessage = (event) => {
           document.getElementById('gps-data').textContent = JSON.stringify(JSON.parse(event.data), null, 2);
         };
       </script>
     </body>
     </html>
     ```
     Save as `index.html` and open in a browser.
2. **HTTP**:
   ```bash
   curl http://<Raspberry_Pi_IP>:8082/gps
   ```
3. **Log Files**:
   - Real-time: `/home/pi/GPS/gps_data_<timestamp>.txt`
   - Offline: `/home/pi/GPS/offline_gps_data.json`

### 9. Troubleshooting
| Issue | Command | Solution |
|-------|---------|----------|
| No GPS Data | `cgps -s`, `ls -l /dev/ttyACM*` | Check module connections, verify `/dev/ttyACM*`, review `dmesg`. |
| Port Conflicts | `sudo netstat -tulnp | grep -E '2947|8765|8082'` | Kill processes: `sudo pkill -9 gpsd`, `sudo pkill -f gps_websocket.py`. |
| Service Failure | `sudo systemctl status gps_data.service` | Check logs: `journalctl -u gps_data.service -b`. |
| gpsd Issues | `sudo systemctl status gpsd` | Restart: `sudo systemctl restart gpsd`. |
| General | `sudo reboot` | Reboot Pi. |

## Automated Setup Script
To streamline setup, use the following script:

1. **Save Script**:
   ```bash
   nano /home/pi/setup_gps_raspberry_pi.sh
   ```
   - Copy the `setup_gps_raspberry_pi.sh` content (provided previously).
   - Save and exit.
2. **Make Executable**:
   ```bash
   chmod +x /home/pi/setup_gps_raspberry_pi.sh
   ```
3. **Run Script**:
   ```bash
   sudo /home/pi/setup_gps_raspberry_pi.sh
   ```

## Customization
- **SHIP_ID**: Modify `SHIP_ID="SHIP456"` in `gps_websocket.py`.
- **Ports**: Change `WEBSOCKET_PORT=8765` or `HTTP_PORT=8082` if conflicts occur.
- **External WebSocket**: Update `EXTERNAL_WEBSOCKET_URL='ws://13.209.33.15:4002'`.
- **Baud Rates**: Adjust in `gps_websocket.py`:
  ```python
  baud_rate = '115200' if device == '/dev/ttyACM0' else '9600'
  ```

## Conclusion
This setup ensures the Raspberry Pi automatically runs the GPS service on boot, providing real-time GPS data via WebSocket and HTTP. Logs are maintained for debugging and offline data storage. For further assistance, check logs at `/home/pi/GPS/gps_websocket.log` or contact support.