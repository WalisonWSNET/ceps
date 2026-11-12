"""Preenche a coluna `ceps_candidatos` da planilha para os serviços ambiguo / nao_encontrado / numero_fora_da_faixa que estão SEM candidatos
mas para os quais o sugerir_ceps.py achou um CEP proposto (confiança baixa) ou alternativas. Assim eles entram no fluxo da coluna `escolha`.

O 1º candidato é o CEP que o sistema propôs (o melhor palpite); os seguintes são as alternativas. Só escreve onde a coluna está vazia.
Não mexe em cep_novo, status_cep nem feito.

Uso:
  python scripts/sugerir_ceps.py data/clientes_fim7.csv                                  # gera logs/sugestoes/sugestoes.csv
  python scripts/preencher_candidatos.py --planilha ID                                   # simula
  python scripts/preencher_candidatos.py --planilha ID --gravar                          # grava (e salva logs/backup_candidatos_*.csv)
"""
import argparse
import csv
from datetime import datetime
from pathlib import Path

VERDADEIRO = {"TRUE", "VERDADEIRO", "SIM", "1", "X"}
STATUS_ALVO = {"ambiguo", "nao_encontrado", "numero_fora_da_faixa"}


def so_digitos(s):
    return "".join(c for c in (s or "") if c.isdigit())


def texto_candidatos(r):
    """CEP proposto primeiro, depois as alternativas, cada CEP uma só vez (a numeração 1, 2, 3 da coluna `escolha` segue essa ordem)."""
    itens, vistos = [], set()
    if r.get("cep_proposto"):
        vistos.add(r["cep_proposto"])
        itens.append(f"{r['cep_proposto']} ({r.get('bairro_correios') or '?'}; {r.get('rua_correios') or ''}"
                     + (f"; {r['distancia_km']} km" if r.get("distancia_km") else "") + ")")
    for alt in (r.get("alternativas") or "").split(" | "):
        cep = so_digitos(alt[:9])
        if len(cep) == 8 and cep not in vistos:
            vistos.add(cep)
            itens.append(alt.strip())
    return " | ".join(itens)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sugestoes", default="logs/sugestoes/sugestoes.csv")
    ap.add_argument("--planilha", required=True)
    ap.add_argument("--credenciais", default="credenciais_google.json")
    ap.add_argument("--aba")
    ap.add_argument("--gravar", action="store_true")
    args = ap.parse_args()

    with open(args.sugestoes, encoding="utf-8", newline="") as f:
        sug = {so_digitos(r["id_cliente_servico"]): r for r in csv.DictReader(f)}

    import gspread
    from gspread.utils import rowcol_to_a1

    sh = gspread.service_account(filename=args.credenciais).open_by_key(args.planilha)
    ws = None
    for w in ([sh.worksheet(args.aba)] if args.aba else sh.worksheets()):
        cab = [c.strip().lower() for c in w.row_values(1)]
        if {"id_cliente_servico", "status_cep", "feito", "ceps_candidatos"} <= set(cab):
            ws, cab_ws = w, cab
            break
    if ws is None:
        raise SystemExit("não achei a aba com id_cliente_servico, status_cep, feito e ceps_candidatos (use --aba)")
    col = {n: i + 1 for i, n in enumerate(cab_ws)}
    todas = ws.get_all_values()

    def valor(r, nome):
        return r[col[nome] - 1] if nome in col and len(r) >= col[nome] else ""

    mudancas, backup, n_ok, sem = [], [], 0, 0
    for n, r in enumerate(todas, 1):
        if n == 1:
            continue
        if valor(r, "status_cep").strip() not in STATUS_ALVO or valor(r, "feito").strip().upper() in VERDADEIRO:
            continue
        if valor(r, "ceps_candidatos").strip():
            continue
        s = sug.get(so_digitos(valor(r, "id_cliente_servico")))
        txt = texto_candidatos(s) if s else ""
        if not txt:
            sem += 1
            continue
        n_ok += 1
        backup.append((so_digitos(valor(r, "id_cliente_servico")), "ceps_candidatos", "", txt))
        mudancas.append({"range": rowcol_to_a1(n, col["ceps_candidatos"]), "values": [[txt[:2000]]]})
    print(f"Serviços sem candidatos na planilha que ganham candidatos: {n_ok} | sem nenhum candidato (continuam vazios): {sem}")
    for idcs, _, _, txt in backup[:5]:
        print(f"   ex.: serviço {idcs}: {txt[:110]}")
    if not args.gravar:
        print("Simulação: nada foi escrito. Use --gravar para aplicar.")
        return
    Path("logs").mkdir(exist_ok=True)
    arquivo = Path("logs") / f"backup_candidatos_{datetime.now():%Y%m%d_%H%M%S}.csv"
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
