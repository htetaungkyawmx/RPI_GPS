import gps
import logging
import time
from datetime import datetime
import pytz

logging.basicConfig(
    level=logging.DEBUG,
    filename='/home/mdt/gps_debug.log',
    filemode='a',
    format='%(asctime)s - %(levelname)s - %(message)s'
)

logging.info("Starting simple_gps.py")
print("Starting simple_gps.py")

try:
    client = gps.gps(mode=gps.WATCH_ENABLE | gps.WATCH_NEWSTYLE)
    logging.info("Connected to gpsd")
    print("Connected to gpsd")
    for report in client:
        timestamp = datetime.now(pytz.UTC)
        output = f"GPS Data (Real-Time): {timestamp.strftime('%Y-%m-%d %H:%M:%S.%f')}\n"
        output += f"Device: {report.get('device', 'Unknown')}\n"
        if report['class'] == 'TPV':
            output += (
                f"Latitude: {report.get('lat', 'Unknown')}\n"
                f"Longitude: {report.get('lon', 'Unknown')}\n"
                f"Altitude (m): {report.get('alt', 'Unknown')}\n"
                f"Speed (km/h): {report.get('speed', 'Unknown') * 3.6 if isinstance(report.get('speed'), (int, float)) else 'Unknown'}\n"
                f"Heading: {report.get('track', 'Unknown')}°\n"
            )
        elif report['class'] == 'SKY':
            output += f"Satellites: {len(report.get('satellites', []))}\n"
        print(output)
        logging.info(output)
        with open('/home/mdt/Desktop/GPS/simple_gps_output.txt', 'a+') as f:
            f.seek(0)
            if f.read(100):
                f.write('\n')
            f.write(output)
        time.sleep(1)
except KeyboardInterrupt:
    print("Stopping GPS")
    logging.info("Stopping GPS")
    client.close()
except Exception as e:
    print(f"Error in simple_gps: {e}")
    logging.error(f"Error in simple_gps: {e}")
