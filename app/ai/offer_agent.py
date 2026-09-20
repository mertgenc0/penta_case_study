""" AI katmanı — tool calling döngüsü, guardrail'ler ve fallback. """

import json
import logging
import re
from dataclasses import dataclass

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, PrivateAttr, ValidationError

from app.ai.prompts import PROMPT_VERSION, build_messages
from app.config import Settings
from app.core.pii import build_ai_context
from app.utils.logger import redact_text

logger = logging.getLogger(__name__)


# Çıktı şeması

class AISummary(BaseModel):
    """ AI katmanının üretebileceği tek çıktı şeması. """
    summary: str
    risk_notes: list[str] = []
    approval_message_draft: str | None = None


# Agent girdisi

@dataclass
class AgentInput:
    """ run_agent() için gereken tüm veriler. """
    offer_id:        str
    decision:        str
    reason_codes:    list[str]
    stock_available: int
    stock_requested: int
    list_price:      float
    margin_pct:      float   # kesir cinsinden (0.0432); tool ve guardrail yüzdeye çevirir
    threshold_pct:   float   # kesir cinsinden (0.07); sayısal doğrulama için iletilir
    offer_total:     float
    segment:         str
    discount_pct:    float   # yüzde cinsinden (12.0)


# İç hata sınıfı

class _GuardrailError(Exception):
    """ Guardrail ihlali — run_agent() yakalar ve fallback döndürür. """
    def __init__(self, violation_type: str) -> None:
        self.violation_type = violation_type
        super().__init__(violation_type)


# Fallback metin haritası

_RC_SUMMARIES: dict[str, str] = {
    "OUT_OF_STOCK":
        "Ürün stoğu tükenmiş durumda olduğundan teklif reddedilmiştir.",
    "INSUFFICIENT_STOCK":
        "Mevcut stok istenen adedi karşılamamaktadır; kısmi teslimat değerlendirilebilir.",
    "DATA_INCONSISTENCY":
        "Teklif verileri ile CRM kaydı arasında tutarsızlık tespit edilmiştir.",
    "LOW_MARGIN":
        "Teklif marjı minimum karlılık eşiğinin altında kalmaktadır.",
    "HIGH_VALUE_OFFER":
        "Yüksek tutarlı bu teklif standart onay limitini aşmaktadır.",
    "HIGH_DISCOUNT_STRATEGIC":
        "Talep edilen indirim oranı stratejik bayi politika sınırını aşmaktadır. "
        "Risk: kâr marjında erozyon ve diğer bayilerde emsal indirim beklentisi oluşması.",
}


# Mock LLM

class MockToolCallingLLM(BaseChatModel):
    """ API anahtarı gerektirmeyen test LLM'i. """

    reason_codes: list[str] = []
    decision: str = "NEED_APPROVAL"
    _bound_tools: list = PrivateAttr(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "mock-tool-calling"

    def bind_tools(self, tools, **kwargs) -> "MockToolCallingLLM":
        """ Tool listesini modele bağlar; yeni örnek döndürür. """
        new_instance = MockToolCallingLLM(reason_codes=self.reason_codes, decision=self.decision)
        new_instance._bound_tools = list(tools)
        return new_instance

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs) -> ChatResult:
        """ Mesaj geçmişine göre tool çağrısı veya JSON özet döndürür. """
        has_tool_results = any(isinstance(m, ToolMessage) for m in messages)
        if not has_tool_results and self._bound_tools:
            tool_calls = [
                {"name": t.name, "args": self._default_args(t.name), "id": f"mock_{i}", "type": "tool_call"}
                for i, t in enumerate(self._bound_tools)
            ]
            ai_msg = AIMessage(content="", tool_calls=tool_calls)
        else:
            ai_msg = AIMessage(content=self._build_json())
        return ChatResult(generations=[ChatGeneration(message=ai_msg)])

    @staticmethod
    def _default_args(tool_name: str) -> dict:
        """ Tool çağrısı için varsayılan argümanlar. """
        if tool_name == "get_offer_context":
            return {"offer_id": "mock"}
        return {"product_id": "mock"}

    def _build_json(self) -> str:
        """ reason_codes ve decision'a göre AISummary uyumlu JSON üretir. """
        sentences = [_RC_SUMMARIES[rc] for rc in self.reason_codes if rc in _RC_SUMMARIES]
        summary = " ".join(sentences) or "Teklif değerlendirmesi tamamlandı."
        draft = None
        if self.decision == "NEED_APPROVAL" and self.reason_codes:
            rc_str = ", ".join(self.reason_codes)
            draft = (
                f"{rc_str} gerekçesiyle onay sürecine alınan teklif için "
                "yönetici onayı beklenmektedir."
            )
        return json.dumps(
            {"summary": summary[:500], "risk_notes": sentences, "approval_message_draft": draft},
            ensure_ascii=False,
        )


# LLM seçimi

