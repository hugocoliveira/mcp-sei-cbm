"""
Módulo de Parsers HTML para o SEI (Sistema Eletrônico de Informações)
Extrai informações estruturadas das páginas HTML, tabelas, árvores e scripts do SEI.
Compatível com SEI 3.x, 4.x e customizações estaduais/federais.
"""

import html as html_lib
import re
from typing import Any, Dict, List, Optional
from bs4 import BeautifulSoup


def clean_html_entities(text: str) -> str:
    """Decodifica entidades HTML e limpa espaços duplicados."""
    if not text:
        return ""
    text = html_lib.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def parse_login_form(html: str) -> Dict[str, Any]:
    """
    Analisa a página de login do SEI/SIP e extrai campos ocultos e opções de órgãos.
    """
    soup = BeautifulSoup(html, "html.parser")
    data: Dict[str, Any] = {
        "action": "controlador.php?acao=login",
        "fields": {},
        "orgaos": {},
        "has_captcha": False,
    }

    form = soup.find("form", id=re.compile(r"frmLogin|frm_login", re.I)) or soup.find("form")
    if form and form.get("action"):
        data["action"] = form.get("action")

    for inp in soup.find_all("input"):
        name = inp.get("name")
        val = inp.get("value", "")
        if name:
            data["fields"][name] = val

    if soup.find("img", id=re.compile(r"captcha|imgCodigo", re.I)) or soup.find("input", id=re.compile(r"captcha|txtCodigo", re.I)):
        data["has_captcha"] = True

    sel_orgao = soup.find("select", id=re.compile(r"selOrgao|id_orgao|sel_orgao", re.I))
    if sel_orgao:
        for opt in sel_orgao.find_all("option"):
            text = clean_html_entities(opt.get_text())
            val = opt.get("value", "")
            if val and text:
                data["orgaos"][text] = val
                sigla_match = re.search(r"\((.*?)\)|^([A-Za-z0-9_-]+)", text)
                if sigla_match:
                    sigla = (sigla_match.group(1) or sigla_match.group(2)).strip()
                    data["orgaos"][sigla] = val

    return data


def parse_login_error(html: str) -> Optional[str]:
    """
    Extrai mensagens de erro ou alerta de login, se presentes no HTML.
    """
    soup = BeautifulSoup(html, "html.parser")

    classes_alerta = [
        "infraMensagemAlerta",
        "infraMensagemErro",
        "infraMensagemAviso",
        "alert",
        "alert-danger",
        "msgErro",
    ]
    for cls in classes_alerta:
        msg_elem = soup.find(class_=re.compile(cls, re.I))
        if msg_elem:
            text = clean_html_entities(msg_elem.get_text())
            if text:
                return text

    for div in soup.find_all(["div", "span", "p"], id=re.compile(r"mensagem|divMensagem|lblMensagem", re.I)):
        text = clean_html_entities(div.get_text())
        if text and len(text) > 3:
            return text

    texto_puro = soup.get_text()
    for erro_padrao in [
        "Usuário ou senha inválidos",
        "Acesso negado",
        "Órgão inválido",
        "Usuário desativado",
        "Senha expirada",
        "Código de segurança inválido",
    ]:
        if erro_padrao.lower() in texto_puro.lower():
            return erro_padrao

    return None


def parse_session_info(html: str) -> Dict[str, Any]:
    """
    Extrai dados da sessão ativa do usuário: usuário logado, unidade ativa e órgãos.
    """
    soup = BeautifulSoup(html, "html.parser")
    info: Dict[str, Any] = {
        "usuario_logado": None,
        "orgao": None,
        "unidade_atual": None,
        "unidades_disponiveis": [],
    }

    elem_user = soup.find(id=re.compile(r"lnkUsuario|lblUsuario|infraSpanUsuario|spnUsuario", re.I))
    if elem_user:
        info["usuario_logado"] = clean_html_entities(elem_user.get_text())

    elem_unidade = soup.find(id=re.compile(r"lnkInfraUnidade|lblUnidade|infraSpanUnidade|spnUnidade", re.I))
    if elem_unidade:
        info["unidade_atual"] = clean_html_entities(elem_unidade.get_text())

    sel_unidades = soup.find("select", id=re.compile(r"selInfraUnidades|selUnidade|selUnidades", re.I))
    if sel_unidades:
        for opt in sel_unidades.find_all("option"):
            text = clean_html_entities(opt.get_text())
            val = opt.get("value", "")
            if val:
                info["unidades_disponiveis"].append({
                    "nome": text,
                    "id": val,
                    "selecionada": opt.has_attr("selected"),
                })
                if opt.has_attr("selected") and not info["unidade_atual"]:
                    info["unidade_atual"] = text

    return info


