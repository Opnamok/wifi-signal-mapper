"""
build_viewer.py - turn the logged CSV into a single self-contained map page.

    python build_viewer.py

Reads data/scan_log.csv (the snapshot produced by `python wifi_mapper.py pull`),
derives three datasets from it, and writes viewer/wifi_map.html with the data
and the Leaflet libraries inlined. The result opens by double-clicking - no
server, no build step, and no network needed except for the map tiles.

This script deliberately has no dependency on gspread, pyserial or
credentials.json: anyone can rebuild the map from the CSV alone.
"""

import json
import os
import sys
from math import cos, floor, radians

import numpy as np
import pandas as pd
from sklearn.cluster import DBSCAN


# ============================================================
# Configuration
# ============================================================

CSV_PATH = os.path.join("data", "scan_log.csv")
TEMPLATE_PATH = os.path.join("viewer", "template.html")
OUTPUT_PATH = os.path.join("viewer", "wifi_map.html")
VENDOR_DIR = os.path.join("viewer", "vendor")

CLUSTER_M = 3.0      # DBSCAN radius for merging repeat sightings of one AP
CELL_M = 5.0         # coverage-grid cell size
EARTH_RADIUS = 6371000.0
M_PER_DEG_LAT = 111320.0


# ============================================================
# Load and clean
# ============================================================

def load_clean(csv_path: str = CSV_PATH) -> pd.DataFrame:
    """Read the log and drop rows that can't be placed on a map."""
    df = pd.read_csv(csv_path)

    missing = [c for c in ("Latitude", "Longitude", "RSSI_dBm", "SSID") if c not in df.columns]
    if missing:
        raise SystemExit(f"{csv_path} is missing required column(s): {', '.join(missing)}")

    raw_rows = len(df)
    df = df.dropna(subset=["Latitude", "Longitude", "RSSI_dBm", "SSID"])

    # Rows logged before the GPS acquired a fix carry the 0,0 placeholder.
    no_fix = ((df["Latitude"] == 0) & (df["Longitude"] == 0)).sum()
    df = df[~((df["Latitude"] == 0) & (df["Longitude"] == 0))]

    df = df.copy()
    df["SSID"] = df["SSID"].astype(str)
    df["Encryption"] = df["Encryption"].astype(str)

    if df.empty:
        raise SystemExit(
            f"No usable rows in {csv_path}. "
            "Every row was either blank or logged before the GPS had a fix."
        )

    print(f"  {raw_rows:,} rows read, {no_fix:,} dropped as pre-fix (0,0), {len(df):,} usable")
    return df


# ============================================================
# AP dots - cluster repeat sightings of the same network
# ============================================================

def cluster_by_ssid(df: pd.DataFrame) -> pd.DataFrame:
    """
    Group nearby measurements of one SSID into a single point.

    One AP seen across 40 consecutive scans while standing still would
    otherwise draw 40 dots on top of each other. DBSCAN with min_samples=1
    keeps every measurement (nothing is discarded as noise) and merges those
    within CLUSTER_M metres, using the haversine metric on radian coordinates.
    """
    rows = []

    for ssid, ssid_df in df.groupby("SSID", sort=False):
        coords = np.radians(ssid_df[["Latitude", "Longitude"]].values)

        labels = DBSCAN(
            eps=CLUSTER_M / EARTH_RADIUS,
            min_samples=1,
            metric="haversine",
        ).fit_predict(coords)

        ssid_df = ssid_df.assign(_cluster=labels)

        for _, cluster in ssid_df.groupby("_cluster", sort=False):
            timestamps = cluster["Timestamp"].astype(str)
            rows.append({
                "SSID": ssid,
                "Latitude": cluster["Latitude"].mean(),
                "Longitude": cluster["Longitude"].mean(),
                # Median, not mean: one anomalous reading shouldn't recolour the dot.
                "RSSI_dBm": cluster["RSSI_dBm"].median(),
                "Channel": cluster["Channel"].mode().iloc[0],
                "Encryption": cluster["Encryption"].mode().iloc[0],
                "Measurements": len(cluster),
                "FirstSeen": timestamps.min(),
                "LastSeen": timestamps.max(),
            })

    return pd.DataFrame(rows)


