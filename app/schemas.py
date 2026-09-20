""" HTTP istek ve yanıt şemaları. """

import re
from enum import Enum

from pydantic import BaseModel, Field, field_validator


class CustomerSegment(str, Enum):
    """ Müşteri segmenti — karar motorunda indirim eşiği kontrolü için kullanılır. """

    standard = "standard"
    gold = "gold"
    strategic_partner = "strategic_partner"


class Customer(BaseModel):
    """ Müşteri bilgisi — PII içerir. (core/pii.p ile maskelenir)  """

    name: str
    email: str | None = None
    phone: str | None = None

    @field_validator("email")
    @classmethod
    def validate_email_format(cls, v: str | None) -> str | None:
        """ E-posta formatını regex ile doğrular. """

        if v is None:
            return v
        pattern = r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$"
        if not re.match(pattern, v):
            raise ValueError("Geçersiz e-posta formatı")
        return v


class OfferRequest(BaseModel):
    """ POST /evaluate-offer istek gövdesi. """

    offer_id: str
    customer: Customer
    product_id: str
    quantity: int = Field(gt=0, description="Teklif edilen ürün adedi (sıfırdan büyük)")
    requested_discount: float = Field(ge=0, le=100, description="İstenen indirim yüzdesi")
    offer_total: float = Field(gt=0, description="Toplam teklif tutarı (TRY)")
    customer_segment: CustomerSegment


class EvaluationResponse(BaseModel):
    """ POST /evaluate-offer başarılı yanıtı (HTTP 200). """

    offer_id: str
    transaction_id: str
    decision: str                          # APPROVE | REJECT | NEED_APPROVAL
    reason_codes: list[str]
    ai_summary: str
    ai_status: str                         # OK | FALLBACK
    approval_required: bool
    approval_message_draft: str | None     # Yalnızca NEED_APPROVAL'da dolu
    recommended_actions: list[str]         # Deterministik; AI üretmez
    rule_version: str
    pii_masked: bool
    # INCLUDE_MARGIN_DETAILS=false iken yanıt dict'inden çıkarılır; None olarak seri edilmez.
    margin_pct: float | None = None


class ErrorResponse(BaseModel):
    """ Tüm 4xx/5xx hata yanıtları için standart yapı. """

    error_code: str
    message: str
    details: list[dict] | None = None
    transaction_id: str