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

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/home/mdt/gps_websocket.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Configuration
OUTPUT_FILE = '/home/mdt/GPS/gps_output.txt'
JSON_LOG_FILE = '/home/mdt/gps_offline_data.json'
GPSD_HOST = '127.0.0.1'
GPSD_PORT = 2947
WEBSOCKET_PORT = 8765
HTTP_PORT = 8080
EXTERNAL_WEBSOCKET_URL = 'ws://192.168.0.164:4001'
TIMEOUT = 10
RECONNECT_DELAY = 2
DATA_TIMEOUT = 30
SHIP_ID = "SHIP123"  # Replace with actual ship ID
BATCH_SEND_DELAY = 0.1  # Delay between sending batched offline data (seconds)

# Global variables
latest_gps_data = None
connected_clients = set()
gps_data_queue = Queue()

def get_device_id():
    """Retrieve the Raspberry Pi's serial number as the device ID."""
    try:
        with open('/proc/cpuinfo', 'r') as f:
            for line in f:
                if line.startswith('Serial'):
                    serial = line.split(':')[1].strip()
                    if serial:
                        logger.info(f"Detected device ID: {serial}")
                        return serial
        logger.error("No serial number found in /proc/cpuinfo")
        return "unknown_device_id"
    except Exception as e:
        logger.error(f"Error retrieving device ID: {e}")
        return "unknown_device_id"

def is_port_free(port):
    """Check if a port is free."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("0.0.0.0", port))
            return True
        except OSError:
            return False

def detect_gps_devices():
    """Detect connected GPS devices."""
    devices = glob.glob('/dev/ttyACM*') + glob.glob('/dev/ttyUSB*')
    if not devices:
        logger.error("No GPS devices detected")
        return []
    logger.info(f"Detected GPS devices: {devices}")
    devices.sort()  # Ensure consistent order
    return devices[:2]  # Limit to two devices

def run_command(cmd):
    """Run a shell command and return success status, stdout, and stderr."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT)
        logger.debug(f"Command {cmd}: stdout={result.stdout}, stderr={result.stderr}")
        return result.returncode == 0, result.stdout, result.stderr
    except subprocess.SubprocessError as e:
        logger.error(f"Command {cmd} failed: {e}")
        return False, "", str(e)

def ensure_gpsd_running(devices):
    """Ensure gpsd is running."""
    run_command(['sudo', 'pkill', '-9', 'gpsd'])
    time.sleep(1)  # Wait for termination
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

def log_offline_data(gps_data):
    """Log GPS data to JSON file when no clients are connected or external server is unreachable."""
    try:
        os.makedirs(os.path.dirname(JSON_LOG_FILE), exist_ok=True)
        with open(JSON_LOG_FILE, 'a') as f:
            json.dump(gps_data, f)
            f.write('\n')  # Newline-separated JSON
        logger.info(f"Logged offline GPS data to {JSON_LOG_FILE}")
    except Exception as e:
        logger.error(f"Failed to log offline data: {e}")

async def send_offline_data(websocket):
    """Send all offline data from JSON log file to the client."""
    try:
        if not os.path.exists(JSON_LOG_FILE):
            logger.info("No offline data to send")
            return
        with open(JSON_LOG_FILE, 'r') as f:
            for line in f:
                try:
                    gps_data = json.loads(line.strip())
                    await websocket.send(json.dumps(gps_data))
                    logger.info(f"Sent offline GPS data: {gps_data}")
                    await asyncio.sleep(BATCH_SEND_DELAY)  # Avoid overwhelming the client
                except json.JSONDecodeError:
                    logger.error(f"Invalid JSON in offline log: {line}")
                except Exception as e:
                    logger.error(f"Error sending offline data: {e}")
                    raise
        logger.info("Finished sending offline data")
        try:
            open(JSON_LOG_FILE, 'w').close()
            logger.info(f"Cleared offline log file {JSON_LOG_FILE}")
        except Exception as e:
            logger.error(f"Failed to clear offline log file: {e}")
    except Exception as e:
        logger.error(f"Error processing offline data: {e}")
        raise

