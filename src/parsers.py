"""
Módulo de Parsers HTML para o SEI (Sistema Eletrônico de Informações)
Extrai informações estruturadas das páginas HTML, tabelas e árvores do SEI.
"""

import re
from typing import Any, Dict, List, Optional
from bs4 import BeautifulSoup


def parse_login_form(html: str) -> Dict[str, Any]:
    """
    Analisa a página de login do SEI/SIP e extrai campos ocultos e opções de órgãos.
    """
    soup = BeautifulSoup(html, "html.parser")
    data: Dict[str, Any] = {
        "action": "controlador.php?acao=login",
        "fields": {},
        "orgaos": {},  # {nome/sigla: valor_option}
        "has_captcha": False,
    }

    # Detecta formulário de login
    form = soup.find("form", id=re.compile(r"frmLogin|frm_login", re.I)) or soup.find("form")
    if form and form.get("action"):
        data["action"] = form.get("action")

    # Extrai inputs ocultos ou pré-definidos
    for inp in soup.find_all("input"):
        name = inp.get("name")
        val = inp.get("value", "")
        if name:
            data["fields"][name] = val

    # Detecta se há captcha na tela
    if soup.find("img", id=re.compile(r"captcha|imgCodigo", re.I)) or soup.find("input", id=re.compile(r"captcha|txtCodigo", re.I)):
        data["has_captcha"] = True

    # Extrai lista de órgãos disponíveis
    sel_orgao = soup.find("select", id=re.compile(r"selOrgao|id_orgao|sel_orgao", re.I))
    if sel_orgao:
        for opt in sel_orgao.find_all("option"):
            text = opt.get_text(strip=True)
            val = opt.get("value", "")
            if val and text:
                data["orgaos"][text] = val
                # Mapeia também por sigla limpa
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

    # Classes comuns de mensagem no SEI / InfraPHP
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
            text = msg_elem.get_text(strip=True)
            if text:
                return text

    # Procura por caixas de erro genéricas
    for div in soup.find_all(["div", "span", "p"], id=re.compile(r"mensagem|divMensagem|lblMensagem", re.I)):
        text = div.get_text(strip=True)
        if text and len(text) > 3:
            return text

    # Padrões comuns no texto
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

    # Usuário
    elem_user = soup.find(id=re.compile(r"lnkUsuario|lblUsuario|infraSpanUsuario", re.I))
    if elem_user:
        info["usuario_logado"] = elem_user.get_text(strip=True)

    # Unidade Atual
    elem_unidade = soup.find(id=re.compile(r"lnkInfraUnidade|lblUnidade|infraSpanUnidade|selInfraUnidades", re.I))
    if elem_unidade:
        info["unidade_atual"] = elem_unidade.get_text(strip=True)

    # Unidades disponíveis no seletor
    sel_unidades = soup.find("select", id=re.compile(r"selInfraUnidades|selUnidade", re.I))
    if sel_unidades:
        for opt in sel_unidades.find_all("option"):
            text = opt.get_text(strip=True)
            val = opt.get("value", "")
            if val:
                info["unidades_disponiveis"].append({"nome": text, "id": val, "selecionada": opt.has_attr("selected")})
                if opt.has_attr("selected") and not info["unidade_atual"]:
                    info["unidade_atual"] = text

    return info


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

    # Procura tabelas de processos gerados e recebidos
    def extrair_linhas_tabela(tabela) -> List[Dict[str, Any]]:
        processos = []
        if not tabela:
            return processos

        linhas = tabela.find_all("tr")
        for tr in linhas:
            link = tr.find("a", href=re.compile(r"procedimento_trabalhar|id_procedimento=", re.I))
            if not link:
                continue

            numero_proc = link.get_text(strip=True)
            href = link.get("href", "")

            # Extrai id_procedimento do href ou onclick
            id_proc = None
            match_id = re.search(r"id_procedimento=(\d+)", href)
            if match_id:
                id_proc = match_id.group(1)
            else:
                onclick = link.get("onclick", "")
                match_id = re.search(r"id_procedimento=(\d+)|(\d{6,})", onclick)
                if match_id:
                    id_proc = match_id.group(1) or match_id.group(2)

            # Colunas adicionais
            tds = tr.find_all("td")
            tipo_proc = ""
            usuario_atribuido = ""
            anotacao = ""

            for td in tds:
                txt = td.get_text(strip=True)
                title = td.get("title", "")
                if "@" in txt or (len(txt) < 30 and "/" not in txt and txt != numero_proc and not tipo_proc):
                    # Possível usuário atribuído
                    if not usuario_atribuido and txt and len(txt) > 2:
                        usuario_atribuido = txt
                if title:
                    anotacao = f"{anotacao} {title}".strip()

            processos.append({
                "numero": numero_proc,
                "id_procedimento": id_proc,
                "href": href,
                "tipo": tipo_proc,
                "usuario_atribuido": usuario_atribuido,
                "detalhes": anotacao,
            })

        return processos

    # Tabelas por ID padrão
    tbl_gerados = soup.find("table", id=re.compile(r"tblProcessosGerados|tblGerados", re.I))
    tbl_recebidos = soup.find("table", id=re.compile(r"tblProcessosRecebidos|tblRecebidos", re.I))

    # Se não encontrou por ID específico, procura tabelas que contenham links de procedimento
    if not tbl_gerados and not tbl_recebidos:
        tabelas = soup.find_all("table", class_=re.compile(r"infraTable", re.I))
        if len(tabelas) >= 2:
            tbl_recebidos = tabelas[0]
            tbl_gerados = tabelas[1]
        elif len(tabelas) == 1:
            tbl_recebidos = tabelas[0]

    resultado["processos_gerados"] = extrair_linhas_tabela(tbl_gerados)
    resultado["processos_recebidos"] = extrair_linhas_tabela(tbl_recebidos)

    # Se ainda estiver vazio, faz varredura ampla por todos os links de processo
    if not resultado["processos_gerados"] and not resultado["processos_recebidos"]:
        todos_links = soup.find_all("a", href=re.compile(r"procedimento_trabalhar|id_procedimento=", re.I))
        for l in todos_links:
            num = l.get_text(strip=True)
            if re.search(r"\d{4,}|\d+/\d+", num):
                m_id = re.search(r"id_procedimento=(\d+)", l.get("href", ""))
                resultado["processos_recebidos"].append({
                    "numero": num,
                    "id_procedimento": m_id.group(1) if m_id else None,
                    "href": l.get("href", ""),
                    "tipo": "",
                    "usuario_atribuido": "",
                    "detalhes": "",
                })

    return resultado


