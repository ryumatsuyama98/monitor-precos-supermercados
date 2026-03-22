"""
Monitor de Preços — Scraper v3 (anti-bot máximo + retry inteligente)

Melhorias vs versão anterior:
- Stealth completo: fingerprint JS, canvas noise, WebGL spoof, plugins fake
- Headers HTTP realistas por supermercado (Referer, Sec-Fetch-*, Accept)
- CEP injetado via cookie E via localStorage antes de cada produto
- wait_for_selector nos elementos de preço (não só timeout fixo)
- Retry automático com backoff: até 3 tentativas por produto
- Rota 0 aprimorada: canonical → busca → URL alternativa no banco
- Seletores CSS atualizados e expandidos para cada supermercado
- Scroll automático para forçar lazy-load de preços
- Salva URL recuperada no banco para reutilizar na próxima coleta
- Commit incremental a cada cidade (não perde dados se o job cair)
"""

import sqlite3, json, re, time, random, csv, hashlib
from datetime import date, datetime
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

DB_PATH  = Path("data/precos.db")
LOG_PATH = Path("data/coleta.log")

# ─── URLs de busca (Rota 0) ───────────────────────────────────────────────────
BUSCA_URL = {
    "Carrefour Mercado": "https://mercado.carrefour.com.br/busca/{q}",
    "Pão de Açúcar":     "https://www.paodeacucar.com/busca?q={q}",
    "Extra":             "https://www.extra.com.br/busca/{q}",
    "Atacadão":          "https://www.atacadao.com.br/busca/{q}",
}

LINK_SELETOR = {
    "Carrefour Mercado": 'a[href*="/p"]',
    "Pão de Açúcar":     'a[href*="/produto/"]',
    "Extra":             'a[href*="/p/"], a[href*="/produto/"]',
    "Atacadão":          'a[href*="/p"]',
}

