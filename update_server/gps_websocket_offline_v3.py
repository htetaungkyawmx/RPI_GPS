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
GPS_DATA_DIR = '/home/mdt/GPS'
GPSD_HOST = '127.0.0.1'
GPSD_PORT = 2947
WEBSOCKET_PORT = 8766
HTTP_PORT = 8080
EXTERNAL_WEBSOCKET_URL = 'ws://192.168.26.133:4002'
TIMEOUT = 10
RECONNECT_DELAY = 2
DATA_TIMEOUT = 30
SHIP_ID = "SHIP456"
BATCH_SEND_DELAY = 0.1
DEBOUNCE_INTERVAL = 0.5  # Debounce TPV reports only
JSON_LOG_FILE = os.path.join(GPS_DATA_DIR, "offline_gps_data.json")

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

def get_output_filename():
    """Generate output filename with current date and time."""
    now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    return os.path.join(GPS_DATA_DIR, f"gps_data_{now}.txt")

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
    devices.sort()
    return devices[:2]

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

def log_offline_data(gps_data):
    """Log GPS data to JSON file when no clients are connected and external server is unavailable."""
    if not all(
        gps.get('latitude') is not None and
        gps.get('longitude') is not None and
        gps.get('altitude') is not None and
        gps.get('speed') is not None and
        gps.get('satellites') is not None
        for gps in gps_data.get('gps_data', [])
    ):
        logger.info("Skipping offline logging of incomplete GPS data")
        return
    try:
        os.makedirs(GPS_DATA_DIR, exist_ok=True)
        with open(JSON_LOG_FILE, 'a') as f:
            json.dump(gps_data, f)
            f.write('\n')
        logger.info(f"Logged offline GPS data to {JSON_LOG_FILE}")
    except Exception as e:
        logger.error(f"Failed to log offline data: {e}")

async def clean_offline_data():
    """Remove entries from offline JSON file where any required GPS field is missing."""
    if not os.path.exists(JSON_LOG_FILE):
        return
    valid_lines = []
    try:
        with open(JSON_LOG_FILE, 'r') as f:
            for line in f:
                try:
                    data = json.loads(line.strip())
                    if all(
                        gps.get('latitude') is not None and
                        gps.get('longitude') is not None and
                        gps.get('altitude') is not None and
                        gps.get('speed') is not None and
                        gps.get('satellites') is not None
                        for gps in data.get('gps_data', [])
                    ):
                        valid_lines.append(line)
                except json.JSONDecodeError:
                    logger.error(f"Invalid JSON in offline log: {line}")
        with open(JSON_LOG_FILE, 'w') as f:
            for line in valid_lines:
                f.write(line)
        logger.info("Cleaned offline data entries with missing GPS values")
    except Exception as e:
        logger.error(f"Failed to clean offline data: {e}")

async def send_offline_data(websocket):
    """Send valid offline data to the client."""
    try:
        await clean_offline_data()
        if not os.path.exists(JSON_LOG_FILE):
            logger.info("No offline data to send")
            return
        with open(JSON_LOG_FILE, 'r') as f:
            for line in f:
                try:
                    gps_data = json.loads(line.strip())
                    if all(
                        gps.get('latitude') is not None and
                        gps.get('longitude') is not None and
                        gps.get('altitude') is not None and
                        gps.get('speed') is not None and
                        gps.get('satellites') is not None
                        for gps in gps_data.get('gps_data', [])
                    ):
                        await websocket.send(json.dumps(gps_data))
                        logger.info(f"Sent offline GPS data: {gps_data}")
                        await asyncio.sleep(BATCH_SEND_DELAY)
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

async def websocket_handler(websocket, path):
    """Handle WebSocket connections."""
    logger.info(f"New WebSocket connection from {websocket.remote_address}")
    connected_clients.add(websocket)
    try:
        # Send any existing data immediately
        if latest_gps_data:
            await websocket.send(json.dumps(latest_gps_data))
        
        # Keep connection alive
        while True:
            try:
                message = await websocket.recv()
                logger.debug(f"Received message from client: {message}")
            except websockets.exceptions.ConnectionClosed:
                break
            except Exception as e:
                logger.error(f"WebSocket error: {e}")
                break
                
    except Exception as e:
        logger.error(f"WebSocket handler error: {e}")
    finally:
        connected_clients.discard(websocket)
        logger.info(f"Client {websocket.remote_address} disconnected")

async def get_gps_data(request):
    """Handle HTTP GET /gps requests."""
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
    """Start the WebSocket server."""
    global ws_server
    if not is_port_free(WEBSOCKET_PORT):
        logger.error(f"Port {WEBSOCKET_PORT} is already in use")
        raise OSError(f"Port {WEBSOCKET_PORT} is already in use")
    
    ws_server = await websockets.serve(
        websocket_handler,
        "0.0.0.0",
        WEBSOCKET_PORT,
        ping_interval=20,
        ping_timeout=30,
        close_timeout=10
    )
    logger.info(f"WebSocket server started on ws://0.0.0.0:{WEBSOCKET_PORT}")
    return ws_server

