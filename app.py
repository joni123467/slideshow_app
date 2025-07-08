from flask import Flask, render_template, redirect, url_for, request, flash, jsonify
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
import pam
import os
import json
import sys
import logging
from logging.handlers import RotatingFileHandler
import socket
import platform
import netifaces
import subprocess
import threading
import time

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

log_handler = RotatingFileHandler('slideshow.log', maxBytes=1048576, backupCount=3)
logging.basicConfig(
    handlers=[log_handler],
    level=getattr(logging, log_level_str.upper(), logging.DEBUG),
    format='%(asctime)s %(levelname)s:%(message)s'
)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

class User(UserMixin):
    def __init__(self, id):
        self.id = id

@login_manager.user_loader
def load_user(user_id):
    return User(user_id)

def authenticate(username, password):
    p = pam.pam()
    return p.authenticate(username, password)

def run_update_script():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    script = os.path.join(base_dir, 'update.sh')
    log_path = os.path.join(base_dir, 'update.log')
    try:
        logging.info("Web-trigger /trigger_update eingegangen — starte update.sh im Hintergrund")
        with open(log_path, 'a') as lf:
            start_ts = time.strftime('%Y-%m-%d %H:%M:%S')
            lf.write(f"\n[{start_ts}] Triggered via web\n")
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
        logging.exception("Fehler in run_update_script()")
        try:
            with open(log_path, 'a') as lf:
                lf.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Exception: {e}\n")
        except:
            pass
        return False

@app.context_processor
def inject_release_branch():
    try:
        with open("current_release.txt", "r") as f:
            current_release = f.read().strip()
    except Exception:
        current_release = "unbekannt"
    return dict(current_release=current_release)

@app.route('/update_release', methods=['GET', 'POST'])
@login_required
def update_release():
    config = load_config()
    current_release = config.get("release_branch", "")
    try:
        subprocess.run(["git", "fetch", "--all", "--tags"], check=True)
        branches = subprocess.check_output(["git", "branch", "-r"], text=True).splitlines()
        release_branches = [
            b.strip().replace("origin/", "")
            for b in branches if b.strip().startswith("origin/release/")
        ]
        tags = subprocess.check_output(["git", "tag", "--list"], text=True).splitlines()
    except Exception as e:
        release_branches = []
        tags = []
        flash("Fehler beim Ermitteln der Branches/Tags: " + str(e), "danger")
    options = sorted(set(release_branches)) + sorted(set(tags))
    return render_template(
        "update_release.html",
        current_release=current_release,
        options=options
    )

@app.route('/set_release_branch', methods=['POST'])
@login_required
def set_release_branch():
    new_release = request.form.get("release_branch")
    config = load_config()
    config["release_branch"] = new_release
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=4)
    flash(f"Release-Branch/Tag auf '{new_release}' gesetzt.", "success")
    return redirect(url_for('update_release'))

@app.route('/trigger_release_update', methods=['POST'])
@login_required
def trigger_release_update():
    threading.Thread(target=run_update_script, daemon=True).start()
    return render_template('updating.html', wait_seconds=60)

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
    config = {'ip': '', 'gateway': '', 'dns': ''}
    try:
        addrs = netifaces.ifaddresses(interface)
        if netifaces.AF_INET in addrs:
            config['ip'] = addrs[netifaces.AF_INET][0].get('addr', '')
        gws = netifaces.gateways()
        if 'default' in gws and netifaces.AF_INET in gws['default']:
            config['gateway'] = gws['default'][netifaces.AF_INET][0]
        with open('/etc/resolv.conf', 'r') as f:
            for line in f:
                if line.startswith('nameserver'):
                    config['dns'] = line.split()[1]
                    break
    except Exception as e:
        logging.error(f"Fehler beim Ermitteln der Interface-Konfiguration: {e}")
    return config

def get_ipv4_address():
    try:
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
    info = []
    info.append(f"Hostname: {socket.gethostname()}")
    info.append(f"Betriebssystem: {platform.system()} {platform.release()}")
    info.append(f"Python-Version: {platform.python_version()}")
    cpu_info = platform.processor() or "Nicht verfügbar"
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
        with open(CONFIG_FILE, 'r') as f:
            current_config = json.load(f)
        current_config["log_level"] = new_log_level
        with open(CONFIG_FILE, 'w') as f:
            json.dump(current_config, f, indent=4)
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
            split_screen_active = ('split_screen' in request.form)
            stretch_images_active = ('stretch_images' in request.form)
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
                "log_level": existing_cfg.get("log_level", "DEBUG")
            }
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
        try:
            with open('/etc/hostname', 'r') as f:
                current_hostname = f.read().strip()
        except Exception as e:
            logging.error(f"Fehler beim Lesen des Hostnamens: {e}")
            current_hostname = ""
        current_network_mode = 'dhcp'
        current_static_ip = ""
        current_routers = ""
        current_dns = ""
        try:
            method_cmd = ["nmcli", "-g", "ipv4.method", "connection", "show", "MyEthernet"]
            method_output = subprocess.check_output(method_cmd, universal_newlines=True).strip()
            if method_output == "manual":
                current_network_mode = "static"
                try:
                    fields_cmd = ["nmcli", "-t", "-f", "ipv4.addresses,ipv4.gateway,ipv4.dns", "connection", "show", "MyEthernet"]
                    fields_output = subprocess.check_output(fields_cmd, universal_newlines=True).strip()
                    nmcli_dict = {}
                    for line in fields_output.splitlines():
                        if ":" in line:
                            key, value = line.split(":", 1)
                            nmcli_dict[key.strip()] = value.strip()
                    current_static_ip = nmcli_dict.get("ipv4.addresses", "")
                    current_routers = nmcli_dict.get("ipv4.gateway", "")
                    current_dns = nmcli_dict.get("ipv4.dns", "")
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
            excerpt = "".join(lines[-20:])
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

# --------------------------------
# NEU: Funktion für Bildliste aus dem Cache
# --------------------------------
def list_cached_images():
    cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'cache')
    if not os.path.isdir(cache_dir):
        return []
    return [
        f for f in os.listdir(cache_dir)
        if f.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.bmp'))
    ]

# Beispiel: So könntest du im Template alle Vorschau-Bilder aus dem Cache anzeigen
# (siehe Text, nicht im Code unten)

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
