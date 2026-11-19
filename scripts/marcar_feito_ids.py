"""Marca feito = TRUE (+ nota em obs_robo) nos id_cliente_servico de um arquivo (um por linha). Para serviços que já estão certos no Hub.
  python scripts/marcar_feito_ids.py --planilha ID --ids-arquivo logs/ids_decisao_F.txt --nota "endereço já atualizado no Hub"           # simula
  python scripts/marcar_feito_ids.py --planilha ID --ids-arquivo logs/ids_decisao_F.txt --nota "..." --gravar
"""
import argparse
import csv
from datetime import datetime
from pathlib import Path

import gspread
from gspread.utils import rowcol_to_a1

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("--planilha", required=True)
ap.add_argument("--ids-arquivo", required=True)
ap.add_argument("--nota", required=True)
ap.add_argument("--credenciais", default="credenciais_google.json")
ap.add_argument("--aba")
ap.add_argument("--gravar", action="store_true")
a = ap.parse_args()
ids = {l.strip() for l in Path(a.ids_arquivo).read_text(encoding="utf-8").splitlines() if l.strip()}
sh = gspread.service_account(filename=a.credenciais).open_by_key(a.planilha)
ws = None
for w in ([sh.worksheet(a.aba)] if a.aba else sh.worksheets()):
    cab = [c.strip().lower() for c in w.row_values(1)]
    if {"id_cliente_servico", "feito"} <= set(cab):
        ws, col = w, {n: i for i, n in enumerate(cab)}
        break
if ws is None:
    raise SystemExit("aba não encontrada (use --aba)")
mud, achados, backup = [], set(), []
for i, r in enumerate(ws.get_all_values(), 1):
    if i == 1 or len(r) <= col["id_cliente_servico"]:
        continue
    idcs = "".join(c for c in r[col["id_cliente_servico"]] if c.isdigit())
    if idcs not in ids:
        continue
    achados.add(idcs)
    feito = (r[col["feito"]] if len(r) > col["feito"] else "").strip().upper()
    if feito in {"TRUE", "VERDADEIRO", "SIM", "1"}:
        continue
    mud.append({"range": rowcol_to_a1(i, col["feito"] + 1), "values": [[True]]})
    backup.append((idcs, "feito", feito, "TRUE"))
    if "obs_robo" in col:
        antigo = (r[col["obs_robo"]] if len(r) > col["obs_robo"] else "").strip()
        mud.append({"range": rowcol_to_a1(i, col["obs_robo"] + 1), "values": [[(antigo + " | " + a.nota if antigo else a.nota)[:300]]]})
        backup.append((idcs, "obs_robo", antigo, a.nota))
print(f"IDs no arquivo: {len(ids)} | achados na planilha: {len(achados)} | a marcar: {len(mud) // (2 if 'obs_robo' in col else 1)} | não achados: {len(ids - achados)}")
if not a.gravar:
    print("Simulação: nada foi escrito. Use --gravar.")
elif mud:
    Path("logs").mkdir(exist_ok=True)
    arq = Path("logs") / f"backup_marcar_feito_{datetime.now():%Y%m%d_%H%M%S}.csv"
    with open(arq, "w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerows([["id_cliente_servico", "coluna", "valor_antigo", "valor_novo"]] + backup)
    for k in range(0, len(mud), 500):
        ws.batch_update(mud[k:k + 500], value_input_option="USER_ENTERED")
    print("Planilha atualizada. Backup:", arq)
