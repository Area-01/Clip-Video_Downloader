# PyInstaller one-directory 배포본을 만들고, 업데이트 가능한 외부 도구 폴더를 포함한다.
# 실행: py -m pip install pyinstaller; .\build_release.ps1

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$projectDir = $PSScriptRoot
$toolSourceDir = Join-Path $projectDir "bin"
$releaseDir = Join-Path $projectDir "dist\cliper"
$zipPath = Join-Path $projectDir "dist\cliper.zip"

foreach ($tool in @("yt-dlp.exe", "ffmpeg.exe", "N_m3u8DL-RE.exe")) {
    if (-not (Test-Path -LiteralPath (Join-Path $toolSourceDir $tool))) {
        throw "필수 외부 도구를 찾을 수 없습니다: bin\\$tool"
    }
}

Push-Location $projectDir
try {
    # --onedir: cliper.exe 옆의 bin 폴더를 독립적으로 관리할 수 있는 배포 형식
    & py -m PyInstaller --noconfirm --clean --noconsole --onedir --name cliper cliper.py
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller 빌드에 실패했습니다."
    }

    Copy-Item -LiteralPath $toolSourceDir -Destination (Join-Path $releaseDir "bin") -Recurse -Force

    if (Test-Path -LiteralPath $zipPath) {
        Remove-Item -LiteralPath $zipPath -Force
    }
    Compress-Archive -Path (Join-Path $releaseDir "*") -DestinationPath $zipPath -Force
    Write-Host "배포본 생성 완료: $zipPath"
}
finally {
    Pop-Location
}
