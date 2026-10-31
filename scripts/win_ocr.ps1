<#
  win_ocr.ps1 —— 用 Windows 自带 OCR 引擎（Windows.Media.Ocr）离线识别图片文字。
  不依赖任何云服务/密钥，纯本地。

  用法：
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\win_ocr.ps1 `
        -ImagePath "F:\...\page_1.png" -OutJson "F:\...\page_1.json" [-LangTag zh-Hans-CN]

  输出 JSON（UTF-8）：
    {"image":"...","lang":"zh-Hans-CN","width":W,"height":H,
     "lines":[{"text":"...","x":1,"y":2,"w":3,"h":4}, ...]}
#>
param(
  [Parameter(Mandatory=$true)][string]$ImagePath,
  [Parameter(Mandatory=$true)][string]$OutJson,
  [string]$LangTag = "zh-Hans-CN"
)

$ErrorActionPreference = "Stop"

Add-Type -AssemblyName System.Runtime.WindowsRuntime

# ---- WinRT 异步转同步 ----
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
  $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
  $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
})[0]

function Await($op, $resultType) {
  $asTask = $asTaskGeneric.MakeGenericMethod($resultType)
  $task = $asTask.Invoke($null, @($op))
  $task.Wait(-1) | Out-Null
  $task.Result
}

# ---- 加载类型 ----
[Windows.Storage.StorageFile, Windows.Foundation, ContentType=WindowsRuntime]            | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType=WindowsRuntime] | Out-Null
[Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType=WindowsRuntime]            | Out-Null
[Windows.Globalization.Language, Windows.Foundation, ContentType=WindowsRuntime]         | Out-Null

$lang = New-Object Windows.Globalization.Language($LangTag)
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($lang)
if ($null -eq $engine) { throw "无法为语言 $LangTag 创建 OCR 引擎（可能未安装该语言包）" }

# ---- 读图 ----
$file    = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($ImagePath)) ([Windows.Storage.StorageFile])
$stream  = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap  = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])

# ---- 识别 ----
$result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])

$items = @()
foreach ($line in $result.Lines) {
  $words = @($line.Words)
  if ($words.Count -eq 0) { continue }
  $minX = [double]::MaxValue; $minY = [double]::MaxValue; $maxX = 0; $maxY = 0
  $ws = @()
  foreach ($wd in $words) {
    $r = $wd.BoundingRect
    if ($r.X -lt $minX) { $minX = $r.X }
    if ($r.Y -lt $minY) { $minY = $r.Y }
    if (($r.X + $r.Width)  -gt $maxX) { $maxX = $r.X + $r.Width }
    if (($r.Y + $r.Height) -gt $maxY) { $maxY = $r.Y + $r.Height }
    # word-level box: needed by layout analysis (table columns, font tiers)
    $ws += [pscustomobject]@{
      t = $wd.Text
      x = [int][math]::Round($r.X)
      y = [int][math]::Round($r.Y)
      w = [int][math]::Round($r.Width)
      h = [int][math]::Round($r.Height)
    }
  }
  $items += [pscustomobject]@{
    text  = $line.Text
    x = [int][math]::Round($minX)
    y = [int][math]::Round($minY)
    w = [int][math]::Round($maxX - $minX)
    h = [int][math]::Round($maxY - $minY)
    words = $ws
  }
}

$payload = [pscustomobject]@{
  image  = $ImagePath
  lang   = $LangTag
  width  = [int]$decoder.PixelWidth
  height = [int]$decoder.PixelHeight
  lines  = $items
}

$json = ConvertTo-Json $payload -Depth 5 -Compress
[System.IO.File]::WriteAllText($OutJson, $json, (New-Object System.Text.UTF8Encoding($false)))

Write-Output ("OK lines=" + $items.Count + " -> " + $OutJson)
