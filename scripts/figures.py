"""Dessin des schémas de panneaux (fictifs) et des photos de test simulées.

Toutes les figures sont générées par code : aucun visuel constructeur n'est utilisé.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

GREEN, ORANGE, RED, YELLOW, BLUE = '#2ecc40', '#ff6a00', '#e3242b', '#ffd400', '#2f7bff'
OFF = '#3a3d42'
BODY, BAY, PANEL_TXT = '#2b2f36', '#1b1e23', '#e8e8e8'
COLORS = {'vert': GREEN, 'orange': ORANGE, 'rouge': RED, 'jaune': YELLOW, 'bleu': BLUE, None: OFF}


def font(size: int, bold: bool = False):
    names = ['arialbd.ttf', 'DejaVuSans-Bold.ttf'] if bold else ['arial.ttf', 'DejaVuSans.ttf']
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


class Canvas:
    """Petit utilitaire : image + liste des voyants allumés (pour l'effet de halo)."""

    def __init__(self, w, h, bg='#f2f2f2'):
        self.img = Image.new('RGB', (w, h), bg)
        self.d = ImageDraw.Draw(self.img)
        self.lit: list[tuple[int, int, str]] = []

    def led(self, x, y, color_name, r=9):
        color = COLORS[color_name]
        self.d.ellipse((x - r - 2, y - r - 2, x + r + 2, y + r + 2), fill='#111')
        self.d.ellipse((x - r, y - r, x + r, y + r), fill=color)
        if color_name:
            self.d.ellipse((x - r // 2, y - r // 2, x - r // 6, y - r // 6), fill='#ffffff')
            self.lit.append((x, y, color))

    def text(self, xy, s, size=16, fill=PANEL_TXT, bold=False, anchor='la'):
        self.d.text(xy, s, font=font(size, bold), fill=fill, anchor=anchor)

    def finish(self, glow=0.6, radius=22):
        if not self.lit or glow <= 0:
            return self.img
        layer = Image.new('RGB', self.img.size, 'black')
        g = ImageDraw.Draw(layer)
        for x, y, c in self.lit:
            g.ellipse((x - radius, y - radius, x + radius, y + radius), fill=c)
        layer = layer.filter(ImageFilter.GaussianBlur(radius / 1.6))
        base = np.asarray(self.img, dtype=np.float32)
        add = np.asarray(layer, dtype=np.float32) * glow
        return Image.fromarray(np.clip(base + add, 0, 255).astype(np.uint8))


def caption_bar(c: Canvas, s: str):
    w, h = c.img.size
    c.d.rectangle((0, h - 34, w, h), fill='#dcdcdc')
    c.text((w // 2, h - 17), s, size=15, fill='#333', anchor='mm')


# ---------------------------------------------------------------- SAN-LAB 2000
def san2000_front(disk_state: dict[str, str] | None = None, pwr='vert', sys='vert',
                  fault: str | None = None, labels=True, caption=True):
    disk_state = disk_state or {}
    c = Canvas(1100, 470)
    c.d.rounded_rectangle((30, 50, 1070, 400), 14, fill=BODY)
    c.d.rectangle((30, 50, 60, 400), fill='#1f2228'); c.d.rectangle((1040, 50, 1070, 400), fill='#1f2228')
    for i in range(12):
        col, row = i % 4, i // 4
        x0, y0 = 80 + col * 175, 75 + row * 105
        c.d.rounded_rectangle((x0, y0, x0 + 160, y0 + 92), 6, fill=BAY, outline='#555')
        for k in range(6):
            c.d.line((x0 + 12, y0 + 18 + k * 11, x0 + 105, y0 + 18 + k * 11), fill='#30343a', width=3)
        bay = f'B{i + 1:02d}'
        c.text((x0 + 12, y0 + 70), bay, size=15)
        c.led(x0 + 132, y0 + 26, 'vert' if bay not in disk_state else 'vert', r=7)
        c.led(x0 + 132, y0 + 60, disk_state.get(bay), r=7)
    px = 800
    c.d.rounded_rectangle((px, 75, 1020, 375), 8, fill='#22262c', outline='#555')
    c.text((px + 110, 105), 'SAN-LAB 2000', size=22, bold=True, anchor='mm')
    for j, (name, state) in enumerate([('PWR', pwr), ('SYS', sys), ('FAULT', fault)]):
        x = px + 45 + j * 65
        c.led(x, 200, state, r=13)
        if labels:
            c.text((x, 232), name, size=15, anchor='mm')
    c.text((px + 110, 300), 'CTRL A | CTRL B', size=15, anchor='mm', fill='#aaa')
    if labels:
        c.text((80, 18), 'Emplacements disques B01 à B12 : LED haute = activité, LED basse = état',
               size=16, fill='#222')
    if caption:
        caption_bar(c, 'Vue avant schématique - SAN-LAB 2000 (équipement fictif)')
    return c


def disk_tray():
    c = Canvas(1000, 520)
    c.d.rounded_rectangle((120, 140, 760, 330), 12, fill='#3c4148', outline='#222', width=3)
    for k in range(9):
        c.d.line((160, 170 + k * 16, 560, 170 + k * 16), fill='#2a2e33', width=5)
    c.d.rounded_rectangle((590, 155, 745, 315), 8, fill='#555b63', outline='#222', width=2)
    c.d.rectangle((600, 290, 735, 308), fill='#c9a227')
    c.led(700, 190, 'vert', r=9); c.led(700, 235, 'orange', r=9)
    c.d.rounded_rectangle((610, 255, 650, 285), 4, fill='#9aa0a6', outline='#222')
    c.d.rectangle((160, 300, 300, 322), fill='white'); c.text((168, 302), 'B03 / DSK-LAB-1.2T', size=14, fill='#111')
    callouts = [((700, 190), (850, 110), 'LED activité (verte)'),
                ((700, 235), (850, 200), 'LED état (orange = pré-alerte)'),
                ((630, 270), (850, 300), 'Bouton de déverrouillage'),
                ((668, 300), (850, 390), 'Levier d\'extraction'),
                ((230, 311), (200, 420), 'Étiquette emplacement + référence')]
    for (x, y), (tx, ty), s in callouts:
        c.d.line((x, y, tx - 10, ty + 10), fill='#c00', width=2)
        c.text((tx - 5, ty), s, size=17, fill='#111')
    c.text((120, 60), 'Tiroir disque SAN-LAB 2000 - vue de face', size=24, fill='#111', bold=True)
    caption_bar(c, 'Schéma didactique - ne correspond à aucun matériel réel')
    return c


# ---------------------------------------------------------------- SAN-LAB 3000
def san3000_front(pwr='vert', fan=None, temp=None, fault=None, disk_state=None, caption=True):
    disk_state = disk_state or {}
    c = Canvas(1100, 420)
    c.d.rounded_rectangle((30, 50, 1070, 350), 14, fill='#30343b')
    c.d.rectangle((30, 50, 170, 350), fill='#1f2228')
    c.text((100, 75), 'SAN-LAB', size=17, bold=True, anchor='mm'); c.text((100, 97), '3000', size=17, bold=True, anchor='mm')
    for j, (name, st) in enumerate([('PWR', pwr), ('FAN', fan), ('TEMP', temp), ('FAULT', fault)]):
        y = 140 + j * 52
        c.led(70, y, st, r=11)
        c.text((92, y), name, size=16, anchor='lm')
    for i in range(24):
        col, row = i % 12, i // 12
        x0, y0 = 185 + col * 72, 70 + row * 140
        c.d.rounded_rectangle((x0, y0, x0 + 64, y0 + 128), 5, fill=BAY, outline='#555')
        for k in range(5):
            c.d.line((x0 + 12, y0 + 12 + k * 12, x0 + 12, y0 + 75), fill='#30343a', width=3)
        bay = f'D{i + 1:02d}'
        c.text((x0 + 32, y0 + 112), bay, size=13, anchor='mm')
        c.led(x0 + 45, y0 + 20, disk_state.get(bay), r=6)
    if caption:
        caption_bar(c, 'Vue avant schématique - SAN-LAB 3000 (24 emplacements D01-D24, équipement fictif)')
    return c


def cabling():
    c = Canvas(1100, 710, bg='white')
    box = lambda xy, s, fill: (c.d.rounded_rectangle(xy, 10, fill=fill, outline='#222', width=2),
                               c.text(((xy[0] + xy[2]) // 2, (xy[1] + xy[3]) // 2), s, size=18, fill='#111', anchor='mm', bold=True))
    box((120, 40, 360, 120), 'Hôte H1 (HBA1 / HBA2)', '#e8eef7')
    box((740, 40, 980, 120), 'Hôte H2 (HBA1 / HBA2)', '#e8eef7')
    box((140, 250, 420, 330), 'SW-FC-A (Fabric A)', '#cfe2ff')
    box((680, 250, 960, 330), 'SW-FC-B (Fabric B)', '#d4f5d4')
    c.d.rounded_rectangle((120, 440, 980, 640), 12, outline='#222', width=3, fill='#f7f7f7')
    c.text((550, 615), 'Baie SAN-LAB (2000 ou 3000)', size=18, fill='#111', anchor='mm', bold=True)
    box((180, 490, 480, 580), 'Contrôleur A / CTRL-1', '#eeeeee')
    box((620, 490, 920, 580), 'Contrôleur B / CTRL-2', '#eeeeee')
    blue, green = '#1f5fd6', '#1c9c3a'
    for x in (200, 300):
        c.d.line((x, 120, 280, 250), fill=blue if x == 200 else green, width=4)
    c.d.line((300, 120, 780, 250), fill=green, width=4)
    c.d.line((800, 120, 300, 250), fill=blue, width=4)
    c.d.line((900, 120, 860, 250), fill=green, width=4)
    for x0 in (250, 380):
        c.d.line((280, 330, x0, 490), fill=blue, width=4)
    for x0 in (700, 860):
        c.d.line((820, 330, x0, 490), fill=green, width=4)
    c.d.line((280, 330, 700, 490), fill=blue, width=4)
    c.d.line((820, 330, 380, 490), fill=green, width=4)
    c.text((30, 665), 'Bleu : Fabric A   Vert : Fabric B   Alimentation : PSU1 vers PDU-A, PSU2 vers PDU-B (circuits distincts)',
           size=17, fill='#111')
    return c


# ---------------------------------------------------------------- Onduleurs
def ups500_front(en_ligne='vert', batterie=None, defaut=None, bypass=None, lcd='EN LIGNE  230V  45%', caption=True):
    c = Canvas(620, 860)
    c.d.rounded_rectangle((110, 40, 510, 790), 22, fill='#25282d')
    c.d.rounded_rectangle((150, 90, 470, 200), 8, fill='#9fd0a8', outline='#111', width=4)
    c.text((310, 145), lcd, size=24, fill='#0b2a12', anchor='mm', bold=True)
    for j, (name, st) in enumerate([('EN LIGNE', en_ligne), ('BATTERIE', batterie), ('DÉFAUT', defaut), ('BYPASS', bypass)]):
        y = 270 + j * 70
        c.led(190, y, st, r=14)
        c.text((225, y), name, size=21, anchor='lm')
    for j, s in enumerate(['ON / OFF', 'TEST / SILENCE']):
        x = 210 + j * 200
        c.d.ellipse((x - 32, 600 - 32, x + 32, 600 + 32), fill='#555', outline='#111', width=3)
        c.text((x, 655), s, size=16, anchor='mm')
    c.text((310, 740), 'UPS-X500', size=30, bold=True, anchor='mm')
    if caption:
        caption_bar(c, 'Façade UPS-X500 (onduleur fictif)')
    return c


def battery_compartment():
    c = Canvas(1000, 560, bg='white')
    c.d.rounded_rectangle((80, 80, 560, 470), 16, fill='#2b2f36', outline='#111', width=3)
    c.d.rectangle((120, 150, 520, 420), fill='#111')
    c.d.rounded_rectangle((150, 180, 490, 390), 8, fill='#4b5563', outline='#ddd', width=2)
    c.text((320, 250), 'BAT-X5', size=30, bold=True, anchor='mm')
    c.text((320, 290), '4 x 12 V 9 Ah', size=20, anchor='mm')
    c.d.rectangle((180, 330, 400, 365), fill='white'); c.text((190, 337), 'Installée : ____ / Lot : ____', size=16, fill='#111')
    c.d.rectangle((455, 200, 485, 240), fill='#d33'); c.d.rectangle((455, 245, 485, 285), fill='#111', outline='#ddd')
    for y in (165, 405):
        c.d.ellipse((128, y - 9, 146, y + 9), fill='#bbb'); c.d.ellipse((494, y - 9, 512, y + 9), fill='#bbb')
    callouts = [((470, 240), (640, 170), 'Connecteur batterie (rouge / noir)'),
                ((137, 165), (640, 260), 'Vis de fixation de la trappe (x4)'),
                ((290, 348), (640, 350), 'Étiquette date d\'installation et lot'),
                ((320, 200), (640, 440), 'Bloc batterie remplaçable BAT-X5')]
    for (x, y), (tx, ty), s in callouts:
        c.d.line((x, y, tx - 10, ty + 10), fill='#c00', width=2)
        c.text((tx - 5, ty), s, size=18, fill='#111')
    c.text((80, 30), 'UPS-X500 - compartiment batterie (façade retirée)', size=24, fill='#111', bold=True)
    return c


def ups900_front(secteur='vert', batt=None, surcharge=None, alarme=None, segments=2, seg_color='vert',
                 lcd='F-01  ON LINE', caption=True):
    c = Canvas(1100, 400)
    c.d.rounded_rectangle((30, 50, 1070, 330), 14, fill='#2a2d33')
    c.text((80, 95), 'UPS-X900', size=28, bold=True)
    c.d.rounded_rectangle((80, 140, 330, 220), 6, fill='#9fc3e8', outline='#111', width=3)
    c.text((205, 180), lcd, size=22, fill='#08213b', anchor='mm', bold=True)
    for j, (name, st) in enumerate([('SECTEUR', secteur), ('BATT', batt), ('SURCHARGE', surcharge), ('ALARME', alarme)]):
        x = 420 + j * 120
        c.led(x, 150, st, r=14)
        c.text((x, 190), name, size=17, anchor='mm')
    c.text((790, 245), 'CHARGE', size=15, anchor='mm', fill='#bbb')
    for k in range(5):
        x0 = 420 + k * 72
        c.d.rectangle((x0, 260, x0 + 60, 285), fill=COLORS[seg_color] if k < segments else OFF, outline='#111')
        c.text((x0 + 30, 300), f'{(k + 1) * 20}%', size=13, anchor='mm', fill='#bbb')
    c.d.rectangle((900, 130, 1030, 300), fill='#1f2228', outline='#555')
    for k in range(8):
        c.d.line((915, 145 + k * 19, 1015, 145 + k * 19), fill='#3a3d42', width=6)
    if caption:
        caption_bar(c, 'Façade UPS-X900 rack 2U (onduleur fictif) - voyants de gauche à droite')
    return c


def flowchart(title: str, steps: list[tuple[str, str]]):
    """steps : liste (texte, couleur). Les étapes sont reliées verticalement."""
    h = 120 + len(steps) * 95
    c = Canvas(900, h, bg='white')
    c.text((450, 40), title, size=24, fill='#111', bold=True, anchor='mm')
    for i, (s, fill) in enumerate(steps):
        y0 = 80 + i * 95
        c.d.rounded_rectangle((150, y0, 750, y0 + 62), 12, fill=fill, outline='#222', width=2)
        c.text((450, y0 + 31), s, size=19, fill='#111', anchor='mm')
        if i < len(steps) - 1:
            c.d.line((450, y0 + 62, 450, y0 + 95), fill='#222', width=3)
            c.d.polygon([(440, y0 + 85), (460, y0 + 85), (450, y0 + 95)], fill='#222')
    return c


def psu_module():
    c = Canvas(900, 460, bg='white')
    c.d.rounded_rectangle((120, 90, 620, 330), 14, fill='#3a3f46', outline='#111', width=3)
    for k in range(10):
        c.d.ellipse((160 + k * 34, 140, 184 + k * 34, 164), fill='#22262b')
        c.d.ellipse((160 + k * 34, 190, 184 + k * 34, 214), fill='#22262b')
    c.d.rectangle((480, 240, 590, 300), fill='#111', outline='#888')
    c.text((535, 270), 'AC IN', size=16, anchor='mm')
    c.led(200, 280, 'vert', r=12)
    c.d.rounded_rectangle((560, 110, 600, 220), 8, fill='#888', outline='#111')
    c.text((370, 60), 'Module PSU-LAB-650 (vue arrière)', size=22, fill='#111', bold=True, anchor='mm')
    for (x, y), (tx, ty), s in [((200, 280), (650, 330), 'Voyant d\'état'), ((535, 270), (650, 270), 'Entrée secteur'),
                                ((580, 165), (650, 160), 'Poignée d\'extraction')]:
        c.d.line((x, y, tx - 10, ty + 10), fill='#c00', width=2)
        c.text((tx - 5, ty), s, size=18, fill='#111')
    return c


def battery_x9():
    c = Canvas(1000, 480, bg='white')
    c.d.rounded_rectangle((80, 120, 640, 400), 12, fill='#2a2d33', outline='#111', width=3)
    c.d.rectangle((110, 160, 610, 370), fill='#4b5563', outline='#ddd', width=2)
    for i in range(8):
        for j in range(2):
            c.d.rectangle((125 + i * 60, 175 + j * 95, 175 + i * 60, 255 + j * 95), fill='#374151', outline='#9ca3af')
    c.d.rounded_rectangle((300, 380, 420, 400), 4, fill='#c9a227')
    c.text((360, 80), 'Tiroir BAT-X9 (UPS-X900)', size=24, fill='#111', bold=True, anchor='mm')
    for (x, y), (tx, ty), s in [((360, 390), (680, 380), 'Poignée (manipulation à deux)'),
                                ((600, 200), (680, 200), '16 blocs 12 V 7 Ah'), ((620, 300), (680, 290), 'Verrou du tiroir')]:
        c.d.line((x, y, tx - 10, ty + 10), fill='#c00', width=2)
        c.text((tx - 5, ty), s, size=18, fill='#111')
    return c


EXTRA_FLOWS = {
    'flow_fw': ('Mise à jour du micrologiciel SAN-LAB 2000', [
        ('Prérequis TAB-02 vérifiés + ticket approuvé', '#e8eef7'), ('Mise à jour du contrôleur B', '#e8eef7'),
        ('SYS vert fixe et chemins FC rétablis ?', '#fff3cd'), ('Mise à jour du contrôleur A', '#e8eef7'),
        ('Contrôle final et documentation', '#d4edda')]),
    'flow_mpio': ('Perte de chemin : diagnostic documentaire', [
        ('Identifier le volume et l\'hôte affectés', '#e8eef7'), ('Compter les chemins restants par fabric', '#e8eef7'),
        ('Un seul chemin restant ?', '#fff3cd'), ('Oui : escalade (redondance perdue)', '#f8d7da'),
        ('Non : brouillon de ticket + approbation', '#d4edda')]),
    'flow_fan': ('Remplacement d\'un ventilateur SAN-LAB 3000', [
        ('Identifier FAN-x (E-310 / AL-31)', '#e8eef7'), ('Ticket approuvé par le responsable', '#e8eef7'),
        ('Module neuf préparé', '#e8eef7'), ('Échange en moins de 2 minutes', '#fff3cd'),
        ('Voyant FAN éteint, E-310 disparu', '#d4edda')]),
    'flow_temp': ('Alerte température SAN-LAB 3000', [
        ('Lire le voyant TEMP et le code', '#e8eef7'), ('Orange (AL-32) : vérifier la salle', '#fff3cd'),
        ('Rouge (AL-33) : escalade immédiate', '#f8d7da'), ('Pas de baisse en 30 min : escalade', '#f8d7da')]),
}


# ---------------------------------------------------------------- Photos simulées
def photoize(panel: Image.Image, angle: float, blur: float, seed: int, brightness=0.85,
             crop: tuple[int, int, int, int] | None = None, noise=10.0, shear=0.06) -> Image.Image:
    rng = np.random.default_rng(seed)
    if crop:
        panel = panel.crop(crop)
    w, h = panel.size
    bg = Image.new('RGB', (w + 260, h + 260), '#3a3632')
    bg.paste(panel, (130, 130))
    bg = bg.transform(bg.size, Image.AFFINE, (1, shear, -shear * bg.size[1] / 2, 0.02, 1, -10),
                      resample=Image.BICUBIC, fillcolor='#3a3632')
    bg = bg.rotate(angle, resample=Image.BICUBIC, expand=False, fillcolor='#2e2a27')
    bg = ImageEnhance.Brightness(bg).enhance(brightness)
    bg = ImageEnhance.Color(bg).enhance(1.1)
    bg = bg.filter(ImageFilter.GaussianBlur(blur))
    arr = np.asarray(bg, dtype=np.float32)
    arr += rng.normal(0, noise, arr.shape)
    # vignettage
    yy, xx = np.mgrid[0:arr.shape[0], 0:arr.shape[1]]
    cy, cx = arr.shape[0] / 2, arr.shape[1] / 2
    v = 1 - 0.35 * (((yy - cy) / cy) ** 2 + ((xx - cx) / cx) ** 2)
    arr *= v[..., None]
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


def build_all(fig_dir: Path, photo_dir: Path) -> None:
    fig_dir.mkdir(parents=True, exist_ok=True)
    photo_dir.mkdir(parents=True, exist_ok=True)
    figs = {
        'san2000_alerte': san2000_front({'B03': 'orange'}, fault='orange'),
        'san2000_nominal': san2000_front(),
        'disk_tray': disk_tray(),
        'san3000_front': san3000_front(fan='orange', disk_state={'D14': 'bleu'}),
        'cablage': cabling(),
        'ups500_front': ups500_front(en_ligne=None, batterie='orange', lcd='ON BATT  12 min'),
        'battery': battery_compartment(),
        'ups900_front': ups900_front(surcharge='orange', segments=5, seg_color='orange', lcd='F-05  OVERLOAD'),
        'flow_san3000': flowchart('SAN-LAB 3000 - analyse documentaire d\'un événement', [
            ('Relever le code E-3xx et l\'horodatage', '#e8eef7'),
            ('Identifier le composant (CTRL, FAN, TEMP, DSK, LINK)', '#e8eef7'),
            ('Consulter TAB-01 et l\'historique de l\'équipement', '#e8eef7'),
            ('Sévérité critique ou récurrence ?', '#fff3cd'),
            ('Oui : escalade au responsable SAN', '#f8d7da'),
            ('Non : brouillon de ticket + approbation humaine', '#d4edda')]),
        'flow_ups900': flowchart('UPS-X900 - conduite à tenir sur alarme', [
            ('Lire le code F-xx sur l\'écran LCD', '#e8eef7'),
            ('Identifier le voyant allumé (position 1 à 4)', '#e8eef7'),
            ('Consulter TAB-01 (UPS-ERR-031)', '#e8eef7'),
            ('Sévérité critique ?', '#fff3cd'),
            ('Oui : escalade immédiate, ne pas réarmer', '#f8d7da'),
            ('Non : brouillon de ticket + approbation humaine', '#d4edda')]),
    }
    figs['psu_module'] = psu_module()
    figs['battery_x9'] = battery_x9()
    for key, (title, steps) in EXTRA_FLOWS.items():
        figs[key] = flowchart(title, steps)
    for name, c in figs.items():
        c.finish(glow=0.35).save(fig_dir / f'{name}.png')

    photos = {
        'photo_san2000_fault_orange.jpg': (san2000_front({'B03': 'orange'}, fault='orange', labels=True, caption=False), -5, 1.3, 1, None),
        'photo_san3000_fan_orange.jpg': (san3000_front(fan='orange', caption=False), 4, 1.4, 2, None),
        'photo_ups500_batterie_orange.jpg': (ups500_front(en_ligne=None, batterie='orange', lcd='ON BATT  9 min', caption=False), 6, 1.2, 3, None),
        'photo_ups900_surcharge_orange.jpg': (ups900_front(surcharge='orange', segments=5, seg_color='orange', lcd='F-05  OVERLOAD', caption=False), -3, 1.3, 4, None),
        'photo_ups900_alarme_rouge.jpg': (ups900_front(alarme='rouge', lcd='F-10  FAULT', caption=False), 2, 1.2, 5, None),
        # Photo volontairement ambiguë : gros plan flou d'un voyant orange, sans étiquette lisible.
        'photo_ambigue_voyant_orange.jpg': (ups900_front(batt='orange', caption=False, lcd=''), 9, 6.0, 6, (360, 100, 620, 230)),
    }
    for name, (c, angle, blur, seed, crop) in photos.items():
        img = c.finish(glow=0.9, radius=30)
        arr = np.asarray(img).copy()
        arr[(arr > 225).all(axis=-1)] = (58, 54, 50)  # fond clair -> mur sombre
        img = Image.fromarray(arr)
        if crop:
            img = img.crop(crop).resize(((crop[2] - crop[0]) * 3, (crop[3] - crop[1]) * 3), Image.BICUBIC)
        photoize(img, angle, blur, seed).save(photo_dir / name, quality=85)