async def parse_gps_data(gps_text):
    """Parse GPS text data into a structured JSON object."""
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
            if "GPS Data (Real-Time):" in line:
                data["timestamp"] = line.split(":", 1)[1].strip()
            elif "Heading:" in line:
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
                    data["gps_data"][current_index]["longitude"] = float(lon_str) if lat_str != "Unknown" else None
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
                    data["gps_data"][current_index]["satellites"] = int(sat_str) if sat_str != "Unknown" else None
                except ValueError:
                    data["gps_data"][current_index]["satellites"] = None
            elif "Satellite PRNs:" in line and current_index is not None:
                prns = line.split(":", 1)[1].strip()
                data["gps_data"][current_index]["satellite_prns"] = [prn.strip() for prn in prns.split(",")] if prns != "Unknown" else []
        return data
    except Exception as e:
        logger.error(f"Error parsing GPS data: {e}")
        return None

async def websocket_handler(websocket, path=None):
    """Handle WebSocket connections."""
    logger.info("WebSocket client connected")
    connected_clients.add(websocket)
    try:
        await send_offline_data(websocket)
        async for message in websocket:
            try:
                data = json.loads(message)
                gps_text = data.get("gps_data", "")
                parsed_data = await parse_gps_data(gps_text)
                if parsed_data:
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
    """Handle HTTP GET /gps requests."""
    global latest_gps_data
    if latest_gps_data:
        return web.json_response(latest_gps_data)
    return web.json_response({"error": "No GPS data available"}, status=404)

async def start_websocket_server():
    """Start the WebSocket server."""
    if not is_port_free(WEBSOCKET_PORT):
        logger.error(f"Port {WEBSOCKET_PORT} is already in use")
        raise OSError(f"Port {WEBSOCKET_PORT} is already in use")
    server = await websockets.serve(websocket_handler, "0.0.0.0", WEBSOCKET_PORT)
    logger.info(f"WebSocket server started on ws://0.0.0.0:{WEBSOCKET_PORT}")
    return server

async def start_http_server():
    """Start the HTTP server."""
    app = web.Application()
    app.router.add_get('/gps', get_gps_data)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', HTTP_PORT)
    await site.start()
    logger.info(f"HTTP server started on http://0.0.0.0:{HTTP_PORT}")

async def send_to_external_websocket():
    """Connect to external WebSocket server and send GPS data."""
    while True:
        try:
            async with websockets.connect(EXTERNAL_WEBSOCKET_URL) as websocket:
                logger.info(f"Connected to external WebSocket server: {EXTERNAL_WEBSOCKET_URL}")
                await send_offline_data(websocket)  # Send any offline data
                while True:
                    try:
                        gps_text = gps_data_queue.get_nowait()
                        parsed_data = await parse_gps_data(gps_text)
                        if parsed_data:
                            global latest_gps_data
                            latest_gps_data = parsed_data
                            try:
                                await websocket.send(json.dumps(parsed_data))
                                logger.info(f"Sent GPS data to external server: {parsed_data}")
                            except Exception as e:
                                logger.error(f"Failed to send to external server: {e}")
                                log_offline_data(parsed_data)
                                raise
                        gps_data_queue.task_done()
                    except Empty:
                        await asyncio.sleep(0.1)
                    except Exception as e:
                        logger.error(f"Error processing GPS data for external server: {e}")
                        raise
        except Exception as e:
            logger.error(f"Failed to connect to external WebSocket server: {e}")
            await asyncio.sleep(RECONNECT_DELAY)

async def broadcast_gps_data():
    """Broadcast GPS data to local WebSocket clients."""
    while True:
        try:
            gps_text = gps_data_queue.get_nowait()
            parsed_data = await parse_gps_data(gps_text)
            if parsed_data:
                global latest_gps_data
                latest_gps_data = parsed_data
                if connected_clients:
                    for client in connected_clients.copy():
                        try:
                            await client.send(json.dumps(parsed_data))
                            logger.info(f"Broadcasted GPS data to local clients: {parsed_data}")
                        except websockets.exceptions.ConnectionClosed:
                            connected_clients.discard(client)
                else:
                    log_offline_data(parsed_data)
            gps_data_queue.task_done()
        except Empty:
            await asyncio.sleep(0.1)
        except Exception as e:
            logger.error(f"Error broadcasting GPS data to local clients: {e}")
            await asyncio.sleep(0.1)

