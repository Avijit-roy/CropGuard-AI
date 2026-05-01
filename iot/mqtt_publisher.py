# ============================================================
# FILE: iot/mqtt_publisher.py
# PURPOSE: Reads sensor JSON from Arduino via USB Serial and
#          publishes to MQTT broker every 10 seconds
# ============================================================

import serial
import json
import time
import logging
import logging.handlers
import paho.mqtt.client as mqtt
from datetime import datetime, timezone

SERIAL_PORT = "/dev/ttyACM0"           # Windows — change to /dev/ttyUSB0 for Linux/Mac
SERIAL_BAUD = 9600

MQTT_BROKER = "broker.hivemq.com"
MQTT_PORT   = 1883
MQTT_TOPIC  = "plant/soil/cropguard_01"
DEVICE_ID   = "cropguard_01"

PUBLISH_INTERVAL = 10

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.handlers.RotatingFileHandler(
            "publisher.log", maxBytes=5*1024*1024, backupCount=3  # 5 MB per file, keep 3 backups
        ),
        logging.StreamHandler(),
    ]
)
log = logging.getLogger(__name__)

def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0: log.info(f"Connected to MQTT broker: {MQTT_BROKER}")
    else:       log.error(f"MQTT connection failed. Code: {rc}")

def on_publish(client, userdata, mid, reason_code=None, properties=None):
    log.info(f"Message published (mid={mid})")

def on_disconnect(client, userdata, flags, rc, properties=None):
    if rc != 0: log.warning("Unexpected MQTT disconnection. Will auto-reconnect.")

def create_mqtt_client():
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"plant_publisher_{DEVICE_ID}")
    client.on_connect    = on_connect
    client.on_publish    = on_publish
    client.on_disconnect = on_disconnect
    try:
        client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
        client.loop_start()
        return client
    except Exception as e:
        log.error(f"Cannot connect to MQTT broker: {e}")
        return None

def read_serial_data(line):
    try:
        if not line or line.startswith('{"status"') or line.startswith('{"error"'):
            if line: log.info(f"Arduino: {line}")
            return None

        data = None
        if line.startswith("{"):
            try:
                data = json.loads(line)
            except json.JSONDecodeError as e:
                log.warning(f"JSON parse error: {e} | line: {line}")
                return None
        else:
            # Try CSV parsing
            parts = line.split(',')
            if len(parts) >= 4:
                try:
                    temperature = float(parts[1])
                    humidity = float(parts[2])
                    soil_raw = int(parts[3])
                    
                    soil_moisture = max(0.0, min(100.0, (1023 - soil_raw) / 10.23))
                    soil_dry = soil_raw > 700
                    temp_high = temperature > 35
                    humidity_low = humidity < 30
                    
                    data = {
                        "temperature": temperature,
                        "humidity": humidity,
                        "soil_raw": soil_raw,
                        "soil_moisture": round(soil_moisture, 1),
                        "soil_dry": soil_dry,
                        "temp_high": temp_high,
                        "humidity_low": humidity_low
                    }
                except ValueError:
                    log.warning(f"CSV parse error | line: {line}")
                    return None
            else:
                return None

        if data:
            data["device_id"] = DEVICE_ID
            data["timestamp"] = datetime.now(timezone.utc).isoformat()

            required = [
                "temperature", "humidity",
                "soil_moisture", "soil_dry", "soil_raw",
                "temp_high", "humidity_low"
            ]
            if not all(k in data for k in required):
                log.warning(f"Incomplete sensor data: {data}")
                return None

            return data

    except Exception as e:
        log.error(f"Serial read error: {e}")
        return None

def main():
    log.info("=== CropGuard AI Publisher Starting ===")
    log.info(f"Serial Port : {SERIAL_PORT} @ {SERIAL_BAUD} baud")
    log.info(f"MQTT Broker : {MQTT_BROKER}:{MQTT_PORT}")
    log.info(f"Topic       : {MQTT_TOPIC}")

    mqtt_client = create_mqtt_client()
    if not mqtt_client:
        log.error("Exiting — cannot connect to MQTT broker")
        return

    try:
        ser = serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=15)
        log.info(f"Serial opened on {SERIAL_PORT}")
        time.sleep(2)
    except serial.SerialException as e:
        log.error(f"Cannot open serial port {SERIAL_PORT}: {e}")
        return

    consecutive_errors = 0
    MAX_ERRORS = 10

    while True:
        try:
            last_line = None
            while ser.in_waiting > 0:
                line = ser.readline()
                if line:
                    last_line = line
                    
            if last_line is None:
                last_line = ser.readline()

            if last_line:
                line_str = last_line.decode("utf-8", errors="ignore").strip()
                data = read_serial_data(line_str)
            else:
                data = None

            if data:
                payload = json.dumps(data)
                result  = mqtt_client.publish(MQTT_TOPIC, payload=payload, qos=1, retain=True)
                if result.rc == mqtt.MQTT_ERR_SUCCESS:
                    log.info(
                        f"Published | Temp:{data['temperature']}°C | "
                        f"Humidity:{data['humidity']}% | "
                        f"Soil:{data['soil_moisture']}%"
                    )
                    consecutive_errors = 0
                else:
                    log.warning(f"Publish failed: rc={result.rc}")
                    consecutive_errors += 1

            if consecutive_errors >= MAX_ERRORS:
                log.error(f"{MAX_ERRORS} consecutive errors — check hardware")
                consecutive_errors = 0

            time.sleep(PUBLISH_INTERVAL)

        except KeyboardInterrupt:
            log.info("Stopped by user")
            break
        except Exception as e:
            log.error(f"Unexpected error: {e}")
            consecutive_errors += 1
            time.sleep(5)

    ser.close()
    mqtt_client.loop_stop()
    mqtt_client.disconnect()
    log.info("Publisher stopped cleanly")

if __name__ == "__main__":
    main()
