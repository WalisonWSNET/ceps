# ceps

Atualiza o CEP dos serviços de clientes (Itanhaém, Mongaguá e Peruíbe) no Hubsoft, trocando o CEP genérico da cidade pelo CEP
da rua (lista dos Correios), sem perder número, complemento e referência. O robô (Playwright) edita o endereço no Hub, confere o
resultado e marca `feito` / escreve `obs_robo` na planilha do Google.

## Arquivos
- `scripts/atualizar_ceps_hub.py`: o robô.
- `scripts/atualizar_ceps.py`: casa cada cliente com o CEP da rua (gera `cep_novo`, `status_cep`, `ceps_candidatos`).
- `scripts/refinar_ceps_geografia.py` e `scripts/aplicar_refinamento_planilha.py`: usam a coordenada do cliente para escolher o CEP.
- `scripts/pendencias.py`: listas de revisão a partir do CSV exportado (só leitura).
- `scripts/sugerir_ceps.py`: sugere CEP para `nao_encontrado`, `numero_fora_da_faixa` e `ambiguo` (busca flexível: escrita por som
  ss/ç/y/i, nome parecido, complemento escrito junto com a rua, bairro vizinho, faixa de numeração). Só leitura.
- `scripts/aplicar_sugestoes.py`: grava na planilha as sugestões validadas (`cep_novo` + `status_cep = conferido_manual`), com backup.
- `scripts/auditar_log.py`: lista serviços já gravados em que um complemento escrito no campo da rua pode ter se perdido. Só leitura.
- `data/ceps_logradouros.csv`: lista dos Correios. Os dados de clientes (`data/clientes*.csv`) e `logs/` **nunca** vão para o GitHub.

## Rodar o robô no dia a dia
Na pasta `ceps`, com o `.venv` ativo (`.\.venv\Scripts\Activate.ps1`) e depois de `git pull`:

```
$base = 'data/clientes_novo.csv --todos --gravar --continuar-no-erro --tempos --navegador chromium --travado-min 4 --reiniciar 25 --planilha ID_DA_PLANILHA'
$inicio = Get-Date '08:00'; $meio = Get-Date '16:55'; $fim = Get-Date '22:00'
function Rodar-Etapa($extra, $ate, $limite) {
  $tent = 0
  while ((Get-Date) -lt $limite -and $tent -lt 6) {
    $t0 = Get-Date
    Invoke-Expression "python scripts/atualizar_ceps_hub.py $base $extra --ate $ate"
    $c = $LASTEXITCODE
    if ($c -notin 1,3) { break }                                   # fim normal, PARANDO ou horário limite: não repete
    if (((Get-Date) - $t0).TotalMinutes -gt 30) { $tent = 0 } else { $tent++ }   # rodou bem por 30 min: zera as tentativas
    Start-Sleep -Seconds 20
  }
}
if ((Get-Date) -lt $inicio) { Start-Sleep -Seconds ([int]($inicio - (Get-Date)).TotalSeconds) }
if ((Get-Date) -lt $meio) { Rodar-Etapa '' '16:55' $meio }
if ((Get-Date) -lt $fim) { Rodar-Etapa '--prioridade-normal' '22:00' $fim }
```
- Até 16:55 o robô roda com prioridade baixa (o PC fica mais livre); depois, prioridade normal até as 22:00.
- Código de saída 3 = o robô travou ou o navegador caiu; código 1 = erro inesperado. Nos dois casos o laço espera 20 s e religa sozinho.
- Limite de 6 tentativas seguidas por etapa (evita ficar em loop se o problema não passa). Se o robô trabalhar mais de 30 minutos seguidos, o contador zera.
- Não religa quando a saída foi normal (horário limite, fila acabou) nem quando o robô parou de propósito (`PARANDO`: login caído, 3 erros seguidos, 25 clientes sem concluir).
- Dormir o PC derruba tudo: desligar a suspensão enquanto o robô roda (`powercfg /change standby-timeout-ac 0`).
- Antes de religar: fechar o Chromium antigo do robô:
  `Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -like '*ms-playwright*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }`
- Login no Hub: na janela do Chromium, fazer o login completo e só depois apertar ENTER no terminal.
- Resumo e produção: `python scripts/atualizar_ceps_hub.py data/clientes_novo.csv --resumo`. Por dia, pelo log:
  `Import-Csv logs\log_ceps.csv | Group-Object { $_.data_hora.Substring(0,10) }`.
- Estatísticas do projeto até 10/11: ~8.950 serviços concluídos pelo robô, ~1.130 a 1.400 por dia, fila restante ~5.000.

