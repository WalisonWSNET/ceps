# Roda o robô em janelas de horário (padrão: sábado 31/10 e domingo 01/11, das 08:05 às 17:00).
# Uso (na pasta ceps, com o .venv ativo):
#   .\scripts\fim_de_semana.ps1
#   .\scripts\fim_de_semana.ps1 -Dias 2026-11-07,2026-11-08 -Inicio 08:05 -Fim 17:00
# Dentro de cada janela, se o robô travar (código 3) ele é religado; se parar por outro motivo (login caído, 3 erros
# seguidos, fila acabou), segue para a próxima janela. O robô para sozinho no horário de --ate.
param(
    [string[]]$Dias = @("2026-10-31", "2026-11-01"),
    [string]$Inicio = "08:05",
    [string]$Fim = "17:00",
    [string]$Planilha = "1XK3rfBXUQ8tZyaPCo0PD9L1kWMk3Oad5sWK7WHrpMT4"
)

foreach ($dia in $Dias) {
    $ini = [datetime]::ParseExact("$dia $Inicio", "yyyy-MM-dd HH:mm", $null)
    $fimDt = [datetime]::ParseExact("$dia $Fim", "yyyy-MM-dd HH:mm", $null)
    if ((Get-Date) -ge $fimDt) {
        Write-Host "Janela de $dia ($Inicio-$Fim) já passou; pulando."
        continue
    }
    $espera = ($ini - (Get-Date)).TotalSeconds
    if ($espera -gt 0) {
        Write-Host "Aguardando até $ini para começar ($([int]($espera / 60)) min)..."
        Start-Sleep -Seconds ([int]$espera)
    }
    do {
        Write-Host "[$(Get-Date -Format 'dd/MM HH:mm')] iniciando o robô (até $Fim)"
        python scripts/atualizar_ceps_hub.py data/clientes.csv --todos --gravar --continuar-no-erro --tempos --navegador chromium --ate $Fim --planilha $Planilha
        $cod = $LASTEXITCODE
        if ((Get-Date) -ge $fimDt) { break }
    } while ($cod -eq 3)
    Write-Host "[$(Get-Date -Format 'dd/MM HH:mm')] janela de $dia encerrada (código $cod)"
}
Write-Host "Todas as janelas terminaram."
