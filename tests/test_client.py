import pytest
import httpx
from src.config import SeiSettings
from src.sei_client import SeiClient, SeiAuthenticationError


@pytest.mark.asyncio
async def test_sei_client_unconfigured():
    settings = SeiSettings(SEI_BASE_URL="", SEI_USUARIO="", SEI_SENHA="", SEI_ORGAO="")
    client = SeiClient(settings=settings)
    with pytest.raises(SeiAuthenticationError) as exc:
        await client.login()
    assert "Configurações incompletas" in str(exc.value)
    await client.close()


@pytest.mark.asyncio
async def test_sei_client_status():
    settings = SeiSettings(
        SEI_BASE_URL="https://sei.cbm.df.gov.br/sei",
        SEI_USUARIO="12345678900",
        SEI_SENHA="segredo",
        SEI_ORGAO="CBM",
        SEI_UNIDADE="CBM/DTI",
    )
    client = SeiClient(settings=settings)
    status = client.get_status()
    assert status["autenticado"] is False
    assert status["usuario"] == "12345678900"
    assert status["orgao"] == "CBM"
    assert status["unidade_atual"] == "CBM/DTI"
    await client.close()
