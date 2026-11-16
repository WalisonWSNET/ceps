"""Prepara os CLIENTES NOVOS (a aba nova da planilha, com CEP genérico) para entrarem na rodagem do robô, no mesmo formato da aba principal.

O que faz (offline, sem tocar no Hub): lê o CSV exportado da aba nova; tira os serviços que JÁ estão na aba principal (compara com o CSV mais
recente); associa cada um ao CEP de rua da lista dos Correios (o mesmo casamento do projeto: status_cep exato, exato_por_bairro, ambiguo,
nao_encontrado...); refina pela coordenada (como o refinar_ceps_geografia); e grava um CSV com as colunas na MESMA ordem da aba principal.
Com --anexar ele escreve essas linhas no fim da aba principal da planilha (cep_novo, status_cep e ceps_candidatos já preenchidos, feito vazio).
Depois disso o fluxo é o de sempre: sugerir_ceps -> escolhas -> exportar um CSV novo -> robô.

Uso:
  python scripts/preparar_clientes_novos.py data/clientes_novos.csv --referencia data/clientes_fim12.csv
  python scripts/preparar_clientes_novos.py data/clientes_novos.csv --referencia data/clientes_fim12.csv --planilha ID --anexar          # simula
  python scripts/preparar_clientes_novos.py data/clientes_novos.csv --referencia data/clientes_fim12.csv --planilha ID --anexar --gravar
"""
import argparse
import csv
import sys
import tempfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import atualizar_ceps as base  # noqa: E402
import refinar_ceps_geografia as refina  # noqa: E402

VERDADEIRO = {"TRUE", "VERDADEIRO", "SIM", "1", "X"}
GENERICOS = {"11700000", "11730000", "11740000", "11750000"}


def so_digitos(s):
    return "".join(c for c in (s or "") if c.isdigit())


