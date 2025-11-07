# путь к вашему TypeScript-скрипту (можно менять)
$scriptPath = "C:\Users\19060455\PycharmProjects\jira-tasks-mfd\widget-store\scripts\build-meta-from-zod.ts"

# путь к директории output
$outputDir = "C:\Users\19060455\PycharmProjects\jira-tasks-mfd\widget-store\output"

# имя файла результата
$outputFile = Join-Path $outputDir "widget-meta.json"

# создать папку output при необходимости
if (!(Test-Path $outputDir)) {
    Write-Host "📁 Создаю папку: $outputDir"
    New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
}

Write-Host "▶️  Запуск TypeScript-скрипта через npx tsx..."
Write-Host "    $scriptPath"
Write-Host ""

# Запускаем tsx
$process = Start-Process -FilePath "npx" -ArgumentList "-y tsx `"$scriptPath`"" -NoNewWindow -PassThru -Wait -RedirectStandardOutput output.log -RedirectStandardError error.log

# читаем вывод
if (Test-Path "output.log") {
    Get-Content "output.log" | ForEach-Object { Write-Host $_ }
}
if (Test-Path "error.log") {
    Get-Content "error.log" | ForEach-Object { Write-Host $_ }
}

# проверка выхода
if ($process.ExitCode -ne 0) {
    Write-Host "❌ Ошибка: tsx завершился с кодом $($process.ExitCode)"
    exit 1
}

# ожидание появления выходного файла
Write-Host ""
Write-Host "⏳ Ожидаю появления файла widget-meta.json ..."

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
    Write-Host "❌ Файл результата не появился в течении $timeoutSec секунд"
    exit 1
}

# вывод информации о файле
$fileInfo = Get-Item $outputFile
Write-Host "✅ Файл создан:" $fileInfo.FullName
Write-Host "   Размер: $([math]::Round($fileInfo.Length / 1KB, 2)) KB"

# проверка JSON
try {
    $json = Get-Content $outputFile -Raw | ConvertFrom-Json
    Write-Host "🟢 JSON корректен"
}
catch {
    Write-Host "⚠️  JSON повреждён или невалиден:"
    Write-Host $_
}

Write-Host ""
Write-Host "🎉 Готово!"
