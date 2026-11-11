"""Auditoria (só leitura) dos serviços com cep_novo em VERDE na planilha ("já correto no Hub", segundo você).
Lê as cores da planilha e o CSV exportado; não usa o Hub e não escreve nada.

Mostra, entre os verdes:
  - quantos têm CEP genérico (11700000 / 11730000 / 11740000 / 11750000) em cep_novo -> NÃO são CEP de rua, podem estar errados;
  - quantos têm cep_novo igual ao cep da linha (nada a mudar) e quantos diferentes;
  - a distribuição de status_cep e quantos já têm `feito`.
Gera logs/verdes/verdes_genericos.csv (os verdes com CEP genérico) e logs/verdes/verdes_resumo.csv.

Uso:
  python scripts/auditar_verdes.py data/clientes_fim.csv --planilha ID [--aba NOME]
"""
import argparse
import csv
import sys
from collections import Counter
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
    ap.add_argument("--saida", default="logs/verdes")
    args = ap.parse_args()

    linhas = hub.ler_linhas(args.csv)
    verdes = hub.ids_cep_verde(args.planilha, args.credenciais, args.aba)
    sel = [r for r in linhas if hub.so_digitos(r.get("id_cliente_servico")) in verdes]
    print(f"Verdes na planilha: {len(verdes)} | encontrados no CSV: {len(sel)}")

    por_status = Counter((r.get("status_cep") or "").strip() or "(vazio)" for r in sel)
    genericos = [r for r in sel if hub.so_digitos(r.get("cep_novo")) in GENERICOS]
    iguais = sum(1 for r in sel if hub.so_digitos(r.get("cep_novo")) == hub.so_digitos(r.get("cep")))
    feitos = sum(1 for r in sel if hub.marcado(r, "feito"))
    print(f"\nCom CEP genérico em cep_novo (não é CEP de rua): {len(genericos)}")
    for cep in sorted(GENERICOS):
        n = sum(1 for r in genericos if hub.so_digitos(r.get("cep_novo")) == cep)
        if n:
            print(f"   {cep}: {n}")
    print(f"cep_novo igual ao cep da linha: {iguais} | diferente: {len(sel) - iguais}")
    print(f"Já marcados em feito: {feitos}")
    print("\nstatus_cep dos verdes:")
    for st, n in por_status.most_common():
        print(f"   {st}: {n}")

    nao_resolvidos = [r for r in sel if (r.get("status_cep") or "").strip() in ("fora_do_escopo", "nao_encontrado", "ambiguo", "numero_fora_da_faixa")]
    print(f"\nVerdes NÃO resolvidos (cep_novo só repete o CEP atual): {len(nao_resolvidos)}")
    print(f"  destes, com CEP genérico: {sum(1 for r in nao_resolvidos if hub.so_digitos(r.get('cep_novo')) in GENERICOS)}")
    sug_arq = Path("logs/sugestoes/sugestoes.csv")
    if sug_arq.exists():
        conf = {}
        with open(sug_arq, encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                conf[hub.so_digitos(r["id_cliente_servico"])] = r.get("confianca") or "sem_sugestao"
        c = Counter(conf.get(hub.so_digitos(r.get("id_cliente_servico")), "(não analisado)") for r in genericos)
        print("Verdes com CEP genérico x sugestão (logs/sugestoes/sugestoes.csv):")
        for k, n in c.most_common():
            print(f"   {k}: {n}")
    Path(args.saida).mkdir(parents=True, exist_ok=True)
    campos = ["id_cliente_servico", "codigo_cliente", "endereco", "numero", "bairro", "cidade", "cep", "cep_novo", "status_cep", "feito"]
    with open(Path(args.saida) / "verdes_genericos.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, campos, extrasaction="ignore")
        w.writeheader()
        w.writerows(genericos)
    with open(Path(args.saida) / "verdes_resumo.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["indicador", "valor"])
        w.writerows([("verdes", len(sel)), ("cep_generico", len(genericos)), ("cep_novo_igual_ao_cep", iguais), ("feito", feitos)]
                    + [(f"status_{st}", n) for st, n in por_status.most_common()])
    print(f"\nArquivos em {args.saida}\\ (verdes_genericos.csv, verdes_resumo.csv)")


if __name__ == "__main__":
    main()
