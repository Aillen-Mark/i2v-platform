"""Provider uncertainty and attempt transitions using the isolated MySQL fixture."""

from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select, update

from app.core.database import SyncSessionLocal
from app.core.timeutil import utcnow
from app.models import GenerationAttempt, Quota, Work
from app.services.handoff_sync import mark_work_failed_and_refund
from tests.test_generation_outbox import prepare_work
from worker.attempts import ExecutionStopped, change_attempt, prepare_attempt
from worker.generation_reconciler import link_verified_task, review_due_submissions
from worker.minimax import GenerationError, MiniMax


@pytest.mark.parametrize(
    ("response", "uncertain", "retry"),
    [
        (httpx.ConnectTimeout("fixture"), False, "resubmit"),
        (httpx.ReadTimeout("fixture"), True, None),
        (httpx.RemoteProtocolError("dropped response"), True, None),
        (httpx.Response(200, json={}), True, None),
        (httpx.Response(500), True, None),
        (httpx.Response(422), False, None),
    ],
)
def test_provider_create_outcomes(response, uncertain, retry):
    def reply(_):
        if isinstance(response, Exception):
            raise response
        return response

    with httpx.Client(transport=httpx.MockTransport(reply)) as client:
        with pytest.raises(GenerationError) as failure:
            MiniMax(client, "test", "https://billing.example.test").create(
                "h3", "768P", 15, "image", "prompt", 1
            )
    assert failure.value.uncertain is uncertain
    assert failure.value.retry == retry


@pytest.mark.usefixtures("clean_state")
async def test_uncertain_request_waits_for_verified_reconciliation(
    admin_client, candidate_client_factory, monkeypatch
):
    from app.services import works

    client, draft = await prepare_work(admin_client, candidate_client_factory)
    monkeypatch.setattr(works.settings, "MOCK_WORKER", False)
    work_id = draft["id"]
    assert (await client.post(f"/api/works/{work_id}/generate")).status_code == 200
    attempt = prepare_attempt(work_id)
    assert prepare_attempt(work_id).id == attempt.id
    submitting = change_attempt(attempt, "submitting")
    with pytest.raises(ExecutionStopped):
        change_attempt(attempt, "submitting")
    uncertain = change_attempt(submitting, "submission_unknown", error="UPSTREAM_TIMEOUT")
    assert prepare_attempt(work_id).id == uncertain.id
    mark_work_failed_and_refund(work_id, "UPSTREAM_TIMEOUT", "test")
    with SyncSessionLocal.begin() as session:
        assert session.scalar(select(Quota.quota_used)) == 1
        assert session.get(Work, work_id).quota_refunded == 0
        session.execute(
            update(GenerationAttempt)
            .where(GenerationAttempt.id == attempt.id)
            .values(next_check_at=utcnow() - timedelta(seconds=1))
        )
    review_due_submissions()
    assert prepare_attempt(work_id).status == "manual_review"

    def reply(request):
        task_id = request.url.path.rsplit("/", 1)[1]
        if task_id == "invalid":
            return httpx.Response(503)
        return httpx.Response(200, json={"task": {"id": task_id, "status": "running"}})

    with httpx.Client(transport=httpx.MockTransport(reply)) as http:
        provider = MiniMax(http, "test", "https://billing.example.test")
        with pytest.raises(GenerationError):
            link_verified_task(work_id, attempt.request_id, "invalid", provider)
        assert prepare_attempt(work_id).status == "manual_review"
        link_verified_task(work_id, attempt.request_id, "confirmed-task", provider)
    accepted = prepare_attempt(work_id)
    assert accepted.id == attempt.id and accepted.status == "accepted"
    assert accepted.provider_task_id == "confirmed-task"
    failed = change_attempt(accepted, "failed", error="UPSTREAM_ERROR")
    assert failed.status == "failed"
    for _ in range(2):
        mark_work_failed_and_refund(work_id, "UPSTREAM_ERROR", "test")
    with SyncSessionLocal() as session:
        assert session.scalar(select(Quota.quota_used)) == 0
        assert session.get(Work, work_id).quota_refunded == 1
