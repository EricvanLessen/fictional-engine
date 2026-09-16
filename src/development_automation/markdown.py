from __future__ import annotations

import re

import yaml  # type: ignore[import-untyped]
from pydantic import ValidationError

from development_automation.errors import MalformedControlDocumentError
from development_automation.schemas.v1 import CorrespondenceDocument, RunEventDocument

FRONT_MATTER_PATTERN = re.compile(
    r"\A---\s*\n(?P<front_matter>.*?)\n---\s*\n(?P<body>.*)\Z",
    re.DOTALL,
)


def _redact_credentials(markdown: str) -> str:
    patterns = (
        r"gh[pousr]_[A-Za-z0-9_]{12,}",
        r"github_pat_[A-Za-z0-9_]{12,}",
        r"sk-[A-Za-z0-9_-]{12,}",
        r"(OPENAI_API_KEY|COPILOT_AGENT_TOKEN|TELEGRAM_API_HASH|TELEGRAM_SESSION_STRING)=[^\s]+",
    )
    redacted = markdown
    for pattern in patterns:
        redacted = re.sub(pattern, "[REDACTED_CREDENTIAL]", redacted)
    return redacted


def parse_control_document(text: str) -> CorrespondenceDocument | RunEventDocument:
    match = FRONT_MATTER_PATTERN.match(text)
    if match is None:
        raise MalformedControlDocumentError("control documents must start with YAML front matter")

    try:
        metadata = yaml.safe_load(match.group("front_matter"))
    except yaml.YAMLError as exc:
        raise MalformedControlDocumentError("invalid YAML front matter") from exc

    if not isinstance(metadata, dict):
        raise MalformedControlDocumentError("front matter must parse to a mapping")

    payload = {**metadata, "body": match.group("body")}
    schema_version = payload.get("schema_version")
    try:
        if schema_version == "control-message.v1":
            return CorrespondenceDocument.model_validate(payload)
        if schema_version == "control-run.v1":
            return RunEventDocument.model_validate(payload)
    except ValidationError as exc:
        raise MalformedControlDocumentError("control document failed schema validation") from exc

    raise MalformedControlDocumentError(f"unsupported schema_version: {schema_version!r}")


def render_control_document(document: CorrespondenceDocument | RunEventDocument) -> str:
    sanitized_document = document.model_copy(update={"body": _redact_credentials(document.body)})
    front_matter = yaml.safe_dump(
        sanitized_document.front_matter(),
        sort_keys=False,
        allow_unicode=False,
    ).strip()
    body = sanitized_document.body.rstrip()
    return f"---\n{front_matter}\n---\n\n{body}\n"