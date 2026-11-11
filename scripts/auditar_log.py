"""Auditoria (só leitura) do log do robô: procura serviços já gravados (`ok`) em que o campo da RUA, antes da troca, trazia um
complemento escrito junto ("COND. X - APTO 5- AV. Y", "RUA Z CASA 2", "... PRÓX. AO MERCADO") que NÃO aparece no Complemento
nem na Referência depois. O Hub reescreve o campo da rua quando o CEP muda, então esse texto pode ter se perdido.

Uso:
  python scripts/auditar_log.py [logs/log_ceps.csv] [--saida logs/auditoria_complemento.csv]

Gera o CSV com: id_cliente_servico, codigo_cliente, endereco_antes, texto_possivelmente_perdido, complemento_depois,
referencia_depois, situacao (perdido / presente). Restaure à mão, no Hub, só os 'perdido' (você tem o texto original na coluna
endereco_antes).
"""
import argparse
import csv
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import atualizar_ceps as base  # noqa: E402

MARCAS = re.compile(r"\b(COND\w*|BLOCO|BL\.?\s?\w|APTO?\.?|AP\.?\s?\d+|CASA\s?\d*|FUNDOS|QUADRA|QD\.?\s?\w+|LOTE|LT\.?\s?\d+|"
                    r"PR[OÓ]X\w*|ESQ\w*|AO LADO|EM FRENTE|SOBRADO|LOJA|SALA)\b", re.I)


def texto_extra(rua):
    """O que sobra do campo da rua além da própria rua: o complemento separado por hífen, ou o trecho a partir da marca."""
    _, comp = base.extrair_complemento(rua)
    if comp:
        return comp
    m = MARCAS.search(rua or "")
    return (rua[m.start():].strip(" -,.") if m else "")


def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", base.sem_acento((s or "").lower())).strip()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log", nargs="?", default="logs/log_ceps.csv")
    ap.add_argument("--saida", default="logs/auditoria_complemento.csv")
    args = ap.parse_args()

    ultimo = {}
    with open(args.log, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            if r["status"] == "ok":
                ultimo[r["id_cliente_servico"]] = r  # a última gravação ok de cada serviço
    saida, n_com_extra = [], 0
    for idcs, r in ultimo.items():
        try:
            det = json.loads(r["detalhes"] or "{}")
        except ValueError:
            continue
        antes, final = det.get("antes") or {}, det.get("final") or {}
        rua = antes.get("Endereço", "")
        extra = texto_extra(rua)
        if not extra:
            continue
        n_com_extra += 1
        comp, ref = final.get("Complemento", ""), final.get("Referência", "")
        alvo = norm(extra)
        presente = bool(alvo) and (alvo in norm(comp) or alvo in norm(ref))
        saida.append(dict(id_cliente_servico=idcs, codigo_cliente=r["codigo_cliente"], endereco_antes=rua,
                          texto_possivelmente_perdido=extra, complemento_depois=comp, referencia_depois=ref,
                          situacao="presente" if presente else "perdido"))
    Path(args.saida).parent.mkdir(parents=True, exist_ok=True)
    campos = ["id_cliente_servico", "codigo_cliente", "endereco_antes", "texto_possivelmente_perdido", "complemento_depois",
              "referencia_depois", "situacao"]
    with open(args.saida, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, campos)
        w.writeheader()
        w.writerows(sorted(saida, key=lambda x: x["situacao"]))
    perdidos = sum(1 for x in saida if x["situacao"] == "perdido")
    print(f"Serviços `ok` analisados: {len(ultimo)}")
    print(f"Com texto extra no campo da rua antes da troca: {n_com_extra}")
    print(f"  presente no Complemento/Referência depois: {n_com_extra - perdidos}")
    print(f"  possivelmente perdido: {perdidos}   -> {args.saida}")


if __name__ == "__main__":
    main()
