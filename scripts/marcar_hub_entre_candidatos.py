"""Marca `feito` (com nota em obs_robo) os serviços NÃO RESOLVIDOS (ambiguo / nao_encontrado / numero_fora_da_faixa) em que o Hub JÁ TEM um
CEP de rua e esse CEP é um dos CEPs candidatos da própria rua (o que a lista dos Correios aponta para ela). Ou seja: a dúvida era "qual dos
CEPs da rua?", e alguém já escolheu um deles no Hub. Não há o que trocar, então o serviço sai da sua lista de pendentes.

Não mexe em cep_novo nem em status_cep. Não marca quando o CEP do Hub NÃO está entre os candidatos (esses ficam para você olhar).

Passo a passo (na pasta ceps, com o .venv ativo):
  1. python scripts/sugerir_ceps.py data/clientes_fim8.csv --incluir-cep-de-rua --saida logs/sug_com_cep
  2. python scripts/marcar_hub_entre_candidatos.py data/clientes_fim8.csv                      # só conta, não usa a planilha
  3. python scripts/marcar_hub_entre_candidatos.py data/clientes_fim8.csv --planilha ID        # simula na planilha
  4. python scripts/marcar_hub_entre_candidatos.py data/clientes_fim8.csv --planilha ID --gravar   (backup em logs/backup_hub_candidatos_*.csv)
"""
import argparse
import csv
from collections import Counter
from datetime import datetime
from pathlib import Path

GENERICOS = {"11700000", "11730000", "11740000", "11750000"}
STATUS_ALVO = {"ambiguo", "nao_encontrado", "numero_fora_da_faixa"}
VERDADEIRO = {"TRUE", "VERDADEIRO", "SIM", "1", "X"}
NOTA = "hub_cep_e_um_dos_candidatos_da_rua: mantido o CEP que o Hub ja tinha"


def so_digitos(s):
    return "".join(c for c in (s or "") if c.isdigit())


def ids_alvo(csv_clientes, sugestoes):
    with open(sugestoes, encoding="utf-8", newline="") as f:
        sug = {so_digitos(r["id_cliente_servico"]): r for r in csv.DictReader(f)}
    with open(csv_clientes, encoding="utf-8-sig", newline="") as f:
        linhas = list(csv.DictReader(f))
    alvo, motivos = {}, Counter()
    for r in linhas:
        idcs = so_digitos(r.get("id_cliente_servico"))
        if (r.get("status_cep") or "").strip() not in STATUS_ALVO or (r.get("feito") or "").strip().upper() in VERDADEIRO:
            continue
        cep = so_digitos(r.get("cep"))
        if len(cep) != 8 or cep in GENERICOS:
            continue
        s = sug.get(idcs)
        if not s:
            motivos["fora_da_analise"] += 1
            continue
        ceps = {so_digitos(x[:9]) for x in (s.get("alternativas") or "").split(" | ")} | {s.get("cep_proposto") or ""}
        ceps.discard("")
        if not ceps:
            motivos["rua_sem_candidatos"] += 1
        elif cep in ceps:
            alvo[idcs] = cep
        else:
            motivos["cep_do_hub_fora_dos_candidatos"] += 1
    return alvo, motivos


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv")
    ap.add_argument("--sugestoes", default="logs/sug_com_cep/sugestoes.csv")
    ap.add_argument("--planilha")
    ap.add_argument("--credenciais", default="credenciais_google.json")
    ap.add_argument("--aba")
    ap.add_argument("--gravar", action="store_true")
    args = ap.parse_args()

    alvo, motivos = ids_alvo(args.csv, args.sugestoes)
    print(f"Serviços cujo CEP no Hub já é um dos candidatos da rua: {len(alvo)} | não marcados: {dict(motivos)}")
    if not args.planilha:
        print("Para simular na planilha, informe --planilha ID.")
        return

    import gspread
    from gspread.utils import rowcol_to_a1

    sh = gspread.service_account(filename=args.credenciais).open_by_key(args.planilha)
    ws = None
    for w in ([sh.worksheet(args.aba)] if args.aba else sh.worksheets()):
        cab = [c.strip().lower() for c in w.row_values(1)]
        if {"id_cliente_servico", "status_cep", "feito"} <= set(cab):
            ws, cab_ws = w, cab
            break
    if ws is None:
        raise SystemExit("não achei a aba com id_cliente_servico, status_cep e feito (use --aba)")
    col = {n: i + 1 for i, n in enumerate(cab_ws)}
    todas = ws.get_all_values()

    def valor(r, nome):
        return r[col[nome] - 1] if nome in col and len(r) >= col[nome] else ""

    mudancas, backup, fora = [], [], Counter()
    for n, r in enumerate(todas, 1):
        if n == 1:
            continue
        idcs = so_digitos(valor(r, "id_cliente_servico"))
        if idcs not in alvo:
            continue
        if valor(r, "feito").strip().upper() in VERDADEIRO:
            fora["ja_marcado_feito"] += 1
            continue
        if valor(r, "status_cep").strip() not in STATUS_ALVO:
            fora["status_mudou"] += 1
            continue
        celulas = {"feito": True}
        if "obs_robo" in col:
            antigo = valor(r, "obs_robo").strip()
            celulas["obs_robo"] = (antigo + " | " + NOTA if antigo else NOTA)[:300]
        for nome, novo in celulas.items():
            backup.append((idcs, nome, valor(r, nome), novo))
            mudancas.append({"range": rowcol_to_a1(n, col[nome]), "values": [[novo]]})
    n_ok = sum(1 for _, nome, _, _ in backup if nome == "feito")
    print(f"A marcar como feito: {n_ok} | de fora: {dict(fora)} | células: {len(mudancas)}")
    if not args.gravar:
        print("Simulação: nada foi escrito. Use --gravar para aplicar.")
        return
    Path("logs").mkdir(exist_ok=True)
    arquivo = Path("logs") / f"backup_hub_candidatos_{datetime.now():%Y%m%d_%H%M%S}.csv"
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