def _extrair_id_procedimento(texto_ou_url: str) -> Optional[str]:
    """Extrai o id_procedimento de uma string, link href ou chamada onclick."""
    if not texto_ou_url:
        return None
    m = re.search(r"id_procedimento=(\d+)", texto_ou_url)
    if m:
        return m.group(1)
    m = re.search(r"trabalharProcedimento\(['\"]?(\d+)['\"]?", texto_ou_url)
    if m:
        return m.group(1)
    m = re.search(r"infraAbreJanela\([^)]*id_procedimento=(\d+)", texto_ou_url)
    if m:
        return m.group(1)
    return None


def parse_controle_processos(html: str) -> Dict[str, List[Dict[str, Any]]]:
    """
    Analisa a tela principal do SEI (Controle de Processos)
    e extrai os processos gerados e recebidos da unidade com suporte a múltiplos formatos de DOM.
    """
    soup = BeautifulSoup(html, "html.parser")
    resultado = {
        "processos_gerados": [],
        "processos_recebidos": [],
    }

    def extrair_linhas_tabela(tabela) -> List[Dict[str, Any]]:
        processos = []
        if not tabela:
            return processos

        for tr in tabela.find_all("tr"):
            # Procura por link ou elemento com id_procedimento ou padrão de processo
            link = tr.find("a", href=re.compile(r"procedimento_trabalhar|id_procedimento=", re.I))
            if not link:
                link = tr.find("a", onclick=re.compile(r"procedimento_trabalhar|id_procedimento=|trabalharProcedimento", re.I))
            
            # Se não encontrou link com ação, procura link com formato de número de protocolo
            if not link:
                for a in tr.find_all("a"):
                    txt_a = clean_html_entities(a.get_text())
                    if re.search(r"\d{4,}", txt_a):
                        link = a
                        break

            if not link:
                continue

            numero_proc = clean_html_entities(link.get_text())
            href = link.get("href", "")
            onclick = link.get("onclick", "")

            id_proc = _extrair_id_procedimento(href) or _extrair_id_procedimento(onclick)
            
            # Se ainda não encontrou id_proc, procura nos checkboxes da linha
            if not id_proc:
                chk = tr.find("input", type="checkbox")
                if chk and chk.get("value", "").isdigit():
                    id_proc = chk.get("value")

            # Colunas adicionais
            tds = tr.find_all("td")
            usuario_atribuido = ""
            anotacoes = []

            for td in tds:
                txt = clean_html_entities(td.get_text())
                title = td.get("title", "")
                if title:
                    anotacoes.append(title)
                if "@" in txt or (len(txt) < 35 and "/" not in txt and txt != numero_proc and len(txt) > 2):
                    if not usuario_atribuido:
                        usuario_atribuido = txt

            processos.append({
                "numero": numero_proc,
                "id_procedimento": id_proc,
                "href": href,
                "tipo": "",
                "usuario_atribuido": usuario_atribuido,
                "detalhes": " | ".join(anotacoes),
            })

        return processos

    # Tabelas por ID padrão
    tbl_gerados = soup.find("table", id=re.compile(r"tblProcessosGerados|tblGerados", re.I))
    tbl_recebidos = soup.find("table", id=re.compile(r"tblProcessosRecebidos|tblRecebidos", re.I))

    if not tbl_gerados and not tbl_recebidos:
        # Busca dentro de divs estruturais (SEI 4)
        div_gerados = soup.find("div", id=re.compile(r"divGerados|divProcessosGerados", re.I))
        div_recebidos = soup.find("div", id=re.compile(r"divRecebidos|divProcessosRecebidos", re.I))
        if div_gerados:
            tbl_gerados = div_gerados.find("table")
        if div_recebidos:
            tbl_recebidos = div_recebidos.find("table")

    if not tbl_gerados and not tbl_recebidos:
        tabelas = soup.find_all("table", class_=re.compile(r"infraTable|tabelaControle", re.I))
        if len(tabelas) >= 2:
            tbl_recebidos = tabelas[0]
            tbl_gerados = tabelas[1]
        elif len(tabelas) == 1:
            tbl_recebidos = tabelas[0]

    resultado["processos_gerados"] = extrair_linhas_tabela(tbl_gerados)
    resultado["processos_recebidos"] = extrair_linhas_tabela(tbl_recebidos)

    # Fallback amplo caso tabelas não sigam padrão usual
    if not resultado["processos_gerados"] and not resultado["processos_recebidos"]:
        for a in soup.find_all("a"):
            num = clean_html_entities(a.get_text())
            href = a.get("href", "")
            onclick = a.get("onclick", "")
            if re.search(r"^\d{11,17}$|^\d{4,}\.\d{4,}|\d+/\d{4}", num):
                id_p = _extrair_id_procedimento(href) or _extrair_id_procedimento(onclick)
                resultado["processos_recebidos"].append({
                    "numero": num,
                    "id_procedimento": id_p,
                    "href": href,
                    "tipo": "",
                    "usuario_atribuido": "",
                    "detalhes": "",
                })

    return resultado