# ─── Headers realistas por supermercado ──────────────────────────────────────
HEADERS_SM = {
    "Carrefour Mercado": {
        "Referer": "https://www.google.com.br/",
        "Sec-Fetch-Site": "cross-site",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Dest": "document",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    },
    "Pão de Açúcar": {
        "Referer": "https://www.google.com.br/",
        "Sec-Fetch-Site": "cross-site",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Dest": "document",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9",
    },
    "Extra": {
        "Referer": "https://www.google.com.br/",
        "Sec-Fetch-Site": "cross-site",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Dest": "document",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9",
    },
    "Atacadão": {
        "Referer": "https://www.google.com.br/",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9",
    },
}

# ─── Script stealth (mascara fingerprints do Playwright) ─────────────────────
STEALTH_JS = """
// Remove webdriver flag
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});

// Plugins realistas
Object.defineProperty(navigator, 'plugins', {get: () => [
  {name:'Chrome PDF Plugin',filename:'internal-pdf-viewer',description:'Portable Document Format'},
  {name:'Chrome PDF Viewer',filename:'mhjfbmdgcfjbbpaeojofohoefgiehjai',description:''},
  {name:'Native Client',filename:'internal-nacl-plugin',description:''},
]});

// Languages
Object.defineProperty(navigator, 'languages', {get: () => ['pt-BR','pt','en-US','en']});

// Platform
Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});

// Hardware concurrency
Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 8});

// Device memory
Object.defineProperty(navigator, 'deviceMemory', {get: () => 8});

// Canvas fingerprint noise
const origGetContext = HTMLCanvasElement.prototype.getContext;
HTMLCanvasElement.prototype.getContext = function(type, ...args) {
  const ctx = origGetContext.call(this, type, ...args);
  if(type === '2d' && ctx) {
    const origFillText = ctx.fillText.bind(ctx);
    ctx.fillText = function(...a) {
      ctx.shadowBlur = Math.random() * 0.1;
      return origFillText(...a);
    };
  }
  return ctx;
};

// WebGL vendor spoof
const origGetParam = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function(param) {
  if(param === 37445) return 'Google Inc. (NVIDIA)';
  if(param === 37446) return 'ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)';
  return origGetParam.call(this, param);
};

// Permissions API
if(navigator.permissions) {
  const origQuery = navigator.permissions.query.bind(navigator.permissions);
  navigator.permissions.query = (params) => {
    if(params.name === 'notifications') return Promise.resolve({state: 'default'});
    return origQuery(params);
  };
}

// Chrome runtime object
window.chrome = {runtime: {}, loadTimes: () => {}, csi: () => {}};
"""

# ─── Injeção de CEP por supermercado ─────────────────────────────────────────
def injetar_cep(page, supermercado, cep):
    """Injeta CEP via cookie e localStorage antes de acessar o produto."""
    try:
        dominio = {
            "Carrefour Mercado": ".carrefour.com.br",
            "Pão de Açúcar":     ".paodeacucar.com",
            "Extra":             ".extra.com.br",
            "Atacadão":          ".atacadao.com.br",
        }.get(supermercado)
        if not dominio: return

        # Cookie
        page.context.add_cookies([{
            "name": "userPostalCode", "value": cep,
            "domain": dominio, "path": "/",
        }])
        # Também tenta via localStorage
        page.evaluate(f"""() => {{
            try {{
                localStorage.setItem('userPostalCode', '{cep}');
                localStorage.setItem('selectedCEP', '{cep}');
                localStorage.setItem('zipCode', '{cep}');
            }} catch(e) {{}}
        }}""")
    except Exception:
        pass

# ─── Cidades ──────────────────────────────────────────────────────────────────
CIDADES = [
    {"cidade":"São Paulo",      "uf":"SP","regiao":"Sudeste","cep":"01310100"},
    {"cidade":"Rio de Janeiro", "uf":"RJ","regiao":"Sudeste","cep":"20040020"},
    {"cidade":"Porto Alegre",   "uf":"RS","regiao":"Sul",    "cep":"90010150"},
    {"cidade":"Curitiba",       "uf":"PR","regiao":"Sul",    "cep":"80010010"},
    {"cidade":"Florianópolis",  "uf":"SC","regiao":"Sul",    "cep":"88010001"},
    {"cidade":"Recife",         "uf":"PE","regiao":"Nordeste","cep":"50010010"},
    {"cidade":"Salvador",       "uf":"BA","regiao":"Nordeste","cep":"40010000"},
    {"cidade":"Fortaleza",      "uf":"CE","regiao":"Nordeste","cep":"60010000"},
]

# ─── Produtos por categoria ───────────────────────────────────────────────────
PRODUTOS = {
    "Cervejas": [
        {"marca":"Heineken",      "nome":"Heineken Lata",           "embalagem":"350ml"},
        {"marca":"Heineken",      "nome":"Heineken Lata",           "embalagem":"269ml"},
        {"marca":"Heineken",      "nome":"Heineken Long Neck",      "embalagem":"355ml"},
        {"marca":"Heineken",      "nome":"Heineken Garrafa",        "embalagem":"600ml"},
        {"marca":"Heineken",      "nome":"Heineken 0.0",            "embalagem":"350ml"},
        {"marca":"Heineken",      "nome":"Heineken Silver",         "embalagem":"350ml"},
        {"marca":"Skol",          "nome":"Skol Lata",               "embalagem":"350ml"},
        {"marca":"Skol",          "nome":"Skol Lata",               "embalagem":"269ml"},
        {"marca":"Brahma",        "nome":"Brahma Chopp Lata",       "embalagem":"350ml"},
        {"marca":"Brahma",        "nome":"Brahma Duplo Malte",      "embalagem":"350ml"},
        {"marca":"Stella Artois", "nome":"Stella Artois Lata",      "embalagem":"350ml"},
        {"marca":"Stella Artois", "nome":"Stella Artois Long Neck", "embalagem":"355ml"},
        {"marca":"Corona",        "nome":"Corona Extra Long Neck",  "embalagem":"355ml"},
        {"marca":"Budweiser",     "nome":"Budweiser Lata",          "embalagem":"350ml"},
        {"marca":"Amstel",        "nome":"Amstel Lata",             "embalagem":"350ml"},
        {"marca":"Amstel",        "nome":"Amstel Ultra",            "embalagem":"350ml"},
        {"marca":"Amstel",        "nome":"Amstel 0,0",              "embalagem":"350ml"},
        {"marca":"Spaten",        "nome":"Spaten Pilsner Long Neck","embalagem":"355ml"},
        {"marca":"Original",      "nome":"Original Long Neck",      "embalagem":"355ml"},
        {"marca":"Itaipava",      "nome":"Itaipava Lata",           "embalagem":"350ml"},
    ],
    "Embutidos": [
        {"marca":"Sadia",    "nome":"Sadia Salsicha Hot Dog",    "embalagem":"500g"},
        {"marca":"Perdigão", "nome":"Perdigão Salsicha Hot Dog", "embalagem":"500g"},
        {"marca":"Seara",    "nome":"Seara Salsicha Hot Dog",    "embalagem":"500g"},
        {"marca":"Sadia",    "nome":"Sadia Mortadela fatiada",   "embalagem":"200g"},
        {"marca":"Perdigão", "nome":"Perdigão Mortadela fatiada","embalagem":"200g"},
        {"marca":"Sadia",    "nome":"Sadia Presunto fatiado",    "embalagem":"200g"},
        {"marca":"Perdigão", "nome":"Perdigão Presunto fatiado", "embalagem":"200g"},
        {"marca":"Sadia",    "nome":"Sadia Linguiça toscana",    "embalagem":"500g"},
        {"marca":"Perdigão", "nome":"Perdigão Linguiça toscana", "embalagem":"500g"},
        {"marca":"Seara",    "nome":"Seara Linguiça toscana",    "embalagem":"500g"},
        {"marca":"Sadia",    "nome":"Sadia Nuggets de frango",   "embalagem":"300g"},
        {"marca":"Perdigão", "nome":"Perdigão Nuggets de frango","embalagem":"300g"},
        {"marca":"Seara",    "nome":"Seara Nuggets de frango",   "embalagem":"300g"},
        {"marca":"Sadia",    "nome":"Sadia Lasanha bolonhesa",   "embalagem":"600g"},
        {"marca":"Perdigão", "nome":"Perdigão Lasanha bolonhesa","embalagem":"600g"},
        {"marca":"Seara",    "nome":"Seara Lasanha bolonhesa",   "embalagem":"600g"},
    ],
    "Biscoitos": [
        {"marca":"Nabisco",   "nome":"Nabisco Biscoito Oreo original",       "embalagem":"144g"},
        {"marca":"Bauducco",  "nome":"Bauducco Biscoito Wafer chocolate",    "embalagem":"140g"},
        {"marca":"Nestlé",    "nome":"Nestlé Biscoito Passatempo",           "embalagem":"150g"},
        {"marca":"Nestlé",    "nome":"Nestlé Biscoito Bono chocolate",       "embalagem":"140g"},
        {"marca":"Nestlé",    "nome":"Nestlé Biscoito Prestígio",            "embalagem":"132g"},
        {"marca":"Lacta",     "nome":"Lacta Biscoito Clube Social",          "embalagem":"141g"},
        {"marca":"Tostines",  "nome":"Tostines Biscoito cream cracker",      "embalagem":"200g"},
        {"marca":"Marilan",   "nome":"Marilan Biscoito água e sal",          "embalagem":"200g"},
        {"marca":"Piraquê",   "nome":"Piraquê Biscoito água e sal",          "embalagem":"200g"},
        {"marca":"Piraquê",   "nome":"Piraquê Biscoito cream cracker",       "embalagem":"200g"},
        {"marca":"Piraquê",   "nome":"Piraquê Biscoito Goiabinha",           "embalagem":"200g"},
        {"marca":"Vitarella", "nome":"Vitarella Biscoito cream cracker",     "embalagem":"350g"},
        {"marca":"Vitarella", "nome":"Vitarella Biscoito recheado chocolate","embalagem":"130g"},
        {"marca":"Adria",     "nome":"Adria Biscoito cream cracker",         "embalagem":"170g"},
        {"marca":"Fortaleza", "nome":"Fortaleza Biscoito Maria",             "embalagem":"200g"},
        {"marca":"Fortaleza", "nome":"Fortaleza Biscoito cream cracker",     "embalagem":"350g"},
        {"marca":"Richester", "nome":"Richester Biscoito recheado chocolate","embalagem":"130g"},
    ],
    "Massas": [
        {"marca":"Barilla",   "nome":"Barilla Macarrão Espaguete n°5","embalagem":"500g"},
        {"marca":"Renata",    "nome":"Renata Macarrão Espaguete",     "embalagem":"500g"},
        {"marca":"Nissin",    "nome":"Nissin Macarrão Espaguete",     "embalagem":"500g"},
        {"marca":"Barilla",   "nome":"Barilla Macarrão Penne",        "embalagem":"500g"},
        {"marca":"Renata",    "nome":"Renata Macarrão Penne",         "embalagem":"500g"},
        {"marca":"Barilla",   "nome":"Barilla Macarrão Fusilli",      "embalagem":"500g"},
        {"marca":"Nissin",    "nome":"Nissin Miojo galinha caipira",  "embalagem":"85g"},
        {"marca":"Nissin",    "nome":"Nissin Miojo carne",            "embalagem":"85g"},
        {"marca":"Maggi",     "nome":"Maggi Macarrão instantâneo frango","embalagem":"85g"},
        {"marca":"Adria",     "nome":"Adria Macarrão Espaguete",      "embalagem":"500g"},
        {"marca":"Adria",     "nome":"Adria Macarrão Penne",          "embalagem":"500g"},
        {"marca":"Adria",     "nome":"Adria Macarrão Fusilli",        "embalagem":"500g"},
        {"marca":"Vitarella", "nome":"Vitarella Macarrão Espaguete",  "embalagem":"500g"},
        {"marca":"Vitarella", "nome":"Vitarella Macarrão Penne",      "embalagem":"500g"},
        {"marca":"Fortaleza", "nome":"Fortaleza Macarrão Espaguete",  "embalagem":"400g"},
        {"marca":"Isabela",   "nome":"Isabela Macarrão Espaguete",    "embalagem":"400g"},
    ],
    "Mercearia": [
        {"marca":"Tio João",    "nome":"Tio João Arroz branco tipo 1",                  "embalagem":"5kg"},
        {"marca":"Camil",       "nome":"Camil Arroz branco tipo 1",                     "embalagem":"5kg"},
        {"marca":"Camil",       "nome":"Camil Feijão carioca",                          "embalagem":"1kg"},
        {"marca":"Kicaldo",     "nome":"Kicaldo Feijão carioca",                        "embalagem":"1kg"},
        {"marca":"Camil",       "nome":"Camil Feijão preto",                            "embalagem":"1kg"},
        {"marca":"União",       "nome":"União Açúcar cristal",                          "embalagem":"1kg"},
        {"marca":"União",       "nome":"União Açúcar refinado",                         "embalagem":"1kg"},
        {"marca":"Anaconda",    "nome":"Anaconda Farinha de trigo",                     "embalagem":"1kg"},
        {"marca":"Renata",      "nome":"Renata Farinha de trigo",                       "embalagem":"1kg"},
        {"marca":"Dona Benta",  "nome":"Dona Benta Farinha de trigo",                   "embalagem":"1kg"},
        {"marca":"Pilão",       "nome":"Pilão Café torrado e moído tradicional",        "embalagem":"500g"},
        {"marca":"3 Corações",  "nome":"3 Corações Café torrado e moído tradicional",   "embalagem":"500g"},
        {"marca":"Melitta",     "nome":"Melitta Café torrado e moído tradicional",      "embalagem":"500g"},
        {"marca":"Café do Ponto","nome":"Café do Ponto Café torrado e moído tradicional","embalagem":"500g"},
        {"marca":"Caboclo",     "nome":"Caboclo Café torrado e moído tradicional",      "embalagem":"500g"},
    ],
}

# ─── Links verificados ────────────────────────────────────────────────────────
LINKS = {
    "Carrefour Mercado": {
        "Cervejas": {
            "Heineken Lata_350ml":            "https://mercado.carrefour.com.br/cerveja-heineken-lata-sleek-350ml-3180018/p",
            "Heineken Lata_269ml":            "https://mercado.carrefour.com.br/cerveja-heineken-lata-269ml/p",
            "Heineken Long Neck_355ml":       "https://mercado.carrefour.com.br/cerveja-heineken-long-neck-355ml/p",
            "Heineken Garrafa_600ml":         "https://mercado.carrefour.com.br/cerveja-heineken-garrafa-600ml/p",
            "Heineken 0.0_350ml":             "https://mercado.carrefour.com.br/cerveja-lager-zero-alcool-heineken-lata-350ml-3180026/p",
            "Heineken Silver_350ml":          "https://mercado.carrefour.com.br/cerveja-heineken-silver-lata-350ml/p",
            "Skol Lata_350ml":                "https://mercado.carrefour.com.br/cerveja-skol-lata-350ml/p",
            "Skol Lata_269ml":                "https://mercado.carrefour.com.br/cerveja-skol-lata-269ml/p",
            "Brahma Chopp Lata_350ml":        "https://mercado.carrefour.com.br/cerveja-brahma-chopp-lata-350ml/p",
            "Brahma Duplo Malte_350ml":       "https://mercado.carrefour.com.br/cerveja-brahma-duplo-malte-lata-350ml/p",
            "Stella Artois Lata_350ml":       "https://mercado.carrefour.com.br/cerveja-stella-artois-lata-350ml/p",
            "Stella Artois Long Neck_355ml":  "https://mercado.carrefour.com.br/cerveja-stella-artois-long-neck-355ml/p",
            "Corona Extra Long Neck_355ml":   "https://mercado.carrefour.com.br/cerveja-corona-extra-long-neck-355ml/p",
            "Budweiser Lata_350ml":           "https://mercado.carrefour.com.br/cerveja-budweiser-lata-350ml/p",
            "Amstel Lata_350ml":              "https://mercado.carrefour.com.br/cerveja-amstel-lata-350ml/p",
            "Amstel Ultra_350ml":             "https://mercado.carrefour.com.br/cerveja-amstel-ultra-lata-350ml/p",
            "Amstel 0,0_350ml":               "https://mercado.carrefour.com.br/cerveja-amstel-zero-alcool-350ml/p",
            "Spaten Pilsner Long Neck_355ml": "https://mercado.carrefour.com.br/cerveja-spaten-pilsner-long-neck-355ml/p",
            "Original Long Neck_355ml":       "https://mercado.carrefour.com.br/cerveja-original-long-neck-355ml/p",
            "Itaipava Lata_350ml":            "https://mercado.carrefour.com.br/cerveja-itaipava-lata-350ml/p",
        },
        "Embutidos": {
            "Sadia Salsicha Hot Dog_500g":        "https://mercado.carrefour.com.br/salsicha-tradicional-sadia-500g-288527/p",
            "Perdigão Salsicha Hot Dog_500g":     "https://mercado.carrefour.com.br/salsicha-tradicional-perdigao-hot-dog-500-g-3237230/p",
            "Seara Salsicha Hot Dog_500g":        "https://mercado.carrefour.com.br/salsicha-hot-dog-seara-500g/p",
            "Sadia Mortadela fatiada_200g":       "https://mercado.carrefour.com.br/mortadela-sadia-fatiada-200g/p",
            "Perdigão Mortadela fatiada_200g":    "https://mercado.carrefour.com.br/mortadela-perdigao-fatiada-200g/p",
            "Sadia Presunto fatiado_200g":        "https://mercado.carrefour.com.br/presunto-sadia-fatiado-200g/p",
            "Perdigão Presunto fatiado_200g":     "https://mercado.carrefour.com.br/presunto-perdigao-fatiado-200g/p",
            "Sadia Linguiça toscana_500g":        "https://mercado.carrefour.com.br/linguica-toscana-sadia-500g/p",
            "Perdigão Linguiça toscana_500g":     "https://mercado.carrefour.com.br/linguica-toscana-perdigao-500g/p",
            "Seara Linguiça toscana_500g":        "https://mercado.carrefour.com.br/linguica-toscana-seara-500g/p",
            "Sadia Nuggets de frango_300g":       "https://mercado.carrefour.com.br/nuggets-de-frango-sadia-300g/p",
            "Perdigão Nuggets de frango_300g":    "https://mercado.carrefour.com.br/nuggets-de-frango-perdigao-300g/p",
            "Seara Nuggets de frango_300g":       "https://mercado.carrefour.com.br/nuggets-de-frango-seara-300g/p",
            "Sadia Lasanha bolonhesa_600g":       "https://mercado.carrefour.com.br/lasanha-bolonhesa-sadia-600g/p",
            "Perdigão Lasanha bolonhesa_600g":    "https://mercado.carrefour.com.br/lasanha-bolonhesa-perdigao-600g/p",
            "Seara Lasanha bolonhesa_600g":       "https://mercado.carrefour.com.br/lasanha-bolonhesa-seara-600g/p",
        },
        "Biscoitos": {
            "Nabisco Biscoito Oreo original_144g":        "https://mercado.carrefour.com.br/biscoito-oreo-original-144g/p",
            "Bauducco Biscoito Wafer chocolate_140g":     "https://mercado.carrefour.com.br/biscoito-wafer-bauducco-chocolate-140g/p",
            "Nestlé Biscoito Passatempo_150g":            "https://mercado.carrefour.com.br/biscoito-passatempo-nestle-150g/p",
            "Nestlé Biscoito Bono chocolate_140g":        "https://mercado.carrefour.com.br/biscoito-bono-chocolate-nestle-140g/p",
            "Nestlé Biscoito Prestígio_132g":             "https://mercado.carrefour.com.br/biscoito-prestigio-nestle-132g/p",
            "Lacta Biscoito Clube Social_141g":           "https://mercado.carrefour.com.br/biscoito-clube-social-lacta-141g/p",
            "Tostines Biscoito cream cracker_200g":       "https://mercado.carrefour.com.br/biscoito-cream-cracker-tostines-200g/p",
            "Marilan Biscoito água e sal_200g":           "https://mercado.carrefour.com.br/biscoito-agua-sal-marilan-200g/p",
            "Piraquê Biscoito água e sal_200g":           "https://mercado.carrefour.com.br/biscoito-agua-sal-piraque-200g/p",
            "Piraquê Biscoito cream cracker_200g":        "https://mercado.carrefour.com.br/biscoito-cream-cracker-piraque-200g/p",
            "Piraquê Biscoito Goiabinha_200g":            "https://mercado.carrefour.com.br/biscoito-goiabinha-piraque-200g/p",
            "Vitarella Biscoito cream cracker_350g":      "https://mercado.carrefour.com.br/biscoito-cream-cracker-vitarella-350g/p",
            "Vitarella Biscoito recheado chocolate_130g": "https://mercado.carrefour.com.br/biscoito-recheado-chocolate-vitarella-130g/p",
            "Adria Biscoito cream cracker_170g":          "https://mercado.carrefour.com.br/biscoito-cream-cracker-adria-170g/p",
            "Fortaleza Biscoito Maria_200g":              "https://mercado.carrefour.com.br/biscoito-maria-fortaleza-200g/p",
            "Fortaleza Biscoito cream cracker_350g":      "https://mercado.carrefour.com.br/biscoito-cream-cracker-fortaleza-350g/p",
            "Richester Biscoito recheado chocolate_130g": "https://mercado.carrefour.com.br/biscoito-recheado-chocolate-richester-130g/p",
        },
        "Massas": {
            "Barilla Macarrão Espaguete n°5_500g":   "https://mercado.carrefour.com.br/macarrao-espaguete-barilla-n5-500g/p",
            "Renata Macarrão Espaguete_500g":         "https://mercado.carrefour.com.br/macarrao-espaguete-renata-500g/p",
            "Nissin Macarrão Espaguete_500g":         "https://mercado.carrefour.com.br/macarrao-espaguete-nissin-500g/p",
            "Barilla Macarrão Penne_500g":            "https://mercado.carrefour.com.br/macarrao-penne-barilla-500g/p",
            "Renata Macarrão Penne_500g":             "https://mercado.carrefour.com.br/macarrao-penne-renata-500g/p",
            "Barilla Macarrão Fusilli_500g":          "https://mercado.carrefour.com.br/macarrao-fusilli-barilla-500g/p",
            "Nissin Miojo galinha caipira_85g":       "https://mercado.carrefour.com.br/miojo-nissin-galinha-caipira-85g/p",
            "Nissin Miojo carne_85g":                 "https://mercado.carrefour.com.br/miojo-nissin-carne-85g/p",
            "Maggi Macarrão instantâneo frango_85g":  "https://mercado.carrefour.com.br/macarrao-instantaneo-maggi-frango-85g/p",
            "Adria Macarrão Espaguete_500g":          "https://mercado.carrefour.com.br/macarrao-espaguete-adria-500g/p",
            "Adria Macarrão Penne_500g":              "https://mercado.carrefour.com.br/macarrao-penne-adria-500g/p",
            "Adria Macarrão Fusilli_500g":            "https://mercado.carrefour.com.br/macarrao-fusilli-adria-500g/p",
            "Vitarella Macarrão Espaguete_500g":      "https://mercado.carrefour.com.br/macarrao-espaguete-vitarella-500g/p",
            "Vitarella Macarrão Penne_500g":          "https://mercado.carrefour.com.br/macarrao-penne-vitarella-500g/p",
            "Fortaleza Macarrão Espaguete_400g":      "https://mercado.carrefour.com.br/macarrao-espaguete-fortaleza-400g/p",
            "Isabela Macarrão Espaguete_400g":        "https://mercado.carrefour.com.br/macarrao-espaguete-isabela-400g/p",
        },
        "Mercearia": {
            "Tio João Arroz branco tipo 1_5kg":                  "https://mercado.carrefour.com.br/arroz-branco-longofino-tipo-1-tio-joao-5-kg/p",
            "Camil Arroz branco tipo 1_5kg":                     "https://mercado.carrefour.com.br/arroz-branco-tipo-1-camil-5kg/p",
            "Camil Feijão carioca_1kg":                          "https://mercado.carrefour.com.br/feijao-carioca-camil-1kg/p",
            "Kicaldo Feijão carioca_1kg":                        "https://mercado.carrefour.com.br/feijao-carioca-kicaldo-1kg/p",
            "Camil Feijão preto_1kg":                            "https://mercado.carrefour.com.br/feijao-preto-camil-1kg/p",
            "União Açúcar cristal_1kg":                          "https://mercado.carrefour.com.br/acucar-cristal-uniao-1kg/p",
            "União Açúcar refinado_1kg":                         "https://mercado.carrefour.com.br/acucar-refinado-uniao-1kg/p",
            "Anaconda Farinha de trigo_1kg":                     "https://mercado.carrefour.com.br/farinha-de-trigo-anaconda-1kg/p",
            "Renata Farinha de trigo_1kg":                       "https://mercado.carrefour.com.br/farinha-de-trigo-renata-1kg/p",
            "Dona Benta Farinha de trigo_1kg":                   "https://mercado.carrefour.com.br/farinha-de-trigo-dona-benta-1kg/p",
            "Pilão Café torrado e moído tradicional_500g":       "https://mercado.carrefour.com.br/cafe-torrado-e-moido-tradicional-pilao-500g/p",
            "3 Corações Café torrado e moído tradicional_500g":  "https://mercado.carrefour.com.br/cafe-torrado-moido-3-coracoes-tradicional-500g/p",
            "Melitta Café torrado e moído tradicional_500g":     "https://mercado.carrefour.com.br/cafe-torrado-e-moido-tradicional-melitta-500g/p",
            "Café do Ponto Café torrado e moído tradicional_500g":"https://mercado.carrefour.com.br/cafe-do-ponto-torrado-moido-500g/p",
            "Caboclo Café torrado e moído tradicional_500g":     "https://mercado.carrefour.com.br/cafe-caboclo-torrado-moido-500g/p",
        },
    },
    "Pão de Açúcar": {
        "Cervejas": {
            "Heineken Lata_350ml":            "https://www.paodeacucar.com/produto/1606865/cerveja-lager-heineken-lata-350ml",
            "Heineken 0.0_350ml":             "https://www.paodeacucar.com/produto/462217/cerveja-lager-premium-puro-malte-zero-alcool-heineken-lata-350ml",
            "Skol Lata_350ml":                "https://www.paodeacucar.com/produto/cerveja-skol-lata-350ml",
            "Brahma Chopp Lata_350ml":        "https://www.paodeacucar.com/produto/cerveja-brahma-chopp-350ml",
            "Stella Artois Lata_350ml":       "https://www.paodeacucar.com/produto/cerveja-stella-artois-350ml",
            "Corona Extra Long Neck_355ml":   "https://www.paodeacucar.com/produto/cerveja-corona-extra-355ml",
            "Budweiser Lata_350ml":           "https://www.paodeacucar.com/produto/cerveja-budweiser-lata-350ml",
            "Amstel Lata_350ml":              "https://www.paodeacucar.com/produto/cerveja-amstel-350ml",
            "Spaten Pilsner Long Neck_355ml": "https://www.paodeacucar.com/produto/cerveja-spaten-355ml",
            "Original Long Neck_355ml":       "https://www.paodeacucar.com/produto/cerveja-original-355ml",
            "Itaipava Lata_350ml":            "https://www.paodeacucar.com/produto/cerveja-itaipava-350ml",
        },
        "Embutidos": {
            "Sadia Salsicha Hot Dog_500g":    "https://www.paodeacucar.com/produto/salsicha-sadia-hot-dog-500g",
            "Perdigão Salsicha Hot Dog_500g": "https://www.paodeacucar.com/produto/salsicha-perdigao-hot-dog-500g",
            "Seara Salsicha Hot Dog_500g":    "https://www.paodeacucar.com/produto/salsicha-seara-hot-dog-500g",
            "Sadia Mortadela fatiada_200g":   "https://www.paodeacucar.com/produto/mortadela-sadia-fatiada-200g",
            "Sadia Presunto fatiado_200g":    "https://www.paodeacucar.com/produto/presunto-sadia-fatiado-200g",
            "Sadia Linguiça toscana_500g":    "https://www.paodeacucar.com/produto/linguica-toscana-sadia-500g",
            "Sadia Nuggets de frango_300g":   "https://www.paodeacucar.com/produto/nuggets-sadia-hot-300g",
            "Seara Nuggets de frango_300g":   "https://www.paodeacucar.com/produto/nuggets-seara-300g",
            "Sadia Lasanha bolonhesa_600g":   "https://www.paodeacucar.com/produto/lasanha-sadia-bolonhesa-600g",
        },
        "Biscoitos": {
            "Nabisco Biscoito Oreo original_144g":    "https://www.paodeacucar.com/produto/biscoito-oreo-original-144g",
            "Bauducco Biscoito Wafer chocolate_140g": "https://www.paodeacucar.com/produto/biscoito-wafer-bauducco-chocolate-140g",
            "Nestlé Biscoito Passatempo_150g":        "https://www.paodeacucar.com/produto/biscoito-passatempo-nestle-150g",
            "Nestlé Biscoito Bono chocolate_140g":    "https://www.paodeacucar.com/produto/biscoito-bono-chocolate-nestle-140g",
            "Lacta Biscoito Clube Social_141g":       "https://www.paodeacucar.com/produto/biscoito-clube-social-141g",
            "Tostines Biscoito cream cracker_200g":   "https://www.paodeacucar.com/produto/biscoito-cream-cracker-tostines-200g",
            "Piraquê Biscoito água e sal_200g":       "https://www.paodeacucar.com/produto/biscoito-agua-sal-piraque-200g",
            "Vitarella Biscoito cream cracker_350g":  "https://www.paodeacucar.com/produto/biscoito-cream-cracker-vitarella-350g",
            "Adria Biscoito cream cracker_170g":      "https://www.paodeacucar.com/produto/biscoito-cream-cracker-adria-170g",
        },
        "Massas": {
            "Barilla Macarrão Espaguete n°5_500g": "https://www.paodeacucar.com/produto/macarrao-espaguete-barilla-500g",
            "Renata Macarrão Espaguete_500g":       "https://www.paodeacucar.com/produto/macarrao-espaguete-renata-500g",
            "Nissin Miojo galinha caipira_85g":     "https://www.paodeacucar.com/produto/miojo-nissin-galinha-caipira-85g",
            "Nissin Miojo carne_85g":               "https://www.paodeacucar.com/produto/miojo-nissin-carne-85g",
            "Adria Macarrão Espaguete_500g":        "https://www.paodeacucar.com/produto/macarrao-espaguete-adria-500g",
            "Vitarella Macarrão Espaguete_500g":    "https://www.paodeacucar.com/produto/macarrao-espaguete-vitarella-500g",
        },
        "Mercearia": {
            "Tio João Arroz branco tipo 1_5kg":                  "https://www.paodeacucar.com/produto/arroz-tio-joao-branco-5kg",
            "Camil Arroz branco tipo 1_5kg":                     "https://www.paodeacucar.com/produto/arroz-camil-branco-5kg",
            "Camil Feijão carioca_1kg":                          "https://www.paodeacucar.com/produto/feijao-carioca-camil-1kg",
            "Kicaldo Feijão carioca_1kg":                        "https://www.paodeacucar.com/produto/feijao-carioca-kicaldo-1kg",
            "Camil Feijão preto_1kg":                            "https://www.paodeacucar.com/produto/feijao-preto-camil-1kg",
            "União Açúcar cristal_1kg":                          "https://www.paodeacucar.com/produto/acucar-cristal-uniao-1kg",
            "União Açúcar refinado_1kg":                         "https://www.paodeacucar.com/produto/acucar-refinado-uniao-1kg",
            "Renata Farinha de trigo_1kg":                       "https://www.paodeacucar.com/produto/farinha-trigo-renata-1kg",
            "Pilão Café torrado e moído tradicional_500g":       "https://www.paodeacucar.com/produto/cafe-pilao-tradicional-500g",
            "3 Corações Café torrado e moído tradicional_500g":  "https://www.paodeacucar.com/produto/cafe-3-coracoes-tradicional-500g",
            "Melitta Café torrado e moído tradicional_500g":     "https://www.paodeacucar.com/produto/cafe-melitta-tradicional-500g",
        },
    },
    "Extra": {
        "Cervejas": {
            "Heineken Lata_350ml":           "https://www.extra.com.br/cerveja-heineken-pilsen-12-unidades-lata-350ml/p/55021179",
            "Skol Lata_350ml":               "https://www.extra.com.br/cerveja-skol-lata-350ml/p",
            "Brahma Chopp Lata_350ml":       "https://www.extra.com.br/cerveja-brahma-chopp-350ml/p",
            "Stella Artois Lata_350ml":      "https://www.extra.com.br/cerveja-stella-artois-350ml/p",
            "Corona Extra Long Neck_355ml":  "https://www.extra.com.br/cerveja-corona-extra-355ml/p",
            "Budweiser Lata_350ml":          "https://www.extra.com.br/cerveja-budweiser-350ml/p",
        },
        "Embutidos": {
            "Sadia Salsicha Hot Dog_500g":    "https://www.extra.com.br/salsicha-sadia-hot-dog-500g/p",
            "Perdigão Salsicha Hot Dog_500g": "https://www.extra.com.br/salsicha-perdigao-hot-dog-500g/p",
            "Sadia Mortadela fatiada_200g":   "https://www.extra.com.br/mortadela-sadia-fatiada-200g/p",
            "Sadia Presunto fatiado_200g":    "https://www.extra.com.br/presunto-sadia-fatiado-200g/p",
            "Sadia Nuggets de frango_300g":   "https://www.extra.com.br/nuggets-sadia-hot-300g/p",
            "Sadia Lasanha bolonhesa_600g":   "https://www.extra.com.br/lasanha-sadia-bolonhesa-600g/p",
        },
        "Biscoitos": {
            "Nabisco Biscoito Oreo original_144g":  "https://www.extra.com.br/biscoito-oreo-original-144g/p",
            "Nestlé Biscoito Passatempo_150g":      "https://www.extra.com.br/biscoito-passatempo-nestle-150g/p",
            "Nestlé Biscoito Bono chocolate_140g":  "https://www.extra.com.br/biscoito-bono-chocolate-140g/p",
            "Lacta Biscoito Clube Social_141g":     "https://www.extra.com.br/biscoito-clube-social-141g/p",
            "Tostines Biscoito cream cracker_200g": "https://www.extra.com.br/biscoito-cream-cracker-tostines-200g/p",
            "Piraquê Biscoito água e sal_200g":     "https://www.extra.com.br/biscoito-agua-sal-piraque-200g/p",
        },
        "Massas": {
            "Barilla Macarrão Espaguete n°5_500g": "https://www.extra.com.br/macarrao-espaguete-barilla-500g/p",
            "Renata Macarrão Espaguete_500g":       "https://www.extra.com.br/macarrao-espaguete-renata-500g/p",
            "Nissin Miojo galinha caipira_85g":     "https://www.extra.com.br/miojo-nissin-galinha-caipira-85g/p",
            "Nissin Miojo carne_85g":               "https://www.extra.com.br/miojo-nissin-carne-85g/p",
            "Adria Macarrão Espaguete_500g":        "https://www.extra.com.br/macarrao-espaguete-adria-500g/p",
        },
        "Mercearia": {
            "Tio João Arroz branco tipo 1_5kg":                  "https://www.extra.com.br/arroz-tio-joao-branco-tipo-1-5kg/p",
            "Camil Arroz branco tipo 1_5kg":                     "https://www.extra.com.br/arroz-camil-branco-tipo-1-5kg/p",
            "Camil Feijão carioca_1kg":                          "https://www.extra.com.br/feijao-carioca-camil-1kg/p",
            "União Açúcar cristal_1kg":                          "https://www.extra.com.br/acucar-cristal-uniao-1kg/p",
            "União Açúcar refinado_1kg":                         "https://www.extra.com.br/acucar-refinado-uniao-1kg/p",
            "Renata Farinha de trigo_1kg":                       "https://www.extra.com.br/farinha-trigo-renata-1kg/p",
            "Pilão Café torrado e moído tradicional_500g":       "https://www.extra.com.br/cafe-pilao-torrado-moido-tradicional-500g/p",
            "3 Corações Café torrado e moído tradicional_500g":  "https://www.extra.com.br/cafe-3-coracoes-tradicional-500g/p",
            "Melitta Café torrado e moído tradicional_500g":     "https://www.extra.com.br/cafe-melitta-tradicional-500g/p",
        },
    },
    "Atacadão": {
        "Cervejas": {
            "Heineken Lata_350ml":            "https://www.atacadao.com.br/cerveja-heineken-sleek-86733/p",
            "Heineken 0.0_350ml":             "https://www.atacadao.com.br/cerveja-heineken-zero-sleek-86709/p",
            "Skol Lata_350ml":                "https://www.atacadao.com.br/cerveja-skol-pilsen-18650-13267/p",
            "Brahma Chopp Lata_350ml":        "https://www.atacadao.com.br/cerveja-brahma-extra-lager-57660-11677/p",
            "Stella Artois Lata_350ml":       "https://www.atacadao.com.br/cerveja-stella-artois-lata-com-350ml-65730/p",
            "Corona Extra Long Neck_355ml":   "https://www.atacadao.com.br/cerveja-corona-extra-50175/p",
            "Budweiser Lata_350ml":           "https://www.atacadao.com.br/cerveja-budweiser-sleek-lata-com-350ml-80258-11811/p",
            "Amstel Lata_350ml":              "https://www.atacadao.com.br/cerveja-amstel-sleek-86708-11276/p",
            "Spaten Pilsner Long Neck_355ml": "https://www.atacadao.com.br/cerveja-spaten-puro-malte-long-neck-com-355ml-74631-13356/p",
            "Itaipava Lata_350ml":            "https://www.atacadao.com.br/cerveja-itaipava-9850-12669/p",
        },
        "Embutidos": {
            "Sadia Salsicha Hot Dog_500g":    "https://www.atacadao.com.br/salsicha-hot-dog-sadia-resfriada-49270/p",
            "Perdigão Salsicha Hot Dog_500g": "https://www.atacadao.com.br/salsicha-hot-dog-perdigao-resfriada-5970/p",
            "Seara Salsicha Hot Dog_500g":    "https://www.atacadao.com.br/salsicha-seara-congelada-37028/p",
            "Sadia Mortadela fatiada_200g":   "https://www.atacadao.com.br/mortadela-defumada-sadia-fatiado-84464/p",
            "Sadia Linguiça toscana_500g":    "https://www.atacadao.com.br/linguica-toscana-sadia-congelada-22113/p",
            "Sadia Nuggets de frango_300g":   "https://www.atacadao.com.br/nuggets-de-frango-sadia-tradicional-19580/p",
            "Sadia Lasanha bolonhesa_600g":   "https://www.atacadao.com.br/lasanha-sadia-congelada-bolonhesa-embalagem-com-600g-54196/p",
            "Perdigão Lasanha bolonhesa_600g":"https://www.atacadao.com.br/lasanha-perdigao-congelada-bolonhesa-58251/p",
            "Seara Lasanha bolonhesa_600g":   "https://www.atacadao.com.br/lasanha-seara-congelada-bolonhesa-32549/p",
        },
        "Biscoitos": {
            "Nabisco Biscoito Oreo original_144g":        "https://www.atacadao.com.br/biscoito-recheado-oreo-original-61657/p",
            "Nestlé Biscoito Passatempo_150g":            "https://www.atacadao.com.br/biscoito-passatempo-nestle-leite-25728/p",
            "Nestlé Biscoito Prestígio_132g":             "https://www.atacadao.com.br/biscoito-recheado-nestle-prestigio-38279/p",
            "Lacta Biscoito Clube Social_141g":           "https://www.atacadao.com.br/biscoito-club-social-original-54629/p",
            "Vitarella Biscoito cream cracker_350g":      "https://www.atacadao.com.br/biscoito-cream-cracker-vitarella-tradicional-75658/p",
            "Adria Biscoito cream cracker_170g":          "https://www.atacadao.com.br/biscoito-adria-cream-cracker-86314/p",
            "Fortaleza Biscoito cream cracker_350g":      "https://www.atacadao.com.br/biscoito-fortaleza-cream-cracker-pacote-com-350g-75661/p",
            "Richester Biscoito recheado chocolate_130g": "https://www.atacadao.com.br/biscoito-richester-cream-cracker-pacote-com-350g-75676/p",
        },
        "Massas": {
            "Barilla Macarrão Espaguete n°5_500g": "https://www.atacadao.com.br/macarrao-espaguete-barilla-500g/p",
            "Renata Macarrão Espaguete_500g":       "https://www.atacadao.com.br/macarrao-espaguete-renata-500g/p",
            "Nissin Miojo galinha caipira_85g":     "https://www.atacadao.com.br/miojo-nissin-galinha-caipira-85g/p",
            "Nissin Miojo carne_85g":               "https://www.atacadao.com.br/miojo-nissin-carne-85g/p",
            "Adria Macarrão Espaguete_500g":        "https://www.atacadao.com.br/macarrao-com-ovos-adria-espaguete-furado-34563/p",
            "Vitarella Macarrão Espaguete_500g":    "https://www.atacadao.com.br/macarrao-comum-vitarella-espaguete-fino-9870/p",
            "Fortaleza Macarrão Espaguete_400g":    "https://www.atacadao.com.br/macarrao-de-semola-fortaleza-espaguete-85114/p",
            "Isabela Macarrão Espaguete_400g":      "https://www.atacadao.com.br/macarrao-de-semola-isabela-espaguete-85652/p",
        },
        "Mercearia": {
            "Tio João Arroz branco tipo 1_5kg":                  "https://www.atacadao.com.br/arroz-tio-joao-agulhinha---tipo-1-5148-15022/p",
            "Camil Arroz branco tipo 1_5kg":                     "https://www.atacadao.com.br/arroz-camil-agulhinha---tipo-1-pacote-com-5kg-12658-13743/p",
            "Camil Feijão carioca_1kg":                          "https://www.atacadao.com.br/feijao-carioca-camil-tipo-1-pacote-com-1kg-7382-9742/p",
            "Kicaldo Feijão carioca_1kg":                        "https://www.atacadao.com.br/feijao-carioca-kicaldo-tipo-1-pacote-com-1kg-11874/p",
            "Camil Feijão preto_1kg":                            "https://www.atacadao.com.br/feijao-preto-camil-1kg/p",
            "União Açúcar cristal_1kg":                          "https://www.atacadao.com.br/acucar-uniao-cristal-1kg/p",
            "União Açúcar refinado_1kg":                         "https://www.atacadao.com.br/acucar-uniao-refinado-21176-2371/p",
            "Anaconda Farinha de trigo_1kg":                     "https://www.atacadao.com.br/farinha-de-trigo-anaconda-tipo-1-6852/p",
            "Renata Farinha de trigo_1kg":                       "https://www.atacadao.com.br/farinha-de-trigo-renata-1kg/p",
            "Dona Benta Farinha de trigo_1kg":                   "https://www.atacadao.com.br/farinha-de-trigo-dona-benta-tipo-1-pacote-com-1kg-23162-8563/p",
            "Pilão Café torrado e moído tradicional_500g":       "https://www.atacadao.com.br/cafe-pilao-vacuo-18437/p",
            "3 Corações Café torrado e moído tradicional_500g":  "https://www.atacadao.com.br/cafe-3-coracoes-tradicional-23371-915/p",
            "Melitta Café torrado e moído tradicional_500g":     "https://www.atacadao.com.br/cafe-melitta-tradicional-vacuo-caixeta-com-500g-18816/p",
            "Caboclo Café torrado e moído tradicional_500g":     "https://www.atacadao.com.br/cafe-caboclo-tradicional-500g/p",
        },
    },
}

# ─── Termos de busca Mateus ───────────────────────────────────────────────────
BUSCA_MATEUS = {
    "Cervejas":  {
        "Heineken Lata_350ml":       "cerveja heineken lata 350ml",
        "Skol Lata_350ml":           "cerveja skol lata 350ml",
        "Brahma Chopp Lata_350ml":   "cerveja brahma chopp 350ml",
        "Budweiser Lata_350ml":      "cerveja budweiser lata 350ml",
        "Itaipava Lata_350ml":       "cerveja itaipava lata 350ml",
        "Amstel Lata_350ml":         "cerveja amstel lata 350ml",
    },
    "Embutidos": {
        "Sadia Salsicha Hot Dog_500g":   "salsicha sadia hot dog 500g",
        "Sadia Nuggets de frango_300g":  "nuggets sadia frango 300g",
        "Sadia Lasanha bolonhesa_600g":  "lasanha sadia bolonhesa 600g",
        "Sadia Mortadela fatiada_200g":  "mortadela sadia fatiada 200g",
        "Sadia Linguiça toscana_500g":   "linguica toscana sadia 500g",
    },
    "Biscoitos": {
        "Nabisco Biscoito Oreo original_144g":    "biscoito oreo 144g",
        "Vitarella Biscoito cream cracker_350g":  "biscoito cream cracker vitarella",
        "Fortaleza Biscoito cream cracker_350g":  "biscoito cream cracker fortaleza",
        "Nestlé Biscoito Passatempo_150g":        "biscoito passatempo nestle 150g",
        "Lacta Biscoito Clube Social_141g":       "biscoito clube social 141g",
    },
    "Massas": {
        "Adria Macarrão Espaguete_500g":     "macarrao espaguete adria 500g",
        "Vitarella Macarrão Espaguete_500g": "macarrao espaguete vitarella 500g",
        "Nissin Miojo galinha caipira_85g":  "miojo nissin galinha caipira",
        "Nissin Miojo carne_85g":            "miojo nissin carne",
        "Barilla Macarrão Espaguete n°5_500g": "macarrao espaguete barilla 500g",
    },
    "Mercearia": {
        "Tio João Arroz branco tipo 1_5kg":                  "arroz tio joao branco 5kg",
        "Camil Arroz branco tipo 1_5kg":                     "arroz camil branco tipo 1 5kg",
        "Camil Feijão carioca_1kg":                          "feijao carioca camil 1kg",
        "União Açúcar cristal_1kg":                          "acucar cristal uniao 1kg",
        "Pilão Café torrado e moído tradicional_500g":       "cafe pilao tradicional 500g",
        "3 Corações Café torrado e moído tradicional_500g":  "cafe 3 coracoes tradicional 500g",
        "Melitta Café torrado e moído tradicional_500g":     "cafe melitta tradicional 500g",
    },
}

# ─── Seletores CSS expandidos por supermercado ───────────────────────────────
SELETORES = {
    "Carrefour Mercado": [
        # VTEX / React — seletores primários
        "span[class*='sellingPrice']",
        "span[class*='SellingPrice']",
        # Seletores alternativos encontrados em SPAs da VTEX
        "[class*='price-selling'] [class*='integer']",
        "div[class*='priceBox'] span",
        # data-testid patterns
        "[data-testid='price-value']",
        "[data-testid*='selling']",
        # Genéricos de preço
        "[class*='Price']:not([class*='list']):not([class*='List'])",
    ],
    "Pão de Açúcar": [
        # GPA / VTEX IO
        ".sales .value",
        "span.sales",
        ".price__sales",
        "[class*='sales'] [class*='value']",
        # VTEX genérico
        "span[class*='sellingPrice']",
        "[class*='ProductPrice'] [class*='selling']",
        ".product-price",
    ],
    "Extra": [
        # GPA (mesmo grupo do Pão de Açúcar)
        ".sales .value",
        "span.sales",
        "[class*='price-selling']",
        "div[class*='ProductPrice']",
        "span[class*='sellingPrice']",
        ".product-price .value",
        "[class*='priceContainer'] span",
    ],
    "Atacadão": [
        # VTEX legado
        "span[class*='sellingPrice']",
        "h3.valornormal",
        ".valornormal",
        # VTEX IO
        ".price-best-price",
        "span[class*='selling']",
        "[class*='Price']:not([class*='list'])",
        # Atacadão específico
        "[class*='bestPrice']",
        "[class*='priceValue']",
    ],
    "Mateus": [
        "[class*='price'] [class*='value']",
        "span[class*='Price']",
        "[class*='selling']",
        ".product-price",
        "[data-price]",
        "[class*='preco']",
    ],
}

# ─── Banco de dados ───────────────────────────────────────────────────────────
def init_db():
    DB_PATH.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS precos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        data_coleta TEXT NOT NULL, horario_coleta TEXT NOT NULL,
        supermercado TEXT NOT NULL, categoria TEXT NOT NULL,
        marca TEXT NOT NULL, nome_produto TEXT NOT NULL, embalagem TEXT NOT NULL,
        cidade TEXT NOT NULL, uf TEXT NOT NULL, regiao TEXT NOT NULL,
        preco_atual REAL, preco_original REAL,
        em_promocao INTEGER DEFAULT 0, disponivel INTEGER DEFAULT 1,
        url TEXT, url_recuperada TEXT, rota_css INTEGER, tentativas INTEGER DEFAULT 1, erro TEXT
    )""")
    cols = [r[1] for r in con.execute("PRAGMA table_info(precos)").fetchall()]
    for col, typ in [("categoria","TEXT"),("rota_css","INTEGER"),
                     ("url_recuperada","TEXT"),("tentativas","INTEGER")]:
        if col not in cols:
            con.execute(f"ALTER TABLE precos ADD COLUMN {col} {typ}")
    for idx in [
        "CREATE INDEX IF NOT EXISTS idx_data    ON precos(data_coleta)",
        "CREATE INDEX IF NOT EXISTS idx_produto ON precos(marca,nome_produto,embalagem)",
        "CREATE INDEX IF NOT EXISTS idx_cat     ON precos(categoria)",
        "CREATE INDEX IF NOT EXISTS idx_url     ON precos(url)",
    ]:
        con.execute(idx)
    con.commit()
    return con

def inserir(con, r):
    con.execute("""INSERT INTO precos
        (data_coleta,horario_coleta,supermercado,categoria,marca,nome_produto,
         embalagem,cidade,uf,regiao,preco_atual,preco_original,
         em_promocao,disponivel,url,url_recuperada,rota_css,tentativas,erro)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
        r["data_coleta"],r["horario_coleta"],r["supermercado"],r["categoria"],
        r["marca"],r["nome_produto"],r["embalagem"],r["cidade"],r["uf"],r["regiao"],
        r.get("preco_atual"),r.get("preco_original"),
        int(r.get("em_promocao",False)),int(r.get("disponivel",True)),
        r.get("url"),r.get("url_recuperada"),r.get("rota_css"),
        r.get("tentativas",1),r.get("erro"),
    ))

