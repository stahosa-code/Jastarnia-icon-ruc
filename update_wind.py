import requests
from datetime import datetime

LAT = 54.696
LON = 18.678

url = (
    "https://api.open-meteo.com/v1/forecast"
    f"?latitude={LAT}&longitude={LON}"
    "&hourly=wind_speed_10m,wind_gusts_10m,wind_direction_10m"
    "&wind_speed_unit=kn"
    "&timezone=Europe%2FWarsaw"
    "&forecast_days=7"
)

response = requests.get(url, timeout=30)
response.raise_for_status()
data = response.json()

times = data["hourly"]["time"]
speeds = data["hourly"]["wind_speed_10m"]
gusts = data["hourly"]["wind_gusts_10m"]
directions = data["hourly"]["wind_direction_10m"]

rows = []

for t, speed, gust, direction in zip(times, speeds, gusts, directions):
    dt = datetime.fromisoformat(t)

    rows.append(
        f"""
        <tr>
            <td>{dt.strftime('%d.%m')}</td>
            <td>{dt.strftime('%H:%M')}</td>
            <td>{speed:.1f}</td>
            <td>{gust:.1f}</td>
            <td>{direction:.0f}°</td>
        </tr>
        """
    )

html = f"""<!DOCTYPE html>
<html lang="pl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">

<title>Jastarnia – wiatr</title>

<style>
body {{
    font-family: Arial, sans-serif;
    margin: 0;
    background: #f4f6f8;
    color: #17202a;
}}

header {{
    background: #1769aa;
    color: white;
    padding: 18px;
    text-align: center;
}}

main {{
    max-width: 900px;
    margin: auto;
    padding: 15px;
}}

.card {{
    background: white;
    padding: 16px;
    margin-bottom: 15px;
    border-radius: 12px;
}}

table {{
    width: 100%;
    border-collapse: collapse;
}}

th, td {{
    padding: 8px;
    text-align: center;
    border-bottom: 1px solid #ddd;
}}

th {{
    background: #1769aa;
    color: white;
}}

footer {{
    text-align: center;
    padding: 20px;
    color: #666;
}}
</style>
</head>

<body>

<header>
<h1>🌬️ Jastarnia – wiatr</h1>
<p>Prognoza dla Zatoki Puckiej</p>
</header>

<main>

<div class="card">
<h2>Prognoza wiatru</h2>
<p>
Punkt: 54.696° N, 18.678° E<br>
Wiatr i porywy w węzłach.
</p>

<table>
<thead>
<tr>
<th>Data</th>
<th>Godzina</th>
<th>Wiatr (kt)</th>
<th>Porywy (kt)</th>
<th>Kierunek</th>
</tr>
</thead>

<tbody>
{''.join(rows)}
</tbody>
</table>

</div>

</main>

<footer>
Automatyczna prognoza dla Jastarni
</footer>

</body>
</html>
"""

with open("index.html", "w", encoding="utf-8") as f:
    f.write(html)

print("index.html został zaktualizowany.")
