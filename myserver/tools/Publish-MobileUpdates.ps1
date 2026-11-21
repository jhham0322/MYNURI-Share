param(
    [Parameter(Mandatory)][string]$AssistantApk,
    [Parameter(Mandatory)][string]$NuriApk,
    [Parameter(Mandatory)][string]$Aapt
)

$ErrorActionPreference = 'Stop'
$feed = (Resolve-Path (Join-Path $PSScriptRoot '../run/fold8-apk')).Path
$releases = @(
    @{ Role = 'assistant'; Label = '도우미'; Source = $AssistantApk },
    @{ Role = 'nuri'; Label = '누리'; Source = $NuriApk }
)

foreach ($release in $releases) {
    $apk = Get-Item -LiteralPath $release.Source
    $badging = (& $Aapt dump badging $apk.FullName) -join "`n"
    if ($LASTEXITCODE -ne 0) { throw "Unable to read $($release.Role) APK" }
    $info = [regex]::Match($badging, "package: name='com\.example\.mynuri' versionCode='([0-9]+)' versionName='([0-9]+(?:\.[0-9]+)+)'")
    if (!$info.Success -or !$badging.Contains("application-label:'$($release.Label)'")) {
        throw "Incorrect package or role for $($release.Role)"
    }
    $role = $release.Role
    $version = $info.Groups[2].Value
    $build = [int]$info.Groups[1].Value
    $name = "$role-$version.apk"
    $destination = Join-Path $feed $name
    $manifest = Join-Path $feed "latest-$role.json"
    $hash = (Get-FileHash -LiteralPath $apk.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    if (Test-Path -LiteralPath $manifest) {
        $previous = Get-Content -LiteralPath $manifest -Raw | ConvertFrom-Json
        if ($build -lt $previous.build -or ($build -eq $previous.build -and $hash -ne $previous.sha256)) {
            throw "The $role feed already contains this build or a newer one"
        }
    }
    if ($apk.FullName -ne $destination) {
        $temporaryApk = "$destination.part"
        Copy-Item -LiteralPath $apk.FullName -Destination $temporaryApk
        [IO.File]::Move($temporaryApk, $destination, $true)
    }
    $data = [ordered]@{ channel = $role; version = $version; build = $build; file = $name; sha256 = $hash; size = $apk.Length }
    $temporaryManifest = "$manifest.part"
    $data | ConvertTo-Json -Compress | Set-Content -LiteralPath $temporaryManifest -Encoding utf8NoBOM
    [IO.File]::Move($temporaryManifest, $manifest, $true)
    "$role $version published"
}
