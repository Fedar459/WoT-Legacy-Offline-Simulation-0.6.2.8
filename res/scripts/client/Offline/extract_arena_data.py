# -*- coding: utf-8 -*-
"""extract_arena_data.py"""

import os
import re
import sys
import zipfile

KNOWN_MAPS = [
    '01_karelia', '02_malinovka', '04_himmelsdorf', '05_prohorovka',
    '06_ensk', '07_lakeville', '11_murovanka',
]

KNOWN_GOOD_BASES = {
    '05_prohorovka': {1: (0.0, -450.0), 2: (0.0, 450.0)},
    '06_ensk': {1: (0.0, -250.0), 2: (0.0, 250.0)},
}

NUM_RE = re.compile(r'-?\d+\.?\d*(?:[eE]-?\d+)?')
_DEFAULT_BASE_RADIUS_FALLBACK = 50.0

class Node(object):
    __slots__ = ('tag', 'text', 'children', 'parent')

    def __init__(self, tag, parent=None):
        self.tag = tag
        self.text = u''
        self.children = []
        self.parent = parent

    def find_all(self, pred):
        out = []
        for c in self.children:
            if pred(c.tag):
                out.append(c)
            out.extend(c.find_all(pred))
        return out

    def full_text(self):
        """Собственный текст узла + текст всех потомков (для случая, когда"""
        parts = [self.text]
        for c in self.children:
            parts.append(c.full_text())
        return u' '.join(parts)

    def path(self):
        parts = []
        n = self
        while n is not None and n.tag != '__root__':
            parts.append(n.tag)
            n = n.parent
        return '/'.join(reversed(parts))

def parse_bw_xml(text):
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    pos = 0
    root = Node('__root__')
    stack = [root]
    for m in re.finditer(r'<(/?)([^!?/>][^>]*?)>', text, re.DOTALL):
        is_close = m.group(1) == '/'
        raw_tag = m.group(2).strip()
        start, end = m.span()
        between = text[pos:start]
        if between.strip():
            stack[-1].text += between
        pos = end
        if is_close:
            if len(stack) > 1:
                stack.pop()
        else:
            node = Node(raw_tag, parent=stack[-1])
            stack[-1].children.append(node)
            stack.append(node)
    return root

def numbers(s):
    return [float(x) for x in NUM_RE.findall(s)]

def find_loose(wot_root):
    found = {}
    for dirpath, dirnames, filenames in os.walk(wot_root):
        for fn in filenames:
            if fn.lower() == 'arena.xml':
                full = os.path.join(dirpath, fn)
                map_id = os.path.basename(dirpath).lower()
                found.setdefault(map_id, []).append(('file', full, None))
    return found

def find_in_packages(wot_root):
    found = {}
    for dirpath, dirnames, filenames in os.walk(wot_root):
        for fn in filenames:
            if not fn.lower().endswith(('.pkg', '.zip')):
                continue
            full = os.path.join(dirpath, fn)
            try:
                with zipfile.ZipFile(full, 'r') as zf:
                    for name in zf.namelist():
                        if name.lower().endswith('/arena.xml') or name.lower().endswith('\\arena.xml'):
                            map_id = name.replace('\\', '/').split('/')[-2].lower()
                            found.setdefault(map_id, []).append(('zip', full, name))
            except (zipfile.BadZipFile, OSError):
                continue
    return found

def _leaf_points(node, min_nums=2, max_nums=3):
    """Числа из СОБСТВЕННОГО текста узла (без учёта текста потомков) - для"""
    if node.children:
        return []
    nums = numbers(node.text)
    if min_nums <= len(nums) <= max_nums:
        return [nums]
    return []

def _spawn_points_of(node):
    """Достаёт координаты точек спавна из узла, название которого совпало с"""
    pts = _leaf_points(node, 2, 3)
    if pts:
        return pts
    out = []
    for c in node.children:
        out.extend(_leaf_points(c, 2, 3))
    return out

