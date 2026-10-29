"""Associa cada cliente ao CEP do logradouro (Itanhaém, Mongaguá, Peruíbe).

Uso: python scripts/atualizar_ceps.py clientes.csv data/ceps_logradouros.csv saida.csv
"""
import csv
import difflib
import re
import sys
import unicodedata
from collections import Counter, defaultdict

CIDADES = {"ITANHAÉM", "MONGAGUÁ", "PERUÍBE"}
TIPOS = {
    "r": "rua", "rua": "rua", "av": "av", "avenida": "av", "al": "al", "alameda": "al",
    "tv": "tv", "trav": "tv", "travessa": "tv", "pc": "pc", "pca": "pc", "praca": "pc",
    "est": "est", "estrada": "est", "rod": "rod", "rodovia": "rod", "vla": "vla",
    "viela": "vla", "lad": "lad", "ladeira": "lad", "trv": "tv", "via": "via",
}
IGNORAR = {"de", "da", "do", "das", "dos", "e", "doutor", "dr", "dra", "padre", "pe", "prof",
           "professor", "professora", "vereador", "ver", "cap", "capitao", "general", "gal",
           "engenheiro", "eng", "major", "maj", "dom", "dona", "coronel", "cel", "sargento",
           "sgt", "tenente", "ten", "comandante", "cmte", "pref", "prefeito", "senador",
           "deputado", "dep", "monsenhor", "mons", "irma", "frei", "jornalista", "bispo"}