def parse_arvore_processo(html: str) -> Dict[str, Any]:
    """
    Analisa a árvore de documentos de um processo no SEI.
    Extrai metadados do processo e lista de documentos/anexos a partir de HTML estático
    e de arrays JavaScript (infraArvoreNo / Nos[...]).
    """
    soup = BeautifulSoup(html, "html.parser")
    dados_processo: Dict[str, Any] = {
        "numero_processo": None,
        "tipo_processo": None,
        "especificacao": None,
        "interessados": [],
        "documentos": [],
    }

    # Metadados do processo nos textos
    for elem in soup.find_all(["span", "div", "td", "label", "p"]):
        txt = clean_html_entities(elem.get_text())
        if "Tipo do Processo:" in txt or "Tipo:" in txt:
            dados_processo["tipo_processo"] = txt.split(":")[-1].strip()
        elif "Especificação:" in txt:
            dados_processo["especificacao"] = txt.split(":")[-1].strip()
        elif "Interessado" in txt and ":" in txt:
            interessado = txt.split(":")[-1].strip()
            if interessado and interessado not in dados_processo["interessados"]:
                dados_processo["interessados"].append(interessado)

    # 1. Extrai nós a partir de tags HTML <a>
    for link in soup.find_all("a"):
        nome_doc = clean_html_entities(link.get_text())
        href = link.get("href", "")
        onclick = link.get("onclick", "")

        id_doc = None
        m_doc = re.search(r"id_documento=(\d+)", href) or re.search(r"id_documento=(\d+)", onclick)
        if m_doc:
            id_doc = m_doc.group(1)

        id_proc = _extrair_id_procedimento(href) or _extrair_id_procedimento(onclick)

        # Se for link de processo em si
        if ("procedimento_dados" in href or "procedimento_trabalhar" in href) and not dados_processo["numero_processo"]:
            if re.search(r"\d{4,}", nome_doc):
                dados_processo["numero_processo"] = nome_doc

        if id_doc and nome_doc:
            tipo_doc = nome_doc.split()[0] if " " in nome_doc else nome_doc
            assinado = bool(link.parent and link.parent.find("img", src=re.compile(r"assin|caneta", re.I)))

            # Evita duplicatas
            if not any(d["id_documento"] == id_doc for d in dados_processo["documentos"]):
                dados_processo["documentos"].append({
                    "id_documento": id_doc,
                    "id_procedimento": id_proc,
                    "nome": nome_doc,
                    "tipo": tipo_doc,
                    "assinado": assinado,
                    "href": href,
                })

    # 2. Extrai nós gerados em JavaScript pelo InfraArvore do SEI (ex: Nos[X] = new infraArvoreNo(...))
    # Padrão: infraArvoreNo('D12345', 'P9876', 'Despacho 10 (12345)', 'controlador.php?acao=documento_visualizar&id_documento=12345...', ...)
    js_pattern = re.compile(
        r"infraArvoreNo\s*\(\s*['\"](?P<id_no>[^'\"]+)['\"]\s*,\s*['\"](?P<pai>[^'\"]*)['\"]\s*,\s*['\"](?P<nome>[^'\"]+)['\"]\s*,\s*['\"](?P<href>[^'\"]+)['\"]",
        re.I,
    )
    for m in js_pattern.finditer(html):
        nome_doc = clean_html_entities(m.group("nome"))
        href_doc = m.group("href")
        
        m_doc = re.search(r"id_documento=(\d+)", href_doc)
        id_doc = m_doc.group(1) if m_doc else None
        id_proc = _extrair_id_procedimento(href_doc)

        if not id_doc and ("procedimento_dados" in href_doc or "procedimento_trabalhar" in href_doc):
            if not dados_processo["numero_processo"]:
                dados_processo["numero_processo"] = nome_doc

        if id_doc and not any(d["id_documento"] == id_doc for d in dados_processo["documentos"]):
            tipo_doc = nome_doc.split()[0] if " " in nome_doc else nome_doc
            dados_processo["documentos"].append({
                "id_documento": id_doc,
                "id_procedimento": id_proc,
                "nome": nome_doc,
                "tipo": tipo_doc,
                "assinado": "assinado" in href_doc.lower() or "caneta" in href_doc.lower(),
                "href": href_doc,
            })

    # 3. Se ainda não encontrou número do processo, procura regex de protocolo no texto
    if not dados_processo["numero_processo"]:
        m_prot = re.search(r"\b(\d{15,17}|\d{5}\.\d{6}/\d{4}-\d{2})\b", html)
        if m_prot:
            dados_processo["numero_processo"] = m_prot.group(1)

    return dados_processo