def url_recuperada_do_banco(con, supermercado, nome_produto, embalagem):
    """Reutiliza URL que já foi recuperada com sucesso em coletas anteriores."""
    row = con.execute("""
        SELECT url_recuperada FROM precos
        WHERE supermercado=? AND nome_produto=? AND embalagem=?
          AND url_recuperada IS NOT NULL AND preco_atual IS NOT NULL
        ORDER BY data_coleta DESC LIMIT 1
    """, (supermercado, nome_produto, embalagem)).fetchone()
    return row[0] if row else None

# ─── Extração de preço ────────────────────────────────────────────────────────
def extrair_preco(texto):
    if not texto: return None
    nums = re.findall(r'\d+[.,]\d{2}', re.sub(r'\s+','',str(texto).replace('\xa0','')))
    return float(nums[0].replace(',','.')) if nums else None

def extrair_via_json_ld(page):
    try:
        for s in page.query_selector_all('script[type="application/ld+json"]'):
            try:
                data = json.loads(s.inner_text())
                for item in (data if isinstance(data,list) else [data]):
                    offers = item.get("offers") or item.get("Offers")
                    if offers:
                        if isinstance(offers,list): offers = offers[0]
                        p = offers.get("price") or offers.get("lowPrice")
                        if p: return float(str(p).replace(',','.'))
            except Exception: continue
    except Exception: pass
    return None

