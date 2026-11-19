"""Lê o CSV de revisão que você preencheu (coluna `decisao`) e gera os arquivos de IDs para o robô. Não escreve na planilha.
  python scripts/aplicar_decisao.py logs/revisao_enderecos_diferentes.csv logs/revisao_cep_divergente.csv
decisao:  S = trocar o CEP dos 4 endereços (--so-um-endereco --preservar-enderecos)
          I = trocar só o endereço de Instalação (--so-um-endereco --so-instalacao)
          vazio / qualquer outra coisa = deixar como está
Gera logs/ids_decisao_S.txt e logs/ids_decisao_I.txt.
"""
import csv
import sys
from pathlib import Path

S, I = [], []
for arq in sys.argv[1:]:
    with open(arq, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f, delimiter=";"):
            d = (r.get("decisao") or "").strip().upper()
            (S if d == "S" else I if d == "I" else []).append(r["id_cliente_servico"])
Path("logs/ids_decisao_S.txt").write_text("\n".join(S) + ("\n" if S else ""), encoding="utf-8")
Path("logs/ids_decisao_I.txt").write_text("\n".join(I) + ("\n" if I else ""), encoding="utf-8")
print(f"S (4 endereços): {len(S)} | I (só Instalação): {len(I)} | ficam como estão: não listados")
