from src.config import SeiSettings


def test_sei_settings_normalization():
    settings = SeiSettings(
        SEI_BASE_URL="https://sei.cbm.df.gov.br/sei///",
        SEI_USUARIO="12345678900",
        SEI_SENHA="minhasenhasecreta",
        SEI_ORGAO="CBM",
    )
    assert settings.base_url == "https://sei.cbm.df.gov.br/sei"
    assert settings.usuario == "12345678900"
    assert settings.orgao == "CBM"
    assert settings.is_configured() is True
    assert "***" in settings.mask_password()


def test_sei_settings_not_configured():
    settings = SeiSettings(
        SEI_BASE_URL="",
        SEI_USUARIO="",
        SEI_SENHA="",
        SEI_ORGAO="",
    )
    assert settings.is_configured() is False
