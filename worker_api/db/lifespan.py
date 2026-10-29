import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from worker_api.audio.services.audio_job_consumer import run_audio_sqs_consumer
from worker_api.notifications.services.chat_notification_consumer import (
    run_chat_notification_sqs_consumer,
)
from worker_api.notifications.services.event_notification_consumer import (
    run_event_notification_sqs_consumer,
)
from worker_api.notifications.services.group_post_notification_consumer import (
    run_group_post_notification_sqs_consumer,
)
from worker_api.notifications.services.join_request_notification_consumer import (
    run_join_request_notification_sqs_consumer,
)
from worker_api.notifications.services.prayer_notification_consumer import (
    run_prayer_notification_sqs_consumer,
)
from worker_api.prayer_translation.services.prayer_translation_consumer import (
    run_prayer_translation_sqs_consumer,
)
from worker_api.recommendations.consumer import (
    run_recommendation_events_sqs_consumer,
    run_recommendation_full_refresh_loop,
)


@asynccontextmanager
async def lifespan(api: FastAPI):
    stop_event = asyncio.Event()
    consumer_tasks = [
        asyncio.create_task(run_audio_sqs_consumer(stop_event)),
        asyncio.create_task(run_chat_notification_sqs_consumer(stop_event)),
        asyncio.create_task(run_prayer_notification_sqs_consumer(stop_event)),
        asyncio.create_task(run_join_request_notification_sqs_consumer(stop_event)),
        asyncio.create_task(run_group_post_notification_sqs_consumer(stop_event)),
        asyncio.create_task(run_event_notification_sqs_consumer(stop_event)),
        asyncio.create_task(run_prayer_translation_sqs_consumer(stop_event)),
        asyncio.create_task(run_recommendation_events_sqs_consumer(stop_event)),
        asyncio.create_task(run_recommendation_full_refresh_loop(stop_event)),
    ]

    yield

    stop_event.set()
    for consumer_task in consumer_tasks:
        try:
            await asyncio.wait_for(consumer_task, timeout=5)
        except asyncio.TimeoutError:
            consumer_task.cancel()
            try:
                await consumer_task
            except asyncio.CancelledError:
                pass
