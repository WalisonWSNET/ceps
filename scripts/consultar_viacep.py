"""Consulta o ViaCEP (https://viacep.com.br) só com NOME DA RUA e CIDADE (nenhum dado de cliente sai do computador) e grava os CEPs
encontrados em logs/viacep.csv, para você conferir. NÃO escreve na planilha.

Use para as ruas que não estão nos PDFs dos Correios (status nao_encontrado sem sugestão). Cada rua+cidade é consultada uma vez.

Uso:
  # teste com uma rua:
  python scripts/consultar_viacep.py --rua "Sebastiao Ferreira de Azevedo" --cidade Itanhaem
  # todas as ruas sem sugestão de logs/sugestoes/sugestoes.csv (depois de rodar sugerir_ceps.py), as mais frequentes primeiro:
  python scripts/consultar_viacep.py --sem-sugestao --limite 40
"""
import argparse
import csv
import json
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import atualizar_ceps as base  # noqa: E402

CIDADES = {"ITANHAÉM": "Itanhaem", "MONGAGUÁ": "Mongagua", "PERUÍBE": "Peruibe"}


def sem_acento(s):
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def consultar(rua, cidade):
    url = "https://viacep.com.br/ws/SP/{}/{}/json/".format(urllib.parse.quote(sem_acento(cidade)), urllib.parse.quote(sem_acento(rua)))
    req = urllib.request.Request(url, headers={"User-Agent": "consulta-de-ceps"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rua")
    ap.add_argument("--cidade", help="Itanhaem, Mongagua ou Peruibe (com ou sem acento)")
    ap.add_argument("--sem-sugestao", action="store_true", help="usa as ruas sem sugestão de logs/sugestoes/sugestoes.csv")
    ap.add_argument("--sugestoes", default="logs/sugestoes/sugestoes.csv")
    ap.add_argument("--limite", type=int, default=40, help="máximo de ruas a consultar (as mais frequentes primeiro)")
    ap.add_argument("--saida", default="logs/viacep.csv")
    args = ap.parse_args()

    pares = []
    if args.sem_sugestao:
        with open(args.sugestoes, encoding="utf-8", newline="") as f:
            linhas = [r for r in csv.DictReader(f) if not r.get("cep_proposto")]
        cont = Counter()
        for r in linhas:
            rua, _ = base.extrair_complemento(r.get("endereco", ""))
            nome = base.separa_tipo(rua)[1].strip()
            cidade = CIDADES.get((r.get("cidade") or "").strip().upper())
            if nome and cidade and len(nome) >= 4:
                cont[(nome.title(), cidade)] += 1
        pares = [(rua, cidade, n) for (rua, cidade), n in cont.most_common(args.limite)]
    elif args.rua and args.cidade:
        pares = [(args.rua, args.cidade, 0)]
    else:
        raise SystemExit("informe --rua e --cidade, ou --sem-sugestao")

    Path(args.saida).parent.mkdir(parents=True, exist_ok=True)
    achou = 0
    with open(args.saida, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["rua_consultada", "cidade", "clientes", "cep", "logradouro_viacep", "complemento", "bairro", "localidade"])
        for rua, cidade, n in pares:
            try:
                res = consultar(rua, cidade)
            except (urllib.error.URLError, TimeoutError, ValueError) as e:
                print(f"  ! {rua} ({cidade}): erro na consulta ({type(e).__name__})")
                continue
            if isinstance(res, list) and res:
                achou += 1
            print(f"  {rua} ({cidade}, {n} clientes): {len(res) if isinstance(res, list) else 0} resultado(s)")
            for e in (res if isinstance(res, list) else []):
                w.writerow([rua, cidade, n, e.get("cep", ""), e.get("logradouro", ""), e.get("complemento", ""), e.get("bairro", ""), e.get("localidade", "")])
            time.sleep(1)  # o ViaCEP limita consultas muito rápidas
    print(f"\nRuas consultadas: {len(pares)} | com algum resultado: {achou} | arquivo: {args.saida}")


if __name__ == "__main__":
    main()
