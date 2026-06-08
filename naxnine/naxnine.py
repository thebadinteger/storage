import os
import sys
import json
import logging
import argparse
import requests
import hashlib
import struct
import threading
import time
import queue
import socket
from typing import Dict, Any, Optional, Tuple

from flask import Flask, jsonify, request, send_from_directory, render_template, Response
try:
    from pyzbar.pyzbar import decode
    from PIL import Image
    HAVE_PYZBAR = True
except ImportError:
    HAVE_PYZBAR = False

# Configuration
CONFIG_FILE = "data.json"
API_BASE = "https://v720.naxclow.com/app/api"

def generate_random_credentials():
    phone = hashlib.md5(os.urandom(16)).hexdigest()
    pwd = hashlib.md5(os.urandom(16)).hexdigest()[:12]
    return phone, pwd

def register_new_account() -> Dict[str, Any]:
    phone, pwd = generate_random_credentials()
    payload = {
        "phone": phone,
        "pwd": pwd,
        "app": "v720"
    }
    headers = {
        "user-agent": "Mozilla/5.0 (Linux; Android 15; wv) Html5Plus/1.0",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    
    resp = requests.post(f"{API_BASE}/ApiAppUser/register", data=payload, headers=headers)
    resp.raise_for_status()
    data = resp.json()
    
    if data.get("code") != 200:
        raise Exception(f"Registration failed: {data}")
        
    user_data = data.get("data", {})
    return {
        "token": user_data.get("token"),
        "userId": user_data.get("userId"),
        "account": phone
    }

def load_or_create_config() -> Dict[str, Any]:
    default_config = {"user": {}, "cameras": {}}
    
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
                if "user" in config and config["user"].get("token"):
                    return config
        except Exception as e:
            logging.error(f"Error reading {CONFIG_FILE}, creating new: {e}")
            
    # Need to register new account
    logging.info("No valid account found in data.json. Registering new anonymous account...")
    try:
        user_info = register_new_account()
        config = {
            "user": user_info,
            "cameras": {}
        }
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2)
        logging.info("Successfully registered and saved to data.json")
        return config
    except Exception as e:
        logging.error(f"Failed to auto-register: {e}")
        return default_config

def save_config(config: Dict[str, Any]):
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2)

# Set up CLI args
parser = argparse.ArgumentParser(description="Naxnine Universal Web App")
parser.add_argument("-d", "--debug", action="store_true", help="Enable debug logging")
args = parser.parse_args()

# Setup Logging
log_level = logging.DEBUG if args.debug else logging.WARNING
logging.basicConfig(level=log_level, format="%(message)s")

if not args.debug:
    # Silence Flask/Werkzeug
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    os.environ['WERKZEUG_RUN_MAIN'] = 'true'

# Initialize Config
app_config = load_or_create_config()

# Setup Flask Server
# Serve static files from 'web' directory relative to this script
web_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web')
app = Flask(__name__, static_folder=web_dir, template_folder=web_dir)

# --- P2P Connection Globals ---
active_p2p: Optional['P2PConnection'] = None
active_device_code: Optional[str] = None
# ------------------------------

@app.route('/')
def index():
    return send_from_directory(web_dir, 'index.html')

@app.route('/<path:filename>')
def serve_static(filename):
    return send_from_directory(web_dir, filename)

@app.route('/api/config', methods=['GET'])
def get_config():
    return jsonify(app_config)

