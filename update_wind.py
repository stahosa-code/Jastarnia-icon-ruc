import bz2
import math
import os
import tempfile
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from eccodes import (
    codes_get_array,
    codes_grib_new_from_file,
    codes_release,
)

# Punkt na Zatoce Puckiej przy Jastarni
LAT = 54.696
LON = 18.678

WARSAW = ZoneInfo("Europe/Warsaw")
USER_AGENT = "Jastarnia-ICON-D2/2.0"

# ICON-D2 RUC – najbliższe ~14 h
RUC_BASE = "https://opendata.dwd.de/weather/nwp/v1/m/icon-d2-ruc/p"
RUC_VARS = ("U_10M", "V_10M", "VMAX_10M")

# Klasyczny ICON-D2 – do +48 h
D2_BASE = "https://opendata.dwd.de/weather/nwp/icon-d2/grib"
D2_VARS = ("u_10m", "v_10m", "vmax_10m")


def head_ok(url):
    try:
        r = requests.head(
            url,
            timeout=20,
            allow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )
        return r.status_code == 200
    except requests.RequestException:
        return False


def get_bytes(url, compressed_bz2=False):
    r = requests.get(
        url,
        timeout=120,
        headers={"User-Agent": USER_AGENT},
    )
    r.raise_for_status()
    data = r.content
    if compressed_bz2:
        data = bz2.decompress(data)
    return data


def read_values(grib_data):
    path = None
    gid = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".grib2", delete=False) as tmp:
            tmp.write(grib_data)
            path = tmp.name

        with open(path, "rb") as handle:
            gid = codes_grib_new_from_file(handle)
            if gid is None:
                raise RuntimeError("Nie udało się odczytać GRIB2.")
            return codes_get_array(gid, "values")
    finally:
        if gid is not None:
            codes_release(gid)
        if path:
            try:
                os.remove(path)
            except OSError:
                pass


def read_values_lats_lons(grib_data):
    path = None
    gid = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".grib2", delete=False) as tmp:
            tmp.write(grib_data)
            path = tmp.name

        with open(path, "rb") as handle:
            gid = codes_grib_new_from_file(handle)
            if gid is None:
                raise RuntimeError("Nie udało się odczytać GRIB2.")
            values = codes_get_array(gid, "values")
            lats = codes_get_array(gid, "latitudes")
            lons = codes_get_array(gid, "longitudes")
            return values, lats, lons
    finally:
        if gid is not None:
            codes_release(gid)
        if path:
            try:
                os.remove(path)
            except OSError:
                pass


def ms_to_kn(value):
    return float(value) * 1.943844492


