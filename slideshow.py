import pygame
import os
import time
import json
import sys
import re
import socket
import platform
import netifaces
import logging
from logging.handlers import RotatingFileHandler
from smb.SMBConnection import SMBConnection
from contextlib import contextmanager
from PIL import Image   # für Bildrotation

CONFIG_FILE = 'config.json'
CURRENT_IMAGE_FULLSCREEN = "current_image_fullscreen.txt"
CURRENT_IMAGE_LEFT = "current_image_left.txt"
CURRENT_IMAGE_RIGHT = "current_image_right.txt"

# --- Logging Setup ---
log_handler = RotatingFileHandler('slideshow.log', maxBytes=1048576, backupCount=3)
formatter = logging.Formatter('%(asctime)s %(levelname)s [%(name)s]: %(message)s')
log_handler.setFormatter(formatter)
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)  # Default-Level, wird später nach config überschrieben
root_logger.addHandler(log_handler)


def to_relative_cache_path(absolute_path):
    filename = os.path.basename(absolute_path)
    return f"/static/cache/{filename}"


@contextmanager
def smb_connection(username, password, domain, client_machine_name, server_name, server_ip):
    """
    domain: z.B. 'MEINE-DOMÄNE' oder '' für Workgroup
    """
    conn = SMBConnection(
        username,
        password,
        client_machine_name,
        server_name,
        domain=domain,
        use_ntlm_v2=True
    )
    try:
        if not (conn.connect(server_ip, 445) or conn.connect(server_ip, 139)):
            logging.error(f"Verbindung zu SMB-Server {server_ip} fehlgeschlagen.")
            yield None
        else:
            logging.info(f"SMB-Verbindung zu \\\\{server_name}\\ (Domain={domain}) hergestellt.")
            yield conn
    except Exception:
        logging.exception("Fehler beim Herstellen der Verbindung zu SMB-Server")
        yield None
    finally:
        try:
            conn.close()
        except Exception:
            logging.exception("Fehler beim Schließen der SMB-Verbindung")
        else:
            logging.info(f"SMB-Verbindung zu {server_ip} geschlossen.")


def load_config():
    default_config = {
        "mode": "info",
        "split_screen": False,
        "mode_left": "slideshow",
        "mode_right": "slideshow",
        "image_path": "",
        "image_path_left": "",
        "image_path_right": "",
        "display_duration": 5,
        "rotation": 0,
        "smb_username": "",
        "smb_domain": "",
        "smb_password": "",
        "smb_username_left": "",
        "smb_domain_left": "",
        "smb_password_left": "",
        "smb_username_right": "",
        "smb_domain_right": "",
        "smb_password_right": "",
        "reload": False,
        "stretch_images": True,
        "log_level": "DEBUG"
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                user_config = json.load(f)
            default_config.update(user_config)
            missing = []
            for key in ["smb_domain", "smb_domain_left", "smb_domain_right"]:
                if key not in default_config:
                    default_config[key] = ""
                    missing.append(key)
            if missing:
                save_config(default_config)
                logging.info(f"Fehlende SMB-Domain-Keys hinzugefügt: {missing}")
            logging.info("Konfigurationsdatei erfolgreich geladen.")
        except Exception:
            logging.exception("Fehler beim Laden der Konfigurationsdatei")
    else:
        logging.warning("Konfigurationsdatei nicht gefunden. Verwende Standardeinstellungen.")
    return default_config


def save_config(config):
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=4)
        logging.info("Konfigurationsdatei erfolgreich aktualisiert.")
    except Exception:
        logging.exception("Fehler beim Schreiben der Konfigurationsdatei")


def get_local_image_files(local_path):
    supported_extensions = ('.png', '.jpg', '.jpeg', '.gif', '.bmp')
    image_files = []
    if os.path.isdir(local_path):
        try:
            image_files = [
                os.path.join(local_path, f)
                for f in os.listdir(local_path)
                if f.lower().endswith(supported_extensions)
            ]
            logging.info(f"Gefundene lokale Bilder: {len(image_files)} in {local_path}")
        except Exception:
            logging.exception(f"Fehler beim Lesen des lokalen Pfads {local_path}")
    else:
        if local_path:
            logging.error(f"Lokaler Pfad ist kein Verzeichnis: {local_path}")
    return image_files


