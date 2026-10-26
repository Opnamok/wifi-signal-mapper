/*
 * wifi_gps_logger.ino - board side of the WiFi strength mapper.
 *
 * Seeed XIAO ESP32-C6 + NEO-6M-compatible GPS on Serial1 (D7 = RX, D6 = TX).
 * Scans WiFi continuously and prints one CSV line per (scan, network) pair
 * over USB at 115200, in the format documented in ../README.md:
 *
 *   Timestamp,Latitude,Longitude,Altitude,Satellites,SSID,RSSI_dBm,Channel,Encryption,SignalQuality
 *
 * Without a GPS fix the coordinates are 0.000000,0.000000 - wifi_mapper.py
 * drops those rows. Status lines start with '#' and contain no commas, so the
 * logger's 10-field check ignores them.
 *
 * Needs the TinyGPSPlus library (Mikal Hart), which also parses the $GN...
 * sentences that multi-constellation modules send instead of $GP....
 */

#include <WiFi.h>
#include <TinyGPS++.h>

static const uint32_t USB_BAUD = 115200;
static const uint32_t GPS_BAUD = 9600;

// The module sends a fix every 1-2 s; anything older means the fix was lost.
static const uint32_t FIX_MAX_AGE_MS = 5000;

TinyGPSPlus gps;

void feedGps() {
  while (Serial1.available()) {
    gps.encode(Serial1.read());
  }
}

bool hasFreshFix() {
  return gps.location.isValid() && gps.location.age() < FIX_MAX_AGE_MS;
}

// Bands must match the RSSI colour bands the viewer uses (see ../README.md).
const char *signalQuality(int rssi) {
  if (rssi >= -50) return "Excellent";
  if (rssi >= -60) return "Good";
  if (rssi >= -70) return "Fair";
  if (rssi >= -80) return "Weak";
  return "Very Poor";
}

void printScanResults(int count) {
  char timestamp[20];
  if (gps.date.isValid() && gps.time.isValid() && gps.date.year() > 2000) {
    snprintf(timestamp, sizeof(timestamp), "%04d-%02d-%02d %02d:%02d:%02d",
             gps.date.year(), gps.date.month(), gps.date.day(),
             gps.time.hour(), gps.time.minute(), gps.time.second());
  } else {
    strcpy(timestamp, "0000-00-00 00:00:00");
  }

  bool fix = hasFreshFix();
  double lat = fix ? gps.location.lat() : 0.0;
  double lon = fix ? gps.location.lng() : 0.0;
  double alt = (fix && gps.altitude.isValid()) ? gps.altitude.meters() : 0.0;
  int sats = gps.satellites.isValid() ? (int)gps.satellites.value() : 0;

  // Strongest first, so a glance at the serial monitor shows the nearby APs.
  int order[count];
  for (int i = 0; i < count; i++) order[i] = i;
  for (int i = 1; i < count; i++) {
    int key = order[i], j = i - 1;
    while (j >= 0 && WiFi.RSSI(order[j]) < WiFi.RSSI(key)) {
      order[j + 1] = order[j];
      j--;
    }
    order[j + 1] = key;
  }

  for (int k = 0; k < count; k++) {
    int i = order[k];
    String ssid = WiFi.SSID(i);
    if (ssid.length() == 0) continue;
    int rssi = WiFi.RSSI(i);
    Serial.printf("%s,%.6f,%.6f,%.2f,%d,%s,%d,%d,%s,%s\n",
                  timestamp, lat, lon, alt, sats, ssid.c_str(), rssi,
                  (int)WiFi.channel(i),
                  WiFi.encryptionType(i) == WIFI_AUTH_OPEN ? "Open" : "Secured",
                  signalQuality(rssi));
    feedGps();
  }

  if (!fix) {
    Serial.printf("# no GPS fix yet: %d satellites  %lu chars from GPS  %lu sentences with fix  %lu bad checksums\n",
                  sats, (unsigned long)gps.charsProcessed(),
                  (unsigned long)gps.sentencesWithFix(), (unsigned long)gps.failedChecksum());
    if (gps.charsProcessed() < 10) {
      Serial.println("# no data from the GPS at all - check GPS TX is wired to D7");
    }
  }
}

void startScan() {
  // Async, so loop() keeps reading the GPS while the radio hops channels.
  if (WiFi.scanNetworks(true, false) == WIFI_SCAN_FAILED) {
    delay(200);
  }
}

void setup() {
  Serial.begin(USB_BAUD);

  // A scan takes a few seconds; at 9600 baud the default 256-byte buffer
  // would overflow and corrupt sentences if it were ever read late.
  Serial1.setRxBufferSize(2048);
  Serial1.begin(GPS_BAUD, SERIAL_8N1, D7, D6);

  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  delay(100);

  Serial.println("Timestamp,Latitude,Longitude,Altitude,Satellites,SSID,RSSI_dBm,Channel,Encryption,SignalQuality");
  startScan();
}

void loop() {
  feedGps();

  int16_t result = WiFi.scanComplete();
  if (result == WIFI_SCAN_RUNNING) return;

  if (result >= 0) {
    printScanResults(result);
    WiFi.scanDelete();
  }
  startScan();
}
