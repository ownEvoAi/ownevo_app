"""OTel GenAI ingest middleware.

The kernel can accept OTLP-formatted GenAI traces from external
collectors (LangSmith via `langsmith-collector-proxy`, OpenLLMetry,
etc.) and decode them into the canonical `AgentEvent` stream the rest
of the loop consumes. The mapping is pinned in `MAPPING.md` next to
this file; the implementation here defers to that document.

Public surface (everything an outside caller needs):

  decode_otlp_payload(payload, *, max_body_bytes=...) -> DecodedBatch
      Parse one OTLP-JSON `ResourceSpans` batch and return the
      AgentEvents extracted from it, alongside a list of warnings for
      spans that were skipped or partially decoded.

  DecodedBatch
      Result envelope: `events: list[AgentEvent]` plus
      `warnings: list[DecodeWarning]`. The receiver responds 200 even
      when warnings are non-empty — warnings are observability, not
      failures.

  OtelDecodeError
      Raised on payloads that cannot be parsed at all (malformed JSON,
      missing `ResourceSpans`). See `OversizedPayloadError` (a subclass)
      for the body-size case — the HTTP layer maps that to 413 rather
      than 400.

The HTTP entry point is wired in `api/routes/otel_ingest.py`; that
module imports from here.
"""

from .auth import (
    MalformedTokenError,
    MissingTokenError,
    ReceiverTokenAuth,
    ReceiverTokenAuthError,
    RevokedTokenError,
    UnknownTokenError,
    hash_token,
    is_auth_optional,
    mint_token,
    verify_request_token,
    verify_token,
)
from .mapper import (
    DEFAULT_MAX_BODY_BYTES,
    DecodedBatch,
    DecodeWarning,
    OtelDecodeError,
    OversizedPayloadError,
    decode_otlp_payload,
)
from .persist import PersistResult, persist_decoded_batch
from .protobuf_decode import decode_otlp_protobuf

__all__ = [
    "DEFAULT_MAX_BODY_BYTES",
    "DecodeWarning",
    "DecodedBatch",
    "decode_otlp_protobuf",
    "MalformedTokenError",
    "MissingTokenError",
    "OtelDecodeError",
    "OversizedPayloadError",
    "PersistResult",
    "ReceiverTokenAuth",
    "ReceiverTokenAuthError",
    "RevokedTokenError",
    "UnknownTokenError",
    "decode_otlp_payload",
    "hash_token",
    "is_auth_optional",
    "mint_token",
    "persist_decoded_batch",
    "verify_request_token",
    "verify_token",
]
