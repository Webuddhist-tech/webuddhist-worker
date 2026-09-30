from fastapi import FastAPI
from starlette import status
from fastapi.middleware.cors import CORSMiddleware

from worker_api.middleware.request_observability import RequestObservabilityMiddleware
from worker_api.db.mongo_database import lifespan
from worker_api.audio.audio_views import audio_router
from worker_api.notifications.internal_views import internal_router
from worker_api.notifications.reminder_views import reminder_router

import uvicorn

api = FastAPI(
    title="WebBuddhist Worker API",
    description="This is the API documentation for WebBuddhist Worker application",
    root_path="/api/v1",
    redoc_url="/docs",
    lifespan=lifespan
)

api.include_router(audio_router)
api.include_router(internal_router)
api.include_router(reminder_router)

api.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
api.add_middleware(RequestObservabilityMiddleware)

@api.get("/props", status_code=status.HTTP_200_OK)
async def get_props():
    return {
        "app_name": "WebBuddhist Worker",
        "version": "0.0.1",
        "status": "operational"
    }


@api.get("/health", status_code=status.HTTP_204_NO_CONTENT)
async def get_health():
    return {'status': 'up'}

if __name__ == "__main__":
    uvicorn.run("worker_api.app:api", host="127.0.0.1", port=8001, reload=True)
