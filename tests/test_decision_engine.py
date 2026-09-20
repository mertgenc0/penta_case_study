""" Karar motoru birim testleri. """
import logging

import pytest

from app.config import Settings
from app.core.decision_engine import (
    Decision,
    OfferContext,
    ReasonCode,
    evaluate,
)
from app.core.pii import mask_email, mask_phone, pseudonymize
from app.utils.logger import PIIRedactionFilter, redact_text
from app.ai.offer_agent import AISummary, _GuardrailError, _check_numeric_consistency

# Test için sabit ayarlar — .env'den bağımsız
_SETTINGS = Settings(
    margin_threshold=0.15,
    high_value_threshold=100000.0,
    strategic_discount_threshold=15.0,
)


def _ctx(**overrides) -> OfferContext:
    """
    Tüm kuralları geçen (APPROVE) varsayılan bağlam oluşturur.
    Testler yalnızca ilgili alanı override eder.
    """
    defaults = dict(
        offer_total=75000.0,
        quantity=5,
        unit_cost=10000.0,
        stock_available=50,
        customer_segment="gold",
        requested_discount=5.0,
        request_product_id="PRD-100",
        crm_product_id="PRD-100",
        crm_quantity=5,
        crm_offer_total=75000.0,
    )
    defaults.update(overrides)
    return OfferContext(**defaults)


# OUT_OF_STOCK

def test_out_of_stock_gives_reject():
    """Stok sıfır olduğunda OUT_OF_STOCK ve REJECT beklenir."""
    result = evaluate(_ctx(stock_available=0), _SETTINGS)
    assert result.decision == Decision.REJECT
    assert ReasonCode.OUT_OF_STOCK in result.reason_codes
    assert result.approval_required is False


# INSUFFICIENT_STOCK

def test_insufficient_stock_gives_need_approval():
    """Stok > 0 ama istenenden az olduğunda INSUFFICIENT_STOCK beklenir."""
    result = evaluate(_ctx(stock_available=3, quantity=5), _SETTINGS)
    assert result.decision == Decision.NEED_APPROVAL
    assert ReasonCode.INSUFFICIENT_STOCK in result.reason_codes


def test_stock_equal_to_quantity_does_not_trigger():
    """Stok tam olarak istek adedine eşitse kural tetiklenmez (< kullanılır)."""
    result = evaluate(_ctx(stock_available=5, quantity=5), _SETTINGS)
    assert ReasonCode.INSUFFICIENT_STOCK not in result.reason_codes
    assert ReasonCode.OUT_OF_STOCK not in result.reason_codes


# DATA_INCONSISTENCY

def test_data_inconsistency_on_quantity_mismatch():
    """CRM'deki adet ile istek adedi uyuşmadığında DATA_INCONSISTENCY beklenir."""
    result = evaluate(_ctx(crm_quantity=10), _SETTINGS)
    assert ReasonCode.DATA_INCONSISTENCY in result.reason_codes


def test_data_inconsistency_on_product_mismatch():
    """CRM'deki ürün kimliği farklıysa DATA_INCONSISTENCY beklenir."""
    result = evaluate(_ctx(crm_product_id="PRD-999"), _SETTINGS)
    assert ReasonCode.DATA_INCONSISTENCY in result.reason_codes


def test_data_inconsistency_on_total_mismatch():
    """CRM'deki tutar farklıysa DATA_INCONSISTENCY beklenir."""
    result = evaluate(_ctx(crm_offer_total=80000.0), _SETTINGS)
    assert ReasonCode.DATA_INCONSISTENCY in result.reason_codes


# LOW_MARGIN

def test_low_margin_gives_need_approval():
    """Marj eşiğin altındaysa LOW_MARGIN beklenir."""
    # unit_cost=7000, freight=0, quantity=5 → maliyet=35000, marj=(40000-35000)/40000=%12.5 < %15
    result = evaluate(_ctx(offer_total=40000.0, quantity=5, unit_cost=7000.0), _SETTINGS)
    assert ReasonCode.LOW_MARGIN in result.reason_codes
    assert result.decision == Decision.NEED_APPROVAL


def test_margin_exactly_at_threshold_does_not_trigger():
    """Marj tam eşiğe eşitse kural tetiklenmez (< kullanılır, <= değil)."""
    # marj = 0.15 tam: offer_total=100, unit_cost=85, marj=(100-85)/100=0.15
    result = evaluate(_ctx(offer_total=100.0, quantity=1, unit_cost=85.0), _SETTINGS)
    assert ReasonCode.LOW_MARGIN not in result.reason_codes


def test_margin_above_threshold_does_not_trigger():
    """Marj eşiğin üzerindeyse LOW_MARGIN tetiklenmez."""
    result = evaluate(_ctx(offer_total=75000.0, quantity=5, unit_cost=10000.0), _SETTINGS)
    assert ReasonCode.LOW_MARGIN not in result.reason_codes


