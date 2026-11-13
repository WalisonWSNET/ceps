"""Lista, direto da planilha, as RUAS que ainda faltam decidir na coluna `escolha` (serviços ambiguo / nao_encontrado / numero_fora_da_faixa,
sem `feito`, com candidatos na coluna P e `escolha` vazia), da rua com mais clientes para a com menos. Para cada rua mostra a quantidade,
dois clientes de exemplo (o de menor e o de maior número, com o código do cliente, para você abrir o mapa), o bairro do cliente e o
candidato mais perto (CEP, bairro e distância). Só lê; não escreve nada na planilha. Também grava logs/pendentes_escolha.csv.

Uso:
  python scripts/listar_pendentes_escolha.py --planilha ID                 # as 40 ruas com mais clientes
  python scripts/listar_pendentes_escolha.py --planilha ID --top 100 --minimo 2
"""
import argparse
import csv
import re
from collections import defaultdict
from pathlib import Path

VERDADEIRO = {"TRUE", "VERDADEIRO", "SIM", "1", "X"}
STATUS_ALVO = {"ambiguo", "nao_encontrado", "numero_fora_da_faixa"}


def numero(s):
    m = re.search(r"\d+", s or "")
    return int(m.group()) if m else 0


def norma_rua(s):
    t = re.sub(r"[.:,]", " ", (s or "").upper())
    t = re.sub(r"\b(R|RUA|AV|AVENIDA|ESTR|ESTRADA|TV|TRAVESSA|AL|ALAMEDA)\b", " ", t)
    return " ".join(t.split())


def primeiro_candidato(p):
    itens = [i.strip() for i in (p or "").split(" | ")]
    for i in itens:
        m = re.match(r"(\d{8}) \(([^;)]*(?:\([^)]*\))?[^;)]*);?\s*([^)]*)\)", i)
        if m:
            km = re.search(r"(\d+(?:\.\d+)?)\s*km", i)
            return m.group(1), m.group(2).strip(), (km.group(1) + " km") if km else "?"
    return "", "", ""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--planilha", required=True)
    ap.add_argument("--credenciais", default="credenciais_google.json")
    ap.add_argument("--aba")
    ap.add_argument("--coluna", default="escolha")
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--minimo", type=int, default=1, help="só ruas com pelo menos este número de clientes")
    args = ap.parse_args()

    import gspread

    sh = gspread.service_account(filename=args.credenciais).open_by_key(args.planilha)
    ws = None
    for w in ([sh.worksheet(args.aba)] if args.aba else sh.worksheets()):
        cab = [c.strip().lower() for c in w.row_values(1)]
        if {"id_cliente_servico", "codigo_cliente", "endereco", "numero", "bairro", "cidade", "status_cep", "feito", "ceps_candidatos", args.coluna} <= set(cab):
            ws, cab_ws = w, cab
            break
    if ws is None:
        raise SystemExit("não achei a aba com as colunas necessárias (use --aba)")
    col = {n: i for i, n in enumerate(cab_ws)}
    ruas = defaultdict(list)
    for r in ws.get_all_values()[1:]:
        def v(nome):
            return r[col[nome]] if len(r) > col[nome] else ""
        if v("status_cep").strip() not in STATUS_ALVO or v("feito").strip().upper() in VERDADEIRO:
            continue
        if not v("ceps_candidatos").strip() or v(args.coluna).strip():
            continue
        ruas[(v("cidade").strip().upper()[:4], norma_rua(v("endereco")))].append(r)

    saida = []
    total = sum(len(x) for x in ruas.values())
    print(f"Serviços ainda sem decisão: {total} em {len(ruas)} ruas (agrupadas pelo nome da rua)\n")
    for (cid, rua), linhas in sorted(ruas.items(), key=lambda t: -len(t[1])):
        if len(linhas) < args.minimo or len(saida) >= args.top:
            continue
        ordem = sorted(linhas, key=lambda r: numero(r[col["numero"]]))
        ex = [ordem[0], ordem[-1]] if len(ordem) > 1 else ordem
        cep, bairro_c, km = primeiro_candidato(ex[0][col["ceps_candidatos"]])
        bairros = sorted({r[col["bairro"]].strip().title() for r in linhas})[:3]
        cods = ", ".join(f"{r[col['codigo_cliente']]} (nº {r[col['numero']]})" for r in ex)
        print(f"{len(linhas):3d}  {cid} {rua[:30]:30} | bairro(s): {', '.join(bairros)[:42]:42} | 1º candidato: {cep} {bairro_c[:22]} {km} | códigos: {cods}")
        saida.append(dict(clientes=len(linhas), cidade=cid, rua=rua, bairros="; ".join(bairros), primeiro_candidato=cep, bairro_candidato=bairro_c,
                          distancia=km, exemplos=cods))
    Path("logs").mkdir(exist_ok=True)
    with open("logs/pendentes_escolha.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, ["clientes", "cidade", "rua", "bairros", "primeiro_candidato", "bairro_candidato", "distancia", "exemplos"])
        w.writeheader()
        w.writerows(saida)
    print("\nArquivo: logs/pendentes_escolha.csv")


if __name__ == "__main__":
    main()
