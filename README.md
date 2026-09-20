# Teklif Ön Değerlendirme API

Teknoloji distribütörü bayilerinin teklif taleplerini değerlendiren FastAPI servisi.
Deterministik kural motoru `APPROVE`, `REJECT` veya `NEED_APPROVAL` kararını verir;
LangChain tabanlı AI katmanı yalnızca kararın Türkçe gerekçesini özetler.

---

## Kurulum

**Gereksinimler:** Python 3.11+

```bash
# 1. Sanal ortam
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 2. Bağımlılıklar
pip install -r requirements.txt

# 3. Ortam değişkenleri
cp .env.example .env
```

> Gerçek OpenAI kullanmak için `.env` içinde `LLM_PROVIDER=openai` ve `OPENAI_API_KEY=sk-...` satırlarını düzenleyin.
> Varsayılan (`LLM_PROVIDER=mock`) API anahtarı gerektirmez.

---

## Çalıştırma

```bash
uvicorn app.main:app --reload
```

Servis `http://localhost:8000` adresinde başlar.
Swagger UI: `http://localhost:8000/docs`

---

## Testler

```bash
pytest -q
```

66 test, ağ erişimi veya gerçek LLM çağrısı yapılmaz.

---

## Konfigürasyon

| Değişken | Varsayılan | Açıklama |
|---|---|---|
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `PII_HASH_SALT` | `default-salt-change-me` | Audit'te müşteri pseudonymization için HMAC salt — **değiştirin** |
| `MARGIN_THRESHOLD` | `0.08` | Varsayılan marj eşiği; marka eşiği yoksa kullanılır |
| `HIGH_VALUE_THRESHOLD` | `100000` | Tutar eşiği (TRY); üzerinde `HIGH_VALUE_OFFER` tetiklenir |
| `STRATEGIC_DISCOUNT_THRESHOLD` | `15` | Stratejik bayi indirim eşiği (%) |
| `INCLUDE_MARGIN_DETAILS` | `true` | `false` yapılırsa `margin_pct` API yanıtında yer almaz |
| `LLM_PROVIDER` | `mock` | `mock` veya `openai` |
| `OPENAI_API_KEY` | — | `LLM_PROVIDER=openai` için gerekli |
| `LLM_MODEL` | `gpt-4o-mini` | Kullanılacak OpenAI modeli |
| `LLM_TIMEOUT_SECONDS` | `10` | LLM istek zaman aşımı |
| `LLM_MAX_TOOL_ITERATIONS` | `3` | Tool calling döngüsü maksimum tur sayısı |
| `LLM_FORCE_FAILURE` | `false` | `true` yapılırsa AI katmanı kasıtlı hata verir (fallback testi) |

---

## Karar Tablosu

| # | Kural | Reason Code | Karar |
|---|---|---|---|
| 1 | ERP stoğu 0 | `OUT_OF_STOCK` | REJECT |
| 2 | 0 < stok < istek adedi | `INSUFFICIENT_STOCK` | NEED_APPROVAL |
| 3 | İstek ile CRM kaydı uyuşmuyor | `DATA_INCONSISTENCY` | NEED_APPROVAL |
| 4 | Marj < eşik (bkz. marj eşik tablosu) | `LOW_MARGIN` | NEED_APPROVAL |
| 5 | offer_total > `HIGH_VALUE_THRESHOLD` | `HIGH_VALUE_OFFER` | NEED_APPROVAL |
| 6 | segment=strategic_partner ve indirim > `STRATEGIC_DISCOUNT_THRESHOLD` | `HIGH_DISCOUNT_STRATEGIC` | NEED_APPROVAL |
| — | Hiçbiri tetiklenmedi | — | APPROVE |

Öncelik: **REJECT > NEED_APPROVAL > APPROVE**. Eşitlik hiçbir kuralı tetiklemez (`<` ve `>` kullanılır).

### Marj Formülü

```
marj = (offer_total − (unit_cost + unit_freight_cost) × quantity) / offer_total
```

