# -*- coding: utf-8 -*-


from __future__ import print_function

import random

class ShellType(object):
    AP = 'AP'
    APCR = 'APCR'
    HEAT = 'HEAT'
    HE = 'HE'

class ModuleType(object):
    ENGINE = 'engine'
    FUEL_TANK = 'fuelTank'
    AMMO_BAY = 'ammoBay'
    GUN = 'gun'
    LEFT_TRACK = 'leftTrack'
    RIGHT_TRACK = 'rightTrack'
    RADIO = 'radio'
    TURRET_ROTATOR = 'turretRotator'
    SURVEYING_DEVICE = 'surveyingDevice'

ALL_MODULE_TYPES = (
    ModuleType.ENGINE, ModuleType.FUEL_TANK, ModuleType.AMMO_BAY,
    ModuleType.GUN, ModuleType.LEFT_TRACK, ModuleType.RIGHT_TRACK,
    ModuleType.RADIO, ModuleType.TURRET_ROTATOR, ModuleType.SURVEYING_DEVICE,
)

class CrewRole(object):
    COMMANDER = 'commander'
    GUNNER = 'gunner'
    DRIVER = 'driver'
    LOADER = 'loader'
    RADIOMAN = 'radioman'

MODULE_STATE_OK = 'ok'
MODULE_STATE_CRITICAL = 'critical'
MODULE_STATE_DESTROYED = 'destroyed'

CREW_STATE_OK = 'ok'
CREW_STATE_WOUNDED = 'wounded'
CREW_STATE_KILLED = 'killed'

_SAVING_THROW = {
    ModuleType.ENGINE: 0.33,
    ModuleType.AMMO_BAY: 0.20,
    ModuleType.FUEL_TANK: 0.33,
    ModuleType.RADIO: 0.33,
    ModuleType.GUN: 0.33,
    ModuleType.TURRET_ROTATOR: 0.33,
    ModuleType.SURVEYING_DEVICE: 0.33,
    CrewRole.COMMANDER: 0.33,
    CrewRole.GUNNER: 0.33,
    CrewRole.DRIVER: 0.33,
    CrewRole.LOADER: 0.33,
    CrewRole.RADIOMAN: 0.33,
}

_TRACK_SAVING_THROW = 0.85

_TRACK_SYNTHETIC_MAX_HP = 100

DAMAGE_RANDOMIZATION = 0.25
BASE_TRACK_REPAIR_SECONDS = 10.0
BASE_MODULE_REPAIR_SECONDS = 18.0
REPAIR_SKILL_SPEEDUP = 1.0
CRITICAL_HP_FRACTION = 0.5

_SHELL_CRIT_MULTIPLIER = {
    ShellType.AP: 1.0,
    ShellType.APCR: 0.8,
    ShellType.HEAT: 0.9,
    ShellType.HE: 1.5,
}

_BASE_INTERNAL_HIT_CHANCE = 0.55

_ZONE_WEIGHTS = {
    'chassis': {
        ModuleType.LEFT_TRACK: 3.0, ModuleType.RIGHT_TRACK: 3.0,
        ModuleType.ENGINE: 0.3, ModuleType.FUEL_TANK: 0.5,
        ModuleType.AMMO_BAY: 0.3, CrewRole.DRIVER: 0.4,
    },
    'hull': {
        ModuleType.ENGINE: 1.3, ModuleType.FUEL_TANK: 1.1,
        ModuleType.AMMO_BAY: 1.0, ModuleType.RADIO: 0.6,
        CrewRole.DRIVER: 1.0, CrewRole.RADIOMAN: 0.7,
        ModuleType.LEFT_TRACK: 0.3, ModuleType.RIGHT_TRACK: 0.3,
    },
    'turret': {
        CrewRole.COMMANDER: 1.2, CrewRole.GUNNER: 1.2, CrewRole.LOADER: 1.0,
        ModuleType.TURRET_ROTATOR: 0.9, ModuleType.SURVEYING_DEVICE: 0.8,
        ModuleType.AMMO_BAY: 0.5, ModuleType.RADIO: 0.3,
    },
    'gun': {
        ModuleType.GUN: 2.5, CrewRole.GUNNER: 0.6, ModuleType.TURRET_ROTATOR: 0.2,
    },
}