# HIGH_VALUE_OFFER

def test_high_value_gives_need_approval():
    """Tutar eşiği aşıyorsa HIGH_VALUE_OFFER beklenir."""
    result = evaluate(_ctx(offer_total=150000.0), _SETTINGS)
    assert ReasonCode.HIGH_VALUE_OFFER in result.reason_codes
    assert result.decision == Decision.NEED_APPROVAL


def test_high_value_exactly_at_threshold_does_not_trigger():
    """Tutar tam eşiğe eşitse kural tetiklenmez (> kullanılır, >= değil)."""
    result = evaluate(_ctx(offer_total=100000.0), _SETTINGS)
    assert ReasonCode.HIGH_VALUE_OFFER not in result.reason_codes


# HIGH_DISCOUNT_STRATEGIC

def test_strategic_high_discount_gives_need_approval():
    """Stratejik bayi eşiği aşan indirim isterse HIGH_DISCOUNT_STRATEGIC beklenir."""
    result = evaluate(
        _ctx(customer_segment="strategic_partner", requested_discount=20.0),
        _SETTINGS,
    )
    assert ReasonCode.HIGH_DISCOUNT_STRATEGIC in result.reason_codes


def test_strategic_discount_exactly_at_threshold_does_not_trigger():
    """İndirim tam eşiğe eşitse kural tetiklenmez (> kullanılır)."""
    result = evaluate(
        _ctx(customer_segment="strategic_partner", requested_discount=15.0),
        _SETTINGS,
    )
    assert ReasonCode.HIGH_DISCOUNT_STRATEGIC not in result.reason_codes


def test_non_strategic_high_discount_does_not_trigger():
    """Stratejik olmayan müşteri yüksek indirim isterse bu kural tetiklenmez."""
    result = evaluate(
        _ctx(customer_segment="gold", requested_discount=20.0),
        _SETTINGS,
    )
    assert ReasonCode.HIGH_DISCOUNT_STRATEGIC not in result.reason_codes


# APPROVE

def test_approve_when_no_rules_triggered():
    """Hiçbir kural tetiklenmediğinde APPROVE ve boş reason_codes beklenir."""
    result = evaluate(_ctx(), _SETTINGS)
    assert result.decision == Decision.APPROVE
    assert result.reason_codes == []
    assert result.approval_required is False


# Çoklu kural

def test_multiple_rules_all_present():
    """Birden fazla kural aynı anda tetiklenebilir."""
    # Hem LOW_MARGIN hem HIGH_VALUE_OFFER
    result = evaluate(
        _ctx(offer_total=150000.0, unit_cost=18000.0, quantity=8),
        _SETTINGS,
    )
    assert ReasonCode.LOW_MARGIN in result.reason_codes
    assert ReasonCode.HIGH_VALUE_OFFER in result.reason_codes
    assert result.decision == Decision.NEED_APPROVAL


def test_reject_takes_priority_over_need_approval():
    """OUT_OF_STOCK (REJECT) ve başka kural birlikte varsa REJECT kazanır."""
    result = evaluate(
        _ctx(stock_available=0, offer_total=150000.0),
        _SETTINGS,
    )
    assert result.decision == Decision.REJECT


# Case örneği

def test_case_example_exact_reason_codes():

    ctx = OfferContext(
        offer_total=125000.0,
        quantity=8,
        unit_cost=14000.0,         # freight=0 (default); maliyet=112000, marj=%10.4 → LOW_MARGIN
        stock_available=20,        # yeterli stok
        customer_segment="strategic_partner",
        requested_discount=12.0,   # 15 eşiğini geçmiyor → HIGH_DISCOUNT_STRATEGIC yok
        request_product_id="PRD-445",
        crm_product_id="PRD-445",
        crm_quantity=8,
        crm_offer_total=125000.0,
    )
    result = evaluate(ctx, _SETTINGS)

    assert result.decision == Decision.NEED_APPROVAL
    assert result.reason_codes == [ReasonCode.LOW_MARGIN, ReasonCode.HIGH_VALUE_OFFER]
    assert result.approval_required is True
    assert abs(result.margin_pct - 0.104) < 0.001


#  PII maskeleme

def test_mask_email():
    """E-posta maskelemesi yerel kısmı gizler, domain açık kalır."""
    assert mask_email("buyer@example.com") == "b***r@example.com"


def test_mask_email_short_local():
    """İki karakterli yerel kısım için ilk karakter ve * döner."""
    assert mask_email("ab@example.com") == "a*@example.com"


def test_mask_phone():
    """Telefon maskelemesi son 2 rakamı açık bırakır."""
    result = mask_phone("+90 532 123 45 67")
    assert result.startswith("+90")
    assert result.endswith("67")