def prefetch_smb_images(smb_path, username, password, domain):
    supported_extensions = ('.png', '.jpg', '.jpeg', '.gif', '.bmp')
    match = re.match(r'smb://([^/]+)/([^/]+)/(.*)', smb_path)
    if not match:
        logging.error(f"Ungültiges SMB-Pfadformat: {smb_path}")
        return []
    server, share, remote_path = match.groups()

    cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'cache')
    os.makedirs(cache_dir, exist_ok=True)

    local_files = []
    logging.info(f"Starte SMB-Prefetch: {smb_path} (Domain={domain})")
    with smb_connection(username, password, domain, "slideshow_client", server, server) as conn:
        if conn:
            try:
                files = conn.listPath(share, remote_path)
                count = 0
                for f in files:
                    name = f.filename
                    if name in ('.', '..') or not name.lower().endswith(supported_extensions):
                        continue
                    remote_file = os.path.join(remote_path, name).replace('\\', '/')
                    cache_path = os.path.join(cache_dir, name)
                    with open(cache_path, 'wb') as out:
                        conn.retrieveFile(share, remote_file, out)
                    local_files.append(cache_path)
                    count += 1
                logging.info(f"SMB-Prefetch abgeschlossen: {count} Dateien heruntergeladen.")
            except Exception:
                logging.exception(f"Fehler beim Listen des SMB-Verzeichnisses {remote_path}")
        else:
            logging.error(f"SMB-Prefetch: Keine Verbindung zu {server}")
    return local_files