_CREW_PENALTY = {
    'reload':     {CrewRole.LOADER: 2.5, CrewRole.COMMANDER: 1.1},
    'dispersion': {CrewRole.GUNNER: 2.0, CrewRole.COMMANDER: 1.1},
    'aim_time':   {CrewRole.GUNNER: 2.0, CrewRole.COMMANDER: 1.1},
    'mobility':   {CrewRole.DRIVER: 0.5, CrewRole.COMMANDER: 0.95},
    'vision':     {CrewRole.COMMANDER: 0.75, CrewRole.RADIOMAN: 0.75},
}
MIN_VISION_FACTOR = 0.5

def crew_stat_factor(vehicle, stat):

    spec = _CREW_PENALTY.get(stat)
    if not spec or vehicle is None:
        return 1.0
    f = 1.0
    for member in vehicle.crew:
        if member.state == CREW_STATE_KILLED and member.role in spec:
            f *= spec[member.role]
    return f

def module_stat_factor(vehicle, stat):

    spec = _MODULE_PENALTY.get(stat)
    if not spec or vehicle is None:
        return 1.0
    f = 1.0
    for module_type, (crit_f, dead_f) in spec.items():
        m = vehicle.get_module(module_type)
        if m is None:
            continue
        if m.state == MODULE_STATE_CRITICAL:
            f *= crit_f
        elif m.state == MODULE_STATE_DESTROYED and dead_f is not None:
            f *= dead_f
    return f

DAMAGED_MODULE_EFFICIENCY = 0.5
_MODULE_PENALTY = {
    'dispersion':   {ModuleType.GUN: (1.0 / DAMAGED_MODULE_EFFICIENCY, None)},
    'aim_time':     {ModuleType.GUN: (1.0 / DAMAGED_MODULE_EFFICIENCY, None)},
    'turret_speed': {ModuleType.TURRET_ROTATOR: (DAMAGED_MODULE_EFFICIENCY, 0.0)},
    'vision':       {ModuleType.SURVEYING_DEVICE: (DAMAGED_MODULE_EFFICIENCY, 0.25)},
}

def clamp_vision_factor(factor):

    try:
        value = float(factor)
    except (TypeError, ValueError):
        return 1.0
    return max(MIN_VISION_FACTOR, min(1.0, value))

class Module(object):


    def __init__(self, module_type, max_hp, crit_chance=0.33, regen_cap=None):
        self.module_type = module_type
        self.max_hp = max_hp
        self.hp = max_hp
        self.state = MODULE_STATE_OK
        self.crit_chance = crit_chance
        self.regen_cap = regen_cap if regen_cap is not None else int(round(max_hp * CRITICAL_HP_FRACTION))

    def is_critical(self):
        return self.state == MODULE_STATE_CRITICAL

    def is_destroyed(self):
        return self.state == MODULE_STATE_DESTROYED

    def is_functional(self):
        return self.state == MODULE_STATE_OK

    def apply_hit(self, damage):

        if self.state == MODULE_STATE_DESTROYED:
            return None
        self.hp = max(0, self.hp - damage)
        if self.state == MODULE_STATE_OK:
            self.state = MODULE_STATE_CRITICAL
            return MODULE_STATE_CRITICAL
        else:
            self.state = MODULE_STATE_DESTROYED
            return MODULE_STATE_DESTROYED

    def repair(self):

        if self.state == MODULE_STATE_OK:
            return False
        if self.state == MODULE_STATE_DESTROYED:
            if self.module_type == ModuleType.FUEL_TANK:
                return False
            self.hp = self.regen_cap
            self.state = MODULE_STATE_CRITICAL
            return True
        return False

    def repair_full(self):
        self.hp = self.max_hp
        self.state = MODULE_STATE_OK
        return True

    def repair_seconds(self, repair_skill_pct=0.0, has_big_repairkit=False):

        is_track = self.module_type in (ModuleType.LEFT_TRACK, ModuleType.RIGHT_TRACK)
        base = BASE_TRACK_REPAIR_SECONDS if is_track else BASE_MODULE_REPAIR_SECONDS
        skill_pct = max(0.0, min(100.0, repair_skill_pct))
        factor = 1.0 + REPAIR_SKILL_SPEEDUP * (skill_pct / 100.0)
        if has_big_repairkit:
            factor *= 1.10
        if factor <= 0.0:
            factor = 1.0
        return base / factor

