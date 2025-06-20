#!/bin/bash

# Exit on any error
set -e

# Define paths
VENV_PATH="/home/mdt/gps_venv"
GPS_SCRIPT_PATH="/home/mdt/gps_data.py"
GPS_DIR="/home/mdt/GPS"
SYSTEMD_SERVICE="/etc/systemd/system/gps-data.service"
USER="mdt"

# Update system packages
echo "Updating system packages..."
sudo apt update && sudo apt upgrade -y

# Install required system packages
echo "Installing system dependencies..."
sudo apt install -y python3 python3-pip python3-venv gpsd gpsd-clients

# Create GPS directory if it doesn't exist
echo "Creating GPS directory..."
sudo mkdir -p "$GPS_DIR"
sudo chown $USER:$USER "$GPS_DIR"

# Create virtual environment
echo "Creating virtual environment..."
python3 -m venv "$VENV_PATH"

# Activate virtual environment
source "$VENV_PATH/bin/activate"

# Upgrade pip
echo "Upgrading pip..."
pip install --upgrade pip

# Install Python packages
echo "Installing Python packages..."
pip install gps3 websocket-client websockets aiohttp pytz

# Save installed packages to requirements.txt
echo "Saving requirements to requirements.txt..."
pip freeze > "$GPS_DIR/requirements.txt"

# Deactivate virtual environment
deactivate

# Create gps_data.py using echo
echo "Creating gps_data.py..."
cat << 'EOF' > "$GPS_SCRIPT_PATH"
#!/home/mdt/gps_venv/bin/python3
import gps
import time
import logging
import subprocess
import os
import glob
import asyncio
import websockets
import json
import pytz
import socket
from datetime import datetime
from aiohttp import web
from queue import Queue, Empty
import sys
import random

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/home/mdt/GPS/gps_websocket.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Configuration
GPS_DATA_DIR = '/home/mdt/GPS'
GPSD_HOST = '127.0.0.1'
GPSD_PORT = 2947
WEBSOCKET_PORT = 8766
HTTP_PORT = 8082
EXTERNAL_WEBSOCKET_URL = 'ws://13.209.33.15:4002'
TIMEOUT = 10
RECONNECT_DELAY = 2
MAX_RECONNECT_DELAY = 30
DATA_TIMEOUT = 30
SHIP_ID = "SHIP456"
DEBOUNCE_INTERVAL = 0.5

# Global variables
latest_gps_data = None
connected_clients = set()
gps_data_queue = Queue()
external_ws_connected = False
last_tpv_time = {device: 0 for device in ['/dev/ttyACM0', '/dev/ttyACM1']}
current_output_file = None
app_start_time = None
ws_server = None
http_runner = None
device_id = None

def create_device_id():
    global device_id
    try:
        with open('/proc/cpuinfo', 'r') as f:
            for line in f:
                if line.startswith('Serial'):
                    serial = line.split(':')[1].strip()
                    if serial:
                        logger.info(f"Detected device ID: {serial}")
                        device_id = serial
                        return serial
        logger.error("No serial number found in /proc/cpuinfo")
    except Exception as e:
        logger.error(f"Error retrieving device ID: {e}")

def get_device_id():
    global device_id
    return device_id or "unknown_device_id"

def get_output_filename():
    now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    return os.path.join(GPS_DATA_DIR, f"gps_data_{now}.txt")

def is_port_free(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("0.0.0.0", port))
            return True
        except OSError:
            return False

def detect_gps_devices():
    devices = glob.glob('/dev/ttyACM*') + glob.glob('/dev/ttyUSB*')
    if not devices:
        logger.error("No GPS devices detected")
        return []
    logger.info(f"Detected GPS devices: {devices}")
    devices.sort()
    return devices[:2]

def run_command(cmd):
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT)
        logger.debug(f"Command {cmd}: stdout={result.stdout}, stderr={result.stderr}")
        return result.returncode == 0, result.stdout, result.stderr
    except subprocess.SubprocessError as e:
        logger.error(f"Command {cmd} failed: {e}")
        return False, "", str(e)

