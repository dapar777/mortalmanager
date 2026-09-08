# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Co to je

Ultimate Commander (repo zatím `mortalmanager`) — dvoupanelový souborový manažer pro Windows ve stylu Total Commanderu (Python 3.12+, PySide6).
Rozdělaný projekt: jádro (panely, taby, kopírování/přesun/mazání přes frontu jobů, archivy, FTP/SFTP, prohlížeč F3,
editor F4, vestavěný terminál, command palette, VCS tečky u souborů, zoom UI) je funkční; chybí balení a GUI testy.
`README.md` je krátký český přehled. Kód, docstringy a komentáře jsou anglicky, UI anglicky, komunikace s autorem česky.

## Příkazy

Aplikace se spouští jako modul z kořene repa (`src/main.py` si sám přidá kořen do `sys.path`). Na stroji autora
je venv v `C:\mm_venv` (Python 3.12, PySide6, pytest); `start.bat` / `restart.bat` ale Python hledají přes
`find_python.bat` (`MM_PYTHON`, `.venv`/`venv` vedle repa, `C:\mm_venv`, `pythonw.exe` v PATH – první, kde jde
`import PySide6`), takže na cizím PC stačí venv vedle projektu; žádná cesta nesmí být v .bat natvrdo.
`restart.bat` zabije **všechny** běžící python/pythonw procesy s `src.main` v příkazové řádce (PowerShell
`Get-CimInstance`, ne `wmic` – to v novém Windows 11 chybí) a spustí novou instanci přes `pythonw.exe` (bez konzole;
`src/main.py` pak loguje do `%APPDATA%\UltimateCommander\ultimatecommander.log`). `pyproject` má `[project.gui-scripts]`, ne `scripts`. Venv stojí na **Pythonu z Microsoft Store (MSIX)** – hlavní panel
proto ignoruje ikonu okna; `MainWindow._apply_taskbar_identity` nastavuje AppUserModel vlastnosti přímo na HWND
(pywin32 `propsys`), bez toho je v panelu ikona Pythonu. Tentýž MSIX Python **virtualizuje zápisy do `%APPDATA%`**: složka
`UltimateCommander` (config.db, index.db, log) je fyzicky v `%LOCALAPPDATA%\Packages\PythonSoftwareFoundation.Python.3.12_…\LocalCache\Roaming\`,
z Exploreru/PowerShellu v `%APPDATA%` není vidět. ruff a mypy v `C:\mm_venv` nainstalované
nejsou (`pip install -e .[dev]` je doplní); jejich konfigurace cílí na Python 3.13.

```bash
python -m src.main                                  # spustit aplikaci
restart.bat                                         # restart běžící instance (Python přes find_python.bat)
start.bat                                           # další instance vedle běžících (nic nezabíjí)
python -m pytest -q                                 # testy (87, běží ~4 s, headless Qt přes offscreen)
python -m pytest -q tests/test_theme.py             # vzhled: hex jen v theme.py, zoom, ikony, barvy tabulky
python -m pytest -q tests/test_core.py::test_format_size_kb   # jeden test
python -m ruff check src tests                      # lint
python -m mypy src                                  # typy, strict
pip install -e .[dev]                               # vývojová instalace
```

`tools/make_demo_video.py` nahraje ukázkové video `docs/demo.mp4`: řídí skutečnou aplikaci (skryté okno, `grab()` vrací
device pixely – při 120 % škálování se přepočítávají na logické; paleta se skládá jako overlay; config se neukládá),
potřebuje `imageio` + `imageio-ffmpeg`. Testy pokrývají `core`, `filesystem`, `archive`, `database` a vzhled (`test_theme.py`; `tests/conftest.py`
dává session fixture `qapp` s `QT_QPA_PLATFORM=offscreen`). GUI testy hlavního okna neexistují – `MainWindow`
čte reálný config v `%APPDATA%`. `qt_api` v pytest konfiguraci hlásí varování (`pytest-qt` není nainstalován).
Kořenové `temp_shell*_debug*.py` jsou jednorázové průzkumné skripty z ladění nativní Windows shell context menu;
samotná funkce je v `PanelWidget._show_context_menu`. Pasti pywin32: `IContextMenu.InvokeCommand` bere **8prvkovou**
n-tici `(fMask, hwnd, verb, params, dir, nShow, hotkey, hicon)` (9 prvků = tichý TypeError); COM se na GUI vlákně
inicializuje jednou (`_com_init`) a nikdy neodinicializuje pod Qt; shell verby `open/delete/rename/copyaspath` se
přeskakují (máme vlastní položky, shell „rename“ mimo Explorer nic nedělá). Pod `pythonw` jdou výjimky ze slotů do
logu přes `sys.excepthook`.

## Architektura

Vrstvy jsou balíčky pod `src/`, GUI závisí na všech ostatních, ostatní na GUI ne:

- **`core/`** — čisté doménové typy bez Qt: `FileEntry`, `DriveInfo`, `SortField`, `SearchQuery`,
  `OperationProgress` (`file_model.py`), `SelectionManager`, `NavigationHistory` (zpět/vpřed),
  `UndoManager` (zásobník vratných souborových operací).
- **`filesystem/`** — `FileSystemProvider` (ABC) s async metodami; `LocalFileSystemProvider` je Windows-optimalizovaný
  (výpis adresáře jedním `os.scandir` průchodem bez stat() na soubor, koš, disky přes `GetLogicalDriveStrings` +
  `GetDriveType` – `psutil.disk_partitions` síťové disky vynechává; `filesystem/drives.get_all_drives`, které používá
  `DriveBar`, na to jen deleguje –, hledání, výpočet velikosti),
  `DirectoryWatcher` emituje `directory_changed(str)`. FTP/SFTP (`ftp/`) jsou samostatní klienti, ne provider.
- **`jobs/`** — `JobQueue` (QObject) běží nad **asyncio smyčkou**, kterou `src/main.py` pumpuje z Qt `QTimer`
  každých 20 ms. Dlouhé operace se odesílají jako `JobSpec(job_type: JobType, sources, destination, options)`
  přes `MainWindow._submit` (eviduje spec podle job_id); fronta hlásí `job_started / job_progress(job_id,
  OperationProgress) / job_finished(job_id, JobResult) / job_failed`, `MainWindow` po dokončení obnoví oba panely
  a ukáže `Toast`. `JobResult.undo_pairs` krmí `UndoManager`. Cokoli blokujícího musí jít touto cestou.
- **`archive/`** — `ArchiveHandler` (ABC) + zip/tar/7z handlery za `ArchiveManager`.
- **`database/`** — `DatabaseManager` nad SQLite v `%APPDATA%\UltimateCommander` (`config._migrate_data_dir` při prvním startu přejmenuje starou složku `MortalManager`; nastavení, záložky, oblíbené, FTP
  relace, historie příkazů a cest, historie operací, taby). **`settings/ConfigManager`** je singleton
  (`get_instance()`) s dataclassami `AppConfig` (mj. `theme`, `zoom`) / `PanelConfig`.
- **`index/`** — index názvů souborů pro paletu, sdílený všemi instancemi: `file_index.py` (SQLite
  `%APPDATA%\UltimateCommander\index.db`, WAL; tabulka `files` + FTS5 s **trigram** tokenizerem, takže `MATCH '"rep"'` je
  indexový dotaz (**ne** `LIKE … ESCAPE` – ESCAPE optimalizaci vypne, 500 ms místo 5 ms na 1,2 M záznamů); generace `gen` pro čištění smazaných po plném skenu; `Excluder` = jména složek kdekoli + prefixy
  cest; `is_network_path` vyřadí síťové disky a UNC z kořenů i když jsou v nastavení, cloudové složky na FIXED disku
  zůstávají) a `indexer.py` (vlákno; **vůdce = držitel Windows named mutexu** `Local\UltimateCommander.Indexer`, ostatní
  instance jen čtou a zkoušejí to každých 30 s; plný sken po `rescan_hours`, živě přes `ReadDirectoryChangesW`
  rekurzivně na každý kořen, přetečení bufferu = přeskan kořene). Bez Qt, testy v `tests/test_index.py`.
  V GUI ho drží `gui/index_service.py` (start 3 s po oknu, čtecí spojení pro hledání, konfigurace z `AppConfig.index_*`,
  dialog `dialogs/index_dialog.py`).
- **`plugins/`** — `PluginManager(plugin_dir)` načítá `*.py` moduly; typy `ListerPlugin`, `ContentPlugin`, `PackerPlugin`.
- **`solarqt/`** — vendorovaný designový systém z `c:\code\solarqt` (manuál `MANUAL.md` tam je závazný vzor;
  referenční aplikace `c:\code\task-master`). `theme.py` = **jediné místo s hex barvami** (Solarized tokeny
  `LIGHT`/`DARK`, `semantic_style`, `status_style` pro VCS stavy, písma, QSS). Rozšíření oproti originálu:
  **zoom** – `theme.set_zoom(f)`, `theme.px(n)` / `theme.pt(n)` škálují každou velikost v QSS i v kódu;
  `icons.pixmap(name, size)` už zoom aplikuje. `widgets.retheme_tree(root, repolish=)` volá `retheme()` na
  každém widgetu, který drží barvu/velikost mimo QSS (IconButton, tabulka, panel, terminál…).
- **`gui/`** — `MainWindow`: `#headerBar` (logo z `assets/icons`, název, `DriveBar` – disky enumeruje worker
  v `QThreadPool`, „Search“, „Commands“, tlačítko nového okna = `_new_instance` (další proces `-m src.main`), přepínač tématu) · splitter dvou `PanelWidget` + `#fkeysBar` ·
  `EmbeddedTerminalWidget` (`shell_session.py` `ShellSession` = **trvalý** cmd/powershell/bash proces s rourami: každý
  příkaz následuje sentinel `__UC_DONE__ rc cwd`, výstup streamuje, cmd má obě roury v OEM kódování (`chcp 65001` rozbije
  vstup) a prompt `__UCP__`, který se odstraňuje; PowerShell přes smyčku `Invoke-Expression` po řádcích,
  `-Command -` by čekalo na EOF; `restart_shell()` = tlačítko ↻ / Ctrl+C na běžícím příkazu; během běhu má řádka placeholder „running…“, příkaz
  delší než 2 s dostane `[done · 12.3 s]` (git clone při rouře na výstupu mlčí); `_CmdRunner` zůstal jen
  jako záloha, když se shell nespustí; zástupné znaky
  `%N %P %T %S %R %SI %RI` rozbaluje čistý `core/cmdline.py` (`expand` → seznam příkazů, `%SI`/`%RI` = jeden
  na označenou položku, terminál je pouští za sebou přes `_queue`) z `CmdContext`, který dodává
  `MainWindow._terminal_context`; Ctrl+Enter / Ctrl+Shift+Enter v tabulce = signál `cmdline_insert` → `insert_text`; v REPL sezení se
  rozbalují bez uvozovek (`quoted=False`, `has_placeholders(strict=True)` ignoruje samotné `%%`) a `%SI`/`%RI`
  pošlou řádek za každou položku; samotné `d:` = přepnutí disku, `cd /d` se toleruje; `terminal_session.py`:
  `classify` pozná REPL (`python`, `py`, `node` bez skriptu → `ReplSession`, trvalý proces s rourami, `-i -q`,
  prompty `>>> `/`... ` se odloupnou do popisku řádky, Ctrl+D = EOF, Ctrl+C = kill, řádky do DB historie pod jménem REPL, `shutdown()` při zavření okna)
  a konzolové programy (`cmd`, `powershell`, `vim`, `ssh`… → nové konzolové okno); Alt++ / Alt+− v řádce = signál `height_step`, `MainWindow._terminal_height_step`
  dočasně přenastaví `_v_splitter`, `_on_focus_changed` přes `QApplication.focusChanged` vrátí původní výšku, jakmile
  fokus opustí terminál) · stavový řádek (info aktivního panelu, zoom %, volné místo). Titulek okna = cesta aktivního panelu + název (`_update_title` při změně cesty i panelu). Zoom: Ctrl+kolečko
  (globální event filter), Ctrl+±, Ctrl+0; `_zoom_step` je **throttlovaný** – první notch se aplikuje hned,
  další se během 220 ms slučují, protože `theme.apply` + repolish celého okna stojí 150–300 ms. Zoom i téma se
  persistují do configu. Pod `theme.HEADER_COMPACT_BELOW` (1100 px × zoom) se schovají texty v hlavičce a v F-liště.
  `PanelWidget` = `QFrame#panel` s property `active` (aktivní = akcentový rámeček): hlavička (zpět/vpřed/nahoru,
  `#pathEdit`, refresh, oblíbené) + řádek `QTabBar` (jen šířka tabů) + `gui/breadcrumb.py` `Breadcrumb` (klikací
  segmenty cesty, signál `path_clicked` → `navigate_to`, úvodní segmenty se při nedostatku místa složí do „…“ s menu,
  QSS `#breadcrumb`) + `FileTableView` + patička. Adresář načítá asynchronně s **generací**
  (pomalý výpis nikdy nepřepíše novější), VCS root/status detekuje `_VcsInfo` v executoru s cache na kořen
  repa (nikdy subprocess z GUI vlákna). Signály ven: `path_changed`, `entry_activated(FileEntry)` (jen soubory; `MainWindow._on_entry_open`: `.lnk` na složku = `navigate_to` cíle (`filesystem/shortcut.py`, IShellLink), `_EXEC_EXTENSIONS` se spustí
  přes `_run_file` – bat/cmd v novém okně `cmd /K`, ps1 přes `powershell -NoExit -File` –, text/obrázky do prohlížeče, zbytek `os.startfile`),
  `status_info`, `request_focus`, `favorites_requested`. `FileTableModel` bere barvy z `_Look` (cache per téma),
  shell ikony cachuje per přípona (per soubor jen exe/lnk/ico/url…), VCS stav kreslí jako sémantickou tečku.
  Označené soubory = akcent (`semantic_fg["accent"]` + tint), kurzor = `selection`. Sloupce Attr → Date se při
  úzkém panelu schovají (`_fit_columns`). Alt+Down = `show_history_menu` (historie tabu + DB path history). Ctrl+S nebo `*` (hlavní klávesnice; numerická `*` zůstává výběr) = rychlý filtr jako v TC: pole pod seznamem,
  podřetězec nebo maska `*?`, Esc zruší, Enter vrátí fokus do seznamu s filtrem, šipky posouvají kurzor; filtr je
  stav tabu (`_Tab.filter_text`), maže se při změně adresáře; `_unfiltered` drží plný výpis. Označování jako v TC: Insert/mezerník přepne a posune kurzor, Shift+šipky/PgUp/PgDn/Home/End přepnou přejeté řádky
  (`FileTableView._shift_navigate` → signál `toggle_rows`), Shift+klik označí rozsah (`mark_rows`), Ctrl+klik přepne;
  stav označení drží `_Tab.selection` (`SelectionManager`), model jen zobrazuje (`set_selected`). F2 / Shift+F6 = přejmenování v místě
  (`FileTableModel.setData` → `rename_requested` → `MainWindow._on_inline_rename` → job RENAME; delegát předvybere
  jméno bez přípony, editor `#renameEditor` je o pár px vyšší než řádek kvůli dolním dotahům; **`keyPressEvent` tabulky
  musí ve stavu `EditingState` volat jen `super()`** – QLineEdit Enter/Esc po zpracování ignoruje a klávesa by jinak
  spustila položku nebo Tabem přepnula panel), Ctrl+M = hromadné.
  Alt+F1 / Alt+F2 = `MainWindow._show_drive_menu(side)` (menu disků nad panelem, výběr panel aktivuje).
  Dlouhá menu jdou přes `panel.fit_menu_on_screen` (vrací zvolenou akci), které při přetečení obrazovky zmenší svislý padding položek
  jen o tolik, kolik je nutné (per-menu stylesheet), a až pak sáhne po QSS property `compact="dense"` (menší písmo),
  aby Qt nelámalo menu do dvou sloupců. Kontextové menu na `..`/prázdné ploše
  je menu aktuální složky (`_populate_windows_shell_menu([cur])`; kořen disku se váže přes desktop folder a absolutní
  pidl). Kontextové menu má nahoře sekci „Frequently used“:
  `_add_frequent_section` počítá kliknutí na položky (klíč = text bez `&` a zkratky, i shell položky) do DB
  settings `context_menu_usage` a ukazuje až 4 položky s ≥2 použitími. Dialogy v `gui/dialogs/` jsou stock widgety stylované QSS;
  `command_palette.py` je paleta „Kategorie · Příkaz [zkratka]“ podle Task Masteru.
