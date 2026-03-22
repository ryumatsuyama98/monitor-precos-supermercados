"""
Gera o dashboard HTML estático a partir do banco SQLite.
Executado após cada coleta pelo GitHub Actions.
"""

import sqlite3
import json
from pathlib import Path
from datetime import date, datetime, timedelta
from collections import defaultdict

DB_PATH  = Path("data/precos.db")
OUT_PATH = Path("docs/index.html")

SENHA_DASHBOARD = "cervejas2025"  # ← Altere para uma senha de sua escolha

# ─── Leitura do banco ─────────────────────────────────────────────────────────

def carregar_dados():
    if not DB_PATH.exists():
        return [], [], None, []

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row

    ultima_data = con.execute("SELECT MAX(data_coleta) FROM precos").fetchone()[0]
    if not ultima_data:
        con.close()
        return [], [], None, []

    coleta = [dict(r) for r in con.execute(
        "SELECT * FROM precos WHERE data_coleta = ? AND disponivel = 1", (ultima_data,)
    ).fetchall()]

    historico = [dict(r) for r in con.execute("""
        SELECT data_coleta, supermercado, marca, nome_produto, embalagem,
               AVG(preco_atual) as preco_medio,
               MIN(preco_atual) as preco_min,
               MAX(preco_atual) as preco_max,
               COUNT(*) as num_cidades
        FROM precos
        WHERE disponivel = 1 AND preco_atual IS NOT NULL
        GROUP BY data_coleta, supermercado, marca, nome_produto, embalagem
        ORDER BY data_coleta DESC
        LIMIT 2000
    """).fetchall()]

    alertas = calcular_alertas(con, ultima_data)
    con.close()
    return coleta, historico, ultima_data, alertas


# ─── Detecção de problemas ────────────────────────────────────────────────────

