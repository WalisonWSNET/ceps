"""Relatório final do projeto de CEPs, a partir do CSV mais recente exportado da planilha e do log do robô (logs/log_ceps.csv). Só lê.

Mostra: total da planilha; serviços de outras cidades (fora do escopo); serviços das 3 cidades que já tinham CEP de rua e os que precisavam
(CEP genérico no Hub original); e, dos que precisavam, quantos foram alterados pelo robô, quantos alterados manualmente, quantos já estavam
certos, quantos ficam com o CEP genérico e quantos ainda estão pendentes. A coluna `cep` da planilha é o CEP que o Hub tinha no início.

Definições:
  alterados pelo robô                 robô gravou (status ok): inclui os CEPs achados pela lista e os decididos por você (conferido_manual).
  alterados manualmente no Hub        você mudou o CEP direto no Hub, à mão: feito = TRUE, CEP novo de rua, mas SEM registro do robô.
  já certos                           o robô achou o Hub já certo (ja_atualizado) ou o Hub já tinha o CEP certo.
  genéricos mantidos                  continuam com 11700000/11730000/11740000/11750000: x, ruas encerradas, ruas sem CEP nas fontes.
  pendentes                           ainda na fila do robô ou pulados para revisão (bairro_longe e outros).

Uso:
  python scripts/relatorio_final.py data/clientes_fim11.csv
"""
import argparse
import csv
from collections import Counter
from pathlib import Path

GENERICOS = {"11700000", "11730000", "11740000", "11750000"}
CIDADES = {"ITAMHAÉM", "ITANHAÉM", "MONGAGUÁ", "PERUÍBE"}
NAO_RESOLVIDOS = {"ambiguo", "nao_encontrado", "numero_fora_da_faixa"}
VERDADEIRO = {"TRUE", "VERDADEIRO", "SIM", "1", "X"}


def d(s):
    return "".join(c for c in (s or "") if c.isdigit())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv")
    ap.add_argument("--log", default="logs/log_ceps.csv")
    args = ap.parse_args()
    with open(args.csv, encoding="utf-8-sig", newline="") as f:
        linhas = list(csv.DictReader(f))
    ultimo = {}
    if Path(args.log).exists():
        with open(args.log, encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                ultimo[r["id_cliente_servico"]] = r["status"]

    c = Counter()
    manuais = []
    for r in linhas:
        c["total"] += 1
        cidade = (r.get("cidade") or "").strip().upper()
        if cidade not in CIDADES:
            c["fora"] += 1
            continue
        c["tres_cidades"] += 1
        cep = d(r.get("cep"))
        if cep not in GENERICOS:
            c["ja_tinham_cep_de_rua"] += 1
            continue
        c["precisavam"] += 1
        idcs = d(r.get("id_cliente_servico"))
        robo = ultimo.get(idcs)
        feito = (r.get("feito") or "").strip().upper() in VERDADEIRO
        st = (r.get("status_cep") or "").strip()
        novo = d(r.get("cep_novo"))
        obs = (r.get("obs_robo") or "").lower()
        if robo == "ok":
            c["robo_decidido_por_voce" if st == "conferido_manual" else "robo_automatico"] += 1
        elif robo == "ja_atualizado":
            c["ja_certos"] += 1
        elif feito and novo not in GENERICOS and novo:
            c["manual_no_hub"] += 1
            manuais.append(r)
        elif novo in GENERICOS and (st in NAO_RESOLVIDOS or "rua_encerrada" in obs or feito):
            c["genericos_mantidos"] += 1
        elif robo and robo != "ok":
            c["pendentes_pulados"] += 1
        else:
            c["pendentes_fila"] += 1
    n = c["precisavam"]
    pelo_robo = c["robo_automatico"] + c["robo_decidido_por_voce"]
    alterados = pelo_robo + c["manual_no_hub"]
    print(f"{c['total']} total da planilha")
    print(f"{c['fora']} outras cidades (fora do escopo)")
    print(f"{c['tres_cidades']} serviços em Itanhaém, Mongaguá e Peruíbe, dos quais:")
    print(f"   {c['ja_tinham_cep_de_rua']} já tinham CEP de rua no Hub (nada a fazer)")
    print(f"   {n} precisavam de CEP de rua (CEP genérico no Hub)")
    print(f"\nDos {n} que precisavam:")
    print(f"   {alterados:6d}  alterados (CEP de rua gravado no Hub)  [{100 * alterados / n:.1f}%]")
    print(f"      {pelo_robo:6d}  alterados pelo robô  (dos quais {c['robo_decidido_por_voce']} com CEP decidido por você: sugestões validadas e escolhas)")
    print(f"      {c['manual_no_hub']:6d}  alterados manualmente no Hub, à mão, sem o robô (feito sem registro do robô)")
    print(f"   {c['ja_certos']:6d}  já estavam certos (robô: ja_atualizado)")
    print(f"   {c['genericos_mantidos']:6d}  CEPs genéricos mantidos  [{100 * c['genericos_mantidos'] / n:.1f}%]")
    print(f"   {c['pendentes_pulados'] + c['pendentes_fila']:6d}  ainda pendentes (fila do robô {c['pendentes_fila']}, pulados para revisão {c['pendentes_pulados']})")
    if manuais:
        Path("logs").mkdir(exist_ok=True)
        with open("logs/alterados_manualmente.csv", "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, ["id_cliente_servico", "codigo_cliente", "endereco", "numero", "bairro", "cidade", "cep", "cep_novo", "status_cep", "obs_robo"],
                               extrasaction="ignore")
            w.writeheader()
            w.writerows(manuais)
        por_status = Counter((r.get("status_cep") or "").strip() for r in manuais)
        print("\nAlterados manualmente no Hub, por status_cep:", dict(por_status.most_common(8)))
        print("   lista completa em logs/alterados_manualmente.csv")
    soma = alterados + c["ja_certos"] + c["genericos_mantidos"] + c["pendentes_pulados"] + c["pendentes_fila"]
    print(f"\nconferência: {soma} de {n}" + ("  OK" if soma == n else "  (diferença: confira)"))


if __name__ == "__main__":
    main()
