"""Por que as linhas sem `feito` estão pendentes? Cruza status_cep (planilha) com o ÚLTIMO status do robô no log.
Considera: feito vazio/FALSE, status_cep diferente de fora_do_escopo e `escolha` vazia (o mesmo filtro da planilha).
  python scripts/diagnostico_pendentes.py data/clientes_fim21.csv
Grava logs/pendentes_diagnostico.csv (uma linha por serviço) para filtrar no Excel.
"""
import csv
import sys
from collections import Counter, defaultdict

arq = sys.argv[1]
ultimo = {}
with open("logs/log_ceps.csv", encoding="utf-8", newline="") as f:
    for r in csv.DictReader(f):
        ultimo[r["id_cliente_servico"]] = r["status"]
CONF = {"exato", "exato_por_bairro", "exato_por_numero", "exato_por_faixa", "exato_por_coordenada", "aprox_exato", "exato_por_geografia", "conferido_manual"}
linhas = []
with open(arq, encoding="utf-8", newline="") as f:
    for r in csv.DictReader(f):
        if (r.get("feito") or "").strip().upper() == "TRUE" or (r.get("status_cep") or "") == "fora_do_escopo" or (r.get("escolha") or "").strip():
            continue
        st = r.get("status_cep") or ""
        grupo = "confiavel" if st in CONF or st.startswith("aprox_exato") else ("sem_cep_novo" if not (r.get("cep_novo") or "").strip() else "nao_confiavel:" + st)
        linhas.append((r, grupo, ultimo.get(r["id_cliente_servico"].strip(), "nunca_tentado")))
print(f"Linhas pendentes (mesmo filtro da planilha): {len(linhas)}\n")
tab = defaultdict(Counter)
for r, g, u in linhas:
    tab[g][u] += 1
for g in sorted(tab, key=lambda x: -sum(tab[x].values())):
    print(f"{g}: {sum(tab[g].values())}")
    for u, n in tab[g].most_common():
        print(f"     {n:5}  {u}")
with open("logs/pendentes_diagnostico.csv", "w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f, delimiter=";")
    w.writerow(["id_cliente_servico", "codigo_cliente", "grupo", "ultimo_status_robo", "status_cep", "endereco", "numero", "bairro", "cidade", "cep", "cep_novo", "obs_robo", "ceps_candidatos"])
    for r, g, u in linhas:
        w.writerow([r.get("id_cliente_servico"), r.get("codigo_cliente"), g, u, r.get("status_cep"), r.get("endereco"), r.get("numero"), r.get("bairro"), r.get("cidade"), r.get("cep"), r.get("cep_novo"), (r.get("obs_robo") or "")[:120], (r.get("ceps_candidatos") or "")[:200]])
print("\nLista: logs/pendentes_diagnostico.csv")
