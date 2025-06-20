# test_gps.py
import gps
import time

client = gps.gps(mode=gps.WATCH_ENABLE | gps.WATCH_NEWSTYLE)
try:
    while True:
        report = client.next()
        if report['class'] == 'TPV':
            print(f"Device: {report.get('device', 'Unknown')}")
            print(f"Time: {report.get('time', 'Unknown')}")
            print(f"Lat: {report.get('lat', 'Unknown')}")
            print(f"Lon: {report.get('lon', 'Unknown')}")
            print(f"Alt: {report.get('alt', 'Unknown')}")
            print(f"Speed: {report.get('speed', 'Unknown')}")
            print("-" * 50)
        time.sleep(1)
except KeyboardInterrupt:
    print("Stopping GPS")
    client.close()
