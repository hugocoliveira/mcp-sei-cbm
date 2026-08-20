"""
Cliente HTTP para o SEI (Sistema Eletrônico de Informações)
Responsável por gerenciar o ciclo de vida da sessão web, autenticação com usuário/senha/órgão,
manutenção de cookies e execução das requisições ao sistema.
"""

import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urljoin, urlparse

import httpx

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
    Cliente de integração HTTP com o SEI baseado em emulação de sessão web.
    """

    def __init__(self, settings: Optional[SeiSettings] = None):
        self.settings = settings or get_settings()
        self._is_logged_in = False
        self._session_info: Dict[str, Any] = {}
        
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
        if not self._is_logged_in:
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
        Executa o fluxo completo de autenticação no SEI.
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

        self._session_info = parse_session_info(login_html)

        # Atualiza dados de sessão acessando a tela de controle
        ctrl_url = self._build_url("controlador.php?acao=procedimento_controle")
        try:
            resp_ctrl = await self.http_client.get(ctrl_url)
            info_ctrl = parse_session_info(resp_ctrl.text)
            if info_ctrl.get("usuario_logado"):
                self._session_info["usuario_logado"] = info_ctrl["usuario_logado"]
            if info_ctrl.get("unidade_atual"):
                self._session_info["unidade_atual"] = info_ctrl["unidade_atual"]
            if info_ctrl.get("unidades_disponiveis"):
                self._session_info["unidades_disponiveis"] = info_ctrl["unidades_disponiveis"]
        except Exception:
            pass

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
        resp = await self.http_client.get(url_troca)
        
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
        url = self._build_url("controlador.php?acao=procedimento_controle")
        
        resp = await self.http_client.get(url)
        resp.raise_for_status()

        if "acao=login" in str(resp.url) or parse_login_error(resp.text):
            logger.info("Sessão expirada. Renovando autenticação...")
            await self.login()
            resp = await self.http_client.get(url)

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
        Realiza busca de processos ou documentos no SEI usando múltiplas estratégias HTTP (POST e GET).
        """
        await self._ensure_logged_in()
        termo = termo.strip()
        
        resultados: List[Dict[str, Any]] = []

        # Estratégia 1: POST para protocolo_pesquisar_rapida (Padrão SEI 3/4)
        url_p1 = self._build_url("controlador.php?acao=protocolo_pesquisar_rapida")
        payload_p1 = {
            "txtPesquisaRapida": termo,
            "chkSinProcessos": "S",
            "chkSinDocumentos": "S",
            "acao": "protocolo_pesquisar_rapida",
            "sbmPesquisar": "Pesquisar",
        }
        try:
            resp1 = await self.http_client.post(url_p1, data=payload_p1)
            # Verifica se redirecionou direto para o processo
            id_p = _extrair_id_procedimento(str(resp1.url)) or _extrair_id_procedimento(resp1.text)
            if id_p and ("procedimento_trabalhar" in str(resp1.url) or "arvore_visualizar" in resp1.text):
                resultados.append({
                    "numero": termo,
                    "id": id_p,
                    "id_procedimento": id_p,
                    "link": str(resp1.url),
                    "resumo": f"Processo localizado diretamente: {termo}",
                })
            else:
                res1 = parse_pesquisa_processos(resp1.text)
                if res1:
                    resultados.extend(res1)
        except Exception as e:
            logger.debug(f"Estratégia 1 falhou: {e}")

        # Estratégia 2: Se não encontrou, tenta POST para protocolo_pesquisar
        if not resultados:
            url_p2 = self._build_url("controlador.php?acao=protocolo_pesquisar")
            payload_p2 = {
                "txtProtocoloPesquisa": termo,
                "txtPalavraChavePesquisa": termo,
                "chkSinProcessos": "S",
                "chkSinDocumentos": "S",
                "acao": "protocolo_pesquisar",
                "sbmPesquisar": "Pesquisar",
            }
            try:
                resp2 = await self.http_client.post(url_p2, data=payload_p2)
                id_p = _extrair_id_procedimento(str(resp2.url)) or _extrair_id_procedimento(resp2.text)
                if id_p and ("procedimento_trabalhar" in str(resp2.url) or "arvore_visualizar" in resp2.text):
                    resultados.append({
                        "numero": termo,
                        "id": id_p,
                        "id_procedimento": id_p,
                        "link": str(resp2.url),
                        "resumo": f"Processo localizado diretamente: {termo}",
                    })
                else:
                    res2 = parse_pesquisa_processos(resp2.text)
                    if res2:
                        resultados.extend(res2)
            except Exception as e:
                logger.debug(f"Estratégia 2 falhou: {e}")

        # Estratégia 3: POST pesquisa_rapida
        if not resultados:
            url_p3 = self._build_url("controlador.php?acao=pesquisa_rapida")
            payload_p3 = {
                "txtPesquisaRapida": termo,
                "chkSinProcessos": "S",
                "chkSinDocumentos": "S",
                "acao": "pesquisa_rapida",
            }
            try:
                resp3 = await self.http_client.post(url_p3, data=payload_p3)
                res3 = parse_pesquisa_processos(resp3.text)
                if res3:
                    resultados.extend(res3)
            except Exception as e:
                logger.debug(f"Estratégia 3 falhou: {e}")

        # Estratégia 4: GET com parâmetros na URL
        if not resultados:
            url_p4 = self._build_url(
                f"controlador.php?acao=protocolo_pesquisar_rapida&txtPesquisaRapida={termo}&chkSinProcessos=S&chkSinDocumentos=S"
            )
            try:
                resp4 = await self.http_client.get(url_p4)
                res4 = parse_pesquisa_processos(resp4.text)
                if res4:
                    resultados.extend(res4)
            except Exception as e:
                logger.debug(f"Estratégia 4 falhou: {e}")

        # Deduplicação de resultados por ID / Número
        unicos = []
        vistos = set()
        for r in resultados:
            chave = (r.get("id"), r.get("numero"))
            if chave not in vistos:
                vistos.add(chave)
                unicos.append(r)

        return {
            "termo_pesquisado": termo,
            "total_encontrados": len(unicos),
            "resultados": unicos,
        }

    async def consultar_processo(self, numero_ou_id: str) -> Dict[str, Any]:
        """
        Consulta informações e a árvore de documentos de um processo no SEI.
        Resolve automaticamente tanto números de protocolo (ex: 202600011025521 ou 00053.000123/2026-10)
        quanto IDs internos numéricos.
        """
        await self._ensure_logged_in()
        param = numero_ou_id.strip()

        id_proc: Optional[str] = None

        # 1. Se for ID curto (<= 8 dígitos) estritamente numérico, tenta abrir diretamente
        if param.isdigit() and len(param) <= 8:
            try:
                dados_direto = await self.obter_arvore_processo(param)
                if dados_direto.get("documentos") or dados_direto.get("numero_processo"):
                    return dados_direto
            except Exception:
                pass

        # 2. Busca o número do processo usando o mecanismo multi-estratégia de pesquisa
        pesquisa_res = await self.pesquisar(param)
        for r in pesquisa_res.get("resultados", []):
            if r.get("id_procedimento"):
                id_proc = r["id_procedimento"]
                break
            elif r.get("id") and str(r.get("id")).isdigit() and len(str(r.get("id"))) <= 10:
                id_proc = str(r["id"])
                break

        # 3. Se não encontrou e o termo possui caracteres não-dígitos (pontos, traços), tenta sem pontuação
        if not id_proc:
            termo_limpo = re.sub(r"\D", "", param)
            if termo_limpo and termo_limpo != param:
                pesq_limpo = await self.pesquisar(termo_limpo)
                for r in pesq_limpo.get("resultados", []):
                    if r.get("id_procedimento"):
                        id_proc = r["id_procedimento"]
                        break
                    elif r.get("id"):
                        id_proc = str(r["id"])
                        break

        # 4. Tenta abertura direta via procedimento_dados / procedimento_trabalhar com o número
        if not id_proc:
            url_tentativa = self._build_url(f"controlador.php?acao=procedimento_trabalhar&id_procedimento={param}")
            try:
                resp_t = await self.http_client.get(url_tentativa)
                id_ext = _extrair_id_procedimento(str(resp_t.url)) or _extrair_id_procedimento(resp_t.text)
                if id_ext:
                    id_proc = id_ext
            except Exception:
                pass

        if not id_proc:
            raise SeiClientError(
                f"Processo '{numero_ou_id}' não foi localizado no SEI. "
                f"Verifique se o número está correto ou se a sua unidade atual ({self._session_info.get('unidade_atual')}) possui permissão de acesso a este processo."
            )

        return await self.obter_arvore_processo(id_proc)

    async def obter_arvore_processo(self, id_procedimento: str) -> Dict[str, Any]:
        """
        Obtém a árvore completa de documentos e metadados de um processo a partir do id_procedimento.
        """
        await self._ensure_logged_in()
        
        # 1. Consulta a tela de trabalho do procedimento
        url_trab = self._build_url(f"controlador.php?acao=procedimento_trabalhar&id_procedimento={id_procedimento}")
        resp_trab = await self.http_client.get(url_trab)
        resp_trab.raise_for_status()

        # 2. Consulta o endpoint específico da árvore (onde o SEI renderiza os nós e scripts)
        url_arvore = self._build_url(f"controlador.php?acao=arvore_visualizar&id_procedimento={id_procedimento}")
        try:
            resp_arvore = await self.http_client.get(url_arvore)
            html_arvore = resp_arvore.text
        except Exception:
            html_arvore = ""

        # Mescla os dados extraídos de ambas as páginas
        dados = parse_arvore_processo(html_arvore or resp_trab.text)
        
        # Se na árvore não veio metadados como interessados/tipo, busca em procedimento_dados
        if not dados.get("tipo_processo") or not dados.get("documentos"):
            dados_complementares = parse_arvore_processo(resp_trab.text)
            if not dados.get("tipo_processo"):
                dados["tipo_processo"] = dados_complementares.get("tipo_processo")
            if not dados.get("numero_processo"):
                dados["numero_processo"] = dados_complementares.get("numero_processo")
            if not dados.get("interessados"):
                dados["interessados"] = dados_complementares.get("interessados")
            for doc in dados_complementares.get("documentos", []):
                if not any(d["id_documento"] == doc["id_documento"] for d in dados["documentos"]):
                    dados["documentos"].append(doc)

        dados["id_procedimento"] = id_procedimento
        return dados

    async def ler_documento(self, id_documento: str, id_procedimento: Optional[str] = None) -> Dict[str, Any]:
        """
        Lê o conteúdo em texto e dados de assinatura de um documento específico no SEI.
        Se o documento for carregado em um iframe interno, o cliente segue o link para extrair o texto.
        """
        await self._ensure_logged_in()
        
        params = f"acao=documento_visualizar&id_documento={id_documento}"
        if id_procedimento:
            params += f"&id_procedimento={id_procedimento}"

        url = self._build_url(f"controlador.php?{params}")
        resp = await self.http_client.get(url)
        resp.raise_for_status()

        if "acao=login" in str(resp.url):
            await self.login()
            resp = await self.http_client.get(url)

        conteudo = parse_conteudo_documento(resp.text)
        conteudo["id_documento"] = id_documento
        conteudo["id_procedimento"] = id_procedimento
        conteudo["url_documento"] = str(resp.url)

        # Se houver um iframe de conteúdo (ex: documento gerado pelo SEI ou conversão)
        if conteudo.get("url_anexo_iframe"):
            url_iframe = self._build_url(conteudo["url_anexo_iframe"])
            try:
                resp_iframe = await self.http_client.get(url_iframe)
                if resp_iframe.status_code == 200 and len(resp_iframe.text) > 50:
                    conteudo_iframe = parse_conteudo_documento(resp_iframe.text)
                    if conteudo_iframe.get("conteudo_texto"):
                        conteudo["conteudo_texto"] = conteudo_iframe["conteudo_texto"]
                    if conteudo_iframe.get("assinaturas"):
                        conteudo["assinaturas"].extend(conteudo_iframe["assinaturas"])
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
        
        resp_post = await self.http_client.post(url_andamento, data=payload)
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
            "endpoints": {},
        }

        # 1. Testa Controle de Processos
        url_ctrl = self._build_url("controlador.php?acao=procedimento_controle")
        try:
            resp_ctrl = await self.http_client.get(url_ctrl)
            diag["endpoints"]["controle_processos"] = {
                "status_code": resp_ctrl.status_code,
                "url_final": str(resp_ctrl.url),
                "tamanho_html": len(resp_ctrl.text),
                "contem_tblGerados": "tblProcessosGerados" in resp_ctrl.text or "divGerados" in resp_ctrl.text,
                "contem_tblRecebidos": "tblProcessosRecebidos" in resp_ctrl.text or "divRecebidos" in resp_ctrl.text,
            }
        except Exception as e:
            diag["endpoints"]["controle_processos"] = {"erro": str(e)}

        # 2. Testa Pesquisa com o termo
        pesq = await self.pesquisar(termo_teste)
        diag["endpoints"]["pesquisa_teste"] = {
            "termo": termo_teste,
            "total_encontrados": pesq.get("total_encontrados", 0),
            "resultados": pesq.get("resultados", []),
        }

        return diag
