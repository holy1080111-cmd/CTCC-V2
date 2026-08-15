param(
    [ValidateRange(1, 1440)]
    [int]$DurationMinutes = 60,

    [ValidateRange(60, 86400)]
    [int]$IntervalSeconds = 300,

    [ValidateRange(1, 10000)]
    [int]$MaxRuns = 12
)

$ErrorActionPreference = "Stop"
$baseUrl = "http://127.0.0.1:8100"

$secureToken = Read-Host "Enter CTCC API_TOKEN" -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)
try {
    $token = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
}
finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
}
$headers = @{ "X-CTCC-Token" = $token }

$confirmation = Read-Host "Type ARM_AND_START_CONTROLLED_DEMO_SOAK"
if ($confirmation -cne "ARM_AND_START_CONTROLLED_DEMO_SOAK") {
    throw "Confirmation text did not match. Nothing was started."
}

$armBody = @{ confirmation = "ARM_OKX_DEMO_AUTOMATION" } | ConvertTo-Json
$armed = $false
try {
    $arm = Invoke-RestMethod `
        -Method Post `
        -Uri "$baseUrl/api/demo-automation/arm" `
        -Headers $headers `
        -ContentType "application/json" `
        -Body $armBody
    if ($arm.armed -ne $true) {
        throw "Automation did not arm."
    }
    $armed = $true

    $preflight = Invoke-RestMethod `
        -Uri "$baseUrl/api/demo-observability/soak/preflight" `
        -Headers $headers
    if ($preflight.ready -ne $true) {
        throw "Execute-soak preflight is blocked: $($preflight.blockers -join ', ')"
    }

    $body = @{
        execute = $true
        duration_minutes = $DurationMinutes
        interval_seconds = $IntervalSeconds
        max_runs = $MaxRuns
        confirmation = "START_DEMO_SOAK_EXECUTE"
    } | ConvertTo-Json

    $started = Invoke-RestMethod `
        -Method Post `
        -Uri "$baseUrl/api/demo-observability/soak/start" `
        -Headers $headers `
        -ContentType "application/json" `
        -Body $body

    $started | ConvertTo-Json -Depth 15
    Write-Host "Controlled Demo execution soak started."
    Write-Host "The session will auto-disarm on completion, stop, error, or safety stop."
}
catch {
    if ($armed) {
        $disarmBody = @{ confirmation = "DISARM_OKX_DEMO_AUTOMATION" } | ConvertTo-Json
        try {
            Invoke-RestMethod `
                -Method Post `
                -Uri "$baseUrl/api/demo-automation/disarm" `
                -Headers $headers `
                -ContentType "application/json" `
                -Body $disarmBody | Out-Null
        }
        catch {
            Write-Warning "Automatic disarm after start failure also failed. Check status immediately."
        }
    }
    throw
}
