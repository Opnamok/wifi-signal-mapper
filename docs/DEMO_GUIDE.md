# Hardware setup and demonstration guide

Everything needed to wire the rig up and run the demonstration end to end.

- [Part 1 — Wiring the hardware](#part-1--wiring-the-hardware)
- [Part 2 — One-time software setup](#part-2--one-time-software-setup)
- [Part 3 — The hour before the demo](#part-3--the-hour-before-the-demo)
- [Part 4 — Running the demonstration](#part-4--running-the-demonstration)
- [Part 5 — Questions you'll be asked](#part-5--questions-youll-be-asked)
- [Troubleshooting](#troubleshooting)

---

## Part 1 — Wiring the hardware

### Parts

| Part | Role |
|---|---|
| Seeed Studio XIAO ESP32-C6 | WiFi scanning + serial output to the laptop |
| NEO-6M GPS module (with ceramic patch antenna) | Position fix |
| USB-C cable (**data**, not charge-only) | Power + serial to the laptop |
| 4 jumper wires | GPS ↔ XIAO |

### Connections

```
   NEO-6M                         XIAO ESP32-C6
  ────────                       ───────────────
   VCC   ───────────────────►     3V3
   GND   ───────────────────►     GND
   TX    ───────────────────►     D7    (GPIO17, UART RX)
   RX    ◄───────────────────     D6    (GPIO16, UART TX)
```

Four things worth getting right:

1. **TX goes to RX, RX goes to TX.** The cross-over is the single most common
   wiring mistake. The GPS *transmits* NMEA sentences; the XIAO *receives* them,
   so the module's TX must land on the XIAO's RX pin (D7).
2. **Power from 3V3, not 5V.** The NEO-6M runs on anything from 3 V to 5 V, but
   the XIAO is a 3.3 V-logic board and voltage above 3.3 V on a GPIO can damage
   the chip. Powering the module from 3V3 guarantees its TX line idles at a
   level the XIAO is happy with.
3. **The RX wire is optional.** We only ever read from the module, so D6 → RX
   is only needed if the sketch sends configuration commands (changing update
   rate or baud). Leave it connected — it costs nothing and keeps the wiring
   matching this diagram.
4. **The antenna must see sky.** The ceramic patch antenna goes face-up with
   nothing metal above it. Indoors, next to a window is the minimum; under a
   concrete slab it will never lock.

### Baud rates

Two different serial links, two different speeds — don't mix them up:

| Link | Baud | Set where |
|---|---|---|
| NEO-6M → XIAO (D7/D6) | **9600** | `Serial1.begin(9600, SERIAL_8N1, D7, D6)` in the sketch |
| XIAO → laptop (USB) | **115200** | `SERIAL_BAUD` in `wifi_mapper.py`, and the Arduino Serial Monitor |

### Checking the wiring before you trust it

Plug in the USB cable and open the Arduino IDE's Serial Monitor at **115200**.
You should see CSV lines appearing:

```
2026-08-27 23:07:48,12.972421,79.162997,159.25,8,L-VIT,-53,6,Open,Good
```

Read the output this way:

- **Lines appearing with real coordinates** → everything works.
- **Lines appearing but with `0,0` coordinates** → WiFi scanning works, the GPS
  hasn't locked yet. Wait, or move toward a window. This is normal for the first
  30 seconds to several minutes after a cold start.
- **No lines at all** → the sketch isn't running or the baud is wrong. Check the
  Serial Monitor is at 115200 and that the USB cable carries data.
- **Garbled characters** → baud mismatch. 115200 on the monitor.

The GPS module's own LED is the quickest fix indicator: **blinking once per
second means it has a fix**; solid or dark means it doesn't.

---

## Part 2 — One-time software setup

```bash
pip install -r requirements.txt
```

**Find the COM port.** Windows Device Manager → Ports (COM & LPT) → look for the
XIAO. `wifi_mapper.py` defaults to `COM6`; if yours differs, change `SERIAL_PORT`
at the top of the file.

**Google Sheets credentials.** `log` and `pull` need a service-account key saved
as `credentials.json` in the project root — see the setup steps in the main
[README](../README.md#google-sheets-setup). It is gitignored; never commit it.

> Only the Arduino IDE **or** `wifi_mapper.py` can hold the serial port, never
> both. Close the Serial Monitor before running `log`.

---

## Part 3 — The hour before the demo

A short checklist that removes almost every way this goes wrong:

- [ ] **Refresh the map from the latest logs.**
      ```bash
      python wifi_mapper.py pull
      python build_viewer.py
      ```
      Confirm it prints the expected counts and writes `viewer/wifi_map.html`.
- [ ] **Open `viewer/wifi_map.html` once** and click through all three views, so
      the basemap tiles are already in the browser cache.
- [ ] **Power the board up early.** Leave it on a windowsill for 15–20 minutes.
      A GPS that already has a fix re-acquires in seconds when you move it; one
      that has been off for days takes minutes. This is the single biggest risk
      in the live portion and costs nothing to eliminate.
- [ ] **Have the Google Sheet open** in a browser tab, scrolled to the bottom.
- [ ] **Do NOT run `python wifi_mapper.py reset`.** It wipes the Sheet. The
      committed `data/scan_log.csv` would survive, but there is no reason to
      take the risk on demo day.
- [ ] **Know your fallback**: if the laptop has no internet, the map still plots
      every point — only the basemap tiles go missing, and the page says so.

---

## Part 4 — Running the demonstration

Roughly 8–10 minutes. The map comes first on purpose: it is the part that
cannot fail, and it gives the evaluator the full picture before any hardware
is asked to cooperate.

### Act 1 — The result (4 minutes)

**Open `viewer/wifi_map.html`** by double-clicking it.

> "This is one HTML file. No server, no install — the data and the map library
> are inlined into it. 2,869 measurements, 217 scan points, 59 networks."

**1. AP dots** (the default view).

> "Each dot is one network at one location. Repeat sightings within 3 metres are
> merged, so standing still for a minute gives one dot, not sixty. The colour is
> the median signal of that cluster."

Click a dot — the popup shows SSID, signal and quality, channel, encryption, how
many measurements it summarises, and when it was first and last seen.

**2. Filter it live.** This is where it stops looking like a static picture:

- Drag **Minimum signal** to about −60 → 257 points collapse to 15, across 3
  networks. *"Only three networks anywhere on this route are genuinely strong."*
- Click **Open** then **Secured** → 123 points across 10 networks vs 134 across
  49. *"The campus networks are few but everywhere; the secured ones are mostly
  personal hotspots, seen once each."*
- Type `VIT` in the search box, click a name → the map zooms to that network.

**3. Coverage.** Switch views; pick **VIT** or **L-VIT** from the dropdown.

> "Measurements binned into 5-metre cells and averaged. An empty cell means we
> never measured there — not that there's no signal."

**4. Density.** Switch again.

> "How many distinct networks were visible at each scan point — up to 15 near
> L Block. This is about channel congestion, not signal strength."

### Act 2 — The hardware (4 minutes)

**Show the rig and the wiring** — four wires, GPS on the hardware UART at 9600,
board talking to the laptop at 115200.

**Start logging:**

```bash
python wifi_mapper.py log
```

Then open **<http://localhost:8765>** in a browser.

Three things are now happening from one read loop, and it's worth saying so:

1. The terminal prints each accepted row, plus a consolidated status block every
   5 seconds listing every network visible right now.
2. The live map moves a marker to the current fix and draws the networks around
   it, fanned out so overlapping ones stay clickable.
3. Rows are batched to the Google Sheet every 5 seconds — switch to the Sheet
   tab and refresh to show fresh rows arriving.

> "Rows that arrive before the GPS has a fix carry 0,0 and are dropped at both
> ends — they never reach the Sheet or the map."

**Close the loop.** Stop with `Ctrl+C` (it flushes whatever is still batched),
then:

```bash
python wifi_mapper.py pull
python build_viewer.py
```

Reopen the map and point out that the data just collected is now in it. That
single round trip — board → Sheet → CSV → map — is the whole project in one
gesture.

### If the GPS won't lock

It happens indoors and it is not a failure of the project. Say what's happening
and keep moving:

> "No fix in here, which is expected under a concrete slab — the logger is
> deliberately refusing to write rows it can't place on a map."

Then show the Arduino Serial Monitor instead: the CSV lines are still streaming
with real SSIDs and RSSI values and `0,0` coordinates, which proves the WiFi
half works and isolates the problem to sky visibility. The map from Act 1 has
already shown what the data looks like once a fix exists.

---

## Part 5 — Questions you'll be asked

**"Why is the coverage view a grid instead of a smooth heatmap?"**

The strongest answer in the project. A blurred heat layer renders *density* — it
brightens where measurements are frequent, not where signal is strong. Feeding
RSSI into one produces a map of where you stood longest. So coverage averages
RSSI into fixed cells, which is an honest surface, and the density view keeps
the heat layer because density is genuinely the quantity it measures.

**"Why cluster at 3 metres?"**

The NEO-6M is accurate to roughly 2.5 m CEP. Merging below that would be
claiming precision the hardware doesn't have; the 5 m coverage cells follow the
same reasoning.

**"Why the median and not the mean?"**

One anomalous reading in a cluster of sixty shouldn't recolour the dot. The
popup shows the measurement count so you can see how much is being summarised.

**"One AP can appear as several dots — why?"**

Because we're mapping *where the signal was observed*, not where the router is.
Each dot is a measurement location. Trilaterating actual AP positions would need
several well-separated fixes per AP and is a reasonable extension.

**"What are the limitations?"**

Listed honestly in the [README](../README.md#known-limitations): SSIDs
containing a comma are dropped by the 10-field parser, GPS accuracy bounds the
resolution, dot colour is a median, and indoor fixes drift.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `could not open port 'COM6'` | Wrong port, or the Arduino Serial Monitor is holding it | Close the Serial Monitor; check Device Manager and update `SERIAL_PORT` |
| Serial Monitor shows garbage | Baud mismatch | Set the monitor to 115200 |
| Rows stream but always `0,0` | No GPS fix | Move to a window or outdoors; check the GPS LED blinks once per second |
| No serial output at all | Sketch not running, or charge-only USB cable | Re-flash; try a known data cable |
| `log` prints nothing | Working as designed — every row so far lacked a fix | Same as the `0,0` case above |
| Live map stuck on "waiting for first GPS fix" | Same — no fixed row has arrived yet | Same |
| `FileNotFoundError: credentials.json` | Service-account key missing | See [README](../README.md#google-sheets-setup) |
| `Rate limited, retrying in 2s...` | Google Sheets write quota | Harmless; it backs off and retries automatically |
| `No data at data/scan_log.csv` | Snapshot never downloaded | `python wifi_mapper.py pull` |
| Map opens but the background is blank | No internet for basemap tiles | Expected; the banner says so and all measurements still plot |
| Background is tiled with "Access blocked" or "API KEY REQUIRED" | A tile provider is refusing the request — it answers with a placeholder image, so nothing detects it automatically | The map uses the FOSSGIS OSM mirror precisely to avoid this. If someone has changed the provider, revert it: `tile.openstreetmap.org` and CARTO both refuse `file://` pages. See [Basemap tiles](../README.md#basemap-tiles) |
| Map shows old data | Snapshot is stale | `python wifi_mapper.py pull && python build_viewer.py` |
