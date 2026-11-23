"""MySQL outbox integration with the real local Redis/Celery broker."""

import asyncio
import socket
from datetime import timedelta

import pytest
from celery import Celery
from redis import Redis
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.core.database import AsyncSessionLocal, SyncSessionLocal
from app.core.timeutil import utcnow
from app.models import AuditLog, Candidate, GenerationOutbox, Quota, Work
from app.services import generation_outbox as outbox
from app.services import handoff_sync
from app.services import works as works_service
from tests.test_m3_quotas_windows_gateway import (
    _create_candidate,
    _create_question,
    _create_window,
    _login_candidate,
    _upload_work,
)
from worker import outbox_dispatcher
from worker.tasks import QUEUE, celery_app, enqueue_video_generation


async def prepare_work(admin_client, candidate_client_factory):
    user = await _create_candidate(admin_client, "outbox candidate", "OUTBOX01")
    question = await _create_question(admin_client, "OUTBOX", default_quota=2)
    now = utcnow()
    await _create_window(admin_client, "outbox test", now - timedelta(minutes=1), now + timedelta(minutes=10))
    client = await _login_candidate(candidate_client_factory, "OUTBOX01", user["password"])
    work = await _upload_work(client, question["id"], "private fixture prompt")
    return client, work


def advance_delivery(work_id):
    with SyncSessionLocal.begin() as session:
        session.execute(
            update(GenerationOutbox)
            .where(GenerationOutbox.work_id == work_id)
            .values(
                dispatch_after=utcnow() - timedelta(seconds=1), leased_until=utcnow() - timedelta(seconds=1)
            )
        )


def read_broker_message():
    with celery_app.connection_for_read() as connection:
        queue = connection.SimpleQueue(QUEUE)
        try:
            message = queue.get(block=True, timeout=5)
            result = (message.headers["id"], message.payload[1])
            message.ack()
            return result
        finally:
            queue.close()


async def test_submission_is_durable_when_broker_is_unavailable_then_replays(
    admin_client, candidate_client_factory, monkeypatch
):
    client, work = await prepare_work(admin_client, candidate_client_factory)
    monkeypatch.setattr(works_service.settings, "MOCK_WORKER", False)
    work_id = work["id"]

    with socket.socket() as unserved:
        unserved.bind(("127.0.0.1", 0))
        port = unserved.getsockname()[1]
        offline = Celery("unavailable", broker=f"redis://127.0.0.1:{port}/0")
        offline.conf.broker_transport_options = {"socket_connect_timeout": 0.2, "max_retries": 0}
        real_publish = enqueue_video_generation.apply_async

        def publish_to_unavailable_broker(**options):
            return offline.send_task("i2v.generate_video", queue=QUEUE, **options)

        monkeypatch.setattr(enqueue_video_generation, "apply_async", publish_to_unavailable_broker)
        submissions = await asyncio.gather(
            client.post(f"/api/works/{work_id}/generate"), client.post(f"/api/works/{work_id}/generate")
        )
        assert [response.status_code for response in submissions] == [200, 200]
        assert all(response.json()["task_status"] == "queued" for response in submissions)
        assert await asyncio.to_thread(outbox_dispatcher.dispatch_once)
        monkeypatch.setattr(enqueue_video_generation, "apply_async", real_publish)

    async with AsyncSessionLocal() as session:
        event = await session.get(GenerationOutbox, work_id)
        row = await session.get(Work, work_id)
        assert row.task_status == "queued"
        assert event.attempts == 1 and event.last_error and event.published_at is None
        assert await session.scalar(select(Quota.quota_used)) == 1
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.action == "debit_quota",
                    AuditLog.target_id == row.task_no,
                )
            )
            == 1
        )

    await asyncio.to_thread(advance_delivery, work_id)
    assert await asyncio.to_thread(outbox_dispatcher.dispatch_once)
    first = await asyncio.to_thread(read_broker_message)
    assert first == (f"i2v-generation-{work_id}", {"work_id": work_id, "from_outbox": True})

    # Broker accepted the message, but no worker receipt was persisted.
    await asyncio.to_thread(advance_delivery, work_id)
    assert await asyncio.to_thread(outbox_dispatcher.dispatch_once)
    assert await asyncio.to_thread(read_broker_message) == first
    receipts = await asyncio.gather(*(asyncio.to_thread(outbox.receive_delivery, work_id) for _ in range(4)))
    assert receipts.count(True) == 1
    await asyncio.to_thread(advance_delivery, work_id)
    assert not await asyncio.to_thread(outbox_dispatcher.dispatch_once)


