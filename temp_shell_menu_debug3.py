from pathlib import Path
import pythoncom
from win32com.shell import shell

path = Path.home() / '.gitconfig'
if not path.exists():
    path = Path.home()
pythoncom.CoInitialize()
try:
    result = shell.SHParseDisplayName(str(path), None, 0)
    print('shparse', result)
    print('type', type(result))
    if isinstance(result, tuple):
        print('len', len(result))
        for i, item in enumerate(result):
            print('item', i, type(item), repr(item)[:120])
finally:
    pythoncom.CoUninitialize()
