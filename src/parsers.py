"""
Módulo de Parsers HTML para o SEI (Sistema Eletrônico de Informações)
Extrai informações estruturadas das páginas HTML, tabelas, árvores e scripts do SEI.
Compatível com SEI 3.x, 4.x e customizações estaduais (ex: SEI-GO / CBMGO).
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
        "Link sem assinatura",
        "Hash inválido",
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
    """Extrai o id_procedimento ou id_protocolo de uma string, link href ou chamada onclick."""
    if not texto_ou_url:
        return None
    m = re.search(r"id_procedimento=(\d+)|id_protocolo=(\d+)", texto_ou_url)
    if m:
        return m.group(1) or m.group(2)
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
    e extrai os processos gerados e recebidos da unidade.
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
            link = None
            # Procura link com número de processo
            for a in tr.find_all("a"):
                txt = clean_html_entities(a.get_text())
                href = a.get("href", "")
                onclick = a.get("onclick", "")
                if re.search(r"\b\d{11,17}\b|\b\d{4,}\.\d{4,}\b|\b\d+/\d{4}\b", txt):
                    link = a
                    break
                elif "procedimento_trabalhar" in href or "procedimento_trabalhar" in onclick:
                    if txt:
                        link = a
                        break

            if not link:
                continue

            numero_proc = clean_html_entities(link.get_text())
            href = link.get("href", "")
            onclick = link.get("onclick", "")

            id_proc = _extrair_id_procedimento(href) or _extrair_id_procedimento(onclick)
            if not id_proc:
                chk = tr.find("input", type="checkbox")
                if chk and chk.get("value", "").isdigit():
                    id_proc = chk.get("value")

            # Colunas adicionais
            usuario_atribuido = ""
            anotacoes = []
            for td in tr.find_all("td"):
                txt = clean_html_entities(td.get_text())
                title = td.get("title", "")
                if title:
                    anotacoes.append(title)
                # No SEI-GO, usuário atribuído aparece como link com matrícula / login
                for a_td in td.find_all("a"):
                    if "usuario_atribuicao" in a_td.get("href", ""):
                        usuario_atribuido = clean_html_entities(a_td.get_text())

            processos.append({
                "numero": numero_proc,
                "id_procedimento": id_proc,
                "href": href,
                "tipo": "",
                "usuario_atribuido": usuario_atribuido,
                "detalhes": " | ".join(anotacoes),
            })

        return processos

    tbl_gerados = soup.find("table", id=re.compile(r"tblProcessosGerados|tblGerados", re.I))
    tbl_recebidos = soup.find("table", id=re.compile(r"tblProcessosRecebidos|tblRecebidos", re.I))

    resultado["processos_gerados"] = extrair_linhas_tabela(tbl_gerados)
    resultado["processos_recebidos"] = extrair_linhas_tabela(tbl_recebidos)

    return resultado


def parse_arvore_processo(html: str) -> Dict[str, Any]:
    """
    Analisa a árvore de documentos de um processo no SEI.
    Extrai metadados do processo e lista de documentos/anexos a partir de nós JavaScript
    (Nos[...] = new infraArvoreNo(...) e Nos[...].src = '...') e tags HTML.
    """
    dados_processo: Dict[str, Any] = {
        "numero_processo": None,
        "tipo_processo": None,
        "especificacao": None,
        "interessados": [],
        "documentos": [],
    }

    # 1. Mapeamento de Nos[X].src no script
    nodes_src: Dict[int, str] = {}
    for line in html.split("\n"):
        m_src = re.search(r'Nos\[(\d+)\]\.src\s*=\s*[\'"]([^\'"]+)[\'"]', line)
        if m_src:
            nodes_src[int(m_src.group(1))] = m_src.group(2)

    # 2. Parsing de Nos[X] = new infraArvoreNo(...)
    for line in html.split("\n"):
        m_no = re.search(r'Nos\[(\d+)\]\s*=\s*new\s+infraArvoreNo\((.*?)\);', line)
        if m_no:
            idx = int(m_no.group(1))
            args = re.findall(r'"([^"]*)"|\'([^\']*)\'', m_no.group(2))
            clean_args = [a[0] if a[0] != "" else a[1] for a in args]

            if len(clean_args) >= 6:
                tipo = clean_args[0]
                id_item = clean_args[1]
                id_pai = clean_args[2]
                href = clean_args[3]
                nome = clean_html_entities(clean_args[5])
                src_url = nodes_src.get(idx) or href

                if tipo == "PROCESSO" and not dados_processo["numero_processo"]:
                    dados_processo["numero_processo"] = nome

                if tipo == "DOCUMENTO" and id_item:
                    tipo_doc = nome.split()[0] if " " in nome else nome
                    if not any(d["id_documento"] == id_item for d in dados_processo["documentos"]):
                        dados_processo["documentos"].append({
                            "id_documento": id_item,
                            "id_procedimento": id_pai if id_pai and id_pai.isdigit() else None,
                            "nome": nome,
                            "tipo": tipo_doc,
                            "assinado": "assinado" in src_url.lower() or "caneta" in src_url.lower(),
                            "href": src_url,
                        })
            elif len(clean_args) >= 4:
                id_item = clean_args[0]
                id_pai = clean_args[1]
                nome = clean_html_entities(clean_args[2])
                href = clean_args[3]
                src_url = nodes_src.get(idx) or href

                m_doc = re.search(r"id_documento=(\d+)", href) or re.search(r"id_documento=(\d+)", id_item)
                if m_doc:
                    id_doc = m_doc.group(1)
                    tipo_doc = nome.split()[0] if " " in nome else nome
                    if not any(d["id_documento"] == id_doc for d in dados_processo["documentos"]):
                        dados_processo["documentos"].append({
                            "id_documento": id_doc,
                            "id_procedimento": id_pai if id_pai and id_pai.isdigit() else None,
                            "nome": nome,
                            "tipo": tipo_doc,
                            "assinado": "assinado" in src_url.lower() or "caneta" in src_url.lower(),
                            "href": src_url,
                        })

    # 3. Fallback: Extração de nós a partir de tags HTML <a>
    soup = BeautifulSoup(html, "html.parser")
    for link in soup.find_all("a"):
        nome_doc = clean_html_entities(link.get_text())
        href = link.get("href", "")
        onclick = link.get("onclick", "")

        id_doc = None
        m_doc = re.search(r"id_documento=(\d+)", href) or re.search(r"id_documento=(\d+)", onclick)
        if m_doc:
            id_doc = m_doc.group(1)

        id_proc = _extrair_id_procedimento(href) or _extrair_id_procedimento(onclick)

        if ("procedimento_dados" in href or "procedimento_trabalhar" in href) and not dados_processo["numero_processo"]:
            if re.search(r"\d{4,}", nome_doc):
                dados_processo["numero_processo"] = nome_doc

        if id_doc and nome_doc and not any(d["id_documento"] == id_doc for d in dados_processo["documentos"]):
            tipo_doc = nome_doc.split()[0] if " " in nome_doc else nome_doc
            assinado = bool(link.parent and link.parent.find("img", src=re.compile(r"assin|caneta", re.I)))
            dados_processo["documentos"].append({
                "id_documento": id_doc,
                "id_procedimento": id_proc,
                "nome": nome_doc,
                "tipo": tipo_doc,
                "assinado": assinado,
                "href": href,
            })

    # 4. Extrai metadados do processo
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

    iframe = soup.find("iframe", id=re.compile(r"ifrVisualizacao|ifrConteudo|ifrArvoreHtml", re.I)) or soup.find("iframe")
    if iframe and iframe.get("src") and iframe.get("src") != "about:blank":
        resultado["url_anexo_iframe"] = iframe.get("src")

    titulo_elem = soup.find(["h1", "h2", "p", "title"], class_=re.compile(r"titulo|docTitulo|infraTitulo", re.I))
    if titulo_elem:
        resultado["titulo"] = clean_html_entities(titulo_elem.get_text())
    elif soup.title:
        resultado["titulo"] = clean_html_entities(soup.title.get_text())

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
    Analisa os resultados de uma pesquisa no SEI.
    """
    soup = BeautifulSoup(html, "html.parser")
    resultados: List[Dict[str, Any]] = []

    tabelas = soup.find_all("table", class_=re.compile(r"infraTable|tabelaControle|resultado", re.I)) or soup.find_all("table")
    for tabela in tabelas:
        for tr in tabela.find_all("tr"):
            link = tr.find("a", href=re.compile(r"procedimento_trabalhar|documento_visualizar|id_procedimento=|id_documento=|id_protocolo=", re.I))
            if not link:
                link = tr.find("a", onclick=re.compile(r"procedimento_trabalhar|documento_visualizar|id_procedimento=|id_documento=|id_protocolo=", re.I))

            if not link:
                continue

            num = clean_html_entities(link.get_text())
            href = link.get("href", "")
            onclick = link.get("onclick", "")
            item_id = _extrair_id_procedimento(href) or _extrair_id_procedimento(onclick)

            m_doc = re.search(r"id_documento=(\d+)", href) or re.search(r"id_documento=(\d+)", onclick)
            id_doc = m_doc.group(1) if m_doc else None

            resumo = clean_html_entities(tr.get_text(" | "))

            if not any(r["numero"] == num and r["id"] == (item_id or id_doc) for r in resultados):
                resultados.append({
                    "numero": num,
                    "id": item_id or id_doc,
                    "id_procedimento": item_id,
                    "id_documento": id_doc,
                    "link": href or onclick,
                    "resumo": resumo,
                })

    # Fallback amplo
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
