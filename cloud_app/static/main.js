let map = L.map('map').setView([23.25, 77.41], 6);

L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png").addTo(map);

let sessionId = null;
let ws = null;
let aircraftMarkers = {};

async function init() {
  const res = await fetch("/api/session");
  const data = await res.json();
  sessionId = data.session_id;

  connectWS();
  setInterval(fetchAircraft, 3000);
}

function connectWS() {
  if (ws && ws.readyState === WebSocket.OPEN) return;

  const wsProtocol = location.protocol === "https:" ? "wss:" : "ws:";
  ws = new WebSocket(`${wsProtocol}//${location.host}/ws/${sessionId}`);

  ws.onmessage = (event) => {
    const data = JSON.parse(event.data);

    renderAlerts(data.alerts);
  };

  ws.onclose = () => {
    setTimeout(connectWS, 2000);
  };
}

function renderAlerts(alerts) {
  const box = document.getElementById("alerts");
  box.innerHTML = alerts.map(a => `<div>${a.message}</div>`).join("");
}

async function fetchAircraft() {
  try {
    const center = map.getCenter();

    const res = await fetch(
      `https://api.airplanes.live/v2/point/${center.lat}/${center.lng}/200`
    );

    const data = await res.json();

    if (!data.ac) return;

    // Clear old markers
    Object.values(aircraftMarkers).forEach(m => map.removeLayer(m));
    aircraftMarkers = {};

    data.ac.forEach(ac => {
      if (!ac.lat || !ac.lon) return;

      const id = ac.hex || Math.random();

      const marker = L.circleMarker([ac.lat, ac.lon], {
        radius: 4,
        color: "#00d4ff"
      }).addTo(map);

      marker.on("click", () => selectAircraft(ac));

      aircraftMarkers[id] = marker;
    });

  } catch (e) {
    console.log("Aircraft fetch error");
  }
}

async function selectAircraft(ac) {
  await fetch(`/api/live-state/${sessionId}`, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({
      latitude: ac.lat,
      longitude: ac.lon,
      altitude_ft: ac.alt_baro || 0,
      speed_kts: ac.gs || 0,
      heading_deg: ac.track || 0
    })
  });
}

init();