## O que cada status quer dizer
- Concluídos (marcam `feito`): `ok` (gravou e conferiu) e `ja_atualizado` (o Hub já estava certo).
- Pulados de propósito, sem alterar nada: `bairro_longe`, `rua_e_bairro_divergem`, `numero_divergente`, `cep_divergente`,
  `cep_formulario_divergente`, `cidade_mudou`, `enderecos_diferentes_*` (cep, numero, logradouro, bairro, condominio, cidade),
  `campo_autocompletar_preenchido`, `servico_nao_encontrado`, `sem_servico_ativo`, `observacoes_nao_fechou`.
- Alarmes (2 seguidos param o robô): `verificacao_*`, `restauracao_*`, `cep_nao_ficou*`, `botao_*`, `campo_sumiu_*`, `formulario*`.
- `erro`: falha passageira (Hub lento, timeout). Volta na fila. O robô para depois de 3 seguidos.

## Regras que o robô aplica
- Preserva número, complemento e referência. Complemento/referência diferentes entre os 4 endereços: um preenchido, copia para todos; dois valores diferentes, deixa em branco.
- O bairro só pode mudar para um bairro perto (até 3 km). Só a rua mudar pode.
- Se o CEP atual do Hub já serve para a rua e o número pela lista dos Correios (faixa de numeração), não muda (`ja_atualizado`).
- Entre os 4 endereços, passam como iguais: "r." = "rua", doutor = dr, "Oásis" = "Jardim Oásis", "Balneário Gaivota" = "Gaivota (Praia)", pequena diferença de grafia em nomes longos. Avenida e Rua são diferentes.
- CEPs diferentes entre os endereços passam se um já é o CEP novo e todos são das 3 cidades (CEP de 11730000 a 11799999).
- Verde na coluna `cep_novo` da planilha = já correto no Hub; o robô ignora.
- Clientes com endereços em outra cidade (ex.: Barueri): `--so-um-endereco` com `--id`. Altera só os endereços das 3 cidades (Instalação, Cadastral, Cobrança, Fiscal), cada um com "Salvar", sem "Atualizar Todos".
- Clientes já revisados por você: `--aceitar-bairro` com `--id` grava mesmo com bairro longe ou bairros diferentes entre os endereços.

