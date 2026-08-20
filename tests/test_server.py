from src.server import mcp


def test_mcp_server_initialization():
    assert mcp.name == "SEI - Sistema Eletrônico de Informações"
