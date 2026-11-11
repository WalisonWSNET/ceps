"""Aplica na planilha (Google Sheets) as sugestões de CEP geradas por sugerir_ceps.py.

Por padrão só SIMULA. Com --gravar escreve na planilha e, antes, grava um backup em logs/backup_sugestoes_AAAAMMDD_HHMMSS.csv.

Para cada linha escolhida (por confiança e/ou --ids), em serviços NÃO marcados em 'feito' e cujo status_cep ainda é o mesmo de
quando a sugestão foi gerada:
  cep_novo <- CEP proposto, status_cep <- 'conferido_manual' (o robô passa a processar), cep_novo_anterior <- valor antigo
  (coluna criada se não existir) e, se existir a coluna obs_robo, uma nota 'sugestao: <motivo>'.

Uso (valide uma amostra no Hub antes de gravar):
  python scripts/aplicar_sugestoes.py logs/sugestoes/sugestoes.csv --planilha ID                    # simula só as de confiança alta
  python scripts/aplicar_sugestoes.py logs/sugestoes/sugestoes.csv --planilha ID --confianca media  # alta + media
  python scripts/aplicar_sugestoes.py logs/sugestoes/sugestoes.csv --planilha ID --ids 123 456 --gravar
"""
import argparse
import csv
from datetime import datetime
from pathlib import Path

VERDADEIRO = {"TRUE", "VERDADEIRO", "SIM", "1", "X"}
GENERICOS = {"11700000", "11730000", "11740000", "11750000"}
NIVEIS = {"alta": ("alta",), "media": ("alta", "media"), "baixa": ("alta", "media", "baixa")}


def so_digitos(s):
    return "".join(c for c in (s or "") if c.isdigit())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sugestoes")
    ap.add_argument("--planilha", required=True)
    ap.add_argument("--credenciais", default="credenciais_google.json")
    ap.add_argument("--aba")
    ap.add_argument("--confianca", choices=list(NIVEIS), default="alta", help="até que nível aplicar (padrão: só alta)")
    ap.add_argument("--ids", nargs="+", help="aplica só estes id_cliente_servico (ignora --confianca)")
    ap.add_argument("--forcar-cep-de-rua", action="store_true",
                    help="aplica também onde o cep_novo atual já é de rua (por padrão essas linhas são puladas)")
    ap.add_argument("--gravar", action="store_true")
    args = ap.parse_args()

    import gspread
    from gspread.utils import rowcol_to_a1

    with open(args.sugestoes, encoding="utf-8", newline="") as f:
        sugestoes = list(csv.DictReader(f))
    ids = set(map(so_digitos, args.ids)) if args.ids else None
    escolhidas = [r for r in sugestoes if r.get("cep_proposto") and
                  ((so_digitos(r["id_cliente_servico"]) in ids) if ids else r.get("confianca") in NIVEIS[args.confianca])]

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

    novas = [c for c in ("cep_novo_anterior",) if c not in col]
    for nome in novas:
        col[nome] = len(cab_ws) + 1 + novas.index(nome)
    mudancas, backup, puladas, n_ok = [], [], {}, 0
    for r in escolhidas:
        idcs = so_digitos(r["id_cliente_servico"])
        n = linha_de.get(idcs)
        motivo = None
        if not n:
            motivo = "id_nao_esta_na_planilha"
        elif valor(n, "feito").strip().upper() in VERDADEIRO:
            motivo = "ja_marcado_feito"
        elif valor(n, "status_cep").strip() != r["status_atual"]:
            motivo = "status_cep_mudou"
        elif so_digitos(valor(n, "cep_novo")) not in GENERICOS and not args.forcar_cep_de_rua:
            motivo = "cep_atual_ja_e_de_rua"  # o Hub já tem um CEP de rua: não troca por um palpite
        if motivo:
            puladas[motivo] = puladas.get(motivo, 0) + 1
            continue
        n_ok += 1
        celulas = {"cep_novo": r["cep_proposto"], "status_cep": "conferido_manual", "cep_novo_anterior": valor(n, "cep_novo")}
        if "obs_robo" in col:
            celulas["obs_robo"] = (f"sugestao: {r['motivo']}" + (f" | complemento: {r['complemento_extraido']}"
                                                                if r.get("complemento_extraido") else ""))[:300]
        for nome, novo in celulas.items():
            backup.append((idcs, nome, valor(n, nome), novo))
            mudancas.append({"range": rowcol_to_a1(n, col[nome]), "values": [[novo]]})

    print(f"Sugestões escolhidas: {len(escolhidas)} | a aplicar: {n_ok} | puladas: {puladas}")
    print(f"Células a escrever: {len(mudancas)}" + (f" | colunas novas: {novas}" if novas else ""))
    for idcs, nome, antigo, novo in backup[:8]:
        print(f"   ex.: serviço {idcs} {nome}: {antigo!r} -> {novo[:70]!r}")
    if not args.gravar:
        print("Simulação: nada foi escrito. Use --gravar para aplicar.")
        return
    Path("logs").mkdir(exist_ok=True)
    arquivo = Path("logs") / f"backup_sugestoes_{datetime.now():%Y%m%d_%H%M%S}.csv"
    with open(arquivo, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id_cliente_servico", "coluna", "valor_antigo", "valor_novo"])
        w.writerows(backup)
    print("Backup dos valores antigos:", arquivo)
    for nome in novas:
        if col[nome] > ws.col_count:
            ws.add_cols(col[nome] - ws.col_count)
        ws.update_cell(1, col[nome], nome)
    for i in range(0, len(mudancas), 500):
        ws.batch_update(mudancas[i:i + 500], value_input_option="USER_ENTERED")
    print("Planilha atualizada.")


if __name__ == "__main__":
    main()
