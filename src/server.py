"""
Servidor MCP (Model Context Protocol) para o SEI (Sistema Eletrônico de Informações).
Expõe ferramentas para interação automatizada com o SEI.
"""

import asyncio
import json
import logging
import sys
from typing import Any, Dict, Optional

try:
    from mcp.server import MCPServer as MCPApp
except ImportError:
    from mcp.server.fastmcp import FastMCP as MCPApp

from src.config import get_settings
from src.sei_client import SeiClient, SeiClientError

# Configuração de Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stderr)],
)
logger = logging.getLogger("mcp_sei.server")

# Inicialização do MCP Server
mcp = MCPApp(
    name="SEI - Sistema Eletrônico de Informações",
    instructions="""Servidor MCP para integração e automação com o SEI (Sistema Eletrônico de Informações).
Permite consultar processos, listar a caixa de entrada da unidade, obter árvores de documentos, ler o teor de minutas e despachos, pesquisar processos e gerenciar a unidade de trabalho atual no SEI.""",
)

# Instância global do cliente SEI
sei_client = SeiClient()


@mcp.tool(name="sei_status", description="Verifica o status atual da conexão com o SEI, informando o usuário logado, órgão e a unidade ativa.")
async def sei_status() -> str:
    """Verifica se há uma sessão ativa com o SEI e retorna informações de status."""
    try:
        status = sei_client.get_status()
        return json.dumps(status, indent=2, ensure_ascii=False)
    except Exception as e:
        return f"Erro ao verificar status do SEI: {str(e)}"


@mcp.tool(
    name="sei_conectar",
    description="Realiza login ou reconfigura as credenciais de acesso ao SEI (URL, Usuário, Senha e Órgão)."
)
async def sei_conectar(
    base_url: Optional[str] = None,
    usuario: Optional[str] = None,
    senha: Optional[str] = None,
    orgao: Optional[str] = None,
    unidade: Optional[str] = None,
) -> str:
    """
    Autentica no SEI utilizando as credenciais fornecidas ou as configuradas no arquivo .env.
    
    Args:
        base_url: URL base do SEI (ex: https://sei.cbm.df.gov.br/sei).
        usuario: Usuário ou CPF para login.
        senha: Senha de acesso ao SEI.
        orgao: Nome ou código do órgão no seletor de login (ex: CBM).
        unidade: (Opcional) Sigla ou código da unidade padrão a ser ativada.
    """
    try:
        resultado = await sei_client.login(
            base_url=base_url,
            usuario=usuario,
            senha=senha,
            orgao=orgao,
            unidade=unidade,
        )
        return f"Autenticado no SEI com sucesso!\n{json.dumps(resultado, indent=2, ensure_ascii=False)}"
    except Exception as e:
        return f"Falha na autenticação com o SEI: {str(e)}"


@mcp.tool(
    name="sei_listar_controle_processos",
    description="Lista todos os processos visíveis na tela de 'Controle de Processos' (Caixa de Entrada da Unidade atual), divididos entre Gerados e Recebidos."
)
async def sei_listar_controle_processos() -> str:
    """Obtém a lista de processos na caixa de entrada da unidade atual no SEI."""
    try:
        dados = await sei_client.listar_controle_processos()
        return json.dumps(dados, indent=2, ensure_ascii=False)
    except Exception as e:
        return f"Erro ao listar controle de processos do SEI: {str(e)}"


@mcp.tool(
    name="sei_consultar_processo",
    description="Consulta os detalhes, metadados e a árvore de documentos de um processo pelo seu número formatado (ex: 00000.000000/2026-00) ou ID."
)
async def sei_consultar_processo(numero_ou_id: str) -> str:
    """
    Busca um processo específico no SEI e retorna seus documentos e interessados.
    
    Args:
        numero_ou_id: Número do processo (ex: 00000.000000/2026-00) ou ID de procedimento.
    """
    try:
        processo = await sei_client.consultar_processo(numero_ou_id)
        return json.dumps(processo, indent=2, ensure_ascii=False)
    except Exception as e:
        return f"Erro ao consultar processo '{numero_ou_id}': {str(e)}"


@mcp.tool(
    name="sei_obter_arvore_processo",
    description="Retorna a árvore completa de documentos e anexos pertencentes a um processo específico no SEI."
)
async def sei_obter_arvore_processo(id_procedimento: str) -> str:
    """
    Obtém a estrutura hierárquica de documentos de um processo a partir do id_procedimento.
    
    Args:
        id_procedimento: Identificador numérico interno do processo no SEI.
    """
    try:
        arvore = await sei_client.obter_arvore_processo(id_procedimento)
        return json.dumps(arvore, indent=2, ensure_ascii=False)
    except Exception as e:
        return f"Erro ao obter árvore do processo '{id_procedimento}': {str(e)}"


@mcp.tool(
    name="sei_ler_documento",
    description="Lê o teor textual completo e dados de assinaturas de um documento específico dentro do SEI."
)
async def sei_ler_documento(id_documento: str, id_procedimento: Optional[str] = None) -> str:
    """
    Extrai e retorna o conteúdo textual e assinaturas de um documento do SEI.
    
    Args:
        id_documento: Identificador numérico do documento no SEI.
        id_procedimento: (Opcional) Identificador do processo associado.
    """
    try:
        documento = await sei_client.ler_documento(id_documento, id_procedimento)
        return json.dumps(documento, indent=2, ensure_ascii=False)
    except Exception as e:
        return f"Erro ao ler documento '{id_documento}': {str(e)}"


@mcp.tool(
    name="sei_pesquisar",
    description="Pesquisa processos ou documentos no SEI por palavras-chave, número ou interessado."
)
async def sei_pesquisar(termo: str) -> str:
    """
    Realiza busca no acervo do SEI.
    
    Args:
        termo: Termo ou número a ser pesquisado.
    """
    try:
        resultados = await sei_client.pesquisar(termo)
        return json.dumps(resultados, indent=2, ensure_ascii=False)
    except Exception as e:
        return f"Erro ao pesquisar '{termo}': {str(e)}"


@mcp.tool(
    name="sei_trocar_unidade",
    description="Altera a unidade de trabalho ativa no SEI para outra unidade permitida ao usuário."
)
async def sei_trocar_unidade(unidade: str) -> str:
    """
    Troca a unidade ativa do usuário logado.
    
    Args:
        unidade: Sigla ou ID numérico da unidade de destino (ex: CBM/DTI ou 110000001).
    """
    try:
        resultado = await sei_client.trocar_unidade(unidade)
        return json.dumps(resultado, indent=2, ensure_ascii=False)
    except Exception as e:
        return f"Erro ao trocar para a unidade '{unidade}': {str(e)}"


@mcp.tool(
    name="sei_adicionar_andamento",
    description="Adiciona um andamento/observação no histórico de um processo no SEI."
)
async def sei_adicionar_andamento(id_procedimento: str, descricao: str) -> str:
    """
    Registra um andamento no histórico do processo.
    
    Args:
        id_procedimento: Identificador numérico do processo no SEI.
        descricao: Texto do andamento a ser registrado.
    """
    try:
        resultado = await sei_client.adicionar_andamento(id_procedimento, descricao)
        return json.dumps(resultado, indent=2, ensure_ascii=False)
    except Exception as e:
        return f"Erro ao registrar andamento no processo '{id_procedimento}': {str(e)}"


def main():
    """Ponto de entrada para execução do MCP Server via stdio."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
