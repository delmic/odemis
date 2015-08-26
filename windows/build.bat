cd /d %~dp0
pyinstaller -y viewer.spec
"C:\Program Files (x86)\NSIS\makensis" setup.nsi
pause