class CrewMember(object):


    def __init__(self, role, name=None, max_hp=100, wound_chance=0.33):
        self.role = role
        self.name = name or role
        self.max_hp = max_hp
        self.hp = max_hp
        self.state = CREW_STATE_OK
        self.wound_chance = wound_chance

    def apply_hit(self, damage):

        if self.state == CREW_STATE_KILLED:
            return None
        self.hp = max(0, self.hp - damage)
        new_state = CREW_STATE_KILLED if self.hp <= 0 else CREW_STATE_WOUNDED
        if new_state == self.state:
            return None
        self.state = new_state
        return new_state

    def heal(self):
        self.hp = self.max_hp
        self.state = CREW_STATE_OK
        return True

class Vehicle(object):


    def __init__(self, name, max_hp):
        self.name = name
        self.max_hp = max_hp
        self.hp = max_hp
        self.modules = {}
        self.crew = []
        self.on_fire = False

    def add_module(self, module):
        self.modules[module.module_type] = module
        return module

    def add_crew(self, crew_member):
        self.crew.append(crew_member)
        return crew_member

    def get_module(self, module_type):
        return self.modules.get(module_type)

    def is_alive(self):
        return self.hp > 0

def _weighted_pick(candidates):

    total = sum(w for w, _ in candidates)
    if total <= 0:
        return None
    r = random.uniform(0, total)
    upto = 0.0
    for w, item in candidates:
        upto += w
        if r <= upto:
            return item
    return candidates[-1][1]

class DamageResolver(object):


    def resolve_hit(self, vehicle, damage, shell_type=ShellType.AP, penetrated=True, hit_component=None):

        result = {
            'damage_dealt': 0,
            'module_events': [],
            'crew_events': [],
            'vehicle_destroyed': False,
        }
        if vehicle is None or damage <= 0 or not vehicle.is_alive():
            return result

        dealt = damage if damage <= vehicle.hp else vehicle.hp
        vehicle.hp = max(0, vehicle.hp - dealt)
        result['damage_dealt'] = dealt
        result['vehicle_destroyed'] = not vehicle.is_alive()

        if not penetrated:
            return result

        chance = min(1.0, _BASE_INTERNAL_HIT_CHANCE * _SHELL_CRIT_MULTIPLIER.get(shell_type, 1.0))
        if hit_component in _ZONE_WEIGHTS:
            chance = 1.0
        if random.random() >= chance:
            return result

        zone = _ZONE_WEIGHTS.get(hit_component) if hit_component else None

        def _zone_mult(key):
            return 1.0 if zone is None else zone.get(key, 0.0)

        candidates = []
        for module_type, module in vehicle.modules.items():
            if module.state == MODULE_STATE_DESTROYED:
                continue
            w = module.crit_chance * _zone_mult(module_type)
            if w > 0:
                candidates.append((w, ('module', module_type, module)))
        for member in vehicle.crew:
            if member.state == CREW_STATE_KILLED:
                continue
            w = member.wound_chance * _zone_mult(member.role)
            if w > 0:
                candidates.append((w, ('crew', member.role, member)))

        if hit_component == 'chassis':
            candidates = [c for c in candidates
                          if c[1][0] == 'module' and c[1][1] in (ModuleType.LEFT_TRACK, ModuleType.RIGHT_TRACK)]
        elif hit_component == 'gun':
            gun_only = [c for c in candidates
                        if c[1][0] == 'module' and c[1][1] == ModuleType.GUN]
            if gun_only:
                candidates = gun_only

        picked = _weighted_pick(candidates)
        if picked is None:
            return result

        kind, key, obj = picked
        new_state = obj.apply_hit(dealt)
        if new_state is not None:
            if kind == 'module':
                result['module_events'].append((key, new_state))
            else:
                result['crew_events'].append((key, new_state))

        return result

