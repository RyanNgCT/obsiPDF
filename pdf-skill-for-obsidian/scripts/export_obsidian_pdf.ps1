[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateScript({ Test-Path -LiteralPath $_ -PathType Leaf })]
    [string]$NotePath,

    [Parameter(Mandatory = $true)]
    [string]$OutputPath,

    [switch]$Overwrite,

    [ValidateRange(10, 180)]
    [int]$TimeoutSeconds = 60
)

$ErrorActionPreference = 'Stop'

function Wait-Until {
    param(
        [Parameter(Mandatory = $true)][scriptblock]$Condition,
        [Parameter(Mandatory = $true)][string]$FailureMessage,
        [int]$Timeout = $TimeoutSeconds
    )
    $deadline = [DateTime]::UtcNow.AddSeconds($Timeout)
    do {
        $result = & $Condition
        if ($null -ne $result -and $false -ne $result) { return $result }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $deadline)
    throw $FailureMessage
}

function Test-PdfReadable {
    param([Parameter(Mandatory = $true)][string]$Path)
    try {
        $stream = [IO.File]::Open(
            $Path,
            [IO.FileMode]::Open,
            [IO.FileAccess]::Read,
            [IO.FileShare]::Read)
        try {
            if ($stream.Length -lt 10) { return $false }
            $header = New-Object byte[] 5
            if ($stream.Read($header, 0, 5) -ne 5) { return $false }
            if ([Text.Encoding]::ASCII.GetString($header) -ne '%PDF-') { return $false }
            $tailLength = [Math]::Min(2048, [int]$stream.Length)
            $stream.Seek(-$tailLength, [IO.SeekOrigin]::End) | Out-Null
            $tail = New-Object byte[] $tailLength
            $read = $stream.Read($tail, 0, $tailLength)
            $tailText = [Text.Encoding]::ASCII.GetString($tail, 0, $read)
            return ($tailText.TrimEnd() -match '%%EOF$')
        } finally {
            $stream.Dispose()
        }
    } catch {
        return $false
    }
}

function Wait-ForPdf {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][DateTime]$After,
        [int]$Timeout = $TimeoutSeconds,
        [int]$StableMilliseconds = 750
    )
    $deadline = [DateTime]::UtcNow.AddSeconds($Timeout)
    $lastLength = -1
    $lastWrite = [DateTime]::MinValue
    $stableSince = $null
    do {
        if (Test-Path -LiteralPath $Path -PathType Leaf) {
            $file = Get-Item -LiteralPath $Path
            if ($file.Length -gt 0 -and $file.LastWriteTimeUtc -gt $After) {
                if ($file.Length -eq $lastLength -and $file.LastWriteTimeUtc -eq $lastWrite) {
                    if ($null -eq $stableSince) {
                        $stableSince = [DateTime]::UtcNow
                    } elseif ((([DateTime]::UtcNow - $stableSince).TotalMilliseconds -ge $StableMilliseconds) -and
                        (Test-PdfReadable -Path $Path)) {
                        return $file
                    }
                } else {
                    $lastLength = $file.Length
                    $lastWrite = $file.LastWriteTimeUtc
                    $stableSince = $null
                }
            }
        }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Obsidian did not finish writing a stable, readable PDF: $Path"
}

function Get-RootWindows {
    $root = [System.Windows.Automation.AutomationElement]::RootElement
    return $root.FindAll(
        [System.Windows.Automation.TreeScope]::Children,
        [System.Windows.Automation.Condition]::TrueCondition
    )
}

function Find-One {
    param(
        [Parameter(Mandatory = $true)][System.Windows.Automation.AutomationElement]$Element,
        [Parameter(Mandatory = $true)][System.Windows.Automation.Condition]$Condition
    )
    return $Element.FindFirst(
        [System.Windows.Automation.TreeScope]::Descendants, $Condition)
}