def parse_arvore_processo(html: str) -> Dict[str, Any]:
    """
    Analisa a árvore de documentos de um processo no SEI.
    Extrai metadados do processo e lista de documentos/anexos.
    """
    soup = BeautifulSoup(html, "html.parser")
    dados_processo = {
        "numero_processo": None,
        "tipo_processo": None,
        "especificacao": None,
        "interessados": [],
        "documentos": [],
    }

    # Metadados do processo
    for span in soup.find_all(["span", "div", "td", "label"]):
        txt = span.get_text(strip=True)
        if "Tipo do Processo:" in txt or "Tipo:" in txt:
            dados_processo["tipo_processo"] = txt.split(":")[-1].strip()
        elif "Especificação:" in txt:
            dados_processo["especificacao"] = txt.split(":")[-1].strip()
        elif "Interessado" in txt:
            dados_processo["interessados"].append(txt.split(":")[-1].strip())

    # Procura nós da árvore (links de documentos)
    # No SEI, links de documentos costumam conter documento_visualizar ou arvore_visualizar
    links_doc = soup.find_all("a", href=re.compile(r"documento_visualizar|id_documento=", re.I))

    # Também pode estar no formato JavaScript `infraArvore` ou nós com ID/imagens
    for link in links_doc:
        nome_doc = link.get_text(strip=True)
        href = link.get("href", "")
        if not nome_doc or nome_doc == "":
            continue

        id_doc = None
        match_id = re.search(r"id_documento=(\d+)", href)
        if match_id:
            id_doc = match_id.group(1)

        id_proc = None
        match_proc = re.search(r"id_procedimento=(\d+)", href)
        if match_proc:
            id_proc = match_proc.group(1)

        # Determina tipo e número do documento
        tipo_doc = nome_doc
        if " " in nome_doc:
            partes = nome_doc.split(" ", 1)
            tipo_doc = partes[0]

        # Verifica se está assinado (ícone de caneta ou texto)
        parent = link.parent
        assinado = False
        if parent:
            if parent.find("img", src=re.compile(r"assinatura|caneta|assinado", re.I)):
                assinado = True

        dados_processo["documentos"].append({
            "id_documento": id_doc,
            "id_procedimento": id_proc,
            "nome": nome_doc,
            "tipo": tipo_doc,
            "assinado": assinado,
            "href": href,
        })

    # Procura número do processo na árvore
    if not dados_processo["numero_processo"]:
        proc_link = soup.find("a", href=re.compile(r"procedimento_dados|id_procedimento=", re.I))
        if proc_link:
            dados_processo["numero_processo"] = proc_link.get_text(strip=True)

    return dados_processo


