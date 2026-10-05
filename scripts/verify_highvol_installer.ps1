[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$PackageRoot,
    [Parameter(Mandatory = $true)][string]$IdentityPath,
    [switch]$DryRun
)

# This verifier never invokes the installer, Docker, a controller, or an exchange.
# Package acceptance is separate from canonical qualification/deployment acceptance.
$ErrorActionPreference = 'Stop'
function Get-SourceSha256([string]$LiteralPath) {
    $stream = [IO.File]::OpenRead($LiteralPath)
    $hasher = [Security.Cryptography.SHA256]::Create()
    try {
        return [BitConverter]::ToString($hasher.ComputeHash($stream)).Replace('-', '').ToLowerInvariant()
    } finally {
        $hasher.Dispose()
        $stream.Dispose()
    }
}
$root = (Resolve-Path -LiteralPath $PackageRoot).Path
$identityFile = (Resolve-Path -LiteralPath $IdentityPath).Path
$identity = Get-Content -LiteralPath $identityFile -Raw | ConvertFrom-Json
if ($identity.schema -cne 'ctcc_offline_installer_identity_v1') {
    throw 'INSTALLER_IDENTITY_SCHEMA_INVALID'
}
$required = @(
    'Install-CTCC-HighVol-Momentum-V2.ps1', 'README.md', 'controller.py',
    'patch.py', 'test_demo_high_volatility_v2.py',
    'test_installer_restart_disarm.py', 'update_manifest.py'
)
$seen = @{}
foreach ($entry in $identity.files) {
    $name = [string]$entry.path
    if ($required -cnotcontains $name -or $seen.ContainsKey($name)) {
        throw 'INSTALLER_IDENTITY_FILE_SET_INVALID'
    }
    if ([string]$entry.sha256 -cnotmatch '^[0-9a-f]{64}$') {
        throw 'INSTALLER_IDENTITY_HASH_INVALID'
    }
    $seen[$name] = $true
    $path = Join-Path $root $name
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw ('INSTALLER_SOURCE_MISSING:{0}' -f $name)
    }
    $item = Get-Item -LiteralPath $path -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw ('INSTALLER_SOURCE_REPARSE_POINT:{0}' -f $name)
    }
    if ((Get-SourceSha256 $path) -cne $entry.sha256) {
        throw ('INSTALLER_SOURCE_IDENTITY_MISMATCH:{0}' -f $name)
    }
}
if ($seen.Count -ne $required.Count) {
    throw 'INSTALLER_IDENTITY_FILE_SET_INCOMPLETE'
}
$scriptPath = Join-Path $root 'Install-CTCC-HighVol-Momentum-V2.ps1'
$parseTokens = $null
$parseErrors = $null
[System.Management.Automation.Language.Parser]::ParseFile(
    $scriptPath, [ref]$parseTokens, [ref]$parseErrors
) | Out-Null
if ($parseErrors.Count -gt 0) {
    # Error IDs and locations are sufficient; source text could contain secrets.
    $locations = @($parseErrors | ForEach-Object {
        '{0}@{1}:{2}' -f $_.ErrorId, $_.Extent.StartLineNumber, $_.Extent.StartColumnNumber
    })
    throw ('INSTALLER_PARSER_REJECTED:{0}' -f ($locations -join ','))
}
$analyzerAvailable = [bool](Get-Module -ListAvailable -Name PSScriptAnalyzer)
[ordered]@{
    schema = 'ctcc_offline_installer_verification_v1'
    source_identity = 'PASS'
    powershell_parser = 'PASS'
    identity_sha256 = Get-SourceSha256 $identityFile
    verified_files = $seen.Count
    dry_run = [bool]$DryRun
    script_analyzer_available = $analyzerAvailable
    external_calls = 0
    deployment_performed = $false
    canonical_qualification_integration = 'NOT_ACCEPTED'
} | ConvertTo-Json -Compress
