"""Sugere CEP para as linhas que o casamento exato não resolveu (nao_encontrado, numero_fora_da_faixa, ambiguo).
Só LÊ o CSV exportado e a lista dos Correios; não usa o Hub nem a planilha.

Busca flexível: separa complemento escrito junto com a rua ("COND. X - APTO 5- AV. Y"), ignora o tipo da via errado/abreviado,
compara a escrita por som (ss = ç, y = i, z = s...), aceita nome parecido, e confere bairro (igual ou vizinho, pela coordenada)
e faixa de numeração.

Uso:
  python scripts/sugerir_ceps.py data/clientes_hoje.csv [data/ceps_logradouros.csv] [--saida logs/sugestoes]

Gera logs/sugestoes/sugestoes.csv (uma linha por serviço) e imprime o resumo por confiança:
  alta   = um único CEP; rua igual ou igual por som; número cabe na faixa; bairro igual ou a até 1,5 km do cliente
  media  = um único CEP; nome parecido/contido, ou bairro vizinho (até 3 km)
  baixa  = vários CEPs possíveis, bairro longe ou número fora da faixa (só para consulta)
Valide uma amostra (alta e media) no Hub e depois use aplicar_sugestoes.py.
"""
import argparse
import csv
import difflib
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import atualizar_ceps as base  # noqa: E402
import refinar_ceps_geografia as geo  # noqa: E402

STATUS_ALVO = ("nao_encontrado", "numero_fora_da_faixa", "ambiguo")
ENFEITES = {"balneario", "praia", "interior"}
CAMPOS = ["id_cliente_servico", "codigo_cliente", "endereco", "numero", "bairro", "cidade", "status_atual", "rua_usada",
          "complemento_extraido", "cep_proposto", "rua_correios", "bairro_correios", "distancia_km", "metodo", "confianca",
          "motivo", "alternativas"]


def so_digitos(s):
    return re.sub(r"\D", "", s or "")


def _tok_bairro(s):
    return {base.fonetica(t) for t in (base.tokens_bairro(s or "") - ENFEITES)}  # Tupi = Tupy, etc.


def bairro_igual(a, b):
    ta, tb = _tok_bairro(a), _tok_bairro(b)
    if not (ta and tb):
        return False
    return ta <= tb or tb <= ta or len(ta & tb) / len(ta | tb) >= 0.6


def montar_indices(idx):
    """por cidade: chave -> entradas; fonética -> chaves."""
    fon = {}
    for cid, d in idx.items():
        fon[cid] = defaultdict(list)
        for k in d:
            fon[cid][base.fonetica(k)].append(k)
    return fon


def candidatos_rua(idx, fon, cidade, nome):
    """[(metodo, chave_lista)] das ruas da lista que podem ser esta rua."""
    k = base.chave(nome)
    achados, vistos = [], set()

    def add(m, key):
        if key not in vistos:
            vistos.add(key)
            achados.append((m, key))

    if k in idx.get(cidade, {}):
        add("exata", k)
    for key in fon.get(cidade, {}).get(base.fonetica(k), []):
        add("fonetica", key)
    fk = base.fonetica(k)
    chaves_f = list(fon.get(cidade, {}).keys())
    for pf in difflib.get_close_matches(fk, chaves_f, n=3, cutoff=0.86):
        for key in fon[cidade][pf]:
            add("parecida", key)
    if len(k) >= 6:
        for key in idx.get(cidade, {}):
            if len(key) >= 6 and (k in key or key in k):
                add("contida", key)
    return achados