def test_pseudonymize_same_input_same_output():
    """Aynı girdi her zaman aynı pseudonym üretmeli (deterministik)."""
    a = pseudonymize("buyer@example.com")
    b = pseudonymize("buyer@example.com")
    assert a == b
    assert a.startswith("cust_")
    assert len(a) == len("cust_") + 12


def test_pseudonymize_different_inputs_different_outputs():
    """Farklı girdiler farklı pseudonym üretmeli."""
    assert pseudonymize("a@x.com") != pseudonymize("b@x.com")


# Log maskeleme (regex → mask_email/mask_phone entegrasyonu)

def test_redact_text_masks_email_with_mask_email():
    """Log metnindeki e-posta mask_email biçimiyle değiştirilmeli; ham hâli görünmemeli."""
    result = redact_text("Müşteri iletişimi: buyer@example.com")
    assert "buyer@example.com" not in result
    assert "b***r@example.com" in result


def test_redact_text_masks_phone_with_mask_phone():
    """Log metnindeki telefon mask_phone biçimiyle değiştirilmeli; ham hâli görünmemeli."""
    result = redact_text("Aranan numara: +90 532 123 45 67")
    assert "+90 532 123 45 67" not in result
    assert "+90 *** *** ** 67" in result


def test_redact_text_removes_tckn_fully():
    """TCKN kısmi maskelenmez; tamamen kaldırılır."""
    result = redact_text("Kimlik: 12345678901 doğrulandı")
    assert "12345678901" not in result
    assert "[tckn gizlendi]" in result


def test_redact_text_removes_iban_fully():
    """IBAN kısmi maskelenmez; tamamen kaldırılır."""
    result = redact_text("Hesap: TR33 0006 1005 1978 6457 8413 26")
    assert "6457" not in result
    assert "[iban gizlendi]" in result


def test_pii_filter_applies_mask_email_on_log_record():
    """PIIRedactionFilter, log kaydını mask_email üzerinden geçirmeli."""
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="", lineno=0,
        msg="E-posta: buyer@example.com kaydedildi", args=(), exc_info=None,
    )
    PIIRedactionFilter().filter(record)
    assert "buyer@example.com" not in record.msg
    assert "b***r@example.com" in record.msg


def test_pii_filter_applies_mask_phone_on_log_record():
    """PIIRedactionFilter, log kaydını mask_phone üzerinden geçirmeli."""
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="", lineno=0,
        msg="Telefon: +90 532 123 45 67 iletildi", args=(), exc_info=None,
    )
    PIIRedactionFilter().filter(record)
    assert "+90 532 123 45 67" not in record.msg
    assert "+90 *** *** ** 67" in record.msg


def test_pii_filter_is_idempotent():
    """Filter birden fazla handler tarafından çalıştırılsa bile maskeli çıktıyı bozmamalı."""
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="", lineno=0,
        msg="E-posta: buyer@example.com", args=(), exc_info=None,
    )
    f = PIIRedactionFilter()
    f.filter(record)
    first_pass = record.msg
    f.filter(record)
    assert record.msg == first_pass
    assert "b***r@example.com" in record.msg


# Sayısal guardrail


def _summary(text: str) -> AISummary:
    """Verilen metni summary alanında taşıyan AISummary oluşturur."""
    return AISummary(summary=text, risk_notes=[], approval_message_draft=None)


def test_numeric_consistency_wrong_number_raises_guardrail_error():
    """Karar motorundan gelmeyen bir yüzde sayısı NUMERIC_MISMATCH hatası üretmeli."""
    with pytest.raises(_GuardrailError) as exc_info:
        _check_numeric_consistency(_summary("Marj %50 ile çok yüksek."), 4.32, 7.0, 12.0)
    assert exc_info.value.violation_type == "NUMERIC_MISMATCH"


def test_numeric_consistency_correct_margin_passes():
    """Karar motorunun hesapladığı marj yüzdesi guardrail'i geçmeli."""
    _check_numeric_consistency(_summary("Marj %4,3 ile eşiğin altında."), 4.32, 7.0, 12.0)


def test_numeric_consistency_correct_threshold_passes():
    """Karar motorunun hesapladığı eşik yüzdesi guardrail'i geçmeli."""
    _check_numeric_consistency(_summary("Eşik %7,0 olarak belirlendi."), 4.32, 7.0, 12.0)


def test_numeric_consistency_correct_discount_passes():
    """Talep edilen indirim oranı guardrail'i geçmeli."""
    _check_numeric_consistency(_summary("12% indirim talep edilmiş."), 4.32, 7.0, 12.0)