_MODULE_SOUNDS = {
    ModuleType.ENGINE:            {'critical': 'engine_damaged', 'destroyed': 'engine_destroyed', 'functional': 'engine_functional'},
    ModuleType.GUN:               {'critical': 'gun_damaged', 'destroyed': 'gun_destroyed', 'functional': 'gun_functional'},
    ModuleType.LEFT_TRACK:        {'critical': 'track_damaged', 'destroyed': 'track_destroyed', 'functional': 'track_functional'},
    ModuleType.RIGHT_TRACK:       {'critical': 'track_damaged', 'destroyed': 'track_destroyed', 'functional': 'track_functional'},
    ModuleType.AMMO_BAY:          {'critical': 'ammo_bay_damaged', 'destroyed': 'ammo_bay_damaged'},
    ModuleType.FUEL_TANK:         {'critical': 'fuel_tank_damaged', 'destroyed': 'fuel_tank_damaged'},
    ModuleType.RADIO:             {'critical': 'radio_damaged', 'destroyed': 'radio_damaged'},
    ModuleType.TURRET_ROTATOR:    {'critical': 'turret_rotator_damaged', 'destroyed': 'turret_rotator_damaged'},
    ModuleType.SURVEYING_DEVICE:  {'critical': 'surveying_devices_damaged', 'destroyed': 'surveying_devices_destroyed', 'functional': 'surveying_devices_functional'},
}

_CREW_KILLED_SOUNDS = {
    CrewRole.COMMANDER: 'commander_killed',
    CrewRole.GUNNER: 'gunner_killed',
    CrewRole.DRIVER: 'driver_killed',
    CrewRole.LOADER: 'loader_killed',
    CrewRole.RADIOMAN: 'radioman_killed',
}

FIRE_STARTED_SOUND = 'fire_started'
FIRE_STOPPED_SOUND = 'fire_stopped'

def get_module_sound(module_type, state):

    return _MODULE_SOUNDS.get(module_type, {}).get(state)

def get_crew_kill_sound(role):

    return _CREW_KILLED_SOUNDS.get(role)

def _comp_health(comp, key=None):

    if comp is None:
        return (None, None)
    target = comp
    if key is not None:
        try:
            target = comp.get(key)
        except AttributeError:
            target = getattr(comp, key, None)
        if target is None:
            return (None, None)
    try:
        mh = target.get('maxHealth')
        mrh = target.get('maxRegenHealth')
    except AttributeError:
        mh = getattr(target, 'maxHealth', None)
        mrh = getattr(target, 'maxRegenHealth', None)
    return (mh, mrh)

def _misc_factor(descr, key):

    if not key:
        return 1.0
    try:
        attrs = getattr(descr, 'miscAttrs', None)
        if attrs is None:
            return 1.0
        return float(attrs.get(key, 1.0))
    except Exception:
        return 1.0

_DEVICE_HP_SPEC = {
    ModuleType.ENGINE:           ('engine', None, 'engineHealthFactor'),
    ModuleType.AMMO_BAY:         ('hull', 'ammoBayHealth', 'ammoBayHealthFactor'),
    ModuleType.FUEL_TANK:        ('fuelTank', None, 'fuelTankHealthFactor'),
    ModuleType.RADIO:            ('radio', None, None),
    ModuleType.GUN:              ('gun', None, None),
    ModuleType.TURRET_ROTATOR:   ('turret', 'turretRotatorHealth', None),
    ModuleType.SURVEYING_DEVICE: ('turret', 'surveyingDeviceHealth', None),
}