def parse_conteudo_documento(html: str) -> Dict[str, Any]:
    """
    Analisa a página de visualização de um documento do SEI e extrai o texto limpo,
    cabeçalhos, parágrafos, tabelas e dados de assinaturas.
    """
    soup = BeautifulSoup(html, "html.parser")

    for elem in soup.find_all(["script", "style", "nav", "noscript"]):
        elem.decompose()

    for elem in soup.find_all(class_=re.compile(r"infraBarra|infraMenu|infraBotoes|no-print", re.I)):
        elem.decompose()

    resultado = {
        "titulo": None,
        "conteudo_texto": "",
        "assinaturas": [],
        "url_anexo_iframe": None,
        "html_limpo": "",
    }

    # Detecta se há iframe com anexo/download
    iframe = soup.find("iframe", id=re.compile(r"ifrVisualizacao|ifrConteudo", re.I)) or soup.find("iframe")
    if iframe and iframe.get("src"):
        resultado["url_anexo_iframe"] = iframe.get("src")

    titulo_elem = soup.find(["h1", "h2", "p"], class_=re.compile(r"titulo|docTitulo|infraTitulo", re.I))
    if titulo_elem:
        resultado["titulo"] = clean_html_entities(titulo_elem.get_text())

    blocos_assinatura = soup.find_all(string=re.compile(r"assinado eletronicamente por|assinatura eletrônica", re.I))
    for ass in blocos_assinatura:
        parent_tag = ass.find_parent(["div", "p", "td", "tr", "table"])
        if parent_tag:
            texto_ass = clean_html_entities(parent_tag.get_text())
            if texto_ass and texto_ass not in resultado["assinaturas"]:
                resultado["assinaturas"].append(texto_ass)

    corpo = (
        soup.find("div", id=re.compile(r"corpo|divConteudo|divTexto|conteudo", re.I))
        or soup.find("div", class_=re.compile(r"corpo|conteudoDocumento|secaoDocumento", re.I))
        or soup.find("body")
        or soup
    )

    linhas = []
    for tag in corpo.find_all(["p", "h1", "h2", "h3", "h4", "h5", "li", "td", "th", "div"]):
        if tag.name == "div" and tag.find(["p", "li", "table"]):
            continue
        texto = clean_html_entities(tag.get_text(" "))
        if texto and texto not in linhas and not any(texto in ass for ass in resultado["assinaturas"]):
            linhas.append(texto)

    resultado["conteudo_texto"] = "\n\n".join(linhas) if linhas else clean_html_entities(corpo.get_text("\n"))
    resultado["html_limpo"] = str(corpo)

    return resultado


