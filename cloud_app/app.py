from fastapi import FastAPI, WebSocket, Request, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import asyncio
import uuid

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


@app.get("/")
def home(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse("cloud_app/static/favicon.ico")


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
            terrain = await get_terrain(state["latitude"], state["longitude"])
            weather = await get_weather(state["latitude"], state["longitude"])

            risk = compute_risk(state)
            prob = compute_probability(risk)
            options = compute_options(prob)
            alerts = compute_alerts(risk, prob)
            guidance = compute_guidance(state, terrain)

            result = {
                "alerts": alerts,
                "guidance": guidance,
                "probabilistic": prob,
                "options": options,
                "terrain": terrain,
                "weather": weather,
            }

            await ws.send_json(result)
            await asyncio.sleep(1.5)

    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        sessions.pop(sid, None)