Nakliye maliyeti ürün başına hesaplanıp toplam miktara çarpılır; bu maliyet de marjı düşürür.

### Marj Eşik Tablosu

Eşik arama sırası: **marka → `MARGIN_THRESHOLD`** (varsayılan).

> **Not:** Aşağıdaki değerler kurgusal örnektir; gerçek tedarikçi anlaşmalarını veya pazar
> koşullarını yansıtmaz. Üretim ortamında şirket politikasına göre belirlenmelidir.

**Marka bazlı eşikler:**

| Marka | Marj Eşiği |
|---|---|
| apple | %3 |
| lenovo | %7 |
| hp | %7 |
| huawei | %9 |
| microsoft | %10 |

Eşiğin hangi seviyeden geldiği API yanıtında yer almaz; yalnızca DEBUG loguna yazılır.

---

## API Referansı

### POST /evaluate-offer

**İstek:**

```json
{
  "offer_id": "OFF-10023",
  "customer": {
    "name": "ABC Teknoloji",
    "email": "buyer@example.com"
  },
  "product_id": "PRD-445",
  "quantity": 8,
  "requested_discount": 12,
  "offer_total": 125000,
  "customer_segment": "strategic_partner"
}
```

**Doğrulama kuralları:** `quantity > 0`, `0 ≤ requested_discount ≤ 100`, `offer_total > 0`,
`customer_segment ∈ {standard, gold, strategic_partner}`, `email` opsiyonel ama verilirse geçerli format.

**Yanıt (200):**

```json
{
  "offer_id": "OFF-10023",
  "transaction_id": "29731dc8-fa97-4b03-a4fe-95dc71e351f8",
  "decision": "NEED_APPROVAL",
  "reason_codes": ["LOW_MARGIN", "HIGH_VALUE_OFFER"],
  "ai_summary": "Teklif marjı minimum karlılık eşiğinin altında kalmaktadır. Yüksek tutarlı bu teklif standart onay limitini aşmaktadır.",
  "ai_status": "OK",
  "approval_required": true,
  "approval_message_draft": "LOW_MARGIN, HIGH_VALUE_OFFER gerekçesiyle onay sürecine alınan teklif için yönetici onayı beklenmektedir.",
  "recommended_actions": [
    "Fiyatlandırma gözden geçirilmeli veya maliyet optimizasyonu yapılmalı.",
    "Yüksek tutarlı teklif için üst yönetim onayı gerekli."
  ],
  "rule_version": "2026.1",
  "pii_masked": true,
  "margin_pct": 4.32
}
```

`margin_pct` alanı `INCLUDE_MARGIN_DETAILS=false` iken yanıtta yer almaz.

**Hata yanıtı:**

```json
{
  "error_code": "VALIDATION_ERROR",
  "message": "İstek doğrulama hatası",
  "details": [{"field": "body -> quantity", "message": "Input should be greater than 0"}],
  "transaction_id": "37d07bdb-bfda-4285-bd58-2f3245d99f6a"
}
```

| HTTP | error_code | Durum |
|---|---|---|
| 422 | `VALIDATION_ERROR` | Eksik/hatalı alan |
| 404 | `OFFER_NOT_FOUND` | Teklif CRM'de yok |
| 404 | `PRODUCT_NOT_FOUND` | Ürün ERP'de yok |
| 503 | `INTEGRATION_UNAVAILABLE` | CRM/ERP erişilemiyor |
| 500 | `INTERNAL_ERROR` | Beklenmeyen hata |

AI hatası durumunda HTTP 200 döner, `ai_status: "FALLBACK"` olur.

### GET /health

```json
{"status": "ok", "service": "teklif-degerlendirme-api"}
```

---

## cURL Örnekleri

### Senaryo 1 — Normal teklif (APPROVE)

