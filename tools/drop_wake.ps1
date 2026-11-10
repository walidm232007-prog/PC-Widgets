<#
drop_wake.ps1 - hold the Bluetooth advertisement that wakes an iPhone's AirDrop.

    powershell -ExecutionPolicy Bypass -File tools\drop_wake.ps1 [seconds]

An idle iPhone keeps its AirDrop receiver off until a nearby sender's Bluetooth
LE advertisement says someone wants to share. A Mac sends it from Finder's
AirDrop window; on Windows the helper that does AirDrop (DROP.md) leaves it to
whoever runs it. This sends it with Windows' own Bluetooth for a while, so a
send can be tried by hand before the board does this itself. Ctrl+C stops it
early. Other Bluetooth devices stay connected.

It needs nothing installed: Windows PowerShell 5.1 and a Bluetooth adapter that
can advertise. Exit code 0 if the advertisement was on air for the time asked,
1 if Windows would not start it.
#>
param([int]$Seconds = 120)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Devices.Bluetooth.Advertisement.BluetoothLEAdvertisementPublisher, Windows.Devices.Bluetooth, ContentType = WindowsRuntime]
$dataType = [Windows.Devices.Bluetooth.Advertisement.BluetoothLEManufacturerData, Windows.Devices.Bluetooth, ContentType = WindowsRuntime]

# Apple's manufacturer data (company 0x004C) holding the AirDrop entry: 05, its
# length 12 (18 bytes), then 40, a random 3-byte tag, four zero bytes, 03 for
# the version, four 2-byte contact hashes and a closing 00. The hashes are zero:
# a sender with no Apple ID has none, and that wakes an iPhone in Everyone mode
# and no other.
$tag = [byte[]](1..3 | ForEach-Object { Get-Random -Maximum 256 })
[byte[]]$payload = @(0x05, 0x12, 0x40) + $tag + @(0, 0, 0, 0, 0x03) + (@(0) * 9)

$data = [Activator]::CreateInstance($dataType)
$buffer = [System.Runtime.InteropServices.WindowsRuntime.WindowsRuntimeBufferExtensions]::AsBuffer($payload)
$dataType.GetProperty('CompanyId').SetValue($data, [uint16]0x004C, $null)
$dataType.GetProperty('Data').SetValue($data, $buffer, $null)

$publisher = New-Object Windows.Devices.Bluetooth.Advertisement.BluetoothLEAdvertisementPublisher

# Windows PowerShell hands the advertisement's list of data back as a bare COM
# object with no Add method, so the call goes through the collection interface.
$collection = [System.Collections.Generic.ICollection``1].MakeGenericType($dataType)
$one = New-Object 'object[]' 1
$one[0] = $data.PSObject.BaseObject
$null = $collection.GetMethod('Add').Invoke($publisher.Advertisement.ManufacturerData.PSObject.BaseObject, $one)

$publisher.Start()
try {
    for ($i = 0; $i -lt 30 -and "$($publisher.Status)" -notin 'Started', 'Aborted'; $i++) {
        Start-Sleep -Milliseconds 100
    }
    if ("$($publisher.Status)" -ne 'Started') {
        Write-Output "The wake-up did not start (status $($publisher.Status)). Is Bluetooth on?"
        exit 1
    }
    Write-Output "Wake-up on air for $Seconds s. Ctrl+C stops it."
    $until = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $until) {
        if ("$($publisher.Status)" -ne 'Started') {
            Write-Output "The wake-up stopped by itself (status $($publisher.Status))."
            exit 1
        }
        Start-Sleep -Milliseconds 250
    }
    Write-Output 'Wake-up off.'
} finally {
    $publisher.Stop()
}
