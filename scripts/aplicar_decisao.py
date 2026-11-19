"""Lê o CSV de revisão que você preencheu (coluna `decisao`) e gera os arquivos de IDs para o robô. Não escreve na planilha.
  python scripts/aplicar_decisao.py logs/revisao_enderecos_diferentes.csv logs/revisao_cep_divergente.csv
  (funciona com CSV do Excel (;) e do Google Planilhas (,); o nome do arquivo pode ser qualquer um que contenha 'cep_divergente' ou não)
decisao:  S = trocar o CEP (enderecos_diferentes: dos 4 endereços; cep_divergente: o CEP que está no Hub pelo CEP novo)
          I = trocar só o endereço de Instalação (--so-instalacao)
          vazio / qualquer outra coisa = deixar como está
Gera logs/ids_decisao_S.txt, logs/ids_decisao_I.txt e logs/ids_decisao_cep.txt (S do arquivo cep_divergente).
"""
import csv
import sys
from pathlib import Path

S, I, C = [], [], []
for arq in sys.argv[1:]:
    texto = Path(arq).read_text(encoding="utf-8-sig")
    delim = ";" if texto.splitlines()[0].count(";") > texto.splitlines()[0].count(",") else ","
    eh_cep = "cep_divergente" in arq.lower()
    for r in csv.DictReader(texto.splitlines(), delimiter=delim):
        d = (r.get("decisao") or "").strip().upper()
        if d == "S":
            (C if eh_cep else S).append(r["id_cliente_servico"].strip())
        elif d == "I":
            I.append(r["id_cliente_servico"].strip())
for nome, lista in (("S", S), ("I", I), ("cep", C)):
    Path(f"logs/ids_decisao_{nome}.txt").write_text("\n".join(lista) + ("\n" if lista else ""), encoding="utf-8")
print(f"S (4 endereços): {len(S)} | I (só Instalação): {len(I)} | cep_divergente trocar pelo novo: {len(C)} | o resto fica como está")
