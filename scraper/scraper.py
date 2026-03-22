"""
Scraper de preços de cervejas em supermercados brasileiros.
Coleta preços diariamente e salva em SQLite.
"""

import sqlite3
import json
import re
import time
import random
from datetime import date, datetime
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

DB_PATH = Path("data/precos.db")
LOG_PATH = Path("data/coleta.log")

CIDADES = [
    {"cidade": "São Paulo",      "uf": "SP", "regiao": "Sudeste", "cep": "01310100"},
    {"cidade": "Rio de Janeiro", "uf": "RJ", "regiao": "Sudeste", "cep": "20040020"},
    {"cidade": "Porto Alegre",   "uf": "RS", "regiao": "Sul",     "cep": "90010150"},
    {"cidade": "Curitiba",       "uf": "PR", "regiao": "Sul",     "cep": "80010010"},
    {"cidade": "Florianópolis",  "uf": "SC", "regiao": "Sul",     "cep": "88010001"},
    {"cidade": "Recife",         "uf": "PE", "regiao": "Nordeste","cep": "50010010"},
    {"cidade": "Salvador",       "uf": "BA", "regiao": "Nordeste","cep": "40010000"},
    {"cidade": "Fortaleza",      "uf": "CE", "regiao": "Nordeste","cep": "60010000"},
]

PRODUTOS = [
    {"marca": "Heineken",      "nome": "Heineken Lata",           "embalagem": "350ml"},
    {"marca": "Heineken",      "nome": "Heineken Lata",           "embalagem": "269ml"},
    {"marca": "Heineken",      "nome": "Heineken Long Neck",      "embalagem": "355ml"},
    {"marca": "Heineken",      "nome": "Heineken Garrafa",        "embalagem": "600ml"},
    {"marca": "Heineken",      "nome": "Heineken 0.0",            "embalagem": "350ml"},
    {"marca": "Heineken",      "nome": "Heineken Silver",         "embalagem": "350ml"},
    {"marca": "Skol",          "nome": "Skol Lata",               "embalagem": "350ml"},
    {"marca": "Skol",          "nome": "Skol Lata",               "embalagem": "269ml"},
    {"marca": "Skol",          "nome": "Skol Long Neck",          "embalagem": "355ml"},
    {"marca": "Skol",          "nome": "Skol Garrafa",            "embalagem": "600ml"},
    {"marca": "Brahma",        "nome": "Brahma Chopp Lata",       "embalagem": "350ml"},
    {"marca": "Brahma",        "nome": "Brahma Chopp Lata",       "embalagem": "269ml"},
    {"marca": "Brahma",        "nome": "Brahma Duplo Malte",      "embalagem": "350ml"},
    {"marca": "Brahma",        "nome": "Brahma 0,0",              "embalagem": "350ml"},
    {"marca": "Stella Artois", "nome": "Stella Artois Lata",      "embalagem": "350ml"},
    {"marca": "Stella Artois", "nome": "Stella Artois Long Neck", "embalagem": "355ml"},
    {"marca": "Stella Artois", "nome": "Stella Artois Garrafa",   "embalagem": "600ml"},
    {"marca": "Corona",        "nome": "Corona Extra Long Neck",  "embalagem": "355ml"},
    {"marca": "Corona",        "nome": "Corona Extra Garrafa",    "embalagem": "600ml"},
    {"marca": "Corona",        "nome": "Corona Cero 0,0",         "embalagem": "355ml"},
    {"marca": "Budweiser",     "nome": "Budweiser Lata",          "embalagem": "350ml"},
    {"marca": "Budweiser",     "nome": "Budweiser Lata",          "embalagem": "269ml"},
    {"marca": "Budweiser",     "nome": "Budweiser Long Neck",     "embalagem": "355ml"},
    {"marca": "Amstel",        "nome": "Amstel Lata",             "embalagem": "350ml"},
    {"marca": "Amstel",        "nome": "Amstel Lata",             "embalagem": "269ml"},
    {"marca": "Amstel",        "nome": "Amstel Long Neck",        "embalagem": "355ml"},
    {"marca": "Amstel",        "nome": "Amstel Ultra",            "embalagem": "350ml"},
    {"marca": "Amstel",        "nome": "Amstel 0,0",              "embalagem": "350ml"},
    {"marca": "Spaten",        "nome": "Spaten Pilsner Long Neck","embalagem": "355ml"},
    {"marca": "Spaten",        "nome": "Spaten Münchner Hell",    "embalagem": "355ml"},
    {"marca": "Original",      "nome": "Original Long Neck",      "embalagem": "355ml"},
    {"marca": "Original",      "nome": "Original Garrafa",        "embalagem": "600ml"},
    {"marca": "Itaipava",      "nome": "Itaipava Lata",           "embalagem": "350ml"},
    {"marca": "Itaipava",      "nome": "Itaipava Lata",           "embalagem": "269ml"},
    {"marca": "Itaipava",      "nome": "Itaipava Garrafa",        "embalagem": "600ml"},
]

