# Dieses Skript automatisiert das Hochladen von Änderungen auf GitHub.
# Es prüft auf Änderungen, fragt nach einer Beschreibung, fügt alle Dateien hinzu,
# speichert (commit) und lädt sie hoch (push).

# 1. Prüfen, ob es überhaupt ungespeicherte Änderungen oder neue Dateien gibt.
$gitStatus = git status --porcelain
if ([string]::IsNullOrWhiteSpace($gitStatus)) {
    Write-Host "Keine neuen Aenderungen zum Hochladen gefunden. Alles ist auf dem neuesten Stand." -ForegroundColor Green
    # Kurze Pause, damit der Benutzer die Nachricht lesen kann.
    Start-Sleep -Seconds 3
    exit
}

# 2. Nach einer Beschreibung für die Aenderungen fragen (Commit-Nachricht)
$commitMessage = Read-Host -Prompt "Bitte gib eine kurze Beschreibung der Aenderungen ein (z.B. 'Einkaufsliste verbessert')"

# Überprüfen, ob der Benutzer eine Nachricht eingegeben hat. Abbruch bei leerer Eingabe.
if ([string]::IsNullOrWhiteSpace($commitMessage)) {
    Write-Host "Abbruch: Es wurde keine Commit-Nachricht eingegeben." -ForegroundColor Red
    Read-Host "Drücke Enter, um das Fenster zu schließen."
    exit
}

# 3. Alle geänderten und neuen Dateien hinzufügen (git add)
Write-Host "Fuege alle Aenderungen hinzu (git add .)..." -ForegroundColor Cyan
git add .
if ($LASTEXITCODE -ne 0) {
    Write-Host "FEHLER: 'git add' ist fehlgeschlagen. Ueberpruefe die Git-Installation." -ForegroundColor Red
    Read-Host "Drücke Enter, um das Fenster zu schließen."
    exit
}

# 4. Die Aenderungen lokal speichern (git commit)
Write-Host "Speichere die Aenderungen lokal (git commit)..." -ForegroundColor Cyan
git commit -m "$commitMessage"
if ($LASTEXITCODE -ne 0) {
    Write-Host "FEHLER: 'git commit' ist fehlgeschlagen. Ueberpruefe die Git-Meldungen oben." -ForegroundColor Red
    Read-Host "Drücke Enter, um das Fenster zu schließen."
    exit
}

# 5. Die Aenderungen auf GitHub hochladen (git push)
Write-Host "Lade Aenderungen auf GitHub hoch (git push)..." -ForegroundColor Cyan
git push

Write-Host "Update erfolgreich abgeschlossen! Die Aenderungen sind jetzt auf GitHub." -ForegroundColor Green
Read-Host "Drücke Enter, um das Fenster zu schließen."