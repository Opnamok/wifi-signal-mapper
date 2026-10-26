"""
wifi_mapper.py - hardware + Google Sheets side of the WiFi strength mapper.

The Seeed XIAO ESP32-C6 scans for WiFi networks while a NEO-6M GPS module
supplies a position fix, and prints one CSV line per (scan, network) pair
over serial. This script reads those lines and gets them into a Google Sheet.

Modes:
    python wifi_mapper.py log      Read the serial port and append rows to the
                                   Google Sheet, while serving a live map at
                                   http://localhost:8765 that updates per-row.
    python wifi_mapper.py live     Write live_map.html, a self-refreshing page
                                   that polls the Sheet's CSV export instead of
                                   the serial port. Safe to run alongside `log`,
                                   including from a different machine.
    python wifi_mapper.py pull     Download the Sheet to data/scan_log.csv.
                                   This snapshot is what build_viewer.py reads.
    python wifi_mapper.py reset    Wipe all logged rows from the Sheet.
                                   Asks for confirmation; --yes skips it.

Building the map viewer is a separate script, build_viewer.py, which needs
only the CSV snapshot - no credentials, no serial port, no network.
"""

import sys
import os
import json
import time
import threading
import http.server
from datetime import datetime, timezone, timedelta


# ============================================================
# Shared configuration
# ============================================================

SHEET_ID = "1PXC3bnRmL76fF3Ra4pkgGVh54sjIJNKkvRgT6nHn9yI"
SHEET_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid=0"

SERIAL_PORT = "COM5"
SERIAL_BAUD = 115200

SNAPSHOT_PATH = os.path.join("data", "scan_log.csv")

HEADERS = [
    "Timestamp", "Latitude", "Longitude", "Altitude", "Satellites",
    "SSID", "RSSI_dBm", "Channel", "Encryption", "SignalQuality",
]


def _has_valid_gps_fix(lat_str, lon_str) -> bool:
    """
    True only if both values parse as floats and aren't the (0, 0) / blank
    placeholder a GPS module reports before it has acquired a fix.
    """
    try:
        lat = float(lat_str)
        lon = float(lon_str)
    except (ValueError, TypeError):
        return False
    if lat == 0.0 and lon == 0.0:
        return False
    return True


# ============================================================
# Google Sheets access
# ============================================================

def _authorize_sheet():
    """Authorize with the service account and return the sheet1 worksheet."""
    import gspread
    from oauth2client.service_account import ServiceAccountCredentials

    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
    client = gspread.authorize(creds)
    return client.open_by_key(SHEET_ID).sheet1


def _setup_headers(sheet):
    """Write headers if missing, then bold them and freeze the row."""
    if not sheet.get_all_values():
        sheet.append_row(HEADERS)
    sheet.format("A1:J1", {"textFormat": {"bold": True}})
    sheet.freeze(rows=1)


def pull_snapshot(out_path: str = SNAPSHOT_PATH) -> None:
    """
    Download the Sheet's CSV export to a local file. This is the snapshot the
    viewer is built from, so the demo never depends on the Sheet being
    reachable. Read-only: needs no credentials, just a publicly viewable sheet.
    """
    import urllib.request

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

    print(f"Downloading sheet {SHEET_ID} ...")
    with urllib.request.urlopen(SHEET_URL, timeout=60) as response:
        body = response.read()

    with open(out_path, "wb") as f:
        f.write(body)

    row_count = body.count(b"\n")
    print(f"Wrote {out_path} ({len(body):,} bytes, ~{row_count:,} rows including header).")
    print("Now run: python build_viewer.py")


