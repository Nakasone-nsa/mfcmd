# 🚀 mfcmd

[![Python Version](https://img.shields.io/badge/python-3.6%2B-blue.svg?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg?style=for-the-badge)](LICENSE)
[![MediaFire SDK](https://img.shields.io/badge/mediafire-0.6.1-orange.svg?style=for-the-badge)](https://pypi.org/project/mediafire/)

O **`mfcmd`** é um utilitário CLI (linha de comando) de alta performance, resiliente e totalmente automatizado para envio de arquivos grandes para o **MediaFire**. Desenvolvido em Python sobre o SDK oficial `mediafire==0.6.1`, o script foi projetado para garantir máxima velocidade, estabilidade e tolerância a falhas em conexões oscilantes.

---

## 🌟 Principais Recursos

* ⚡ **Uploads Paralelos Multithread (`-t`):** Envia múltiplos blocos (*chunks*) simultaneamente, aproveitando ao máximo a largura de banda da sua conexão.
* 🔄 **Uploads Resumíveis (Resumable Uploads):** Em caso de queda de conexão ou interrupção manual (`Ctrl+C`), o script decodifica o bitmap do servidor e retoma exatamente da última parte pendente.
* 🔑 **Renovação Automática de Sessão (`Token Refresh`):** Identifica expirações de sessão (`Error 105`) durante transferências longas e renova a autenticação em segundo plano sem interromper o fluxo.
* ⚡ **Deduplicação Instantânea (Instant Upload):** Analisa o hash do arquivo local antes do envio; se o arquivo já existir no servidor, o upload é concluído instantaneamente e o link é gerado.
* 🎮 **Menu Interativo Pós-Envio:** Ao finalizar a transferência dos blocos, escolha entre aguardar a liberação do link final, iniciar um novo upload imediatamente ou encerrar o script.
* 📦 **Processamento em Lote (Batch Uploads):** Envie múltiplos arquivos em sequência dentro da mesma execução.
* 🧠 **Gerenciamento Eficiente de Memória (`UnitFile`):** Transmite blocos utilizando buffers otimizados em memória RAM, sem carregar arquivos gigantescos por inteiro na memória do sistema.
* 🛡️ **Tolerância a Falhas e Retentativas:** Executa até 5 tentativas automáticas por bloco em caso de erros temporários de rede.
* 📊 **Interface Limpa & Logging Avançado:** Barra de progresso detalhada em tempo real com `tqdm` e registro completo de operações no arquivo `upload_mfcmd.log`.

---

## 📐 Arquitetura & Fluxo de Operação

```text
                        ┌───────────────────────────────┐
                        │    🔑 Autenticação na API     │
                        └───────────────┬───────────────┘
                                        │
                                        ▼
                        ┌───────────────────────────────┐
                        │   🧮 Cálculo de Hashes Locais │
                        │    (MD5 e Global SHA-256)     │
                        └───────────────┬───────────────┘
                                        │
                                        ▼
                        ┌───────────────────────────────┐
                        │ 🔍 Verificação no MediaFire   │
                        └───────────────┬───────────────┘
                                        │
         ┌──────────────────────────────┴──────────────────────────────┐
         │                                                             │
  [ Arquivo Existe ]                                           [ Envio Necessário ]
         │                                                             │
         ▼                                                             ▼
┌─────────────────┐                                            ┌───────────────┐
│ ⚡ Link Direto  │                                            │ 🗺️ Decodificar │
│  Gerado         │                                            │   Bitmap      │
└─────────────────┘                                            └───────┬───────┘
                                                                       │
                                                                       ▼
                                                               ┌───────────────┐
                                                       ┌──────►│ 🧵 ThreadPool │
                                                       │       │ Envio Paralelo│
                                                       │       └───────┬───────┘
                                                       │               │
                                                [ Faltam Partes? ] ────┘
                                                       │
                                                       ▼
                                               ┌───────────────┐
                                               │ 🎮 Menu       │
                                               │ Interativo &  │
                                               │ Link Final    │
                                               └───────────────┘
```

---

## 🛠️ Requisitos Prévios

* **Python:** `3.6` ou superior
* **Biblioteca Oficial:** `mediafire==0.6.1`
* **Progresso em Terminal:** `tqdm`

---

## 📦 Instalação

1. **Clone o repositório:**
   ```bash
   git clone [https://github.com/Nakasone-nsa/mfcmd.git](https://github.com/Nakasone-nsa/mfcmd.git)
   cd mfcmd
   ```

2. **Instale as dependências:**
   ```bash
   pip install mediafire==0.6.1 tqdm
   ```

---

## 💻 Como Usar

```bash
python3 mfcmd.py -e <email> [-p <senha>] -f <caminho_do_arquivo> [opções]
```

### 📋 Parâmetros e Sinalizadores (Flags)

| Parâmetro | Sinalizador | Descrição | Obrigatório | Padrão |
| :--- | :--- | :--- | :---: | :---: |
| `-e` | `--email` | E-mail cadastrado na conta MediaFire | **Sim** | — |
| `-p` | `--password` | Senha da conta *(solicitada no terminal se omitida)* | Não | — |
| `-f` | `--file` | Caminho do arquivo local para upload | **Sim** | — |
| `-u` | `--upload-folder` | Pasta de destino no MediaFire | Não | `My Files` |
| `-t` | `--threads` | Número de uploads simultâneos (*threads*) | Não | `4` |
| `-s` | `--hash` | SHA-256 pré-calculado *(pula o cálculo local)* | Não | Automático |

---

## 💡 Exemplos Práticos

### 1. Upload Simples
Envia um arquivo diretamente para a pasta raiz (`My Files`):
```bash
python3 mfcmd.py \
    -e "usuario@exemplo.com" \
    -p "SuaSenhaSegura" \
    -f "backup_sistema.zip"
```

### 2. Upload de Alta Velocidade em Pasta Específica
Acelera o envio de um arquivo grande usando **8 threads paralelas** dentro da pasta `Filmes`:
```bash
python3 mfcmd.py \
    -e "usuario@exemplo.com" \
    -u "Filmes" \
    -t 8 \
    -f "video_4k.mkv"
```

### 3. Envio de Todos os Arquivos de uma Pasta (Modo Lote)

* **No Windows (PowerShell):**
  ```powershell
  Get-ChildItem "C:\MeusArquivos\*" -File | ForEach-Object { python mfcmd.py -e "usuario@exemplo.com" -f $_.FullName -u "MinhaPasta" }
  ```

* **No Linux / macOS (Bash):**
  ```bash
  for file in /caminho/da/pasta/*; do
      [ -f "$file" ] && python3 mfcmd.py -e "usuario@exemplo.com" -f "$file" -u "MinhaPasta"
  done
  ```

---

## 🚦 Códigos de Saída (Exit Status)

| Código | Status | Descrição |
| :---: | :--- | :--- |
| `0` | **SUCESSO** | Upload concluído, duplicata detectada ou menu finalizado corretamente. |
| `1` | **ERRO** | Falha de autenticação, arquivo não encontrado ou conexão perdida irrecuperável. |
| `130` | **INTERROMPIDO** | Cancelado pelo usuário (`Ctrl+C`). O progresso já enviado permanece no servidor para retoma posterior. |

---

## 📄 Licença

Este projeto está sob a licença [MIT](LICENSE). Conteúdo livre para modificação e distribuição.
``────``