function Find-OverwriteConfirmation {
    param(
        [System.Windows.Automation.AutomationElement]$SaveDialog,
        [int]$ProcessId
    )

    $dialogCondition = [System.Windows.Automation.AndCondition]::new(
        [System.Windows.Automation.PropertyCondition]::new(
            [System.Windows.Automation.AutomationElement]::ProcessIdProperty, $ProcessId),
        [System.Windows.Automation.AndCondition]::new(
            [System.Windows.Automation.PropertyCondition]::new(
                [System.Windows.Automation.AutomationElement]::NameProperty, 'Confirm Save As'),
            [System.Windows.Automation.PropertyCondition]::new(
                [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
                [System.Windows.Automation.ControlType]::Window)
        )
    )
    $root = [System.Windows.Automation.AutomationElement]::RootElement
    $dialog = $root.FindFirst(
        [System.Windows.Automation.TreeScope]::Children, $dialogCondition)
    if (-not $dialog) {
        try {
            $dialog = $SaveDialog.FindFirst(
                [System.Windows.Automation.TreeScope]::Descendants, $dialogCondition)
        } catch [System.Windows.Automation.ElementNotAvailableException] {
            return $null
        }
    }
    if (-not $dialog) { return $null }

    $yesCondition = [System.Windows.Automation.AndCondition]::new(
        [System.Windows.Automation.PropertyCondition]::new(
            [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
            [System.Windows.Automation.ControlType]::Button),
        [System.Windows.Automation.OrCondition]::new(
            [System.Windows.Automation.PropertyCondition]::new(
                [System.Windows.Automation.AutomationElement]::NameProperty, 'Yes'),
            [System.Windows.Automation.PropertyCondition]::new(
                [System.Windows.Automation.AutomationElement]::NameProperty, '&Yes')
        )
    )
    return $dialog.FindFirst(
        [System.Windows.Automation.TreeScope]::Descendants, $yesCondition)
}

function Find-ExportMenuItem {
    param([System.Windows.Automation.AutomationElement]$Element)
    $condition = [System.Windows.Automation.AndCondition]::new(
        [System.Windows.Automation.PropertyCondition]::new(
            [System.Windows.Automation.AutomationElement]::NameProperty, 'Export to PDF...'),
        [System.Windows.Automation.PropertyCondition]::new(
            [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
            [System.Windows.Automation.ControlType]::Text)
    )
    $candidate = Find-One -Element $Element -Condition $condition
    if (-not $candidate) { return $null }

    $walker = [System.Windows.Automation.TreeWalker]::RawViewWalker
    $ancestor = $candidate
    for ($depth = 0; $depth -lt 8 -and $ancestor; $depth++) {
        if ($ancestor.Current.ClassName -match '(^|\s)menu-item(\s|$)') { return $ancestor }
        $ancestor = $walker.GetParent($ancestor)
    }
    return $null
}

Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName System.Windows.Forms
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class ObsidianPdfWindow {
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll", SetLastError = true)] public static extern bool PostMessage(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam);
}
'@

$resolvedNote = (Resolve-Path -LiteralPath $NotePath).Path
if ([IO.Path]::GetExtension($resolvedNote) -ne '.md') {
    throw 'NotePath must identify a Markdown file.'
}

$resolvedOutput = [IO.Path]::GetFullPath($OutputPath)
if ([IO.Path]::GetExtension($resolvedOutput) -ne '.pdf') {
    throw 'OutputPath must end in .pdf.'
}
$outputDirectory = [IO.Path]::GetDirectoryName($resolvedOutput)
if (-not (Test-Path -LiteralPath $outputDirectory -PathType Container)) {
    throw "Output directory does not exist: $outputDirectory"
}
if ((Test-Path -LiteralPath $resolvedOutput) -and -not $Overwrite) {
    throw 'Output PDF already exists. Use -Overwrite only when replacement is authorized.'
}

$noteTitle = [IO.Path]::GetFileNameWithoutExtension($resolvedNote)
$uri = 'obsidian://open?path=' + [Uri]::EscapeDataString($resolvedNote)
Start-Process -FilePath $uri -WindowStyle Normal

$obsidianWindow = Wait-Until -FailureMessage "Obsidian did not open the requested note: $noteTitle" -Condition {
    $windows = @(Get-RootWindows)
    for ($index = 0; $index -lt $windows.Count; $index++) {
        $window = $windows[$index]
        if (($window.Current.Name -replace '&', '') -like ("$noteTitle*Obsidian*" -replace '&', '')) { return $window }
    }
    return $null
}

$handle = [IntPtr]$obsidianWindow.Current.NativeWindowHandle
[ObsidianPdfWindow]::ShowWindow($handle, 9) | Out-Null
[ObsidianPdfWindow]::SetForegroundWindow($handle) | Out-Null

$exportMenuItem = Find-ExportMenuItem -Element $obsidianWindow
if (-not $exportMenuItem) {
    $promptCondition = [System.Windows.Automation.OrCondition]::new(
        [System.Windows.Automation.PropertyCondition]::new(
            [System.Windows.Automation.AutomationElement]::ClassNameProperty, 'prompt'),
        [System.Windows.Automation.PropertyCondition]::new(
            [System.Windows.Automation.AutomationElement]::NameProperty, 'Select a command...')
    )
    $stalePrompt = Find-One -Element $obsidianWindow -Condition $promptCondition
    if ($stalePrompt) {
        [System.Windows.Forms.SendKeys]::SendWait('{ESC}')
        Start-Sleep -Milliseconds 250
    }

    $moreOptionsCondition = [System.Windows.Automation.AndCondition]::new(
        [System.Windows.Automation.PropertyCondition]::new(
            [System.Windows.Automation.AutomationElement]::NameProperty, 'More options'),
        [System.Windows.Automation.PropertyCondition]::new(
            [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
            [System.Windows.Automation.ControlType]::Button)
    )
    $moreOptionsButton = Find-One -Element $obsidianWindow -Condition $moreOptionsCondition
    if ($moreOptionsButton) {
        $moreOptionsButton.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
        try {
            $exportMenuItem = Wait-Until -Timeout 5 -FailureMessage 'The note options menu did not expose Export to PDF.' -Condition {
                Find-ExportMenuItem -Element $obsidianWindow
            }
        } catch {
            $exportMenuItem = $null
        }
    }
}

if ($exportMenuItem) {
    $exportMenuItem.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
} else {
    throw 'Obsidian note menu did not expose Export to PDF; rerun the three-dot workflow.'
}

$exportButtonCondition = [System.Windows.Automation.AndCondition]::new(
    [System.Windows.Automation.PropertyCondition]::new(
        [System.Windows.Automation.AutomationElement]::NameProperty, 'Export to PDF'),
    [System.Windows.Automation.PropertyCondition]::new(
        [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
        [System.Windows.Automation.ControlType]::Button)
)
$exportButton = Wait-Until -FailureMessage 'Obsidian Export to PDF dialog did not open.' -Condition {
    Find-One -Element $obsidianWindow -Condition $exportButtonCondition
}

$dialogItems = @($obsidianWindow.FindAll(
    [System.Windows.Automation.TreeScope]::Descendants,
    [System.Windows.Automation.Condition]::TrueCondition))
$dropdowns = @()
$toggles = @()
$slider = $null
for ($index = 0; $index -lt $dialogItems.Count; $index++) {
    $item = $dialogItems[$index]
    if ($item.Current.ClassName -eq 'dropdown') { $dropdowns += $item }
    if ($item.Current.ClassName -like 'checkbox-container*') { $toggles += $item }
    if ($item.Current.ClassName -eq 'slider') { $slider = $item }
}
if ($dropdowns.Count -lt 2 -or $toggles.Count -lt 2 -or -not $slider) {
    throw 'Could not read all Obsidian PDF export settings.'
}

$pageSize = ($dropdowns[0].GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)).Current.Value
$margin = ($dropdowns[1].GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)).Current.Value
$downscale = ($slider.GetCurrentPattern([System.Windows.Automation.RangeValuePattern]::Pattern)).Current.Value
$includeTitle = $toggles[0].Current.ClassName -match 'is-enabled'
$landscape = $toggles[1].Current.ClassName -match 'is-enabled'

$beforeWrite = if (Test-Path -LiteralPath $resolvedOutput) {
    (Get-Item -LiteralPath $resolvedOutput).LastWriteTimeUtc
} else {
    [DateTime]::MinValue
}

$exportButton.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()

$saveDialog = Wait-Until -FailureMessage 'Windows Save As dialog did not open.' -Condition {
    $windows = @(Get-RootWindows)
    for ($index = 0; $index -lt $windows.Count; $index++) {
        $window = $windows[$index]
        if ($window.Current.Name -eq 'Save As' -and
            $window.Current.ProcessId -eq $obsidianWindow.Current.ProcessId) {
            return $window
        }
    }
    return $null
}

$fileNameCondition = [System.Windows.Automation.AndCondition]::new(
    [System.Windows.Automation.PropertyCondition]::new(
        [System.Windows.Automation.AutomationElement]::NameProperty, 'File name:'),
    [System.Windows.Automation.AndCondition]::new(
        [System.Windows.Automation.PropertyCondition]::new(
            [System.Windows.Automation.AutomationElement]::AutomationIdProperty, '1001'),
        [System.Windows.Automation.PropertyCondition]::new(
            [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
            [System.Windows.Automation.ControlType]::Edit)
    )
)
$saveButtonCondition = [System.Windows.Automation.AndCondition]::new(
    [System.Windows.Automation.PropertyCondition]::new(
        [System.Windows.Automation.AutomationElement]::NameProperty, 'Save'),
    [System.Windows.Automation.PropertyCondition]::new(
        [System.Windows.Automation.AutomationElement]::AutomationIdProperty, '1')
)
$fileNameInput = Find-One -Element $saveDialog -Condition $fileNameCondition
$saveButton = Find-One -Element $saveDialog -Condition $saveButtonCondition
if (-not $fileNameInput -or -not $saveButton) { throw 'Save As controls were not found.' }

$fileNameInput.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).SetValue($resolvedOutput)
# Queue BM_CLICK to the identified native Save button so a modal confirmation
# cannot block this automation thread before it starts looking for the popup.
$saveButtonHandle = [IntPtr]$saveButton.Current.NativeWindowHandle
if ($saveButtonHandle -eq [IntPtr]::Zero) {
    throw 'The Windows Save button has no native window handle.'
}
if (-not [ObsidianPdfWindow]::PostMessage(
    $saveButtonHandle, 0x00F5, [IntPtr]::Zero, [IntPtr]::Zero)) {
    throw 'Could not queue the Windows Save button click.'
}

if ($Overwrite) {
    $confirmation = Wait-Until -FailureMessage 'No overwrite confirmation was shown.' -Condition {
        if (-not (Test-Path -LiteralPath $resolvedOutput) -or
            (Get-Item -LiteralPath $resolvedOutput).LastWriteTimeUtc -le $beforeWrite) {
            return Find-OverwriteConfirmation -SaveDialog $saveDialog `
                -ProcessId $obsidianWindow.Current.ProcessId
        }
        return $true
    }
    if ($confirmation -is [System.Windows.Automation.AutomationElement]) {
        $confirmation.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
    }
}

Wait-ForPdf -Path $resolvedOutput -After $beforeWrite -Timeout $TimeoutSeconds | Out-Null

[ordered]@{
    notePath = $resolvedNote
    outputPath = $resolvedOutput
    includeFileNameAsTitle = $includeTitle
    pageSize = $pageSize
    landscape = $landscape
    margin = $margin
    downscalePercent = [double]$downscale
} | ConvertTo-Json
