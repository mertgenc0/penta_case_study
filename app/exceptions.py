""" Uygulama özel hata sınıfları. """


class OfferNotFoundError(Exception):
    """ CRM'de teklif kaydı bulunamadığında. """
    def __init__(self, offer_id: str) -> None:
        self.offer_id = offer_id
        super().__init__(f"Teklif bulunamadı: {offer_id}")


class ProductNotFoundError(Exception):
    """ ERP'de ürün kaydı bulunamadığında. """
    def __init__(self, product_id: str) -> None:
        self.product_id = product_id
        super().__init__(f"Ürün bulunamadı: {product_id}")


class IntegrationError(Exception):
    """ CRM veya ERP servisine erişilemediğinde fırlatılır. """

    def __init__(self, service: str, detail: str = "") -> None:
        self.service = service
        self.detail = detail
        message = f"{service} servisine erişilemiyor"
        if detail:
            message += f": {detail}"
        super().__init__(message)