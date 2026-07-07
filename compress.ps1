Add-Type -AssemblyName System.Drawing
$src = "c:\Users\MShubkhi\Downloads\dani_coding\roc_workflow\static\img\logo-interport-technology.png"

# Load original
$img = [System.Drawing.Image]::FromFile($src)

# Determine new size (scale down by factor, e.g., max width 800)
$ratio = 400 / $img.Width
$newWidth = 400
$newHeight = [int]($img.Height * $ratio)

# Resize for Sidebar Logo
$bmpLogo = New-Object System.Drawing.Bitmap($newWidth, $newHeight)
$g1 = [System.Drawing.Graphics]::FromImage($bmpLogo)
$g1.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
$g1.DrawImage($img, 0, 0, $newWidth, $newHeight)
$g1.Dispose()
$bmpLogo.Save("c:\Users\MShubkhi\Downloads\dani_coding\roc_workflow\static\img\logo-sidebar.png", [System.Drawing.Imaging.ImageFormat]::Png)
$bmpLogo.Dispose()

# Create Watermark Version (High compression / high opacity/lightness via CSS is better, but let's just make it 800px width)
$ratio2 = 800 / $img.Width
$nw2 = 800
$nh2 = [int]($img.Height * $ratio2)
$bmpWm = New-Object System.Drawing.Bitmap($nw2, $nh2)
$g2 = [System.Drawing.Graphics]::FromImage($bmpWm)
$g2.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
$g2.DrawImage($img, 0, 0, $nw2, $nh2)
$g2.Dispose()
$bmpWm.Save("c:\Users\MShubkhi\Downloads\dani_coding\roc_workflow\static\img\logo-watermark.png", [System.Drawing.Imaging.ImageFormat]::Png)
$bmpWm.Dispose()

$img.Dispose()
Write-Host "Compression completed."
