$ErrorActionPreference = 'Stop'
$runtimeRoot = Join-Path $env:RUNNER_TEMP 'ctcc-validation-python-3.12.14'
$archive = Join-Path $runtimeRoot 'python.tar.gz'
$url = 'https://github.com/astral-sh/python-build-standalone/releases/download/20260929/cpython-3.12.14%2B20260929-x86_64-pc-windows-msvc-install_only_stripped.tar.gz'
$expected = 'f38e68f4d612ade6dd50c894fc80b14c0be0c3b5201145d6fff5f20b9323204d'

New-Item -ItemType Directory -Path $runtimeRoot -ErrorAction Stop | Out-Null
Invoke-WebRequest -Uri $url -OutFile $archive -TimeoutSec 90 -MaximumRedirection 5
if ((Get-Item -LiteralPath $archive).Length -ne 21992568 -or
    (Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash -ine $expected) {
    throw 'WINDOWS_VALIDATION_RUNTIME_ARCHIVE_CHANGED'
}

tar.exe -xzf $archive -C $runtimeRoot
if ($LASTEXITCODE -ne 0) { throw 'WINDOWS_VALIDATION_RUNTIME_EXTRACTION_FAILED' }
$pythonRoot = Join-Path $runtimeRoot 'python'
$python = Join-Path $pythonRoot 'python.exe'
$stdlib = Join-Path $pythonRoot 'Lib'
$sslFile = Join-Path $stdlib 'ssl.py'
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $sslFile).Hash -ine
    '039a0e3f1782e14824b1624c7ffefc2de31714b4a9994424be45a4271966097c') {
    throw 'WINDOWS_VALIDATION_SSL_SOURCE_CHANGED'
}

# Reproduce the existing reviewed desktop trust policy in the CI runtime.
$rejectedCa = Join-Path $PSScriptRoot 'codex-rejected-cas.pem'
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $rejectedCa).Hash -ine
    'c916ce1b14ed8ca69e9b5c9fab22799a900dd610d6d4ec9b1347f4fd48e53718') {
    throw 'WINDOWS_VALIDATION_REJECTED_CA_CHANGED'
}
[IO.File]::Copy($rejectedCa, (Join-Path $stdlib 'codex-rejected-cas.pem'), $false)
$sslSource = [IO.File]::ReadAllText($sslFile)
$lf = [string][char]10
$anchor = '        self = _SSLContext.__new__(cls, protocol)' + $lf
$restriction = @(
    '        # Codex: distrust emSign ECC Root CA - G3.'
    '        _SSLContext.load_verify_locations('
    '            self, cafile=os.path.join(os.path.dirname(__file__), ''codex-rejected-cas.pem'')'
    '        )'
) -join $lf
if ($sslSource.IndexOf($anchor) -lt 0 -or $sslSource.IndexOf($anchor) -ne $sslSource.LastIndexOf($anchor)) {
    throw 'WINDOWS_VALIDATION_SSL_ANCHOR_CHANGED'
}
$patched = $sslSource.Replace($anchor, $anchor + $restriction + $lf)
[IO.File]::WriteAllText($sslFile, $patched, [Text.UTF8Encoding]::new($false))

$vendorPins = @{
    'ssl.py' = '88c619ce59e1c92f2234c10700836c5c7198a070cdab21cfebb4a023e700205f'
    'asyncio/proactor_events.py' = '62d15e99315cae52e8548f487391ae6c46827bf8e5b4bcf3382c777a494a759b'
    'asyncio/selector_events.py' = 'b77c4b8a85f1e1e2882a2bf3bd85a4b1c1fcfd7cb37ed0d8d8eb5f9a2ea44231'
}
foreach ($vendor in $vendorPins.GetEnumerator()) {
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $stdlib $vendor.Key)).Hash -ine $vendor.Value) {
        throw 'WINDOWS_VALIDATION_VENDOR_SOURCE_CHANGED'
    }
}
& $python -I -c 'import sys, sysconfig; raise SystemExit(sys.version_info[:3] != (3, 12, 14) or sysconfig.get_platform() != "win-amd64")'
if ($LASTEXITCODE -ne 0) { throw 'WINDOWS_VALIDATION_RUNTIME_IDENTITY_CHANGED' }
[IO.File]::AppendAllText($env:GITHUB_PATH, $pythonRoot + [Environment]::NewLine)