def _base_pos_and_radius_of(node):
    """Достаёт (позиция, radius) из узла, название которого совпало с"""
    pos, radius = None, None
    direct_nums = numbers(node.text) if not node.children else []
    if len(direct_nums) in (2, 3):
        pos = direct_nums
    for c in node.children:
        tl = c.tag.lower()
        if radius is None and 'radius' in tl:
            rn = numbers(c.full_text())
            if rn: radius = rn[0]
        elif pos is None and any(k in tl for k in ('pos', 'point', 'location', 'coord')):
            pn = numbers(c.full_text())
            if len(pn) in (2, 3): pos = pn
    if pos is None:
        own = numbers(node.text)
        if len(own) in (2, 3): pos = own
    return pos, radius

def collect_points_grouped_by_team(matches, kind):
    """matches: список Node, чьё имя содержит ключевое слово ('spawn' или"""
    grouped = {}
    radii = {}
    for node in matches:
        team = None
        n = node
        while n is not None:
            m = re.search(r'team\D*(\d+)', n.tag, re.IGNORECASE)
            if m:
                team = int(m.group(1))
                break
            n = n.parent
        key = team if team is not None else '?'
        grouped.setdefault(key, [])

        if kind == 'spawn':
            for pt in _spawn_points_of(node):
                grouped[key].append(pt)
        else:
            pos, radius = _base_pos_and_radius_of(node)
            if pos:
                grouped[key].append(pos)
            if radius is not None:
                radii.setdefault(key, radius)
    return grouped, radii

def extract_tag_structure(root, max_depth=6):
    """Плоский список уникальных путей тегов - для ручной проверки, если"""
    paths = set()

    def walk(node, depth):
        if depth > max_depth:
            return
        if node.tag != '__root__':
            paths.add(node.path())
        for c in node.children:
            walk(c, depth + 1)

    walk(root, 0)
    return sorted(paths)

