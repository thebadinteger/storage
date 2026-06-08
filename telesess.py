import re
import socks
import random
from telethon.sync import TelegramClient
from telethon.errors import SessionPasswordNeededError

APP_ID = 2040 
APP_HASH = "b18441a1ff607e10a989891a5462e627"
APP_VERSION = "6.7.6"

LAPTOP_MODELS = [
    "Dell XPS 13", "Dell Latitude 5420", "HP Spectre x360", "HP Pavilion 15",
    "Lenovo ThinkPad X1 Carbon", "Lenovo IdeaPad 3", "ASUS ZenBook 14",
    "ASUS ROG Strix", "MSI Prestige 14", "Acer Swift 3", "Microsoft Surface Laptop 4"
]

WINDOWS_VERSIONS = [
    "Windows 10 (Build 19045)",
    "Windows 10 (Build 19044)",
    "Windows 11 (Build 22000)",
    "Windows 11 (Build 22621)",
    "Windows 11 (Build 22631)"
]

def parse_proxy(proxy_str):
    if not proxy_str:
        return None
    
    pattern = r'(?:(?P<proto>\w+)://)?(?:(?P<user>[^:]+):(?P<password>[^@]+)@)?(?P<ip>[^:]+):(?P<port>\d+)'
    match = re.match(pattern, proxy_str)
    
    if not match:
        return None
    
    p = match.groupdict()
    proxy_type = socks.SOCKS5
    if p['proto']:
        proto = p['proto'].lower()
        if 'http' in proto: proxy_type = socks.HTTP
        elif 'socks4' in proto: proxy_type = socks.SOCKS4

    return {
        'proxy_type': proxy_type,
        'addr': p['ip'],
        'port': int(p['port']),
        'username': p['user'],
        'password': p['password'],
        'rdns': True
    }

def main():
    proxy_input = input("proxy > ").strip()
    session_name = input("name > ").strip()
    phone = input("phone > ").strip()

    proxy = parse_proxy(proxy_input)

    chosen_device = random.choice(LAPTOP_MODELS)
    chosen_os = random.choice(WINDOWS_VERSIONS)

    client = TelegramClient(
        session_name, 
        APP_ID, 
        APP_HASH,
        proxy=proxy,
        device_model=chosen_device,
        system_version=chosen_os,
        app_version=APP_VERSION
    )

    try:
        client.connect()

        if not client.is_user_authorized():
            client.send_code_request(phone)
            code = input("code > ").strip()
            try:
                client.sign_in(phone, code)
            except SessionPasswordNeededError:
                password = input("password > ").strip()
                client.sign_in(password=password)
        
        print(f"[+] done!")
        
    except Exception as e:
        print(f"[-] error: {e}")
    finally:
        client.disconnect()

if __name__ == "__main__":
    main()
