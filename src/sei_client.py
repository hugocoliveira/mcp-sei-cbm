"""
Cliente HTTP para o SEI (Sistema Eletrônico de Informações)
Responsável por gerenciar o ciclo de vida da sessão web, autenticação com usuário/senha/órgão,
manutenção de cookies e execução das requisições ao sistema com suporte a URLs assinadas (infra_hash).
"""

import logging
import re
import unicodedata
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from src.config import SeiSettings, get_settings
from src.parsers import (
    _extrair_id_procedimento,
    parse_arvore_processo,
    parse_conteudo_documento,
    parse_controle_processos,
    parse_login_error,
    parse_login_form,
    parse_pesquisa_processos,
    parse_session_info,
)

logger = logging.getLogger("mcp_sei.client")


class SeiClientError(Exception):
    """Exceção base para erros do cliente SEI."""
    pass


class SeiAuthenticationError(SeiClientError):
    """Exceção levantada em caso de falha de autenticação no SEI."""
    pass


class SeiClient:
    """
    Cliente de integração HTTP com o SEI baseado em emulação de sessão web e URLs assinadas.
    """

    def __init__(self, settings: Optional[SeiSettings] = None):
        self.settings = settings or get_settings()
        self._is_logged_in = False
        self._session_info: Dict[str, Any] = {}
        self._home_url: Optional[str] = None
        self._home_html: Optional[str] = None
        self._search_form_url: Optional[str] = None
        self._doc_urls_cache: Dict[str, str] = {}  # {id_documento: signed_url}
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
            "Connection": "keep-alive",
        }

        self.http_client = httpx.AsyncClient(
            headers=headers,
            timeout=httpx.Timeout(self.settings.timeout),
            verify=self.settings.verify_ssl,
            follow_redirects=True,
        )

    def _build_url(self, path: str) -> str:
        """Resolve a URL completa baseada na URL base do SEI."""
        if path.startswith("http://") or path.startswith("https://"):
            return path
        base = self.settings.base_url.rstrip("/") + "/"
        return urljoin(base, path.lstrip("/"))

    async def close(self):
        """Fecha a sessão HTTP do cliente."""
        await self.http_client.aclose()

    async def _ensure_logged_in(self):
        """Garante que o cliente esteja autenticado antes de executar uma ação."""
        if not self._is_logged_in or not self._home_url:
            await self.login()

    async def login(
        self,
        base_url: Optional[str] = None,
        usuario: Optional[str] = None,
        senha: Optional[str] = None,
        orgao: Optional[str] = None,
        unidade: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Executa o fluxo completo de autenticação no SEI e captura as rotas assinadas da sessão.
        """
        if base_url:
            self.settings.base_url = base_url.strip().rstrip("/")
        if usuario:
            self.settings.usuario = usuario.strip()
        if senha:
            self.settings.senha = senha
        if orgao:
            self.settings.orgao = orgao.strip()
        if unidade:
            self.settings.unidade = unidade.strip()

        if not self.settings.is_configured():
            raise SeiAuthenticationError(
                "Configurações incompletas! Parâmetros obrigatórios: SEI_BASE_URL, SEI_USUARIO, SEI_SENHA, SEI_ORGAO."
            )

        logger.info(f"Iniciando login no SEI: {self.settings.base_url} com usuário: {self.settings.usuario} e órgão: {self.settings.orgao}")

        # Passo 1: Acessar página de login para extrair formulário e mapear órgãos
        login_url = self.settings.login_url or self._build_url("controlador.php?acao=login")
        try:
            resp_init = await self.http_client.get(login_url)
            resp_init.raise_for_status()
        except Exception as e:
            try:
                login_url = self._build_url("inicio.php")
                resp_init = await self.http_client.get(login_url)
            except Exception:
                raise SeiAuthenticationError(f"Não foi possível alcançar a página de login do SEI em {login_url}: {e}")

        login_form_data = parse_login_form(resp_init.text)
        
        if login_form_data.get("has_captcha"):
            raise SeiAuthenticationError(
                "A página de login do SEI possui Captcha ativado, o que impede a autenticação automatizada direta por formulário."
            )

        # Passo 2: Mapear o valor do órgão
        orgao_param = self.settings.orgao
        orgaos_disponiveis = login_form_data.get("orgaos", {})
        valor_orgao = orgao_param

        if orgaos_disponiveis:
            for k, val in orgaos_disponiveis.items():
                if k.strip().lower() == orgao_param.lower() or val.strip().lower() == orgao_param.lower():
                    valor_orgao = val
                    break
            else:
                for k, val in orgaos_disponiveis.items():
                    if orgao_param.lower() in k.lower():
                        valor_orgao = val
                        break

        # Passo 3: Preparar payload de login
        post_url = urljoin(str(resp_init.url), login_form_data.get("action", "controlador.php?acao=login"))
        payload = {
            "txtUsuario": self.settings.usuario,
            "pwdSenha": self.settings.senha,
            "selOrgao": valor_orgao,
            "sbmLogin": "Acessar",
            "hdnOrgao": valor_orgao,
            "acao": "login",
        }
        for k, v in login_form_data.get("fields", {}).items():
            if k not in payload and v:
                payload[k] = v
        payload.update(self.settings.get_login_extra_fields())

        # Passo 4: Enviar POST de autenticação
        try:
            resp_login = await self.http_client.post(
                post_url,
                data=payload,
                headers={"Referer": str(resp_init.url)},
            )
            resp_login.raise_for_status()
        except Exception as e:
            raise SeiAuthenticationError(f"Erro na requisição de login: {e}")

        login_html = resp_login.text
        erro_msg = parse_login_error(login_html)
        if erro_msg:
            self._is_logged_in = False
            raise SeiAuthenticationError(f"Falha na autenticação do SEI: {erro_msg}")

        # Salva o estado da tela principal com as URLs assinadas
        self._home_url = str(resp_login.url)
        self._home_html = login_html
        self._session_info = parse_session_info(login_html)

        # Captura a URL assinada da barra de pesquisa
        soup_home = BeautifulSoup(login_html, "html.parser")
        form_pesq = soup_home.find("form", id=re.compile(r"frmPesquisa|frmProtocoloPesquisa|frmPesquisaRapida", re.I)) or soup_home.find("form", action=re.compile(r"pesquisa", re.I))
        if form_pesq and form_pesq.get("action"):
            self._search_form_url = urljoin(self._home_url, form_pesq.get("action"))
        else:
            self._search_form_url = self._build_url("controlador.php?acao=protocolo_pesquisa_rapida")

        self._is_logged_in = True
        logger.info(f"Login no SEI efetuado com sucesso! Unidade atual: {self._session_info.get('unidade_atual')}")

        if self.settings.unidade and self.settings.unidade != self._session_info.get("unidade_atual"):
            try:
                await self.trocar_unidade(self.settings.unidade)
            except Exception as e:
                logger.warning(f"Não foi possível alterar para a unidade configurada '{self.settings.unidade}': {e}")

        return self.get_status()

    def get_status(self) -> Dict[str, Any]:
        """Retorna o status atual da conexão e dados da sessão."""
        return {
            "autenticado": self._is_logged_in,
            "base_url": self.settings.base_url,
            "usuario": self.settings.usuario,
            "orgao": self.settings.orgao,
            "unidade_atual": self._session_info.get("unidade_atual") or self.settings.unidade or "Não identificada",
            "unidades_disponiveis": self._session_info.get("unidades_disponiveis", []),
        }

    async def trocar_unidade(self, unidade_nome_ou_id: str) -> Dict[str, Any]:
        """
        Altera a unidade de trabalho atual no SEI.
        """
        await self._ensure_logged_in()
        
        id_unidade = unidade_nome_ou_id
        for u in self._session_info.get("unidades_disponiveis", []):
            if u["nome"].strip().lower() == unidade_nome_ou_id.strip().lower() or unidade_nome_ou_id.lower() in u["nome"].lower():
                id_unidade = u["id"]
                break

        url_troca = self._build_url(f"controlador.php?acao=infra_selecionar_unidade&id_unidade={id_unidade}")
        resp = await self.http_client.get(url_troca, headers={"Referer": self._home_url or self.settings.base_url})
        
        self._home_url = str(resp.url)
        self._home_html = resp.text
        self._session_info = parse_session_info(resp.text)
        return {
            "sucesso": True,
            "mensagem": f"Unidade alterada para: {self._session_info.get('unidade_atual', unidade_nome_ou_id)}",
            "unidade_atual": self._session_info.get("unidade_atual"),
        }

    async def listar_controle_processos(self) -> Dict[str, Any]:
        """
        Lista os processos abertos na tela principal (Controle de Processos)
        da unidade atual (Processos Gerados e Processos Recebidos).
        """
        await self._ensure_logged_in()
        
        # Acessa a URL da home assinada
        url = self._home_url or self._build_url("controlador.php?acao=procedimento_controlar")
        resp = await self.http_client.get(url, headers={"Referer": self._home_url or self.settings.base_url})
        resp.raise_for_status()

        if "acao=login" in str(resp.url) or parse_login_error(resp.text):
            logger.info("Sessão expirada. Renovando autenticação...")
            await self.login()
            resp = await self.http_client.get(self._home_url or url)

        self._home_html = resp.text
        dados = parse_controle_processos(resp.text)
        status = self.get_status()
        
        return {
            "unidade": status["unidade_atual"],
            "total_gerados": len(dados["processos_gerados"]),
            "total_recebidos": len(dados["processos_recebidos"]),
            "processos_gerados": dados["processos_gerados"],
            "processos_recebidos": dados["processos_recebidos"],
        }

    async def pesquisar(self, termo: str) -> Dict[str, Any]:
        """
        Realiza busca de processos ou documentos no SEI usando a rota de pesquisa assinada.
        """
        await self._ensure_logged_in()
        termo = termo.strip()
        
        url_pesq = self._search_form_url or self._build_url("controlador.php?acao=protocolo_pesquisa_rapida")
        payload = {
            "txtPesquisaRapida": termo,
            "chkSinProcessos": "S",
            "chkSinDocumentos": "S",
        }
        
        resp = await self.http_client.post(url_pesq, data=payload, headers={"Referer": self._home_url or self.settings.base_url})
        
        if "acao=login" in str(resp.url) or parse_login_error(resp.text):
            await self.login()
            url_pesq = self._search_form_url or self._build_url("controlador.php?acao=protocolo_pesquisa_rapida")
            resp = await self.http_client.post(url_pesq, data=payload, headers={"Referer": self._home_url or self.settings.base_url})

        resultados: List[Dict[str, Any]] = []

        # Caso 1: Redirecionamento direto para a tela do processo (procedimento_trabalhar)
        id_p = _extrair_id_procedimento(str(resp.url)) or _extrair_id_procedimento(resp.text)
        if id_p and ("procedimento_trabalhar" in str(resp.url) or "id_protocolo=" in str(resp.url)):
            resultados.append({
                "numero": termo,
                "id": id_p,
                "id_procedimento": id_p,
                "link": str(resp.url),
                "resumo": f"Processo localizado diretamente: {termo}",
            })
        else:
            # Caso 2: Tabela de resultados de pesquisa
            res_lista = parse_pesquisa_processos(resp.text)
            resultados.extend(res_lista)

        return {
            "termo_pesquisado": termo,
            "total_encontrados": len(resultados),
            "resultados": resultados,
        }

    async def _ir_para_arvore(self, numero_ou_id: str) -> httpx.Response:
        """
        Navega até a página da árvore de documentos de um processo (via busca rápida) e
        retorna a resposta HTTP crua, já com uma URL/infra_hash válidos para a sessão atual.
        Usado tanto por consultar_processo quanto por operações de escrita (ex: incluir_documento)
        que precisam do link "Incluir Documento" (assinado) presente nessa página.
        """
        await self._ensure_logged_in()
        param = numero_ou_id.strip()

        # Executa pesquisa pelo termo
        url_pesq = self._search_form_url or self._build_url("controlador.php?acao=protocolo_pesquisa_rapida")
        payload = {"txtPesquisaRapida": param}

        resp = await self.http_client.post(url_pesq, data=payload, headers={"Referer": self._home_url or self.settings.base_url})

        if "acao=login" in str(resp.url) or parse_login_error(resp.text):
            await self.login()
            url_pesq = self._search_form_url or self._build_url("controlador.php?acao=protocolo_pesquisa_rapida")
            resp = await self.http_client.post(url_pesq, data=payload, headers={"Referer": self._home_url or self.settings.base_url})

        # Verifica se abriu a tela do processo diretamente
        soup_proc = BeautifulSoup(resp.text, "html.parser")
        ifr_arvore = soup_proc.find("iframe", id=re.compile(r"ifrArvore", re.I)) or soup_proc.find("iframe")

        url_arvore_src = None
        if ifr_arvore and ifr_arvore.get("src"):
            url_arvore_src = urljoin(str(resp.url), ifr_arvore.get("src"))

        if not url_arvore_src:
            # Se não abriu direto, procura link de processo nos resultados da pesquisa
            link_proc = soup_proc.find("a", href=re.compile(r"procedimento_trabalhar|procedimento_controlar|id_procedimento=", re.I))
            if link_proc and link_proc.get("href"):
                url_abrir = urljoin(str(resp.url), link_proc.get("href"))
                resp_abrir = await self.http_client.get(url_abrir, headers={"Referer": str(resp.url)})
                soup_abrir = BeautifulSoup(resp_abrir.text, "html.parser")
                ifr_arvore = soup_abrir.find("iframe", id=re.compile(r"ifrArvore", re.I)) or soup_abrir.find("iframe")
                if ifr_arvore and ifr_arvore.get("src"):
                    url_arvore_src = urljoin(str(resp_abrir.url), ifr_arvore.get("src"))

        if not url_arvore_src:
            raise SeiClientError(
                f"Processo '{numero_ou_id}' não foi localizado no SEI. "
                f"Verifique se o número está correto ou se a unidade ({self._session_info.get('unidade_atual')}) possui permissão de acesso."
            )

        # Obtém a árvore completa a partir do iframe da árvore
        resp_arv = await self.http_client.get(url_arvore_src, headers={"Referer": str(resp.url)})
        resp_arv.raise_for_status()
        return resp_arv

    async def consultar_processo(self, numero_ou_id: str) -> Dict[str, Any]:
        """
        Consulta informações e a árvore de documentos de um processo no SEI.
        Resolve números de protocolo (ex: 202600011025521 ou 202300011005879) e IDs internos.
        """
        resp_arv = await self._ir_para_arvore(numero_ou_id)
        param = numero_ou_id.strip()
        id_procedimento_resolvido = _extrair_id_procedimento(str(resp_arv.url))

        # Processos grandes têm a árvore paginada em "Pastas" (Pasta I, Pasta II, ...).
        # Por padrão só a última pasta vem carregada na resposta acima (demais nós ficam
        # como "AGUARDE" placeholder, carregado=false). Segue o link "Abrir todas as Pastas"
        # (ação ABRIR_PASTAS, sempre presente na árvore) para forçar a expansão de todas elas
        # antes de parsear — sem isso, documentos das pastas mais antigas somem da consulta.
        m_abrir = re.search(
            r'"ABRIR_PASTAS".*?"(controlador\.php\?acao=procedimento_visualizar[^"]+abrir_pastas=1[^"]+)"',
            resp_arv.text,
        )
        if m_abrir:
            url_abrir_pastas = urljoin(str(resp_arv.url), m_abrir.group(1))
            resp_abrir = await self.http_client.get(url_abrir_pastas, headers={"Referer": str(resp_arv.url)})
            if resp_abrir.status_code == 200 and "acao=login" not in str(resp_abrir.url):
                resp_arv = resp_abrir

        dados_arvore = parse_arvore_processo(resp_arv.text)
        dados_arvore["id_procedimento"] = id_procedimento_resolvido
        if not dados_arvore.get("numero_processo"):
            dados_arvore["numero_processo"] = param

        # Popula o cache de URLs dos documentos para leitura rápida posterior
        for doc in dados_arvore.get("documentos", []):
            if doc.get("id_documento") and doc.get("href"):
                full_href = urljoin(str(resp_arv.url), doc["href"])
                self._doc_urls_cache[doc["id_documento"]] = full_href

        return dados_arvore

    async def obter_arvore_processo(self, id_procedimento: str) -> Dict[str, Any]:
        """
        Obtém a árvore completa de documentos de um processo.
        """
        return await self.consultar_processo(id_procedimento)

    @staticmethod
    def _normalizar_texto(texto: str) -> str:
        """Remove acentos e caixa para comparação tolerante de nomes de tipo de documento."""
        sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
        return sem_acento.strip().lower()

    def _resolver_id_serie(self, tipo: str, html_escolher_tipo: str) -> str:
        """
        Resolve o nome de um tipo de documento (ex: "Ordem de Serviço") para o id_serie numérico
        exigido pelo SEI, a partir da lista de séries disponíveis na tela "Incluir Documento" da
        unidade atual (atributo data-desc de cada linha da tabela). Aceita também o id_serie
        numérico diretamente.
        """
        tipo_norm = tipo.strip()
        if tipo_norm.isdigit():
            return tipo_norm

        tipo_lower = self._normalizar_texto(tipo_norm)
        soup = BeautifulSoup(html_escolher_tipo, "html.parser")

        candidatos = []
        for row in soup.find_all("tr"):
            desc = row.get("data-desc")
            if not desc:
                continue
            link = row.find("a", onclick=re.compile(r"escolher\(\d+\)"))
            if not link:
                continue
            m = re.search(r"escolher\((\d+)\)", link.get("onclick", ""))
            if not m:
                continue
            desc_norm = self._normalizar_texto(desc)
            if desc_norm == tipo_lower:
                return m.group(1)
            if tipo_lower in desc_norm:
                candidatos.append(m.group(1))

        if candidatos:
            return candidatos[0]

        raise SeiClientError(
            f"Tipo de documento '{tipo}' não encontrado na lista de séries disponíveis para a unidade atual."
        )

    async def incluir_documento(
        self,
        id_procedimento: str,
        tipo: str,
        descricao: str = "",
        id_documento_modelo: Optional[str] = None,
        nivel_acesso: str = "0",
    ) -> Dict[str, Any]:
        """
        Inclui um novo documento interno em um processo do SEI, opcionalmente copiando o
        texto de um documento existente como conteúdo inicial (texto-base).

        Não edita o corpo/conteúdo do documento além do texto-base copiado — a edição fina
        do texto (CKEditor) ainda precisa ser feita manualmente no navegador.
        """
        resp_arv = await self._ir_para_arvore(id_procedimento)
        html = resp_arv.text

        m_tipo = re.search(r'(controlador\.php\?acao=documento_escolher_tipo[^"\']+)', html)
        if not m_tipo:
            raise SeiClientError(
                f"Não foi possível localizar o link de 'Incluir Documento' no processo '{id_procedimento}' "
                f"(verifique se a unidade atual tem permissão para incluir documentos nele)."
            )
        url_tipo = urljoin(str(resp_arv.url), m_tipo.group(1))
        resp_tipo = await self.http_client.get(url_tipo, headers={"Referer": str(resp_arv.url)})

        soup_tipo = BeautifulSoup(resp_tipo.text, "html.parser")
        form1 = soup_tipo.find("form", id="frmDocumentoEscolherTipo")
        if not form1:
            raise SeiClientError("Formulário de escolha de tipo de documento não encontrado na resposta do SEI.")
        action1 = urljoin(str(resp_tipo.url), form1.get("action"))
        fields1 = {inp.get("name"): inp.get("value", "") for inp in form1.find_all("input") if inp.get("name")}

        id_serie = self._resolver_id_serie(tipo, resp_tipo.text)
        fields1["hdnIdSerie"] = id_serie

        resp_gerar = await self.http_client.post(action1, data=fields1, headers={"Referer": str(resp_tipo.url)})

        soup_gerar = BeautifulSoup(resp_gerar.text, "html.parser")
        form2 = soup_gerar.find("form", id="frmDocumentoCadastro")
        if not form2:
            raise SeiClientError(
                f"O SEI não retornou o formulário de cadastro para o tipo de documento '{tipo}' "
                f"(id_serie={id_serie}) no processo '{id_procedimento}'."
            )
        action2 = urljoin(str(resp_gerar.url), form2.get("action"))

        fields2: Dict[str, str] = {}
        for inp in form2.find_all("input"):
            name = inp.get("name")
            if not name:
                continue
            if inp.get("type") == "radio":
                if inp.get("checked") is not None:
                    fields2[name] = inp.get("value", "")
                continue
            fields2[name] = inp.get("value", "")
        for sel in form2.find_all("select"):
            name = sel.get("name")
            if not name:
                continue
            opt = sel.find("option", selected=True) or sel.find("option")
            fields2[name] = opt.get("value", "") if opt else ""

        fields2["txtDescricao"] = descricao
        fields2["rdoNivelAcesso"] = nivel_acesso
        fields2["rdoFormato"] = "N"
        # O botão "Salvar" da tela de cadastro NÃO é um submit nativo: seu onclick roda
        # confirmarDados(), que só então seta hdnFlagDocumentoCadastro='2' antes de enviar o
        # formulário (submeter()). Sem replicar essa mudança de flag, o SEI apenas re-renderiza
        # a mesma tela de cadastro (HTTP 200, sem mensagem de erro) e nenhum documento é criado.
        fields2["hdnFlagDocumentoCadastro"] = "2"

        if id_documento_modelo:
            fields2["rdoTextoInicial"] = "D"
            fields2["hdnIdDocumentoTextoBase"] = id_documento_modelo
            fields2["txtProtocoloDocumentoTextoBase"] = id_documento_modelo
        else:
            fields2["rdoTextoInicial"] = "N"

        resp_save = await self.http_client.post(action2, data=fields2, headers={"Referer": str(resp_gerar.url)})

        m_novo_doc = re.search(r"[?&]id_documento=(\d+)", str(resp_save.url))
        if not m_novo_doc or "acao=documento_gerar" in str(resp_save.url):
            raise SeiClientError(
                "O documento não foi criado — o SEI reapresentou o formulário de cadastro. "
                "Verifique se o tipo de documento e os campos obrigatórios estão corretos."
            )

        return {
            "sucesso": True,
            "id_documento": m_novo_doc.group(1),
            "id_procedimento": id_procedimento,
            "id_serie": id_serie,
            "id_documento_modelo": id_documento_modelo,
            "url": str(resp_save.url),
        }

    async def ler_documento(self, id_documento: str, id_procedimento: Optional[str] = None) -> Dict[str, Any]:
        """
        Lê o conteúdo em texto e dados de assinatura de um documento específico no SEI.
        """
        await self._ensure_logged_in()
        
        # 1. Tenta recuperar a URL assinada direta do cache
        url_doc = self._doc_urls_cache.get(id_documento)

        # 2. Se não estiver em cache, consulta o processo correspondente para popular
        if not url_doc and id_procedimento:
            await self.consultar_processo(id_procedimento)
            url_doc = self._doc_urls_cache.get(id_documento)

        if not url_doc:
            # Fallback para pesquisa do próprio ID do documento
            res_pesq = await self.pesquisar(id_documento)
            if res_pesq.get("resultados"):
                link_res = res_pesq["resultados"][0].get("link")
                if link_res:
                    url_doc = self._build_url(link_res)

        if not url_doc:
            url_doc = self._build_url(f"controlador.php?acao=documento_visualizar&id_documento={id_documento}")

        resp = await self.http_client.get(url_doc, headers={"Referer": self._home_url or self.settings.base_url})
        resp.raise_for_status()

        if "acao=login" in str(resp.url) or parse_login_error(resp.text):
            await self.login()
            resp = await self.http_client.get(url_doc, headers={"Referer": self._home_url or self.settings.base_url})

        conteudo = parse_conteudo_documento(resp.text)
        conteudo["id_documento"] = id_documento
        conteudo["id_procedimento"] = id_procedimento
        conteudo["url_documento"] = str(resp.url)

        # Se houver iframe interno no documento, busca o conteúdo dentro dele
        if conteudo.get("url_anexo_iframe"):
            url_iframe = urljoin(str(resp.url), conteudo["url_anexo_iframe"])
            try:
                resp_iframe = await self.http_client.get(url_iframe, headers={"Referer": str(resp.url)})
                if resp_iframe.status_code == 200 and len(resp_iframe.text) > 30:
                    c_iframe = parse_conteudo_documento(resp_iframe.text)
                    if c_iframe.get("conteudo_texto"):
                        conteudo["conteudo_texto"] = c_iframe["conteudo_texto"]
                    if c_iframe.get("assinaturas"):
                        conteudo["assinaturas"].extend(c_iframe["assinaturas"])
            except Exception as e:
                logger.debug(f"Não foi possível carregar o iframe do documento: {e}")

        return conteudo

    async def adicionar_andamento(self, id_procedimento: str, descricao: str) -> Dict[str, Any]:
        """
        Registra uma nota de andamento no histórico do processo.
        """
        await self._ensure_logged_in()
        
        url_andamento = self._build_url(f"controlador.php?acao=andamento_registrar&id_procedimento={id_procedimento}")
        payload = {
            "txaDescricao": descricao,
            "sbmSalvar": "Salvar",
            "acao": "andamento_registrar",
            "id_procedimento": id_procedimento,
        }
        
        resp_post = await self.http_client.post(url_andamento, data=payload, headers={"Referer": self._home_url or self.settings.base_url})
        return {
            "sucesso": resp_post.status_code == 200,
            "mensagem": f"Andamento registrado no processo {id_procedimento} com sucesso.",
            "descricao": descricao,
        }

    async def diagnosticar(self, termo_teste: str = "202600011025521") -> Dict[str, Any]:
        """
        Executa um diagnóstico detalhado da conexão com o SEI e dos endpoints de consulta.
        """
        diag: Dict[str, Any] = {
            "status_sessao": self.get_status(),
            "home_url": self._home_url,
            "search_form_url": self._search_form_url,
            "endpoints": {},
        }

        # 1. Testa Controle de Processos
        try:
            ctrl = await self.listar_controle_processos()
            diag["endpoints"]["controle_processos"] = {
                "sucesso": True,
                "total_gerados": ctrl.get("total_gerados"),
                "total_recebidos": ctrl.get("total_recebidos"),
                "primeiros_recebidos": [p.get("numero") for p in ctrl.get("processos_recebidos", [])[:5]],
            }
        except Exception as e:
            diag["endpoints"]["controle_processos"] = {"erro": str(e)}

        # 2. Testa Pesquisa e Abertura do Processo de Referência
        try:
            proc = await self.consultar_processo(termo_teste)
            diag["endpoints"]["processo_teste"] = {
                "numero_processo": proc.get("numero_processo"),
                "total_documentos": len(proc.get("documentos", [])),
                "primeiros_documentos": [d.get("nome") for d in proc.get("documentos", [])[:5]],
            }
        except Exception as e:
            diag["endpoints"]["processo_teste"] = {"erro": str(e)}

        return diag
