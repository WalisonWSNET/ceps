"""Lista os serviços NÃO RESOLVIDOS (ambiguo / nao_encontrado / numero_fora_da_faixa, sem `feito`, com `escolha` vazia) em que o cep_novo/Hub já é
um CEP de rua mas esse CEP NÃO está entre os candidatos da coluna P. Para cada um mostra a rua a que esse CEP pertence na lista dos Correios,
o bairro dessa rua, a distância até o bairro do cliente (se houver) e os 2 primeiros candidatos, para você julgar se o CEP do Hub parece de
outra rua (possível erro) ou só uma grafia diferente. Só lê. Também grava logs/hub_fora_candidatos.csv.

Uso:
  python scripts/listar_cep_hub_fora_candidatos.py data/clientes_fim9.csv
"""
import argparse
import csv
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import atualizar_ceps as base  # noqa: E402
import refinar_ceps_geografia as geo  # noqa: E402

GENERICOS = {"11700000", "11730000", "11740000", "11750000"}
STATUS_ALVO = {"ambiguo", "nao_encontrado", "numero_fora_da_faixa"}
VERDADEIRO = {"TRUE", "VERDADEIRO", "SIM", "1", "X"}


def d(s):
    return "".join(c for c in (s or "") if c.isdigit())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv")
    ap.add_argument("--correios", default="data/ceps_logradouros.csv")
    args = ap.parse_args()
    idx = base.carrega_ref(args.correios)
    por_cep = {}
    for cid in idx.values():
        for es in cid.values():
            for e in es:
                por_cep.setdefault(e["cep"], []).append(e)
    with open(args.csv, encoding="utf-8-sig", newline="") as f:
        linhas = list(csv.DictReader(f))
    mapa = geo.MapaBairros(geo.centros_dos_bairros(linhas))
    saida = []
    for r in linhas:
        if (r.get("status_cep") or "").strip() not in STATUS_ALVO or (r.get("feito") or "").strip().upper() in VERDADEIRO:
            continue
        if (r.get("escolha") or "").strip():
            continue
        cep = d(r.get("cep_novo")) or d(r.get("cep"))
        p = (r.get("ceps_candidatos") or "").strip()
        if len(cep) != 8 or cep in GENERICOS or not p:
            continue
        cands = [d(x[:9]) for x in p.split(" | ")]
        if cep in cands:
            continue
        es = por_cep.get(cep, [])
        dono = " | ".join(dict.fromkeys(e["txt"].split("; ", 1)[-1].rstrip(")") for e in es)) or "(CEP não está na lista)"
        bairro_dono = es[0]["bairro_txt"] if es else ""
        c = geo.coordenada(r.get("coordenadas"))
        km = ""
        if es and c:
            ctr, _ = mapa.centro((r.get("cidade") or "").strip().upper(), bairro_dono)
            if ctr:
                km = f"{geo.km(c, ctr):.1f}"
        saida.append(dict(codigo_cliente=r.get("codigo_cliente"), endereco=r.get("endereco"), numero=r.get("numero"), bairro=r.get("bairro"),
                          cep_no_hub=cep, rua_dona_do_cep=dono[:70], bairro_do_cep=bairro_dono, km_ate_bairro_do_cep=km, candidatos=p[:90]))
    Path("logs").mkdir(exist_ok=True)
    with open("logs/hub_fora_candidatos.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, list(saida[0].keys()) if saida else ["codigo_cliente"])
        w.writeheader()
        w.writerows(saida)
    for a in saida:
        print(f"{a['codigo_cliente']:>6} | {a['endereco'][:22]:22} {a['numero']:>5} | {a['bairro'][:14]:14} | Hub {a['cep_no_hub']} = {a['rua_dona_do_cep'][:34]:34} [{a['bairro_do_cep'][:16]}] {a['km_ate_bairro_do_cep'] or '?':>4} km")
    print(f"\n{len(saida)} serviços | arquivo: logs/hub_fora_candidatos.csv")


if __name__ == "__main__":
    main()
