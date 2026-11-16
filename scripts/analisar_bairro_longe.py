"""Analisa os serviços que o robô pulou por `bairro_longe` (o bairro do CEP novo ficou a mais de 3 km do bairro do cliente no Hub, ou o
robô não conhece um dos bairros). Offline: só lê o log do robô e o CSV da planilha; não usa o Hub e não escreve na planilha.

Ideia: o `bairro_longe` costuma ser o bairro do Hub vago ou com outro nome, e não um CEP errado. Para cada serviço pulado, procura OUTROS
serviços que já receberam o MESMO CEP novo (marcados `feito`, ou concluídos pelo robô) e mede a distância entre a coordenada do cliente e a
desses vizinhos. Vizinho muito perto com o mesmo CEP = o CEP está fisicamente na área certa.

  confirmado_vizinhos   há um vizinho com o mesmo CEP a até 300 m
  provavel              há um vizinho com o mesmo CEP entre 300 m e 1 km
  vizinhos_longe        há vizinhos com o mesmo CEP, mas todos a mais de 1 km (o CEP pode estar errado para este cliente)
  sem_evidencia         nenhum outro serviço com esse CEP foi concluído ainda
  sem_coordenada        o cliente não tem coordenada

Uso:
  python scripts/analisar_bairro_longe.py data/clientes_fim14.csv
  python scripts/analisar_bairro_longe.py data/clientes_fim14.csv --ids-saida logs/ids_bairro_longe.txt --classes confirmado_vizinhos provavel
"""
import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import refinar_ceps_geografia as geo  # noqa: E402

VERDADEIRO = {"TRUE", "VERDADEIRO", "SIM", "1", "X"}


def d(s):
    return "".join(c for c in (s or "") if c.isdigit())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv")
    ap.add_argument("--log", default="logs/log_ceps.csv")
    ap.add_argument("--saida", default="logs/bairro_longe_analise.csv")
    ap.add_argument("--ids-saida", help="grava neste arquivo os id_cliente_servico das classes escolhidas em --classes (para o --ids-arquivo do robô)")
    ap.add_argument("--classes", nargs="+", default=["confirmado_vizinhos"], help="classes a gravar em --ids-saida")
    args = ap.parse_args()

    with open(args.csv, encoding="utf-8-sig", newline="") as f:
        linhas = list(csv.DictReader(f))
    por_id = {d(r["id_cliente_servico"]): r for r in linhas}
    ultimo = {}
    with open(args.log, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            ultimo[r["id_cliente_servico"]] = r
    pulados = {i: r for i, r in ultimo.items() if r["status"] == "bairro_longe" and i in por_id
               and (por_id[i].get("feito") or "").strip().upper() not in VERDADEIRO}
    # serviços que JÁ têm o CEP novo no Hub: marcados feito com cep_novo de rua, ou concluídos (ok / ja_atualizado) pelo robô
    prontos = defaultdict(list)
    for r in linhas:
        i = d(r["id_cliente_servico"])
        cep = d(r.get("cep_novo"))
        feito = (r.get("feito") or "").strip().upper() in VERDADEIRO
        if len(cep) == 8 and (feito or ultimo.get(i, {}).get("status") in ("ok", "ja_atualizado")):
            c = geo.coordenada(r.get("coordenadas"))
            if c:
                prontos[cep].append((i, c))

    saida, cont, pares = [], Counter(), defaultdict(Counter)
    for i, rl in pulados.items():
        r = por_id[i]
        try:
            det = json.loads(rl.get("detalhes") or "{}")
        except ValueError:
            det = {}
        antes, depois = (det.get("bairro") or ["", ""])[:2] if isinstance(det.get("bairro"), list) else ("", "")
        cep = d(r.get("cep_novo")) or d(rl.get("cep_novo"))
        c = geo.coordenada(r.get("coordenadas"))
        viz = []
        if c:
            viz = sorted(geo.km(c, cc) * 1000 for j, cc in prontos.get(cep, []) if j != i)
        if not c:
            classe = "sem_coordenada"
        elif not viz:
            classe = "sem_evidencia"
        elif viz[0] <= 300:
            classe = "confirmado_vizinhos"
        elif viz[0] <= 1000:
            classe = "provavel"
        else:
            classe = "vizinhos_longe"
        cont[classe] += 1
        pares[(str(antes), str(depois))][classe] += 1
        saida.append(dict(id_cliente_servico=i, codigo_cliente=r.get("codigo_cliente"), endereco=r.get("endereco"), numero=r.get("numero"),
                          bairro_hub=antes or r.get("bairro"), bairro_do_cep_novo=depois, km_entre_bairros=det.get("bairro_km"),
                          cep_novo=cep, vizinho_mais_perto_m="" if not viz else round(viz[0]), vizinhos_ate_500m=sum(1 for x in viz if x <= 500),
                          classe=classe))
    Path(args.saida).parent.mkdir(parents=True, exist_ok=True)
    with open(args.saida, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, list(saida[0].keys()) if saida else ["id_cliente_servico"])
        w.writeheader()
        w.writerows(saida)
    total = len(saida)
    print(f"Serviços pulados por bairro_longe e ainda sem `feito`: {total}\n")
    for k in ("confirmado_vizinhos", "provavel", "vizinhos_longe", "sem_evidencia", "sem_coordenada"):
        print(f"   {cont[k]:5d}  {k}")
    print("\nPares 'bairro do Hub -> bairro do CEP novo' mais frequentes (n | confirmado+provável):")
    for (a, b), cc in sorted(pares.items(), key=lambda t: -sum(t[1].values()))[:15]:
        n = sum(cc.values())
        print(f"   {n:4d} | {cc['confirmado_vizinhos'] + cc['provavel']:4d}  {a[:26]:26} -> {b[:26]}")
    print("\nExemplos para você conferir no Hub (3 por classe; codigo_cliente):")
    for k in ("confirmado_vizinhos", "provavel", "vizinhos_longe"):
        ex = [x for x in saida if x["classe"] == k][:3]
        if ex:
            print(f"   {k}: " + ", ".join(f"{x['codigo_cliente']} ({x['endereco'][:22]} {x['numero']}, {x['vizinho_mais_perto_m'] or '?'} m)" for x in ex))
    print(f"\nLista completa: {args.saida}")
    if args.ids_saida:
        ids = [x["id_cliente_servico"] for x in saida if x["classe"] in set(args.classes)]
        Path(args.ids_saida).write_text("\n".join(ids) + "\n", encoding="utf-8")
        print(f"{len(ids)} id_cliente_servico das classes {args.classes} gravados em {args.ids_saida}")


if __name__ == "__main__":
    main()