def extrair_via_meta(page):
    try:
        for sel in [
            'meta[property="product:price:amount"]',
            'meta[name="price"]',
            'meta[itemprop="price"]',
            'meta[property="og:price:amount"]',
        ]:
            el = page.query_selector(sel)
            if el:
                p = extrair_preco(el.get_attribute("content"))
                if p: return p
    except Exception: pass
    return None

def extrair_via_js(page):
    try:
        return page.evaluate(r"""() => {
            // 1. TreeWalker por texto R$ X,XX
            const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
            let n;
            while(n = w.nextNode()) {
                const t = n.textContent.trim();
                if (/R\$\s*\d+[.,]\d{2}/.test(t) && t.length < 30) {
                    const m = t.match(/\d+[.,]\d{2}/);
                    if (m) return parseFloat(m[0].replace(',','.'));
                }
            }
            // 2. Seletores data-* e atributos
            const attrSels = [
                '[data-price]','[data-selling-price]','[data-product-price]',
                '[itemprop="price"]','[class*="priceValue"]','[class*="bestPrice"]',
            ];
            for (const sel of attrSels) {
                for (const el of document.querySelectorAll(sel)) {
                    const dp = el.getAttribute('data-price')
                             || el.getAttribute('data-selling-price')
                             || el.getAttribute('data-product-price')
                             || el.getAttribute('content');
                    if (dp) {
                        const m = dp.match(/\d+[.,]\d{2}/);
                        if (m) return parseFloat(m[0].replace(',','.'));
                    }
                }
            }
            // 3. Classes de preço genéricas
            const classSels = [
                '[class*="price"]','[class*="Price"]','[class*="preco"]','[class*="Preco"]',
            ];
            for (const sel of classSels) {
                for (const el of document.querySelectorAll(sel)) {
                    const t = el.textContent.trim();
                    if (/R\$\s*\d+[.,]\d{2}/.test(t) && t.length < 30) {
                        const m = t.match(/\d+[.,]\d{2}/);
                        if (m) return parseFloat(m[0].replace(',','.'));
                    }
                }
            }
            return null;
        }""")
    except Exception: return None

