


import math
import os
import tempfile
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from eccodes import (
    codes_grib_find_nearest,
    codes_grib_new_from_file,
    codes_release,
)

LAT = 54.696
LON = 18.678

BASE = "https://opendata.dwd.de/weather/nwp/v1/m/icon-d2-ruc/p"
VARIABLES = ("U_10M", "V_10M", "VMAX_10M")
WARSAW = ZoneInfo("Europe/Warsaw")
USER_AGENT = "Jastarnia-ICON-D2-RUC/1.0"


def file_url(variable, run, lead):
    run_name = run.strftime("%Y-%m-%dT%H:00")
    return (
        f"{BASE}/{variable}/r/{run_name}/s/"
        f"PT{lead:03d}H00M.grib2"
    )


def url_exists(url):
    try:
        response = requests.head(
            url,
            timeout=20,
            allow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )
        return response.status_code == 200
    except requests.RequestException:
        return False


def find_latest_complete_run():
    now = datetime.now(timezone.utc).replace(
        minute=0, second=0, microsecond=0
    )

    for hours_back in range(0, 10):
        run = now - timedelta(hours=hours_back)
        if all(url_exists(file_url(var, run, 14)) for var in VARIABLES):
            return run

    raise RuntimeError(
        "Nie znaleziono kompletnego przebiegu ICON-D2 RUC "
        "z prognozą do +14 h."
    )


