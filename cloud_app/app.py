from fastapi import FastAPI, WebSocket, Request, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import asyncio
import httpx
import uuid
import random

from cloud_app.services.risk_engine import compute_risk
from cloud_app.services.guidance_engine import compute_guidance
from cloud_app.services.alert_engine import compute_alerts
from cloud_app.services.probability_engine import compute_probability
from cloud_app.services.options_engine import compute_options
from cloud_app.services.terrain_engine import get_terrain
from cloud_app.services.weather_engine import get_weather

app = FastAPI()

app.mount("/static", StaticFiles(directory="cloud_app/static"), name="static")
templates = Jinja2Templates(directory="cloud_app/templates")

sessions = {}


def ensure_session(sid):
    if sid not in sessions:
        sessions[sid] = {
            "latitude": 23.25,
            "longitude": 77.41,
            "altitude_ft": 5000,
            "speed_kts": 100,
            "heading_deg": 90,
        }
    return sessions[sid]


def generate_cells(state, terrain, prob):
    lat      = state["latitude"]
    lon      = state["longitude"]
    is_water = terrain.get("is_water", False)
    # slope_deg is derived by terrain_engine from DEM data
    base_slope = terrain.get("slope_deg", 0.0)
    elev       = terrain.get("elevation_m", 0.0)

    cells = []
    size  = 0.01

    for i in range(-4, 5):
        for j in range(-4, 5):
            cell_lat = lat + i * size
            cell_lon = lon + j * size

            if is_water:
                # Ocean / water body — ditching is always high-risk
                # Vary depth slightly for visual texture
                local_depth = abs(elev) + random.uniform(-150, 150)
                local_depth = max(0, local_depth)

                # Risk: water is always dangerous; deeper = slightly worse
                risk = round(min(1.0, max(0.75, 1.0 - prob * 0.25)), 2)

                # Blue depth palette
                if local_depth < 50:
                    color = "#06b6d4"   # shallow coastal — cyan
                elif local_depth < 500:
                    color = "#0284c7"   # continental shelf — sky blue
                elif local_depth < 2000:
                    color = "#1d4ed8"   # mid ocean — blue
                else:
                    color = "#1e3a5f"   # deep ocean — navy

                cells.append({
                    "corners": [
                        [cell_lat,        cell_lon],
                        [cell_lat + size, cell_lon],
                        [cell_lat + size, cell_lon + size],
                        [cell_lat,        cell_lon + size],
                    ],
                    "risk":     risk,
                    "color":    color,
                    "slope":    0.0,
                    "is_water": True,
                    "depth_m":  round(local_depth, 0),
                })
            else:
                # Land — slope_deg from terrain engine, normalised to [0, 1]
                slope = max(0.0, base_slope + random.uniform(-1.0, 1.0))

                # Slope contribution: 0° = 0.0, 30° = 1.0
                slope_risk = min(1.0, slope / 30.0)
                # Combine probability failure with slope hazard
                risk = round(min(1.0, (1.0 - prob) * 0.7 + slope_risk * 0.3), 2)

                if risk > 0.6:
                    color = "#ba2627"
                elif risk > 0.45:
                    color = "#ff9c00"
                elif risk > 0.25:
                    color = "#d8d62b"
                else:
                    color = "#2cb64f"

                cells.append({
                    "corners": [
                        [cell_lat,        cell_lon],
                        [cell_lat + size, cell_lon],
                        [cell_lat + size, cell_lon + size],
                        [cell_lat,        cell_lon + size],
                    ],
                    "risk":     risk,
                    "color":    color,
                    "slope":    round(slope, 2),
                    "is_water": False,
                })

    return cells


@app.get("/")
def home(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse("cloud_app/static/favicon.ico")


@app.get("/api/aircraft")
async def proxy_aircraft(lat: float, lon: float, radius: int = 200):
    sources = [
        f"https://api.airplanes.live/v2/point/{lat}/{lon}/{radius}",
        f"https://api.adsb.lol/v2/lat/{lat}/lon/{lon}/dist/{radius}",
    ]
    async with httpx.AsyncClient(timeout=6.0) as client:
        for url in sources:
            try:
                resp = await client.get(url, headers={"User-Agent": "SETL-EFB/1.0"})
                data = resp.json()
                if data.get("ac"):
                    return data
            except Exception:
                continue
    return {"ac": []}


@app.get("/api/session")
def create_session():
    sid = str(uuid.uuid4())
    ensure_session(sid)
    return {"session_id": sid}


@app.post("/api/live-state/{sid}")
async def update_state(sid: str, request: Request):
    data = await request.json()
    state = ensure_session(sid)
    state.update(data)
    return {"ok": True}


@app.websocket("/ws/{sid}")
async def ws_endpoint(ws: WebSocket, sid: str):
    await ws.accept()
    state = ensure_session(sid)

    try:
        while True:
            try:
                terrain = await get_terrain(state["latitude"], state["longitude"])
                weather = await get_weather(state["latitude"], state["longitude"])

                risk = compute_risk(state)
                prob = compute_probability(risk)
                options = compute_options(prob)
                alerts = compute_alerts(risk, prob)
                guidance = compute_guidance(state, terrain)

                cells = generate_cells(state, terrain, prob["success"])

                result = {
                    "alerts": alerts,
                    "guidance": guidance,
                    "probabilistic": prob,
                    "options": options,
                    "terrain": terrain,
                    "weather": weather,
                    "cells": cells,
                }

                await ws.send_json(result)
                await asyncio.sleep(1.5)

            except RuntimeError:
                break

    except WebSocketDisconnect:
        print("Client disconnected")

    except Exception as e:
        print("WebSocket error:", e)

    finally:
        try:
            await ws.close()
        except:
            pass
