const map = L.map("map").setView([23.25, 77.41], 13);

// 🔥 REAL SATELLITE
L.tileLayer(
  "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
  { maxZoom: 19 }
).addTo(map);

const terrainLayer = L.layerGroup().addTo(map);
const gridLayer = L.layerGroup().addTo(map);

let ws = null;

// ---------------- INIT ----------------
async function init() {
  const res = await fetch("/api/session");
  const data = await res.json();
  connect(data.session_id);
}

// ---------------- WS (FIXED FOR REPLIT) ----------------
function connect(sessionId) {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
    return;
  }

  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws/${sessionId}`);

  ws.onmessage = (e) => {
    const data = JSON.parse(e.data);
    drawTerrain(data);
    drawGrid(data);
    drawDecision(data);
  };

  ws.onclose = () => setTimeout(() => connect(sessionId), 2000);
}

// ---------------- TERRAIN ----------------
function drawTerrain(data) {
  terrainLayer.clearLayers();

  const terrain = data.terrain || {};
  const slope = terrain.slope || 0;
  const elev = terrain.elevation || 0;

  const center = map.getCenter();

  let color = "#2cb64f";
  if (slope > 1.2) color = "#ba2627";
  else if (slope > 0.6) color = "#ff9c00";

  L.circle(center, {
    radius: 2000,
    color,
    fillColor: color,
    fillOpacity: 0.15,
  }).addTo(terrainLayer);

  L.marker(center, {
    icon: L.divIcon({
      html: `<div style="
        color:white;
        background:rgba(0,0,0,0.7);
        padding:6px;
        border-radius:6px;
      ">
        Elev: ${Math.round(elev)} m<br>
        Slope: ${slope.toFixed(2)}
      </div>`
    })
  }).addTo(terrainLayer);
}

// ---------------- GRID ----------------
function drawGrid(data) {
  gridLayer.clearLayers();

  const cells = data.cells || [];

  cells.forEach(cell => {
    L.polygon(cell.corners, {
      color: cell.color,
      fillColor: cell.color,
      fillOpacity: 0.35,
      weight: 1.2
    })
    .addTo(gridLayer)
    .bindTooltip(
      `Risk: ${cell.risk}<br>Slope: ${cell.slope}`
    );
  });
}

// ---------------- DECISION ----------------
function drawDecision(data) {
  const box = document.getElementById("decisionBox");
  if (!box) return;

  const prob = data?.probabilistic?.success_probability || 0;
  const guidance = data.guidance || {};

  box.innerHTML = `
    <strong>${(prob * 100).toFixed(0)}% SAFE</strong><br>
    Glide Range: ${guidance.glide_range_km || "--"} km<br>
    Heading: ${guidance.heading || "--"}°
  `;
}

init();