@app.route('/api/bind', methods=['POST'])
def bind_camera():
    global app_config
    token = app_config.get("user", {}).get("token")
    if not token:
        return jsonify({"error": "Not authenticated"}), 401
    
    data = request.json or {}
    share_code = data.get("shareCode")
    if not share_code:
        return jsonify({"error": "Missing shareCode"}), 400
        
    url = f"{API_BASE}/ApiSysDevices/shareDevices"
    payload = {"shareCode": share_code}
    headers = {
        "user-agent": "Mozilla/5.0 (Linux; Android 15; wv) Html5Plus/1.0",
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": token
    }
    try:
        resp = requests.post(url, headers=headers, data=payload, timeout=10)
        res_data = resp.json()
        if res_data.get("code") == 200:
            dev_code = share_code[:12]
            app_config["cameras"][dev_code] = {
                "shareCode": share_code,
                "lastBind": time.time(),
                "deviceName": f"Camera {dev_code}",
                "state": "1",
                "type": "monitor",
                "batch": "A9AlertA27"
            }
            save_config(app_config)
            return jsonify({"success": True, "deviceCode": dev_code})
        return jsonify({"error": res_data.get("message", "Bind failed")}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/bind_qr', methods=['POST'])
def bind_qr_camera():
    if not HAVE_PYZBAR:
        return jsonify({"error": "pyzbar library not installed on server"}), 501
    
    if 'qr_image' not in request.files:
        return jsonify({"error": "No image uploaded"}), 400
        
    file = request.files['qr_image']
    try:
        img = Image.open(file.stream)
        decoded_objects = decode(img)
        if not decoded_objects:
            return jsonify({"error": "No QR code detected in image"}), 400
            
        share_code = decoded_objects[0].data.decode('utf-8')
        
        # Now bind using the share_code we just decoded
        global app_config
        token = app_config.get("user", {}).get("token")
        if not token:
            return jsonify({"error": "Not authenticated"}), 401
            
        url = f"{API_BASE}/ApiSysDevices/shareDevices"
        payload = {"shareCode": share_code}
        headers = {
            "user-agent": "Mozilla/5.0 (Linux; Android 15; wv) Html5Plus/1.0",
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": token
        }
        
        resp = requests.post(url, headers=headers, data=payload, timeout=10)
        res_data = resp.json()
        if res_data.get("code") == 200:
            dev_code = share_code[:12]
            app_config["cameras"][dev_code] = {
                "shareCode": share_code,
                "lastBind": time.time(),
                "deviceName": f"Camera {dev_code}",
                "state": "1",
                "type": "monitor",
                "batch": "A9AlertA27"
            }
            save_config(app_config)
            return jsonify({"success": True, "deviceCode": dev_code})
        return jsonify({"error": res_data.get("message", "Bind failed")}), 400

    except Exception as e:
        return jsonify({"error": f"Failed to process image: {str(e)}"}), 500

@app.route('/api/connect/<dev_code>', methods=['POST'])
def connect_camera(dev_code):
    global active_p2p, active_device_code, app_config
    if dev_code not in app_config.get("cameras", {}):
        return jsonify({"error": "Camera not found in config"}), 404
        
    if active_p2p and active_device_code == dev_code and active_p2p.running:
        return jsonify({"success": True, "message": "Already connected"})
        
    token = app_config.get("user", {}).get("token")
    if not token:
        return jsonify({"error": "Not authenticated"}), 401

    if active_p2p:
        active_p2p.running = False
        time.sleep(0.5)
        active_p2p = None

    url = f"{API_BASE}/ApiServer/getA9VideoConf"
    headers = {
        "user-agent": "Mozilla/5.0 (Linux; Android 15; wv) Html5Plus/1.0",
        "Authorization": token
    }
    try:
        conf_resp = requests.get(url, headers=headers, params={"devicesCode": dev_code}, timeout=10)
        conf_data = conf_resp.json()
        if conf_data.get("code") != 200:
            return jsonify({"error": "Failed to get P2P config"}), 400
            
        conf = conf_data.get("data", {})
        host = conf.get('host', '')
        port = conf.get('tcpPort', 29940)
        uid = conf.get('uid', dev_code)
        tar_pwd = conf.get('tarPwd', '')

        # Also signal watching to ensure stream isn't blocked by cloud
        watch_url = f"{API_BASE}/ApiServer/watching"
        form_data = {"devicesCode": dev_code, "pwd": ""}
        watch_headers = headers.copy()
        watch_headers["Content-Type"] = "application/x-www-form-urlencoded"
        try:
            requests.post(watch_url, headers=watch_headers, data=form_data, timeout=20)
        except Exception as watch_e:
            print(f"[-] Warning: watching signal failed: {watch_e}")

        active_p2p = P2PConnection(host=host, port=port, device_code=dev_code, uid=uid, tar_pwd=tar_pwd)
        if active_p2p.connect():
            active_device_code = dev_code
            return jsonify({"success": True})
        else:
            active_p2p = None
            return jsonify({"error": "Failed to establish P2P connection"}), 502
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route('/api/stream/<dev_code>')
def stream_video(dev_code):
    if not active_p2p or active_device_code != dev_code or not active_p2p.running:
        return Response("Not connected", status=400)
        
    def generate():
        while active_p2p and active_p2p.running:
            frame = active_p2p.get_latest_frame()
            if frame:
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
            time.sleep(0.01)
    return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/api/audio/<dev_code>')
def stream_audio(dev_code):
    if not active_p2p or active_device_code != dev_code or not active_p2p.running:
        return Response(b'', status=204)
    pcm_data = active_p2p.get_audio_chunk()
    if pcm_data:
        return Response(pcm_data, mimetype='application/octet-stream')
    return Response(b'', status=204)

@app.route('/api/audio_reset/<dev_code>')
def audio_reset(dev_code):
    if active_p2p and active_device_code == dev_code:
        active_p2p.flush_audio()
    return Response('ok', status=200)

@app.route('/api/flip/<dev_code>', methods=['POST'])
def flip_video(dev_code):
    if not active_p2p or active_device_code != dev_code:
        return jsonify({'error': 'Not connected'}), 400
    new_state = active_p2p.toggle_mirror_flip()
    return jsonify({'flipped': new_state == 4})

def _send_mqtt_setting(dev_code, payload):
    token = app_config.get("user", {}).get("token")
    if not token:
        raise Exception("Not authenticated")
    url = f"{API_BASE}/ApiMqtt/sendSetting"
    headers = {
        "user-agent": "Mozilla/5.0 (Linux; Android 15; wv) Html5Plus/1.0",
        "Authorization": token,
        "Content-Type": "application/x-www-form-urlencoded"
    }
    data = {"devicesCode": dev_code, "json": json.dumps(payload)}
    resp = requests.post(url, headers=headers, data=data, timeout=10)
    return resp.json()

@app.route('/api/share/<dev_code>')
def get_share_code(dev_code):
    admin = request.args.get("admin", "0")
    token = app_config.get("user", {}).get("token")
    if not token:
        return jsonify({"error": "Not authenticated"}), 401
    url = f"{API_BASE}/ApiSysDevices/getShareCode"
    headers = {
        "user-agent": "Mozilla/5.0",
        "Authorization": token
    }
    resp = requests.get(url, headers=headers, params={"devicesId": dev_code, "isMdf": admin}, timeout=10)
    return jsonify(resp.json())

@app.route('/api/settings/<dev_code>', methods=['POST'])
def update_settings(dev_code):
    try:
        data = request.json or {}
        action = data.get("action")
        value = data.get("value")
        
        token = app_config.get("user", {}).get("token")
        if not token:
            return jsonify({"error": "Not authenticated"}), 401

        if action == "rename":
            url = f"{API_BASE}/ApiSysDevices/reNameDevice"
            headers = {"Authorization": token, "Content-Type": "application/x-www-form-urlencoded"}
            resp = requests.post(url, headers=headers, data={"devicesCode": dev_code, "newName": value}, timeout=10)
            return jsonify(resp.json())
            
        elif action == "network_indicator":
            return jsonify(_send_mqtt_setting(dev_code, {"code": 210, "instLed": int(str(value))}))
            
        elif action == "ir_light":
            return jsonify(_send_mqtt_setting(dev_code, {"code": 202, "IrLed": int(str(value))}))
            
        elif action == "reboot":
            return jsonify(_send_mqtt_setting(dev_code, {"code": 299, "reboot": 1}))
            
        elif action == "motion_detection":
            return jsonify(_send_mqtt_setting(dev_code, {"code": 213, "pirAlert": int(str(value))}))
            
        elif action == "motion_interval":
            return jsonify(_send_mqtt_setting(dev_code, {"code": 206, "moveGrade": int(str(value))}))
            
        elif action == "sensitivity":
            return jsonify(_send_mqtt_setting(dev_code, {"code": 215, "pirGrade": int(str(value))}))
            
        elif action == "format_sd":
            return jsonify(_send_mqtt_setting(dev_code, {"code": 207}))
            
        return jsonify({"error": "Unknown action"}), 400
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route('/api/camera_info/<dev_code>')
def get_camera_info(dev_code):
    token = app_config.get("user", {}).get("token")
    if not token:
        return jsonify({"error": "Not authenticated"}), 401
    url = f"{API_BASE}/ApiSysDevices/getDeviceByCode"
    headers = {"Authorization": token}
    try:
        resp = requests.get(url, headers=headers, params={"devicesCode": dev_code}, timeout=10)
        return jsonify(resp.json())
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/delete_camera/<dev_code>', methods=['POST'])
def delete_camera(dev_code):
    token = app_config.get("user", {}).get("token")
    if not token:
        return jsonify({"error": "Not authenticated"}), 401
    url = f"{API_BASE}/ApiSysDevices/delete"
    headers = {"Authorization": token, "Content-Type": "application/x-www-form-urlencoded"}
    try:
        # Get internal device ID first
        info_resp = requests.get(f"{API_BASE}/ApiSysDevices/getDeviceByCode", headers=headers, params={"devicesCode": dev_code}, timeout=10).json()
        if not info_resp.get("data"):
            return jsonify({"error": "Device not found on server"}), 404
        internal_id = info_resp["data"]["id"]
        
        # Delete from server
        del_resp = requests.post(url, headers=headers, data={"id": internal_id}, timeout=10)
        
        # Delete from local config
        if dev_code in app_config.get("cameras", {}):
            del app_config["cameras"][dev_code]
            save_config(app_config)
            
        return jsonify(del_resp.json())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/thumbnail/<dev_code>')
def get_thumbnail(dev_code):
    return jsonify({"error": "Not implemented"}), 404

@app.route('/api/alarms/<dev_code>')
def get_alarms(dev_code):
    return jsonify({"error": "Not implemented"}), 404

class NaxclowHeader:
    """
    20-byte Naxclow protocol header.
    Format from prot_udp.py: <LHBB8sI
    Order: PayloadLen(4), Cmd(2), MsgFlag(1), DealFlag(1), ForwardId(8), PkgId(4)
    """
    SIZE = 20
    FMT = '<LHBB8sI'
    DEFAULT_FWD_ID = b'00000000'

    def __init__(self, payload_len=0, cmd=0, msg_flag=0, deal_flag=0, forward_id=b'00000000', pkg_id=0):
        self.payload_len = payload_len
        self.cmd = cmd
        self.msg_flag = msg_flag
        self.deal_flag = deal_flag
        self.forward_id = forward_id if isinstance(forward_id, bytes) else forward_id.encode('ascii')[:8].ljust(8, b'\x00')
        self.pkg_id = pkg_id

    def pack(self) -> bytes:
        return struct.pack(self.FMT,
                           self.payload_len, self.cmd, self.msg_flag,
                           self.deal_flag, self.forward_id, self.pkg_id)

    @classmethod
    def unpack(cls, data: bytes):
        if len(data) < cls.SIZE:
            return None
        p = struct.unpack(cls.FMT, data[:int(cls.SIZE)])
        return cls(payload_len=p[0], cmd=p[1], msg_flag=p[2],
                   deal_flag=p[3], forward_id=p[4], pkg_id=p[5])

    def __repr__(self):
        return f'HDR(len={self.payload_len}, cmd={self.cmd}, flag={self.msg_flag}, deal={self.deal_flag}, fwd={self.forward_id}, pkg={self.pkg_id})'


class V720Client:
    API_BASE = "https://v720.naxclow.com/app/api"
    DEFAULT_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 15; LM-V409N Build/BP1A.250505.005; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/145.0.7632.120 Mobile Safari/537.36 uni-app Html5Plus/1.0 (Immersed/24.0)",
        "Content-Type": "application/json",
        "Accept-Encoding": "gzip"
    }

    def __init__(self, account: Optional[str] = None, password: str = "123456", token_file: str = "token.json", devices_file: str = "bound_devices.json"):
        self.token_file = token_file
        self.devices_file = devices_file
        self.account = account
        self.password = password
        self.token = None
        self.user_id = None
        self.devices = []
        self.bound_devices = {}
        
        self._load_token()
        if not self.account:
            self.account = self.generate_guest_account()
            
