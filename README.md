# ceps
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
