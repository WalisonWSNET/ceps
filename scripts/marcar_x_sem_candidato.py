"""Marca `x` na coluna `escolha` dos serviços JÁ DECIDIDOS como "fica com o CEP genérico": sem `feito`, sem `escolha`, status_cep
ambiguo / nao_encontrado / numero_fora_da_faixa e SEM ceps_candidatos (não há o que escolher).
  python scripts/marcar_x_sem_candidato.py --planilha ID            # só simula
  python scripts/marcar_x_sem_candidato.py --planilha ID --gravar   # escreve (backup em logs/backup_x_sem_candidato_*.csv)
"""
import argparse
import csv
from datetime import datetime
from pathlib import Path

import gspread
from gspread.utils import rowcol_to_a1

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("--planilha", required=True)
ap.add_argument("--credenciais", default="credenciais_google.json")
ap.add_argument("--aba")
ap.add_argument("--gravar", action="store_true")
a = ap.parse_args()
STATUS = {"ambiguo", "nao_encontrado", "numero_fora_da_faixa"}
sh = gspread.service_account(filename=a.credenciais).open_by_key(a.planilha)
ws = None
for w in ([sh.worksheet(a.aba)] if a.aba else sh.worksheets()):
    cab = [c.strip().lower() for c in w.row_values(1)]
    if {"id_cliente_servico", "status_cep", "feito", "escolha", "ceps_candidatos"} <= set(cab):
        ws, col = w, {n: i for i, n in enumerate(cab)}
        break
if ws is None:
    raise SystemExit("aba não encontrada (precisa de id_cliente_servico, status_cep, feito, escolha, ceps_candidatos)")
mud, ids, v = [], [], lambda r, n: (r[col[n]] if len(r) > col[n] else "").strip()
for i, r in enumerate(ws.get_all_values(), 1):
    if i == 1 or v(r, "feito").upper() in {"TRUE", "VERDADEIRO", "SIM", "1"} or v(r, "escolha") or v(r, "status_cep") not in STATUS or v(r, "ceps_candidatos"):
        continue
    ids.append(v(r, "id_cliente_servico"))
    mud.append({"range": rowcol_to_a1(i, col["escolha"] + 1), "values": [["x"]]})
print(f"Serviços a marcar com x (sem candidato, genérico mantido): {len(mud)}")
if not a.gravar:
    print("Simulação: nada foi escrito. Use --gravar.")
elif mud:
    Path("logs").mkdir(exist_ok=True)
    arq = Path("logs") / f"backup_x_sem_candidato_{datetime.now():%Y%m%d_%H%M%S}.csv"
    with open(arq, "w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerows([["id_cliente_servico", "escolha_antiga", "escolha_nova"]] + [[i, "", "x"] for i in ids])
    for k in range(0, len(mud), 500):
        ws.batch_update(mud[k:k + 500], value_input_option="USER_ENTERED")
    print("Planilha atualizada. Backup:", arq)