def avaliar(linha, idx, fon, mapa):
    cidade = linha["cidade"].strip().upper()
    rua, compl = base.extrair_complemento(linha["endereco"])
    _, nome = base.separa_tipo(rua)
    n = base.numero(linha.get("numero"))
    c = geo.coordenada(linha.get("coordenadas"))
    ruas = candidatos_rua(idx, fon, cidade, nome)
    saida = dict(rua_usada=rua, complemento_extraido=compl)
    if not ruas:
        return dict(saida, confianca="", motivo="sem_rua_parecida_na_lista")
    ordem = {"exata": 0, "fonetica": 1, "parecida": 2, "contida": 3}
    opcoes = {}  # cep -> dict
    for metodo, key in ruas:
        for e in idx[cidade][key]:
            cabe = base.serve(e, n)
            ctr, _ = mapa.centro(cidade, e["bairro_txt"])
            dist = geo.km(c, ctr) if (c and ctr) else None
            mesmo_bairro = bairro_igual(linha.get("bairro"), e["bairro_txt"])
            o = dict(cep=e["cep"], rua=key, bairro=e["bairro_txt"], dist=dist, cabe=cabe, mesmo_bairro=mesmo_bairro,
                     metodo=metodo, txt=e["txt"])
            atual = opcoes.get(e["cep"])
            if atual is None or ordem[metodo] < ordem[atual["metodo"]]:
                opcoes[e["cep"]] = o
    todas = list(opcoes.values())
    alt = " | ".join(f"{o['cep']} ({o['bairro']}; {'?' if o['dist'] is None else round(o['dist'], 1)} km; "
                     f"{'número cabe' if o['cabe'] else 'número fora'})" for o in sorted(todas, key=lambda x: (x["dist"] is None, x["dist"] or 0))[:6])
    saida["alternativas"] = alt
    servem = [o for o in todas if o["cabe"]]
    if linha["status_atual"] == "numero_fora_da_faixa" or not servem:
        servem = todas  # número fora de qualquer faixa: só consulta (baixa)
        numero_ok = False
    else:
        numero_ok = True
    # prefere o CEP com bairro igual; senão o mais perto
    bons = [o for o in servem if o["mesmo_bairro"]] or sorted(
        [o for o in servem if o["dist"] is not None and o["dist"] <= 3], key=lambda x: x["dist"])[:1]
    if len({o["cep"] for o in bons}) != 1:
        if len({o["cep"] for o in servem}) == 1 and not bons:
            bons = servem[:1]
        else:
            motivo = "varios_ceps_possiveis" if len(bons) > 1 else "bairro_longe_ou_sem_coordenada"
            return dict(saida, confianca="baixa", motivo=motivo)
    o = bons[0]
    vizinho = o["dist"] is not None and o["dist"] <= 3
    coord_ok = o["dist"] is None or o["dist"] <= 3  # bairro igual, mas a coordenada do cliente não pode estar longe dele
    if numero_ok and o["metodo"] in ("exata", "fonetica") and o["mesmo_bairro"] and coord_ok:
        conf = "alta"      # a única que sai direto: rua igual (ou igual por som) E bairro igual E número na faixa
    elif numero_ok and (o["mesmo_bairro"] or vizinho):
        conf = "media"     # nome parecido, ou só o bairro vizinho, ou a coordenada discorda: validar por amostra
    else:
        conf = "baixa"
    motivo = f"rua_{o['metodo']}" + ("+bairro_igual" if o["mesmo_bairro"] else "+bairro_vizinho" if vizinho else "") \
        + ("" if numero_ok else "+numero_fora_da_faixa") + ("+complemento_separado" if compl else "")
    return dict(saida, cep_proposto=o["cep"], rua_correios=o["txt"].split("; ", 1)[-1].rstrip(")"), bairro_correios=o["bairro"],
                distancia_km="" if o["dist"] is None else round(o["dist"], 1), metodo=o["metodo"], confianca=conf, motivo=motivo)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv")
    ap.add_argument("correios", nargs="?", default="data/ceps_logradouros.csv")
    ap.add_argument("--saida", default="logs/sugestoes")
    args = ap.parse_args()

    with open(args.csv, encoding="utf-8-sig", newline="") as f:
        amostra = f.readline()
        f.seek(0)
        delim = ";" if amostra.count(";") > amostra.count(",") else ","
        linhas = list(csv.DictReader(f, delimiter=delim))
    idx = base.carrega_ref(args.correios)
    fon = montar_indices(idx)
    mapa = geo.MapaBairros(geo.centros_dos_bairros(linhas))
    saida, cont = [], Counter()
    for r in linhas:
        st = (r.get("status_cep") or "").strip()
        if st not in STATUS_ALVO or (r.get("feito") or "").strip().upper() in geo.VERDADEIRO:
            continue
        if (r.get("cidade") or "").strip().upper() not in base.CIDADES:
            continue
        d = avaliar(dict(r, status_atual=st), idx, fon, mapa)
        row = {k: "" for k in CAMPOS}
        row.update(id_cliente_servico=so_digitos(r.get("id_cliente_servico")), codigo_cliente=r.get("codigo_cliente", ""),
                   endereco=r.get("endereco", ""), numero=r.get("numero", ""), bairro=r.get("bairro", ""),
                   cidade=r.get("cidade", ""), status_atual=st)
        row.update({k: v for k, v in d.items() if k in CAMPOS})
        saida.append(row)
        cont[(st, row["confianca"] or "sem_sugestao")] += 1
    Path(args.saida).mkdir(parents=True, exist_ok=True)
    caminho = Path(args.saida) / "sugestoes.csv"
    with open(caminho, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, CAMPOS)
        w.writeheader()
        w.writerows(saida)
    print(f"{len(saida)} linhas analisadas -> {caminho}\n")
    for st in STATUS_ALVO:
        partes = ", ".join(f"{c}: {n}" for (s, c), n in sorted(cont.items()) if s == st)
        print(f"  {st}: {partes}")
    total = Counter(r["confianca"] or "sem_sugestao" for r in saida)
    print("\nTotal por confiança:", dict(total))
    comp = sum(1 for r in saida if r["complemento_extraido"])
    print(f"Com complemento escrito junto com a rua: {comp}")


if __name__ == "__main__":
    main()
