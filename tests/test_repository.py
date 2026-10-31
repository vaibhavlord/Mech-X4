from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_hackathon_declaration():
    content = (ROOT / "HACKATHON.md").read_text()
    assert "Problem 7" in content


def test_required_repository_files():
    assert (ROOT / "requirements.txt").exists()
    assert (ROOT / ".github/workflows/ci.yml").exists()
    assert (ROOT / "LICENSE").exists()
