# MCP Server SEI (Sistema Eletrônico de Informações)

Servidor **MCP (Model Context Protocol)** para integração e automação com o **SEI (Sistema Eletrônico de Informações)** através de autenticação direta por **Usuário, Senha e Órgão** (sessão web), dispensando a necessidade de tokens de API.

---

## 🚀 Funcionalidades

- **Autenticação Automática e Parametrizada**: Conecta ao SEI informando Usuário, Senha e Órgão com suporte a seleção de Unidade.
- **Gerenciamento de Sessão**: Mantém os cookies de sessão ativos e realiza auto-relogin se a sessão expirar.
- **Controle de Processos**: Lista todos os processos abertos na caixa da unidade (gerados e recebidos).
- **Consulta de Processos e Árvore**: Obtém os metadados do processo e toda a hierarquia de documentos/anexos.
- **Leitura de Documentos**: Extrai o conteúdo textual limpo e dados de assinatura eletrônica de qualquer documento/despacho/ofício.
- **Pesquisa Avançada**: Busca processos e documentos por número, interessado ou termos textuais.
- **Gestão de Unidades**: Permite alternar entre diferentes unidades do órgão às quais o usuário tem acesso.
- **Registro de Andamentos**: Adiciona despachos e observações no histórico do processo.

---

## 📦 Instalação

### 1. Clonar ou Acessar o Diretório
```bash
cd c:\Users\chaga\OneDrive\Documentos\mcp-sei-cbm
```

### 2. Criar Ambiente Virtual e Instalar Dependências
```bash
# Criar ambiente virtual
python -m venv .venv

# Ativar ambiente virtual
# No Windows (PowerShell):
.\.venv\Scripts\Activate.ps1
# No Linux/Mac:
# source .venv/bin/activate

# Instalar dependências
pip install -r requirements.txt
```

---

## ⚙️ Configuração (.env)

Copie o arquivo de exemplo `.env.example` para `.env` e preencha com os seus dados de acesso:

```bash
cp .env.example .env
```

Edite o arquivo `.env`:
```ini
# URL base do SEI da sua instituição
SEI_BASE_URL=https://sei.cbm.df.gov.br/sei

# Usuário e senha de acesso
SEI_USUARIO=seu_usuario_ou_cpf
SEI_SENHA=sua_senha_secreta

# Órgão de autenticação (sigla ou ID conforme a lista de seleção de login)
SEI_ORGAO=CBM

# (Opcional) Unidade padrão a ser ativada no login (ex: CBM/DTI)
SEI_UNIDADE=

# (Opcional) Configurações de rede
SEI_TIMEOUT=30
SEI_VERIFY_SSL=true
```

---

## 🛠️ Ferramentas Disponíveis (MCP Tools)

| Ferramenta | Descrição | Parâmetros |
|---|---|---|
| `sei_status` | Informa status da conexão, usuário logado e unidade ativa. | - |
| `sei_conectar` | Autentica ou atualiza credenciais em tempo de execução. | `base_url`, `usuario`, `senha`, `orgao`, `unidade` |
| `sei_listar_controle_processos` | Lista os processos na caixa de entrada da unidade atual. | - |
| `sei_consultar_processo` | Consulta processo e retorna metadados e árvore de documentos. | `numero_ou_id` (ex: `00053.000123/2026-10`) |
| `sei_obter_arvore_processo` | Retorna todos os documentos anexados a um processo. | `id_procedimento` |
| `sei_ler_documento` | Lê o texto e assinaturas de um documento específico. | `id_documento`, `id_procedimento` (opcional) |
| `sei_pesquisar` | Pesquisa processos ou documentos por palavra-chave. | `termo` |
| `sei_trocar_unidade` | Altera a unidade de trabalho atual no SEI. | `unidade` (ex: `CBM/DTI`) |
| `sei_adicionar_andamento` | Adiciona um andamento no histórico do processo. | `id_procedimento`, `descricao` |

---

## 🤖 Como Configurar no Claude Desktop / Cursor / Antigravity

Adicione a seguinte configuração no seu arquivo de configuração do MCP (ex: `claude_desktop_config.json` ou similar):

```json
{
  "mcpServers": {
    "sei-cbm": {
      "command": "c:\\Users\\chaga\\OneDrive\\Documentos\\mcp-sei-cbm\\.venv\\Scripts\\python.exe",
      "args": [
        "-m",
        "src.server"
      ],
      "cwd": "c:\\Users\\chaga\\OneDrive\\Documentos\\mcp-sei-cbm",
      "env": {
        "SEI_BASE_URL": "https://sei.cbm.df.gov.br/sei",
        "SEI_USUARIO": "seu_usuario",
        "SEI_SENHA": "sua_senha",
        "SEI_ORGAO": "CBM"
      }
    }
  }
}
```

---

## 🧪 Testes Automatizados

Para executar a suíte de testes de validação dos parsers e configurações:

```bash
.\.venv\Scripts\python.exe -m pytest -v
```