def wind_direction(u, v):
    degrees = (math.degrees(math.atan2(-u, -v)) + 360.0) % 360.0
    labels = (
        "N", "NNE", "NE", "ENE",
        "E", "ESE", "SE", "SSE",
        "S", "SSW", "SW", "WSW",
        "W", "WNW", "NW", "NNW",
    )
    label = labels[int((degrees + 11.25) // 22.5) % 16]
    return degrees, label


def row_color(speed_kn):
    if speed_kn >= 22:
        return "#ffe5e5"
    if speed_kn >= 16:
        return "#fff0c7"
    if speed_kn >= 10:
        return "#e9f8df"
    return "#ffffff"


# ---------------- RUC ----------------

def ruc_url(variable, run, lead):
    run_name = run.strftime("%Y-%m-%dT%H:00")
    return (
        f"{RUC_BASE}/{variable}/r/{run_name}/s/"
        f"PT{lead:03d}H00M.grib2"
    )


def find_latest_ruc_run():
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    for back in range(0, 10):
        run = now - timedelta(hours=back)
        if all(head_ok(ruc_url(var, run, 14)) for var in RUC_VARS):
            return run
    raise RuntimeError("Nie znaleziono kompletnego przebiegu ICON-D2 RUC.")


def ruc_grid_index(run):
    clat = read_values(get_bytes(ruc_url("CLAT", run, 0)))
    clon = read_values(get_bytes(ruc_url("CLON", run, 0)))

    lat_is_rad = max(abs(float(x)) for x in clat[:1000]) <= math.pi + 0.1
    lon_is_rad = max(abs(float(x)) for x in clon[:1000]) <= 2 * math.pi + 0.1

    coslat = math.cos(math.radians(LAT))
    best_i = None
    best_d2 = float("inf")
    best_lat = best_lon = None

    for i, (la, lo) in enumerate(zip(clat, clon)):
        la = float(la)
        lo = float(lo)
        if lat_is_rad:
            la = math.degrees(la)
        if lon_is_rad:
            lo = math.degrees(lo)
        if lo > 180:
            lo -= 360

        d2 = (la - LAT) ** 2 + ((lo - LON) * coslat) ** 2
        if d2 < best_d2:
            best_d2 = d2
            best_i = i
            best_lat = la
            best_lon = lo

    return best_i, best_lat, best_lon


def ruc_rows():
    run = find_latest_ruc_run()
    idx, grid_lat, grid_lon = ruc_grid_index(run)
    rows = []

    for lead in range(0, 15):
        u = float(read_values(get_bytes(ruc_url("U_10M", run, lead)))[idx])
        v = float(read_values(get_bytes(ruc_url("V_10M", run, lead)))[idx])
        gust = float(read_values(get_bytes(ruc_url("VMAX_10M", run, lead)))[idx])

        speed_kn = ms_to_kn(math.hypot(u, v))
        gust_kn = ms_to_kn(gust)
        deg, direction = wind_direction(u, v)

        rows.append({
            "time": (run + timedelta(hours=lead)).astimezone(WARSAW),
            "speed": speed_kn,
            "gust": gust_kn,
            "degrees": deg,
            "direction": direction,
            "model": "RUC",
        })

    return run, grid_lat, grid_lon, rows


# ---------------- klasyczny ICON-D2 ----------------

def d2_url(variable, run, lead):
    hour = run.strftime("%H")
    stamp = run.strftime("%Y%m%d%H")
    return (
        f"{D2_BASE}/{hour}/{variable}/"
        f"icon-d2_germany_regular-lat-lon_single-level_"
        f"{stamp}_{lead:03d}_2d_{variable}.grib2.bz2"
    )


def find_latest_d2_run():
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    valid_hours = (0, 6, 9, 12, 15, 18, 21)

    candidates = []
    for back in range(0, 30):
        candidate = now - timedelta(hours=back)
        if candidate.hour in valid_hours:
            candidates.append(candidate)

    for run in candidates:
        # Do wykrycia kompletnego przebiegu wystarczy sprawdzić wiatr na +48 h.
        if head_ok(d2_url("u_10m", run, 48)) and head_ok(d2_url("v_10m", run, 48)):
            return run

    raise RuntimeError("Nie znaleziono kompletnego przebiegu klasycznego ICON-D2.")


def nearest_regular_grid_index(grib_data):
    values, lats, lons = read_values_lats_lons(grib_data)
    coslat = math.cos(math.radians(LAT))

    best_i = None
    best_d2 = float("inf")
    best_lat = best_lon = None

    for i, (la, lo) in enumerate(zip(lats, lons)):
        la = float(la)
        lo = float(lo)
        if lo > 180:
            lo -= 360

        d2 = (la - LAT) ** 2 + ((lo - LON) * coslat) ** 2
        if d2 < best_d2:
            best_d2 = d2
            best_i = i
            best_lat = la
            best_lon = lo

    return best_i, best_lat, best_lon, values


def d2_rows(after_time):
    run = find_latest_d2_run()

    first_u = get_bytes(d2_url("u_10m", run, 0), compressed_bz2=True)
    idx, grid_lat, grid_lon, _ = nearest_regular_grid_index(first_u)

    rows = []

    for lead in range(0, 49):
        valid_local = (run + timedelta(hours=lead)).astimezone(WARSAW)

        # Klasyczny ICON-D2 pokazujemy dopiero po końcu RUC.
        if valid_local <= after_time:
            continue

        u = float(read_values(get_bytes(d2_url("u_10m", run, lead), True))[idx])
        v = float(read_values(get_bytes(d2_url("v_10m", run, lead), True))[idx])
        gust = float(read_values(get_bytes(d2_url("vmax_10m", run, lead), True))[idx])

        speed_kn = ms_to_kn(math.hypot(u, v))
        gust_kn = ms_to_kn(gust)
        deg, direction = wind_direction(u, v)

        rows.append({
            "time": valid_local,
            "speed": speed_kn,
            "gust": gust_kn,
            "degrees": deg,
            "direction": direction,
            "model": "ICON-D2",
        })

    return run, grid_lat, grid_lon, rows


def make_table(rows):
    day_names = {
        0: "pon.", 1: "wt.", 2: "śr.", 3: "czw.",
        4: "pt.", 5: "sob.", 6: "niedz.",
    }

    out = []
    for row in rows:
        t = row["time"]
        stamp = f"{day_names[t.weekday()]} {t:%d.%m %H:%M}"
        gust_text = "—" if row["gust"] <= 0.1 else f"{row['gust']:.1f} kn"

        out.append(
            f"""
            <tr style="background:{row_color(row['speed'])}">
              <td>{stamp}</td>
              <td><b>{row['speed']:.1f}</b> kn</td>
              <td>{gust_text}</td>
              <td>{row['direction']} {row['degrees']:.0f}°</td>
            </tr>
            """
        )
    return "".join(out)


def summary(rows):
    if not rows:
        return "Brak danych."

    strongest_wind = max(rows, key=lambda r: r["speed"])
    gust_rows = [r for r in rows if r["gust"] > 0.1]
    strongest_gust = max(gust_rows, key=lambda r: r["gust"]) if gust_rows else None

    gust_text = (
        f"{strongest_gust['gust']:.1f} kn o {strongest_gust['time']:%H:%M}"
        if strongest_gust else "brak danych"
    )

    return (
        f"<b>Najsilniejszy wiatr:</b> "
        f"{strongest_wind['speed']:.1f} kn o {strongest_wind['time']:%H:%M}<br>"
        f"<b>Najsilniejsze porywy:</b> {gust_text}"
    )


def main():
    generated = datetime.now(WARSAW)
    current_hour = generated.replace(minute=0, second=0, microsecond=0)

    ruc_run, ruc_lat, ruc_lon, ruc = ruc_rows()
    ruc = [r for r in ruc if r["time"] >= current_hour]

    if not ruc:
        raise RuntimeError("Brak przyszłych godzin RUC.")

    ruc_end = ruc[-1]["time"]

    d2_run, d2_lat, d2_lon, d2 = d2_rows(ruc_end)
    d2 = [r for r in d2 if r["time"] >= current_hour]

    ruc_run_local = ruc_run.astimezone(WARSAW)
    d2_run_local = d2_run.astimezone(WARSAW)

    html = f"""<!doctype html>
<html lang="pl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Jastarnia – ICON-D2</title>
<style>
body {{
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif;
  margin:0; background:#f3f6f9; color:#17202a;
}}
header {{
  background:#1769aa; color:white; padding:18px; text-align:center;
}}
header h1 {{ margin:0; font-size:26px; }}
header p {{ margin:6px 0 0; }}
main {{ max-width:900px; margin:auto; padding:14px; }}
.card {{
  background:white; border-radius:14px; padding:16px;
  margin-bottom:14px; box-shadow:0 2px 10px rgba(0,0,0,.10);
}}
.info {{ color:#5d6d7e; line-height:1.5; }}
.summary {{ font-size:17px; line-height:1.6; }}
.section-title {{ margin:4px 0 10px; }}
.badge {{
  display:inline-block; padding:4px 9px; border-radius:999px;
  font-size:12px; font-weight:700; background:#e8f1fb; color:#1769aa;
}}
table {{
  width:100%; border-collapse:collapse; font-variant-numeric:tabular-nums;
}}
th,td {{
  padding:11px 8px; border-bottom:1px solid #ddd; text-align:right;
}}
th {{ background:#1769aa; color:white; }}
th:first-child,td:first-child {{ text-align:left; }}
th:last-child,td:last-child {{ text-align:center; }}
footer {{
  text-align:center; padding:18px; color:#687078; font-size:13px;
}}
@media (max-width:600px) {{
  main {{ padding:8px; }}
  .card {{ padding:10px; }}
  th,td {{ padding:9px 5px; font-size:14px; }}
}}
</style>
</head>
<body>
<header>
  <h1>🌬️ Jastarnia – ICON-D2</h1>
  <p>Prognoza wiatru dla Zatoki Puckiej</p>
</header>

<main>

<div class="card info">
  <b>Punkt docelowy:</b> {LAT:.3f}°N, {LON:.3f}°E<br>
  <b>Aktualizacja strony:</b> {generated:%d.%m.%Y %H:%M}<br><br>

  <span class="badge">RUC</span>
  przebieg {ruc_run_local:%d.%m %H:%M},
  punkt siatki {ruc_lat:.4f}°N, {ruc_lon:.4f}°E<br>

  <span class="badge">ICON-D2</span>
  przebieg {d2_run_local:%d.%m %H:%M},
  punkt siatki {d2_lat:.4f}°N, {d2_lon:.4f}°E
</div>

<div class="card summary">
  <h2 class="section-title">Najbliższe godziny · ICON-D2 RUC</h2>
  {summary(ruc)}
</div>

<div class="card" style="overflow-x:auto">
  <h2 class="section-title">ICON-D2 RUC · do +14 h</h2>
  <table>
    <thead>
      <tr><th>Godzina</th><th>Wiatr</th><th>Porywy</th><th>Kierunek</th></tr>
    </thead>
    <tbody>{make_table(ruc)}</tbody>
  </table>
</div>

<div class="card summary">
  <h2 class="section-title">Dalsza prognoza · ICON-D2</h2>
  {summary(d2)}
</div>

<div class="card" style="overflow-x:auto">
  <h2 class="section-title">ICON-D2 · dalszy horyzont do +48 h</h2>
  <table>
    <thead>
      <tr><th>Godzina</th><th>Wiatr</th><th>Porywy</th><th>Kierunek</th></tr>
    </thead>
    <tbody>{make_table(d2)}</tbody>
  </table>
</div>

</main>

<footer>
Dane: Deutscher Wetterdienst (DWD) · ICON-D2 RUC + ICON-D2
</footer>
</body>
</html>
"""

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)

    print("Gotowe: zapisano index.html z RUC i ICON-D2.")


if __name__ == "__main__":
    main()
