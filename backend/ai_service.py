"""Real local Ollama inference, with strict validation and no fallback verdicts.

Configuration: TRUSTMESH_OLLAMA_MODEL (default gemma3:4b) and
TRUSTMESH_OLLAMA_TIMEOUT_SECONDS (default 120). The HTTP endpoint stays local.
"""

import json
import math
import os
import re

import httpx
from fastapi import HTTPException
from pydantic import ValidationError

from .schemas import ExplainResponse, ExplanationEvidence, ModelAssessment


OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
MAINTAINER_NOTE = "These signals do not confirm spam; maintainers make the final decision."
SYSTEM_PROMPT = """You assist open-source maintainers by assessing supplied evidence.
Decide whether the available signals suggest possibly coordinated low-quality
contributions or are inconclusive. You have no PR code, reviews, or contributor
identities. Text similarity and timing may suggest coordination, but cannot
establish contribution quality, spam, or intent. Legitimate coordination is possible.

Return only a JSON object matching the supplied schema, without Markdown fences.
Use only the provided evidence. Never invent account identities, author counts,
code changes, review outcomes, motives, or additional facts. Account ages are
measured in DAYS, never minutes or hours; they are per-PR observations, not
necessarily distinct people. Only time_window_minutes is measured in minutes.
Young accounts alone are
not evidence of abuse. Similarity is text overlap, not a confidence percentage.
Do not describe suspicion as confirmed spam or claim a contributor is malicious.
The final decision remains with maintainers and requires review of the actual PRs.

Allowed risk_level values:
- suspicious: combined evidence suggests possible coordination worth inspecting.
- needs_review: mixed or ambiguous signals require more context from maintainers.
- inconclusive: insufficient evidence to suggest coordinated low-quality work.

Write a short, cautious summary grounded only in the supplied fields. State what
cannot be concluded. Cite specific fields by copying one or more distinct entries
from allowed_evidence_references EXACTLY into evidence_referenced. Do not invent
citations or change their values. Do not produce model or used_model fields.
"""


def get_settings():
    """Read settings per request so a bad AI setting cannot break other routes."""
    model = os.environ.get("TRUSTMESH_OLLAMA_MODEL", "gemma3:4b").strip()
    tag = model.rsplit(":", 1)[-1].lower()
    if (
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:-]*", model)
        or "://" in model
        or tag == "cloud"
        or tag.endswith("-cloud")
    ):
        raise HTTPException(500, "TRUSTMESH_OLLAMA_MODEL must name a local Ollama model, not a cloud model.")
    try:
        timeout = float(os.environ.get("TRUSTMESH_OLLAMA_TIMEOUT_SECONDS", "120"))
    except ValueError as error:
        raise HTTPException(500, "TRUSTMESH_OLLAMA_TIMEOUT_SECONDS must be a positive finite number.") from error
    if not math.isfinite(timeout) or timeout <= 0:
        raise HTTPException(500, "TRUSTMESH_OLLAMA_TIMEOUT_SECONDS must be a positive finite number.")
    return model, timeout


def evidence_references(evidence: ExplanationEvidence) -> list[str]:
    """Create exact field/value citations that the model may select."""
    return [
        f"{field}: {json.dumps(value, allow_nan=False)}"
        for field, value in evidence.model_dump().items()
    ]


def build_payload(evidence: ExplanationEvidence, model: str):
    """Constrain generation with a JSON schema and request-specific citations."""
    references = evidence_references(evidence)
    schema = ModelAssessment.model_json_schema()
    schema["properties"]["evidence_referenced"]["items"] = {"type": "string", "enum": references}
    schema["properties"]["evidence_referenced"]["uniqueItems"] = True
    prompt = json.dumps({
        "evidence": evidence.model_dump(),
        "evidence_units": {"account_ages": "days", "time_window_minutes": "minutes"},
        "allowed_evidence_references": references,
        "response_schema": schema,
    }, allow_nan=False)
    return {
        "model": model,
        "system": SYSTEM_PROMPT,
        "prompt": prompt,
        "format": schema,
        "stream": False,
        "options": {"temperature": 0, "num_predict": 1024},
    }