def ensure_gpsd_running(devices):
    run_command(['sudo', 'pkill', '-9', 'gpsd'])
    time.sleep(1)
    socket_path = '/var/run/gpsd.sock'
    if os.path.exists(socket_path):
        logger.info(f"Removing stale gpsd socket: {socket_path}")
        run_command(['sudo', 'rm', '-f', socket_path])
    if not devices:
        logger.error("No devices provided for gpsd")
        return False
    logger.info(f"Starting gpsd with devices: {devices}")
    cmd = ['sudo', 'gpsd', '-n', '-F', '/var/run/gpsd.sock', '-G', '127.0.0.1', '-b'] + devices
    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        stdout, stderr = process.communicate(timeout=5)
        if process.returncode == 0 or process.poll() is None:
            logger.info("gpsd started successfully")
            return True
        logger.error(f"Failed to start gpsd: stdout={stdout}, stderr={stderr}")
        return False
    except subprocess.TimeoutExpired:
        if process.poll() is None:
            logger.info("gpsd is running in background")
            return True
    except Exception as e:
        logger.error(f"Failed to start gpsd: {e}")
        return False

async def parse_gps_data(gps_text):
    try:
        data = {
            "timestamp": "",
            "ship_id": SHIP_ID,
            "device_id": get_device_id(),
            "heading": None,
            "gps_data": [
                {"gps": "top_gps", "latitude": None, "longitude": None, "altitude": None, "speed": None, "satellites": None, "satellite_prns": []},
                {"gps": "bottom_gps", "latitude": None, "longitude": None, "altitude": None, "speed": None, "satellites": None, "satellite_prns": []}
            ]
        }
        lines = gps_text.strip().split("\n")
        current_index = None
        for line in lines:
            line = line.strip()
            if line.startswith("GPS Data (Real-Time):"):
                data["timestamp"] = line.split(":", 1)[1].strip()
            elif line.startswith("Heading:"):
                heading_str = line.split(":", 1)[1].strip()
                try:
                    data["heading"] = float(heading_str) if heading_str != "Unknown" else None
                except ValueError:
                    data["heading"] = None
            elif "Top GPS" in line:
                current_index = 0
            elif "Bottom GPS" in line:
                current_index = 1
            elif "Latitude:" in line and current_index is not None:
                lat_str = line.split(":", 1)[1].strip()
                try:
                    data["gps_data"][current_index]["latitude"] = float(lat_str) if lat_str != "Unknown" else None
                except ValueError:
                    data["gps_data"][current_index]["latitude"] = None
            elif "Longitude:" in line and current_index is not None:
                lon_str = line.split(":", 1)[1].strip()
                try:
                    data["gps_data"][current_index]["longitude"] = float(lon_str) if lon_str != "Unknown" else None
                except ValueError:
                    data["gps_data"][current_index]["longitude"] = None
            elif "Altitude (m):" in line and current_index is not None:
                alt_str = line.split(":", 1)[1].strip()
                try:
                    data["gps_data"][current_index]["altitude"] = float(alt_str) if alt_str != "Unknown" else None
                except ValueError:
                    data["gps_data"][current_index]["altitude"] = None
            elif "Speed (km/h):" in line and current_index is not None:
                speed_str = line.split(":", 1)[1].strip()
                try:
                    data["gps_data"][current_index]["speed"] = float(speed_str) if speed_str != "Unknown" else None
                except ValueError:
                    data["gps_data"][current_index]["speed"] = None
            elif "Satellites:" in line and current_index is not None:
                sat_str = line.split(":", 1)[1].strip()
                try:
                    data["gps_data"][current_index]["satellites"] = int(sat_str) if sat_str != "Unknown" and sat_str else None
                except ValueError:
                    data["gps_data"][current_index]["satellites"] = None
            elif "Satellite PRNs:" in line and current_index is not None:
                prns = line.split(":", 1)[1].strip()
                data["gps_data"][current_index]["satellite_prns"] = [prn.strip() for prn in prns.split(",") if prn.strip()] if prns and prns != "Unknown" else []
        return data
    except Exception as e:
        logger.error(f"Error parsing GPS data: {e}")
        return None