def ler(caminho):
    with open(caminho, encoding="utf-8-sig", newline="") as f:
        amostra = f.read(4096)
        f.seek(0)
        delim = ";" if amostra.split("\n")[0].count(";") > amostra.split("\n")[0].count(",") else ","
        rd = csv.DictReader(f, delimiter=delim)
        return rd.fieldnames, list(rd)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("novos", help="CSV exportado da aba nova (clientes novos)")
    ap.add_argument("--referencia", required=True, help="CSV mais recente da aba principal (cabeçalho e IDs já existentes)")
    ap.add_argument("--correios", default="data/ceps_logradouros.csv")
    ap.add_argument("--col-endereco", default="endereco",
                    help="nome da coluna que tem a RUA no CSV dos novos (ex.: servico, se o título da coluna estiver diferente)")
    ap.add_argument("--saida", default="data/clientes_novos_prontos.csv")
    ap.add_argument("--planilha")
    ap.add_argument("--credenciais", default="credenciais_google.json")
    ap.add_argument("--aba", help="aba PRINCIPAL (padrão: a primeira com id_cliente_servico, cep_novo e status_cep)")
    ap.add_argument("--anexar", action="store_true")
    ap.add_argument("--gravar", action="store_true")
    args = ap.parse_args()

    cab_ref, ref = ler(args.referencia)
    ids_existentes = {so_digitos(r["id_cliente_servico"]) for r in ref}
    cab_novos, novos = ler(args.novos)
    if args.col_endereco != "endereco":
        if args.col_endereco not in cab_novos:
            raise SystemExit(f"a coluna '{args.col_endereco}' não existe no CSV dos novos. Colunas: {cab_novos}")
        for r in novos:
            r["endereco"] = r[args.col_endereco]
        if "endereco" not in cab_novos:
            cab_novos = list(cab_novos) + ["endereco"]
    amostra_ruas = [r["endereco"] for r in novos if r.get("endereco")][:3]
    print(f"Coluna da rua: '{args.col_endereco}' | exemplos: {amostra_ruas}")
    faltam = [c for c in ("id_cliente_servico", "codigo_cliente", "endereco", "numero", "bairro", "cidade", "coordenadas", "cep") if c not in cab_novos]
    if faltam:
        raise SystemExit(f"o CSV dos novos não tem as colunas: {faltam}")
    repetidos = [r for r in novos if so_digitos(r["id_cliente_servico"]) in ids_existentes]
    vistos, unicos = set(), []
    for r in novos:
        i = so_digitos(r["id_cliente_servico"])
        if not i or i in ids_existentes or i in vistos:
            continue
        vistos.add(i)
        unicos.append(r)
    print(f"Clientes novos no CSV: {len(novos)} | já estão na aba principal: {len(repetidos)} | a preparar: {len(unicos)}")
    if not unicos:
        raise SystemExit("nada a preparar")

    with tempfile.TemporaryDirectory() as tmp:
        t1, t2, t3 = (str(Path(tmp) / n) for n in ("m.csv", "n.csv", "r.csv"))
        campos = [c for c in cab_novos if c not in ("cep_novo", "status_cep", "ceps_candidatos")]
        with open(t1, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, campos, extrasaction="ignore")
            w.writeheader()
            # os clientes da aba principal entram junto (só como vizinhança): os bairros e os vizinhos já resolvidos ajudam a decidir
            # os novos; depois só as linhas dos novos são aproveitadas
            w.writerows(unicos)
            w.writerows(ref)
        base.main(t1, args.correios, t2)  # casamento com a lista dos Correios: cep_novo, status_cep, ceps_candidatos
        _, casados = ler(t2)
        refina.main(t2, args.correios, t3)  # refinamento pela coordenada
        _, refinados = ler(t3)
        casados = [r for r in casados if so_digitos(r["id_cliente_servico"]) in vistos]
    ref_por_id = {so_digitos(r["id_cliente_servico"]): r for r in refinados}
    prontos, cont = [], Counter()
    for r in casados:
        rf = ref_por_id.get(so_digitos(r["id_cliente_servico"]))
        if rf:
            acao, sug = rf["acao"], so_digitos(rf["cep_novo_sugerido"])
            if acao in ("trocar", "resolver") and sug:
                r["cep_novo"], r["status_cep"] = sug, "exato_por_geografia"
                r["ceps_candidatos"] = ""
            elif acao == "revisar" and r["status_cep"] in ("ambiguo", "nao_encontrado", "numero_fora_da_faixa") and rf.get("alternativas"):
                r["ceps_candidatos"] = rf["alternativas"]
        linha = {c: r.get(c, "") for c in cab_ref}
        linha.update(cep_novo=r["cep_novo"], status_cep=r["status_cep"], ceps_candidatos=r.get("ceps_candidatos", ""))
        for c in ("feito", "obs_robo", "cep_novo_anterior", "escolha"):
            if c in linha:
                linha[c] = ""
        prontos.append(linha)
        cont[r["status_cep"]] += 1
    with open(args.saida, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, cab_ref)
        w.writeheader()
        w.writerows(prontos)
    print(f"\nPreparados {len(prontos)} serviços -> {args.saida}")
    for st, n in cont.most_common():
        print(f"   {st}: {n}")

    if not args.anexar:
        print("\nPara colocar na planilha: rode de novo com --planilha ID --anexar (simula) e depois --gravar.")
        return
    if not args.planilha:
        raise SystemExit("--anexar precisa de --planilha ID")
    import gspread

    sh = gspread.service_account(filename=args.credenciais).open_by_key(args.planilha)
    ws = None
    for w in ([sh.worksheet(args.aba)] if args.aba else sh.worksheets()):
        cab = [c.strip().lower() for c in w.row_values(1)]
        if {"id_cliente_servico", "cep_novo", "status_cep"} <= set(cab):
            ws, cab_ws = w, cab
            break
    if ws is None:
        raise SystemExit("não achei a aba principal (use --aba)")
    existentes = {so_digitos(v) for v in ws.col_values(cab_ws.index("id_cliente_servico") + 1)[1:]}
    a_anexar = [l for l in prontos if so_digitos(l["id_cliente_servico"]) not in existentes]
    print(f"\nAba '{ws.title}': linhas hoje {len(existentes)} | a anexar {len(a_anexar)} (já existiam na aba: {len(prontos) - len(a_anexar)})")
    if not args.gravar:
        print("Simulação: nada foi escrito. Use --gravar para anexar.")
        return
    cab_orig = ws.row_values(1)
    matriz = [[linha.get(c.strip(), linha.get(c.strip().lower(), "")) for c in cab_orig] for linha in a_anexar]
    ws.append_rows(matriz, value_input_option="USER_ENTERED")
    Path("logs").mkdir(exist_ok=True)
    with open("logs/clientes_novos_anexados.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id_cliente_servico"])
        w.writerows([[l["id_cliente_servico"]] for l in a_anexar])
    print(f"Anexadas {len(a_anexar)} linhas ao fim da aba '{ws.title}'. IDs em logs/clientes_novos_anexados.csv (para desfazer, apague essas linhas).")


if __name__ == "__main__":
    main()
