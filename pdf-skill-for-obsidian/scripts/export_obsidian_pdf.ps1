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

function Get-RootWindows {
    $root = [System.Windows.Automation.AutomationElement]::RootElement
    return $root.FindAll(
        [System.Windows.Automation.TreeScope]::Children,
        [System.Windows.Automation.Condition]::TrueCondition
    )
}

function Get-Descendants {
    param([System.Windows.Automation.AutomationElement]$Element)
    return $Element.FindAll(
        [System.Windows.Automation.TreeScope]::Descendants,
        [System.Windows.Automation.Condition]::TrueCondition
    )
}

function Find-Descendant {
    param(
        [System.Windows.Automation.AutomationElement]$Element,
        [scriptblock]$Predicate
    )
    $items = @(Get-Descendants -Element $Element)
    for ($index = 0; $index -lt $items.Count; $index++) {
        $candidate = $items[$index]
        if (& $Predicate $candidate) { return $candidate }
    }
    return $null
}

function Find-ExportMenuItem {
    param([System.Windows.Automation.AutomationElement]$Element)
    $walker = [System.Windows.Automation.TreeWalker]::RawViewWalker
    $items = @(Get-Descendants -Element $Element)
    for ($index = 0; $index -lt $items.Count; $index++) {
        $candidate = $items[$index]
        if ($candidate.Current.Name -ne 'Export to PDF...' -or
            $candidate.Current.ControlType -ne [System.Windows.Automation.ControlType]::Text) {
            continue
        }
        $ancestor = $candidate
        for ($depth = 0; $depth -lt 8 -and $ancestor; $depth++) {
            if ($ancestor.Current.ClassName -match '(^|\s)menu-item(\s|$)') { return $ancestor }
            $ancestor = $walker.GetParent($ancestor)
        }
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
        if ($window.Current.Name -like "$noteTitle*Obsidian*") { return $window }
    }
    return $null
}

$handle = [IntPtr]$obsidianWindow.Current.NativeWindowHandle
[ObsidianPdfWindow]::ShowWindow($handle, 9) | Out-Null
[ObsidianPdfWindow]::SetForegroundWindow($handle) | Out-Null

$exportMenuItem = Find-ExportMenuItem -Element $obsidianWindow
if (-not $exportMenuItem) {
    $stalePrompt = Find-Descendant -Element $obsidianWindow -Predicate {
        param($element)
        $element.Current.ClassName -eq 'prompt' -or $element.Current.Name -eq 'Select a command...'
    }
    if ($stalePrompt) {
        [System.Windows.Forms.SendKeys]::SendWait('{ESC}')
        Start-Sleep -Milliseconds 250
    }
    $moreOptionsButton = Find-Descendant -Element $obsidianWindow -Predicate {
        param($element)
        $element.Current.Name -eq 'More options' -and
            $element.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button
    }
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

$exportButton = Wait-Until -FailureMessage 'Obsidian Export to PDF dialog did not open.' -Condition {
    Find-Descendant -Element $obsidianWindow -Predicate {
        param($element)
        $element.Current.Name -eq 'Export to PDF' -and
            $element.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button
    }
}

$dialogItems = @(Get-Descendants -Element $obsidianWindow)
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

$fileNameInput = Find-Descendant -Element $saveDialog -Predicate {
    param($element)
    $element.Current.Name -eq 'File name:' -and
        $element.Current.AutomationId -eq '1001' -and
        $element.Current.ControlType -eq [System.Windows.Automation.ControlType]::Edit
}
$saveButton = Find-Descendant -Element $saveDialog -Predicate {
    param($element)
    $element.Current.Name -eq 'Save' -and $element.Current.AutomationId -eq '1'
}
if (-not $fileNameInput -or -not $saveButton) { throw 'Save As controls were not found.' }

$fileNameInput.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).SetValue($resolvedOutput)
$saveButton.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()

if ($Overwrite) {
    $confirmation = Wait-Until -Timeout 60 -FailureMessage 'No overwrite confirmation was shown.' -Condition {
        if (-not (Test-Path -LiteralPath $resolvedOutput) -or
            (Get-Item -LiteralPath $resolvedOutput).LastWriteTimeUtc -le $beforeWrite) {
            $root = [System.Windows.Automation.AutomationElement]::RootElement
            return Find-Descendant -Element $root -Predicate {
                param($element)
                $element.Current.Name -match '^(Yes|&Yes)$' -and
                    $element.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button
            }
        }
        return $true
    }
    if ($confirmation -is [System.Windows.Automation.AutomationElement]) {
        $confirmation.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
    }
}

Wait-Until -FailureMessage "Obsidian did not finish writing the PDF: $resolvedOutput" -Condition {
    if (-not (Test-Path -LiteralPath $resolvedOutput)) { return $null }
    $file = Get-Item -LiteralPath $resolvedOutput
    if ($file.Length -gt 0 -and $file.LastWriteTimeUtc -gt $beforeWrite) { return $file }
    return $null
} | Out-Null

[ordered]@{
    notePath = $resolvedNote
    outputPath = $resolvedOutput
    includeFileNameAsTitle = $includeTitle
    pageSize = $pageSize
    landscape = $landscape
    margin = $margin
    downscalePercent = [double]$downscale
} | ConvertTo-Json
