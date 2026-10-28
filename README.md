# ceps
## Encerramento (fazer ao terminar o processo)

No terminal, dentro da pasta `ceps`:

```
del auth.json
rmdir /s /q .perfil
del data\clientes*.csv
```

- Sair do Hub pelo navegador normal (encerrar a sessão no servidor).
- Guardar o log de trocas em lugar seguro, só seu, se quiser poder desfazer; depois apagar `logs\`.
- Nunca versionar `auth.json`, `.perfil/`, `clientes*.csv` nem `logs/` (já estão no `.gitignore`).

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
