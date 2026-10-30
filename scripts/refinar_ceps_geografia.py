"""Refina o cep_novo usando a COORDENADA de cada cliente (offline: não toca no Hub nem na planilha).

Quando a mesma rua existe em mais de um bairro (ex.: "Rua Itália" no Jd Coronel e em Bopiranga), escolhe o CEP
cujo bairro fica mais perto de onde o cliente está. Se só existe um candidato, confere se o bairro dele fica perto
do cliente. Linhas marcadas em 'feito' ou com status 'conferido_manual' NÃO são mexidas.

Uso:
  python scripts/refinar_ceps_geografia.py data/clientes.csv data/ceps_logradouros.csv data/clientes_refinado.csv
"""
import csv
import difflib
import math
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import atualizar_ceps as base  # noqa: E402

PERTO_KM = 3.0      # o bairro do CEP precisa ficar a até X km do cliente
MAX_ALTERAR_KM = 1.5  # só TROCA/RESOLVE sozinho se o bairro do CEP novo ficar a até X km do cliente; acima disso vai p/ revisão
MARGEM_KM = 0.4     # o melhor candidato precisa ser ao menos X km mais perto que o segundo
VERDADEIRO = {"TRUE", "VERDADEIRO", "SIM", "1", "X"}
CONFIAVEIS = {"exato", "exato_por_bairro", "exato_por_numero", "exato_por_faixa", "exato_por_coordenada",
              "aprox_exato", "aprox_exato_por_numero", "aprox_exato_por_bairro", "conferido_manual",
              "exato_por_geografia"}
EXTRA_ABREV = {"sra": "senhora", "sto": "santo", "n": "nova"}


def km(a, b):
    return math.hypot((a[0] - b[0]) * 111.0, (a[1] - b[1]) * 111.0 * math.cos(math.radians(24.2)))


def coordenada(s):
    try:
        la, lo = (float(x) for x in (s or "").strip('"').split(","))
    except ValueError:
        return None
    return (la, lo) if -25 < la < -23.5 and -47.5 < lo < -46.3 else None


def tokens_b(s):
    return {EXTRA_ABREV.get(t, t) for t in base.tokens_bairro(s)}


def jaccard(a, b):
    return len(a & b) / len(a | b) if a | b else 0.0


def centros_dos_bairros(linhas):
    """{(cidade, bairro_hub): (lat, lon)} pela mediana das coordenadas dos clientes (3+ por bairro)."""
    pts = defaultdict(list)
    for r in linhas:
        c = coordenada(r.get("coordenadas"))
        if c:
            pts[(r["cidade"].strip().upper(), r["bairro"].strip().upper())].append(c)
    return {k: (statistics.median(p[0] for p in v), statistics.median(p[1] for p in v))
            for k, v in pts.items() if len(v) >= 3}


class MapaBairros:
    """Liga o nome do bairro do cadastro dos Correios ('Jd Coronel') ao bairro do Hub ('JARDIM CORONEL')."""

    def __init__(self, centros):
        self.centros = centros
        self.por_cidade = defaultdict(list)
        for (cidade, bairro) in centros:
            self.por_cidade[cidade].append((bairro, tokens_b(bairro)))
        self.cache, self.sem_par = {}, set()

    def centro(self, cidade, bairro_correios):
        k = (cidade, bairro_correios)
        if k not in self.cache:
            t = tokens_b(bairro_correios)
            melhor = max(((jaccard(t, tb), nome) for nome, tb in self.por_cidade[cidade]), default=(0, None))
            if melhor[0] >= 0.5:
                self.cache[k] = (self.centros[(cidade, melhor[1])], melhor[1])
            else:
                self.cache[k] = (None, None)
                self.sem_par.add(k)
        return self.cache[k]


def candidatos(idx, cidade, endereco, numero):
    """Entradas da lista dos Correios que servem para a rua e o número do cliente."""
    tipo, nome = base.separa_tipo(endereco)
    k = base.chave(nome)
    cands = idx.get(cidade, {}).get(k)
    if not cands:
        p = difflib.get_close_matches(k, list(idx.get(cidade, {}).keys()), n=1, cutoff=0.88)
        cands = idx[cidade][p[0]] if p else []
    if tipo:
        mesmo = [e for e in cands if e["tipo"] == tipo]
        cands = mesmo or cands
    n = base.numero(numero)
    return [e for e in cands if base.serve(e, n)]


