"""
Teste Zé Delivery — fluxo humano completo.
1. Abre homepage
2. Fecha modal de app
3. Digita CEP no campo de endereço
4. Confirma endereço
5. Navega para produto
6. Extrai preço
"""
import re, os, time
from playwright.sync_api import sync_playwright

CEP = "01310100"

URLS_TESTE = [
    ("Heineken Lata 350ml",   "https://www.ze.delivery/entrega-produto/9991/heineken-350ml"),
    ("Skol Lata 350ml",       "https://www.ze.delivery/entrega-produto/8504/skol-350ml"),
    ("Antarctica Lata 350ml", "https://www.ze.delivery/entrega-produto/8522/antarctica-pilsen-350ml"),
]

def digitar_humano(page, selector, texto, delay=80):
    """Digita texto caractere por caractere como humano."""
    el = page.wait_for_selector(selector, timeout=8000, state="visible")
    el.click()
    page.wait_for_timeout(300)
    for c in texto:
        page.keyboard.type(c)
        page.wait_for_timeout(delay + int(re.sub(r'\D','',str(hash(c)))[:2] or 10))

def configurar_cep(page):
    """Faz o fluxo completo de configurar o CEP no site."""
    print("   → Abrindo homepage...")
    page.goto("https://www.ze.delivery/", wait_until="domcontentloaded", timeout=20000)
    page.wait_for_timeout(2000)

    # Fecha modal de app se aparecer
    for sel in ['[data-testid="close-button"]', '[class*="secondaryButton"]']:
        try:
            btn = page.query_selector(sel)
            if btn and btn.is_visible():
                btn.click()
                page.wait_for_timeout(1000)
                print("   ✓ Modal app fechado")
                break
        except: pass

    # Diagnóstico: lista todos os botões e inputs visíveis
    diag = page.evaluate("""() => {
        const els = [...document.querySelectorAll('button, input')];
        return els.filter(e => e.offsetParent !== null).map(e => ({
            tag: e.tagName, id: e.id.slice(0,40),
            cls: e.className.slice(0,60),
            txt: e.textContent.trim().slice(0,40),
            ph: e.placeholder || ''
        }));
    }""")
    print("   📋 Botões/inputs visíveis:")
    for d in diag:
        print(f"      {d['tag']} id='{d['id']}' txt='{d['txt']}' ph='{d['ph']}'")

    # Age gate — clica via JS em qualquer botão que NÃO seja "Voltar"
    age_clicked = page.evaluate("""() => {
        const btns = [...document.querySelectorAll('button')];
        for (const btn of btns) {
            const txt = btn.textContent.trim().toLowerCase();
            const id = btn.id.toLowerCase();
            if (id.includes('age-gate') && !txt.includes('voltar') && !txt.includes('back')) {
                btn.click(); return 'clicked: ' + btn.textContent.trim();
            }
        }
        // Fallback: qualquer botão com texto de confirmação
        for (const btn of btns) {
            const txt = btn.textContent.trim().toLowerCase();
            if (['sim', 'tenho 18', 'sou maior', 'confirmar', 'continuar', 'tenho mais'].some(w => txt.includes(w))) {
                btn.click(); return 'clicked: ' + btn.textContent.trim();
            }
        }
        return null;
    }""")
    if age_clicked:
        print(f"   ✓ Age gate: {age_clicked}")
        page.wait_for_timeout(1500)
    else:
        # Se não tem botão de confirmação, o age gate já passou (cookie)
        print("   ℹ️  Age gate sem botão de confirmação — já aceito ou inexistente")

    # Digita CEP no input fake-address-search
    print(f"   → Digitando CEP {CEP}...")
    digitou = False
    for sel in [
        '#fake-address-search-input',
        '[id*="fake-address-search"]',
        'input[placeholder*="endereço"]',
        'input[placeholder*="Inserir"]',
        'input[placeholder*="CEP"]',
    ]:
        try:
            inp = page.wait_for_selector(sel, timeout=4000, state="visible")
            if inp:
                inp.click()
                page.wait_for_timeout(500)
                inp.fill("")
                page.wait_for_timeout(200)
                inp.type(CEP, delay=100)
                page.wait_for_timeout(2000)
                digitou = True
                print(f"   ✓ CEP digitado via {sel}")
                break
        except: pass

    if not digitou:
        print("   ❌ Não conseguiu digitar o CEP")
        return False

    # Aguarda sugestões aparecerem e clica na primeira
    print("   → Aguardando sugestões de endereço...")
    for sel in [
        '[class*="suggestion"]',
        '[class*="Suggestion"]',
        '[class*="autocomplete"] li',
        '[class*="AddressSearch"] li',
        '[role="option"]',
        '[class*="addressItem"]',
        '[class*="listItem"]',
    ]:
        try:
            item = page.wait_for_selector(sel, timeout=5000, state="visible")
            if item:
                item.click()
                page.wait_for_timeout(1500)
                print(f"   ✓ Selecionou sugestão via {sel}")
                break
        except: pass

    # Confirma o endereço se tiver botão
    for sel in [
        'button[data-testid="confirm-address"]',
        'button[class*="confirm"]',
        '[class*="confirmButton"]',
        'button[type="submit"]',
    ]:
        try:
            btn = page.query_selector(sel)
            if btn and btn.is_visible():
                btn.click()
                page.wait_for_timeout(1500)
                print(f"   ✓ Endereço confirmado via {sel}")
                break
        except: pass

    # Verifica se endereço foi configurado
    addr = page.evaluate("""() => {
        const el = document.querySelector('[class*="DeliveryOptionsCard_address"]');
        return el ? el.textContent : null;
    }""")
    if addr and "Carregando" not in addr:
        print(f"   ✅ Endereço configurado: {addr[:60]}")
        return True
    else:
        print(f"   ⚠️  Endereço possivelmente não configurado: {addr}")
        return True  # Tenta mesmo assim

def testar_url(page, nome, url):
    print(f"\n{'─'*50}\n🍺 {nome}")

    # Navega para o produto
    print(f"   → Navegando para produto...")
    page.goto(url, wait_until="domcontentloaded", timeout=25000)
    page.wait_for_timeout(2000)
    print(f"   ✓ Título: {page.title()[:50]}")

    # Fecha modal de app se aparecer
    for sel in ['[data-testid="close-button"]', '[class*="secondaryButton"]']:
        try:
            btn = page.query_selector(sel)
            if btn and btn.is_visible():
                btn.click()
                page.wait_for_timeout(1000)
                print("   ✓ Modal fechado")
                break
        except: pass

    # Aguarda preço
    try:
        page.wait_for_selector('[data-testid="product-price"]', timeout=8000)
    except: pass

    # Extrai preço
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
    body = page.evaluate("() => document.querySelector('[class*=ProductWithAddress]')?.innerHTML?.slice(0,300) || 'container não encontrado'")
    print(f"   📄 Container produto: {body}")

def main():
    print("=" * 50)
    print("TESTE ZÉ DELIVERY — Fluxo Humano")
    print("=" * 50)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ]
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
            viewport={"width": 1366, "height": 768},
            locale="pt-BR",
        )
        # Remove o webdriver flag
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
            window.chrome = {runtime: {}};
        """)

        page = context.new_page()

        # Configura CEP uma vez
        cep_ok = configurar_cep(page)
        if not cep_ok:
            print("\n⚠️  CEP não configurado — testando produto mesmo assim")

        # Testa produtos
        for nome, url in URLS_TESTE:
            testar_url(page, nome, url)
            time.sleep(1)

        browser.close()

    print("\n" + "=" * 50)
    print("Teste concluído")

if __name__ == "__main__":
    main()
