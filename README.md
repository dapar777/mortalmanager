# Ultimate Commander

Dvoupanelový souborový manažer pro Windows ve stylu Total Commanderu (Python 3.12+, PySide6).

**Stav:** rozdělaný projekt. Jádro je funkční: dva panely s taby, kopírování/přesun/mazání přes frontu jobů
s průběhem a pause/resume/cancel, archivy (zip, tar, 7z), FTP/SFTP, prohlížeč (F3) a editor (F4, výchozí externí `code`), vestavěný
terminál, paleta příkazů, hledání, hromadné přejmenování, duplicity, hashe, oblíbené, nativní shell context menu,
git/svn stav souborů, tmavé a světlé téma, plynulý zoom celého UI. Chybí balení a GUI testy.

## Spuštění

```bash
py -m venv .venv                      # na novém PC: venv vedle projektu (start.bat ho najde sám)
.venv\Scripts\pip install -r requirements.txt
python -m src.main        # s konzolí (log do konzole)
restart.bat              # restart: zabije běžící instance a spustí novou (pythonw.exe, log v %APPDATA%\UltimateCommander\ultimatecommander.log)
start.bat                # další instance vedle běžících (nic nezabíjí)
```

`start.bat` i `restart.bat` hledají Python přes `find_python.bat`: proměnná `MM_PYTHON`, `.venv` / `venv` vedle
projektu, `C:\mm_venv`, nakonec `pythonw.exe` v PATH; bere první, ve kterém jde importovat PySide6.

## Ukázka

[![Ukázka hlavních funkcí – video](docs/demo-frame.png)](docs/demo.mp4)

Krátké video s hlavními funkcemi: [docs/demo.mp4](docs/demo.mp4) (53 s). Generuje ho `tools/make_demo_video.py`,
který projede skutečnou aplikaci skriptovanou prohlídkou; potřebuje `imageio` + `imageio-ffmpeg`.

## Vzhled

Vzhled se řídí manuálem [solarqt](../solarqt/MANUAL.md) (Solarized, teplý „papír“, sémantické barvy, kreslené
ikony, dvě témata). Balíček je vendorovaný v `src/solarqt/` a rozšířený o **zoom**: jeden faktor
(`theme.set_zoom`) škáluje písma, odsazení, výšky řádků i ikony. Ikona aplikace je z terakota sady
(`assets/icons/ultimatecommander-*.ico`, samotný disk bez pozadí).

