import time
import serial
import serial.tools.list_ports
import json
import logging
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

API_URL = "https://cropguard-ai-1-ys7p.onrender.com/api/sensor/upload"

def find_arduino_port():
    ports = list(serial.tools.list_ports.comports())
    for p in ports:
        if "ACM" in p.device or "USB" in p.device or "Arduino" in p.description:
            return p.device
    return None

def main():
    log.info("=== CropGuard Local USB Serial Reader Started ===")
    ser = None
    
    while True:
        try:
            if ser is None:
                port = find_arduino_port()
                if port:
                    log.info(f"Connecting to Arduino on {port}...")
                    ser = serial.Serial(port, 9600, timeout=2)
                    time.sleep(2)  # Wait for Arduino to reset
                    log.info("Connected!")
                else:
                    log.warning("No Arduino found on USB. Retrying in 5s...")
                    time.sleep(5)
                    continue

            line = ser.readline().decode('utf-8', errors='replace').strip()
            if not line:
                continue
                
            data = json.loads(line)
            # Post to local API
            try:
                r = requests.post(API_URL, json={
                    "device_id": data.get("device_id", "local_usb"),
                    "temperature": data.get("temperature"),
                    "humidity": data.get("humidity"),
                    "soil_moisture": data.get("soil_moisture")
                }, timeout=3)
                if r.ok:
                    log.info(f"Pushed to API: Temp {data.get('temperature')}°C | Soil {data.get('soil_moisture')}%")
                else:
                    log.warning(f"API rejected data: {r.status_code}")
            except requests.RequestException:
                log.warning("Could not reach local API. Is the server running?")
                
        except json.JSONDecodeError:
            pass  # Ignore garbage serial data
        except (serial.SerialException, OSError) as e:
            log.error(f"Serial error: {e}")
            if ser:
                ser.close()
                ser = None
            time.sleep(2)
        except Exception as e:
            log.error(f"Unexpected error: {e}")
            time.sleep(2)

if __name__ == "__main__":
    main()