```bash
curl -s -X POST http://localhost:8000/evaluate-offer \
  -H "Content-Type: application/json" \
  -d '{
    "offer_id": "OFF-10001",
    "customer": {"name": "Hızlı Bilişim A.Ş.", "email": "satis@hizlibilisim.com"},
    "product_id": "PRD-100",
    "quantity": 5,
    "requested_discount": 5,
    "offer_total": 75000,
    "customer_segment": "gold"
  }' | python -m json.tool
```

**Yanıt:** `decision: "APPROVE"`, `reason_codes: []`
Marj = (75000 − (10000+300)×5) / 75000 = **%31.3** > %7 (hp eşiği)

---

### Senaryo 2 — Stok yok (REJECT)

```bash
curl -s -X POST http://localhost:8000/evaluate-offer \
  -H "Content-Type: application/json" \
  -d '{
    "offer_id": "OFF-10002",
    "customer": {"name": "Doğu Teknoloji Ltd.", "email": "teklif@dogutekno.com"},
    "product_id": "PRD-200",
    "quantity": 3,
    "requested_discount": 8,
    "offer_total": 30000,
    "customer_segment": "standard"
  }' | python -m json.tool
```

**Yanıt:** `decision: "REJECT"`, `reason_codes: ["OUT_OF_STOCK"]`

---

### Senaryo 3 — Yetersiz stok (NEED_APPROVAL)

```bash
curl -s -X POST http://localhost:8000/evaluate-offer \
  -H "Content-Type: application/json" \
  -d '{
    "offer_id": "OFF-10003",
    "customer": {"name": "Batı Sistem Tic.", "email": "info@batisistem.com"},
    "product_id": "PRD-300",
    "quantity": 5,
    "requested_discount": 6,
    "offer_total": 40000,
    "customer_segment": "standard"
  }' | python -m json.tool
```

**Yanıt:** `decision: "NEED_APPROVAL"`, `reason_codes: ["INSUFFICIENT_STOCK"]`

---

### Senaryo 4 — Düşük marj (NEED_APPROVAL)

```bash
curl -s -X POST http://localhost:8000/evaluate-offer \
  -H "Content-Type: application/json" \
  -d '{
    "offer_id": "OFF-10004",
    "customer": {"name": "Merkez Dağıtım A.Ş.", "email": "satin@merkezdag.com"},
    "product_id": "PRD-400",
    "quantity": 3,
    "requested_discount": 5,
    "offer_total": 20000,
    "customer_segment": "standard"
  }' | python -m json.tool
```

**Yanıt:** `decision: "NEED_APPROVAL"`, `reason_codes: ["LOW_MARGIN"]`
Marj = (20000 − (6200+100)×3) / 20000 = **%5.5** < %10 (microsoft eşiği)

---

### Senaryo 5 — Case örneği (LOW_MARGIN + HIGH_VALUE_OFFER)

```bash
curl -s -X POST http://localhost:8000/evaluate-offer \
  -H "Content-Type: application/json" \
  -d '{
    "offer_id": "OFF-10023",
    "customer": {"name": "ABC Teknoloji", "email": "buyer@example.com"},
    "product_id": "PRD-445",
    "quantity": 8,
    "requested_discount": 12,
    "offer_total": 125000,
    "customer_segment": "strategic_partner"
  }' | python -m json.tool
```

**Yanıt:**
```json
{
  "offer_id": "OFF-10023",
  "transaction_id": "29731dc8-fa97-4b03-a4fe-95dc71e351f8",
  "decision": "NEED_APPROVAL",
  "reason_codes": ["LOW_MARGIN", "HIGH_VALUE_OFFER"],
  "ai_summary": "Teklif marjı minimum karlılık eşiğinin altında kalmaktadır. Yüksek tutarlı bu teklif standart onay limitini aşmaktadır.",
  "ai_status": "OK",
  "approval_required": true,
  "approval_message_draft": "LOW_MARGIN, HIGH_VALUE_OFFER gerekçesiyle onay sürecine alınan teklif için yönetici onayı beklenmektedir.",
  "recommended_actions": [
    "Fiyatlandırma gözden geçirilmeli veya maliyet optimizasyonu yapılmalı.",
    "Yüksek tutarlı teklif için üst yönetim onayı gerekli."
  ],
  "rule_version": "2026.1",
  "pii_masked": true,
  "margin_pct": 4.32
}
```

