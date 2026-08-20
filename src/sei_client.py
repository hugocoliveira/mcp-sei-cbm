"""
Cliente HTTP para o SEI (Sistema Eletrônico de Informações)
Responsável por gerenciar o ciclo de vida da sessão web, autenticação com usuário/senha/órgão,
manutenção de cookies e execução das requisições ao sistema.
"""

import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse

import httpx

from src.config import SeiSettings, get_settings
from src.parsers import (
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
        
        # Configuração do cliente HTTP com gerenciamento automático de cookies
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
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
        Executa o fluxo completo de autenticação no SEI:
        1. Atualiza credenciais (se informadas como override).
        2. Obtém a página de login para coletar tokens e mapear o ID do órgão.
        3. Envia o formulário POST de autenticação (txtUsuario, pwdSenha, selOrgao).
        4. Valida se a sessão foi estabelecida com sucesso.
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
        login_url = self._build_url("controlador.php?acao=login")
        try:
            resp_init = await self.http_client.get(login_url)
            resp_init.raise_for_status()
        except Exception as e:
            # Tenta fallback para início ou SIP caso controlador.php?acao=login falhe
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
            # Procura casamento exato por chave (nome ou sigla)
            for k, val in orgaos_disponiveis.items():
                if k.strip().lower() == orgao_param.lower() or val.strip().lower() == orgao_param.lower():
                    valor_orgao = val
                    break
            else:
                # Procura casamento parcial
                for k, val in orgaos_disponiveis.items():
                    if orgao_param.lower() in k.lower():
                        valor_orgao = val
                        break

        # Passo 3: Preparar payload de login
        post_url = self._build_url(login_form_data.get("action", "controlador.php?acao=login"))
        payload = {
            "txtUsuario": self.settings.usuario,
            "pwdSenha": self.settings.senha,
            "selOrgao": valor_orgao,
            "sbmLogin": "Acessar",
            "hdnOrgao": valor_orgao,
            "acao": "login",
        }
        # Adiciona quaisquer campos ocultos identificados
        for k, v in login_form_data.get("fields", {}).items():
            if k not in payload and v:
                payload[k] = v

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

        # Passo 5: Analisar resposta de login
        login_html = resp_login.text
        erro_msg = parse_login_error(login_html)
        if erro_msg:
            self._is_logged_in = False
            raise SeiAuthenticationError(f"Falha na autenticação do SEI: {erro_msg}")

        # Verifica se estamos em uma página logada (Controle de Processos ou Menu Principal)
        self._session_info = parse_session_info(login_html)

        # Se não capturou dados da sessão no HTML do post, faz uma requisição para a tela de controle
        if not self._session_info.get("usuario_logado"):
            ctrl_url = self._build_url("controlador.php?acao=procedimento_controle")
            try:
                resp_ctrl = await self.http_client.get(ctrl_url)
                if "procedimento_controle" in str(resp_ctrl.url) or "Controle de Processos" in resp_ctrl.text:
                    self._session_info = parse_session_info(resp_ctrl.text)
            except Exception:
                pass

        self._is_logged_in = True
        logger.info(f"Login no SEI efetuado com sucesso! Unidade atual: {self._session_info.get('unidade_atual')}")

        # Se foi configurada uma unidade específica e for diferente da atual, tenta trocar
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
        
        # Primeiro localiza o ID da unidade se foi passado o nome/sigla
        id_unidade = unidade_nome_ou_id
        for u in self._session_info.get("unidades_disponiveis", []):
            if u["nome"].strip().lower() == unidade_nome_ou_id.strip().lower() or unidade_nome_ou_id.lower() in u["nome"].lower():
                id_unidade = u["id"]
                break

        url_troca = self._build_url(f"controlador.php?acao=infra_selecionar_unidade&id_unidade={id_unidade}")
        resp = await self.http_client.get(url_troca)
        
        # Atualiza informações de sessão
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

        # Verifica se a sessão expirou
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

    async def consultar_processo(self, numero_ou_id: str) -> Dict[str, Any]:
        """
        Consulta informações e a árvore de documentos de um processo no SEI
        a partir do seu número formatado (ex: 00000.000000/2026-00) ou ID de procedimento.
        """
        await self._ensure_logged_in()
        
        # Se for numérico longo sem formatação, tenta direto pelo id_procedimento
        id_proc = None
        if numero_ou_id.isdigit() and len(numero_ou_id) >= 6:
            id_proc = numero_ou_id
        else:
            # Realiza pesquisa rápida para localizar o id_procedimento correspondente
            pesquisa_res = await self.pesquisar(numero_ou_id)
            if pesquisa_res.get("resultados"):
                primeiro = pesquisa_res["resultados"][0]
                id_proc = primeiro.get("id")

        if not id_proc:
            # Tenta abrir diretamente com o parâmetro se fornecido
            url_tentativa = self._build_url(f"controlador.php?acao=procedimento_trabalhar&id_procedimento={numero_ou_id}")
            resp_tentativa = await self.http_client.get(url_tentativa)
            if "procedimento_trabalhar" in str(resp_tentativa.url) or "arvore" in resp_tentativa.text:
                id_proc = numero_ou_id

        if not id_proc:
            raise SeiClientError(f"Processo '{numero_ou_id}' não foi encontrado no SEI.")

        # Obtém os dados da árvore
        return await self.obter_arvore_processo(id_proc)

    async def obter_arvore_processo(self, id_procedimento: str) -> Dict[str, Any]:
        """
        Obtém a árvore completa de documentos e metadados de um processo.
        """
        await self._ensure_logged_in()
        url = self._build_url(f"controlador.php?acao=procedimento_trabalhar&id_procedimento={id_procedimento}")
        resp = await self.http_client.get(url)
        resp.raise_for_status()

        # Também consulta a visualização direta da árvore se disponível
        url_arvore = self._build_url(f"controlador.php?acao=arvore_visualizar&id_procedimento={id_procedimento}")
        try:
            resp_arvore = await self.http_client.get(url_arvore)
            conteudo_arvore = resp_arvore.text
        except Exception:
            conteudo_arvore = resp.text

        dados = parse_arvore_processo(conteudo_arvore)
        dados["id_procedimento"] = id_procedimento
        return dados

    async def ler_documento(self, id_documento: str, id_procedimento: Optional[str] = None) -> Dict[str, Any]:
        """
        Lê o conteúdo em texto e dados de assinatura de um documento específico no SEI.
        """
        await self._ensure_logged_in()
        
        params = f"acao=documento_visualizar&id_documento={id_documento}"
        if id_procedimento:
            params += f"&id_procedimento={id_procedimento}"

        url = self._build_url(f"controlador.php?{params}")
        resp = await self.http_client.get(url)
        resp.raise_for_status()

        # Se retornou redirecionamento para login, renova a sessão
        if "acao=login" in str(resp.url):
            await self.login()
            resp = await self.http_client.get(url)

        conteudo = parse_conteudo_documento(resp.text)
        conteudo["id_documento"] = id_documento
        conteudo["id_procedimento"] = id_procedimento
        conteudo["url_documento"] = str(resp.url)
        return conteudo

    async def pesquisar(self, termo: str) -> Dict[str, Any]:
        """
        Realiza uma pesquisa rápida de processos ou documentos no SEI.
        """
        await self._ensure_logged_in()
        
        # Tenta pesquisa rápida padrão do SEI
        url_pesquisa = self._build_url(
            f"controlador.php?acao=protocolo_pesquisar_rapida&txtPesquisaRapida={termo}&chkSinProcessos=S&chkSinDocumentos=S"
        )
        resp = await self.http_client.get(url_pesquisa)
        
        # Se falhar ou redirecionar para tela de pesquisa padrão
        if resp.status_code != 200 or len(resp.text) < 200:
            url_pesquisa = self._build_url(
                f"controlador.php?acao=protocolo_pesquisar&txtPalavraChavePesquisa={termo}"
            )
            resp = await self.http_client.get(url_pesquisa)

        resultados = parse_pesquisa_processos(resp.text)
        return {
            "termo_pesquisado": termo,
            "total_encontrados": len(resultados),
            "resultados": resultados,
        }

    async def adicionar_andamento(self, id_procedimento: str, descricao: str) -> Dict[str, Any]:
        """
        Registra uma nota de andamento no histórico do processo.
        """
        await self._ensure_logged_in()
        
        url_andamento = self._build_url(f"controlador.php?acao=andamento_registrar&id_procedimento={id_procedimento}")
        # Obtém formulário de andamento
        resp_form = await self.http_client.get(url_andamento)
        
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
