<#
.SYNOPSIS
    CarBot Headless CLI Manager for Windows PowerShell.
.DESCRIPTION
    Provides commands to interact with the Python Motion Server on Jetson Xavier NX.
#>

$Global:CarbotDefaultIP   = "192.168.99.1"
$Global:CarbotDefaultPort = 5000

function Send-CarBotCommand {
    param (
        [Parameter(Mandatory = $true)]
        [hashtable]$Payload,

        [string]$IPAddress = $Global:CarbotDefaultIP,
        [int]$Port         = $Global:CarbotDefaultPort
    )

    try {
        $json = $Payload | ConvertTo-Json -Compress

        $client = New-Object System.Net.Sockets.TcpClient
        $client.Connect($IPAddress, $Port)

        $stream = $client.GetStream()
        $writer = New-Object System.IO.StreamWriter($stream)
        $reader = New-Object System.IO.StreamReader($stream)

        $writer.WriteLine($json)
        $writer.Flush()

        $response = $reader.ReadLine()

        $reader.Close()
        $writer.Close()
        $stream.Close()
        $client.Close()

        if ($response) {
            return $response | ConvertFrom-Json
        }
        return $null
    }
    catch {
        Write-Error "CarBot connection failed ($IPAddress`:$Port): $_"
        return $null
    }
}

function carbot-play {
    param (
        [Parameter(Position = 0, Mandatory = $true)]
        [string]$FileName,

        [switch]$Loop
    )

    $payload = @{
        cmd  = "play"
        file = $FileName
        loop = [bool]$Loop
    }

    $resp = Send-CarBotCommand -Payload $payload

    if ($resp -and $resp.status -eq "started") {
        Write-Host "Started: $FileName" -ForegroundColor Green
    }
    elseif ($resp) {
        Write-Host "Failed: $($resp.error)" -ForegroundColor Red
    }
}

function carbot-stop {
    param (
        [ValidateSet("soft", "hard")]
        [string]$Mode = "soft"
    )

    $payload = @{
        cmd  = "stop"
        mode = $Mode
    }

    $resp = Send-CarBotCommand -Payload $payload

    if ($resp) {
        Write-Host "Stopped ($Mode)" -ForegroundColor Cyan
    }
}

function carbot-status {
    $payload = @{ cmd = "status" }
    $resp = Send-CarBotCommand -Payload $payload

    if (-not $resp) {
        Write-Host "No response from robot" -ForegroundColor Red
        return
    }

    Write-Host "`n=== CarBot Status ===" -ForegroundColor Cyan

    if ($resp.is_playing) {
        Write-Host "State: PLAYING" -ForegroundColor Green
    } else {
        Write-Host "State: IDLE" -ForegroundColor DarkGray
    }

    if ($resp.positions) {
        Write-Host "`nMotor Positions:"
        foreach ($prop in $resp.positions.PSObject.Properties) {
            Write-Host ("  Servo {0}: {1}" -f $prop.Name, $prop.Value)
        }
    }

    Write-Host "====================" -ForegroundColor Cyan
}

function carbot-neutral {
    $resp = Send-CarBotCommand -Payload @{ cmd = "neutral" }

    if ($resp -and $resp.status -eq "neutral") {
        Write-Host "Torque OFF (Free move)" -ForegroundColor Yellow
    }
}

function carbot-freeze {
    $resp = Send-CarBotCommand -Payload @{ cmd = "freeze" }

    if ($resp -and $resp.status -eq "frozen") {
        Write-Host "Torque ON (Locked)" -ForegroundColor Red
    }
}

function carbot-record {
    param (
        [Parameter(Position = 0, Mandatory = $true)]
        [string]$FileName,

        [double]$Delay    = 0.0,
        [double]$Duration = 1.0,
        [int]$Speed       = 200
    )

    $payload = @{
        cmd      = "record"
        file     = $FileName
        delay    = $Delay
        duration = $Duration
        speed    = $Speed
    }

    $resp = Send-CarBotCommand -Payload $payload

    if ($resp -and $resp.status -eq "recorded") {
        Write-Host "Frame #$($resp.frame_count) saved to '$($resp.file)'" -ForegroundColor Green
    }
    elseif ($resp) {
        Write-Host "Record failed: $($resp.error)" -ForegroundColor Red
    }
}

function Invoke-CarbotMenu {
    $currentFile = "actions/test1.json"

    while ($true) {
        Write-Host ""
        Write-Host "------CarBot Windows Manager --------------------" -ForegroundColor Cyan
        Write-Host " v  - view status"
        Write-Host " t  - toggle torque"
        Write-Host " f  - set filename ($currentFile)"
        Write-Host " r  - record frame"
        Write-Host " p  - play"
        Write-Host " s  - stop"
        Write-Host " q  - quit"
        Write-Host "------------------------------------------------" -ForegroundColor Cyan

        $choice = (Read-Host " > ").ToLower()

        switch ($choice) {
            "v" { carbot-status }

            "t" {
                $t = (Read-Host " [f]reeze / [n]eutral").ToLower()
                if ($t -eq "f") { carbot-freeze }
                elseif ($t -eq "n") { carbot-neutral }
            }

            "f" {
                $new = Read-Host " Enter filename"
                if (-not [string]::IsNullOrWhiteSpace($new)) {
                    $currentFile = $new
                }
            }

            "r" {
                $delay    = Read-Host " Delay [0.5]"
                $duration = Read-Host " Duration [1.0]"
                $speed    = Read-Host " Speed [200]"

                if ([string]::IsNullOrWhiteSpace($delay))    { $delay = 0.5 }
                if ([string]::IsNullOrWhiteSpace($duration)) { $duration = 1.0 }
                if ([string]::IsNullOrWhiteSpace($speed))    { $speed = 200 }

                carbot-record `
                    -FileName $currentFile `
                    -Delay ([double]$delay) `
                    -Duration ([double]$duration) `
                    -Speed ([int]$speed)
            }

            "p" { carbot-play -FileName $currentFile }
            "s" { carbot-stop }
            "q" { Write-Host "Exited"; return }

            default { Write-Host "Invalid option" -ForegroundColor Red }
        }
    }
}

Write-Host "CarBot CLI Loaded" -ForegroundColor DarkCyan
Write-Host "Commands: carbot-play, carbot-stop, carbot-status, carbot-neutral, carbot-freeze, carbot-record, Invoke-CarbotMenu"