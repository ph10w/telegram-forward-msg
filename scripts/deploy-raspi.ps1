param(
    [Parameter()]
    [string]$Pi = "fspi@rpi",

    [Parameter()]
    [string]$Target = "/home/fspi/telegram-forward-msg"
)

[string]$Service = "telegram-voice-forwarder.service"

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# Prüfen, ob wir uns in einem Git-Repository befinden
$GitRoot = git rev-parse --show-toplevel 2>$null

if ($LASTEXITCODE -ne 0 -or -not $GitRoot) {
    throw "Kein Git-Repository gefunden."
}

$GitRoot = $GitRoot.Trim()
$SourceRevision = (git -C $GitRoot rev-parse HEAD 2>$null).Trim()

if ($LASTEXITCODE -ne 0 -or $SourceRevision -notmatch '^[0-9a-f]{40}$') {
    throw "Git-Quellrevision konnte nicht ermittelt werden."
}

$TrackedChanges = @(git -C $GitRoot status --porcelain --untracked-files=no)

if ($LASTEXITCODE -ne 0) {
    throw "Git-Arbeitsbaumstatus konnte nicht ermittelt werden."
}

if ($TrackedChanges.Count -gt 0) {
    throw "Getrackte Aenderungen verhindern eine eindeutige Deployment-Revision."
}

# Temporäres Deployment-Verzeichnis
$TempDir = Join-Path ([System.IO.Path]::GetTempPath()) `
    ("pi-deploy-" + [guid]::NewGuid().ToString("N"))

try {
    Write-Host "Git-Repository: $GitRoot"
    Write-Host "Ziel:           ${Pi}:${Target}"
    Write-Host ""

    New-Item `
        -ItemType Directory `
        -Path $TempDir `
        -Force | Out-Null

    #
    # Alle Dateien ermitteln:
    #
    # --cached           = von Git getrackte Dateien
    # --others           = untracked Dateien
    # --exclude-standard = .gitignore, .git/info/exclude usw. beachten
    #
    $Files = @(
        git -C $GitRoot `
            -c core.quotepath=false `
            ls-files `
            --cached `
            --others `
            --exclude-standard
    )

    if ($LASTEXITCODE -ne 0) {
        throw "git ls-files ist fehlgeschlagen."
    }

    Write-Host "$($Files.Count) Dateien werden vorbereitet..."

    foreach ($RelativePath in $Files) {

        if ([string]::IsNullOrWhiteSpace($RelativePath)) {
            continue
        }

        $Source = Join-Path $GitRoot $RelativePath
        $Destination = Join-Path $TempDir $RelativePath

        $DestinationDirectory = Split-Path $Destination -Parent

        if (-not (Test-Path -LiteralPath $DestinationDirectory)) {
            New-Item `
                -ItemType Directory `
                -Path $DestinationDirectory `
                -Force | Out-Null
        }

        Copy-Item `
            -LiteralPath $Source `
            -Destination $Destination `
            -Force
    }

    Set-Content `
        -LiteralPath (Join-Path $TempDir ".source-revision") `
        -Value $SourceRevision `
        -NoNewline

    Write-Host "Zielverzeichnis auf dem Pi erstellen..."

    & ssh $Pi "mkdir -p '$Target'"

    if ($LASTEXITCODE -ne 0) {
        throw "SSH-Verbindung bzw. mkdir auf dem Pi fehlgeschlagen."
    }

    Write-Host "Dateien werden übertragen..."

    #
    # Der Punkt sorgt dafür, dass auch versteckte Dateien aus dem
    # Staging-Verzeichnis übertragen werden. Die von Git ignorierte .env
    # wird von git ls-files nicht gelistet und folgt deshalb separat.
    #
    & scp -r "$TempDir/." "${Pi}:${Target}/"

    if ($LASTEXITCODE -ne 0) {
        throw "SCP-Übertragung fehlgeschlagen."
    }

    Write-Host "Konfiguration .env wird übertragen..."

    if (-not (Test-Path -LiteralPath (Join-Path $GitRoot ".env"))) {
        throw "Die Datei .env wurde im Repository nicht gefunden."
    }

    Push-Location $GitRoot
    try {
        & scp ".env" "${Pi}:${Target}/.env"

        if ($LASTEXITCODE -ne 0) {
            throw "SCP-Übertragung der .env fehlgeschlagen."
        }

        & ssh $Pi "chmod 600 '$Target/.env'"

        if ($LASTEXITCODE -ne 0) {
            throw "Schutzrechte der .env auf dem Pi konnten nicht gesetzt werden."
        }
    }
    finally {
        Pop-Location
    }

    Write-Host ""
    Write-Host "Deployment erfolgreich abgeschlossen."

    if ($Service) {
		Write-Host "Service '$Service' wird neu gestartet..."

		& ssh $Pi "sudo systemctl restart '$Service'"

		if ($LASTEXITCODE -ne 0) {
			throw "Service '$Service' konnte nicht neu gestartet werden."
	    }
    }
}
finally {

    if (Test-Path -LiteralPath $TempDir) {
        Remove-Item `
            -LiteralPath $TempDir `
            -Recurse `
            -Force
    }
}
