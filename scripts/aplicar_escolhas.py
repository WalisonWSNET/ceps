"""Aplica as ESCOLHAS que você digitou na coluna `escolha` da planilha (para os serviços ambiguo / nao_encontrado / numero_fora_da_faixa).

Como usar na planilha:
  1. Crie uma coluna com o título  escolha  (pode ser a primeira coluna livre à direita).
  2. Em cada linha que você conferiu, digite na coluna `escolha`:
       1, 2, 3 ...   = o 1º, 2º, 3º CEP da coluna `ceps_candidatos` (a ordem em que aparecem lá)
       11746362      = um CEP de 8 dígitos qualquer (com ou sem hífen), se o certo não estiver nos candidatos
       x  (ou 0, -)  = "não sei / deixar como está" (a linha é ignorada)
     Dá para digitar o número na primeira linha de um grupo e arrastar para baixo (mesma rua e mesmo número = mesma escolha).
  3. Rode:
       python scripts/aplicar_escolhas.py --planilha ID            # só simula e mostra o que faria
       python scripts/aplicar_escolhas.py --planilha ID --gravar   # escreve na planilha (e salva logs/backup_escolhas_*.csv)

Para cada linha escolhida (e ainda sem `feito`, com status ambiguo / nao_encontrado / numero_fora_da_faixa) o script escreve:
  cep_novo <- CEP escolhido, status_cep <- conferido_manual (o robô passa a processar), cep_novo_anterior <- valor antigo,
  obs_robo <- 'escolha manual: ...', e troca a célula `escolha` por 'aplicado' (para não repetir).
Só aceita CEP da região (11730000 a 11799999). Para desfazer:
  python scripts/reverter_sugestoes.py --backups "logs/backup_escolhas_*.csv" --ids <id_cliente_servico> --planilha ID --gravar
"""
import argparse
import csv
import re
from datetime import datetime
from pathlib import Path

VERDADEIRO = {"TRUE", "VERDADEIRO", "SIM", "1", "X"}
STATUS_ALVO = {"ambiguo", "nao_encontrado", "numero_fora_da_faixa"}
PULAR = {"x", "0", "-", "n", "nao", "não", "?"}


def so_digitos(s):
    return "".join(c for c in (s or "") if c.isdigit())


def candidatos(texto):
    """CEPs (8 dígitos) de `ceps_candidatos`, na ordem em que aparecem (cada item começa pelo CEP)."""
    return [so_digitos(m.group(1)) for m in re.finditer(r"(?:^|\|)\s*(\d{5}-?\d{3})\b", texto or "")]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--planilha", required=True)
    ap.add_argument("--credenciais", default="credenciais_google.json")
    ap.add_argument("--aba")
    ap.add_argument("--coluna", default="escolha")
    ap.add_argument("--gravar", action="store_true")
    args = ap.parse_args()

    import gspread
    from gspread.utils import rowcol_to_a1

    sh = gspread.service_account(filename=args.credenciais).open_by_key(args.planilha)
    ws = None
    for w in ([sh.worksheet(args.aba)] if args.aba else sh.worksheets()):
        cab = [c.strip().lower() for c in w.row_values(1)]
        if {"id_cliente_servico", "cep_novo", "status_cep", args.coluna} <= set(cab):
            ws, cab_ws = w, cab
            break
    if ws is None:
        raise SystemExit(f"não achei uma aba com as colunas id_cliente_servico, cep_novo, status_cep e {args.coluna} (use --aba; "
                         f"a coluna '{args.coluna}' precisa existir na linha 1)")
    col = {n: i + 1 for i, n in enumerate(cab_ws)}
    todas = ws.get_all_values()

    def valor(r, nome):
        return r[col[nome] - 1] if nome in col and len(r) >= col[nome] else ""

    mudancas, backup, ignoradas, n_ok, exemplos = [], [], {}, 0, []
    for n, r in enumerate(todas, 1):
        if n == 1:
            continue
        esc = valor(r, args.coluna).strip()
        if not esc or esc.lower() == "aplicado":
            continue
        idcs = so_digitos(valor(r, "id_cliente_servico"))
        motivo = None
        cep = None
        if esc.lower() in PULAR:
            motivo = "marcado_para_deixar_como_esta"
        elif valor(r, "feito").strip().upper() in VERDADEIRO:
            motivo = "ja_marcado_feito"
        elif valor(r, "status_cep").strip() not in STATUS_ALVO:
            motivo = "status_cep_nao_e_ambiguo_nem_nao_encontrado_nem_fora_da_faixa"
        elif len(so_digitos(esc)) == 8 and esc.replace("-", "").isdigit():
            cep = so_digitos(esc)
        elif esc.isdigit() and 1 <= int(esc) <= 9:
            lista = candidatos(valor(r, "ceps_candidatos"))
            if int(esc) > len(lista):
                motivo = f"candidato_{esc}_nao_existe_(tem_{len(lista)})"
            else:
                cep = lista[int(esc) - 1]
        else:
            motivo = "valor_de_escolha_invalido"
        if cep and not ("11730000" <= cep <= "11799999"):
            motivo, cep = "cep_fora_da_regiao", None
        if motivo:
            ignoradas[motivo] = ignoradas.get(motivo, 0) + 1
            continue
        n_ok += 1
        celulas = {"cep_novo": cep, "status_cep": "conferido_manual", args.coluna: "aplicado"}
        if "cep_novo_anterior" in col:
            celulas["cep_novo_anterior"] = valor(r, "cep_novo")
        if "obs_robo" in col:
            celulas["obs_robo"] = f"escolha manual: {'candidato ' + esc if len(esc) < 3 else 'CEP digitado'} ({cep})"[:300]
        for nome, novo in celulas.items():
            backup.append((idcs, nome, valor(r, nome), novo))
            mudancas.append({"range": rowcol_to_a1(n, col[nome]), "values": [[novo]]})
        if len(exemplos) < 6:
            exemplos.append((idcs, valor(r, "endereco")[:34], valor(r, "numero"), valor(r, "cep_novo"), cep))

    print(f"Linhas com escolha a aplicar: {n_ok} | ignoradas: {ignoradas} | células a escrever: {len(mudancas)}")
    for idcs, end, num, antigo, novo in exemplos:
        print(f"   ex.: serviço {idcs} {end} {num}: cep_novo {antigo} -> {novo}")
    if not args.gravar:
        print("Simulação: nada foi escrito. Use --gravar para aplicar.")
        return
    Path("logs").mkdir(exist_ok=True)
    arquivo = Path("logs") / f"backup_escolhas_{datetime.now():%Y%m%d_%H%M%S}.csv"
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
