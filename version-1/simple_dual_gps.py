import gps
import time
import logging
import subprocess
import os
from datetime import datetime
import pytz
import websocket
import json
import threading
import queue

# Setup logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/home/mdt/gps_simple.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Configuration
SERIAL_DEVICES = ['/dev/ttyACM1', '/dev/ttyACM0']
OUTPUT_FILE = '/home/mdt/Desktop/GPS/simple_dual_gps_output.txt'
GPSD_HOST = '127.0.0.1'
GPSD_PORT = 2947
MAX_ATTEMPTS = 5
TIMEOUT = 10
WEBSOCKET_URL = "ws://localhost:8765"  # WebSocket server URL

# WebSocket client setup
ws = None
ws_lock = threading.Lock()
data_queue = queue.Queue()

def websocket_thread():
    """Run WebSocket connection in a separate thread."""
    global ws
    while True:
        try:
            with ws_lock:
                if not ws or not ws.connected:
                    try:
                        ws = websocket.create_connection(WEBSOCKET_URL)
                        logger.info(f"Connected to WebSocket at {WEBSOCKET_URL}")
                    except Exception as e:
                        logger.error(f"Failed to connect to WebSocket: {e}")
                        time.sleep(5)
                        continue

            # Send queued data
            while not data_queue.empty():
                with ws_lock:
                    if ws and ws.connected:
                        try:
                            data = data_queue.get()
                            ws.send(json.dumps({"gps_data": data}))
                            logger.debug("Sent GPS data to WebSocket")
                        except Exception as e:
                            logger.error(f"Failed to send to WebSocket: {e}")
                            ws = None
                            data_queue.put(data)  # Re-queue data
                            break
            time.sleep(0.1)
        except Exception as e:
            logger.error(f"WebSocket thread error: {e}")
            time.sleep(5)

def send_to_websocket(data):
    """Queue data to be sent to the WebSocket server."""
    data_queue.put(data)
    logger.debug("Queued GPS data for WebSocket")

def run_command(cmd):
    """Run a shell command and return success status, stdout, and stderr."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT)
        logger.debug(f"Command {cmd}: stdout={result.stdout}, stderr={result.stderr}")
        return result.returncode == 0, result.stdout, result.stderr
    except subprocess.SubprocessError as e:
        logger.error(f"Command {cmd} failed: {e}")
        return False, "", str(e)

def ensure_gpsd_running():
    """Ensure gpsd is running manually."""
    success, stdout, stderr = run_command(['pidof', 'gpsd'])
    if success:
        logger.info("gpsd is already running")
        return True
    logger.info("Starting gpsd manually")
    cmd = ['sudo', 'gpsd', '-n', '-F', '/var/run/gpsd.sock', '-G', '127.0.0.1', '-b'] + SERIAL_DEVICES
    try:
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        time.sleep(2)
        if process.poll() is None:
            logger.info("gpsd started successfully")
            return True
        stdout, stderr = process.communicate()
        logger.error(f"Failed to start gpsd: stdout={stdout}, stderr={stderr}")
    except Exception as e:
        logger.error(f"Failed to start gpsd: {e}")
    return False

def main():
    logger.info("Starting simple_dual_gps.py")
    # Set baud rates
    for device in SERIAL_DEVICES:
        run_command(['sudo', 'stty', '-F', device, '9600'])

    if not ensure_gpsd_running():
        logger.error("Cannot proceed without gpsd running")
        return

    # Start WebSocket thread
    threading.Thread(target=websocket_thread, daemon=True).start()

    client = None
    try:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                client = gps.gps(host=GPSD_HOST, port=GPSD_PORT, mode=gps.WATCH_ENABLE | gps.WATCH_NEWSTYLE)
                logger.info("Connected to gpsd")
                break
            except Exception as e:
                logger.error(f"Failed to connect to gpsd (attempt {attempt}): {e}")
                if attempt == MAX_ATTEMPTS:
                    logger.error("Max connection attempts reached")
                    return
                time.sleep(2)
                ensure_gpsd_running()

        device_data = {device: {} for device in SERIAL_DEVICES}
        os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
        while True:
            try:
                report = client.next()
                if report['class'] == 'TPV':
                    device = report.get('device', 'Unknown')
                    if device not in SERIAL_DEVICES:
                        continue
                    timestamp = datetime.now(pytz.UTC)
                    lat = report.get('lat', 'Unknown')
                    lon = report.get('lon', 'Unknown')
                    alt = report.get('alt', 'Unknown')
                    speed = report.get('speed', 'Unknown')
                    if isinstance(speed, (int, float)):
                        speed = round(speed * 3.6, 2)  # Convert m/s to km/h
                    satellites = device_data.get(device, {}).get('satellites', 'Unknown')
                    device_data[device] = {
                        'timestamp': timestamp.strftime('%Y-%m-%d %H:%M:%S.%f'),
                        'latitude': lat,
                        'longitude': lon,
                        'altitude': alt,
                        'speed': speed,
                        'satellites': satellites
                    }

                    output = [
                        f"GPS Data (Real-Time): {timestamp.strftime('%Y-%m-%d %H:%M:%S.%f')}",
                        f"Device ID: 10000000e5ab9cbe"
                    ]
                    for device in SERIAL_DEVICES:
                        label = "Top GPS" if device == '/dev/ttyACM0' else "Bottom GPS"
                        data = device_data.get(device, {})
                        output.extend([
                            f"{label} ({device}):",
                            f"  Latitude: {data.get('latitude', 'Unknown')}",
                            f"  Longitude: {data.get('longitude', 'Unknown')}",
                            f"  Altitude (m): {data.get('altitude', 'Unknown')}",
                            f"  Speed (km/h): {data.get('speed', 'Unknown')}",
                            f"  Satellites: {data.get('satellites', 'Unknown')}"
                        ])
                    output_str = "\n".join(output) + "\n---\n"
                    print(output_str)
                    logger.info(output_str)
                    try:
                        with open(OUTPUT_FILE, 'a') as f:
                            f.write(output_str)
                    except Exception as e:
                        logger.error(f"Failed to write to output file: {e}")
                    # Send GPS data to WebSocket
                    send_to_websocket(output_str)
                elif report['class'] == 'SKY':
                    satellites = len([sat for sat in report.get('satellites', []) if sat.get('used', False)])
                    for device in SERIAL_DEVICES:
                        if device in device_data:
                            device_data[device]['satellites'] = satellites
            except Exception as e:
                logger.error(f"Error processing report: {e}")
                time.sleep(1)
                continue
            time.sleep(0.5)
    except KeyboardInterrupt:
        logger.info("Stopping GPS")
    except Exception as e:
        logger.error(f"Error: {e}")
    finally:
        if client:
            client.close()
        with ws_lock:
            if ws:
                ws.close()

if __name__ == "__main__":
    main()