def sem_acento(s):
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def tokens(s):
    s = sem_acento(s.lower())
    s = re.sub(r"\([^)]*\)", " ", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return [NUMEROS[int(t)] if t.isdigit() and int(t) < len(NUMEROS) else t
            for t in s.split() if t not in IGNORAR]


NUMEROS = "zero um dois tres quatro cinco seis sete oito nove dez onze doze treze quatorze quinze dezesseis dezessete dezoito dezenove vinte".split()
ABREV_BAIRRO = {"jd": "jardim", "baln": "balneario", "etn": "estancia", "vl": "vila", "prq": "parque",
                "pq": "parque", "cid": "cidade", "cpos": "campos", "res": "residencial", "sta": "santa",
                "qta": "quinta", "chs": "chacaras", "rcr": "recreio", "lot": "loteamento", "pr": "praia",
                "gda": "guarda", "bsq": "bosque", "sit": "sitio", "cond": "condominio", "s": "sao"}


def tokens_bairro(s):
    s = sem_acento(s.lower())
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return {ABREV_BAIRRO.get(t, t) for t in s.split() if t not in IGNORAR}


def chave(s):
    return " ".join(sorted(tokens(s)))


def separa_tipo(endereco):
    t = re.sub(r"[.:]", " ", sem_acento(endereco.lower())).split()
    if t and t[0] in TIPOS:
        return TIPOS[t[0]], " ".join(t[1:])
    return None, endereco


def faixa(nome):
    """Devolve (nome_sem_faixa, lo, hi, paridade) a partir do texto do logradouro."""
    base = re.split(r"\s+-\s+(?=até|de \d|lado)", nome, maxsplit=1)
    sufixo = base[1] if len(base) > 1 else ""
    sufixo = re.split(r"\s-\s(?:r|av|al|tv|pç|est|vla)\b", sufixo)[0]
    lo, hi, par = 0, 10**9, None
    m = re.search(r"até (\d+)(?:/(\d+))?", sufixo)
    if m:
        hi = max(int(g) for g in m.groups() if g)
    m = re.search(r"de (\d+)(?:/(\d+))? ao fim", sufixo)
    if m:
        lo = min(int(g) for g in m.groups() if g)
    m = re.search(r"de (\d+)(?:/(\d+))? a (\d+)(?:/(\d+))?", sufixo)
    if m:
        nums = [int(g) for g in m.groups() if g]
        lo, hi = min(nums), max(nums)
    if "lado ímpar" in sufixo:
        par = 1
    elif "lado par" in sufixo:
        par = 0
    return base[0], lo, hi, par


def carrega_ref(path):
    ref = defaultdict(list)
    for r in csv.DictReader(open(path, encoding="utf-8")):
        nome, lo, hi, par = faixa(r["logradouro"])
        tipo = TIPOS.get(sem_acento(r["tipo"].lower()).replace(".", "").split(" ")[0])
        ref[r["cidade"].upper()].append(
            dict(k=chave(nome), cep=r["cep"], tipo=tipo, lo=lo, hi=hi, par=par,
                 bairro=tokens_bairro(r["bairro"]), bairro_txt=r["bairro"], txt=f'{r["cep"]} ({r["bairro"]}; {r["logradouro"]})')
        )
    idx = {c: defaultdict(list) for c in ref}
    for c, lst in ref.items():
        for e in lst:
            idx[c][e["k"]].append(e)
    return idx


def numero(s):
    m = re.search(r"\d+", s or "")
    return int(m.group()) if m else None


def serve(e, n):
    if n is None:
        return e["lo"] == 0 and e["hi"] == 10**9 and e["par"] is None
    return e["lo"] <= n <= e["hi"] and (e["par"] is None or n % 2 == e["par"])


def escolhe(cands, n, tipo, bairro):
    if tipo:
        mesmo = [e for e in cands if e["tipo"] == tipo]
        cands = mesmo or cands
    ok = [e for e in cands if serve(e, n)]
    ceps = {e["cep"] for e in ok}
    if len(ceps) == 1:
        return ok[0]["cep"], "exato" if len(cands) == 1 else "exato_por_numero"
    if len(ceps) > 1:
        b = tokens_bairro(bairro)
        pont = [(len(b & e["bairro"]) / len(b | e["bairro"]), e["cep"]) for e in ok]
        topo = max(p for p, _ in pont)
        melhor = {c for p, c in pont if p == topo}
        if topo > 0 and len(melhor) == 1:
            return melhor.pop(), "exato_por_bairro"
        restritas = {e["cep"] for e in ok if e["lo"] > 0 or e["hi"] < 10**9}
        if len(restritas) == 1:
            return restritas.pop(), "exato_por_faixa"
        return None, "ambiguo:" + "/".join(sorted(ceps))
    return None, "numero_fora_da_faixa" if cands else "nao_encontrado"


def coord(s):
    try:
        la, lo = (float(x) for x in s.split(","))
        return la, lo
    except ValueError:
        return None


def dist2(a, b):
    return (a[0] - b[0]) ** 2 + ((a[1] - b[1]) * 0.91) ** 2


def main(clientes, ref_path, saida):
    idx = carrega_ref(ref_path)
    with open(clientes, encoding="utf-8-sig") as f:
        rd = csv.DictReader(f)
        campos = rd.fieldnames
        rows = list(rd)
    for r in rows:
        cidade = r["cidade"].strip().upper()
        r["cep_novo"], r["status_cep"], r["ceps_candidatos"] = r["cep"], "fora_do_escopo", ""
        if cidade not in CIDADES:
            continue
        tipo, nome = separa_tipo(r["endereco"])
        k = chave(nome)
        n = numero(r["numero"])
        cands = idx[cidade].get(k)
        p = None
        if cands:
            cep, status = escolhe(cands, n, tipo, r["bairro"])
        else:
            p = difflib.get_close_matches(k, idx[cidade].keys(), n=1, cutoff=0.88)
            if p:
                cep, status = escolhe(idx[cidade][p[0]], n, tipo, r["bairro"])
                status = "aprox_" + status if cep else status
            else:
                cep, status = None, "nao_encontrado"
        r["cep_novo"], r["status_cep"] = cep or r["cep"], status
        ref_rua = cands or (idx[cidade][p[0]] if not cands and p else [])
        r["ceps_candidatos"] = " | ".join(dict.fromkeys(e["txt"] for e in ref_rua)) if (
            status.startswith("ambiguo") or status == "numero_fora_da_faixa") else ""
    # segunda passada: ambíguos resolvidos pelo cliente já resolvido mais próximo (coordenadas)
    por_cep = defaultdict(list)
    for r in rows:
        c = coord(r["coordenadas"].strip('"'))
        if c and r["status_cep"].replace("aprox_", "").startswith("exato"):
            por_cep[r["cep_novo"]].append(c)
    for r in rows:
        if not r["status_cep"].startswith("ambiguo:"):
            continue
        c = coord(r["coordenadas"].strip('"'))
        if not c:
            continue
        melhor = min(((dist2(c, o), cep) for cep in r["status_cep"][8:].split("/")
                      for o in por_cep.get(cep, [])), default=None)
        if melhor and melhor[0] < (0.003) ** 2:  # ~330 m
            r["cep_novo"], r["status_cep"] = melhor[1], "exato_por_coordenada"
    cont = Counter()
    with open(saida, "w", newline="", encoding="utf-8") as o:
        w = csv.DictWriter(o, campos + ["cep_novo", "status_cep", "ceps_candidatos"])
        w.writeheader()
        for r in rows:
            st = r["status_cep"]
            r["status_cep"] = "ambiguo" if st.startswith("ambiguo:") else st
            if r["status_cep"] == "exato_por_coordenada":
                r["ceps_candidatos"] = ""
            cont[(r["cidade"] if r["cidade"].upper() in CIDADES else "OUTRAS", r["status_cep"])] += 1
            w.writerow({k: r[k] for k in campos + ["cep_novo", "status_cep", "ceps_candidatos"]})
    revisao = [r for r in rows if r["status_cep"] in ("ambiguo", "nao_encontrado", "numero_fora_da_faixa")
               or r["status_cep"].startswith("aprox_")]
    with open(saida.replace(".csv", "_revisao.csv"), "w", newline="", encoding="utf-8") as o:
        w = csv.DictWriter(o, campos + ["cep_novo", "status_cep", "ceps_candidatos"])
        w.writeheader()
        w.writerows({k: r[k] for k in campos + ["cep_novo", "status_cep", "ceps_candidatos"]} for r in revisao)
    for k, v in sorted(cont.items()):
        print(k, v)


if __name__ == "__main__":
    main(*sys.argv[1:4])