Marj = (125000 − (14700+250)×8) / 125000 = **%4.32** < %7 (lenovo eşiği) ve 125000 > 100.000 eşiği.

---

### Senaryo 6 — Entegrasyon hatası (503)

```bash
curl -s -X POST http://localhost:8000/evaluate-offer \
  -H "Content-Type: application/json" \
  -d '{
    "offer_id": "OFF-10099",
    "customer": {"name": "Test Firma", "email": "test@testfirma.com"},
    "product_id": "PRD-ERR",
    "quantity": 1,
    "requested_discount": 0,
    "offer_total": 10000,
    "customer_segment": "standard"
  }' | python -m json.tool
```

**Yanıt (503):**
```json
{
  "error_code": "INTEGRATION_UNAVAILABLE",
  "message": "ERP servisine erişilemiyor",
  "details": null,
  "transaction_id": "0fa824ce-c2d5-48e4-b751-c776b446c756"
}
```

---

### Hata örneği — Doğrulama hatası (422)

```bash
curl -s -X POST http://localhost:8000/evaluate-offer \
  -H "Content-Type: application/json" \
  -d '{
    "offer_id": "OFF-10023",
    "customer": {"name": "ABC Teknoloji"},
    "product_id": "PRD-445",
    "quantity": -1,
    "requested_discount": 12,
    "offer_total": 125000,
    "customer_segment": "strategic_partner"
  }' | python -m json.tool
```

**Yanıt (422):**
```json
{
  "error_code": "VALIDATION_ERROR",
  "message": "İstek doğrulama hatası",
  "details": [{"field": "body -> quantity", "message": "Input should be greater than 0"}],
  "transaction_id": "37d07bdb-bfda-4285-bd58-2f3245d99f6a"
}
```

---

### AI fallback testi

```bash
# .env dosyasında LLM_FORCE_FAILURE=true yapın, sunucuyu yeniden başlatın
curl -s -X POST http://localhost:8000/evaluate-offer \
  -H "Content-Type: application/json" \
  -d '{"offer_id":"OFF-10023","customer":{"name":"ABC Teknoloji","email":"buyer@example.com"},"product_id":"PRD-445","quantity":8,"requested_discount":12,"offer_total":125000,"customer_segment":"strategic_partner"}' \
  | python -m json.tool
# Yanıtta ai_status: "FALLBACK", decision yine "NEED_APPROVAL"
```

---

## Gerçek LLM'e Geçiş

```bash
pip install langchain-openai
```

`.env` dosyasında:
```
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
LLM_MODEL=gpt-4o-mini
```

Sunucuyu yeniden başlat. Kod değişikliği gerekmez; `get_llm()` sağlayıcıyı şeffaf şekilde seçer.

---

## Mock Veri

| offer_id | product_id | Marka | Kategori | Senaryo |
|---|---|---|---|---|
| OFF-10001 | PRD-100 | hp | hardware | Normal teklif → APPROVE |
| OFF-10002 | PRD-200 | apple | hardware | Stok 0 → REJECT |
| OFF-10003 | PRD-300 | lenovo | hardware | Stok yetersiz → NEED_APPROVAL |
| OFF-10004 | PRD-400 | microsoft | software_license | Düşük marj → NEED_APPROVAL |
| OFF-10023 | PRD-445 | lenovo | hardware | Case örneği → NEED_APPROVAL |
| OFF-10099 | PRD-ERR | — | — | ERP hatası → 503 |

---

## Varsayımlar ve Tasarım Kararları