def reset_all_data(delete_local_files: bool = True, skip_confirm: bool = False) -> None:
    """
    Wipe every logged row from the Google Sheet (keeping only the header),
    and delete stale generated map files so a fresh `log` run starts from zero.

    This is destructive and irreversible, so it asks for confirmation
    unless skip_confirm=True (e.g. `python wifi_mapper.py reset --yes`).
    """
    if not skip_confirm:
        answer = input(
            "This will permanently delete ALL logged rows from the Google "
            "Sheet and any local live_map.html file.\n"
            "Type YES to confirm: "
        )
        if answer.strip() != "YES":
            print("Reset cancelled. Nothing was deleted.")
            return

    print("Connecting to Google Sheet...")
    sheet = _authorize_sheet()

    print("Clearing all rows...")
    sheet.clear()
    _setup_headers(sheet)
    print("Sheet cleared - only the header row remains.")

    if delete_local_files:
        for fname in ("live_map.html",):
            if os.path.exists(fname):
                os.remove(fname)
                print(f"Deleted stale local file: {fname}")

    print("Reset complete. Run 'log' again to start collecting fresh data -")
    print("only scans taken after the GPS has a real fix will be recorded.")
    print(f"Note: {SNAPSHOT_PATH} was NOT touched, so the existing demo data is safe.")


# ============================================================
# Live in-process map (served while `log` runs)
# ============================================================

LOCAL_SERVER_PORT = 8765

# In-memory snapshot of "what's around me right now" - updated the instant a
# valid-fix row is read, independent of the batched Sheet flush. This is what
# makes the map update in real time instead of waiting on the Sheets round-trip.
_live_state_lock = threading.Lock()
_live_state = {
    "timestamp": None,
    "latitude": None,
    "longitude": None,
    "altitude": None,
    "satellites": None,
    "networks": [],  # networks seen at the CURRENT fix (cleared when lat/lon changes)
}


def _update_live_state(ts, lat, lon, alt, sats, ssid, rssi, channel, encryption, quality):
    """Update the in-memory snapshot the local live map reads from.
    Networks list resets whenever the GPS fix moves to a new coordinate,
    so it always reflects only the networks around the current location."""
    with _live_state_lock:
        same_fix = (_live_state["latitude"] == lat and _live_state["longitude"] == lon)
        if not same_fix:
            _live_state["networks"] = []

        _live_state["timestamp"] = ts
        _live_state["latitude"] = lat
        _live_state["longitude"] = lon
        _live_state["altitude"] = alt
        _live_state["satellites"] = sats

        # Several APs can share an SSID; keep the strongest reading of each.
        existing = next((n for n in _live_state["networks"] if n["ssid"] == ssid), None)
        if existing is not None and float(existing["rssi"]) >= float(rssi):
            return
        _live_state["networks"] = [n for n in _live_state["networks"] if n["ssid"] != ssid]
        _live_state["networks"].append({
            "ssid": ssid, "rssi": rssi, "channel": channel,
            "encryption": encryption, "quality": quality,
        })


def _print_live_status_loop(interval_seconds: int = 5):
    """
    Runs forever in a background thread: every `interval_seconds`, prints a
    consolidated snapshot of the current fix + networks seen there to the
    terminal - independent of the per-row prints in the main read loop, so
    the terminal keeps showing live output even during quiet stretches.
    """
    while True:
        time.sleep(interval_seconds)
        with _live_state_lock:
            ts = _live_state["timestamp"]
            lat = _live_state["latitude"]
            lon = _live_state["longitude"]
            alt = _live_state["altitude"]
            sats = _live_state["satellites"]
            networks = list(_live_state["networks"])

        print("\n" + "=" * 70)
        if lat is None:
            print("LIVE STATUS: waiting for first GPS fix...")
        else:
            print(f"LIVE STATUS @ {ts}")
            print(f"Location: {lat:.6f}, {lon:.6f}  (alt {alt} m, {sats} satellites)")
            print(f"Networks visible at this location ({len(networks)}):")
            for n in networks:
                print(f"   {n['ssid']:<28} {n['rssi']:>5} dBm  ch{n['channel']:<3} {n['encryption']}")
        print("=" * 70)


