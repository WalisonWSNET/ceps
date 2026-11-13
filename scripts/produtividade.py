"""Produtividade do robô por dia, desde o início, e quanto falta (estimativa) para a fila acabar.

Lê logs/log_ceps.csv (o registro do robô) e o CSV mais recente exportado da planilha. Só lê; não usa o Hub nem escreve nada.

Por dia mostra: registros do robô, concluídos (ok + ja_atualizado), pulados de propósito (vão para revisão), erros/alarmes e o acumulado de
concluídos. No fim: quantos serviços ainda estão na fila do robô (mesma regra do "Faltam X de Y" que ele mostra ao iniciar) e uma previsão
de dias, pela média dos últimos dias de trabalho.

Uso:
  python scripts/produtividade.py data/clientes_fim9.csv                      # fila aproximada (verdes = cep_novo igual ao cep)
  python scripts/produtividade.py data/clientes_fim9.csv --planilha ID        # fila exata (lê as cores da planilha)
"""
import argparse
import csv
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import atualizar_ceps_hub as hub  # noqa: E402


def classe(status):
    if status in hub.FEITOS:
        return "concluidos"
    if status in hub.PULADOS or status.startswith(("enderecos_diferentes", "enderecos_conflito")):
        return "pulados"
    return "erros_alarmes"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", help="CSV mais recente exportado da planilha (para calcular a fila)")
    ap.add_argument("--log", default=str(hub.LOG_GRAVACAO))
    ap.add_argument("--planilha")
    ap.add_argument("--credenciais", default="credenciais_google.json")
    ap.add_argument("--aba")
    ap.add_argument("--dias-media", type=int, default=5, help="quantos dias de trabalho entram na média da previsão (padrão 5)")
    args = ap.parse_args()

    hub.LOG_GRAVACAO = Path(args.log)  # carregar_feitos/carregar_pulados leem este arquivo
    por_dia = defaultdict(Counter)
    ultimo = {}
    with open(args.log, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            dia = r["data_hora"][:10]
            por_dia[dia][classe(r["status"])] += 1
            por_dia[dia]["registros"] += 1
            ultimo[r["id_cliente_servico"]] = r["status"]
    print(f"{'dia':10} {'registros':>9} {'concluídos':>10} {'pulados':>8} {'erros':>6} {'acumulado':>10}")
    acum, uteis = 0, []
    for dia in sorted(por_dia):
        c = por_dia[dia]
        acum += c["concluidos"]
        print(f"{dia:10} {c['registros']:9d} {c['concluidos']:10d} {c['pulados']:8d} {c['erros_alarmes']:6d} {acum:10d}")
        if c["registros"] >= 400:
            uteis.append(c["registros"])
    feitos_unicos = sum(1 for st in ultimo.values() if st in hub.FEITOS)
    print(f"\nServiços diferentes concluídos pelo robô até hoje: {feitos_unicos} | registros no log: {sum(c['registros'] for c in por_dia.values())}")

    todas = hub.ler_linhas(args.csv)
    elegiveis = hub.selecionar(todas, None, "feito")
    feitos, pulados = hub.carregar_feitos(), hub.carregar_pulados()
    if args.planilha:
        verdes = hub.ids_cep_verde(args.planilha, args.credenciais, args.aba)
    else:
        verdes = {hub.so_digitos(r["id_cliente_servico"]) for r in elegiveis if hub.so_digitos(r.get("cep_novo")) == hub.so_digitos(r.get("cep"))}
    fila = [r for r in elegiveis if hub.so_digitos(r["id_cliente_servico"]) not in (feitos | pulados | verdes)]
    n_pulados = sum(1 for r in elegiveis if hub.so_digitos(r["id_cliente_servico"]) in pulados)
    print(f"\nFila do robô agora: {len(fila)} serviços (elegíveis {len(elegiveis)}; já pulados em rodadas anteriores {n_pulados}; "
          f"verdes {'da planilha' if args.planilha else 'aproximados'})")
    if uteis:
        media = sum(uteis[-args.dias_media:]) / len(uteis[-args.dias_media:])
        print(f"Média dos últimos {min(args.dias_media, len(uteis))} dias de trabalho: {media:.0f} registros por dia")
        print(f"Previsão: cerca de {len(fila) / media:.1f} dia(s) de trabalho para zerar a fila (sem contar o que você ainda acrescentar)")


if __name__ == "__main__":
    main()
