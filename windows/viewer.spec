# -*- mode: python -*-
a = Analysis(['viewer.py'],
             pathex=['D:\\Development\\Odemis\\windows'],
             hiddenimports=[
                 'cairo',
             ],
             hookspath=None,
             runtime_hooks=None)
pyz = PYZ(a.pure)
exe = EXE(pyz,
          a.scripts,
          exclude_binaries=True,
          name='OdemisViewer.exe',
          debug=False,
          strip=None,
          upx=False,
          console=False
       )
coll = COLLECT(exe,
               a.binaries,
               a.zipfiles,
               a.datas,
               strip=None,
               upx=False,
               name='OdemisViewer'
       )