def decidir(linha, cands, mapa, cidade):
    """Devolve (cep_sugerido, status, distancia_km, bairro_do_cep, alternativas, motivo)."""
    c = coordenada(linha.get("coordenadas"))
    por_cep = defaultdict(list)
    for e in cands:
        por_cep[e["cep"]].append(e)
    if not por_cep:
        return None, "", None, "", "", "sem_candidato_na_lista"
    infos = []
    for cep, es in por_cep.items():
        centro, bairro_hub = mapa.centro(cidade, es[0]["bairro_txt"])
        d = km(c, centro) if (c and centro) else None
        infos.append(dict(cep=cep, bairro=es[0]["bairro_txt"], bairro_hub=bairro_hub, d=d,
                          jacc=jaccard(tokens_b(linha.get("bairro", "")), tokens_b(es[0]["bairro_txt"]))))
    alternativas = " | ".join(f"{i['cep']} ({i['bairro']}; {'?' if i['d'] is None else round(i['d'], 1)} km)"
                              for i in sorted(infos, key=lambda x: (x["d"] is None, x["d"] or 0)))
    if len(infos) == 1:
        i = infos[0]
        if i["d"] is None:
            return i["cep"], "exato_por_geografia", None, i["bairro"], alternativas, "unico_candidato_sem_coordenada"
        if i["d"] <= PERTO_KM:
            return i["cep"], "exato_por_geografia", i["d"], i["bairro"], alternativas, "unico_candidato_perto"
        return None, "", i["d"], i["bairro"], alternativas, f"unico_candidato_longe_{i['d']:.1f}km"
    # vários CEPs: se o bairro cadastrado do cliente é o de algum candidato, vale esse bairro (a coordenada só desempata
    # quando o bairro cadastrado não bate com nenhum candidato)
    por_nome = [i for i in infos if i["jacc"] >= 0.6]
    if por_nome:
        if len(por_nome) == 1:
            i = por_nome[0]
            if i["d"] is None or i["d"] <= PERTO_KM:
                return i["cep"], "exato_por_geografia", i["d"], i["bairro"], alternativas, "bairro_cadastrado_bate"
            return None, "", i["d"], i["bairro"], alternativas, "bairro_cadastrado_longe_da_coordenada"
        return None, "", min((i["d"] for i in por_nome if i["d"] is not None), default=None), por_nome[0]["bairro"], \
            alternativas, "varios_ceps_no_bairro_cadastrado"
    com_d = sorted((i for i in infos if i["d"] is not None), key=lambda x: x["d"])
    if not com_d:
        return None, "", None, "", alternativas, "sem_coordenada_para_decidir"
    melhor = com_d[0]
    segundo = com_d[1] if len(com_d) > 1 else None
    if melhor["d"] <= PERTO_KM and (segundo is None or segundo["d"] - melhor["d"] >= MARGEM_KM) and len(com_d) == len(infos):
        return melhor["cep"], "exato_por_geografia", melhor["d"], melhor["bairro"], alternativas, "mais_perto"
    return None, "", melhor["d"], melhor["bairro"], alternativas, "empate_ou_longe"


def dist_cep_atual(idx, mapa, cidade, cep, linha):
    """Distância (km) do cliente ao centro do bairro do CEP atual; None se não der para saber."""
    c = coordenada(linha.get("coordenadas"))
    if not (c and cep):
        return None
    for es in idx.get(cidade, {}).values():
        for e in es:
            if e["cep"] == cep:
                centro, _ = mapa.centro(cidade, e["bairro_txt"])
                return km(c, centro) if centro else None
    return None


def main(entrada, ref_path, saida):
    with open(entrada, encoding="utf-8-sig", newline="") as f:
        amostra = f.read(4096)
        f.seek(0)
        delim = ";" if amostra.split("\n")[0].count(";") > amostra.split("\n")[0].count(",") else ","
        linhas = list(csv.DictReader(f, delimiter=delim))
    idx = base.carrega_ref(ref_path)
    mapa = MapaBairros(centros_dos_bairros(linhas))
    saida_linhas, cont = [], Counter()
    for r in linhas:
        cidade = r["cidade"].strip().upper()
        feito = (r.get("feito") or "").strip().upper() in VERDADEIRO
        atual = "".join(ch for ch in (r.get("cep_novo") or "") if ch.isdigit())
        status_atual = (r.get("status_cep") or "").strip()
        if cidade not in base.CIDADES:
            continue
        if feito:
            acao, sug, st, dist, bco, alt, motivo = "ignorar_feito", atual, status_atual, None, "", "", ""
        elif status_atual == "conferido_manual":
            acao, sug, st, dist, bco, alt, motivo = "ignorar_manual", atual, status_atual, None, "", "", ""
        else:
            cands = candidatos(idx, cidade, r["endereco"], r.get("numero"))
            sug, st, dist, bco, alt, motivo = decidir(r, cands, mapa, cidade)
            confiavel = status_atual in CONFIAVEIS
            if sug and sug == atual:
                acao = "manter"
            elif sug and confiavel:
                # só troca um CEP "confiável" se o bairro dele estiver LONGE do cliente; se já está perto, a diferença entre
                # os dois candidatos é de faixa/trecho da mesma rua e a geografia sozinha não prova que o atual esteja errado
                d_atual = dist_cep_atual(idx, mapa, cidade, atual, r)
                if d_atual is not None and d_atual <= PERTO_KM:
                    acao, motivo = "manter", f"atual_perto_{d_atual:.1f}km"
                else:
                    acao = "trocar"
            elif sug:
                acao = "resolver"
            else:
                acao = "revisar"
            if acao in ("trocar", "resolver") and dist is not None and dist > MAX_ALTERAR_KM:
                acao, motivo = "revisar", f"sugestao_longe_{dist:.1f}km"
        cont[acao] += 1
        saida_linhas.append(dict(
            id_cliente_servico=r["id_cliente_servico"], codigo_cliente=r["codigo_cliente"], endereco=r["endereco"],
            numero=r.get("numero", ""), bairro=r["bairro"], cidade=r["cidade"], cep_antigo=r.get("cep", ""),
            cep_novo_atual=atual, status_cep_atual=status_atual, acao=acao, cep_novo_sugerido=sug or "",
            status_sugerido=st, distancia_km="" if dist is None else round(dist, 1), bairro_do_cep=bco,
            motivo=motivo, alternativas=alt))
    campos = list(saida_linhas[0].keys())
    with open(saida, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, campos)
        w.writeheader()
        w.writerows(saida_linhas)
    print("Ações sugeridas:")
    for a, n in cont.most_common():
        print(f"  {a}: {n}")
    motivos = Counter(r["motivo"] for r in saida_linhas if r["acao"] == "revisar")
    print("Motivos dos que ficaram para revisar:")
    for m, n in motivos.most_common(8):
        print(f"  {m}: {n}")
    if mapa.sem_par:
        print(f"Bairros dos Correios sem par no Hub ({len(mapa.sem_par)}), ex.: {sorted(mapa.sem_par)[:8]}")


if __name__ == "__main__":
    main(*sys.argv[1:4])
