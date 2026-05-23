"""Per-request token accountant for demo-quota accounting.

The NL-gen and design-agent helpers consume the ``Message`` returned by
``client.messages.create`` internally — they extract the tool-use block
and discard the response. That makes it awkward to add quota accounting
at the helper level without threading a counter through every call
signature.

Instead, we monkey-patch the bound ``messages.create`` method on the
``AsyncAnthropic`` instance for the lifetime of one request. The wrapper
records ``usage.input_tokens + usage.output_tokens`` on every call.
Because each request builds its own client via
:func:`build_async_anthropic`, the patch never escapes the request
boundary.

Anthropic bills each call's ``input_tokens`` separately, including
re-sent context on validation-retry rounds, so summing across calls
matches the actual bill.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class TokenAccountant:
    input_tokens: int = 0
    output_tokens: int = 0

    def record_from_message(self, msg: Any) -> None:
        usage = getattr(msg, "usage", None)
        if usage is None:
            return
        self.input_tokens += int(getattr(usage, "input_tokens", 0) or 0)
        self.output_tokens += int(getattr(usage, "output_tokens", 0) or 0)


def wrap_client_for_accounting(client: Any, accountant: TokenAccountant) -> None:
    """Replace ``client.messages.create`` with a usage-recording proxy.

    The wrapper preserves the original signature and return value. It
    only intercepts the response object to add usage to the accountant.
    Raises propagate untouched.
    """
    original = client.messages.create

    async def tracked(*args: Any, **kwargs: Any) -> Any:
        msg = await original(*args, **kwargs)
        accountant.record_from_message(msg)
        return msg

    client.messages.create = tracked  # type: ignore[method-assign]
