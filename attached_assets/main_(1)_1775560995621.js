const map = L.map("map").setView([34.1526, 77.5771], 13);

L.tileLayer(
  "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
  {
    attribution:
      "&copy; Esri, Maxar, Earthstar Geographics, and the GIS User Community",
    maxZoom: 19,
  },
).addTo(map);

const terrainLayer = L.layerGroup().addTo(map);
const lzLayer = L.layerGroup().addTo(map);
const trafficLayer = L.layerGroup().addTo(map);

let selectedAircraftMarker = null;
let ws = null;
let reconnectTimer = null;
let latestPayload = null;

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.getRegistrations().then((regs) => {
    regs.forEach((reg) => reg.unregister());
  });
}

function sessionId() {
  return (
    document.getElementById("sessionId").value.trim() || "demo-classroom-1"
  );
}

function layerLabel(k) {
  return (
    {
      ground_safety: "Ground Safety",
      flight_state: "Flight State",
      weather_risk: "Weather Risk",
      disaster_alert: "Disaster Alert",
      human_exposure: "Human Exposure",
      road_activity: "Road Activity",
      air_traffic: "Air Traffic",
      aviation_hazard: "Aviation Hazard",
      runway_option: "Runway Option",
      road_candidate: "Road Candidate",
    }[k] || k
  );
}

function esc(v) {
  return String(v ?? "").replace(
    /[&<>"']/g,
    (s) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        s
      ],
  );
}

function trafficIcon(selected = false) {
  const color = selected ? "#80ffdb" : "#60a5fa";
  const size = selected ? 18 : 12;
  const border = selected ? "2px solid #062b2e" : "1px solid #0f172a";
  return L.divIcon({
    className: "",
    html: `<div style="
      width:${size}px;height:${size}px;border-radius:999px;
      background:${color};border:${border};
      box-shadow:0 0 0 3px rgba(255,255,255,0.18);
    "></div>`,
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
  });
}

function drawTraffic(feed, selectedAircraft) {
  trafficLayer.clearLayers();

  (feed || []).forEach((ac) => {
    if (ac.latitude == null || ac.longitude == null) return;
    const isSelected = selectedAircraft && ac.id === selectedAircraft.id;
    const marker = L.marker([ac.latitude, ac.longitude], {
      icon: trafficIcon(isSelected),
      zIndexOffset: isSelected ? 1000 : 0,
    }).addTo(trafficLayer);

    marker.bindTooltip(
      `<strong>${esc(ac.callsign || ac.icao24 || ac.id)}</strong><br>` +
        `Alt ${Math.round(ac.altitude_ft || 0)} ft<br>` +
        `Spd ${Math.round(ac.speed_kts || 0)} kt<br>` +
        `Hdg ${Math.round(ac.heading_deg || 0)}°<br>` +
        `Dist ${esc(ac.distance_km ?? "--")} km`,
    );
    marker.on("click", () => selectAircraft(ac.id));
  });

  if (
    selectedAircraft &&
    selectedAircraft.latitude != null &&
    selectedAircraft.longitude != null
  ) {
    if (!selectedAircraftMarker) {
      selectedAircraftMarker = L.marker(
        [selectedAircraft.latitude, selectedAircraft.longitude],
        {
          icon: L.divIcon({
            className: "",
            html: `<div style="
              width:24px;height:24px;border-radius:999px;
              background:#80ffdb;border:3px solid #083344;
              box-shadow:0 0 0 4px rgba(128,255,219,0.18);
            "></div>`,
            iconSize: [24, 24],
            iconAnchor: [12, 12],
          }),
          zIndexOffset: 2000,
        },
      ).addTo(map);
    } else {
      selectedAircraftMarker.setLatLng([
        selectedAircraft.latitude,
        selectedAircraft.longitude,
      ]);
    }
  } else if (selectedAircraftMarker) {
    map.removeLayer(selectedAircraftMarker);
    selectedAircraftMarker = null;
  }
}

