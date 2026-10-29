"""Aplica na planilha (Google Sheets) o resultado de refinar_ceps_geografia.py.

Por padrão só SIMULA (mostra o que mudaria). Com --gravar escreve na planilha, e antes grava um backup em
logs/backup_refinamento.csv com cada valor antigo.

O que muda, só em linhas NÃO marcadas em 'feito' e que não estejam em 'conferido_manual':
  - acao 'trocar' / 'resolver': cep_novo <- sugerido, status_cep <- 'exato_por_geografia',
                                cep_novo_anterior <- valor antigo (coluna criada se não existir)
  - acao 'revisar' com status confiável: status_cep <- 'revisar_geografia' (o robô não mexe) e
                                ceps_candidatos <- alternativas com a distância
  - acao 'revisar' sem status confiável: só ceps_candidatos <- alternativas com a distância
Se a célula cep_novo da planilha já não é a que estava no CSV (alguém mexeu), a linha é pulada.

Uso:
  python scripts/aplicar_refinamento_planilha.py data/clientes_refinado.csv --planilha ID
  python scripts/aplicar_refinamento_planilha.py data/clientes_refinado.csv --planilha ID --gravar
"""
import argparse
import csv
from datetime import datetime
from pathlib import Path

CONFIAVEIS = {"exato", "exato_por_bairro", "exato_por_numero", "exato_por_faixa", "exato_por_coordenada",
              "aprox_exato", "aprox_exato_por_numero", "aprox_exato_por_bairro", "exato_por_geografia"}
VERDADEIRO = {"TRUE", "VERDADEIRO", "SIM", "1", "X"}


def so_digitos(s):
    return "".join(c for c in (s or "") if c.isdigit())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("refinado")
    ap.add_argument("--planilha", required=True)
    ap.add_argument("--credenciais", default="credenciais_google.json")
    ap.add_argument("--aba")
    ap.add_argument("--gravar", action="store_true")
    args = ap.parse_args()

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

    with open(args.refinado, encoding="utf-8", newline="") as f:
        refinado = list(csv.DictReader(f))

    novas_colunas = [c for c in ("cep_novo_anterior", "ceps_candidatos") if c not in col]
    for nome in novas_colunas:
        col[nome] = len(cab_ws) + 1 + novas_colunas.index(nome)

    mudancas, backup, resumo, pulados = [], [], {}, []
    for r in refinado:
        acao, idcs = r["acao"], so_digitos(r["id_cliente_servico"])
        n = linha_de.get(idcs)
        if acao not in ("trocar", "resolver", "revisar") or not n:
            continue
        if valor(n, "feito").strip().upper() in VERDADEIRO or valor(n, "status_cep").strip() == "conferido_manual":
            continue
        atual = so_digitos(valor(n, "cep_novo"))
        if atual != so_digitos(r["cep_novo_atual"]):
            pulados.append(idcs)  # alguém mexeu na planilha depois de o CSV ser gerado
            continue
        status_atual = valor(n, "status_cep").strip()
        alt = (f'{r["alternativas"]}' + (f' [{r["motivo"]}]' if r["motivo"] else "")) if r["alternativas"] else ""
        celulas = {}
        if acao in ("trocar", "resolver"):
            celulas = {"cep_novo": r["cep_novo_sugerido"], "status_cep": "exato_por_geografia", "cep_novo_anterior": atual}
        elif status_atual in CONFIAVEIS:
            celulas = {"status_cep": "revisar_geografia"}
            if alt:  # sem alternativas novas, mantém o texto antigo da coluna
                celulas["ceps_candidatos"] = alt
        elif alt:
            celulas = {"ceps_candidatos": alt}
        if not celulas:
            continue
        resumo[acao + ("*" if acao == "revisar" and status_atual in CONFIAVEIS else "")] = \
            resumo.get(acao + ("*" if acao == "revisar" and status_atual in CONFIAVEIS else ""), 0) + 1
        for nome, novo in celulas.items():
            backup.append((idcs, nome, valor(n, nome), novo))
            mudancas.append({"range": rowcol_to_a1(n, col[nome]), "values": [[novo]]})

    print("Linhas que mudariam (por ação; * = revisar com status confiável, sai da fila do robô):", resumo)
    print(f"Células a escrever: {len(mudancas)} | linhas puladas porque a planilha mudou: {len(pulados)}")
    if novas_colunas:
        print("Colunas novas que serão criadas no fim do cabeçalho:", novas_colunas)
    for idcs, nome, antigo, novo in backup[:8]:
        print(f"   ex.: serviço {idcs} {nome}: {antigo!r} -> {novo[:70]!r}")
    if not args.gravar:
        print("Simulação: nada foi escrito. Use --gravar para aplicar.")
        return

    Path("logs").mkdir(exist_ok=True)
    arquivo = Path("logs") / f"backup_refinamento_{datetime.now():%Y%m%d_%H%M%S}.csv"
    with open(arquivo, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id_cliente_servico", "coluna", "valor_antigo", "valor_novo"])
        w.writerows(backup)
    print("Backup dos valores antigos:", arquivo)
    for nome in novas_colunas:
        if col[nome] > ws.col_count:
            ws.add_cols(col[nome] - ws.col_count)
        ws.update_cell(1, col[nome], nome)
    for i in range(0, len(mudancas), 500):
        ws.batch_update(mudancas[i:i + 500], value_input_option="USER_ENTERED")
    print("Planilha atualizada.")


if __name__ == "__main__":
    main()
