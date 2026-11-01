# Exercise Windows' real Authenticode verifier; no certificates are installed.
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$root = Split-Path -Parent $PSScriptRoot
$checker = Join-Path $root 'scripts/verify_windows_signatures.ps1'
$python = (Get-Command python).Source
$signature = Get-AuthenticodeSignature -LiteralPath $python
if ($signature.Status -ne 'Valid' -or -not $signature.TimeStamperCertificate) {
    throw 'The official Python test fixture must have a valid, timestamped signature.'
}
$subject = $signature.SignerCertificate.Subject
$temp = Join-Path ([IO.Path]::GetTempPath()) ('shigaraki-signature-test-' + [guid]::NewGuid())
New-Item -ItemType Directory -Path $temp | Out-Null

function Check-Exit([string] $Name, [string[]] $Files, [string] $Expected, [int] $Code) {
    $report = Join-Path $temp ($Name + '.json')
    & pwsh -NoProfile -File $checker -FilePath $Files -ExpectedSubject $Expected -ReportPath $report
    if ($LASTEXITCODE -ne $Code) { throw "$Name returned $LASTEXITCODE instead of $Code" }
    $rows = @(Get-Content -LiteralPath $report -Raw | ConvertFrom-Json)
    if ($rows.Count -ne $Files.Count) { throw "$Name omitted an input file" }
    if ($Code -eq 0 -and @($rows | Where-Object { -not $_.accepted }).Count) { throw "$Name accepted invalid output" }
    if ($Code -ne 0 -and @($rows | Where-Object { -not $_.accepted }).Count -eq 0) { throw "$Name lacks a rejection" }
    Write-Host "PASS $Name"
}

try {
    Check-Exit 'trusted-signed-file' @($python) $subject 0
    Check-Exit 'different-publisher' @($python) 'CN=Unexpected publisher' 1
    Check-Exit 'missing-file' @(Join-Path $temp 'missing.exe') $subject 1

    # Preserve array arguments by invoking the checker inside PowerShell.
    $multiRunner = Join-Path $temp 'multiple.ps1'
    @'
param([string] $Checker, [string] $Signed, [string] $Missing, [string] $Subject, [string] $Report)
& $Checker -FilePath @($Signed, $Missing) -ExpectedSubject $Subject -ReportPath $Report
exit $LASTEXITCODE
'@ | Set-Content -LiteralPath $multiRunner
    $multipleReport = Join-Path $temp 'multiple.json'
    & pwsh -NoProfile -File $multiRunner -Checker $checker -Signed $python -Missing (Join-Path $temp 'missing.exe') -Subject $subject -Report $multipleReport
    if ($LASTEXITCODE -ne 1) { throw 'Mixed valid and missing input must be rejected.' }
    $multiple = @(Get-Content -LiteralPath $multipleReport -Raw | ConvertFrom-Json)
    if ($multiple.Count -ne 2 -or -not $multiple[0].accepted -or $multiple[1].accepted) {
        throw 'Mixed input report must retain both results.'
    }
    Write-Host 'PASS multiple-inputs'

    $preserved = Join-Path $temp 'preserved.exe'
    Copy-Item -LiteralPath $python -Destination $preserved
    $originalHash = (Get-FileHash -LiteralPath $preserved).Hash
    & pwsh -NoProfile -File $checker -FilePath $preserved -ExpectedSubject $subject -ReportPath $preserved 2>$null
    if ($LASTEXITCODE -ne 1 -or (Get-FileHash -LiteralPath $preserved).Hash -ne $originalHash) {
        throw 'An existing report/input path must be rejected without modifying its contents.'
    }
    Write-Host 'PASS report-cannot-overwrite-input'

    $source = Join-Path $temp 'fixture.cs'
    'class Fixture { static void Main() {} }' | Set-Content -LiteralPath $source
    $unsigned = Join-Path $temp 'unsigned.exe'
    $compiler = Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    & $compiler /nologo "/out:$unsigned" $source
    if ($LASTEXITCODE -ne 0) { throw 'Cannot compile unsigned fixture.' }
    Check-Exit 'unsigned-executable' @($unsigned) $subject 1

    $tampered = Join-Path $temp 'tampered.exe'
    $bytes = [IO.File]::ReadAllBytes($python)
    $bytes[0x20] = $bytes[0x20] -bxor 1
    [IO.File]::WriteAllBytes($tampered, $bytes)
    Check-Exit 'tampered-executable' @($tampered) $subject 1
}
finally {
    # Only remove fixtures created in this new, task-owned temporary directory.
    Remove-Item -LiteralPath $temp -Recurse -Force
}