def calcular_alertas(con, ultima_data):
    alertas = []
    hoje = date.today()

    # 1. Dias desde a última coleta
    if ultima_data:
        ultima = date.fromisoformat(ultima_data)
        dias_parado = (hoje - ultima).days
        if dias_parado >= 2:
            alertas.append({
                "nivel": "critico",
                "titulo": f"Coleta parada há {dias_parado} dias",
                "detalhe": f"A última coleta bem-sucedida foi em {ultima.strftime('%d/%m/%Y')}. O scraper parou de rodar.",
                "acao": "Acesse GitHub → Actions → Coleta Diária → Run workflow para rodar manualmente.",
            })
        elif dias_parado == 1:
            alertas.append({
                "nivel": "aviso",
                "titulo": "Coleta de ontem não encontrada",
                "detalhe": "Pode ser atraso do GitHub Actions ou falha pontual. Se persistir amanhã, investigue.",
                "acao": "Acesse GitHub → Actions e veja se a última execução teve erro.",
            })

    # 2. Supermercados com alta taxa de erro nos últimos 7 dias
    rows = con.execute("""
        SELECT supermercado,
               COUNT(*) as total,
               SUM(CASE WHEN erro IS NOT NULL THEN 1 ELSE 0 END) as erros
        FROM precos
        WHERE data_coleta >= date('now', '-7 days')
        GROUP BY supermercado
    """).fetchall()

    for r in rows:
        if r["total"] == 0:
            continue
        taxa = r["erros"] / r["total"]
        if taxa >= 0.8:
            alertas.append({
                "nivel": "critico",
                "titulo": f"{r['supermercado']} — falha em {int(taxa*100)}% das coletas",
                "detalhe": "Provável mudança de layout, seletor CSS quebrado ou URL desatualizada nos últimos 7 dias.",
                "acao": f"Abra o site do {r['supermercado']} manualmente e compare com as URLs no arquivo scraper.py.",
            })
        elif taxa >= 0.4:
            alertas.append({
                "nivel": "aviso",
                "titulo": f"{r['supermercado']} — coleta instável ({int(taxa*100)}% de erros)",
                "detalhe": "Pode ser bloqueio temporário, lentidão do site ou mudança parcial de layout.",
                "acao": "Monitore por 2–3 dias. Se persistir, revise os seletores CSS no scraper.py.",
            })

    # 3. Produtos individuais sem preço há 3+ dias consecutivos
    rows2 = con.execute("""
        SELECT supermercado, nome_produto, embalagem,
               COUNT(DISTINCT data_coleta) as dias_erro
        FROM precos
        WHERE data_coleta >= date('now', '-14 days')
          AND preco_atual IS NULL
        GROUP BY supermercado, nome_produto, embalagem
        HAVING dias_erro >= 3
          AND NOT EXISTS (
              SELECT 1 FROM precos p2
              WHERE p2.supermercado = precos.supermercado
                AND p2.nome_produto = precos.nome_produto
                AND p2.embalagem    = precos.embalagem
                AND p2.data_coleta >= date('now', '-14 days')
                AND p2.preco_atual IS NOT NULL
          )
        ORDER BY dias_erro DESC
        LIMIT 15
    """).fetchall()

    for r in rows2:
        alertas.append({
            "nivel": "aviso",
            "titulo": f"{r['nome_produto']} {r['embalagem']} sem preço no {r['supermercado']}",
            "detalhe": f"{r['dias_erro']} dias consecutivos sem coletar preço. Seletor CSS ou URL provavelmente mudou.",
            "acao": f"Abra a URL do produto no {r['supermercado']} e verifique se o produto ainda existe na página.",
        })

    # 4. URLs que nunca retornaram preço (desde o início)
    rows3 = con.execute("""
        SELECT supermercado, nome_produto, embalagem, url,
               COUNT(*) as tentativas
        FROM precos
        WHERE preco_atual IS NULL
        GROUP BY supermercado, nome_produto, embalagem, url
        HAVING tentativas >= 5
           AND NOT EXISTS (
               SELECT 1 FROM precos p2
               WHERE p2.supermercado = precos.supermercado
                 AND p2.nome_produto = precos.nome_produto
                 AND p2.embalagem    = precos.embalagem
                 AND p2.preco_atual IS NOT NULL
           )
        ORDER BY tentativas DESC
        LIMIT 10
    """).fetchall()

    for r in rows3:
        alertas.append({
            "nivel": "info",
            "titulo": f"URL nunca retornou preço — {r['nome_produto']} {r['embalagem']} ({r['supermercado']})",
            "detalhe": f"{r['tentativas']} tentativas sem sucesso desde o início. A URL pode estar errada.",
            "acao": f"Verifique manualmente: {r['url']}",
        })

    return alertas


# ─── Resumo por produto ────────────────────────────────────────────────────────

def resumo_por_produto(coleta):
    agrupado = defaultdict(list)
    for r in coleta:
        chave = (r["marca"], r["nome_produto"], r["embalagem"])
        agrupado[chave].append(r)

    resumo = []
    for (marca, nome, emb), items in sorted(agrupado.items()):
        precos = [i["preco_atual"] for i in items if i["preco_atual"]]
        if not precos:
            continue
        min_item = min(items, key=lambda x: x["preco_atual"] or 9999)
        max_item = max(items, key=lambda x: x["preco_atual"] or 0)
        resumo.append({
            "marca": marca, "nome": nome, "embalagem": emb,
            "preco_min": min(precos), "preco_max": max(precos),
            "preco_medio": sum(precos) / len(precos),
            "sm_min": min_item["supermercado"],
            "sm_max": max_item["supermercado"],
            "variacao": max(precos) - min(precos),
            "num_ofertas": len(precos),
        })
    return resumo


# ─── Geração do HTML ──────────────────────────────────────────────────────────