def scroll_e_aguarda(page, supermercado):
    """Scroll para forçar lazy-load + espera adaptativa por supermercado."""
    try:
        # Scroll suave até o meio da página (onde geralmente fica o preço)
        page.evaluate("window.scrollTo({top: 500, behavior: 'smooth'})")
        page.wait_for_timeout(800)
        page.evaluate("window.scrollTo({top: 0, behavior: 'smooth'})")
        page.wait_for_timeout(400)
    except Exception: pass

    # Espera por seletor de preço específico (mais confiável que timeout fixo)
    seletores_espera = {
        "Carrefour Mercado": "span[class*='sellingPrice'], [data-testid='price-value']",
        "Pão de Açúcar":     ".sales .value, span[class*='sellingPrice']",
        "Extra":             ".sales .value, span[class*='sellingPrice']",
        "Atacadão":          "span[class*='sellingPrice'], .valornormal, .price-best-price",
    }
    sel = seletores_espera.get(supermercado)
    if sel:
        try:
            page.wait_for_selector(sel, timeout=5000, state="visible")
        except Exception:
            pass  # se não aparecer, tenta as rotas de extração mesmo assim

def pagina_valida(page):
    try:
        titulo = page.title().lower()
        url    = page.url.lower()
        if any(x in titulo for x in ["página não encontrada","not found","erro 404","indisponível","acesso negado"]):
            return False
        if any(x in url for x in ["404","not-found","erro","blocked","captcha"]):
            return False
        tem_produto = page.query_selector(
            'h1, [class*="product"], [class*="Product"], '
            '[class*="pdp"], [class*="item-page"], [itemtype*="Product"]'
        )
        return tem_produto is not None
    except Exception:
        return True

