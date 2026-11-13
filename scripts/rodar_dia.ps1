# Roda o robô o dia todo: prioridade baixa até 16:55, prioridade normal de 16:55 até 22:00.
# Uso (na pasta ceps, com o .venv ativo), UM comando só, sem colar nada em seguida:
#   .\scripts\rodar_dia.ps1 -Csv data\clientes_fim8.csv
#   .\scripts\rodar_dia.ps1 -Csv data\clientes_fim8.csv -Inicio 08:00 -Meio 16:55 -Fim 22:00
# Por que um arquivo e não colar o bloco: linhas coladas enquanto o robô roda ficam "na fila do teclado" e podem ser engolidas
# pelo próprio robô (o pedido de ENTER do login), então a 2ª etapa (16:55 a 22:00) nunca começava.
# Se o robô travar (código 1 ou 3) ele é religado (até 6 vezes seguidas; rodar bem por 30 min zera a conta).
# Para parando de propósito (login caído, 3 erros seguidos, 25 clientes sem concluir), não religa.
param(
    [Parameter(Mandatory = $true)][string]$Csv,
    [string]$Planilha = "1XK3rfBXUQ8tZyaPCo0PD9L1kWMk3Oad5sWK7WHrpMT4",
    [string]$Inicio = "08:00",
    [string]$Meio = "16:55",
    [string]$Fim = "22:00"
)

$hoje = Get-Date -Format "yyyy-MM-dd"
$tInicio = [datetime]::ParseExact("$hoje $Inicio", "yyyy-MM-dd HH:mm", $null)
$tMeio = [datetime]::ParseExact("$hoje $Meio", "yyyy-MM-dd HH:mm", $null)
$tFim = [datetime]::ParseExact("$hoje $Fim", "yyyy-MM-dd HH:mm", $null)
$base = "$Csv --todos --gravar --continuar-no-erro --tempos --navegador chromium --travado-min 4 --reiniciar 25 --planilha $Planilha"

function Rodar-Etapa([string]$extra, [string]$ate, [datetime]$limite) {
    $tent = 0
    while ((Get-Date) -lt $limite -and $tent -lt 6) {
        $t0 = Get-Date
        Write-Host "[$(Get-Date -Format 'dd/MM HH:mm')] iniciando o robô (até $ate) $extra"
        Invoke-Expression "python scripts/atualizar_ceps_hub.py $base $extra --ate $ate" | Out-Host  # Out-Host: sem isso a saída do robô vira o valor da função e some da tela
        $c = $LASTEXITCODE
        if ($c -notin 1, 3) { break }
        if (((Get-Date) - $t0).TotalMinutes -gt 30) { $tent = 0 } else { $tent++ }
        Start-Sleep -Seconds 20
    }
    return $c
}

if ((Get-Date) -lt $tInicio) {
    Write-Host "Aguardando até $tInicio para começar..."
    Start-Sleep -Seconds ([int]($tInicio - (Get-Date)).TotalSeconds)
}
$cod = $null
if ((Get-Date) -lt $tMeio) { $cod = Rodar-Etapa '' $Meio $tMeio }
if ((Get-Date) -lt $tFim) { $cod = Rodar-Etapa '--prioridade-normal' $Fim $tFim }
Write-Host "[$(Get-Date -Format 'dd/MM HH:mm')] fim do dia (último código: $cod)"
