// ============================================================
// FILE: iot/arduino_sensor.ino
// UPLOAD TO: Arduino Uno R3 via Arduino IDE
//
// SENSORS:
//   DHT11          → Temperature + Humidity       → Pin D2
//   Soil Sensor    → Soil moisture (analog)       → Pin A0
//   LED            → Alert indicator              → Pin D8
//   Buzzer         → Alert sound                  → Pin D9
//
// LIBRARIES TO INSTALL:
//   1. DHT sensor library (Adafruit)
// ============================================================

#include <DHT.h>

#define DHT_PIN     2
#define DHT_TYPE    DHT11
#define SOIL_PIN    A0
#define LED_PIN     8
#define BUZZER_PIN  9

DHT dht(DHT_PIN, DHT_TYPE);

// ── Thresholds ──────────────────────────────────────────────
const int SOIL_DRY_THRESHOLD = 700;
const int TEMP_HIGH          = 35;
const int HUM_LOW            = 30;

// ============================================================
void setup() {
  Serial.begin(9600);
  dht.begin();

  pinMode(LED_PIN, OUTPUT);
  pinMode(BUZZER_PIN, OUTPUT);

  digitalWrite(LED_PIN, LOW);
  noTone(BUZZER_PIN);

  delay(2000);
  Serial.println("{\"status\":\"SENSOR_READY\"}");
}

// ============================================================
void loop() {

  float temperature = dht.readTemperature();
  float humidity    = dht.readHumidity();
  int soilRaw       = analogRead(SOIL_PIN);

  if (isnan(temperature) || isnan(humidity)) {
    Serial.println("{\"error\":\"DHT11_READ_FAILED\"}");
    delay(2000);
    return;
  }

  // Soil interpretation
  float soilMoisture = constrain(map(soilRaw, 1023, 0, 0, 100), 0, 100);
  bool soilDry = (soilRaw > SOIL_DRY_THRESHOLD);

  // Alerts
  bool tempHigh = (temperature > TEMP_HIGH);
  bool humLow   = (humidity < HUM_LOW);

  // --- Actuators ---
  if (soilDry || tempHigh || humLow) {
    digitalWrite(LED_PIN, HIGH);
    tone(BUZZER_PIN, 1000);
  } else {
    digitalWrite(LED_PIN, LOW);
    noTone(BUZZER_PIN);
  }

  // --- JSON Output ---
  Serial.print("{");
  Serial.print("\"device_id\":\"cropguard_basic\",");
  Serial.print("\"time_ms\":");        Serial.print(millis()); Serial.print(",");
  Serial.print("\"temperature\":");    Serial.print(temperature, 1); Serial.print(",");
  Serial.print("\"humidity\":");       Serial.print(humidity, 1); Serial.print(",");
  Serial.print("\"soil_raw\":");       Serial.print(soilRaw); Serial.print(",");
  Serial.print("\"soil_moisture\":");  Serial.print(soilMoisture, 1); Serial.print(",");
  Serial.print("\"soil_dry\":");       Serial.print(soilDry ? "true" : "false"); Serial.print(",");
  Serial.print("\"temp_high\":");      Serial.print(tempHigh ? "true" : "false"); Serial.print(",");
  Serial.print("\"humidity_low\":");   Serial.print(humLow ? "true" : "false");
  Serial.println("}");

  delay(2000);
}