async def start_http_server():
    """Start the HTTP server."""
    global http_runner
    if not is_port_free(HTTP_PORT):
        logger.error(f"Port {HTTP_PORT} is already in use")
        raise OSError(f"Port {HTTP_PORT} is already in use")
    
    app = web.Application()
    app.router.add_get('/gps', get_gps_data)
    http_runner = web.AppRunner(app)
    await http_runner.setup()
    site = web.TCPSite(http_runner, '0.0.0.0', HTTP_PORT)
    await site.start()
    logger.info(f"HTTP server started on http://0.0.0.0:{HTTP_PORT}")

async def send_to_external_websocket():
    """Send GPS data to the external WebSocket server."""
    global external_ws_connected
    while True:
        try:
            async with websockets.connect(EXTERNAL_WEBSOCKET_URL) as websocket:
                logger.info(f"Connected to external WebSocket server: {EXTERNAL_WEBSOCKET_URL}")
                external_ws_connected = True
                await send_offline_data(websocket)
                
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
            external_ws_connected = False
            await asyncio.sleep(RECONNECT_DELAY)

async def broadcast_gps_data():
    """Broadcast GPS data to local WebSocket clients."""
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
                
                if connected_clients:
                    tasks = []
                    for client in connected_clients.copy():
                        try:
                            tasks.append(client.send(json.dumps(parsed_data)))
                        except:
                            connected_clients.discard(client)
                    
                    if tasks:
                        await asyncio.gather(*tasks, return_exceptions=True)
                        logger.info(f"Broadcasted GPS data to {len(tasks)} clients")
                
                elif not external_ws_connected:
                    log_offline_data(parsed_data)
                    
            gps_data_queue.task_done()
            
        except Empty:
            await asyncio.sleep(0.1)
        except Exception as e:
            logger.error(f"Error broadcasting GPS data: {e}")
            await asyncio.sleep(1)

def process_gps_data():
    """Process GPS data from gpsd and put it into the queue."""
    global current_output_file, app_start_time

    # Log application start time
    app_start_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    start_message = f"\n=== Application Started at: {app_start_time} ===\n"
    logger.info(start_message.strip())

    # Create new output file with timestamp
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
            session = gps.gps(host=GPSD_HOST, port=GPSD_PORT, mode=gps.WATCH_ENABLE | gps.WATCH_JSON)
            while True:
                try:
                    report = session.next()
                    if not report:
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
                            continue  # Debounce TPV reports only
                        last_tpv_time[device] = current_time
                        
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
                        prns = [str(sat.get('PRN', '')) for sat in report.get('satellites', []) if sat.get('used', False) and sat.get('PRN')]
                        device_data[device].update({
                            'satellites': satellites if satellites > 0 else None,
                            'satellite_prns': prns
                        })
                        logger.info(f"Device {device} using {satellites} satellites with PRNs: {prns}")
                    
                    if all(
                        device_data[dev].get('latitude') is not None and
                        device_data[dev].get('longitude') is not None and
                        device_data[dev].get('altitude') is not None and
                        device_data[dev].get('speed') is not None and
                        device_data[dev].get('satellites') is not None
                        for dev in SERIAL_DEVICES
                    ):
                        output = [
                            f"GPS Data (Real-Time): {timestamp}",
                            f"Ship ID: {SHIP_ID}",
                            f"Device ID: {get_device_id()}",
                            f"Heading: {device_data[SERIAL_DEVICES[0]].get('heading', 'Unknown') if device_data[SERIAL_DEVICES[0]].get('heading') is not None else 'Unknown'}"
                        ]
                        
                        for idx, dev in enumerate(sorted(SERIAL_DEVICES)):
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
                        
                        output_str = "\n".join(output) + "\n---------------------------\n"
                        print(output_str)
                        logger.info(output_str)
                        
                        try:
                            with open(current_output_file, 'a') as f:
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

async def run_gps_processing():
    """Run GPS processing in an executor."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, process_gps_data)

async def main():
    """Main application entry point."""
    global app_start_time, current_output_file
    
    try:
        # Initialize
        app_start_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        current_output_file = get_output_filename()
        
        # Start servers
        await start_http_server()
        await start_websocket_server()
        
        # Start background tasks
        asyncio.create_task(run_gps_processing())
        asyncio.create_task(broadcast_gps_data())
        asyncio.create_task(send_to_external_websocket())
        
        # Keep application running
        while True:
            await asyncio.sleep(1)
            
    except KeyboardInterrupt:
        logger.info("Shutting down gracefully...")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
    finally:
        # Cleanup
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
