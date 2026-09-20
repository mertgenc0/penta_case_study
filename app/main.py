""" FastAPI uygulama giriş noktası. """

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.routes import router
from app.config import get_settings
from app.exceptions import IntegrationError, OfferNotFoundError, ProductNotFoundError
from app.schemas import ErrorResponse
from app.utils.logger import setup_logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """ Uygulama başlangıç ve kapanış mantığı. """

    settings = get_settings()
    setup_logging(settings.log_level)
    logger.info("Uygulama başlatıldı", extra={"log_level": settings.log_level})
    yield
    logger.info("Uygulama kapatıldı")


app = FastAPI(
    title="Teklif Ön Değerlendirme API",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(router)


@app.middleware("http")
async def transaction_id_middleware(request: Request, call_next) -> Response:
    """ Her isteğe benzersiz transaction_id ve start_time atar. """

    transaction_id = str(uuid.uuid4())
    request.state.transaction_id = transaction_id
    request.state.start_time = time.time()

    response = await call_next(request)
    response.headers["X-Transaction-ID"] = transaction_id
    return response


def _get_transaction_id(request: Request) -> str:
    """ request.state'ten transaction_id alır; state henüz set edilmediyse yeni üretir."""
    return getattr(request.state, "transaction_id", str(uuid.uuid4()))


# Exception handler'lar
@app.exception_handler(OfferNotFoundError)
async def offer_not_found_handler(request: Request, exc: OfferNotFoundError) -> JSONResponse:
    """ CRM'de teklif bulunamadığında 404 döner."""
    return JSONResponse(
        status_code=404,
        content=ErrorResponse(
            error_code="OFFER_NOT_FOUND",
            message=f"Teklif bulunamadı: {exc.offer_id}",
            transaction_id=_get_transaction_id(request),
        ).model_dump(),
    )


@app.exception_handler(ProductNotFoundError)
async def product_not_found_handler(
    request: Request, exc: ProductNotFoundError
) -> JSONResponse:
    """ ERP'de ürün bulunamadığında 404 döner."""
    return JSONResponse(
        status_code=404,
        content=ErrorResponse(
            error_code="PRODUCT_NOT_FOUND",
            message=f"Ürün bulunamadı: {exc.product_id}",
            transaction_id=_get_transaction_id(request),
        ).model_dump(),
    )


@app.exception_handler(IntegrationError)
async def integration_error_handler(request: Request, exc: IntegrationError) -> JSONResponse:
    """ CRM veya ERP erişilemez olduğunda 503 döner."""
    return JSONResponse(
        status_code=503,
        content=ErrorResponse(
            error_code="INTEGRATION_UNAVAILABLE",
            message=f"{exc.service} servisine erişilemiyor",
            transaction_id=_get_transaction_id(request),
        ).model_dump(),
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """ Pydantic doğrulama hatalarını standart formata dönüştürür. """

    details = [
        {
            "field": " -> ".join(str(loc) for loc in err["loc"]),
            "message": err["msg"],
        }
        for err in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content=ErrorResponse(
            error_code="VALIDATION_ERROR",
            message="İstek doğrulama hatası",
            details=details,
            transaction_id=_get_transaction_id(request),
        ).model_dump(),
    )


@app.exception_handler(Exception)
async def generic_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """ Yakalanmayan tüm hatalar için 500 döner. """
    logger.error(
        "Beklenmeyen hata",
        exc_info=exc,
        extra={"transaction_id": _get_transaction_id(request)},
    )
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(
            error_code="INTERNAL_ERROR",
            message="Beklenmeyen bir hata oluştu",
            transaction_id=_get_transaction_id(request),
        ).model_dump(),
    )


@app.get("/health", tags=["system"])
async def health_check() -> dict[str, str]:
    """ Servis sağlık kontrolü. """
    return {"status": "ok", "service": "teklif-degerlendirme-api"}