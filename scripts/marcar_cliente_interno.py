"""Marca como feito (+ nota em obs_robo) TODOS os serviços de um cliente interno (ex.: cadastro da própria empresa) para o robô ignorar.
  python scripts/marcar_cliente_interno.py --planilha ID --cliente 158            # simula
  python scripts/marcar_cliente_interno.py --planilha ID --cliente 158 --gravar
"""
import argparse
import gspread
from gspread.utils import rowcol_to_a1

ap = argparse.ArgumentParser()
ap.add_argument("--planilha", required=True)
ap.add_argument("--cliente", required=True)
ap.add_argument("--credenciais", default="credenciais_google.json")
ap.add_argument("--aba")
ap.add_argument("--gravar", action="store_true")
a = ap.parse_args()
sh = gspread.service_account(filename=a.credenciais).open_by_key(a.planilha)
ws = None
for w in ([sh.worksheet(a.aba)] if a.aba else sh.worksheets()):
    cab = [c.strip().lower() for c in w.row_values(1)]
    if {"codigo_cliente", "id_cliente_servico", "feito"} <= set(cab):
        ws, col = w, {n: i for i, n in enumerate(cab)}
        break
if ws is None:
    raise SystemExit("aba não encontrada (use --aba)")
mud, n = [], 0
for i, r in enumerate(ws.get_all_values(), 1):
    if i == 1 or len(r) <= col["codigo_cliente"] or r[col["codigo_cliente"]].strip() != a.cliente:
        continue
    feito = r[col["feito"]].strip().upper() if len(r) > col["feito"] else ""
    if feito in {"TRUE", "VERDADEIRO", "SIM", "1"}:
        continue
    n += 1
    mud.append({"range": rowcol_to_a1(i, col["feito"] + 1), "values": [[True]]})
    if "obs_robo" in col:
        mud.append({"range": rowcol_to_a1(i, col["obs_robo"] + 1), "values": [["cadastro interno WSNET: ignorado"]]})
print(f"Serviços do cliente {a.cliente} a marcar: {n}")
if a.gravar and mud:
    for k in range(0, len(mud), 500):
        ws.batch_update(mud[k:k + 500], value_input_option="USER_ENTERED")
    print("Planilha atualizada.")
elif not a.gravar:
    print("Simulação: use --gravar.")