## Plano para fechar o projeto (depois que a fila automática acabar, ~13 a 16/11)
**Fase 1: fotografar o estado**
1. Deixar o robô terminar (`Faltam 0`).
2. `--resumo` e exportar o CSV novo da planilha com um nome novo (ex.: `clientes_fim.csv`).
3. `python scripts/pendencias.py data/clientes_fim.csv` (listas em `logs\pendencias\`).

**Fase 2: pulados do robô (~750)**
1. Rodar uma vez `--refazer-pulados` (as regras novas devem resolver parte):
   `python scripts/atualizar_ceps_hub.py data/clientes_fim.csv --todos --refazer-pulados --gravar --continuar-no-erro --tempos --navegador chromium --planilha ID_DA_PLANILHA`
2. `bairro_longe` (~390): os 4 revisados até agora eram "mantém o CEP da planilha". Conferir uma amostra de 20 (ordenada pela distância em `obs_robo`); se 90% ou mais forem "mantém", rodar o restante com `--aceitar-bairro --id ...`. Acima de ~5 km, revisar um a um.
3. `campo_autocompletar_preenchido` (~90): ver à mão. `sem_servico_ativo` (~80): cancelados, em geral ignorar. `servico_nao_encontrado` (~47): conferir o ID no Hub.
4. Grupo C: serviços 69951 e 56822 (cliente 35817), 58300 (cliente 34088), 19400 (cliente 22654), 3976 (cliente 4142).

**Fase 3: linhas que nunca entraram na fila (só na planilha)**
- `numero_fora_da_faixa` (157): a rua existe, o CEP certo costuma estar em `ceps_candidatos`. O mais barato.
- `ambiguo` (~1.080) e `ceps_candidatos` (~1.200): escolher o CEP entre as opções.
- `nao_encontrado` (~1.580): rua com grafia diferente ou nova. O mais trabalhoso.
- `fora_do_escopo` (~1.665): outras cidades. Ignorar.
- CEP genérico ainda em `cep_novo` (~2.280) e CEPs suspeitos (~820, sendo ~60 `nao_bate`).
- Sugestões automáticas (busca flexível):
  1. `python scripts/sugerir_ceps.py data/clientes_hoje.csv` gera `logs/sugestoes/sugestoes.csv` com o CEP proposto, a rua e o bairro dos
     Correios, a distância e o motivo. Confiança `alta` = rua igual (ou igual por som) + bairro igual + número na faixa;
     `media` = nome parecido ou bairro vizinho; `baixa` = só consulta.
  2. Conferir no Hub uma amostra de 20 a 30 (alta e media).
  3. Simular e gravar: `python scripts/aplicar_sugestoes.py logs/sugestoes/sugestoes.csv --planilha ID` (só alta) e, depois de validar,
     `--confianca media` e `--gravar` (faz backup em `logs\backup_sugestoes_*.csv`). As linhas viram `conferido_manual`.
  4. Exportar o CSV novo e rodar o robô (elas entram na fila). Complemento escrito junto com a rua ("COND. X - APTO 5- AV. Y") é
     separado pelo robô e colocado no campo Complemento.
- Auditoria do que já foi gravado: `python scripts/auditar_log.py` (lista `perdido` / `presente`); restaurar à mão só os `perdido`.
- Para cada linha corrigida à mão: preencher `cep_novo` e `status_cep = conferido_manual`; exportar o CSV e rodar o robô (elas entram na fila).

**Fase 4: conferência por amostra**
- `--conferir-marcados` (só leitura) compara o CEP do Hub com o da planilha. Fazer uma amostra de 300 a 500 (todos levariam ~20 horas). Nos primeiros 533, 524 estavam certos.

**Fase 5: encerramento**: a seção abaixo.

## Encerramento (fazer ao terminar o processo)

### Trabalho que falta fechar
1. Exportar o CSV novo da planilha e rodar `python scripts/pendencias.py data/clientes_novo.csv` (listas em `logs\pendencias\`):
   `status_cep` pendentes (ambiguo, fora_do_escopo, nao_encontrado, numero_fora_da_faixa), `ceps_candidatos`, `obs_robo`,
   CEPs genéricos (11700000/11730000/11740000/11750000) e CEPs suspeitos (só os dígitos finais mudam, ex. 11730002).
2. Corrigir à mão o que sobrar em `logs\pendentes.csv` e rodar o robô com `--refazer-pulados`.
   Para clientes já revisados: `--id ... --aceitar-bairro` (bairro longe) e `--so-um-endereco` (endereços de outra cidade).
3. Casos em aberto: serviços 69951 e 56822 (cliente 35817), 58300 (cliente 34088), 19400 (cliente 22654), 3976 (cliente 4142);
   marcar `feito` no 59581 (cliente 46281).
4. Colunas `cep_novo_anterior` (CEP antigo das linhas trocadas pelo refinamento) e `ceps_candidatos`: conferir e limpar se não servirem mais.
5. Opcional: tirar a validação do cabeçalho `feito` (Dados → Validação de dados → intervalo `O2:O`).

### Limpeza e segurança
No terminal, dentro da pasta `ceps`:

```
del auth.json
rmdir /s /q .perfil
rmdir /s /q .perfil_chromium
del data\clientes*.csv
```

- Apagar também os CSVs com dados de clientes: `clientes_novo.csv`, `clientes_antigo_28out.csv`, `clientes.csv.csv`, `clientes_refinado.csv`.
- Guardar o log de trocas em lugar seguro, só seu, se quiser poder desfazer; depois apagar `logs\` (tem IDs, observações lidas do Hub e capturas de tela).
- Sair do Hub pelo navegador normal (encerrar a sessão no servidor).
- Google Sheets: tirar o compartilhamento da planilha com o e-mail da conta de serviço, apagar a chave no Google Cloud e `del credenciais_google.json`.
- Voltar o horário de acesso do Hub ao normal e desfazer ajustes de energia do PC (suspensão), se foram mudados.
- Nunca versionar `auth.json`, `.perfil*/`, `clientes*.csv`, `credenciais_google.json` nem `logs/` (já estão no `.gitignore`).

## Marcar a coluna `feito` na planilha automaticamente (opcional)

1. `pip install gspread`
2. Conta de serviço do Google com a **Google Sheets API** ativada; baixe o JSON da chave e salve como
   `credenciais_google.json` nesta pasta (está no `.gitignore`; nunca envie para o GitHub).
3. Compartilhe a planilha, como **Editor**, com o e-mail da conta de serviço (campo `client_email` do JSON).
4. Rode com `--planilha ID_DA_PLANILHA` (o trecho entre `/d/` e `/edit` na URL):

```
python scripts/atualizar_ceps_hub.py data/clientes.csv --limite 50 --gravar --planilha ID_DA_PLANILHA
```

O robô só escreve `TRUE` na coluna `feito` das linhas concluídas (ok/ja_atualizado). Ao começar, ele também
marca o que o log já tem e a planilha ainda não. O terminal mostra `faltam N` a cada cliente.