def process_gps_data():
    """Process GPS data and put it into the queue."""
    logger.info("Starting GPS data processing")
    SERIAL_DEVICES = detect_gps_devices()
    if not SERIAL_DEVICES:
        logger.error("No GPS devices found, exiting")
        return
    for device in SERIAL_DEVICES:
        success, _, _ = run_command(['sudo', 'stty', '-F', device, '9600'])
        if not success:
            logger.warning(f"Failed to set baud rate for {device}")
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
            session = gps.gps(host=GPSD_HOST, port=GPSD_PORT, mode=gps.WATCH_ENABLE | gps.WATCH_JSON)
            while True:
                try:
                    report = session.next()
                    if not report:
                        continue
                    current_time = time.time()
                    for device in SERIAL_DEVICES:
                        if current_time - last_data_time[device] > DATA_TIMEOUT:
                            logger.warning(f"No data received from {device} for {DATA_TIMEOUT} seconds")
                    device = getattr(report, 'device', None)
                    if device not in SERIAL_DEVICES:
                        logger.debug(f"Ignoring report for unknown device: {device}")
                        continue
                    last_data_time[device] = current_time
                    timestamp = datetime.now(pytz.UTC).strftime('%Y-%m-%d %H:%M:%S.%f')
                    if report.get('class') == 'TPV':
                        lat = getattr(report, 'lat', None)
                        lon = getattr(report, 'lon', None)
                        alt = getattr(report, 'alt', None)
                        speed = getattr(report, 'speed', None)
                        heading = getattr(report, 'track', None)
                        if isinstance(speed, (int, float)):
                            speed = round(speed * 3.6, 2)  # Convert m/s to km/h
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
                        prns = [str(sat.get('PRN', 'Unknown')) for sat in report.get('satellites', []) if sat.get('used', False)]
                        device_data[device].update({
                            'satellites': satellites,
                            'satellite_prns': prns
                        })
                        logger.info(f"Device {device} using {satellites} satellites with PRNs: {prns}")
                    output = [
                        f"GPS Data (Real-Time): {timestamp}",
                        f"Ship ID: {SHIP_ID}",
                        f"Device ID: {get_device_id()}",
                        f"Heading: {device_data[device].get('heading', 'Unknown') if device_data[device].get('heading') is not None else 'Unknown'}"
                    ]
                    for idx, dev in enumerate(sorted(SERIAL_DEVICES)):
                        label = "Top GPS" if idx == 0 else "Bottom GPS"
                        data = device_data.get(dev, {})
                        output.extend([
                            f"{label} ({dev}):",
                            f"  Latitude: {data.get('latitude', 'Unknown') if data.get('latitude') is not None else 'Unknown'}",
                            f"  Longitude: {data.get('longitude', 'Unknown') if data.get('longitude') is not None else 'Unknown'}",
                            f"  Altitude (m): {data.get('altitude', 'Unknown') if data.get('altitude') is not None else 'Unknown'}",
                            f"  Speed (km/h): {data.get('speed', 'Unknown') if data.get('speed') is not None else 'Unknown'}",
                            f"  Satellites: {data.get('satellites', 'Unknown') if data.get('satellites') is not None else 'Unknown'}",
                            f"  Satellite PRNs: {', '.join(data.get('satellite_prns', ['Unknown']))}"
                        ])
                    output_str = "\n".join(output) + "\n---------------------------\n"
                    print(output_str)
                    logger.info(output_str)
                    try:
                        os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
                        with open(OUTPUT_FILE, 'a') as f:
                            f.write(output_str)
                    except Exception as e:
                        logger.error(f"Failed to write to output file: {e}")
                    gps_data_queue.put(output_str)
                except Exception as e:
                    logger.error(f"Error processing report: {e}")
                    break
            session.close()
        except Exception as e:
            logger.error(f"Failed to connect to gpsd: {e}")
            time.sleep(RECONNECT_DELAY)

async def main():
    """Run WebSocket and HTTP servers with GPS data processing concurrently."""
    server = await start_websocket_server()
    await start_http_server()
    loop = asyncio.get_event_loop()
    try:
        await asyncio.gather(
            loop.run_in_executor(None, process_gps_data),
            broadcast_gps_data(),
            send_to_external_websocket(),
            server.wait_closed()
        )
    except KeyboardInterrupt:
        logger.info("Shutting down")
        server.close()
        await server.wait_closed()

if __name__ == "__main__":
    asyncio.run(main())
