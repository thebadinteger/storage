### 13.04.2026 aisc.py  
Python AI Studio .json cleaner  
Usage:  
```shell
python aisc.py -h
```  

### 01.05.2026 tgalert.py  
Python script for obtaining a badge ("Darwin Award")  
`%s uses an unofficial Telegram client — messages to this user may be less secure.`  
Usage:  
```shell
pip install telethon
python tgalert.py
```  
You can remove the badge by ending the script session in Settings > Privacy and Security > Active sessions  

### 23.04.2026 telesess.py  
Python script for creating a Telethon .session file on TDesktop API Keys  
(Supports SOCKS5 Proxies)  
Usage:  
```shell
pip install telethon PySocks
python telesess.py
```

### 10.03.2026 naxnine/  
Python Control Script for P2P NaxClow Camera (A9 clone)  
Features:  
- View video
- Listen to audio
- Flip image
- Turn IR light on/off
- Settings (most options; I didn't include some)
- Web interface  
You need a QR code from the mobile app to log into camera  
Usage:  
```shell
pip install Flask requests pyzbar Pillow
python naxnine.py
```