async def websocket_handler(websocket, path=None):
    logger.info("WebSocket client connected")
    connected_clients.add(websocket)
    try:
        async for message in websocket:
            try:
                data = json.loads(message)
                gps_text = data.get("gps_data", "")
                parsed_data = await parse_gps_data(gps_text)
                if parsed_data and all(
                    gps.get('latitude') is not None and
                    gps.get('longitude') is not None and
                    gps.get('altitude') is not None and
                    gps.get('speed') is not None and
                    gps.get('satellites') is not None
                    for gps in parsed_data.get('gps_data', [])
                ):
                    global latest_gps_data
                    latest_gps_data = parsed_data
                    for client in connected_clients.copy():
                        try:
                            await client.send(json.dumps(parsed_data))
                        except websockets.exceptions.ConnectionClosed:
                            connected_clients.discard(client)
                    logger.info(f"Broadcasted GPS data to local clients: {parsed_data}")
            except json.JSONDecodeError:
                logger.error("Invalid JSON received")
    except websockets.exceptions.ConnectionClosed:
        logger.info("WebSocket client disconnected")
    finally:
        connected_clients.discard(websocket)

async def get_gps_data(request):
    global latest_gps_data
    if latest_gps_data and all(
        gps.get('latitude') is not None and
        gps.get('longitude') is not None and
        gps.get('altitude') is not None and
        gps.get('speed') is not None and
        gps.get('satellites') is not None
        for gps in latest_gps_data.get('gps_data', [])
    ):
        return web.json_response(latest_gps_data)
    return web.json_response({"error": "No valid GPS data available"}, status=404)

async def start_websocket_server():
    global ws_server
    if not is_port_free(WEBSOCKET_PORT):
        logger.error(f"Port {WEBSOCKET_PORT} is already in use")
        raise OSError(f"Port {WEBSOCKET_PORT} is already in use")
    ws_server = await websockets.serve(websocket_handler, "0.0.0.0", WEBSOCKET_PORT)
    logger.info(f"WebSocket server started on ws://0.0.0.0:{WEBSOCKET_PORT}")
    return ws_server

async def send_to_external_websocket():
    global external_ws_connected
    reconnect_delay = RECONNECT_DELAY
    while True:
        try:
            # Clear queue before connecting to avoid sending stale data
            while not gps_data_queue.empty():
                gps_data_queue.get_nowait()
                gps_data_queue.task_done()
            async with await asyncio.wait_for(websockets.connect(EXTERNAL_WEBSOCKET_URL), timeout=TIMEOUT) as websocket:
                logger.info(f"Connected to external WebSocket server: {EXTERNAL_WEBSOCKET_URL}")
                external_ws_connected = True
                reconnect_delay = RECONNECT_DELAY  # Reset delay on successful connection
                while True:
                    try:
                        gps_text = gps_data_queue.get_nowait()
                        parsed_data = await parse_gps_data(gps_text)
                        if parsed_data and all(
                            gps.get('latitude') is not None and
                            gps.get('longitude') is not None and
                            gps.get('altitude') is not None and
                            gps.get('speed') is not None and
                            gps.get('satellites') is not None
                            for gps in parsed_data.get('gps_data', [])
                        ):
                            global latest_gps_data
                            latest_gps_data = parsed_data
                            await websocket.send(json.dumps(parsed_data))
                            logger.info(f"Sent GPS data to external server: {parsed_data}")
                        gps_data_queue.task_done()
                    except Empty:
                        await asyncio.sleep(0.1)
                    except websockets.exceptions.ConnectionClosed:
                        logger.error("External WebSocket connection closed unexpectedly")
                        external_ws_connected = False
                        break
                    except Exception as e:
                        logger.error(f"Error processing GPS data for external server: {e}")
                        break
        except asyncio.TimeoutError:
            logger.error(f"Timeout connecting to external WebSocket server: {EXTERNAL_WEBSOCKET_URL}")
            external_ws_connected = False
        except Exception as e:
            logger.error(f"Failed to connect to external WebSocket server: {e}")
            external_ws_connected = False
        # Exponential backoff with jitter
        reconnect_delay = min(reconnect_delay * 2, MAX_RECONNECT_DELAY)
        jitter = random.uniform(0, 0.1 * reconnect_delay)
        logger.info(f"Retrying connection in {reconnect_delay + jitter:.2f} seconds")
        await asyncio.sleep(reconnect_delay + jitter)

