import websocket
import json

def on_message(ws, message):
    print("Received:", json.loads(message))

def on_error(ws, error):
    print("Error:", error)

def on_close(ws, code, reason):
    print("Closed:", code, reason)

def on_open(ws):
    print("Connected to WebSocket")

ws = websocket.WebSocketApp("ws://192.168.0.226:8765",
                            on_message=on_message,
                            on_error=on_error,
                            on_close=on_close,
                            on_open=on_open)
ws.run_forever()

