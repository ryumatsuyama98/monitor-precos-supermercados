"""
Script de limpeza do banco de dados.
Remove:
1. Todo o histórico do Carrefour Mercado
2. 4 SKUs específicos do Atacadão:
   - Stella Artois Lata 350ml
   - Corona Extra Long Neck 355ml
   - Spaten Pilsner Long Neck 355ml  
   - Brahma Chopp 350ml
"""
import sqlite3
from pathlib import Path

_ROOT   = Path(__file__).resolve().parent.parent
DB_PATH = _ROOT / "data/precos.db"

if not DB_PATH.exists():
    print(f"Banco não encontrado: {DB_PATH}")
    exit(1)

con = sqlite3.connect(DB_PATH)

# Antes
total_antes = con.execute("SELECT COUNT(*) FROM precos").fetchone()[0]
print(f"Registros antes: {total_antes}")

# 1. Deleta todo o Carrefour
r1 = con.execute("DELETE FROM precos WHERE supermercado='Carrefour Mercado'")
print(f"Carrefour deletado: {r1.rowcount} registros")

# 2. Deleta os 4 SKUs do Atacadão
# Deleta por nome parcial E embalagem — cobre variações históricas de nome
atac_skus = [
    ("Stella Artois Lata",       "350ml"),   # nome antigo no banco
    ("Stella Artois Long Neck",  "355ml"),   # outro possível nome no banco
    ("Corona Extra Long Neck",   "355ml"),   # 355ml = versão antiga
    ("Spaten Pilsner Long Neck", "355ml"),   # 355ml = versão antiga
    ("Spaten Puro Malte Lata",   "355ml"),   # outro possível nome
    ("Brahma Chopp",             "350ml"),   # cobre "Brahma Chopp Lata"
]
for nome, emb in atac_skus:
    r = con.execute("""
        DELETE FROM precos
        WHERE supermercado='Atacadão'
          AND nome_produto LIKE ?
          AND embalagem=?
    """, (f"%{nome}%", emb))
    if r.rowcount > 0:
        print(f"Atacadão '{nome}' {emb}: {r.rowcount} registros deletados")

con.commit()

total_depois = con.execute("SELECT COUNT(*) FROM precos").fetchone()[0]
print(f"\nRegistros depois: {total_depois}")
print(f"Total removido: {total_antes - total_depois}")

# Mostra o que sobrou do Atacadão Cervejas
print("\nAtacadão Cervejas restante:")
rows = con.execute("""
    SELECT nome_produto, embalagem, COUNT(*) as n
    FROM precos
    WHERE supermercado='Atacadão' AND categoria='Cervejas'
    GROUP BY nome_produto, embalagem
    ORDER BY nome_produto
""").fetchall()
for r in rows:
    print(f"  {r[0]} {r[1]}: {r[2]} registros")

con.close()
print("\nConcluído.")