def get_llm(settings: Settings, reason_codes: list[str], decision: str) -> BaseChatModel:
    """ Konfigürasyona göre LLM örneği döndürür. """
    if settings.llm_provider == "openai":
        return ChatOpenAI(
            model=settings.llm_model,
            api_key=settings.openai_api_key,
            timeout=settings.llm_timeout_seconds,
        )
    return MockToolCallingLLM(reason_codes=reason_codes, decision=decision)


# Tool'lar

def _build_tools(inp: AgentInput) -> list:
    """ AgentInput verisiyle kapatılmış (closure) tool fonksiyonları oluşturur. """
    stock_available = inp.stock_available
    stock_requested = inp.stock_requested
    list_price      = inp.list_price
    margin_pct      = inp.margin_pct
    threshold_pct   = inp.threshold_pct
    segment         = inp.segment
    offer_total     = inp.offer_total
    discount_pct    = inp.discount_pct

    @tool
    def get_stock_status(product_id: str) -> dict:
        """ Ürün stok durumunu döndürür. """
        return {
            "available": stock_available,
            "requested": stock_requested,
            "sufficient": stock_available >= stock_requested,
        }

    @tool
    def get_pricing_summary(product_id: str) -> dict:
        """  Ürün fiyat, marj ve eşik özetini döndürür. """
        return {
            "list_price":    list_price,
            "margin_pct":    round(margin_pct * 100, 1),
            "threshold_pct": round(threshold_pct * 100, 1),
        }

    @tool
    def get_offer_context(offer_id: str) -> dict:
        """ Teklif bağlam bilgisini döndürür — müşteri kimliği içermez. """
        if offer_total <= 50000:
            band = "düşük (≤ 50K TRY)"
        elif offer_total <= 100000:
            band = "orta (50K–100K TRY)"
        else:
            band = "yüksek (> 100K TRY)"
        return {"segment": segment, "amount_band": band, "discount_pct": discount_pct}

    return [get_stock_status, get_pricing_summary, get_offer_context]


# Tool calling döngüsü

def _run_tool_loop(
    llm_with_tools, messages: list[BaseMessage], tools_by_name: dict, max_iterations: int
) -> AIMessage:
    """ En fazla max_iterations tur döner. """
    last_response: AIMessage = AIMessage(content="")
    for _ in range(max_iterations):
        response = llm_with_tools.invoke(messages)
        messages.append(response)
        last_response = response
        if not getattr(response, "tool_calls", None):
            break
        for tc in response.tool_calls:
            tool_fn = tools_by_name.get(tc["name"])
            if tool_fn is None:
                logger.warning("Bilinmeyen tool çağrısı yok sayıldı", extra={"tool": tc["name"]})
                result = json.dumps({"hata": "bilinmeyen araç"})
            else:
                result = json.dumps(tool_fn.invoke(tc["args"]), ensure_ascii=False, default=str)
            messages.append(ToolMessage(content=result, tool_call_id=tc["id"]))
    return last_response


# Parse ve Guardrail'ler

def _parse_ai_output(content: str) -> AISummary:
    """ LLM çıktısını JSON parse eder ve AISummary şemasına doğrular. """
    try:
        clean = content.strip()
        if clean.startswith("```"):
            clean = re.sub(r"```(?:json)?\n?", "", clean).strip().rstrip("`")
        data = json.loads(clean)
        return AISummary.model_validate(data)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise _GuardrailError("JSON_PARSE_ERROR") from exc


def _check_numeric_consistency(
    summary: AISummary,
    margin_pct: float,
    threshold_pct: float,
    discount_pct: float,
) -> None:
    """
    AI çıktısındaki yüzde sayılarını karar motorunun hesapladığı değerlerle karşılaştırır.
    Onaylanan değer kümesi: marj yüzdesi, eşik yüzdesi, talep edilen indirim oranı.
    Tolerans: 0.1 puan. Kümede karşılığı olmayan tek bir sayı bile fallback'e düşürür.
    Bu kontrol, dil modellerinin sayısal halüsinasyon riskine karşı alınmış bir önlemdir.
    """
    combined = summary.summary + " " + " ".join(summary.risk_notes or [])
    if summary.approval_message_draft:
        combined += " " + summary.approval_message_draft

    number_patterns = [
        r'%\s*(\d+(?:[,.]\d+)?)',
        r'(\d+(?:[,.]\d+)?)\s*%',
        r'yüzde\s+(\d+(?:[,.]\d+)?)',
    ]
    found: list[float] = []
    for pat in number_patterns:
        for m in re.finditer(pat, combined, re.IGNORECASE):
            try:
                found.append(float(m.group(1).replace(',', '.')))
            except ValueError:
                pass

    if not found:
        return  # Sayısal yüzde ifadesi yok — geçti

    allowed = {round(margin_pct, 1), round(threshold_pct, 1), round(discount_pct, 1)}
    tolerance = 0.1

    for num in found:
        if not any(abs(num - a) <= tolerance for a in allowed):
            raise _GuardrailError("NUMERIC_MISMATCH")


