from fastapi import FastAPI, WebSocket
import asyncio
import math
import numpy as np

from cloud_app.services.terrain_engine import get_terrain, compute_slope_grid, compute_roughness_grid
from cloud_app.services.weather_engine import get_weather
from cloud_app.services.crowd_engine import get_crowd_density
from cloud_app.services.population_engine import get_cached_population, sample_population
from cloud_app.services.decision_engine import compute_cells

app = FastAPI()


def distance(lat1, lon1, lat2, lon2):
    return math.sqrt((lat1 - lat2) ** 2 + (lon1 - lon2) ** 2) * 111


def detect_surface(elevation):
    return "water" if elevation < 5 else "land"


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()

    state = {"lat": 23.25, "lon": 77.41}

    while True:

        lat, lon = state["lat"], state["lon"]

        terrain = await get_terrain(lat, lon)
        weather = await get_weather(lat, lon)

        wind = weather.get("wind_speed", 5)

        elevation_grid = np.array(terrain["elevation_grid"])

        slope_grid = compute_slope_grid(elevation_grid)
        roughness_grid = compute_roughness_grid(elevation_grid)

        grid_size = len(slope_grid)
        center = grid_size // 2

        pop_grid, window = get_cached_population(lat, lon)
        base_crowd = await get_crowd_density(lat, lon)

        cells = []
        size = 0.01

        for i in range(-4, 5):
            for j in range(-4, 5):

                cell_lat = lat + i * size
                cell_lon = lon + j * size

                gi = center + i
                gj = center + j

                if 0 <= gi < grid_size and 0 <= gj < grid_size:
                    slope = float(slope_grid[gi][gj])
                    roughness = float(roughness_grid[gi][gj])
                    elevation = float(elevation_grid[gi][gj])
                else:
                    slope, roughness, elevation = 5, 10, 100

                crowd = sample_population(cell_lat, cell_lon, window, pop_grid)
                if crowd == 0:
                    crowd = base_crowd

                dist = distance(lat, lon, cell_lat, cell_lon)
                surface = detect_surface(elevation)

                cells.append({
                    "lat": cell_lat,
                    "lon": cell_lon,
                    "slope": slope,
                    "roughness": roughness,
                    "crowd": crowd,
                    "wind": wind,
                    "surface": surface,
                    "distance": dist,
                    "obstacle": 0
                })

        cells = compute_cells(cells, max_range=10)

        await ws.send_json({"cells": cells})

        await asyncio.sleep(1.5)