LINKS_CARREFOUR = {
    "Heineken Lata_350ml":           "https://mercado.carrefour.com.br/cerveja-heineken-lata-sleek-350ml-3180018/p",
    "Heineken Lata_269ml":           "https://mercado.carrefour.com.br/cerveja-heineken-lata-269ml/p",
    "Heineken Long Neck_355ml":      "https://mercado.carrefour.com.br/cerveja-heineken-long-neck-355ml/p",
    "Heineken Garrafa_600ml":        "https://mercado.carrefour.com.br/cerveja-heineken-garrafa-600ml/p",
    "Heineken 0.0_350ml":            "https://mercado.carrefour.com.br/cerveja-lager-zero-alcool-heineken-lata-350ml-3180026/p",
    "Heineken Silver_350ml":         "https://mercado.carrefour.com.br/cerveja-heineken-silver-lata-350ml/p",
    "Skol Lata_350ml":               "https://mercado.carrefour.com.br/cerveja-skol-lata-350ml/p",
    "Brahma Chopp Lata_350ml":       "https://mercado.carrefour.com.br/cerveja-brahma-chopp-lata-350ml/p",
    "Brahma Duplo Malte_350ml":      "https://mercado.carrefour.com.br/cerveja-brahma-duplo-malte-lata-350ml/p",
    "Stella Artois Lata_350ml":      "https://mercado.carrefour.com.br/cerveja-stella-artois-lata-350ml/p",
    "Stella Artois Long Neck_355ml": "https://mercado.carrefour.com.br/cerveja-stella-artois-long-neck-355ml/p",
    "Corona Extra Long Neck_355ml":  "https://mercado.carrefour.com.br/cerveja-corona-extra-long-neck-355ml/p",
    "Budweiser Lata_350ml":          "https://mercado.carrefour.com.br/cerveja-budweiser-lata-350ml/p",
    "Amstel Lata_350ml":             "https://mercado.carrefour.com.br/cerveja-amstel-lata-350ml/p",
    "Amstel Ultra_350ml":            "https://mercado.carrefour.com.br/cerveja-amstel-ultra-lata-350ml/p",
    "Amstel 0,0_350ml":              "https://mercado.carrefour.com.br/cerveja-amstel-zero-alcool-350ml/p",
    "Spaten Pilsner Long Neck_355ml":"https://mercado.carrefour.com.br/cerveja-spaten-pilsner-long-neck-355ml/p",
    "Original Long Neck_355ml":      "https://mercado.carrefour.com.br/cerveja-original-long-neck-355ml/p",
    "Itaipava Lata_350ml":           "https://mercado.carrefour.com.br/cerveja-itaipava-lata-350ml/p",
}

LINKS_PAO_DE_ACUCAR = {
    "Heineken Lata_350ml":           "https://www.paodeacucar.com/produto/1606865/cerveja-lager-heineken-lata-350ml",
    "Heineken 0.0_350ml":            "https://www.paodeacucar.com/produto/462217/cerveja-lager-premium-puro-malte-zero-alcool-heineken-lata-350ml",
    "Skol Lata_350ml":               "https://www.paodeacucar.com/produto/cerveja-skol-lata-350ml",
    "Brahma Chopp Lata_350ml":       "https://www.paodeacucar.com/produto/cerveja-brahma-chopp-350ml",
    "Stella Artois Lata_350ml":      "https://www.paodeacucar.com/produto/cerveja-stella-artois-350ml",
    "Corona Extra Long Neck_355ml":  "https://www.paodeacucar.com/produto/cerveja-corona-extra-355ml",
    "Budweiser Lata_350ml":          "https://www.paodeacucar.com/produto/cerveja-budweiser-lata-350ml",
    "Amstel Lata_350ml":             "https://www.paodeacucar.com/produto/cerveja-amstel-350ml",
    "Spaten Pilsner Long Neck_355ml":"https://www.paodeacucar.com/produto/cerveja-spaten-355ml",
    "Original Long Neck_355ml":      "https://www.paodeacucar.com/produto/cerveja-original-355ml",
    "Itaipava Lata_350ml":           "https://www.paodeacucar.com/produto/cerveja-itaipava-350ml",
}

