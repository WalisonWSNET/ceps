"""Marca `feito` (TRUE) nos serviços que JÁ ESTÃO CORRETOS no Hub, segundo a planilha: status_cep confiável (exato, aprox_exato...) e
cep_novo IGUAL ao cep atual (o casamento por rua achou o mesmo CEP de rua que o cliente já tem). São os "verdes de status confiável".
Não mexe em cep_novo nem em status_cep; só escreve `feito` e, se estiver vazia, uma nota em obs_robo ("ja_correto_pela_planilha").

Por padrão só SIMULA. Com --gravar escreve na planilha e, antes, salva logs/backup_ja_corretos_AAAAMMDD_HHMMSS.csv.
NÃO usa o Hub: confira antes uma amostra com `atualizar_ceps_hub.py --conferir-verdes N`.

Não marca: CEP genérico (11700000/11730000/11740000/11750000), linhas já marcadas, status não resolvidos
(ambiguo, nao_encontrado, fora_do_escopo, numero_fora_da_faixa) ou cidade fora das 3.

Uso:
  python scripts/marcar_ja_corretos.py data/clientes_fim.csv --planilha ID            # simula
  python scripts/marcar_ja_corretos.py data/clientes_fim.csv --planilha ID --gravar
"""
import argparse
import csv
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import atualizar_ceps_hub as hub  # noqa: E402

GENERICOS = {"11700000", "11730000", "11740000", "11750000"}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv")
    ap.add_argument("--planilha", required=True)
    ap.add_argument("--credenciais", default="credenciais_google.json")
    ap.add_argument("--aba")
    ap.add_argument("--gravar", action="store_true")
    args = ap.parse_args()

    import gspread
    from gspread.utils import rowcol_to_a1

    linhas = hub.ler_linhas(args.csv)
    alvo = []
    for r in linhas:
        cep, novo = hub.so_digitos(r.get("cep")), hub.so_digitos(r.get("cep_novo"))
        if ((r.get("status_cep") or "").strip() in hub.STATUS_CONFIAVEIS and len(novo) == 8 and novo == cep
                and novo not in GENERICOS and (r.get("cidade") or "").strip().upper() in hub.CIDADES
                and not hub.marcado(r, "feito") and hub.so_digitos(r.get("id_cliente_servico"))):
            alvo.append(r)
    por_status = Counter((r.get("status_cep") or "").strip() for r in alvo)
    print(f"Candidatos (status confiável, cep_novo = cep, não genérico, sem feito): {len(alvo)}")
    for st, n in por_status.most_common():
        print(f"   {st}: {n}")

    sh = gspread.service_account(filename=args.credenciais).open_by_key(args.planilha)
    ws = None
    for w in ([sh.worksheet(args.aba)] if args.aba else sh.worksheets()):
        cab = [c.strip().lower() for c in w.row_values(1)]
        if {"id_cliente_servico", "feito", "status_cep"} <= set(cab):
            ws, cab_ws = w, cab
            break
    if ws is None:
        raise SystemExit("não achei uma aba com id_cliente_servico, feito e status_cep (use --aba)")
    col = {n: i + 1 for i, n in enumerate(cab_ws)}
    todas = ws.get_all_values()
    linha_de = {hub.so_digitos(r[col["id_cliente_servico"] - 1]): n for n, r in enumerate(todas, 1)
                if n > 1 and len(r) >= col["id_cliente_servico"]}

    def valor(n, nome):
        r = todas[n - 1]
        return r[col[nome] - 1] if nome in col and len(r) >= col[nome] else ""

    mudancas, backup, puladas = [], [], Counter()
    for r in alvo:
        idcs = hub.so_digitos(r["id_cliente_servico"])
        n = linha_de.get(idcs)
        if not n:
            puladas["id_nao_esta_na_planilha"] += 1
        elif valor(n, "feito").strip().upper() in hub.VERDADEIRO:
            puladas["ja_marcado_feito"] += 1
        elif valor(n, "status_cep").strip() != (r.get("status_cep") or "").strip():
            puladas["status_cep_mudou"] += 1
        else:
            celulas = {"feito": True}
            if "obs_robo" in col and not valor(n, "obs_robo").strip():
                celulas["obs_robo"] = "ja_correto_pela_planilha: o CEP de rua da planilha é o mesmo que o cliente já tinha"
            for nome, novo in celulas.items():
                backup.append((idcs, nome, valor(n, nome), novo))
                mudancas.append({"range": rowcol_to_a1(n, col[nome]), "values": [[novo]]})
    n_feito = sum(1 for _, nome, _, _ in backup if nome == "feito")
    print(f"\nA marcar como feito: {n_feito} | puladas: {dict(puladas)} | células: {len(mudancas)}")
    for idcs, nome, antigo, novo in backup[:6]:
        print(f"   ex.: serviço {idcs} {nome}: {antigo!r} -> {str(novo)[:60]!r}")
    if not args.gravar:
        print("Simulação: nada foi escrito. Use --gravar para aplicar.")
        return
    Path("logs").mkdir(exist_ok=True)
    arquivo = Path("logs") / f"backup_ja_corretos_{datetime.now():%Y%m%d_%H%M%S}.csv"
    with open(arquivo, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id_cliente_servico", "coluna", "valor_antigo", "valor_novo"])
        w.writerows(backup)
    print("Backup dos valores antigos:", arquivo)
    for i in range(0, len(mudancas), 500):
        ws.batch_update(mudancas[i:i + 500], value_input_option="USER_ENTERED")
    print("Planilha atualizada.")


if __name__ == "__main__":
    main()