def download(url):
    response = requests.get(
        url,
        timeout=90,
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()
    return response.content


def nearest_value(grib_data):
    path = None
    gid = None

    try:
        with tempfile.NamedTemporaryFile(
            suffix=".grib2", delete=False
        ) as tmp:
            tmp.write(grib_data)
            path = tmp.name

        with open(path, "rb") as handle:
            gid = codes_grib_new_from_file(handle)

            if gid is None:
                raise RuntimeError("Nie udało się odczytać pliku GRIB2.")

            nearest = codes_grib_find_nearest(
                gid, LAT, LON
            )[0]

            return (
                float(nearest.value),
                float(nearest.lat),
                float(nearest.lon),
            )

    finally:
        if gid is not None:
            codes_release(gid)

        if path:
            try:
                os.remove(path)
            except OSError:
                pass


def ms_to_kn(value):
    return value * 1.943844492


def wind_direction(u, v):
    degrees = (
        math.degrees(math.atan2(-u, -v)) + 360.0
    ) % 360.0

    labels = (
        "N", "NNE", "NE", "ENE",
        "E", "ESE", "SE", "SSE",
        "S", "SSW", "SW", "WSW",
        "W", "WNW", "NW", "NNW",
    )

    label = labels[
        int((degrees + 11.25) // 22.5) % 16
    ]

    return degrees, label


def row_color(speed_kn):
    if speed_kn >= 22:
        return "#ffe5e5"
    if speed_kn >= 16:
        return "#fff0c7"
    if speed_kn >= 10:
        return "#e9f8df"
    return "#ffffff"


def main():
    run = find_latest_complete_run()

    print(
        "Wybrany przebieg ICON-D2 RUC:",
        run.strftime("%Y-%m-%d %H:%M UTC"),
    )

    rows = []
    grid_lat = None
    grid_lon = None

    for lead in range(0, 15):
        u, lat_u, lon_u = nearest_value(
            download(file_url("U_10M", run, lead))
        )
        v, _, _ = nearest_value(
            download(file_url("V_10M", run, lead))
        )
        gust, _, _ = nearest_value(
            download(file_url("VMAX_10M", run, lead))
        )

        if grid_lat is None:
            grid_lat = lat_u
            grid_lon = lon_u
            print(
                "Najbliższy punkt siatki:",
                f"{grid_lat:.4f}°N, {grid_lon:.4f}°E",
            )

        speed_kn = ms_to_kn(math.hypot(u, v))
        gust_kn = ms_to_kn(gust)
        degrees, direction = wind_direction(u, v)

        valid_local = (
            run + timedelta(hours=lead)
        ).astimezone(WARSAW)

        rows.append(
            {
                "time": valid_local,
                "speed": speed_kn,
                "gust": gust_kn,
                "degrees": degrees,
                "direction": direction,
            }
        )

        print(
            valid_local.strftime("%d.%m %H:%M"),
            f"{speed_kn:.1f} kn",
            f"porywy {gust_kn:.1f} kn",
            f"{direction} {degrees:.0f}°",
        )

    generated = datetime.now(WARSAW)
    run_local = run.astimezone(WARSAW)

    day_names = {
        0: "pon.",
        1: "wt.",
        2: "śr.",
        3: "czw.",
        4: "pt.",
        5: "sob.",
        6: "niedz.",
    }

    table_rows = []

    for row in rows:
        t = row["time"]
        stamp = (
            f"{day_names[t.weekday()]} "
            f"{t:%d.%m %H:%M}"
        )

        table_rows.append(
            f"""
            <tr style="background:{row_color(row['speed'])}">
              <td>{stamp}</td>
              <td><b>{row['speed']:.1f}</b> kn</td>
              <td>{row['gust']:.1f} kn</td>
              <td>{row['direction']} {row['degrees']:.0f}°</td>
            </tr>
            """
        )

    html = f"""<!doctype html>
<html lang="pl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Jastarnia – ICON-D2 RUC</title>
<style>
body {{
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif;
  margin:0;
  background:#f3f6f9;
  color:#17202a;
}}
header {{
  background:#1769aa;
  color:white;
  padding:18px;
  text-align:center;
}}
header h1 {{
  margin:0;
  font-size:26px;
}}
header p {{
  margin:6px 0 0;
}}
main {{
  max-width:900px;
  margin:auto;
  padding:14px;
}}
.card {{
  background:white;
  border-radius:14px;
  padding:16px;
  margin-bottom:14px;
  box-shadow:0 2px 10px rgba(0,0,0,.10);
}}
.info {{
  color:#5d6d7e;
  line-height:1.5;
}}
table {{
  width:100%;
  border-collapse:collapse;
  font-variant-numeric:tabular-nums;
}}
th,td {{
  padding:11px 8px;
  border-bottom:1px solid #ddd;
  text-align:right;
}}
th {{
  background:#1769aa;
  color:white;
}}
th:first-child,td:first-child {{
  text-align:left;
}}
th:last-child,td:last-child {{
  text-align:center;
}}
footer {{
  text-align:center;
  padding:18px;
  color:#687078;
  font-size:13px;
}}
@media (max-width:600px) {{
  main {{ padding:8px; }}
  .card {{ padding:10px; }}
  th,td {{
    padding:9px 5px;
    font-size:14px;
  }}
}}
</style>
</head>
<body>
<header>
  <h1>🌬️ Jastarnia – ICON-D2 RUC</h1>
  <p>Prognoza wiatru dla Zatoki Puckiej</p>
</header>
<main>
<div class="card info">
  <b>Model:</b> DWD ICON-D2 Rapid Update Cycle<br>
  <b>Przebieg:</b> {run_local:%d.%m.%Y %H:%M} czasu polskiego<br>
  <b>Punkt docelowy:</b> {LAT:.3f}°N, {LON:.3f}°E<br>
  <b>Punkt siatki:</b> {grid_lat:.4f}°N, {grid_lon:.4f}°E<br>
  <b>Aktualizacja strony:</b> {generated:%d.%m.%Y %H:%M}
</div>
<div class="card" style="overflow-x:auto">
<table>
<thead>
<tr>
  <th>Godzina</th>
  <th>Wiatr</th>
  <th>Porywy</th>
  <th>Kierunek</th>
</tr>
</thead>
<tbody>
{''.join(table_rows)}
</tbody>
</table>
</div>
</main>
<footer>
Dane: Deutscher Wetterdienst (DWD) · ICON-D2 RUC
</footer>
</body>
</html>
"""

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)

    print("Gotowe: zapisano index.html")


if __name__ == "__main__":
    main()
