import os
import math
import tempfile
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
import numpy as np
from eccodes import (
    codes_grib_new_from_file,
    codes_get_array,
    codes_release,
)

LAT = 54.696
LON = 18.678

BASE = "https://opendata.dwd.de/weather/nwp/v1/m/icon-d2-ruc/p"
VARIABLES = ["U_10M", "V_10M", "VMAX_10M"]

WARSAW = ZoneInfo("Europe/Warsaw")


def file_url(variable, run, lead):
    run_name = run.strftime("%Y-%m-%dT%H:00")
    return (
        f"{BASE}/{variable}/r/{run_name}/s/"
        f"PT{lead:03d}H00M.grib2"
    )


def exists(url):
    try:
        r = requests.head(url, timeout=15, allow_redirects=True)
        return r.status_code == 200
    except requests.RequestException:
        return False


def find_latest_complete_run():
    now = datetime.now(timezone.utc).replace(
        minute=0, second=0, microsecond=0
    )

    # Cofamy się maksymalnie o 8 godzin.
    # Wybieramy pierwszy przebieg, który ma komplet danych
    # co najmniej do +14 h dla wszystkich trzech parametrów.
    for back in range(0, 9):
        run = now - timedelta(hours=back)

        checks = [
            exists(file_url(var, run, 14))
            for var in VARIABLES
        ]

        if all(checks):
            return run

    raise RuntimeError(
        "Nie znaleziono kompletnego przebiegu ICON-D2 RUC."
    )


def download(url):
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    return r.content