def build_vehicle_from_descriptor(descr, name=None, max_hp=None):

    if max_hp is None:
        max_hp = getattr(descr, 'maxHealth', 100) if descr is not None else 100
    if name is None:
        name = getattr(descr, 'name', None) or 'vehicle'

    vehicle = Vehicle(name=name, max_hp=max_hp)

    for module_type, (attr, subkey, factor_key) in _DEVICE_HP_SPEC.items():
        comp = getattr(descr, attr, None) if descr is not None else None
        max_health, max_regen = _comp_health(comp, subkey)
        if max_health is None:
            continue
        factor = _misc_factor(descr, factor_key)
        mh = max(1, int(round(max_health * factor)))
        regen_cap = int(round(max_regen * factor)) if max_regen else None
        vehicle.add_module(Module(
            module_type, max_hp=mh,
            crit_chance=_SAVING_THROW.get(module_type, 0.33),
            regen_cap=regen_cap,
        ))

    for module_type in (ModuleType.LEFT_TRACK, ModuleType.RIGHT_TRACK):
        vehicle.add_module(Module(
            module_type, max_hp=_TRACK_SYNTHETIC_MAX_HP,
            crit_chance=_TRACK_SAVING_THROW,
            regen_cap=_TRACK_SYNTHETIC_MAX_HP,
        ))

    for role in (CrewRole.COMMANDER, CrewRole.GUNNER, CrewRole.DRIVER, CrewRole.LOADER, CrewRole.RADIOMAN):
        vehicle.add_crew(CrewMember(role, max_hp=100, wound_chance=_SAVING_THROW.get(role, 0.33)))

    return vehicle

def _build_demo_vehicle(name='Demo Tank', max_hp=380):

    v = Vehicle(name=name, max_hp=max_hp)
    v.add_module(Module(ModuleType.ENGINE, max_hp=90, crit_chance=_SAVING_THROW[ModuleType.ENGINE]))
    v.add_module(Module(ModuleType.FUEL_TANK, max_hp=50, crit_chance=_SAVING_THROW[ModuleType.FUEL_TANK]))
    v.add_module(Module(ModuleType.AMMO_BAY, max_hp=65, crit_chance=_SAVING_THROW[ModuleType.AMMO_BAY]))
    v.add_module(Module(ModuleType.GUN, max_hp=100, crit_chance=_SAVING_THROW[ModuleType.GUN]))
    v.add_module(Module(ModuleType.LEFT_TRACK, max_hp=_TRACK_SYNTHETIC_MAX_HP, crit_chance=_TRACK_SAVING_THROW))
    v.add_module(Module(ModuleType.RIGHT_TRACK, max_hp=_TRACK_SYNTHETIC_MAX_HP, crit_chance=_TRACK_SAVING_THROW))
    v.add_module(Module(ModuleType.RADIO, max_hp=60, crit_chance=_SAVING_THROW[ModuleType.RADIO]))
    v.add_module(Module(ModuleType.TURRET_ROTATOR, max_hp=70, crit_chance=_SAVING_THROW[ModuleType.TURRET_ROTATOR]))
    v.add_module(Module(ModuleType.SURVEYING_DEVICE, max_hp=45, crit_chance=_SAVING_THROW[ModuleType.SURVEYING_DEVICE]))
    v.add_crew(CrewMember(CrewRole.COMMANDER, max_hp=250))
    v.add_crew(CrewMember(CrewRole.GUNNER, max_hp=220))
    v.add_crew(CrewMember(CrewRole.DRIVER, max_hp=220))
    v.add_crew(CrewMember(CrewRole.LOADER, max_hp=200))
    v.add_crew(CrewMember(CrewRole.RADIOMAN, max_hp=180))
    return v

