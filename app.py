from flask import Flask, render_template, redirect, url_for, request, flash, jsonify
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
import pam
import os
import re
import json
import ipaddress
import sys
import logging
from logging.handlers import RotatingFileHandler
import socket
import platform
from smb.SMBConnection import SMBConnection
from contextlib import contextmanager
import netifaces
import subprocess  # Für Neustart, Helper-Skripte und Passwortänderung
import threading
import time

# ------------------------------
# Flask-Anwendung und Konfiguration
# ------------------------------

app = Flask(__name__)
app.secret_key = b'your-fixed-secret-key-here'  # Ersetze dies durch einen starken Schlüssel

CONFIG_FILE = 'config.json'
try:
    with open(CONFIG_FILE, 'r') as f:
        config_data = json.load(f)
except Exception:
    config_data = {}

# Log-Level aus Config oder Default
log_level_str = config_data.get("log_level", "DEBUG")

# Rotating File Handler einrichten
log_handler = RotatingFileHandler('slideshow.log', maxBytes=1048576, backupCount=3)
logging.basicConfig(
    handlers=[log_handler],
    level=getattr(logging, log_level_str.upper(), logging.DEBUG),
    format='%(asctime)s %(levelname)s:%(message)s'
)

# Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# ------------------------------
# User-Klasse für Flask-Login
# ------------------------------

class User(UserMixin):
    def __init__(self, id):
        self.id = id

@login_manager.user_loader
def load_user(user_id):
    return User(user_id)

# ------------------------------
# PAM-Authentifizierung
# ------------------------------

def authenticate(username, password):
    p = pam.pam()
    return p.authenticate(username, password)
    
# ------------------------------
# Update-Script starten
# ------------------------------

def run_update_script():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    script = os.path.join(base_dir, 'update.sh')
    log_path = os.path.join(base_dir, 'update.log')

    try:
        logging.info("Web-trigger /trigger_update eingegangen — starte update.sh im Hintergrund")

        # Öffne unser Update-Logfile
        with open(log_path, 'a') as lf:
            start_ts = time.strftime('%Y-%m-%d %H:%M:%S')
            lf.write(f"\n[{start_ts}] Triggered via web\n")

            # Starte das Script per Bash, leite alles in unser Log
            proc = subprocess.Popen(
                ['/bin/bash', script],
                cwd=base_dir,
                stdout=lf,
                stderr=lf
            )
            proc.wait()

            end_ts = time.strftime('%Y-%m-%d %H:%M:%S')
            lf.write(f"[{end_ts}] update.sh beendet mit Exit-Code {proc.returncode}\n")

        if proc.returncode != 0:
            logging.error(f"update.sh endete mit Exit-Code {proc.returncode}")
            return False

        logging.info("update.sh erfolgreich durchgelaufen")
        return True

    except Exception as e:
        # Fange **alle** Fehler und schreibe sie ins Log
        logging.exception("Fehler in run_update_script()")
        try:
            with open(log_path, 'a') as lf:
                lf.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Exception: {e}\n")
        except:
            pass
        return False


# ------------------------------
# Web-Endpoint für Update
# ------------------------------

@app.route('/trigger_update', methods=['POST'])
@login_required
def trigger_update():
    # Update in separatem Thread starten, damit der HTTP-Request nicht hängen bleibt
    threading.Thread(target=run_update_script, daemon=True).start()
    # Zwischen-Seite mit automatischem Redirect zurück zur Startseite
    return render_template('updating.html', wait_seconds=15)

# ------------------------------
# SMB-Verbindung als Context Manager
# ------------------------------

@contextmanager
def smb_connection(username, password, domain, client_machine_name, server_name, server_ip):
    conn = SMBConnection(username, password, client_machine_name, server_name, domain=domain, use_ntlm_v2=True)
    try:
        connected = conn.connect(server_ip, 139) or conn.connect(server_ip, 445)
        if not connected:
            logging.error(f"Verbindung zum SMB-Server {server_ip} konnte nicht hergestellt werden.")
            yield None
        else:
            logging.info(f"Verbindung zu SMB-Freigabe \\{server_name}\\ erfolgreich hergestellt.")
            yield conn
    except Exception as e:
        logging.error(f"Fehler beim Herstellen der Verbindung zu SMB-Server: {e}")
        yield None
    finally:
        conn.close()
        logging.info(f"Verbindung zum SMB-Server {server_ip} geschlossen.")

