"""Lista (e, com --gravar, anota em obs_robo) os serviços NÃO RESOLVIDOS (nao_encontrado, ambiguo, numero_fora_da_faixa) em que o Hub
JÁ TEM um CEP de rua (o `cep` atual não é um dos genéricos 11700000/11730000/11740000/11750000). Para esses não há o que sugerir e o
robô não mexe; a nota serve para você filtrar na planilha.

Sempre grava a lista em logs/cep_de_rua_existente.csv. Por padrão NÃO escreve na planilha (só --gravar), e só preenche obs_robo se
estiver vazia (backup em logs/backup_cep_de_rua_AAAAMMDD_HHMMSS.csv).

Uso:
  python scripts/marcar_cep_de_rua_existente.py data/clientes_fim4.csv                      # só lista
  python scripts/marcar_cep_de_rua_existente.py data/clientes_fim4.csv --planilha ID        # simula a anotação
  python scripts/marcar_cep_de_rua_existente.py data/clientes_fim4.csv --planilha ID --gravar
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
NOTA = "hub_ja_tem_cep_de_rua: nao_resolvido pela lista, mas o cep atual nao e generico"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv")
    ap.add_argument("--planilha")
    ap.add_argument("--credenciais", default="credenciais_google.json")
    ap.add_argument("--aba")
    ap.add_argument("--gravar", action="store_true")
    args = ap.parse_args()

    alvo = []
    for r in hub.ler_linhas(args.csv):
        cep = hub.so_digitos(r.get("cep"))
        if ((r.get("status_cep") or "").strip() in ("nao_encontrado", "ambiguo", "numero_fora_da_faixa") and len(cep) == 8
                and cep not in GENERICOS and (r.get("cidade") or "").strip().upper() in hub.CIDADES
                and not hub.marcado(r, "feito")):
            alvo.append(r)
    Path("logs").mkdir(exist_ok=True)
    campos = ["id_cliente_servico", "codigo_cliente", "endereco", "numero", "bairro", "cidade", "cep", "cep_novo", "status_cep"]
    with open("logs/cep_de_rua_existente.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, campos, extrasaction="ignore")
        w.writeheader()
        w.writerows(alvo)
    por_status = Counter((r.get("status_cep") or "").strip() for r in alvo)
    print(f"Não resolvidos em que o Hub já tem CEP de rua: {len(alvo)} -> logs/cep_de_rua_existente.csv")
    for st, n in por_status.most_common():
        print(f"   {st}: {n}")
    if not args.planilha:
        print("\nPara anotar em obs_robo (e poder filtrar na planilha), informe --planilha ID.")
        return

    import gspread
    from gspread.utils import rowcol_to_a1

    sh = gspread.service_account(filename=args.credenciais).open_by_key(args.planilha)
    ws = None
    for w in ([sh.worksheet(args.aba)] if args.aba else sh.worksheets()):
        cab = [c.strip().lower() for c in w.row_values(1)]
        if {"id_cliente_servico", "obs_robo", "status_cep"} <= set(cab):
            ws, cab_ws = w, cab
            break
    if ws is None:
        raise SystemExit("não achei uma aba com id_cliente_servico, obs_robo e status_cep (use --aba)")
    col = {n: i + 1 for i, n in enumerate(cab_ws)}
    todas = ws.get_all_values()
    linha_de = {hub.so_digitos(r[col["id_cliente_servico"] - 1]): n for n, r in enumerate(todas, 1)
                if n > 1 and len(r) >= col["id_cliente_servico"]}
    mudancas, backup, puladas = [], [], Counter()
    for r in alvo:
        n = linha_de.get(hub.so_digitos(r["id_cliente_servico"]))
        atual = (todas[n - 1][col["obs_robo"] - 1] if n and len(todas[n - 1]) >= col["obs_robo"] else "")
        if not n:
            puladas["id_nao_esta_na_planilha"] += 1
        elif atual.strip():
            puladas["obs_robo_ja_preenchida"] += 1
        else:
            backup.append((r["id_cliente_servico"], "obs_robo", atual, NOTA))
            mudancas.append({"range": rowcol_to_a1(n, col["obs_robo"]), "values": [[NOTA]]})
    print(f"\nA anotar em obs_robo: {len(mudancas)} | puladas: {dict(puladas)}")
    if not args.gravar:
        print("Simulação: nada foi escrito. Use --gravar para anotar.")
        return
    arquivo = Path("logs") / f"backup_cep_de_rua_{datetime.now():%Y%m%d_%H%M%S}.csv"
    with open(arquivo, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id_cliente_servico", "coluna", "valor_antigo", "valor_novo"])
        w.writerows(backup)
    for i in range(0, len(mudancas), 500):
        ws.batch_update(mudancas[i:i + 500], value_input_option="USER_ENTERED")
    print("Backup:", arquivo, "| Planilha atualizada.")


if __name__ == "__main__":
    main()
