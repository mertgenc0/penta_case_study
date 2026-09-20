""" CRM istemcisi — teklif kayıtlarını okur. """
import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from app.exceptions import OfferNotFoundError
from app.schemas import Customer, CustomerSegment

logger = logging.getLogger(__name__)


_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"


class OfferRecord(BaseModel):
    """ CRM'deki teklif kaydı. """

    offer_id: str
    customer: Customer
    product_id: str
    quantity: int
    requested_discount: float
    offer_total: float
    customer_segment: CustomerSegment


class CRMClient(Protocol):
    """ CRM istemci arayüzü (Protocol). """

    def get_offer(self, offer_id: str) -> OfferRecord:
        """ Teklif kaydını getirir. """
        ...


class JsonCRMClient:
    """ data/offers.json dosyasından okuyan mock CRM istemcisi. """

    def __init__(self) -> None:
        """ JSON dosyasını yükler; uygulama başlangıcında bir kez çalışır. """

        offers_path = _DATA_DIR / "offers.json"

        with offers_path.open(encoding="utf-8") as f:
            raw: dict = json.load(f)

        # Ham dict'i doğrulayarak OfferRecord nesnelerine dönüştür
        self._offers: dict[str, OfferRecord] = {
            key: OfferRecord.model_validate(value) for key, value in raw.items()
        }
        logger.info("CRM verileri yüklendi", extra={"kayit_sayisi": len(self._offers)})

    def get_offer(self, offer_id: str) -> OfferRecord:
        """ Teklif kaydını döndürür; bulunamazsa OfferNotFoundError fırlatır. """

        record = self._offers.get(offer_id)
        if record is None:
            raise OfferNotFoundError(offer_id)
        return record


@lru_cache
def _crm_singleton() -> JsonCRMClient:
    """ JSON dosyasını yalnızca bir kez yükler; uygulama boyunca aynı örneği döndürür. """
    return JsonCRMClient()


def get_crm_client() -> CRMClient:
    """ FastAPI dependency fonksiyonu. """
    return _crm_singleton()