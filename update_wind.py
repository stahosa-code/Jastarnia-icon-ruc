import bz2
import math
import os
import tempfile
from collections import defaultdict
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
USER_AGENT = "Jastarnia-ICON-D2/3.0"

# ICON-D2 RUC – najbliższe ~14 h
RUC_BASE = "https://opendata.dwd.de/weather/nwp/v1/m/icon-d2-ruc/p"
RUC_VARS = ("U_10M", "V_10M", "VMAX_10M")

# Klasyczny ICON-D2 – dalszy horyzont do +48 h
D2_BASE = "https://opendata.dwd.de/weather/nwp/icon-d2/grib"


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
    return bz2.decompress(data) if compressed_bz2 else data


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


def speed_class(speed_kn):
    if speed_kn < 8:
        return "calm"
    if speed_kn < 12:
        return "light"
    if speed_kn < 16:
        return "good"
    if speed_kn < 20:
        return "strong"
    if speed_kn < 24:
        return "very-strong"
    return "hard"


def speed_badge(speed_kn):
    if speed_kn < 8:
        label = "słabo"
    elif speed_kn < 12:
        label = "lekko"
    elif speed_kn < 16:
        label = "dobrze"
    elif speed_kn < 20:
        label = "mocno"
    elif speed_kn < 24:
        label = "bardzo mocno"
    else:
        label = "bardzo silnie"
    return label


def day_label(dt, today):
    d = dt.date()
    if d == today:
        return "Dzisiaj"
    if d == today + timedelta(days=1):
        return "Jutro"
    if d == today + timedelta(days=2):
        return "Pojutrze"

    weekdays = (
        "Poniedziałek", "Wtorek", "Środa", "Czwartek",
        "Piątek", "Sobota", "Niedziela"
    )
    return weekdays[dt.weekday()]


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

    if best_i is None:
        raise RuntimeError("Nie udało się znaleźć punktu siatki RUC.")

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
    valid_hours = (0, 3, 6, 9, 12, 15, 18, 21)

    candidates = []
    for back in range(0, 30):
        candidate = now - timedelta(hours=back)
        if candidate.hour in valid_hours:
            candidates.append(candidate)

    for run in candidates:
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

    if best_i is None:
        raise RuntimeError("Nie udało się znaleźć punktu siatki ICON-D2.")

    return best_i, best_lat, best_lon, values


def d2_rows(after_time):
    run = find_latest_d2_run()

    first_u = get_bytes(d2_url("u_10m", run, 0), compressed_bz2=True)
    idx, grid_lat, grid_lon, _ = nearest_regular_grid_index(first_u)

    rows = []

    for lead in range(0, 49):
        valid_local = (run + timedelta(hours=lead)).astimezone(WARSAW)

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


# ---------------- HTML ----------------

def make_rows(rows):
    out = []

    for row in rows:
        t = row["time"]
        cls = speed_class(row["speed"])
        gust_text = "—" if row["gust"] <= 0.1 else f"{row['gust']:.1f}"

        out.append(
            f"""
            <tr class="{cls}">
              <td class="time">{t:%H:%M}</td>
              <td class="wind">
                <span class="wind-number">{row['speed']:.1f}</span>
                <span class="unit">kn</span>
              </td>
              <td class="gust">
                <span class="gust-number">{gust_text}</span>
                <span class="unit">{'' if gust_text == '—' else 'kn'}</span>
              </td>
              <td class="dir">
                <span class="dir-main">{row['direction']}</span>
                <span class="deg">{row['degrees']:.0f}°</span>
              </td>
            </tr>
            """
        )

    return "".join(out)


def day_summary(rows):
    if not rows:
        return ""

    strongest = max(rows, key=lambda r: r["speed"])
    gusts = [r for r in rows if r["gust"] > 0.1]
    max_gust = max(gusts, key=lambda r: r["gust"]) if gusts else None

    avg = sum(r["speed"] for r in rows) / len(rows)

    gust_text = (
        f"{max_gust['gust']:.1f} kn o {max_gust['time']:%H:%M}"
        if max_gust else "brak"
    )

    return (
        f"""
        <div class="day-stats">
          <div><span>Średnio</span><b>{avg:.1f} kn</b></div>
          <div><span>Maks. wiatr</span><b>{strongest['speed']:.1f} kn · {strongest['time']:%H:%M}</b></div>
          <div><span>Maks. poryw</span><b>{gust_text}</b></div>
        </div>
        """
    )


