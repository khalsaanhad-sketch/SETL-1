# SETL – Smart Emergency Terrain Landing (EFB System)

SETL is a real-time decision support system designed to assist pilots during emergency situations by providing terrain-aware landing guidance. It operates as a lightweight Electronic Flight Bag (EFB) application and integrates live aircraft data, terrain analysis, and environmental conditions to suggest safe landing options.

---

## 🚀 Key Features

* ✈️ Live aircraft tracking using ADS-B data (airplanes.live)
* 🌍 Real-time terrain awareness (DEM-based with fallback)
* 🌦️ Live weather integration (Open-Meteo)
* 🧠 Risk-based landing zone evaluation
* 📊 Probabilistic success estimation
* 🔁 Multi-option decision support (Primary / Secondary / Emergency)
* ⚡ WebSocket-based real-time updates
* 💻 Lightweight and deployable (Replit / VPS)

---

## 🧠 System Architecture

Frontend (Leaflet Map + Aircraft Feed)
↓
Backend (FastAPI + WebSocket)
↓
Terrain + Weather APIs
↓
Risk Engine + Guidance + Alerts
↓
Real-time Decision Output

---

## 📦 Project Structure

```
setl_app/
│
├── cloud_app/
│   ├── app.py
│   ├── services/
│   ├── templates/
│   └── static/
│
├── requirements.txt
├── run.sh
└── README.md
```

---

## ⚙️ Installation

### 1. Clone the repository

```
git clone <your-repo-url>
cd setl_app
```

---

### 2. Install dependencies

```
pip install -r requirements.txt
```

---

### 3. Run the application

```
uvicorn cloud_app.app:app --host 0.0.0.0 --port 5000
```

---

## 🌐 Running on Replit

1. Import your GitHub repository into Replit
2. Set the Run command:

```
uvicorn cloud_app.app:app --host 0.0.0.0 --port 5000
```

3. Click **Run**

---

## 🛰️ Data Sources

* Aircraft Data: airplanes.live (ADS-B)
* Weather Data: Open-Meteo
* Terrain Data: DEM (OpenTopography / fallback simulation)

---

## ⚠️ Disclaimer

This project is intended for research, educational, and prototype development purposes only.
It is **not certified for operational aviation use**.

---

## 🔮 Future Enhancements

* Real DEM raster parsing and caching
* Synthetic vision (cockpit-style terrain view)
* Advanced fuzzy + MCDM integration
* Predictive terrain collision modeling
* Voice alerts and adaptive UI modes

---

## 👨‍💻 Author

SETL Project
Aviation Safety • AI Decision Systems • EFB Intelligence

---

## ⭐ Contribution

If you find this project useful or interesting, feel free to fork, improve, and contribute.

---
