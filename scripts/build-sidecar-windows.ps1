$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$Backend = Join-Path $RepoRoot "backend"
$Tauri = Join-Path $RepoRoot "src-tauri"
$Target = "x86_64-pc-windows-msvc"
$Vendor = Join-Path $Backend "vendor"
$BinaryDir = Join-Path $Tauri "binaries"
$ResourceDir = Join-Path $Tauri "resources\sidecar"

python -m pip install --upgrade pip
python -m pip install --only-binary=cryptography -r (Join-Path $Backend "requirements-desktop.txt")
# Tesseract is the single OCR engine used by both the server and desktop
# paths. Install it separately on Windows and vendor its executable/data.
$TesseractCommand = Get-Command tesseract -ErrorAction SilentlyContinue
if (-not $TesseractCommand) {
  throw "Tesseract OCR is required on the Windows build host"
}

# The Chocolatey `poppler` 25.12.0 package contains Poppler *source* archives,
# not Windows executables (for example pdfinfo.cc rather than pdfinfo.exe).
# Download a pinned, prebuilt Windows release instead.  It bundles pdfinfo,
# pdftoppm and every adjacent DLL required at runtime.
$PopplerVersion = "25.12.0-0"
$FfmpegVersion = "9.0.1"
$BuildTemp = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { $env:TEMP }
$PopplerArchive = Join-Path $BuildTemp "poppler-$PopplerVersion.zip"
$PopplerExtract = Join-Path $BuildTemp "poppler-$PopplerVersion"
$PopplerUri = "https://github.com/oschwartz10612/poppler-windows/releases/download/v$PopplerVersion/Release-$PopplerVersion.zip"
Remove-Item $PopplerArchive -Force -ErrorAction SilentlyContinue
Remove-Item $PopplerExtract -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $PopplerExtract | Out-Null
Write-Host "Downloading Poppler Windows binaries $PopplerVersion"
Invoke-WebRequest -Uri $PopplerUri -OutFile $PopplerArchive -UseBasicParsing
Expand-Archive -LiteralPath $PopplerArchive -DestinationPath $PopplerExtract -Force

$PopplerInfo = Get-ChildItem -LiteralPath $PopplerExtract -Recurse -Force -File |
  Where-Object { $_.Name -ieq "pdfinfo.exe" } |
  Select-Object -First 1
if (-not $PopplerInfo) {
  $layout = Get-ChildItem -LiteralPath $PopplerExtract -Recurse -Force -ErrorAction SilentlyContinue |
    Select-Object -First 80 -ExpandProperty FullName
  $layoutText = $layout -join [Environment]::NewLine
  throw "pdfinfo.exe was not found in downloaded Poppler Windows release '$PopplerVersion'. Layout:`n$layoutText"
}
$PopplerBin = $PopplerInfo.Directory
if (-not (Test-Path (Join-Path $PopplerBin "pdftoppm.exe"))) {
  throw "pdftoppm.exe was not found next to $($PopplerInfo.FullName)"
}
& $PopplerInfo.FullName -v
if ($LASTEXITCODE -ne 0) {
  throw "Downloaded Poppler binary could not start (exit code $LASTEXITCODE)"
}

# The desktop sidecar also prepares and cuts web audio.  FFmpeg/FFprobe must
# be shipped beside the EXE; relying on a user's PATH was the reason the old
# installer appeared to hang after OCR while waiting for audio preparation.
$FfmpegArchive = Join-Path $BuildTemp "ffmpeg-$FfmpegVersion-essentials_build.zip"
$FfmpegExtract = Join-Path $BuildTemp "ffmpeg-$FfmpegVersion-essentials_build"
$FfmpegUri = "https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-$FfmpegVersion-essentials_build.zip"
Remove-Item $FfmpegArchive -Force -ErrorAction SilentlyContinue
Remove-Item $FfmpegExtract -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $FfmpegExtract | Out-Null
Write-Host "Downloading FFmpeg/FFprobe Windows binaries $FfmpegVersion"
Invoke-WebRequest -Uri $FfmpegUri -OutFile $FfmpegArchive -UseBasicParsing
Expand-Archive -LiteralPath $FfmpegArchive -DestinationPath $FfmpegExtract -Force
$FfmpegExe = Get-ChildItem -LiteralPath $FfmpegExtract -Recurse -Force -File |
  Where-Object { $_.Name -ieq "ffmpeg.exe" } |
  Select-Object -First 1
$FfprobeExe = Get-ChildItem -LiteralPath $FfmpegExtract -Recurse -Force -File |
  Where-Object { $_.Name -ieq "ffprobe.exe" } |
  Select-Object -First 1
