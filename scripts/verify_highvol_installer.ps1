[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$PackageRoot,
    [Parameter(Mandatory = $true)][string]$IdentityPath,
    [ValidatePattern('^[0-9a-fA-F]{64}$')][string]$ExpectedIdentitySha256 =
        '34ef5b12fa3855977d784d31803e0e1c7cb871959609f68a29cca2efcbffd722',
    [switch]$DryRun,
    [switch]$StageTrace
)

# This verifier never invokes the installer, Docker, a controller, or an exchange.
# Package acceptance is separate from canonical qualification/deployment acceptance.
$ErrorActionPreference = 'Stop'
function Write-VerifyStage([string]$Stage) {
    if ($StageTrace) {
        [Console]::Error.WriteLine(('INSTALLER_VERIFY_STAGE:{0}' -f $Stage))
    }
}
Write-VerifyStage 'start'
function Get-SourceSha256([byte[]]$Bytes) {
    $hasher = [Security.Cryptography.SHA256]::Create()
    try {
        return [BitConverter]::ToString($hasher.ComputeHash($Bytes)).Replace('-', '').ToLowerInvariant()
    } finally {
        $hasher.Dispose()
    }
}
function Convert-StrictUtf8([byte[]]$Bytes, [string]$Kind) {
    $offset = 0
    if ($Bytes.Length -ge 3 -and $Bytes[0] -eq 0xEF -and $Bytes[1] -eq 0xBB -and $Bytes[2] -eq 0xBF) {
        $offset = 3
    }
    try {
        return [Text.UTF8Encoding]::new($false, $true).GetString($Bytes, $offset, $Bytes.Length - $offset)
    } catch {
        throw ('INSTALLER_{0}_UTF8_INVALID' -f $Kind)
    }
}
$root = (Resolve-Path -LiteralPath $PackageRoot).Path
$identityFile = (Resolve-Path -LiteralPath $IdentityPath).Path
if (((Get-Item -LiteralPath $root -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw 'INSTALLER_PACKAGE_ROOT_REPARSE_POINT'
}
$identityParent = Split-Path -Path $identityFile -Parent
if (((Get-Item -LiteralPath $identityParent -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw 'INSTALLER_IDENTITY_PARENT_REPARSE_POINT'
}
$identityItem = Get-Item -LiteralPath $identityFile -Force
if (($identityItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw 'INSTALLER_IDENTITY_REPARSE_POINT'
}
$identityBytes = [IO.File]::ReadAllBytes($identityFile)
$identitySha256 = Get-SourceSha256 $identityBytes
if ($identitySha256 -cne $ExpectedIdentitySha256.ToLowerInvariant()) {
    throw 'INSTALLER_IDENTITY_MISMATCH'
}
Write-VerifyStage 'identity_hash'
$identity = Convert-StrictUtf8 $identityBytes 'IDENTITY' | ConvertFrom-Json
if ($identity.schema -cne 'ctcc_offline_installer_identity_v1') {
    throw 'INSTALLER_IDENTITY_SCHEMA_INVALID'
}
$required = @(
    'Install-CTCC-HighVol-Momentum-V2.ps1', 'README.md', 'controller.py',
    'patch.py', 'test_demo_high_volatility_v2.py',
    'test_installer_restart_disarm.py', 'update_manifest.py'
)
$seen = @{}
$scriptText = $null
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
    $sourceBytes = [IO.File]::ReadAllBytes($path)
    if ((Get-SourceSha256 $sourceBytes) -cne $entry.sha256) {
        throw ('INSTALLER_SOURCE_IDENTITY_MISMATCH:{0}' -f $name)
    }
    if ($name -ceq 'Install-CTCC-HighVol-Momentum-V2.ps1') {
        $scriptText = Convert-StrictUtf8 $sourceBytes 'SCRIPT'
    }
}
if ($seen.Count -ne $required.Count) {
    throw 'INSTALLER_IDENTITY_FILE_SET_INCOMPLETE'
}
Write-VerifyStage 'source_hashes'
$parseTokens = $null
$parseErrors = $null
[System.Management.Automation.Language.Parser]::ParseInput(
    $scriptText, [ref]$parseTokens, [ref]$parseErrors
) | Out-Null
if ($parseErrors.Count -gt 0) {
    # Error IDs and locations are sufficient; source text could contain secrets.
    $locations = @($parseErrors | ForEach-Object {
        '{0}@{1}:{2}' -f $_.ErrorId, $_.Extent.StartLineNumber, $_.Extent.StartColumnNumber
    })
    throw ('INSTALLER_PARSER_REJECTED:{0}' -f ($locations -join ','))
}
Write-VerifyStage 'parser'
Write-VerifyStage 'analyzer_discovery_start'
$analyzerAvailable = [bool](Get-Module -ListAvailable -Name PSScriptAnalyzer)
Write-VerifyStage 'analyzer_discovery_end'
[ordered]@{
    schema = 'ctcc_offline_installer_verification_v1'
    source_identity = 'PASS'
    powershell_parser = 'PASS'
    identity_sha256 = $identitySha256
    verified_files = $seen.Count
    dry_run = [bool]$DryRun
    script_analyzer_available = $analyzerAvailable
    external_calls = 0
    deployment_performed = $false
    canonical_qualification_integration = 'NOT_ACCEPTED'
} | ConvertTo-Json -Compress
Write-VerifyStage 'complete'
