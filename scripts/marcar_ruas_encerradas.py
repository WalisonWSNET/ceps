"""Marca como ENCERRADOS (feito = TRUE + nota em obs_robo) os serviços das ruas listadas em data/ruas_encerradas.csv: ruas que você
decidiu deixar com o CEP genérico depois de tentar os PDFs dos Correios, o ViaCEP e o Hub (nada achado).

Só marca serviços que: estão sem `feito`, têm status_cep ambiguo / nao_encontrado / numero_fora_da_faixa e ainda estão com CEP genérico
em cep_novo (11700000, 11730000, 11740000, 11750000). Serviços dessas ruas que já têm CEP de rua ficam como estão.
A rua do cadastro precisa ser IGUAL à da lista (ignorando tipo da via, acentos e "de/da/dos"); o código usa o mesmo corte de nome do resto.
NÃO mexe em cep_novo nem em status_cep.

Uso:
  python scripts/marcar_ruas_encerradas.py --csv data/clientes_fim7.csv                 # só conta (offline, não usa a planilha)
  python scripts/marcar_ruas_encerradas.py --planilha ID                                # simula na planilha
  python scripts/marcar_ruas_encerradas.py --planilha ID --gravar                       # grava (e salva logs/backup_ruas_encerradas_*.csv)
"""
import argparse
import csv
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import atualizar_ceps as base  # noqa: E402

GENERICOS = {"11700000", "11730000", "11740000", "11750000"}
STATUS_ALVO = {"ambiguo", "nao_encontrado", "numero_fora_da_faixa"}
VERDADEIRO = {"TRUE", "VERDADEIRO", "SIM", "1", "X"}


def so_digitos(s):
    return "".join(c for c in (s or "") if c.isdigit())


def nome_da_rua(endereco):
    rua, _ = base.extrair_complemento(endereco or "")
    tipo, nome = base.separa_tipo(rua)
    t2, nome2 = base.separa_tipo(nome)  # "Rua Alameda X"
    if t2 and nome2.strip():
        nome = nome2
    return frozenset(base.tokens(nome))


def carregar_ruas(caminho):
    ruas = []
    with open(caminho, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            ruas.append(((r["cidade"] or "").strip().upper(), frozenset(base.tokens(r["rua"])), r["motivo"].strip()))
    return ruas


def motivo_da_rua(ruas, cidade, endereco):
    nome = nome_da_rua(endereco)
    for c, toks, motivo in ruas:
        if toks == nome and (not c or c == (cidade or "").strip().upper()):
            return motivo
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ruas", default="data/ruas_encerradas.csv")
    ap.add_argument("--csv", help="só conta, a partir de um CSV exportado (não usa a planilha)")
    ap.add_argument("--planilha")
    ap.add_argument("--credenciais", default="credenciais_google.json")
    ap.add_argument("--aba")
    ap.add_argument("--gravar", action="store_true")
    args = ap.parse_args()
    ruas = carregar_ruas(args.ruas)

    if args.csv:
        with open(args.csv, encoding="utf-8-sig", newline="") as f:
            linhas = list(csv.DictReader(f))
        cont, fora = Counter(), Counter()
        for r in linhas:
            m = motivo_da_rua(ruas, r.get("cidade"), r.get("endereco"))
            if not m or (r.get("feito") or "").strip().upper() in VERDADEIRO:
                continue
            if (r.get("status_cep") or "").strip() not in STATUS_ALVO:
                fora["status_diferente"] += 1
            elif so_digitos(r.get("cep_novo")) not in GENERICOS:
                fora["ja_tem_cep_de_rua"] += 1
            else:
                cont[(r.get("cidade") or "")[:4].upper() + " " + " ".join(sorted(nome_da_rua(r.get("endereco"))))] += 1
        print(f"A marcar como encerrados: {sum(cont.values())} | de fora: {dict(fora)}")
        for k, n in cont.most_common():
            print(f"   {n:4d}  {k}")
        return

    if not args.planilha:
        raise SystemExit("use --csv ARQUIVO (só conta) ou --planilha ID")
    import gspread
    from gspread.utils import rowcol_to_a1

    sh = gspread.service_account(filename=args.credenciais).open_by_key(args.planilha)
    ws = None
    for w in ([sh.worksheet(args.aba)] if args.aba else sh.worksheets()):
        cab = [c.strip().lower() for c in w.row_values(1)]
        if {"id_cliente_servico", "endereco", "cidade", "cep_novo", "status_cep", "feito"} <= set(cab):
            ws, cab_ws = w, cab
            break
    if ws is None:
        raise SystemExit("não achei a aba com id_cliente_servico, endereco, cidade, cep_novo, status_cep e feito (use --aba)")
    col = {n: i + 1 for i, n in enumerate(cab_ws)}
    todas = ws.get_all_values()

    def valor(r, nome):
        return r[col[nome] - 1] if nome in col and len(r) >= col[nome] else ""

    mudancas, backup, fora, n_ok = [], [], Counter(), 0
    for n, r in enumerate(todas, 1):
        if n == 1:
            continue
        m = motivo_da_rua(ruas, valor(r, "cidade"), valor(r, "endereco"))
        if not m or valor(r, "feito").strip().upper() in VERDADEIRO:
            continue
        if valor(r, "status_cep").strip() not in STATUS_ALVO:
            fora["status_diferente"] += 1
            continue
        if so_digitos(valor(r, "cep_novo")) not in GENERICOS:
            fora["ja_tem_cep_de_rua"] += 1
            continue
        n_ok += 1
        celulas = {"feito": True}
        if "obs_robo" in col:
            antigo = valor(r, "obs_robo").strip()
            nota = f"rua_encerrada: {m}"
            celulas["obs_robo"] = (antigo + " | " + nota if antigo else nota)[:300]
        for nome, novo in celulas.items():
            backup.append((so_digitos(valor(r, "id_cliente_servico")), nome, valor(r, nome), novo))
            mudancas.append({"range": rowcol_to_a1(n, col[nome]), "values": [[novo]]})
    print(f"A marcar como encerrados: {n_ok} | de fora: {dict(fora)} | células: {len(mudancas)}")
    if not args.gravar:
        print("Simulação: nada foi escrito. Use --gravar para aplicar.")
        return
    Path("logs").mkdir(exist_ok=True)
    arquivo = Path("logs") / f"backup_ruas_encerradas_{datetime.now():%Y%m%d_%H%M%S}.csv"
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
