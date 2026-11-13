"""Quantos serviços vão ficar com o CEP genérico (os "x" e afins) e quantos terminam com CEP de rua. Só lê o CSV exportado da planilha.

Mostra, a partir do CSV mais recente:
  - serviços já resolvidos / na fila do robô (CEP de rua no Hub ou a caminho);
  - os que ficam como estão: com `x` em `escolha`, ruas encerradas, ruas sem candidatos e os que ainda faltam decidir;
  - os que o robô pulou e esperam a fase de revisão.

Uso:
  python scripts/contagem_final.py data/clientes_fim9.csv
"""
import argparse
import csv
from collections import Counter

GENERICOS = {"11700000", "11730000", "11740000", "11750000"}
NAO_RESOLVIDOS = {"ambiguo", "nao_encontrado", "numero_fora_da_faixa"}
VERDADEIRO = {"TRUE", "VERDADEIRO", "SIM", "1", "X"}


def d(s):
    return "".join(c for c in (s or "") if c.isdigit())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv")
    args = ap.parse_args()
    with open(args.csv, encoding="utf-8-sig", newline="") as f:
        linhas = list(csv.DictReader(f))
    tem_escolha = bool(linhas) and "escolha" in linhas[0]
    c = Counter()
    por_cidade = Counter()
    for r in linhas:
        c["total"] += 1
        feito = (r.get("feito") or "").strip().upper() in VERDADEIRO
        st = (r.get("status_cep") or "").strip()
        escolha = (r.get("escolha") or "").strip().lower() if tem_escolha else ""
        obs = (r.get("obs_robo") or "").lower()
        generico = d(r.get("cep_novo")) in GENERICOS
        cand = bool((r.get("ceps_candidatos") or "").strip())
        if st in NAO_RESOLVIDOS and generico:
            if "rua_encerrada" in obs:
                chave = "ficam_ruas_encerradas"
            elif escolha == "x":
                chave = "ficam_x_na_escolha"
            elif not cand:
                chave = "ficam_sem_candidatos"
            elif feito:
                chave = "ficam_feito_sem_troca"
            elif escolha == "aplicado":
                chave = "ja_aplicado"
            else:
                chave = "ainda_a_decidir"
            c[chave] += 1
            if chave.startswith("ficam") or chave == "ainda_a_decidir":
                por_cidade[(r.get("cidade") or "")[:4].upper()] += 1
        elif feito:
            c["feito_certo"] += 1
        elif st in NAO_RESOLVIDOS:
            c["nao_resolvido_com_cep_de_rua_no_hub"] += 1
        else:
            c["na_fila_do_robo_ou_pulados"] += 1
    t = c["total"]
    ficam = c["ficam_x_na_escolha"] + c["ficam_ruas_encerradas"] + c["ficam_sem_candidatos"] + c["ficam_feito_sem_troca"]
    print(f"Total de serviços: {t}\n")
    print(f"Já concluídos (feito):                                  {c['feito_certo']:6d}")
    print(f"Na fila do robô ou pulados por ele (a caminho):         {c['na_fila_do_robo_ou_pulados']:6d}")
    print(f"Não resolvidos, mas o Hub já tem CEP de rua:            {c['nao_resolvido_com_cep_de_rua_no_hub']:6d}")
    print(f"Escolhas já aplicadas na planilha (a caminho do robô):  {c['ja_aplicado']:6d}")
    print("\nFicam com o CEP GENÉRICO (decisão tomada):")
    print(f"   marcados com x em `escolha`:                         {c['ficam_x_na_escolha']:6d}")
    print(f"   ruas encerradas (sem CEP de rua nas fontes):         {c['ficam_ruas_encerradas']:6d}")
    print(f"   ruas sem nenhum candidato:                           {c['ficam_sem_candidatos']:6d}")
    print(f"   outros marcados feito sem troca de CEP:              {c['ficam_feito_sem_troca']:6d}")
    print(f"   TOTAL que ficam genéricos:                           {ficam:6d}  ({100 * ficam / t:.1f}% do total)")
    print(f"\nAinda a decidir (escolha vazia, com candidatos):        {c['ainda_a_decidir']:6d}")
    if not tem_escolha:
        print("AVISO: o CSV não tem a coluna `escolha`; os números de x ficam zerados.")
    if por_cidade:
        print("Por cidade (ficam + a decidir):", dict(por_cidade))


if __name__ == "__main__":
    main()