def recuperar_url(page, nome_produto, embalagem, supermercado, con=None):
    """
    Rota 0 em 3 etapas:
    1. URL recuperada anteriormente no banco (mais rápido)
    2. Tag canonical da página atual
    3. Busca no site
    """
    # Etapa 1: banco de dados
    if con:
        url_banco = url_recuperada_do_banco(con, supermercado, nome_produto, embalagem)
        if url_banco:
            return url_banco, "banco"

    # Etapa 2: canonical
    try:
        canonical = page.query_selector('link[rel="canonical"]')
        if canonical:
            href = canonical.get_attribute("href")
            if href and href != page.url and "/p" in href:
                return href, "canonical"
    except Exception: pass

    # Etapa 3: busca no site
    if supermercado not in BUSCA_URL:
        return None, None
    try:
        query = f"{nome_produto} {embalagem}".replace(" ","+")
        url_busca = BUSCA_URL[supermercado].format(q=query)
        page.goto(url_busca, wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(2500)
        sel = LINK_SELETOR.get(supermercado, 'a[href*="/p"]')
        # Pega o primeiro link de produto válido (ignora links de categoria)
        for link in page.query_selector_all(sel):
            href = link.get_attribute("href") or ""
            if not href: continue
            if not href.startswith("http"):
                base = "/".join(BUSCA_URL[supermercado].split("/")[:3])
                href = base + href
            # Filtra links de listagem/categoria
            if any(x in href for x in ["/busca","/categoria","/c/","/colecao"]):
                continue
            return href, "busca"
    except Exception: pass
    return None, None

def coletar_pagina(page, url, supermercado, nome_produto="", embalagem="", con=None, tentativa=1):
    resultado = {
        "url": url, "disponivel": False, "preco_atual": None,
        "preco_original": None, "em_promocao": False,
        "rota_css": None, "url_recuperada": None, "tentativas": tentativa, "erro": None,
    }
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=32000)

        # Injeta CEP após carregar a página (para sites que leem do localStorage)
        scroll_e_aguarda(page, supermercado)

        # Rota 0 — página inválida → recupera URL
        if not pagina_valida(page):
            nova_url, origem = recuperar_url(page, nome_produto, embalagem, supermercado, con)
            if nova_url:
                resultado["url_recuperada"] = nova_url
                page.goto(nova_url, wait_until="domcontentloaded", timeout=28000)
                scroll_e_aguarda(page, supermercado)
            else:
                resultado["erro"] = "pagina_invalida_url_nao_recuperada"
                return resultado

        # Rotas 1-N: seletores CSS em cascata
        for i, sel in enumerate(SELETORES.get(supermercado, []), 1):
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    p = extrair_preco(el.inner_text())
                    if p and 0.5 < p < 10000:
                        resultado["preco_atual"] = p
                        resultado["rota_css"] = i
                        break
            except Exception: continue

        # Rota JSON-LD
        if not resultado["preco_atual"]:
            p = extrair_via_json_ld(page)
            if p and 0.5 < p < 10000:
                resultado["preco_atual"] = p; resultado["rota_css"] = 10

        # Rota meta tags
        if not resultado["preco_atual"]:
            p = extrair_via_meta(page)
            if p and 0.5 < p < 10000:
                resultado["preco_atual"] = p; resultado["rota_css"] = 11

        # Rota varredura JS
        if not resultado["preco_atual"]:
            p = extrair_via_js(page)
            if p and 0.5 < p < 10000:
                resultado["preco_atual"] = p; resultado["rota_css"] = 12

        # Preço original (riscado)
        for sel in [
            "span[class*='listPrice']","span[class*='ListPrice']",
            ".price__list","s span","del span",
            "span[class*='oldPrice']","[class*='originalPrice']",
            "[class*='priceFrom']","[class*='price-from']",
        ]:
            try:
                el = page.query_selector(sel)
                if el:
                    p = extrair_preco(el.inner_text())
                    if p and p > (resultado.get("preco_atual") or 0):
                        resultado["preco_original"] = p; break
            except Exception: continue

        if resultado["preco_atual"]:
            resultado["disponivel"] = True
            if resultado["preco_original"] and resultado["preco_original"] > resultado["preco_atual"]:
                resultado["em_promocao"] = True
        else:
            resultado["erro"] = "preco_nao_encontrado_todas_rotas"

    except PWTimeout:
        resultado["erro"] = "timeout"
    except Exception as e:
        resultado["erro"] = str(e)[:120]
    return resultado

