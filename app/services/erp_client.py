""" ERP istemcisi — stok ve fiyat bilgilerini okur. """

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from app.exceptions import IntegrationError, ProductNotFoundError

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"

# PRD-ERR ile başlayan product_id'ler entegrasyon hatasını simüle eder.
_INTEGRATION_ERROR_PREFIX = "PRD-ERR"


class StockInfo(BaseModel):
    """ ERP'den gelen stok bilgisi. """
    product_id: str
    available: int


class PriceInfo(BaseModel):
    """ ERP'den gelen fiyat ve maliyet bilgisi. """

    product_id: str
    unit_cost: float
    unit_freight_cost: float = 0.0
    list_price: float
    brand: str | None = None


class ERPClient(Protocol):
    """ ERP istemci arayüzü (Protocol). """

    def get_stock(self, product_id: str) -> StockInfo:
        """ Ürün stok bilgisini döndürür. """
        ...

    def get_price(self, product_id: str) -> PriceInfo:
        """ Ürün fiyat ve maliyet bilgisini döndürür. """
        ...


class JsonERPClient:
    """ data/stock.json ve data/prices.json dosyalarından okuyan mock ERP istemcisi. """

    def __init__(self) -> None:
        """ Her iki JSON dosyasını yükler. """

        with (_DATA_DIR / "stock.json").open(encoding="utf-8") as f:
            raw_stock: dict = json.load(f)
        with (_DATA_DIR / "prices.json").open(encoding="utf-8") as f:
            raw_prices: dict = json.load(f)

        self._stock: dict[str, StockInfo] = {
            k: StockInfo.model_validate(v) for k, v in raw_stock.items()
        }
        self._prices: dict[str, PriceInfo] = {
            k: PriceInfo.model_validate(v) for k, v in raw_prices.items()
        }

        logger.info(
            "ERP verileri yüklendi",
            extra={"stok_kayit": len(self._stock), "fiyat_kayit": len(self._prices)},
        )

    def get_stock(self, product_id: str) -> StockInfo:
        """ Ürün stok bilgisini döndürür. """

        # Case: PRD-ERR ürünü ERP bağlantı hatasını simüle eder
        if product_id.startswith(_INTEGRATION_ERROR_PREFIX):
            raise IntegrationError("ERP", f"ürün sorgusu başarısız: {product_id}")

        stock = self._stock.get(product_id)
        if stock is None:
            raise ProductNotFoundError(product_id)
        return stock

    def get_price(self, product_id: str) -> PriceInfo:
        """ Ürün fiyat ve maliyet bilgisini döndürür. """

        if product_id.startswith(_INTEGRATION_ERROR_PREFIX):
            raise IntegrationError("ERP", f"fiyat sorgusu başarısız: {product_id}")

        price = self._prices.get(product_id)
        if price is None:
            raise ProductNotFoundError(product_id)
        return price


@lru_cache
def _erp_singleton() -> JsonERPClient:
    """JSON dosyalarını yalnızca bir kez yükler."""
    return JsonERPClient()


def get_erp_client() -> ERPClient:
    """ FastAPI dependency fonksiyonu. """
    return _erp_singleton()