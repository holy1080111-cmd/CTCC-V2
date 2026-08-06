param(
    [string]$ReportDate = (Get-Date).ToUniversalTime().ToString("yyyy-MM-dd"),
    [string]$OutputDirectory = ".\reports"
)

$ErrorActionPreference = "Stop"
$baseUrl = "http://127.0.0.1:8100"

$parsedDate = [datetime]::MinValue
if (-not [datetime]::TryParseExact(
    $ReportDate,
    "yyyy-MM-dd",
    [Globalization.CultureInfo]::InvariantCulture,
    [Globalization.DateTimeStyles]::AssumeUniversal,
    [ref]$parsedDate
)) {
    throw "ReportDate must use yyyy-MM-dd."
}

$secureToken = Read-Host "Enter CTCC API_TOKEN" -AsSecureString
$pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)
try {
    $token = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
}
finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
}
$headers = @{ "X-CTCC-Token" = $token }

$report = Invoke-RestMethod `
    -Uri "$baseUrl/api/demo-performance/daily/$ReportDate?refresh=true" `
    -Headers $headers

New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$outputPath = Join-Path $OutputDirectory "demo-performance-$ReportDate.json"
$report | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath $outputPath -Encoding UTF8

Write-Host "Demo daily performance report generated."
Write-Host "UTC report date: $ReportDate"
Write-Host "Output: $outputPath"
Write-Host "Realized trades: $($report.realized_trade_count)"
Write-Host "Net after recorded costs: $($report.net_after_costs)"