def gerar_html(coleta, historico, ultima_data, alertas=[]):
    resumo = resumo_por_produto(coleta)
    total_coletas   = len(coleta)
    total_promocoes = sum(1 for r in coleta if r.get("em_promocao"))
    total_produtos  = len(set((r["marca"], r["nome_produto"], r["embalagem"]) for r in coleta))
    total_sm        = len(set(r["supermercado"] for r in coleta))

    n_criticos = sum(1 for a in alertas if a["nivel"] == "critico")
    n_avisos   = sum(1 for a in alertas if a["nivel"] == "aviso")
    n_info     = sum(1 for a in alertas if a["nivel"] == "info")
    badge_alertas = n_criticos + n_avisos + n_info

    coleta_json    = json.dumps(coleta,    ensure_ascii=False)
    resumo_json    = json.dumps(resumo,    ensure_ascii=False)
    historico_json = json.dumps(historico, ensure_ascii=False)
    alertas_json   = json.dumps(alertas,   ensure_ascii=False)

    html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Monitor de Preços — Cervejas</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<style>
  :root {{
    --bg:#f8f9fa;--card:#fff;--border:#e9ecef;--text:#212529;--muted:#6c757d;
    --accent:#1F4E79;--green:#198754;--red:#dc3545;--yellow:#ffc107;--blue:#0d6efd;
    --radius:10px;
  }}
  *{{box-sizing:border-box;margin:0;padding:0;}}
  body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:var(--bg);color:var(--text);font-size:14px;}}

  /* LOGIN */
  #login-screen{{display:flex;align-items:center;justify-content:center;min-height:100vh;}}
  .login-card{{background:var(--card);border:1px solid var(--border);border-radius:var(--radius);padding:2rem;width:320px;text-align:center;}}
  .login-card h2{{margin-bottom:1.5rem;color:var(--accent);}}
  .login-card input{{width:100%;padding:10px 14px;border:1px solid var(--border);border-radius:6px;font-size:14px;margin-bottom:12px;}}
  .login-card button{{width:100%;padding:10px;background:var(--accent);color:#fff;border:none;border-radius:6px;font-size:14px;cursor:pointer;}}
  .login-card .erro{{color:var(--red);font-size:12px;margin-top:8px;display:none;}}
  #app{{display:none;}}

  /* LAYOUT */
  header{{background:var(--accent);color:#fff;padding:1rem 2rem;display:flex;justify-content:space-between;align-items:center;}}
  header h1{{font-size:18px;font-weight:600;}}
  header span{{font-size:12px;opacity:.8;}}
  nav{{background:var(--card);border-bottom:1px solid var(--border);padding:0 2rem;display:flex;gap:0;align-items:center;}}
  nav button{{padding:12px 20px;background:none;border:none;border-bottom:2px solid transparent;font-size:13px;cursor:pointer;color:var(--muted);transition:.15s;position:relative;}}
  nav button.active{{color:var(--accent);border-bottom-color:var(--accent);font-weight:500;}}
  .nav-badge{{position:absolute;top:8px;right:6px;background:var(--red);color:#fff;font-size:10px;font-weight:700;border-radius:10px;padding:1px 5px;min-width:16px;text-align:center;}}
  .main{{padding:1.5rem 2rem;max-width:1400px;margin:0 auto;}}
  .page{{display:none;}}.page.active{{display:block;}}

  /* ALERTAS */
  .alertas-wrap{{display:flex;flex-direction:column;gap:10px;margin-bottom:1.5rem;}}
  .alerta{{border-radius:var(--radius);padding:14px 16px;display:flex;gap:14px;align-items:flex-start;border:1px solid transparent;}}
  .alerta-critico{{background:#fff5f5;border-color:#f5c2c7;}}
  .alerta-aviso{{background:#fffbf0;border-color:#ffe69c;}}
  .alerta-info{{background:#f0f7ff;border-color:#b6d4fe;}}
  .alerta-icon{{font-size:20px;flex-shrink:0;margin-top:1px;}}
  .alerta-body{{flex:1;}}
  .alerta-titulo{{font-weight:600;font-size:13px;margin-bottom:3px;}}
  .alerta-critico .alerta-titulo{{color:#842029;}}
  .alerta-aviso   .alerta-titulo{{color:#664d03;}}
  .alerta-info    .alerta-titulo{{color:#084298;}}
  .alerta-detalhe{{font-size:12px;color:var(--muted);line-height:1.5;margin-bottom:4px;}}
  .alerta-acao{{font-size:12px;font-style:italic;}}
  .alerta-critico .alerta-acao{{color:#842029;}}
  .alerta-aviso   .alerta-acao{{color:#664d03;}}
  .alerta-info    .alerta-acao{{color:#084298;}}
  .alertas-ok{{background:#f0fdf4;border:1px solid #bbf7d0;border-radius:var(--radius);padding:12px 16px;display:flex;gap:10px;align-items:center;font-size:13px;color:#166534;margin-bottom:1.5rem;}}

  /* CARDS */
  .cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:1.5rem;}}
  .card{{background:var(--card);border:1px solid var(--border);border-radius:var(--radius);padding:1rem 1.2rem;}}
  .card-label{{font-size:11px;color:var(--muted);margin-bottom:4px;}}
  .card-val{{font-size:24px;font-weight:600;color:var(--accent);}}
  .card-sub{{font-size:11px;color:var(--muted);margin-top:2px;}}
  .card-alerta .card-val{{color:var(--red);}}

  /* SEÇÕES / TABELA */
  .section{{background:var(--card);border:1px solid var(--border);border-radius:var(--radius);padding:1.2rem;margin-bottom:1.2rem;}}
  .section h3{{font-size:14px;font-weight:600;margin-bottom:1rem;color:var(--accent);}}
  .filters{{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:1rem;align-items:center;}}
  .filters label{{font-size:12px;color:var(--muted);}}
  .filters select{{font-size:12px;padding:5px 9px;border:1px solid var(--border);border-radius:6px;background:var(--card);color:var(--text);}}
  table{{width:100%;border-collapse:collapse;font-size:12px;}}
  th{{text-align:left;padding:8px 10px;background:#f1f3f5;font-weight:500;font-size:11px;color:var(--muted);border-bottom:1px solid var(--border);}}
  td{{padding:8px 10px;border-bottom:1px solid var(--border);}}
  tr:hover td{{background:#f8f9fa;}}
  .badge{{font-size:10px;padding:2px 7px;border-radius:10px;display:inline-block;font-weight:500;}}
  .badge-promo{{background:#fff3cd;color:#856404;}}
  .badge-disp{{background:#d1e7dd;color:#0f5132;}}
  .badge-sm-cf{{background:#e8f4fd;color:#0d6efd;}}
  .badge-sm-pa{{background:#e8f5e9;color:#198754;}}
  .badge-sm-ex{{background:#fff3e0;color:#e65100;}}
  .badge-sm-at{{background:#fff8e1;color:#f57f17;}}
  .chart-wrap{{position:relative;height:320px;}}
  .top-promo{{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:10px;}}
  .promo-card{{border:1px solid var(--border);border-radius:8px;padding:12px;}}
  .promo-card .promo-nome{{font-weight:500;font-size:13px;margin-bottom:4px;}}
  .promo-card .promo-sm{{font-size:11px;color:var(--muted);margin-bottom:6px;}}
  .promo-card .promo-preco{{font-size:20px;font-weight:700;color:var(--green);}}
  .promo-card .promo-de{{font-size:11px;color:var(--muted);text-decoration:line-through;}}
  .promo-card .promo-desc{{font-size:12px;color:var(--red);font-weight:500;}}
  @media(max-width:600px){{.main{{padding:1rem;}}header{{padding:1rem;}}nav{{overflow-x:auto;}}}}
</style>
</head>
<body>

<div id="login-screen">
  <div class="login-card">
    <h2>Monitor de Preços</h2>
    <p style="font-size:12px;color:var(--muted);margin-bottom:1.5rem">Cervejas — Brasil</p>
    <input type="password" id="senha-input" placeholder="Senha de acesso" onkeydown="if(event.key==='Enter')login()">
    <button onclick="login()">Entrar</button>
    <div class="erro" id="erro-login">Senha incorreta</div>
  </div>
</div>

<div id="app">
  <header>
    <h1>Monitor de Preços — Cervejas</h1>
    <span>Última coleta: {ultima_data} &nbsp;|&nbsp; {total_sm} supermercados &nbsp;|&nbsp; {total_produtos} produtos</span>
  </header>
  <nav>
    <button class="active" onclick="showPage('resumo',this)">Resumo</button>
    <button onclick="showPage('tabela',this)">Tabela completa</button>
    <button onclick="showPage('promocoes',this)">Promoções</button>
    <button onclick="showPage('historico',this)">Histórico</button>
    <button onclick="showPage('alertas',this)" id="btn-alertas">
      Alertas
      {'<span class="nav-badge" id="nav-badge">' + str(badge_alertas) + '</span>' if badge_alertas > 0 else ''}
    </button>
  </nav>

  <div class="main">

    <!-- RESUMO -->
    <div class="page active" id="page-resumo">
      <div id="alertas-banner"></div>
      <div class="cards">
        <div class="card"><div class="card-label">Coletas hoje</div><div class="card-val">{total_coletas}</div><div class="card-sub">{ultima_data}</div></div>
        <div class="card"><div class="card-label">Em promoção</div><div class="card-val" style="color:var(--green)">{total_promocoes}</div><div class="card-sub">produtos com desconto</div></div>
        <div class="card"><div class="card-label">Produtos monitorados</div><div class="card-val">{total_produtos}</div><div class="card-sub">marcas e embalagens</div></div>
        <div class="card {'card-alerta' if n_criticos > 0 else ''}"><div class="card-label">Alertas ativos</div><div class="card-val">{badge_alertas}</div><div class="card-sub">{n_criticos} críticos, {n_avisos} avisos</div></div>
      </div>
      <div class="section">
        <h3>Preço médio por supermercado — hoje</h3>
        <div class="chart-wrap"><canvas id="chart-sm"></canvas></div>
      </div>
      <div class="section">
        <h3>Preço mínimo vs máximo por marca — hoje</h3>
        <div class="chart-wrap"><canvas id="chart-marca"></canvas></div>
      </div>
    </div>

    <!-- TABELA -->
    <div class="page" id="page-tabela">
      <div class="section">
        <h3>Todos os preços coletados hoje</h3>
        <div class="filters">
          <label>Supermercado:</label>
          <select id="f-sm" onchange="filtrarTabela()">
            <option value="">Todos</option>
            <option>Carrefour Mercado</option><option>Pão de Açúcar</option>
            <option>Extra</option><option>Atacadão</option>
          </select>
          <label>Marca:</label><select id="f-marca" onchange="filtrarTabela()"><option value="">Todas</option></select>
          <label>Cidade:</label><select id="f-cidade" onchange="filtrarTabela()"><option value="">Todas</option></select>
          <label>Região:</label>
          <select id="f-regiao" onchange="filtrarTabela()">
            <option value="">Todas</option><option>Sul</option><option>Sudeste</option><option>Nordeste</option>
          </select>
        </div>
        <div style="overflow-x:auto"><table>
          <thead><tr>
            <th>Supermercado</th><th>Marca</th><th>Produto</th><th>Emb.</th>
            <th>Cidade</th><th>UF</th><th>Preço atual</th><th>Preço original</th>
            <th>Desconto</th><th>Status</th>
          </tr></thead>
          <tbody id="tabela-body"></tbody>
        </table></div>
      </div>
    </div>

    <!-- PROMOÇÕES -->
    <div class="page" id="page-promocoes">
      <div class="section">
        <h3>Promoções detectadas hoje</h3>
        <div class="top-promo" id="promo-grid"></div>
      </div>
    </div>

    <!-- HISTÓRICO -->
    <div class="page" id="page-historico">
      <div class="section">
        <h3>Evolução do preço médio</h3>
        <div class="filters">
          <label>Produto:</label>
          <select id="h-produto" onchange="renderHistorico()"><option value="">Selecione um produto...</option></select>
        </div>
        <div class="chart-wrap"><canvas id="chart-hist"></canvas></div>
      </div>
    </div>

    <!-- ALERTAS -->
    <div class="page" id="page-alertas">
      <div id="alertas-lista"></div>
    </div>

  </div>
</div>

<script>
const SENHA     = "{SENHA_DASHBOARD}";
const COLETA    = {coleta_json};
const RESUMO    = {resumo_json};
const HISTORICO = {historico_json};
const ALERTAS   = {alertas_json};

function login() {{
  if (document.getElementById('senha-input').value === SENHA) {{
    sessionStorage.setItem('auth','1');
    document.getElementById('login-screen').style.display = 'none';
    document.getElementById('app').style.display = 'block';
    init();
  }} else {{
    document.getElementById('erro-login').style.display = 'block';
  }}
}}
window.onload = () => {{
  if (sessionStorage.getItem('auth')==='1') {{
    document.getElementById('login-screen').style.display='none';
    document.getElementById('app').style.display='block';
    init();
  }}
}};

function showPage(id, btn) {{
  document.querySelectorAll('.page').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('nav button').forEach(b=>b.classList.remove('active'));
  document.getElementById('page-'+id).classList.add('active');
  btn.classList.add('active');
}}

const smBadge = {{'Carrefour Mercado':'badge-sm-cf','Pão de Açúcar':'badge-sm-pa','Extra':'badge-sm-ex','Atacadão':'badge-sm-at'}};
const nivelIcon = {{critico:'🔴', aviso:'🟡', info:'🔵'}};
const nivelLabel = {{critico:'Crítico', aviso:'Aviso', info:'Informação'}};

function fmt(v) {{ return v!=null ? 'R$ '+v.toFixed(2).replace('.',',') : '—'; }}

function init() {{
  renderAlertasBanner();
  renderAlertasLista();
  renderChartSM();
  renderChartMarca();
  renderTabela();
  renderPromos();
  populaFiltros();
  populaHistoricoSelect();
}}

function renderAlertasBanner() {{
  const criticos = ALERTAS.filter(a=>a.nivel==='critico');
  const banner = document.getElementById('alertas-banner');
  if (criticos.length === 0) {{
    banner.innerHTML = `<div class="alertas-ok">✅ <span>Tudo funcionando — nenhum problema crítico detectado na coleta de hoje.</span></div>`;
  }} else {{
    banner.innerHTML = criticos.map(a => `
      <div class="alerta alerta-critico" style="margin-bottom:10px">
        <div class="alerta-icon">🔴</div>
        <div class="alerta-body">
          <div class="alerta-titulo">${{a.titulo}}</div>
          <div class="alerta-detalhe">${{a.detalhe}}</div>
          <div class="alerta-acao">O que fazer: ${{a.acao}}</div>
        </div>
      </div>`).join('');
  }}
}}

function renderAlertasLista() {{
  const el = document.getElementById('alertas-lista');
  if (ALERTAS.length === 0) {{
    el.innerHTML = `<div class="alertas-ok">✅ <span>Nenhum alerta ativo. Todas as coletas estão funcionando corretamente.</span></div>`;
    return;
  }}
  const ordem = ['critico','aviso','info'];
  const sorted = [...ALERTAS].sort((a,b)=>ordem.indexOf(a.nivel)-ordem.indexOf(b.nivel));
  el.innerHTML = `<div class="alertas-wrap">`+sorted.map(a=>`
    <div class="alerta alerta-${{a.nivel}}">
      <div class="alerta-icon">${{nivelIcon[a.nivel]}}</div>
      <div class="alerta-body">
        <div class="alerta-titulo">[${{nivelLabel[a.nivel]}}] ${{a.titulo}}</div>
        <div class="alerta-detalhe">${{a.detalhe}}</div>
        <div class="alerta-acao">O que fazer: ${{a.acao}}</div>
      </div>
    </div>`).join('')+`</div>`;
}}

function renderChartSM() {{
  const grupos={{}};
  COLETA.forEach(r=>{{ if(!grupos[r.supermercado]) grupos[r.supermercado]=[]; grupos[r.supermercado].push(r.preco_atual); }});
  const labels=Object.keys(grupos);
  const medias=labels.map(sm=>{{ const a=grupos[sm].filter(Boolean); return a.length?(a.reduce((x,y)=>x+y,0)/a.length).toFixed(2):0; }});
  new Chart(document.getElementById('chart-sm'),{{type:'bar',data:{{labels,datasets:[{{label:'Preço médio (R$)',data:medias,backgroundColor:['#0d6efd','#198754','#e65100','#f57f17'],borderRadius:6}}]}},options:{{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{display:false}}}},scales:{{y:{{beginAtZero:false}}}}}}}});
}}

function renderChartMarca() {{
  const marcas=[...new Set(RESUMO.map(r=>r.marca))];
  const mins=marcas.map(m=>{{const r=RESUMO.find(x=>x.marca===m);return r?+r.preco_min.toFixed(2):0;}});
  const maxs=marcas.map(m=>{{const r=RESUMO.find(x=>x.marca===m);return r?+r.preco_max.toFixed(2):0;}});
  new Chart(document.getElementById('chart-marca'),{{type:'bar',data:{{labels:marcas,datasets:[{{label:'Mínimo',data:mins,backgroundColor:'#198754',borderRadius:4}},{{label:'Máximo',data:maxs,backgroundColor:'#dc3545',borderRadius:4}}]}},options:{{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{position:'top'}}}},scales:{{y:{{beginAtZero:false}}}}}}}});
}}

let tabelaData=[...COLETA];
function filtrarTabela() {{
  const sm=document.getElementById('f-sm').value;
  const marca=document.getElementById('f-marca').value;
  const cidade=document.getElementById('f-cidade').value;
  const regiao=document.getElementById('f-regiao').value;
  tabelaData=COLETA.filter(r=>(!sm||r.supermercado===sm)&&(!marca||r.marca===marca)&&(!cidade||r.cidade===cidade)&&(!regiao||r.regiao===regiao));
  renderTabelaBody();
}}
function renderTabela(){{tabelaData=[...COLETA];renderTabelaBody();}}
function renderTabelaBody(){{
  document.getElementById('tabela-body').innerHTML=tabelaData.slice(0,500).map(r=>{{
    const desc=r.preco_original&&r.preco_atual<r.preco_original?Math.round((r.preco_original-r.preco_atual)/r.preco_original*100)+'%':'—';
    return `<tr><td><span class="badge ${{smBadge[r.supermercado]||''}}">${{r.supermercado}}</span></td><td style="font-weight:500">${{r.marca}}</td><td>${{r.nome_produto}}</td><td>${{r.embalagem}}</td><td>${{r.cidade}}</td><td>${{r.uf}}</td><td style="font-weight:600;color:${{r.em_promocao?'var(--green)':'inherit'}}">${{fmt(r.preco_atual)}}</td><td style="color:var(--muted)">${{fmt(r.preco_original)}}</td><td>${{desc!=='—'?`<span class="badge badge-promo">-${{desc}}</span>`:'—'}}</td><td><span class="badge badge-disp">Disponível</span></td></tr>`;
  }}).join('');
}}

function renderPromos(){{
  const promos=COLETA.filter(r=>r.em_promocao).sort((a,b)=>((b.preco_original-b.preco_atual)/b.preco_original)-((a.preco_original-a.preco_atual)/a.preco_original));
  document.getElementById('promo-grid').innerHTML=promos.slice(0,40).map(r=>{{
    const pct=Math.round((r.preco_original-r.preco_atual)/r.preco_original*100);
    return `<div class="promo-card"><div class="promo-nome">${{r.nome_produto}} ${{r.embalagem}}</div><div class="promo-sm">${{r.supermercado}} — ${{r.cidade}}</div><div class="promo-preco">${{fmt(r.preco_atual)}}</div><div class="promo-de">de ${{fmt(r.preco_original)}}</div><div class="promo-desc">-${{pct}}% de desconto</div></div>`;
  }}).join('');
}}

function populaFiltros(){{
  const marcas=[...new Set(COLETA.map(r=>r.marca))].sort();
  const cidades=[...new Set(COLETA.map(r=>r.cidade))].sort();
  const mSel=document.getElementById('f-marca');
  const cSel=document.getElementById('f-cidade');
  marcas.forEach(m=>mSel.innerHTML+=`<option>${{m}}</option>`);
  cidades.forEach(c=>cSel.innerHTML+=`<option>${{c}}</option>`);
}}

let chartHist=null;
function populaHistoricoSelect(){{
  const produtos=[...new Set(HISTORICO.map(r=>r.marca+' — '+r.nome_produto+' '+r.embalagem))].sort();
  const sel=document.getElementById('h-produto');
  produtos.forEach(p=>sel.innerHTML+=`<option>${{p}}</option>`);
}}
function renderHistorico(){{
  const val=document.getElementById('h-produto').value;
  if(!val)return;
  const [marca,resto]=val.split(' — ');
  const partes=resto.split(' ');
  const emb=partes[partes.length-1];
  const nome=partes.slice(0,-1).join(' ');
  const sms=['Carrefour Mercado','Pão de Açúcar','Extra','Atacadão'];
  const cores=['#0d6efd','#198754','#e65100','#f57f17'];
  const datas=[...new Set(HISTORICO.filter(r=>r.marca===marca&&r.nome_produto===nome&&r.embalagem===emb).map(r=>r.data_coleta))].sort();
  const datasets=sms.map((sm,i)=>{{
    const dados=datas.map(d=>{{const r=HISTORICO.find(x=>x.data_coleta===d&&x.supermercado===sm&&x.marca===marca&&x.nome_produto===nome&&x.embalagem===emb);return r?+r.preco_medio.toFixed(2):null;}});
    return {{label:sm,data:dados,borderColor:cores[i],backgroundColor:cores[i]+'22',tension:.3,spanGaps:true,pointRadius:3}};
  }});
  if(chartHist)chartHist.destroy();
  chartHist=new Chart(document.getElementById('chart-hist'),{{type:'line',data:{{labels:datas,datasets}},options:{{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{position:'top'}}}},scales:{{y:{{beginAtZero:false}}}}}}}});
}}
</script>
</body>
</html>"""
    return html


def main():
    OUT_PATH.parent.mkdir(exist_ok=True)
    coleta, historico, ultima_data, alertas = carregar_dados()
    if not ultima_data:
        ultima_data = str(date.today())
    html = gerar_html(coleta, historico, ultima_data, alertas)
    OUT_PATH.write_text(html, encoding="utf-8")
    n_criticos = sum(1 for a in alertas if a["nivel"] == "critico")
    n_avisos   = sum(1 for a in alertas if a["nivel"] == "aviso")
    print(f"Dashboard gerado: {OUT_PATH} ({len(coleta)} registros, {n_criticos} críticos, {n_avisos} avisos)")

if __name__ == "__main__":
    main()