async def test_expired_dispatcher_claim_is_replaced_and_stale_result_cannot_acknowledge(
    admin_client, candidate_client_factory, monkeypatch
):
    client, work = await prepare_work(admin_client, candidate_client_factory)
    monkeypatch.setattr(works_service.settings, "MOCK_WORKER", False)
    work_id = work["id"]
    assert (await client.post(f"/api/works/{work_id}/generate")).status_code == 200
    old = await asyncio.to_thread(outbox.claim_delivery)
    assert old is not None and old.work_id == work_id
    assert await asyncio.to_thread(outbox.claim_delivery) is None
    await asyncio.to_thread(advance_delivery, work_id)
    replacement = await asyncio.to_thread(outbox.claim_delivery)
    assert replacement is not None and replacement.token != old.token
    await asyncio.to_thread(outbox.finish_delivery, old)
    async with AsyncSessionLocal() as session:
        event = await session.get(GenerationOutbox, work_id)
        assert event.lease_token == replacement.token and event.published_at is None


async def test_outbox_insert_failure_rolls_back_work_and_debit(
    admin_client, candidate_client_factory, monkeypatch
):
    _, work = await prepare_work(admin_client, candidate_client_factory)
    monkeypatch.setattr(works_service.settings, "MOCK_WORKER", False)
    work_id = work["id"]
    async with AsyncSessionLocal.begin() as session:
        now = utcnow()
        session.add(GenerationOutbox(work_id=work_id, created_at=now, dispatch_after=now))

    async with AsyncSessionLocal() as session:
        candidate = await session.scalar(select(Candidate).where(Candidate.account == "OUTBOX01"))
        with pytest.raises(IntegrityError):
            await works_service.submit_work(session, candidate, work_id)
        await session.rollback()

    async with AsyncSessionLocal() as session:
        row = await session.get(Work, work_id)
        assert row.task_status == "draft" and row.submitted_at is None
        assert await session.scalar(select(Quota.quota_used)) == 0
        assert (
            await session.scalar(
                select(func.count()).select_from(AuditLog).where(AuditLog.action == "debit_quota")
            )
            == 0
        )


async def test_refund_finishes_once_when_redis_queue_cleanup_is_unavailable(
    admin_client, candidate_client_factory, monkeypatch
):
    client, work = await prepare_work(admin_client, candidate_client_factory)
    monkeypatch.setattr(works_service.settings, "MOCK_WORKER", False)
    work_id = work["id"]
    assert (await client.post(f"/api/works/{work_id}/generate")).status_code == 200

    with socket.socket() as unserved:
        unserved.bind(("127.0.0.1", 0))
        inaccessible = Redis(host="127.0.0.1", port=unserved.getsockname()[1], socket_connect_timeout=0.2)
        monkeypatch.setattr(handoff_sync, "sync_redis", inaccessible)
        try:
            for _ in range(2):
                await asyncio.to_thread(
                    handoff_sync.mark_work_failed_and_refund, work_id, "TEST_FAIL", "Test failure"
                )
        finally:
            inaccessible.close()

    async with AsyncSessionLocal() as session:
        row = await session.get(Work, work_id)
        assert row.task_status == "failed" and row.quota_refunded == 1
        assert await session.scalar(select(Quota.quota_used)) == 0
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.action == "refund_quota",
                    AuditLog.target_id == row.task_no,
                )
            )
            == 1
        )