if (-not $FfmpegExe -or -not $FfprobeExe) {
  throw "ffmpeg.exe/ffprobe.exe were not found in downloaded FFmpeg release '$FfmpegVersion'"
}
& $FfmpegExe.FullName -version | Select-Object -First 1
if ($LASTEXITCODE -ne 0) {
  throw "Downloaded FFmpeg binary could not start (exit code $LASTEXITCODE)"
}
$FfmpegBin = $FfmpegExe.Directory
if ($FfprobeExe.Directory.FullName -ne $FfmpegBin.FullName) {
  throw "ffprobe.exe is not beside ffmpeg.exe in the downloaded FFmpeg release"
}

# These are generated build inputs.  Removing them avoids mixing a prior
# Linux/PyInstaller layout with the fresh Windows one.
Remove-Item $Vendor -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force (Join-Path $Vendor "tesseract") | Out-Null
New-Item -ItemType Directory -Force (Join-Path $Vendor "poppler") | Out-Null
New-Item -ItemType Directory -Force (Join-Path $Vendor "ffmpeg") | Out-Null
# Copy only the directory that contains the executables and their adjacent DLLs.
# Keeping pdfinfo/pdftoppm beside the DLLs is required by Poppler on Windows.
Copy-Item "$PopplerBin\*" (Join-Path $Vendor "poppler") -Recurse -Force
Copy-Item "$FfmpegBin\*" (Join-Path $Vendor "ffmpeg") -Recurse -Force

Copy-Item $TesseractCommand.Source (Join-Path $Vendor "tesseract\tesseract.exe") -Force
$TesseractData = Join-Path (Split-Path $TesseractCommand.Source) "tessdata"
if (Test-Path $TesseractData) {
  Copy-Item $TesseractData (Join-Path $Vendor "tesseract\tessdata") -Recurse -Force
}

if (-not (Get-ChildItem (Join-Path $Vendor "poppler") -Recurse -Filter "pdfinfo.exe" -File | Select-Object -First 1)) {
  throw "Poppler was not copied into backend/vendor"
}
if (-not (Test-Path (Join-Path $Vendor "ffmpeg\ffmpeg.exe")) -or
    -not (Test-Path (Join-Path $Vendor "ffmpeg\ffprobe.exe"))) {
  throw "FFmpeg/FFprobe were not copied into backend/vendor"
}

Push-Location $Backend
try {
  pyinstaller --noconfirm --clean smart_exam_sidecar.spec
} finally {
  Pop-Location
}

$Dist = Join-Path $Backend "dist\smart-exam-sidecar"
$SidecarExe = Join-Path $Dist "smart-exam-sidecar.exe"
$Internal = Join-Path $Dist "_internal"
if (-not ((Test-Path $SidecarExe) -and (Test-Path $Internal))) {
  throw "PyInstaller output is incomplete"
}
if (-not (Get-ChildItem $Internal -Recurse -Filter "pdfinfo.exe" -File | Select-Object -First 1)) {
  throw "The packaged sidecar is missing pdfinfo.exe"
}
if (-not (Get-ChildItem $Internal -Recurse -Filter "pdftoppm.exe" -File | Select-Object -First 1)) {
  throw "The packaged sidecar is missing pdftoppm.exe"
}
if (-not (Get-ChildItem $Internal -Recurse -Filter "tesseract.exe" -File | Select-Object -First 1)) {
  throw "The packaged sidecar is missing Tesseract OCR"
}
if (-not (Get-ChildItem $Internal -Recurse -Filter "ffmpeg.exe" -File | Select-Object -First 1)) {
  throw "The packaged sidecar is missing FFmpeg"
}
if (-not (Get-ChildItem $Internal -Recurse -Filter "ffprobe.exe" -File | Select-Object -First 1)) {
  throw "The packaged sidecar is missing FFprobe"
}
if (-not (Get-ChildItem $Internal -Recurse -Filter "wordninja_words.txt.gz" -File | Select-Object -First 1)) {
  throw "The packaged sidecar is missing wordninja/wordninja_words.txt.gz"
}

Remove-Item (Join-Path $BinaryDir "smart-exam-sidecar-$Target.exe") -Force -ErrorAction SilentlyContinue
Remove-Item (Join-Path $ResourceDir "_internal") -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $BinaryDir | Out-Null
New-Item -ItemType Directory -Force $ResourceDir | Out-Null
Copy-Item $SidecarExe (Join-Path $BinaryDir "smart-exam-sidecar-$Target.exe") -Force
Copy-Item $Internal (Join-Path $ResourceDir "_internal") -Recurse -Force
# Keep the repository marker intact after a local build; it is harmless in
# the installer and prevents a generated build from deleting a tracked file.
New-Item -ItemType File -Force (Join-Path $ResourceDir "_internal\.gitkeep") | Out-Null

Write-Host "Sidecar staged with Poppler at $PopplerBin and FFmpeg at $FfmpegBin"
