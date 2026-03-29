"""
Limpeza do banco — executar UMA VEZ após subir o scraper v4.
Remove todos os registros de categorias/SKUs que não existem mais:
- Todo o histórico do Carrefour Mercado
- Categorias antigas: Embutidos, Biscoitos, Massas, Mercearia (nomes antigos)
- SKUs do Atacadão que foram removidos
- Registros com preços absurdos (pack capturado por engano)
Mantém: Cervejas (todos), e os novos SKUs de Carnes/Biscoitos/Massas/Mercearia
"""
import sqlite3
from pathlib import Path

_ROOT   = Path(__file__).resolve().parent.parent
DB_PATH = _ROOT / "data/precos.db"

if not DB_PATH.exists():
    print(f"Banco não encontrado: {DB_PATH}"); exit(1)

con = sqlite3.connect(DB_PATH)
total_antes = con.execute("SELECT COUNT(*) FROM precos").fetchone()[0]
print(f"Registros antes: {total_antes}")

# 1. Remove Carrefour
r = con.execute("DELETE FROM precos WHERE supermercado='Carrefour Mercado'")
print(f"✓ Carrefour: {r.rowcount} removidos")

# 2. Remove Mateus
r = con.execute("DELETE FROM precos WHERE supermercado='Mateus'")
print(f"✓ Mateus: {r.rowcount} removidos")

# 3. Remove SKUs antigos que não existem mais no scraper
# Categorias antigas com nomes no formato "Marca Produto" que foram substituídas
skus_antigos = [
    # Embutidos antigos
    "Sadia Salsicha Hot Dog", "Perdigão Salsicha Hot Dog", "Seara Salsicha Hot Dog",
    "Sadia Mortadela fatiada", "Perdigão Mortadela fatiada",
    "Sadia Presunto fatiado", "Perdigão Presunto fatiado",
    "Sadia Linguiça toscana", "Perdigão Linguiça toscana", "Seara Linguiça toscana",
    "Sadia Nuggets de frango", "Perdigão Nuggets de frango", "Seara Nuggets de frango",
    "Sadia Lasanha bolonhesa", "Perdigão Lasanha bolonhesa", "Seara Lasanha bolonhesa",
    # Biscoitos antigos
    "Nabisco Biscoito Oreo", "Bauducco Biscoito Wafer", "Nestlé Biscoito",
    "Lacta Biscoito", "Tostines Biscoito", "Marilan Biscoito",
    "Piraquê Biscoito", "Vitarella Biscoito", "Adria Biscoito",
    "Fortaleza Biscoito", "Richester Biscoito",
    # Massas antigas
    "Barilla Macarrão", "Renata Macarrão", "Nissin Macarrão",
    "Nissin Miojo galinha", "Nissin Miojo carne", "Maggi Macarrão",
    "Adria Macarrão", "Vitarella Macarrão", "Fortaleza Macarrão", "Isabela Macarrão",
    # Mercearia antiga
    "Tio João Arroz branco", "Camil Arroz branco", "Camil Feijão carioca",
    "Kicaldo Feijão carioca", "Camil Feijão preto",
    "União Açúcar cristal", "União Açúcar refinado",
    "Anaconda Farinha", "Renata Farinha", "Dona Benta Farinha",
    "Pilão Café torrado", "3 Corações Café torrado", "Melitta Café torrado",
    "Café do Ponto Café", "Caboclo Café torrado",
]
total_antigos = 0
for sku in skus_antigos:
    r = con.execute("DELETE FROM precos WHERE nome_produto LIKE ?", (f"{sku}%",))
    if r.rowcount > 0:
        print(f"  SKU antigo '{sku}%': {r.rowcount} removidos")
        total_antigos += r.rowcount
print(f"✓ SKUs antigos: {total_antigos} removidos")

# 4. Remove preços absurdos (pack capturado por engano)
preco_max = {"Cervejas":20, "Carnes":200, "Biscoitos":25, "Massas":30, "Mercearia":60}
total_absurdos = 0
for cat, max_p in preco_max.items():
    r = con.execute("DELETE FROM precos WHERE categoria=? AND preco_atual>?", (cat, max_p))
    if r.rowcount > 0:
        print(f"  Preço absurdo {cat} >R${max_p}: {r.rowcount} removidos")
        total_absurdos += r.rowcount
print(f"✓ Preços absurdos: {total_absurdos} removidos")

con.commit()
total_depois = con.execute("SELECT COUNT(*) FROM precos").fetchone()[0]
print(f"\nRegistros depois: {total_depois}")
print(f"Total removido: {total_antes - total_depois}")

# Resumo do que sobrou
print("\n=== Histórico restante por categoria ===")
rows = con.execute("""
    SELECT coalesce(categoria,'NULL'), coalesce(supermercado,'NULL'),
           COUNT(*) as n, MIN(data_coleta), MAX(data_coleta)
    FROM precos GROUP BY categoria, supermercado ORDER BY categoria, supermercado
""").fetchall()
for r in rows:
    print(f"  {str(r[0]):15} | {str(r[1]):20} | {r[2]:4} registros | {r[3]} → {r[4]}")

con.close()
print("\nConcluído.")
