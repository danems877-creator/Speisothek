# Dieses Skript startet die Streamlit Inventar-App für die lokale Entwicklung.
# Es stellt sicher, dass die virtuelle Umgebung und alle Abhängigkeiten vorhanden sind.

# Pfad zum Python-Interpreter in der virtuellen Umgebung
$venv_path = ".\.venv"
$python_executable = Join-Path $venv_path "Scripts\python.exe"

# 1. Überprüfen und Erstellen der virtuellen Umgebung
if (-not (Test-Path $venv_path)) {
    Write-Host "Virtuelle Umgebung (.venv) nicht gefunden. Sie wird jetzt erstellt..." -ForegroundColor Yellow
    py -m venv $venv_path
    if ($LASTEXITCODE -ne 0) {
        Write-Host "FEHLER: Die virtuelle Umgebung konnte nicht erstellt werden." -ForegroundColor Red
        Read-Host "Drücke Enter, um das Fenster zu schließen."
        exit
    }
}

# 2. Installieren der Abhängigkeiten
Write-Host "Installiere/Aktualisiere Abhängigkeiten..." -ForegroundColor Cyan
& $python_executable -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Write-Host "FEHLER: Abhängigkeiten konnten nicht installiert werden." -ForegroundColor Red
    Read-Host "Drücke Enter, um das Fenster zu schließen."
    exit
}

# 3. Starte die Streamlit-App
Write-Host "Starte die Inventar-App..." -ForegroundColor Green
& $python_executable -m streamlit run app.py