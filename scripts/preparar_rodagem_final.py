"""Separa os serviços pulados (último status no log) em listas para as rodagens finais e em arquivos de revisão.
Só LÊ o log e o CSV; escreve em logs/. Uso:
  python scripts/preparar_rodagem_final.py data/clientes_fim21.csv [--desde "2026-11-18 08:00"] [--extra 37974 32921]
Gera:
  logs/ids_preservar.txt       numero_divergente + enderecos_diferentes_numero/condominio (+ --extra)  -> --so-um-endereco --preservar-enderecos
  logs/ids_autocompletar.txt   campo_autocompletar_preenchido                                          -> idem (testar em poucos antes)
  logs/ids_repetir.txt         erro / consulta_sem_resposta                                            -> rodagem normal com --id
  logs/revisao_enderecos_diferentes.csv   _bairro/_cep/_logradouro/_cidade com os dados, para você decidir
  logs/revisao_cep_divergente.csv         cep_divergente (o CEP do Hub não é o da planilha)
"""
import argparse
import csv
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("csv")
ap.add_argument("--log", default="logs/log_ceps.csv")
ap.add_argument("--desde", default="2026-11-18 08:00")
ap.add_argument("--extra", nargs="*", default=[])
a = ap.parse_args()

corte = datetime.fromisoformat(a.desde.replace(" ", "T"))
ultimo = {}
with open(a.log, encoding="utf-8", newline="") as f:
    for r in csv.DictReader(f):
        ultimo[r["id_cliente_servico"]] = r
ultimo = {i: r for i, r in ultimo.items() if datetime.fromisoformat(r["data_hora"]) >= corte}
dados = {}
with open(a.csv, encoding="utf-8", newline="") as f:
    for r in csv.DictReader(f):
        dados[(r.get("id_cliente_servico") or "").strip()] = r
feito = lambda i: (dados.get(i, {}).get("feito") or "").upper() == "TRUE"

def ids_de(pred):
    return [i for i, r in ultimo.items() if pred(r["status"]) and not feito(i)]

preservar = ids_de(lambda s: s in ("numero_divergente", "enderecos_diferentes_numero", "enderecos_diferentes_condominio"))
for e in a.extra:
    if e not in preservar and not feito(e):
        preservar.append(e)
auto = ids_de(lambda s: s == "campo_autocompletar_preenchido")
repetir = ids_de(lambda s: s in ("erro", "consulta_sem_resposta"))
Path("logs").mkdir(exist_ok=True)
for nome, ids in (("ids_preservar", preservar), ("ids_autocompletar", auto), ("ids_repetir", repetir)):
    Path(f"logs/{nome}.txt").write_text("\n".join(ids) + ("\n" if ids else ""), encoding="utf-8")
    print(f"logs/{nome}.txt: {len(ids)} serviços")

def detalhe(r):
    try:
        d = json.loads(r["detalhes"])
    except ValueError:
        return r["detalhes"][:150]
    return json.dumps(d.get("info") or d.get("erro") or d, ensure_ascii=False)[:200]

campos = ["id_cliente_servico", "codigo_cliente", "status", "endereco", "numero", "bairro", "cidade", "cep_antigo", "cep_novo", "detalhe", "decisao"]
for nome, pred in (("revisao_enderecos_diferentes", lambda s: s.startswith("enderecos_diferentes_") and s not in ("enderecos_diferentes_numero", "enderecos_diferentes_condominio")),
                   ("revisao_cep_divergente", lambda s: s == "cep_divergente")):
    ids = sorted(ids_de(pred), key=lambda i: ultimo[i]["status"])
    with open(f"logs/{nome}.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(campos)
        for i in ids:
            r, d = ultimo[i], dados.get(i, {})
            w.writerow([i, r["codigo_cliente"], r["status"], d.get("endereco"), d.get("numero"), d.get("bairro"), d.get("cidade"),
                        r["cep_antigo"], r["cep_novo"], detalhe(r), ""])
    print(f"logs/{nome}.csv: {len(ids)} serviços")
print("\nOutros status (ficam como estão):", dict(Counter(r["status"] for i, r in ultimo.items()
      if not feito(i) and r["status"] not in ("ok", "ja_atualizado"))))
