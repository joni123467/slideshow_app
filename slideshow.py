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
import uuid
from logging.handlers import RotatingFileHandler
from PIL import Image

# SMB2/3 Imports
from smbprotocol.connection import Connection
from smbprotocol.session import Session
from smbprotocol.tree import TreeConnect
from smbprotocol.open import Open, CreateDisposition, FilePipePrinterAccessMask, DirectoryAccessMask, ShareAccess, CreateOptions
from smbprotocol.file_info import FileInformationClass

CONFIG_FILE = 'config.json'
CURRENT_IMAGE_FULLSCREEN = "current_image_fullscreen.txt"
CURRENT_IMAGE_LEFT = "current_image_left.txt"
CURRENT_IMAGE_RIGHT = "current_image_right.txt"

def set_loglevel_from_config(cfg):
    lvl_name = cfg.get("log_level", "INFO").upper()
    lvl = getattr(logging, lvl_name, logging.INFO)
    logger = logging.getLogger()
    logger.setLevel(lvl)
    for handler in logger.handlers:
        handler.setLevel(lvl)

def setup_logging(log_level="INFO"):
    root_logger = logging.getLogger()
    while root_logger.handlers:
        root_logger.handlers.pop()
    log_handler = RotatingFileHandler('slideshow.log', maxBytes=1048576, backupCount=3)
    formatter = logging.Formatter('%(asctime)s %(levelname)s [%(name)s]: %(message)s')
    log_handler.setFormatter(formatter)
    log_handler.setLevel(log_level)
    root_logger.addHandler(log_handler)
    root_logger.setLevel(log_level)

setup_logging("DEBUG") 

logging.warning("Logging-Test: WARNING")
logging.info("Logging-Test: INFO")
logging.debug("Logging-Test: DEBUG")

def normalize_smb_path(smb_path):
    if smb_path.startswith('\\\\') or smb_path.startswith('//'):
        path = smb_path.lstrip('\\/')
        parts = re.split(r'[\\/]', path, maxsplit=2)
        if len(parts) == 3:
            server, share, rest = parts
            return f"smb://{server}/{share}/{rest}"
        elif len(parts) == 2:
            server, share = parts
            return f"smb://{server}/{share}/"
        else:
            return smb_path
    return smb_path

def to_relative_cache_path(absolute_path):
    filename = os.path.basename(absolute_path)
    return f"/static/cache/{filename}"

