""" Güvenli JSON loglama yapısı. """

import json
import re
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.core.pii import mask_email, mask_phone

# E-posta ve telefon: mask_email/mask_phone biçimlendirir (ilk/son karakter açık kalır,
# "bu bir e-postaydı" bilgisi debug için korunur).
# TCKN ve IBAN: kısmi maskeleme bile brute force ipucu verebilir; tamamen kaldırılır.
_PII_PATTERNS: list[tuple[re.Pattern[str], str | Callable[[re.Match[str]], str]]] = [
    (re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", re.IGNORECASE),
     lambda m: mask_email(m.group(0))),
    (re.compile(r"(\+90[\s\-]?|0)5\d{2}[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}"),
     lambda m: mask_phone(m.group(0))),
    (re.compile(r"\b[1-9]\d{10}\b"), "[tckn gizlendi]"),
    (re.compile(r"\bTR\d{2}[\s]?(\d{4}[\s]?){5}\d{2}\b", re.IGNORECASE), "[iban gizlendi]"),
]

def redact_text(text: str) -> str:
    """ Metinde bilinen PII kalıplarını gizler. """
    for pattern, replacement in _PII_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class PIIRedactionFilter(logging.Filter):
    """ Tüm log kayıtlarının mesaj ve extra alanlarına PII temizleme uygular. """

    def filter(self, record: logging.LogRecord) -> bool:
        """ Log kaydını yerinde değiştirir; PII içeriyorsa temizler. """
        # Aynı record birden fazla handler tarafından işlenirken filter'ın
        # iki kez çalışıp maskeli çıktıyı yeniden maskelemesini önle.
        if getattr(record, "_pii_redacted", False):
            return True
        try:
            formatted = record.getMessage()
        except Exception:
            formatted = str(record.msg)
        record.msg = redact_text(formatted)
        record.args = None
        record._pii_redacted = True
        return True

# LogRecord'un standart dahili alanları — JSON çıktısına tekrar eklenmez
_STANDARD_ATTRS: frozenset[str] = frozenset({
    "args", "created", "exc_info", "exc_text", "filename", "funcName",
    "levelname", "levelno", "lineno", "message", "module", "msecs",
    "msg", "name", "pathname", "process", "processName", "relativeCreated",
    "stack_info", "thread", "threadName", "taskName",
})

class JsonFormatter(logging.Formatter):
    """ Log kayıtlarını tek satır JSON olarak biçimlendirir. """

    def format(self, record: logging.LogRecord) -> str:
        """ LogRecord'u JSON satırına dönüştürür. """

        record.message = record.getMessage()
        entry: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.message,
        }
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                entry[key] = value
        return json.dumps(entry, ensure_ascii=False, default=str)

def setup_logging(log_level: str = "INFO") -> None:
    """ Uygulama genelinde loglama altyapısını kurar. """

    level = getattr(logging, log_level.upper(), logging.INFO)

    Path("logs").mkdir(exist_ok=True)

    pii_filter = PIIRedactionFilter()
    json_formatter = JsonFormatter()

    # stdout handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(json_formatter)
    console_handler.addFilter(pii_filter)

    # Dosya handler
    file_handler = logging.FileHandler("logs/app.log", encoding="utf-8")
    file_handler.setFormatter(json_formatter)
    file_handler.addFilter(pii_filter)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Var olan handler'ları temizle
    root_logger.handlers.clear()
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)


_AUDIT_PATH = Path("logs/audit.jsonl")


def write_audit(
    transaction_id: str,
    offer_id: str,
    customer_ref: str,
    decision: str,
    reason_codes: list[str],
    rule_version: str,
    ai_status: str,
    prompt_version: str,
    latency_ms: float,
    http_status: int,
    error_code: str | None = None,
    margin_pct: float | None = None,
) -> None:
    """ Audit kaydını logs/audit.jsonl dosyasına ekler.. """

    entry = {
        "timestamp":      datetime.now(timezone.utc).isoformat(),
        "transaction_id": transaction_id,
        "offer_id":       offer_id,
        "customer_ref":   customer_ref,
        "decision":       decision,
        "reason_codes":   reason_codes,
        "rule_version":   rule_version,
        "ai_status":      ai_status,
        "prompt_version": prompt_version,
        "latency_ms":     round(latency_ms, 2),
        "http_status":    http_status,
        "error_code":     error_code,
        "margin_pct":     round(margin_pct * 100, 2) if margin_pct is not None else None,
    }
    # append modunda açılır — her satır bir istek kaydı (JSONL formatı)
    _AUDIT_PATH.parent.mkdir(exist_ok=True)
    with _AUDIT_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")