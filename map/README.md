# RHOAI System Map

Interactive visual map of the RHOAI CI/CD infrastructure. Standalone — works without IC installed.

## Quick Start

```bash
# 1. Start Neo4j
docker compose -f ../docker-compose.yml up neo4j -d

# 2. Seed the graph
pip install neo4j
python seed.py --no-cluster   # static data only (no IC needed)
python seed.py                # with live components from IC API

# 3. Start backend
pip install fastapi uvicorn neo4j httpx
SLK_NEO4J_PASSWORD=changeme uvicorn backend.main:app --port 8081

# 4. Start frontend (dev)
cd frontend && npm install && npm run dev
# Opens at http://localhost:3001
```

## Architecture

```
map/
├── backend/          # FastAPI + Neo4j (Python)
│   ├── main.py       # App entrypoint, CORS, static serving
│   ├── graph.py      # Neo4j queries (standalone, no IC imports)
│   └── routes.py     # REST API endpoints
├── frontend/         # React + React Flow (Vite)
│   └── src/
│       ├── App.jsx        # Main graph view
│       ├── DetailPanel.jsx # Node detail sidebar
│       ├── Toolbar.jsx     # Search + filter bar
│       ├── layout.js       # Dagre auto-layout
│       └── nodes/MapNode.jsx  # Custom node renderer
├── seed.py           # Graph seeding (static + optional IC API)
└── pyproject.toml    # Python dependencies
```

## API Endpoints

| Endpoint | Description |
|---|---|
| `GET /api/map/graph` | Full graph in React Flow format |
| `GET /api/map/nodes?type=X` | All nodes, optional type filter |
| `GET /api/map/edges` | All relationships |
| `GET /api/map/node/{id}` | Node detail with neighbors |
| `GET /api/map/search?q=X` | Full-text search |
| `GET /api/map/path/{from}/{to}` | Shortest path |
| `GET /api/map/stats` | Node/edge counts by type |
| `GET /api/map/gaps` | Infrastructure limitations |
| `GET /api/map/health` | Health check |

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `SLK_NEO4J_URI` | `bolt://localhost:7687` | Neo4j connection |
| `SLK_NEO4J_USER` | `neo4j` | Neo4j username |
| `SLK_NEO4J_PASSWORD` | (required) | Neo4j password |
| `IC_API_URL` | `http://localhost:8000` | IC API for live component data |
| `MAP_PORT` | `8081` | Backend server port |
