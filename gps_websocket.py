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

# Global variable to store latest GPS data
latest_gps_data = None
connected_clients = set()

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
            if "GPS Data (Real-Time):" in line:
                data["timestamp"] = line.split(":", 1)[1].strip()
            elif "Heading:" in line:
                data["heading"] = float(line.split(":", 1)[1].strip()) if line.split(":", 1)[1].strip() != "Unknown" else None
            elif "Top GPS (/dev/ttyACM0):" in line:
                current_index = 0
            elif "Bottom GPS (/dev/ttyACM1):" in line:
                current_index = 1
            elif "Latitude:" in line and current_index is not None:
                data["gps_data"][current_index]["latitude"] = float(line.split(":", 1)[1].strip()) if line.split(":", 1)[1].strip() != "Unknown" else None
            elif "Longitude:" in line and current_index is not None:
                data["gps_data"][current_index]["longitude"] = float(line.split(":", 1)[1].strip()) if line.split(":", 1)[1].strip() != "Unknown" else None
            elif "Altitude (m):" in line and current_index is not None:
                data["gps_data"][current_index]["altitude"] = float(line.split(":", 1)[1].strip()) if line.split(":", 1)[1].strip() != "Unknown" else None
            elif "Speed (km/h):" in line and current_index is not None:
                data["gps_data"][current_index]["speed"] = float(line.split(":", 1)[1].strip()) if line.split(":", 1)[1].strip() != "Unknown" else None
            elif "Satellites:" in line and current_index is not None:
                data["gps_data"][current_index]["satellites"] = int(line.split(":", 1)[1].strip()) if line.split(":", 1)[1].strip() != "Unknown" else None
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

def process_gps_data():
    """Process GPS data and send to WebSocket clients."""
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
                heading = report.get('track', 'Unknown')
                if isinstance(speed, (int, float)):
                    speed = round(speed * 3.6, 2)  # Convert m/s to km/h
                if isinstance(heading, (int, float)):
                    heading = round(heading, 1)  # Round to 1 decimal place
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
                    f"Device ID: 10000000e123456be",
                    f"Heading: {heading}"
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
                output_str = "\n".join(output) + "\n---------------------------\n"
                print(output_str)
                logger.info(output_str)
                try:
                    with open(OUTPUT_FILE, 'a') as f:
                        f.write(output_str)
                except Exception as e:
                    logger.error(f"Failed to write to output file: {e}")

                # Update latest GPS data and broadcast
                global latest_gps_data
                parsed_data = parse_gps_data(output_str)
                if parsed_data:
                    latest_gps_data = parsed_data
                    for client in connected_clients.copy():
                        try:
                            asyncio.run_coroutine_threadsafe(
                                client.send(json.dumps(parsed_data)),
                                asyncio.get_event_loop()
                            )
                        except websockets.exceptions.ConnectionClosed:
                            connected_clients.discard(client)

            elif report['class'] == 'SKY':
                satellites = len([sat for sat in report.get('satellites', []) if sat.get('used', False)])
                for device in SERIAL_DEVICES:
                    if device in device_data:
                        device_data[device]['satellites'] = satellites
        except Exception as e:
            logger.error(f"Error processing report: {e}")
            time.sleep(RECONNECT_DELAY)
            try:
                client = gps.gps(host=GPSD_HOST, port=GPSD_PORT, mode=gps.WATCH_ENABLE | gps.WATCH_NEWSTYLE)
                logger.info("Reconnected to gpsd")
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
            server.wait_closed()
        )
    except KeyboardInterrupt:
        logger.info("Shutting down")
        server.close()
        await server.wait_closed()

if __name__ == "__main__":
    asyncio.run(main())
