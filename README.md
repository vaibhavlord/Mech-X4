# TrustMesh

TrustMesh is a local-first tool for open-source maintainers to review contributor signals across projects and surface potentially coordinated pull-request campaigns. It combines project-to-project trust relationships, contributor vouches and denounces, deterministic text-and-timing analysis, and an optional local language-model assessment. Results are evidence for human review, not automated accusations.

## Hack Day

Built for **Hacktoberfest Hack Day 2026, Problem 7: A trust network against AI slop**. The problem asks projects to share whom they vouch for, choose whom they trust, and catch spam campaigns early.

**Team MechX4:** Kushal, Vaibhav, and Aryan.

## Features

- **Trust Network:** View projects, directional trust relationships, and contributor signals. Load the repeatable demo network to explore the interface.
- **Contributor Review:** Evaluate a contributor from the perspective of a project. Only signals from projects it directly trusts affect the result; transitive trust is not followed. A trusted denounce requests review and does not establish malicious intent.
- **Campaign Scanner:** Submit a batch of pull-request records. The scanner compares normalized title and body text, then looks for groups of at least three similar PRs across at least two projects within a rolling two-hour window.
- **AI Assessment:** Ask local **Gemma 3 4B** (`gemma3:4b`) through **Ollama** to summarize measured campaign evidence. The model receives structured evidence rather than PR contents or contributor identities. Inference is real; there is no fallback assessment when Ollama is unavailable.

## Architecture

- **Backend:** Python and FastAPI provide the JSON API, input validation, and static frontend serving.
- **Storage:** SQLite stores projects, directed trust links, and contributor signals. The database is initialized at application startup. Its default path is `data/trustmesh.db`.
- **Frontend:** Static HTML, CSS, and vanilla JavaScript in `frontend/`; no separate frontend build step is required. The FastAPI app serves it at `/`.
- **Campaign analysis:** A deterministic Python scanner uses token-set Jaccard similarity over PR title and body text, plus timestamps. A similarity score is not a probability.
- **AI analysis:** The backend calls the local Ollama generate API at `http://127.0.0.1:11434`. The default model is `gemma3:4b`; generated output and its evidence references are validated before being returned.

## Prerequisites

- Python 3.10 or later.
- Ollama installed and running locally only if you want to use AI Assessment. The rest of the application and test suite do not require a downloaded model.

## Install

Run these commands from the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

For AI Assessment, start Ollama and download the default model:

```bash
ollama pull gemma3:4b
```

On macOS, Ollama can be started from its app. Alternatively, start its local server with `ollama serve` if it is not already running.

## Run

From the repository root, with the virtual environment activated:

```bash
python -m uvicorn backend.main:app --reload
```

Open the TrustMesh interface at <http://127.0.0.1:8000>. Interactive API documentation is available at <http://127.0.0.1:8000/docs>.

The first launch creates the SQLite database. Use **Load demo data** in the interface, or call `POST /api/seed-demo`, to populate the example trust network.

Optional configuration:

| Environment variable | Default | Purpose |
| --- | --- | --- |
| `TRUSTMESH_DB_PATH` | `data/trustmesh.db` | SQLite database file path |
| `TRUSTMESH_OLLAMA_MODEL` | `gemma3:4b` | Installed local Ollama model to use |
| `TRUSTMESH_OLLAMA_TIMEOUT_SECONDS` | `120` | Ollama request timeout in seconds |

## API

All API routes are served by the FastAPI application. JSON request bodies are validated; invalid input returns FastAPI validation errors.

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/api/health` | Returns `{"status":"ok"}` when the API is running. |
| `POST` | `/api/seed-demo` | Idempotently adds the demonstration projects, trust links, and signals. |
| `GET` | `/api/network` | Returns `projects`, directional `trusts`, and `signals` for the network. |
| `GET` | `/api/evaluate?project={project}&contributor={contributor}` | Evaluates one contributor from a reviewing project's direct trusted signals. Decisions are `vouched`, `review`, or `unknown`. |
| `POST` | `/api/scan` | Scans a `{"pull_requests":[...]}` batch and returns `total_prs` and detected `campaigns`. |
| `POST` | `/api/explain` | Sends structured campaign evidence to local Ollama and returns a validated assessment. |

Each pull request in a scan requires a positive integer `id`, non-empty `project`, `author`, and `title`, a `body` string, a timezone-aware ISO 8601 `created_at`, and a nonnegative integer `account_age_days`. IDs must be unique within a scan. The response includes campaign PR IDs, project count, minimum pairwise similarity score, and supporting evidence.

`/api/explain` accepts an `evidence` object containing `pr_ids`, `project_count`, `similarity_score`, `account_ages`, and `time_window_minutes`. The response risk level is `suspicious`, `needs_review`, or `inconclusive`; the endpoint returns `503` if local Ollama or the model is unavailable and `502` if Ollama's output fails validation. The maintainers make the final decision.

The root path `/` serves the frontend. The FastAPI-generated schema is at `/openapi.json` and the interactive docs are at `/docs`.

## Examples

[`examples/input.json`](examples/input.json) is a valid scan request with three nearly identical PRs submitted to three projects within a short time window. It includes the required PR IDs, project and author identifiers, title and body text, timezone-aware timestamps, and account ages.

[`examples/output.json`](examples/output.json) is the corresponding sample scan response: one campaign containing PRs `101`, `102`, and `103`, with a similarity score of `1.0`. Its evidence reports that the PRs span three projects and 97 minutes. The account ages are reported as context only; age does not determine detection. Similarity and timing are indicators for human review, not proof of spam or intent.

## Tests

Install the requirements as described above, then run from the repository root:

```bash
python -m pytest
```

The tests use isolated temporary SQLite databases. Ollama calls are mocked in tests, so running pytest does not require Ollama or downloading Gemma.

## Limitations

- This is a hackathon MVP. It accepts PR records supplied to the scanner; it does not fetch pull requests from GitHub or connect to a repository-hosting service.
- The trust graph is directional and evaluation uses direct trust only. The API currently provides demo seeding and network reads, not general endpoints for managing projects, trust links, or signals.
- Campaign detection is a heuristic over title/body token overlap and timestamps. Its threshold is 0.75; qualifying groups need at least three PRs across two or more projects in a rolling two-hour window. Pair comparisons are quadratic, and the grouping strategy is a conservative greedy approximation rather than exhaustive search. It is intended for small batches.
- Account age is supporting evidence, not a campaign detection rule. The scanner does not inspect code changes, reviews, or contributor identities to establish quality or intent.
- AI Assessment requires a local Ollama service and the configured model. It only explains the supplied structured evidence; it does not replace review of the actual pull requests. There is no model-free fallback verdict.
- The application has no authentication or authorization layer and is intended to run locally, not as a production service exposed to the public internet.

## License

TrustMesh is released under the [MIT License](LICENSE).