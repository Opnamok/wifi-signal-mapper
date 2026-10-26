# WiFi Signal Strength Mapper

Maps WiFi signal strength across a location by pairing WiFi scans with GPS fixes.

A **Seeed XIAO ESP32-C6** scans for nearby networks while a **NEO-6M GPS module**
supplies a position fix. The board prints one CSV line per *(scan, network)* pair
over serial; a Python logger pushes those lines into a Google Sheet; and a build
script turns the collected log into a single self-contained HTML map.

The current dataset: **2,869 measurements** across **217 scan points** and
**59 networks**, logged 2026-08-27 to 2026-09-10.

> **Wiring the hardware or running the demo?** See
> **[docs/DEMO_GUIDE.md](docs/DEMO_GUIDE.md)** — connections, pre-demo checklist,
> a step-by-step demonstration script, and troubleshooting.

---

## The map

Open **`viewer/wifi_map.html`** in a browser. That's it — no server, no
`npm install`, no Python needed to view it. Everything (the data, Leaflet, the
heatmap plugin) is inlined into that one file, so the only thing it needs the
network for is the basemap tiles. If tiles can't load, it says so and still
plots every measurement.

### Three views

| View | What it shows |
|---|---|
| **AP dots** | One dot per network per location, with repeat sightings within 3 m merged into a single point. Colour is the median RSSI of that cluster. Click any dot for SSID, signal, channel, encryption, measurement count and first/last seen. |
| **Coverage** | One network at a time. Measurements are binned into 5 m cells and averaged, giving a signal-strength surface. |
| **Density** | How many distinct networks were visible at each scan point — AP congestion rather than signal quality. |

### Why Coverage is a grid and Density is a blur

A blurred heat layer renders *density*: it brightens where measurements are
frequent, not where signal is strong. Feeding RSSI into one produces a map of
where you stood longest, which is why the Coverage view averages into fixed
cells instead — an empty cell means "no measurement here", not "no signal here".

Density is the one quantity a heat layer genuinely measures, so that view does
use one.

### Reviewing data on the map

The side panel filters the AP-dots view live, with no rebuild:

- **Minimum signal** — hide everything weaker than a threshold.
- **Encryption** — All / Open / Secured.
- **Networks** — searchable checklist with measurement counts; `all` / `none`
  act on whatever the search currently shows. Click a network's name to zoom to it.
- **Statistics** — points shown, networks shown, strongest and weakest, all
  recomputed as you filter.

---

## Signal bands

These thresholds reproduce exactly what the firmware writes into the
`SignalQuality` column, so a dot's colour always agrees with the raw log.

| RSSI (dBm) | Label |
|---|---|
| ≥ −50 | Excellent |
| −51 to −60 | Good |
| −61 to −70 | Fair |
| −71 to −80 | Weak |
| ≤ −81 | Very Poor |

---

## Basemap tiles

The map ships with the **standard OpenStreetMap rendering served by FOSSGIS**
(`tile.openstreetmap.de`) — full street, building and footpath detail, and no
API key. If it fails to load, the viewer falls through to OSM France, then to
Esri's World Street Map.

Two providers you might reach for do **not** work here:

| Provider | What happens |
|---|---|
| `tile.openstreetmap.org` | OSM's main servers block clients that send no identifying `Referer`, which is what a page opened by double-clicking looks like. Returns an "Access blocked" tile. |
| `basemaps.cartocdn.com` | CARTO now requires an API key on every style and endpoint, including the legacy Fastly host. Returns an "API KEY REQUIRED" watermark. |

Both serve their refusal as a **200 OK image**, so the browser reports a
successful load and the tile renders like a normal map. No error handler can
catch it. **If you change provider, look at a tile — a successful load proves
nothing.**

### Using CARTO

CARTO's Voyager style works once you have a key (free tier at
<https://carto.com/basemaps>). Paste it into `CARTO_API_KEY` near the top of
the `<script>` block in `viewer/template.html` and rebuild:

```js
const CARTO_API_KEY = "your-key-here";
```

CARTO then becomes the preferred street basemap, with the no-key providers
still behind it as fallbacks. Left blank, nothing breaks.

### Working fully offline