| Akce | Klávesy |
| --- | --- |
| Zoom | Ctrl+kolečko, Ctrl++ / Ctrl+−, Ctrl+0 reset (70–200 %) |
| Tmavé / světlé téma | ikona v hlavičce, menu *Show → Dark Theme* |
| Paleta příkazů (víceúrovňová: řazení, disky, taby, oblíbené, historie, téma, zoom; nahoře naposledy použité; od 3 znaků i soubory z indexu a příkazy z historie terminálu) |
| Prefixy palety | mezera = jen příkazy, `c ` historie terminálu (předvyplní řádku), `dc ` mazání z historie (delete command, fyzicky i ze souboru DB) (Enter smaže, paleta zůstane otevřená pro další), `a ` soubory+složky, `f ` soubory, `d ` složky; dotaz může být maska `*.txt` nebo regex | Ctrl+Shift+P, Backspace = o úroveň zpět |
| Enter na souboru | `.lnk` na složku (i síťovou) přejde do složky; spustitelné soubory se spustí: `.exe` `.com` `.msi` `.lnk` `.jar` `.vbs` `.js` … přes shell, `.bat` / `.cmd` / `.ps1` v novém konzolovém okně, které zůstane otevřené; text a obrázky v prohlížeči, ostatní přes asociaci; F3 zobrazí vždy |
| Přejmenovat v místě / hromadně | F2 (Shift+F6) / Ctrl+M |
| Cesta u tabů | vedle tabů je klikací drobečková cesta „C: › Users › dapar“, klik na část = přechod do ní; při nedostatku místa se začátek složí do „…“ s menu |
| Nové okno (další instance) | ikona vedle přepínače tématu, *Files → New Window*, paleta „App › New window“ |
| Přepnutí panelu | Tab |
| Označení souborů (jako v TC) | Insert / mezerník (přepne a posune kurzor), Shift+šipky / PgUp / PgDn / Home / End, Shift+klik = rozsah, Ctrl+klik = přepnout, Ctrl+A, Num +/−/\* |
| Drag & drop z klávesnice | Ctrl+. začne táhnout označené soubory bez myši (velký vlastní kurzor s počtem souborů, kurzor zůstane, kde je); Alt+Tab přepne na cílové okno a teprve pak kurzor skočí do jeho středu, šipky ho posunou o desetinu obrazovky, Enter pustí, Esc zruší bez puštění; klávesy se cílové aplikaci nepředají |
| Drag & drop | tažení označených souborů (nebo řádku pod myší) do druhého panelu, na složku v seznamu, na `..` i do Exploreru a jiných aplikací; zpět z Exploreru sem; bez modifikátoru kopie, Shift přesun |
| Schránka se soubory (kompatibilní s Explorerem) | Ctrl+C / Ctrl+X označené soubory, Ctrl+V vloží do aktivního panelu; vložení do stejné složky vytvoří „název - Kopie.ext“ (přípona v nastavení `copy_suffix`) |
| Kopírovat názvy / celé cesty do schránky | Ctrl+Shift+C / Ctrl+Alt+C (menu Mark) |
| Historie procházení panelu | Alt+Down |
| Rychlý filtr seznamu (jako v TC) | Ctrl+S nebo `*`, Esc zruší, Enter zpět do seznamu |
| Kontextové menu | pravé tlačítko; nahoře sekce nejčastěji používaných položek; na `..` nebo prázdné ploše menu aktuální složky (včetně shell menu Windows) jako v TC |
| Cesta / oblíbené / nový tab | Ctrl+L / Ctrl+D / Ctrl+T |
| Přepnutí disku | lišta disků v hlavičce (včetně síťových, ty se ale nikdy neindexují), Alt+F1 / Alt+F2 = menu disků pro levý / pravý panel, paleta „Go to drive C:“, v terminálu `d:` nebo `cd /d D:\cesta` (panel jde s ním) |
| Editor | F4 = externí editor (výchozí `code -n`, tj. nové okno VS Code, nastavení *Commands → External Editor…*, `{file}` = místo pro cesty; prázdné = vestavěný), *Files → Edit in Built-in Editor* |
| Soubory z panelu v příkazové řádce | Ctrl+Enter vloží jméno, Ctrl+Shift+Enter celou cestu; zástupné znaky `%N` (pod kurzorem), `%P` / `%T` (složka aktivního / druhého panelu), `%S` / `%R` (označené: jména / celé cesty), `%SI` / `%RI` (příkaz se spustí pro každou označenou položku zvlášť), `%%`; platí i pro řádky posílané do interaktivního `python` / `node`, tam bez uvozovek (`print('%SI')`) |
| Terminál | trvalá shell session (cmd / PowerShell / Git Bash běží pořád, `set` / `$env:` / `export` přežijí mezi příkazy, výstup jde průběžně, `cd` v shellu posune panel), tlačítko ↻ nebo Ctrl+C na běžícím příkazu = nová session; Ctrl+Down (do terminálu), Ctrl+Up (zpět), Ctrl+E vymazat, `python` / `node` běží interaktivně (řádky jdou do něj, ukládají se do historie pod jménem programu, Ctrl+D konec, Ctrl+C kill), `cmd` / `powershell` / `vim` / `ssh` se otevřou v novém okně, Alt++ / Alt+− dočasně zvětší / zmenší výstup (vrátí se při odchodu fokusu z terminálu) |

Barvy v kódu: pouze v `src/solarqt/theme.py` (`tests/test_theme.py` to hlídá). Stav git/svn se ukazuje jako
sémantická tečka na ikoně (žlutá = změněno, modrá = nesledováno, zelená = přidáno, červená = smazáno/konflikt).

## Testy a lint

```bash
python -m pytest -q
python -m ruff check src tests
python -m mypy src
```

Nastavení a stav aplikace (včetně tématu a zoomu) se ukládají do SQLite v `%APPDATA%\UltimateCommander` (starší složka `MortalManager` se při prvním startu přejmenuje).

## Index souborů

Paleta hledá soubory podle názvu v indexu (`%APPDATA%\UltimateCommander\index.db`, SQLite FTS5 s trigramy). Co se
indexuje, nastavíte v *Commands → File Index Settings…* (výchozí `C:\` bez `C:\Windows`, `ProgramData`, dočasných
složek, `node_modules`, `.git` apod.). Index udržuje vždy jen jedna běžící instance (Windows mutex; po jejím
ukončení převezme práci další), ostatní z něj jen čtou. Změny na disku se promítají živě přes sledování
adresářů, plný přeskan proběhne po nastaveném intervalu nebo na vyžádání.
