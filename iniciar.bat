@echo off
echo Iniciando Asistente Regulatorio...
echo.
cd /d "%~dp0"
pip install -r requirements.txt -q
echo.
echo Abriendo aplicacion...
streamlit run app.py --server.headless false --browser.gatherUsageStats false
pause
