"""
Monitor de Preços — Dashboard HTML completo
Abas: Início | Erros | Cervejas | Embutidos | Biscoitos | Massas | Mercearia
"""
import sqlite3, json, csv, io
from pathlib import Path
from datetime import date
from collections import defaultdict

DB_PATH  = Path("data/precos.db")
OUT_PATH = Path("docs/index.html")
SENHA    = "cervejas2025"
CATS     = ["Cervejas","Embutidos","Biscoitos","Massas","Mercearia"]

# ─── Carga de dados ────────────────────────────────────────────────────────────
def carregar_dados():
    if not DB_PATH.exists():
        return {}, {}, {}, None, []
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row

    ultima = con.execute("SELECT MAX(data_coleta) FROM precos").fetchone()[0]
    if not ultima:
        con.close(); return {}, {}, {}, None, []

    # Todos os registros do último dia (OK + erro) para a tabela principal
    todos = [dict(r) for r in con.execute(
        "SELECT * FROM precos WHERE data_coleta=? ORDER BY categoria,supermercado,nome_produto", (ultima,)
    ).fetchall()]

    # Erros por dia (todos os dias)
    erros = [dict(r) for r in con.execute("""
        SELECT data_coleta, supermercado, categoria, marca, nome_produto, embalagem,
               cidade, uf, url, url_recuperada, erro, rota_css
        FROM precos
        WHERE erro IS NOT NULL
        ORDER BY data_coleta DESC, supermercado, categoria, nome_produto
    """).fetchall()]

    # Histórico por cidade (preço real coletado, sem agregação)
    historico = [dict(r) for r in con.execute("""
        SELECT data_coleta, supermercado, categoria, marca, nome_produto, embalagem,
               cidade, uf, regiao, preco_atual
        FROM precos
        WHERE preco_atual IS NOT NULL AND disponivel=1
        ORDER BY data_coleta, supermercado, categoria, nome_produto, embalagem, cidade
    """).fetchall()]

    alertas = calcular_alertas(con, ultima)
    con.close()
    return todos, erros, historico, ultima, alertas

def calcular_alertas(con, ultima_data):
    alertas = []
    hoje = date.today()
    if ultima_data:
        dias = (hoje - date.fromisoformat(ultima_data)).days
        if dias >= 2:
            alertas.append({"nivel":"critico","titulo":f"Coleta parada há {dias} dias",
                "detalhe":f"Última coleta: {ultima_data}. O scraper parou de rodar.",
                "acao":"Acesse GitHub → Actions → Coleta Diária → Run workflow."})
        elif dias == 1:
            alertas.append({"nivel":"aviso","titulo":"Coleta de ontem não encontrada",
                "detalhe":"Pode ser atraso do GitHub Actions ou falha pontual.",
                "acao":"Verifique em Actions se a última execução teve erro."})

    rows = con.execute("""
        SELECT supermercado, categoria,
               COUNT(*) total,
               SUM(CASE WHEN erro IS NOT NULL THEN 1 ELSE 0 END) erros
        FROM precos WHERE data_coleta >= date('now','-7 days')
        GROUP BY supermercado, categoria
    """).fetchall()
    for r in rows:
        if r["total"] == 0: continue
        taxa = r["erros"] / r["total"]
        if taxa >= 0.8:
            alertas.append({"nivel":"critico",
                "titulo":f"{r['supermercado']} / {r['categoria']} — falha em {int(taxa*100)}% das coletas",
                "detalhe":"Todas as 6 rotas de fallback falharam. Provável mudança de layout ou URL obsoleta.",
                "acao":f"Abra o site do {r['supermercado']} e verifique as URLs no scraper.py."})
        elif taxa >= 0.4:
            alertas.append({"nivel":"aviso",
                "titulo":f"{r['supermercado']} / {r['categoria']} — coleta instável ({int(taxa*100)}% erros)",
                "detalhe":"Possível bloqueio temporário ou mudança parcial de layout.",
                "acao":"Monitore por 2–3 dias. Se persistir, revise os seletores."})

    rows2 = con.execute("""
        SELECT supermercado, categoria, nome_produto, embalagem,
               COUNT(DISTINCT data_coleta) dias_erro
        FROM precos
        WHERE data_coleta >= date('now','-14 days') AND preco_atual IS NULL
        GROUP BY supermercado, categoria, nome_produto, embalagem
        HAVING dias_erro >= 3
          AND NOT EXISTS (
              SELECT 1 FROM precos p2
              WHERE p2.supermercado=precos.supermercado
                AND p2.nome_produto=precos.nome_produto
                AND p2.data_coleta >= date('now','-14 days')
                AND p2.preco_atual IS NOT NULL)
        ORDER BY dias_erro DESC LIMIT 20
    """).fetchall()
    for r in rows2:
        alertas.append({"nivel":"aviso",
            "titulo":f"{r['nome_produto']} {r['embalagem']} sem preço no {r['supermercado']} ({r['categoria']})",
            "detalhe":f"{r['dias_erro']} dias consecutivos sem coleta.",
            "acao":f"Abra a URL do produto no {r['supermercado']} e verifique se ainda existe."})
    return alertas