def _build_alaw_decode_table():
    """Build A-law to 16-bit linear PCM lookup table (256 entries)."""
    table = [0] * 256
    for i in range(256):
        ix = i ^ 0x55  # XOR with 0x55
        ix &= 0x7F  # strip sign for now
        
        quant = (ix & 0x0F) << 4
        seg = (ix & 0x70) >> 4
        
        if seg == 0:
            val = quant + 8
        elif seg == 1:
            val = quant + 0x108
        else:
            val = (quant + 0x108) << (seg - 1)
        
        if i & 0x80:  # sign bit in original byte (after XOR, bit 7 of ix)
            table[i] = val
        else:
            table[i] = -val
    return table

ALAW_DECODE_TABLE = _build_alaw_decode_table()

def decode_g711a(data: bytes) -> bytes:
    """Decode G.711 A-law bytes to 16-bit signed LE PCM."""
    pcm = bytearray(len(data) * 2)
    for i, b in enumerate(data):
        sample = ALAW_DECODE_TABLE[b]
        # Pack as signed 16-bit little-endian
        pcm[i*2] = sample & 0xFF
        pcm[i*2 + 1] = (sample >> 8) & 0xFF
    return bytes(pcm)
class P2PConnection:
    """Naxclow Cloud Relay hybrid client.
    
    RELAY protocol uses prot_udp header (TCP & UDP): <LHBB8sI (20 bytes)
      PayloadLen(4), Cmd(2), MsgFlag(1), DealFlag(1), ForwardId(8), PkgId(4)
    
    Handshake requires TCP signaling (port 29942) and UDP holepunching/tunneling.
    Video frames are exclusively sent over the UDP socket.
    """
    # Relay Cmd IDs
    CMD_JSON = 0
    CMD_JPEG = 1
    CMD_AAC = 3
    CMD_G711 = 4
    CMD_RAW = 5
    CMD_PCM = 6
    CMD_PING = 9
    CMD_HEARTBEAT = 100
    CMD_RETRANSMISSION_CONFIRM = 605

    # Relay JSON codes
    CODE_REGISTER = 100
    CODE_REQ_UDP_INFO = 20
    CODE_REQ_CONNECT = 10
    CODE_REQ_FORWARD_ID = 30
    CODE_HOLE_PUNCH = 50
    CODE_DEV_STATUS = 52
    CODE_UDP_KEEPALIVE = 54
    CODE_FORWARD = 301

    # Forward Content codes
    PP_BASE_INFO = 4
    PP_AV_SWITCH = 3
    PP_MIRROR_FLIP = 216

    # MJPEG fragmentation flags
    FLAG_HEAD = 250
    FLAG_BODY = 251
    FLAG_END = 252
    FLAG_FINISH = 255

    HDR_FORMAT = "<LHBB8sI"
    HDR_SIZE = 20

    def __init__(self, host: str, port: int, device_code: str,
                 forward_id: str = "00000000", uid: str = "", tar_pwd: str = ""):
        self.host, self.port, self.device_code = host, port, device_code
        self.initial_forward_id = forward_id.encode('ascii')[:8].ljust(8, b'\x00') if isinstance(forward_id, str) else forward_id
        self.tcp_forward_id = self.initial_forward_id
        self.udp_forward_id = self.initial_forward_id
        self.uid, self.tar_pwd = uid, tar_pwd
        
        self.tcp_sock = None
        self.udp_sock = None
        self.running = False
        
        self.tcp_pkg_counter = 0
        self.udp_pkg_counter = 0
        
        # State machine events
        self.tcp_connected_event = threading.Event()
        self.udp_info_received = threading.Event()
        self.connect_req_approved = threading.Event()
        self.forward_id_received = threading.Event()
        self.udp_tunnel_established = threading.Event()
        self.device_online_event = threading.Event()
        
        # NAT details
        self.cli_pub_ip = None
        self.cli_pub_port = None
        self.dev_pub_ip = None
        self.dev_pub_port = None
        self.dev_nat_ip = None
        self.dev_nat_port = None
        self.udp_target = None
        self.mirror_flip = 4  # default from baseInfo, 4=up_down flipped, 1=normal
        
        self.video_queue = queue.Queue(maxsize=10)
        self.audio_queue = queue.Queue(maxsize=30)
        self._g711_buffer = bytearray()  # Buffer to accumulate G711 data before decode
        self._g711_lock = threading.Lock()
        self.received_pkg_ids = set()
        self._pkg_lock = threading.Lock()
        self._lock = threading.Lock()
        self._tcp_send_lock = threading.Lock()
        self._udp_pkg_counter = 0

    def _next_tcp_pkg(self):
        self.tcp_pkg_counter += 1
        return self.tcp_pkg_counter

    def _next_udp_pkg(self):
        self.udp_pkg_counter += 1
        return self.udp_pkg_counter

    def _send_tcp(self, cmd: int, payload: bytes = b'', flag: int = 0, deal: int = 0):
        hdr = struct.pack(self.HDR_FORMAT, len(payload), cmd, flag, deal, self.tcp_forward_id, self._next_tcp_pkg())
        try:
            with self._tcp_send_lock:
                self.tcp_sock.sendall(hdr + payload)
        except Exception as e:
            print(f"[-] TCP Send error: {e}", flush=True)
            self.running = False

    def _send_tcp_json(self, obj: dict):
        payload = json.dumps(obj).encode('utf-8')
        print(f"[TCP->] {obj}", flush=True)
        self._send_tcp(self.CMD_JSON, payload)

    def _send_udp(self, cmd: int, payload: bytes = b'', flag: int = 0, deal: int = 0, target_addr=None, fwd_id=None):
        if not target_addr: target_addr = self.udp_target or (self.host, self.port)
        if not fwd_id: fwd_id = self.udp_forward_id
        hdr = struct.pack(self.HDR_FORMAT, len(payload), cmd, flag, deal, fwd_id, self._next_udp_pkg())
        try:
            self.udp_sock.sendto(hdr + payload, target_addr)
        except Exception as e:
            if self.running: print(f"[-] UDP Send error: {e}", flush=True)

    def _send_udp_json(self, obj: dict, target_addr=None):
        payload = json.dumps(obj).encode('utf-8')
        print(f"[UDP->] {obj} to {target_addr or (self.host, self.port)}", flush=True)
        self._send_udp(self.CMD_JSON, payload, target_addr=target_addr)

    def _recvall_tcp(self, n: int) -> Optional[bytes]:
        data = bytearray()
        while len(data) < n:
            try:
                chunk = self.tcp_sock.recv(n - len(data))
                if not chunk: return None
                data.extend(chunk)
            except socket.timeout:
                if not self.running: return None
                continue
            except:
                return None
        return bytes(data)

    def connect(self):
        try:
            # 1. Start Sockets
            self.tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.tcp_sock.connect((self.host, self.port))
            self.tcp_sock.settimeout(5.0)
            
            self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            
            # Increase UDP receive buffer to 1MB to prevent dropping MJPEG fragments
            try: self.udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024)
            except: pass
            
            self.udp_sock.bind(('', 0)) # Ephemeral port
            self.udp_sock.settimeout(2.0)
            
            self.udp_target = (self.host, self.port) # Initialize with relay address
            
            self.running = True
            threading.Thread(target=self._tcp_receive_loop, daemon=True).start()
            threading.Thread(target=self._udp_receive_loop, daemon=True).start()

            print(f"[*] Step 1: TCP Registration (code=100)...", flush=True)
            self._send_tcp_json({
                "code": self.CODE_REGISTER,
                "uid": self.uid,
                "token": self.tar_pwd,
                "domain": "v720.naxclow.com"
            })
            if not self.tcp_connected_event.wait(5.0): return False

            print(f"[*] Step 2: Request UDP Info (code=20)...", flush=True)
            self._send_udp_json({"code": self.CODE_REQ_UDP_INFO})
            if not self.udp_info_received.wait(5.0): return False

            print(f"[*] Step 3: Request Target Connect (code=10)...", flush=True)
            self._send_tcp_json({
                "code": self.CODE_REQ_CONNECT,
                "cliIp": self.cli_pub_ip,
                "cliPort": self.cli_pub_port,
                "cliNatIp": "192.168.0.101", # Typical Fake NAT
                "cliNatPort": self.udp_sock.getsockname()[1],
                "devTarget": self.device_code,
                "devToken": self.tar_pwd
            })
            if not self.connect_req_approved.wait(10.0): return False

            print(f"[*] Step 4: Direct Hole Punch Attempts (code=50)...", flush=True)
            # Try to holepunch the camera's local/public IP explicitly
            # Note: We also send to Relay just in case
            for _ in range(5):
                out_msg = {
                    "code": self.CODE_HOLE_PUNCH,
                    "cliId": self.uid,
                    "cliToken": self.tar_pwd,
                    "devTarget": self.device_code,
                    "devToken": self.tar_pwd
                }
                
                # Try camera's public network
                if self.dev_pub_ip and self.dev_pub_port:
                    self._send_udp_json(out_msg, target_addr=(self.dev_pub_ip, self.dev_pub_port))
                # Try camera's local network (frequently works if on same wifi)
                if self.dev_nat_ip and self.dev_nat_port:
                    self._send_udp_json(out_msg, target_addr=(self.dev_nat_ip, self.dev_nat_port))
                
                time.sleep(0.1)

            # Wait briefly to see if direct holepunch works
            # If so, the UDP loop will set `udp_tunnel_established` and set self.udp_target.
            if not self.udp_tunnel_established.wait(2.0):
                print(f"[*] Step 5: Direct Holepunch failed. Requesting Relay Forward ID (code=30)...", flush=True)
                self._send_tcp_json({
                    "code": self.CODE_REQ_FORWARD_ID,
                    "cliIp": self.cli_pub_ip,
                    "cliPort": self.cli_pub_port,
                    "devIp": self.dev_pub_ip,
                    "devPort": self.dev_pub_port,
                    "devTarget": self.device_code,
                    "devToken": self.tar_pwd
                })
                if not self.forward_id_received.wait(10.0): return False

                print(f"[*] Step 6: Establish UDP Tunnel to Relay (code=50)...", flush=True)
                self._send_udp_json({
                    "code": self.CODE_HOLE_PUNCH,
                    "cliId": self.uid,
                    "cliToken": self.tar_pwd,
                    "devTarget": self.device_code,
                    "devToken": self.tar_pwd
                }, target_addr=(self.host, self.port))
                
                if not self.udp_tunnel_established.wait(10.0): return False

            print(f"[*] Step 7: Acknowledge Tunnel (code=52)...", flush=True)
            self._send_tcp_json({
                "code": self.CODE_DEV_STATUS,
                "devTarget": self.device_code,
                "devToken": self.tar_pwd,
                "status": 1
            })
            
            # Send the forwarding commands! They occur right away according to frida.
            print("[*] Step 8: Forward Video Activation Commands (301)...", flush=True)
            time.sleep(0.1)
            self._send_tcp_json({
                "code": self.CODE_FORWARD,
                "target": self.device_code,
                "content": {"code": 298}
            })
            self._send_tcp_json({
                "code": self.CODE_FORWARD,
                "target": self.device_code,
                "content": {"code": self.PP_BASE_INFO}
            })
            self._send_tcp_json({
                "code": self.CODE_FORWARD,
                "target": self.device_code,
                "content": {"code": self.PP_AV_SWITCH}
            })

            threading.Thread(target=self._heartbeat_loop, daemon=True).start()
            threading.Thread(target=self._ack_send_loop, daemon=True).start()
            return True
        except Exception as e:
            print(f"[-] Connection error: {e}", flush=True)
            self.running = False
            return False

    def _heartbeat_loop(self):
        """Send periodic relay heartbeats and camera timestamps."""
        while self.running:
            try:
                # Relay Heartbeat (TCP)
                self._send_tcp(self.CMD_HEARTBEAT)
                
                # Camera Timestamp (UDP) - CRITICAL for FPS
                self._send_udp_json({'code': 54, 'timerStamp': str(int(time.time()*1000))})
                
                time.sleep(1) # Frequent heartbeats to keep stream fast
            except:
                break

    def _ack_send_loop(self):
        """Periodically send selective ACKs (CMD 605) for received UDP packets."""
        while self.running:
            try:
                ids_to_ack = []
                with self._pkg_lock:
                    if self.received_pkg_ids:
                        ids_to_ack = sorted(list(self.received_pkg_ids))
                        self.received_pkg_ids.clear()
                
                if ids_to_ack:
                    # In app fwd_id is often null for ACKs, implying deal=0
                    # If we have a real forward_id, we use it only if not empty
                    fwd_id = b'00000000'
                    deal = 0
                    if self.udp_forward_id and self.udp_forward_id != b'\x00'*8:
                        fwd_id = self.udp_forward_id
                        deal = 1
                    
                    payload = b''.join(struct.pack('<I', i) for i in ids_to_ack)
                    self._send_udp(self.CMD_RETRANSMISSION_CONFIRM, payload, deal=deal, fwd_id=fwd_id)
                
                time.sleep(0.05) # Send ACKs every 50ms
            except Exception as e:
                print(f"[-] ACK loop error: {e}", flush=True)
                time.sleep(1)

    def _tcp_receive_loop(self):
        while self.running:
            try:
                hdr_data = self._recvall_tcp(self.HDR_SIZE)
                if not hdr_data: continue

                p_len, p_cmd, p_flag, p_deal, p_fwd, p_pkg = struct.unpack(self.HDR_FORMAT, hdr_data)
                payload = b''
                if p_len > 0:
                    payload = self._recvall_tcp(p_len)
                    if not payload: break

                if p_cmd == self.CMD_JSON:
                    data = json.loads(payload.decode('utf-8', 'replace'))
                    print(f"[<-TCP] {data}", flush=True)
                    code = data.get('code')

                    if code == 101:
                        self.tcp_connected_event.set()
                    elif code == 13:
                        if data.get("status") == 200:
                            self.dev_pub_ip = data.get('devIp')
                            self.dev_pub_port = data.get('devPort')
                            self.dev_nat_ip = data.get('devNatIp')
                            self.dev_nat_port = data.get('devPort') # Uses same key in JSON unfortunately
                            self.connect_req_approved.set()
                    elif code == 31:
                        if data.get("status") == 200:
                            fwd = data.get("forwardId")
                            self.tcp_forward_id = fwd.encode('ascii')[:8].ljust(8, b'\x00')
                            self.udp_forward_id = self.tcp_forward_id 
                            self.forward_id_received.set()
                    elif code == 53:
                        # Device Online
                        self.device_online_event.set()
                    elif code == 301:
                        # Forward response from device
                        content = data.get('content', {})
                        content_code = content.get('code')
                        if content_code == self.PP_BASE_INFO:
                            # Parse mirrorFlip from baseInfo
                            mf = content.get('mirrorFlip')
                            if mf is not None:
                                self.mirror_flip = mf
                                print(f"[*] Camera mirrorFlip: {mf} ({'flipped' if mf == 4 else 'normal'})", flush=True)
                elif p_cmd == self.CMD_HEARTBEAT:
                    self._send_tcp(self.CMD_HEARTBEAT)

            except socket.timeout:
                continue
            except Exception as e:
                if self.running: print(f"[-] TCP Receive error: {e}", flush=True)
                break

    def _udp_receive_loop(self):
        fragment_buffer: Dict[int, Tuple[int, bytes]] = {} # PkgId -> payload
        frames_received = 0
        last_fps_time = time.time()
        
        while self.running:
            try:
                data, addr = self.udp_sock.recvfrom(65535)
                if len(data) < self.HDR_SIZE: continue
                
                # Always track the source of packets for ACKs and Timestamps
                self.udp_target = addr

                hdr_data = data[:self.HDR_SIZE]
                payload = data[self.HDR_SIZE:]
                p_len, p_cmd, p_flag, p_deal, p_fwd, p_pkg = struct.unpack(self.HDR_FORMAT, hdr_data)

                if p_cmd == self.CMD_JSON:
                    try:
                        jdata = json.loads(payload.decode('utf-8', 'replace'))
                        print(f"[<-UDP] {jdata}", flush=True) 
                        code = jdata.get('code')
                        if code == 51:
                            if jdata.get('status') == 200:
                                self.udp_tunnel_established.set()
                        elif code == 21:
                            self.cli_pub_ip = jdata.get("ip")
                            self.cli_pub_port = jdata.get("port")
                            self.udp_info_received.set()
                    except: pass
                
                elif p_cmd == self.CMD_JPEG:
                    # Track PkgId for Selective ACK
                    with self._pkg_lock:
                        self.received_pkg_ids.add(p_pkg)

                    # Store fragment
                    fragment_buffer[p_pkg] = (p_flag, payload)

                    # If we hit an END or FINISH flag, attempt to assemble the frame
                    if p_flag in (self.FLAG_END, self.FLAG_FINISH):
                        # Look backwards for the HEAD
                        frame_fragments = []
                        curr_id = p_pkg
                        valid_frame = False
                        
                        # Max MJPEG frame size is usually < 100KB, so < 100 packets
                        for _ in range(200): 
                            if curr_id not in fragment_buffer:
                                break
                            f_flag, f_payload = fragment_buffer[curr_id]
                            frame_fragments.insert(0, f_payload)
                            if f_flag in (self.FLAG_HEAD, self.FLAG_FINISH):
                                valid_frame = True
                                break
                            curr_id -= 1
                        
                        if valid_frame:
                            full_frame = b''.join(frame_fragments)
                            
                            frames_received += 1
                            now = time.time()
                            if now - last_fps_time >= 1.0:
                                print(f"[UDP] FPS: {frames_received}", flush=True)
                                frames_received = 0
                                last_fps_time = now
                                
                            if len(full_frame) > 5:
                                frame_data = full_frame[:-5] # Strip 5 bytes of trailing firmware stats
                                
                                # Maintain realtime queue
                                if self.video_queue.qsize() > 2:
                                    try: self.video_queue.get_nowait()
                                    except: pass
                                
                                self.video_queue.put(frame_data)
                            
                            # Cleanup old fragments from buffer to prevent memory leak
                            # Keep only very recent ones for potential retransmissions/late arrivals
                            min_id = p_pkg - 500
                            fragment_buffer = {k: v for k, v in fragment_buffer.items() if k > min_id}

                elif p_cmd == self.CMD_G711:
                    # G.711 A-law audio packet
                    with self._pkg_lock:
                        self.received_pkg_ids.add(p_pkg)
                    
                    with self._g711_lock:
                        self._g711_buffer.extend(payload)
                        # Decode in 1024-byte chunks (matches app behavior)
                        while len(self._g711_buffer) >= 1024:
                            chunk = bytes(self._g711_buffer[:1024])
                            del self._g711_buffer[:1024]
                            pcm_data = decode_g711a(chunk)
                            # Keep audio queue fresh - drop old data aggressively
                            if self.audio_queue.qsize() > 3:
                                try: self.audio_queue.get_nowait()
                                except: pass
                            self.audio_queue.put(pcm_data)

                elif p_cmd in (self.CMD_AAC, self.CMD_PCM, self.CMD_RAW):
                    # Other audio formats - track for ACK, log first occurrence
                    with self._pkg_lock:
                        self.received_pkg_ids.add(p_pkg)
                    if not hasattr(self, '_other_audio_logged'):
                        self._other_audio_logged = True
                        print(f"[UDP] Non-G711 audio packet: cmd={p_cmd} len={len(payload)}", flush=True)

            except socket.timeout:
                continue
            except ConnectionResetError:
                continue
            except Exception as e:
                if self.running: print(f"[-] UDP Receive error: {e}", flush=True)
                break

    def get_latest_frame(self) -> Optional[bytes]:
        try:
            return self.video_queue.get(timeout=2.0)
        except:
            return None

    def get_audio_chunk(self) -> Optional[bytes]:
        """Get a decoded PCM16 audio chunk for streaming."""
        chunks = []
        try:
            # Drain up to 2 chunks (~256ms of audio)
            for _ in range(2):
                chunks.append(self.audio_queue.get_nowait())
        except queue.Empty:
            pass
        if chunks:
            return b''.join(chunks)
        return None

    def flush_audio(self):
        """Clear all buffered audio data."""
        while not self.audio_queue.empty():
            try: self.audio_queue.get_nowait()
            except: break
        with self._g711_lock:
            self._g711_buffer.clear()

    def send_forward_command(self, content: dict):
        """Send a forward command (code 301) to the device via TCP."""
        self._send_tcp_json({
            "code": self.CODE_FORWARD,
            "target": self.device_code,
            "content": content
        })

    def toggle_mirror_flip(self) -> int:
        """Toggle vertical mirror flip. Returns new mirror_flip value."""
        # Toggle: 4 (up_down/flipped) <-> 1 (normal)
        if self.mirror_flip == 4:
            self.mirror_flip = 1
        else:
            self.mirror_flip = 4
        self.send_forward_command({
            "code": self.PP_MIRROR_FLIP,
            "mirrorFlip": self.mirror_flip
        })
        print(f"[*] Mirror flip set to: {self.mirror_flip} ({'flipped' if self.mirror_flip == 4 else 'normal'})", flush=True)
        return self.mirror_flip

if __name__ == "__main__":
    if args.debug:
        print("[*] Starting Naxnine Web App in DEBUG mode")
        print("[*] Serving API and Dashboard at http://127.0.0.1:5000")
    else:
        print("Naxnine started silently on http://127.0.0.1:5000")
        
    # Silence werkzeug logging unless in debug mode
    if not args.debug:
        log = logging.getLogger('werkzeug')
        log.disabled = True
        
    app.run(host='0.0.0.0', port=5000, debug=False)
