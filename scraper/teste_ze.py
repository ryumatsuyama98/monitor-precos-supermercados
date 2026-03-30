"""
Teste Zé Delivery — injeta cookies de endereço antes de navegar.
Usa o formato exato dos cookies reais capturados do browser.
"""
import re, os, time, json, urllib.parse
from playwright.sync_api import sync_playwright

# Endereço SP no formato exato que o Zé Delivery usa nos cookies
USER_ADDRESS = urllib.parse.quote(json.dumps({
    "latitude": -23.58734437003894,
    "longitude": -46.682922566589006,
    "zipcode": "04538-132",
    "street": "Avenida Brigadeiro Faria Lima",
    "neighborhood": "Itaim Bibi",
    "city": "São Paulo",
    "province": "SP",
    "country": "BR",
    "number": "3500",
    "referencePoint": "",
    "type": {"displayName": "", "id": "HISTORIC"}
}), safe='')

DELIVERY_OPTIONS = urllib.parse.quote(json.dumps({
    "__typename": "DeliveryOption",
    "deliveryMethod": "DELIVERY",
    "address": {
        "__typename": "AddressOutput",
        "latitude": -23.58734437003894,
        "longitude": -46.682922566589006,
        "zipcode": "04538-132",
        "country": "BR",
        "province": "SP",
        "city": "São Paulo",
        "neighborhood": "Itaim Bibi",
        "street": "Avenida Brigadeiro Faria Lima",
        "number": "3500",
        "addressLine2": None,
        "referencePoint": ""
    }
}), safe='')

COOKIES_ENDERECO = [
    {"name": "userAddress",                 "value": USER_ADDRESS,          "domain": ".ze.delivery", "path": "/"},
    {"name": "deliveryOptions",             "value": DELIVERY_OPTIONS,      "domain": ".ze.delivery", "path": "/"},
    {"name": "deliveryMethod",              "value": "%22DELIVERY%22",       "domain": ".ze.delivery", "path": "/"},
    {"name": "isScheduledDeliveryAvailable","value": "false",                "domain": ".ze.delivery", "path": "/"},
    {"name": "ageGateAccepted",             "value": "true",                 "domain": ".ze.delivery", "path": "/"},
    {"name": "non_logged_split_id",         "value": "5989",                 "domain": ".ze.delivery", "path": "/"},
]

URLS_TESTE = [
    ("Heineken Lata 350ml",   "https://www.ze.delivery/entrega-produto/9991/heineken-350ml"),
    ("Skol Lata 350ml",       "https://www.ze.delivery/entrega-produto/8504/skol-350ml"),
    ("Antarctica Lata 350ml", "https://www.ze.delivery/entrega-produto/8522/antarctica-pilsen-350ml"),
]

def testar_url(page, nome, url):
    print(f"\n{'─'*50}\n🍺 {nome}")
    page.goto(url, wait_until="domcontentloaded", timeout=25000)
    page.wait_for_timeout(3000)
    print(f"   ✓ Título: {page.title()[:50]}")

    # Fecha modal
    for sel in ['[data-testid="close-button"]', '[class*="secondaryButton"]']:
        try:
            btn = page.query_selector(sel)
            if btn and btn.is_visible():
                btn.click()
                page.wait_for_timeout(1000)
                print("   ✓ Modal fechado")
                break
        except: pass

    # Verifica endereço na página
    addr = page.evaluate("""() => {
        const el = document.querySelector('[class*="DeliveryOptionsCard_address"]');
        return el ? el.textContent.trim() : null;
    }""")
    print(f"   📍 Endereço na página: {addr}")

    # Aguarda preço
    try:
        page.wait_for_selector('[data-testid="product-price"]', timeout=8000)
    except: pass

    preco = page.evaluate("""() => {
        const el = document.querySelector('[data-testid="product-price"]');
        if (el) return el.textContent.trim();
        const el2 = document.querySelector('[class*="priceText"]');
        if (el2) return el2.textContent.trim();
        return null;
    }""")

    if preco:
        nums = re.findall(r'\d+[.,]\d{2}', preco.replace('\xa0',''))
        if nums:
            p = float(nums[0].replace(',','.'))
            if 0.5 < p < 50:
                print(f"   ✅ PREÇO: R$ {p:.2f}")
                return
    print(f"   ❌ Preço não encontrado: {preco}")

def main():
    extra_cookies = os.environ.get("ZE_COOKIES", "")
    print("=" * 50)
    print("TESTE ZÉ DELIVERY — Cookies de endereço")
    print("=" * 50)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--no-sandbox","--disable-blink-features=AutomationControlled",
                  "--disable-dev-shm-usage","--disable-gpu"]
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
            viewport={"width": 1366, "height": 768},
            locale="pt-BR",
        )
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            window.chrome = {runtime: {}};
        """)

        # Injeta cookies de endereço
        context.add_cookies(COOKIES_ENDERECO)
        print(f"✓ {len(COOKIES_ENDERECO)} cookies de endereço injetados")

        # Injeta cookies do Imperva se disponíveis
        if extra_cookies:
            imperva = []
            for part in extra_cookies.split('; '):
                if any(k in part for k in ['visid_incap','nlbi_','incap_ses']):
                    name, _, value = part.strip().partition('=')
                    imperva.append({"name": name, "value": value, "domain": ".ze.delivery", "path": "/"})
            if imperva:
                context.add_cookies(imperva)
                print(f"✓ {len(imperva)} cookies Imperva injetados")

        page = context.new_page()

        for nome, url in URLS_TESTE:
            testar_url(page, nome, url)
            time.sleep(1)

        browser.close()

    print("\n" + "=" * 50)
    print("Teste concluído")

if __name__ == "__main__":
    main()
