# Firmware — serial output contract

Hardware: **Seeed XIAO ESP32-C6** (WiFi scanning) + **NEO-6M GPS** on UART.

The sketch is [`wifi_gps_logger/wifi_gps_logger.ino`](wifi_gps_logger/wifi_gps_logger.ino).
What matters to the Python side is the line format, recorded below so the
sketch and the parser stay in sync.

## Building and flashing

Needs the **esp32** board package (Espressif) and the **TinyGPSPlus** library
(Mikal Hart). In the Arduino IDE pick the board *XIAO_ESP32C6*; from the
command line:

```bash
arduino-cli lib install TinyGPSPlus
arduino-cli compile -b esp32:esp32:XIAO_ESP32C6 firmware/wifi_gps_logger
arduino-cli upload -b esp32:esp32:XIAO_ESP32C6 -p COM5 firmware/wifi_gps_logger
```

The GPS module in this rig sends multi-constellation `$GN...` sentences rather
than the NEO-6M's `$GP...`; TinyGPSPlus parses both. The WiFi scan runs
asynchronously so the GPS is read continuously while the radio hops channels.

## Wiring

```
   NEO-6M                         XIAO ESP32-C6
  ────────                       ───────────────
   VCC   ───────────────────►     3V3
   GND   ───────────────────►     GND
   TX    ───────────────────►     D7    (GPIO17, UART RX)
   RX    ◄───────────────────     D6    (GPIO16, UART TX)
```

So the sketch opens the GPS link as:

```cpp
Serial1.begin(9600, SERIAL_8N1, D7, D6);   // RX, TX
```

Two serial links at two speeds: the GPS talks to the board at **9600**, the
board talks to the laptop over USB at **115200**. Power the module from **3V3**
— the XIAO is a 3.3 V-logic board, and more than 3.3 V on a GPIO can damage the
chip. The `D6 → RX` wire is only needed if the sketch sends configuration
commands to the module; reading NMEA alone does not require it.

Full setup and demonstration steps: [../docs/DEMO_GUIDE.md](../docs/DEMO_GUIDE.md).

## Line format

The board prints **one CSV line per (scan, network) pair** — a single scan that
sees 8 networks emits 8 lines sharing a timestamp and position:

```
Timestamp,Latitude,Longitude,Altitude,Satellites,SSID,RSSI_dBm,Channel,Encryption,SignalQuality
```

Example:

```
2026-08-27 23:07:48,12.972421,79.162997,159.25,8,L-VIT,-53,6,Open,Good
```

| Field | Type | Notes |
|---|---|---|
| `Timestamp` | string | Overwritten by the logger with host time in IST. |
| `Latitude` / `Longitude` | float | Decimal degrees. `0,0` before the GPS has a fix. |
| `Altitude` | float | Metres. |
| `Satellites` | int | Satellites used in the fix. |
| `SSID` | string | Free-form; may contain non-ASCII characters. |
| `RSSI_dBm` | int | Negative, typically −20 to −100. |
| `Channel` | int | 1–13 observed. |
| `Encryption` | string | `Open` or `Secured`. |
| `SignalQuality` | string | `Excellent` / `Good` / `Fair` / `Weak` / `Very Poor`. |

Baud rate **115200**. The logger ignores any line that doesn't split into
exactly 10 comma-separated fields, and any line starting with `Timestamp`.

The sketch prints the header line once at boot, and while it has no fix it
prints a status line after each scan, such as
`# no GPS fix yet: 4 satellites  51234 chars from GPS  0 sentences with fix  0 bad checksums`.
Status lines start with `#` and contain no commas, so the logger skips them.
If the chars count stays near 0, no data is reaching D7 from the GPS.

## Rules the parser depends on

1. **Exactly 10 fields per line.** An SSID containing a comma yields 11 fields
   and that network is silently skipped. This is the one known data-loss path.
2. **`0,0` means "no fix yet".** Emitting those rows is fine — both the logger
   and the viewer drop them — but they are what the 212 discarded rows in the
   current dataset are.
3. **`SignalQuality` must match these RSSI bands**, because the map's colours
   are derived from RSSI and are expected to agree with this column:

   | RSSI (dBm) | Label |
   |---|---|
   | ≥ −50 | `Excellent` |
   | −51 to −60 | `Good` |
   | −61 to −70 | `Fair` |
   | −71 to −80 | `Weak` |
   | ≤ −81 | `Very Poor` |

If the sketch's output format changes, update this file and check
`run_logger()` in `../wifi_mapper.py` and `load_clean()` in
`../build_viewer.py`.
