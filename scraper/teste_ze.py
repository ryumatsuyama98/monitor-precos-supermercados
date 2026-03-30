"""
Teste Zé Delivery via requests puro — sem browser, sem Playwright.
O preço vem do SSR do Next.js via Apollo, passando endereço nos cookies.
"""
import re, os, urllib.request, urllib.error, json

URLS_TESTE = [
    ("Heineken Lata 350ml",   "https://www.ze.delivery/entrega-produto/9991/heineken-350ml"),
    ("Skol Lata 350ml",       "https://www.ze.delivery/entrega-produto/8504/skol-350ml"),
    ("Antarctica Lata 350ml", "https://www.ze.delivery/entrega-produto/8522/antarctica-pilsen-350ml"),
]

# Endereço SP codificado como o site espera nos cookies
import urllib.parse
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
}))

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
}))

def extrair_preco_html(html):
    # Tenta "price": numero no JSON do Next.js
    m = re.search(r'"price"\s*:\s*(\d+\.?\d*)', html)
    if m:
        p = float(m.group(1))
        if 0.5 < p < 50:
            return p
    # Tenta R$ no HTML
    matches = re.findall(r'R\$\s*(?:&nbsp;|\u00a0)?\s*(\d+)[,.](\d{2})', html)
    precos = [float(f"{a}.{b}") for a,b in matches if 0.5 < float(f"{a}.{b}") < 50]
    if precos:
        return min(precos)
    return None

def testar_url(nome, url, extra_cookies=""):
    print(f"\n{'─'*50}\n🍺 {nome}\n   {url}")

    # Monta cookie string com endereço
    cookies = (
        f"userAddress={USER_ADDRESS}; "
        f"deliveryOptions={DELIVERY_OPTIONS}; "
        f"deliveryMethod=%22DELIVERY%22; "
        f"isScheduledDeliveryAvailable=false; "
        f"ageGateAccepted=true; "
    )
    if extra_cookies:
        cookies += extra_cookies

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Cookie": cookies,
        "Referer": "https://www.ze.delivery/",
    }

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            import gzip
            raw = resp.read()
            try:
                html = gzip.decompress(raw).decode('utf-8', errors='replace')
            except:
                html = raw.decode('utf-8', errors='replace')

        print(f"   ✓ HTTP {resp.status} | {len(html)} chars")

        # Verifica initialStoreData
        if '"userAddress":null' in html:
            print("   ⚠️  userAddress=null no SSR — servidor não leu o cookie")
        elif '"userAddress":{' in html or '"userAddress":"{' in html:
            print("   ✓ userAddress presente no SSR")

        p = extrair_preco_html(html)
        if p:
            print(f"   ✅ PREÇO: R$ {p:.2f}")
        else:
            print("   ❌ Preço não encontrado no HTML")
            # Mostra trecho do initialStoreData
            idx = html.find('"initialStoreData"')
            if idx > 0:
                print(f"   📄 initialStoreData: {html[idx:idx+300]}")

    except urllib.error.HTTPError as e:
        print(f"   ❌ HTTP {e.code}: {e.reason}")
    except Exception as e:
        print(f"   ❌ Erro: {e}")

def main():
    extra = os.environ.get("ZE_COOKIES", "")
    print("=" * 50)
    print("TESTE ZÉ DELIVERY — requests puro")
    print("=" * 50)
    for nome, url in URLS_TESTE:
        testar_url(nome, url, extra)

    print("\n" + "=" * 50)
    print("Teste concluído")

if __name__ == "__main__":
    main()