# ------------------------------
# Hilfsfunktionen
# ------------------------------

def load_config():
    try:
        with open(CONFIG_FILE, 'r') as f:
            config = json.load(f)
        logging.info("Konfigurationsdatei erfolgreich geladen.")
        return config
    except Exception as e:
        logging.error(f"Fehler beim Laden der Konfigurationsdatei: {e}")
        sys.exit(1)
        
def get_current_interface_config(interface='eth0'):
    """Ermittelt aktuelle IP, Gateway und DNS für das angegebene Interface."""
    config = {'ip': '', 'gateway': '', 'dns': ''}
    try:
        import netifaces
        # IP-Adresse
        addrs = netifaces.ifaddresses(interface)
        if netifaces.AF_INET in addrs:
            config['ip'] = addrs[netifaces.AF_INET][0].get('addr', '')
        # Gateway
        gws = netifaces.gateways()
        if 'default' in gws and netifaces.AF_INET in gws['default']:
            config['gateway'] = gws['default'][netifaces.AF_INET][0]
        # DNS aus /etc/resolv.conf (nur der erste Eintrag)
        with open('/etc/resolv.conf', 'r') as f:
            for line in f:
                if line.startswith('nameserver'):
                    config['dns'] = line.split()[1]
                    break
    except Exception as e:
        logging.error(f"Fehler beim Ermitteln der Interface-Konfiguration: {e}")
    return config


def get_image_files(path, username='', password='', domain=''):
    supported_extensions = ('.png', '.jpg', '.jpeg', '.gif', '.bmp')
    image_files = []

    if path.startswith('smb://'):
        match = re.match(r'smb://([^/]+)/([^/]+)/(.*)', path)
        if not match:
            logging.error("Ungültiges SMB-Pfadformat.")
            return []
        server, share, remote_path = match.groups()

        with smb_connection(username, password, domain, "slideshow_client", server, server) as conn:
            if conn:
                try:
                    files = conn.listPath(share, remote_path)
                    for file in files:
                        file_name = file.filename
                        if file_name not in ['.', '..'] and file_name.lower().endswith(supported_extensions):
                            image_files.append(f"smb://{server}/{share}/{remote_path}/{file_name}")
                    logging.info(f"Gefundene Bilder im SMB-Pfad: {len(image_files)}")
                except Exception as e:
                    logging.error(f"Fehler beim Auflisten des Verzeichnisses {remote_path}: {e}")
    else:
        if os.path.isdir(path):
            try:
                image_files = [
                    os.path.join(path, f)
                    for f in os.listdir(path)
                    if f.lower().endswith(supported_extensions)
                ]
                logging.info(f"Gefundene Bilder im lokalen Pfad: {len(image_files)}")
            except Exception as e:
                logging.error(f"Fehler beim Lesen des lokalen Pfads: {e}")
        else:
            logging.error("Lokaler Pfad ist kein Verzeichnis.")
    return image_files