- **Kararı AI veremez.** `AISummary` şemasında `decision` alanı yoktur; AI kararı açıklar, değiştiremez.
- **AI hatası HTTP 200'ü durdurmaz.** LLM timeout veya geçersiz çıktı verirse deterministik fallback özet döner, iş süreci devam eder.
- **Eşitlik kural tetiklemez.** `margin == eşik` `LOW_MARGIN` üretmez; `offer_total == 100000` `HIGH_VALUE_OFFER` üretmez. Sınır değerler bu şekilde tanımlanmıştır.
- **İki katmanlı PII koruması.**
  - **Veri minimizasyonu:** Prompt'a giden bağlamda ve API yanıtında müşteri adı, e-posta veya telefon bulunmaz; `build_ai_context()` bunu yapısal olarak engeller.
  - **Log maskeleme:** `utils/logger.py` içindeki `PIIRedactionFilter` regex ile eşleşmeleri yakalar; e-posta ve telefonu `core/pii.py` içindeki `mask_email` / `mask_phone` fonksiyonlarına devreder (örn. `buyer@example.com` → `b***r@example.com`, `+90 532 123 45 67` → `+90 *** *** ** 67`). TCKN ve IBAN kısmi maskeleme brute force ipucu verebileceği için tamamen kaldırılır. Regex yakalama tarafı logger'da, biçimlendirme tarafı pii modülünde kalır; aynı mantık iki yerde tekrar etmez.
  - **Audit kaydı:** Müşteri referansı `pseudonymize()` (HMAC-SHA256 + salt) ile hash'lenerek `customer_ref` alanına yazılır; ham kişisel veri audit satırına düşmez. İsim için ayrı bir maskeleme fonksiyonu tutulmaz — serbest metin olduğundan güvenilir regex tetikleyicisi yoktur ve pseudonymize bu ihtiyacı zaten karşılar.
- **Mock veri JSON dosyalarında.** Gerçek dağıtımda CRM/ERP istemcileri yalnızca `Protocol` arayüzünü uygulayan yeni sınıflarla değiştirilir; route kodu değişmez.
- **AI katmanına yalnızca kararı açıklamak için gereken veriler iletilir.** Birim maliyet (`unit_cost`) ve nakliye maliyeti (`unit_freight_cost`), marj zaten hesaplanmış olduğundan prompt'a dahil edilmez.
- **AI çıktısındaki sayısal değerler doğrulanır.** AI özetindeki yüzde ifadeleri, karar motorunun hesapladığı marj, eşik ve indirim oranıyla karşılaştırılır. Uyuşmayan bir değer tespit edildiğinde AI çıktısı reddedilir ve deterministik özet kullanılır. Bu kontrol, dil modellerinin sayısal halüsinasyon riskine karşı alınmış bir önlemdir.
- **`INCLUDE_MARGIN_DETAILS` bayrağı**, rol tabanlı erişim eklendiğinde marj detayının yönetici rolüne açılacağı bağlantı noktasıdır. Sektör pratiğinde marj ve maliyet bilgisi satış danışmanı seviyesinde görünmez; ancak kimlik doğrulama bu çalışmanın kapsamı dışında olduğundan alan varsayılan olarak açık bırakılmıştır.
- **Yetkilendirme (RBAC) ve kimlik doğrulama** kapsam dışıdır; API gateway katmanında ele alınmalıdır.
- **LangGraph kullanılmadı.** Akış doğrusal ve öngörülebilir; `_run_tool_loop()` ile yönetilen basit döngü yeterlidir.
- **Para birimi TRY.** Tüm tutar eşikleri Türk Lirası cinsindendir.
- **`HIGH_VALUE_THRESHOLD` varsayılanı 100.000 TL'dir.** Sektörde bu eşik 1.500.000 TL seviyesindedir; varsayılan, case metnindeki örnek isteğin beklenen yanıtı üretebilmesi için düşük tutulmuştur ve `.env` üzerinden değiştirilebilir.
- **Marka marj eşikleri kurgusal örnektir.** Gerçek tedarikçi anlaşmalarını veya pazar koşullarını yansıtmaz; üretim ortamında şirket politikasına göre belirlenmelidir.
- **Müşteri segmentleri** case metnindeki örnek istek ve Durum 4 ile uyumlu kalacak şekilde korunmuştur. Sektörde segmentasyon genellikle bayi, kurumsal ve kamu şeklinde yapılmakta ve her segmentin kendine özgü fiyatlandırma ile onay kuralları bulunmaktadır. Kamu segmenti ihale mevzuatına tabi olduğundan ayrı bir kural seti gerektirir ve bu çalışmanın kapsamı dışında bırakılmıştır. Segment listesinin genişletilmesi, karar motorundaki segment bazlı kuralın mevcut yapısı korunarak yapılabilir.
- **Kamu ihale mevzuatına özel kurallar kapsam dışıdır.**

