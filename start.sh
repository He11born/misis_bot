#!/bin/bash

# Запуск Uvicorn-сервера, который будет слушать на порте 10000 
# (это стандартный порт на Render). 
# Он запускает FastAPI-приложение (fastapi_app), 
# которое находится в файле app.py.

echo "Starting Uvicorn server on port 10000..."
uvicorn app:fastapi_app --host 0.0.0.0 --port 10000
