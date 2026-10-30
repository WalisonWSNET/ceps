"""Listas de pendências para revisar depois da rodada do robô. Só LÊ o CSV exportado da planilha e a lista dos Correios;
não mexe na planilha nem no Hub.

Uso: python scripts/pendencias.py data/clientes.csv [data/ceps_logradouros.csv] [--saida logs/pendencias]

Gera (um CSV por item, em --saida) e imprime as contagens:
  1_status_cep_pendentes.csv   status_cep ambiguo / fora_do_escopo / nao_encontrado / numero_fora_da_faixa (inclui os verdes:
                               o CSV não traz cor, então nada é excluído)
  2_com_ceps_candidatos.csv    linhas com a coluna ceps_candidatos preenchida
  3_com_obs_robo.csv           linhas com obs_robo preenchida (+ contagem por motivo no terminal e em 3b_motivos_obs_robo.csv)
  4_cep_generico.csv           clientes ainda com 11700000 / 11730000 / 11740000 / 11750000 (em cep ou em cep_novo)
  5_cep_suspeito.csv           cep_novo com cara de CEP "inventado" (só os dígitos finais mudam, ex. 11730002): existe nos
                               Correios? se existe, a rua bate com a do cliente?
"""
import argparse
import csv
import difflib
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from atualizar_ceps import chave, faixa, sem_acento  # noqa: E402

GENERICOS = {"11700000", "11730000", "11740000", "11750000"}
PREFIXOS = {g[:5] for g in GENERICOS}
STATUS_PENDENTES = ("ambiguo", "fora_do_escopo", "nao_encontrado", "numero_fora_da_faixa")


def so_digitos(s):
    return re.sub(r"\D", "", s or "")


def ler(caminho):
    with open(caminho, encoding="utf-8-sig", newline="") as f:
        primeira = f.readline()
        f.seek(0)
        delim = ";" if primeira.count(";") > primeira.count(",") else ","
        return list(csv.DictReader(f, delimiter=delim))


def numero(s):
    m = re.search(r"\d+", s or "")
    return int(m.group()) if m else None


def carregar_correios(caminho):
    """cep -> lista de (cidade, logradouro_completo, nome_sem_faixa, lo, hi, par, bairro)"""
    por_cep = defaultdict(list)
    for r in csv.DictReader(open(caminho, encoding="utf-8", newline="")):
        nome, lo, hi, par = faixa(r["logradouro"])
        por_cep[so_digitos(r["cep"])].append((r["cidade"].upper(), f'{r["tipo"]} {r["logradouro"]}', nome, lo, hi, par, r["bairro"]))
    return por_cep


def compara_rua(endereco, nome_correios):
    a, b = chave(endereco), chave(nome_correios)
    if not a or not b:
        return "sem_rua"
    if a == b:
        return "bate"
    return "parecida" if difflib.SequenceMatcher(None, a, b).ratio() >= 0.8 else "nao_bate"


def avalia_suspeito(r, por_cep):
    cep = so_digitos(r.get("cep_novo"))
    if cep not in por_cep:
        return {"existe_nos_correios": "nao", "rua_bate": "", "logradouro_correios": "", "bairro_correios": "", "numero_na_faixa": ""}
    n = numero(r.get("numero"))
    melhor = None
    for cidade, completo, nome, lo, hi, par, bairro in por_cep[cep]:
        res = compara_rua(r.get("endereco", ""), nome)
        na_faixa = "" if n is None else ("sim" if lo <= n <= hi and (par is None or n % 2 == par) else "nao")
        pontos = {"bate": 3, "parecida": 2, "nao_bate": 1, "sem_rua": 0}[res]
        if melhor is None or pontos > melhor[0]:
            melhor = (pontos, res, completo, bairro, na_faixa)
    _, res, completo, bairro, na_faixa = melhor
    return {"existe_nos_correios": "sim", "rua_bate": res, "logradouro_correios": completo, "bairro_correios": bairro,
            "numero_na_faixa": na_faixa}