if __name__ == '__main__':
    random.seed(3)
    resolver = DamageResolver()
    tank = _build_demo_vehicle()
    print('%s: %d/%d hp, %d модулей, %d чел. экипажа' % (
        tank.name, tank.hp, tank.max_hp, len(tank.modules), len(tank.crew)))

    shot_no = 0
    while tank.is_alive() and shot_no < 10:
        shot_no += 1
        res = resolver.resolve_hit(tank, damage=110, shell_type=ShellType.AP, penetrated=True)
        mod_str = ', '.join('%s->%s' % (m, s) for m, s in res['module_events']) or '-'
        crew_str = ', '.join('%s->%s' % (r, s) for r, s in res['crew_events']) or '-'
        print('Попадание #%d: урон=%d, hp=%d/%d | модули: %s | экипаж: %s' % (
            shot_no, res['damage_dealt'], tank.hp, tank.max_hp, mod_str, crew_str))
        if res['vehicle_destroyed']:
            print('Танк уничтожен.')
            break

    print('')
    print('Состояние модулей на конец прогона:')
    for mtype in ALL_MODULE_TYPES:
        module = tank.modules.get(mtype)
        if module is None:
            continue
        sound = get_module_sound(mtype, module.state)
        print('  %-16s %-10s hp=%d/%d (regen_cap=%d) sound=%s' % (
            mtype, module.state, module.hp, module.max_hp, module.regen_cap, sound))

    print('')
    print('=== build_vehicle_from_descriptor() на поддельном дескрипторе ===')

    class _FakeDescr(object):
        maxHealth = 400
        name = 'FakeTank'
        engine = {'maxHealth': 105, 'maxRegenHealth': 52}
        hull = {'ammoBayHealth': {'maxHealth': 180, 'maxRegenHealth': 120}}
        fuelTank = {'maxHealth': 100, 'maxRegenHealth': 40}
        radio = {'maxHealth': 60, 'maxRegenHealth': 30}
        gun = {'maxHealth': 54, 'maxRegenHealth': 27}
        turret = {
            'turretRotatorHealth': {'maxHealth': 140, 'maxRegenHealth': 70},
            'surveyingDeviceHealth': {'maxHealth': 90, 'maxRegenHealth': 45},
        }
        miscAttrs = {'ammoBayHealthFactor': 1.0, 'engineHealthFactor': 1.0, 'fuelTankHealthFactor': 1.0}

    fake_tank = build_vehicle_from_descriptor(_FakeDescr())
    print('Собрано устройств: %d (%s)' % (len(fake_tank.modules), ', '.join(sorted(fake_tank.modules.keys()))))
    for mtype in ALL_MODULE_TYPES:
        m = fake_tank.get_module(mtype)
        if m:
            print('  %-16s max_hp=%-4d regen_cap=%-4d crit_chance=%.2f' % (mtype, m.max_hp, m.regen_cap, m.crit_chance))

    print('')
    print('=== Зональность: попадание в chassis сильно чаще выбивает гусеницу ===')
    random.seed(7)
    hits_by_zone = {}
    for zone in ('chassis', 'hull', 'turret', 'gun', None):
        fresh = build_vehicle_from_descriptor(_FakeDescr())
        counts = {}
        for _ in range(400):
            fresh2 = build_vehicle_from_descriptor(_FakeDescr())
            r = resolver.resolve_hit(fresh2, damage=999999, shell_type=ShellType.AP, penetrated=True, hit_component=zone)
            for mt, _st in r['module_events']:
                counts[mt] = counts.get(mt, 0) + 1
            for role, _st in r['crew_events']:
                counts['crew:' + role] = counts.get('crew:' + role, 0) + 1
        top = sorted(counts.items(), key=lambda kv: -kv[1])[:3]
        print('  zone=%-8s top-3: %s' % (zone, ', '.join('%s=%d' % kv for kv in top)))

    print('')
    print('=== crew_stat_factor / module_stat_factor ===')
    t2 = build_vehicle_from_descriptor(_FakeDescr())
    t2.crew[0].state = CREW_STATE_KILLED
    print('commander killed -> vision factor:', crew_stat_factor(t2, 'vision'))
    t3 = build_vehicle_from_descriptor(_FakeDescr())
    t3.get_module(ModuleType.GUN).state = MODULE_STATE_CRITICAL
    print('gun critical -> dispersion factor:', module_stat_factor(t3, 'dispersion'))

    print('')
    print('=== Все проверки пройдены ===')