def parse_conteudo_documento(html: str) -> Dict[str, Any]:
    """
    Analisa a página de visualização de um documento do SEI e extrai o texto limpo,
    cabeçalhos, parágrafos, tabelas e dados de assinaturas.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Remove scripts, estilos e barras de navegação do SEI
    for elem in soup.find_all(["script", "style", "nav", "noscript"]):
        elem.decompose()

    for elem in soup.find_all(class_=re.compile(r"infraBarra|infraMenu|infraBotoes|no-print", re.I)):
        elem.decompose()

    resultado = {
        "titulo": None,
        "conteudo_texto": "",
        "assinaturas": [],
        "html_limpo": "",
    }

    # Título do documento
    titulo_elem = soup.find(["h1", "h2", "p"], class_=re.compile(r"titulo|docTitulo|infraTitulo", re.I))
    if titulo_elem:
        resultado["titulo"] = titulo_elem.get_text(strip=True)

    # Extrai blocos de assinaturas
    # Geralmente contêm "Documento assinado eletronicamente por"
    blocos_assinatura = soup.find_all(string=re.compile(r"assinado eletronicamente por|assinatura eletrônica", re.I))
    for ass in blocos_assinatura:
        parent_tag = ass.find_parent(["div", "p", "td", "tr", "table"])
        if parent_tag:
            texto_ass = parent_tag.get_text(strip=True)
            if texto_ass not in resultado["assinaturas"]:
                resultado["assinaturas"].append(texto_ass)

    # Corpo principal do documento
    corpo = (
        soup.find("div", id=re.compile(r"corpo|divConteudo|divTexto", re.I))
        or soup.find("div", class_=re.compile(r"corpo|conteudoDocumento", re.I))
        or soup.find("body")
        or soup
    )

    linhas = []
    for tag in corpo.find_all(["p", "h1", "h2", "h3", "h4", "h5", "li", "td", "th", "div"]):
        # Evita duplicação se o pai já processou
        if tag.name == "div" and tag.find(["p", "li", "table"]):
            continue
        texto = tag.get_text(" ", strip=True)
        if texto and texto not in linhas:
            linhas.append(texto)

    resultado["conteudo_texto"] = "\n\n".join(linhas) if linhas else corpo.get_text("\n", strip=True)
    resultado["html_limpo"] = str(corpo)

    return resultado


def parse_pesquisa_processos(html: str) -> List[Dict[str, Any]]:
    """
    Analisa os resultados de uma pesquisa rápida ou pesquisa avançada no SEI.
    """
    soup = BeautifulSoup(html, "html.parser")
    resultados = []

    # Procura tabela de resultados de pesquisa
    tabela = soup.find("table", id=re.compile(r"tblPesquisa|tblProtocolos|tblResultado", re.I)) or soup.find(
        "table", class_=re.compile(r"infraTable", re.I)
    )

    if tabela:
        for tr in tabela.find_all("tr"):
            link_proc = tr.find("a", href=re.compile(r"procedimento_trabalhar|documento_visualizar|id_procedimento=", re.I))
            if not link_proc:
                continue

            numero = link_proc.get_text(strip=True)
            href = link_proc.get("href", "")
            m_id = re.search(r"id_procedimento=(\d+)|id_documento=(\d+)", href)
            item_id = m_id.group(1) or m_id.group(2) if m_id else None

            texto_linha = tr.get_text(" | ", strip=True)

            resultados.append({
                "numero": numero,
                "id": item_id,
                "link": href,
                "resumo": texto_linha,
            })
    else:
        # Fallback para lista de links
        for link in soup.find_all("a", href=re.compile(r"procedimento_trabalhar|id_procedimento=", re.I)):
            num = link.get_text(strip=True)
            if re.search(r"\d{4,}", num):
                m_id = re.search(r"id_procedimento=(\d+)", link.get("href", ""))
                resultados.append({
                    "numero": num,
                    "id": m_id.group(1) if m_id else None,
                    "link": link.get("href", ""),
                    "resumo": num,
                })

    return resultados