def section_html(label, date_text, rows):
    return f"""
    <section class="day-card">
      <div class="day-header">
        <div>
          <h2>{label}</h2>
          <div class="date">{date_text}</div>
        </div>
      </div>

      {day_summary(rows)}

      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Godz.</th>
              <th>Wiatr</th>
              <th>Porywy</th>
              <th>Kierunek</th>
            </tr>
          </thead>
          <tbody>
            {make_rows(rows)}
          </tbody>
        </table>
      </div>
    </section>
    """


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

    all_rows = sorted(ruc + d2, key=lambda r: r["time"])

    grouped = defaultdict(list)
    for row in all_rows:
        grouped[row["time"].date()].append(row)

    today = generated.date()
    sections = []

    for day in sorted(grouped):
        rows = grouped[day]
        first = rows[0]["time"]
        label = day_label(first, today)
        date_text = first.strftime("%d.%m.%Y")
        sections.append(section_html(label, date_text, rows))

    strongest = max(all_rows, key=lambda r: r["speed"])
    gust_rows = [r for r in all_rows if r["gust"] > 0.1]
    strongest_gust = max(gust_rows, key=lambda r: r["gust"]) if gust_rows else None

    overall_gust = (
        f"{strongest_gust['gust']:.1f} kn · {strongest_gust['time']:%a %H:%M}"
        if strongest_gust else "brak"
    )

    ruc_run_local = ruc_run.astimezone(WARSAW)
    d2_run_local = d2_run.astimezone(WARSAW)

    html = f"""<!doctype html>
<html lang="pl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Jastarnia – wiatr</title>
<style>
:root {{
  --blue:#0f67ad;
  --blue-dark:#084d84;
  --bg:#edf2f6;
  --card:#ffffff;
  --text:#17202a;
  --muted:#667482;

  --c-calm:#eef2f5;
  --c-light:#e5f5ef;
  --c-good:#dff4cf;
  --c-strong:#fff0b8;
  --c-vstrong:#ffd79a;
  --c-hard:#ffb4ad;
}}

* {{ box-sizing:border-box; }}

body {{
  margin:0;
  background:var(--bg);
  color:var(--text);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif;
}}

header {{
  background:linear-gradient(135deg,var(--blue-dark),var(--blue));
  color:white;
  padding:20px 14px 18px;
  box-shadow:0 2px 10px rgba(0,0,0,.18);
}}

.header-inner {{
  max-width:920px;
  margin:auto;
}}

header h1 {{
  margin:0;
  font-size:28px;
  line-height:1.15;
}}

header p {{
  margin:6px 0 0;
  opacity:.92;
}}

main {{
  max-width:920px;
  margin:auto;
  padding:12px;
}}

.hero {{
  background:var(--card);
  border-radius:16px;
  padding:15px;
  margin-bottom:12px;
  box-shadow:0 2px 12px rgba(0,0,0,.08);
}}

.hero-grid {{
  display:grid;
  grid-template-columns:repeat(3,1fr);
  gap:8px;
}}

.hero-box {{
  border-radius:12px;
  padding:12px;
  background:#f6f8fa;
}}

.hero-box span {{
  display:block;
  color:var(--muted);
  font-size:12px;
  margin-bottom:5px;
}}

.hero-box b {{
  font-size:17px;
}}

.meta {{
  margin-top:12px;
  color:var(--muted);
  font-size:13px;
  line-height:1.55;
}}

.legend {{
  display:flex;
  flex-wrap:wrap;
  gap:7px;
  margin-top:12px;
}}

.legend span {{
  padding:5px 8px;
  border-radius:999px;
  font-size:12px;
  font-weight:700;
}}

.day-card {{
  background:var(--card);
  border-radius:16px;
  margin-bottom:14px;
  overflow:hidden;
  box-shadow:0 2px 12px rgba(0,0,0,.08);
}}

.day-header {{
  padding:15px 15px 9px;
  border-bottom:1px solid #e5e9ed;
}}

.day-header h2 {{
  margin:0;
  font-size:24px;
}}

.date {{
  color:var(--muted);
  margin-top:2px;
  font-size:14px;
}}

.day-stats {{
  display:grid;
  grid-template-columns:repeat(3,1fr);
  gap:1px;
  background:#e5e9ed;
  border-bottom:1px solid #e5e9ed;
}}

.day-stats div {{
  background:#f8fafb;
  padding:10px 12px;
}}

.day-stats span {{
  display:block;
  color:var(--muted);
  font-size:11px;
  margin-bottom:3px;
}}

.day-stats b {{
  font-size:14px;
}}

.table-wrap {{
  overflow-x:auto;
}}

table {{
  width:100%;
  border-collapse:collapse;
  min-width:560px;
  font-variant-numeric:tabular-nums;
}}

th {{
  background:var(--blue);
  color:white;
  padding:10px 9px;
  font-size:13px;
  text-align:right;
  position:sticky;
  top:0;
}}

th:first-child {{
  text-align:left;
}}

td {{
  padding:10px 9px;
  border-bottom:1px solid rgba(0,0,0,.075);
  text-align:right;
}}

td.time {{
  text-align:left;
  font-weight:700;
  width:90px;
}}

.wind-number {{
  font-size:18px;
  font-weight:800;
}}

.gust-number {{
  font-weight:700;
}}

.unit {{
  font-size:11px;
  color:#5d6872;
  margin-left:2px;
}}

.dir-main {{
  font-weight:800;
}}

.deg {{
  color:#5d6872;
  margin-left:4px;
}}

tr.calm {{ background:var(--c-calm); }}
tr.light {{ background:var(--c-light); }}
tr.good {{ background:var(--c-good); }}
tr.strong {{ background:var(--c-strong); }}
tr.very-strong {{ background:var(--c-vstrong); }}
tr.hard {{ background:var(--c-hard); }}

footer {{
  color:var(--muted);
  text-align:center;
  font-size:12px;
  padding:8px 12px 24px;
}}

@media (max-width:680px) {{
  header h1 {{ font-size:24px; }}
  main {{ padding:8px; }}

  .hero-grid {{
    grid-template-columns:1fr;
  }}

  .day-stats {{
    grid-template-columns:1fr;
  }}

  .day-header h2 {{
    font-size:22px;
  }}

  td {{
    padding:9px 7px;
  }}
}}
</style>
</head>

<body>

<header>
  <div class="header-inner">
    <h1>🌬️ Jastarnia – prognoza wiatru</h1>
    <p>ICON-D2 RUC + ICON-D2 · Zatoka Pucka</p>
  </div>
</header>

<main>

<div class="hero">
  <div class="hero-grid">
    <div class="hero-box">
      <span>Najsilniejszy wiatr</span>
      <b>{strongest['speed']:.1f} kn · {strongest['time']:%H:%M}</b>
    </div>
    <div class="hero-box">
      <span>Najsilniejszy poryw</span>
      <b>{overall_gust}</b>
    </div>
    <div class="hero-box">
      <span>Aktualizacja</span>
      <b>{generated:%H:%M}</b>
    </div>
  </div>

  <div class="legend">
    <span style="background:var(--c-calm)">0–8 kn</span>
    <span style="background:var(--c-light)">8–12 kn</span>
    <span style="background:var(--c-good)">12–16 kn</span>
    <span style="background:var(--c-strong)">16–20 kn</span>
    <span style="background:var(--c-vstrong)">20–24 kn</span>
    <span style="background:var(--c-hard)">24+ kn</span>
  </div>

  <div class="meta">
    <b>RUC:</b> przebieg {ruc_run_local:%d.%m %H:%M},
    punkt siatki {ruc_lat:.4f}°N, {ruc_lon:.4f}°E<br>
    <b>ICON-D2:</b> przebieg {d2_run_local:%d.%m %H:%M},
    punkt siatki {d2_lat:.4f}°N, {d2_lon:.4f}°E
  </div>
</div>

{''.join(sections)}

</main>

<footer>
  Dane: Deutscher Wetterdienst (DWD) · punkt docelowy {LAT:.3f}°N, {LON:.3f}°E
</footer>

</body>
</html>
"""

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)

    print("Gotowe: zapisano czytelną wersję index.html.")


if __name__ == "__main__":
    main()