function drawTrafficList(data) {
  const feed = data.traffic_feed || [];
  const selected = data.selected_aircraft || null;
  const selectedId = selected
    ? selected.id
    : (data.traffic_summary || {}).selected_aircraft_id;

  const q = (document.getElementById("trafficSearch")?.value || "")
    .trim()
    .toLowerCase();

  const filtered = feed.filter((ac) => {
    const text =
      `${ac.callsign || ""} ${ac.icao24 || ""} ${ac.id || ""}`.toLowerCase();
    return !q || text.includes(q);
  });

  const summary = document.getElementById("trafficSummary");
  const nearest = (data.traffic_summary || {}).nearest_aircraft_km;
  summary.innerHTML =
    `Feed aircraft: ${feed.length}<br>` +
    `Within 20 km: ${(data.traffic_summary || {}).within_20km ?? 0}<br>` +
    `Nearest: ${nearest == null ? "--" : `${nearest} km`}`;

  const list = document.getElementById("trafficList");
  list.innerHTML = "";

  if (!filtered.length) {
    list.innerHTML = `<div class="lz-item"><strong>No aircraft</strong><span>Try a different search or wait for feed update</span></div>`;
    return;
  }

  filtered.slice(0, 40).forEach((ac) => {
    const row = document.createElement("div");
    row.className = "lz-item";
    row.style.cursor = "pointer";
    if (ac.id === selectedId) {
      row.style.outline = "1px solid rgba(128,255,219,0.45)";
      row.style.background = "rgba(128,255,219,0.08)";
    }
    row.innerHTML =
      `<strong>${esc(ac.callsign || ac.icao24 || ac.id)}</strong>` +
      `<span>${Math.round(ac.altitude_ft || 0)} ft • ${Math.round(ac.speed_kts || 0)} kt • ${esc(ac.distance_km ?? "--")} km</span>`;
    row.addEventListener("click", () => selectAircraft(ac.id));
    list.appendChild(row);
  });
}

function draw(data) {
  latestPayload = data;

  terrainLayer.clearLayers();
  lzLayer.clearLayers();

  (data.cells || []).forEach((cell) => {
    L.polygon(cell.corners, {
      color: cell.color,
      fillColor: cell.color,
      fillOpacity: 0.25,
      opacity: 0.85,
      weight: 1,
    })
      .addTo(terrainLayer)
      .bindTooltip(
        `Risk ${cell.risk}<br>Ground ${cell.ground_safety}<br>Slope ${cell.slope_deg}°<br>Obstacle ${cell.obstacle}`,
        { className: "terrain-tip" },
      );
  });

  const z = document.getElementById("zones");
  z.innerHTML = "";
  (data.landing_zones || []).forEach((zone) => {
    L.polygon(zone.corners, {
      color: "#80ffdb",
      weight: 2,
      fillOpacity: 0.08,
      dashArray: "6,5",
    }).addTo(lzLayer);

    const div = document.createElement("div");
    div.className = "lz-item";
    div.innerHTML = `<strong>${esc(zone.label)}</strong><span>Risk ${zone.risk}</span>`;
    z.appendChild(div);
  });

  const selected = data.selected_aircraft || null;
  const t = selected || data.telemetry || {};
  const layers = data.layers || {};
  const ops = data.ops || {};
  const road = data.road || {};
  const decision = data.decision || {
    title: "No decision yet",
    reason: "Waiting for data.",
  };

  document.getElementById("source").textContent =
    t.source || data.telemetry?.source || "--";
  document.getElementById("status").textContent =
    t.connector_status || data.telemetry?.connector_status || "--";
  document.getElementById("altitude").textContent =
    `${Math.round(t.altitude_ft || 0)} ft`;
  document.getElementById("speed").textContent =
    `${Math.round(t.speed_kts || 0)} kt`;
  document.getElementById("heading").textContent =
    `${Math.round(t.heading_deg || 0)}°`;

  const layersBox = document.getElementById("layers");
  layersBox.innerHTML = "";
  Object.entries(layers).forEach(([k, v]) => {
    const div = document.createElement("div");
    div.className = "lz-item";
    div.innerHTML = `<strong>${esc(layerLabel(k))}</strong><span>${esc(v)}</span>`;
    layersBox.appendChild(div);
  });

  document.getElementById("decisionBox").innerHTML =
    `<strong>${esc(decision.title)}</strong><br>${esc(decision.reason)}`;

  const nearestAirport = ops.nearest_airport
    ? `${esc(ops.nearest_airport.ident)} ${esc(ops.nearest_airport.distance_km)} km, runway ${esc(ops.nearest_airport.longest_runway_ft)} ft`
    : "No nearby airport data";
  const warn =
    (ops.airspace_warnings || []).join(" | ") ||
    "No immediate aerodrome warning";
  const selectedTxt = selected
    ? `Selected aircraft: ${esc(selected.callsign || selected.icao24 || selected.id)}`
    : "Selected aircraft: none";

  document.getElementById("opsInfo").innerHTML =
    `${selectedTxt}<br>` +
    `Nearest airport: ${nearestAirport}<br>` +
    `Warnings: ${esc(warn)}<br>` +
    `Road proxy: major ${esc(road.major_roads ?? 0)}, secondary ${esc(road.secondary_roads ?? 0)}, local ${esc(road.local_roads ?? 0)}`;

  drawTraffic(data.traffic_feed || [], selected);
  drawTrafficList(data);

  if (t.latitude != null && t.longitude != null) {
    map.panTo([t.latitude, t.longitude], { animate: true, duration: 0.7 });
  }
}

