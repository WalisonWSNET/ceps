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
NUM_PALAVRAS = set(base.NUMEROS) | set(base.DEZENAS.values()) | {"cem", "cento", "mil"}
CAMPOS = ["id_cliente_servico", "codigo_cliente", "endereco", "numero", "bairro", "cidade", "status_atual", "rua_usada",
          "complemento_extraido", "cep_proposto", "rua_correios", "bairro_correios", "distancia_km", "metodo", "confianca",
          "motivo", "vizinhos_n", "vizinhos_cep", "alternativas"]
CONFIAVEIS_VIZ = {"exato", "exato_por_bairro", "exato_por_numero", "exato_por_faixa", "exato_por_coordenada", "aprox_exato",
                  "aprox_exato_por_numero", "aprox_exato_por_bairro", "exato_por_geografia", "conferido_manual"}
GENERICOS = {"11700000", "11730000", "11740000", "11750000"}
RAIO_VIZ_KM = 0.25


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


def palavras_compativeis(a, b, minimo=0.8):
    """Duas ruas "parecidas" só valem se TODA palavra de um lado tem uma palavra parecida do outro (só grafia).
    Evita "Josefa Maria da Conceição" x "Josefa Salvina da Conceição" (nome do meio diferente)."""
    ta, tb = a.split(), b.split()

    def par(t, u):  # grafia parecida, ou abreviação ("gov" = "governador", "eng" = "engenheiro")
        return difflib.SequenceMatcher(None, t, u).ratio() >= minimo or (min(len(t), len(u)) >= 2 and (u.startswith(t) or t.startswith(u)))

    def cobre(x, y):
        return all(any(par(t, u) for u in y) for t in x)

    return cobre(ta, tb) and cobre(tb, ta)


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
            if palavras_compativeis(base.fonetica(k), base.fonetica(key)):
                add("parecida", key)
    numerada = any(t in NUM_PALAVRAS for t in k.split())  # "Treze" não pode virar "Três": rua numerada só casa exata/por som
    if len(k) >= 6 and not numerada:
        tk = set(k.split())  # a chave tem as palavras em ordem alfabética: compara por CONJUNTO de palavras, não por texto
        for key in idx.get(cidade, {}):
            tl = set(key.split())
            if len(key) < 6 or tk == tl:
                continue
            if tl < tk and len(tl) >= 2:
                add("contida_maior", key)  # o cadastro tem a rua da lista + palavras a mais (ex.: "Walter Apelian Barbosa" x "Walter Apelian")
            elif tk < tl and (len(tk) >= 2 or len(k) >= 8):
                add("contida", key)  # o cadastro tem só PARTE do nome da lista ("Av. Francisco"): nunca passa de baixa
    if numerada:
        achados = [(m, key) for m, key in achados if m in ("exata", "fonetica")]
    return achados


def indice_vizinhos(linhas):
    """(cidade, rua por som) -> [(coordenada, cep)] dos serviços JÁ resolvidos (status confiável, CEP de rua)."""
    res = defaultdict(list)
    for r in linhas:
        cep = so_digitos(r.get("cep_novo"))
        if (r.get("status_cep") or "").strip() in CONFIAVEIS_VIZ and len(cep) == 8 and cep not in GENERICOS:
            c = geo.coordenada(r.get("coordenadas"))
            if c:
                rua, _ = base.extrair_complemento(r.get("endereco", ""))
                res[((r.get("cidade") or "").strip().upper(), base.fonetica(base.chave(base.separa_tipo(rua)[1])))].append((c, cep))
    return res


def vizinhos(linha, idx_viz):
    """CEPs dos clientes já resolvidos na MESMA rua e a até RAIO_VIZ_KM do cliente. Devolve (n, {cep: n})."""
    c = geo.coordenada(linha.get("coordenadas"))
    if not c:
        return 0, Counter()
    rua, _ = base.extrair_complemento(linha.get("endereco", ""))
    chave = ((linha.get("cidade") or "").strip().upper(), base.fonetica(base.chave(base.separa_tipo(rua)[1])))
    perto = [cep for (cc, cep) in idx_viz.get(chave, []) if geo.km(c, cc) <= RAIO_VIZ_KM]
    return len(perto), Counter(perto)