def reject_constant(value):
    """JSON does not permit NaN or Infinity."""
    raise ValueError(f"Invalid JSON constant: {value}")


def unique_object(pairs):
    """Reject ambiguous JSON objects with duplicate keys."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def load_json(value):
    """Parse JSON as data only, without extracting or repairing model output."""
    return json.loads(value, parse_constant=reject_constant, object_pairs_hook=unique_object)


def parse_assessment(response: httpx.Response, evidence: ExplanationEvidence, model: str):
    """Accept only completed output from the requested model with exact citations."""
    try:
        envelope = load_json(response.text)
        if not isinstance(envelope, dict):
            raise ValueError("Expected an Ollama JSON object")
        if envelope.get("error"):
            raise HTTPException(503, "Ollama reported an inference error. Check the local server and installed model.")
        if envelope.get("done") is not True or envelope.get("done_reason") not in (None, "stop"):
            raise ValueError("Ollama did not complete generation normally")
        reported_model = envelope.get("model")
        # Ollama may expand a tagless local model name to its :latest tag.
        allowed_models = {model} if ":" in model.rsplit("/", 1)[-1] else {model, model + ":latest"}
        if not isinstance(reported_model, str) or reported_model not in allowed_models:
            raise ValueError("Ollama returned a different or missing model name")
        generated_text = envelope.get("response")
        if not isinstance(generated_text, str):
            raise ValueError("Ollama response must contain generated JSON text")
        assessment = ModelAssessment.model_validate(load_json(generated_text))
        references = assessment.evidence_referenced
        if len(references) != len(set(references)):
            raise ValueError("Duplicate evidence references")
        if not set(references) <= set(evidence_references(evidence)):
            raise ValueError("Model cited evidence not present in the request")
    except (ValueError, TypeError, ValidationError, RecursionError) as error:
        raise HTTPException(
            502, "Ollama returned an invalid assessment: expected completed JSON with "
            "a valid risk level and exact references to the supplied evidence. No assessment was accepted."
        ) from error

    # This caveat accompanies a validated model summary; it is never a fallback.
    summary = assessment.summary
    if MAINTAINER_NOTE not in summary:
        summary += " " + MAINTAINER_NOTE
    return ExplainResponse(
        summary=summary,
        risk_level=assessment.risk_level,
        evidence_referenced=assessment.evidence_referenced,
        model=reported_model,
        used_model=True,
    )


async def explain_evidence(evidence: ExplanationEvidence) -> ExplainResponse:
    """Call the real loopback Ollama API; failures are explicit HTTP errors."""
    model, timeout = get_settings()
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=min(timeout, 5)),
            trust_env=False,
            follow_redirects=False,
        ) as client:
            response = await client.post(OLLAMA_URL, json=build_payload(evidence, model))
            response.raise_for_status()
    except httpx.TimeoutException as error:
        raise HTTPException(
            503, "Local Ollama inference timed out. Check the server or increase "
            "TRUSTMESH_OLLAMA_TIMEOUT_SECONDS; no assessment was generated."
        ) from error
    except httpx.HTTPStatusError as error:
        if error.response.status_code == 404:
            detail = f"Local Ollama model '{model}' is unavailable. Install it with 'ollama pull {model}'."
        else:
            detail = f"Local Ollama inference failed (HTTP {error.response.status_code}). Check the Ollama server."
        raise HTTPException(503, detail) from error
    except httpx.RequestError as error:
        raise HTTPException(
            503, "Cannot reach local Ollama at http://127.0.0.1:11434. "
            "Start Ollama and ensure the configured model is installed."
        ) from error
    return parse_assessment(response, evidence, model)