def coletar_com_retry(page, url, supermercado, nome_produto, embalagem, con, max_tentativas=3):
    """Tenta coletar até max_tentativas vezes com backoff exponencial."""
    for tentativa in range(1, max_tentativas + 1):
        dados = coletar_pagina(page, url, supermercado, nome_produto, embalagem, con, tentativa)
        if dados["preco_atual"]:
            return dados
        if tentativa < max_tentativas:
            # Backoff: 3s, 8s, 20s — com jitter
            espera = (3 ** tentativa) + random.uniform(0, 2)
            print(f"  [retry {tentativa}/{max_tentativas}] {nome_produto} — aguardando {espera:.0f}s")
            time.sleep(espera)
    return dados  # retorna o último (com erro)

def buscar_mateus(page, termo, max_tentativas=2):
    url_busca = f"https://mateusmais.com.br/busca?q={termo.replace(' ','+')}"
    resultado = {
        "url": url_busca, "disponivel": False, "preco_atual": None,
        "preco_original": None, "em_promocao": False,
        "rota_css": None, "url_recuperada": None, "tentativas": 1, "erro": None,
    }
    for tentativa in range(1, max_tentativas + 1):
        try:
            page.goto(url_busca, wait_until="domcontentloaded", timeout=28000)
            page.wait_for_timeout(3000)
            # Encontra primeiro produto válido
            for link in page.query_selector_all('a[href*="/produto/"], a[href*="/p/"]'):
                href = link.get_attribute("href") or ""
                if not href: continue
                if not href.startswith("http"):
                    href = "https://mateusmais.com.br" + href
                resultado["url"] = href
                page.goto(href, wait_until="domcontentloaded", timeout=25000)
                page.wait_for_timeout(3000)
                break
            for rota, fn in [(10,extrair_via_json_ld),(11,extrair_via_meta),(12,extrair_via_js)]:
                p = fn(page)
                if p and 0.5 < p < 10000:
                    resultado.update(preco_atual=p, rota_css=rota, disponivel=True, tentativas=tentativa)
                    return resultado
            if tentativa < max_tentativas:
                time.sleep(5)
        except PWTimeout:
            resultado["erro"] = "timeout"
        except Exception as e:
            resultado["erro"] = str(e)[:120]
    if not resultado["preco_atual"]:
        resultado["erro"] = resultado.get("erro") or "preco_nao_encontrado_mateus"
    return resultado

