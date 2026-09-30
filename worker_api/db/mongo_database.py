import asyncio
import logging
from contextlib import asynccontextmanager

from beanie import init_beanie
from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorClient

from ..config import get
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

mongodb_client = None
mongodb = None


@asynccontextmanager
async def lifespan(api: FastAPI):
    global mongodb_client, mongodb
    # Initialize the MongoDB client and database
    mongodb_client = AsyncIOMotorClient(get("MONGO_CONNECTION_STRING"))
    mongodb = mongodb_client[get("MONGO_DATABASE_NAME")]
    api.mongodb = mongodb  # Attach the database instance to the FastAPI app

    # Initialize collections and indexes if necessary
    try:
        # Note: Add document models here as needed when transferring endpoints
        await init_beanie(database=mongodb, document_models=[])
        logging.info("Beanie initialized for worker API.")
        
    except Exception as e:
        logging.error(f"Error during collection initialization: {e}")
        raise

    stop_event = asyncio.Event()
    consumer_tasks = [
        asyncio.create_task(run_audio_sqs_consumer(stop_event)),
        asyncio.create_task(run_chat_notification_sqs_consumer(stop_event)),
        asyncio.create_task(run_prayer_notification_sqs_consumer(stop_event)),
        asyncio.create_task(run_join_request_notification_sqs_consumer(stop_event)),
        asyncio.create_task(run_group_post_notification_sqs_consumer(stop_event)),
        asyncio.create_task(run_event_notification_sqs_consumer(stop_event)),
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

    if mongodb_client:
        mongodb_client.close()