def process_gps_data():
    global current_output_file, app_start_time
    app_start_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    start_message = f"\n=== Application Started at: {app_start_time} ===\n"
    logger.info(start_message.strip())
    current_output_file = get_output_filename()
    os.makedirs(GPS_DATA_DIR, exist_ok=True)
    with open(current_output_file, 'a') as f:
        f.write(start_message)
    logger.info("Starting GPS data processing")
    SERIAL_DEVICES = detect_gps_devices()
    if not SERIAL_DEVICES:
        logger.error("No GPS devices found, exiting")
        return
    for device in SERIAL_DEVICES:
        baud_rate = '115200' if device == '/dev/ttyACM0' else '9600'
        success, stdout, stderr = run_command(['sudo', 'stty', '-F', device, baud_rate])
        if not success:
            logger.warning(f"Failed to set baud rate {baud_rate} for {device}: {stderr}")
    if not ensure_gpsd_running(SERIAL_DEVICES):
        logger.error("Cannot proceed without gpsd running")
        return
    device_data = {device: {
        'latitude': None,
        'longitude': None,
        'altitude': None,
        'speed': None,
        'satellites': None,
        'timestamp': None,
        'heading': None,
        'satellite_prns': []
    } for device in SERIAL_DEVICES}
    last_data_time = {device: time.time() for device in SERIAL_DEVICES}
    while True:
        try:
            session = gps.gps(host=GPSD_HOST, port=GPSD_PORT)
            session.stream(gps.WATCH_ENABLE | gps.WATCH_JSON)
            while True:
                try:
                    report = session.next()
                    if report is None or not hasattr(report, 'get'):
                        continue
                    current_time = time.time()
                    device = getattr(report, 'device', None)
                    if device not in SERIAL_DEVICES:
                        logger.debug(f"Ignoring report for unknown device: {device}")
                        continue
                    last_data_time[device] = current_time
                    if current_time - min(last_data_time.values()) > DATA_TIMEOUT:
                        logger.warning(f"No data received from some devices for {DATA_TIMEOUT} seconds")
                    timestamp = datetime.now(pytz.UTC).strftime('%Y-%m-%d %H:%M:%S.%f')
                    if report.get('class') == 'TPV':
                        if current_time - last_tpv_time[device] < DEBOUNCE_INTERVAL:
                            continue
                        last_tpv_time[device] = current_time
                        lat = getattr(report, 'lat', None)
                        lon = getattr(report, 'lon', None)
                        alt = getattr(report, 'alt', None)
                        speed = getattr(report, 'speed', None)
                        heading = getattr(report, 'track', None)
                        if isinstance(speed, (int, float)):
                            speed = round(speed * 3.6, 2)
                        if isinstance(heading, (int, float)):
                            heading = round(heading, 1)
                        device_data[device].update({
                            'timestamp': timestamp,
                            'latitude': lat,
                            'longitude': lon,
                            'altitude': alt,
                            'speed': speed,
                            'heading': heading
                        })
                    elif report.get('class') == 'SKY':
                        satellites = len([sat for sat in report.get('satellites', []) if sat.get('used', False)])
                        prns = [str(sat.get('PRN', '')) for sat in report.get('satellites', []) if sat.get('used', False) and sat.get('PRN')]
                        device_data[device].update({
                            'satellites': satellites if satellites > 0 else None,
                            'satellite_prns': prns
                        })
                        logger.info(f"Device {device} using {satellites} satellites with PRNs: {prns}")
                    output = [
                        f"GPS Data (Real-Time): {timestamp}",
                        f"Device ID: {get_device_id()}",
                        f"SHIP_ID: {SHIP_ID}",
                        f"Heading: {device_data[SERIAL_DEVICES[0]].get('heading', 'Unknown') if device_data[SERIAL_DEVICES[0]].get('heading') is not None else 'Unknown'}"
                    ]
                    for dev in sorted(SERIAL_DEVICES):
                        label = "Top GPS" if dev == '/dev/ttyACM0' else "Bottom GPS"
                        data = device_data.get(dev, {})
                        output.extend([
                            f"{label} ({dev}):",
                            f"  Latitude: {data.get('latitude', 'Unknown')}",
                            f"  Longitude: {data.get('longitude', 'Unknown')}",
                            f"  Altitude (m): {data.get('altitude', 'Unknown')}",
                            f"  Speed (km/h): {data.get('speed', 'Unknown')}",
                            f"  Satellites: {data.get('satellites', 'Unknown')}",
                            f"  Satellite PRNs: {', '.join(data.get('satellite_prns', ['Unknown']))}"
                        ])
                    output_str = "\n".join(output) + "\n------------------------------------------------------------------------------------------------------\n"
                    logger.info(output_str)
                    try:
                        with open(current_output_file, 'a') as f:
                            f.write(output_str)
                    except Exception as e:
                        logger.error(f"Failed to write to output file: {e}")
                    if external_ws_connected:
                        gps_data_queue.put(output_str)
                    else:
                        logger.debug("Skipping queue insert due to disconnected external WebSocket")
                except json.JSONDecodeError as e:
                    logger.error(f"JSON decode error in GPS report: {e}")
                    continue
                except Exception as e:
                    logger.error(f"Error processing GPS report: {e}")
                    continue
            session.close()
        except Exception as e:
            logger.error(f"Failed to connect to gpsd: {e}")
            time.sleep(RECONNECT_DELAY)