def display_message(surface, message, infoObject):
    try:
        font = pygame.font.SysFont(None, 48)
    except Exception:
        logging.exception("Fehler beim Laden der Schriftart für Anzeige")
        sys.exit(1)
    lines = message.split('\n')
    y = infoObject.current_h // 2 - len(lines) * 30
    for line in lines:
        text = font.render(line, True, (255, 255, 255))
        rect = text.get_rect(center=(infoObject.current_w // 2, y))
        surface.blit(text, rect)
        y += 60
    pygame.display.flip()


def get_ipv4_address():
    try:
        for iface in netifaces.interfaces():
            if iface == 'lo':
                continue
            addrs = netifaces.ifaddresses(iface)
            for addr in addrs.get(netifaces.AF_INET, []):
                ip = addr.get('addr')
                if ip and not ip.startswith('169.254.'):
                    logging.info(f"Ermittelte IPv4-Adresse: {ip}")
                    return ip
        logging.warning("Keine gültige IPv4-Adresse gefunden.")
    except Exception:
        logging.exception("Fehler beim Ermitteln der IPv4-Adresse")
    return "Nicht verfügbar"


def get_device_info():
    info = [
        f"Hostname: {socket.gethostname()}",
        f"Betriebssystem: {platform.system()} {platform.release()}",
        f"Python-Version: {platform.python_version()}",
        f"CPU: {platform.processor() or 'Nicht verfügbar'}"
    ]
    try:
        with open('/proc/meminfo', 'r') as mem:
            mem_info = mem.read()
        total_kb = int(re.search(r'MemTotal:\s+(\d+)', mem_info).group(1))
        info.append(f"RAM: {total_kb // 1024} MB")
    except Exception:
        logging.exception("Fehler beim Lesen von /proc/meminfo")
        info.append("RAM: Nicht verfügbar")
    ip = get_ipv4_address()
    info.append(f"IPv4-Adresse: {ip}")
    info.append("")
    info.append("Die Slideshow kann über das Webinterface konfiguriert werden.")
    logging.info("Geräteinformationen erstellt")
    return '\n'.join(info)

def fetch_images_from_config(cfg):
    split_screen = cfg.get('split_screen', False)
    if not split_screen:
        path = cfg.get('image_path', '')
        if path.startswith('smb://'):
            imgs = prefetch_smb_images(
                path,
                cfg.get('smb_username', ''),
                cfg.get('smb_password', ''),
                cfg.get('smb_domain', '')
            )
        else:
            imgs = get_local_image_files(path)
        logging.info(f"Fetch fullscreen: {len(imgs)} Bilder")
        return (imgs, [], [])
    else:
        left = cfg.get('image_path_left', '')
        right = cfg.get('image_path_right', '')
        if left.startswith('smb://'):
            left_imgs = prefetch_smb_images(
                left,
                cfg.get('smb_username_left', ''),
                cfg.get('smb_password_left', ''),
                cfg.get('smb_domain_left', '')
            )
        else:
            left_imgs = get_local_image_files(left)
        if right.startswith('smb://'):
            right_imgs = prefetch_smb_images(
                right,
                cfg.get('smb_username_right', ''),
                cfg.get('smb_password_right', ''),
                cfg.get('smb_domain_right', '')
            )
        else:
            right_imgs = get_local_image_files(right)
        logging.info(f"Fetch split: left={len(left_imgs)}, right={len(right_imgs)} Bilder")
        return ([], left_imgs, right_imgs)


def main():
    logging.info("Starte Slideshow-Programm")
    try:
        pygame.init()
        pygame.mouse.set_visible(False)
    except Exception:
        logging.exception("Fehler beim Initialisieren von Pygame")
        sys.exit(1)

    try:
        infoObject = pygame.display.Info()
        screen = pygame.display.set_mode(
            (infoObject.current_w, infoObject.current_h),
            pygame.FULLSCREEN | pygame.NOFRAME
        )
        pygame.display.set_caption('Slideshow')
    except Exception:
        logging.exception("Fehler beim Einrichten des Pygame-Fensters")
        sys.exit(1)

    clock = pygame.time.Clock()

    # Config laden und Log-Level setzen
    config = load_config()
    lvl_name = config.get("log_level", "INFO").upper()
    lvl = getattr(logging, lvl_name, logging.INFO)
    for handler in logging.getLogger().handlers:
        handler.setLevel(lvl)
    logging.getLogger().setLevel(lvl)
    logging.info(f"Log-Level auf {lvl_name} gesetzt")

    stretch_images = config.get("stretch_images", True)
    image_path = config.get('image_path', '')
    left_path = config.get('image_path_left', '')
    right_path = config.get('image_path_right', '')

    image_files, left_images, right_images = fetch_images_from_config(config)

    mode = config.get('mode', 'info')
    mode_left = config.get('mode_left', 'slideshow')
    mode_right = config.get('mode_right', 'slideshow')
    split_screen = config.get('split_screen', False)
    display_duration = config.get('display_duration', 5)
    rotation = config.get('rotation', 0)

    if split_screen:
        if not left_images:
            mode_left = "info"
        if not right_images:
            mode_right = "info"
    else:
        if not image_files:
            mode = "info"

    index = 0
    left_index = 0
    right_index = 0
    last_switch = time.time()
    config_check_interval = 1.0
    last_config_check = time.time()
    running = True

    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                logging.info("Beenden des Slideshow-Skripts.")
                running = False
            elif event.type == pygame.KEYDOWN and event.key in [pygame.K_ESCAPE, pygame.K_q]:
                logging.info("Beenden des Slideshow-Skripts durch Benutzer.")
                running = False

        # Config reload?
        if time.time() - last_config_check >= config_check_interval:
            current_config = load_config()
            last_config_check = time.time()
            new_stretch = current_config.get("stretch_images", True)
            new_mode = current_config.get('mode', 'info')
            new_mode_left = current_config.get('mode_left', 'slideshow')
            new_mode_right = current_config.get('mode_right', 'slideshow')
            new_split = current_config.get('split_screen', False)
            new_duration = current_config.get('display_duration', 5)
            new_rotation = current_config.get('rotation', 0)
            new_reload = current_config.get('reload', False)
            new_path = current_config.get('image_path', '')
            new_left = current_config.get('image_path_left', '')
            new_right = current_config.get('image_path_right', '')

            changed = (
                new_mode != mode or
                new_mode_left != mode_left or
                new_mode_right != mode_right or
                new_split != split_screen or
                new_duration != display_duration or
                new_rotation != rotation or
                new_reload or
                new_path != image_path or
                new_left != left_path or
                new_right != right_path or
                new_stretch != stretch_images
            )
            if changed:
                logging.info("Änderungen in config.json erkannt – lade neu.")
                mode, mode_left, mode_right = new_mode, new_mode_left, new_mode_right
                split_screen = new_split
                display_duration = new_duration
                rotation = new_rotation
                stretch_images = new_stretch
                image_path, left_path, right_path = new_path, new_left, new_right

                image_files, left_images, right_images = fetch_images_from_config(current_config)

                if split_screen:
                    if not left_images:
                        mode_left = "info"
                    if not right_images:
                        mode_right = "info"
                else:
                    if not image_files:
                        mode = "info"

                index = left_index = right_index = 0
                last_switch = time.time()

                if new_reload:
                    current_config['reload'] = False
                    save_config(current_config)

                config = current_config

        screen.fill((0, 0, 0))

        if split_screen:
            left_w = infoObject.current_w // 2
            right_w = infoObject.current_w - left_w
            left_surf = pygame.Surface((left_w, infoObject.current_h))
            right_surf = pygame.Surface((right_w, infoObject.current_h))

            if time.time() - last_switch > display_duration:
                if left_path.startswith('smb://'):
                    left_images = prefetch_smb_images(
                        left_path,
                        config.get('smb_username_left',''),
                        config.get('smb_password_left',''),
                        config.get('smb_domain_left','')
                    )
                if right_path.startswith('smb://'):
                    right_images = prefetch_smb_images(
                        right_path,
                        config.get('smb_username_right',''),
                        config.get('smb_password_right',''),
                        config.get('smb_domain_right','')
                    )
                if mode_left == "slideshow" and left_images:
                    left_index = (left_index + 1) % len(left_images)
                elif mode_left == "slideshow":
                    mode_left = "info"
                if mode_right == "slideshow" and right_images:
                    right_index = (right_index + 1) % len(right_images)
                elif mode_right == "slideshow":
                    mode_right = "info"
                last_switch = time.time()

            # Linke Seite
            if mode_left == "slideshow" and left_images:
                left_file = left_images[left_index]
                try:
                    surf = pygame.image.load(left_file)
                    if rotation:
                        pil = Image.open(left_file)
                        rot = pil.rotate(rotation, expand=True)
                        p = os.path.join(os.path.dirname(left_file), f"rotated_left_{os.path.basename(left_file)}")
                        rot.save(p)
                        surf = pygame.image.load(p)
                    w_img, h_img = surf.get_size()
                    if stretch_images:
                        surf = pygame.transform.scale(surf, (left_w, infoObject.current_h))
                        left_surf.blit(surf, (0, 0))
                    else:
                        scale = min(left_w / w_img, infoObject.current_h / h_img)
                        nw, nh = int(w_img * scale), int(h_img * scale)
                        if (nw, nh) != (w_img, h_img):
                            surf = pygame.transform.scale(surf, (nw, nh))
                        xo = (left_w - nw) // 2
                        yo = (infoObject.current_h - nh) // 2
                        left_surf.blit(surf, (xo, yo))
                    with open("current_image.txt", "w") as f:
                        f.write(left_file)
                    rel = to_relative_cache_path(left_file)
                    with open(CURRENT_IMAGE_LEFT, "w") as f:
                        f.write(rel)
                except Exception:
                    logging.exception(f"Fehler beim Anzeigen des linken Bildes {left_file}")
                    display_message(left_surf, "Fehler beim Laden (links).", left_surf)
            elif mode_left == "info":
                font = pygame.font.SysFont(None, 36)
                info_text = get_device_info()
                y0 = 20
                for line in info_text.split('\n'):
                    text = font.render(line, True, (255, 255, 255))
                    left_surf.blit(text, (20, y0))
                    y0 += 40
                with open(CURRENT_IMAGE_LEFT, "w") as f:
                    f.write("/static/infoscreen.jpg")
            else:
                display_message(left_surf, "Keine Bilder (links).", left_surf)
                with open(CURRENT_IMAGE_LEFT, "w") as f:
                    f.write("/static/infoscreen.jpg")

            # Rechte Seite
            if mode_right == "slideshow" and right_images:
                right_file = right_images[right_index]
                try:
                    surf = pygame.image.load(right_file)
                    if rotation:
                        pil = Image.open(right_file)
                        rot = pil.rotate(rotation, expand=True)
                        p = os.path.join(os.path.dirname(right_file), f"rotated_right_{os.path.basename(right_file)}")
                        rot.save(p)
                        surf = pygame.image.load(p)
                    w_img, h_img = surf.get_size()
                    if stretch_images:
                        surf = pygame.transform.scale(surf, (right_w, infoObject.current_h))
                        right_surf.blit(surf, (0, 0))
                    else:
                        scale = min(right_w / w_img, infoObject.current_h / h_img)
                        nw, nh = int(w_img * scale), int(h_img * scale)
                        if (nw, nh) != (w_img, h_img):
                            surf = pygame.transform.scale(surf, (nw, nh))
                        xo = (right_w - nw) // 2
                        yo = (infoObject.current_h - nh) // 2
                        right_surf.blit(surf, (xo, yo))
                    with open("current_image.txt", "w") as f:
                        f.write(right_file)
                    rel = to_relative_cache_path(right_file)
                    with open(CURRENT_IMAGE_RIGHT, "w") as f:
                        f.write(rel)
                except Exception:
                    logging.exception(f"Fehler beim Anzeigen des rechten Bildes {right_file}")
                    display_message(right_surf, "Fehler beim Laden (rechts).", right_surf)
            elif mode_right == "info":
                font = pygame.font.SysFont(None, 36)
                info_text = get_device_info()
                y0 = 20
                for line in info_text.split('\n'):
                    text = font.render(line, True, (255, 255, 255))
                    right_surf.blit(text, (20, y0))
                    y0 += 40
                with open(CURRENT_IMAGE_RIGHT, "w") as f:
                    f.write("/static/infoscreen.jpg")
            else:
                display_message(right_surf, "Keine Bilder (rechts).", right_surf)
                with open(CURRENT_IMAGE_RIGHT, "w") as f:
                    f.write("/static/infoscreen.jpg")

            screen.blit(left_surf, (0, 0))
            screen.blit(right_surf, (left_w, 0))

        else:
            # Vollbild
            if mode == 'slideshow' and image_files:
                if time.time() - last_switch > display_duration:
                    if image_path.startswith('smb://'):
                        image_files = prefetch_smb_images(
                            image_path,
                            config.get('smb_username',''),
                            config.get('smb_password',''),
                            config.get('smb_domain','')
                        )
                    if image_files:
                        index = (index + 1) % len(image_files)
                    else:
                        mode = 'info'
                    last_switch = time.time()

                if mode == 'slideshow' and image_files:
                    image_file = image_files[index]
                try:
                    surf = pygame.image.load(image_file)
                    if rotation:
                        pil = Image.open(image_file)
                        rot = pil.rotate(rotation, expand=True)
                        p = os.path.join(os.path.dirname(image_file), f"rotated_{os.path.basename(image_file)}")
                        rot.save(p)
                        surf = pygame.image.load(p)
                    sw, sh = infoObject.current_w, infoObject.current_h
                    w_img, h_img = surf.get_size()
                    if stretch_images:
                        surf = pygame.transform.scale(surf, (sw, sh))
                        screen.blit(surf, (0, 0))
                    else:
                        scale = min(sw / w_img, sh / h_img)
                        nw, nh = int(w_img * scale), int(h_img * scale)
                        if (nw, nh) != (w_img, h_img):
                            surf = pygame.transform.scale(surf, (nw, nh))
                        xo = (sw - nw) // 2
                        yo = (sh - nh) // 2
                        screen.blit(surf, (xo, yo))
                    with open("current_image.txt", "w") as f:
                        f.write(image_file)
                    rel = to_relative_cache_path(image_file)
                    with open(CURRENT_IMAGE_FULLSCREEN, "w") as f:
                        f.write(rel)
                except Exception:
                    logging.exception(f"Fehler beim Anzeigen des Bildes {image_file}")
                    display_message(screen, "Fehler beim Laden der Bilder.", infoObject)
            elif mode == 'info':
                device_info = get_device_info()
                font = pygame.font.SysFont(None, 36)
                y0 = 50
                for line in device_info.split('\n'):
                    text = font.render(line, True, (255, 255, 255))
                    rect = text.get_rect(center=(infoObject.current_w // 2, y0))
                    screen.blit(text, rect)
                    y0 += 40
                with open("current_image.txt", "w") as f:
                    f.write("/static/infoscreen.jpg")
                with open(CURRENT_IMAGE_FULLSCREEN, "w") as f:
                    f.write("/static/infoscreen.jpg")
                pygame.display.flip()
                clock.tick(30)
                continue
            else:
                display_message(screen, "Keine Bilder gefunden.", infoObject)
                with open("current_image.txt", "w") as f:
                    f.write("/static/infoscreen.jpg")
                with open(CURRENT_IMAGE_FULLSCREEN, "w") as f:
                    f.write("/static/infoscreen.jpg")

        pygame.display.flip()
        clock.tick(30)


if __name__ == '__main__':
    if not os.path.exists(CONFIG_FILE):
        default_config = {
            "mode": "info",
            "split_screen": False,
            "mode_left": "slideshow",
            "mode_right": "slideshow",
            "image_path": "",
            "image_path_left": "",
            "image_path_right": "",
            "display_duration": 5,
            "rotation": 0,
            "smb_username": "",
            "smb_domain": "",
            "smb_password": "",
            "smb_username_left": "",
            "smb_domain_left": "",
            "smb_password_left": "",
            "smb_username_right": "",
            "smb_domain_right": "",
            "smb_password_right": "",
            "reload": False,
            "stretch_images": True,
            "log_level": "DEBUG"
        }
        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(default_config, f, indent=4)
            logging.info("Standardkonfigurationsdatei erstellt.")
        except Exception:
            logging.exception("Fehler beim Erstellen der Standardkonfigurationsdatei")
        sys.exit(1)

    main()
