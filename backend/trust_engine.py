"""Evaluate only signals from projects explicitly trusted by the requester."""

from .database import get_connection


def evaluate_contributor(project: str, contributor: str):
    """A denounce requests human review; it never establishes malicious intent."""
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT s.project_id, s.contributor_id, s.signal_type, s.reason, "
            "t.target_project AS trusted_project "
            "FROM signals AS s LEFT JOIN trusts AS t "
            "ON t.source_project = ? AND t.target_project = s.project_id "
            "WHERE s.contributor_id = ? "
            "ORDER BY s.project_id, s.signal_type, s.id",
            (project, contributor),
        ).fetchall()

    vouches, denounces, ignored = [], [], []
    for row in rows:
        signal = {
            "project": row["project_id"],
            "contributor": row["contributor_id"],
            "reason": row["reason"],
        }
        if row["trusted_project"] is not None:
            if row["signal_type"] == "vouch":
                vouches.append(signal)
            elif row["signal_type"] == "denounce":
                denounces.append(signal)
        elif row["signal_type"] == "denounce":
            ignored.append(signal)

    decision = "review" if denounces else "vouched" if vouches else "unknown"
    evidence = [
        f"Only projects directly trusted by {project} affect this decision.",
        f"Found {len(vouches)} trusted vouch(es) and {len(denounces)} trusted denounce(s).",
    ]
    if denounces:
        evidence.append("Trusted denounces require human review; they do not establish malicious intent.")
    elif not vouches:
        evidence.append("No trusted signals were found for this contributor.")
    for signal in ignored:
        evidence.append(
            f"Ignored denounce from {signal['project']}: {project} does not directly trust that project."
        )
    return {
        "decision": decision,
        "trusted_vouches": vouches,
        "trusted_denounces": denounces,
        "ignored_denounces": ignored,
        "evidence": evidence,
    }
