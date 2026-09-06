# MortalManager

Dvoupanelový souborový manažer pro Windows ve stylu Total Commanderu (Python 3.12+, PySide6).

**Stav:** rozdělaný projekt. Jádro je funkční: dva panely s taby, kopírování/přesun/mazání přes frontu jobů
s průběhem a pause/resume/cancel, archivy (zip, tar, 7z), FTP/SFTP, prohlížeč (F3) a editor (F4, výchozí externí `code`), vestavěný
terminál, paleta příkazů, hledání, hromadné přejmenování, duplicity, hashe, oblíbené, nativní shell context menu,
git/svn stav souborů, tmavé a světlé téma, plynulý zoom celého UI. Chybí balení a GUI testy.

## Spuštění

```bash
pip install -e .[dev]
python -m src.main        # s konzolí (log do konzole)
restart.bat              # restart: zabije běžící instance a spustí novou (pythonw.exe, log v %APPDATA%\MortalManager\mortalmanager.log)
start.bat                # další instance vedle běžících (nic nezabíjí)
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
| Paleta příkazů (víceúrovňová: řazení, disky, taby, oblíbené, historie, téma, zoom; nahoře naposledy použité; od 3 znaků i soubory z indexu a příkazy z historie terminálu) |
| Prefixy palety | mezera = jen příkazy, `c ` historie terminálu (předvyplní řádku), `a ` soubory+složky, `f ` soubory, `d ` složky; dotaz může být maska `*.txt` nebo regex | Ctrl+Shift+P, Backspace = o úroveň zpět |
| Přejmenovat v místě / hromadně | F2 (Shift+F6) / Ctrl+M |
| Přepnutí panelu | Tab |
| Označení souborů (jako v TC) | Insert / mezerník (přepne a posune kurzor), Shift+šipky / PgUp / PgDn / Home / End, Shift+klik = rozsah, Ctrl+klik = přepnout, Ctrl+A, Num +/−/\* |
| Kopírovat názvy / celé cesty do schránky | Ctrl+Shift+C / Ctrl+Alt+C (menu Mark) |
| Historie procházení panelu | Alt+Down |
| Rychlý filtr seznamu (jako v TC) | Ctrl+S nebo `*`, Esc zruší, Enter zpět do seznamu |
| Kontextové menu | pravé tlačítko; nahoře sekce nejčastěji používaných položek |
| Cesta / oblíbené / nový tab | Ctrl+L / Ctrl+D / Ctrl+T |
| Editor | F4 = externí editor (výchozí `code`, nastavení *Commands → External Editor…*, `{file}` = místo pro cesty; prázdné = vestavěný), *Files → Edit in Built-in Editor* |
| Terminál | Ctrl+Down (do terminálu), Ctrl+Up (zpět), Ctrl+E vymazat, Alt++ / Alt+− dočasně zvětší / zmenší výstup (vrátí se při odchodu fokusu z terminálu) |

Barvy v kódu: pouze v `src/solarqt/theme.py` (`tests/test_theme.py` to hlídá). Stav git/svn se ukazuje jako
sémantická tečka na ikoně (žlutá = změněno, modrá = nesledováno, zelená = přidáno, červená = smazáno/konflikt).

## Testy a lint

```bash
python -m pytest -q
python -m ruff check src tests
python -m mypy src
```

Nastavení a stav aplikace (včetně tématu a zoomu) se ukládají do SQLite v `%APPDATA%\MortalManager`.

## Index souborů

Paleta hledá soubory podle názvu v indexu (`%APPDATA%\MortalManager\index.db`, SQLite FTS5 s trigramy). Co se
indexuje, nastavíte v *Commands → File Index Settings…* (výchozí `C:\` bez `C:\Windows`, `ProgramData`, dočasných
složek, `node_modules`, `.git` apod.). Index udržuje vždy jen jedna běžící instance (Windows mutex; po jejím
ukončení převezme práci další), ostatní z něj jen čtou. Změny na disku se promítají živě přes sledování
adresářů, plný přeskan proběhne po nastaveném intervalu nebo na vyžádání.