- **`viewer/`, `editor/`** — samostatná okna pro F3/F4; F4 nejdřív zkusí externí editor (`AppConfig.external_editor`,
  výchozí `code -n`, uložené holé `code` se při načtení povýší; `editor/external.py`: tokenizace s uvozovkami, `{file}` = cesty, `code` → `Code.exe` místo
  `code.cmd`, nenalezený program = Toast + vestavěný editor; dialog `dialogs/editor_dialog.py`); mono písmo `theme.mono_font()`, zvýraznění syntaxe
  ze Solarized konstant (`theme.GREEN` klíčová slova, `CYAN` řetězce, `MAGENTA` čísla).

## Paleta příkazů = registr všech funkcí

`MainWindow._build_palette_commands` je **první místo**, kam patří každá nová uživatelská funkce (kategorie, popisek,
zkratka, `run`); menu a F-lišta jsou jen podmnožiny. Položka s `children` (seznam nebo callable) otevře další úroveň
jako ve VS Code (Sort by › Size › Descending, Switch tab, Favourites, History, Theme, Zoom,
Terminal shell); `checked=True` označí aktuální stav; disky jsou naopak ploché příkazy „Go to drive C:“ (label stálý, popis disku ve sloupci zkratky). Nová funkce bez záznamu v paletě = nedokončená. Paleta ukazuje nahoře naposledy použité příkazy (i listy
podúrovní zploštělé na „Sort by › Size › Descending“); cesty se ukládají do DB settings `palette_recent`, klíč =
popisky oddělené `|`, takže **přejmenování popisku příkazu** starý záznam tiše zahodí.
Hledání na kořenové úrovni prohledává i listy podúrovní (zploštělé, `_deep_entries`, hloubka 3), takže „name“
najde „Sort by › Name › Ascending“.
Dynamická úroveň = položka se `search` (callable(q) → seznam), např. „Find file on disk“ nad indexem; `extra_search`
palety přidá na kořenové úrovni od 3 znaků pár souborů a příkazů z historie terminálu („Terminal history ›“ je
i samostatná úroveň; spuštění jde přes `EmbeddedTerminalWidget.run_command`). Nalezený soubor otevře
`PanelWidget.reveal(path)` (kurzor na souboru).
Prefixy na kořenové úrovni (`parse_mode`): mezera = jen příkazy, `c ` historie terminálu (výběr příkaz jen předvyplní
do řádky přes `EmbeddedTerminalWidget.prefill`), `dc ` mazání z historie (položky s `keep_open=True`: `_run_current` je
spustí, znovu naplní seznam a paletu nezavře; `DatabaseManager.delete_command_history` maže fyzicky: `secure_delete`, `wal_checkpoint(TRUNCATE)`, `VACUUM`, test to hlídá), `a ` soubory i složky, `f ` soubory, `d ` složky. Dotaz interpretuje
`index/pattern.py`: slova (podřetězce), maska `*?` (fnmatch), nebo regex (má-li regex metaznaky); pro index se
z masky/regexu vytáhnou literální běhy ≥3 znaků na trigram MATCH a zbytek ověří SQLite funkce REGEXP.