def read_grib(data, nearest_index=None):
    with tempfile.NamedTemporaryFile(
        suffix=".grib2", delete=False
    ) as tmp:
        tmp.write(data)
        path = tmp.name

    try:
        with open(path, "rb") as f:
            gid = codes_grib_new_from_file(f)

            if gid is None:
                raise RuntimeError("Nie udało się odczytać GRIB2.")

            values = np.asarray(
                codes_get_array(gid, "values"),
                dtype=float
            )

            if nearest_index is None:
                lats = np.asarray(
                    codes_get_array(gid, "latitudes"),
                    dtype=float
                )
                lons = np.asarray(
                    codes_get_array(gid, "longitudes"),
                    dtype=float
                )

                # Korekta odległości długości geograficznej
                # dla szerokości Jastarni.
                coslat = math.cos(math.radians(LAT))

                dist2 = (
                    (lats - LAT) ** 2
                    + ((lons - LON) * coslat) ** 2
                )

                nearest_index = int(np.nanargmin(dist2))
                grid_lat = float(lats[nearest_index])
                grid_lon = float(lons[nearest_index])
            else:
                grid_lat = None
                grid_lon = None

            value = float(values[nearest_index])

            codes_release(gid)

            return value, nearest_index, grid_lat, grid_lon

    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def wind_direction(u, v):
    # Kierunek meteorologiczny: SKĄD wieje wiatr.
    deg = (
        math.degrees(math.atan2(-u, -v)) + 360
    ) % 360

    labels = [
        "N", "NNE", "NE", "ENE",
        "E", "ESE", "SE", "SSE",
        "S", "SSW", "SW", "WSW",
        "W", "WNW", "NW", "NNW",
    ]

    label = labels[
        int((deg + 11.25) // 22.5) % 16
    ]

    return deg, label


def ms_to_kn(value):
    return value * 1.943844492


def row_color(speed):
    if speed >= 22:
        return "#ffe4e4"
    if speed >= 16:
        return "#fff0c7"
    if speed >= 10:
        return "#e7f7df"
    return "#ffffff"


def main():
    run = find_latest_complete_run()

    print(
        "Wybrany przebieg ICON-D2 RUC:",
        run.strftime("%Y-%m-%d %H:%M UTC"),
    )

    rows = []
    grid_index = None
    grid_lat = None
    grid_lon = None

    for lead in range(0, 15):

        u_data = download(
            file_url("U_10M", run, lead)
        )
        v_data = download(
            file_url("V_10M", run, lead)
        )
        gust_data = download(
            file_url("VMAX_10M", run, lead)
        )

        u, idx, lat0, lon0 = read_grib(
            u_data, grid_index
        )

        if grid_index is None:
            grid_index = idx
            grid_lat = lat0
            grid_lon = lon0

            print(
                "Punkt siatki:",
                f"{grid_lat:.4f} N, {grid_lon:.4f} E"
            )

        v, _, _, _ = read_grib(
            v_data, grid_index
        )

        gust, _, _, _ = read_grib(
            gust_data, grid_index
        )

        speed = ms_to_kn(math.hypot(u, v))
        gust_kn = ms_to_kn(gust)

        deg, direction = wind_direction(u, v)

        valid_utc = run + timedelta(hours=lead)
        valid_local = valid_utc.astimezone(WARSAW)

        rows.append({
            "time": valid_local,
            "speed": speed,
            "gust": gust_kn,
            "deg": deg,
            "direction": direction,
        })

        print(
            valid_local.strftime("%H:%M"),
            f"{speed:.1f} kn",
            f"porywy {gust_kn:.1f} kn",
            direction,
        )

    generated = datetime.now(WARSAW)

    table_rows = []

    days = {
        0: "pon.",
        1: "wt.",
        2: "śr.",
        3: "czw.",
        4: "pt.",
        5: "sob.",
        6: "niedz.",
    }

    for row in rows:
        t = row["time"]

        time_text = (
            f"{days[t.weekday()]} "
            f"{t:%d.%m %H:%M}"
        )

        bg = row_color(row["speed"])

        table_rows.append(
            f"""
            <tr style="background:{bg}">
              <td>{time_text}</td>
              <td><b>{row["speed"]:.1f}</b> kn</td>
              <td>{row["gust"]:.1f} kn</td>
              <td>
                {row["direction"]}
                {row["deg"]:.0f}°
              </td>
            </tr>
            """
        )

    run_local = run.astimezone(WARSAW)

    html = f"""<!DOCTYPE html>
<html lang="pl">
<head>
<meta charset="UTF-8">
<meta name="viewport"
      content="width=device-width, initial-scale=1.0">

<title>Jastarnia – ICON-D2 RUC</title>

<style>

body {{
  font-family:
    -apple-system,
    BlinkMacSystemFont,
    "Segoe UI",
    Arial,
    sans-serif;

  margin: 0;
  background: #f3f6f9;
  color: #17202a;
}}

header {{
  background: #1769aa;
  color: white;
  padding: 18px;
  text-align: center;
}}

header h1 {{
  margin: 0;
  font-size: 26px;
}}

header p {{
  margin: 6px 0 0;
}}

main {{
  max-width: 900px;
  margin: auto;
  padding: 14px;
}}

.card {{
  background: white;
  border-radius: 14px;
  padding: 16px;
  margin-bottom: 14px;
  box-shadow:
    0 2px 10px rgba(0,0,0,.10);
}}

.info {{
  color: #5d6d7e;
  line-height: 1.5;
}}

table {{
  width: 100%;
  border-collapse: collapse;
  font-variant-numeric: tabular-nums;
}}

th, td {{
  padding: 11px 8px;
  border-bottom: 1px solid #ddd;
  text-align: right;
}}

th {{
  background: #1769aa;
  color: white;
}}

th:first-child,
td:first-child {{
  text-align: left;
}}

th:last-child,
td:last-child {{
  text-align: center;
}}

footer {{
  text-align: center;
  padding: 18px;
  color: #687078;
  font-size: 13px;
}}

@media (max-width: 600px) {{

  main {{
    padding: 8px;
  }}

  .card {{
    padding: 10px;
  }}

  th, td {{
    padding: 9px 5px;
    font-size: 14px;
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

<b>Model:</b> DWD ICON-D2 RUC<br>

<b>Przebieg:</b>
{run_local:%d.%m.%Y %H:%M}
czasu polskiego<br>

<b>Punkt docelowy:</b>
{LAT:.3f}° N, {LON:.3f}° E<br>

<b>Punkt siatki:</b>
{grid_lat:.4f}° N,
{grid_lon:.4f}° E<br>

<b>Aktualizacja strony:</b>
{generated:%d.%m.%Y %H:%M}

</div>


<div class="card">

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
Dane: Deutscher Wetterdienst (DWD)
ICON-D2 Rapid Update Cycle
</footer>

</body>
</html>
"""

    with open(
        "index.html",
        "w",
        encoding="utf-8"
    ) as f:
        f.write(html)

    print("Gotowe: zapisano index.html")


if __name__ == "__main__":
    main()