def display_message(screen, message, infoObject):
    try:
        import pygame
        font = pygame.font.SysFont(None, 48)
    except Exception as e:
        logging.error(f"Fehler beim Laden der Schriftart: {e}")
        sys.exit(1)
    lines = message.split('\n')
    y_offset = infoObject.current_h // 2 - (len(lines) * 30)
    for line in lines:
        text = font.render(line, True, (255, 255, 255))
        rect = text.get_rect(center=(infoObject.current_w // 2, y_offset))
        screen.blit(text, rect)
        y_offset += 60
    pygame.display.flip()

def get_ipv4_address():
    try:
        import netifaces
        interfaces = netifaces.interfaces()
        for interface in interfaces:
            if interface == 'lo':
                continue
            addrs = netifaces.ifaddresses(interface)
            if netifaces.AF_INET in addrs:
                for addr in addrs[netifaces.AF_INET]:
                    ip = addr.get('addr')
                    if ip and not ip.startswith('169.254.'):
                        logging.info(f"Gefundene IPv4-Adresse: {ip}")
                        return ip
        logging.warning("Keine gültige IPv4-Adresse gefunden.")
    except Exception as e:
        logging.error(f"Fehler beim Ermitteln der IPv4-Adresse: {e}")
    return "Nicht verfügbar"

def get_device_info():
    import platform
    info = []
    info.append(f"Hostname: {socket.gethostname()}")
    info.append(f"Betriebssystem: {platform.system()} {platform.release()}")
    info.append(f"Python-Version: {platform.python_version()}")
    
    cpu_info = platform.processor()
    if not cpu_info:
        cpu_info = "Nicht verfügbar"
    info.append(f"CPU: {cpu_info}")
    
    try:
        with open('/proc/meminfo', 'r') as mem:
            mem_info = mem.read()
        total_mem = re.search(r'MemTotal:\s+(\d+) kB', mem_info).group(1)
        info.append(f"RAM: {int(total_mem) / 1024} MB")
    except:
        info.append("RAM: Nicht verfügbar")
    
    ipv4 = get_ipv4_address()
    info.append(f"IPv4-Adresse: {ipv4}")
    
    info.append("")
    info.append("Die Slideshow kann über das Webinterface konfiguriert werden.")
    
    logging.info("Geräteinformationen gesammelt.")
    return '\n'.join(info)

def update_hostname_helper(new_hostname):
    try:
        subprocess.check_call(['sudo', '/usr/local/bin/update_hostname.sh', new_hostname])
        logging.info(f"Hostname erfolgreich zu {new_hostname} geändert (über Helper-Skript).")
        return True
    except subprocess.CalledProcessError as e:
        logging.error(f"Fehler beim Ändern des Hostnamens: {e}")
        return False

def update_network_config_helper(mode, static_ip, routers, dns):
    try:
        subprocess.check_call([
            'sudo',
            '/usr/local/bin/update_network_config.sh',
            mode,
            static_ip,
            routers,
            dns
        ])
        logging.info("Netzwerkeinstellungen erfolgreich aktualisiert (über Helper-Skript).")
        return True
    except subprocess.CalledProcessError as e:
        logging.error(f"Fehler beim Aktualisieren der Netzwerkeinstellungen: {e}")
        return False

@app.route('/change_password', methods=['GET', 'POST'])
@login_required
def change_password():
    if request.method == 'POST':
        current_password = request.form.get('current_password', '').strip()
        new_password = request.form.get('new_password', '').strip()
        confirm_password = request.form.get('confirm_password', '').strip()
        if new_password != confirm_password:
            flash("Die neuen Passwörter stimmen nicht überein.", "danger")
            return redirect(url_for('change_password'))
        
        if not authenticate(current_user.id, current_password):
            flash("Das aktuelle Passwort ist falsch.", "danger")
            return redirect(url_for('change_password'))
        
        try:
            command = f"echo '{current_user.id}:{new_password}' | sudo chpasswd"
            subprocess.check_call(command, shell=True)
            flash("Passwort erfolgreich geändert.", "success")
            logging.info(f"Passwort erfolgreich geändert für Benutzer {current_user.id}")
        except subprocess.CalledProcessError as e:
            flash("Fehler beim Ändern des Passworts.", "danger")
            logging.error(f"Fehler beim Ändern des Passworts: {e}")
        
        return redirect(url_for('index'))
    return render_template('change_password.html')

@app.route('/login', methods=['GET', 'POST'])
def login_route():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        if not username or not password:
            flash('Bitte geben Sie sowohl Benutzername als auch Passwort ein.', 'danger')
            return render_template('login.html')
        if authenticate(username, password):
            user = User(username)
            login_user(user)
            flash('Erfolgreich eingeloggt.', 'success')
            next_page = request.args.get('next')
            return redirect(next_page or url_for('index'))
        else:
            flash('Ungültiger Benutzername oder Passwort.', 'danger')
    return render_template('login.html')

app.add_url_rule('/login', 'login', login_route)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Erfolgreich ausgeloggt.', 'success')
    return redirect(url_for('login'))

@app.route('/')
@login_required
def index():
# Lese die letzten 20 Zeilen der Logdatei
    log_excerpt = ""
    try:
        with open('slideshow.log', 'r') as f:
            lines = f.readlines()
            log_excerpt = "".join(lines[-20:])
    except Exception as e:
        log_excerpt = "Keine Log-Daten verfügbar."
    
    current_config = load_config()
    current_log_level = current_config.get("log_level", "DEBUG")
    
    return render_template('index.html', log_excerpt=log_excerpt, current_log_level=current_log_level)
    
@app.route('/update_log_level', methods=['POST'])
@login_required
def update_log_level():
    new_log_level = request.form.get("log_level", "DEBUG")
    try:
        # Config laden, aktualisieren und speichern
        with open(CONFIG_FILE, 'r') as f:
            current_config = json.load(f)
        current_config["log_level"] = new_log_level
        with open(CONFIG_FILE, 'w') as f:
            json.dump(current_config, f, indent=4)
        
        # Logger dynamisch anpassen
        logging.getLogger().setLevel(getattr(logging, new_log_level.upper(), logging.DEBUG))
        flash("Log-Level erfolgreich aktualisiert.", "success")
    except Exception as e:
        flash("Fehler beim Aktualisieren des Log-Levels: " + str(e), "danger")
    return redirect(url_for('index'))

@app.route('/config', methods=['GET', 'POST'])
@login_required
def config():
    if request.method == 'POST':
        try:
            split_screen_active   = ('split_screen' in request.form)
            stretch_images_active = ('stretch_images' in request.form)
            # Bestehende Config einlesen, um log_level nicht zu verlieren
            with open(CONFIG_FILE, 'r') as f:
                existing_cfg = json.load(f)

            new_config = {
                "image_path": request.form.get('image_path', '').strip(),
                "image_path_left": request.form.get('image_path_left', '').strip(),
                "image_path_right": request.form.get('image_path_right', '').strip(),
                "display_duration": int(request.form.get('display_duration', 5)),
                "rotation": int(request.form.get('rotation', 0)),
                "smb_username": request.form.get('smb_username', '').strip(),
                "smb_password": request.form.get('smb_password', '').strip(),
                "smb_domain": request.form.get('smb_domain', '').strip(),
                "smb_username_left": request.form.get('smb_username_left', '').strip(),
                "smb_password_left": request.form.get('smb_password_left', '').strip(),
                "smb_domain_left": request.form.get('smb_domain_left', '').strip(),
                "smb_username_right": request.form.get('smb_username_right', '').strip(),
                "smb_password_right": request.form.get('smb_password_right', '').strip(),
                "smb_domain_right": request.form.get('smb_domain_right', '').strip(),
                "mode": request.form.get('mode', 'info').strip(),
                "mode_left": request.form.get('mode_left', 'slideshow').strip(),
                "mode_right": request.form.get('mode_right', 'slideshow').strip(),
                "reload": False,
                "split_screen": split_screen_active,
                "stretch_images": stretch_images_active,
                # log_level aus bestehender Config übernehmen
                "log_level": existing_cfg.get("log_level", "DEBUG")
            }

            # Validierung wie gehabt...

            with open(CONFIG_FILE, 'w') as f:
                json.dump(new_config, f, indent=4)
            flash('Konfiguration erfolgreich gespeichert.', 'success')
            logging.info(f"Konfiguration aktualisiert: {new_config}")
            return redirect(url_for('config'))
        except ValueError:
            flash('Bitte geben Sie gültige numerische Werte für Anzeigedauer und Rotation ein.', 'danger')
            return redirect(url_for('config'))
        except Exception as e:
            logging.error(f"Fehler beim Speichern der Konfiguration: {e}")
            flash('Fehler beim Speichern der Konfiguration.', 'danger')
            return redirect(url_for('config'))
    else:
        try:
            current_config = load_config()
        except FileNotFoundError:
            current_config = {
                "image_path": "",
                "image_path_left": "",
                "image_path_right": "",
                "display_duration": 5,
                "rotation": 0,
                "smb_username": "",
                "smb_password": "",
                "smb_domain": "",
                "smb_username_left": "",
                "smb_password_left": "",
                "smb_domain_left": "",
                "smb_username_right": "",
                "smb_password_right": "",
                "smb_domain_right": "",
                "mode": "info",
                "mode_left": "slideshow",
                "mode_right": "slideshow",
                "reload": False,
                "split_screen": False,
                "stretch_images": True,
                "log_level": "DEBUG"
            }
            with open(CONFIG_FILE, 'w') as f:
                json.dump(current_config, f, indent=4)
            logging.info("Standardkonfiguration erstellt.")

        # Fehlende Keys ergänzen
        needed_keys = [
            "image_path", "image_path_left", "image_path_right",
            "display_duration", "rotation",
            "smb_username", "smb_password", "smb_domain",
            "smb_username_left", "smb_password_left", "smb_domain_left",
            "smb_username_right", "smb_password_right", "smb_domain_right",
            "mode", "mode_left", "mode_right",
            "reload", "split_screen", "stretch_images", "log_level"
        ]
        changed = False
        for k in needed_keys:
            if k not in current_config:
                if k == "display_duration":
                    current_config[k] = 5
                elif k == "rotation":
                    current_config[k] = 0
                elif k in ("mode",):
                    current_config[k] = "info"
                elif k in ("mode_left", "mode_right"):
                    current_config[k] = "slideshow"
                elif k in ("reload", "split_screen", "stretch_images"):
                    current_config[k] = False if k != "stretch_images" else True
                elif k == "log_level":
                    current_config[k] = "DEBUG"
                else:
                    current_config[k] = ""
                changed = True
        if changed:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(current_config, f, indent=4)
            logging.info("Konfigurationsdatei mit fehlenden Keys ergänzt.")

        return render_template('config.html', config=current_config)

@app.route('/network_config', methods=['GET', 'POST'])
@login_required
def network_config():
    if request.method == 'POST':
        # (POST-Logik bleibt unverändert)
        new_hostname = request.form.get('hostname', '').strip()
        network_mode = request.form.get('network_mode', 'dhcp').strip()
        static_ip = request.form.get('static_ip', '').strip()
        routers = request.form.get('routers', '').strip()
        dns = request.form.get('dns', '').strip()
        errors = []
        
        if not new_hostname:
            errors.append("Hostname darf nicht leer sein.")
        if network_mode not in ['dhcp', 'static']:
            errors.append("Ungültiger Netzwerkmodus.")
        if network_mode == 'static':
            if not static_ip:
                errors.append("Statische IP-Adresse ist erforderlich.")
            if not routers:
                errors.append("Router-Adresse ist erforderlich.")
            if not dns:
                errors.append("DNS-Server ist erforderlich.")
        
        if errors:
            for error in errors:
                flash(error, 'danger')
            return redirect(url_for('network_config'))
        
        hostname_changed = update_hostname_helper(new_hostname)
        if network_mode == 'static':
            network_changed = update_network_config_helper(network_mode, static_ip, routers, dns)
        else:
            network_changed = update_network_config_helper(network_mode, "", "", "")
        
        if hostname_changed and network_changed:
            flash("Netzwerkeinstellungen wurden erfolgreich aktualisiert.", "success")
        else:
            flash("Fehler beim Aktualisieren der Netzwerkeinstellungen.", "danger")
        return redirect(url_for('network_config'))
    else:
        # Ermittlung des aktuellen Hostnamens
        try:
            with open('/etc/hostname', 'r') as f:
                current_hostname = f.read().strip()
        except Exception as e:
            logging.error(f"Fehler beim Lesen des Hostnamens: {e}")
            current_hostname = ""
        
        # Standardwerte setzen
        current_network_mode = 'dhcp'
        current_static_ip = ""
        current_routers = ""
        current_dns = ""
        
        try:
            # Zuerst nur den ipv4.method-Wert abfragen
            method_cmd = ["nmcli", "-g", "ipv4.method", "connection", "show", "MyEthernet"]
            method_output = subprocess.check_output(method_cmd, universal_newlines=True).strip()
            
            if method_output == "manual":
                current_network_mode = "static"
                # Nun die restlichen Felder abfragen
                try:
                    fields_cmd = ["nmcli", "-t", "-f", "ipv4.addresses,ipv4.gateway,ipv4.dns", "connection", "show", "MyEthernet"]
                    fields_output = subprocess.check_output(fields_cmd, universal_newlines=True).strip()
                    nmcli_dict = {}
                    for line in fields_output.splitlines():
                        if ":" in line:
                            key, value = line.split(":", 1)
                            nmcli_dict[key.strip()] = value.strip()
                    current_static_ip = nmcli_dict.get("ipv4.addresses", "")
                    current_routers   = nmcli_dict.get("ipv4.gateway", "")
                    current_dns       = nmcli_dict.get("ipv4.dns", "")
                except Exception as e:
                    logging.error("Fehler beim Abrufen der statischen Netzwerkeinstellungen: " + str(e))
                    iface_config = get_current_interface_config('eth0')
                    current_network_mode = 'dhcp'
                    current_static_ip = iface_config.get('ip', '')
                    current_routers = iface_config.get('gateway', '')
                    current_dns = iface_config.get('dns', '')

            else:
                current_network_mode = "dhcp"
                iface_config = get_current_interface_config('eth0')
                current_static_ip = iface_config.get('ip', '')
                current_routers = iface_config.get('gateway', '')
                current_dns = iface_config.get('dns', '')
        except Exception as e:
            logging.error("Fehler beim Abrufen der Netzwerkeinstellungen via nmcli: " + str(e))
            iface_config = get_current_interface_config('eth0')
            current_network_mode = 'dhcp'
            current_static_ip = iface_config.get('ip', '')
            current_routers = iface_config.get('gateway', '')
            current_dns = iface_config.get('dns', '')
        
        return render_template(
            'network_config.html',
            hostname=current_hostname,
            network_mode=current_network_mode,
            static_ip=current_static_ip,
            routers=current_routers,
            dns=current_dns
        )
        
@app.route('/current_image')
@login_required
def current_image():
    data = {
        "compat": "",
        "fullscreen": "",
        "left": "",
        "right": ""
    }
    try:
        with open("current_image.txt", "r") as f:
            data["compat"] = f.read().strip()
    except:
        data["compat"] = ""
    
    cfg = load_config()
    is_split = cfg.get("split_screen", False)
    
    if is_split:
        try:
            with open("current_image_left.txt", "r") as f:
                data["left"] = f.read().strip()
        except:
            data["left"] = ""
        
        try:
            with open("current_image_right.txt", "r") as f:
                data["right"] = f.read().strip()
        except:
            data["right"] = ""
    else:
        try:
            with open("current_image_fullscreen.txt", "r") as f:
                data["fullscreen"] = f.read().strip()
        except:
            data["fullscreen"] = ""
    
    return jsonify(data)
    
@app.route('/log_excerpt')
@login_required
def log_excerpt():
    try:
        with open('slideshow.log', 'r') as f:
            lines = f.readlines()
            excerpt = "".join(lines[-20:])  # Letzte 20 Zeilen
    except Exception as e:
        excerpt = "Keine Log-Daten verfügbar."
    return jsonify({'log_excerpt': excerpt})

@app.route('/restart', methods=['POST'])
@login_required
def restart():
    try:
        subprocess.check_call(['sudo', '/sbin/reboot'])
        flash("Neustart eingeleitet. Der Raspberry Pi wird in Kürze neu starten.", "success")
    except subprocess.CalledProcessError as e:
        flash("Fehler beim Neustarten: " + str(e), "danger")
        logging.error("Neustart Fehler: " + str(e))
    return redirect(url_for('index'))

if __name__ == '__main__':
    if not os.path.exists(CONFIG_FILE):
        default_config = {
            "image_path": "",
            "image_path_left": "",
            "image_path_right": "",
            "display_duration": 5,
            "rotation": 0,
            "smb_username": "",
            "smb_password": "",
            "smb_domain": "",
            "smb_username_left": "",
            "smb_password_left": "",
            "smb_domain_left": "",
            "smb_username_right": "",
            "smb_password_right": "",
            "smb_domain_right": "",
            "mode": "info",
            "mode_left": "slideshow",
            "mode_right": "slideshow",
            "reload": False,
            "split_screen": False,
            "stretch_images": True,
            "log_level": "DEBUG"
        }
        with open(CONFIG_FILE, 'w') as f:
            json.dump(default_config, f, indent=4)
        logging.info("Standardkonfigurationsdatei erstellt.")
    app.run(host='0.0.0.0', port=5000)
