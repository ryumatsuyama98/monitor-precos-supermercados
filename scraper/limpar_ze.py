"""
Remove todos os registros do Zé Delivery do banco de dados.
Uso: python scraper/limpar_ze.py
"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data/precos.db"

con = sqlite3.connect(DB_PATH)
cur = con.cursor()

cur.execute("SELECT COUNT(*) FROM precos WHERE supermercado = 'Zé Delivery'")
total = cur.fetchone()[0]
print(f"Registros Zé Delivery encontrados: {total}")

if total > 0:
    cur.execute("DELETE FROM precos WHERE supermercado = 'Zé Delivery'")
    con.commit()
    print(f"✓ {total} registros removidos")
else:
    print("Nenhum registro para remover")

con.close()
