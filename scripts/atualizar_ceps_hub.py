"""Robô: troca o CEP do endereço dos serviços no Hub, preservando número,
complemento e referência (que o Hub apaga ao mudar o CEP).

Por padrão NÃO grava nada (modo simulação). Só grava com --gravar.

Uso (na pasta ceps, com o .venv ativo):
  1) descobrir os campos da tela (não altera nada):
       python scripts/atualizar_ceps_hub.py data/clientes.csv --inspecionar 1
  2) simular em 3 clientes (preenche, confere, NÃO grava):
       python scripts/atualizar_ceps_hub.py data/clientes.csv --limite 3
  3) gravar 3 clientes específicos:
       python scripts/atualizar_ceps_hub.py data/clientes.csv --id 111 222 333 --gravar
  4) volume (só depois de conferir os testes):
       python scripts/atualizar_ceps_hub.py data/clientes.csv --todos --gravar

O CSV é a aba da planilha (colunas id_cliente_servico, codigo_cliente, cep,
cep_novo, numero, cidade, status_cep). Só entram linhas com status confiável.
O serviço é identificado pelo "ID Cliente Serviço" do cartão, não pelo
endereço: assim um cliente com vários serviços altera só o serviço certo.
"""
import argparse
import csv
import json
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path

HUB = "https://wsnet.hubsoft.com.br/"
PERFIL = Path(".perfil")  # mesmo perfil do codegen (login já feito)
LOGS = Path("logs")
LOG_GRAVACAO = LOGS / "log_ceps.csv"
LOG_SIMULACAO = LOGS / "simulacao.csv"
CAMPOS_LOG = ["data_hora", "id_cliente_servico", "codigo_cliente", "cep_antigo", "cep_novo", "status", "detalhes"]

CIDADES = {"ITANHAÉM", "MONGAGUÁ", "PERUÍBE"}
STATUS_CONFIAVEIS = {
    "exato", "exato_por_bairro", "exato_por_numero", "exato_por_faixa", "exato_por_coordenada",
    "aprox_exato", "aprox_exato_por_numero", "aprox_exato_por_bairro",
}
FEITOS = {"ok", "ja_atualizado"}  # status que não são refeitos na retomada
DIALOGO = "md-dialog"  # se a tela usar outro componente, ajustar após --inspecionar

# Rótulos dos campos na tela de edição do endereço (confirmar com --inspecionar)
PADROES = {
    "cep": r"^\s*CEP",
    "numero": r"^\s*N[úu]mero",
    "complemento": r"Complemento",
    "referencia": r"Refer[êe]ncia",
    "logradouro": r"Endere[çc]o|Logradouro|Rua",
    "bairro": r"Bairro",
    "cidade": r"Cidade|Munic",
}
PRESERVAR = ("numero", "complemento", "referencia")  # o Hub apaga ao trocar o CEP

JS_CAMPOS = r"""el => [...el.querySelectorAll('input, textarea')].map((e, i) => ({
  i, tipo: e.type, nome: e.name || '',
  rotulo: (((e.closest('md-input-container') || {}).innerText) || e.getAttribute('aria-label') || e.placeholder || '').trim().replace(/\s+/g, ' '),
  valor: e.value || '', visivel: !!(e.offsetWidth || e.offsetHeight)
}))"""


class ErroPulavel(Exception):
    """Problema deste cliente: registra e segue para o próximo."""


class ErroFatal(Exception):
    """Algo que pode afetar todos os clientes: o robô para."""


def so_digitos(s):
    return re.sub(r"\D", "", s or "")


def ler_linhas(caminho):
    with open(caminho, encoding="utf-8-sig", newline="") as f:
        amostra = f.read(4096)
        f.seek(0)
        delim = ";" if amostra.split("\n")[0].count(";") > amostra.split("\n")[0].count(",") else ","
        return list(csv.DictReader(f, delimiter=delim))