# ============================================================
# Coverage grid - average RSSI per cell, per SSID
# ============================================================

def build_coverage_grid(df: pd.DataFrame, origin_lat: float, origin_lon: float,
                        cell_lat: float, cell_lon: float) -> dict:
    """
    Bin raw measurements into fixed cells and average the RSSI in each.

    Signal strength is a scalar field, not a density, so a blurred heat layer
    would be misleading: it brightens where measurements are *frequent* rather
    than where signal is *strong*. Averaging into cells keeps the quantity
    honest, and a cell with no measurement is simply not drawn.
    """
    binned = df.assign(
        iy=np.floor((df["Latitude"] - origin_lat) / cell_lat).astype(int),
        ix=np.floor((df["Longitude"] - origin_lon) / cell_lon).astype(int),
    )

    agg = (
        binned.groupby(["SSID", "iy", "ix"], sort=False)
        .agg(MeanRSSI=("RSSI_dBm", "mean"), Count=("RSSI_dBm", "size"))
        .reset_index()
    )

    grid = {}
    for row in agg.itertuples(index=False):
        grid.setdefault(row.SSID, []).append(
            [int(row.iy), int(row.ix), round(float(row.MeanRSSI), 1), int(row.Count)]
        )
    return grid


# ============================================================
# Scan points - how many networks were visible at each fix
# ============================================================

def build_scan_points(df: pd.DataFrame) -> pd.DataFrame:
    """One row per distinct (time, place) scan, with the AP count seen there."""
    return (
        df.groupby(["Timestamp", "Latitude", "Longitude"], sort=True)
        .agg(APCount=("SSID", "nunique"), BestRSSI=("RSSI_dBm", "max"))
        .reset_index()
    )


# ============================================================
# Assemble the payload
# ============================================================

def build_payload(df: pd.DataFrame) -> dict:
    lat_min, lat_max = float(df["Latitude"].min()), float(df["Latitude"].max())
    lon_min, lon_max = float(df["Longitude"].min()), float(df["Longitude"].max())

    # Metres-per-degree varies with latitude, so fix it once at the data's centre.
    # Over a campus-sized area the error from treating it as constant is negligible.
    lat0 = (lat_min + lat_max) / 2.0
    cell_lat = CELL_M / M_PER_DEG_LAT
    cell_lon = CELL_M / (M_PER_DEG_LAT * cos(radians(lat0)))

    print("  clustering AP sightings...")
    aps = cluster_by_ssid(df)

    print("  binning coverage grid...")
    grid_by_ssid = build_coverage_grid(df, lat_min, lon_min, cell_lat, cell_lon)

    print("  aggregating scan points...")
    scans = build_scan_points(df)

    # String tables + integer indices keep the inlined payload compact.
    ssids = sorted(df["SSID"].unique())
    ssid_index = {name: i for i, name in enumerate(ssids)}
    encs = sorted(df["Encryption"].unique())
    enc_index = {name: i for i, name in enumerate(encs)}

    ap_rows = [
        [
            ssid_index[r.SSID],
            round(float(r.Latitude), 6),
            round(float(r.Longitude), 6),
            round(float(r.RSSI_dBm), 1),
            int(r.Channel),
            enc_index[r.Encryption],
            int(r.Measurements),
            str(r.FirstSeen),
            str(r.LastSeen),
        ]
        for r in aps.itertuples(index=False)
    ]

    scan_rows = [
        [
            round(float(r.Latitude), 6),
            round(float(r.Longitude), 6),
            int(r.APCount),
            int(r.BestRSSI),
            str(r.Timestamp),
        ]
        for r in scans.itertuples(index=False)
    ]

    grid = {str(ssid_index[name]): cells for name, cells in grid_by_ssid.items()}

    timestamps = df["Timestamp"].astype(str)

    return {
        "meta": {
            "center": [round((lat_min + lat_max) / 2, 6), round((lon_min + lon_max) / 2, 6)],
            "bounds": [[lat_min, lon_min], [lat_max, lon_max]],
            "originLat": lat_min,
            "originLon": lon_min,
            "cellLat": cell_lat,
            "cellLon": cell_lon,
            "cellM": int(CELL_M),
            "clusterM": int(CLUSTER_M),
            "rows": int(len(df)),
            "scans": int(len(scans)),
            "timeStart": timestamps.min(),
            "timeEnd": timestamps.max(),
        },
        "ssids": ssids,
        "enc": encs,
        "aps": ap_rows,
        "grid": grid,
        "scans": scan_rows,
    }


