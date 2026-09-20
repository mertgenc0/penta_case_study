""" Uygulama konfigürasyonu. """

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """ Tüm uygulama ayarları tek sınıfta."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore" #Tanımsız env değişkenlerini yoksay — eksik .env'de hata vermez.
    )
    log_level: str = "INFO"

    # PII pseudonymization için HMAC-SHA256 salt değeri
    pii_hash_salt: str = "default-salt-change-me"

    # Karar motoru eşikleri. Marka ve kategori bazlı eşikler için bkz. BRAND_MARGIN_THRESHOLDS.
    margin_threshold: float = 0.08
    high_value_threshold: float = 100000.0
    strategic_discount_threshold: float = 15.0

    #AI konfigürasyonu
    llm_provider: str = "mock"
    openai_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    llm_timeout_seconds: int = 10
    llm_max_tool_iterations: int = 3

    # true iken AI katmanı kasıtlı hata fırlatır; fallback yolunu test için
    llm_force_failure: bool = False

    # Varsayılan True: satış danışmanı marjı görebilir; erişim kısıtlaması API gateway'e bırakılmıştır.
    include_margin_details: bool = True

@lru_cache()
def get_settings() -> Settings:
    """ Singleton ayar nesnesi döndürür. """
    return Settings()


# ortamında şirket politikasına göre belirlenmeli ve güncellenmeli.
BRAND_MARGIN_THRESHOLDS: dict[str, float] = {
    "apple":     0.03,
    "lenovo":    0.07,
    "hp":        0.07,
    "huawei":    0.09,
    "microsoft": 0.10,
}

