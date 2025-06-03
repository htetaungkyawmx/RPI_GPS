const WebSocket = require('ws');

const WS_URL = 'ws://localhost:4001';
const NUM_CLIENTS = 5;
const clients = [];

function generateRandomData(shipId, deviceId) {
  const baseLat = 16.8167;
  const baseLon = 96.1927;
  const prns = ['5', '15', '18', '23', '24', '195', '196', '67', '81', '82', '83'];
  return {
    timestamp: new Date().toISOString(),
    ship_id: shipId,
    device_id: deviceId,
    heading: parseFloat((Math.random() * 360).toFixed(1)),
    gps_data: [
      {
        gps: 'top_gps',
        latitude: parseFloat((baseLat + (Math.random() - 0.5) * 0.001).toFixed(7)),
        longitude: parseFloat((baseLon + (Math.random() - 0.5) * 0.001).toFixed(7)),
        altitude: parseFloat((Math.random() * 10 + 5).toFixed(3)),
        speed: parseFloat((Math.random() * 1).toFixed(2)),
        satellites: Math.floor(Math.random() * 5) + 10,
        satellite_prns: prns.slice(0, Math.floor(Math.random() * 3) + 8)
      },
      {
        gps: 'bottom_gps',
        latitude: parseFloat((baseLat + (Math.random() - 0.5) * 0.001).toFixed(7)),
        longitude: parseFloat((baseLon + (Math.random() - 0.5) * 0.001).toFixed(7)),
        altitude: parseFloat((Math.random() * 10 + 5).toFixed(3)),
        speed: parseFloat((Math.random() * 1).toFixed(2)),
        satellites: Math.floor(Math.random() * 5) + 8,
        satellite_prns: prns.slice(0, Math.floor(Math.random() * 3) + 7)
      }
    ]
  };
}

for (let i = 0; i < NUM_CLIENTS; i++) {
  const ws = new WebSocket(WS_URL);

  ws.on('open', () => {
    console.log(`Client ${i + 1} connected`);
    clients.push(ws);

    setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) {
        const data = generateRandomData(`SHIP${i + 1}`, `DEVICE${i + 1}`);
        ws.send(JSON.stringify(data));
        console.log(`Client ${i + 1} sent:`, data);
      }
    }, 2000);
  });

  ws.on('message', (data) => {
    try {
      const message = JSON.parse(data);
      if (i === 0) {
        console.log(`Client ${i + 1} received:`, message);
      }
    } catch (error) {
      console.error(`Client ${i + 1} error parsing message:`, error);
    }
  });

  ws.on('close', () => {
    console.log(`Client ${i + 1} disconnected`);
  });

  ws.on('error', (error) => {
    console.error(`Client ${i + 1} error:`, error);
  });
}