`LOCAL_TILES` in the same block takes a path like `"tiles/{z}/{x}/{y}.png"` and
is tried before everything else. The logged area is only about 250 × 120 m, so
caching zooms 16–19 is a few dozen tiles.

---

## Rebuilding the map

```bash
pip install -r requirements.txt
python build_viewer.py
```

`build_viewer.py` reads `data/scan_log.csv` and writes `viewer/wifi_map.html`.
It needs only pandas, numpy and scikit-learn — **no credentials, no serial port,
no network**. Anyone with the CSV can rebuild the map.

To refresh the data snapshot from the Google Sheet first:

```bash
python wifi_mapper.py pull
```

---

## Collecting data

`wifi_mapper.py` is the hardware and Google Sheets side.

| Command | What it does |
|---|---|
| `python wifi_mapper.py log` | Read the serial port, append rows to the Sheet in 5-second batches, and serve a live map at <http://localhost:8765> that updates per-row. |
| `python wifi_mapper.py live` | Write `live_map.html`, a self-refreshing page that polls the Sheet instead of the serial port. Safe to run alongside `log`, including from another machine. |
| `python wifi_mapper.py pull` | Download the Sheet to `data/scan_log.csv`. |
| `python wifi_mapper.py reset` | Wipe all logged rows from the Sheet. Asks for confirmation; `--yes` skips it. Does **not** touch `data/scan_log.csv`. |

`log`, `pull` and `reset` need a Google service-account key at
`credentials.json` (see `firmware/README.md` for the serial format, and the
setup notes below). The serial port defaults to `COM6` at 115200 baud —
change `SERIAL_PORT` near the top of `wifi_mapper.py` if yours differs.

Rows arriving before the GPS has a fix carry `0,0` coordinates and are dropped
at both ends: the logger never writes them, and the viewer skips them if any
slip through.

### Google Sheets setup

1. In [Google Cloud Console](https://console.cloud.google.com/), create a
   project and enable the **Google Sheets API** and **Google Drive API**.
2. Create a **Service Account**, add a **JSON key**, and save it as
   `credentials.json` in this folder. It is gitignored — it is a private key,
   never commit it.
3. Create a Google Sheet, share it with the service account's `client_email`
   as **Editor**, and put its ID in `SHEET_ID` at the top of `wifi_mapper.py`.

The headers are written automatically on first run.

---

## Layout

```
wifi_mapper.py        logger / live tracker / pull / reset  (hardware + Sheets)
build_viewer.py       data/scan_log.csv -> viewer/wifi_map.html
data/scan_log.csv     committed snapshot — the dataset the map is built from
viewer/
  template.html       the Leaflet viewer, with placeholders for data + libraries
  vendor/             leaflet.css, leaflet.js, leaflet-heat.js (committed, inlined at build)
  wifi_map.html       generated — this is the file you open
firmware/README.md    wiring + the serial line format the parser expects
docs/
  DEMO_GUIDE.md       hardware setup and step-by-step demonstration script
  initialcoding.txt   the original single-file prototype, kept for reference
```

`viewer/template.html` is not openable on its own — its `/*__..._*/` markers are
filled in by `build_viewer.py`.

---

## Known limitations

- **SSIDs containing a comma are dropped.** The logger splits serial lines on
  `,` and requires exactly 10 fields, so an SSID with a comma in it produces 11
  and is skipped. Rare in practice; fixing it properly means changing the
  firmware's output format.
- **GPS accuracy bounds the resolution.** The NEO-6M is good to roughly 2.5 m
  CEP, which is why AP sightings are merged at 3 m and coverage cells are 5 m.
  Finer binning would be reading precision the hardware doesn't have.
- **Dot colour is a median.** A cluster of 60 measurements shows the median
  RSSI, so a single anomalous reading doesn't recolour the dot — but the spread
  within a cluster isn't visible. The measurement count in the popup tells you
  how much is being summarised.
- **Indoor fixes drift.** Scans taken inside buildings inherit whatever fix the
  GPS last managed, so indoor measurements are less trustworthy than outdoor ones.
- **The basemap needs a provider that allows `file://`.** See
  [Basemap tiles](#basemap-tiles) below — two obvious providers refuse this
  page, and both refuse it as a 200 OK image, so nothing detects it
  automatically.
