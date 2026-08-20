from src.parsers import (
    parse_arvore_processo,
    parse_conteudo_documento,
    parse_controle_processos,
    parse_login_error,
    parse_login_form,
    parse_pesquisa_processos,
    parse_session_info,
)


def test_parse_login_form():
    html = """
    <html>
      <body>
        <form id="frmLogin" action="controlador.php?acao=login" method="post">
          <input type="hidden" name="hdnSec" value="xyz987" />
          <input type="text" name="txtUsuario" id="txtUsuario" />
          <input type="password" name="pwdSenha" id="pwdSenha" />
          <select id="selOrgao" name="selOrgao">
            <option value="0">Corpo de Bombeiros Militar (CBM)</option>
            <option value="1">Polícia Militar (PM)</option>
          </select>
          <button type="submit">Acessar</button>
        </form>
      </body>
    </html>
    """
    form_data = parse_login_form(html)
    assert form_data["action"] == "controlador.php?acao=login"
    assert "CBM" in form_data["orgaos"] or "Corpo de Bombeiros Militar (CBM)" in form_data["orgaos"]
    assert form_data["has_captcha"] is False
    assert form_data["fields"].get("hdnSec") == "xyz987"


def test_parse_login_error():
    html = """
    <html>
      <body>
        <div class="infraMensagemAlerta">Usuário ou senha inválidos.</div>
      </body>
    </html>
    """
    error = parse_login_error(html)
    assert error == "Usuário ou senha inválidos."


def test_parse_session_info():
    html = """
    <html>
      <body>
        <span id="lnkUsuario">JOAO DA SILVA</span>
        <span id="lnkInfraUnidade">CBM/DTI</span>
        <select id="selInfraUnidades">
          <option value="101" selected>CBM/DTI</option>
          <option value="102">CBM/GAB</option>
        </select>
      </body>
    </html>
    """
    info = parse_session_info(html)
    assert info["usuario_logado"] == "JOAO DA SILVA"
    assert info["unidade_atual"] == "CBM/DTI"
    assert len(info["unidades_disponiveis"]) == 2


def test_parse_controle_processos():
    html = """
    <html>
      <body>
        <table id="tblProcessosRecebidos" class="infraTable">
          <tr>
            <td><a href="controlador.php?acao=procedimento_trabalhar&id_procedimento=1234567">00053.000123/2026-10</a></td>
            <td>Ofício</td>
            <td>chagas.silva</td>
          </tr>
        </table>
        <table id="tblProcessosGerados" class="infraTable">
          <tr>
            <td><a href="controlador.php?acao=procedimento_trabalhar&id_procedimento=7654321">00053.000456/2026-20</a></td>
            <td>Aquisição</td>
            <td>admin</td>
          </tr>
        </table>
      </body>
    </html>
    """
    res = parse_controle_processos(html)
    assert len(res["processos_recebidos"]) == 1
    assert res["processos_recebidos"][0]["numero"] == "00053.000123/2026-10"
    assert res["processos_recebidos"][0]["id_procedimento"] == "1234567"

    assert len(res["processos_gerados"]) == 1
    assert res["processos_gerados"][0]["numero"] == "00053.000456/2026-20"
    assert res["processos_gerados"][0]["id_procedimento"] == "7654321"


def test_parse_arvore_processo():
    html = """
    <html>
      <body>
        <div><span>Tipo do Processo: Aquisição de Equipamentos</span></div>
        <div><span>Interessado: CBMDF</span></div>
        <div id="divArvore">
          <a href="controlador.php?acao=documento_visualizar&id_documento=11111&id_procedimento=99999">Despacho 123 (11111)</a>
          <a href="controlador.php?acao=documento_visualizar&id_documento=22222&id_procedimento=99999">Termo de Referência (22222)</a>
        </div>
      </body>
    </html>
    """
    res = parse_arvore_processo(html)
    assert res["tipo_processo"] == "Aquisição de Equipamentos"
    assert len(res["documentos"]) == 2
    assert res["documentos"][0]["id_documento"] == "11111"
    assert "Despacho" in res["documentos"][0]["nome"]


def test_parse_conteudo_documento():
    html = """
    <html>
      <body>
        <div class="infraBarraComandos"><button>Imprimir</button></div>
        <h1 class="infraTitulo">Despacho nº 42/2026 - CBM</h1>
        <div id="corpo">
          <p>1. Encaminho os autos para análise e parecer técnico da DTI.</p>
          <p>2. Solicito urgência na manifestação.</p>
          <div class="assinatura">
            <p>Documento assinado eletronicamente por Comandante Geral em 20/08/2026 às 10:00.</p>
          </div>
        </div>
      </body>
    </html>
    """
    res = parse_conteudo_documento(html)
    assert "Despacho nº 42/2026" in res["titulo"]
    assert "Encaminho os autos" in res["conteudo_texto"]
    assert len(res["assinaturas"]) >= 1
    assert "Comandante Geral" in res["assinaturas"][0]


def test_parse_pesquisa_processos():
    html = """
    <html>
      <body>
        <table id="tblPesquisa" class="infraTable">
          <tr>
            <td><a href="controlador.php?acao=procedimento_trabalhar&id_procedimento=55555">00053.999888/2026-99</a></td>
            <td>Processo de Contratação Emergencial</td>
          </tr>
        </table>
      </body>
    </html>
    """
    res = parse_pesquisa_processos(html)
    assert len(res) == 1
    assert res[0]["numero"] == "00053.999888/2026-99"
    assert res[0]["id"] == "55555"
