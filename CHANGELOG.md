# Changelog

Alle nennenswerten Änderungen an **Google Photos Dedupe** werden hier dokumentiert.

---

## [V1.0.0] - 2026-11-19

### Erstveröffentlichung
- **Playwright Browser-Automation (`chrome` → `msedge` → Chromium):** Nutzt ein persistentes Browserprofil, damit die reguläre Google-Anmeldung sauber funktioniert.
- **Zweistufiger Duplikat-Scanner:** Schneller Raster-Scan per 64-Bit `dHash`, `pHash` (2D-DCT) und Farbhistogramm mit SQLite-Cache (pausier- und fortsetzbar); Detail-Abruf des Google-Fotos-Info-Panels (`i`) ausschließlich für Duplikat-Kandidaten.
- **Intelligentes Auswahl-Ranking:**
  1. Höhere Bildqualität (Auflösung/Megapixel, *Originalqualität* vor *Speicherplatz sparen*, größere Datei / weniger Kompression)
  2. Mehr Bildinformationen (Kamera/EXIF, GPS-Standort, sauberer Original-Dateiname statt `(1)`/`-kopie`)
  3. Älteres Original-Erstell- bzw. Upload-Datum (schützt ältere Bestandseinträge vor neuen Backup-Uploads)
- **Einsteigerfreundliche Tkinter-GUI & Batch-Bremse:** Schritt-für-Schritt-Oberfläche mit Nebeneinander-Vergleich (Vorschaubilder + Metadaten + Grund), *Tauschen*- & *Überspringen*-Buttons, konfigurierbaren Paketgrößen/Pausen und 60-Tage-Papierkorb-Protokoll (`trash_log.csv`).
