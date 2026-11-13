"""Escreve na coluna `escolha` o "1" (o primeiro candidato da coluna P) para TODOS os serviços ainda sem decisão das ruas que você listar em --um,
e, com --resto-x, escreve "x" em todos os demais pendentes. Serve para fechar de uma vez as ruas em que o 1º candidato é claro (mesmo bairro,
bem perto, sem empate), em vez de digitar rua por rua.

Só mexe em serviços com status ambiguo / nao_encontrado / numero_fora_da_faixa, sem `feito`, com candidatos na coluna P e `escolha` VAZIA.
Os nomes de --um casam por TRECHO do nome da rua (sem acento, maiúsculas): "VATRAPUA" casa com "Rua Vatrapuã". Depois rode o aplicar_escolhas.py.

Uso:
  python scripts/escolher_por_rua.py --planilha ID --um "RICARDO LACERDA" "VATRAPUA" --resto-x            # simula
  python scripts/escolher_por_rua.py --planilha ID --um "RICARDO LACERDA" "VATRAPUA" --resto-x --gravar    # grava (backup em logs/backup_por_rua_*.csv)
  python scripts/escolher_por_rua.py --planilha ID --um "JOAO CAPISTRANO" --sobrescrever-x                  # troca x por 1 nessas ruas
"""
import argparse
import csv
import re
import unicodedata
from collections import Counter
from datetime import datetime
from pathlib import Path

VERDADEIRO = {"TRUE", "VERDADEIRO", "SIM", "1", "X"}
STATUS_ALVO = {"ambiguo", "nao_encontrado", "numero_fora_da_faixa"}


def sem_acento(s):
    return "".join(c for c in unicodedata.normalize("NFD", s or "") if unicodedata.category(c) != "Mn")


def norma_rua(s):
    t = re.sub(r"[.:,]", " ", sem_acento(s).upper())
    t = re.sub(r"\b(R|RUA|AV|AVENIDA|ESTR|ESTRADA|TV|TRAVESSA|AL|ALAMEDA)\b", " ", t)
    return " ".join(t.split())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--planilha", required=True)
    ap.add_argument("--credenciais", default="credenciais_google.json")
    ap.add_argument("--aba")
    ap.add_argument("--coluna", default="escolha")
    ap.add_argument("--um", nargs="*", default=[], help="trechos de nome de rua que recebem 1 (o 1º candidato)")
    ap.add_argument("--resto-x", action="store_true", help="todos os outros pendentes recebem x")
    ap.add_argument("--sobrescrever-x", action="store_true",
                    help="também troca um x JÁ escrito em `escolha` por 1, nas ruas de --um (para corrigir um x em lote)")
    ap.add_argument("--gravar", action="store_true")
    args = ap.parse_args()
    um = [norma_rua(x) for x in args.um if x.strip()]

    import gspread
    from gspread.utils import rowcol_to_a1

    sh = gspread.service_account(filename=args.credenciais).open_by_key(args.planilha)
    ws = None
    for w in ([sh.worksheet(args.aba)] if args.aba else sh.worksheets()):
        cab = [c.strip().lower() for c in w.row_values(1)]
        if {"id_cliente_servico", "endereco", "status_cep", "feito", "ceps_candidatos", args.coluna} <= set(cab):
            ws, cab_ws = w, cab
            break
    if ws is None:
        raise SystemExit("não achei a aba com as colunas necessárias (use --aba)")
    col = {n: i + 1 for i, n in enumerate(cab_ws)}
    todas = ws.get_all_values()

    def valor(r, nome):
        return r[col[nome] - 1] if nome in col and len(r) >= col[nome] else ""

    mudancas, backup, cont, casadas = [], [], Counter(), Counter()
    for n, r in enumerate(todas, 1):
        if n == 1:
            continue
        if valor(r, "status_cep").strip() not in STATUS_ALVO or valor(r, "feito").strip().upper() in VERDADEIRO:
            continue
        atual = valor(r, args.coluna).strip().lower()
        if not valor(r, "ceps_candidatos").strip() or (atual and not (args.sobrescrever_x and atual == "x")):
            continue
        rua = norma_rua(valor(r, "endereco"))
        achou = next((u for u in um if u in rua), None)
        if atual == "x" and not achou:
            continue  # com --sobrescrever-x só mexe nas ruas de --um
        if achou:
            novo = "1"
            casadas[achou] += 1
        elif args.resto_x:
            novo = "x"
        else:
            continue
        cont[novo] += 1
        backup.append((valor(r, "id_cliente_servico"), args.coluna, "", novo))
        mudancas.append({"range": rowcol_to_a1(n, col[args.coluna]), "values": [[novo]]})
    print(f"Recebem 1: {cont['1']} | recebem x: {cont['x']} | células: {len(mudancas)}")
    for u in um:
        print(f"   {casadas[u]:3d}  {u}")
    nao = [u for u in um if not casadas[u]]
    if nao:
        print("AVISO: estes nomes não casaram com nenhum pendente:", nao)
    if not args.gravar:
        print("Simulação: nada foi escrito. Use --gravar para aplicar.")
        return
    Path("logs").mkdir(exist_ok=True)
    arquivo = Path("logs") / f"backup_por_rua_{datetime.now():%Y%m%d_%H%M%S}.csv"
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
