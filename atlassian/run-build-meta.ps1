# Path to TypeScript script
$scriptPath = "C:\Users\19060455\PycharmProjects\jira-tasks-mfd\widget-store\scripts\build-meta-from-zod.ts"

# Output directory
$outputDir = "C:\Users\19060455\PycharmProjects\jira-tasks-mfd\widget-store\output"

# Output file name
$outputFile = Join-Path $outputDir "widget-meta.json"

# Create output directory if missing
if (!(Test-Path $outputDir)) {
    Write-Host "Creating output directory: $outputDir"
    New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
}

Write-Host ""
Write-Host "Running TypeScript script using 'npx tsx'..."
Write-Host ""

# Run the Node/TS script
$process = Start-Process `
    -FilePath "npx" `
    -ArgumentList "-y tsx `"$scriptPath`"" `
    -NoNewWindow `
    -PassThru `
    -Wait `
    -RedirectStandardOutput output.log `
    -RedirectStandardError error.log

# Read logs
if (Test-Path "output.log") { Get-Content "output.log" }
if (Test-Path "error.log")  { Get-Content "error.log" }

# Check exit code
if ($process.ExitCode -ne 0) {
    Write-Host "ERROR: tsx exited with code $($process.ExitCode)"
    exit 1
}

Write-Host ""
Write-Host "Waiting for widget-meta.json to be created..."

$timeoutSec = 60
$elapsed = 0

while ($elapsed -lt $timeoutSec) {
    if (Test-Path $outputFile) {
        break
    }
    Start-Sleep -Seconds 1
    $elapsed++
}

if (!(Test-Path $outputFile)) {
    Write-Host "ERROR: widget-meta.json not found after $timeoutSec seconds"
    exit 1
}

# Print result file info
$fileInfo = Get-Item $outputFile
Write-Host "File created: $($fileInfo.FullName)"
Write-Host "Size: $($fileInfo.Length) bytes"

# Validate JSON
try {
    $json = Get-Content $outputFile -Raw | ConvertFrom-Json
    Write-Host "JSON structure is valid."
}
catch {
    Write-Host "WARNING: JSON is invalid:"
    Write-Host $_
}

Write-Host ""
Write-Host "Done."
