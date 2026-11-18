"""Lista os PARES 'bairro do Hub -> bairro do CEP novo' dos bairro_longe que ainda estão pendentes (depois do lote) e gera o arquivo de
IDs dos pares que você aceitar. Usa logs/bairro_longe_analise.csv (rode antes analisar_bairro_longe.py com o CSV mais novo).

  python scripts/pares_bairro_longe.py                      # lista os pares (n = quantos serviços)
  python scripts/pares_bairro_longe.py --aceitar 1 3 5      # grava logs/ids_pares.txt com os pares de número 1, 3 e 5 da lista
Depois: python scripts/atualizar_ceps_hub.py data/clientes_NOVO.csv --ids-arquivo logs/ids_pares.txt --aceitar-bairro --todos --gravar ...
"""
import argparse
import csv
from collections import defaultdict
from pathlib import Path

LIBERADAS = {"mesmo_bairro_outro_nome", "confirmado_vizinhos", "provavel"}  # já tratadas no lote

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("--analise", default="logs/bairro_longe_analise.csv")
ap.add_argument("--aceitar", nargs="+", type=int, metavar="N", help="números dos pares (da lista) a liberar")
ap.add_argument("--saida", default="logs/ids_pares.txt")
ap.add_argument("--top", type=int, default=30)
a = ap.parse_args()

pares = defaultdict(list)
with open(a.analise, encoding="utf-8", newline="") as f:
    for r in csv.DictReader(f):
        if r["classe"] in LIBERADAS:
            continue
        pares[(r["bairro_hub"].strip().upper(), r["bairro_do_cep_novo"].strip().upper())].append(r)
ordem = sorted(pares.items(), key=lambda kv: -len(kv[1]))
print(f"Serviços ainda pendentes por bairro_longe: {sum(len(v) for v in pares.values())} em {len(pares)} pares\n")
print(" nº |   n | km médio | bairro do Hub  ->  bairro do CEP novo")
for k, ((hub, cep), lista) in enumerate(ordem[:a.top], 1):
    kms = [float(x["km_entre_bairros"]) for x in lista if x.get("km_entre_bairros") not in (None, "", "None")]
    km = f"{sum(kms) / len(kms):6.1f}" if kms else "     ?"
    print(f"{k:3} | {len(lista):3} | {km}   | {hub}  ->  {cep}")
if a.aceitar:
    ids = []
    for n in a.aceitar:
        if not 1 <= n <= len(ordem):
            raise SystemExit(f"par {n} não existe")
        ids += [x["id_cliente_servico"] for x in ordem[n - 1][1]]
    Path(a.saida).write_text("\n".join(ids) + "\n", encoding="utf-8")
    print(f"\n{len(ids)} serviços dos pares {a.aceitar} gravados em {a.saida}")
