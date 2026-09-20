""" Uçtan uca API testleri."""
import json

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import app

# Test ayarları


def _make_settings(**overrides) -> Settings:
    """ Test için Settings nesnesi üretir. """
    return Settings(
        log_level="ERROR",
        pii_hash_salt="test-salt-for-tests",
        margin_threshold=0.15,
        high_value_threshold=100000.0,
        strategic_discount_threshold=15.0,
        llm_provider="mock",
        llm_timeout_seconds=10,
        llm_max_tool_iterations=3,
        **overrides,
    )


@pytest.fixture(scope="module")
def client():
    """TestClient yaşam döngüsünü lifespan ile birlikte yönetir."""
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def default_settings(client):
    """Her test için varsayılan mock settings enjekte eder; testten sonra temizler."""
    app.dependency_overrides[get_settings] = lambda: _make_settings()
    yield
    app.dependency_overrides.clear()


# Yardımcı: geçerli istek gövdesi


def _body(**overrides) -> dict:
    """Case örneği (OFF-10023) temel alınarak istek gövdesi üretir."""
    base = {
        "offer_id": "OFF-10023",
        "customer": {"name": "ABC Teknoloji", "email": "buyer@example.com"},
        "product_id": "PRD-445",
        "quantity": 8,
        "requested_discount": 12,
        "offer_total": 125000,
        "customer_segment": "strategic_partner",
    }
    base.update(overrides)
    return base


#  Normal teklif → APPROVE

