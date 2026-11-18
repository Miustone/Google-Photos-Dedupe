# Google Photos Dedupe 🖼️

Ein einsteigerfreundliches Desktop-Tool (Python + Playwright + GUI), das Duplikate in **Google Fotos** zuverlässig findet, die beste Version automatisch auswählt und schlechtere bzw. doppelt hochgeladene Kopien **paketweise (Batch-gesichert)** in den Google Fotos **Papierkorb** verschiebt.

---

## ✨ Features

- **Kinderleichte Schritt-für-Schritt-GUI:** Große Buttons, klare Statusanzeigen, Nebeneinander-Vergleich mit Vorschaubildern, *Tauschen*- & *Überspringen*-Funktion.
- **Kein eigenhändiges Treiber-Setup nötig:** Nutzt automatisch das auf Windows installierte **Google Chrome** oder **Microsoft Edge** (mit persistentem Profil für normalen Google-Login).
- **Zweistufiger Schutz gegen Blockaden (Batch-Bremse):**
  - Scannt das Mediathek-Raster in konfigurierbaren Paketen (Standard `40` + Pause) und speichert alles im lokalen SQLite-Cache (jederzeit pausier- & fortsetzbar).
  - Öffnet die Detail-Seitenleiste (`i`) **nur bei echten Duplikat-Kandidaten** in kleinen Paketen (`15` + Pause).
  - Verschiebt Duplikate in konfigurierbaren Lösch-Paketen (Standard `15` Stück + `4,5 s` Pause) in den **Papierkorb** (60 Tage wiederherstellbar) und protokolliert alles in `trash_log.csv`.
- **Smartes Ranking (Welche Variante bleibt?):**
  1. **Höhere Bildqualität:** Höhere Auflösung (Megapixel) schlägt heruntergerechnete Kopien (*Originalqualität* vor *Speicherplatz sparen* / WhatsApp); bei gleicher Auflösung gewinnt die **größere Datei (weniger Kompression)**.
  2. **Mehr Bildinformationen:** Bei gleicher Qualität gewinnt das Foto mit Kamera-/EXIF-Daten, GPS-Standort oder sauberem Original-Dateinamen statt `foto(1).jpg` / `-kopie`.
  3. **Älteres Erstell- bzw. Upload-Datum:** Bei gleichen Infos bleibt der ältere Originaleintrag erhalten (ideal nach erneuten Festplatten-/Takeout-Backups).

---

## 🚀 Schnellstart (für Nutzer ohne Python)

1. Lade die fertige **`GooglePhotosDedupe.exe`** von der [Releases-Seite](https://github.com/Miustone/Google-Photos-Dedupe/releases) herunter.
2. Starte `GooglePhotosDedupe.exe` per Doppelklick.
3. Folge den 4 Schritten in der App (*Browser öffnen & anmelden → Mediathek scannen → Nebeneinander prüfen → In Papierkorb verschieben*).

---

## 🐍 Start aus dem Quellcode (Windows)

1. Repository herunterladen / klonen nach z. B. `GooglePhotosDedupe`.
2. Doppelklick auf **`start.bat`** – richtet automatisch `.venv`, `requirements.txt` und Playwright ein und startet die GUI.

Alternativ manuell:
```cmd
python -m pip install -r requirements.txt
python app.py
```

Eigene `.exe` lokal bauen:
```cmd
build_exe.bat
```

---

## 📄 Lizenz

Veröffentlicht unter der [MIT-Lizenz](LICENSE) · Copyright (c) 2026 M1U5T0N3.
