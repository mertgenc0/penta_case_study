""" Kişisel veri (PII) maskeleme, pseudonymization araçları ve AI bağlam oluşturma. """

import hashlib
import hmac
import re

from app.config import get_settings

# Maskeleme fonksiyonları

def mask_email(email: str) -> str:
    """ E-postayı maskeler: buyer@example.com → b***r@example.com """

    if "@" not in email:
        return "***"
    local, domain = email.split("@", 1)
    if len(local) <= 2:
        masked_local = local[0] + "*"
    else:
        masked_local = local[0] + "***" + local[-1]
    return f"{masked_local}@{domain}"


def mask_phone(phone: str) -> str:
    """ Telefon numarasını maskeler: +90 532 123 45 67 → +90 *** *** ** 67 """

    digits = re.sub(r"\D", "", phone)
    last_two = digits[-2:] if len(digits) >= 2 else digits
    if phone.strip().startswith("+90"):
        return f"+90 *** *** ** {last_two}"
    elif phone.strip().startswith("0"):
        return f"0*** *** ** {last_two}"
    return f"*** *** ** {last_two}"


def pseudonymize(value: str) -> str:
    """ Değeri HMAC-SHA256 ile pseudonymize eder. """
    salt = get_settings().pii_hash_salt.encode("utf-8")
    h = hmac.new(salt, value.encode("utf-8"), hashlib.sha256)
    return f"cust_{h.hexdigest()[:12]}"


# AI bağlam oluşturma

def build_ai_context(
    offer_id: str,
    segment: str,
    decision: str,
    reason_codes: list[str],
    margin_pct: float,
    threshold_pct: float,
    offer_total: float,
    stock_available: int,
    stock_requested: int,
) -> dict:
    """ AI katmanına gönderilecek bağlam sözlüğünü oluşturur. """

    if offer_total <= 50000:
        amount_band = "düşük (≤ 50K TRY)"
    elif offer_total <= 100000:
        amount_band = "orta (50K–100K TRY)"
    else:
        amount_band = "yüksek (> 100K TRY)"

    if stock_available == 0:
        stock_status = "stok yok"
    elif stock_available < stock_requested:
        stock_status = f"yetersiz ({stock_available}/{stock_requested})"
    else:
        stock_status = "yeterli"

    return {
        "offer_id":      offer_id,
        "segment":       segment,
        "decision":      decision,
        "reason_codes":  reason_codes,
        "margin_pct":    round(margin_pct * 100, 1),
        "threshold_pct": round(threshold_pct * 100, 1),
        "amount_band":   amount_band,
        "stock_status":  stock_status,
    }