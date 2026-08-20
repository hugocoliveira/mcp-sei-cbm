"""
Módulo de Configuração para o MCP SEI
Carrega e valida as variáveis de ambiente e parâmetros de conexão.
"""

import json
import logging
from typing import Dict, Optional
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger("mcp_sei.config")


class SeiSettings(BaseSettings):
    """Configurações de conexão e credenciais do SEI."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    base_url: str = Field(
        default="",
        alias="SEI_BASE_URL",
        description="URL base do SEI (ex: https://sei.cbm.df.gov.br/sei)",
    )
    usuario: str = Field(
        default="",
        alias="SEI_USUARIO",
        description="Usuário ou CPF para autenticação no SEI",
    )
    senha: str = Field(
        default="",
        alias="SEI_SENHA",
        description="Senha de acesso ao SEI",
    )
    orgao: str = Field(
        default="",
        alias="SEI_ORGAO",
        description="Órgão de acesso (sigla ou ID no dropdown de login, ex: CBM)",
    )
    unidade: Optional[str] = Field(
        default=None,
        alias="SEI_UNIDADE",
        description="Unidade padrão ativa (sigla ou ID, ex: CBM/DTI)",
    )
    login_url: Optional[str] = Field(
        default=None,
        alias="SEI_LOGIN_URL",
        description=(
            "URL completa alternativa para a página de login (ex: bridge de SSO "
            "institucional, tipo MdGoiasLoginSEI.php). Sobrescreve o padrão "
            "controlador.php?acao=login quando definida."
        ),
    )
    login_extra_fields: Optional[str] = Field(
        default=None,
        alias="SEI_LOGIN_EXTRA_FIELDS",
        description=(
            'JSON com campos extras/override para o POST de login (ex: '
            '\'{"hdnAcao":"2"}\'), para paginas de login cujo JS de submit '
            "altera campos ocultos alem do que aparece no HTML estatico."
        ),
    )
    timeout: float = Field(
        default=30.0,
        alias="SEI_TIMEOUT",
        description="Tempo limite das requisições HTTP em segundos",
    )
    verify_ssl: bool = Field(
        default=True,
        alias="SEI_VERIFY_SSL",
        description="Validar certificado SSL do servidor SEI",
    )
    debug: bool = Field(
        default=False,
        alias="SEI_DEBUG",
        description="Ativar logs detalhados de debug",
    )

    @field_validator("base_url")
    @classmethod
    def normalize_base_url(cls, v: str) -> str:
        """Remove barras finais da URL base para padronização."""
        if v:
            v = v.strip().rstrip("/")
        return v

    def is_configured(self) -> bool:
        """Verifica se os parâmetros mínimos obrigatórios foram informados."""
        return bool(self.base_url and self.usuario and self.senha and self.orgao)

    def mask_password(self) -> str:
        """Retorna a senha mascarada para exibição segura em logs/status."""
        if not self.senha:
            return ""
        if len(self.senha) <= 3:
            return "***"
        return f"{self.senha[:2]}***{self.senha[-1]}"

    def get_login_extra_fields(self) -> Dict[str, str]:
        """Decodifica SEI_LOGIN_EXTRA_FIELDS (JSON) em dict; vazio se nao configurado/invalido."""
        if not self.login_extra_fields:
            return {}
        try:
            data = json.loads(self.login_extra_fields)
            return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
        except (json.JSONDecodeError, AttributeError) as e:
            logger.warning(f"SEI_LOGIN_EXTRA_FIELDS invalido (esperado JSON de objeto): {e}")
            return {}


def get_settings() -> SeiSettings:
    """Instancia e retorna as configurações atuais."""
    return SeiSettings()