## Pravidla vzhledu (viz solarqt MANUAL.md)

- Žádný hex mimo `src/solarqt/theme.py`; ptej se `theme.current()`, `theme.semantic_style(kind)`, `theme.status_style()`.
- Každá velikost v pixelech/bodech přes `theme.px()` / `theme.pt()` (i v `setContentsMargins`, `setMinimumWidth`),
  jinak se prvek nezoomuje. Widget, který si drží barvu/velikost/písmo mimo QSS, má `retheme()`.
- Ikony jen kreslené (`icons.icon("name")`), ne emoji ani unicode šipky; nová ikona = záznam v `icons._PATHS`
  (24×24, tah bez barvy). Ikona bez textu má tooltip.
- Barva nese význam: modrá info, žlutá varování, červená chyba/destruktivní, zelená úspěch, oranžová akcent/výběr.
  Destruktivní tlačítko = `DangerButton` / property `danger`, nikdy `:default`.
- Nový widget s pevnou šířkou v hlavičce/panelu ověř v úzkém okně (`MIN_WINDOW_WIDTH` 350 px): dlouhé popisky
  mají `QSizePolicy.Ignored` vodorovně.

Drag & drop: `FileTableView.startDrag` táhne označené položky jako URL souborů (Explorer je bere), `dropEvent` přijme URL
z druhého panelu i z cizích aplikací → signál `files_dropped(paths, dest, move)` → panel → `MainWindow._transfer`
(společné s Ctrl+V: stejná složka = „ - Kopie“, přesun na sebe se přeskočí); cíl = složka pod myší (i `..`) nebo
`drop_root` tabulky; Shift = přesun; přesun provedený Explorerem obnoví zdrojový panel (`external_move_done`).