def analyze_arena_xml(map_id, text, report):
    report.write("\n" + "=" * 70 + "\n")
    report.write("КАРТА: %s\n" % map_id)
    report.write("=" * 70 + "\n")

    root = parse_bw_xml(text)

    structure = extract_tag_structure(root)
    report.write("\n--- Все пути тегов (первые 200) ---\n")
    for p in structure[:200]:
        report.write("  " + p + "\n")
    if len(structure) > 200:
        report.write("  ... ещё %d путей (обрезано)\n" % (len(structure) - 200))

    spawn_nodes = root.find_all(lambda t: 'spawn' in t.lower())
    base_nodes = root.find_all(lambda t: any(k in t.lower() for k in ('base', 'control', 'capture')))
    radius_nodes = root.find_all(lambda t: 'radius' in t.lower())

    report.write("\n--- Найдено эвристикой ---\n")
    report.write("  тегов со словом 'spawn': %d\n" % len(spawn_nodes))
    report.write("  тегов со словом 'base'/'control'/'capture': %d\n" % len(base_nodes))
    report.write("  тегов со словом 'radius': %d\n" % len(radius_nodes))

    spawns_grouped, _ = collect_points_grouped_by_team(spawn_nodes, 'spawn')
    bases_grouped, base_radii = collect_points_grouped_by_team(base_nodes, 'base')

    report.write("\n--- Готовый кусок для _MAP_SPAWNS (ПРОВЕРЬТЕ перед вставкой!) ---\n")
    if spawns_grouped:
        report.write("    '%s': {\n" % map_id)
        for team, pts in sorted(spawns_grouped.items(), key=lambda kv: str(kv[0])):
            pts3 = [p for p in pts if len(p) == 3][:30]
            report.write("        %r: %s,\n" % (team, pts3))
        report.write("    },\n")
    else:
        report.write("    (ничего не найдено эвристикой - смотрите карту тегов выше)\n")

    report.write("\n--- Готовый кусок для _BASE_ZONES (ПРОВЕРЬТЕ перед вставкой!) ---\n")
    if bases_grouped:
        report.write("    '%s': {\n" % map_id)
        for team, pts in sorted(bases_grouped.items(), key=lambda kv: str(kv[0])):
            if pts:
                x, z = pts[0][0], pts[0][-1]
                r = base_radii.get(team, _DEFAULT_BASE_RADIUS_FALLBACK)
                report.write("        %r: {'pos': Math.Vector3(%.2f, 0, %.2f), 'radius': %.1f},\n" % (
                    team, x, z, r))
        report.write("    },\n")
    else:
        report.write("    (ничего не найдено эвристикой - смотрите карту тегов выше)\n")

    if base_radii:
        report.write("\n  Найденные значения radius по командам: %s\n" % base_radii)

    if map_id in KNOWN_GOOD_BASES:
        report.write("\n--- САМОПРОВЕРКА (эта карта уже есть в _BASE_ZONES, сверяем) ---\n")
        expected = KNOWN_GOOD_BASES[map_id]
        ok = True
        for team, (ex, ez) in expected.items():
            found_pt = None
            if team in bases_grouped and bases_grouped[team]:
                found_pt = bases_grouped[team][0]
            if found_pt and len(found_pt) >= 2:
                fx, fz = found_pt[0], found_pt[-1]
                match = (abs(fx - ex) < 1.0 and abs(fz - ez) < 1.0)
                report.write("  team %s: ожидалось (%.1f, %.1f), эвристика нашла (%.1f, %.1f) -> %s\n" % (
                    team, ex, ez, fx, fz, "СОВПАЛО" if match else "НЕ СОВПАЛО"))
                ok = ok and match
            else:
                report.write("  team %s: ожидалось (%.1f, %.1f), эвристика ничего не нашла -> НЕ СОВПАЛО\n" % (
                    team, ex, ez))
                ok = False
        if ok:
            report.write("  => Эвристика подтверждена на известной карте, остальным картам можно доверять больше.\n")
        else:
            report.write("  => Эвристика зацепила не те теги. Не используйте авто-вывод для других карт "
                          "без ручной проверки по карте тегов выше.\n")

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        print("Использование: python extract_arena_data.py <путь к папке с игрой>")
        sys.exit(1)

    wot_root = sys.argv[1]
    if not os.path.isdir(wot_root):
        print("Папка не найдена: %s" % wot_root)
        sys.exit(1)

    print("Ищу arena.xml в %s ..." % wot_root)
    loose = find_loose(wot_root)
    packaged = find_in_packages(wot_root)

    all_map_ids = sorted(set(list(loose.keys()) + list(packaged.keys()) + KNOWN_MAPS))

    report_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'arena_data_report.txt')
    with open(report_path, 'w', encoding='utf-8') as report:
        report.write("Отчёт extract_arena_data.py\n")
        report.write("WoT root: %s\n" % wot_root)
        report.write("Найдено файлов (обычных): %d карт\n" % len(loose))
        report.write("Найдено файлов (в .pkg/.zip): %d карт\n" % len(packaged))

        any_found = False
        for map_id in all_map_ids:
            sources = loose.get(map_id) or packaged.get(map_id)
            if not sources:
                report.write("\n%s: arena.xml НЕ НАЙДЕН (ни обычным файлом, ни в .pkg)\n" % map_id)
                print("  [%s] arena.xml не найден" % map_id)
                continue
            any_found = True
            kind, path, inner = sources[0]
            print("  [%s] найден: %s%s" % (map_id, path, (' :: ' + inner) if inner else ''))
            try:
                if kind == 'file':
                    with open(path, 'r', encoding='utf-8', errors='replace') as f:
                        text = f.read()
                else:
                    with zipfile.ZipFile(path, 'r') as zf:
                        text = zf.read(inner).decode('utf-8', errors='replace')
            except Exception as e:
                report.write("\n%s: ошибка чтения (%s)\n" % (map_id, e))
                continue
            analyze_arena_xml(map_id, text, report)

        if not any_found:
            report.write("\nНичего не найдено вообще. Проверьте, что путь указывает на папку с "
                          "WorldOfTanks.exe (или на её res/res_mods), и что игра действительно этой "
                          "версии (0.4.5) содержит эти карты локально.\n")

    print("\nГотово. Полный отчёт: %s" % report_path)
    print("Пришлите этот файл мне, если хотите, чтобы я подставил точные координаты в BattleStarter.py.")

if __name__ == '__main__':
    main()