def selecionar(linhas, ids=None):
    vistos, saida = set(), []
    for r in linhas:
        idcs = so_digitos(r.get("id_cliente_servico"))
        if not idcs or idcs in vistos:
            continue
        if ids and idcs not in ids:
            continue
        if (r.get("status_cep") or "").strip() not in STATUS_CONFIAVEIS:
            continue
        if (r.get("cidade") or "").strip().upper() not in CIDADES:
            continue
        novo, antigo = so_digitos(r.get("cep_novo")), so_digitos(r.get("cep"))
        if len(novo) != 8 or novo == antigo:
            continue
        vistos.add(idcs)
        saida.append(r)
    return saida


def carregar_feitos():
    if not LOG_GRAVACAO.exists():
        return set()
    with open(LOG_GRAVACAO, encoding="utf-8", newline="") as f:
        return {r["id_cliente_servico"] for r in csv.DictReader(f) if r["status"] in FEITOS}


def registrar(arquivo, linha, status, detalhes=None):
    LOGS.mkdir(exist_ok=True)
    novo = not arquivo.exists()
    with open(arquivo, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, CAMPOS_LOG)
        if novo:
            w.writeheader()
        w.writerow({
            "data_hora": datetime.now().isoformat(timespec="seconds"),
            "id_cliente_servico": so_digitos(linha.get("id_cliente_servico")),
            "codigo_cliente": linha.get("codigo_cliente"),
            "cep_antigo": so_digitos(linha.get("cep")),
            "cep_novo": so_digitos(linha.get("cep_novo")),
            "status": status,
            "detalhes": json.dumps(detalhes or {}, ensure_ascii=False),
        })


# ---------------------------------------------------------------- navegação

def abrir_servico(page, linha):
    """Consulta o cliente, abre a lista de serviços e devolve o cartão do serviço certo."""
    from playwright.sync_api import expect  # noqa: F401

    codigo = so_digitos(linha["codigo_cliente"])
    idcs = so_digitos(linha["id_cliente_servico"])
    page.goto(HUB)
    consultar = page.get_by_role("link", name="Consultar Cliente")
    if not consultar.is_visible():
        page.get_by_text("Cliente", exact=True).click()
    consultar.click()
    page.get_by_role("textbox", name="Consulta *").fill(codigo)
    page.get_by_role("button", name="Consultar").click()

    menus = page.locator("md-menu").filter(has_text=re.compile(r"Ver\s*DASHBOARD"))
    menus.first.wait_for()
    if menus.count() != 1:
        raise ErroPulavel(f"consulta_retornou_{menus.count()}_clientes")
    menus.get_by_label("Abrir menu").click()
    page.get_by_role("menuitem", name=re.compile("SERVIÇOS")).click()

    page.get_by_text(re.compile("ID Cliente Serviço")).first.wait_for()
    cartao = (
        page.locator("div")
        .filter(has_text=re.compile(rf"ID Cliente Serviço:\s*{idcs}(?!\d)"))
        .filter(has=page.get_by_role("button", name="Outras Opções"))
    )
    if cartao.count() == 0:
        raise ErroPulavel("servico_nao_encontrado")
    cartao = cartao.last  # o contêiner mais interno que tem o ID e o botão
    if cartao.get_by_role("button", name="Outras Opções").count() != 1:
        raise ErroPulavel("cartao_ambiguo")
    return cartao


def cep_do_cartao(cartao):
    texto = re.sub(r"\s+", " ", cartao.inner_text())
    m = re.search(r"INSTALAÇÃO:(.*?)CEP:\s*(\d{5}-?\d{3})", texto)
    return so_digitos(m.group(2)) if m else None


def abrir_dialogo_endereco(page, cartao):
    cartao.get_by_role("button", name="Outras Opções").click()
    page.get_by_role("menuitem", name="Editar").click()
    page.get_by_role("button", name="ENDEREÇOS").click()
    linhas = page.get_by_role("row").filter(has_text=re.compile("Cadastral"))
    linhas.first.wait_for()
    if linhas.count() != 1:
        raise ErroPulavel(f"linhas_cadastral_{linhas.count()}")
    linhas.get_by_label("Editar").click()
    dialogo = page.locator(DIALOGO)
    dialogo = dialogo.last if dialogo.count() else page.locator("body")
    dialogo.get_by_role("textbox", name=re.compile(PADROES["cep"])).wait_for()
    return dialogo