LINKS_EXTRA = {
    "Heineken Lata_350ml":           "https://www.extra.com.br/cerveja-heineken-pilsen-12-unidades-lata-350ml/p/55021179",
    "Skol Lata_350ml":               "https://www.extra.com.br/cerveja-skol-lata-350ml/p",
    "Brahma Chopp Lata_350ml":       "https://www.extra.com.br/cerveja-brahma-chopp-350ml/p",
    "Stella Artois Lata_350ml":      "https://www.extra.com.br/cerveja-stella-artois-350ml/p",
    "Corona Extra Long Neck_355ml":  "https://www.extra.com.br/cerveja-corona-extra-355ml/p",
    "Budweiser Lata_350ml":          "https://www.extra.com.br/cerveja-budweiser-350ml/p",
    "Original Long Neck_355ml":      "https://www.extra.com.br/cerveja-original-garrafa-600ml/p",
}

LINKS_ATACADAO = {
    "Heineken Lata_350ml":           "https://www.atacadao.com.br/cerveja-heineken-sleek-86733/p",
    "Heineken 0.0_350ml":            "https://www.atacadao.com.br/cerveja-heineken-zero-sleek-86709/p",
    "Skol Lata_350ml":               "https://www.atacadao.com.br/cerveja-skol-lata-350ml/p",
    "Brahma Chopp Lata_350ml":       "https://www.atacadao.com.br/cerveja-brahma-lata-350ml/p",
    "Stella Artois Lata_350ml":      "https://www.atacadao.com.br/cerveja-stella-artois-350ml/p",
    "Corona Extra Long Neck_355ml":  "https://www.atacadao.com.br/cerveja-corona-extra-355ml/p",
    "Budweiser Lata_350ml":          "https://www.atacadao.com.br/cerveja-budweiser-lata-350ml/p",
    "Amstel Lata_350ml":             "https://www.atacadao.com.br/cerveja-amstel-lata-350ml/p",
    "Spaten Pilsner Long Neck_355ml":"https://www.atacadao.com.br/cerveja-spaten-355ml/p",
    "Itaipava Lata_350ml":           "https://www.atacadao.com.br/cerveja-itaipava-lata-350ml/p",
    "Itaipava Garrafa_600ml":        "https://www.atacadao.com.br/cerveja-itaipava-garrafa-600ml/p",
}

SUPERMERCADOS = {
    "Carrefour Mercado": {"links": LINKS_CARREFOUR,     "cep_method": "cookie"},
    "Pão de Açúcar":     {"links": LINKS_PAO_DE_ACUCAR, "cep_method": "cookie"},
    "Extra":             {"links": LINKS_EXTRA,          "cep_method": "query"},
    "Atacadão":          {"links": LINKS_ATACADAO,       "cep_method": "cookie"},
}

