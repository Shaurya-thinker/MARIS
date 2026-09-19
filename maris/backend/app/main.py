from contextlib import asynccontextmanager
import logging
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.experiment_routes import router as experiment_router
from app.api.routes import router
from app.core.config import settings

logger = logging.getLogger("maris.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.warning(
        "Investigation store is in-memory and ephemeral. All investigation data will be lost on server restart."
    )
    yield


app = FastAPI(title=settings.service_name, lifespan=lifespan)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception(
        "Unhandled server error processing %s %s: %s",
        request.method,
        request.url.path,
        exc,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "An unexpected internal error occurred."},
    )


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    start_time = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        duration_ms = (time.perf_counter() - start_time) * 1000
        logger.error(
            "%s %s 500 - %.2fms",
            request.method,
            request.url.path,
            duration_ms,
        )
        raise

    duration_ms = (time.perf_counter() - start_time) * 1000
    status_code = response.status_code
    msg = f"{request.method} {request.url.path} {status_code} - {duration_ms:.2f}ms"
    if status_code < 400:
        logger.info(msg)
    elif status_code < 500:
        logger.warning(msg)
    else:
        logger.error(msg)
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(router)
app.include_router(experiment_router, prefix="/api/experiment")