# ---------------------------------------------------------------- campos

def ler_campos(dialogo):
    return [c for c in dialogo.evaluate(JS_CAMPOS)
            if c["visivel"] and c["tipo"] not in ("hidden", "checkbox", "radio")]


def mapear(campos):
    achados = {}
    for chave, padrao in PADROES.items():
        for c in campos:
            if re.search(padrao, c["rotulo"], re.I):
                achados[chave] = c
                break
    return achados


def valores(campos):
    return {k: c["valor"].strip() for k, c in mapear(campos).items()}


def preencher(dialogo, campo, valor):
    dialogo.locator("input, textarea").nth(campo["i"]).fill(valor)


# ---------------------------------------------------------------- fluxo

def processar(page, linha, args, tela):
    idcs = so_digitos(linha["id_cliente_servico"])
    antigo, novo = so_digitos(linha["cep"]), so_digitos(linha["cep_novo"])
    cartao = abrir_servico(page, linha)

    atual = cep_do_cartao(cartao)
    if atual == novo:
        return "ja_atualizado", {"cep_cartao": atual}
    if atual != antigo:
        return "cep_divergente", {"cep_cartao": atual, "cep_planilha": antigo}

    dialogo = abrir_dialogo_endereco(page, cartao)
    campos = ler_campos(dialogo)
    mapa = mapear(campos)
    if args.inspecionar:
        tela.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(tela / f"{idcs}_dialogo.png"))
        print(json.dumps(campos, ensure_ascii=False, indent=2))
        print("Campos reconhecidos:", {k: v["rotulo"] for k, v in mapa.items()})
        return "inspecionado", {"campos": campos}

    faltando = [k for k in ("cep",) + PRESERVAR if k not in mapa]
    if faltando:
        raise ErroFatal(f"campos não encontrados na tela: {faltando}. Rode --inspecionar e ajuste PADROES.")

    antes = valores(campos)
    n_hub, n_plan = so_digitos(antes.get("numero")), so_digitos(linha.get("numero"))
    if n_hub and n_plan and n_hub != n_plan:
        return "numero_divergente", {"numero_hub": antes.get("numero"), "numero_planilha": linha.get("numero")}

    cep_loc = dialogo.locator("input, textarea").nth(mapa["cep"]["i"])
    cep_loc.fill(novo)
    cep_loc.press("Tab")
    page.wait_for_timeout(2500)  # o Hub busca o endereço pelo CEP

    depois = valores(ler_campos(dialogo))
    for chave in PRESERVAR:
        if depois.get(chave, "") != antes.get(chave, ""):
            preencher(dialogo, mapear(ler_campos(dialogo))[chave], antes.get(chave, ""))
    final = valores(ler_campos(dialogo))
    for chave in PRESERVAR:
        if final.get(chave, "") != antes.get(chave, ""):
            raise ErroPulavel(f"restauracao_falhou_{chave}")
    if so_digitos(final.get("cep")) != novo:
        raise ErroPulavel("cep_nao_ficou_no_campo")

    detalhes = {"antes": antes, "apos_trocar_cep": depois, "final": final}
    if not args.gravar:
        tela.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(tela / f"{idcs}_simulacao.png"))
        return "simulado_ok", detalhes  # nada foi gravado; a navegação seguinte descarta o formulário

    dialogo.get_by_role("button", name="Atualizar Todos").click()
    page.get_by_text(re.compile("Todos os endereços atualizados com sucesso")).wait_for()
    page.get_by_role("button", name="Salvar").click()
    page.get_by_text(re.compile("Serviço atualizado com sucesso")).wait_for()
    page.get_by_role("button", name="Close dialog").click()

    # verificação: relê o cartão e (opcional) o diálogo
    page.reload()
    cartao = abrir_servico(page, linha)
    if cep_do_cartao(cartao) != novo:
        raise ErroPulavel("verificacao_cep_nao_confere")
    if args.verificacao == "completa":
        dialogo = abrir_dialogo_endereco(page, cartao)
        conferido = valores(ler_campos(dialogo))
        detalhes["conferido"] = conferido
        for chave in PRESERVAR:
            if conferido.get(chave, "") != antes.get(chave, ""):
                raise ErroPulavel(f"verificacao_{chave}_nao_confere")
    return "ok", detalhes