# ─── Geração HTML ──────────────────────────────────────────────────────────────
def gerar_html(todos, erros, historico, ultima_data, alertas):
    ok      = [r for r in todos if r.get("preco_atual")]
    err     = [r for r in todos if not r.get("preco_atual")]
    promos  = [r for r in ok if r.get("em_promocao")]
    n_crit  = sum(1 for a in alertas if a["nivel"]=="critico")
    badge   = len(alertas)

    todos_j  = json.dumps(todos,     ensure_ascii=False)
    erros_j  = json.dumps(erros,     ensure_ascii=False)
    hist_j   = json.dumps(historico, ensure_ascii=False)
    alertas_j= json.dumps(alertas,   ensure_ascii=False)

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Monitor de Preços</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/xlsx@0.18.5/dist/xlsx.full.min.js"></script>
<style>
:root{{
  --bg:#f4f6f9;--card:#fff;--border:#e2e8f0;--text:#1a202c;--muted:#718096;
  --accent:#1a56db;--accent2:#1e429f;--green:#0e9f6e;--red:#e02424;
  --yellow:#c27803;--radius:10px;--font:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:var(--font);background:var(--bg);color:var(--text);font-size:14px}}

/* Login */
#login-screen{{display:flex;align-items:center;justify-content:center;min-height:100vh;background:var(--accent2)}}
.login-card{{background:var(--card);border-radius:16px;padding:2.5rem 2rem;width:340px;text-align:center;box-shadow:0 20px 60px rgba(0,0,0,.25)}}
.login-card h2{{color:var(--accent2);margin-bottom:.4rem;font-size:20px}}
.login-card p{{font-size:12px;color:var(--muted);margin-bottom:1.8rem}}
.login-card input{{width:100%;padding:10px 14px;border:1.5px solid var(--border);border-radius:8px;font-size:14px;margin-bottom:12px;outline:none;transition:.2s}}
.login-card input:focus{{border-color:var(--accent)}}
.login-card button{{width:100%;padding:11px;background:var(--accent);color:#fff;border:none;border-radius:8px;font-size:14px;font-weight:600;cursor:pointer}}
.erro-login{{color:var(--red);font-size:12px;margin-top:8px;display:none}}

/* App layout */
#app{{display:none;min-height:100vh}}
header{{background:linear-gradient(135deg,var(--accent2),var(--accent));color:#fff;padding:.85rem 1.5rem;display:flex;justify-content:space-between;align-items:center}}
header h1{{font-size:16px;font-weight:700;letter-spacing:.3px}}
header .meta{{font-size:11px;opacity:.75;text-align:right;line-height:1.6}}

/* Top tab bar */
.tab-bar{{background:var(--card);border-bottom:1.5px solid var(--border);padding:0 1.5rem;display:flex;overflow-x:auto;gap:0}}
.tab-btn{{padding:11px 16px;background:none;border:none;border-bottom:2.5px solid transparent;font-size:13px;font-weight:500;cursor:pointer;color:var(--muted);white-space:nowrap;transition:.15s;position:relative}}
.tab-btn.active{{color:var(--accent);border-bottom-color:var(--accent)}}
.tab-btn:hover:not(.active){{color:var(--text)}}
.nbadge{{position:absolute;top:8px;right:4px;background:var(--red);color:#fff;font-size:9px;font-weight:700;border-radius:8px;padding:1px 5px;min-width:16px}}

/* Main content */
.main{{padding:1.25rem 1.5rem;max-width:1600px;margin:0 auto}}
.page{{display:none}}.page.active{{display:block}}

/* Section cards */
.section{{background:var(--card);border:1px solid var(--border);border-radius:var(--radius);padding:1.1rem 1.25rem;margin-bottom:1.1rem}}
.section-head{{display:flex;justify-content:space-between;align-items:center;margin-bottom:.9rem;flex-wrap:wrap;gap:.5rem}}
.section-title{{font-size:14px;font-weight:600;color:var(--accent2)}}

/* KPI cards */
.kpi-row{{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:.75rem;margin-bottom:1.1rem}}
.kpi{{background:var(--card);border:1px solid var(--border);border-radius:var(--radius);padding:.9rem 1rem}}
.kpi-label{{font-size:11px;color:var(--muted);margin-bottom:3px}}
.kpi-val{{font-size:22px;font-weight:700;color:var(--accent2)}}
.kpi-sub{{font-size:11px;color:var(--muted);margin-top:2px}}
.kpi.warn .kpi-val{{color:var(--red)}}
.kpi.green .kpi-val{{color:var(--green)}}

/* Alertas */
.alerta{{border-radius:8px;padding:11px 14px;display:flex;gap:10px;align-items:flex-start;margin-bottom:8px;border:1px solid transparent}}
.alerta-critico{{background:#fdf2f2;border-color:#fbd5d5}}
.alerta-aviso{{background:#fdf6ec;border-color:#fcd9a0}}
.alerta-info{{background:#ebf5fb;border-color:#bfdbfe}}
.al-icon{{font-size:16px;flex-shrink:0;margin-top:1px}}
.al-body{{flex:1}}
.al-titulo{{font-weight:600;font-size:13px;margin-bottom:2px}}
.alerta-critico .al-titulo{{color:#9b1c1c}}
.alerta-aviso   .al-titulo{{color:#92400e}}
.alerta-info    .al-titulo{{color:#1e429f}}
.al-detalhe{{font-size:11.5px;color:var(--muted);line-height:1.5}}
.al-acao{{font-size:11px;font-style:italic;margin-top:2px}}
.alerta-critico .al-acao{{color:#9b1c1c}}
.alerta-aviso   .al-acao{{color:#92400e}}
.alertas-ok{{background:#f0fdf4;border:1px solid #a7f3d0;border-radius:8px;padding:11px 14px;display:flex;gap:8px;align-items:center;font-size:13px;color:#065f46;margin-bottom:8px}}

/* Filtros */
.filters{{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:.85rem}}
.filters label{{font-size:12px;color:var(--muted);font-weight:500}}
.filters select,.filters input[type=text]{{font-size:12px;padding:5px 9px;border:1px solid var(--border);border-radius:6px;background:var(--card);color:var(--text);outline:none}}
.filters select:focus,.filters input:focus{{border-color:var(--accent)}}

/* Tabela */
.table-wrap{{overflow-x:auto;max-height:520px;overflow-y:auto}}
table{{width:100%;border-collapse:collapse;font-size:12px}}
thead th{{position:sticky;top:0;z-index:2;text-align:left;padding:7px 10px;background:#f8fafc;font-weight:600;font-size:11px;color:var(--muted);border-bottom:1.5px solid var(--border)}}
td{{padding:7px 10px;border-bottom:1px solid var(--border);vertical-align:middle}}
tr:hover td{{background:#f8fafc}}
.badge{{font-size:10px;padding:2px 7px;border-radius:10px;font-weight:500;display:inline-block}}
.b-ok{{background:#d1fae5;color:#065f46}}
.b-err{{background:#fee2e2;color:#991b1b}}
.b-promo{{background:#fef3c7;color:#92400e}}
.b-cf{{background:#dbeafe;color:#1e40af}}
.b-pa{{background:#dcfce7;color:#166534}}
.b-ex{{background:#fff7ed;color:#9a3412}}
.b-at{{background:#fefce8;color:#713f12}}
.b-mt{{background:#f3e8ff;color:#6b21a8}}
.b-rec{{background:#e0f2fe;color:#0369a1;font-size:9px}}

/* Botões */
.btn{{padding:6px 14px;border-radius:7px;border:none;font-size:12px;font-weight:600;cursor:pointer;display:inline-flex;align-items:center;gap:5px;transition:.15s}}
.btn-primary{{background:var(--accent);color:#fff}}
.btn-primary:hover{{background:var(--accent2)}}
.btn-outline{{background:transparent;border:1.5px solid var(--border);color:var(--text)}}
.btn-outline:hover{{border-color:var(--accent);color:var(--accent)}}
.btn-green{{background:#0e9f6e;color:#fff}}
.btn-green:hover{{background:#057a55}}

/* Gráfico */
.chart-row{{display:grid;grid-template-columns:1fr 1fr;gap:1rem;margin-bottom:1rem}}
.chart-row.full{{grid-template-columns:1fr}}
.chart-box{{background:var(--card);border:1px solid var(--border);border-radius:var(--radius);padding:1rem}}
.chart-title{{font-size:13px;font-weight:600;color:var(--accent2);margin-bottom:.75rem}}
.chart-wrap{{position:relative;height:260px}}
.chart-controls{{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-bottom:.75rem}}
.chart-controls label{{font-size:11px;color:var(--muted);font-weight:500}}
.chart-controls select{{font-size:11px;padding:4px 8px;border:1px solid var(--border);border-radius:5px;background:#fff}}

/* Erros */
.err-day-header{{background:#fef2f2;border-left:3px solid var(--red);padding:6px 12px;font-weight:600;font-size:12px;color:#991b1b;margin-bottom:4px;border-radius:4px}}

@media(max-width:700px){{
  .chart-row{{grid-template-columns:1fr}}
  .main{{padding:1rem}}
  .kpi-row{{grid-template-columns:repeat(2,1fr)}}
}}
</style>
</head>
<body>

<!-- LOGIN -->
<div id="login-screen">
  <div class="login-card">
    <h2>Monitor de Preços</h2>
    <p>Supermercados — Brasil</p>
    <input type="password" id="senha-input" placeholder="Senha de acesso" onkeydown="if(event.key==='Enter')login()">
    <button onclick="login()">Entrar</button>
    <div class="erro-login" id="erro-login">Senha incorreta</div>
  </div>
</div>

<!-- APP -->
<div id="app">
  <header>
    <h1>📊 Monitor de Preços — Supermercados</h1>
    <div class="meta">
      Última coleta: <strong>{ultima_data}</strong><br>
      {len(ok)} preços coletados &nbsp;·&nbsp; {len(err)} erros &nbsp;·&nbsp; {len(promos)} promoções
    </div>
  </header>

  <!-- TAB BAR -->
  <div class="tab-bar" id="tab-bar">
    <button class="tab-btn active" onclick="showTab('inicio',this)">Início</button>
    <button class="tab-btn" onclick="showTab('cat-Cervejas',this)">Cervejas</button>
    <button class="tab-btn" onclick="showTab('cat-Embutidos',this)">Embutidos</button>
    <button class="tab-btn" onclick="showTab('cat-Biscoitos',this)">Biscoitos</button>
    <button class="tab-btn" onclick="showTab('cat-Massas',this)">Massas</button>
    <button class="tab-btn" onclick="showTab('cat-Mercearia',this)">Mercearia + Café</button>
    <button class="tab-btn" onclick="showTab('erros',this)">
      Erros{'<span class="nbadge">'+str(len(err))+'</span>' if err else ''}
    </button>
    <button class="tab-btn" onclick="showTab('alertas',this)">
      Alertas{'<span class="nbadge">'+str(badge)+'</span>' if badge else ''}
    </button>
  </div>
  </div>

  <div class="main">

    <!-- ═══ INÍCIO ═══ -->
    <div class="page active" id="page-inicio">

      <!-- KPIs -->
      <div class="kpi-row" id="kpi-row"></div>

      <!-- Alertas críticos no topo se houver -->
      <div id="alertas-banner"></div>

      <!-- Tabela completa com exportação -->
      <div class="section">
        <div class="section-head">
          <span class="section-title">Base de dados — coleta de {ultima_data}</span>
          <button class="btn btn-green" onclick="exportarExcel()">⬇ Excel</button>
        </div>
        <div class="filters">
          <label>Supermercado:</label>
          <select id="f-sm" onchange="filtrarTabela()">
            <option value="">Todos</option>
            <option>Carrefour Mercado</option><option>Pão de Açúcar</option>
            <option>Extra</option><option>Atacadão</option><option>Mateus</option>
          </select>
          <label>Categoria:</label>
          <select id="f-cat" onchange="filtrarTabela()">
            <option value="">Todas</option>
            <option>Cervejas</option><option>Embutidos</option>
            <option>Biscoitos</option><option>Massas</option><option>Mercearia</option>
          </select>
          <label>Marca:</label>
          <select id="f-marca" onchange="filtrarTabela()"><option value="">Todas</option></select>
          <label>UF:</label>
          <select id="f-uf" onchange="filtrarTabela()"><option value="">Todas</option></select>
          <label>Cidade:</label>
          <select id="f-cidade" onchange="filtrarTabela()"><option value="">Todas</option></select>
          <label>Status:</label>
          <select id="f-status" onchange="filtrarTabela()">
            <option value="">Todos</option>
            <option value="ok">✅ Com preço</option>
            <option value="erro">❌ Com erro</option>
          </select>
          <input type="text" id="f-busca" placeholder="Buscar produto..." oninput="filtrarTabela()" style="width:160px">
        </div>
        <div class="table-wrap">
          <table>
            <thead><tr>
              <th>Status</th><th>Supermercado</th><th>Categoria</th>
              <th>Marca</th><th>Produto</th><th>Emb.</th>
              <th>Cidade</th><th>UF</th>
              <th>Preço atual</th><th>Preço original</th><th>Desc.</th>
              <th>Rota CSS</th><th>URL recuperada</th><th>Erro</th>
            </tr></thead>
            <tbody id="tabela-body"></tbody>
          </table>
        </div>
        <div id="tabela-count" style="font-size:11px;color:var(--muted);margin-top:6px;text-align:right"></div>
      </div>

    </div>

    <!-- ═══ ERROS ═══ -->
    <div class="page" id="page-erros">
      <div class="section">
        <div class="section-head">
          <span class="section-title">Erros por dia de coleta</span>
          <div style="display:flex;gap:6px;flex-wrap:wrap">
            <button class="btn btn-green" onclick="exportarErrosExcel()">⬇ Excel</button>
          </div>
        </div>
        <div class="filters">
          <label>Dia:</label>
          <select id="fe-dia" onchange="filtrarErros()"><option value="">Todos</option></select>
          <label>Supermercado:</label>
          <select id="fe-sm" onchange="filtrarErros()">
            <option value="">Todos</option>
            <option>Carrefour Mercado</option><option>Pão de Açúcar</option>
            <option>Extra</option><option>Atacadão</option><option>Mateus</option>
          </select>
          <label>Categoria:</label>
          <select id="fe-cat" onchange="filtrarErros()">
            <option value="">Todas</option>
            <option>Cervejas</option><option>Embutidos</option>
            <option>Biscoitos</option><option>Massas</option><option>Mercearia</option>
          </select>
          <label>UF:</label>
          <select id="fe-uf" onchange="filtrarErros()"><option value="">Todas</option></select>
          <label>Tipo de erro:</label>
          <select id="fe-tipo" onchange="filtrarErros()"><option value="">Todos</option></select>
        </div>
        <div class="table-wrap">
          <table>
            <thead><tr>
              <th>Data</th><th>Supermercado</th><th>Categoria</th>
              <th>Produto</th><th>Emb.</th><th>Cidade</th>
              <th>Tipo de erro</th><th>URL</th><th>URL recuperada</th>
            </tr></thead>
            <tbody id="erros-body"></tbody>
          </table>
        </div>
        <div id="erros-count" style="font-size:11px;color:var(--muted);margin-top:6px;text-align:right"></div>
      </div>
    </div>

    <!-- ═══ PÁGINAS POR CATEGORIA ═══ -->
    {''.join(gerar_aba_cat(cat) for cat in ["Cervejas","Embutidos","Biscoitos","Massas","Mercearia"])}

    <!-- ═══ ALERTAS ═══ -->
    <div class="page" id="page-alertas">
      <div class="section">
        <div class="section-title" style="margin-bottom:.9rem">Alertas do sistema</div>
        <div id="alertas-lista"></div>
      </div>
    </div>

  </div><!-- /main -->
</div><!-- /app -->

<script>
const SENHA   = "{SENHA}";
const TODOS   = {todos_j};
const ERROS_H = {erros_j};
const HIST    = {hist_j};
const ALERTAS = {alertas_j};

const SM_BADGE = {{"Carrefour Mercado":"b-cf","Pão de Açúcar":"b-pa","Extra":"b-ex","Atacadão":"b-at","Mateus":"b-mt"}};
const AL_ICON  = {{critico:"🔴",aviso:"🟡",info:"🔵"}};
const AL_LABEL = {{critico:"Crítico",aviso:"Aviso",info:"Info"}};
const CORES    = ["#1a56db","#0e9f6e","#e02424","#c27803","#7e3af2","#0694a2","#ff8a4c","#84cc16"];

function fmt(v){{return v!=null?"R$ "+v.toFixed(2).replace(".",","):"—"}}
function fmtPct(a,b){{return(a&&b&&b>a)?"-"+Math.round((b-a)/b*100)+"%":"—"}}

// ── Login ─────────────────────────────────────────────────────────────────────
function login(){{
  if(document.getElementById("senha-input").value===SENHA){{
    sessionStorage.setItem("auth","1");
    document.getElementById("login-screen").style.display="none";
    document.getElementById("app").style.display="block";
    init();
  }} else {{ document.getElementById("erro-login").style.display="block"; }}
}}
window.onload=()=>{{
  if(sessionStorage.getItem("auth")==="1"){{
    document.getElementById("login-screen").style.display="none";
    document.getElementById("app").style.display="block";
    init();
  }}
}};

// ── Navegação de abas ─────────────────────────────────────────────────────────
function showTab(id,btn){{
  document.querySelectorAll(".page").forEach(p=>p.classList.remove("active"));
  document.querySelectorAll(".tab-btn").forEach(b=>b.classList.remove("active"));
  document.getElementById("page-"+id).classList.add("active");
  btn.classList.add("active");
}}

// ── Init ──────────────────────────────────────────────────────────────────────
function init(){{
  renderKPIs();
  renderAlertasBanner();
  renderAlertasLista();
  populaFiltros();
  filtrarTabela();
  populaFiltrosErros();
  filtrarErros();
  initCatPages();
}}

// ── KPIs ──────────────────────────────────────────────────────────────────────
function renderKPIs(){{
  const ok     = TODOS.filter(r=>r.preco_atual);
  const erros  = TODOS.filter(r=>!r.preco_atual);
  const promos = TODOS.filter(r=>r.em_promocao);
  const taxa   = TODOS.length>0?(erros.length/TODOS.length*100).toFixed(0):0;
  const nc     = ALERTAS.filter(a=>a.nivel==="critico").length;
  document.getElementById("kpi-row").innerHTML=`
    <div class="kpi"><div class="kpi-label">Total coletados</div><div class="kpi-val">${{TODOS.length}}</div></div>
    <div class="kpi green"><div class="kpi-label">Com preço</div><div class="kpi-val">${{ok.length}}</div></div>
    <div class="kpi ${{erros.length>0?"warn":""}}"><div class="kpi-label">Com erro</div><div class="kpi-val">${{erros.length}}</div><div class="kpi-sub">${{taxa}}% da coleta</div></div>
    <div class="kpi"><div class="kpi-label">Em promoção</div><div class="kpi-val" style="color:var(--yellow)">${{promos.length}}</div></div>
    <div class="kpi"><div class="kpi-label">Supermercados</div><div class="kpi-val">${{new Set(ok.map(r=>r.supermercado)).size}}</div></div>
    <div class="kpi ${{nc>0?"warn":""}}"><div class="kpi-label">Alertas ativos</div><div class="kpi-val">${{ALERTAS.length}}</div><div class="kpi-sub">${{nc}} críticos</div></div>
  `;
}}

// ── Alertas banner ────────────────────────────────────────────────────────────
function renderAlertasBanner(){{
  const crit = ALERTAS.filter(a=>a.nivel==="critico");
  const el = document.getElementById("alertas-banner");
  if(crit.length===0){{
    el.innerHTML=`<div class="alertas-ok">✅ Nenhum problema crítico detectado na última coleta.</div>`;
  }} else {{
    el.innerHTML=crit.map(a=>`
      <div class="alerta alerta-critico" style="margin-bottom:8px">
        <div class="al-icon">🔴</div>
        <div class="al-body">
          <div class="al-titulo">${{a.titulo}}</div>
          <div class="al-detalhe">${{a.detalhe}}</div>
          <div class="al-acao">O que fazer: ${{a.acao}}</div>
        </div>
      </div>`).join("");
  }}
}}

function renderAlertasLista(){{
  const el=document.getElementById("alertas-lista");
  if(ALERTAS.length===0){{el.innerHTML=`<div class="alertas-ok">✅ Nenhum alerta ativo.</div>`;return;}}
  const ordem=["critico","aviso","info"];
  el.innerHTML=[...ALERTAS].sort((a,b)=>ordem.indexOf(a.nivel)-ordem.indexOf(b.nivel)).map(a=>`
    <div class="alerta alerta-${{a.nivel}}">
      <div class="al-icon">${{AL_ICON[a.nivel]}}</div>
      <div class="al-body">
        <div class="al-titulo">[${{AL_LABEL[a.nivel]}}] ${{a.titulo}}</div>
        <div class="al-detalhe">${{a.detalhe}}</div>
        <div class="al-acao">O que fazer: ${{a.acao}}</div>
      </div>
    </div>`).join("");
}}

// ── Tabela principal ──────────────────────────────────────────────────────────
function populaFiltros(){{
  const marcas  = [...new Set(TODOS.map(r=>r.marca))].sort();
  const cidades = [...new Set(TODOS.map(r=>r.cidade))].sort();
  const ufs     = [...new Set(TODOS.map(r=>r.uf).filter(Boolean))].sort();
  const m=document.getElementById("f-marca");
  const c=document.getElementById("f-cidade");
  const u=document.getElementById("f-uf");
  marcas.forEach(x=>m.innerHTML+=`<option>${{x}}</option>`);
  cidades.forEach(x=>c.innerHTML+=`<option>${{x}}</option>`);
  ufs.forEach(x=>u.innerHTML+=`<option>${{x}}</option>`);
}}

let tabelaData=[];
function filtrarTabela(){{
  const sm    = document.getElementById("f-sm").value;
  const cat   = document.getElementById("f-cat").value;
  const marca = document.getElementById("f-marca").value;
  const uf    = document.getElementById("f-uf").value;
  const cid   = document.getElementById("f-cidade").value;
  const st    = document.getElementById("f-status").value;
  const busca = document.getElementById("f-busca").value.toLowerCase();
  tabelaData = TODOS.filter(r=>
    (!sm    || r.supermercado===sm) &&
    (!cat   || r.categoria===cat) &&
    (!marca || r.marca===marca) &&
    (!uf    || r.uf===uf) &&
    (!cid   || r.cidade===cid) &&
    (!st    || (st==="ok"?!!r.preco_atual:!r.preco_atual)) &&
    (!busca || (r.nome_produto+r.marca+r.embalagem).toLowerCase().includes(busca))
  );
  renderTabela();
}}

function renderTabela(){{
  const body=document.getElementById("tabela-body");
  body.innerHTML=tabelaData.slice(0,2000).map(r=>{{
    const status = r.preco_atual
      ? `<span class="badge b-ok">✅ OK</span>`
      : `<span class="badge b-err">❌ Erro</span>`;
    const rec = r.url_recuperada
      ? `<span class="badge b-rec" title="${{r.url_recuperada}}">🔄 recuperada</span>` : "—";
    const promo = r.em_promocao ? `<span class="badge b-promo">promo</span>` : "";
    const desc = fmtPct(r.preco_atual, r.preco_original);
    const url_short = r.url ? `<a href="${{r.url}}" target="_blank" style="font-size:10px;color:var(--accent)">↗</a>` : "—";
    return `<tr>
      <td>${{status}}</td>
      <td><span class="badge ${{SM_BADGE[r.supermercado]||""}}">${{r.supermercado}}</span></td>
      <td style="color:var(--muted)">${{r.categoria}}</td>
      <td style="font-weight:500">${{r.marca}}</td>
      <td>${{r.nome_produto}} ${{url_short}}</td>
      <td>${{r.embalagem}}</td>
      <td>${{r.cidade}}</td><td>${{r.uf}}</td>
      <td style="font-weight:700;color:${{r.em_promocao?"var(--green)":"inherit"}}">${{fmt(r.preco_atual)}} ${{promo}}</td>
      <td style="color:var(--muted);text-decoration:line-through">${{fmt(r.preco_original)}}</td>
      <td>${{desc!=="—"?`<span class="badge b-promo">${{desc}}</span>`:"—"}}</td>
      <td style="color:var(--muted)">${{r.rota_css?"rota "+r.rota_css:"—"}}</td>
      <td>${{rec}}</td>
      <td style="font-size:11px;color:var(--red);max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${{r.erro||""}}">${{r.erro||""}}</td>
    </tr>`;
  }}).join("");
  document.getElementById("tabela-count").textContent=
    `Exibindo ${{Math.min(tabelaData.length,2000)}} de ${{tabelaData.length}} registros`;
}}

// ── Erros ─────────────────────────────────────────────────────────────────────
function populaFiltrosErros(){{
  const dias  = [...new Set(ERROS_H.map(r=>r.data_coleta))].sort().reverse();
  const tipos = [...new Set(ERROS_H.map(r=>r.erro).filter(Boolean))].sort();
  const ufs   = [...new Set(ERROS_H.map(r=>r.uf).filter(Boolean))].sort();
  const d=document.getElementById("fe-dia");
  const t=document.getElementById("fe-tipo");
  const u=document.getElementById("fe-uf");
  dias.forEach(x=>d.innerHTML+=`<option>${{x}}</option>`);
  tipos.forEach(x=>t.innerHTML+=`<option>${{x}}</option>`);
  ufs.forEach(x=>u.innerHTML+=`<option>${{x}}</option>`);
}}

let errosData=[];
function filtrarErros(){{
  const dia  = document.getElementById("fe-dia").value;
  const sm   = document.getElementById("fe-sm").value;
  const cat  = document.getElementById("fe-cat").value;
  const uf   = document.getElementById("fe-uf").value;
  const tipo = document.getElementById("fe-tipo").value;
  errosData = ERROS_H.filter(r=>
    (!dia  || r.data_coleta===dia) &&
    (!sm   || r.supermercado===sm) &&
    (!cat  || r.categoria===cat) &&
    (!uf   || r.uf===uf) &&
    (!tipo || r.erro===tipo)
  );
  renderErros();
}}

function renderErros(){{
  const body=document.getElementById("erros-body");
  body.innerHTML=errosData.slice(0,2000).map(r=>{{
    const url_s = r.url?`<a href="${{r.url}}" target="_blank" style="font-size:10px;color:var(--accent)">↗ ver</a>`:"—";
    const rec = r.url_recuperada?`<a href="${{r.url_recuperada}}" target="_blank" style="font-size:10px;color:var(--green)">↗ nova</a>`:"—";
    return `<tr>
      <td style="font-weight:500">${{r.data_coleta}}</td>
      <td><span class="badge ${{SM_BADGE[r.supermercado]||""}}">${{r.supermercado}}</span></td>
      <td style="color:var(--muted)">${{r.categoria}}</td>
      <td>${{r.nome_produto}}</td>
      <td>${{r.embalagem}}</td>
      <td>${{r.cidade}}</td>
      <td><span class="badge b-err">${{r.erro||""}}</span></td>
      <td>${{url_s}}</td>
      <td>${{rec}}</td>
    </tr>`;
  }}).join("");
  document.getElementById("erros-count").textContent=
    `${{errosData.length}} erros encontrados`;
}}

// ── Exportação ────────────────────────────────────────────────────────────────
function exportarExcel(){{
  const dados = tabelaData.length > 0 ? tabelaData : TODOS;
  const ws = XLSX.utils.json_to_sheet(dados);
  const wb = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(wb, ws, "Precos");
  // Aba de erros
  if(ERROS_H.length>0){{
    const we = XLSX.utils.json_to_sheet(ERROS_H);
    XLSX.utils.book_append_sheet(wb, we, "Erros");
  }}
  XLSX.writeFile(wb, `monitor_precos_${{'{ultima_data}'}}.xlsx`);
}}


function exportarErrosExcel(){{
  if(!ERROS_H.length) return alert("Nenhum erro registrado.");
  const dados = errosData.length>0 ? errosData : ERROS_H;
  const ws = XLSX.utils.json_to_sheet(dados);
  const wb = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(wb, ws, "Erros");
  XLSX.writeFile(wb, `erros_coleta_${{'{ultima_data}'}}.xlsx`);
}}

// ══════════════════════════════════════════════════════════════════════════════
// PÁGINAS POR CATEGORIA — checkboxes, date range, preço por cidade
// ══════════════════════════════════════════════════════════════════════════════
const catCharts = {{}};

function initCatPages(){{
  ["Cervejas","Embutidos","Biscoitos","Massas","Mercearia"].forEach(cat=>{{
    popularChkProd(cat);
    popularChkSM(cat);
    popularCidadeSel(cat);
    renderGraficoCat(cat);
  }});
}}

// ── Checkboxes de produto ─────────────────────────────────────────────────────
function popularChkProd(cat){{
  const el = document.getElementById("chk-prod-"+cat);
  if(!el) return;
  const prods = [...new Map(
    HIST.filter(r=>r.categoria===cat)
        .map(r=>[r.nome_produto+"_"+r.embalagem, r.nome_produto+" "+r.embalagem])
  ).entries()].sort((a,b)=>a[1].localeCompare(b[1]));
  el.innerHTML = prods.map(([k,v],i)=>`
    <label style="display:flex;align-items:center;gap:6px;padding:3px 0;font-size:11px;cursor:pointer;color:var(--text)">
      <input type="checkbox" value="${{k}}" ${{i<6?"checked":""}}
        onchange="renderGraficoCat('${{cat}}')"
        style="accent-color:var(--accent);cursor:pointer">
      ${{v}}
    </label>`).join("");
}}

// ── Checkboxes de supermercado ────────────────────────────────────────────────
function popularChkSM(cat){{
  const el = document.getElementById("chk-sm-"+cat);
  if(!el) return;
  const sms = [...new Set(HIST.filter(r=>r.categoria===cat).map(r=>r.supermercado))].sort();
  el.innerHTML = sms.map(sm=>`
    <label style="display:flex;align-items:center;gap:6px;padding:3px 0;font-size:11px;cursor:pointer;color:var(--text)">
      <input type="checkbox" value="${{sm}}" checked
        onchange="renderGraficoCat('${{cat}}')"
        style="accent-color:var(--accent);cursor:pointer">
      ${{sm}}
    </label>`).join("");
}}

// ── Select de cidade ──────────────────────────────────────────────────────────
function popularCidadeSel(cat){{
  const sel = document.getElementById("sel-cidade-"+cat);
  if(!sel) return;
  const cidades = [...new Set(HIST.filter(r=>r.categoria===cat).map(r=>r.cidade))].sort();
  sel.innerHTML = `<option value="">Todas (média)</option>` +
    cidades.map(c=>`<option>${{c}}</option>`).join("");
}}

// ── Marcar/desmarcar todos ────────────────────────────────────────────────────
function toggleTodos(containerId, marcar, cat){{
  document.querySelectorAll(`#${{containerId}} input[type=checkbox]`)
    .forEach(cb=>cb.checked=marcar);
  renderGraficoCat(cat);
}}

// ── Lê checkboxes marcados ────────────────────────────────────────────────────
function getChecked(containerId){{
  return [...document.querySelectorAll(`#${{containerId}} input[type=checkbox]:checked`)]
    .map(cb=>cb.value);
}}

// ── Controle de período ───────────────────────────────────────────────────────
function onPeriodoChange(cat){{
  const val = document.getElementById("sel-periodo-"+cat).value;
  const rangeEl = document.getElementById("range-"+cat);
  rangeEl.style.display = val==="custom" ? "flex" : "none";
  renderGraficoCat(cat);
}}

function getPeriodoDatas(cat, todasDatas){{
  const sorted = [...todasDatas].sort();
  if(!sorted.length) return sorted;
  const periodo = document.getElementById("sel-periodo-"+cat)?.value || "tudo";
  if(periodo==="7d")   return sorted.slice(-7);
  if(periodo==="30d")  return sorted.slice(-30);
  if(periodo==="3m")   return sorted.slice(-90);
  if(periodo==="ano")  return sorted.filter(d=>d.startsWith(sorted[sorted.length-1].slice(0,4)));
  if(periodo==="custom"){{
    const de  = document.getElementById("dt-de-"+cat)?.value  || "";
    const ate = document.getElementById("dt-ate-"+cat)?.value || "";
    return sorted.filter(d=>(!de||d>=de)&&(!ate||d<=ate));
  }}
  return sorted;
}}

// ── Renderiza gráfico ─────────────────────────────────────────────────────────
function renderGraficoCat(cat){{
  const prodsSel  = getChecked("chk-prod-"+cat);
  const smsSel    = getChecked("chk-sm-"+cat);
  const cidadeSel = document.getElementById("sel-cidade-"+cat)?.value || "";

  let dados = HIST.filter(r=>r.categoria===cat);
  if(prodsSel.length)  dados=dados.filter(r=>prodsSel.some(p=>{{
    const[nome,emb]=p.split("_"); return r.nome_produto===nome&&r.embalagem===emb;
  }}));
  if(smsSel.length)    dados=dados.filter(r=>smsSel.includes(r.supermercado));
  if(cidadeSel)        dados=dados.filter(r=>r.cidade===cidadeSel);

  const todasDatas = [...new Set(dados.map(r=>r.data_coleta))].sort();
  const datas = getPeriodoDatas(cat, todasDatas);
  const dadosFiltrados = dados.filter(r=>datas.includes(r.data_coleta));

  // Agrupa por série: supermercado · produto embalagem
  // Se cidade selecionada: usa preco_atual direto
  // Se "Todas": calcula média das cidades no JS
  const series = new Map();
  dadosFiltrados.forEach(r=>{{
    const key = r.supermercado+" · "+r.nome_produto+" "+r.embalagem;
    if(!series.has(key)) series.set(key, {{}});
    const byDate = series.get(key);
    if(!byDate[r.data_coleta]) byDate[r.data_coleta] = [];
    byDate[r.data_coleta].push(r.preco_atual);
  }});

  const datasets = [...series.entries()].slice(0,16).map(([label,byDate],i)=>{{
    const data = datas.map(d=>{{
      const vals = byDate[d];
      if(!vals||!vals.length) return null;
      const media = vals.reduce((a,b)=>a+b,0)/vals.length;
      return +media.toFixed(2);
    }});
    return {{
      label,
      data,
      borderColor: CORES[i%CORES.length],
      backgroundColor: CORES[i%CORES.length]+"22",
      tension:.3, spanGaps:true, pointRadius:3, pointHoverRadius:5,
    }};
  }});

  const canvasId = "chart-"+cat;
  if(catCharts[cat]){{ catCharts[cat].destroy(); catCharts[cat]=null; }}
  const ctx = document.getElementById(canvasId);
  if(!ctx) return;

  const labelCidade = cidadeSel ? ` — ${{cidadeSel}}` : " — média das cidades";
  catCharts[cat] = new Chart(ctx,{{
    type:"line",
    data:{{labels:datas, datasets}},
    options:{{
      responsive:true, maintainAspectRatio:false,
      interaction:{{mode:"index",intersect:false}},
      plugins:{{
        legend:{{position:"top",labels:{{boxWidth:12,font:{{size:11}}}}}},
        tooltip:{{
          callbacks:{{
            title: items => items[0].label + labelCidade,
            label: c => c.dataset.label+": "+fmt(c.parsed.y)
          }}
        }}
      }},
      scales:{{
        y:{{beginAtZero:false,ticks:{{callback:v=>"R$"+v.toFixed(2).replace(".",",")}}}},
        x:{{ticks:{{maxTicksLimit:14,font:{{size:10}}}}}}
      }}
    }}
  }});

  const ultimoDia = datas[datas.length-1];
  renderTabelaComparacao(cat, dadosFiltrados.filter(r=>r.data_coleta===ultimoDia), ultimoDia, cidadeSel);
}}

// ── Tabela comparativa ────────────────────────────────────────────────────────
function renderTabelaComparacao(cat, dados, dia, cidadeSel){{
  const el = document.getElementById("tabela-comp-"+cat);
  if(!el) return;
  if(!dados.length){{
    el.innerHTML=`<p style="color:var(--muted);font-size:12px;padding:.5rem 0">Nenhum dado para o período/filtro selecionado.</p>`;
    return;
  }}
  // Agrupa por supermercado+produto e calcula média (ou preço único se cidade filtrada)
  const agrupado = new Map();
  dados.forEach(r=>{{
    const key = r.supermercado+"||"+r.nome_produto+"||"+r.embalagem;
    if(!agrupado.has(key)) agrupado.set(key,{{
      supermercado:r.supermercado, nome_produto:r.nome_produto,
      embalagem:r.embalagem, precos:[], cidade:r.cidade
    }});
    agrupado.get(key).precos.push(r.preco_atual);
  }});
  const linhas = [...agrupado.values()].map(g=>{{
    const sorted_p = [...g.precos].sort((a,b)=>a-b);
    return {{
      supermercado:g.supermercado, nome_produto:g.nome_produto, embalagem:g.embalagem,
      cidade:g.cidade,
      preco_medio: g.precos.reduce((a,b)=>a+b,0)/g.precos.length,
      preco_min: sorted_p[0],
      preco_max: sorted_p[sorted_p.length-1],
      num_cidades: g.precos.length
    }};
  }}).sort((a,b)=>a.preco_medio-b.preco_medio);

  const minPreco = linhas[0]?.preco_medio;
  const labelPreco = cidadeSel ? `Preço em ${{cidadeSel}}` : "Preço médio (cidades)";
  const labelCols  = cidadeSel
    ? `<th>Supermercado</th><th>Produto</th><th>Emb.</th><th>${{labelPreco}}</th>`
    : `<th>Supermercado</th><th>Produto</th><th>Emb.</th><th>Preço médio</th><th>Mín. cidade</th><th>Máx. cidade</th><th>Cidades</th>`;

  const desc = cidadeSel
    ? `Preço coletado em <strong>${{cidadeSel}}</strong> no dia <strong>${{dia||"—"}}</strong>`
    : `Média das cidades coletadas no dia <strong>${{dia||"—"}}</strong>`;

  el.innerHTML=`
    <p style="font-size:11px;color:var(--muted);margin-bottom:6px">${{desc}}</p>
    <div class="table-wrap" style="max-height:220px">
    <table>
      <thead><tr>${{labelCols}}</tr></thead>
      <tbody>
      ${{linhas.map(r=>`<tr style="${{r.preco_medio===minPreco?"background:#f0fdf4":""}}">
        <td><span class="badge ${{SM_BADGE[r.supermercado]||""}}">${{r.supermercado}}</span></td>
        <td style="font-weight:${{r.preco_medio===minPreco?600:400}}">${{r.nome_produto}}</td>
        <td>${{r.embalagem}}</td>
        <td style="font-weight:600;color:${{r.preco_medio===minPreco?"var(--green)":"inherit"}}">
          ${{fmt(r.preco_medio)}} ${{r.preco_medio===minPreco?"🏆":""}}
        </td>
        ${{!cidadeSel?`<td style="color:var(--muted)">${{fmt(r.preco_min)}}</td><td style="color:var(--muted)">${{fmt(r.preco_max)}}</td><td style="color:var(--muted)">${{r.num_cidades}}</td>`:"" }}
      </tr>`).join("")}}
      </tbody>
    </table>
    </div>`;
}}
</script>
</body>
</html>"""

def gerar_aba_cat(cat):
    cid = f"cat-{cat}"
    nome_display = "Mercearia + Café" if cat == "Mercearia" else cat
    return f"""
    <div class="page" id="page-{cid}">
      <div class="section">
        <div class="section-head">
          <span class="section-title">{nome_display} — Monitoramento de preços</span>
          <span style="font-size:11px;color:var(--muted)">Preço médio entre as cidades coletadas</span>
        </div>

        <!-- Controles do gráfico -->
        <div style="display:flex;gap:1.25rem;flex-wrap:wrap;align-items:flex-start;margin-bottom:.85rem">

          <!-- Produtos (checkboxes) -->
          <div>
            <div style="font-size:11px;font-weight:600;color:var(--muted);margin-bottom:5px;text-transform:uppercase;letter-spacing:.5px">Produto</div>
            <div id="chk-prod-{cat}"
              style="max-height:160px;overflow-y:auto;border:1px solid var(--border);border-radius:6px;padding:6px 8px;min-width:210px;background:var(--card)">
            </div>
            <button onclick="toggleTodos('chk-prod-{cat}',true,'{cat}')"
              style="font-size:10px;color:var(--accent);background:none;border:none;cursor:pointer;padding:3px 0;margin-right:8px">todos</button>
            <button onclick="toggleTodos('chk-prod-{cat}',false,'{cat}')"
              style="font-size:10px;color:var(--muted);background:none;border:none;cursor:pointer;padding:3px 0">nenhum</button>
          </div>

          <!-- Supermercados (checkboxes) -->
          <div>
            <div style="font-size:11px;font-weight:600;color:var(--muted);margin-bottom:5px;text-transform:uppercase;letter-spacing:.5px">Supermercado</div>
            <div id="chk-sm-{cat}"
              style="border:1px solid var(--border);border-radius:6px;padding:6px 8px;min-width:170px;background:var(--card)">
            </div>
            <button onclick="toggleTodos('chk-sm-{cat}',true,'{cat}')"
              style="font-size:10px;color:var(--accent);background:none;border:none;cursor:pointer;padding:3px 0;margin-right:8px">todos</button>
            <button onclick="toggleTodos('chk-sm-{cat}',false,'{cat}')"
              style="font-size:10px;color:var(--muted);background:none;border:none;cursor:pointer;padding:3px 0">nenhum</button>
          </div>

          <!-- Período -->
          <div>
            <div style="font-size:11px;font-weight:600;color:var(--muted);margin-bottom:5px;text-transform:uppercase;letter-spacing:.5px">Período</div>
            <select id="sel-periodo-{cat}" onchange="onPeriodoChange('{cat}')"
              style="font-size:11px;border:1px solid var(--border);border-radius:6px;padding:5px 8px;display:block;width:180px;margin-bottom:6px">
              <option value="tudo">Todo o histórico</option>
              <option value="7d">Últimos 7 dias</option>
              <option value="30d">Últimos 30 dias</option>
              <option value="3m">Últimos 3 meses</option>
              <option value="ano">Este ano</option>
              <option value="custom">Intervalo personalizado...</option>
            </select>
            <div id="range-{cat}" style="display:none;flex-direction:column;gap:4px">
              <div style="display:flex;align-items:center;gap:6px;font-size:11px;color:var(--muted)">
                <span>De</span>
                <input type="date" id="dt-de-{cat}" onchange="renderGraficoCat('{cat}')"
                  style="font-size:11px;border:1px solid var(--border);border-radius:5px;padding:4px 6px">
              </div>
              <div style="display:flex;align-items:center;gap:6px;font-size:11px;color:var(--muted)">
                <span>Até</span>
                <input type="date" id="dt-ate-{cat}" onchange="renderGraficoCat('{cat}')"
                  style="font-size:11px;border:1px solid var(--border);border-radius:5px;padding:4px 6px">
              </div>
            </div>
          </div>

          <!-- Cidade -->
          <div>
            <div style="font-size:11px;font-weight:600;color:var(--muted);margin-bottom:5px;text-transform:uppercase;letter-spacing:.5px">Cidade</div>
            <select id="sel-cidade-{cat}" onchange="renderGraficoCat('{cat}')"
              style="font-size:11px;border:1px solid var(--border);border-radius:6px;padding:5px 8px;width:155px">
              <option value="">Todas (média)</option>
            </select>
          </div>

        </div>

        <!-- Gráfico de linha -->
        <div class="chart-wrap" style="height:300px;margin-bottom:1rem">
          <canvas id="chart-{cat}"></canvas>
        </div>

        <!-- Tabela comparativa do dia mais recente -->
        <div class="section-title" style="font-size:12px;margin-bottom:.5rem">Comparação — dia mais recente do período</div>
        <div id="tabela-comp-{cat}"></div>
      </div>
    </div>
"""

def main():
    OUT_PATH.parent.mkdir(exist_ok=True)
    todos, erros, historico, ultima_data, alertas = carregar_dados()
    if not ultima_data:
        ultima_data = str(date.today())
    html = gerar_html(todos, erros, historico, ultima_data, alertas)
    OUT_PATH.write_text(html, encoding="utf-8")
    n_err = sum(1 for r in todos if not r.get("preco_atual"))
    nc = sum(1 for a in alertas if a["nivel"]=="critico")
    print(f"Dashboard gerado: {len(todos)} registros, {n_err} erros, {nc} alertas críticos")

if __name__ == "__main__":
    main()