def parse_pesquisa_processos(html: str) -> List[Dict[str, Any]]:
    """
    Analisa os resultados de uma pesquisa rápida ou avançada no SEI.
    Extrai processos e documentos listados na página.
    """
    soup = BeautifulSoup(html, "html.parser")
    resultados: List[Dict[str, Any]] = []

    # 1. Procura por linhas de tabela
    tabelas = soup.find_all("table", class_=re.compile(r"infraTable|tabelaControle|resultado", re.I)) or soup.find_all("table")
    for tabela in tabelas:
        for tr in tabela.find_all("tr"):
            link = tr.find("a", href=re.compile(r"procedimento_trabalhar|documento_visualizar|id_procedimento=|id_documento=", re.I))
            if not link:
                link = tr.find("a", onclick=re.compile(r"procedimento_trabalhar|documento_visualizar|id_procedimento=|id_documento=", re.I))

            if not link:
                continue

            num = clean_html_entities(link.get_text())
            href = link.get("href", "")
            onclick = link.get("onclick", "")
            item_id = _extrair_id_procedimento(href) or _extrair_id_procedimento(onclick)
            
            m_doc = re.search(r"id_documento=(\d+)", href) or re.search(r"id_documento=(\d+)", onclick)
            id_doc = m_doc.group(1) if m_doc else None

            resumo = clean_html_entities(tr.get_text(" | "))

            if not any(r["id"] == (item_id or id_doc) and r["numero"] == num for r in resultados):
                resultados.append({
                    "numero": num,
                    "id": item_id or id_doc,
                    "id_procedimento": item_id,
                    "id_documento": id_doc,
                    "link": href or onclick,
                    "resumo": resumo,
                })

    # 2. Procura em divs de resultados (SEI 4)
    divs_resultado = soup.find_all("div", class_=re.compile(r"resultadoPesquisa|pesquisaResultado|resultadoItem", re.I))
    for div in divs_resultado:
        link = div.find("a")
        if not link:
            continue
        num = clean_html_entities(link.get_text())
        href = link.get("href", "")
        onclick = link.get("onclick", "")
        item_id = _extrair_id_procedimento(href) or _extrair_id_procedimento(onclick)
        resumo = clean_html_entities(div.get_text(" | "))

        if not any(r["numero"] == num for r in resultados):
            resultados.append({
                "numero": num,
                "id": item_id,
                "id_procedimento": item_id,
                "link": href or onclick,
                "resumo": resumo,
            })

    # 3. Fallback: Qualquer link com padrão de processo ou id_procedimento
    if not resultados:
        for a in soup.find_all("a"):
            num = clean_html_entities(a.get_text())
            href = a.get("href", "")
            onclick = a.get("onclick", "")
            id_p = _extrair_id_procedimento(href) or _extrair_id_procedimento(onclick)
            if id_p or re.search(r"^\d{11,17}$|^\d{4,}\.\d{4,}", num):
                if not any(r["numero"] == num for r in resultados):
                    resultados.append({
                        "numero": num,
                        "id": id_p,
                        "id_procedimento": id_p,
                        "link": href or onclick,
                        "resumo": num,
                    })

    return resultados
