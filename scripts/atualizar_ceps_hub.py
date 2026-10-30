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
import difflib
import math
import statistics
import unicodedata
from collections import defaultdict
import json
import random
import re
import os
import sys
import threading
import time
from datetime import datetime, timedelta
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
    "exato_por_geografia",  # escolhido pela coordenada do cliente (refinar_ceps_geografia.py)
    "conferido_manual",  # linhas dos status duvidosos que você revisou e escolheu o CEP em cep_novo
}
ALARMES = ("verificacao_", "restauracao_", "cep_nao_ficou", "botao_", "campo_sumiu", "formulario")  # sinal de problema geral
FEITOS = {"ok", "ja_atualizado"}  # status que não são refeitos na retomada
DIALOGO = "md-dialog"  # se a tela usar outro componente, ajustar após --inspecionar

# Rótulos dos campos na tela de edição do endereço (confirmar com --inspecionar)
PADROES = {  # sem \b: o rótulo pode vir com texto de ícone colado antes (ex.: "icon-pinCEP *")
    "cep": r"CEP",
    "numero": r"N[úu]mero",
    "complemento": r"Complemento",
    "referencia": r"Refer[êe]ncia",
    "logradouro": r"Endere[çc]o|Logradouro",
    "bairro": r"Bairro",
    "cidade": r"Cidade|Munic",
    "estado": r"Estado|\bUF\b",
}
# O CEP preenche sozinho estes campos; todos os OUTROS campos visíveis são guardados e recolocados
# (o Hub apaga número, complemento, referência etc. ao trocar o CEP).
DERIVADOS = ("cep", "logradouro", "bairro", "cidade", "estado")
OBRIGATORIOS = ("cep", "numero", "complemento", "referencia")

JS_CAMPOS = r"""el => [...el.querySelectorAll('input, textarea')].map((e, i) => ({
  i, tipo: e.type, nome: e.name || '',
  rotulo: (((e.closest('md-input-container') || {}).innerText) || e.getAttribute('aria-label') || e.placeholder || '').trim().replace(/\s+/g, ' '),
  valor: e.value || '', visivel: !!(e.offsetWidth || e.offsetHeight)
}))"""


JS_CAMPOS_PAGINA = r"""() => [...document.querySelectorAll('input, textarea, md-select')]
  .filter(e => e.offsetWidth || e.offsetHeight)
  .map(e => ({tag: e.tagName.toLowerCase(), rotulo: (((e.closest('md-input-container') || {}).innerText) || e.getAttribute('aria-label') || e.placeholder || '').trim().replace(/\s+/g, ' ').slice(0, 60), valor: (e.value || '').slice(0, 40)}))"""
JS_TITULOS = r"""() => [...document.querySelectorAll('md-dialog, [role=dialog], md-toolbar, h1, h2, h3')]
  .filter(e => e.offsetWidth || e.offsetHeight).map(e => (e.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 80)).slice(0, 15)"""


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


VERDADEIRO = {"TRUE", "VERDADEIRO", "SIM", "1", "X"}


def marcado(linha, coluna):
    """Caixa de seleção da planilha marcada = já atualizado no Hub."""
    chave = next((k for k in linha if k and k.strip().lower() == coluna.lower()), None)
    return chave is not None and (linha[chave] or "").strip().upper() in VERDADEIRO


def selecionar(linhas, ids=None, coluna_check="feito"):
    vistos, saida = set(), []
    for r in linhas:
        idcs = so_digitos(r.get("id_cliente_servico"))
        if not idcs or idcs in vistos:
            continue
        if ids and idcs not in ids:
            continue
        if marcado(r, coluna_check):
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


PULADOS = {"servico_nao_encontrado", "numero_divergente", "cep_divergente", "campo_autocompletar_preenchido",
           "enderecos_diferentes", "cep_formulario_divergente", "bairro_longe", "rua_e_bairro_divergem", "cidade_mudou"}  # precisam de tratamento à mão; não repetir a cada rodada


