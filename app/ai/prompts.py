""" AI katmanı prompt şablonları. """
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

PROMPT_VERSION = "1.1"

# Sistem mesajı: LLM'in rolünü ve kısıtlarını tanımlar.
_SYSTEM_TEMPLATE = """\
Sen bir teklif değerlendirme asistanısın. Görevin, satış ekibine sunulmak üzere \
teklif kararının Türkçe gerekçesini hazırlamaktır.

ZORUNLU KURALLAR:
1. Karar zaten verilmiştir. Bu kararı ASLA değiştirme, yalnızca açıkla.
2. Müşteri adı, e-posta, telefon veya kimlik bilgisi KULLANMA.
3. Sayısal değer kullanacaksan yalnızca sana verilen marj yüzdesi, eşik \
yüzdesi ve indirim oranını kullanabilirsin; başka sayı üretemezsin.
4. Yanıtını yalnızca aşağıdaki JSON formatında ver, başka metin ekleme:

{{
  "summary": "Kararın Türkçe özeti — maksimum 500 karakter",
  "risk_notes": ["risk notu 1", "risk notu 2"],
  "approval_message_draft": "Onay mesajı taslağı (yalnızca NEED_APPROVAL kararında, diğerlerinde null)"
}}
"""

# İnsan mesajı: AI context sözlüğündeki alanlar buraya yerleştirilir.
_HUMAN_TEMPLATE = """\
Teklif Değerlendirme Bağlamı:
- Teklif ID   : {offer_id}
- Segment     : {segment}
- Karar       : {decision}
- Gerekçe     : {reason_codes}
- Marj        : %{margin_pct}  (uygulanan eşik: %{threshold_pct})
- Tutar Bandı : {amount_band}
- Stok Durumu : {stock_status}

Ek bilgiye ihtiyaç duyarsan araçları kullanabilirsin. \
Ardından yukarıdaki JSON formatında yanıt ver.\
"""


def build_messages(ai_context: dict) -> list[BaseMessage]:
    """ AI bağlamından prompt mesajlarını oluşturur. """
    human_content = _HUMAN_TEMPLATE.format(**ai_context)
    return [
        SystemMessage(content=_SYSTEM_TEMPLATE),
        HumanMessage(content=human_content),
    ]