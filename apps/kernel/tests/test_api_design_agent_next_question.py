"""HTTP tests for `POST /api/design-agent/next-question`.

DB-free, key-free — the endpoint reads only from the static prompt
registry. Mounted on a bare FastAPI instance so the kernel app's DB
lifespan does not have to be satisfied.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport
from ownevo_kernel.api.routes.design_agent import router
from ownevo_kernel.design_agent.prompts import (
    GENERIC_DISCOVERY_QUESTIONS,
    get_discovery_questions,
)

_RETAIL = "retail-demand-planning"
_CREDIT = "credit-risk-recalibration"
_CLINICAL = "clinical-trial-site-selection"
_DESC = "Forecast weekly demand at SKU-store level over the next four weeks."


@pytest.fixture
async def client() -> AsyncGenerator[httpx.AsyncClient, None]:
    app = FastAPI()
    app.include_router(router)
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://api.test"
    ) as c:
        yield c


async def test_empty_prior_answers_returns_first_question(
    client: httpx.AsyncClient,
) -> None:
    resp = await client.post(
        "/api/design-agent/next-question",
        json={
            "description": _DESC,
            "template_id": _RETAIL,
            "prior_answers": [],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["done"] is False
    assert body["answered_count"] == 0
    assert body["total_questions"] == len(get_discovery_questions(_RETAIL))

    nq = body["next_question"]
    assert nq is not None
    assert nq["question_index"] == 0
    # First retail question is the metric trade-off per the demo plan.
    assert nq["kind"] == "metric"
    assert "overstock" in nq["question"].lower()
    assert nq["rationale"]
    assert isinstance(nq["options"], list)


async def test_walk_to_done_returns_done_true(client: httpx.AsyncClient) -> None:
    """Answer every question in order; the final call returns done=true."""
    questions = get_discovery_questions(_CREDIT)
    prior: list[dict] = []
    for expected_idx in range(len(questions)):
        resp = await client.post(
            "/api/design-agent/next-question",
            json={
                "description": _DESC,
                "template_id": _CREDIT,
                "prior_answers": prior,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["done"] is False, f"unexpectedly done at idx={expected_idx}"
        assert body["next_question"]["question_index"] == expected_idx
        prior.append(
            {"question_index": expected_idx, "answer": "Point-in-time (PIT)"},
        )

    # All questions answered — next call returns done=true.
    resp = await client.post(
        "/api/design-agent/next-question",
        json={
            "description": _DESC,
            "template_id": _CREDIT,
            "prior_answers": prior,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["done"] is True
    assert "next_question" not in body  # excluded by response_model_exclude_none=True
    assert body["answered_count"] == len(questions)


async def test_skipped_answer_still_advances_conversation(
    client: httpx.AsyncClient,
) -> None:
    """`answer=null` means the operator declined to answer.

    The design agent should not re-ask the skipped question; it advances
    to the next one. `answered_count` includes skipped entries (the
    interview has dealt with that question, regardless of the response).
    """
    resp = await client.post(
        "/api/design-agent/next-question",
        json={
            "description": _DESC,
            "template_id": _CLINICAL,
            "prior_answers": [{"question_index": 0, "answer": None}],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["done"] is False
    assert body["answered_count"] == 1
    assert body["next_question"]["question_index"] == 1


async def test_unknown_template_falls_back_to_generic(
    client: httpx.AsyncClient,
) -> None:
    resp = await client.post(
        "/api/design-agent/next-question",
        json={
            "description": _DESC,
            "template_id": "not-a-real-template",
            "prior_answers": [],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_questions"] == len(GENERIC_DISCOVERY_QUESTIONS)
    assert body["next_question"]["kind"] == GENERIC_DISCOVERY_QUESTIONS[0].kind
    assert body["next_question"]["question"] == GENERIC_DISCOVERY_QUESTIONS[0].question


async def test_null_template_id_uses_generic(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/api/design-agent/next-question",
        json={
            "description": "Free-form description that did not start from a template.",
            "template_id": None,
            "prior_answers": [],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_questions"] == len(GENERIC_DISCOVERY_QUESTIONS)


async def test_out_of_range_question_index_returns_422(
    client: httpx.AsyncClient,
) -> None:
    total = len(get_discovery_questions(_RETAIL))
    resp = await client.post(
        "/api/design-agent/next-question",
        json={
            "description": _DESC,
            "template_id": _RETAIL,
            "prior_answers": [{"question_index": total + 5, "answer": "x"}],
        },
    )
    assert resp.status_code == 400
    assert "out of range" in resp.json()["detail"]


async def test_empty_description_rejected(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/api/design-agent/next-question",
        json={
            "description": "",
            "template_id": _RETAIL,
            "prior_answers": [],
        },
    )
    assert resp.status_code == 422


async def test_negative_question_index_rejected(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/api/design-agent/next-question",
        json={
            "description": _DESC,
            "template_id": _RETAIL,
            "prior_answers": [{"question_index": -1, "answer": "x"}],
        },
    )
    assert resp.status_code == 422


async def test_extra_fields_rejected(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/api/design-agent/next-question",
        json={
            "description": _DESC,
            "template_id": _RETAIL,
            "prior_answers": [],
            "rogue_field": "nope",
        },
    )
    assert resp.status_code == 422


async def test_answered_count_does_not_overflow_total(
    client: httpx.AsyncClient,
) -> None:
    """Duplicate indices in prior_answers should not inflate answered_count."""
    resp = await client.post(
        "/api/design-agent/next-question",
        json={
            "description": _DESC,
            "template_id": _RETAIL,
            "prior_answers": [
                {"question_index": 0, "answer": "Avoid overstock"},
                {"question_index": 0, "answer": "Avoid overstock again"},
            ],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    # The set-based dedup means we report 1 answered, not 2.
    assert body["answered_count"] == 1
    assert body["next_question"]["question_index"] == 1
<<<<<<< HEAD


async def test_too_many_prior_answers_rejected(client: httpx.AsyncClient) -> None:
    """prior_answers list exceeding _MAX_PRIOR_ANSWERS=32 should return 422."""
    questions = get_discovery_questions(_RETAIL)
    prior = [{"question_index": i % len(questions), "answer": "x"} for i in range(33)]
    resp = await client.post(
        "/api/design-agent/next-question",
        json={"description": _DESC, "template_id": _RETAIL, "prior_answers": prior},
    )
    assert resp.status_code == 422


async def test_description_at_max_length_accepted(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/api/design-agent/next-question",
        json={"description": "x" * 4096, "template_id": None, "prior_answers": []},
    )
    assert resp.status_code == 200


async def test_description_over_max_length_rejected(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/api/design-agent/next-question",
        json={"description": "x" * 4097, "template_id": None, "prior_answers": []},
    )
    assert resp.status_code == 422


async def test_oversized_answer_rejected(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/api/design-agent/next-question",
        json={
            "description": _DESC,
            "template_id": _RETAIL,
            "prior_answers": [{"question_index": 0, "answer": "a" * 2049}],
        },
    )
    assert resp.status_code == 422


@pytest.mark.parametrize("template_id", [None, _RETAIL, "not-real"])
async def test_out_of_range_index_returns_400_for_any_template(
    client: httpx.AsyncClient, template_id: str | None
) -> None:
    total = len(get_discovery_questions(template_id))
    resp = await client.post(
        "/api/design-agent/next-question",
        json={
            "description": _DESC,
            "template_id": template_id,
            "prior_answers": [{"question_index": total, "answer": "x"}],
        },
    )
    assert resp.status_code == 400


async def test_out_of_order_prior_answers_fills_gap_at_lowest_index(
    client: httpx.AsyncClient,
) -> None:
    """Answering a later question first must not skip unanswered earlier ones."""
    resp = await client.post(
        "/api/design-agent/next-question",
        json={
            "description": _DESC,
            "template_id": _RETAIL,
            "prior_answers": [{"question_index": 1, "answer": "Slow-sell risk"}],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["next_question"]["question_index"] == 0
