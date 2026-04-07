from fastapi import FastAPI, WebSocket, Request, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import asyncio
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


# 🔥 Backend-driven grid (safe version)
def generate_cells(state, terrain, prob):
    lat = state["latitude"]
    lon = state["longitude"]

    cells = []
    size = 0.01

    base_slope = terrain.get("slope", 0)

    for i in range(-4, 5):
        for j in range(-4, 5):
            cell_lat = lat + i * size
            cell_lon = lon + j * size

            slope = base_slope + random.uniform(-0.2, 0.2)
            slope = max(0, slope)

            risk = min(1.0, slope + (1 - prob))

            if risk > 0.6:
                color = "#ba2627"
            elif risk > 0.3:
                color = "#ff9c00"
            else:
                color = "#2cb64f"

            cells.append(
                {
                    "corners": [
                        [cell_lat, cell_lon],
                        [cell_lat + size, cell_lon],
                        [cell_lat + size, cell_lon + size],
                        [cell_lat, cell_lon + size],
                    ],
                    "risk": round(risk, 2),
                    "color": color,
                    "slope": round(slope, 2),
                }
            )

    return cells


@app.get("/")
def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


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

                cells = generate_cells(state, terrain, prob["success_probability"])

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
