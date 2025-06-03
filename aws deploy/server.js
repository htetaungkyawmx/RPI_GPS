const WebSocket = require('ws');
const fs = require('fs');
const path = require('path');

const WS_PORT = 4001;
const WS_URL = `ws://localhost:${WS_PORT}`;
const MAX_CLIENTS = 200;

const LOG_FILE_PATH = path.join(__dirname, 'ships_log.jsonl');

const wss = new WebSocket.Server({ port: WS_PORT });
let clients = [];
let shipLocations = {};

wss.on('connection', function connection(ws) {
  if (clients.length >= MAX_CLIENTS) {
    console.log('Max clients (200) reached, rejecting new connection');
    ws.close(1000, 'Maximum clients reached');
    return;
  }

  console.log(`Client connected. Total clients: ${clients.length + 1}`);
  clients.push(ws);

  ws.send(JSON.stringify({
    type: 'welcome',
    message: `Connected to WebSocket server at ${WS_URL}`,
    clientCount: clients.length
  }));

  ws.on('message', function incoming(data) {
    try {
      const msg = JSON.parse(data);
      if (msg.ship_id && Array.isArray(msg.gps_data) && msg.gps_data.length > 0) {
        const validGpsData = msg.gps_data.filter(gpsEntry => 
          typeof gpsEntry.latitude === 'number' && 
          typeof gpsEntry.longitude === 'number' && 
          gpsEntry.gps
        ).map(gpsEntry => ({
          gps: gpsEntry.gps,
          latitude: gpsEntry.latitude,
          longitude: gpsEntry.longitude,
          altitude: gpsEntry.altitude ?? null,
          speed: gpsEntry.speed ?? null,
          satellites: gpsEntry.satellites ?? null,
          satellite_prns: Array.isArray(gpsEntry.satellite_prns) ? gpsEntry.satellite_prns : []
        }));

        if (validGpsData.length > 0) {
          shipLocations[msg.ship_id] = {
            timestamp: msg.timestamp || new Date().toISOString(),
            ship_id: msg.ship_id,
            device_id: msg.device_id || null,
            heading: msg.heading ?? null,
            gps_data: validGpsData
          };
          console.log(`Received for ${msg.ship_id}:`, shipLocations[msg.ship_id]);
        } else {
          console.log(`No valid GPS entries in message from ${msg.ship_id}:`, msg.gps_data);
        }
      } else {
        console.log('Invalid message structure:', msg);
      }
    } catch (error) {
      console.error('Error parsing message:', error);
    }
  });

  ws.on('close', () => {
    clients = clients.filter(client => client !== ws);
    console.log(`Client disconnected. Total clients: ${clients.length}`);
  });

  ws.on('error', (error) => {
    console.error('WebSocket error:', error);
    clients = clients.filter(client => client !== ws);
    console.log(`Client error, removed. Total clients: ${clients.length}`);
  });
});

// Broadcast all ship locations every second
setInterval(() => {
  let shipsArray = Object.values(shipLocations);
  // Sort by ship_id
  shipsArray.sort((a, b) => a.ship_id.localeCompare(b.ship_id));
  const packet = {
    type: 'shipsUpdate',
    ships: shipsArray,
    timestamp: Date.now()
  };

  // Broadcast to all clients
  const packetString = JSON.stringify(packet);
  clients.forEach(client => {
    if (client.readyState === WebSocket.OPEN) {
      client.send(packetString);
    }
  });

  // Write to log file only if ships data exists
  if (shipsArray.length > 0) {
    const logEntry = JSON.stringify({
      ships: shipsArray,
      timestamp: packet.timestamp
    }) + '\n';
    fs.appendFile(LOG_FILE_PATH, logEntry, err => {
      if (err) {
        console.error('Error writing to log file:', err);
      }
    });
  } else {
    console.log('No ship data to log, skipping write to ships_log.jsonl');
  }
}, 1000);

console.log(`WebSocket server running at ${WS_URL}`);