def carregar_pulados():
    if not LOG_GRAVACAO.exists():
        return set()
    ultimo = {}
    with open(LOG_GRAVACAO, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            ultimo[r["id_cliente_servico"]] = r["status"]
    return {i for i, st in ultimo.items() if st in PULADOS or st.startswith(("enderecos_diferentes", "enderecos_conflito", "verificacao_"))}


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

def escolher_tipo_busca(page):
    """A consulta abre em 'Nome Cliente': troca para a busca por código do cliente."""
    page.get_by_label(re.compile(r"Tipo de Busca")).first.click()
    opcoes = page.get_by_role("option")
    opcoes.first.wait_for()
    textos = [t.strip() for t in opcoes.all_inner_texts()]
    alvo = (next((t for t in textos if re.search(r"c[óo]digo\s*(do\s*)?cliente", t, re.I)), None)
            or next((t for t in textos if re.search(r"c[óo]digo", t, re.I)), None))
    if not alvo:
        raise ErroFatal(f"opção de código não encontrada em Tipo de Busca. Opções: {textos}")
    opcoes.filter(has_text=alvo).first.click()


FASES = []  # (nome, instante) do cliente atual, para --tempos


def marca(nome):
    FASES.append((nome, time.time()))


OBSERVACOES_LIDAS = []  # textos das janelas "Observações Obrigatórias" fechadas no cliente atual


def fechar_observacoes(page, espera_ms=0):
    """Clica em OK na janela 'Observações Obrigatórias' (pode haver mais de uma) e guarda o texto lido."""
    from playwright.sync_api import TimeoutError as PWTimeout

    padrao = re.compile(r"Observações Obrigatórias", re.I)
    titulo = page.get_by_text(padrao).filter(visible=True)
    for _ in range(5):
        try:
            titulo.first.wait_for(state="visible", timeout=max(espera_ms, 200))
        except PWTimeout:
            return
        caixa = page.locator("md-dialog").filter(has_text=padrao)
        if not caixa.count():
            caixa = (page.locator("div").filter(has_text=padrao)
                     .filter(has=page.get_by_text(re.compile(r"^\s*OK\s*$"))))
        caixa = caixa.last
        OBSERVACOES_LIDAS.append(re.sub(r"\s+", " ", caixa.inner_text())[:300])
        ok = caixa.get_by_text(re.compile(r"^\s*OK\s*$")).filter(visible=True).first
        for tentativa in range(3):
            try:
                ok.click(timeout=4000, force=(tentativa == 2))
                break
            except PWTimeout:
                page.wait_for_timeout(400)
                if titulo.count() == 0:  # a janela fechou sozinha enquanto tentávamos clicar
                    break
        else:
            raise ErroPulavel("observacoes_nao_fechou")
        page.wait_for_timeout(500)
        espera_ms = 0  # as próximas só se já estiverem na tela
    raise ErroPulavel("observacoes_nao_fechou")  # 5 cliques em OK e a janela continua


def escolher_status_todos(page):
    """A consulta abre com 'Status Cadastro = Ativo': troca para 'Todos' (clientes inativos)."""
    page.get_by_label(re.compile(r"Status Cadastro")).first.click()
    opcoes = page.get_by_role("option")
    opcoes.first.wait_for()
    opcoes.filter(has_text=re.compile(r"^\s*Todos\s*$")).first.click()


def aguardar_cliente(page):
    """Espera o resultado da consulta. Se vier vazio, refaz a consulta com Status Cadastro = Todos."""
    menus = page.locator("md-menu").filter(has_text=re.compile(r"Ver\s*DASHBOARD"))
    vazio = page.get_by_text(re.compile(r"Não foi encontrado nenhum resultado")).filter(visible=True)
    ampliou = False
    for _ in range(80):  # ~20 s
        if menus.count():
            return menus
        if vazio.count():
            if ampliou:
                raise ErroPulavel("cliente_nao_encontrado")
            escolher_status_todos(page)
            page.get_by_role("button", name="Consultar").click()
            ampliou = True
            page.wait_for_timeout(1500)
            continue
        page.wait_for_timeout(250)
    raise ErroPulavel("consulta_sem_resposta")


def localizar_cartao(page, idcs, tentativas=12):
    """Acha, na lista de serviços que está na tela, o cartão do serviço com este ID."""
    cartao = (
        page.locator("div")
        .filter(has_text=re.compile(rf"ID Cliente Serviço:\s*{idcs}(?!\d)"))
        .filter(has=page.get_by_role("button", name="Outras Opções"))
    )
    from playwright.sync_api import TimeoutError as PWTimeout

    for tentativa in range(tentativas):
        fechar_observacoes(page)
        try:
            cartao.first.wait_for(state="attached", timeout=1000)  # os cartões carregam aos poucos
            break
        except PWTimeout:
            if tentativa == tentativas - 1:
                ids = re.findall(r"ID Cliente Serviço:\s*(\d+)", page.inner_text("body"))
                raise ErroPulavel("servico_nao_encontrado:ids_na_tela=" + ",".join(ids[:10]))
    cartao = cartao.last  # o contêiner mais interno que tem o ID e o botão
    if cartao.get_by_role("button", name="Outras Opções").count() != 1:
        raise ErroPulavel("cartao_ambiguo")
    return cartao


def reabrir_rapido(page, linha):
    """A janela do serviço sumiu: tenta reabrir a partir do cartão que já está na tela (sem refazer a consulta)."""
    try:
        cartao = localizar_cartao(page, so_digitos(linha["id_cliente_servico"]), tentativas=3)
    except Exception:  # noqa: BLE001 - a lista não está mais na tela: refaz tudo
        cartao = abrir_servico(page, linha)
    abrir_aba_enderecos(page, cartao)


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
    escolher_tipo_busca(page)
    page.get_by_role("textbox", name="Consulta *").fill(codigo)
    page.get_by_role("button", name="Consultar").click()

    menus = aguardar_cliente(page)
    if menus.count() != 1:
        raise ErroPulavel(f"consulta_retornou_{menus.count()}_clientes")
    fechar_observacoes(page)
    menus.get_by_label("Abrir menu").click()
    page.get_by_role("menuitem", name=re.compile("SERVIÇOS")).click()

    page.get_by_text(re.compile("ID Cliente Serviço")).first.wait_for()
    fechar_observacoes(page, espera_ms=1200)  # o aviso costuma aparecer logo após a lista carregar
    return localizar_cartao(page, idcs)


def cep_do_cartao(cartao):
    texto = re.sub(r"\s+", " ", cartao.inner_text())
    m = re.search(r"INSTALAÇÃO:(.*?)CEP:\s*(\d{5}-?\d{3})", texto)
    return so_digitos(m.group(2)) if m else None


TIPOS_ENDERECO = re.compile(r"Instala[çc][ãa]o|Cadastral|Cobran[çc]a|Fiscal", re.I)
LINHAS_ENDERECO = re.compile(r"CEP:\s*\d")


def enderecos_iguais(page):
    """Os 4 endereços (Instalação/Cadastral/Cobrança/Fiscal) têm o mesmo texto na lista?"""
    linhas = page.get_by_role("row").filter(has_text=LINHAS_ENDERECO)
    textos = [re.sub(r"\s+", " ", t).strip() for t in linhas.all_inner_texts()]
    return len({re.sub(r"^(Instala[çc][ãa]o|Cadastral|Cobran[çc]a|Fiscal)\s*", "", t, flags=re.I).strip()
                for t in textos}) <= 1


JS_BOTOES = r"""el => {
  const topo = el.getBoundingClientRect().top;
  return [...el.querySelectorAll('button, [role=button], md-icon, a')].map(e => {
    const r = e.getBoundingClientRect();
    return {tag: e.tagName.toLowerCase(), aria: e.getAttribute('aria-label') || '', titulo: e.getAttribute('title') || '',
            classe: (e.className && e.className.baseVal === undefined ? e.className : '').toString().slice(0, 50),
            texto: (e.innerText || '').trim().slice(0, 25), visivel: !!(e.offsetWidth || e.offsetHeight),
            dy: Math.round(r.top - topo), dx: Math.round(r.left)};
  }).filter(b => b.visivel);
}"""
FECHAR = re.compile(r"close|fechar|clear|cancel", re.I)


def botoes_do_painel(dlg):
    return dlg.evaluate(JS_BOTOES)


JS_FECHAR = r"""el => {
  const vis = e => !!(e.offsetWidth || e.offsetHeight);
  const re = /Endere[çc]o de Cliente/i;
  const tits = [...el.querySelectorAll('*')].filter(e => vis(e) && re.test(e.textContent || '') && (e.textContent || '').trim().length < 60);
  const tit = tits.find(e => ![...e.children].some(c => tits.includes(c)));   // o menor elemento com o título do painel
  if (!tit) return null;
  const fechaveis = a => {
    const xs = [];
    for (const s of ["[aria-label*='close' i]", "[aria-label*='fechar' i]", "[title*='close' i]", "[title*='fechar' i]"]) xs.push(...a.querySelectorAll(s));
    xs.push(...[...a.querySelectorAll('button, [role=button], md-icon, a')].filter(e => /^\s*(close|clear)\s*$/i.test(e.innerText || '')));
    return xs.filter(vis);
  };
  let a = tit;
  for (let i = 0; i < 4 && a; i++, a = a.parentElement) {   // só a barra de título do painel: sobe poucos níveis
    const xs = fechaveis(a);
    if (xs.length) { xs[xs.length - 1].click(); return a.tagName.toLowerCase() + ':' + i; }
  }
  return null;
}"""


DEPURAR = False
ULTIMO_FECHAR = {}
PEDIDOS = []        # (instante, método, url) dos últimos pedidos de rede do Hub
RESPOSTAS = []      # (instante, status, url) das últimas respostas de rede do Hub
JANELA_ABERTA_EM = [0.0]
JS_INIT = r"""(() => {
  window.__remocoes = [];
  const reg = (n, via) => { try {
    if (n && n.nodeType === 1 && (n.tagName === 'MD-DIALOG' || (n.querySelector && n.querySelector('md-dialog'))))
      window.__remocoes.push({t: Date.now(), via, tag: n.tagName, cls: String(n.className || '').slice(0, 60),
        txt: (n.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 50),
        stack: new Error().stack.split('\n').slice(2, 10).map(s => s.trim().slice(0, 150))});
  } catch (e) {} };
  const rc = Node.prototype.removeChild;
  Node.prototype.removeChild = function (c) { reg(c, 'removeChild'); return rc.call(this, c); };
  const rm = Element.prototype.remove;
  Element.prototype.remove = function () { reg(this, 'remove'); return rm.call(this); };
})();"""
DIALOGOS_VISTOS = []  # alert/confirm/beforeunload que o navegador mostrou (aceitos automaticamente)


def ao_dialogo(d):
    DIALOGOS_VISTOS.append(f"{d.type}:{d.message[:120]}")
    print(f"   [diálogo do navegador] {d.type}: {d.message[:120]!r}")
    d.accept()


JS_ANIMACOES = r"""() => Promise.race([
  Promise.all(document.getAnimations().map(a => a.finished.catch(() => null))),
  new Promise(r => setTimeout(r, 2500))
]).then(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))))"""


def painel_aberto(page):
    """O painel de endereço está aberto se o campo CEP dele (name='cep') está visível."""
    return page.locator("input[name='cep']").filter(visible=True).count() > 0


def esperar_fechar(page, ms=3000):
    for _ in range(ms // 250):
        if not painel_aberto(page):
            return True
        page.wait_for_timeout(250)
    return False


def fechar_formulario(page, dlg):
    """Fecha o painel 'Endereço de Cliente' SEM gravar. NUNCA usa Esc nem Descartar: os dois fecham a janela inteira do
    serviço (a evidência mostrou a janela sumindo ~3,8 s depois do 1º clique no X: era o Descartar do plano B).
    Se o X não fechar o painel (às vezes ele ainda está carregando e ignora o clique), clica no X de novo."""
    painel = dlg.element_handle()
    try:  # força o navegador a desenhar quadros e termina as animações do painel antes de fechar
        page.bring_to_front()
        page.screenshot(clip={"x": 0, "y": 0, "width": 2, "height": 2}, type="jpeg", quality=10)
        page.evaluate(JS_ANIMACOES)
    except Exception:  # noqa: BLE001
        pass
    if DEPURAR:  # mostra o painel e os botões ANTES de fechar
        LOGS.joinpath("telas").mkdir(parents=True, exist_ok=True)
        foto = LOGS / "telas" / f"painel_antes_{datetime.now():%H%M%S}.png"
        page.screenshot(path=str(foto))
        print(f"   [depurar] captura do painel: {foto.name}")
        for b in botoes_do_painel(dlg):
            print("   [depurar] botão:", b)
    retorno = None
    for tentativa in range(4):
        ULTIMO_FECHAR["t"] = time.time()
        retorno = painel.evaluate(JS_FECHAR)
        ULTIMO_FECHAR["retorno"] = retorno
        ULTIMO_FECHAR["tentativas"] = tentativa + 1
        if DEPURAR:
            print(f"   [depurar] o robô clicou em: {retorno} (tentativa {tentativa + 1})")
        if retorno and esperar_fechar(page, 2000):
            return
        page.wait_for_timeout(700)  # o painel ainda carrega: espera um pouco e clica de novo
    raise ErroPulavel("formulario_nao_fechou:botoes=" + json.dumps(botoes_do_painel(dlg), ensure_ascii=False)[:600])


def evidencia(page):
    """Registra o que aconteceu quando a janela do serviço sumiu: há quanto tempo abriu, quanto depois do meu clique
    no X ela foi removida, quais respostas de rede chegaram logo antes e quem a removeu."""
    agora = time.time()
    try:
        remocoes = page.evaluate("window.__remocoes || []")
    except Exception:  # noqa: BLE001
        remocoes = []
    t_clique = ULTIMO_FECHAR.get("t")
    print(f"   [evidência] janela aberta há {agora - JANELA_ABERTA_EM[0]:.1f} s; etiqueta de conexão levou {STATUS_CONEXAO_S[0]} s")
    if remocoes:
        t_rem = remocoes[-1]["t"] / 1000.0
        if t_clique:
            print(f"   [evidência] a janela foi removida {t_rem - t_clique:+.2f} s em relação ao meu clique no X "
                  f"(negativo = ANTES do clique; clique há {agora - t_clique:.1f} s)")
        print(f"   [evidência] respostas de rede perto da remoção (±1,5 s):")
        for t, status, url in RESPOSTAS:
            if abs(t - t_rem) <= 1.5:
                print(f"        {t - t_rem:+.2f} s  {status}  ...{url}")
        for r in remocoes[-4:]:
            print(f"   [evidência] removido {r['t'] / 1000.0 - (t_clique or r['t'] / 1000.0):+.2f} s do clique: {r.get('tag')} "
                  f"classe={r.get('cls')!r} texto={r.get('txt')!r}")
        print("   [evidência] remoção de", remocoes[-1].get("tag"), "via", remocoes[-1].get("via"), "; pilha:")
        for linha in remocoes[-1].get("stack", [])[:7]:
            print("        ", linha)


REABERTURAS = [0]  # quantas vezes a janela do serviço sumiu e foi reaberta no cliente atual


def ler_todos_enderecos(page, reabrir=None, ignorar=()):
    """Abre o formulário de cada tipo de endereço, lê os campos e fecha SEM gravar.
    Se a janela do serviço sumir ao fechar um painel, chama `reabrir()` (volta ao serviço/aba Endereços) e segue."""
    n = page.get_by_role("row").filter(has_text=LINHAS_ENDERECO).count()
    todos, i = {}, 0
    while i < n:
        linha = page.get_by_role("row").filter(has_text=LINHAS_ENDERECO).nth(i)
        m = TIPOS_ENDERECO.search(linha.inner_text())
        tipo = m.group(0).lower() if m else f"linha{i}"
        if tipo in ignorar:  # ex.: o Cadastral: fechar o painel dele derruba a janela do serviço; ele é lido ao editar
            i += 1
            continue
        linha.get_by_label("Editar").click()
        dlg = achar_formulario(page)
        esperar_estabilizar(page, dlg, minimo_ms=600)
        todos[tipo] = ler_campos(dlg)
        fechar_formulario(page, dlg)
        fechar_observacoes(page, espera_ms=800)  # o aviso pode reaparecer e cobrir a lista
        try:  # a lista volta a aparecer quando o painel fecha; se não voltar, a janela do serviço foi fechada
            page.get_by_role("row").filter(has_text=LINHAS_ENDERECO).first.wait_for(timeout=5000)
        except Exception:  # noqa: BLE001
            evidencia(page)
            if reabrir and REABERTURAS[0] < 4:
                REABERTURAS[0] += 1
                print(f"   a janela do serviço fechou depois do endereço '{tipo}'; reabrindo ({REABERTURAS[0]})"
                      + (f" | diálogos do navegador: {DIALOGOS_VISTOS[-2:]}" if DIALOGOS_VISTOS else ""))
                reabrir()
            else:
                LOGS.joinpath("telas").mkdir(parents=True, exist_ok=True)
                captura = LOGS / "telas" / f"janela_fechou_{datetime.now():%H%M%S}.png"
                try:
                    page.screenshot(path=str(captura))
                    titulos = page.evaluate(JS_TITULOS)
                except Exception:  # noqa: BLE001
                    titulos = []
                raise ErroPulavel(f"janela_do_servico_fechou:tipo={tipo};lidos={list(todos)};x={ULTIMO_FECHAR.get('retorno')};"
                                  f"captura={captura.name};dialogos={DIALOGOS_VISTOS[-3:]};titulos={json.dumps(titulos, ensure_ascii=False)[:300]}")
        i += 1
    return todos


NUCLEO = ("cep", "estado", "cidade", "bairro", "logradouro", "numero")
MESCLAR = ("complemento", "referencia")


def mesclar_enderecos(todos):
    """Se os endereços só diferem em complemento/referência, devolve o valor a usar em todos (o preenchido).
    Qualquer outra diferença, ou dois valores preenchidos diferentes, vira ErroPulavel."""
    mapas = {t: {k: c["valor"].strip() for k, c in mapear(cs).items()} for t, cs in todos.items()}
    for chave in NUCLEO:
        valores_ = {so_digitos(m.get(chave, "")) if chave == "cep" else re.sub(r"\s+", " ", m.get(chave, "")).casefold()
                    for m in mapas.values()}
        if len(valores_) > 1:
            raise ErroPulavel(f"enderecos_diferentes_{chave}:" + " | ".join(sorted(valores_))[:200])
    auto = {frozenset((c["rotulo"], c["valor"].strip()) for c in cs
                      if c["tipo"] == "search" and not derivado(c)) for cs in todos.values()}
    if len(auto) > 1:
        raise ErroPulavel("enderecos_diferentes_condominio")
    mesclado, conflitos = {}, {}
    for chave in MESCLAR:
        preenchidos = {m.get(chave, "") for m in mapas.values() if m.get(chave, "")}
        if len(preenchidos) > 1:  # valores diferentes: não preenche (os originais ficam só no log)
            conflitos[chave] = sorted(preenchidos)
            mesclado[chave] = ""
        else:
            mesclado[chave] = next(iter(preenchidos), "")
    if conflitos:
        mesclado["_conflitos"] = conflitos
    return mesclado


TIPOS_VIA = {"rua", "r", "avenida", "av", "travessa", "tv", "alameda", "al", "estrada", "est", "praca", "pc",
             "rodovia", "rod", "viela", "vla", "ladeira", "lad", "via"}


PARTICULAS = {"de", "da", "do", "das", "dos", "e"}


def norm_txt(s):
    s = "".join(c for c in unicodedata.normalize("NFD", (s or "").lower()) if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", s)).strip()


def nome_rua(s):
    return " ".join(sorted(t for t in norm_txt(s).split() if t not in TIPOS_VIA and t not in PARTICULAS))


def km(a, b):
    return math.hypot((a[0] - b[0]) * 111.0, (a[1] - b[1]) * 111.0 * math.cos(math.radians(24.2)))


def construir_bairros(linhas):
    """Centro de cada bairro (mediana das coordenadas dos clientes da planilha); precisa de 3+ clientes."""
    pontos = defaultdict(list)
    for r in linhas:
        try:
            la, lo = (float(x) for x in (r.get("coordenadas") or "").strip('"').split(","))
        except ValueError:
            continue
        if -25 < la < -23.5 and -47.5 < lo < -46.3:
            pontos[(norm_txt(r.get("cidade")), norm_txt(r.get("bairro")))].append((la, lo))
    return {k: (statistics.median(p[0] for p in v), statistics.median(p[1] for p in v))
            for k, v in pontos.items() if len(v) >= 3}


def avaliar_divergencia(antes, depois, cidade_planilha, bairros, perto_km=3.0, rua_parecida=0.6):
    """Compara rua/bairro/cidade do formulário antes e depois do CEP novo.
    Devolve (motivo_para_pular_ou_None, info). Pula se a cidade muda ou se o bairro muda para um bairro longe
    (ou desconhecido); só a rua mudar não impede."""
    info = {}
    if norm_txt(antes.get("cidade")) != norm_txt(depois.get("cidade")):
        return "cidade_mudou", {"antes": antes.get("cidade"), "depois": depois.get("cidade")}
    rua_a, rua_d = nome_rua(antes.get("logradouro")), nome_rua(depois.get("logradouro"))
    sim = difflib.SequenceMatcher(None, rua_a, rua_d).ratio() if rua_a or rua_d else 1.0
    rua_muito = sim < rua_parecida
    ba, bd = norm_txt(antes.get("bairro")), norm_txt(depois.get("bairro"))
    bairro_mudou = ba != bd
    dist = None
    if bairro_mudou:
        c = norm_txt(cidade_planilha)
        A, B = bairros.get((c, ba)), bairros.get((c, bd))
        dist = km(A, B) if A and B else None
    bairro_longe = bairro_mudou and (dist is None or dist > perto_km)  # bairro desconhecido = longe (conservador)
    info = {"rua": [antes.get("logradouro"), depois.get("logradouro")], "rua_semelhanca": round(sim, 2),
            "bairro": [antes.get("bairro"), depois.get("bairro")],
            "bairro_km": None if dist is None else round(dist, 1)}
    if bairro_longe:  # bairro só pode mudar para um bairro perto; a rua sozinha pode mudar à vontade
        return "bairro_longe", info
    return None, info


STATUS_CONEXAO_S = [0.0]


def esperar_status_conexao(page, cartao, max_s=2):
    """A etiqueta 'CONECTADO HÁ ...' do cartão chega por uma consulta lenta; quando chega, o Hub redesenha a lista de
    serviços e destrói a janela que estiver aberta. Por isso esperamos ela carregar ANTES de abrir a janela."""
    t0 = time.time()
    while time.time() - t0 < max_s:
        try:
            if re.search(r"CONECTAD", cartao.inner_text(), re.I):
                break
        except Exception:  # noqa: BLE001
            pass
        page.wait_for_timeout(300)
    STATUS_CONEXAO_S[0] = round(time.time() - t0, 1)


def abrir_aba_enderecos(page, cartao):
    esperar_status_conexao(page, cartao)
    marca("espera_status")
    JANELA_ABERTA_EM[0] = time.time()
    fechar_observacoes(page)
    cartao.get_by_role("button", name="Outras Opções").click()
    page.get_by_role("menuitem", name="Editar").click()
    page.get_by_role("button", name="ENDEREÇOS").click()
    page.get_by_role("row").filter(has_text=re.compile("Cadastral")).first.wait_for()


def abrir_linha_cadastral(page):
    linhas = page.get_by_role("row").filter(has_text=re.compile("Cadastral"))
    if linhas.count() != 1:
        raise ErroPulavel(f"linhas_cadastral_{linhas.count()}")
    linhas.get_by_label("Editar").click()
    return achar_formulario(page)


def abrir_dialogo_endereco(page, cartao):
    abrir_aba_enderecos(page, cartao)
    return abrir_linha_cadastral(page)


def achar_formulario(page):
    """Formulário 'Endereço de Cliente': o contêiner mais interno com o título e campos de texto."""
    base = (page.locator("div").filter(has_text=re.compile(r"Endere[çc]o de Cliente"))
            .filter(has=page.locator("input")))
    base.first.wait_for()
    cont = base.last
    for _ in range(40):  # até ~12 s para os campos carregarem
        if "cep" in mapear(ler_campos(cont)):
            return cont
        page.wait_for_timeout(300)
    rotulos = [c["rotulo"] for c in ler_campos(cont)]
    raise ErroFatal(f"formulário de endereço aberto, mas sem campo CEP reconhecido. Rótulos vistos: {rotulos}")


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


def derivado(campo):
    return any(re.search(PADROES[k], campo["rotulo"], re.I) for k in DERIVADOS)


def valores(campos):
    return {c["rotulo"] or f"campo_{c['i']}": c["valor"].strip() for c in campos}


def preencher(dialogo, campo, valor):
    dialogo.locator("input, textarea").nth(campo["i"]).fill(valor)


# ---------------------------------------------------------------- fluxo

AVISO_SERVICO = re.compile(r"Servi.o atualizado com sucesso", re.I)


def aviso_visivel(page, padrao):
    return page.get_by_text(padrao).filter(visible=True).count() > 0


def clicar_texto(page, padrao, nome):
    """Clica no único botão visível com este texto (o nome acessível dos botões do Hub não é confiável).
    Espera até ~6 s para sobrar só um (um diálogo fechando ainda pode mostrar um segundo botão igual)."""
    alvo = page.get_by_text(re.compile(padrao, re.I)).filter(visible=True)
    alvo.first.wait_for()
    for _ in range(20):
        if alvo.count() == 1:
            alvo.click()
            return
        page.wait_for_timeout(300)
    raise ErroPulavel(f"botao_{nome}_ambiguo_{alvo.count()}")


def esperar_estabilizar(page, dialogo, minimo_ms=1000, maximo_ms=4000):
    """Espera o Hub terminar de reagir ao CEP: pelo menos 1 s e os campos iguais em 2 leituras seguidas."""
    t0, anterior, estavel = time.time(), None, 0
    while (time.time() - t0) * 1000 < maximo_ms:
        page.wait_for_timeout(250)
        atual = valores(ler_campos(dialogo))
        estavel = estavel + 1 if atual == anterior else 0
        anterior = atual
        if (time.time() - t0) * 1000 >= minimo_ms and estavel >= 2:
            return


def gravar_endereco(page):
    """Atualizar Todos + Salvar, sem esperas à toa. O resultado de verdade é conferido depois, consultando o cliente."""
    clicar_texto(page, r"^\s*Atualizar\s+Todos\s*$", "atualizar_todos")
    page.get_by_text(re.compile("Todos os endereços atualizados com sucesso")).wait_for()
    # o aviso do serviço costuma aparecer junto com o dos endereços e sumir logo: olha já
    aviso_antes = False
    for _ in range(3):  # ~0,5 s
        if aviso_visivel(page, AVISO_SERVICO):
            aviso_antes = True
            break
        page.wait_for_timeout(250)
    clicar_texto(page, r"^\s*Salvar\s*$", "salvar")
    visto = aviso_antes
    for _ in range(0 if aviso_antes else 16):  # até ~4 s, só se ainda não vimos
        if aviso_visivel(page, AVISO_SERVICO):
            visto = True
            break
        page.wait_for_timeout(250)
    page.wait_for_timeout(1000)
    return {"servico_antes_do_salvar": aviso_antes, "servico_visto": visto}


def norm_campo(v):
    return re.sub(r"\s+", " ", (v or "").strip()).casefold()  # o Hub pode salvar em MAIÚSCULAS


def esperados_do_formulario(guardar, mesclado):
    """Valor que cada campo guardado precisa ter na hora de gravar: o original, ou o mesclado (complemento/referência)."""
    esp = {}
    for i, c in guardar.items():
        esperado = c["valor"].strip()
        for chave in MESCLAR:
            if mesclado and mapear([c]).get(chave):
                esperado = mesclado[chave]
        esp[i] = esperado
    return esp


def faltas_no_formulario(dialogo, esp):
    conf = {c["i"]: c for c in ler_campos(dialogo)}
    return [(i, esperado, conf[i]["valor"] if i in conf else None) for i, esperado in esp.items()
            if i not in conf or norm_campo(conf[i]["valor"]) != norm_campo(esperado)]


def garantir_formulario(page, dialogo, esp):
    """Logo antes de gravar: o Hub às vezes apaga os campos DE NOVO, atrasado. Espera, relê e recoloca o que sumiu.
    Devolve quantos campos precisou recolocar; levanta ErroPulavel se o formulário não firmar."""
    recolocados = 0
    esperar_estabilizar(page, dialogo, minimo_ms=1500)
    for _ in range(4):
        faltas = faltas_no_formulario(dialogo, esp)
        if not faltas:
            return recolocados
        for i, esperado, _ in faltas:
            dialogo.locator("input, textarea").nth(i).fill(esperado)
        recolocados += len(faltas)
        esperar_estabilizar(page, dialogo, minimo_ms=1000)
    raise ErroPulavel("formulario_instavel_antes_de_gravar")


def verificar_e_reparar(page, linha, cartao, esp):
    """Depois de gravar: reabre o Cadastral e confere. Se faltar algo, REPARA uma vez (recoloca, grava, confere de novo).
    Devolve (faltas_que_restaram, reparos, valores_lidos)."""
    dialogo = abrir_dialogo_endereco(page, cartao)
    esperar_estabilizar(page, dialogo, minimo_ms=800)
    faltas = []
    for _ in range(3):
        faltas = faltas_no_formulario(dialogo, esp)
        if not faltas:
            return [], 0, valores(ler_campos(dialogo))
        page.wait_for_timeout(1000)  # relê: pode ter sido pressa
    for i, esperado, _ in faltas:
        dialogo.locator("input, textarea").nth(i).fill(esperado)
    page.wait_for_timeout(800)
    restantes = faltas_no_formulario(dialogo, esp)
    if restantes:
        return restantes, 1, valores(ler_campos(dialogo))
    gravar_endereco(page)
    dialogo = abrir_dialogo_endereco(page, abrir_servico(page, linha))
    esperar_estabilizar(page, dialogo, minimo_ms=800)
    return faltas_no_formulario(dialogo, esp), 1, valores(ler_campos(dialogo))


def trocar_cep(page, dialogo, campos, mapa, novo):
    """Troca o CEP e recoloca tudo que o Hub apagou. Devolve (guardados, antes, depois, final)."""
    guardar = {c["i"]: c for c in campos if not derivado(c)}  # tudo que o CEP não preenche sozinho
    antes = valores(campos)
    cep_loc = dialogo.locator("input, textarea").nth(mapa["cep"]["i"])
    cep_loc.fill(novo)
    cep_loc.press("Tab")
    esperar_estabilizar(page, dialogo)  # o Hub busca o endereço pelo CEP

    depois_campos = {c["i"]: c for c in ler_campos(dialogo)}
    depois = valores(list(depois_campos.values()))
    for i, c in guardar.items():
        if i not in depois_campos:
            raise ErroPulavel(f"campo_sumiu_{c['rotulo']}")
        if depois_campos[i]["valor"].strip() != c["valor"].strip():
            dialogo.locator("input, textarea").nth(i).fill(c["valor"].strip())
    page.wait_for_timeout(300)  # confirma que o Hub não apaga de novo depois de recolocar
    try:  # o Hub às vezes apaga de novo, atrasado: espera, relê e recoloca antes de desistir
        garantir_formulario(page, dialogo, {i: c["valor"].strip() for i, c in guardar.items()})
    except ErroPulavel:
        faltou = faltas_no_formulario(dialogo, {i: c["valor"].strip() for i, c in guardar.items()})
        rot = {i: c["rotulo"] for i, c in guardar.items()}
        raise ErroPulavel(f"restauracao_falhou_{rot.get(faltou[0][0], 'campo') if faltou else 'campo'}")
    final_campos = {c["i"]: c for c in ler_campos(dialogo)}
    if so_digitos(final_campos[mapa["cep"]["i"]]["valor"]) != novo:
        raise ErroPulavel("cep_nao_ficou_no_campo")
    return guardar, antes, depois, valores(list(final_campos.values()))



def processar(page, linha, args, tela):
    idcs = so_digitos(linha["id_cliente_servico"])
    antigo, novo = so_digitos(linha["cep"]), so_digitos(linha["cep_novo"])
    OBSERVACOES_LIDAS.clear()
    DIALOGOS_VISTOS.clear()
    REABERTURAS[0] = 0
    FASES.clear()
    marca("inicio")
    cartao = abrir_servico(page, linha)
    marca("consulta_e_servicos")

    atual = cep_do_cartao(cartao)
    if atual == novo:
        return "ja_atualizado", {"cep_cartao": atual}
    if atual != antigo:
        if args.aceitar_cep_atual and args.id and atual:  # corrigir um CEP gravado errado: parte do que está no Hub
            antigo = atual
        else:
            return "cep_divergente", {"cep_cartao": atual, "cep_planilha": antigo}

    abrir_aba_enderecos(page, cartao)
    mesclado = None
    todos_lidos = None
    lista_igual = enderecos_iguais(page)
    sorteado = (not args.ler_todos_enderecos and lista_igual and args.amostra > 0 and random.random() < args.amostra)
    if args.ler_todos_enderecos or not lista_igual or sorteado:  # sorteado: mede a taxa de referência diferente  # diferem (ex.: só um tem complemento)
        # o Cadastral fica de fora: o robô abre o painel dele para editar, e fechar esse painel sem gravar derruba a janela
        todos_lidos = ler_todos_enderecos(page, lambda: reabrir_rapido(page, linha), ignorar=("cadastral",))
    dialogo = abrir_linha_cadastral(page)
    esperar_estabilizar(page, dialogo, minimo_ms=600)  # o formulário pode terminar de carregar depois de abrir
    campos = ler_campos(dialogo)
    mapa = mapear(campos)
    marca("abrir_endereco")
    if args.inspecionar:
        tela.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(tela / f"{idcs}_dialogo.png"))
        print(json.dumps(campos, ensure_ascii=False, indent=2))
        print("Campos reconhecidos:", {k: v["rotulo"] for k, v in mapa.items()})
        print("Botões do painel (dy = distância do topo):")
        for b in botoes_do_painel(dialogo):
            print("   ", b)
        return "inspecionado", {"campos": campos}

    faltando = [k for k in OBRIGATORIOS if k not in mapa]
    if faltando:
        raise ErroFatal(f"campos não encontrados na tela: {faltando}. Rode --inspecionar e ajuste PADROES.")

    if so_digitos(mapa["cep"]["valor"]) != antigo:  # formulário lido pela metade ou endereço diferente do esperado
        return "cep_formulario_divergente", {"cep_formulario": mapa["cep"]["valor"], "cep_planilha": antigo}
    n_hub, n_plan = so_digitos(mapa["numero"]["valor"]), so_digitos(linha.get("numero"))
    if n_hub != n_plan:  # inclui Hub vazio e planilha com número: o endereço Cadastral não é o da instalação
        if args.aceitar_cep_atual and args.id and not n_hub and n_plan:
            # correção de um CEP gravado errado: o Hub apagou o número; recoloca o da planilha antes de trocar o CEP
            dialogo.locator("input, textarea").nth(mapa["numero"]["i"]).fill(re.sub(r"\s+", "", linha["numero"]))
            campos = ler_campos(dialogo)
            mapa = mapear(campos)
        else:
            return "numero_divergente", {"numero_hub": mapa["numero"]["valor"], "numero_planilha": linha.get("numero")}

    if todos_lidos is not None:  # junta o Cadastral (lido agora) aos outros 3 e decide o que mesclar
        todos_lidos["cadastral"] = campos
        mesclado = mesclar_enderecos(todos_lidos)

    # campos de autocompletar (ex.: Condomínio) só guardam o valor ao escolher uma opção da lista:
    # se estiverem preenchidos, o robô não consegue recolocá-los com segurança -> deixa para ver à mão
    auto = {c["rotulo"]: c["valor"].strip() for c in campos
            if c["tipo"] == "search" and not derivado(c) and c["valor"].strip()}
    if auto:
        return "campo_autocompletar_preenchido", auto

    guardar, antes, depois, final = trocar_cep(page, dialogo, campos, mapa, novo)
    if mesclado:  # completa o Cadastral com o complemento/referência que só existiam em outro tipo
        atuais = mapear(ler_campos(dialogo))
        for chave in MESCLAR:  # (em conflito o valor é "" e o campo fica em branco)
            if atuais[chave]["valor"].strip() != mesclado[chave]:
                dialogo.locator("input, textarea").nth(atuais[chave]["i"]).fill(mesclado[chave])
        depois_m = mapear(ler_campos(dialogo))
        for chave in MESCLAR:
            if depois_m[chave]["valor"].strip() != mesclado[chave]:
                raise ErroPulavel(f"mesclagem_falhou_{chave}")
        final = valores(ler_campos(dialogo))
    atual = mapear(ler_campos(dialogo))
    motivo, info_div = avaliar_divergencia(
        {k: mapa[k]["valor"] for k in ("logradouro", "bairro", "cidade") if k in mapa},
        {k: atual[k]["valor"] for k in ("logradouro", "bairro", "cidade") if k in atual},
        linha.get("cidade"), args.bairros, args.bairro_perto_km, args.rua_parecida)
    if motivo:
        return motivo, info_div  # nada foi gravado
    marca("trocar_cep")

    detalhes = {"antes": antes, "apos_trocar_cep": depois, "final": final}
    detalhes["rua_bairro"] = info_div
    detalhes["espera_status_conexao_s"] = STATUS_CONEXAO_S[0]
    detalhes["lista_dos_4_igual"] = lista_igual
    if sorteado:
        detalhes["amostra_4_enderecos"] = True
    if REABERTURAS[0]:
        detalhes["reaberturas"] = REABERTURAS[0]
    if mesclado:
        detalhes["mesclado"] = mesclado
    if todos_lidos:  # tudo o que havia nos 4 endereços, para dar para reconstruir qualquer caso
        detalhes["enderecos_antes"] = {t: valores(cs) for t, cs in todos_lidos.items()}
    esp = esperados_do_formulario(guardar, mesclado)
    recolocados = garantir_formulario(page, dialogo, esp)
    if recolocados:
        detalhes["recolocados_antes_de_gravar"] = recolocados
    if not args.gravar:
        tela.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(tela / f"{idcs}_simulacao.png"))
        return "simulado_ok", detalhes  # nada foi gravado; a navegação seguinte descarta o formulário

    detalhes["avisos"] = gravar_endereco(page)
    marca("gravar")

    # verificação: consulta o cliente de novo (navega do zero) e relê o cartão e (opcional) o endereço
    cartao = abrir_servico(page, linha)
    if cep_do_cartao(cartao) != novo:
        raise ErroPulavel("verificacao_cep_nao_confere")
    if args.verificacao == "completa":
        faltas, reparos, lidos = verificar_e_reparar(page, linha, cartao, esp)
        detalhes["conferido"] = lidos
        if reparos:
            detalhes["reparos"] = reparos
        if faltas:
            # o endereço JÁ foi gravado: guarda tudo no log para dar para corrigir à mão
            rot = {i: c["rotulo"] for i, c in guardar.items()}
            raise ErroPulavel(f"verificacao_{rot.get(faltas[0][0], 'campo')}_nao_confere:" + json.dumps(
                {"divergencias": [(rot.get(i), e, a) for i, e, a in faltas], "antes": antes,
                 "mesclado": mesclado, "enderecos_antes": detalhes.get("enderecos_antes")}, ensure_ascii=False)[:2500])
    marca("verificacao")
    return "ok", detalhes


SINAL = [time.time()]  # último sinal de vida do laço principal (o vigia abaixo confere)


def vigiar_travamento(limite_s, marcador):
    """Se o laço principal ficar parado (página congelada: o Playwright não tem tempo limite em evaluate/click presos),
    envia as marcações pendentes e encerra o processo com código 3, para um laço externo religar o robô."""
    def vigia():
        while True:
            time.sleep(20)
            if time.time() - SINAL[0] > limite_s:
                print(f"\nTRAVADO: {limite_s // 60} min sem avançar ({datetime.now():%d/%m %H:%M}); salvando e encerrando "
                      "(código 3). Rode o mesmo comando para continuar.", flush=True)
                try:
                    if marcador:
                        marcador.enviar()
                finally:
                    os._exit(3)
    threading.Thread(target=vigia, daemon=True).start()


def diagnostico(page):
    """Lista os campos visíveis da página, para entender onde o robô parou."""
    try:
        campos = page.evaluate(JS_CAMPOS_PAGINA)
        titulos = page.evaluate(JS_TITULOS)
        print("   campos visíveis na página:")
        for c in campos[:40]:
            print("    -", c)
        print("   textos de títulos/diálogos:", titulos)
    except Exception as e:  # noqa: BLE001
        print(f"   diagnóstico falhou: {type(e).__name__}")


def texto_observacao(status, det):
    """Texto curto para a coluna de observações da planilha ('' = nada a anotar)."""
    if status in FEITOS or status == "confere":
        conflitos = (det.get("mesclado") or {}).get("_conflitos")
        if conflitos:
            return "deixado em branco por conflito: " + "; ".join(f"{k}: {' | '.join(v)}" for k, v in conflitos.items())
        return ""
    if status == "bairro_longe":
        info = f"{det['bairro'][0]} -> {det['bairro'][1]} ({det.get('bairro_km')} km)"
    elif status == "numero_divergente":
        info = f"Hub {det.get('numero_hub')!r} x planilha {det.get('numero_planilha')!r}"
    elif status in ("cep_divergente", "cep_formulario_divergente"):
        info = f"Hub {det.get('cep_cartao') or det.get('cep_formulario')} x planilha {det.get('cep_planilha')}"
    elif status == "cep_diferente":
        info = f"marcado como feito, mas o Hub mostra {det.get('cep_hub')} (planilha: {det.get('cep_planilha')})"
    elif status == "campo_autocompletar_preenchido":
        info = ", ".join(f"{k}: {v}" for k, v in det.items())
    else:
        info = det.get("info", "")
    return (f"{status}: {info}" if info else status)[:300]


class MarcadorPlanilha:
    """Escreve na planilha (Google Sheets): TRUE na coluna 'feito' e observações na coluna 'obs_robo'
    (criada no fim do cabeçalho se não existir). Não toca em nenhuma outra coluna.
    Reencontra a linha pelo id_cliente_servico a cada envio (aguenta reordenação)."""

    def __init__(self, planilha_id, credenciais, coluna, aba=None, coluna_obs="obs_robo"):
        import gspread

        sh = gspread.service_account(filename=credenciais).open_by_key(planilha_id)
        self.ws = None
        for ws in ([sh.worksheet(aba)] if aba else sh.worksheets()):
            cab = [c.strip().lower() for c in ws.row_values(1)]
            if "id_cliente_servico" in cab and coluna.lower() in cab:
                self.ws, self.col_id, self.col_feito = ws, cab.index("id_cliente_servico") + 1, cab.index(coluna.lower()) + 1
                self.n_cab = len(cab)
                self.coluna_obs = coluna_obs
                self.col_obs = cab.index(coluna_obs.lower()) + 1 if coluna_obs.lower() in cab else None
                break
        if self.ws is None:
            raise ErroFatal(f"não achei uma aba com as colunas id_cliente_servico e {coluna} (use --aba NOME)")
        self.pendentes, self.obs = [], {}

    def marcar(self, idcs):
        self.pendentes.append(idcs)
        self._talvez_enviar()

    def observar(self, idcs, texto):
        if texto:
            self.obs[idcs] = texto
            self._talvez_enviar()

    def _talvez_enviar(self):
        if len(self.pendentes) + len(self.obs) >= 10:
            self.enviar()

    def enviar(self):
        from gspread.utils import rowcol_to_a1

        if not self.pendentes and not self.obs:
            return
        try:
            if self.obs and self.col_obs is None:  # cria a coluna de observações no fim do cabeçalho
                self.col_obs = self.n_cab + 1
                if self.col_obs > self.ws.col_count:
                    self.ws.add_cols(self.col_obs - self.ws.col_count)
                self.ws.update_cell(1, self.col_obs, self.coluna_obs)
            ids = [so_digitos(x) for x in self.ws.col_values(self.col_id)]
            linha_de = {i: n for n, i in enumerate(ids, 1) if i}
            dados, sem_linha = [], []
            for idcs in self.pendentes:
                if idcs in linha_de:
                    dados.append({"range": rowcol_to_a1(linha_de[idcs], self.col_feito), "values": [[True]]})
                else:
                    sem_linha.append(idcs)
            for idcs, texto in self.obs.items():
                if idcs in linha_de:
                    dados.append({"range": rowcol_to_a1(linha_de[idcs], self.col_obs), "values": [[texto]]})
                else:
                    sem_linha.append(idcs)
            for i in range(0, len(dados), 500):
                self.ws.batch_update(dados[i:i + 500], value_input_option="USER_ENTERED")
            if sem_linha:
                print("   não achei na planilha:", sorted(set(sem_linha)))
            self.pendentes.clear()
            self.obs.clear()
        except Exception as e:  # noqa: BLE001 - falha da planilha não pode parar o robô
            print(f"   AVISO: não consegui escrever na planilha agora ({type(e).__name__}: {str(e)[:120]}); tento no próximo envio")


LOG_CONFERENCIA = LOGS / "conferencia.csv"
CONCLUIDOS = FEITOS | {"confere", "cep_diferente"}


def carregar_conferidos():
    if not LOG_CONFERENCIA.exists():
        return set()
    with open(LOG_CONFERENCIA, encoding="utf-8", newline="") as f:
        return {r["id_cliente_servico"] for r in csv.DictReader(f) if r["status"] in ("confere", "cep_diferente")}


def conferir_cartao(page, linha, args, tela):
    """Só lê: compara o CEP do cartão do serviço no Hub com o cep_novo da planilha. Não altera nada."""
    OBSERVACOES_LIDAS.clear()
    cartao = abrir_servico(page, linha)
    cep, novo = cep_do_cartao(cartao), so_digitos(linha["cep_novo"])
    if cep == novo:
        return "confere", {"cep_hub": cep}
    return "cep_diferente", {"cep_hub": cep, "cep_planilha": novo}


def proximo_horario(hhmm, agora=None):
    """Próxima ocorrência de HH:MM (hoje, ou amanhã se já passou)."""
    h, m = (int(x) for x in hhmm.split(":"))
    agora = agora or datetime.now()
    alvo = agora.replace(hour=h, minute=m, second=0, microsecond=0)
    return alvo if alvo > agora else alvo + timedelta(days=1)


def servicos_multi_numero_diferente(todas):
    """Serviços de clientes que têm a MESMA rua com NÚMEROS diferentes entre serviços. Se o Hub usa um só registro de
    endereço para eles, o Atualizar Todos de um serviço mudaria o número do outro: ficam para revisão à mão."""
    por = defaultdict(list)
    for r in todas:
        if (r.get("cidade") or "").strip().upper() in CIDADES:
            por[so_digitos(r.get("codigo_cliente"))].append(r)
    ids = set()
    for v in por.values():
        if len(v) < 2:
            continue
        grupos = defaultdict(set)
        for r in v:
            grupos[nome_rua(r.get("endereco"))].add(so_digitos(r.get("numero")))
        if any(len(n - {""}) > 1 for n in grupos.values()):
            ids |= {so_digitos(r["id_cliente_servico"]) for r in v}
    return ids


def executar(args):
    from playwright.sync_api import sync_playwright

    global DEPURAR
    DEPURAR = args.depurar
    todas = ler_linhas(args.csv)
    args.bairros = construir_bairros(todas)
    colunas = list(todas[0].keys()) if todas else []
    if any(c and c.strip().lower() == args.coluna_check.lower() for c in colunas):
        n_marcadas = sum(marcado(r, args.coluna_check) for r in todas)
        print(f"{n_marcadas} linhas marcadas em '{args.coluna_check}' (ignoradas)")
        if not n_marcadas:
            chave = next(c for c in colunas if c and c.strip().lower() == args.coluna_check.lower())
            print(f"   valores vistos na coluna: {sorted({(r[chave] or '').strip() for r in todas})[:5]}")
    else:
        print(f"AVISO: coluna '{args.coluna_check}' não encontrada; linhas já feitas NÃO serão ignoradas. Colunas: {colunas}")
    linhas = selecionar(todas, set(map(so_digitos, args.id)) if args.id else None,
                        "" if (args.aceitar_cep_atual and args.id) else args.coluna_check)  # "" = ignora a marca em feito
    if args.conferir_marcados:  # só leitura: confere no Hub os serviços já marcados como feitos
        ja = carregar_conferidos()
        linhas = [r for r in todas if marcado(r, args.coluna_check) and so_digitos(r.get("id_cliente_servico"))
                  and (r.get("cidade") or "").strip().upper() in CIDADES and len(so_digitos(r.get("cep_novo"))) == 8
                  and (not args.id or so_digitos(r["id_cliente_servico"]) in set(map(so_digitos, args.id)))
                  and so_digitos(r["id_cliente_servico"]) not in ja]
    if args.excluir_multi_servico and not args.conferir_marcados and not args.id:
        suspeitos = servicos_multi_numero_diferente(todas)
        fora = [r for r in linhas if so_digitos(r["id_cliente_servico"]) in suspeitos]
        if fora:
            linhas = [r for r in linhas if so_digitos(r["id_cliente_servico"]) not in suspeitos]
            LOGS.mkdir(exist_ok=True)
            with open(LOGS / "multi_servico_para_revisar.csv", "w", encoding="utf-8", newline="") as f:
                w = csv.writer(f)
                w.writerow(["id_cliente_servico", "codigo_cliente", "endereco", "numero", "cidade", "cep_novo"])
                w.writerows((r["id_cliente_servico"], r["codigo_cliente"], r["endereco"], r["numero"], r["cidade"],
                             r.get("cep_novo")) for r in fora)
            print(f"{len(fora)} serviços de clientes com a mesma rua e números diferentes entre serviços ficam de fora "
                  f"(lista em logs\\multi_servico_para_revisar.csv; tirar --excluir-multi-servico para incluir)")
    feitos = carregar_feitos()
    pulados = set() if (args.refazer_pulados or args.id) else carregar_pulados()
    fila = [r for r in linhas if so_digitos(r["id_cliente_servico"]) not in feitos | pulados]
    if args.conferir_marcados:
        fila = linhas  # conferir não exclui o que o robô já fez: a ideia é revisar todos os marcados
    if pulados and not args.conferir_marcados:
        print(f"{len(pulados)} serviços pulados em rodadas anteriores ficam de fora (use --refazer-pulados para tentar de novo)")
    if args.inspecionar:
        fila = fila[: args.inspecionar]
    elif not args.todos:
        fila = fila[: args.limite]
    modo = ("conferência dos marcados (só leitura)" if args.conferir_marcados else
            "inspeção" if args.inspecionar else ("GRAVAÇÃO" if args.gravar else "simulação (não grava)"))
    faltam = len([r for r in linhas if so_digitos(r["id_cliente_servico"]) not in feitos | pulados])
    print(f"Faltam {faltam} de {len(linhas)} serviços elegíveis; {len(fila)} nesta rodada. Modo: {modo}")
    if not fila:
        return
    arquivo_log = LOG_CONFERENCIA if args.conferir_marcados else (LOG_GRAVACAO if args.gravar else LOG_SIMULACAO)
    marcador = None
    if args.planilha and (args.gravar or args.conferir_marcados):
        marcador = MarcadorPlanilha(args.planilha, args.credenciais, args.coluna_check, args.aba)
        if args.gravar:
            marcador.pendentes.extend(sorted(feitos))  # recupera o que o log já tem e a planilha ainda não
        marcador.enviar()
        import atexit

        atexit.register(marcador.enviar)  # também no Ctrl+C
    funcao = conferir_cartao if args.conferir_marcados else processar
    tela = LOGS / "telas"

    def abrir(p, pedir_login=True):
        perfil = PERFIL if args.navegador == "chrome" else Path(".perfil_chromium")
        opcoes = dict(headless=False, viewport={"width": 1366, "height": 900},
                      # com a tela bloqueada o Chrome trata a janela como oculta e fica bem mais lento
                      args=["--disable-features=CalculateNativeWinOcclusion", "--disable-backgrounding-occluded-windows",
                            "--disable-renderer-backgrounding", "--disable-background-timer-throttling"])
        if args.navegador == "chrome":
            opcoes["channel"] = "chrome"
        ctx = p.chromium.launch_persistent_context(str(perfil), **opcoes)
        ctx.add_init_script(JS_INIT)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.set_default_timeout(20000)
        page.on("dialog", ao_dialogo)  # "sair da página?" ao descartar formulário
        page.on("response", lambda r: (RESPOSTAS.append((time.time(), r.status, r.url[-70:])), RESPOSTAS[:-60] and RESPOSTAS.__delitem__(slice(0, -60))))
        page.on("request", lambda r: (PEDIDOS.append((time.time(), r.method, r.url[-90:])), PEDIDOS[:-40] and PEDIDOS.__delitem__(slice(0, -40))))
        page.goto(HUB)
        if pedir_login and "/login" in page.url:
            input("Faça login na janela do navegador (inclusive validações) e tecle ENTER aqui... ")
        return ctx, page

    with sync_playwright() as p:
        ctx, page = abrir(p)
        SINAL[0] = time.time()
        if args.travado_min:
            vigiar_travamento(args.travado_min * 60, marcador)
        erros_seguidos = alarmes = sem_feito = 0
        limite_t = proximo_horario(args.ate) if args.ate else None
        if limite_t:
            print(f"O robô vai parar sozinho, depois de terminar o cliente em andamento, às {limite_t:%d/%m %H:%M}.")
        for n, linha in enumerate(fila, 1):
            if limite_t and datetime.now() >= limite_t:
                print(f"Horário limite ({limite_t:%H:%M}) atingido. Parando com tudo salvo; rode o mesmo comando para continuar.")
                break
            SINAL[0] = time.time()
            idcs = so_digitos(linha["id_cliente_servico"])
            if n > 1 and args.reiniciar and (n - 1) % args.reiniciar == 0:  # evita o navegador travar em rodadas longas
                print(f"   reiniciando o navegador (a cada {args.reiniciar} clientes)")
                ctx.close()
                ctx, page = abrir(p, pedir_login=False)
            if "/login" in page.url:
                print(f"PARANDO: o Hub voltou para a tela de login ({datetime.now():%d/%m %H:%M}); a sessão caiu ou o acesso foi "
                      "fechado por horário. Tudo salvo; entre no Hub e rode o mesmo comando para continuar.")
                break
            try:
                try:
                    status, det = funcao(page, linha, args, tela)
                except Exception as e0:  # noqa: BLE001
                    if "closed" not in str(e0).lower():
                        raise
                    # navegador fechou/travou no meio: reabre e refaz este cliente (a gravação só vale depois do
                    # "Atualizar Todos", e um cliente já atualizado vira ja_atualizado)
                    print("   o navegador fechou; reabrindo e tentando este cliente de novo")
                    ctx, page = abrir(p)
                    status, det = funcao(page, linha, args, tela)
                erros_seguidos = 0
            except ErroPulavel as e:
                status, _, info = str(e).partition(":")
                det = {"info": info} if info else {}
            except ErroFatal as e:
                registrar(arquivo_log, linha, "erro_fatal", {"erro": str(e)})
                print(f"[{n}/{len(fila)}] {idcs}: ERRO FATAL: {e}")
                break
            except Exception as e:  # noqa: BLE001 - qualquer erro inesperado
                tela.mkdir(parents=True, exist_ok=True)
                try:
                    page.screenshot(path=str(tela / f"{idcs}_erro_{datetime.now():%H%M%S}.png"))
                except Exception as e2:  # noqa: BLE001
                    print(f"   não consegui salvar a captura: {type(e2).__name__}: {str(e2)[:150]}")
                if "/login" in page.url:
                    print(f"[{n}/{len(fila)}] {idcs}: o Hub voltou para a tela de login ({datetime.now():%d/%m %H:%M}); "
                          "parando. Entre no Hub de novo e rode o mesmo comando para continuar.")
                    break
                registrar(arquivo_log, linha, "erro", {"erro": f"{type(e).__name__}: {str(e)[:300]}", "url": page.url})
                print(f"[{n}/{len(fila)}] {idcs}: ERRO inesperado: {type(e).__name__}: {str(e)[:200]}")
                print(f"   página no momento do erro: {page.url}")
                print(f"   captura: {tela}  (arquivo {idcs}_erro_HHMMSS.png)")
                diagnostico(page)
                erros_seguidos += 1
                if not args.continuar_no_erro or erros_seguidos >= 3:
                    print("Parando. Veja logs/telas/ e o log.")
                    break
                continue
            if OBSERVACOES_LIDAS:
                det = dict(det, observacoes_lidas=list(OBSERVACOES_LIDAS))
            registrar(arquivo_log, linha, status, det)
            if args.gravar and status in FEITOS:
                faltam -= 1
                if marcador:
                    marcador.marcar(idcs)
            if marcador:
                marcador.observar(idcs, texto_observacao(status, det))
            print(f"[{n}/{len(fila)}] {idcs}: {status}" + (f" ({det['info']})" if det.get("info") else "")
                  + (f"  | faltam {faltam}" if args.gravar else ""))
            if status == "simulado_ok":
                print("   antes:", det["antes"], "\n   após trocar o CEP:", det["apos_trocar_cep"])
            if status in CONCLUIDOS:
                alarmes = sem_feito = 0
            else:
                sem_feito += 1
                alarmes = alarmes + 1 if status.startswith(ALARMES) else alarmes
            if alarmes >= 2 or sem_feito >= 25:
                print(f"PARANDO: {alarmes} alertas seguidos / {sem_feito} clientes seguidos sem concluir. "
                      "Veja o log (--resumo) antes de continuar.")
                break
            if args.tempos and FASES:
                print("   tempos:", ", ".join(f"{b[0]} {b[1] - a[1]:.1f}s" for a, b in zip(FASES, FASES[1:])),
                      f"| total {FASES[-1][1] - FASES[0][1]:.1f}s")
            time.sleep(random.uniform(*args.pausa))
        if marcador:
            marcador.enviar()
        ctx.close()


def resumo(args):
    """Último status de cada serviço no log, e quantos concluídos tinham complemento/referência preenchidos."""
    from collections import Counter

    if not LOG_GRAVACAO.exists():
        print("Ainda não há log de gravação.")
        return
    ultimo = {}
    with open(LOG_GRAVACAO, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            ultimo[r["id_cliente_servico"]] = r
    por_status = Counter(r["status"] for r in ultimo.values())
    print("Último status por serviço:")
    for st, n in por_status.most_common():
        print(f"  {st}: {n}")
    preservados = 0
    for r in ultimo.values():
        if r["status"] != "ok":
            continue
        antes = json.loads(r["detalhes"] or "{}").get("antes", {})
        if any(v for k, v in antes.items() if re.search(r"Complemento|Refer[êe]ncia|Condom", k, re.I)):
            preservados += 1
    print(f"Concluídos (ok) que tinham complemento/referência/condomínio preenchidos: {preservados}")
    mudou_rua = mudou_bairro = mesclados = conflitos = 0
    pares = Counter()
    for r in ultimo.values():
        if r["status"] != "ok":
            continue
        det = json.loads(r["detalhes"] or "{}")
        rb = det.get("rua_bairro") or {}
        if rb.get("rua") and norm_txt(rb["rua"][0]) != norm_txt(rb["rua"][1]):
            mudou_rua += 1
        if rb.get("bairro") and norm_txt(rb["bairro"][0]) != norm_txt(rb["bairro"][1]):
            mudou_bairro += 1
            pares[(rb["bairro"][0], rb["bairro"][1], rb.get("bairro_km"))] += 1
        mesclados += bool(det.get("mesclado"))
        conflitos += bool((det.get("mesclado") or {}).get("_conflitos"))
    print(f"Concluídos em que o Hub mudou a rua: {mudou_rua}; o bairro: {mudou_bairro}; "
          f"com complemento/referência mesclados: {mesclados} (em conflito, deixados em branco: {conflitos})")
    for (a_, d_, k_), n_ in pares.most_common(10):
        print(f"   bairro {a_} -> {d_} ({k_} km): {n_}")
    lidos4 = dif_comp = dif_ref = dif_outro = 0
    exemplos = []
    fontes = list(ultimo.values())
    if LOG_SIMULACAO.exists():  # o levantamento também vale com o que foi lido nas simulações
        sim = {}
        with open(LOG_SIMULACAO, encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                sim[r["id_cliente_servico"]] = r
        vistos = {r["id_cliente_servico"] for r in fontes}
        fontes += [r for i, r in sim.items() if i not in vistos]
    for r in fontes:
        det = json.loads(r["detalhes"] or "{}")
        ea = det.get("enderecos_antes")
        if not ea or len(ea) < 2:
            continue
        lidos4 += 1

        def conj(rx):
            return {re.sub(r"\s+", " ", v).strip().casefold() for t in ea.values()
                    for k, v in t.items() if re.search(rx, k, re.I) and (v or "").strip()}

        def varia(rx):  # preenchido em alguns tipos e vazio/diferente em outros
            vals = [next((v.strip() for k, v in t.items() if re.search(rx, k, re.I)), "") for t in ea.values()]
            return len({re.sub(r"\s+", " ", v).casefold() for v in vals}) > 1
        c, f = varia(r"Complemento"), varia(r"Refer")
        dif_comp += c
        dif_ref += f
        if c or f:
            exemplos.append((r["id_cliente_servico"], "complemento" if c else "", "referência" if f else ""))
    if lidos4:
        print(f"Clientes com os 4 endereços lidos: {lidos4}; com COMPLEMENTO diferente entre os tipos: {dif_comp}; "
              f"com REFERÊNCIA diferente entre os tipos: {dif_ref}")
        for e in exemplos[:6]:
            print("   serviço", e[0], "difere em:", ", ".join(x for x in e[1:] if x))
    pendentes = [r for r in ultimo.values() if r["status"] not in FEITOS]
    if pendentes:
        with open(LOGS / "pendentes.csv", "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["id_cliente_servico", "codigo_cliente", "status", "detalhes"])
            w.writerows((r["id_cliente_servico"], r["codigo_cliente"], r["status"], r["detalhes"]) for r in pendentes)
        print(f"{len(pendentes)} serviços para tratar à mão em {LOGS / 'pendentes.csv'}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", help="CSV exportado da planilha (aba filtrada)")
    ap.add_argument("--inspecionar", type=int, metavar="N", help="só abre o diálogo de N clientes e lista os campos")
    ap.add_argument("--gravar", action="store_true", help="grava de verdade (sem isso, só simula)")
    ap.add_argument("--limite", type=int, default=3, help="máximo de clientes nesta rodada (padrão 3)")
    ap.add_argument("--todos", action="store_true", help="ignora --limite")
    ap.add_argument("--coluna-check", default="feito", help="nome da coluna da caixa de seleção (marcado = já feito)")
    ap.add_argument("--listar", type=int, metavar="N",
                    help="lista N clientes elegíveis com um único serviço (bons para teste) e sai")
    ap.add_argument("--planilha", metavar="ID", help="ID da planilha Google: o robô marca TRUE na coluna 'feito' a cada serviço concluído")
    ap.add_argument("--credenciais", default="credenciais_google.json", help="JSON da conta de serviço do Google")
    ap.add_argument("--aba", help="nome da aba da planilha (se omitido, acha pela coluna id_cliente_servico)")
    ap.add_argument("--resumo", action="store_true",
                    help="mostra o total por status do log e grava logs/pendentes.csv (o que precisa de tratamento à mão)")
    ap.add_argument("--exportar-feitos", action="store_true",
                    help="grava logs/ids_feitos.csv com os serviços concluídos (ok/ja_atualizado) e sai")
    ap.add_argument("--tempos", action="store_true", help="mostra quanto tempo levou cada etapa de cada cliente")
    ap.add_argument("--pausa", type=float, nargs=2, default=[0.3, 0.8], metavar=("MIN", "MAX"),
                    help="pausa aleatória entre clientes, em segundos (padrão 0.3 0.8)")
    ap.add_argument("--bairro-perto-km", type=float, default=3.0,
                    help="bairros a até X km (entre os centros) contam como próximos (padrão 3)")
    ap.add_argument("--rua-parecida", type=float, default=0.6,
                    help="semelhança mínima (0-1) para a rua contar como a mesma (padrão 0.6)")
    ap.add_argument("--navegador", choices=["chrome", "chromium"], default="chrome",
                    help="chrome = o Chrome instalado (padrão); chromium = o do Playwright, separado do seu Chrome do dia a dia")
    ap.add_argument("--reiniciar", type=int, default=100, metavar="N",
                    help="reabre o navegador a cada N clientes (0 = nunca; padrão 100)")
    ap.add_argument("--travado-min", type=int, default=8, metavar="MIN",
                    help="se um cliente ficar MIN minutos sem avançar (página congelada), salva e encerra com código 3 (0 = desligado; padrão 8)")
    ap.add_argument("--depurar", action="store_true",
                    help="tira foto do painel de endereço e lista os botões antes de fechar (mais lento; para diagnóstico)")
    ap.add_argument("--aceitar-cep-atual", action="store_true",
                    help="com --id: se o Hub tem um CEP diferente do 'antigo' da planilha, parte dele (corrige CEP gravado errado) "
                         "e ignora a marca em 'feito' desses IDs")
    ap.add_argument("--conferir-marcados", action="store_true",
                    help="só leitura: confere no Hub o CEP dos serviços já marcados em 'feito' (log em logs/conferencia.csv)")
    ap.add_argument("--ate", metavar="HH:MM",
                    help="para sozinho neste horário (termina o cliente em andamento); ex.: --ate 07:50")
    ap.add_argument("--excluir-multi-servico", action="store_true",
                    help="deixa de fora clientes com a mesma rua e números diferentes entre serviços "
                         "(no Hub os registros de endereço são separados; só use se quiser cautela extra)")
    ap.add_argument("--amostra", type=float, default=0.1, metavar="FRAC",
                    help="fração dos clientes (padrão 0.1) em que o robô lê os 4 endereços mesmo com a lista igual, "
                         "só para medir quantos têm referência diferente (0 desliga)")
    ap.add_argument("--ler-todos-enderecos", action="store_true",
                    help="lê os 4 endereços de todo cliente (mais lento, ~+4 s) em vez de só quando a lista mostra diferença")
    ap.add_argument("--refazer-pulados", action="store_true",
                    help="tenta de novo os serviços que já foram pulados (servico_nao_encontrado, numero_divergente...)")
    ap.add_argument("--id", nargs="+", help="processa só estes id_cliente_servico")
    ap.add_argument("--verificacao", choices=["card", "completa"], default="completa",
                    help="completa = reabre o endereço e confere número/complemento/referência")
    ap.add_argument("--continuar-no-erro", action="store_true", help="segue após erro inesperado (para após 3 seguidos)")
    args = ap.parse_args(argv)
    if args.resumo:
        resumo(args)
        return
    if args.exportar_feitos:
        feitos = {}
        if LOG_GRAVACAO.exists():
            with open(LOG_GRAVACAO, encoding="utf-8", newline="") as f:
                for r in csv.DictReader(f):
                    if r["status"] in FEITOS:
                        feitos[r["id_cliente_servico"]] = (r["status"], r["data_hora"])
        LOGS.mkdir(exist_ok=True)
        with open(LOGS / "ids_feitos.csv", "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["id_cliente_servico", "status", "data_hora"])
            w.writerows((i, *v) for i, v in sorted(feitos.items()))
        print(f"{len(feitos)} serviços gravados em {LOGS / 'ids_feitos.csv'}")
        return
    if args.listar:
        todas = ler_linhas(args.csv)
        por_cliente = {}
        for r in todas:
            por_cliente[so_digitos(r.get("codigo_cliente"))] = por_cliente.get(so_digitos(r.get("codigo_cliente")), 0) + 1
        unicos = [r for r in selecionar(todas, None, args.coluna_check)
                  if por_cliente[so_digitos(r["codigo_cliente"])] == 1]
        for r in unicos[: args.listar]:
            print(r["id_cliente_servico"], r["codigo_cliente"], r["endereco"], r["numero"], r["cidade"],
                  r["cep"], "->", r["cep_novo"])
        return
    try:
        executar(args)
    except ErroFatal as e:
        sys.exit(f"ERRO FATAL: {e}")


if __name__ == "__main__":
    main()
