import asyncio
import websockets
import json
import logging
import re

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/home/mdt/ws_server.log'),
        logging.StreamHandler()
    ]
)

# Set to keep track of connected clients
connected_clients = set()

async def parse_gps_data(gps_text):
    """Parse GPS text data into a structured JSON object."""
    try:
        data = {
            "timestamp": "",
            "device_id": "",
            "top_gps": {"latitude": None, "longitude": None, "altitude": None, "speed": None, "satellites": None},
            "bottom_gps": {"latitude": None, "longitude": None, "altitude": None, "speed": None, "satellites": None}
        }
        lines = gps_text.strip().split("\n")
        for line in lines:
            if "GPS Data (Real-Time):" in line:
                data["timestamp"] = line.split(":", 1)[1].strip()
            elif "Device ID:" in line:
                data["device_id"] = line.split(":", 1)[1].strip()
            elif "Top GPS (/dev/ttyACM0):" in line:
                current = data["top_gps"]
            elif "Bottom GPS (/dev/ttyACM1):" in line:
                current = data["bottom_gps"]
            elif "Latitude:" in line:
                current["latitude"] = float(line.split(":", 1)[1].strip()) if line.split(":", 1)[1].strip() != "Unknown" else None
            elif "Longitude:" in line:
                current["longitude"] = float(line.split(":", 1)[1].strip()) if line.split(":", 1)[1].strip() != "Unknown" else None
            elif "Altitude (m):" in line:
                current["altitude"] = float(line.split(":", 1)[1].strip()) if line.split(":", 1)[1].strip() != "Unknown" else None
            elif "Speed (km/h):" in line:
                current["speed"] = float(line.split(":", 1)[1].strip()) if line.split(":", 1)[1].strip() != "Unknown" else None
            elif "Satellites:" in line:
                current["satellites"] = int(line.split(":", 1)[1].strip()) if line.split(":", 1)[1].strip() != "Unknown" else None
        return data
    except Exception as e:
        logging.error(f"Failed to parse GPS data: {e}")
        return None

async def handle_connection(websocket, path=None):
    logging.info("Client connected")
    # Register client
    connected_clients.add(websocket)
    try:
        async for message in websocket:
            try:
                data = json.loads(message)
                gps_text = data.get("gps_data", "")
                parsed_data = await parse_gps_data(gps_text)
                if parsed_data:
                    # Broadcast parsed data to all connected clients
                    for client in connected_clients:
                        try:
                            await client.send(json.dumps(parsed_data))
                        except websockets.exceptions.ConnectionClosed:
                            continue
                    logging.info(f"Broadcasted parsed GPS data: {parsed_data}")
            except json.JSONDecodeError:
                logging.error("Invalid JSON received")
            except Exception as e:
                logging.error(f"Error processing message: {e}")
    except websockets.exceptions.ConnectionClosed:
        logging.info("Client disconnected")
    finally:
        # Unregister client
        connected_clients.remove(websocket)

async def main():
    server = await websockets.serve(handle_connection, "0.0.0.0", 8765)
    logging.info("WebSocket server started on ws://localhost:8765")
    await server.wait_closed()

if __name__ == "__main__":
    asyncio.run(main())