# ============================================================
# Render
# ============================================================

def read_vendor(filename: str) -> str:
    path = os.path.join(VENDOR_DIR, filename)
    if not os.path.exists(path):
        raise SystemExit(
            f"Missing {path}. The Leaflet libraries are committed alongside this "
            "project so the output needs no CDN; re-download them with:\n"
            "  curl -L -o viewer/vendor/leaflet.css      https://unpkg.com/leaflet@1.9.4/dist/leaflet.css\n"
            "  curl -L -o viewer/vendor/leaflet.js       https://unpkg.com/leaflet@1.9.4/dist/leaflet.js\n"
            "  curl -L -o viewer/vendor/leaflet-heat.js  https://unpkg.com/leaflet.heat@0.2.0/dist/leaflet-heat.js"
        )
    with open(path, encoding="utf-8") as f:
        return f.read()


def render(payload: dict, template_path: str = TEMPLATE_PATH,
           output_path: str = OUTPUT_PATH) -> None:
    with open(template_path, encoding="utf-8") as f:
        html = f.read()

    # "</" inside a <script> block would end the block early, whatever the
    # surrounding JSON quoting says. An SSID is free-form text, so escape it.
    data_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")

    substitutions = {
        "/*__LEAFLET_CSS__*/": read_vendor("leaflet.css"),
        "/*__LEAFLET_JS__*/": read_vendor("leaflet.js"),
        "/*__LEAFLET_HEAT_JS__*/": read_vendor("leaflet-heat.js"),
        "/*__DATA__*/": data_json,
    }

    for marker, value in substitutions.items():
        if marker not in html:
            raise SystemExit(f"{template_path} no longer contains the marker {marker}")
        html = html.replace(marker, value, 1)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)


# ============================================================
# Entry point
# ============================================================

def main() -> None:
    # SSIDs are arbitrary text and routinely contain non-ASCII characters,
    # which the default Windows console encoding cannot print.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if not os.path.exists(CSV_PATH):
        raise SystemExit(
            f"No data at {CSV_PATH}.\nRun `python wifi_mapper.py pull` first to "
            "download the logged data from the Google Sheet."
        )

    print(f"Reading {CSV_PATH} ...")
    df = load_clean()

    payload = build_payload(df)
    render(payload)

    size_kb = os.path.getsize(OUTPUT_PATH) / 1024
    cells = sum(len(v) for v in payload["grid"].values())
    meta = payload["meta"]

    print()
    print(f"  measurements   {meta['rows']:,}")
    print(f"  scan points    {meta['scans']:,}")
    print(f"  networks       {len(payload['ssids']):,}")
    print(f"  AP clusters    {len(payload['aps']):,}  (within {meta['clusterM']} m)")
    print(f"  grid cells     {cells:,}  ({meta['cellM']} x {meta['cellM']} m)")
    print(f"  time span      {meta['timeStart']} to {meta['timeEnd']}")
    print()
    print(f"Wrote {OUTPUT_PATH} ({size_kb:,.0f} KB). Open it in a browser.")


if __name__ == "__main__":
    main()
