"""Provider uncertainty and attempt transitions using the isolated MySQL fixture."""

from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select, update

from app.core.database import SyncSessionLocal
from app.core.timeutil import utcnow
from app.models import AuditLog, GenerationAttempt, Quota, Work
from app.services.handoff_sync import mark_work_failed_and_refund
from tests.test_generation_outbox import prepare_work
from worker.attempts import ExecutionStopped, change_attempt, prepare_attempt
from worker.generation_reconciler import link_verified_task, review_due_submissions, shell_operator
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
            link_verified_task(work_id, attempt.request_id, "invalid", provider, reason="INC-42")
        assert prepare_attempt(work_id).status == "manual_review"
        with pytest.raises(ValueError):
            link_verified_task(
                work_id, attempt.request_id, "confirmed-task", provider, reason="https://example.com"
            )
        link_verified_task(work_id, attempt.request_id, "confirmed-task", provider, reason="INC-42")
    with SyncSessionLocal() as session:
        audit = session.scalar(select(AuditLog).where(AuditLog.action == "link_provider_task"))
        assert audit.admin_username == shell_operator()
        assert audit.remark == "INC-42" and audit.created_at is not None
        assert audit.before_val["status"] == "manual_review"
        assert audit.after_val == {
            "status": "accepted",
            "work_id": work_id,
            "attempt_id": attempt.id,
            "request_id": attempt.request_id,
            "provider_task_id": "confirmed-task",
            "correlation_result": "provider_id_verified_operator_attested",
            "provider_status": "running",
        }
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


@pytest.mark.parametrize("provider_status", ["failed", "cancelled"])
async def test_reconciler_rejects_terminated_provider_task(
    provider_status, admin_client, candidate_client_factory, monkeypatch
):
    from app.services import works

    client, draft = await prepare_work(admin_client, candidate_client_factory)
    monkeypatch.setattr(works.settings, "MOCK_WORKER", False)
    work_id = draft["id"]
    assert (await client.post(f"/api/works/{work_id}/generate")).status_code == 200
    attempt = prepare_attempt(work_id)
    uncertain = change_attempt(
        change_attempt(attempt, "submitting"), "submission_unknown", error="UPSTREAM_TIMEOUT"
    )

    def reply(request):
        task_id = request.url.path.rsplit("/", 1)[1]
        return httpx.Response(200, json={"task": {"id": task_id, "status": provider_status}})

    with httpx.Client(transport=httpx.MockTransport(reply)) as http:
        with pytest.raises(ValueError, match="already terminated"):
            link_verified_task(
                work_id,
                uncertain.request_id,
                "terminated-task",
                MiniMax(http, "test", "https://example"),
                reason="INC-99",
            )
    assert prepare_attempt(work_id).status == "submission_unknown"


@pytest.mark.parametrize(
    "provider_status,media_failed,allowed",
    [
        ("queued", False, True),
        ("succeeded", False, True),
        ("succeeded", True, True),
        ("queued", True, False),
        ("running", True, False),
        ("failed", True, False),
        ("cancelled", True, False),
    ],
)
async def test_manual_binding_respects_recovery_status(
    provider_status,
    media_failed,
    allowed,
    admin_client,
    candidate_client_factory,
    monkeypatch,
):
    from app.services import works

    client, draft = await prepare_work(admin_client, candidate_client_factory)
    monkeypatch.setattr(works.settings, "MOCK_WORKER", False)
    work_id = draft["id"]
    assert (await client.post(f"/api/works/{work_id}/generate")).status_code == 200
    task = "approved-task"
    attempt = change_attempt(prepare_attempt(work_id), "submitting")
    if media_failed:
        attempt = change_attempt(attempt, "accepted", task_id=task)
        attempt = change_attempt(attempt, "uploading")
        attempt = change_attempt(attempt, "manual_review", error="MEDIA_FAILED")
    else:
        attempt = change_attempt(attempt, "submission_unknown")

    def reply(_):
        return httpx.Response(200, json={"task": {"id": task, "status": provider_status}})

    with httpx.Client(transport=httpx.MockTransport(reply)) as http:
        provider = MiniMax(http, "test", "https://example.test")
        if allowed:
            link_verified_task(work_id, attempt.request_id, task, provider, reason="INC-99")
        else:
            with pytest.raises(ValueError):
                link_verified_task(work_id, attempt.request_id, task, provider, reason="INC-99")
    expected = ("uploading" if media_failed else "accepted") if allowed else attempt.status
    assert prepare_attempt(work_id).status == expected
    with SyncSessionLocal() as session:
        assert session.scalar(select(Quota.quota_used)) == 1