async def run_gps_processing():
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, process_gps_data)

async def main():
    global app_start_time, current_output_file, ws_server, http_runner
    try:
        create_device_id()
        app_start_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        current_output_file = get_output_filename()
        await start_websocket_server()
        await asyncio.gather(
            run_gps_processing(),
            send_to_external_websocket()
        )
    except KeyboardInterrupt:
        logger.info("Shutting down gracefully...")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
    finally:
        if ws_server:
            ws_server.close()
            await ws_server.wait_closed()
        if http_runner:
            await http_runner.cleanup()
        end_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        logger.info(f"Application stopped at {end_time}")
        if current_output_file:
            try:
                with open(current_output_file, 'a') as f:
                    f.write(f"\n=== Application Ended at: {end_time} ===\n")
            except Exception as e:
                logger.error(f"Failed to write end time to output file: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Application terminated by user")
        sys.exit(0)
EOF

# Set permissions for gps_data.py
sudo chmod 755 "$GPS_SCRIPT_PATH"
sudo chown $USER:$USER "$GPS_SCRIPT_PATH"

# Create systemd service file
echo "Creating systemd service file..."
cat << EOF | sudo tee "$SYSTEMD_SERVICE"
[Unit]
Description=GPS Data Python Script Service
After=network.target

[Service]
ExecStart=$VENV_PATH/bin/python3 $GPS_SCRIPT_PATH
WorkingDirectory=/home/mdt
StandardOutput=inherit
StandardError=inherit
Restart=always
User=$USER

[Install]
WantedBy=multi-user.target
EOF

# Set permissions for systemd service file
sudo chmod 644 "$SYSTEMD_SERVICE"

# Reload systemd daemon
echo "Reloading systemd daemon..."
sudo systemctl daemon-reload

# Enable and start the service
echo "Enabling and starting GPS service..."
sudo systemctl enable gps-data.service
sudo systemctl start gps-data.service

echo "Installation complete! GPS service is now running."
echo "Check status with: sudo systemctl status gps-data.service"
echo "Logs are available at: $GPS_DIR/gps_websocket.log"