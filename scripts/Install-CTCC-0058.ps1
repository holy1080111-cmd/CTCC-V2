[CmdletBinding()]
param(
    [ValidateSet('Plan', 'Stage', 'Install', 'Reinstall', 'Verify', 'Configure', 'Check', 'Launch', 'Upgrade', 'Rollback')]
    [string] $Mode = 'Plan',
    [Parameter(Mandatory = $true)] [string] $PackageRoot,
    [string] $PackageIdentitySha256,
    [string] $Wheelhouse,
    [string] $ProfileFile,
    [ValidateSet('analysis', 'demo', 'live')] [string] $Tool = 'analysis',
    [ValidateRange(1024, 65535)] [int] $Port = 8128,
    [string] $Python = 'python.exe',
    [switch] $DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Bridge = 'import runpy,sys;root,module=sys.argv[1:3];sys.path.insert(0,root);sys.argv=[module,*sys.argv[3:]];runpy.run_module(module,run_name="__main__")'
$SourceRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$PackageRoot = [IO.Path]::GetFullPath($PackageRoot)

function Invoke-CTCCPython {
    param([string] $Interpreter, [string] $Source, [string] $Module, [string[]] $Arguments)
    & $Interpreter '-I' '-B' '-c' $Bridge $Source $Module @Arguments
    if ($LASTEXITCODE -ne 0) { throw ('CTCC_STOP:{0}:VALIDATION_REFUSED' -f $Mode) }
}

try {
    if ($Mode -in @('Plan', 'Stage', 'Upgrade')) {
        # Upgrade stages a DIFFERENT directory. It never activates a profile,
        # migrates a DB, touches old files, rolls policy out or restores Arm.
        $Operation = if ($Mode -eq 'Plan' -or $DryRun) { 'plan' } else { 'stage' }
        Invoke-CTCCPython $Python $SourceRoot 'scripts.windows_tool_0058_v1' @($Operation, '--package', $PackageRoot)
        exit 0
    }
    if ($PackageIdentitySha256 -notmatch '^[a-f0-9]{64}$') { throw 'CTCC_STOP:PACKAGE_IDENTITY_REQUIRED' }
    Invoke-CTCCPython $Python $SourceRoot 'scripts.windows_tool_0058_v1' @('verify', '--package', $PackageRoot, '--identity-sha256', $PackageIdentitySha256)
    if ($Mode -in @('Verify', 'Rollback') -or ($DryRun -and $Mode -notin @('Install', 'Reinstall'))) {
        # Rollback only verifies a preserved release. Its matching PRIVATE
        # profile must then be launched explicitly. No DB downgrade or switch.
        exit 0
    }
    if ($Mode -in @('Install', 'Reinstall')) {
        if ([string]::IsNullOrWhiteSpace($Wheelhouse)) { throw 'CTCC_STOP:OFFLINE_WHEELS_REQUIRED' }
        $InstallArguments = @($Mode.ToLowerInvariant(), '--package', $PackageRoot, '--identity-sha256', $PackageIdentitySha256, '--wheelhouse', ([IO.Path]::GetFullPath($Wheelhouse)))
        if ($DryRun) { $InstallArguments += '--dry-run' }
        Invoke-CTCCPython $Python $SourceRoot 'scripts.windows_tool_0058_v1' $InstallArguments
        exit 0
    }
    $RuntimePython = Join-Path $PackageRoot 'runtime\Scripts\python.exe'
    $RuntimeSource = Join-Path $PackageRoot 'source'
    if ($Mode -eq 'Configure') {
        Invoke-CTCCPython $RuntimePython $RuntimeSource 'scripts.configure_windows_tool_0058_v1' @('--package', $PackageRoot, '--identity-sha256', $PackageIdentitySha256, '--mode', $Tool, '--port', $Port.ToString())
        exit 0
    }
    if ([string]::IsNullOrWhiteSpace($ProfileFile)) { throw 'CTCC_STOP:PRIVATE_PROFILE_REQUIRED' }
    $Operation = if ($Mode -eq 'Check') { '--check' } else { '--serve' }
    Invoke-CTCCPython $RuntimePython $RuntimeSource 'scripts.launch_windows_tool_0058_v1' @('--profile', ([IO.Path]::GetFullPath($ProfileFile)), $Operation)
    exit 0
}
catch {
    # Never print exception text: private file paths/DSNs/inputs may be inside.
    Write-Output ('CTCC_STOP:{0}:REFUSED' -f $Mode)
    exit 1
}
