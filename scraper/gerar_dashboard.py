"""
Monitor de Preços — Dashboard HTML completo
Abas: Início | Erros | Cervejas | Embutidos | Biscoitos | Massas | Mercearia
"""
import sqlite3, json, csv, io
from pathlib import Path
from datetime import date
from collections import defaultdict

_ROOT    = Path(__file__).resolve().parent.parent
DB_PATH  = _ROOT / "data/precos.db"
OUT_PATH = _ROOT / "docs/index.html"
SENHA    = "ibbafb123"
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

    # Melhor registro por produto/supermercado/cidade no dia mais recente:
    # - se tem preço: pega o menor preço do dia
    # - se só tem erro: pega o mais recente
    todos = [dict(r) for r in con.execute("""
        SELECT p.* FROM precos p
        INNER JOIN (
            SELECT supermercado, categoria, nome_produto, embalagem, cidade,
                   CASE WHEN MIN(preco_atual) IS NOT NULL
                        THEN MIN(preco_atual)
                        ELSE NULL END AS melhor_preco,
                   MAX(id) AS ultimo_id
            FROM precos
            WHERE data_coleta=?
            GROUP BY supermercado, categoria, nome_produto, embalagem, cidade
        ) m ON p.supermercado=m.supermercado
           AND p.categoria=m.categoria
           AND p.nome_produto=m.nome_produto
           AND p.embalagem=m.embalagem
           AND p.cidade=m.cidade
           AND p.data_coleta=?
           AND (
               (m.melhor_preco IS NOT NULL AND p.preco_atual=m.melhor_preco)
               OR
               (m.melhor_preco IS NULL AND p.id=m.ultimo_id)
           )
        GROUP BY p.supermercado, p.categoria, p.nome_produto, p.embalagem, p.cidade
        ORDER BY p.categoria, p.supermercado, p.nome_produto
    """, (ultima, ultima)).fetchall()]

    # Erros — só o mais recente por produto/supermercado/cidade/data
    erros = [dict(r) for r in con.execute("""
        SELECT data_coleta, supermercado, categoria, marca, nome_produto, embalagem,
               cidade, uf, url, url_recuperada, erro, rota_css
        FROM precos
        WHERE erro IS NOT NULL
          AND id IN (
              SELECT MAX(id) FROM precos
              WHERE erro IS NOT NULL
              GROUP BY data_coleta, supermercado, nome_produto, embalagem, cidade
          )
        ORDER BY data_coleta DESC, supermercado, categoria, nome_produto
    """).fetchall()]

    # Histórico — menor preço por data/produto/supermercado/cidade
    historico = [dict(r) for r in con.execute("""
        SELECT data_coleta, supermercado, categoria, marca, nome_produto, embalagem,
               cidade, uf, regiao, MIN(preco_atual) as preco_atual
        FROM precos
        WHERE preco_atual IS NOT NULL AND disponivel=1
        GROUP BY data_coleta, supermercado, categoria, marca, nome_produto, embalagem, cidade
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
<title>Monitor de Preços F&B - Supermercados</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/xlsx@0.18.5/dist/xlsx.full.min.js"></script>
<style>
:root{{
  --bg:#f4f6f9;--card:#fff;--border:#e2e8f0;--text:#1a202c;--muted:#718096;
  --accent:#0a0a0f;--accent2:#0a0a0f;--green:#0e9f6e;--red:#e02424;
  --yellow:#c27803;--radius:10px;--font:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:var(--font);background:var(--bg);color:var(--text);font-size:14px}}

/* Login */
#login-screen{{display:flex;align-items:center;justify-content:center;min-height:100vh;background:#0a0a0f}}
.login-card{{background:var(--card);border-radius:16px;padding:2.5rem 2rem;width:340px;text-align:center;box-shadow:0 20px 60px rgba(0,0,0,.25)}}
.login-card h2{{color:#0a0a0f;margin-bottom:.4rem;font-size:20px}}
.login-card p{{font-size:12px;color:var(--muted);margin-bottom:1.8rem}}
.login-card input{{width:100%;padding:10px 14px;border:1.5px solid var(--border);border-radius:8px;font-size:14px;margin-bottom:12px;outline:none;transition:.2s}}
.login-card input:focus{{border-color:var(--accent)}}
.login-card button{{width:100%;padding:11px;background:#0a0a0f;color:#fff;border:none;border-radius:8px;font-size:14px;font-weight:600;cursor:pointer}}
.erro-login{{color:var(--red);font-size:12px;margin-top:8px;display:none}}

/* App layout */
#app{{display:none;min-height:100vh}}
header{{background:#0a0a0f;color:#fff;padding:.85rem 1.5rem;display:flex;justify-content:space-between;align-items:center}}
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
    <div style="display:flex;justify-content:center;margin-bottom:1.25rem"><img src="data:image/png;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/4gHYSUNDX1BST0ZJTEUAAQEAAAHIAAAAAAQwAABtbnRyUkdCIFhZWiAH4AABAAEAAAAAAABhY3NwAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAQAA9tYAAQAAAADTLQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAlkZXNjAAAA8AAAACRyWFlaAAABFAAAABRnWFlaAAABKAAAABRiWFlaAAABPAAAABR3dHB0AAABUAAAABRyVFJDAAABZAAAAChnVFJDAAABZAAAAChiVFJDAAABZAAAAChjcHJ0AAABjAAAADxtbHVjAAAAAAAAAAEAAAAMZW5VUwAAAAgAAAAcAHMAUgBHAEJYWVogAAAAAAAAb6IAADj1AAADkFhZWiAAAAAAAABimQAAt4UAABjaWFlaIAAAAAAAACSgAAAPhAAAts9YWVogAAAAAAAA9tYAAQAAAADTLXBhcmEAAAAAAAQAAAACZmYAAPKnAAANWQAAE9AAAApbAAAAAAAAAABtbHVjAAAAAAAAAAEAAAAMZW5VUwAAACAAAAAcAEcAbwBvAGcAbABlACAASQBuAGMALgAgADIAMAAxADb/2wBDAAUDBAQEAwUEBAQFBQUGBwwIBwcHBw8LCwkMEQ8SEhEPERETFhwXExQaFRERGCEYGh0dHx8fExciJCIeJBweHx7/2wBDAQUFBQcGBw4ICA4eFBEUHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh7/wAARCADUAZwDASIAAhEBAxEB/8QAHQABAAICAwEBAAAAAAAAAAAAAAcIBgkCAwUEAf/EAFcQAAEDAgMDBAYVCAoCAgMAAAEAAgMEBQYHEQgSIRMxQVEUOGFzkbQJFxgiNDdSU1ZXcXJ0dYGSlaGxstIjMjZilLPR0xUWJDM1QlV2gpNjokPhpKXj/8QAGAEBAAMBAAAAAAAAAAAAAAAAAAECAwT/xAAiEQEBAQEAAgEFAAMAAAAAAAAAAQIREjEDEyEyQVEiM0L/2gAMAwEAAhEDEQA/ALlryMX4msOEbDUX3El0p7bbqcavmmJ5+hrQNS5x6GtBJ6Av3GOIbThPDFwxHfKkU1ut8Jmnk5zoOYAdLidAB0kgLWZn1m5iDNjFclwuEklNaYHkW63B3nKdnWfVPPOXH3BoAArZz0TTm7tjXyunnt2W9tjtdGNWtuVbGJKh/wCsyM6sZ/y3j7nMq8YnzEx5iaZ8l+xhfLgHOLuTlrX8m0/qsB3W+4AFiyLeYjPrsM0zjqZXknpLivzlZfXH/OK4IrHHPlZfXH/OKcrL64/5xXBEQ58rL64/5xTlZfXH/OK4Ig58rL64/wCcU5WX1x/ziuCIOfKy+uP+cU5WX1x/ziuCIOfKy+uP+cU5WX1x/wA4rgiDnysvrj/nFOVl9cf84rgiDnysvrj/AJxTlZfXH/OK4Ig58rL64/5xTlZfXH/OK4Ig9iyYpxNY5xPZcRXa2yt5n0tZJEfC0hTblltZ5j4Znip8SvhxVbRoHNqQI6lrf1ZWjiffhyr0iXMvsbVsn818HZpWY12Ga/8AtMTQaqgn0ZU0xPqm68R1OaS09euoGdLUTgzFF9wdiOlxDhy4y0FxpXb0csZ5x0tcDwc08xaeBWy3Z6zWtmbOBmXmnYylulK4Q3OjB15GXTXVuvEscNS0+6OdpXPrPF+pJREVFhERAREQEREBERAREQEREBERAREQEREBERAREQEREFJfJDMxJaq+27La3zObTUTW11y3TwfK4fkmH3rdXdR329SqSsvzov0mJ82cU318he2quk5iJ6ImvLYx8jA0LEF1ZnIrRERSoIiICIiAiIgIiICIiAiIgIiICIiAiIgIiIOSlTZZzDly7zetddLO5lpuDxQ3Jm953knkASH3jtHa8+gcOlRSijietyCLDMjL9JibJ3Cd7mkMk9TaoOXef80rWhkh+c1yzNclaCIikEREBERAREQEREBERAREQEREBERAREQEREBERBp1riXVs7nHUmRxJ+VdK7az0XN3x32rqXWyEREBERAREQEREBERAREQEREBERAREQEREBERAREQbM9jkl2zbhAk6nkqgf8A5Uql1RFsb9rZhDvVT41MpdXLr3WkERFCRERAREQEREBERAREQEREBERAREQEREBERAREQadKz0XN3x32rqXbWei5u+O+1dS62QiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiDZnsb9rZhDvVT41MpdURbG/a2YQ71U+NTKXVy691pBERQkREQEREBERAREQEREBERAREQER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" style="height:48px;object-fit:contain" alt="Itau BBA"></div>
    <h2>Monitor de Preços F&B</h2>
    <p>Supermercados — Brasil</p>
    <input type="text" id="usuario-input" placeholder="Usuário" style="margin-bottom:8px" onkeydown="if(event.key==='Enter')document.getElementById('senha-input').focus()">
    <input type="password" id="senha-input" placeholder="Senha" onkeydown="if(event.key==='Enter')login()">
    <button onclick="login()">Entrar</button>
    <div class="erro-login" id="erro-login">Usuário ou senha incorretos</div>
  </div>
</div>

<!-- APP -->
<div id="app">
  <header>
    <div style="display:flex;align-items:center;gap:12px">
      <img src="data:image/png;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/4gHYSUNDX1BST0ZJTEUAAQEAAAHIAAAAAAQwAABtbnRyUkdCIFhZWiAH4AABAAEAAAAAAABhY3NwAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAQAA9tYAAQAAAADTLQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAlkZXNjAAAA8AAAACRyWFlaAAABFAAAABRnWFlaAAABKAAAABRiWFlaAAABPAAAABR3dHB0AAABUAAAABRyVFJDAAABZAAAAChnVFJDAAABZAAAAChiVFJDAAABZAAAAChjcHJ0AAABjAAAADxtbHVjAAAAAAAAAAEAAAAMZW5VUwAAAAgAAAAcAHMAUgBHAEJYWVogAAAAAAAAb6IAADj1AAADkFhZWiAAAAAAAABimQAAt4UAABjaWFlaIAAAAAAAACSgAAAPhAAAts9YWVogAAAAAAAA9tYAAQAAAADTLXBhcmEAAAAAAAQAAAACZmYAAPKnAAANWQAAE9AAAApbAAAAAAAAAABtbHVjAAAAAAAAAAEAAAAMZW5VUwAAACAAAAAcAEcAbwBvAGcAbABlACAASQBuAGMALgAgADIAMAAxADb/2wBDAAUDBAQEAwUEBAQFBQUGBwwIBwcHBw8LCwkMEQ8SEhEPERETFhwXExQaFRERGCEYGh0dHx8fExciJCIeJBweHx7/2wBDAQUFBQcGBw4ICA4eFBEUHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh7/wAARCADUAZwDASIAAhEBAxEB/8QAHQABAAICAwEBAAAAAAAAAAAAAAcIBgkCAwUEAf/EAFcQAAEDAgMDBAYVCAoCAgMAAAEAAgMEBQYHEQgSIRMxQVEUOGFzkbQJFxgiNDdSU1ZXcXJ0dYGSlaGxstIjMjZilLPR0xUWJDM1QlV2gpNjokPhpKXj/8QAGAEBAAMBAAAAAAAAAAAAAAAAAAECAwT/xAAiEQEBAQEAAgEFAAMAAAAAAAAAAQIREjEDEyEyQVEiM0L/2gAMAwEAAhEDEQA/ALlryMX4msOEbDUX3El0p7bbqcavmmJ5+hrQNS5x6GtBJ6Av3GOIbThPDFwxHfKkU1ut8Jmnk5zoOYAdLidAB0kgLWZn1m5iDNjFclwuEklNaYHkW63B3nKdnWfVPPOXH3BoAArZz0TTm7tjXyunnt2W9tjtdGNWtuVbGJKh/wCsyM6sZ/y3j7nMq8YnzEx5iaZ8l+xhfLgHOLuTlrX8m0/qsB3W+4AFiyLeYjPrsM0zjqZXknpLivzlZfXH/OK4IrHHPlZfXH/OKcrL64/5xXBEQ58rL64/5xTlZfXH/OK4Ig58rL64/wCcU5WX1x/ziuCIOfKy+uP+cU5WX1x/ziuCIOfKy+uP+cU5WX1x/wA4rgiDnysvrj/nFOVl9cf84rgiDnysvrj/AJxTlZfXH/OK4Ig58rL64/5xTlZfXH/OK4Ig9iyYpxNY5xPZcRXa2yt5n0tZJEfC0hTblltZ5j4Znip8SvhxVbRoHNqQI6lrf1ZWjiffhyr0iXMvsbVsn818HZpWY12Ga/8AtMTQaqgn0ZU0xPqm68R1OaS09euoGdLUTgzFF9wdiOlxDhy4y0FxpXb0csZ5x0tcDwc08xaeBWy3Z6zWtmbOBmXmnYylulK4Q3OjB15GXTXVuvEscNS0+6OdpXPrPF+pJREVFhERAREQEREBERAREQEREBERAREQEREBERAREQEREFJfJDMxJaq+27La3zObTUTW11y3TwfK4fkmH3rdXdR329SqSsvzov0mJ82cU318he2quk5iJ6ImvLYx8jA0LEF1ZnIrRERSoIiICIiAiIgIiICIiAiIgIiICIiAiIgIiIOSlTZZzDly7zetddLO5lpuDxQ3Jm953knkASH3jtHa8+gcOlRSijietyCLDMjL9JibJ3Cd7mkMk9TaoOXef80rWhkh+c1yzNclaCIikEREBERAREQEREBERAREQEREBERAREQEREBERBp1riXVs7nHUmRxJ+VdK7az0XN3x32rqXWyEREBERAREQEREBERAREQEREBERAREQEREBERAREQbM9jkl2zbhAk6nkqgf8A5Uql1RFsb9rZhDvVT41MpdXLr3WkERFCRERAREQEREBERAREQEREBERAREQEREBERAREQadKz0XN3x32rqXbWei5u+O+1dS62QiIgIiICIiAiIgIiICIiAiIgIiICIiAiIgIiICIiDZnsb9rZhDvVT41MpdURbG/a2YQ71U+NTKXVy691pBERQkREQEREBERAREQEREBERAREQER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" style="height:28px;object-fit:contain;filter:brightness(0) invert(1)" alt="Itau BBA">
      <div style="width:1px;height:24px;background:rgba(255,255,255,.25)"></div>
      <h1>Monitor de Preços F&B — Supermercados</h1>
    </div>
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
const USUARIOS = {{
  "ryumatsuyama":  "admin123",
  "brunotomazetto":"admin123",
  "gustavotroyano":"admin123"
}};
const SENHA_GLOBAL = "ibbafb123";
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
  const usuario = (document.getElementById("usuario-input")?.value||"").trim().toLowerCase();
  const senha   = document.getElementById("senha-input").value;
  const ok = (USUARIOS[usuario] && USUARIOS[usuario]===senha) || senha===SENHA_GLOBAL;
  if(ok){{
    sessionStorage.setItem("auth","1");
    sessionStorage.setItem("usuario", usuario||"admin");
    document.getElementById("login-screen").style.display="none";
    document.getElementById("app").style.display="block";
    init();
  }} else {{
    document.getElementById("erro-login").style.display="block";
  }}
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
  const sms     = [...new Set(TODOS.map(r=>r.supermercado))].sort();
  const m = document.getElementById("f-marca");
  if(m) marcas.forEach(x=>m.innerHTML+=`<option>${{x}}</option>`);
  // f-cidade e f-uf foram removidos (só SP por enquanto)
}}

let tabelaData=[];
function filtrarTabela(){{
  const sm    = document.getElementById("f-sm").value;
  const cat   = document.getElementById("f-cat").value;
  const marca = document.getElementById("f-marca").value;
  const uf    = "";
  const cid   = "";
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