def prefetch_smb2_images(smb_path, username, password, domain):
    import uuid
    from smbprotocol.connection import Connection
    from smbprotocol.session import Session
    from smbprotocol.tree import TreeConnect
    from smbprotocol.open import Open, CreateDisposition, FilePipePrinterAccessMask, DirectoryAccessMask, ShareAccess, CreateOptions
    from smbprotocol.file_info import FileInformationClass

    import os, re, logging

    smb_path = normalize_smb_path(smb_path)
    supported_extensions = ('.png', '.jpg', '.jpeg', '.gif', '.bmp')
    match = re.match(r'smb://([^/]+)/([^/]+)/(.*)', smb_path)
    if not match:
        logging.error(f"Ungültiges SMB-Pfadformat: {smb_path}")
        return []
    server, share, remote_path = match.groups()
    if remote_path.endswith('/'):
        remote_path = remote_path[:-1]
    cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'cache')
    os.makedirs(cache_dir, exist_ok=True)
    local_files = []
    try:
        logging.debug(f"Starte SMB-Connection zu {server}/{share}/{remote_path}")
        connection = Connection(uuid.uuid4(), server, 445)
        connection.connect()
        username_domain = f"{domain}\\{username}" if domain else username
        session = Session(connection, username_domain, password)
        session.connect()
        tree = TreeConnect(session, f"\\\\{server}\\{share}")
        tree.connect()
        # ACHTUNG: Pfad immer mit Backslashes!
        folder = Open(tree, remote_path.replace("/", "\\"))
        folder.create(
            impersonation_level=2,
            desired_access=DirectoryAccessMask.FILE_LIST_DIRECTORY,
            file_attributes=0,
            share_access=ShareAccess.FILE_SHARE_READ,
            create_disposition=CreateDisposition.FILE_OPEN,
            create_options=CreateOptions.FILE_DIRECTORY_FILE
        )
        files = folder.query_directory("*", FileInformationClass.FILE_DIRECTORY_INFORMATION)
        count = 0
        for f in files:
            name = f['file_name']
            if hasattr(name, 'get_value'):
                name = name.get_value()
            if isinstance(name, bytes):
                name = name.decode('utf-16-le').rstrip('\x00')
            if name in ('.', '..') or not name.lower().endswith(supported_extensions):
                logging.debug(f"Überspringe Datei/Ordner: {name}")
                continue
            # Hier wird der Pfad korrekt gebaut:
            smb_file_path = (remote_path + "\\" + name) if remote_path else name
            smb_file_path = smb_file_path.replace("/", "\\").replace("\\\\", "\\")
            logging.warning(f"Open(tree, {smb_file_path!r}) für Download.")
            cache_path = os.path.join(cache_dir, name)
            try:
                file_open = Open(tree, smb_file_path)
                file_open.create(
                    impersonation_level=2,
                    desired_access=FilePipePrinterAccessMask.FILE_READ_DATA,
                    file_attributes=0,
                    share_access=ShareAccess.FILE_SHARE_READ,
                    create_disposition=CreateDisposition.FILE_OPEN,
                    create_options=0
                )
                size = f['end_of_file']
                if hasattr(size, 'get_value'):
                    size = size.get_value()
                if size > 0:
                    data = file_open.read(0, size)
                    with open(cache_path, 'wb') as out:
                        out.write(data)
                    logging.debug(f"Datei geladen: {name}, {size} Bytes")
                file_open.close()
                local_files.append(cache_path)
                count += 1
            except Exception:
                logging.warning(f"Fehler beim Laden der Datei {smb_file_path}", exc_info=True)
        folder.close()
        tree.disconnect()
        session.disconnect()
        connection.disconnect()
        logging.info(f"SMB2/3-Prefetch abgeschlossen: {count} Dateien heruntergeladen.")
    except Exception:
        logging.exception(f"Fehler beim Zugriff auf SMB2/3: {server}/{share}/{remote_path}")
    return local_files



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
            logging.debug("Konfigurationsdatei erfolgreich geladen.")
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
                    logging.debug(f"Ermittelte IPv4-Adresse: {ip}")
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
        logging.warning("Fehler beim Lesen von /proc/meminfo", exc_info=True)
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
        if path.startswith('smb://') or path.startswith('\\\\') or path.startswith('//'):
            imgs = prefetch_smb2_images(
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
        if left.startswith('smb://') or left.startswith('\\\\') or left.startswith('//'):
            left_imgs = prefetch_smb2_images(
                left,
                cfg.get('smb_username_left', ''),
                cfg.get('smb_password_left', ''),
                cfg.get('smb_domain_left', '')
            )
        else:
            left_imgs = get_local_image_files(left)
        if right.startswith('smb://') or right.startswith('\\\\') or right.startswith('//'):
            right_imgs = prefetch_smb2_images(
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
    config = load_config()
    set_loglevel_from_config(config)
    lvl_name = config.get("log_level", "INFO").upper()
    setup_logging(lvl_name)
    logging.info(f"Log-Level auf {lvl_name} gesetzt")
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
                set_loglevel_from_config(current_config)
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
                if left_path.startswith('smb://') or left_path.startswith('\\\\') or left_path.startswith('//'):
                    left_images = prefetch_smb2_images(
                        left_path,
                        config.get('smb_username_left',''),
                        config.get('smb_password_left',''),
                        config.get('smb_domain_left','')
                    )
                if right_path.startswith('smb://') or right_path.startswith('\\\\') or right_path.startswith('//'):
                    right_images = prefetch_smb2_images(
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
                    if image_path.startswith('smb://') or image_path.startswith('\\\\') or image_path.startswith('//'):
                        image_files = prefetch_smb2_images(
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
    # **Achtung:** Logging wird jetzt im Main gesetzt!
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
            # Minimal Logging, da setup_logging noch nicht gesetzt ist!
            print("Standardkonfigurationsdatei erstellt.")
        except Exception as e:
            print("Fehler beim Erstellen der Standardkonfigurationsdatei:", e)
        sys.exit(1)
    main()
