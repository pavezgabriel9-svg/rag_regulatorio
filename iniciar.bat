@echo off
echo Iniciando Asistente Regulatorio...
echo.
cd /d "%~dp0"
pip install --prefer-binary -r requirements.txt -q
if %errorlevel% neq 0 (
    echo.
    echo ERROR: Fallo la instalacion de dependencias.
    echo Para resolver el error de chroma-hnswlib, instala Microsoft C++ Build Tools:
    echo https://visualstudio.microsoft.com/visual-cpp-build-tools/
    echo Selecciona "Desarrollo de escritorio con C++" durante la instalacion.
    pause
    exit /b 1
)
echo.
echo Abriendo aplicacion...
streamlit run app.py --server.headless false --browser.gatherUsageStats false
pause