def test_numeric_consistency_no_numbers_passes():
    """Sayısal yüzde ifadesi içermeyen metin guardrail'i geçmeli."""
    _check_numeric_consistency(
        _summary("Teklif marjı minimum karlılık eşiğinin altında kalmaktadır."),
        4.32, 7.0, 12.0,
    )


def test_numeric_consistency_tolerance_boundary():
    """0.1 puan tolerans sınırı: 0.1 fark geçmeli, 0.11 fark fallback'e düşürmeli."""
    _check_numeric_consistency(_summary("Marj %4,2 ile eşiğin altında."), 4.32, 7.0, 12.0)  # |4.2-4.32|=0.12 > 0.1
    # Not: 4.2 → allowed={4.3, 7.0, 12.0} → |4.2-4.3|=0.1 ≤ 0.1 → geçer (round(4.32,1)=4.3)


def test_numeric_consistency_percent_sign_prefix():
    """%7,2 biçimi (Türkçe önek) doğru yakalanmalı."""
    with pytest.raises(_GuardrailError):
        _check_numeric_consistency(_summary("Marj %50,0 ile yüksek."), 4.32, 7.0, 12.0)


def test_numeric_consistency_percent_sign_suffix():
    """7.2% biçimi (sonek) doğru yakalanmalı."""
    with pytest.raises(_GuardrailError):
        _check_numeric_consistency(_summary("Marj 50.0% ile yüksek."), 4.32, 7.0, 12.0)


def test_numeric_consistency_yuzde_word():
    """yüzde 7,2 biçimi (yazıyla) doğru yakalanmalı."""
    with pytest.raises(_GuardrailError):
        _check_numeric_consistency(_summary("Marj yüzde 50 ile yüksek."), 4.32, 7.0, 12.0)


# Nakliye dahil marj hesabı


def test_margin_calculation_includes_freight():
    """Nakliye maliyeti marj hesabına dahil edilmeli."""
    # unit_cost=14700, unit_freight_cost=250, quantity=8, offer_total=125000
    # (125000 - (14700+250)*8) / 125000 = 5400/125000 ≈ 4.32%
    ctx = OfferContext(
        offer_total=125000.0,
        quantity=8,
        unit_cost=14700.0,
        unit_freight_cost=250.0,
        stock_available=20,
        customer_segment="strategic_partner",
        requested_discount=12.0,
        request_product_id="PRD-445",
        crm_product_id="PRD-445",
        crm_quantity=8,
        crm_offer_total=125000.0,
        brand="lenovo",
    )
    result = evaluate(ctx, Settings(margin_threshold=0.08))
    assert abs(result.margin_pct - 0.0432) < 0.001
    assert ReasonCode.LOW_MARGIN in result.reason_codes


def test_margin_calculation_zero_freight():
    """Nakliye sıfırken marj hesabı eski formülle aynı sonucu vermeli."""
    ctx = OfferContext(
        offer_total=100.0,
        quantity=1,
        unit_cost=85.0,
        unit_freight_cost=0.0,
        stock_available=10,
        customer_segment="gold",
        requested_discount=5.0,
        request_product_id="PRD-X",
        crm_product_id="PRD-X",
        crm_quantity=1,
        crm_offer_total=100.0,
    )
    result = evaluate(ctx, Settings(margin_threshold=0.15))
    assert abs(result.margin_pct - 0.15) < 0.0001
    assert ReasonCode.LOW_MARGIN not in result.reason_codes


# Marka ve kategori eşik çözümlemesi

def test_brand_threshold_used_over_default():
    """Ürüne marka eşiği atanmışsa MARGIN_THRESHOLD yerine marka eşiği kullanılmalı."""
    # lenovo eşiği 0.07; quantity=5 default; marj=(10000-9200)/10000=8% → 8%>7% → tetiklenmez
    # settings.margin_threshold=0.15 → marka eşiği (0.07) kullanılmalı, LOW_MARGIN çıkmamalı
    ctx = _ctx(
        offer_total=10000.0, quantity=5, unit_cost=1840.0, unit_freight_cost=0.0,
        brand="lenovo", crm_offer_total=10000.0,
    )
    result = evaluate(ctx, _SETTINGS)
    assert ReasonCode.LOW_MARGIN not in result.reason_codes


def test_default_threshold_used_when_no_brand():
    """Marka eşiği yoksa settings.margin_threshold kullanılmalı."""
    # settings.margin_threshold=0.15; marj=(10000-8600)/10000=14% < 15% → LOW_MARGIN tetiklenir
    ctx = _ctx(
        offer_total=10000.0, quantity=5, unit_cost=1720.0, unit_freight_cost=0.0,
        brand=None, crm_offer_total=10000.0,
    )
    result = evaluate(ctx, _SETTINGS)
    assert ReasonCode.LOW_MARGIN in result.reason_codes