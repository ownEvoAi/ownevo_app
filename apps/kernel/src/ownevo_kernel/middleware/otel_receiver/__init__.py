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

  decode_otlp_protobuf(raw, *, max_body_bytes=...) -> DecodedBatch
      Parse one OTLP-protobuf `ResourceSpans` batch. The protobuf body
      is normalised to the same dict shape the JSON path uses, then
      handed to the shared mapper.

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

  ReceiverTokenAuth / ReceiverTokenAuthError
      Auth result type and base exception for the bearer-token gate.
      Use `verify_request_token(conn, authorization_header)` to verify
      an incoming OTLP request.

The HTTP entry point is wired in `api/routes/otel_ingest.py`; that
module imports from here.
"""

from .auth import ReceiverTokenAuth, ReceiverTokenAuthError, verify_request_token
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
    "OtelDecodeError",
    "OversizedPayloadError",
    "PersistResult",
    "ReceiverTokenAuth",
    "ReceiverTokenAuthError",
    "decode_otlp_payload",
    "decode_otlp_protobuf",
    "persist_decoded_batch",
    "verify_request_token",
]