LOCAL_LIVE_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Live WiFi + GPS Tracker</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  html, body { height: 100%; margin: 0; font-family: sans-serif; }
  #map { height: 100%; width: 100%; }
  #panel {
    position: absolute; top: 10px; right: 10px; z-index: 1000;
    background: white; padding: 10px 14px; border-radius: 6px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.3);
    font-size: 13px; line-height: 1.5; width: 260px; max-height: 90vh;
    overflow-y: auto;
  }
  #panel b { display: inline-block; width: 90px; }
  #status-dot {
    height: 10px; width: 10px; border-radius: 50%; display: inline-block;
    margin-right: 6px; background: #999;
  }
  #status-dot.live { background: #2ecc71; }
  #status-dot.stale { background: #e74c3c; }
  table { width: 100%; border-collapse: collapse; margin-top: 8px; font-size: 12px; }
  td { padding: 2px 4px; border-bottom: 1px solid #eee; }
</style>
</head>
<body>
<div id="map"></div>
<div id="panel">
  <div><span id="status-dot"></span><b id="status-text">Connecting...</b></div>
  <div><b>Timestamp:</b> <span id="f-time">-</span></div>
  <div><b>Lat/Lon:</b> <span id="f-latlon">-</span></div>
  <div><b>Altitude:</b> <span id="f-alt">-</span></div>
  <div><b>Satellites:</b> <span id="f-sats">-</span></div>
  <div style="margin-top:6px;"><b>Networks here:</b></div>
  <table id="net-table"><tbody></tbody></table>
</div>

<script>
const REFRESH_MS = 5000;  // matches the ~5s WiFi scan / Sheets flush cadence
const map = L.map('map').setView([0, 0], 3);
// The standard OSM rendering, served by FOSSGIS. Not tile.openstreetmap.org
// (blocks clients sending no identifying Referer) and not CARTO (needs an API
// key); both answer with a 200 OK placeholder image, so their refusal renders
// as a normal-looking tile and no error handler catches it.
// FOSSGIS only renders zoom 17+ over Germany (404 elsewhere), so above zoom 16
// Leaflet scales up the zoom-16 tiles instead of showing a blank background.
L.tileLayer('https://tile.openstreetmap.de/{z}/{x}/{y}.png', {
    attribution: '&copy; OpenStreetMap contributors (tiles: FOSSGIS)',
    maxZoom: 19,
    maxNativeZoom: 16,
}).addTo(map);

let marker = null;
let trail = L.polyline([], { color: '#3388ff', weight: 3 }).addTo(map);
let networkLayer = L.layerGroup().addTo(map);
let hasCentered = false;
let lastTimestamp = null;

function rssiColor(rssi) {
    if (rssi >= -50) return 'green';
    if (rssi >= -60) return 'lightgreen';
    if (rssi >= -70) return 'orange';
    if (rssi >= -80) return 'red';
    return 'darkred';
}

// Draws ONLY the networks from the current scan, at the current fix.
// Called fresh every refresh cycle - old networks/locations are never
// left on the map, only what's here right now.
function redrawNetworks(lat, lon, networks) {
    networkLayer.clearLayers();
    const n = networks.length;
    const jitterRadius = 0.00004; // ~4m, so overlapping networks are visible as a small cluster
    networks.forEach(function(net, idx) {
        let mlat = lat, mlon = lon;
        if (n > 1) {
            const angle = (2 * Math.PI * idx) / n;
            mlat += jitterRadius * Math.cos(angle);
            mlon += jitterRadius * Math.sin(angle);
        }
        const rssiVal = parseFloat(net.rssi);
        const cm = L.circleMarker([mlat, mlon], {
            radius: 7,
            color: '#333',
            weight: 1,
            fillColor: rssiColor(rssiVal),
            fillOpacity: 0.85,
        }).bindPopup(
            '<b>SSID:</b> ' + net.ssid + '<br>' +
            '<b>RSSI:</b> ' + net.rssi + ' dBm<br>' +
            '<b>Channel:</b> ' + net.channel + '<br>' +
            '<b>Encryption:</b> ' + net.encryption
        );
        networkLayer.addLayer(cm);
    });
}

const pulseIcon = L.divIcon({
    className: '',
    html: '<div style="width:16px;height:16px;border-radius:50%;background:#3388ff;' +
          'border:3px solid white;box-shadow:0 0 6px rgba(0,0,0,0.5);"></div>',
    iconSize: [16, 16],
    iconAnchor: [8, 8],
});

function setStatus(state, text) {
    document.getElementById('status-dot').className = state;
    document.getElementById('status-text').textContent = text;
}

async function refresh() {
    try {
        const res = await fetch('/latest?_=' + Date.now(), { cache: 'no-store' });
        if (!res.ok) throw new Error('HTTP ' + res.status);
        const s = await res.json();
        if (s.latitude == null || s.longitude == null) throw new Error('Waiting for GPS fix...');

        const lat = s.latitude, lon = s.longitude;

        if (!marker) {
            marker = L.marker([lat, lon], { icon: pulseIcon }).addTo(map);
        } else {
            marker.setLatLng([lat, lon]);
        }
        trail.addLatLng([lat, lon]);

        if (!hasCentered) {
            map.setView([lat, lon], 18);
            hasCentered = true;
        } else {
            map.panTo([lat, lon]);
        }

        document.getElementById('f-time').textContent = s.timestamp || '-';
        document.getElementById('f-latlon').textContent = lat.toFixed(6) + ', ' + lon.toFixed(6);
        document.getElementById('f-alt').textContent = (s.altitude || '-') + ' m';
        document.getElementById('f-sats').textContent = s.satellites || '-';

        redrawNetworks(lat, lon, s.networks || []);

        const tbody = document.querySelector('#net-table tbody');
        tbody.innerHTML = '';
        (s.networks || []).forEach(function(n) {
            const tr = document.createElement('tr');
            tr.innerHTML = '<td>' + n.ssid + '</td><td>' + n.rssi + ' dBm</td>';
            tbody.appendChild(tr);
        });

        const isNewFix = s.timestamp !== lastTimestamp;
        lastTimestamp = s.timestamp;
        setStatus(isNewFix ? 'live' : 'stale', isNewFix ? 'Live' : 'No new fix yet');
    } catch (err) {
        setStatus('stale', err.message);
    }
}

refresh();
setInterval(refresh, REFRESH_MS);
</script>
</body>
</html>
"""


class _LiveRequestHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/latest"):
            with _live_state_lock:
                payload = json.dumps(_live_state).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(payload)
        else:
            body = LOCAL_LIVE_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # silence per-request console spam


def _start_local_live_server(port: int = LOCAL_SERVER_PORT):
    server = http.server.ThreadingHTTPServer(("0.0.0.0", port), _LiveRequestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


# ============================================================
# Serial -> Google Sheets logger
# ============================================================

def run_logger(port: str = SERIAL_PORT, baud: int = SERIAL_BAUD):
    """Continuously read WiFi scan lines from a serial port, and:
      1. Batch-append valid rows to the Google Sheet, retrying on rate limits.
      2. Simultaneously update an in-process live map (localhost) instantly per row,
         so the sheet and the map both reflect the current location's networks
         at the same time, from the same read loop.

    Lines are only accepted once the GPS module has an actual fix - any scan
    line arriving before that (typically carrying 0,0 or blank coordinates)
    is dropped rather than written to the sheet or the live map.
    """
    from gspread.exceptions import APIError
    import serial

    sheet = _authorize_sheet()

    IST = timezone(timedelta(hours=5, minutes=30))
    FLUSH_INTERVAL = 5
    MAX_RETRIES = 5

    _setup_headers(sheet)

    def flush_batch(batch):
        if not batch:
            return
        delay = 2
        for attempt in range(MAX_RETRIES):
            try:
                sheet.append_rows(batch, value_input_option="RAW")
                print(f"Flushed {len(batch)} rows to Sheets.")
                return
            except APIError as e:
                if "429" in str(e) or "Quota exceeded" in str(e):
                    print(f"Rate limited, retrying in {delay}s... (attempt {attempt + 1}/{MAX_RETRIES})")
                    time.sleep(delay)
                    delay *= 2
                else:
                    print(f"Sheets API error: {e}")
                    return
        print(f"Failed to flush {len(batch)} rows after {MAX_RETRIES} retries. Data kept for next flush.")

    _start_local_live_server()
    print(f"Live map running at http://localhost:{LOCAL_SERVER_PORT} - "
          f"open it in a browser now. It shows ONLY the networks from the "
          f"current scan, redrawn fresh every 5s - not every network ever "
          f"logged to the sheet.")

    threading.Thread(target=_print_live_status_loop, args=(5,), daemon=True).start()
    print("Terminal will print a live status summary every 5 seconds.")
    print("This is a continuous tracking loop - it runs until you stop it with Ctrl+C.")

    ser = serial.Serial(port, baud, timeout=1)
    print("Logging live to Google Sheets (batched, IST timestamps)...")

    batch = []
    last_flush = time.time()

    try:
        while True:
            line = ser.readline().decode(errors="ignore").strip()
            if line and "," in line and not line.startswith("Timestamp"):
                row = line.split(",")
                if len(row) == 10:
                    lat_str, lon_str = row[1], row[2]
                    if not _has_valid_gps_fix(lat_str, lon_str):
                        # No GPS fix yet - don't log this WiFi network at all,
                        # and don't feed it to the live map either.
                        continue
                    ist_time = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
                    row[0] = ist_time
                    batch.append(row)
                    print(row)

                    # Update the live map instantly (independent of the
                    # batched Sheets flush below), so map + sheet both
                    # reflect the current fix's networks simultaneously.
                    _update_live_state(
                        ts=row[0], lat=float(row[1]), lon=float(row[2]),
                        alt=row[3], sats=row[4], ssid=row[5], rssi=row[6],
                        channel=row[7], encryption=row[8], quality=row[9],
                    )

            if batch and (time.time() - last_flush) >= FLUSH_INTERVAL:
                flush_batch(batch)
                batch = []
                last_flush = time.time()

    except KeyboardInterrupt:
        print("\nStopped. Flushing remaining rows...")
        flush_batch(batch)
    finally:
        ser.close()


# ============================================================
# Standalone live tracker page (polls the Sheet, not the serial port)
# ============================================================
#
# This does NOT read the serial port directly (the `log` process already
# owns it). Instead it generates a self-refreshing HTML page that polls
# the same Google Sheet the logger is writing to, grabs the most recent
# row's Latitude/Longitude, and moves a marker there - so you can run
# `log` and `live` at the same time and watch your position update on
# the map as new rows land in the sheet.

LIVE_MAP_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Live GPS Tracker</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/PapaParse/5.4.1/papaparse.min.js"></script>
<style>
  html, body {{ height: 100%; margin: 0; }}
  #map {{ height: 100%; width: 100%; }}
  #info {{
    position: absolute; top: 10px; right: 10px; z-index: 1000;
    background: white; padding: 10px 14px; border-radius: 6px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.3);
    font-family: sans-serif; font-size: 13px; line-height: 1.5;
    min-width: 200px;
  }}
  #info b {{ display: inline-block; width: 90px; }}
  #status-dot {{
    height: 10px; width: 10px; border-radius: 50%; display: inline-block;
    margin-right: 6px; background: #999;
  }}
  #status-dot.live {{ background: #2ecc71; }}
  #status-dot.stale {{ background: #e74c3c; }}
</style>
</head>
<body>
<div id="map"></div>
<div id="info">
  <div><span id="status-dot"></span><b id="status-text">Connecting...</b></div>
  <div><b>Timestamp:</b> <span id="f-time">-</span></div>
  <div><b>Lat/Lon:</b> <span id="f-latlon">-</span></div>
  <div><b>Altitude:</b> <span id="f-alt">-</span></div>
  <div><b>Satellites:</b> <span id="f-sats">-</span></div>
  <div><b>Last SSID:</b> <span id="f-ssid">-</span></div>
</div>

<script>
const CSV_URL = "{sheet_url}";
const REFRESH_MS = {refresh_ms};

const map = L.map('map').setView([0, 0], 3);
// See the note in LOCAL_LIVE_HTML: tile.openstreetmap.org blocks requests
// carrying no identifying Referer (which is what file:// pages send), and
// CARTO needs an API key. This FOSSGIS mirror serves the same OSM rendering.
// Zoom 17+ tiles 404 outside Germany - scale up zoom 16 (see LOCAL_LIVE_HTML).
L.tileLayer('https://tile.openstreetmap.de/{{z}}/{{x}}/{{y}}.png', {{
    attribution: '&copy; OpenStreetMap contributors (tiles: FOSSGIS)',
    maxZoom: 19,
    maxNativeZoom: 16,
}}).addTo(map);

let marker = null;
let trail = L.polyline([], {{ color: '#3388ff', weight: 3 }}).addTo(map);
let hasCentered = false;
let lastTimestamp = null;

const pulseIcon = L.divIcon({{
    className: '',
    html: '<div style="width:16px;height:16px;border-radius:50%;background:#3388ff;' +
          'border:3px solid white;box-shadow:0 0 6px rgba(0,0,0,0.5);"></div>',
    iconSize: [16, 16],
    iconAnchor: [8, 8],
}});

function setStatus(state, text) {{
    const dot = document.getElementById('status-dot');
    const label = document.getElementById('status-text');
    dot.className = state;
    label.textContent = text;
}}

async function refresh() {{
    try {{
        const res = await fetch(CSV_URL + "&_=" + Date.now(), {{ cache: "no-store" }});
        if (!res.ok) throw new Error("HTTP " + res.status);
        const text = await res.text();

        const parsed = Papa.parse(text.trim(), {{ header: true, skipEmptyLines: true }});
        const rows = parsed.data;
        if (!rows.length) throw new Error("No rows yet");

        const last = rows[rows.length - 1];
        const lat = parseFloat(last.Latitude);
        const lon = parseFloat(last.Longitude);
        if (isNaN(lat) || isNaN(lon) || (lat === 0 && lon === 0)) {{
            throw new Error("No GPS fix yet in latest row");
        }}

        if (!marker) {{
            marker = L.marker([lat, lon], {{ icon: pulseIcon }}).addTo(map);
        }} else {{
            marker.setLatLng([lat, lon]);
        }}
        trail.addLatLng([lat, lon]);

        if (!hasCentered) {{
            map.setView([lat, lon], 18);
            hasCentered = true;
        }} else {{
            map.panTo([lat, lon]);
        }}

        document.getElementById('f-time').textContent = last.Timestamp || "-";
        document.getElementById('f-latlon').textContent = lat.toFixed(6) + ", " + lon.toFixed(6);
        document.getElementById('f-alt').textContent = (last.Altitude || "-") + " m";
        document.getElementById('f-sats').textContent = last.Satellites || "-";
        document.getElementById('f-ssid').textContent = last.SSID || "-";

        const isNewFix = last.Timestamp !== lastTimestamp;
        lastTimestamp = last.Timestamp;
        setStatus(isNewFix ? 'live' : 'stale',
                   isNewFix ? 'Live' : 'No new fix yet');
    }} catch (err) {{
        setStatus('stale', 'Error: ' + err.message);
        console.error(err);
    }}
}}

refresh();
setInterval(refresh, REFRESH_MS);
</script>
</body>
</html>
"""


def build_live_map(out_path: str = "live_map.html", refresh_seconds: int = 5) -> None:
    """
    Generate a self-refreshing HTML page that shows the most recent GPS
    fix as a moving marker + trail, by polling the same Google Sheet the
    `log` mode writes to. Safe to run at the same time as `log` mode,
    since it never touches the serial port.
    """
    html = LIVE_MAP_TEMPLATE.format(
        sheet_url=SHEET_URL,
        refresh_ms=refresh_seconds * 1000,
    )
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Live tracker created: {out_path}")
    print(f"Open it in a browser while 'log' mode is running - "
          f"it refreshes every {refresh_seconds}s.")


# ============================================================
# Entry point
# ============================================================

def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else None
    skip_confirm = "--yes" in sys.argv or "-y" in sys.argv

    if mode == "log":
        run_logger()
    elif mode == "live":
        build_live_map()
    elif mode == "pull":
        pull_snapshot()
    elif mode == "reset":
        reset_all_data(skip_confirm=skip_confirm)
    else:
        if mode is not None:
            print(f"Unknown mode '{mode}'.\n")
        print(__doc__.strip())
        sys.exit(1 if mode is not None else 0)


if __name__ == "__main__":
    main()