Drag z klávesnice (Ctrl+.): `gui/keyboard_drag.py` jde **mimo Qt** přes pywin32 `pythoncom.DoDragDrop` s vlastním
`IDropSource` (Qt zdroj ukončí drag bez stisknutého tlačítka). Pasti, které stály hodiny: (1) Windows při Alt+Tab
pustí mouse capture a OLE drag zruší, pokud není fyzicky stisknuté tlačítko – proto se levé tlačítko drží přes
`SendInput` po celou dobu (stisk i uvolnění nad naším stavovým řádkem, `client_bottom_center`, nikdy nad rámem okna =
resize smyčka); (2) Qt musí ten stisk zpracovat **před** `DoDragDrop`, jinak jeho SetCapture uvnitř smyčky sebere
capture OLE = cancel; (3) smyčka OLE se budí jen vstupem – hlídací vlákno každých 80 ms vkládá nulový pohyb myši
(`PostThreadMessage` OLE bere jako ztrátu capture = cancel); (4) Qt časovače uvnitř smyčky OLE neběží, proto vlákno.
Vlákno drží i low-level keyboard hook (Enter = drop, Esc = cancel, šipky = kurzor o 1/10 monitoru, klávesy se
spolknou, aby je nedostala aplikace vpředu; ctypes musí mít `restype` HMODULE/HHOOK, jinak se 64bit handle usekne a
`SetWindowsHookExW` tiše selže) a při změně foreground okna posune kurzor do jeho středu. Po dobu dragu jsou systémové
kurzory nahrazeny velkým vlastním (`SetSystemCursor`, obnova `SPI_SETCURSORS`). Data = shellový IDataObject
(`SHCreateShellItemArrayFromIDLists` → `BHID_DataObject`); `DoDragDrop` v pywin32 vrací jen DROPEFFECT. Souřadnice pro
`SendInput` jsou fyzické pixely. Ověřuje se skriptem s pomocným procesem jako cílem a vláknem, které vkládá vstup.

Schránka se soubory: `FileTableView` Ctrl+C/X/V → signál `clipboard_requested` → panel → `MainWindow._clip_copy` /
`_clip_paste`; `gui/file_clipboard.py` dává na systémovou schránku URL souborů + Explorerový „Preferred DropEffect“
(copy/move), takže funguje i mezi UC a Explorerem; vložení do téže složky pojmenuje `core/naming.copy_target`
(„název - Kopie.ext“, `AppConfig.copy_suffix`) a jde jako COPY job s plnou cílovou cestou (`_copy_one` bere
neexistující cíl jako jméno souboru).

Konvence: Qt widgety komunikují signály, ne přímými voláními do rodiče; stav, který má přežít restart, patří do
`DatabaseManager`, ne do souborů; `FileEntry.full_path` je jediný zdroj cesty položky.
