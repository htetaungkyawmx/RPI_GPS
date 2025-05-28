import gps
import time
import logging
import subprocess
import os
import asyncio
import websockets
import json
import pytz
from datetime import datetime
from aiohttp import web
from queue import Queue

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
SERIAL_DEVICES = ['/dev/ttyACM1', '/dev/ttyACM0']  # Bottom, Top GPS
OUTPUT_FILE = '/home/mdt/Desktop/GPS/gps_output.txt'
GPSD_HOST = '127.0.0.1'
GPSD_PORT = 2947
WEBSOCKET_PORT = 8765
HTTP_PORT = 8080
TIMEOUT = 10
MAX_ATTEMPTS = 5
RECONNECT_DELAY = 2
DATA_TIMEOUT = 30  # seconds

# Global variables
latest_gps_data = None
connected_clients = set()
gps_data_queue = Queue()

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
    """Ensure gpsd is running."""
    success, stdout, stderr = run_command(['pidof', 'gpsd'])
    if success:
        logger.info("gpsd is already running")
        return True
    logger.info("Starting gpsd manually")
    cmd = ['sudo', 'gpsd', '-n', '-F', '/var/run/gpsd.sock'] + SERIAL_DEVICES
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

async def parse_gps_data(gps_text):
    """Parse GPS text data into a structured JSON object."""
    try:
        data = {
            "timestamp": "",
            "device_id": "10000000e123456be",
            "heading": None,
            "gps_data": [
                {"gps": "top_gps", "latitude": None, "longitude": None, "altitude": None, "speed": None, "satellites": None},
                {"gps": "bottom_gps", "latitude": None, "longitude": None, "altitude": None, "speed": None, "satellites": None}
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
            elif "Top GPS (/dev/ttyACM0):" in line:
                current_index = 0
            elif "Bottom GPS (/dev/ttyACM1):" in line:
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
                    data["gps_data"][current_index]["satellites"] = int(sat_str) if sat_str != "Unknown" else None
                except ValueError:
                    data["gps_data"][current_index]["satellites"] = None
        return data
    except Exception as e:
        logger.error(f"Error parsing GPS data: {e}")
        return None

async def websocket_handler(websocket, path=None):
    """Handle WebSocket connections."""
    logger.info("WebSocket client connected")
    connected_clients.add(websocket)
    try:
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
                    logger.info(f"Broadcasted GPS data: {parsed_data}")
            except json.JSONDecodeError:
                logger.error("Invalid JSON received")
    except websockets.exceptions.ConnectionError:
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

async def broadcast_gps_data():
    """Broadcast GPS data from the queue to connected WebSocket clients."""
    while True:
        try:
            gps_text = gps_data_queue.get_nowait()
            parsed_data = await parse_gps_data(gps_text)
            if parsed_data:
                global latest_gps_data
                latest_gps_data = parsed_data
                for client in connected_clients.copy():
                    try:
                        await client.send(json.dumps(parsed_data))
                        logger.info(f"Broadcasted GPS data: {parsed_data}")
                    except websockets.exceptions.ConnectionClosed:
                        connected_clients.discard(client)
            gps_data_queue.task_done()
        except Queue.Empty:
            await asyncio.sleep(0.1)  # Prevent tight loop
        except Exception as e:
            logger.error(f"Error broadcasting GPS data: {e}")

def process_gps_data():
    """Process GPS data and put it into the queue."""
    logger.info("Starting GPS data processing")
    for device in SERIAL_DEVICES:
        success, _, _ = run_command(['sudo', 'stty', '-F', device, '9600'])
        if not success:
            logger.warning(f"Failed to set baud rate for {device}")

    if not ensure_gpsd_running():
        logger.error("Cannot proceed without gpsd running")
        return

    client = None
    attempt = 0
    while attempt < MAX_ATTEMPTS:
        try:
            client = gps.gps(host=GPSD_HOST, port=GPSD_PORT, mode=gps.WATCH_ENABLE | gps.WATCH_NEWSTYLE)
            logger.info("Connected to gpsd")
            break
        except Exception as e:
            logger.error(f"Failed to connect to gpsd (attempt {attempt + 1}): {e}")
            attempt += 1
            if attempt == MAX_ATTEMPTS:
                logger.error("Max connection attempts reached")
                return
            time.sleep(RECONNECT_DELAY)

    device_data = {device: {
        'latitude': None,
        'longitude': None,
        'altitude': None,
        'speed': None,
        'satellites': None,
        'timestamp': None,
        'heading': None
    } for device in SERIAL_DEVICES}
    last_data_time = {device: time.time() for device in SERIAL_DEVICES}

    while True:
        try:
            report = client.next()
            current_time = time.time()
            for device in SERIAL_DEVICES:
                if current_time - last_data_time[device] > DATA_TIMEOUT:
                    logger.warning(f"No data received from {device} for {DATA_TIMEOUT} seconds")

            if report['class'] == 'TPV':
                device = report.get('device', None)
                if device not in SERIAL_DEVICES:
                    logger.debug(f"Ignoring TPV report for unknown device: {device}")
                    continue
                last_data_time[device] = current_time
                timestamp = datetime.now(pytz.UTC).strftime('%Y-%m-%d %H:%M:%S.%f')
                lat = report.get('lat', None)
                lon = report.get('lon', None)
                alt = report.get('alt', None)
                speed = report.get('speed', None)
                heading = report.get('track', None)
                if isinstance(speed, (int, float)):
                    speed = round(speed * 3.6, 2)  # Convert m/s to km/h
                if isinstance(heading, (int, float)):
                    heading = round(heading, 1)  # Round to 1 decimal place

                device_data[device].update({
                    'timestamp': timestamp,
                    'latitude': lat,
                    'longitude': lon,
                    'altitude': alt,
                    'speed': speed,
                    'heading': heading
                })

                # Use the heading from the most recent device update
                current_heading = device_data[device].get('heading', None)

                output = [
                    f"GPS Data (Real-Time): {timestamp}",
                    f"Device ID: 10000000e123456be",
                    f"Heading: {current_heading if current_heading is not None else 'Unknown'}"
                ]
                for device in SERIAL_DEVICES:
                    label = "Top GPS" if device == '/dev/ttyACM0' else "Bottom GPS"
                    data = device_data.get(device, {})
                    output.extend([
                        f"{label} ({device}):",
                        f"  Latitude: {data.get('latitude', 'Unknown') if data.get('latitude') is not None else 'Unknown'}",
                        f"  Longitude: {data.get('longitude', 'Unknown') if data.get('longitude') is not None else 'Unknown'}",
                        f"  Altitude (m): {data.get('altitude', 'Unknown') if data.get('altitude') is not None else 'Unknown'}",
                        f"  Speed (km/h): {data.get('speed', 'Unknown') if data.get('speed') is not None else 'Unknown'}",
                        f"  Satellites: {data.get('satellites', 'Unknown') if data.get('satellites') is not None else 'Unknown'}"
                    ])
                output_str = "\n".join(output) + "\n---------------------------\n"
                print(output_str)
                logger.info(output_str)
                try:
                    with open(OUTPUT_FILE, 'a') as f:
                        f.write(output_str)
                except Exception as e:
                    logger.error(f"Failed to write to output file: {e}")

                gps_data_queue.put(output_str)

            elif report['class'] == 'SKY':
                device = report.get('device', None)
                if device not in SERIAL_DEVICES:
                    logger.debug(f"Ignoring SKY report for unknown device: {device}")
                    continue
                last_data_time[device] = current_time
                try:
                    satellites = len([sat for sat in report.get('satellites', []) if sat.get('used', False)])
                    device_data[device]['satellites'] = satellites
                    logger.debug(f"Updated satellites for {device}: {satellites}")
                except Exception as e:
                    logger.error(f"Error processing SKY report for {device}: {e}")
        except Exception as e:
            logger.error(f"Error processing report: {e}")
            time.sleep(RECONNECT_DELAY)
            try:
                client = gps.gps(host=GPSD_HOST, port=GPSD_PORT, mode=gps.WATCH_ENABLE | gps.WATCH_NEWSTYLE)
                logger.info("Reconnected to gpsd")
                # Reset device_data to avoid stale data
                device_data.update({device: {
                    'latitude': None,
                    'longitude': None,
                    'altitude': None,
                    'speed': None,
                    'satellites': None,
                    'timestamp': None,
                    'heading': None
                } for device in SERIAL_DEVICES})
            except Exception as reconnect_e:
                logger.error(f"Failed to reconnect to gpsd: {reconnect_e}")
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
            server.wait_closed()
        )
    except KeyboardInterrupt:
        logger.info("Shutting down")
        server.close()
        await server.wait_closed()

if __name__ == "__main__":
    asyncio.run(main())
