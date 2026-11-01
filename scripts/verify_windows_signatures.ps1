# Verify release files on Windows before calling them signed.
# This script neither signs files nor changes the machine's trust settings.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string[]] $FilePath,
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string] $ExpectedSubject,
    [string] $ReportPath
)

$ErrorActionPreference = 'Stop'
if (-not $IsWindows) {
    throw 'Run this verification with PowerShell 7 on Windows.'
}

$results = @(
    foreach ($path in $FilePath) {
        try {
            $file = Get-Item -LiteralPath $path -ErrorAction Stop
            if ($file.PSIsContainer) { throw 'Expected a file.' }
            $signature = Get-AuthenticodeSignature -LiteralPath $file.FullName
            $subject = if ($signature.SignerCertificate) { $signature.SignerCertificate.Subject } else { '' }
            $thumbprint = if ($signature.SignerCertificate) { $signature.SignerCertificate.Thumbprint } else { '' }
            $timestamped = $null -ne $signature.TimeStamperCertificate
            $subjectMatches = [StringComparer]::Ordinal.Equals($subject, $ExpectedSubject)
            [pscustomobject]@{
                file = $file.Name
                sha256 = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
                status = [string] $signature.Status
                signer_subject = $subject
                signer_thumbprint = $thumbprint
                timestamp_present = $timestamped
                expected_subject_matches = $subjectMatches
                accepted = ($signature.Status -eq 'Valid' -and $subjectMatches -and $timestamped)
            }
        }
        catch {
            [pscustomobject]@{
                file = [IO.Path]::GetFileName($path)
                status = 'VerificationError'
                accepted = $false
            }
        }
    }
)

if ($ReportPath) {
    # Never overwrite an input executable, an existing report or a linked file.
    $stream = [IO.FileStream]::new([IO.Path]::GetFullPath($ReportPath),
        [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    try {
        $bytes = [Text.UTF8Encoding]::new($false).GetBytes(($results | ConvertTo-Json -Depth 4))
        $stream.Write($bytes, 0, $bytes.Length)
    }
    finally {
        $stream.Dispose()
    }
}
$results | Format-Table file, status, timestamp_present, expected_subject_matches, accepted -AutoSize | Out-Host
if (@($results | Where-Object { -not $_.accepted }).Count -gt 0) {
    exit 1
}
exit 0
