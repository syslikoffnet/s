"""Healdragg — каталог публичных групп Telegram.

Пакет собран из независимых слоёв:

* :mod:`healdragg.db`     — хранилище (SQLite, WAL, нормализованная схема);
* :mod:`healdragg.tg`     — пул Telethon-сессий, троттлинг, разрешение сущностей;
* :mod:`healdragg.scan`   — сбор каталога и проверка участия цели;
* :mod:`healdragg.textfmt`— рендер списков в «телеграмном» стиле;
* :mod:`healdragg.bot`    — aiogram-бот @healdragg_bot;
* :mod:`healdragg.tui`    — живая панель в терминале;
* :mod:`healdragg.api`    — REST + веб-панель для Android-приложения.
"""

__version__ = "1.4.0"
__all__ = ["__version__"]
