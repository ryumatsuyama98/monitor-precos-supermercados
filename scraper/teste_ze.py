"""
Teste Zé Delivery — intercepta resposta SSR para extrair preço do HTML bruto.
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
            cookies.append({"name": name.strip(), "value": value.strip(),
                            "domain": ".ze.delivery", "path": "/"})
    return cookies

def extrair_preco_html(html):
    """Extrai preço diretamente do HTML do SSR — sem depender do JS."""
    # Tenta JSON embedded no __next_f
    matches = re.findall(r'"price"\s*:\s*(\d+\.?\d*)', html)
    if matches:
        precos = [float(m) for m in matches if 0.5 < float(m) < 50]
        if precos:
            return min(precos)

    # Tenta texto R$ no HTML
    matches = re.findall(r'R\$\s*(?:&nbsp;)?\s*(\d+)[,.](\d{2})', html)
    if matches:
        precos = [float(f"{a}.{b}") for a,b in matches if 0.5 < float(f"{a}.{b}") < 50]
        if precos:
            return min(precos)

    # Tenta data-testid="product-price" no HTML
    m = re.search(r'data-testid="product-price"[^>]*>([^<]+)<', html)
    if m:
        txt = m.group(1).replace('&nbsp;','').strip()
        nums = re.findall(r'\d+[.,]\d{2}', txt)
        if nums:
            return float(nums[0].replace(',','.'))

    return None

def testar_url(context, nome, url):
    print(f"\n{'─'*50}\n🍺 {nome}\n   {url}")

    html_capturado = []

    page = context.new_page()
    try:
        # Intercepta a resposta da página principal
        def handle_response(response):
            if url in response.url and response.status == 200:
                try:
                    body = response.body()
                    html_capturado.append(body.decode('utf-8', errors='replace'))
                except: pass

        page.on("response", handle_response)
        page.goto(url, wait_until="domcontentloaded", timeout=25000)
        print(f"   ✓ Página carregada | título: {page.title()[:60]}")

        # Tenta extrair do HTML capturado (SSR)
        if html_capturado:
            html = html_capturado[0]
            p = extrair_preco_html(html)
            if p:
                print(f"   ✅ PREÇO (SSR): R$ {p:.2f}")
            else:
                print(f"   ⚠️  HTML capturado ({len(html)} chars) mas preço não encontrado")
                # Mostra trecho relevante
                idx = html.find('price')
                if idx > 0:
                    print(f"   📄 Trecho 'price': ...{html[max(0,idx-50):idx+100]}...")
                idx2 = html.find('R$')
                if idx2 > 0:
                    print(f"   📄 Trecho 'R$': ...{html[max(0,idx2-20):idx2+60]}...")
        else:
            print("   ❌ Nenhuma resposta HTML capturada")
            # Fallback: tenta via JS mesmo assim
            preco_txt = page.evaluate("""() => {
                const el = document.querySelector('[data-testid="product-price"]');
                return el ? el.textContent.trim() : null;
            }""")
            if preco_txt:
                print(f"   ✅ PREÇO (DOM): {preco_txt}")
            else:
                body = page.evaluate("() => document.body?.innerHTML?.slice(0,200) || 'vazio'")
                print(f"   📄 Body DOM: {body}")

    except Exception as e:
        print(f"   ❌ Erro: {e}")
    finally:
        page.close()

def main():
    cookie_str = os.environ.get("ZE_COOKIES", "")
    print("=" * 50)
    print("TESTE ZÉ DELIVERY — Interceptação SSR")
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
        if cookie_str:
            context.add_cookies(parse_cookies(cookie_str))
            print(f"✓ Cookies injetados")

        for nome, url in URLS_TESTE:
            testar_url(context, nome, url)
            time.sleep(1)

        browser.close()

    print("\n" + "=" * 50)
    print("Teste concluído")

if __name__ == "__main__":
    main()
