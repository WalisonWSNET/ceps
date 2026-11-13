"""Escreve `x` na coluna `escolha` dos serviços (ambiguo / nao_encontrado / numero_fora_da_faixa, sem `feito`, com candidatos na coluna P)
em que TODOS os candidatos estão longe do bairro do cliente (padrão: mais de 1,5 km) ou sem distância conhecida. Nesses casos a rua certa
provavelmente não está na lista (os candidatos são ruas de mesmo nome em outros bairros) e a resposta seria `x` de qualquer jeito.

Só preenche `escolha` onde ela está VAZIA; não mexe em cep_novo, status_cep nem feito. O `x` é ignorado pelo aplicar_escolhas.py.
Depois, para ver só o que falta decidir, filtre a coluna `escolha` por "está vazio".

Uso:
  python scripts/marcar_x_candidatos_longe.py --planilha ID                    # simula (padrão: limite 1.5 km)
  python scripts/marcar_x_candidatos_longe.py --planilha ID --limite 1.0       # mais agressivo (marca mais linhas com x)
  python scripts/marcar_x_candidatos_longe.py --planilha ID --gravar           # grava (backup em logs/backup_x_longe_*.csv)
"""
import argparse
import csv
import re
from datetime import datetime
from pathlib import Path

VERDADEIRO = {"TRUE", "VERDADEIRO", "SIM", "1", "X"}
STATUS_ALVO = {"ambiguo", "nao_encontrado", "numero_fora_da_faixa"}


def distancias(texto):
    return [float(m) for m in re.findall(r"(\d+(?:\.\d+)?)\s*km", texto or "")]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--planilha", required=True)
    ap.add_argument("--credenciais", default="credenciais_google.json")
    ap.add_argument("--aba")
    ap.add_argument("--coluna", default="escolha")
    ap.add_argument("--limite", type=float, default=1.5, help="km: se o candidato MAIS PERTO está a mais que isso, marca x (padrão 1.5)")
    ap.add_argument("--gravar", action="store_true")
    args = ap.parse_args()

    import gspread
    from gspread.utils import rowcol_to_a1

    sh = gspread.service_account(filename=args.credenciais).open_by_key(args.planilha)
    ws = None
    for w in ([sh.worksheet(args.aba)] if args.aba else sh.worksheets()):
        cab = [c.strip().lower() for c in w.row_values(1)]
        if {"id_cliente_servico", "status_cep", "feito", "ceps_candidatos", args.coluna} <= set(cab):
            ws, cab_ws = w, cab
            break
    if ws is None:
        raise SystemExit(f"não achei a aba com id_cliente_servico, status_cep, feito, ceps_candidatos e {args.coluna}")
    col = {n: i + 1 for i, n in enumerate(cab_ws)}
    todas = ws.get_all_values()

    def valor(r, nome):
        return r[col[nome] - 1] if nome in col and len(r) >= col[nome] else ""

    mudancas, backup, ficam = [], [], 0
    for n, r in enumerate(todas, 1):
        if n == 1:
            continue
        if valor(r, "status_cep").strip() not in STATUS_ALVO or valor(r, "feito").strip().upper() in VERDADEIRO:
            continue
        p = valor(r, "ceps_candidatos").strip()
        if not p or valor(r, args.coluna).strip():
            continue
        d = distancias(p)
        if d and min(d) <= args.limite:
            ficam += 1
            continue
        backup.append((valor(r, "id_cliente_servico"), args.coluna, "", "x"))
        mudancas.append({"range": rowcol_to_a1(n, col[args.coluna]), "values": [["x"]]})
    print(f"Linhas que recebem x (candidato mais perto a mais de {args.limite} km ou sem distância): {len(mudancas)} | ficam para você: {ficam}")
    if not args.gravar:
        print("Simulação: nada foi escrito. Use --gravar para aplicar.")
        return
    Path("logs").mkdir(exist_ok=True)
    arquivo = Path("logs") / f"backup_x_longe_{datetime.now():%Y%m%d_%H%M%S}.csv"
    with open(arquivo, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id_cliente_servico", "coluna", "valor_antigo", "valor_novo"])
        w.writerows(backup)
    print("Backup:", arquivo)
    for i in range(0, len(mudancas), 500):
        ws.batch_update(mudancas[i:i + 500], value_input_option="RAW")
    print("Planilha atualizada.")


if __name__ == "__main__":
    main()
