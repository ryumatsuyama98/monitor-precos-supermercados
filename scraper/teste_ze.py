"""
Teste isolado do Zé Delivery com cookies reais via env var ZE_COOKIES.
Uso: ZE_COOKIES="cookie1=val1; cookie2=val2" python scraper/teste_ze.py
"""
import re, json, os, time
from playwright.sync_api import sync_playwright

URLS_TESTE = [
    ("Heineken Lata 350ml",   "https://www.ze.delivery/entrega-produto/9991/heineken-350ml"),
    ("Skol Lata 350ml",       "https://www.ze.delivery/entrega-produto/8504/skol-350ml"),
    ("Antarctica Lata 350ml", "https://www.ze.delivery/entrega-produto/8522/antarctica-pilsen-350ml"),
]

def parse_cookies(cookie_str):
    cookies = []
    for part in cookie_str.split('; '):
        part = part.strip()
        if '=' in part:
            name, _, value = part.partition('=')
            cookies.append({
                "name": name.strip(),
                "value": value.strip(),
                "domain": ".ze.delivery",
                "path": "/"
            })
    return cookies

def extrair_preco(txt):
    if not txt: return None
    nums = re.findall(r'\d+[.,]\d{2}', txt.replace('\xa0','').replace('\u00a0',''))
    return float(nums[0].replace(',','.')) if nums else None

def testar_url(page, nome, url):
    print(f"\n{'─'*50}\n🍺 {nome}\n   {url}")
    try:
        page.goto(url, wait_until="networkidle", timeout=30000)
        print(f"   ✓ Página carregada | título: {page.title()[:60]}")

        # Fecha modal se aparecer
        for sel in ['[data-testid="close-button"]', '[class*="secondaryButton"]']:
            try:
                btn = page.query_selector(sel)
                if btn and btn.is_visible():
                    btn.click()
                    page.wait_for_timeout(1500)
                    print("   ✓ Modal fechado")
                    break
            except: pass

        # Aguarda preço
        try:
            page.wait_for_selector('[data-testid="product-price"]', timeout=8000)
            print("   ✓ Elemento de preço encontrado")
        except:
            print("   ⚠️  Timeout aguardando preço")

        # Extrai via JS
        preco_txt = page.evaluate("""() => {
            const el = document.querySelector('[data-testid="product-price"]');
            if (el) return el.textContent.trim();
            const el2 = document.querySelector('[class*="priceText"]');
            if (el2) return el2.textContent.trim();
            return null;
        }""")

        if preco_txt:
            p = extrair_preco(preco_txt)
            if p and 0.5 < p < 50:
                print(f"   ✅ PREÇO: R$ {p:.2f}")
            else:
                print(f"   ⚠️  Texto: {preco_txt} — preço inválido")
        else:
            print("   ❌ Elemento de preço não encontrado")
            body = page.evaluate("() => document.body?.innerHTML?.slice(0,300) || 'vazio'")
            print(f"   📄 Body: {body}")

    except Exception as e:
        print(f"   ❌ Erro: {e}")

def main():
    cookie_str = os.environ.get("ZE_COOKIES", "")
    if not cookie_str:
        print("⚠️  ZE_COOKIES não definido — rodando sem cookies (pode falhar)")
    else:
        print(f"✓ {len(cookie_str.split(';'))} cookies carregados do ZE_COOKIES")

    print("=" * 50)
    print("TESTE ZÉ DELIVERY")
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
            extra_http_headers={"Accept-Language": "pt-BR,pt;q=0.9"}
        )

        # Injeta cookies reais se disponíveis
        if cookie_str:
            cookies = parse_cookies(cookie_str)
            context.add_cookies(cookies)
            print(f"✓ {len(cookies)} cookies injetados no contexto")

        page = context.new_page()

        for nome, url in URLS_TESTE:
            testar_url(page, nome, url)
            time.sleep(1)

        browser.close()

    print("\n" + "=" * 50)
    print("Teste concluído")

if __name__ == "__main__":
    main()