def suspeito(cep):
    """Mesmo prefixo de cidade de um genérico, mas não é o genérico, e os 3 últimos dígitos são baixos (ex.: 11730002)."""
    return len(cep) == 8 and cep[:5] in PREFIXOS and cep not in GENERICOS and int(cep[5:]) < 100


def escrever(caminho, linhas, colunas):
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with open(caminho, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, colunas, extrasaction="ignore")
        w.writeheader()
        w.writerows(linhas)
    return len(linhas)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv")
    ap.add_argument("correios", nargs="?", default="data/ceps_logradouros.csv")
    ap.add_argument("--saida", default="logs/pendencias")
    args = ap.parse_args()

    linhas = ler(args.csv)
    if not linhas:
        sys.exit("CSV vazio")
    cols = set(linhas[0])
    for c in ("id_cliente_servico", "cep_novo"):
        if c not in cols:
            sys.exit(f"coluna {c} não existe no CSV (colunas: {sorted(cols)})")
    saida = Path(args.saida)
    base = ["id_cliente_servico", "codigo_cliente", "nome_razaosocial", "endereco", "numero", "bairro", "cidade", "cep", "cep_novo",
            "status_cep", "feito", "obs_robo"]
    print(f"{len(linhas)} linhas lidas de {args.csv}\n")

    p = [r for r in linhas if (r.get("status_cep") or "").strip() in STATUS_PENDENTES]
    n = escrever(saida / "1_status_cep_pendentes.csv", p, base + ["ceps_candidatos"])
    print(f"1. status_cep pendentes: {n}")
    for st, q in Counter((r["status_cep"] or "").strip() for r in p).most_common():
        print(f"     {st}: {q}")

    c = [r for r in linhas if (r.get("ceps_candidatos") or "").strip()]
    print(f"2. com ceps_candidatos preenchida: {escrever(saida / '2_com_ceps_candidatos.csv', c, base + ['ceps_candidatos'])}")

    o = [r for r in linhas if (r.get("obs_robo") or "").strip()]
    print(f"3. com obs_robo preenchida: {escrever(saida / '3_com_obs_robo.csv', o, base)}")
    motivos = Counter(re.split(r"[:(]", r["obs_robo"].strip(), maxsplit=1)[0].strip() for r in o)
    with open(saida / "3b_motivos_obs_robo.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["motivo", "quantidade"])
        w.writerows(motivos.most_common())
    for m, q in motivos.most_common():
        print(f"     {m}: {q}")

    g = []
    for r in linhas:
        onde = [nome for nome, v in (("cep", so_digitos(r.get("cep"))), ("cep_novo", so_digitos(r.get("cep_novo")))) if v in GENERICOS]
        if onde:
            g.append(dict(r, onde="+".join(onde)))
    print(f"4. ainda com CEP genérico (11700000/11730000/11740000/11750000): {escrever(saida / '4_cep_generico.csv', g, base + ['onde'])}")
    for cep in sorted(GENERICOS):
        print(f"     {cep}: {sum(1 for r in g if cep in (so_digitos(r.get('cep')), so_digitos(r.get('cep_novo'))))}")

    por_cep = carregar_correios(args.correios)
    s = [dict(r, **avalia_suspeito(r, por_cep)) for r in linhas if suspeito(so_digitos(r.get("cep_novo")))]
    colunas_s = base + ["existe_nos_correios", "rua_bate", "numero_na_faixa", "logradouro_correios", "bairro_correios"]
    print(f"5. cep_novo suspeito (só os dígitos finais mudam, ex. 11730002): {escrever(saida / '5_cep_suspeito.csv', s, colunas_s)}")
    for chave_, q in Counter((r["existe_nos_correios"], r["rua_bate"]) for r in s).most_common():
        print(f"     existe={chave_[0]} rua_bate={chave_[1] or '-'}: {q}")
    print(f"\nArquivos em {saida}\\")


if __name__ == "__main__":
    main()
