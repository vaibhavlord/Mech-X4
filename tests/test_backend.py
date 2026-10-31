"""API contract and regression tests, each with its own temporary SQLite DB."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.database import get_connection, initialize_database


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path, monkeypatch):
    """Keep all test databases and frontend fixtures outside the repository."""
    database_path = tmp_path / "nested" / "trustmesh.db"
    monkeypatch.setenv("TRUSTMESH_DB_PATH", str(database_path))
    frontend = tmp_path / "frontend"
    monkeypatch.setattr(main, "FRONTEND_DIR", frontend)
    monkeypatch.setattr(main.frontend_files, "directory", str(frontend))
    monkeypatch.setattr(main.frontend_files, "all_directories", [str(frontend)])
    monkeypatch.setattr(main.frontend_files, "config_checked", False)
    return database_path


@pytest.fixture
def client():
    """Run the application lifespan so the temporary database is initialized."""
    with TestClient(main.app) as test_client:
        yield test_client


@pytest.fixture
def seeded_client(client):
    assert client.post("/api/seed-demo").status_code == 200
    return client


def evaluate(client, project="project-a", contributor="new-dev"):
    """Request an evaluation and check the common response status."""
    response = client.get("/api/evaluate", params={"project": project, "contributor": contributor})
    assert response.status_code == 200
    return response.json()


def add_signal(project, contributor, kind, reason="Please review this contribution."):
    """Insert controlled trust evidence using the same configured database."""
    with get_connection() as connection:
        connection.execute(
            "INSERT INTO signals (project_id, contributor_id, signal_type, reason) "
            "VALUES (?, ?, ?, ?)",
            (project, contributor, kind, reason),
        )


def make_pr(pr_id, project, **changes):
    """Build a complete valid PR; tests vary the actual detection evidence."""
    pr = {
        "id": pr_id,
        "project": project,
        "author": f"dev-{pr_id}",
        "title": "Improve project documentation",
        "body": "Update documentation and fix minor formatting.",
        "created_at": "2026-10-31T22:00:00Z",
        "account_age_days": 2,
    }
    pr.update(changes)
    return pr


def demo_prs():
    """Return three identical PR texts across independent projects."""
    return [make_pr(101, "alpha"), make_pr(102, "beta"), make_pr(103, "gamma")]


def scan(client, prs):
    """Return the JSON response to a valid scan request."""
    response = client.post("/api/scan", json={"pull_requests": prs})
    assert response.status_code == 200, response.text
    return response.json()


def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_seed_is_idempotent(client, isolated_environment):
    expected = {"status": "ok", "message": "Demo data ready"}
    assert client.post("/api/seed-demo").json() == expected
    original = client.get("/api/network").json()
    assert client.post("/api/seed-demo").json() == expected
    assert client.get("/api/network").json() == original
    connection = sqlite3.connect(isolated_environment)
    try:
        assert connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 4
        assert connection.execute("SELECT COUNT(*) FROM trusts").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM signals").fetchone()[0] == 2
    finally:
        connection.close()


def test_network_response(seeded_client):
    response = seeded_client.get("/api/network")
    assert response.status_code == 200
    data = response.json()
    assert data["projects"] == [
        {"id": "project-a", "name": "Project A"},
        {"id": "project-b", "name": "Project B"},
        {"id": "project-c", "name": "Project C"},
        {"id": "project-x", "name": "Project X"},
    ]
    assert data["trusts"] == [
        {"from": "project-a", "to": "project-b"},
        {"from": "project-a", "to": "project-c"},
    ]
    assert len(data["signals"]) == 2
    for signal in data["signals"]:
        assert set(signal) == {"project_id", "contributor_id", "type", "reason"}
        assert signal["contributor_id"] == "new-dev"
        assert signal["reason"]
    assert [signal["type"] for signal in data["signals"]] == ["vouch", "denounce"]


def test_trusted_vouch_accepted(seeded_client):
    data = evaluate(seeded_client)
    assert data["decision"] == "vouched"
    assert data["trusted_vouches"] == [{
        "project": "project-b",
        "contributor": "new-dev",
        "reason": "Contributed a useful, reviewed fix.",
    }]
    assert data["trusted_denounces"] == []
    assert data["evidence"]


def test_untrusted_denounce_ignored(seeded_client):
    data = evaluate(seeded_client)
    assert data["decision"] == "vouched"
    assert data["ignored_denounces"][0]["project"] == "project-x"
    assert any("Ignored denounce from project-x" in item for item in data["evidence"])


def test_conflicting_trusted_signals_require_review(seeded_client):
    add_signal("project-c", "new-dev", "denounce")
    data = evaluate(seeded_client)
    assert data["decision"] == "review"
    assert len(data["trusted_vouches"]) == 1
    assert data["trusted_denounces"][0]["project"] == "project-c"


def test_trusted_denounce_alone_requires_review(seeded_client):
    add_signal("project-b", "another-dev", "denounce")
    data = evaluate(seeded_client, contributor="another-dev")
    assert data["decision"] == "review"
    assert data["trusted_vouches"] == []
    assert any("do not establish malicious intent" in item for item in data["evidence"])


@pytest.mark.parametrize("project,contributor", [
    ("project-a", "unseen-dev"),
    ("unseen-project", "new-dev"),
    ("project-b", "new-dev"),
    ("project-a' OR 1=1 --", "new-dev"),
    ("project-a", "new-dev' OR 1=1 --"),
])
def test_no_direct_trusted_signals_is_unknown(seeded_client, project, contributor):
    data = evaluate(seeded_client, project, contributor)
    assert data["decision"] == "unknown"
    assert data["trusted_vouches"] == data["trusted_denounces"] == []


def test_transitive_trust_does_not_count(seeded_client):
    with get_connection() as connection:
        connection.execute(
            "INSERT INTO trusts (source_project, target_project) VALUES (?, ?)",
            ("project-b", "project-x"),
        )
    add_signal("project-x", "chain-dev", "vouch")
    assert evaluate(seeded_client, contributor="chain-dev")["decision"] == "unknown"
    assert evaluate(seeded_client)["ignored_denounces"][0]["project"] == "project-x"


def test_configured_database_path_is_used_for_every_query(seeded_client, monkeypatch, tmp_path):
    other = tmp_path / "other" / "database.db"
    monkeypatch.setenv("TRUSTMESH_DB_PATH", str(other))
    initialize_database()
    assert seeded_client.get("/api/network").json() == {"projects": [], "trusts": [], "signals": []}
    assert evaluate(seeded_client)["decision"] == "unknown"
    assert seeded_client.post("/api/seed-demo").status_code == 200
    assert evaluate(seeded_client)["decision"] == "vouched"
    assert other.is_file()


def test_default_database_path(client, monkeypatch, tmp_path):
    monkeypatch.delenv("TRUSTMESH_DB_PATH")
    monkeypatch.chdir(tmp_path)
    initialize_database()
    assert (tmp_path / "data" / "trustmesh.db").is_file()
    assert client.post("/api/seed-demo").status_code == 200
    assert evaluate(client)["decision"] == "vouched"


def test_three_identical_prs_flagged(client):
    result = scan(client, demo_prs())
    assert result["total_prs"] == 3
    assert len(result["campaigns"]) == 1
    campaign = result["campaigns"][0]
    assert set(campaign) == {"id", "pr_ids", "project_count", "similarity_score", "evidence"}
    assert campaign["id"] == "campaign-1"
    assert campaign["pr_ids"] == [101, 102, 103]
    assert campaign["project_count"] == 3
    assert campaign["similarity_score"] == 1.0
    assert any("Account age is supporting context" in item for item in campaign["evidence"])


def test_different_prs_not_flagged(client):
    prs = [
        make_pr(1, "alpha", title="Fix database migration", body="Repair SQLite column indexing"),
        make_pr(2, "beta", title="Draw rocket animation", body="Canvas sprite spaceship rendering"),
        make_pr(3, "gamma", title="Translate Spanish tutorial", body="Localization language glossary"),
    ]
    assert scan(client, prs) == {"total_prs": 3, "campaigns": []}


def test_prs_outside_two_hours_not_flagged(client):
    prs = demo_prs()
    prs[1]["created_at"] = "2026-10-31T23:00:00Z"
    prs[2]["created_at"] = "2026-11-01T00:00:01Z"
    assert scan(client, prs)["campaigns"] == []


def test_rolling_window_includes_exact_two_hour_boundary(client):
    prs = demo_prs()
    prs[0]["created_at"] = "2026-10-31T23:17:00Z"
    prs[1]["created_at"] = "2026-11-01T00:30:00Z"
    prs[2]["created_at"] = "2026-11-01T01:17:00Z"
    assert scan(client, prs)["campaigns"][0]["pr_ids"] == [101, 102, 103]


def test_rolling_window_finds_later_subset(client):
    prs = [make_pr(100, "early", created_at="2026-10-31T18:00:00Z"), *demo_prs()]
    assert scan(client, prs)["campaigns"][0]["pr_ids"] == [101, 102, 103]


def test_timezone_offsets_represent_same_instant(client):
    prs = demo_prs()
    prs[1]["created_at"] = "2026-11-01T03:30:00+05:30"
    prs[2]["created_at"] = "2026-10-31T18:00:00-04:00"
    assert "PRs span 0 minutes" in scan(client, prs)["campaigns"][0]["evidence"][2]


def test_single_project_not_a_campaign(client):
    assert scan(client, [make_pr(n, "alpha") for n in range(1, 5)])["campaigns"] == []


def test_two_projects_are_enough(client):
    prs = [make_pr(1, "alpha"), make_pr(2, "alpha"), make_pr(3, "beta")]
    assert scan(client, prs)["campaigns"][0]["project_count"] == 2


def test_fewer_than_three_prs_not_a_campaign(client):
    assert scan(client, demo_prs()[:2])["campaigns"] == []
    assert scan(client, demo_prs()[:1])["campaigns"] == []


def test_old_accounts_can_match(client):
    prs = [make_pr(n, str(n), account_age_days=900) for n in range(1, 4)]
    assert len(scan(client, prs)["campaigns"]) == 1


def test_stopwords_case_and_punctuation_are_normalized(client):
    prs = demo_prs()
    prs[1]["title"] = "IMPROVE THE PROJECT DOCUMENTATION!!!"
    prs[1]["body"] = "Update the documentation; and fix minor formatting."
    assert scan(client, prs)["campaigns"][0]["similarity_score"] == 1.0


def test_stopwords_only_have_no_similarity_evidence(client):
    prs = [make_pr(n, str(n), title="The and or", body="a an the") for n in range(1, 4)]
    assert scan(client, prs)["campaigns"] == []


def test_jaccard_threshold_is_inclusive_and_score_is_measured(client):
    prs = [
        make_pr(1, "alpha", title="red green blue", body=""),
        make_pr(2, "beta", title="red green blue yellow", body=""),
        make_pr(3, "gamma", title="red green blue yellow", body=""),
    ]
    assert scan(client, prs)["campaigns"][0]["similarity_score"] == 0.75
    prs[0]["title"] = "red green"
    assert scan(client, prs)["campaigns"] == []


def test_similarity_chain_does_not_flag_unrelated_endpoints(client):
    prs = [
        make_pr(1, "alpha", title="red green blue", body=""),
        make_pr(2, "beta", title="red green blue yellow", body=""),
        make_pr(3, "gamma", title="green blue yellow", body=""),
    ]
    assert scan(client, prs)["campaigns"] == []


def test_strong_subset_survives_weak_bridge(client):
    prs = [
        make_pr(1, "alpha", title="red green blue", body=""),
        make_pr(2, "beta", title="red green blue yellow", body=""),
        make_pr(3, "gamma", title="green blue yellow", body=""),
        make_pr(4, "delta", title="red green blue", body=""),
    ]
    assert [item["pr_ids"] for item in scan(client, prs)["campaigns"]] == [[1, 2, 4]]


def test_campaigns_are_stable_and_subsets_are_not_repeated(client):
    prs = [*demo_prs(), make_pr(104, "delta"), make_pr(105, "epsilon")]
    result = scan(client, prs)
    assert result == scan(client, list(reversed(prs)))
    assert len(result["campaigns"]) == 1
    assert result["campaigns"][0]["pr_ids"] == [101, 102, 103, 104, 105]


def test_distinct_campaigns_are_reported_separately(client):
    prs = demo_prs() + [
        make_pr(n, str(n), title="Database schema migration", body="Repair SQL index rollback")
        for n in range(201, 204)
    ]
    campaigns = scan(client, prs)["campaigns"]
    assert [item["pr_ids"] for item in campaigns] == [[101, 102, 103], [201, 202, 203]]
    assert [item["id"] for item in campaigns] == ["campaign-1", "campaign-2"]


@pytest.mark.parametrize("payload", [
    {},
    {"pull_requests": []},
    {"pull_requests": [{}]},
    {"pull_requests": None},
    {"pull_requests": "invalid"},
    {"pull_requests": [make_pr(1, "alpha"), make_pr(1, "beta")]},
    {"pull_requests": demo_prs(), "unexpected": True},
])
def test_invalid_scan_payload_rejected(client, payload):
    assert client.post("/api/scan", json=payload).status_code == 422


@pytest.mark.parametrize("field", list(make_pr(1, "alpha")))
def test_each_missing_pr_field_rejected(client, field):
    pr = make_pr(1, "alpha")
    del pr[field]
    assert client.post("/api/scan", json={"pull_requests": [pr]}).status_code == 422


@pytest.mark.parametrize("changes", [
    {"created_at": "not-a-date"},
    {"created_at": "2026-02-30T22:00:00Z"},
    {"created_at": "2026-10-31"},
    {"created_at": "2026-10-31T22:00:00"},
    {"created_at": "0001-01-01T00:00:00+01:00"},
    {"created_at": 123456},
    {"account_age_days": -1},
    {"account_age_days": 1.5},
    {"account_age_days": True},
    {"id": True},
    {"id": 0},
    {"project": " "},
    {"author": ""},
    {"title": " "},
    {"body": None},
    {"unexpected": "field"},
])
def test_invalid_pr_values_rejected(client, changes):
    pr = make_pr(1, "alpha")
    pr.update(changes)
    assert client.post("/api/scan", json={"pull_requests": [pr]}).status_code == 422


def test_zero_account_age_is_valid(client):
    assert scan(client, [make_pr(1, "alpha", account_age_days=0)])["total_prs"] == 1


@pytest.mark.parametrize("params", [{}, {"project": "project-a"}, {"project": " ", "contributor": "x"}])
def test_invalid_evaluation_query_rejected(client, params):
    assert client.get("/api/evaluate", params=params).status_code == 422


@pytest.mark.parametrize("origin", ["http://localhost:5500", "http://127.0.0.1:5500"])
def test_cors_allows_frontend_origins(client, origin):
    response = client.options("/api/scan", headers={
        "Origin": origin,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
    })
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin


def test_cors_rejects_other_origins(client):
    response = client.get("/api/health", headers={"Origin": "https://untrusted.example"})
    assert "access-control-allow-origin" not in response.headers


def test_missing_frontend_is_honest_and_does_not_crash(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "frontend is not available yet" in response.text
    assert client.get("/style.css").status_code == 404
    assert client.get("/api/unknown").status_code == 404
    assert client.get("/docs").status_code == 200


def test_frontend_and_assets_can_appear_after_startup(client, tmp_path):
    assert client.get("/style.css").status_code == 404
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "index.html").write_text('<html><link rel="stylesheet" href="style.css">TrustMesh</html>')
    (frontend / "style.css").write_text("body { color: blue; }")
    (frontend / "assets").mkdir()
    (frontend / "assets" / "app.js").write_text("console.log('TrustMesh');")
    assert client.get("/").status_code == 200
    assert "TrustMesh" in client.get("/").text
    assert client.get("/style.css").text == "body { color: blue; }"
    assert client.get("/assets/app.js").status_code == 200
    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.get("/docs").status_code == 200


def test_static_files_cannot_escape_frontend(client, tmp_path):
    (tmp_path / "frontend").mkdir()
    (tmp_path / "private.txt").write_text("outside-frontend")
    response = client.get("/%2e%2e/private.txt")
    assert response.status_code == 404
    assert "outside-frontend" not in response.text
