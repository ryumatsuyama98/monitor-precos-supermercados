"""
Teste isolado do Zé Delivery — roda só 3 SKUs para diagnóstico rápido.
Uso: python scraper/teste_ze.py
"""
import re, json, time
from playwright.sync_api import sync_playwright

ZE_CEP = "01310100"
ZE_LAT = -23.5646162
ZE_LNG = -46.6527547

URLS_TESTE = [
    ("Heineken Lata 350ml",       "https://www.ze.delivery/entrega-produto/9991/heineken-350ml"),
    ("Skol Lata 350ml",           "https://www.ze.delivery/entrega-produto/8504/skol-350ml"),
    ("Antarctica Lata 350ml",     "https://www.ze.delivery/entrega-produto/8522/antarctica-pilsen-350ml"),
]

def extrair_preco(txt):
    if not txt: return None
    nums = re.findall(r'\d+[.,]\d{2}', txt.replace('\xa0','').replace('\u00a0',''))
    return float(nums[0].replace(',','.')) if nums else None

def testar_url(page, nome, url):
    print(f"\n{'─'*50}\n🍺 {nome}\n   {url}")
    try:
        # Abre homepage
        page.goto("https://www.ze.delivery/", wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(2000)

        # Fecha modal inicial se aparecer
        for sel in ['[data-testid="close-button"]', '[class*="secondaryButton"]']:
            try:
                btn = page.query_selector(sel)
                if btn and btn.is_visible():
                    btn.click()
                    page.wait_for_timeout(800)
                    print("   ✓ Modal inicial fechado")
                    break
            except: pass

        # Injeta endereço no Redux store
        page.evaluate("""() => {
            const addr = {
                latitude: -23.5646162, longitude: -46.6527547,
                zipcode: '01310100', street: 'Avenida Paulista',
                neighborhood: 'Bela Vista', city: 'São Paulo',
                province: 'SP', country: 'BR', number: '1000',
                referencePoint: '', type: {displayName: '', id: 'HISTORIC'}
            };
            const addrStr = JSON.stringify(addr);
            try {
                localStorage.setItem('userAddress', addrStr);
                localStorage.setItem('ze-address', addrStr);
                localStorage.setItem('deliveryAddress', addrStr);
                localStorage.setItem('persist:address', JSON.stringify({
                    userAddress: addrStr,
                    deliveryOptions: JSON.stringify({deliveryMethod: 'DELIVERY', address: addr}),
                    _persist: '{"version":1,"rehydrated":true}'
                }));
            } catch(e) { console.log('localStorage error:', e); }
        }""")
        print("   ✓ Endereço injetado no localStorage")

        # Navega para o produto — espera networkidle para garantir hidratação do React
        page.goto(url, wait_until="networkidle", timeout=30000)
        print(f"   ✓ Página carregada | título: {page.title()[:60]}")

        # Fecha modal do produto
        for sel in ['[data-testid="close-button"]', '[class*="secondaryButton"]']:
            try:
                btn = page.query_selector(sel)
                if btn and btn.is_visible():
                    btn.click()
                    page.wait_for_timeout(1000)
                    print("   ✓ Modal produto fechado")
                    break
            except: pass

        # Aguarda o elemento de preço aparecer (React precisa hidratar após o modal fechar)
        try:
            page.wait_for_selector('[data-testid="product-price"]', timeout=10000)
            print("   ✓ Elemento de preço encontrado no DOM")
        except Exception:
            print("   ⚠️  Timeout aguardando preço — tentando mesmo assim")

        # Tenta extrair preço via JS
        preco_txt = page.evaluate("""() => {
            const el1 = document.querySelector('[data-testid="product-price"]');
            if (el1) return '(testid) ' + el1.textContent.trim();
            const el2 = document.querySelector('[class*="priceText"]');
            if (el2) return '(priceText) ' + el2.textContent.trim();
            const container = document.querySelector('[class*="ProductWithAddress"]');
            if (container) {
                const els = container.querySelectorAll('div, span, p');
                for (const el of els) {
                    if (el.textContent.includes('R$') && el.children.length === 0)
                        return '(varredura) ' + el.textContent.trim();
                }
            }
            return null;
        }""")

        if preco_txt:
            p = extrair_preco(preco_txt)
            if p and 0.5 < p < 50:
                print(f"   ✅ PREÇO: R$ {p:.2f}  ← {preco_txt}")
            else:
                print(f"   ⚠️  Texto encontrado mas preço inválido: {preco_txt}")
        else:
            print("   ❌ Elemento de preço não encontrado no DOM")
            # Diagnóstico: mostra os primeiros 500 chars do body
            body = page.evaluate("() => document.body?.innerHTML?.slice(0,500) || 'body vazio'")
            print(f"   📄 Body snippet: {body[:300]}")

    except Exception as e:
        print(f"   ❌ Erro: {e}")

def main():
    print("=" * 50)
    print("TESTE ZÉ DELIVERY")
    print("=" * 50)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--no-first-run",
            ]
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
            locale="pt-BR",
        )
        page = context.new_page()

        for nome, url in URLS_TESTE:
            testar_url(page, nome, url)
            time.sleep(1)

        browser.close()

    print("\n" + "=" * 50)
    print("Teste concluído")

if __name__ == "__main__":
    main()