def _run_guardrails(summary: AISummary, decision: str, margin_pct: float, threshold_pct: float,  discount_pct: float) -> AISummary:
    """ Tüm guardrail'leri sırayla çalıştırır. Herhangi biri başarısız olursa """

    # GR-1: Uzunluk
    if len(summary.summary) > 500:
        raise _GuardrailError("LENGTH_EXCEEDED")

    # GR-2: Karar tutarlılığı
    text = (summary.summary + " ".join(summary.risk_notes or [])).lower()
    if decision != "APPROVE":
        if any(p in text for p in ["onaylandı", "approved", "kabul edildi", "onaylanmıştır"]):
            raise _GuardrailError("DECISION_INCONSISTENCY")
    if decision != "REJECT":
        if any(p in text for p in ["reddedildi", "reddedilmiştir"]):
            raise _GuardrailError("DECISION_INCONSISTENCY")

    # GR-3: PII kontrolü — redact_text() metni değiştiriyorsa PII var
    combined = summary.summary + " ".join(summary.risk_notes or [])
    if summary.approval_message_draft:
        combined += " " + summary.approval_message_draft
    if redact_text(combined) != combined:
        raise _GuardrailError("PII_DETECTED")

    # GR-4: approval_message_draft temizleme (hata değil, otomatik düzeltme)
    if decision != "NEED_APPROVAL" and summary.approval_message_draft is not None:
        summary = AISummary(
            summary=summary.summary,
            risk_notes=summary.risk_notes,
            approval_message_draft=None,
        )

    # GR-5: Sayısal tutarlılık — AI'ın ürettiği yüzde sayıları yalnızca bilinen değerler olmalı.
    _check_numeric_consistency(summary, margin_pct * 100, threshold_pct * 100, discount_pct)

    return summary


#Fallback

def _build_fallback(reason_codes: list[str], decision: str) -> AISummary:
    """ Deterministik fallback özeti — AI kullanılamadığında döner. """
    sentences = [_RC_SUMMARIES.get(rc, "") for rc in reason_codes if rc in _RC_SUMMARIES]
    summary = " ".join(filter(None, sentences)) or "Teklif değerlendirmesi tamamlandı."
    draft = None
    if decision == "NEED_APPROVAL" and reason_codes:
        draft = (
            f"{', '.join(reason_codes)} gerekçesiyle onay sürecine alınan teklif için "
            "yönetici incelemesi beklenmektedir."
        )
    return AISummary(
        summary=summary[:500],
        risk_notes=list(filter(None, sentences)),
        approval_message_draft=draft,
    )


# Ana fonksiyon

def run_agent(agent_input: AgentInput, settings: Settings) -> tuple[AISummary, str]:
    """ AI katmanını çalıştırır; başarısızlıkta fallback döndürür. """
    if settings.llm_force_failure:
        logger.info("LLM_FORCE_FAILURE aktif — fallback döndürülüyor")
        return _build_fallback(agent_input.reason_codes, agent_input.decision), "FALLBACK"

    try:
        tools = _build_tools(agent_input)
        tools_by_name = {t.name: t for t in tools}

        llm = get_llm(settings, agent_input.reason_codes, agent_input.decision)
        llm_with_tools = llm.bind_tools(tools)

        # GÜVENLİK: build_ai_context yalnızca PII içermeyen, maliyet içermeyen bağlam döndürür.
        ai_context = build_ai_context(
            offer_id=agent_input.offer_id,
            segment=agent_input.segment,
            decision=agent_input.decision,
            reason_codes=agent_input.reason_codes,
            margin_pct=agent_input.margin_pct,
            threshold_pct=agent_input.threshold_pct,
            offer_total=agent_input.offer_total,
            stock_available=agent_input.stock_available,
            stock_requested=agent_input.stock_requested,
        )
        messages = build_messages(ai_context)

        logger.debug(
            "LLM'e giden mesajlar",
            extra={"messages": [{"type": m.type, "content": m.content} for m in messages]},
        )

        final_response = _run_tool_loop(llm_with_tools, messages, tools_by_name, settings.llm_max_tool_iterations)

        logger.debug("LLM'den gelen ham yanıt", extra={"content": str(final_response.content)})

        ai_summary = _parse_ai_output(str(final_response.content))
        ai_summary = _run_guardrails(
            ai_summary,
            agent_input.decision,
            agent_input.margin_pct,
            agent_input.threshold_pct,
            agent_input.discount_pct,
        )

        logger.info(
            "AI özeti üretildi",
            extra={"offer_id": agent_input.offer_id, "prompt_version": PROMPT_VERSION},
        )
        return ai_summary, "OK"

    except _GuardrailError as exc:
        # İhlal türü loglanır; AI çıktısının kendisi loglanmaz.
        logger.warning(
            "Guardrail ihlali — fallback kullanılıyor",
            extra={"violation_type": exc.violation_type, "offer_id": agent_input.offer_id},
        )
        return _build_fallback(agent_input.reason_codes, agent_input.decision), "FALLBACK"

    except Exception as exc:
        logger.error(
            "AI katmanı beklenmeyen hata — fallback kullanılıyor",
            extra={"error_type": type(exc).__name__, "offer_id": agent_input.offer_id},
        )
        return _build_fallback(agent_input.reason_codes, agent_input.decision), "FALLBACK"