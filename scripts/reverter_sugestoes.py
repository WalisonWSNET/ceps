"""Desfaz (parcial ou total) o que `aplicar_sugestoes.py --gravar` escreveu, usando os arquivos de backup logs/backup_sugestoes_*.csv.

Principal uso: achar as sugestões aplicadas em linhas em que o cep_novo ANTERIOR já era um CEP de rua (não genérico), isto é, em que o Hub
já tinha CEP de rua e a sugestão o trocaria por um palpite. Por padrão só LISTA e SIMULA; com --gravar restaura a planilha.

Restaura, por serviço: cep_novo, status_cep, cep_novo_anterior e obs_robo para os valores do backup. Só mexe se a célula ainda tem o
valor que a sugestão escreveu (se alguém mudou depois, a linha é pulada) e se o serviço ainda não está marcado em `feito`.

Uso:
  python scripts/reverter_sugestoes.py --so-cep-de-rua                       # lista as que trocaram um CEP de rua
  python scripts/reverter_sugestoes.py --so-cep-de-rua --planilha ID         # simula a restauração
  python scripts/reverter_sugestoes.py --so-cep-de-rua --planilha ID --gravar
  python scripts/reverter_sugestoes.py --ids 123 456 --planilha ID --gravar  # restaura serviços específicos
"""
import argparse
import csv
import glob
from collections import defaultdict
from datetime import datetime
from pathlib import Path

GENERICOS = {"11700000", "11730000", "11740000", "11750000"}
VERDADEIRO = {"TRUE", "VERDADEIRO", "SIM", "1", "X"}


def so_digitos(s):
    return "".join(c for c in (s or "") if c.isdigit())


def carregar_backups(padrao):
    """id -> {coluna: (valor_antigo, valor_novo)} (o primeiro backup de cada serviço vale: é o estado antes da 1ª aplicação)."""
    por_id = {}
    for arq in sorted(glob.glob(padrao)):
        with open(arq, encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                d = por_id.setdefault(r["id_cliente_servico"], {})
                d.setdefault(r["coluna"], (r["valor_antigo"], r["valor_novo"], arq))
    return por_id


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backups", default="logs/backup_sugestoes_*.csv")
    ap.add_argument("--so-cep-de-rua", action="store_true", help="só os serviços cujo cep_novo anterior NÃO era genérico")
    ap.add_argument("--ids", nargs="+")
    ap.add_argument("--planilha")
    ap.add_argument("--credenciais", default="credenciais_google.json")
    ap.add_argument("--aba")
    ap.add_argument("--gravar", action="store_true")
    args = ap.parse_args()

    por_id = carregar_backups(args.backups)
    ids = set(map(so_digitos, args.ids)) if args.ids else None
    alvo = {}
    for idcs, cols in por_id.items():
        if "cep_novo" not in cols:
            continue
        antigo = so_digitos(cols["cep_novo"][0])
        if ids is not None and so_digitos(idcs) not in ids:
            continue
        if args.so_cep_de_rua and antigo in GENERICOS:
            continue
        alvo[idcs] = cols
    print(f"Serviços nos backups: {len(por_id)} | a restaurar: {len(alvo)}")
    for idcs, cols in list(alvo.items())[:25]:
        print(f"   {idcs}: cep_novo {cols['cep_novo'][0]!r} (antes) -> {cols['cep_novo'][1]!r} (sugestão)")
    if not args.planilha:
        print("\nPara simular a restauração na planilha, informe --planilha ID.")
        return

    import gspread
    from gspread.utils import rowcol_to_a1

    sh = gspread.service_account(filename=args.credenciais).open_by_key(args.planilha)
    ws = None
    for w in ([sh.worksheet(args.aba)] if args.aba else sh.worksheets()):
        cab = [c.strip().lower() for c in w.row_values(1)]
        if {"id_cliente_servico", "cep_novo", "status_cep"} <= set(cab):
            ws, cab_ws = w, cab
            break
    if ws is None:
        raise SystemExit("não achei uma aba com id_cliente_servico, cep_novo e status_cep (use --aba)")
    col = {n: i + 1 for i, n in enumerate(cab_ws)}
    todas = ws.get_all_values()
    linha_de = {so_digitos(r[col["id_cliente_servico"] - 1]): n for n, r in enumerate(todas, 1)
                if n > 1 and len(r) >= col["id_cliente_servico"]}

    def valor(n, nome):
        r = todas[n - 1]
        return r[col[nome] - 1] if nome in col and len(r) >= col[nome] else ""

    mudancas, backup, puladas, ok = [], [], defaultdict(int), 0
    for idcs, cols in alvo.items():
        n = linha_de.get(so_digitos(idcs))
        if not n:
            puladas["id_nao_esta_na_planilha"] += 1
            continue
        if valor(n, "feito").strip().upper() in VERDADEIRO:
            puladas["ja_marcado_feito"] += 1
            continue
        if so_digitos(valor(n, "cep_novo")) != so_digitos(cols["cep_novo"][1]):
            puladas["cep_novo_mudou_depois"] += 1
            continue
        ok += 1
        for nome, (antigo, novo, _) in cols.items():
            if nome in col:
                backup.append((idcs, nome, valor(n, nome), antigo))
                mudancas.append({"range": rowcol_to_a1(n, col[nome]), "values": [[antigo]]})
    print(f"\nA restaurar na planilha: {ok} | puladas: {dict(puladas)} | células: {len(mudancas)}")
    if not args.gravar:
        print("Simulação: nada foi escrito. Use --gravar para restaurar.")
        return
    Path("logs").mkdir(exist_ok=True)
    arquivo = Path("logs") / f"backup_reversao_{datetime.now():%Y%m%d_%H%M%S}.csv"
    with open(arquivo, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id_cliente_servico", "coluna", "valor_antes_da_reversao", "valor_restaurado"])
        w.writerows(backup)
    print("Backup antes da reversão:", arquivo)
    for i in range(0, len(mudancas), 500):
        ws.batch_update(mudancas[i:i + 500], value_input_option="USER_ENTERED")
    print("Planilha restaurada.")


if __name__ == "__main__":
    main()