def executar(args):
    from playwright.sync_api import sync_playwright

    linhas = selecionar(ler_linhas(args.csv), set(map(so_digitos, args.id)) if args.id else None)
    feitos = carregar_feitos()
    fila = [r for r in linhas if so_digitos(r["id_cliente_servico"]) not in feitos]
    if args.inspecionar:
        fila = fila[: args.inspecionar]
    elif not args.todos:
        fila = fila[: args.limite]
    modo = "inspeção" if args.inspecionar else ("GRAVAÇÃO" if args.gravar else "simulação (não grava)")
    print(f"{len(linhas)} linhas elegíveis, {len(feitos)} já feitas, {len(fila)} nesta rodada. Modo: {modo}")
    if not fila:
        return
    arquivo_log = LOG_GRAVACAO if args.gravar else LOG_SIMULACAO
    tela = LOGS / "telas"

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(PERFIL), channel="chrome", headless=False, viewport={"width": 1366, "height": 900})
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.set_default_timeout(20000)
        page.on("dialog", lambda d: d.accept())  # "sair da página?" ao descartar formulário
        page.goto(HUB)
        if "/login" in page.url:
            input("Faça login na janela do navegador (inclusive validações) e tecle ENTER aqui... ")
        erros_seguidos = 0
        for n, linha in enumerate(fila, 1):
            idcs = so_digitos(linha["id_cliente_servico"])
            try:
                status, det = processar(page, linha, args, tela)
                erros_seguidos = 0
            except ErroPulavel as e:
                status, det = str(e), {}
            except ErroFatal as e:
                registrar(arquivo_log, linha, "erro_fatal", {"erro": str(e)})
                print(f"[{n}/{len(fila)}] {idcs}: ERRO FATAL: {e}")
                break
            except Exception as e:  # noqa: BLE001 - qualquer erro inesperado
                tela.mkdir(parents=True, exist_ok=True)
                try:
                    page.screenshot(path=str(tela / f"{idcs}_erro.png"))
                except Exception:  # noqa: BLE001
                    pass
                registrar(arquivo_log, linha, "erro", {"erro": f"{type(e).__name__}: {str(e)[:300]}"})
                print(f"[{n}/{len(fila)}] {idcs}: ERRO inesperado: {type(e).__name__}: {str(e)[:200]}")
                erros_seguidos += 1
                if not args.continuar_no_erro or erros_seguidos >= 3:
                    print("Parando. Veja logs/telas/ e o log.")
                    break
                continue
            registrar(arquivo_log, linha, status, det)
            print(f"[{n}/{len(fila)}] {idcs}: {status}")
            if status == "simulado_ok":
                print("   antes:", det["antes"], "\n   após trocar o CEP:", det["apos_trocar_cep"])
            time.sleep(random.uniform(1.0, 3.0))
        ctx.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", help="CSV exportado da planilha (aba filtrada)")
    ap.add_argument("--inspecionar", type=int, metavar="N", help="só abre o diálogo de N clientes e lista os campos")
    ap.add_argument("--gravar", action="store_true", help="grava de verdade (sem isso, só simula)")
    ap.add_argument("--limite", type=int, default=3, help="máximo de clientes nesta rodada (padrão 3)")
    ap.add_argument("--todos", action="store_true", help="ignora --limite")
    ap.add_argument("--id", nargs="+", help="processa só estes id_cliente_servico")
    ap.add_argument("--verificacao", choices=["card", "completa"], default="completa",
                    help="completa = reabre o endereço e confere número/complemento/referência")
    ap.add_argument("--continuar-no-erro", action="store_true", help="segue após erro inesperado (para após 3 seguidos)")
    args = ap.parse_args(argv)
    try:
        executar(args)
    except ErroFatal as e:
        sys.exit(f"ERRO FATAL: {e}")


if __name__ == "__main__":
    main()
