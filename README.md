# MortalManager

Dvoupanelový souborový manažer pro Windows ve stylu Total Commanderu (Python 3.12+, PySide6).

**Stav:** rozdělaný projekt. Jádro je funkční: dva panely s taby, kopírování/přesun/mazání přes frontu jobů
s průběhem a pause/resume/cancel, archivy (zip, tar, 7z), FTP/SFTP, prohlížeč (F3) a editor (F4), vestavěný
terminál, paleta příkazů, hledání, hromadné přejmenování, duplicity, hashe, oblíbené, nativní shell context menu,
git/svn stav souborů, tmavé a světlé téma, plynulý zoom celého UI. Chybí balení a GUI testy.

## Spuštění

```bash
pip install -e .[dev]
python -m src.main        # s konzolí (log do konzole)
restart.bat              # bez konzole (pythonw.exe), log v %APPDATA%\MortalManager\mortalmanager.log
```

## Vzhled

Vzhled se řídí manuálem [solarqt](../solarqt/MANUAL.md) (Solarized, teplý „papír“, sémantické barvy, kreslené
ikony, dvě témata). Balíček je vendorovaný v `src/solarqt/` a rozšířený o **zoom**: jeden faktor
(`theme.set_zoom`) škáluje písma, odsazení, výšky řádků i ikony. Ikona aplikace je z terakota sady
(`assets/icons/mortalmanager-*.ico`, samotný disk bez pozadí).

| Akce | Klávesy |
| --- | --- |
| Zoom | Ctrl+kolečko, Ctrl++ / Ctrl+−, Ctrl+0 reset (70–200 %) |
| Tmavé / světlé téma | ikona v hlavičce, menu *Show → Dark Theme* |
| Paleta příkazů (víceúrovňová: řazení, disky, taby, oblíbené, historie, téma, zoom; nahoře naposledy použité) | Ctrl+Shift+P, Backspace = o úroveň zpět |
| Přejmenovat v místě / hromadně | F2 (Shift+F6) / Ctrl+M |
| Přepnutí panelu | Tab |
| Označení souboru | Mezerník, Ctrl+A, Num +/−/\* |
| Kopírovat názvy / celé cesty do schránky | Ctrl+Shift+C / Ctrl+Alt+C (menu Mark) |
| Historie procházení panelu | Alt+Down |
| Kontextové menu | pravé tlačítko; nahoře sekce nejčastěji používaných položek |
| Cesta / oblíbené / nový tab | Ctrl+L / Ctrl+D / Ctrl+T |
| Terminál | Ctrl+Down (do terminálu), Ctrl+Up (zpět), Ctrl+E vymazat |

Barvy v kódu: pouze v `src/solarqt/theme.py` (`tests/test_theme.py` to hlídá). Stav git/svn se ukazuje jako
sémantická tečka na ikoně (žlutá = změněno, modrá = nesledováno, zelená = přidáno, červená = smazáno/konflikt).

## Testy a lint

```bash
python -m pytest -q
python -m ruff check src tests
python -m mypy src
```

Nastavení a stav aplikace (včetně tématu a zoomu) se ukládají do SQLite v `%APPDATA%\MortalManager`.
