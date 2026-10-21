@echo off
chcp 65001 >nul
cd /d "%~dp0\.."
if not exist .venv (
  echo 正在创建虚拟环境...
  python -m venv .venv || goto :err
)
call .venv\Scripts\activate.bat
python -c "import fastapi, uvicorn, multipart" 2>nul || (
  echo 正在安装依赖...
  pip install -r requirements.txt || goto :err
)
python run.py --load-samples %*
goto :eof
:err
echo 启动失败，请确认已安装 Python 3.10+ 且可在命令行使用 python。
pause