async function fetchState() {
  const res = await fetch(`/api/session/${sessionId()}`);
  draw(await res.json());
}

async function startDemo() {
  await fetch(`/api/demo/${sessionId()}`, { method: "POST" });
}

async function selectAircraft(aircraftId) {
  await fetch(`/api/select-aircraft/${sessionId()}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ aircraft_id: aircraftId }),
  });
}

function connect() {
  if (
    ws &&
    (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)
  ) {
    return;
  }
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }

  const proto = location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${proto}://${location.host}/ws/${sessionId()}`);
  ws = socket;

  socket.onmessage = (e) => draw(JSON.parse(e.data));
  socket.onopen = () => socket.send("hello");
  socket.onclose = () => {
    if (ws === socket) {
      ws = null;
      reconnectTimer = setTimeout(connect, 1500);
    }
  };
}

async function searchLocation(query) {
  query = (query || "").trim();
  if (!query) return;

  const notice = document.getElementById("appNotice");
  if (notice) notice.textContent = `Searching "${query}"…`;

  try {
    const res = await fetch(`/api/search-location/${sessionId()}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query }),
    });
    if (!res.ok) {
      if (notice) notice.textContent = `Location not found: ${query}`;
      return;
    }
    const data = await res.json();
    map.setView([data.anchor_lat, data.anchor_lon], 13);
    if (notice) notice.textContent = `Moved to: ${data.display_name}`;
  } catch (e) {
    console.error("[Search]", e);
    if (notice) notice.textContent = "Search failed — check connection.";
  }
}

document.getElementById("connectBtn").addEventListener("click", async () => {
  await fetchState();
  connect();
});

document.getElementById("demoBtn").addEventListener("click", startDemo);

document.getElementById("locationBtn").addEventListener("click", () => {
  searchLocation(document.getElementById("locationSearch").value);
});
document.getElementById("locationSearch").addEventListener("keydown", (e) => {
  if (e.key === "Enter")
    searchLocation(document.getElementById("locationSearch").value);
});

document.getElementById("trafficSearch").addEventListener("input", () => {
  if (latestPayload) drawTrafficList(latestPayload);
});
document.getElementById("trafficSearchBtn").addEventListener("click", () => {
  if (latestPayload) drawTrafficList(latestPayload);
});

document.getElementById("clearTrackBtn").addEventListener("click", () => {
  selectAircraft(null);
  const notice = document.getElementById("appNotice");
  if (notice) notice.textContent = "Returned to area mode.";
});

fetchState().then(() => connect());
