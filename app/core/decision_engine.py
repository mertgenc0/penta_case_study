""" Deterministik karar motoru. """

import logging
from dataclasses import dataclass, field
from enum import Enum

from app.config import BRAND_MARGIN_THRESHOLDS, Settings

logger = logging.getLogger(__name__)


class ReasonCode(str, Enum):
    """ Karar gerekçe kodları — her kural bir kod üretir. """

    OUT_OF_STOCK = "OUT_OF_STOCK"
    INSUFFICIENT_STOCK = "INSUFFICIENT_STOCK"
    DATA_INCONSISTENCY = "DATA_INCONSISTENCY"
    LOW_MARGIN = "LOW_MARGIN"
    HIGH_VALUE_OFFER = "HIGH_VALUE_OFFER"
    HIGH_DISCOUNT_STRATEGIC = "HIGH_DISCOUNT_STRATEGIC"


class Decision(str, Enum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    NEED_APPROVAL = "NEED_APPROVAL"


RULE_VERSION = "2026.1"

_REASON_TO_DECISION: dict[ReasonCode, Decision] = {
    ReasonCode.OUT_OF_STOCK:           Decision.REJECT,
    ReasonCode.INSUFFICIENT_STOCK:     Decision.NEED_APPROVAL,
    ReasonCode.DATA_INCONSISTENCY:     Decision.NEED_APPROVAL,
    ReasonCode.LOW_MARGIN:             Decision.NEED_APPROVAL,
    ReasonCode.HIGH_VALUE_OFFER:       Decision.NEED_APPROVAL,
    ReasonCode.HIGH_DISCOUNT_STRATEGIC: Decision.NEED_APPROVAL,
}

RECOMMENDED_ACTIONS: dict[ReasonCode, str] = {
    ReasonCode.OUT_OF_STOCK:           "Stok yeterli değil, alternatif ürün veya tedarik takvimi önerilmeli.",
    ReasonCode.INSUFFICIENT_STOCK:     "Mevcut stok yeterliyse kısmi teslimat onaylanabilir.",
    ReasonCode.DATA_INCONSISTENCY:     "CRM kaydı ile istek arasındaki tutarsızlık giderilmeli.",
    ReasonCode.LOW_MARGIN:             "Fiyatlandırma gözden geçirilmeli veya maliyet optimizasyonu yapılmalı.",
    ReasonCode.HIGH_VALUE_OFFER:       "Yüksek tutarlı teklif için üst yönetim onayı gerekli.",
    ReasonCode.HIGH_DISCOUNT_STRATEGIC: "Stratejik bayi indirim politikası ile kontrol edilmeli.",
}


@dataclass
class OfferContext:
    """ Karar motoru için gereken tüm girdi verileri. """

    offer_total: float
    quantity: int
    unit_cost: float
    stock_available: int
    customer_segment: str
    requested_discount: float
    request_product_id: str
    crm_product_id: str
    crm_quantity: int
    crm_offer_total: float
    brand: str | None = None
    unit_freight_cost: float = 0.0


@dataclass
class DecisionResult:
    """ Karar motoru çıktısı. """

    decision: Decision
    reason_codes: list[ReasonCode] = field(default_factory=list)
    approval_required: bool = False
    recommended_actions: list[str] = field(default_factory=list)
    margin_pct: float = 0.0
    # Hangi marj eşiğinin uygulandığı — AI katmanı ve sayısal guardrail için iletilir.
    effective_threshold: float = 0.0


# Yardımcı fonksiyon
def resolve_margin_threshold(brand: str | None, settings: Settings) -> float:
    """ Marj eşiğini marka → varsayılan sırasıyla çözümler. """

    if brand and brand in BRAND_MARGIN_THRESHOLDS:
        threshold = BRAND_MARGIN_THRESHOLDS[brand]
        logger.debug("Marka bazlı marj eşiği kullanıldı", extra={"brand": brand, "threshold": threshold})
        return threshold

    logger.debug("Varsayılan marj eşiği kullanıldı", extra={"threshold": settings.margin_threshold})
    return settings.margin_threshold


# Kural fonksiyonları

def _check_stock(available: int, requested: int) -> list[ReasonCode]:
    """ Stok kurallarını kontrol eder (Kural 1 ve 2). """
    if available == 0:
        return [ReasonCode.OUT_OF_STOCK]
    if available < requested:
        return [ReasonCode.INSUFFICIENT_STOCK]
    return []


def _check_data_inconsistency(
    req_product_id: str, crm_product_id: str,
    req_quantity: int, crm_quantity: int,
    req_offer_total: float, crm_offer_total: float,
) -> list[ReasonCode]:
    """ İstek ile CRM kaydını karşılaştırır (Kural 3). """
    if req_product_id != crm_product_id or req_quantity != crm_quantity or req_offer_total != crm_offer_total:
        return [ReasonCode.DATA_INCONSISTENCY]
    return []


def _calculate_margin(offer_total: float, unit_cost: float, unit_freight_cost: float, quantity: int) -> float:
    """ marj oranını hesaplar. """

    if offer_total == 0:
        return 0.0
    return (offer_total - (unit_cost + unit_freight_cost) * quantity) / offer_total


def _check_margin(margin_pct: float, threshold: float) -> list[ReasonCode]:
    """ Marjı eşikle karşılaştırır  """

    if margin_pct < threshold:
        return [ReasonCode.LOW_MARGIN]
    return []


def _check_high_value(offer_total: float, threshold: float) -> list[ReasonCode]:
    """ Yüksek tutar eşiğini kontrol eder """

    if offer_total > threshold:
        return [ReasonCode.HIGH_VALUE_OFFER]
    return []


def _check_strategic_discount(segment: str, discount: float, threshold: float) -> list[ReasonCode]:
    """ Stratejik bayi indirim eşiğini kontrol eder. """
    if segment == "strategic_partner" and discount > threshold:
        return [ReasonCode.HIGH_DISCOUNT_STRATEGIC]
    return []


def _determine_decision(reason_codes: list[ReasonCode]) -> Decision:
    """ Reason code listesinden nihai kararı belirler; REJECT > NEED_APPROVAL > APPROVE. """

    if not reason_codes:
        return Decision.APPROVE
    decisions = {_REASON_TO_DECISION[rc] for rc in reason_codes}
    if Decision.REJECT in decisions:
        return Decision.REJECT
    return Decision.NEED_APPROVAL


# Ana değerlendirme fonksiyonu

def evaluate(ctx: OfferContext, settings: Settings) -> DecisionResult:
    """ Tüm kuralları sırayla çalıştırır ve karar döndürür. """
    reason_codes: list[ReasonCode] = []

    # Kural 1-2: Stok kontrolü
    reason_codes.extend(_check_stock(ctx.stock_available, ctx.quantity))

    # Kural 3: CRM-istek tutarsızlığı
    reason_codes.extend(_check_data_inconsistency(
        ctx.request_product_id, ctx.crm_product_id,
        ctx.quantity, ctx.crm_quantity,
        ctx.offer_total, ctx.crm_offer_total,
    ))

    # Kural 4: Marj kontrolü — marka → kategori → varsayılan eşik sırası
    effective_threshold = resolve_margin_threshold(ctx.brand, settings)
    margin_pct = _calculate_margin(ctx.offer_total, ctx.unit_cost, ctx.unit_freight_cost, ctx.quantity)
    reason_codes.extend(_check_margin(margin_pct, effective_threshold))

    # Kural 5: Yüksek tutar kontrolü
    reason_codes.extend(_check_high_value(ctx.offer_total, settings.high_value_threshold))

    # Kural 6: Stratejik bayi indirim kontrolü
    reason_codes.extend(_check_strategic_discount(
        ctx.customer_segment, ctx.requested_discount,
        settings.strategic_discount_threshold,
    ))

    decision = _determine_decision(reason_codes)

    return DecisionResult(
        decision=decision,
        reason_codes=reason_codes,
        approval_required=(decision == Decision.NEED_APPROVAL),
        recommended_actions=[RECOMMENDED_ACTIONS[rc] for rc in reason_codes],
        margin_pct=margin_pct,
        effective_threshold=effective_threshold,
    )