---

## AI Katmanı — Değerlendirme Notu

**Amaç.** Yapay zekâ katmanı, karar motorunun ürettiği deterministik sonucu (`decision` + `reason_codes`) satış danışmanına ve onay merciine anlaşılır bir dilde açıklamak için kullanılır. Somut çıktılar: (1) kararın Türkçe gerekçe özeti (`ai_summary`), (2) NEED_APPROVAL durumunda onay merciine iletilecek kısa mesaj taslağı (`approval_message_draft`), (3) LLM'in tool calling ile ERP/CRM verilerini doğrulamasına imkân veren bağlam.

**Kapsam.** AI şu işleri yapar: karar gerekçesini özetler, riskleri kısa notlar hâlinde vurgular, onay mesajı taslağı hazırlar, tool çağrılarıyla stok/fiyat/teklif meta verisini bağlama alır. Aksiyon önerileri (`recommended_actions`) ise **deterministik** olarak `reason_codes`'a göre üretilir; AI bu alanı yazmaz.

**Sınırlamalar.**
- **Kararı AI veremez.** `AISummary` şemasında `decision` alanı bulunmaz. Karar yalnızca kural motoru tarafından verilir; AI çıktısı kararı değiştiremez.
- **Sayısal halüsinasyon riski guardrail ile kontrol edilir.** AI özetindeki yüzde ifadeleri (marj, eşik, indirim) karar motorunun hesapladığı değerlerle karşılaştırılır; uyuşmazsa çıktı reddedilir ve deterministik fallback özet kullanılır (`ai_status: "FALLBACK"`, HTTP 200 korunur).
- **Tool çağrıları whitelisted ve limitlidir.** AI ancak önceden tanımlı ERP/CRM okuma araçlarını çağırabilir; iterasyon `LLM_MAX_TOOL_ITERATIONS` ile sınırlıdır. Yazma ya da dış sistem erişimi yoktur.
- **Sağlayıcı bağımsızdır.** `LLM_PROVIDER=mock` varsayılan olarak deterministik bir sahte model kullanır; `openai`'ye geçmek için yalnızca `.env` değişikliği yeterlidir, kod değişmez.
- **Timeout ve hata toleransı.** LLM yavaşlar veya hata verirse (`LLM_TIMEOUT_SECONDS`, ağ hatası, geçersiz JSON) iş süreci durmaz; fallback devreye girer.

**Kişisel veri koruma.**
- **Veri minimizasyonu.** `build_ai_context()` prompt'a giden bağlamı yapısal olarak sınırlar: müşteri adı, e-posta veya telefon prompt'a **girmez**. AI'a yalnızca karar için gereken metrikler (segment, karar, gerekçe kodları, marj/eşik yüzdesi, tutar bandı, stok durumu) iletilir.
- **Log maskeleme.** `PIIRedactionFilter` tüm log kayıtlarını regex ile tarar; e-posta ve telefon `mask_email`/`mask_phone` üzerinden biçimlendirilir (`b***r@example.com`, `+90 *** *** ** 67`). TCKN ve IBAN tamamen kaldırılır.
- **Audit pseudonymization.** Denetim kaydında müşteri referansı `pseudonymize()` (HMAC-SHA256 + salt) ile hash'lenerek yazılır; ham kişisel veri audit satırına düşmez.
- **API yanıtında `pii_masked: true`** bayrağı, yanıtta kişisel veri bulunmadığını tüketicilere açıkça bildirir.