def init_db():
    DB_PATH.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS precos (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            data_coleta     TEXT NOT NULL,
            horario_coleta  TEXT NOT NULL,
            supermercado    TEXT NOT NULL,
            marca           TEXT NOT NULL,
            nome_produto    TEXT NOT NULL,
            embalagem       TEXT NOT NULL,
            cidade          TEXT NOT NULL,
            uf              TEXT NOT NULL,
            regiao          TEXT NOT NULL,
            preco_atual     REAL,
            preco_original  REAL,
            em_promocao     INTEGER DEFAULT 0,
            disponivel      INTEGER DEFAULT 1,
            url             TEXT,
            erro            TEXT
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_data ON precos(data_coleta)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_produto ON precos(marca, nome_produto, embalagem)")
    con.commit()
    return con

def inserir(con, registro):
    con.execute("""
        INSERT INTO precos
          (data_coleta, horario_coleta, supermercado, marca, nome_produto,
           embalagem, cidade, uf, regiao, preco_atual, preco_original,
           em_promocao, disponivel, url, erro)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        registro["data_coleta"], registro["horario_coleta"],
        registro["supermercado"], registro["marca"], registro["nome_produto"],
        registro["embalagem"], registro["cidade"], registro["uf"], registro["regiao"],
        registro.get("preco_atual"), registro.get("preco_original"),
        int(registro.get("em_promocao", False)),
        int(registro.get("disponivel", True)),
        registro.get("url"), registro.get("erro"),
    ))

def extrair_preco(texto):
    if not texto:
        return None
    texto = texto.replace('\xa0', '').replace(' ', '')
    numeros = re.findall(r'\d+[.,]\d{2}', texto)
    if numeros:
        return float(numeros[0].replace(',', '.'))
    return None

def extrair_preco_js(page):
    """Tenta extrair preço via JavaScript procurando no DOM por padrão R$ X,XX."""
    try:
        preco_texto = page.evaluate("""() => {
            const walker = document.createTreeWalker(
                document.body,
                NodeFilter.SHOW_TEXT,
                null
            );
            let node;
            while (node = walker.nextNode()) {
                const t = node.textContent.trim();
                if (/R\$\s*\d+[.,]\d{2}/.test(t) && t.length < 30) {
                    return t;
                }
            }
            // Fallback: procura em elementos com classes de preço
            const sels = [
                '[class*="price"]', '[class*="Price"]', '[class*="preco"]',
                '[class*="Preco"]', '[class*="valor"]', '[class*="Valor"]',
                '[itemprop="price"]', '[data-price]'
            ];
            for (const sel of sels) {
                const els = document.querySelectorAll(sel);
                for (const el of els) {
                    const t = el.textContent.trim();
                    if (/R\$\s*\d+[.,]\d{2}/.test(t) && t.length < 30) {
                        return t;
                    }
                    // Verifica atributo data-price
                    const dp = el.getAttribute('data-price') || el.getAttribute('content');
                    if (dp && /\d+[.,]\d{2}/.test(dp)) return dp;
                }
            }
            return null;
        }""")
        return extrair_preco(preco_texto)
    except Exception:
        return None

def coletar_pagina(page, url, supermercado):
    resultado = {
        "url": url, "disponivel": False,
        "preco_atual": None, "preco_original": None,
        "em_promocao": False, "erro": None
    }
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        # Aguarda JS carregar o preço
        page.wait_for_timeout(4000)

        # Estratégia específica por supermercado
        if supermercado == "Atacadão":
            # Atacadão: preço visível no HTML estático — R$X.XX logo após o título
            preco = extrair_preco_js(page)
            if not preco:
                # Tenta seletores conhecidos do Atacadão (VTEX)
                for sel in ["h3.valornormal", ".valornormal", ".price-best-price",
                            "span.vtex-product-price-1-x-sellingPrice",
                            "span[class*='sellingPrice']", "span[class*='selling']"]:
                    el = page.query_selector(sel)
                    if el:
                        preco = extrair_preco(el.inner_text())
                        if preco:
                            break
            resultado["preco_atual"] = preco

        elif supermercado == "Pão de Açúcar":
            # Pão de Açúcar: preço em elemento com classe sales ou similar
            page.wait_for_timeout(2000)
            for sel in [".sales .value", ".price__sales", "span.sales",
                        "[class*='sales'] [class*='value']",
                        "span[class*='sellingPrice']", ".product-price"]:
                el = page.query_selector(sel)
                if el:
                    preco = extrair_preco(el.inner_text())
                    if preco:
                        resultado["preco_atual"] = preco
                        break
            if not resultado["preco_atual"]:
                resultado["preco_atual"] = extrair_preco_js(page)

        elif supermercado == "Carrefour Mercado":
            # Carrefour: React app, precisa esperar mais
            page.wait_for_timeout(3000)
            for sel in ["span[class*='sellingPrice']", "span[class*='SellingPrice']",
                        "div[class*='price'] span", "span[class*='Price']",
                        "[data-testid='price-value']", "strong[class*='price']"]:
                el = page.query_selector(sel)
                if el:
                    preco = extrair_preco(el.inner_text())
                    if preco:
                        resultado["preco_atual"] = preco
                        break
            if not resultado["preco_atual"]:
                resultado["preco_atual"] = extrair_preco_js(page)

        elif supermercado == "Extra":
            # Extra: React app similar ao Pão de Açúcar (mesmo grupo GPA)
            page.wait_for_timeout(2000)
            for sel in [".sales .value", "span.sales", ".product-price .value",
                        "span[class*='sellingPrice']", "[class*='price-selling']",
                        "div[class*='ProductPrice']"]:
                el = page.query_selector(sel)
                if el:
                    preco = extrair_preco(el.inner_text())
                    if preco:
                        resultado["preco_atual"] = preco
                        break
            if not resultado["preco_atual"]:
                resultado["preco_atual"] = extrair_preco_js(page)

        # Tenta preço original (riscado)
        for sel in ["span[class*='listPrice']", "span[class*='ListPrice']",
                    ".price__list", "span.list", "[class*='price-list']",
                    "s span", "del span", "span[class*='oldPrice']"]:
            el = page.query_selector(sel)
            if el:
                preco_orig = extrair_preco(el.inner_text())
                if preco_orig:
                    resultado["preco_original"] = preco_orig
                    break

        # Disponibilidade: se achou preço, está disponível
        if resultado["preco_atual"]:
            resultado["disponivel"] = True
            if resultado["preco_original"] and resultado["preco_original"] > resultado["preco_atual"]:
                resultado["em_promocao"] = True
        else:
            resultado["erro"] = "preco_nao_encontrado"

    except PWTimeout:
        resultado["erro"] = "timeout"
    except Exception as e:
        resultado["erro"] = str(e)[:100]

    return resultado

def main():
    log = []
    con = init_db()
    hoje = date.today().isoformat()
    total_ok = 0
    total_erro = 0

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"]
        )

        for sm_nome, sm_config in SUPERMERCADOS.items():
            links = sm_config["links"]
            print(f"\n=== {sm_nome} ===")

            for cidade_info in CIDADES:
                ctx = browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    viewport={"width": 1280, "height": 800},
                    locale="pt-BR",
                    extra_http_headers={"Accept-Language": "pt-BR,pt;q=0.9"}
                )
                page = ctx.new_page()
                # Remove webdriver flag
                page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

                for produto in PRODUTOS:
                    chave = f"{produto['nome']}_{produto['embalagem']}"
                    url = links.get(chave)
                    if not url:
                        continue

                    horario = datetime.now().strftime("%H:%M:%S")
                    dados = coletar_pagina(page, url, sm_nome)

                    registro = {
                        "data_coleta":    hoje,
                        "horario_coleta": horario,
                        "supermercado":   sm_nome,
                        "marca":          produto["marca"],
                        "nome_produto":   produto["nome"],
                        "embalagem":      produto["embalagem"],
                        "cidade":         cidade_info["cidade"],
                        "uf":             cidade_info["uf"],
                        "regiao":         cidade_info["regiao"],
                        **dados,
                    }
                    inserir(con, registro)

                    status = "OK" if dados["preco_atual"] else f"ERRO:{dados['erro']}"
                    preco_str = f"R${dados['preco_atual']:.2f}" if dados["preco_atual"] else "—"
                    msg = f"{hoje} | {sm_nome} | {produto['nome']} {produto['embalagem']} | {cidade_info['cidade']} | {preco_str} | {status}"
                    log.append(msg)
                    print(msg)

                    if dados["preco_atual"]:
                        total_ok += 1
                    else:
                        total_erro += 1

                    time.sleep(random.uniform(2.0, 4.0))

                ctx.close()

        browser.close()

    con.commit()
    con.close()

    # Exportar CSV do dia
    import csv
    csv_path = Path(f"data/coleta_{hoje}.csv")
    con2 = sqlite3.connect(DB_PATH)
    con2.row_factory = sqlite3.Row
    rows = con2.execute("SELECT * FROM precos WHERE data_coleta = ?", (hoje,)).fetchall()
    if rows:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=rows[0].keys())
            w.writeheader()
            w.writerows([dict(r) for r in rows])
    con2.close()

    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(f"\n=== Coleta {hoje} | OK:{total_ok} ERRO:{total_erro} ===\n")
        f.write("\n".join(log[-100:]))

    print(f"\nColeta finalizada: {total_ok} preços coletados, {total_erro} erros.")

if __name__ == "__main__":
    main()
