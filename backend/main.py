"""FastAPI entry point: run with uvicorn backend.main:app."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from .ai_service import explain_evidence
from .campaign_detector import detect_campaigns
from .database import get_network, initialize_database, seed_demo
from .schemas import EvaluationResponse, ExplainRequest, ExplainResponse
from .schemas import NetworkResponse, ScanRequest, ScanResponse
from .trust_engine import evaluate_contributor


FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create the configured database when the application starts."""
    initialize_database()
    yield


app = FastAPI(title="TrustMesh", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5500", "http://127.0.0.1:5500"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.get("/api/health")
def health():
    """Report that the backend is running."""
    return {"status": "ok"}


@app.post("/api/seed-demo")
def seed_demo_endpoint():
    """Populate the repeatable demonstration trust network."""
    seed_demo()
    return {"status": "ok", "message": "Demo data ready"}


@app.get("/api/network", response_model=NetworkResponse)
def network():
    """Return project nodes, trust edges, and contributor signals."""
    return get_network()


@app.get("/api/evaluate", response_model=EvaluationResponse)
def evaluate(
    project: str = Query(min_length=1, pattern=r"\S"),
    contributor: str = Query(min_length=1, pattern=r"\S"),
):
    """Evaluate a contributor using direct project trust only."""
    return evaluate_contributor(project, contributor)


@app.post("/api/scan", response_model=ScanResponse)
def scan(request: ScanRequest):
    """Find campaigns from actual PR text, timestamps, and project counts."""
    return detect_campaigns(request.pull_requests)


@app.post(
    "/api/explain",
    response_model=ExplainResponse,
    responses={
        502: {"description": "Ollama returned invalid or unsupported assessment data."},
        503: {"description": "Local Ollama or the requested model is unavailable."},
    },
)
async def explain(request: ExplainRequest):
    """Request a real local model assessment. Maintainers make the final decision."""
    return await explain_evidence(request.evidence)


@app.get("/", include_in_schema=False)
def index():
    """Serve the frontend when available, otherwise describe the current state."""
    index_path = FRONTEND_DIR / "index.html"
    if index_path.is_file():
        return FileResponse(index_path)
    return PlainTextResponse(
        "TrustMesh backend is running. The frontend is not available yet. "
        "Open /docs to try the API."
    )


class OptionalFrontendFiles(StaticFiles):
    """Allow the backend to start before the frontend directory is created."""

    async def check_config(self):
        """Defer missing-directory handling to the honest 404 response below."""
        if Path(self.directory).is_dir():
            await super().check_config()

    async def get_response(self, path, scope):
        if not Path(self.directory).is_dir():
            return PlainTextResponse("Frontend assets are not available yet.", status_code=404)
        return await super().get_response(path, scope)


# Keep this last so /api/* and /docs take priority. Relative assets such as
# /style.css, /js/app.js, /assets/*, and /static/* resolve inside frontend/.
frontend_files = OptionalFrontendFiles(directory=str(FRONTEND_DIR), check_dir=False)
app.mount("/", frontend_files, name="frontend")
