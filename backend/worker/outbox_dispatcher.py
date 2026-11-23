"""Independent MySQL-to-Celery generation publisher."""

import logging
import signal
from datetime import timedelta
from threading import Event
from time import monotonic, time

from kombu.exceptions import OperationalError
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.services.generation_outbox import claim_delivery, delayed_delivery_count, finish_delivery
from app.services.handoff_sync import QUEUE_KEY, sync_redis
from worker.tasks import enqueue_video_generation

logger = logging.getLogger(__name__)


def dispatch_once() -> bool:
    delivery = claim_delivery()
    if delivery is None:
        return False
    try:
        sync_redis.zadd(QUEUE_KEY, {str(delivery.work_id): time()}, nx=True)
    except RedisError:
        logger.warning("Queue position unavailable work_id=%s", delivery.work_id)

    try:
        enqueue_video_generation.apply_async(
            kwargs={"work_id": delivery.work_id, "from_outbox": True},
            task_id=f"i2v-generation-{delivery.work_id}",
            retry=False,
        )
    except (OperationalError, RedisError, OSError) as error:
        finish_delivery(delivery, error=type(error).__name__)
        logger.warning("Outbox publish deferred work_id=%s attempt=%s", delivery.work_id, delivery.attempt)
    else:
        finish_delivery(delivery)
    return True


def main() -> None:
    if get_settings().MOCK_WORKER:
        raise SystemExit("Outbox dispatcher requires MOCK_WORKER=false.")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    stop = Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())

    next_check = 0.0
    while not stop.is_set():
        try:
            if monotonic() >= next_check:
                delayed = delayed_delivery_count(timedelta(minutes=5))
                if delayed:
                    logger.warning(
                        "Generation outbox has %s unreceived works older than five minutes", delayed
                    )
                next_check = monotonic() + 60
            if not dispatch_once():
                stop.wait(1)
        except (SQLAlchemyError, RedisError, OperationalError):
            logger.error("Outbox dependency unavailable; dispatch will retry")
            stop.wait(5)


if __name__ == "__main__":
    main()