# ─── Loop principal ───────────────────────────────────────────────────────────
def main():
    log, total_ok, total_erro = [], 0, 0
    con = init_db()
    hoje = date.today().isoformat()

    # User-agents variados para rotacionar
    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
    ]

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-extensions",
                "--disable-infobars",
            ]
        )

        for sm_nome, cats in LINKS.items():
            print(f"\n{'='*60}\n{sm_nome}\n{'='*60}")
            headers_sm = HEADERS_SM.get(sm_nome, {})

            for cat_nome, links in cats.items():
                print(f"\n  [{cat_nome}]")
                for cidade_info in CIDADES:
                    # Novo contexto por cidade + supermercado (isolamento total)
                    ua = random.choice(USER_AGENTS)
                    ctx = browser.new_context(
                        user_agent=ua,
                        viewport={"width": random.choice([1280,1366,1440,1920]),
                                  "height": random.choice([768,800,900,1080])},
                        locale="pt-BR",
                        timezone_id="America/Sao_Paulo",
                        extra_http_headers={
                            "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
                            **headers_sm,
                        },
                        color_scheme="light",
                    )
                    page = ctx.new_page()
                    page.add_init_script(STEALTH_JS)
                    injetar_cep(page, sm_nome, cidade_info["cep"])

                    for produto in PRODUTOS[cat_nome]:
                        chave = f"{produto['nome']}_{produto['embalagem']}"
                        url = links.get(chave)
                        if not url: continue

                        horario = datetime.now().strftime("%H:%M:%S")
                        dados = coletar_com_retry(
                            page, url, sm_nome,
                            produto["nome"], produto["embalagem"], con
                        )
                        reg = {
                            "data_coleta": hoje, "horario_coleta": horario,
                            "supermercado": sm_nome, "categoria": cat_nome,
                            "marca": produto["marca"], "nome_produto": produto["nome"],
                            "embalagem": produto["embalagem"],
                            "cidade": cidade_info["cidade"], "uf": cidade_info["uf"],
                            "regiao": cidade_info["regiao"], **dados,
                        }
                        inserir(con, reg)
                        con.commit()  # commit incremental — não perde dados se cair

                        rec = " [URL recuperada]" if dados.get("url_recuperada") else ""
                        ret = f" [tentativa {dados['tentativas']}]" if dados.get("tentativas",1) > 1 else ""
                        status = f"OK(rota{dados['rota_css']}){rec}{ret}" if dados["preco_atual"] else f"ERRO:{dados['erro']}"
                        preco_s = f"R${dados['preco_atual']:.2f}" if dados["preco_atual"] else "—"
                        msg = f"{hoje}|{sm_nome}|{cat_nome}|{produto['nome']} {produto['embalagem']}|{cidade_info['cidade']}|{preco_s}|{status}"
                        log.append(msg)
                        print(f"    {msg}")
                        total_ok   += bool(dados["preco_atual"])
                        total_erro += not bool(dados["preco_atual"])

                        # Delay humanizado — mais longo nos sites com maior bloqueio
                        base_delay = 2.5 if sm_nome in ["Carrefour Mercado","Pão de Açúcar","Extra"] else 1.5
                        time.sleep(random.uniform(base_delay, base_delay + 2.5))

                    ctx.close()

        # ── Mateus ────────────────────────────────────────────────────────────
        print(f"\n{'='*60}\nMateus (busca dinâmica)\n{'='*60}")
        for cat_nome, termos in BUSCA_MATEUS.items():
            print(f"\n  [{cat_nome}]")
            for cidade_info in CIDADES:
                ua = random.choice(USER_AGENTS)
                ctx = browser.new_context(
                    user_agent=ua,
                    viewport={"width":1280,"height":800},
                    locale="pt-BR",
                    timezone_id="America/Sao_Paulo",
                )
                page = ctx.new_page()
                page.add_init_script(STEALTH_JS)

                for chave, termo in termos.items():
                    nome, emb = chave.rsplit("_", 1)
                    prod = next(
                        (p for p in PRODUTOS[cat_nome] if p["nome"]==nome and p["embalagem"]==emb),
                        {"marca": nome.split()[0], "nome": nome, "embalagem": emb}
                    )
                    horario = datetime.now().strftime("%H:%M:%S")
                    dados = buscar_mateus(page, termo)
                    reg = {
                        "data_coleta": hoje, "horario_coleta": horario,
                        "supermercado": "Mateus", "categoria": cat_nome,
                        "marca": prod["marca"], "nome_produto": prod["nome"],
                        "embalagem": prod["embalagem"],
                        "cidade": cidade_info["cidade"], "uf": cidade_info["uf"],
                        "regiao": cidade_info["regiao"], **dados,
                    }
                    inserir(con, reg)
                    con.commit()

                    status = f"OK(rota{dados['rota_css']})" if dados["preco_atual"] else f"ERRO:{dados['erro']}"
                    preco_s = f"R${dados['preco_atual']:.2f}" if dados["preco_atual"] else "—"
                    msg = f"{hoje}|Mateus|{cat_nome}|{nome} {emb}|{cidade_info['cidade']}|{preco_s}|{status}"
                    log.append(msg)
                    print(f"    {msg}")
                    total_ok   += bool(dados["preco_atual"])
                    total_erro += not bool(dados["preco_atual"])
                    time.sleep(random.uniform(2.5, 5.0))

                ctx.close()

        browser.close()

    con.close()

    # CSV diário
    csv_path = Path(f"data/coleta_{hoje}.csv")
    con2 = sqlite3.connect(DB_PATH); con2.row_factory = sqlite3.Row
    rows = con2.execute("SELECT * FROM precos WHERE data_coleta=?", (hoje,)).fetchall()
    if rows:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=rows[0].keys())
            w.writeheader(); w.writerows([dict(r) for r in rows])
    con2.close()

    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(f"\n=== {hoje} | OK:{total_ok} ERRO:{total_erro} ===\n")
        f.write("\n".join(log[-200:]))

    print(f"\n{'='*60}")
    print(f"Finalizado: {total_ok} OK, {total_erro} erros ({total_erro/(total_ok+total_erro)*100:.1f}%)")

if __name__ == "__main__":
    main()
