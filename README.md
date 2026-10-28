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