def avaliar(linha, idx, fon, mapa):
    cidade = linha["cidade"].strip().upper()
    rua, compl = base.extrair_complemento(linha["endereco"])
    tipo_cli, nome = base.separa_tipo(rua)
    n = base.numero(linha.get("numero"))
    c = geo.coordenada(linha.get("coordenadas"))
    ruas = candidatos_rua(idx, fon, cidade, nome)
    saida = dict(rua_usada=rua, complemento_extraido=compl)
    if not ruas:
        return dict(saida, confianca="", motivo="sem_rua_parecida_na_lista")
    ordem = {"exata": 0, "fonetica": 1, "parecida": 2, "contida_maior": 3, "contida": 4}
    opcoes = {}  # cep -> dict
    for metodo, key in ruas:
        for e in idx[cidade][key]:
            cabe = base.serve(e, n)
            ctr, _ = mapa.centro(cidade, e["bairro_txt"])
            dist = geo.km(c, ctr) if (c and ctr) else None
            q = re.search(r"\(([^)]*)", e["txt"].split("; ", 1)[-1])
            qual = q.group(1) if q else ""  # "Nove (Baln Tonhão)": o que vem entre parênteses diz de qual bairro é esta rua
            qual_igual = bool(qual) and bairro_igual(linha.get("bairro"), qual)
            mesmo_bairro = bairro_igual(linha.get("bairro"), e["bairro_txt"]) or qual_igual
            qual_ok = (not qual) or qual_igual or bairro_igual(linha.get("bairro"), e["bairro_txt"])
            tipo_difere = bool(tipo_cli and e.get("tipo") and tipo_cli != e["tipo"])  # Av. Sorocabana x Rua Sorocabana
            o = dict(cep=e["cep"], rua=key, bairro=e["bairro_txt"], dist=dist, cabe=cabe, mesmo_bairro=mesmo_bairro,
                     metodo=metodo, txt=e["txt"], qual_ok=qual_ok, tipo_difere=tipo_difere)
            atual = opcoes.get(e["cep"])
            if atual is None or ordem[metodo] < ordem[atual["metodo"]]:
                opcoes[e["cep"]] = o
    todas = list(opcoes.values())
    alt = " | ".join(f"{o['cep']} ({o['bairro']}; {'?' if o['dist'] is None else round(o['dist'], 1)} km; "
                     f"{'número cabe' if o['cabe'] else 'número fora'})" for o in sorted(todas, key=lambda x: (x["dist"] is None, x["dist"] or 0))[:6])
    saida["alternativas"] = alt
    servem = [o for o in todas if o["cabe"]]
    if not servem:  # (a lista foi corrigida: um número antes "fora da faixa" pode agora caber numa linha que estava colada no PDF)
        servem = todas  # número fora de qualquer faixa: só consulta (baixa)
        numero_ok = False
    else:
        numero_ok = True
    saida["numero_na_faixa"] = numero_ok
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
    if o["dist"] is not None and o["dist"] > 3:
        # o bairro do cadastro bate com este CEP, mas a COORDENADA do cliente fica longe dele: se outro CEP da mesma rua que serve o número
        # fica bem perto da coordenada, é ele (cadastro com bairro errado, ex.: Av. Mário Covas 6380 "Tupi" que na verdade é Jamaica)
        perto = sorted([x for x in servem if x["dist"] is not None and x["dist"] <= 1.5], key=lambda x: x["dist"])
        if len(perto) >= 1 and (len(perto) == 1 or perto[1]["dist"] - perto[0]["dist"] >= 0.4):
            o = dict(perto[0], coord_corrige=True)
        else:
            saida["motivo_extra"] = "coordenada_longe_do_bairro"
    vizinho = o["dist"] is not None and o["dist"] <= 3
    coord_ok = o["dist"] is None or o["dist"] <= 3  # bairro igual, mas a coordenada do cliente não pode estar longe dele
    if o.get("coord_corrige"):
        conf = "media"  # a coordenada aponta outro bairro: confirmar no Hub antes de aplicar
    elif numero_ok and o["metodo"] in ("exata", "fonetica") and o["mesmo_bairro"] and coord_ok:
        conf = "alta"      # a única que sai direto: rua igual (ou igual por som) E bairro igual E número na faixa
    elif numero_ok and o["metodo"] in ("exata", "fonetica", "parecida") and len({x["cep"] for x in servem}) == 1 and o["dist"] is None:
        conf = "media"     # a lista não diz o bairro deste CEP (linha colada no PDF), mas só ESTE CEP serve a rua e o número
    elif numero_ok and (o["mesmo_bairro"] or vizinho) and not (o["dist"] is not None and o["dist"] > 3):
        conf = "media"     # nome parecido, ou só o bairro vizinho: validar por amostra
    else:
        conf = "baixa"
    if o["metodo"] == "contida_maior" and not o["mesmo_bairro"]:
        conf = "baixa"  # nome do cadastro com palavras a mais: só é média se o bairro for o MESMO da lista
    if o["metodo"] == "contida":
        conf = "baixa"  # nome PARCIAL (ex.: "Av. Francisco" x "Francisco Antônio Mendes Caruso"): só vira média se os vizinhos confirmarem
    if not o["qual_ok"]:
        conf = "baixa"  # a lista tem várias ruas com esse nome e esta é de outro bairro (o qualificador não bate com o cliente)
    elif o["tipo_difere"] and conf == "alta":
        conf = "media"  # o tipo da via (rua/avenida/travessa) do cadastro não é o da lista: conferir no Hub
    motivo = f"rua_{o['metodo']}" + ("+bairro_igual" if o["mesmo_bairro"] else "+bairro_vizinho" if vizinho else "") \
        + ("" if numero_ok else "+numero_fora_da_faixa") + ("+complemento_separado" if compl else "") \
        + ("+coordenada_corrige_bairro" if o.get("coord_corrige") else "") + ("" if o["qual_ok"] else "+rua_homonima_de_outro_bairro") + ("+tipo_da_via_difere" if o["tipo_difere"] else "")
    return dict(saida, cep_proposto=o["cep"], rua_correios=o["txt"].split("; ", 1)[-1].rstrip(")"), bairro_correios=o["bairro"],
                distancia_km="" if o["dist"] is None else round(o["dist"], 1), metodo=o["metodo"], confianca=conf, motivo=motivo)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv")
    ap.add_argument("correios", nargs="?", default="data/ceps_logradouros.csv")
    ap.add_argument("--saida", default="logs/sugestoes")
    ap.add_argument("--incluir-cep-de-rua", action="store_true",
                    help="analisa também as linhas em que o Hub já tem um CEP de rua (por padrão ficam de fora)")
    args = ap.parse_args()

    with open(args.csv, encoding="utf-8-sig", newline="") as f:
        amostra = f.readline()
        f.seek(0)
        delim = ";" if amostra.count(";") > amostra.count(",") else ","
        linhas = list(csv.DictReader(f, delimiter=delim))
    idx = base.carrega_ref(args.correios)
    fon = montar_indices(idx)
    mapa = geo.MapaBairros(geo.centros_dos_bairros(linhas))
    idx_viz = indice_vizinhos(linhas)
    por_cep = defaultdict(list)
    for _cid in idx.values():
        for _es in _cid.values():
            for _e in _es:
                por_cep[_e["cep"]].append(_e)
    saida, cont = [], Counter()
    ja_tem_cep_de_rua = 0
    for r in linhas:
        st = (r.get("status_cep") or "").strip()
        if st not in STATUS_ALVO or (r.get("feito") or "").strip().upper() in geo.VERDADEIRO:
            continue
        if (r.get("cidade") or "").strip().upper() not in base.CIDADES:
            continue
        if not args.incluir_cep_de_rua and so_digitos(r.get("cep")) not in GENERICOS:
            ja_tem_cep_de_rua += 1  # o Hub já tem um CEP de rua (não genérico): não há o que sugerir, e trocar seria arriscado
            continue
        d = avaliar(dict(r, status_atual=st), idx, fon, mapa)
        nviz, ceps_viz = vizinhos(r, idx_viz)
        if nviz >= 2 and len(ceps_viz) == 1:  # os vizinhos já resolvidos, na mesma rua e perto, concordam em UM CEP
            cep_viz = next(iter(ceps_viz))
            motivo_d = d.get("motivo") or ""
            risco = any(m in motivo_d for m in ("numero_fora_da_faixa", "homonima", "tipo_da_via_difere"))
            if d.get("cep_proposto") == cep_viz and d.get("confianca") == "media" and not risco and d.get("metodo") in ("exata", "fonetica"):
                d["confianca"], d["motivo"] = "alta", motivo_d + "+vizinhos_confirmam"
            elif d.get("cep_proposto") == cep_viz and d.get("confianca") in ("media", "baixa") and "numero_fora_da_faixa" not in motivo_d:
                d["confianca"], d["motivo"] = "media", motivo_d + "+vizinhos_confirmam"  # os vizinhos ajudam, mas há um risco: só media
            elif d.get("numero_na_faixa") is False:
                pass  # a lista não tem nenhuma faixa que sirva este número: os vizinhos (de outras numerações) não provam nada
            elif not d.get("cep_proposto") or d.get("confianca") == "baixa":
                d.update(cep_proposto=cep_viz, metodo="vizinhos", confianca="media", motivo=f"vizinhos_mesma_rua({nviz})",
                         bairro_correios="", rua_correios="(CEP dos vizinhos já resolvidos)")
            elif d.get("cep_proposto") != cep_viz:
                n_cli = base.numero(r.get("numero"))
                viz_serve = any(base.serve(e, n_cli) for e in por_cep.get(cep_viz, []))
                if not viz_serve:
                    pass  # o CEP dos vizinhos NÃO serve este número (fim de uma faixa): a lista, e não os vizinhos, decide
                else:
                    d["confianca"], d["motivo"] = "baixa", (d.get("motivo") or "") + "+vizinhos_discordam"
        elif nviz >= 2 and d.get("cep_proposto") and d["cep_proposto"] not in ceps_viz:
            d["confianca"], d["motivo"] = "baixa", (d.get("motivo") or "") + "+vizinhos_divergem_entre_si"
        row = {k: "" for k in CAMPOS}
        row.update(id_cliente_servico=so_digitos(r.get("id_cliente_servico")), codigo_cliente=r.get("codigo_cliente", ""),
                   endereco=r.get("endereco", ""), numero=r.get("numero", ""), bairro=r.get("bairro", ""),
                   cidade=r.get("cidade", ""), status_atual=st)
        row.update({k: v for k, v in d.items() if k in CAMPOS})
        row["vizinhos_n"] = nviz or ""
        row["vizinhos_cep"] = " | ".join(f"{c} ({n})" for c, n in ceps_viz.most_common(3))
        saida.append(row)
        cont[(st, row["confianca"] or "sem_sugestao")] += 1
    Path(args.saida).mkdir(parents=True, exist_ok=True)
    caminho = Path(args.saida) / "sugestoes.csv"
    with open(caminho, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, CAMPOS)
        w.writeheader()
        w.writerows(saida)
    print(f"{len(saida)} linhas analisadas -> {caminho}")
    if ja_tem_cep_de_rua:
        print(f"({ja_tem_cep_de_rua} linhas não resolvidas ficaram de fora porque o Hub já tem CEP de rua; use --incluir-cep-de-rua para vê-las)")
    print()
    for st in STATUS_ALVO:
        partes = ", ".join(f"{c}: {n}" for (s, c), n in sorted(cont.items()) if s == st)
        print(f"  {st}: {partes}")
    total = Counter(r["confianca"] or "sem_sugestao" for r in saida)
    print("\nTotal por confiança:", dict(total))
    comp = sum(1 for r in saida if r["complemento_extraido"])
    print(f"Com complemento escrito junto com a rua: {comp}")


if __name__ == "__main__":
    main()
