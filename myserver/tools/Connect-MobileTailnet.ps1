param(
    [string]$Device,
    [string]$Python = 'python',
    [ValidateRange(1024,65535)][int]$ServerPort = 18782,
    [ValidateRange(1024,65535)][int]$ProxyPort = 18783,
    [ValidateRange(1024,65535)][int]$HttpsPort = 8443,
    [switch]$Preview
)
$ErrorActionPreference = 'Stop'
if (@($ServerPort, $ProxyPort, $HttpsPort | Select-Object -Unique).Count -ne 3) {
    throw 'Choose distinct server, proxy, and HTTPS ports.'
}
$tailscale = (Get-Command tailscale -ErrorAction Stop).Source
$statusJson = & $tailscale status --json
if ($LASTEXITCODE) { throw 'Could not read Tailscale status.' }
$state = $statusJson | ConvertFrom-Json
$name = ([string]$state.Self.DNSName).TrimEnd('.')
if ($state.BackendState -ne 'Running' -or $name -notmatch '^[a-z0-9.-]+\.ts\.net$') {
    throw 'Connect Tailscale and enable MagicDNS before pairing.'
}
$phones = @($state.Peer.PSObject.Properties.Value | Where-Object {
    $_.Online -and $_.OS -eq 'android' -and (!$Device -or $_.HostName -eq $Device -or ([string]$_.DNSName).TrimEnd('.') -eq $Device)
})
if ($phones.Count -ne 1) { throw 'Connect one Android peer, or select an online Android hostname with -Device.' }
$phone = $phones[0]
$account = ($state.User.PSObject.Properties | Where-Object Name -eq ([string]$phone.UserID)).Value
$login = [string]$account.LoginName
$ips = @($phone.TailscaleIPs | Where-Object { ($null -ne $_) -and ($_ -as [Net.IPAddress]) })
if ($login -notmatch '^[a-zA-Z0-9][a-zA-Z0-9@._+-]*$' -or !$ips.Count) {
    throw 'The phone has no usable Tailscale identity.'
}
$url = "https://${name}:${HttpsPort}"
if ($Preview) { "Detected Android peer $($phone.HostName). Endpoint $url"; return }
if ((Invoke-RestMethod "http://127.0.0.1:$ServerPort/v1/status" -TimeoutSec 3).status -ne 'ready') {
    throw 'Start the MYNURI PC executor first.'
}
$serveJson = & $tailscale serve status --json
if ($LASTEXITCODE) { throw 'Could not verify existing Tailscale Serve ports.' }
$serve = $serveJson | ConvertFrom-Json
if ($serve.TCP.PSObject.Properties.Name -contains [string]$HttpsPort -or
    (Get-NetTCPConnection -LocalPort $ProxyPort -State Listen -ErrorAction SilentlyContinue)) {
    throw 'This setup needs an unused HTTPS and proxy port.'
}
$pythonCommand = (Get-Command $Python -ErrorAction Stop).Source
$proxyScript = Join-Path $PSScriptRoot 'tailscale_commands.py'
$proxyArgs = @("`"$proxyScript`"", '--login', $login, '--port', $ProxyPort, '--upstream-port', $ServerPort)
foreach ($ip in $ips) { $proxyArgs += @('--device-ip', $ip) }
$proxy = Start-Process -FilePath $pythonCommand -ArgumentList $proxyArgs -WindowStyle Hidden -PassThru
$verifiedChild = $null
try {
    $listener = $null
    for ($attempt = 0; $attempt -lt 15; $attempt++) {
        if ($proxy.HasExited) { throw 'The authenticated proxy could not start.' }
        $listener = Get-NetTCPConnection -LocalPort $ProxyPort -State Listen -ErrorAction SilentlyContinue
        if ($listener) { break }
        Start-Sleep -Milliseconds 200
    }
    if (!$listener -or $listener.LocalAddress -ne '127.0.0.1') {
        throw 'Could not verify the local authenticated proxy.'
    }
    if ($listener.OwningProcess -ne $proxy.Id) {
        $owner = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)"
        if (!$owner -or $owner.ParentProcessId -ne $proxy.Id) {
            throw 'The proxy port belongs to a different process.'
        }
        $verifiedChild = $owner.ProcessId
    }
    & $tailscale serve --bg "--https=$HttpsPort" "http://127.0.0.1:$ProxyPort"
    if ($LASTEXITCODE) { throw 'Tailscale Serve configuration failed.' }
    Write-Host "Enter $url in the app connection settings."
} catch {
    if ($verifiedChild) { Stop-Process -Id $verifiedChild -ErrorAction SilentlyContinue }
    if (!$proxy.HasExited) { Stop-Process -Id $proxy.Id }
    throw
}
