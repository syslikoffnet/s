# language: Python, file: syntax.py, target: Python 3.10+
import asyncio
import time
import random
from datetime import datetime
from collections import deque
from telethon import TelegramClient, events
from telethon.tl.types import User, Chat, Channel
from rich.console import Console, Group
from rich.panel import Panel
from rich.text import Text
from rich.align import Align
from rich.theme import Theme
from rich.live import Live
from rich.layout import Layout
from rich.box import HEAVY, ROUNDED

# --- НАСТРОЙКИ ---
API_ID = АПИ АЙДИ
API_HASH = "АПИ ХАШ"
SESSION_NAME = "syntax_session"

syntax_theme = Theme({
    "msg_text": "#f8f8f2",
    "msg_time": "bold #50fa7b",
    "chat_tag": "bold black on #8be9fd",
})
console = Console(theme=syntax_theme)
messages_buffer = deque(maxlen=30)

def get_time():
    return datetime.now().strftime("%H:%M:%S")

def get_header():
    return Panel(
        Align.center(Text("SYNTAX | @KENPHOBIC", style="bold #ff79c6")),
        style="on #282a36", box=ROUNDED, height=3
    )

def get_footer():
    return Panel(
        Align.center(Text("SYNTAX GLOBAL SNIFFER | Ctrl+C для выхода", style="dim #8be9fd")),
        style="on #282a36", box=ROUNDED, height=3
    )

def get_messages_panel():
    if not messages_buffer:
        return Panel(Align.center(Text("Ожидание сообщений...", style="dim #6272a4")),
                     title="[dim]GLOBAL FEED[/dim]", title_align="left",
                     border_style="dim #bd93f9", box=HEAVY, padding=(0, 1))
    return Panel(Group(*messages_buffer), title="[dim]GLOBAL FEED[/dim]", title_align="left",
                 border_style="dim #bd93f9", box=HEAVY, padding=(0, 1))

def make_message_card(chat_name: str, sender_name: str, username: str, text: str, is_self: bool = False, is_media: bool = False):
    header = Text(" ВЫ ", style="bold black on #50fa7b") if is_self else Text(f" {sender_name.upper()} ", style="bold black on #bd93f9")
    if username:
        header.append(f" @{username} ", style="dim #f8f8f2 on #44475a")
    
    short_chat = chat_name[:15] + "..." if len(chat_name) > 15 else chat_name
    header.append(f" [ {short_chat} ] ", style="chat_tag")
    header.append(f" {get_time()} ", style="bold black on #8be9fd")

    safe_text = text.replace("[", r"\[").replace("]", r"\]")
    if len(safe_text) > 200:
        safe_text = safe_text[:197] + "..."

    content = Text(f"📎 {safe_text}", style="italic #f1fa8c") if is_media else Text(safe_text, style="msg_text")
    
    # Явно указываем HEX цвета, чтобы rich не ругался
    border_color = "#50fa7b" if is_self else "#bd93f9"
    
    return Panel(content, title=header, title_align="left",
                 border_style=border_color,
                 box=HEAVY, padding=(0, 1), expand=False)

def make_layout():
    layout = Layout()
    layout.split_column(Layout(name="header", size=3), Layout(name="main", ratio=1), Layout(name="footer", size=3))
    return layout

async def main():
    client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
    layout = make_layout()
    layout["header"].update(get_header())
    layout["footer"].update(get_footer())
    layout["main"].update(Align.center(Text("Инициализация...", style="bold #ff79c6")))

    # Флаг для рендера
    render_needed = True

    with Live(layout, screen=True, refresh_per_second=10) as live:
        
        # 1. Матрица
        start = time.time()
        while time.time() - start < 1.5:
            lines = [Text("".join(random.choice("01") for _ in range(50)), style="dim #50fa7b") for _ in range(4)]
            layout["main"].update(Align.center(Group(*lines)))
            await asyncio.sleep(0.05)

        # 2. Подключение
        layout["main"].update(Align.center(Text("Подключение к Telegram...", style="bold #8be9fd")))
        try:
            await client.start()
            me = await client.get_me()
        except Exception as e:
            live.stop()
            console.print(f"[bold red]Ошибка авторизации: {e}[/bold red]")
            return

        # 3. Начало
        messages_buffer.append(Text(f" [+] Авторизация: {me.first_name} (@{me.username})", style="bold #50fa7b"))
        messages_buffer.append(Text(f" [+] Режим: Глобальный перехват", style="bold #ff79c6"))
        messages_buffer.append(Text(f" [+] Слушаю все чаты...\n", style="bold #8be9fd"))
        layout["main"].update(get_messages_panel())

        # 4. Обработчик
        @client.on(events.NewMessage)
        async def handler(event):
            nonlocal render_needed
            try:
                sender = await event.get_sender()
                chat = await event.get_chat()
                
                sender_name = "Unknown"
                username = ""
                if isinstance(sender, User):
                    sender_name = sender.first_name or "User"
                    username = sender.username or ""
                elif isinstance(sender, (Chat, Channel)):
                    sender_name = sender.title or "Chat"
                    
                chat_name = getattr(chat, "title", None) or getattr(chat, "first_name", None) or "ЛС"
                is_self = sender.id == me.id if sender else False
                
                text = event.text or ""
                is_media = False
                if not text:
                    if event.media:
                        is_media = True
                        text = f"[ МЕДИА: {type(event.media).__name__} ]"
                    else:
                        text = "[ ПУСТОЕ СООБЩЕНИЕ ]"

                messages_buffer.append(make_message_card(chat_name, sender_name, username, text, is_self, is_media))
                render_needed = True
            except Exception:
                pass

        # 5. Запускаем клиент
        client_task = asyncio.create_task(client.run_until_disconnected())

        # 6. Цикл интерфейса
        while not client_task.done():
            if render_needed:
                layout["main"].update(get_messages_panel())
                live.refresh()
                render_needed = False
            await asyncio.sleep(0.1)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[!] Сессия завершена.")