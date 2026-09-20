""" POST /evaluate-offer uç noktası ve istek orkestrasyonu. """
import logging
import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.ai.offer_agent import AgentInput, run_agent
from app.ai.prompts import PROMPT_VERSION
from app.config import Settings, get_settings
from app.core import decision_engine
from app.core.decision_engine import OfferContext
from app.core.pii import pseudonymize
from app.exceptions import IntegrationError, OfferNotFoundError, ProductNotFoundError
from app.schemas import EvaluationResponse, OfferRequest
from app.services.crm_client import CRMClient, get_crm_client
from app.services.erp_client import ERPClient, get_erp_client
from app.utils.logger import write_audit

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/evaluate-offer", response_model=EvaluationResponse, tags=["offers"])
async def evaluate_offer(
    request: Request,
    body: OfferRequest,
    settings: Settings = Depends(get_settings),
    crm: CRMClient = Depends(get_crm_client),
    erp: ERPClient = Depends(get_erp_client),
) -> JSONResponse:
    """ Teklif ön değerlendirmesi yapar ve sonucu döndürür. """

    transaction_id = request.state.transaction_id
    start_time = getattr(request.state, "start_time", time.time())

    # Audit için varsayılan değerler — hata durumunda bunlar yazılır
    decision_value = "UNKNOWN"
    reason_codes_values: list[str] = []
    ai_status = "FALLBACK"
    http_status = 200
    error_code: str | None = None
    margin_pct_value: float | None = None

    # GÜVENLİK: pseudonymize en erken noktada yapılır
    customer_ref = pseudonymize(body.customer.email or body.customer.name)

    try:
        # 1. CRM: teklif kaydını al
        offer = crm.get_offer(body.offer_id)

        # 2. ERP: stok ve fiyat bilgisini al
        stock = erp.get_stock(body.product_id)
        price = erp.get_price(body.product_id)

        # 3. Deterministik karar motoru
        ctx = OfferContext(
            offer_total=body.offer_total,
            quantity=body.quantity,
            unit_cost=price.unit_cost,
            unit_freight_cost=price.unit_freight_cost,
            stock_available=stock.available,
            customer_segment=body.customer_segment.value,
            requested_discount=body.requested_discount,
            request_product_id=body.product_id,
            crm_product_id=offer.product_id,
            crm_quantity=offer.quantity,
            crm_offer_total=offer.offer_total,
            brand=price.brand,
        )
        result = decision_engine.evaluate(ctx, settings)
        decision_value = result.decision.value
        reason_codes_values = [rc.value for rc in result.reason_codes]
        margin_pct_value = result.margin_pct

        # 4. AI katmanı — run_agent her zaman değer döner, exception fırlatmaz
        agent_input = AgentInput(
            offer_id=body.offer_id,
            decision=result.decision.value,
            reason_codes=reason_codes_values,
            stock_available=stock.available,
            stock_requested=body.quantity,
            list_price=price.list_price,
            margin_pct=result.margin_pct,
            threshold_pct=result.effective_threshold,
            offer_total=body.offer_total,
            segment=body.customer_segment.value,
            discount_pct=body.requested_discount,
        )
        ai_summary, ai_status = run_agent(agent_input, settings)

        logger.info(
            "Teklif değerlendirmesi tamamlandı",
            extra={
                "offer_id": body.offer_id,
                "decision": decision_value,
                "ai_status": ai_status,
                "transaction_id": transaction_id,
            },
        )

        resp = EvaluationResponse(
            offer_id=body.offer_id,
            transaction_id=transaction_id,
            decision=decision_value,
            reason_codes=reason_codes_values,
            ai_summary=ai_summary.summary,
            ai_status=ai_status,
            approval_required=result.approval_required,
            approval_message_draft=ai_summary.approval_message_draft,
            recommended_actions=result.recommended_actions,
            rule_version=decision_engine.RULE_VERSION,
            pii_masked=True,
            margin_pct=round(result.margin_pct * 100, 2),
        )
        resp_dict = resp.model_dump()

        # GÜVENLİK: INCLUDE_MARGIN_DETAILS=false iken marj alanı yanıttan tamamen çıkarılır.
        # Bu bayrak, rol tabanlı erişim eklendiğinde yönetici/danışman ayrımının bağlantı noktasıdır.
        if not settings.include_margin_details:
            resp_dict.pop("margin_pct", None)

        return JSONResponse(content=resp_dict)

    except OfferNotFoundError:
        http_status = 404
        error_code = "OFFER_NOT_FOUND"
        raise
    except ProductNotFoundError:
        http_status = 404
        error_code = "PRODUCT_NOT_FOUND"
        raise
    except IntegrationError:
        http_status = 503
        error_code = "INTEGRATION_UNAVAILABLE"
        raise
    except Exception:
        http_status = 500
        error_code = "INTERNAL_ERROR"
        raise
    finally:
        latency_ms = (time.time() - start_time) * 1000
        write_audit(
            transaction_id=transaction_id,
            offer_id=body.offer_id,
            customer_ref=customer_ref,
            decision=decision_value,
            reason_codes=reason_codes_values,
            rule_version=decision_engine.RULE_VERSION,
            ai_status=ai_status,
            prompt_version=PROMPT_VERSION,
            latency_ms=latency_ms,
            http_status=http_status,
            error_code=error_code,
            margin_pct=margin_pct_value,
        )