def test_normal_offer_approve(client):
    """OFF-10001: stok yeterli, marj ~%33, tutar 75k < 100k — APPROVE beklenir."""
    resp = client.post("/evaluate-offer", json={
        "offer_id": "OFF-10001",
        "customer": {"name": "Hızlı Bilişim A.Ş.", "email": "satis@hizlibilisim.com"},
        "product_id": "PRD-100",
        "quantity": 5,
        "requested_discount": 5,
        "offer_total": 75000,
        "customer_segment": "gold",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["decision"] == "APPROVE"
    assert data["reason_codes"] == []
    assert data["approval_required"] is False
    assert data["approval_message_draft"] is None
    assert data["pii_masked"] is True
    assert data["rule_version"] == "2026.1"
    assert "transaction_id" in data


# Senaryo 2: Stok yok → REJECT


def test_out_of_stock_reject(client):
    """OFF-10002: stok 0 — REJECT / OUT_OF_STOCK beklenir."""
    resp = client.post("/evaluate-offer", json={
        "offer_id": "OFF-10002",
        "customer": {"name": "Doğu Teknoloji Ltd.", "email": "teklif@dogutekno.com"},
        "product_id": "PRD-200",
        "quantity": 3,
        "requested_discount": 8,
        "offer_total": 30000,
        "customer_segment": "standard",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["decision"] == "REJECT"
    assert "OUT_OF_STOCK" in data["reason_codes"]
    assert data["approval_required"] is False


# Senaryo 3: Yetersiz stok → NEED_APPROVAL


def test_insufficient_stock_need_approval(client):
    """OFF-10003: stok 2, istek 5 — NEED_APPROVAL / INSUFFICIENT_STOCK beklenir."""
    resp = client.post("/evaluate-offer", json={
        "offer_id": "OFF-10003",
        "customer": {"name": "Batı Sistem Tic.", "email": "info@batisistem.com"},
        "product_id": "PRD-300",
        "quantity": 5,
        "requested_discount": 6,
        "offer_total": 40000,
        "customer_segment": "standard",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["decision"] == "NEED_APPROVAL"
    assert "INSUFFICIENT_STOCK" in data["reason_codes"]
    assert data["approval_required"] is True


# Senaryo 4: Düşük marj → NEED_APPROVAL


def test_low_margin_need_approval(client):
    """OFF-10004: microsoft eşiği %10, marj (20000-(6200+100)×3)/20000=%7 < %10 — LOW_MARGIN beklenir."""
    resp = client.post("/evaluate-offer", json={
        "offer_id": "OFF-10004",
        "customer": {"name": "Merkez Dağıtım A.Ş.", "email": "satin@merkezdag.com"},
        "product_id": "PRD-400",
        "quantity": 3,
        "requested_discount": 5,
        "offer_total": 20000,
        "customer_segment": "standard",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["decision"] == "NEED_APPROVAL"
    assert "LOW_MARGIN" in data["reason_codes"]


# Senaryo 5: Case örneği → LOW_MARGIN + HIGH_VALUE_OFFER


def test_case_example_low_margin_and_high_value(client):
    """OFF-10023: lenovo eşiği %7, marj (125000-(14700+250)×8)/125000=%4.32 < %7; tutar>100k — LOW_MARGIN+HIGH_VALUE_OFFER beklenir."""
    resp = client.post("/evaluate-offer", json=_body())
    assert resp.status_code == 200
    data = resp.json()
    assert data["decision"] == "NEED_APPROVAL"
    assert "LOW_MARGIN" in data["reason_codes"]
    assert "HIGH_VALUE_OFFER" in data["reason_codes"]
    assert data["approval_required"] is True
    assert data["approval_message_draft"] is not None
    assert data["ai_status"] in ("OK", "FALLBACK")


# Senaryo 6: Entegrasyon hatası → 503


def test_erp_integration_error(client):
    """OFF-10099/PRD-ERR: ERP erişim hatası — 503 / INTEGRATION_UNAVAILABLE beklenir."""
    resp = client.post("/evaluate-offer", json={
        "offer_id": "OFF-10099",
        "customer": {"name": "Test Firma", "email": "test@testfirma.com"},
        "product_id": "PRD-ERR",
        "quantity": 1,
        "requested_discount": 0,
        "offer_total": 10000,
        "customer_segment": "standard",
    })
    assert resp.status_code == 503
    data = resp.json()
    assert data["error_code"] == "INTEGRATION_UNAVAILABLE"
    assert "transaction_id" in data


# Bilinmeyen teklif → 404


def test_offer_not_found(client):
    """Bilinmeyen offer_id → 404 / OFFER_NOT_FOUND beklenir."""
    resp = client.post("/evaluate-offer", json=_body(offer_id="OFF-99999"))
    assert resp.status_code == 404
    data = resp.json()
    assert data["error_code"] == "OFFER_NOT_FOUND"
    assert "transaction_id" in data


# Doğrulama hataları → 422


def test_validation_error_missing_field(client):
    """Zorunlu product_id alanı eksikse 422 / VALIDATION_ERROR beklenir."""
    body = _body()
    del body["product_id"]
    resp = client.post("/evaluate-offer", json=body)
    assert resp.status_code == 422
    data = resp.json()
    assert data["error_code"] == "VALIDATION_ERROR"
    assert "transaction_id" in data


def test_validation_error_zero_quantity(client):
    """quantity=0 gönderilirse 422 beklenir (quantity > 0 kuralı)."""
    resp = client.post("/evaluate-offer", json=_body(quantity=0))
    assert resp.status_code == 422


def test_validation_error_negative_offer_total(client):
    """offer_total <= 0 gönderilirse 422 beklenir."""
    resp = client.post("/evaluate-offer", json=_body(offer_total=-1))
    assert resp.status_code == 422


def test_validation_error_no_input_in_details(client):
    """422 yanıtının details alanı kullanıcı girdisini (input) içermemeli."""
    resp = client.post("/evaluate-offer", json=_body(quantity=-5))
    assert resp.status_code == 422
    data = resp.json()
    for detail in (data.get("details") or []):
        assert "input" not in detail


def test_validation_error_invalid_email(client):
    """Geçersiz e-posta formatı 422 dönmeli."""
    body = _body()
    body["customer"]["email"] = "gecersiz-email"
    resp = client.post("/evaluate-offer", json=body)
    assert resp.status_code == 422


def test_validation_error_invalid_segment(client):
    """Geçersiz customer_segment değeri 422 dönmeli."""
    resp = client.post("/evaluate-offer", json=_body(customer_segment="vip"))
    assert resp.status_code == 422


# AI fallback senaryosu


def test_ai_failure_returns_fallback_with_200(client):
    """LLM_FORCE_FAILURE=true: AI hatası → HTTP 200 + ai_status=FALLBACK beklenir."""
    app.dependency_overrides[get_settings] = lambda: _make_settings(llm_force_failure=True)
    resp = client.post("/evaluate-offer", json=_body())
    assert resp.status_code == 200
    data = resp.json()
    assert data["ai_status"] == "FALLBACK"
    # Karar kural motorundan gelir; AI hatası kararı etkilemez
    assert data["decision"] == "NEED_APPROVAL"


# PII sızma kontrolü


def test_pii_not_in_response_body(client):
    """Yanıt gövdesinde e-posta ve müşteri adı açık görünmemeli."""
    resp = client.post("/evaluate-offer", json=_body())
    assert resp.status_code == 200
    raw = resp.text
    assert "buyer@example.com" not in raw
    assert "ABC Teknoloji" not in raw


def test_pii_not_in_audit(client, tmp_path, monkeypatch):
    """Audit kaydında müşteri e-posta veya adı açık olmamalı; customer_ref pseudonymize."""
    audit_file = tmp_path / "audit.jsonl"
    monkeypatch.setattr("app.utils.logger._AUDIT_PATH", audit_file)

    client.post("/evaluate-offer", json=_body())

    content = audit_file.read_text(encoding="utf-8")
    assert "buyer@example.com" not in content
    assert "ABC Teknoloji" not in content

    record = json.loads(content.strip())
    # customer_ref pseudonymize değeri cust_ önekiyle başlamalı
    assert record["customer_ref"].startswith("cust_")
    assert "offer_id" in record
    assert "decision" in record


# Header kontrolü


def test_transaction_id_in_response_header(client):
    """Her yanıt X-Transaction-ID header'ı taşımalı."""
    resp = client.post("/evaluate-offer", json=_body())
    assert "x-transaction-id" in resp.headers


def test_transaction_id_in_error_response(client):
    """Hata yanıtlarında da transaction_id bulunmalı."""
    body = _body()
    del body["product_id"]
    resp = client.post("/evaluate-offer", json=body)
    assert resp.status_code == 422
    assert resp.json()["transaction_id"] is not None


# /health


def test_health_check(client):
    """GET /health her zaman 200 + status: ok dönmeli."""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# Marj görünürlüğü (INCLUDE_MARGIN_DETAILS)


def test_margin_shown_when_flag_true(client):
    """INCLUDE_MARGIN_DETAILS=true (varsayılan) iken yanıtta margin_pct bulunmalı."""
    resp = client.post("/evaluate-offer", json=_body())
    assert resp.status_code == 200
    data = resp.json()
    assert "margin_pct" in data
    assert isinstance(data["margin_pct"], float)


def test_margin_hidden_when_flag_false(client):
    """INCLUDE_MARGIN_DETAILS=false iken margin_pct alanı yanıtta hiç bulunmamalı."""
    app.dependency_overrides[get_settings] = lambda: _make_settings(include_margin_details=False)
    resp = client.post("/evaluate-offer", json=_body())
    assert resp.status_code == 200
    assert "margin_pct" not in resp.json()


#  Maliyet gizliliği


def test_cost_fields_not_in_response(client):
    """unit_cost ve unit_freight_cost API yanıtında görünmemeli."""
    resp = client.post("/evaluate-offer", json=_body())
    data = resp.json()
    assert "unit_cost" not in data
    assert "unit_freight_cost" not in data


def test_cost_fields_not_in_audit(client, tmp_path, monkeypatch):
    """unit_cost ve unit_freight_cost audit kaydında görünmemeli."""
    audit_file = tmp_path / "audit.jsonl"
    monkeypatch.setattr("app.utils.logger._AUDIT_PATH", audit_file)
    client.post("/evaluate-offer", json=_body())
    content = audit_file.read_text(encoding="utf-8")
    assert "unit_cost" not in content
    assert "unit_freight_cost" not in content
    record = json.loads(content.strip())
    assert "margin_pct" in record