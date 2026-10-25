# Firmware — serial output contract

Hardware: **Seeed XIAO ESP32-C6** (WiFi scanning) + **NEO-6M GPS** on UART.

The sketch lives in the Arduino sketchbook and is not duplicated here. What
matters to the Python side is the line format, recorded below so the sketch and
the parser stay in sync.

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
