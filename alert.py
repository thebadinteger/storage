# @thebadinteger

import asyncio
from telethon import TelegramClient

async def main():
    client = TelegramClient(
        None,
        21882615,
        "a55678cc05c1aad2fb0aaccbf9663241",
        device_model="Z Phone",
        system_version="Edinaya Rossia OS"
    )
    await client.start()
    await client.get_me()
    await client.disconnect()
    input("Теперь ты опущеный!\nENTER для выхода.")

asyncio.run(main())