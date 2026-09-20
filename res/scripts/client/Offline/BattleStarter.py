
# -*- coding: utf-8 -*-
import BigWorld
import Math
import math
import game
import Keys
import cPickle
import string as _string
from functools import partial
import constants
from ClientArena import ClientArena
from Event import Event, EventManager
from AreaDestructibles import g_destructiblesManager
from DestructiblesCache import (
    chunkIDFromPosition, chunkIDFromChunkIndexes, chunkIndexesFromChunkID,
    encodeFallenDestructible, encodeFallenParams,
    encodeDestructibleModule,
    DESTR_TYPE_TREE, DESTR_TYPE_FALLING_ATOM,
    DESTR_TYPE_FRAGILE, DESTR_TYPE_STRUCTURE
)
import re as _re
import constants as _const
import math as _math
import random as _random
from items import vehicles, ITEM_TYPE_INDICES, getTypeOfCompactDescr
from items.vehicles import NUM_EQUIPMENT_SLOTS, makeIntCompactDescrByID
from debug_utils import LOG_NOTE, LOG_ERROR, LOG_WARNING, LOG_CURRENT_EXCEPTION
from PlayerEvents import g_playerEvents
from helpers.DecalMap import DecalMap
from gui.WindowsManager import g_windowsManager
from messenger.gui import MessengerDispatcher as _MessengerDispatcherMod
from messenger.wrappers import ChannelWrapper as _ChannelWrapper, ChatActionWrapper as _ChatActionWrapper
from chat_shared import CHAT_CHANNEL_BATTLE, CHAT_CHANNEL_BATTLE_TEAM

from tank_battle_damage import (
    DamageResolver, Vehicle as DmgVehicle, Module, ModuleType,
    CrewMember, CrewRole, ShellType,
    MODULE_STATE_DESTROYED, CREW_STATE_KILLED,
    get_module_sound, get_crew_kill_sound, FIRE_STARTED_SOUND, FIRE_STOPPED_SOUND,
    build_vehicle_from_descriptor, crew_stat_factor, module_stat_factor,
    clamp_vision_factor,
)

try:
    from Offline.spawnpoints import SPAWN_POINTS as _REVIVED_SPAWN_POINTS
except Exception:
    _REVIVED_SPAWN_POINTS = {}

try:
    from Offline.BotDirector import BotDirector
except Exception:
    BotDirector = None

if not hasattr(constants, 'ARENA_PERIOD_WAITING'):
    _AP = constants.ARENA_PERIOD
    constants.ARENA_PERIOD_WAITING = _AP.WAITING
    constants.ARENA_PERIOD_PREBATTLE = _AP.PREBATTLE
    constants.ARENA_PERIOD_BATTLE = _AP.BATTLE
    constants.ARENA_PERIOD_AFTERBATTLE = _AP.AFTERBATTLE

if not hasattr(constants, 'ARENA_UPDATE_VEHICLE_ADDED'):
    _AU = constants.ARENA_UPDATE
    constants.ARENA_UPDATE_VEHICLE_LIST = _AU.VEHICLE_LIST
    constants.ARENA_UPDATE_VEHICLE_ADDED = _AU.VEHICLE_ADDED
    constants.ARENA_UPDATE_PERIOD = _AU.PERIOD
    constants.ARENA_UPDATE_STATISTICS = _AU.STATISTICS
    constants.ARENA_UPDATE_VEHICLE_STATISTICS = _AU.VEHICLE_STATISTICS
    constants.ARENA_UPDATE_VEHICLE_KILLED = _AU.VEHICLE_KILLED
    constants.ARENA_UPDATE_AVATAR_READY = _AU.AVATAR_READY
    constants.ARENA_UPDATE_BASE_POINTS = _AU.BASE_POINTS
    constants.ARENA_UPDATE_BASE_CAPTURED = _AU.BASE_CAPTURED

_DM = constants.DESTRUCTIBLE_MATKINDS
if not hasattr(constants, 'DESTRUCTIBLE_MATKINDS_MIN'):
    constants.DESTRUCTIBLE_MATKINDS_MIN = _DM.MIN
    constants.DESTRUCTIBLE_MATKINDS_MAX = _DM.MAX
constants.DESTRUCTIBLE_MATKINDS_NORMAL_MIN = _DM.NORMAL_MIN
constants.DESTRUCTIBLE_MATKINDS_NORMAL_MAX = _DM.NORMAL_MAX
constants.DESTRUCTIBLE_MATKINDS_DAMAGED_MIN = _DM.DAMAGED_MIN
constants.DESTRUCTIBLE_MATKINDS_DAMAGED_MAX = _DM.DAMAGED_MAX

def _arena_vehicle_info_tuple(vehID, compactDescr, name, team, isAlive=True, isAvatarReady=True, accountDBID=0, prebattleID=0):

    return (vehID, compactDescr, name, team, isAlive, isAvatarReady, accountDBID, '', 0, int(prebattleID or 0))

def _arena_name_to_id(arena_id):

    import ArenaType
    glist = ArenaType.g_list or {}
    if arena_id is None or arena_id == -1:
        return None, None
    if isinstance(arena_id, (tuple, list)):
        arena_id = arena_id[0]
    if isinstance(arena_id, (int, long)) and arena_id in glist:
        return int(arena_id), glist[arena_id]
    name = str(arena_id).strip()
    if name.startswith('spaces/'):
        name = name[7:]
    for typeID, typeName in glist.iteritems():
        if typeName == name or typeName == str(arena_id):
            return typeID, typeName
    return None, name

def _music_loading_event(MC):

    return getattr(MC, 'MUSIC_EVENT_COMBAT_LOADING', getattr(MC, 'MUSIC_EVENT_LOADING', MC.MUSIC_EVENT_COMBAT))

def _patch_offline_media_fixes():

    try:
        import BigWorld as _BigWorld
        if not getattr(_BigWorld, '_offline_decal_patched', False):
            _orig_wg_addDecalGroup = _BigWorld.wg_addDecalGroup
            def _safe_wg_addDecalGroup(name, lifeTime, trianglesCount, *a, **kw):
                try:
                    trianglesCount = int(trianglesCount)
                except Exception:
                    pass
                try:
                    return _orig_wg_addDecalGroup(name, lifeTime, trianglesCount, *a, **kw)
                except RuntimeError:
                    return None
            _BigWorld.wg_addDecalGroup = _safe_wg_addDecalGroup
            _BigWorld._offline_decal_patched = True
            LOG_NOTE("[OFFLINE] BigWorld.wg_addDecalGroup patched (int trianglesCount)")
    except Exception as _e:
        LOG_NOTE("[OFFLINE] wg_addDecalGroup patch failed: %s" % _e)

    try:
        import FMOD as _FMOD
        if not getattr(_FMOD, '_offline_getSound_patched', False):
            _orig_getSound = _FMOD.getSound
            def _safe_getSound(eventName, *a, **kw):
                try:
                    return _orig_getSound(eventName, *a, **kw)
                except KeyError:
                    return None
            _FMOD.getSound = _safe_getSound
            _FMOD._offline_getSound_patched = True
            LOG_NOTE("[OFFLINE] FMOD.getSound patched (missing-event safety)")
    except Exception as _e:
        LOG_NOTE("[OFFLINE] FMOD.getSound patch failed: %s" % _e)

    try:
        import BigWorld as _BigWorld
        import game as _game
        if not getattr(_BigWorld, '_offline_mem_hook_patched', False):
            def _safe_onMemoryCritical(*_a, **_kw):
                try:
                    LOG_WARNING("[OFFLINE][MEM] Memory load critical - freeing Python memory")
                    import gc as _gc
                    before = _gc.collect()
                    LOG_NOTE("[OFFLINE][MEM] gc.collect() reclaimed %d objects" % before)
                    try:
                        _scenery_low_memory()
                    except Exception:
                        pass
                    try:
                        _wall_mem_clear()
                    except Exception:
                        pass
                except Exception as _e2:
                    LOG_NOTE("[OFFLINE][MEM] onMemoryCritical failed: %s" % _e2)
            _MEM_CRIT_LAST = [0.0]
            _orig_mem_fn = _safe_onMemoryCritical
            def _throttled_onMemoryCritical(*_a, **_kw):
                try:
                    _now = 0.0
                    try:
                        _now = _BigWorld.time()
                    except Exception:
                        pass
                    if _now and (_now - _MEM_CRIT_LAST[0]) < 1.5:
                        return
                    _MEM_CRIT_LAST[0] = _now
                except Exception:
                    pass
                return _orig_mem_fn(*_a, **_kw)
            _BigWorld.onMemoryCritical = _throttled_onMemoryCritical
            try:
                _game.onMemoryCritical = _throttled_onMemoryCritical
            except Exception:
                pass
            _BigWorld._offline_mem_hook_patched = True
            LOG_NOTE("[OFFLINE] onMemoryCritical installed on BigWorld and game")
    except Exception as _e:
        LOG_NOTE("[OFFLINE] onMemoryCritical patch failed: %s" % _e)
    try:
        from Offline.Manager import _patch_sync_controller_fini
        _patch_sync_controller_fini()
    except Exception as _e:
        LOG_NOTE("[OFFLINE] SyncController patch failed: %s" % _e)

    try:
        from gui.Scaleform.Flash import Flash as _Flash
        if not getattr(_Flash, '_offline_active_patched', False):
            _orig_flash_active = _Flash.active
            def _safe_flash_active(self, state):
                return _orig_flash_active(self, bool(state))
            _Flash.active = _safe_flash_active
            _Flash._offline_active_patched = True
    except Exception as _e:
        LOG_NOTE("[OFFLINE] Flash.active patch failed: %s" % _e)

    try:
        from gui.Scaleform.windows import GUIWindow as _GUIWindow
        if not getattr(_GUIWindow, '_offline_active_patched', False):
            _orig_guiwindow_active = _GUIWindow.active
            def _safe_guiwindow_active(self, state):
                return _orig_guiwindow_active(self, bool(state))
            _GUIWindow.active = _safe_guiwindow_active
            _GUIWindow._offline_active_patched = True
    except Exception as _e:
        LOG_NOTE("[OFFLINE] GUIWindow.active patch failed: %s" % _e)

_MODULE_RU = {
    ModuleType.ENGINE:           (u'Двигатель', u'поврежден', u'уничтожен', u'починен'),
    ModuleType.FUEL_TANK:        (u'Топливный бак', u'поврежден', u'уничтожен', u'починен'),
    ModuleType.AMMO_BAY:         (u'Боеукладка', u'повреждена', u'уничтожена', u'починена'),
    ModuleType.GUN:              (u'Орудие', u'повреждено', u'уничтожено', u'починено'),
    ModuleType.LEFT_TRACK:       (u'Левая гусеница', u'повреждена', u'уничтожена', u'починена'),
    ModuleType.RIGHT_TRACK:      (u'Правая гусеница', u'повреждена', u'уничтожена', u'починена'),
    ModuleType.RADIO:            (u'Рация', u'повреждена', u'уничтожена', u'починена'),
    ModuleType.TURRET_ROTATOR:   (u'Привод поворота башни', u'поврежден', u'уничтожен', u'починен'),
    ModuleType.SURVEYING_DEVICE: (u'Приборы наблюдения', u'повреждены', u'уничтожены', u'починены'),
}
_CREW_RU = {
    CrewRole.COMMANDER: u'Командир',
    CrewRole.GUNNER: u'Наводчик',
    CrewRole.DRIVER: u'Механик-водитель',
    CrewRole.LOADER: u'Заряжающий',
    CrewRole.RADIOMAN: u'Радист',
}

_DMG_COLOUR_ORANGE    = (255.0, 160.0, 0.0, 255.0)
_DMG_COLOUR_GREEN     = (90.0, 220.0, 90.0, 255.0)
_DMG_COLOUR_REDPURPLE = (205.0, 60.0, 120.0, 255.0)

_DMG_MSG_MODULE_CRIT   = (u'Выстрел %(entity)s повредил %(device)s.', u'Устройство %(device)s повреждено.')
_DMG_MSG_MODULE_DESTR  = (u'Выстрел %(entity)s разрушил %(device)s.', u'Устройство %(device)s разрушено.')
_DMG_MSG_CREW_HIT      = (u'Выстрел %(entity)s вывел из строя %(device)s.', u'%(device)s выведен из строя.')
_DMG_MSG_REPAIRED      = u'Устройство %(device)s восстановлено.'
_DMG_MSG_FIRE_BURNING  = u'Танк горит!'
_DMG_MSG_FIRE_STOPPED  = u'Пожар остановлен.'
_DMG_MSG_DEATH_SHOT    = u'Танк уничтожен выстрелом %(entity)s.'
_DMG_MSG_DEATH_SHORT   = u'Танк уничтожен.'

_SHELL_KIND_TO_TYPE = {
    'ARMOR_PIERCING':    ShellType.AP,
    'ARMOR_PIERCING_CR': ShellType.APCR,
    'HOLLOW_CHARGE':     ShellType.HEAT,
    'HIGH_EXPLOSIVE':    ShellType.HE,
}

import Vehicle
if hasattr(Vehicle, 'DumbFilter'):
    Vehicle.DumbFilter = BigWorld.WGVehicleFilter
    LOG_NOTE("[BATTLE] DumbFilter replaced with WGVehicleFilter")

try:
    from gui.Scaleform.CommonPage import CommonPage
    if not hasattr(CommonPage, '_patched_offline_btn'):
        orig_updateFightButton = CommonPage._updateFightButton
        def safe_updateFightButton(self, *a, **kw):
            try: orig_updateFightButton(self, *a, **kw)
            except: pass
        CommonPage._updateFightButton = safe_updateFightButton
        CommonPage._patched_offline_btn = True
except: pass

_NAME_PREFIXES = ['Dark', 'Iron', 'Steel', 'Ghost', 'Shadow', 'Night', 'Pro', 'Noob', 'Killer', 'Sniper', 'Tank', 'Red', 'Blue', 'Wolf', 'Bear', 'Tiger', 'XxX', 'Nagibator', 'Misha', 'Valera', 'Vasyan', 'vova', 'CAT', 'goga', 'qwerty', 'Hamster', 'Pivo', 'Alesha',
    'Stalker', 'Zloj', 'Fire', 'Blade', 'Storm', 'Thunder', 'Crazy', 'Mad', 'Psycho', 'Mega', 'Turbo', 'Ultra', 'Super', 'Diamond', 'Golden', 'Silver', 'Black', 'White', 'Green', 'Purple', 'Eagle', 'Hawk', 'Falcon', 'Dragon', 'Lion', 'Cobra', 'Python', 'Shark', 'Panda', 'KoT', 'T34', 'IS7', 'E100', 'Maus', 'KV2', 'Tiger2', 'RUS', 'USA', 'GER', 'UA', 'BY', 'PL', 'CZ', 'LV', 'LT', 'EE', 'MD',
    'Kapitan', 'Leitenant', 'Serzhant', 'General', 'Marshal', 'Polkovnik', 'Maidan', 'Batya', 'Ded', 'Pacan', 'Brat', 'KoT9l', 'Xopow', 'CrazY', 'Rambo', 'Terminator', 'Predator', 'Neo', 'Morro', 'Witcher', 'Viking', 'Spartan', 'Gladiator', 'Knight', 'Ninja', 'Samurai', 'Pirat', 'Bandit', 'Gangster', 'Hacker', 'Coder', 'Toxa', 'Serega', 'Dimon', 'Pasha', 'Sanya', 'Kolya', 'Gennadij', 'Potter', 'Sova', 'Bars', 'Veter', 'Uragan', 'Shtorm', 'Molniya', 'Plamya', 'Kamen', 'Grom', 'Almaz', 'Rubin', 'Topaz', 'Yantar', 'Granit', 'Bazalt', 'Shtab', 'Pobeda', 'Slava', 'Puma', 'Gepard', 'Yaguar', 'Barsik', '_pro_', 'xX', 'XX', 'NoScope', 'Boom', 'Bang', 'Flash', 'Glow', 'Roy', 'Luck', 'Frost', 'Ice', 'NoFrost', 'Traktor', 'Tankist', 'Nalivay']

_NAME_ROOTS = ['Warrior', 'Hunter', 'Destroyer', 'Slayer', 'Rider', 'Shooter', 'Gunner', 'Master', 'Lord', 'King', 'Viper', 'Phantom', '2000', '1999', '666', '777', 'RU', 'KZ', '1986', '1998', 'Imperator', 'Voin', 'Geroj', '2010', 'little', 'Legenda', 'pivnoj', 'korobok',
    'WoT', 'Bota', 'Player', 'Pro', 'God', 'Best', 'Top', 'Winner', 'Champ', 'Hero', 'Zlo', 'Killer', 'Pack', 'Over', 'Rage', 'Fury', 'Wrath', 'Soul', 'Heart', 'Mind', 'Fist', 'Blade', 'Bolt', 'Storm', 'Rush', 'Dash', 'Creeper', 'Runner', 'Jumper', 'Dancer', 'Fighter', 'Brawler', 'Mage', 'Wizard', 'Beast', 'Freak', 'Gamer', 'Boss', 'Queen', 'Duke', 'Baron', 'Knight', 'Rogue', 'Nomad', 'Wanderer', 'Shadow', 'Smoke', 'Dust', 'Steel', 'Lead', 'Copper', 'Bronze', 'Silver', 'Golden', 'Neon', 'Cyber', 'Pixel', 'Matrix', 'Turbo', 'Grav', 'Tank', 'Bronya', 'Stal', 'Stalnoj', 'Grom', 'Molnija', 'Raketa', 'Sputnik', 'Kometa', 'Zvezda', 'Luna', 'Solnce', 'Huy', 'Ad', 'Raj', 'Gorod', 'Ulica', 'Zona', 'Kaif', 'Drag', 'Batya', 'Ded', 'Pashka', 'TVO', 'HZ', 'nagibator', 'Pro100', 'Xopow', 'Klass', 'Krutoy', 'Muzhik', 'CheLovek', '1990', '1991', '1992', '1993', '1994', '1995', '1996', '1997', '1985', '1984', '1983', '1982', '1981', '1980', '2005', '2001', '2002', '2003', '2004', '007', '7777', '13', '999', '911', '228', '1337', '333', '667', '765', 'nik', 'nikola', 'roma', 'toma', 'kir', 'dim', 'SAMAYA', 'tochnaya', 'jednaya', 'krasnaya', 'siniy', 'zeleniy', 'beliy', 'chernyi', 'ryzhiy', 'lisyi', 'puh', 'kompot', 'barsik', 'zhuchka', 'bonik', 'sharik']

_SECRET_NAMES = ['PROTEZEAD', 'sqwzyy', 'oguzok31ru', 'Ronext', 'Supramacy', ' FedorH_F_1', 'Vonamos', 'Jov', 'DoctorAbobus', 'KarbenLalkas', 'HANS', 'Imperator_Glekobes', 'RoT_Tester', 'anaway921', 'mx',
    'mry', 'Kot0z', 'ZhopaX', 'Xuchen', 'PivoVod', 'SaloEat', 'Borzaya', 'Kapitoshka', 'Chempion_Mira', 'Tankist_PRO', 'MozgVipail', 'NoSmoking', 'Zapolarnik', 'Shuravi', 'Molodoy', 'Starichok', 'Iron_ma', 'Grom0v', 'Kapkan', 'Zamorozka', 'elegant', 'Nolik', 'Kvitok', 'Malyshka', 'Tsar', 'Oplot', 'Kornet', 'Vintorez', 'Kalash', 'Destroyer_44', 'LuchShego', 'Pobeditel', 'Serega_Boss', 'Tolik2007', 'VitalikXD']


_OFFLINE_CHAT_TEAM_CID = 900001
_OFFLINE_CHAT_ALL_CID = 900002

_BOT_CHAT_PHRASES = [
    u'гг', u'вп', u'ааааааааааааааа', u'Удачи вам', u'помогите',
    u'да шел ты н...й', u'вижу их', u'танк говно', u'сижу на базе =)',
    u'ес нету', u'аптечку бы сейчас', u'эх.. ремку бы', u'кто на защиту?',
    u'держим фланг', u'боеукладка', u'го в кусты', u'нет',
    u'вот их так!', u'жгут!', u'нужна поддержка', u'едем захватывать',
    u'у меня танк крутой', u'пушка не бьет', u'тупой бой',
    u'слился тупо', u'ггвп', u'изи катка', u'ВЫ ЛОШКИ', u'Какой пиво лучшее?',
    u'э да тут читер', u'команда топ', u'команда днище', u'куда мы едем вообще',
    u'alt + f4 включает фары', u'я еду и вы едете', u'спс за помощь',
    u'обидно проиграли', u'вы че а?', u'пинг 300 не играю нормально',
    u'ну и лохи в команде', u'топ это ты?', u'слив не засчитан',
    u'жми на газ!', u'не ссы в кустах', u'жаба, давай вперёд',
    u'пробил? шиш тебе', u'куда лезешь, там снайпер', u'обойди справа',
    u'прикрываю тебя', u'держись базы', u'не иди в одиночку',
    u'свети в овраге', u'гонду не выбили?', u'ремка сгорела',
    u'пожар сам потушу', u'экипаж в ауте', u'модуль в красный',
    u'стреляю по бокам', u'лови в башню', u'сплэш по гусле',
    u'танкастый урон', u'не пробил, жаль', u'опять рикошет',
    u'изи чит от снайпера', u'кто меня засветил?', u'опять СТ в центре',
    u'тяжи на фланг, СТ в город', u'АРТУ СНЕСИТЕ', u'не пускайте их на базу',
    u'захватываем!', u'наши захватывают!', u'свои, не стреляйте',
    u'я своих не бью', u'фраги не главное, главное победа', u'как зашло?',
    u'за 5 минут слили', u'норм катка', u'такое себе',
    u'всем удачи, ребят', u'погнали', u'лайт за деревней',
    u'танки в лес не ссыте', u'кто прождал ремку?', u'гусля слетела',
    u'починился, еду дальше', u'башку не подставляй', u'играем на уроне',
    u'злодеи в городе', u'зашёл с тыла', u'их СТ ушли вправо',
    u'база под угрозой, возвращаюсь', u'не бей по мёртвому', u'бесполезно стрелять',
    u'зачем ты туда поехал?', u'это был саботаж', u'тима саппорт нужен',
    u'реально месяц играю', u'новичок тут', u'у меня акк новый',
    u'где моя команда?!', u'они жмут нас к базе', u'перегруппировка',
    u'тактик-режим включён', u'всё под контролем', u'ггвп изи',
    u'спс за катку', u'надо было изи катку', u'увлекательная катка',
    u'кто в клан вступит?', u'СВОИ ГДЕ?', u'все за базу драться',
    u'арта, не дремли', u'пинг 999, я тормоз', u'новый патч лагает',
    u'графику на минималки поставь', u'фпс упал', u'не могу ехать, лаги'
]

def _generate_offline_chat_channels():

    try:
        dispatcher = _MessengerDispatcherMod.g_instance
        if dispatcher is None:
            return False
        teamChannel = _ChannelWrapper(id=_OFFLINE_CHAT_TEAM_CID, channelName='Team',
                                       flags=CHAT_CHANNEL_BATTLE | CHAT_CHANNEL_BATTLE_TEAM)
        allChannel = _ChannelWrapper(id=_OFFLINE_CHAT_ALL_CID, channelName='All',
                                      flags=CHAT_CHANNEL_BATTLE)
        _cmap = dispatcher.channels._ChannelsManager__channelMap
        _cmap[_OFFLINE_CHAT_TEAM_CID] = _cmap[_OFFLINE_CHAT_TEAM_CID].update(teamChannel)
        _cmap[_OFFLINE_CHAT_ALL_CID] = _cmap[_OFFLINE_CHAT_ALL_CID].update(allChannel)
        dispatcher.channels._setJoinedFlag(_OFFLINE_CHAT_TEAM_CID, True)
        dispatcher.channels._setJoinedFlag(_OFFLINE_CHAT_ALL_CID, True)
        dispatcher.battleMessenger.addChannel(teamChannel)
        dispatcher.battleMessenger.addChannel(allChannel)
        LOG_NOTE("[BATTLE][CHAT] offline chat channels ready (team=%d, all=%d)" %
                 (_OFFLINE_CHAT_TEAM_CID, _OFFLINE_CHAT_ALL_CID))
        return True
    except Exception as e:
        LOG_ERROR("[BATTLE][CHAT] channel init failed: %s" % e)
        return False

def _offline_post_chat_message(cid, originatorID, originatorName, text):

    try:
        dispatcher = _MessengerDispatcherMod.g_instance
        if dispatcher is None:
            return
        if isinstance(text, unicode):
            text = text.encode('utf-8')
        msg = _ChatActionWrapper(channel=cid, originator=originatorID,
                                  originatorNickName=originatorName, data=text)
        dispatcher.battleMessenger.addChannelMessage(msg)
        dispatcher.channels.addChannelMessage(cid, msg)
    except Exception as e:
        LOG_ERROR("[BATTLE][CHAT] post message failed: %s" % e)

def _offline_try_chat_shortcuts(avatar, isDown, key, mods):

    if not isDown:
        return False
    if mods not in (0, None):
        return False
    if avatar is None or not getattr(avatar, 'isVehicleAlive', True):
        return False
    try:
        import CommandMapping
        from chat_shared import CHAT_COMMANDS
        from helpers import i18n
        cmdMap = CommandMapping.g_instance
        now = BigWorld.time()
        if now - float(getattr(avatar, '_chat_sc_time', 0.0) or 0.0) < 0.35:
            return False
        attack_target = False
        cmdName = None
        try:
            if cmdMap.isFired(CommandMapping.CMD_CHAT_SHORTCAT_ATTACK_MY_TARGET, key):
                attack_target = True
        except Exception:
            pass
        if not attack_target:
            _pairs = (
                ('ATTACK', 'CMD_CHAT_SHORTCAT_ATTACK'),
                ('BACKTOBASE', 'CMD_CHAT_SHORTCAT_BACKTOBASE'),
                ('FOLLOWME', 'CMD_CHAT_SHORTCAT_FOLLOWME'),
                ('POSITIVE', 'CMD_CHAT_SHORTCAT_POSITIVE'),
                ('NEGATIVE', 'CMD_CHAT_SHORTCAT_NEGATIVE'),
                ('HELPME', 'CMD_CHAT_SHORTCAT_HELPME'),
            )
            for _n, _attr in _pairs:
                _cid = getattr(CommandMapping, _attr, None)
                if _cid is not None and cmdMap.isFired(_cid, key):
                    cmdName = _n
                    break
        if not attack_target and cmdName is None:
            return False
        avatar._chat_sc_time = now
        origin = getattr(avatar, 'name', 'Player')
        try:
            origin = avatar.arena.vehicles.get(avatar.playerVehicleID, {}).get('name', origin)
        except Exception:
            pass
        cmd = None
        if attack_target:
            tgt = BigWorld.target()
            if tgt is None:
                return True
            try:
                if tgt.publicInfo['team'] == avatar.team:
                    return True
            except Exception:
                pass
            tname = ''
            try:
                tname = tgt.publicInfo['name']
            except Exception:
                tname = ''
            cmd = getattr(CHAT_COMMANDS, 'ATTACKENEMY', None)
            try:
                text = i18n.makeString(cmd.msgText, name=tname)
            except Exception:
                try:
                    text = i18n.makeString(cmd.msgText)
                except Exception:
                    text = 'Attack!'
        else:
            cmd = getattr(CHAT_COMMANDS, cmdName, None)
            try:
                text = i18n.makeString(cmd.msgText)
            except Exception:
                text = cmdName
        _offline_post_chat_message(
            _OFFLINE_CHAT_TEAM_CID,
            getattr(avatar, 'playerVehicleID', 1),
            origin,
            text)
        try:
            sn = getattr(avatar, 'soundNotifications', None)
            snd = None
            if cmd is not None:
                snd = getattr(cmd, 'sound_notification', None)
                if snd is None and hasattr(cmd, 'get'):
                    snd = cmd.get('sound_notification')
            if sn is not None and snd:
                sn.play(snd)
        except Exception:
            pass
        return True
    except Exception:
        return False

def _weighted_choice(pairs):

    total = float(sum(w for _, w in pairs))
    if total <= 0: return pairs[0][0]
    r = _random.uniform(0, total)
    upto = 0.0
    for value, w in pairs:
        upto += w
        if r <= upto:
            return value
    return pairs[-1][0]

def _generate_bot_name():
    if _random.random() < 0.03:
        return _random.choice(_SECRET_NAMES)

    if _random.random() > 0.3:
        pfx = _random.choice(_NAME_PREFIXES)
        rt = _random.choice(_NAME_ROOTS)
        sfx = str(_random.randint(10, 9999)) if _random.random() > 0.5 else ""
        sep = _random.choice(['', '_'])
        return "%s%s%s%s" % (pfx, sep, rt, sfx)
    else:
        length = _random.randint(5, 10)
        return ''.join(_random.choice(_string.ascii_letters) for _ in range(length)).capitalize()


def _randomize_bot_modules(botDescr):

    try:
        t = botDescr.type

        chassis_opts = list(t.chassis)
        _random.shuffle(chassis_opts)
        for c in chassis_opts:
            ok, _reason = botDescr.mayInstallComponent(c['compactDescr'])
            if ok:
                botDescr.installComponent(c['compactDescr'])
                break

        for comp_list in (t.engines, t.radios, t.fuelTanks):
            opts = list(comp_list)
            _random.shuffle(opts)
            for c in opts:
                ok, _reason = botDescr.mayInstallComponent(c['compactDescr'])
                if ok:
                    botDescr.installComponent(c['compactDescr'])
                    break

        turret_opts = list(t.turrets[0])
        _random.shuffle(turret_opts)
        for turretDescr in turret_opts:
            gun_opts = list(turretDescr['guns'])
            _random.shuffle(gun_opts)
            installed = False
            for gunDescr in gun_opts:
                ok, _reason = botDescr.mayInstallTurret(turretDescr['compactDescr'], gunDescr['compactDescr'])
                if ok:
                    botDescr.installTurret(turretDescr['compactDescr'], gunDescr['compactDescr'])
                    installed = True
                    break
            if installed:
                break
    except Exception:
        pass


_BOT_TANK_LIST = [
    'ussr:MS-1', 'ussr:AT-1', 'ussr:BT-2', 'ussr:Churchill_LL',
    'ussr:GAZ-74b', 'ussr:IS', 'ussr:IS-3', 'ussr:IS-4', 'ussr:IS-7',
    'ussr:ISU-152', 'ussr:KV', 'ussr:KV-1s', 'ussr:KV-3',
    'ussr:M3_Stuart_LL', 'ussr:Matilda_II_LL','ussr:A-20',
    'ussr:S-51', 'ussr:SU-5', 'ussr:SU-8', 'ussr:SU-14',
    'ussr:SU-18', 'ussr:SU-26', 'ussr:SU-76', 'ussr:SU-85',
    'ussr:SU-100', 'ussr:SU-152', 'ussr:T-26', 'ussr:T-28',
    'ussr:T-34', 'ussr:T-34-85', 'ussr:T-43', 'ussr:T-44',
    'ussr:Valentine_LL', 'ussr:T-46', 'ussr:BT-7',

    'germany:Ltraktor', 'germany:Bison_I', 'germany:Ferdinand',
    'germany:G20_Marder_II', 'germany:Grille', 'germany:H39_captured',
    'germany:Hetzer', 'germany:Hummel', 'germany:JagdPanther',
    'germany:JagdPzIV', 'germany:B-1bis_captured', 'germany:Maus',
    'germany:PanzerJager_I', 'germany:Pz35t',
    'germany:Pz38t', 'germany:PzII', 'germany:PzII_Luchs', 'germany:PzIII',
    'germany:PzIII_A', 'germany:PzIII_IV', 'germany:PzV',
    'germany:PzVIB_Tiger_II', 'germany:S35_captured', 'germany:StuGIII',
    'germany:Sturmpanzer_II', 'germany:VK1602', 'germany:VK3001H',
    'germany:VK3001P', 'germany:VK3002DB', 'germany:VK3601H',
    'germany:VK4502P', 'germany:Wespe', 'germany:PzVI', 'germany:PzIV'
]

_CFG = {
    'basic': {
        'v_start_angles': Math.Vector3(0, 0, 0),
        'v_start_pos':    Math.Vector3(50, 0, 50),
        'cam_start_dist': 9.0,
        'cam_start_angles':     [-25.0, 110.0],
        'cam_start_target_pos': Math.Vector3(50, 0, 50),
        'cam_dist_constr':  [6.0, 11.0],
        'cam_pitch_constr': [-70.0, -5.0],
        'cam_sens':       0.005,
        'cam_pivot_pos':  Math.Vector3(0, 1, 0),
        'cam_fluency':    0.05,
        'shadow_light_dir': (0.55, -1, -1.7)
    }
}

_V_START_ANGLES   = None
_V_START_POS      = None
_CAM_START_DIST   = None
_CAM_START_ANGLES = None
_CAM_START_TARGET_POS = None
_CAM_PIVOT_POS    = None
_CAM_FLUENCY      = None
_SHADOW_LIGHT_DIR = None

_TEAM_BASE_RADIUS = 25.0
_G_BASE_FLAG_POINTS = []
_FLAG_KEEP_R2 = 64.0
_BASE_ZONES = {
    '05_prohorovka': {
        1: {'pos': Math.Vector3(0, 0, -450), 'radius': _TEAM_BASE_RADIUS},
        2: {'pos': Math.Vector3(0, 0, 450),  'radius': _TEAM_BASE_RADIUS},
    },
    '06_ensk': {
        1: {'pos': Math.Vector3(0, 0, -250), 'radius': _TEAM_BASE_RADIUS},
        2: {'pos': Math.Vector3(0, 0, 250),  'radius': _TEAM_BASE_RADIUS},
    }
}

_MAP_SPAWNS_OLD = {
    '01_karelia': {
        1: [[372.27, 53.42, 388.37], [384.27, 53.42, 388.37], [396.27, 53.28, 388.37], [408.27, 53.4, 388.37], [420.27, 53.4, 388.37], [372.27, 53.58, 402.37], [384.27, 53.58, 402.37], [396.27, 53.63, 402.37], [408.27, 53.72, 402.37], [420.27, 53.72, 402.37], [372.27, 53.86, 416.37], [384.27, 53.86, 416.37], [396.27, 54.2, 416.37], [408.27, 54.15, 416.37], [420.27, 54.15, 416.37]],
        2: [[-429.14, 53.26, -412.27], [-417.14, 53.26, -412.27], [-405.14, 53.26, -412.27], [-393.14, 53.26, -412.27], [-381.14, 53.26, -412.27], [-429.14, 53.28, -398.27], [-417.14, 53.28, -398.27], [-405.14, 53.26, -398.27], [-393.14, 53.26, -398.27], [-381.14, 53.26, -398.27], [-429.14, 53.26, -384.27], [-417.14, 53.26, -384.27], [-405.14, 53.26, -384.27], [-393.14, 53.24, -384.27], [-381.14, 53.24, -384.27]],
    },
    '02_malinovka': {
        1: [[-396.7, 17.0, 94.1], [-384.7, 17.0, 94.1], [-372.7, 17.0, 94.1], [-360.7, 17.0, 94.1], [-348.7, 17.0, 94.1], [-396.7, 17.0, 108.1], [-384.7, 17.0, 108.1], [-372.7, 17.0, 108.1], [-360.7, 17.0, 108.1], [-348.7, 17.0, 108.1], [-396.7, 17.0, 122.1], [-384.7, 17.0, 122.1], [-372.7, 17.0, 122.1], [-360.7, 17.0, 122.1], [-348.7, 17.0, 122.1]],
        2: [[61.6, 15.0, -415.92], [75.6, 15.0, -415.92], [89.6, 15.0, -415.92], [61.6, 15.0, -403.92], [75.6, 15.0, -403.92], [89.6, 15.0, -403.92], [61.6, 15.0, -391.92], [75.6, 15.0, -391.92], [89.6, 15.0, -391.92], [61.6, 15.0, -379.92], [75.6, 15.0, -379.92], [89.6, 15.0, -379.92], [61.6, 15.0, -367.92], [75.6, 15.0, -367.92], [89.6, 15.0, -367.92]],
    },
    '04_himmelsdorf': {
        1: [[-40.0, 0.0, -270.0], [-20.0, 0.0, -270.0], [0.0, 0.0, -270.0], [20.0, 0.0, -270.0], [40.0, 0.0, -270.0], [-40.0, 0.0, -250.0], [-20.0, 0.0, -250.0], [0.0, 0.0, -250.0], [20.0, 0.0, -250.0], [40.0, 0.0, -250.0], [-40.0, 0.0, -230.0], [-20.0, 0.0, -230.0], [0.0, 0.0, -230.0], [20.0, 0.0, -230.0], [40.0, 0.0, -230.0]],
        2: [[30.0, -0.0, 330.0], [47.5, -0.0, 330.0], [65.0, -0.0, 330.0], [82.5, -0.0, 330.0], [100.0, -0.0, 330.0], [30.0, -0.0, 350.0], [47.5, -0.0, 350.0], [65.0, -0.0, 350.0], [82.5, -0.0, 350.0], [100.0, -0.0, 350.0], [30.0, -0.0, 370.0], [47.5, -0.0, 370.0], [65.0, -0.0, 370.0], [82.5, -0.0, 370.0], [100.0, -0.0, 370.0]],
    },
    '05_prohorovka': {
        1: [[50.0, 5.3, -445.0], [30.0, 5.3, -445.0], [10.0, 5.3, -445.0], [70.0, 5.3, -445.0], [90.0, 5.3, -445.0], [50.0, 5.3, -465.0], [30.0, 5.3, -465.0], [10.0, 5.3, -465.0], [70.0, 5.3, -465.0], [90.0, 5.3, -465.0], [50.0, 5.3, -425.0], [70.0, 5.3, -425.0], [90.0, 5.3, -425.0], [30.0, 5.3, -425.0], [10.0, 5.3, -425.0]],
        2: [[-125.0, 5.0, 450.0], [-145.0, 5.0, 450.0], [-165.0, 5.0, 450.0], [-105.0, 5.0, 450.0], [-85.0, 5.0, 450.0], [-125.0, 5.0, 430.0], [-105.0, 5.0, 430.0], [-85.0, 5.0, 430.0], [-145.0, 5.0, 430.0], [-165.0, 5.0, 430.0], [-125.0, 5.0, 470.0], [-105.0, 5.0, 470.0], [-85.0, 5.0, 470.0], [-165.0, 5.0, 470.0], [-145.0, 5.0, 470.0]],
    },
    '06_ensk': {
        1: [[-20.0, -0.0, -290.0], [0.0, -0.0, -290.0], [20.0, -0.0, -290.0], [40.0, -0.0, -290.0], [60.0, -0.0, -290.0], [-20.0, -0.0, -250.0], [0.0, -0.0, -250.0], [20.0, -0.0, -250.0], [40.0, -0.0, -250.0], [60.0, -0.0, -250.0], [-20.0, -0.0, -210.0], [0.0, -0.0, -210.0], [20.0, -0.0, -210.0], [40.0, -0.0, -210.0], [60.0, -0.0, -210.0]],
        2: [[20.0, 0.0, 250.0], [20.0, 0.0, 230.0], [0.0, 0.0, 230.0], [20.0, 0.0, 270.0], [0.0, 0.0, 250.0], [0.0, 0.0, 270.0], [-20.0, 0.0, 250.0], [-20.0, 0.0, 270.0], [-20.0, 0.0, 230.0], [40.0, 0.0, 250.0], [40.0, 0.0, 270.0], [40.0, 0.0, 230.0], [60.0, 0.0, 250.0], [60.0, 0.0, 270.0], [60.0, 0.0, 230.0]],
    },
    '07_lakeville': {
        1: [[-170.0, 12.0, -320.0], [-170.0, 12.0, -340.0], [-150.0, 12.0, -340.0], [-130.0, 12.0, -340.0], [-190.0, 12.0, -340.0], [-210.0, 12.0, -340.0], [-170.0, 12.0, -300.0], [-150.0, 12.0, -300.0], [-130.0, 12.0, -300.0], [-190.0, 12.0, -300.0], [-210.0, 12.0, -300.0], [-190.0, 12.0, -320.0], [-210.0, 12.0, -320.0], [-150.0, 12.0, -320.0], [-130.0, 12.0, -320.0]],
        2: [[-170.0, 12.0, 320.0], [-170.0, 12.0, 340.0], [-150.0, 12.0, 340.0], [-130.0, 12.0, 340.0], [-190.0, 12.0, 340.0], [-210.0, 12.0, 340.0], [-170.0, 12.0, 300.0], [-150.0, 12.0, 300.0], [-130.0, 12.0, 300.0], [-190.0, 12.0, 300.0], [-210.0, 12.0, 300.0], [-190.0, 12.0, 320.0], [-210.0, 12.0, 320.0], [-150.0, 12.0, 320.0], [-130.0, 12.0, 320.0]],
    },
    '11_murovanka': {
        1: [[-229.0, 0.0, -302.0], [-217.0, 0.0, -302.0], [-205.0, 0.0, -302.0], [-193.0, 0.0, -302.0], [-181.0, 0.0, -302.0], [-229.0, 0.0, -290.0], [-217.0, 0.0, -290.0], [-205.0, 0.0, -290.0], [-193.0, 0.0, -290.0], [-181.0, 0.0, -290.0], [-229.0, 0.0, -278.0], [-217.0, 0.0, -278.0], [-205.0, 0.0, -278.0], [-193.0, 0.0, -278.0], [-181.0, 0.0, -278.0]],
        2: [[176.5, 0.0, 283.0], [188.5, 0.0, 283.0], [200.5, 0.0, 283.0], [212.5, 0.0, 283.0], [224.5, 0.0, 283.0], [176.5, 0.0, 295.0], [188.5, 0.0, 295.0], [200.5, 0.0, 295.0], [212.5, 0.0, 295.0], [224.5, 0.0, 295.0], [176.5, 0.0, 307.0], [188.5, 0.0, 307.0], [200.5, 0.0, 307.0], [212.5, 0.0, 307.0], [224.5, 0.0, 307.0]],
    },
}

def _build_map_spawns():

    result = {}
    src = _REVIVED_SPAWN_POINTS or {}
    for k, teams in src.items():
        nk = str(k).replace("spaces/", "")
        converted = {}
        for team, pts in teams.items():
            t = int(team)
            if t in (1, 2):
                t = 3 - t
            converted[t] = [list(p) for p in pts]
        result[nk] = converted
    if result:
        return result
    return _MAP_SPAWNS_OLD

_MAP_SPAWNS = _build_map_spawns()

def _hex16(n):
    return '%04x' % (int(n) & 0xFFFF)

def _chunk_local_to_world(gx, gz, x, y, z):
    return (gx * 100.0 + x, y, gz * 100.0 + z)

def _read_transform_xyz(sec):
    try:
        m = sec.readMatrix('transform')
        if m is not None:
            t = m.translation
            return (t.x, t.y, t.z)
    except Exception:
        pass
    try:
        tr = sec['transform']
        if tr is not None:
            row3 = tr.readVector3('row3')
            if row3 is not None:
                return (row3.x, row3.y, row3.z)
    except Exception:
        pass
    return None

def _scan_chunk_for_bases(sec, gx, gz, out):
    if sec is None:
        return
    try:
        items = sec.items()
    except Exception:
        return
    for name, child in items:
        n = str(name)
        if n == 'UserDataObject':
            try:
                typ = child.readString('type', '').strip()
            except Exception:
                typ = ''
            if typ == 'TeamBase':
                xyz = _read_transform_xyz(child)
                if xyz is None:
                    continue
                wx, wy, wz = _chunk_local_to_world(gx, gz, xyz[0], xyz[1], xyz[2])
                team = 0
                radius = _TEAM_BASE_RADIUS
                try:
                    props = child['properties']
                    if props is not None:
                        team = int(props.readInt('team', 0) or 0)
                        try:
                            radius = float(props.readFloat('radius', _TEAM_BASE_RADIUS))
                        except Exception:
                            radius = _TEAM_BASE_RADIUS
                except Exception:
                    pass
                if team in (1, 2):
                    out.append((team, Math.Vector3(wx, wy, wz), radius))
            continue
        if n == 'model':
            res = ''
            try:
                res = child.readString('resource', '')
            except Exception:
                res = ''
            res_l = (res or '').lower()
            if 'flagstaff' in res_l or 'flagstaf' in res_l:
                xyz = _read_transform_xyz(child)
                if xyz is None:
                    continue
                wx, wy, wz = _chunk_local_to_world(gx, gz, xyz[0], xyz[1], xyz[2])
                team = 0
                if 'blue' in res_l:
                    team = 1
                elif 'red' in res_l:
                    team = 2
                if team in (1, 2):
                    out.append((team, Math.Vector3(wx, wy, wz), _TEAM_BASE_RADIUS))
            continue
        try:
            if child is not None and hasattr(child, 'items'):
                _scan_chunk_for_bases(child, gx, gz, out)
        except Exception:
            pass

def _extract_team_bases_from_chunks(arena_id):

    found = []
    if not arena_id:
        return found
    try:
        import ResMgr
    except Exception:
        return found
    space = 'spaces/' + str(arena_id).replace('spaces/', '')
    if ResMgr.openSection(space) is None:
        return found
    bounds = _MAP_BOUNDS.get(str(arena_id).replace('spaces/', ''), _DEFAULT_MAP_BOUNDS)
    min_gx = int(_math.floor(bounds[0] / 100.0))
    max_gx = int(_math.ceil(bounds[2] / 100.0)) - 1
    min_gz = int(_math.floor(bounds[1] / 100.0))
    max_gz = int(_math.ceil(bounds[3] / 100.0)) - 1
    if max_gx - min_gx > 30 or max_gz - min_gz > 30:
        min_gx, max_gx, min_gz, max_gz = -8, 8, -8, 8
    gx = min_gx
    while gx <= max_gx:
        gz = min_gz
        while gz <= max_gz:
            for suffix in ('o.chunk', '.chunk'):
                path = '%s/%s%s%s' % (space, _hex16(gx), _hex16(gz), suffix)
                try:
                    chunk = ResMgr.openSection(path)
                except Exception:
                    chunk = None
                if chunk is not None:
                    _scan_chunk_for_bases(chunk, gx, gz, found)
                    break
            gz += 1
        gx += 1
    return found

def _pair_bases_to_spawn_teams(extracted, arena_id):

    spawn_data = _MAP_SPAWNS.get(arena_id) or {}
    centroids = {}
    for team, coords in spawn_data.items():
        if not coords:
            continue
        centroids[int(team)] = (
            sum(c[0] for c in coords) / float(len(coords)),
            sum(c[2] for c in coords) / float(len(coords)),
        )
    zones = {}
    for team, pos, radius in extracted:
        if centroids:
            best = None
            best_d = None
            for st, (sx, sz) in centroids.items():
                dx = pos.x - sx
                dz = pos.z - sz
                d = dx * dx + dz * dz
                if best_d is None or d < best_d:
                    best_d = d
                    best = st
            team = best if best is not None else team
        if team in zones:
            continue
        zones[team] = {'pos': pos, 'radius': float(radius) or _TEAM_BASE_RADIUS, 'baseID': 1}
    return zones

_MAP_BOUNDS = {
    '01_karelia':     (-500.0, -500.0, 500.0, 500.0),
    '02_malinovka':   (-500.0, -500.0, 500.0, 500.0),
    '04_himmelsdorf': (-300.0, -300.0, 400.0, 400.0),
    '05_prohorovka':  (-500.0, -500.0, 500.0, 500.0),
    '06_ensk':        (-300.0, -300.0, 300.0, 300.0),
    '07_lakeville':   (-400.0, -400.0, 400.0, 400.0),
    '11_murovanka':   (-400.0, -400.0, 400.0, 400.0),
}
_DEFAULT_MAP_BOUNDS = (-450.0, -450.0, 450.0, 450.0)
_MAP_BOUNDS_MARGIN = 50.0
_MAP_EDGE_INSET = 20.0

_MAP_MUSIC_INTRO = {
    '01_karelia':     '/music/intro/rus_village',
    '02_malinovka':   '/music/intro/Malinovka',
    '04_himmelsdorf': '/music/intro/Himmelsdorf',
    '05_prohorovka':  '/music/intro/rus_village',
    '06_ensk':        '/music/intro/rus_city',
    '07_lakeville':   '/music/intro/euro_village',
    '11_murovanka':   '/music/intro/rus_village',
}
_MAP_MUSIC_DEFAULT_INTRO = '/music/intro/rus_village'
_MAP_MUSIC_COMBAT = '/music/combat/combat'

_MUSIC_GROUPS = (
    '/music/combat',
    '/music/intro',
    '/music/victory',
    '/music/defeat',
    '/music/drawn_game',
    '/ambient/wind',
)

_BOT_AI_INTERVAL = 0.85
_BOT_MOVE_LOD_DIST2 = 220.0 * 220.0

ROUTES = {
    '01_karelia': {
        'HT': [
            [
                {'id': 'gorge_entry',      'type': 'transit', 'lane_frac': -0.70, 'depth_frac': 0.16, 'desc': 'въезд в ущелье'},
                {'id': 'gorge_mid_cover',  'type': 'hold',    'lane_frac': -0.75, 'depth_frac': 0.40, 'desc': 'за скальным выступом, середина ущелья'},
                {'id': 'gorge_front_hold', 'type': 'hold',    'lane_frac': -0.80, 'depth_frac': 0.62, 'desc': 'точка подствольного огня в глубине'},
                {'id': 'gorge_deep',       'type': 'transit', 'lane_frac': -0.78, 'depth_frac': 0.80, 'desc': 'выход к базе противника'},
            ],
            [
                {'id': 'plateau_approach', 'type': 'transit', 'lane_frac': 0.60, 'depth_frac': 0.18, 'desc': 'подход к плато'},
                {'id': 'plateau_crest',    'type': 'hold',    'lane_frac': 0.75, 'depth_frac': 0.45, 'desc': 'гребень плато, hull-down'},
                {'id': 'plateau_watch',    'type': 'hold',    'lane_frac': 0.80, 'depth_frac': 0.65, 'desc': 'обзор на базу противника'},
            ],
            [
                {'id': 'road_climb',  'type': 'transit', 'lane_frac': -0.10, 'depth_frac': 0.22, 'desc': 'западная дорога'},
                {'id': 'road_hold',   'type': 'hold',     'lane_frac': 0.05,  'depth_frac': 0.48, 'desc': 'позиция у дороги'},
                {'id': 'road_advance','type': 'hold',     'lane_frac': 0.10,  'depth_frac': 0.70, 'desc': 'продвижение к флангу базы'},
            ],
        ],
        'MT': [
            [
                {'id': 'plateau_climb', 'type': 'transit', 'lane_frac': 0.70, 'depth_frac': 0.18, 'desc': 'серпантин на плато'},
                {'id': 'plateau_crest', 'type': 'hold',     'lane_frac': 0.75, 'depth_frac': 0.45, 'desc': 'гребень, hull-down'},
                {'id': 'plateau_watch', 'type': 'hold',     'lane_frac': 0.80, 'depth_frac': 0.65, 'desc': 'обзор на центр и фланг базы врага'},
            ],
            [
                {'id': 'gorge_side',   'type': 'transit', 'lane_frac': -0.55, 'depth_frac': 0.22, 'desc': 'боковое ущелье'},
                {'id': 'gorge_crest',  'type': 'hold',     'lane_frac': -0.60, 'depth_frac': 0.50, 'desc': 'гребень над ущельем'},
                {'id': 'gorge_deep',   'type': 'hold',     'lane_frac': -0.65, 'depth_frac': 0.75, 'desc': 'глубокий фланг'},
            ],
            [
                {'id': 'swamp_road',  'type': 'transit', 'lane_frac': 0.00, 'depth_frac': 0.20, 'desc': 'дорога к болоту'},
                {'id': 'swamp_spot',  'type': 'spot',     'lane_frac': 0.00, 'depth_frac': 0.42, 'desc': 'засвет у болота'},
                {'id': 'swamp_adv',   'type': 'hold',     'lane_frac': 0.05, 'depth_frac': 0.70, 'desc': 'к центру базы противника'},
            ],
        ],
        'LT': [
            [
                {'id': 'swamp_edge', 'type': 'transit', 'lane_frac': 0.05, 'depth_frac': 0.16, 'desc': 'каменистая дорога, край болота'},
                {'id': 'swamp_spot', 'type': 'spot',     'lane_frac': 0.00, 'depth_frac': 0.35, 'desc': 'кратковременный засвет в центре'},
                {'id': 'deep_spot',  'type': 'spot',     'lane_frac': 0.03, 'depth_frac': 0.60, 'desc': 'дальний засвет у их базы'},
                {'id': 'fallback_pt','type': 'hold',     'lane_frac': 0.10, 'depth_frac': 0.20, 'desc': 'откат к своему плато/ущелью'},
            ],
            [
                {'id': 'flank_up',   'type': 'transit', 'lane_frac': 0.65, 'depth_frac': 0.20, 'desc': 'подъём на фланге'},
                {'id': 'flank_spot', 'type': 'spot',     'lane_frac': 0.70, 'depth_frac': 0.50, 'desc': 'засвет со стороны плато'},
                {'id': 'flank_deep', 'type': 'hold',     'lane_frac': 0.72, 'depth_frac': 0.78, 'desc': 'глубокий фланг к их спавну'},
            ],
        ],
        'TD': [
            [
                {'id': 'base_shelf', 'type': 'ambush', 'lane_frac': -0.30, 'depth_frac': 0.12, 'desc': 'полка/балкон у базы'},
            ],
            [
                {'id': 'gorge_pt',   'type': 'ambush', 'lane_frac': -0.60, 'depth_frac': 0.35, 'desc': 'засада в ущелье'},
                {'id': 'gorge_pt2',  'type': 'ambush', 'lane_frac': -0.65, 'depth_frac': 0.55, 'desc': 'глубокая засада'},
            ],
        ],
        'SPG': [
            [
                {'id': 'corner_fire', 'type': 'fire', 'lane_frac': -0.85, 'depth_frac': 0.08, 'desc': 'угол карты за камнями'},
            ],
        ],
    },
    '04_himmelsdorf': {
        'HT': [
            [
                {'id': 'banana_approach', 'type': 'transit', 'lane_frac': -0.65, 'depth_frac': 0.16, 'desc': 'улица у подножия горы (В)'},
                {'id': 'banana_corner',   'type': 'hold',     'lane_frac': -0.70, 'depth_frac': 0.45, 'desc': 'угол для ближнего боя'},
                {'id': 'banana_deep',     'type': 'hold',     'lane_frac': -0.72, 'depth_frac': 0.70, 'desc': 'в глубину городских кварталов'},
            ],
            [
                {'id': 'rail_ht',      'type': 'transit', 'lane_frac': 0.55, 'depth_frac': 0.20, 'desc': 'подход вдоль ж/д'},
                {'id': 'rail_ht_corner','type': 'hold',   'lane_frac': 0.60, 'depth_frac': 0.50, 'desc': 'открытый фланг, прострел улиц'},
                {'id': 'rail_ht_deep', 'type': 'hold',     'lane_frac': 0.62, 'depth_frac': 0.75, 'desc': 'давление на их базу с запада'},
            ],
            [
                {'id': 'central_ht',  'type': 'transit', 'lane_frac': 0.10, 'depth_frac': 0.25, 'desc': 'через центр города'},
                {'id': 'central_hold','type': 'hold',     'lane_frac': 0.05, 'depth_frac': 0.50, 'desc': 'площадь, держит проход'},
                {'id': 'central_deep','type': 'hold',     'lane_frac': 0.08, 'depth_frac': 0.72, 'desc': 'к их спавну центром'},
            ],
        ],
        'MT': [
            [
                {'id': 'mountain_climb', 'type': 'transit', 'lane_frac': -0.50, 'depth_frac': 0.18, 'desc': 'подъём на гору'},
                {'id': 'castle_crest',   'type': 'hold',     'lane_frac': -0.60, 'depth_frac': 0.48, 'desc': 'руины замка, вершина'},
                {'id': 'rear_flank',     'type': 'hold',     'lane_frac': -0.55, 'depth_frac': 0.70, 'desc': 'спуск в тыл врагу'},
            ],
            [
                {'id': 'mid_climb',  'type': 'transit', 'lane_frac': 0.00, 'depth_frac': 0.22, 'desc': 'центральные улицы'},
                {'id': 'mid_hold',   'type': 'hold',     'lane_frac': 0.05, 'depth_frac': 0.50, 'desc': 'перекрёсток, держит проход'},
                {'id': 'mid_deep',   'type': 'hold',     'lane_frac': 0.02, 'depth_frac': 0.75, 'desc': 'к базе противника'},
            ],
            [
                {'id': 'rail_mt',    'type': 'transit', 'lane_frac': 0.60, 'depth_frac': 0.20, 'desc': 'вдоль ж/д путей'},
                {'id': 'rail_mt_hold','type': 'hold',   'lane_frac': 0.65, 'depth_frac': 0.50, 'desc': 'ж/д фланг'},
                {'id': 'rail_mt_deep','type': 'hold',   'lane_frac': 0.62, 'depth_frac': 0.75, 'desc': 'заход в тыл'},
            ],
        ],
        'LT': [
            [
                {'id': 'rail_approach', 'type': 'transit', 'lane_frac': 0.60, 'depth_frac': 0.15, 'desc': 'вдоль ж/д путей'},
                {'id': 'rail_sight',    'type': 'spot',     'lane_frac': 0.65, 'depth_frac': 0.40, 'desc': 'за вагоном, простреливает улицу'},
                {'id': 'rail_deep_spot','type': 'spot',     'lane_frac': 0.60, 'depth_frac': 0.65, 'desc': 'дальний засвет по ж/д'},
                {'id': 'rail_fallback', 'type': 'hold',     'lane_frac': 0.55, 'depth_frac': 0.20, 'desc': 'укрытие на западном фланге'},
            ],
            [
                {'id': 'lt_climb',  'type': 'transit', 'lane_frac': -0.45, 'depth_frac': 0.20, 'desc': 'подъём на гору'},
                {'id': 'lt_spot',   'type': 'spot',     'lane_frac': -0.50, 'depth_frac': 0.50, 'desc': 'засвет с вершины'},
                {'id': 'lt_deep',   'type': 'hold',     'lane_frac': -0.45, 'depth_frac': 0.78, 'desc': 'рейд к их арте'},
            ],
        ],
        'TD': [
            [
                {'id': 'street_hold', 'type': 'ambush', 'lane_frac': -0.30, 'depth_frac': 0.15, 'desc': 'линия 3 или 7 у базы'},
            ],
            [
                {'id': 'rail_td',  'type': 'ambush', 'lane_frac': 0.55, 'depth_frac': 0.30, 'desc': 'засада вдоль ж/д'},
                {'id': 'rail_td2', 'type': 'ambush', 'lane_frac': 0.60, 'depth_frac': 0.55, 'desc': 'глубокая засада на ж/д'},
            ],
        ],
        'SPG': [
            [
                {'id': 'rail_or_corner_fire', 'type': 'fire', 'lane_frac': 0.30, 'depth_frac': 0.08, 'desc': 'ж/д или угол базы'},
            ],
        ],
    },
    '07_lakeville': {
        'HT': [
            [
                {'id': 'city_approach',   'type': 'transit', 'lane_frac': 0.65, 'depth_frac': 0.16, 'desc': 'въезд в город'},
                {'id': 'city_square',     'type': 'hold',     'lane_frac': 0.70, 'depth_frac': 0.45, 'desc': 'угол площади/церкви'},
                {'id': 'city_brawl_hold', 'type': 'hold',     'lane_frac': 0.75, 'depth_frac': 0.62, 'desc': 'контактный бой в улочке'},
                {'id': 'city_deep',       'type': 'transit', 'lane_frac': 0.73, 'depth_frac': 0.80, 'desc': 'к базе противника городом'},
            ],
            [
                {'id': 'field_ht',   'type': 'transit', 'lane_frac': 0.15, 'depth_frac': 0.22, 'desc': 'через центральное поле'},
                {'id': 'field_hold', 'type': 'hold',     'lane_frac': 0.10, 'depth_frac': 0.50, 'desc': 'центр, держит переправу'},
                {'id': 'field_deep', 'type': 'hold',     'lane_frac': 0.12, 'depth_frac': 0.72, 'desc': 'выход к их городу'},
            ],
            [
                {'id': 'lake_ht',  'type': 'transit', 'lane_frac': -0.55, 'depth_frac': 0.20, 'desc': 'вдоль озера'},
                {'id': 'lake_hold','type': 'hold',     'lane_frac': -0.60, 'depth_frac': 0.50, 'desc': 'за береговыми укрытиями'},
                {'id': 'lake_deep','type': 'hold',     'lane_frac': -0.58, 'depth_frac': 0.75, 'desc': 'фланг по озеру'},
            ],
        ],
        'MT': [
            [
                {'id': 'city_support', 'type': 'transit', 'lane_frac': 0.50, 'depth_frac': 0.18, 'desc': 'вторая линия за ТТ'},
                {'id': 'flex_point',   'type': 'hold',     'lane_frac': 0.50, 'depth_frac': 0.42, 'desc': 'развилка - город либо ущелье по свету ЛТ'},
                {'id': 'city_support2','type': 'hold',     'lane_frac': 0.55, 'depth_frac': 0.65, 'desc': 'глубокая вторая линия'},
            ],
            [
                {'id': 'mt_gorge',  'type': 'transit', 'lane_frac': -0.60, 'depth_frac': 0.20, 'desc': 'подход к ущелью'},
                {'id': 'mt_gorge2', 'type': 'hold',     'lane_frac': -0.65, 'depth_frac': 0.50, 'desc': 'в ущелье'},
                {'id': 'mt_gorge3', 'type': 'hold',     'lane_frac': -0.62, 'depth_frac': 0.75, 'desc': 'выход к их базе'},
            ],
            [
                {'id': 'mt_lake',  'type': 'transit', 'lane_frac': -0.20, 'depth_frac': 0.22, 'desc': 'вдоль озера центром'},
                {'id': 'mt_lake2', 'type': 'hold',     'lane_frac': -0.15, 'depth_frac': 0.52, 'desc': 'береговая позиция'},
                {'id': 'mt_lake3', 'type': 'hold',     'lane_frac': -0.10, 'depth_frac': 0.75, 'desc': 'фланг к их базе'},
            ],
        ],
        'LT': [
            [
                {'id': 'kishka_entry',  'type': 'transit', 'lane_frac': 0.05, 'depth_frac': 0.15, 'desc': 'вход в прибрежный коридор'},
                {'id': 'kishka_spot',   'type': 'spot',     'lane_frac': 0.00, 'depth_frac': 0.38, 'desc': 'точка света - скала/вода'},
                {'id': 'kishka_deep',   'type': 'spot',     'lane_frac': 0.02, 'depth_frac': 0.62, 'desc': 'дальний засвет по коридору'},
                {'id': 'immediate_fallback', 'type': 'hold','lane_frac': 0.05, 'depth_frac': 0.18, 'desc': 'откат сразу по контакту'},
            ],
            [
                {'id': 'lt_city',  'type': 'transit', 'lane_frac': 0.60, 'depth_frac': 0.20, 'desc': 'в город'},
                {'id': 'lt_city2', 'type': 'spot',     'lane_frac': 0.65, 'depth_frac': 0.50, 'desc': 'засвет в городе'},
                {'id': 'lt_city3', 'type': 'hold',     'lane_frac': 0.60, 'depth_frac': 0.78, 'desc': 'рейд к их спавну'},
            ],
        ],
        'TD': [
            [
                {'id': 'gorge_approach', 'type': 'transit', 'lane_frac': -0.60, 'depth_frac': 0.15, 'desc': 'подход к ущелью'},
                {'id': 'gorge_ambush',   'type': 'ambush',   'lane_frac': -0.70, 'depth_frac': 0.40, 'desc': 'засада на выходе из ущелья'},
            ],
            [
                {'id': 'td_city',  'type': 'ambush', 'lane_frac': 0.55, 'depth_frac': 0.30, 'desc': 'засада в городе'},
                {'id': 'td_city2', 'type': 'ambush', 'lane_frac': 0.60, 'depth_frac': 0.55, 'desc': 'глубокая городская засада'},
            ],
        ],
        'SPG': [
            [
                {'id': 'base_fire', 'type': 'fire', 'lane_frac': 0.15, 'depth_frac': 0.08, 'desc': 'прикрывает кишку и город'},
            ],
        ],
    },
    '11_murovanka': {
        'HT': [
            [
                {'id': 'village_approach', 'type': 'transit', 'lane_frac': 0.05, 'depth_frac': 0.16, 'desc': 'осторожный подход к деревне'},
                {'id': 'village_cover',    'type': 'hold',     'lane_frac': 0.00, 'depth_frac': 0.40, 'desc': 'за стеной разрушенной церкви'},
                {'id': 'village_deep',     'type': 'hold',     'lane_frac': 0.03, 'depth_frac': 0.65, 'desc': 'через всю деревню к их базе'},
            ],
            [
                {'id': 'ht_hill',  'type': 'transit', 'lane_frac': 0.55, 'depth_frac': 0.20, 'desc': 'подход к холмам'},
                {'id': 'ht_hill2', 'type': 'hold',     'lane_frac': 0.60, 'depth_frac': 0.48, 'desc': 'на холмах'},
                {'id': 'ht_hill3', 'type': 'hold',     'lane_frac': 0.58, 'depth_frac': 0.72, 'desc': 'фланг холмов к их базе'},
            ],
            [
                {'id': 'ht_forest',  'type': 'transit', 'lane_frac': -0.55, 'depth_frac': 0.22, 'desc': 'край леса'},
                {'id': 'ht_forest2', 'type': 'hold',     'lane_frac': -0.60, 'depth_frac': 0.50, 'desc': 'в лесу'},
                {'id': 'ht_forest3', 'type': 'hold',     'lane_frac': -0.57, 'depth_frac': 0.74, 'desc': 'продвижение лесом'},
            ],
        ],
        'MT': [
            [
                {'id': 'hills_approach', 'type': 'transit', 'lane_frac': 0.60, 'depth_frac': 0.18, 'desc': 'холмы'},
                {'id': 'hills_hold',     'type': 'hold',     'lane_frac': 0.65, 'depth_frac': 0.45, 'desc': 'точка на пересечённой местности'},
                {'id': 'hills_deep',     'type': 'hold',     'lane_frac': 0.62, 'depth_frac': 0.72, 'desc': 'глубокий холм фланг'},
            ],
            [
                {'id': 'mt_village', 'type': 'transit', 'lane_frac': 0.00, 'depth_frac': 0.20, 'desc': 'в деревню'},
                {'id': 'mt_village2','type': 'hold',     'lane_frac': 0.02, 'depth_frac': 0.48, 'desc': 'за домами деревни'},
                {'id': 'mt_village3','type': 'hold',     'lane_frac': 0.00, 'depth_frac': 0.72, 'desc': 'к их базе деревней'},
            ],
            [
                {'id': 'mt_forest', 'type': 'transit', 'lane_frac': -0.55, 'depth_frac': 0.22, 'desc': 'по лесу'},
                {'id': 'mt_forest2','type': 'hold',     'lane_frac': -0.60, 'depth_frac': 0.50, 'desc': 'в лесу на фланге'},
                {'id': 'mt_forest3','type': 'hold',     'lane_frac': -0.57, 'depth_frac': 0.74, 'desc': 'продвижение лесом'},
            ],
        ],
        'LT': [
            [
                {'id': 'lt_spot',  'type': 'transit', 'lane_frac': 0.30, 'depth_frac': 0.16, 'desc': 'свет с холмов/деревни'},
                {'id': 'lt_spot2', 'type': 'spot',     'lane_frac': 0.25, 'depth_frac': 0.40, 'desc': 'засвет в лесу'},
                {'id': 'lt_deep',  'type': 'hold',     'lane_frac': 0.20, 'depth_frac': 0.70, 'desc': 'глубокий засвет к их спавну'},
            ],
        ],
        'TD': [
            [
                {'id': 'forest_edge',   'type': 'transit', 'lane_frac': -0.60, 'depth_frac': 0.16, 'desc': 'край леса'},
                {'id': 'forest_ambush', 'type': 'ambush',   'lane_frac': -0.70, 'depth_frac': 0.42, 'desc': 'глубокая точка, маскировка'},
            ],
            [
                {'id': 'td_hill',  'type': 'ambush', 'lane_frac': 0.55, 'depth_frac': 0.30, 'desc': 'засада на холмах'},
                {'id': 'td_hill2', 'type': 'ambush', 'lane_frac': 0.60, 'depth_frac': 0.52, 'desc': 'глубокая засада на холмах'},
            ],
        ],
        'SPG': [
            [
                {'id': 'base_fire', 'type': 'fire', 'lane_frac': 0.20, 'depth_frac': 0.08, 'desc': 'навесом по холмам/деревне, по лесу вслепую'},
            ],
        ],
    },
    '02_malinovka': {
        'HT': [
            [
                {'id': 'hill_approach', 'type': 'transit', 'lane_frac': 0.60, 'depth_frac': 0.16, 'desc': 'подъём на гору с мельницей'},
                {'id': 'mill_crest',    'type': 'hold',     'lane_frac': 0.65, 'depth_frac': 0.45, 'desc': 'вершина, hull-down у мельницы'},
                {'id': 'rear_descent',  'type': 'hold',     'lane_frac': 0.55, 'depth_frac': 0.68, 'desc': 'спуск во фланг базе врага'},
            ],
            [
                {'id': 'field_ht',   'type': 'transit', 'lane_frac': 0.02, 'depth_frac': 0.20, 'desc': 'рывок через поле'},
                {'id': 'field_hold', 'type': 'hold',     'lane_frac': 0.00, 'depth_frac': 0.50, 'desc': 'центр поля'},
                {'id': 'field_deep', 'type': 'hold',     'lane_frac': 0.03, 'depth_frac': 0.72, 'desc': 'к их базе полем'},
            ],
            [
                {'id': 'barn_ht',  'type': 'transit', 'lane_frac': -0.45, 'depth_frac': 0.22, 'desc': 'через коровники'},
                {'id': 'barn_hold','type': 'hold',     'lane_frac': -0.50, 'depth_frac': 0.50, 'desc': 'за коровниками'},
                {'id': 'barn_deep','type': 'hold',     'lane_frac': -0.48, 'depth_frac': 0.74, 'desc': 'фланг к их базе'},
            ],
        ],
        'MT': [
            [
                {'id': 'village_approach', 'type': 'transit', 'lane_frac': 0.25, 'depth_frac': 0.18, 'desc': 'деревня между полем и горой'},
                {'id': 'village_cover',    'type': 'hold',     'lane_frac': 0.20, 'depth_frac': 0.45, 'desc': 'за домом вдоль дороги'},
                {'id': 'village_deep',     'type': 'hold',     'lane_frac': 0.22, 'depth_frac': 0.70, 'desc': 'через деревню к их базе'},
            ],
            [
                {'id': 'mt_hill',  'type': 'transit', 'lane_frac': 0.60, 'depth_frac': 0.20, 'desc': 'подъём на гору'},
                {'id': 'mt_hill2', 'type': 'hold',     'lane_frac': 0.62, 'depth_frac': 0.50, 'desc': 'на горной дороге'},
                {'id': 'mt_hill3', 'type': 'hold',     'lane_frac': 0.58, 'depth_frac': 0.72, 'desc': 'заход по верху к их базе'},
            ],
        ],
        'LT': [
            [
                {'id': 'field_rush',         'type': 'transit', 'lane_frac': 0.02, 'depth_frac': 0.16, 'desc': 'рывок через поле к болоту'},
                {'id': 'field_spot',         'type': 'spot',     'lane_frac': 0.00, 'depth_frac': 0.34, 'desc': 'точка света на середине'},
                {'id': 'deep_spot',          'type': 'spot',     'lane_frac': 0.03, 'depth_frac': 0.60, 'desc': 'дальний засвет полем'},
                {'id': 'immediate_fallback', 'type': 'hold',     'lane_frac': 0.05, 'depth_frac': 0.16, 'desc': 'откат сразу'},
            ],
            [
                {'id': 'lt_hill',  'type': 'transit', 'lane_frac': 0.60, 'depth_frac': 0.18, 'desc': 'подъём на гору'},
                {'id': 'lt_spot',  'type': 'spot',     'lane_frac': 0.65, 'depth_frac': 0.50, 'desc': 'засвет с горы'},
                {'id': 'lt_deep',  'type': 'hold',     'lane_frac': 0.60, 'depth_frac': 0.78, 'desc': 'рейд к их спавну'},
            ],
        ],
        'TD': [
            [
                {'id': 'base_bush', 'type': 'ambush', 'lane_frac': -0.20, 'depth_frac': 0.10, 'desc': 'кусты у своей базы'},
            ],
            [
                {'id': 'td_hill',  'type': 'ambush', 'lane_frac': 0.65, 'depth_frac': 0.30, 'desc': 'засада на горе'},
                {'id': 'td_hill2', 'type': 'ambush', 'lane_frac': 0.62, 'depth_frac': 0.52, 'desc': 'глубокая засада на горе'},
            ],
        ],
        'SPG': [
            [
                {'id': 'base_fire', 'type': 'fire', 'lane_frac': 0.10, 'depth_frac': 0.06, 'desc': 'почти вся карта, кроме обратных скатов горы'},
            ],
        ],
    },
    '05_prohorovka': {
        'HT': [
            [
                {'id': 'proh_ht_w',   'type': 'transit', 'lane_frac': -0.60, 'depth_frac': 0.18, 'desc': 'подход к западной гряде'},
                {'id': 'proh_ht_w2',  'type': 'hold',     'lane_frac': -0.65, 'depth_frac': 0.45, 'desc': 'на западной гряде, hull-down'},
                {'id': 'proh_ht_w3',  'type': 'hold',     'lane_frac': -0.62, 'depth_frac': 0.68, 'desc': 'глубина западной гряды'},
            ],
            [
                {'id': 'proh_ht_e',  'type': 'transit', 'lane_frac': 0.55, 'depth_frac': 0.20, 'desc': 'шоу в восточном лесу'},
                {'id': 'proh_ht_e2', 'type': 'hold',     'lane_frac': 0.60, 'depth_frac': 0.48, 'desc': 'за деревьями у ж/д'},
                {'id': 'proh_ht_e3', 'type': 'hold',     'lane_frac': 0.58, 'depth_frac': 0.72, 'desc': 'давление к их базе с востока'},
            ],
            [
                {'id': 'proh_ht_c',  'type': 'transit', 'lane_frac': 0.00, 'depth_frac': 0.22, 'desc': 'по центральной лощине'},
                {'id': 'proh_ht_c2', 'type': 'hold',     'lane_frac': 0.05, 'depth_frac': 0.50, 'desc': 'середина лощины, укрытие'},
                {'id': 'proh_ht_c3', 'type': 'hold',     'lane_frac': 0.03, 'depth_frac': 0.72, 'desc': 'переправа к их базе'},
            ],
        ],
        'MT': [
            [
                {'id': 'proh_mt_c',  'type': 'transit', 'lane_frac': 0.00, 'depth_frac': 0.18, 'desc': 'в центральную лощину'},
                {'id': 'proh_mt_c2', 'type': 'hold',     'lane_frac': 0.05, 'depth_frac': 0.45, 'desc': 'гребень гряды, обзор на обе стороны'},
                {'id': 'proh_mt_c3', 'type': 'hold',     'lane_frac': 0.00, 'depth_frac': 0.68, 'desc': 'к их базе по центру'},
            ],
            [
                {'id': 'proh_mt_w',  'type': 'transit', 'lane_frac': -0.55, 'depth_frac': 0.20, 'desc': 'западная гряда'},
                {'id': 'proh_mt_w2', 'type': 'hold',     'lane_frac': -0.60, 'depth_frac': 0.48, 'desc': 'на гряде, кусты'},
                {'id': 'proh_mt_w3', 'type': 'hold',     'lane_frac': -0.57, 'depth_frac': 0.72, 'desc': 'фланг по гряде'},
            ],
            [
                {'id': 'proh_mt_e',  'type': 'transit', 'lane_frac': 0.55, 'depth_frac': 0.22, 'desc': 'восточный лес'},
                {'id': 'proh_mt_e2', 'type': 'hold',     'lane_frac': 0.60, 'depth_frac': 0.50, 'desc': 'в лесу'},
                {'id': 'proh_mt_e3', 'type': 'hold',     'lane_frac': 0.57, 'depth_frac': 0.74, 'desc': 'продвижение лесом'},
            ],
        ],
        'LT': [
            [
                {'id': 'proh_lt_c',  'type': 'transit', 'lane_frac': 0.00, 'depth_frac': 0.16, 'desc': 'вперёд по лощине'},
                {'id': 'proh_lt_c2', 'type': 'spot',     'lane_frac': 0.00, 'depth_frac': 0.35, 'desc': 'центральный засвет'},
                {'id': 'proh_lt_c3', 'type': 'spot',     'lane_frac': 0.03, 'depth_frac': 0.60, 'desc': 'глубокий засвет у их базы'},
            ],
            [
                {'id': 'proh_lt_e',  'type': 'transit', 'lane_frac': 0.55, 'depth_frac': 0.18, 'desc': 'вдоль ж/д на восток'},
                {'id': 'proh_lt_e2', 'type': 'spot',     'lane_frac': 0.60, 'depth_frac': 0.45, 'desc': 'засвет у ж/д'},
                {'id': 'proh_lt_e3', 'type': 'hold',     'lane_frac': 0.57, 'depth_frac': 0.75, 'desc': 'рейд к их спавну'},
            ],
        ],
        'TD': [
            [
                {'id': 'proh_td_w',  'type': 'ambush', 'lane_frac': -0.55, 'depth_frac': 0.18, 'desc': 'гряда у своей базы, кусты'},
                {'id': 'proh_td_w2', 'type': 'ambush', 'lane_frac': -0.60, 'depth_frac': 0.42, 'desc': 'глубокая засада на гряде'},
            ],
            [
                {'id': 'proh_td_e',  'type': 'ambush', 'lane_frac': 0.55, 'depth_frac': 0.20, 'desc': 'засада в восточном лесу'},
                {'id': 'proh_td_e2', 'type': 'ambush', 'lane_frac': 0.60, 'depth_frac': 0.46, 'desc': 'глубокая засада в лесу'},
            ],
        ],
        'SPG': [
            [
                {'id': 'proh_spg', 'type': 'fire', 'lane_frac': 0.20, 'depth_frac': 0.08, 'desc': 'за грядой, навесом по обеим сторонам'},
            ],
        ],
    },
    '06_ensk': {
        'HT': [
            [
                {'id': 'city_approach', 'type': 'transit', 'lane_frac': 0.60, 'depth_frac': 0.16, 'desc': 'улицы'},
                {'id': 'street_corner', 'type': 'hold',     'lane_frac': 0.65, 'depth_frac': 0.45, 'desc': "угол здания, дуэль 'качели'"},
                {'id': 'street_deep',   'type': 'hold',     'lane_frac': 0.62, 'depth_frac': 0.70, 'desc': 'в глубину города к их базе'},
            ],
            [
                {'id': 'ht_main',  'type': 'transit', 'lane_frac': 0.10, 'depth_frac': 0.20, 'desc': 'главная улица'},
                {'id': 'ht_main2','type': 'hold',     'lane_frac': 0.05, 'depth_frac': 0.50, 'desc': 'центр города'},
                {'id': 'ht_main3','type': 'hold',     'lane_frac': 0.08, 'depth_frac': 0.72, 'desc': 'к их базе центром'},
            ],
        ],
        'MT': [
            [
                {'id': 'railyard_approach', 'type': 'transit', 'lane_frac': 0.05, 'depth_frac': 0.18, 'desc': 'вокзал'},
                {'id': 'railyard_cover',    'type': 'hold',     'lane_frac': 0.00, 'depth_frac': 0.45, 'desc': 'между составами'},
                {'id': 'railyard_deep',     'type': 'hold',     'lane_frac': 0.03, 'depth_frac': 0.68, 'desc': 'через вокзал к их базе'},
            ],
            [
                {'id': 'mt_city',  'type': 'transit', 'lane_frac': 0.55, 'depth_frac': 0.20, 'desc': 'в город за ТТ'},
                {'id': 'mt_city2', 'type': 'hold',     'lane_frac': 0.60, 'depth_frac': 0.48, 'desc': 'в городских кварталах'},
                {'id': 'mt_city3', 'type': 'hold',     'lane_frac': 0.58, 'depth_frac': 0.72, 'desc': 'заход к их базе'},
            ],
            [
                {'id': 'mt_green',  'type': 'transit', 'lane_frac': -0.50, 'depth_frac': 0.22, 'desc': 'по зелёнке'},
                {'id': 'mt_green2', 'type': 'hold',     'lane_frac': -0.55, 'depth_frac': 0.50, 'desc': 'в зелёнке'},
                {'id': 'mt_green3', 'type': 'hold',     'lane_frac': -0.52, 'depth_frac': 0.74, 'desc': 'фланг зелёнкой'},
            ],
        ],
        'LT': [
            [
                {'id': 'greenzone_rush',  'type': 'transit', 'lane_frac': -0.60, 'depth_frac': 0.15, 'desc': 'зелёнка'},
                {'id': 'breakthrough_pt', 'type': 'hold',     'lane_frac': -0.65, 'depth_frac': 0.42, 'desc': 'точка прорыва'},
                {'id': 'hunt_enemy_spg',  'type': 'hold',     'lane_frac': -0.60, 'depth_frac': 0.62, 'desc': 'рейд на вражескую арту'},
            ],
            [
                {'id': 'lt_city',  'type': 'transit', 'lane_frac': 0.55, 'depth_frac': 0.18, 'desc': 'по улицам'},
                {'id': 'lt_city2', 'type': 'spot',     'lane_frac': 0.60, 'depth_frac': 0.45, 'desc': 'засвет в городе'},
                {'id': 'lt_city3', 'type': 'hold',     'lane_frac': 0.57, 'depth_frac': 0.75, 'desc': 'рейд к их спавну'},
            ],
        ],
        'TD': [
            [
                {'id': 'city_street_hold', 'type': 'ambush', 'lane_frac': 0.35, 'depth_frac': 0.15, 'desc': 'рядом с ТТ, прикрывает улицу'},
            ],
            [
                {'id': 'td_green',  'type': 'ambush', 'lane_frac': -0.55, 'depth_frac': 0.30, 'desc': 'засада в зелёнке'},
                {'id': 'td_green2', 'type': 'ambush', 'lane_frac': -0.60, 'depth_frac': 0.52, 'desc': 'глубокая засада в зелёнке'},
            ],
        ],
        'SPG': [
            [
                {'id': 'edge_or_rail_fire', 'type': 'fire', 'lane_frac': 0.10, 'depth_frac': 0.08, 'desc': 'край карты у спауна либо прямая наводка на ж/д'},
            ],
        ],
    },
}

_PATROL_ZONES = {
    '01_karelia': [
        {'name': 'полка у базы',      'lane_frac': -0.35, 'depth_frac': 0.10, 'r_frac': 0.30},
        {'name': 'болото/центр',      'lane_frac': 0.00,  'depth_frac': 0.25, 'r_frac': 0.35},
        {'name': 'ущелье (СВ)',       'lane_frac': -0.70, 'depth_frac': 0.35, 'r_frac': 0.35},
        {'name': 'плато (СЗ)',        'lane_frac': 0.70,  'depth_frac': 0.45, 'r_frac': 0.35},
        {'name': 'дорога/центр',      'lane_frac': 0.05,  'depth_frac': 0.50, 'r_frac': 0.35},
        {'name': 'глубокий фланг',    'lane_frac': 0.75,  'depth_frac': 0.78, 'r_frac': 0.30},
        {'name': 'юг у их базы',      'lane_frac': -0.70, 'depth_frac': 0.82, 'r_frac': 0.28},
    ],
    '02_malinovka': [
        {'name': 'своя база/кусты',   'lane_frac': -0.20, 'depth_frac': 0.10, 'r_frac': 0.30},
        {'name': 'деревня',           'lane_frac': 0.22,  'depth_frac': 0.24, 'r_frac': 0.30},
        {'name': 'центральное поле',  'lane_frac': 0.00,  'depth_frac': 0.50, 'r_frac': 0.35},
        {'name': 'гора с мельницей',  'lane_frac': 0.60,  'depth_frac': 0.45, 'r_frac': 0.35},
        {'name': 'коровники/юг',      'lane_frac': -0.50, 'depth_frac': 0.50, 'r_frac': 0.35},
        {'name': 'к их базе',         'lane_frac': 0.55,  'depth_frac': 0.80, 'r_frac': 0.30},
    ],
    '04_himmelsdorf': [
        {'name': 'своя база/улица',   'lane_frac': 0.15,  'depth_frac': 0.10, 'r_frac': 0.25},
        {'name': 'ж/д (запад)',       'lane_frac': 0.55,  'depth_frac': 0.45, 'r_frac': 0.35},
        {'name': 'город (восток)',    'lane_frac': -0.70, 'depth_frac': 0.28, 'r_frac': 0.35},
        {'name': 'центр города',      'lane_frac': 0.00,  'depth_frac': 0.50, 'r_frac': 0.35},
        {'name': 'малая площадь',     'lane_frac': 0.10,  'depth_frac': 0.25, 'r_frac': 0.30},
        {'name': 'гора/замок',        'lane_frac': -0.55, 'depth_frac': 0.48, 'r_frac': 0.35},
        {'name': 'глубокий город',    'lane_frac': -0.20, 'depth_frac': 0.75, 'r_frac': 0.30},
    ],
    '05_prohorovka': [
        {'name': 'своя гряда',        'lane_frac': -0.55, 'depth_frac': 0.15, 'r_frac': 0.30},
        {'name': 'середина поля',     'lane_frac': 0.00,  'depth_frac': 0.25, 'r_frac': 0.35},
        {'name': 'центральная лощина','lane_frac': 0.00,  'depth_frac': 0.48, 'r_frac': 0.38},
        {'name': 'западная гряда',    'lane_frac': -0.60, 'depth_frac': 0.45, 'r_frac': 0.35},
        {'name': 'восточный лес/ж/д', 'lane_frac': 0.60,  'depth_frac': 0.45, 'r_frac': 0.35},
        {'name': 'к их базе',         'lane_frac': 0.50,  'depth_frac': 0.80, 'r_frac': 0.30},
    ],
    '06_ensk': [
        {'name': 'ж/д у базы',        'lane_frac': 0.35,  'depth_frac': 0.12, 'r_frac': 0.28},
        {'name': 'главная улица',     'lane_frac': 0.10,  'depth_frac': 0.25, 'r_frac': 0.30},
        {'name': 'вокзал/центр',      'lane_frac': 0.00,  'depth_frac': 0.45, 'r_frac': 0.35},
        {'name': 'город (запад)',     'lane_frac': 0.60,  'depth_frac': 0.45, 'r_frac': 0.35},
        {'name': 'зелёнка (восток)',  'lane_frac': -0.55, 'depth_frac': 0.45, 'r_frac': 0.35},
        {'name': 'глубокий город',    'lane_frac': 0.60,  'depth_frac': 0.74, 'r_frac': 0.30},
    ],
    '07_lakeville': [
        {'name': 'своя база',         'lane_frac': 0.00,  'depth_frac': 0.10, 'r_frac': 0.30},
        {'name': 'прибрежный коридор','lane_frac': 0.02,  'depth_frac': 0.38, 'r_frac': 0.30},
        {'name': 'ущелье (запад)',    'lane_frac': -0.65, 'depth_frac': 0.45, 'r_frac': 0.35},
        {'name': 'город (восток)',    'lane_frac': 0.68,  'depth_frac': 0.45, 'r_frac': 0.35},
        {'name': 'центр/переправа',   'lane_frac': 0.00,  'depth_frac': 0.55, 'r_frac': 0.35},
        {'name': 'к их базе',         'lane_frac': 0.60,  'depth_frac': 0.78, 'r_frac': 0.30},
    ],
    '11_murovanka': [
        {'name': 'своя база/край',    'lane_frac': 0.00,  'depth_frac': 0.12, 'r_frac': 0.30},
        {'name': 'деревня/центр',     'lane_frac': 0.00,  'depth_frac': 0.45, 'r_frac': 0.35},
        {'name': 'холмы (запад)',     'lane_frac': 0.60,  'depth_frac': 0.45, 'r_frac': 0.35},
        {'name': 'лес (восток)',      'lane_frac': -0.60, 'depth_frac': 0.45, 'r_frac': 0.35},
        {'name': 'глубокий засвет',   'lane_frac': -0.20, 'depth_frac': 0.75, 'r_frac': 0.30},
    ],
}

_PATROL_ARRIVE_DIST = 12.0
_PATROL_MIN_REPICK_DIST = 18.0
_PATROL_MAX_LEG = 160.0
_PATROL_VISITS_PER_ZONE = 2

_SPOT_INTERVAL = 0.5
_SPOT_LINGER = 10.0
_SPOT_ALWAYS_RANGE = 50.0

_SPOT_ENTRY_FLASH = 0.2
_SPOT_GHOST_TIME = 0.5
_SPOT_ANNOUNCE_RATE = 4.0

_NAV_GRID_CELL = 25.0
_NAV_GRID_BATCH = 40
_NAV_GRID_MAX_CELLS = 3000

_NAV_LOG_CD = {}
_NAV_LOG_LAST = [0.0]

def _nav_log(key, msg, interval=5.0):

    try:
        now = BigWorld.time()
        if now - _NAV_LOG_LAST[0] < interval:
            return
        _NAV_LOG_LAST[0] = now
        LOG_NOTE(msg)
    except Exception:
        pass

def _bot_park(bot_veh, seconds=12.0):

    try:
        t = BigWorld.time()
        bot_veh._ai_reverse_until = 0.0
        bot_veh._ai_reverse_blocked_until = t + max(float(seconds), 8.0)
        bot_veh._ai_hold_until = t + float(seconds)
        bot_veh._cur_speed = 0.0
        bot_veh._ai_target_speed = 0.0
        bot_veh._ai_rev_target = None
    except Exception:
        pass

_BOT_CLIMB_STEP = 1.2

_CUMULATIVE_CLIMB_CAP = 3.4
_CLIMB_REF_RESET_DIST = 9.0

_BOT_STUCK_RADIUS = 3.0
_BOT_STUCK_TIMEOUT = 2.5

_BOT_STUCK_REVERSE_DURATION = 3.0
_BOT_STUCK_REVERSE_SPEED_FRAC = 0.7

_BOT_REVERSE_TRAPPED_MAX = 6.0

_BOT_REVERSE_FUTILE_DELAY = 1.5
_BOT_REVERSE_FUTILE_DIST = 1.0
_BOT_REVERSE_FUTILE_COOLDOWN = 8.0

_BOT_SLOPE_STUCK_WINDOW = 2.5
_BOT_SLOPE_STUCK_HORIZ = 1.5
_BOT_SLOPE_STUCK_RISE = 0.8

_BOT_IMPACT_SLIDE_SPD = 3.5
_BOT_IMPACT_PRESS = 0.45
_BOT_IMPACT_PROGRESS_MIN = 0.25

_BOT_AVOID_SCAN_ANGLES = (10, -10, 15, -15, 25, -25, 35, -35, 45, -45, 60, -60, 90, -90, 120, -120, 150, -150, 180)
_BOT_AVOID_SCAN_DIST = 12.0
_BOT_AVOID_RESCAN_INTERVAL = 0.7

_BOT_FEELER_RESCAN = 0.3
_BOT_FEELER_HOLD = 0.6

_BOT_REVERSE_AFTER_BLOCKED = 2.5

_BOT_HOLD_AFTER_BLOCK = 0.55

_BOT_CRUISE_COAST_CHANCE = 0.35
_BOT_TURN_SPEED_PENALTY  = 0.55

_NAV_GRID_REACHABLE_MAX_RISE = 3.5

_NAV_GRID_MAX_TOTAL_RISE = 22.0

_NAV_GRID_SPARSE_FLOOR = 20
_NAV_GRID_MAX_REBUILDS = 5
_NAV_GRID_REBUILD_DELAY = 2.0


_G_DESTR_BROKEN = set()

_SCENERY_CHUNK = 100.0
_SCENERY_MARGIN = 2.2
_SCENERY_BUCKET = 10.0
_SCENERY_STRIDE = 16
_SCENERY_MAX_CHUNKS = 96
_SCENERY_MAX_FELL = 256
_SCENERY_UNIT_MASS = 18000.0
_SCENERY_LOD = _re.compile('/lod./')
_SCENERY_NON_INTACT = ('/crash/', '/broken/', '/destroyed/', '/debris/')
_scenery_space = None
_scenery_chunks = {}
_scenery_counts = {}
_scenery_destroyed = 0
_scenery_lowmem = False
_scenery_cache_patched = False
_G_CHASSIS_BOX = {}
_G_MESH_SAMPLES = {}

def _scenery_norm_path(filename):

    p = filename.replace('\\', '/').lower().strip()
    p = _SCENERY_LOD.sub('/', p)
    while '//' in p:
        p = p.replace('//', '/')
    parts = []
    for part in p.split('/'):
        if not part:
            continue
        if part.startswith('lod') and len(part) <= 5:
            continue
        parts.append(part)
    return '/'.join(parts)

def _scenery_path_suffix(filename):
    parts = [p for p in _scenery_norm_path(filename).split('/') if p]
    if len(parts) >= 3:
        return '/'.join(parts[-3:])
    return '/'.join(parts)

def _scenery_teach_cache():
    global _scenery_cache_patched
    if _scenery_cache_patched:
        return
    try:
        import DestructiblesCache as _DC
        cls = _DC.DestructiblesCache
        original = cls.getDescIDByFilename

        def getDescIDByFilename(self, filename):
            if not filename:
                return None
            found = original(self, filename)
            if found is not None:
                return found
            slash = filename.replace('\\', '/')
            found = original(self, slash)
            if found is not None:
                return found
            found = original(self, _SCENERY_LOD.sub('/lod0/', slash))
            if found is not None:
                return found
            normalised = _scenery_norm_path(filename)
            low = filename.replace('\\', '/').lower()
            for part in _SCENERY_NON_INTACT:
                if part in low:
                    return None
            found = original(self, normalised)
            if found is not None:
                return found
            table = getattr(self, '_offline_bySuffix', None)
            if table is None:
                table = {}
                known = getattr(self, '_DestructiblesCache__descIDs', None) or {}
                for name, descID in known.iteritems():
                    nkey = _scenery_norm_path(name)
                    if nkey and nkey not in table:
                        table[nkey] = descID
                    skey = _scenery_path_suffix(name)
                    if skey and skey not in table:
                        table[skey] = descID
                    elif skey and table.get(skey) != descID:
                        table[skey] = table.get(skey)
                    bkey = nkey.split('/')[-1] if nkey else ''
                    if bkey:
                        table.setdefault('$' + bkey, descID)
                self._offline_bySuffix = table
            found = table.get(normalised)
            if found is not None:
                return found
            found = table.get(_scenery_path_suffix(filename))
            if found is not None:
                return found
            base = normalised.split('/')[-1] if normalised else ''
            if base:
                return table.get('$' + base)
            return None

        cls.getDescIDByFilename = getDescIDByFilename
        _scenery_cache_patched = True
        LOG_NOTE("[SCENERY] getDescIDByFilename lod fallback installed")
    except Exception:
        LOG_CURRENT_EXCEPTION()

def _scenery_reset():
    global _scenery_space, _scenery_chunks, _scenery_counts
    global _scenery_destroyed, _scenery_lowmem
    _scenery_space = None
    _scenery_chunks = {}
    _scenery_counts = {}
    _scenery_destroyed = 0
    _scenery_lowmem = False
    try:
        _G_MESH_SAMPLES.clear()
    except Exception:
        pass

def _scenery_low_memory():
    global _scenery_lowmem, _scenery_chunks
    _scenery_lowmem = True
    _scenery_chunks = {}
    try:
        _G_MESH_SAMPLES.clear()
    except Exception:
        pass

def _scenery_install(spaceID):
    global _scenery_space
    _scenery_reset()
    _scenery_teach_cache()
    _scenery_space = spaceID

def _scenery_remember(chunkID, numDestructibles):
    if not _scenery_lowmem and numDestructibles:
        _scenery_counts[chunkID] = numDestructibles

def _scenery_bucket(localX, localZ):
    return (int(_math.floor(localX / _SCENERY_BUCKET)) * _SCENERY_STRIDE
            + int(_math.floor(localZ / _SCENERY_BUCKET)))

def _scenery_index(chunkID):
    if _scenery_lowmem or _scenery_space is None:
        return None
    chunk = _scenery_chunks.get(chunkID)
    if chunk is not None:
        return chunk
    count = _scenery_counts.get(chunkID)
    if not count:
        return None
    if len(_scenery_chunks) >= _SCENERY_MAX_CHUNKS:
        return None
    try:
        from AreaDestructibles import g_cache
        chunkMatrix = BigWorld.wg_getChunkMatrix(_scenery_space, chunkID)
        origin = chunkMatrix.translation
        chunk = {
            'indices': [], 'xs': [], 'zs': [], 'health': [], 'kinetic': [],
            'kinds': [], 'broken': set(), 'buckets': {},
            'ox': origin.x, 'oz': origin.z,
        }
        _scenery_chunks[chunkID] = chunk
        for destrIndex in xrange(count):
            try:
                filename = BigWorld.wg_getDestructibleFilename(_scenery_space, chunkID, destrIndex)
                if not filename:
                    continue
                desc = g_cache.getDescByFilename(filename)
                if desc is None:
                    desc = g_cache.getDescByFilename(_SCENERY_LOD.sub('/lod0/', filename))
                if desc is None or desc['type'] == DESTR_TYPE_STRUCTURE:
                    continue
                matrix = BigWorld.wg_getDestructibleMatrix(_scenery_space, chunkID, destrIndex)
                if matrix is None:
                    continue
                offset = matrix.translation
                scale = matrix.applyVector((0.0, 1.0, 0.0)).length
                health = float(desc.get('health', 50)) * max(1.0, scale * scale)
                wx = origin.x + offset.x
                wz = origin.z + offset.z
                slot = len(chunk['indices'])
                chunk['indices'].append(destrIndex)
                chunk['xs'].append(wx)
                chunk['zs'].append(wz)
                chunk['health'].append(health)
                chunk['kinetic'].append(float(desc.get('kineticDamageCorrection', 0.0)))
                chunk['kinds'].append(desc['type'])
                key = _scenery_bucket(wx - origin.x, wz - origin.z)
                chunk['buckets'].setdefault(key, []).append(slot)
            except Exception:
                continue
        return chunk
    except Exception:
        LOG_CURRENT_EXCEPTION()
        return _scenery_chunks.setdefault(chunkID, {
            'indices': [], 'xs': [], 'zs': [], 'health': [], 'kinetic': [],
            'kinds': [], 'broken': set(), 'buckets': {}, 'ox': 0.0, 'oz': 0.0,
        })

def _scenery_candidates(minX, minZ, maxX, maxZ):
    found = []
    firstX = int(_math.floor(minX / _SCENERY_CHUNK))
    lastX = int(_math.floor(maxX / _SCENERY_CHUNK))
    firstZ = int(_math.floor(minZ / _SCENERY_CHUNK))
    lastZ = int(_math.floor(maxZ / _SCENERY_CHUNK))
    for cx in xrange(firstX, lastX + 1):
        for cz in xrange(firstZ, lastZ + 1):
            chunkID = chunkIDFromChunkIndexes(cx, cz)
            chunk = _scenery_index(chunkID)
            if chunk is None or not chunk['indices']:
                continue
            buckets = chunk['buckets']
            indices = chunk['indices']
            broken = chunk['broken']
            bx0 = int(_math.floor((minX - chunk['ox']) / _SCENERY_BUCKET))
            bx1 = int(_math.floor((maxX - chunk['ox']) / _SCENERY_BUCKET))
            bz0 = int(_math.floor((minZ - chunk['oz']) / _SCENERY_BUCKET))
            bz1 = int(_math.floor((maxZ - chunk['oz']) / _SCENERY_BUCKET))
            for bx in xrange(bx0, bx1 + 1):
                base = bx * _SCENERY_STRIDE
                for bz in xrange(bz0, bz1 + 1):
                    slots = buckets.get(base + bz)
                    if not slots:
                        continue
                    for slot in slots:
                        if indices[slot] not in broken:
                            found.append((chunkID, chunk, slot))
    return found

def _scenery_seg_dist(px, pz, ax, az, dx, dz, lengthSquared):
    if lengthSquared < 0.0001:
        return _math.sqrt((px - ax) ** 2 + (pz - az) ** 2)
    t = ((px - ax) * dx + (pz - az) * dz) / lengthSquared
    if t < 0.0:
        t = 0.0
    elif t > 1.0:
        t = 1.0
    return _math.sqrt((px - (ax + dx * t)) ** 2 + (pz - (az + dz * t)) ** 2)

def _scenery_fell(chunkID, chunk, slot, fallDirYaw, energy):
    global _scenery_destroyed
    destrIndex = chunk['indices'][slot]
    dKey = (chunkID, destrIndex)
    if (destrIndex in chunk['broken'] or dKey in _G_DESTR_BROKEN
            or _scenery_lowmem or _scenery_destroyed >= _SCENERY_MAX_FELL):
        return
    chunk['broken'].add(destrIndex)
    _G_DESTR_BROKEN.add(dKey)
    _scenery_destroyed += 1
    try:
        if chunk['kinds'][slot] == DESTR_TYPE_FRAGILE:
            g_destructiblesManager.orderDestructibleDestroy(chunkID, 1, destrIndex, True)
        else:
            headroom = energy / max(1.0, float(chunk['health'][slot]))
            fallSpeed = max(0, min(3, int(headroom)))
            g_destructiblesManager.orderDestructibleDestroy(
                chunkID, 0,
                encodeFallenDestructible(destrIndex, encodeFallenParams(fallDirYaw, fallSpeed)),
                True)
    except Exception:
        LOG_CURRENT_EXCEPTION()

def _scenery_run_over(vehicle, fromXZ, toXZ, elapsed):

    if _scenery_space is None or elapsed <= 0.0:
        return
    ax, az = float(fromXZ[0]), float(fromXZ[1])
    bx, bz = float(toXZ[0]), float(toXZ[1])
    dx = bx - ax
    dz = bz - az
    travelled = dx * dx + dz * dz
    speed = _math.sqrt(travelled) / elapsed
    if speed < 0.5:
        return
    descr = getattr(vehicle, 'typeDescriptor', None)
    if descr is None:
        return
    try:
        mass = float(descr.physics['weight'])
    except Exception:
        mass = 15000.0
    try:
        hw, hlf, hlb = _tank_hull_dims(descr)
        reach = max(hw, 0.8) + _SCENERY_MARGIN
    except Exception:
        reach = 2.0
    energy = 0.5 * mass * speed * speed * 0.00015
    fallDirYaw = _math.atan2(dx, dz) if travelled > 0.0001 else 0.0
    minX = min(ax, bx) - reach
    maxX = max(ax, bx) + reach
    minZ = min(az, bz) - reach
    maxZ = max(az, bz) + reach
    for chunkID, chunk, slot in _scenery_candidates(minX, minZ, maxX, maxZ):
        if _scenery_seg_dist(chunk['xs'][slot], chunk['zs'][slot], ax, az, dx, dz, travelled) > reach:
            continue
        damage = energy
        correction = chunk['kinetic'][slot]
        if correction:
            damage *= _math.pow(mass / _SCENERY_UNIT_MASS, correction)
        kind = None
        try:
            kind = chunk['kinds'][slot]
        except Exception:
            kind = None
        if kind == DESTR_TYPE_FRAGILE:
            continue
        if kind in (DESTR_TYPE_TREE, DESTR_TYPE_FALLING_ATOM):
            if damage <= chunk['health'][slot]:
                damage = float(chunk['health'][slot]) + 1.0
        if chunk['health'][slot] >= damage:
            continue
        _scenery_fell(chunkID, chunk, slot, fallDirYaw, damage)

_WALL_MEM_CELL = 15.0
_WALL_MEM_RADIUS = 30.0
_WALL_MEM_TTL = 1000000.0
_WALL_MEM_MAX = 200
_G_WALL_MEM = {}

def _wall_mem_key(x, z):
    try:
        return (int(_math.floor(x / _WALL_MEM_CELL)), int(_math.floor(z / _WALL_MEM_CELL)))
    except Exception:
        return (0, 0)

def _wall_mem_clear():
    try:
        _G_WALL_MEM.clear()
    except Exception:
        pass

def _wall_mem_add(x, z):

    try:
        _k = _wall_mem_key(x, z)
        _now = 0.0
        try:
            _now = BigWorld.time()
        except Exception:
            pass
        if _k in _G_WALL_MEM:
            try:
                _G_WALL_MEM[_k][2] = _now
            except Exception:
                pass
            return False
        if len(_G_WALL_MEM) >= _WALL_MEM_MAX:
            try:
                _oldest_k = None
                _oldest_t = None
                for _kk, _vv in _G_WALL_MEM.items():
                    try:
                        _tt = _vv[2]
                    except Exception:
                        _tt = 0.0
                    if _oldest_t is None or _tt < _oldest_t:
                        _oldest_t = _tt
                        _oldest_k = _kk
                if _oldest_k is not None:
                    del _G_WALL_MEM[_oldest_k]
            except Exception:
                pass
        _G_WALL_MEM[_k] = [x, z, _now]
        return True
    except Exception:
        return False

def _wall_mem_near(x, z, radius=None):

    try:
        if not _G_WALL_MEM:
            return False
        if radius is None:
            radius = _WALL_MEM_RADIUS
        _r2 = radius * radius
        for _vv in _G_WALL_MEM.values():
            try:
                _dx = x - _vv[0]
                _dz = z - _vv[1]
            except Exception:
                continue
            if _dx * _dx + _dz * _dz < _r2:
                return True
    except Exception:
        pass
    return False

def _wall_mem_seg_blocked(x0, z0, x1, z1, radius=None, samples=3):

    try:
        if not _G_WALL_MEM:
            return False
        if radius is None:
            radius = _WALL_MEM_RADIUS * 0.7
        if samples < 1:
            samples = 1
        if samples > 4:
            samples = 4
        for _i in range(1, samples + 1):
            _t = float(_i) / float(samples + 1)
            _px = x0 + (x1 - x0) * _t
            _pz = z0 + (z1 - z0) * _t
            if _wall_mem_near(_px, _pz, radius):
                return True
    except Exception:
        pass
    return False

_BOT_INVISIBLE_SCAN_DIST = 8.0
_BOT_INVISIBLE_SCAN_MASKS = (128, 18, 1, 7, 11, 15, 31, 63, 255)
_BOT_INVISIBLE_STUCK_RADIUS = 2.0
_BOT_INVISIBLE_STUCK_TIME = 1.2
_BOT_UNSTUCK_REVERSE_TIME = 2.5
_BOT_UNSTUCK_TURN_DEG_MIN = 90
_BOT_UNSTUCK_TURN_DEG_MAX = 135
_BOT_INVISIBLE_SCAN_CD = 0.4

def _bot_probe_hit_any_mask(spaceID, sx, sy, sz, ex, ey, ez):

    _hit128 = None
    try:
        _hit128 = BigWorld.wg_collideSegment(spaceID,
            Math.Vector3(sx, sy, sz), Math.Vector3(ex, ey, ez), 128)
    except Exception:
        _hit128 = None
    if _hit128 is not None:
        return True, False
    try:
        for _m in (18, 1, 7, 11, 15, 31, 63, 255):
            try:
                _r = BigWorld.wg_collideSegment(spaceID,
                    Math.Vector3(sx, sy, sz), Math.Vector3(ex, ey, ez), _m)
            except Exception:
                _r = None
            if _r is not None:
                return True, True
    except Exception:
        pass
    return False, False

def _bot_see_invisible_wall(spaceID, x, y, z, yaw, dist=None):

    try:
        if dist is None:
            dist = _BOT_INVISIBLE_SCAN_DIST
        _sy = _math.sin(yaw)
        _cy = _math.cos(yaw)
        try:
            if _wall_mem_near(x + _sy * 7.0, z + _cy * 7.0, _WALL_MEM_RADIUS * 0.7):
                return True
        except Exception:
            pass
        try:
            _ax = x + _sy * dist
            _az = z + _cy * dist
            if _nav_probe_ground(spaceID, _ax, _az) is None:
                return True
        except Exception:
            pass
        try:
            _h = _BOT_CLIMB_STEP + 0.8
            _ex = x + _sy * dist
            _ez = z + _cy * dist
            _hit, _invis = _bot_probe_hit_any_mask(spaceID, x, y + _h, z, _ex, y + _h, _ez)
            if _invis:
                return True
        except Exception:
            pass
    except Exception:
        pass
    return False

def _bot_trigger_unstuck_reverse(bot_veh, cur_x, cur_z, cur_yaw, reason):

    try:
        _bot_park(bot_veh, 20.0)
    except Exception:
        pass
    return getattr(bot_veh, '_ai_reverse_side', 1)

def _destr_desc(spaceID, chunkID, itemIndex):

    try:
        from AreaDestructibles import g_cache
        desc = g_cache.getDestructibleDesc(spaceID, chunkID, itemIndex)
        if desc is not None:
            return desc
        filename = BigWorld.wg_getDestructibleFilename(spaceID, chunkID, itemIndex)
        if not filename:
            return None
        desc = g_cache.getDescByFilename(filename)
        if desc is not None:
            return desc
        return g_cache.getDescByFilename(_SCENERY_LOD.sub('/lod0/', filename))
    except Exception:
        return None

def _destr_type_for_matKind(spaceID, chunkID, itemIndex):

    try:
        desc = _destr_desc(spaceID, chunkID, itemIndex)
        if desc is None:
            return None
        return desc['type']
    except Exception:
        return None

def _remember_base_flag_points(zones):
    global _G_BASE_FLAG_POINTS
    pts = []
    try:
        for z in (zones or {}).itervalues():
            p = z.get('pos')
            if p is None:
                continue
            pts.append(Math.Vector3(p.x, getattr(p, 'y', 0.0) or 0.0, p.z))
    except Exception:
        pts = []
    _G_BASE_FLAG_POINTS = pts

def _is_protected_cap_scenery(spaceID, chunkID, itemIndex):

    filename = None
    try:
        filename = BigWorld.wg_getDestructibleFilename(spaceID, chunkID, itemIndex)
    except Exception:
        filename = None
    if filename:
        fl = filename.replace('\\', '/').lower()
        if ('flagstaff' in fl or 'flagstaf' in fl or 'flag_staff' in fl
                or 'christmas' in fl or 'newyear' in fl or 'new_year' in fl
                or 'xmas' in fl or 'nytree' in fl or 'ny_tree' in fl
                or 'elka' in fl or 'yelka' in fl or 'yolka' in fl):
            return True
    pos = None
    try:
        m = BigWorld.wg_getDestructibleMatrix(spaceID, chunkID, itemIndex)
        if m is not None:
            pos = m.translation
    except Exception:
        pos = None
    if pos is None:
        return False
    for bp in _G_BASE_FLAG_POINTS:
        try:
            dx = pos.x - bp.x
            dz = pos.z - bp.z
            if dx * dx + dz * dz <= _FLAG_KEEP_R2:
                return True
        except Exception:
            continue
    return False

def _seg_hit_is_ground(res, minPlaneY=0.40, tankY=None):

    try:
        if res is None:
            return False
        if res[1].y >= minPlaneY:
            return True
    except Exception:
        pass
    return False

def _destr_whisker_blocks(spaceID, matKind, itemIndex, chunkID):

    if matKind < _const.DESTRUCTIBLE_MATKINDS_MIN or matKind > _const.DESTRUCTIBLE_MATKINDS_MAX:
        return True
    dtype = _destr_type_for_matKind(spaceID, chunkID, itemIndex)
    if dtype is None:
        return True
    if dtype == DESTR_TYPE_STRUCTURE and (matKind >= _const.DESTRUCTIBLE_MATKINDS_NORMAL_MIN and
                                           matKind <= _const.DESTRUCTIBLE_MATKINDS_NORMAL_MAX):
        return True
    return False

def _destr_structure_intact(spaceID, res):

    try:
        if res is None:
            return False
        matKind = res[2]
        if matKind < _const.DESTRUCTIBLE_MATKINDS_MIN or matKind > _const.DESTRUCTIBLE_MATKINDS_MAX:
            return False
        if matKind < _const.DESTRUCTIBLE_MATKINDS_NORMAL_MIN or matKind > _const.DESTRUCTIBLE_MATKINDS_NORMAL_MAX:
            return False
        return _destr_type_for_matKind(spaceID, res[5], res[4]) == DESTR_TYPE_STRUCTURE
    except Exception:
        return False

def _measure_building_footprint(spaceID, wx, wz, probeH):

    try:
        pts = []
        for _ai in xrange(16):
            _ang = _ai * 0.39269908169872414
            _hx = _math.sin(_ang)
            _hz = _math.cos(_ang)
            _res = BigWorld.wg_collideSegment(spaceID,
                Math.Vector3(wx, probeH, wz),
                Math.Vector3(wx + _hx * 14.0, probeH, wz + _hz * 14.0), 128)
            if _res is None:
                return None
            _ddx = _res[0].x - wx
            _ddz = _res[0].z - wz
            if _ddx * _ddx + _ddz * _ddz < 0.36:
                return None
            pts.append((_res[0].x, _res[0].z))
        if len(pts) < 4:
            return None
        _xMin = min(_p[0] for _p in pts)
        _zMin = min(_p[1] for _p in pts)
        _xMax = max(_p[0] for _p in pts)
        _zMax = max(_p[1] for _p in pts)
        if (_xMax - _xMin) < 1.5 or (_zMax - _zMin) < 1.5:
            return None
        return (_xMin, _zMin, _xMax, _zMax)
    except Exception:
        return None

def _dispatch_destructible_hit(spaceID, matKind, itemIndex, chunkID, dirVec, isShotDamage=True):

    try:
        if matKind < _const.DESTRUCTIBLE_MATKINDS_MIN or matKind > _const.DESTRUCTIBLE_MATKINDS_MAX:
            return False

        if _is_protected_cap_scenery(spaceID, chunkID, itemIndex):
            return False

        dtype = _destr_type_for_matKind(spaceID, chunkID, itemIndex)
        if dtype is None:
            return False

        _dKey = (chunkID, itemIndex)
        if dtype in (DESTR_TYPE_TREE, DESTR_TYPE_FALLING_ATOM, DESTR_TYPE_FRAGILE) and _dKey in _G_DESTR_BROKEN:
            return False

        if dtype == DESTR_TYPE_TREE or dtype == DESTR_TYPE_FALLING_ATOM:
            fallDirYaw = _math.atan2(dirVec.x, dirVec.z)
            fallSpeed  = 2
            params     = encodeFallenParams(fallDirYaw, fallSpeed)
            data       = encodeFallenDestructible(itemIndex, params)
            g_destructiblesManager.orderDestructibleDestroy(chunkID, 0, data, True)
            _G_DESTR_BROKEN.add(_dKey)
            return True

        elif dtype == DESTR_TYPE_FRAGILE:
            if not isShotDamage:
                return False
            data = itemIndex
            g_destructiblesManager.orderDestructibleDestroy(chunkID, 1, data, True)
            _G_DESTR_BROKEN.add(_dKey)
            return True

        elif dtype == DESTR_TYPE_STRUCTURE:
            if not isShotDamage:
                return False
            if matKind >= _const.DESTRUCTIBLE_MATKINDS_NORMAL_MIN and matKind <= _const.DESTRUCTIBLE_MATKINDS_NORMAL_MAX:
                data = encodeDestructibleModule(itemIndex, matKind, True)
                g_destructiblesManager.orderDestructibleDestroy(chunkID, 2, data, True)
                return True
    except Exception as e:
        LOG_NOTE("[BATTLE] _dispatch_destructible_hit failed: %s" % e)
    return False

def _try_destroy_destructible_at(spaceID, impactPoint, shotDir):

    try:
        nearPt = impactPoint - shotDir.scale(0.5)
        farPt  = impactPoint + shotDir.scale(0.5)
        res = BigWorld.wg_collideSegment(spaceID, nearPt, farPt, 128)
        if res is None:
            return False
        point, normal, matKind, collFlags, itemIndex, chunkID = res
        return _dispatch_destructible_hit(spaceID, matKind, itemIndex, chunkID, shotDir)
    except Exception as e:
        LOG_NOTE("[BATTLE] _try_destroy_destructible_at failed: %s" % e)
        return False

def _shot_static_hit(spaceID, shotPos, farPoint, shotDir):

    _pRes = BigWorld.wg_collideSegment(spaceID, shotPos, farPoint, 128)
    _pReached = _pRes is None
    if _pRes is not None:
        for _pi in range(3):
            _pMk = _pRes[2]
            _pSoft = False
            try:
                _pSoft = (_pMk >= _const.DESTRUCTIBLE_MATKINDS_MIN and
                          _pMk <= _const.DESTRUCTIBLE_MATKINDS_MAX and
                          not _destr_whisker_blocks(spaceID, _pMk, _pRes[4], _pRes[5]))
            except Exception:
                _pSoft = False
            if not _pSoft:
                break
            try:
                _try_destroy_destructible_at(spaceID, _pRes[0], shotDir)
            except Exception:
                pass
            _pNext = _pRes[0] + shotDir * 0.5
            _probe2 = BigWorld.wg_collideSegment(spaceID, _pNext, farPoint, 128)
            if _probe2 is None:
                _pRes = None
                _pReached = True
                break
            _pRes = _probe2
    if _pReached or _pRes is None:
        return farPoint, (farPoint - shotPos).length, True
    return _pRes[0], (_pRes[0] - shotPos).length, False

def _load_cfg(mapName):
    global _V_START_ANGLES, _V_START_POS, _CAM_START_DIST, _CAM_START_ANGLES
    global _CAM_START_TARGET_POS, _CAM_PIVOT_POS, _CAM_FLUENCY, _SHADOW_LIGHT_DIR
    cfg = _CFG['basic']
    _V_START_ANGLES       = cfg['v_start_angles']
    _V_START_POS          = cfg['v_start_pos']
    _CAM_START_DIST       = cfg['cam_start_dist']
    _CAM_START_ANGLES     = cfg['cam_start_angles']
    _CAM_START_TARGET_POS = cfg['cam_start_target_pos']
    _CAM_PIVOT_POS        = cfg['cam_pivot_pos']
    _CAM_FLUENCY          = cfg['cam_fluency']
    _SHADOW_LIGHT_DIR     = cfg['shadow_light_dir']
    LOG_NOTE("[BATTLE][CFG] Loaded config for map: %s" % mapName)

def load_arena_type(arenaTypeID):
    from ArenaType import g_cache
    typeID, typeName = _arena_name_to_id(arenaTypeID)
    if typeID is None:
        LOG_ERROR("[BATTLE] load_arena_type: cannot resolve arenaTypeID=%r" % (arenaTypeID,))
        return None
    try:
        result = g_cache.get(typeID)
    except Exception as _e:
        LOG_ERROR("[BATTLE] load_arena_type: exception for arenaTypeID=%s (%s): %s" % (typeID, typeName, _e))
        result = None
    if result is None:
        LOG_ERROR("[BATTLE] load_arena_type: UNKNOWN arenaTypeID=%s (%s)" % (typeID, typeName))
    else:
        LOG_NOTE("[BATTLE] load_arena_type: OK arenaTypeID=%s typeName=%s geometry=%s" % (
            typeID, typeName, getattr(result, 'geometry', None)))
    return result

_SPAWN_Y_SLACK = 8.0
_SPAWN_Y_LIFT = 0.05

def _spawn_ground_y(spaceID, x, z, hint_y):

    try:
        hint = float(hint_y)
    except Exception:
        hint = 2.0
    start_y = hint + 12.0
    end_y = hint - 40.0
    try:
        res = BigWorld.wg_collideSegment(spaceID, Math.Vector3(x, start_y, z), Math.Vector3(x, end_y, z), 128)
        while res is not None and _destr_structure_intact(spaceID, res) and res[0].y > (hint + 0.5):
            res = BigWorld.wg_collideSegment(spaceID, Math.Vector3(x, res[0].y - 0.05, z), Math.Vector3(x, end_y, z), 128)
        if res is not None:
            gy = res[0].y
            if gy > hint + 0.45:
                gy = hint
            if abs(gy - hint) <= _SPAWN_Y_SLACK:
                return gy + _SPAWN_Y_LIFT
    except Exception:
        pass
    try:
        if hasattr(BigWorld, "findDropPoint"):
            drop = BigWorld.findDropPoint(spaceID, Math.Vector3(x, hint + 12.0, z))
            if drop is not None and len(drop) > 0:
                gy = drop[0].y
                if gy > hint + 0.45:
                    gy = hint
                if abs(gy - hint) <= _SPAWN_Y_SLACK:
                    return gy + _SPAWN_Y_LIFT
    except Exception:
        pass
    return hint + _SPAWN_Y_LIFT

def get_ground_height(spaceID, pos):
    res = BigWorld.wg_collideSegment(spaceID, Math.Vector3(pos.x, 500.0, pos.z), Math.Vector3(pos.x, -500.0, pos.z), 18)
    if res is not None: return res[0].y
    res = BigWorld.wg_collideSegment(spaceID, Math.Vector3(pos.x, 500.0, pos.z), Math.Vector3(pos.x, -500.0, pos.z), 128)
    while res is not None and _destr_structure_intact(spaceID, res) and res[0].y > pos.y:
        res = BigWorld.wg_collideSegment(spaceID, Math.Vector3(pos.x, res[0].y - 0.05, pos.z), Math.Vector3(pos.x, -500.0, pos.z), 128)
    if res is not None: return res[0].y
    return 0.0

def _nav_ground_snap(spaceID, x, z, ref_y, band=3.0):

    try:
        res = BigWorld.wg_collideSegment(spaceID, Math.Vector3(x, ref_y + band, z), Math.Vector3(x, ref_y - band, z), 128)
        if res is not None:
            return res[0].y
    except Exception:
        pass
    return get_ground_height(spaceID, Math.Vector3(x, 0, z))

def _nav_ground_matkind(spaceID, x, z, ref_y, band=3.0):

    try:
        res = BigWorld.wg_collideSegment(spaceID, Math.Vector3(x, ref_y + band, z), Math.Vector3(x, ref_y - band, z), 128)
        if res is not None:
            return res[2]
    except Exception:
        pass
    return None

def _water_point_hit(x, y, z):

    try:
        if not hasattr(BigWorld, 'wg_collideWater'):
            return False
        p0 = Math.Vector3(x, y + 4.0, z)
        p1 = Math.Vector3(x, y - 6.0, z)
        wr = BigWorld.wg_collideWater(p0, p1)
        if wr is None or wr <= 0:
            return False
        waterY = p0.y - wr
        return waterY >= (y - 1.0)
    except Exception:
        return False

def _hull_over_water(spaceID, cx, cz, y, yaw, hw, hl):

    try:
        _sy = _math.sin(yaw)
        _cy = _math.cos(yaw)
        _ry = _math.cos(yaw)
        _rz = -_math.sin(yaw)
        _pts = ((cx, cz),
                (cx + _sy * hl, cz + _cy * hl),
                (cx - _sy * hl, cz - _cy * hl),
                (cx + _ry * hw, cz + _rz * hw),
                (cx - _ry * hw, cz - _rz * hw))
        for (_px, _pz) in _pts:
            if _water_point_hit(_px, y, _pz):
                return True
    except Exception:
        pass
    return False

def _ground_narrow(spaceID, x, z, ref_y, band=3.0):

    try:
        res = BigWorld.wg_collideSegment(spaceID, Math.Vector3(x, ref_y + band, z), Math.Vector3(x, ref_y - band, z), 128)
        while res is not None:
            hit_y = res[0].y
            overhead = hit_y > (ref_y + 0.25)
            destr = _destr_structure_intact(spaceID, res)
            if overhead or destr:
                res = BigWorld.wg_collideSegment(spaceID, Math.Vector3(x, hit_y - 0.05, z), Math.Vector3(x, ref_y - band, z), 128)
                continue
            try:
                if res[1].y < 0.40:
                    return None
            except Exception:
                pass
            return hit_y
    except Exception:
        pass
    return None

def _carrying_corners_world(x, z, yaw, hw, hl):

    sy = _math.sin(yaw)
    cy = _math.cos(yaw)
    rx = _math.cos(yaw)
    rz = -_math.sin(yaw)
    return (
        (x + sy * hl + rx * hw, z + cy * hl + rz * hw),
        (x + sy * hl - rx * hw, z + cy * hl - rz * hw),
        (x - sy * hl + rx * hw, z - cy * hl + rz * hw),
        (x - sy * hl - rx * hw, z - cy * hl - rz * hw),
    )

def _climb_max_dy(minNy, dist):

    try:
        ny = min(0.999, max(float(minNy), 0.15))
        return _math.tan(_math.acos(ny)) * max(float(dist), 0.01)
    except Exception:
        return 0.55

def _filter_carrying_heights(raw, ref_y, minNy, hw, hl):

    valid = [h for h in raw if h is not None]
    if not valid:
        return list(raw)
    valid_sorted = sorted(valid)
    med = valid_sorted[len(valid_sorted) // 2]
    max_one = _climb_max_dy(minNy, max(float(hw), 0.5) * 2.0)
    max_all = _climb_max_dy(minNy, max(float(hw), float(hl), 0.5) * 2.0)
    out = []
    for h in raw:
        if h is None:
            out.append(None)
        elif (h - med) > max_one:
            out.append(None)
        elif (h - ref_y) > max_all:
            out.append(None)
        else:
            out.append(h)
    return out

def _sample_carrying_heights(spaceID, x, z, yaw, hw, hl, ref_y, minNy):
    raw = []
    for px, pz in _carrying_corners_world(x, z, yaw, hw, hl):
        raw.append(_terrain_y_for_tilt(spaceID, px, pz, ref_y, 5.0, minNy))
    return _filter_carrying_heights(raw, ref_y, minNy, hw, hl)

def _hull_rotate_blocked(spaceID, x, y, z, yaw, td, vehicle=None, pitch=0.0, roll=0.0, minNy=0.40):

    try:
        wps = _mesh_world_points(td, vehicle, x, y, z, yaw, pitch, roll)
        if not wps:
            return False
        for wp in wps:
            ox = wp.x - x
            oz = wp.z - z
            ln = _math.sqrt(ox * ox + oz * oz)
            if ln < 0.08:
                continue
            ox = ox / ln * 0.22
            oz = oz / ln * 0.22
            res = BigWorld.wg_collideSegment(
                spaceID,
                Math.Vector3(wp.x - ox * 0.25, wp.y, wp.z - oz * 0.25),
                Math.Vector3(wp.x + ox, wp.y, wp.z + oz),
                128)
            if _static_seg_blocks(spaceID, res, minNy):
                return True
    except Exception:
        return False
    return False

def _carrying_ground_y(spaceID, x, z, ref_y, band=2.2, minNy=0.40):

    try:
        res = BigWorld.wg_collideSegment(spaceID, Math.Vector3(x, ref_y + band, z), Math.Vector3(x, ref_y - band, z), 128)
        while res is not None:
            hit_y = res[0].y
            if hit_y > (ref_y + 0.25) or _destr_structure_intact(spaceID, res):
                res = BigWorld.wg_collideSegment(spaceID, Math.Vector3(x, hit_y - 0.05, z), Math.Vector3(x, ref_y - band, z), 128)
                continue
            try:
                if res[1].y < minNy:
                    return None
            except Exception:
                pass
            return hit_y
    except Exception:
        pass
    return None

def _ground_drop_y(spaceID, x, z, ref_y):

    gy = _ground_narrow(spaceID, x, z, ref_y, 2.2)
    if gy is not None:
        return gy
    try:
        start = Math.Vector3(x, ref_y + 2.0, z)
        end = Math.Vector3(x, min(ref_y, 0.0) - 80.0, z)
        res = BigWorld.wg_collideSegment(spaceID, start, end, 128)
        while res is not None:
            hit_y = res[0].y
            if hit_y > (ref_y + 0.25) or _destr_structure_intact(spaceID, res):
                res = BigWorld.wg_collideSegment(spaceID, Math.Vector3(x, hit_y - 0.05, z), end, 128)
                continue
            try:
                if res[1].y < 0.40:
                    return None
            except Exception:
                pass
            return hit_y
    except Exception:
        pass
    gy = get_ground_height(spaceID, Math.Vector3(x, 0, z))
    if gy != 0.0:
        return gy
    return None

def _hull_hang_snap(spaceID, x, z, yaw, hw, hl, ref_y, band=2.2, minNy=0.40):

    ys = _sample_carrying_heights(spaceID, x, z, yaw, hw, hl, ref_y, minNy)
    hits = [h for h in ys if h is not None]
    if not hits:
        raw = []
        for px, pz in _carrying_corners_world(x, z, yaw, hw, hl):
            raw.append(_carrying_ground_y(spaceID, px, pz, ref_y, band, minNy))
        hits = [h for h in _filter_carrying_heights(raw, ref_y, minNy, hw, hl) if h is not None]
    if hits:
        y = sum(hits) / float(len(hits))
        return (y, False)
    gy = _ground_drop_y(spaceID, x, z, ref_y)
    return (gy, False)

def _terrain_speed_factor(descr, matKind):

    try:
        resistances = descr.chassis.get('materialResistances')
        if not resistances:
            return 1.0
        firm = resistances.get(0, 1.0)
        if firm <= 0.0:
            return 1.0
        resistance = resistances.get(matKind, firm) if matKind is not None else firm
        if resistance <= 0.0:
            return 1.0
        return max(0.78, min(1.0, firm / resistance))
    except Exception:
        return 1.0

_SLOPE_MAX_ANGLE = math.radians(35.0)
_SLOPE_UP_STRENGTH    = 0.30
_SLOPE_UP_FLOOR       = 0.85
_SLOPE_DOWN_STRENGTH  = 0.35
_SLOPE_DOWN_CEIL      = 1.30
_SLOPE_EASE_RISE_RATE = 0.30
_SLOPE_EASE_FALL_RATE = 0.20

_TILT_MAX_H_DIFF = 1.8
_TILT_MAX_VISUAL = math.radians(48.0)
_TILT_STIFFNESS = 10.0

def _min_plane_ny(td):

    try:
        return float(td.physics['minPlaneNormalY'])
    except Exception:
        try:
            return float(td.chassis['minPlaneNormalY'])
        except Exception:
            return 0.766

def _terrain_y_for_tilt(spaceID, x, z, ref_y, band=5.0, minNy=0.40):

    try:
        res = BigWorld.wg_collideSegment(
            spaceID,
            Math.Vector3(x, ref_y + band, z),
            Math.Vector3(x, ref_y - band, z),
            18)
        if res is not None:
            try:
                if res[1].y < minNy:
                    res = None
                else:
                    return res[0].y
            except Exception:
                return res[0].y
    except Exception:
        pass
    try:
        res = BigWorld.wg_collideSegment(
            spaceID,
            Math.Vector3(x, ref_y + band, z),
            Math.Vector3(x, ref_y - band, z),
            128)
        while res is not None:
            hit_y = res[0].y
            if hit_y > (ref_y + 0.25) or _destr_structure_intact(spaceID, res):
                res = BigWorld.wg_collideSegment(
                    spaceID,
                    Math.Vector3(x, hit_y - 0.05, z),
                    Math.Vector3(x, ref_y - band, z),
                    128)
                continue
            try:
                if res[1].y < minNy:
                    return None
            except Exception:
                pass
            return hit_y
    except Exception:
        pass
    return None

def _tilt_target_pitch_roll(spaceID, pos, yaw, descr):

    try:
        trc = descr.chassis['topRightCarryingPoint']
        hw = max(float(trc[0]), 0.5)
        hl = max(float(trc[1]), 1.0)
    except Exception:
        hw, hl = 0.78, 1.65
    minNy = _min_plane_ny(descr)
    x, y, z = pos.x, pos.y, pos.z
    y_tr, y_tl, y_br, y_bl = _sample_carrying_heights(
        spaceID, x, z, yaw, hw, hl, y, minNy)
    _void = y

    def _avg(a, b, fb):
        if a is not None and b is not None:
            return 0.5 * (a + b)
        if a is not None:
            return a
        if b is not None:
            return b
        return fb

    h_front = _avg(y_tr, y_tl, _void)
    h_back = _avg(y_br, y_bl, _void)
    h_right = _avg(y_tr, y_br, _void)
    h_left = _avg(y_tl, y_bl, _void)
    target_pitch = _math.atan2(h_back - h_front, hl * 2.0)
    target_roll = _math.atan2(h_right - h_left, hw * 2.0)
    mv = _TILT_MAX_VISUAL
    target_pitch = max(-mv, min(mv, target_pitch))
    target_roll = max(-mv, min(mv, target_roll))
    return target_pitch, target_roll, hl, hw, h_front, h_back

def _carrying_sit_y(spaceID, x, z, yaw, pitch, roll, hw, hl, origin_y, minNy=0.766):

    try:
        rm = Math.Matrix()
        rm.setRotateYPR((yaw, pitch, roll))
    except Exception:
        return origin_y
    ys = _sample_carrying_heights(spaceID, x, z, yaw, hw, hl, origin_y, minNy)
    locals_xz = ((hw, hl), (-hw, hl), (hw, -hl), (-hw, -hl))
    lift = 0.0
    i = 0
    for lx, lz in locals_xz:
        gy = ys[i]
        i += 1
        if gy is None:
            continue
        try:
            v = rm.applyVector(Math.Vector3(lx, 0.0, lz))
            wy = origin_y + v.y
            d = gy - wy
            if d > lift:
                lift = d
        except Exception:
            pass
    if lift <= 0.0:
        return origin_y
    cap = _climb_max_dy(minNy, max(hw, 0.5) * 2.0)
    if cap < 0.12:
        cap = 0.12
    if lift > cap:
        lift = cap
    return origin_y + lift

def _slope_speed_target(h_front, h_back, half_L, direction):

    if h_front is None or h_back is None or not half_L or half_L <= 0.01 or direction == 0.0:
        return 1.0
    angle = _math.atan2(h_front - h_back, 2.0 * half_L) * direction
    if angle > 0.0:
        return max(_SLOPE_UP_FLOOR, 1.0 - _math.sin(min(angle, _SLOPE_MAX_ANGLE)) * _SLOPE_UP_STRENGTH)
    elif angle < 0.0:
        return min(_SLOPE_DOWN_CEIL, 1.0 + _math.sin(min(-angle, _SLOPE_MAX_ANGLE)) * _SLOPE_DOWN_STRENGTH)
    return 1.0

def _slope_ease_toward(current, target, dt):

    if current < target:
        return min(current + _SLOPE_EASE_RISE_RATE * dt, target)
    return max(current - _SLOPE_EASE_FALL_RATE * dt, target)

_WOT_DYN_RATIO_MIN = 9.5
_WOT_DYN_RATIO_MAX = 21.0
_WOT_BKWD_POWER    = 0.55
_WOT_ANG_SPIN_TIME = 0.60
_WOT_COAST_RATIO   = 0.35

def _nav_probe_ground(spaceID, x, z):

    try:
        res = BigWorld.wg_collideSegment(spaceID, Math.Vector3(x, 500.0, z), Math.Vector3(x, -500.0, z), 128)
        if res is None:
            return None
        if _destr_structure_intact(spaceID, res):
            return None
        return (res[0].y, res[1].y)
    except Exception:
        return None

def _nav_point_ok(spaceID, x, z, minPlaneY=0.75):

    probe = _nav_probe_ground(spaceID, x, z)
    if probe is None:
        return False, 0.0
    groundY, normalY = probe
    if normalY < minPlaneY:
        return False, groundY
    try:
        if hasattr(BigWorld, 'wg_collideWater'):
            wres = BigWorld.wg_collideWater(Math.Vector3(x, groundY + 2.0, z), Math.Vector3(x, groundY - 1.0, z))
            if wres > 0:
                return False, groundY
    except Exception:
        pass
    return True, groundY

def _nav_step_blocked(dy, max_step):

    if dy < 0.0:
        return abs(dy) > max_step * 1.15
    return dy > max_step

def _nav_track_climb(ref, cur_x, cur_z, cur_y):

    if ref is None:
        return (cur_x, cur_z, cur_y), False
    rx, rz, ry = ref
    dx, dz = cur_x - rx, cur_z - rz
    dist = _math.sqrt(dx * dx + dz * dz)
    rise = cur_y - ry
    if dist >= _CLIMB_REF_RESET_DIST or rise <= 0.0:
        return (cur_x, cur_z, cur_y), False
    return ref, rise > _CUMULATIVE_CLIMB_CAP

def _nav_path_clear(spaceID, x0, z0, x1, z1, minPlaneY=0.75, samples=5):

    try:
        _dx = x1 - x0
        _dz = z1 - z0
        _dist = _math.sqrt(_dx * _dx + _dz * _dz)
        _need = 2 + int(_dist / 30.0)
        if _need > samples:
            samples = _need if _need <= 12 else 12
    except Exception:
        pass
    probe0 = _nav_probe_ground(spaceID, x0, z0)
    prevY = probe0[0] if probe0 is not None else None
    for i in range(1, samples + 1):
        t = float(i) / (samples + 1)
        ok, groundY = _nav_point_ok(spaceID, x0 + (x1 - x0) * t, z0 + (z1 - z0) * t, minPlaneY)
        if not ok:
            return False
        if prevY is not None and _nav_step_blocked(groundY - prevY, _BOT_CLIMB_STEP):
            return False
        prevY = groundY
    try:
        y0 = probe0[0] + 1.05 if probe0 is not None else 2.0
        y1 = groundY + 1.05
        res = BigWorld.wg_collideSegment(
            spaceID,
            Math.Vector3(x0, y0, z0),
            Math.Vector3(x1, y1, z1),
            128)
        if res is not None and not _seg_hit_is_ground(res, minPlaneY):
            if _destr_structure_intact(spaceID, res) or _destr_whisker_blocks(spaceID, res[2], res[4], res[5]):
                return False
    except Exception:
        pass
    return True

def _nav_sightline_dist(spaceID, x, z, dirX, dirZ, maxDist=300.0, height=2.2):

    d = _math.sqrt(dirX * dirX + dirZ * dirZ)
    if d < 1e-6:
        return 0.0
    ndx, ndz = dirX / d, dirZ / d
    probe = _nav_probe_ground(spaceID, x, z)
    if probe is None:
        return 0.0
    startY = probe[0] + height
    ex = x + ndx * maxDist
    ez = z + ndz * maxDist
    try:
        res = BigWorld.wg_collideSegment(spaceID, Math.Vector3(x, startY, z), Math.Vector3(ex, startY, ez), 128)
    except Exception:
        res = None
    if res is None:
        return maxDist
    hit = res[0]
    hdx, hdz = hit.x - x, hit.z - z
    return _math.sqrt(hdx * hdx + hdz * hdz)

def _find_cover_point(spaceID, ax, az, chunk_counts, toward=None, max_r=130.0):

    best = None
    best_score = -1e18
    try:
        _cx, _cz = chunkIndexesFromChunkID(chunkIDFromPosition(Math.Vector3(ax, 0.0, az)))
    except Exception:
        return None
    r2max = max_r * max_r
    try:
        for gx in (_cx - 1, _cx, _cx + 1):
            for gz in (_cz - 1, _cz, _cz + 1):
                chunkID = chunkIDFromChunkIndexes(gx, gz)
                if not g_destructiblesManager.isChunkLoaded(chunkID):
                    continue
                n = int((chunk_counts or {}).get(chunkID, 0) or 0)
                if n <= 0:
                    continue
                for di in xrange(n):
                    dtype = _destr_type_for_matKind(spaceID, chunkID, di)
                    if dtype not in (DESTR_TYPE_TREE, DESTR_TYPE_FALLING_ATOM):
                        continue
                    dm = BigWorld.wg_getDestructibleMatrix(spaceID, chunkID, di)
                    if dm is None:
                        continue
                    t = dm.translation
                    dx = t.x - ax
                    dz = t.z - az
                    d2 = dx * dx + dz * dz
                    if d2 < 64.0 or d2 > r2max:
                        continue
                    ok, gy = _nav_point_ok(spaceID, t.x, t.z)
                    if not ok:
                        continue
                    score = -d2
                    if toward is not None:
                        score += ((t.x - ax) * toward.x + (t.z - az) * toward.z) * 40.0
                    if score > best_score:
                        best_score = score
                        best = Math.Vector3(t.x, gy, t.z)
    except Exception:
        return best
    return best

def _offline_entity_pos(veh):

    if veh is None:
        return None
    try:
        om = getattr(veh, '_offline_matrix', None)
        if om is not None:
            return om.translation
    except Exception:
        pass
    try:
        return veh.position
    except Exception:
        return None

def _xz_near_vehicle(x, z, ignore_id=None, rad=4.2):

    try:
        import Vehicle as _VehMod
    except Exception:
        return False
    r2 = rad * rad
    try:
        ents = BigWorld.entities.values()
    except Exception:
        return False
    for ent in ents:
        try:
            if ignore_id is not None and getattr(ent, 'id', None) == ignore_id:
                continue
            if not isinstance(ent, _VehMod.Vehicle):
                continue
            if not _vehicle_is_alive(ent):
                continue
            om = getattr(ent, '_offline_matrix', None)
            p = om.translation if om is not None else ent.position
            dx = p.x - x
            dz = p.z - z
            if dx * dx + dz * dz <= r2:
                return True
        except Exception:
            continue
    return False

def _nav_hit_is_static_wall(res, ox, oz):

    if res is None:
        return False
    try:
        hit = res[0]
        dx = hit.x - ox
        dz = hit.z - oz
        if (dx * dx + dz * dz) < 6.0:
            return False
        if _xz_near_vehicle(hit.x, hit.z, rad=4.0):
            return False
        try:
            if not _destr_whisker_blocks(None, res[2], res[4], res[5]):
                return False
        except Exception:
            pass
        return True
    except Exception:
        return True

def _bot_los_blocked(spaceID, gunPos, aimPos, targetVeh):

    try:
        res = BigWorld.wg_collideSegment(spaceID, gunPos, aimPos, 128)
    except Exception:
        return True
    if res is None:
        return False
    try:
        hit = res[0]
    except Exception:
        return True
    try:
        if (aimPos - hit).length < 1.6:
            return False
    except Exception:
        pass
    if targetVeh is not None:
        try:
            om = getattr(targetVeh, '_offline_matrix', None)
            tp = om.translation if om is not None else targetVeh.position
            tyaw = getattr(om, 'yaw', 0.0) if om is not None else getattr(targetVeh, 'yaw', 0.0)
            if _world_point_hits_vehicle(
                    targetVeh, hit, tp.x, tp.y, tp.z, tyaw,
                    getattr(targetVeh, '_cur_pitch', 0.0),
                    getattr(targetVeh, '_cur_roll', 0.0), 0.40):
                return False
        except Exception:
            pass
    try:
        mk = res[2]
        import constants as _c
        if (mk >= _c.DESTRUCTIBLE_MATKINDS_MIN and mk <= _c.DESTRUCTIBLE_MATKINDS_MAX
                and not _destr_whisker_blocks(spaceID, mk, res[4], res[5])):
            return False
    except Exception:
        pass
    return True

def _nav_scan_best_heading(spaceID, x, y, z, cur_yaw):

    height = _BOT_CLIMB_STEP + 0.3
    for deg in _BOT_AVOID_SCAN_ANGLES:
        ang = cur_yaw + _math.radians(deg)
        ex = x + _math.sin(ang) * _BOT_AVOID_SCAN_DIST
        ez = z + _math.cos(ang) * _BOT_AVOID_SCAN_DIST
        try:
            res = BigWorld.wg_collideSegment(spaceID, Math.Vector3(x, y + height, z),
                                              Math.Vector3(ex, y + height, ez), 128)
        except Exception:
            res = None
        if res is None or not _nav_hit_is_static_wall(res, x, z):
            return _math.radians(deg)
    return _math.radians(180)

def _feeler_ray_clear(spaceID, x, z, y, ang, height, hw, hl, dist):

    cs = _math.sin(ang)
    cc = _math.cos(ang)
    ox = _math.cos(ang)
    oz = -_math.sin(ang)
    sx0 = x + cs * hl
    sz0 = z + cc * hl
    for oo in (-hw * 0.9, 0.0, hw * 0.9):
        try:
            res = BigWorld.wg_collideSegment(spaceID,
                Math.Vector3(sx0 + ox * oo, y + height, sz0 + oz * oo),
                Math.Vector3(sx0 + ox * oo + cs * dist, y + height, sz0 + oz * oo + cc * dist), 128)
        except Exception:
            res = None
        if res is not None and _nav_hit_is_static_wall(res, x, z):
            return False
    return True

def _bot_avoid_scan(spaceID, x, y, z, cur_yaw, side):

    _rays = [0.0]
    for _ri in range(1, 6):
        if side >= 0:
            _rays.extend([_ri * 0.25, -_ri * 0.25])
        else:
            _rays.extend([-_ri * 0.25, _ri * 0.25])
    for _margin in (2.2, 1.6):
        _center_blocked = False
        _best_ang = None
        for _ang in _rays:
            _fy = cur_yaw + _ang
            _s_y = _math.sin(_fy)
            _c_y = _math.cos(_fy)
            _hit = False
            if _wall_mem_near(x + _s_y * 9.0, z + _c_y * 9.0):
                _hit = True
            if not _hit:
                for _h, _dist in ((0.7, 7.0), (1.5, 12.0)):
                    if _hit:
                        break
                _dest_x = x + _s_y * _dist
                _dest_z = z + _c_y * _dist
                _gp = None
                try:
                    _gp = _nav_probe_ground(spaceID, _dest_x, _dest_z)
                except Exception:
                    _gp = None
                if _gp is None:
                    _hit = True
                    break
                _dest_y = _gp[0]
                _y_diff = _dest_y - y
                if _y_diff > _dist * 0.45 or _y_diff < -_dist * 0.7:
                    _hit = True
                    break
                _end_h = (_dest_y if _dest_y > y else y) + _h
                for _ox in (-_margin, 0.0, _margin):
                    _sx = x + _c_y * _ox
                    _sz = z - _s_y * _ox
                    try:
                        _res = BigWorld.wg_collideSegment(spaceID,
                            Math.Vector3(_sx, y + _h, _sz),
                            Math.Vector3(_sx + _s_y * _dist, _end_h, _sz + _c_y * _dist), 128)
                    except Exception:
                        _res = None
                    if _res is not None and _destr_whisker_blocks(spaceID, _res[2], _res[4], _res[5]):
                        _hit = True
                        break
            if _ang == 0.0:
                if _hit:
                    _center_blocked = True
                else:
                    return cur_yaw
            elif not _hit:
                _best_ang = _ang
                break
        if not _center_blocked:
            return cur_yaw
        if _best_ang is not None:
            return cur_yaw + _best_ang
    return None

def _nav_drivable_rise(spaceID, x, y, z, yaw, look, fw):

    try:
        _seg_n = 6
        _seg = max(look, 0.01) / _seg_n
        _first_y = None
        _prev_y = None
        for _si in range(_seg_n + 1):
            _dd = _seg * _si
            _px = x + _math.sin(yaw) * _dd * fw
            _pz = z + _math.cos(yaw) * _dd * fw
            _gp = _nav_probe_ground(spaceID, _px, _pz)
            if _gp is None:
                return False
            _gy = _gp[0]
            if _prev_y is not None and (_gy - _prev_y) > _seg * 0.7:
                return False
            if _first_y is None:
                _first_y = _gy
            _prev_y = _gy
        return (_prev_y - _first_y) >= 1.2
    except Exception:
        return False

_SLOP = 0.02
_PCT = 0.4
_RAM_SAFE_SPEED = 0.5
_RAM_CD = 0.75
_FALL_SAFE_SPEED = 4.0
_FALL_DMG_PER_MS = 0.03
_g_offh_ram_cd = {}

def _tank_hull_dims(td):

    hw = 0.0
    hlf = 0.0
    hlb = 0.0
    got = False
    try:
        ht = td.chassis['hitTester']
        if ht is not None and ht.isBspModelLoaded() and ht.bbox is not None:
            bb = ht.bbox
            hw = max(hw, abs(bb[0][0]), abs(bb[1][0]))
            hlf = max(hlf, float(bb[1][2]))
            hlb = max(hlb, float(-bb[0][2]))
            got = True
    except Exception:
        pass
    try:
        ht = td.hull['hitTester']
        if ht is not None and ht.isBspModelLoaded() and ht.bbox is not None:
            bb = ht.bbox
            hpz = float(td.chassis['hullPosition'][2])
            hw = max(hw, abs(bb[0][0]), abs(bb[1][0]))
            hlf = max(hlf, float(bb[1][2]) + hpz)
            hlb = max(hlb, -(float(bb[0][2]) + hpz))
            got = True
    except Exception:
        pass
    if got and hw > 0.3 and (hlf + hlb) > 0.8:
        return (hw, max(hlf, 0.4), max(hlb, 0.4))
    try:
        trc = td.chassis['topRightCarryingPoint']
        hw = max(float(trc[0]), 0.8)
        hl = max(float(trc[1]), 1.0)
        return (hw, hl, hl)
    except Exception:
        return (1.5, 2.5, 2.5)

def _bsp_load_parts(td, names=('chassis', 'hull', 'turret', 'gun'), load=True):
    if _scenery_lowmem or td is None:
        return False
    ok = False
    for name in names:
        try:
            ht = getattr(td, name)['hitTester']
            if load and ht is not None and not ht.isBspModelLoaded():
                ht.loadBspModel()
            if ht is not None and ht.isBspModelLoaded():
                ok = True
        except Exception:
            pass
    return ok

def _veh_turret_gun_angles(vehicle):

    try:
        app = getattr(vehicle, 'appearance', None)
        if app is not None:
            ty = Math.Matrix(app.turretMatrix).yaw
            gp = Math.Matrix(app.gunMatrix).pitch
            return float(ty), float(gp)
    except Exception:
        pass
    try:
        return (float(getattr(vehicle, '_bot_turret_yaw_cur', 0.0) or 0.0),
                float(getattr(vehicle, '_bot_gun_pitch_cur', 0.0) or 0.0))
    except Exception:
        return 0.0, 0.0

def _descr_components(td, turretYaw=0.0, gunPitch=0.0):

    m = Math.Matrix()
    m.setIdentity()
    yield (td.chassis, m, 'chassis')
    hullOffset = td.chassis['hullPosition']
    m = Math.Matrix()
    m.setTranslate(-hullOffset)
    yield (td.hull, m, 'hull')
    try:
        turretPos = td.hull['turretPositions'][0]
        turretMatrix = Math.Matrix()
        turretMatrix.setTranslate(-hullOffset - turretPos)
        r = Math.Matrix()
        r.setRotateY(-turretYaw)
        turretMatrix.postMultiply(r)
        yield (td.turret, turretMatrix, 'turret')
        gunMatrix = Math.Matrix()
        gunMatrix.setTranslate(-td.turret['gunPosition'])
        rx = Math.Matrix()
        rx.setRotateX(-gunPitch)
        gunMatrix.postMultiply(rx)
        gunMatrix.preMultiply(turretMatrix)
        yield (td.gun, gunMatrix, 'gun')
    except Exception:
        pass

def _ht_hits_point(ht, localPt, radius):
    try:
        if ht.localSphericHitTest(localPt, radius):
            return True
    except Exception:
        pass
    try:
        dx = Math.Vector3(radius, 0.0, 0.0)
        dy = Math.Vector3(0.0, radius, 0.0)
        dz = Math.Vector3(0.0, 0.0, radius)
        if ht.localAnyHitTest(localPt - dx, localPt + dx):
            return True
        if ht.localAnyHitTest(localPt - dz, localPt + dz):
            return True
        if ht.localAnyHitTest(localPt - dy, localPt + dy):
            return True
    except Exception:
        pass
    return False

def _descr_parts_no_gun(td, turretYaw=0.0, include_turret=False):
    m = Math.Matrix()
    m.setIdentity()
    yield (td.chassis, m, 'chassis')
    hullOffset = td.chassis['hullPosition']
    m = Math.Matrix()
    m.setTranslate(-hullOffset)
    yield (td.hull, m, 'hull')
    if not include_turret:
        return
    try:
        turretMatrix = Math.Matrix()
        turretMatrix.setTranslate(-hullOffset - td.hull['turretPositions'][0])
        r = Math.Matrix()
        r.setRotateY(-turretYaw)
        turretMatrix.postMultiply(r)
        yield (td.turret, turretMatrix, 'turret')
    except Exception:
        pass

def _mesh_samples_for(td, with_aim=False):

    name = getattr(td, 'name', None)
    key = (name, 1 if with_aim else 0)
    if key in _G_MESH_SAMPLES:
        return _G_MESH_SAMPLES[key]
    samples = []
    parts = ('chassis', 'hull', 'turret', 'gun') if with_aim else ('chassis', 'hull')
    if not _bsp_load_parts(td, parts, load=False):
        if name is not None:
            _G_MESH_SAMPLES[key] = samples
        return samples
    seen = {}

    def _add_hit_points(ht, bb, to_veh, part, cap):
        try:
            mn, mx = bb[0], bb[1]
        except Exception:
            return
        cx = (mn[0] + mx[0]) * 0.5
        cy = (mn[1] + mx[1]) * 0.5
        cz = (mn[2] + mx[2]) * 0.5
        pad = 0.35
        starts = (
            Math.Vector3(cx, cy, mx[2] + pad),
            Math.Vector3(cx, cy, mn[2] - pad),
            Math.Vector3(mx[0] + pad, cy, cz),
            Math.Vector3(mn[0] - pad, cy, cz),
            Math.Vector3(mn[0], cy, mx[2] + pad),
            Math.Vector3(mx[0], cy, mx[2] + pad),
            Math.Vector3(cx, (cy + mx[1]) * 0.5, mx[2] + pad),
        )
        center = Math.Vector3(cx, cy, cz)
        added = 0
        for start in starts:
            if added >= cap:
                break
            try:
                hits = ht.localHitTest(start, center)
            except Exception:
                hits = None
            if not hits:
                continue
            dist = hits[0][0]
            for h in hits:
                if h[0] < dist:
                    dist = h[0]
            direc = center - start
            ln = direc.length
            if ln < 0.001:
                continue
            direc.normalise()
            p = start + direc * dist
            vp = to_veh(p)
            key = (part, round(vp.x, 2), round(vp.y, 2), round(vp.z, 2))
            seen[key] = (part, vp.x, vp.y, vp.z)
            added += 1

    try:
        ht = td.chassis['hitTester']
        _add_hit_points(ht, ht.bbox, lambda p: p, 'chassis', 6)
    except Exception:
        pass
    try:
        ht = td.hull['hitTester']
        hp = td.chassis['hullPosition']

        def _hull_to_veh(p):
            return Math.Vector3(p.x + hp[0], p.y + hp[1], p.z + hp[2])

        _add_hit_points(ht, ht.bbox, _hull_to_veh, 'hull', 6)
    except Exception:
        pass
    if with_aim:
        try:
            ht = td.turret['hitTester']
            hp = td.chassis['hullPosition']
            tp = td.hull['turretPositions'][0]
            j = (hp[0] + tp[0], hp[1] + tp[1], hp[2] + tp[2])

            def _turret_to_veh(p):
                return Math.Vector3(p.x + j[0], p.y + j[1], p.z + j[2])

            _add_hit_points(ht, ht.bbox, _turret_to_veh, 'turret', 5)
        except Exception:
            pass
        try:
            ht = td.gun['hitTester']
            hp = td.chassis['hullPosition']
            tp = td.hull['turretPositions'][0]
            gp = td.turret['gunPosition']
            j = (hp[0] + tp[0] + gp[0], hp[1] + tp[1] + gp[1], hp[2] + tp[2] + gp[2])

            def _gun_to_veh(p):
                return Math.Vector3(p.x + j[0], p.y + j[1], p.z + j[2])

            _add_hit_points(ht, ht.bbox, _gun_to_veh, 'gun', 4)
        except Exception:
            pass
    samples = list(seen.values())
    _G_MESH_SAMPLES[key] = samples
    return samples

def _part_sample_vehicle_point(part, sx, sy, sz, td, turretYaw, gunPitch):

    if part in ('chassis', 'hull'):
        return Math.Vector3(sx, sy, sz)
    try:
        hp = td.chassis['hullPosition']
        tp = td.hull['turretPositions'][0]
        jx = hp[0] + tp[0]
        jy = hp[1] + tp[1]
        jz = hp[2] + tp[2]
    except Exception:
        return Math.Vector3(sx, sy, sz)
    if part == 'gun':
        try:
            gp = td.turret['gunPosition']
            gx, gy, gz = jx + gp[0], jy + gp[1], jz + gp[2]
            lx, ly, lz = sx - gx, sy - gy, sz - gz
            c = _math.cos(gunPitch)
            s = _math.sin(gunPitch)
            ly2 = ly * c - lz * s
            lz2 = ly * s + lz * c
            sx, sy, sz = gx + lx, gy + ly2, gz + lz2
        except Exception:
            pass
    lx, ly, lz = sx - jx, sy - jy, sz - jz
    fx = _math.sin(turretYaw)
    fz = _math.cos(turretYaw)
    rx = _math.cos(turretYaw)
    rz = -_math.sin(turretYaw)
    return Math.Vector3(jx + rx * lx + fx * lz, jy + ly, jz + rz * lx + fz * lz)

def _pose_world_matrix(x, y, z, yaw, pitch, roll):
    m = Math.Matrix()
    m.setRotateYPR((yaw, pitch, roll))
    m.translation = Math.Vector3(x, y, z)
    return m

def _world_point_hits_vehicle(vehicle, worldPt, x, y, z, yaw, pitch, roll, radius=0.10):
    td = getattr(vehicle, 'typeDescriptor', None)
    if td is None:
        return False
    _bsp_load_parts(td, ('chassis', 'hull'), load=False)
    w2v = _pose_world_matrix(x, y, z, yaw, pitch, roll)
    w2v.invert()
    vehLocal = w2v.applyPoint(worldPt)
    for compDescr, compMatrix, _cname in _descr_parts_no_gun(td, 0.0, False):
        try:
            ht = compDescr['hitTester']
            if ht is None or not ht.isBspModelLoaded():
                continue
            if _ht_hits_point(ht, compMatrix.applyPoint(vehLocal), radius):
                return True
        except Exception:
            continue
    return False

def _tanks_mesh_overlap(vehA, ax, ay, az, ayaw, apitch, aroll,
                        vehB, bx, by, bz, byaw, bpitch, broll):

    if vehA is None or vehB is None or _scenery_lowmem:
        return None
    if not getattr(vehA, 'isPlayer', False) and not getattr(vehB, 'isPlayer', False):
        return None
    try:
        samplesA = _mesh_samples_for(vehA.typeDescriptor)
        samplesB = _mesh_samples_for(vehB.typeDescriptor)
    except Exception:
        return None
    if not samplesA and not samplesB:
        return None
    matA = _pose_world_matrix(ax, ay, az, ayaw, apitch, aroll)
    matB = _pose_world_matrix(bx, by, bz, byaw, bpitch, broll)
    try:
        for item in samplesA:
            if len(item) >= 4:
                wp = matA.applyPoint(Math.Vector3(item[1], item[2], item[3]))
            else:
                wp = matA.applyPoint(Math.Vector3(item[0], item[1], item[2]))
            if _world_point_hits_vehicle(vehB, wp, bx, by, bz, byaw, bpitch, broll):
                return True
        for item in samplesB:
            if len(item) >= 4:
                wp = matB.applyPoint(Math.Vector3(item[1], item[2], item[3]))
            else:
                wp = matB.applyPoint(Math.Vector3(item[0], item[1], item[2]))
            if _world_point_hits_vehicle(vehA, wp, ax, ay, az, ayaw, apitch, aroll):
                return True
    except Exception:
        return None
    return False

def _tank_circles(x, z, yaw, td):

    try:
        hw, hlf, hlb = _tank_hull_dims(td)
        r = hw if hw > 0.8 else 0.8
        fx = _math.sin(yaw); fz = _math.cos(yaw)
        start = -hlb + r
        end = hlf - r
        span = end - start
        n = int(span / (r * 1.5)) if r > 0 else 0
        if n < 0: n = 0
        step = span / max(n, 1) if span > 0 else 0
        out = []
        for i in range(n + 1):
            o = start + step * i
            out.append((x + fx*o, z + fz*o, r))
        if not out:
            out.append((x, z, r))
        return out
    except Exception:
        return [(x, z, 1.5)]

def _tank_resolve(ax, az, ayaw, atd, bx, bz, byaw, btd, a_vx=0.0, a_vz=0.0, b_vx=0.0, b_vz=0.0, ay=None, by=None):

    try:
        if ay is not None and by is not None:
            if abs(ay - by) > 3.0:
                return None
        dcx = ax - bx; dcz = az - bz
        if dcx*dcx + dcz*dcz > 144.0:
            return None
        try:
            am = max(float(atd.physics['weight']), 1000.0)
        except Exception:
            am = 15000.0
        try:
            bm = max(float(btd.physics['weight']), 1000.0)
        except Exception:
            bm = 15000.0
        isum = (1.0/am + 1.0/bm)
        if isum <= 0: isum = 1.0/15000.0
        my_c = _tank_circles(ax, az, ayaw, atd)
        ot_c = _tank_circles(bx, bz, byaw, btd)
        best = 0.0; bnx = 0.0; bnz = 1.0
        found = False
        for cc in my_c:
            for oc in ot_c:
                ddx = cc[0] - oc[0]; ddz = cc[1] - oc[1]
                rr = cc[2] + oc[2]
                d2 = ddx*ddx + ddz*ddz
                if d2 < rr*rr and d2 > 1e-6:
                    dist = _math.sqrt(d2)
                    pen = rr - dist
                    if pen > best:
                        best = pen
                        bnx = ddx / dist
                        bnz = ddz / dist
                        found = True
                elif d2 <= 1e-6:
                    pen = rr
                    if pen > best:
                        best = pen
                        bnx = _math.sin(ayaw)
                        bnz = _math.cos(ayaw)
                        found = True
        if not found or best <= 0.0:
            return None
        corr = max(best - _SLOP, 0.0) / isum * _PCT * (1.0/am)
        corr_x = bnx * corr
        corr_z = bnz * corr
        vn = (a_vx - b_vx)*bnx + (a_vz - b_vz)*bnz
        dvx = 0.0; dvz = 0.0
        if vn < 0.0:
            j = -vn / isum
            dvx = j * (1.0/am) * bnx
            dvz = j * (1.0/am) * bnz
        return (corr_x, corr_z, dvx, dvz, best, bnx, bnz, am, bm)
    except Exception:
        return None

def _ram_damage(closing_speed, mass_self, mass_other):

    rel = abs(float(closing_speed))
    if rel <= _RAM_SAFE_SPEED:
        return (0, 0)
    imp = (rel - _RAM_SAFE_SPEED) ** 2
    try:
        ratio = max(0.1, min(4.0, float(mass_self) / max(1.0, float(mass_other))))
    except Exception:
        ratio = 1.0
    dmg_other = int(imp * 1.7 * max(0.35, min(2.6, ratio)))
    dmg_self = int(imp * 1.0 * max(0.25, min(2.2, 1.0/max(0.1, ratio))))
    return (min(450, dmg_other), min(350, dmg_self))

def _fall_damage(maxHealth, impact_speed):
    if impact_speed <= _FALL_SAFE_SPEED:
        return 0
    return int(maxHealth * (impact_speed - _FALL_SAFE_SPEED) * _FALL_DMG_PER_MS)

def _check_horizontal_collision(spaceID, pos, yaw, td):

    try:
        hw, hlf, hlb = _tank_hull_dims(td)
        try:
            look = hlf + hlb
            seg_n = 6; seg = max(look, 0.1) / seg_n
            prev_y = None; first_y = None; smooth = True
            for si in range(seg_n + 1):
                dd = seg * si
                px = pos.x + _math.sin(yaw) * dd
                pz = pos.z + _math.cos(yaw) * dd
                gp = _nav_probe_ground(spaceID, px, pz)
                if gp is None:
                    smooth = False; break
                gy = gp[0]
                if prev_y is not None and (gy - prev_y) > seg * 1.28:
                    smooth = False; break
                if first_y is None: first_y = gy
                prev_y = gy
            if smooth and first_y is not None and prev_y is not None and (prev_y - first_y) >= 1.2:
                return None
        except Exception:
            pass
        cos_y = _math.cos(yaw); sin_y = _math.sin(yaw)
        for off_x in (-hw, 0.0, hw):
            sx = pos.x + cos_y * off_x
            sz = pos.z - sin_y * off_x
            low = None; high = None; mid = None
            try:
                for h, store in ((0.6, 'low'), (1.6, 'high'), (1.1, 'mid')):
                    sy = pos.y + h
                    ex = sx + sin_y * (hlf + 0.5)
                    ez = sz + cos_y * (hlf + 0.5)
                    res = BigWorld.wg_collideSegment(spaceID, Math.Vector3(sx, sy, sz), Math.Vector3(ex, sy, ez), 128)
                    if res is not None:
                        if store == 'low': low = res
                        elif store == 'high': high = res
                        else: mid = res
            except Exception:
                pass
            is_wall = False
            hit_res = None
            if low is not None and high is not None:
                if abs(high[0].y - low[0].y) < 0.5 and abs(high[0].x - low[0].x) < 5.0:
                    is_wall = True; hit_res = low
            elif low is not None and mid is not None:
                if abs(mid[0].y - low[0].y) < 0.25:
                    is_wall = True; hit_res = low
            elif low is not None:
                try:
                    n = low[1]
                    ny = getattr(n, 'y', 1.0)
                    is_wall = ny < 0.45
                    hit_res = low
                except Exception:
                    is_wall = False
            if is_wall and hit_res is not None:
                return (hit_res[0].x, hit_res[0].z, hit_res)
        return None
    except Exception:
        return None

def _static_seg_blocks(spaceID, res, minNy):
    if res is None:
        return False
    if _seg_hit_is_ground(res, minNy):
        return False
    try:
        if not _destr_whisker_blocks(spaceID, res[2], res[4], res[5]):
            return False
    except Exception:
        pass
    return True

def _bbox_local_points(bb):

    try:
        mn, mx = bb[0], bb[1]
        x0, y0, z0 = float(mn[0]), float(mn[1]), float(mn[2])
        x1, y1, z1 = float(mx[0]), float(mx[1]), float(mx[2])
    except Exception:
        return []
    pts = []
    for x in (x0, x1):
        for y in (y0, y1):
            for z in (z0, z1):
                pts.append(Math.Vector3(x, y, z))
    cx = 0.5 * (x0 + x1)
    cy = 0.5 * (y0 + y1)
    cz = 0.5 * (z0 + z1)
    pts.append(Math.Vector3(cx, cy, z1))
    pts.append(Math.Vector3(cx, cy, z0))
    pts.append(Math.Vector3(x1, cy, cz))
    pts.append(Math.Vector3(x0, cy, cz))
    pts.append(Math.Vector3(cx, y1, cz))
    pts.append(Math.Vector3(cx, y0, cz))
    return pts

def _mesh_world_points(td, vehicle, x, y, z, yaw, pitch, roll):

    if td is None:
        return []
    full = True
    if vehicle is not None and not getattr(vehicle, 'isPlayer', False):
        full = False
    names = ('chassis', 'hull', 'turret', 'gun') if full else ('chassis', 'hull')
    _bsp_load_parts(td, names, load=False)
    ty, gp = 0.0, 0.0
    if vehicle is not None:
        ty, gp = _veh_turret_gun_angles(vehicle)
    pose = _pose_world_matrix(x, y, z, yaw, pitch, roll)
    pts = []
    for compDescr, compMatrix, cname in _descr_components(td, ty, gp):
        if not full and cname in ('turret', 'gun'):
            continue
        try:
            ht = compDescr['hitTester']
            if ht is None or ht.bbox is None:
                continue
            inv = Math.Matrix(compMatrix)
            inv.invert()
            for lp in _bbox_local_points(ht.bbox):
                pts.append(pose.applyPoint(inv.applyPoint(lp)))
        except Exception:
            continue
    return pts

def _sweep_player_move(spaceID, prev_x, prev_z, new_x, new_z, y, yaw, pitch, roll, td, vehicle, minNy):

    dx = new_x - prev_x
    dz = new_z - prev_z
    step = _math.sqrt(dx * dx + dz * dz)
    if step < 1e-5:
        return (new_x, new_z, False, None)
    wps = _mesh_world_points(td, vehicle, prev_x, y, prev_z, yaw, pitch, roll)
    if not wps:
        return (new_x, new_z, False, None)
    ux = dx / step
    uz = dz / step
    look = step + 0.12
    for wp in wps:
        res = BigWorld.wg_collideSegment(
            spaceID,
            wp,
            Math.Vector3(wp.x + ux * look, wp.y, wp.z + uz * look),
            128)
        if _static_seg_blocks(spaceID, res, minNy):
            return (prev_x, prev_z, True, res)
    return (new_x, new_z, False, None)

def _hull_embedded_in_static(spaceID, x, y, z, yaw, td, vehicle=None, pitch=0.0, roll=0.0, minNy=0.40):

    try:
        wps = _mesh_world_points(td, vehicle, x, y, z, yaw, pitch, roll)
        if not wps:
            return False
        n = 0
        for wp in wps:
            ox = wp.x - x
            oz = wp.z - z
            ln = _math.sqrt(ox * ox + oz * oz)
            if ln < 0.08:
                continue
            ox /= ln
            oz /= ln
            res = BigWorld.wg_collideSegment(
                spaceID,
                Math.Vector3(x + ox * 0.20, y + 0.90, z + oz * 0.20),
                wp,
                128)
            if not _static_seg_blocks(spaceID, res, minNy):
                continue
            hdx = res[0].x - x
            hdz = res[0].z - z
            if (hdx * hdx + hdz * hdz) < (ln - 0.05) * (ln - 0.05):
                n += 1
        return n >= max(3, len(wps) / 3)
    except Exception:
        return False

def _hull_static_push(spaceID, pos, yaw, td, speed, vehicle=None, pitch=0.0, roll=0.0, max_pts=12):

    try:
        minNy = _min_plane_ny(td)
        fx = _math.sin(yaw)
        fz = _math.cos(yaw)
        sign = 1.0
        if speed < 0.0:
            sign = -1.0
        look = 0.28 + min(abs(float(speed)) * 0.06, 0.55)
        dx = fx * sign
        dz = fz * sign
        pts = _mesh_world_points(td, vehicle, pos.x, pos.y, pos.z, yaw, pitch, roll)
        if max_pts and len(pts) > max_pts:
            step = max(1, int((len(pts) + max_pts - 1) / max_pts))
            pts = pts[::step][:max_pts]
        if not pts:
            return None
        best_pen = 0.0
        bnx = 0.0
        bnz = 1.0
        best_res = None
        for wp in pts:
            res = BigWorld.wg_collideSegment(
                spaceID,
                wp,
                Math.Vector3(wp.x + dx * look, wp.y, wp.z + dz * look),
                128)
            if res is None:
                continue
            if _seg_hit_is_ground(res, minNy):
                continue
            try:
                if not _destr_whisker_blocks(spaceID, res[2], res[4], res[5]):
                    if best_res is None:
                        best_res = res
                    continue
            except Exception:
                pass
            hit = res[0]
            dist = _math.sqrt((hit.x - wp.x) * (hit.x - wp.x) + (hit.z - wp.z) * (hit.z - wp.z))
            pen = look - dist
            if pen <= best_pen:
                continue
            nx = wp.x - hit.x
            nz = wp.z - hit.z
            ln = _math.sqrt(nx * nx + nz * nz)
            if ln < 1e-4:
                nx, nz, ln = -dx, -dz, 1.0
            best_pen = pen
            bnx = nx / ln
            bnz = nz / ln
            best_res = res
        if best_pen <= 0.0:
            if best_res is not None:
                return (0.0, 0.0, speed, best_res)
            return None
        corr = max(best_pen - _SLOP, 0.0) * _PCT
        vn = speed * (fx * bnx + fz * bnz)
        new_speed = speed
        if vn < 0.0:
            new_speed = speed - vn * (fx * bnx + fz * bnz)
        return (bnx * corr, bnz * corr, new_speed, best_res)
    except Exception:
        return None

def _wall_slide_angles():
    return (0.55, -0.55, 1.0, -1.0)

def _fell_destructibles_near(spaceID, px, pz, yaw, descr, cooldown=None, chunk_counts=None):

    try:
        if not spaceID or g_destructiblesManager.getSpaceID() != spaceID:
            return
        try:
            hw, hlf, hlb = _tank_hull_dims(descr)
            reach = max(float(hw), 1.15) + 0.55
        except Exception:
            reach = 2.2
        fallDirYaw = yaw if yaw is not None else 0.0
        r2 = reach * reach
        for chunkID, chunk, slot in _scenery_candidates(px - reach, pz - reach, px + reach, pz + reach):
            dx = chunk['xs'][slot] - px
            dz = chunk['zs'][slot] - pz
            if dx * dx + dz * dz > r2:
                continue
            kind = None
            try:
                kind = chunk['kinds'][slot]
            except Exception:
                kind = None
            if kind not in (DESTR_TYPE_TREE, DESTR_TYPE_FALLING_ATOM):
                continue
            _scenery_fell(chunkID, chunk, slot, fallDirYaw, 1.0e9)
    except Exception:
        pass

def _face_yaw(fromX, fromZ, toX, toZ):

    _dx = toX - fromX
    _dz = toZ - fromZ
    if _dx * _dx + _dz * _dz < 0.0001:
        return 0.0
    return _math.atan2(_dx, _dz)

def _arena_center_xz(arena):

    try:
        bl, ur = arena.typeDescriptor.boundingBox
        return ((float(bl[0]) + float(ur[0])) * 0.5,
                (float(bl[1]) + float(ur[1])) * 0.5)
    except Exception:
        return (0.0, 0.0)

def _fmod_sound_play(snd):

    if snd is None:
        return False
    try:
        snd.play()
        return True
    except Exception:
        return False

def _offline_prepare_music(mc=None):

    import FMOD
    for _mg in _MUSIC_GROUPS:
        try:
            FMOD.loadSoundGroup(_mg)
        except Exception:
            pass
    if mc is not None:
        try:
            mc._loadConfig()
        except Exception:
            pass
    try:
        from helpers import SoundGroups as _SG
        if getattr(_SG, 'g_instance', None) is not None:
            try:
                _SG.g_instance.enableSounds('music', True)
            except Exception:
                pass
            try:
                _SG.g_instance.applyPreferences()
            except Exception:
                pass
    except Exception:
        pass

def _coords_centroid(coords):

    return (sum(_c[0] for _c in coords) / float(len(coords)),
            sum(_c[2] for _c in coords) / float(len(coords)))

def _nav_clear_forward(spaceID, x, z, dirX, dirZ, height, maxDist):

    try:
        gp = _nav_probe_ground(spaceID, x, z)
        groundY = gp[0] if gp is not None else 0.0
        length = _math.sqrt(dirX * dirX + dirZ * dirZ)
        if length < 0.001:
            return 0.0
        nx, nz = dirX / length, dirZ / length
        start = Math.Vector3(x, groundY + height, z)
        end = Math.Vector3(x + nx * maxDist, groundY + height, z + nz * maxDist)
        res = BigWorld.wg_collideSegment(spaceID, start, end, 128)
        if res is None:
            return maxDist
        dx, dz = res[0].x - x, res[0].z - z
        return _math.sqrt(dx * dx + dz * dz)
    except Exception:
        return 0.0

def _nav_elevation(spaceID, x, z, radius=25.0):

    try:
        gp = _nav_probe_ground(spaceID, x, z)
        if gp is None:
            return 0.0
        total, cnt = 0.0, 0
        for ang in (0.0, 1.5708, 3.14159, 4.71239):
            sp = _nav_probe_ground(spaceID, x + _math.sin(ang) * radius, z + _math.cos(ang) * radius)
            if sp is not None:
                total += sp[0]; cnt += 1
        if cnt == 0:
            return 0.0
        return gp[0] - (total / cnt)
    except Exception:
        return 0.0

def _solve_ballistic_dir(gunPos, target, speed, gravity, lofted, maxElevation=None):

    dx = target.x - gunPos.x
    dz = target.z - gunPos.z
    horiz = _math.sqrt(dx * dx + dz * dz)
    if horiz < 0.05 or speed <= 0.0:
        return None
    hx, hz = dx / horiz, dz / horiz
    dy = target.y - gunPos.y
    g = abs(gravity)
    if g < 0.001:
        return Math.Vector3(hx, dy / horiz, hz)

    v2 = speed * speed
    disc = v2 * v2 - g * (g * horiz * horiz + 2.0 * dy * v2)
    if disc < 0.0:
        theta = _math.radians(45.0)
    else:
        sqrtDisc = _math.sqrt(disc)
        if lofted:
            tanTheta = (v2 + sqrtDisc) / (g * horiz)
        else:
            tanTheta = (v2 - sqrtDisc) / (g * horiz)
        theta = _math.atan(tanTheta)

    if maxElevation is not None and theta > maxElevation:
        theta = max(0.0, maxElevation)

    result = Math.Vector3(hx * _math.cos(theta), _math.sin(theta), hz * _math.cos(theta))
    if result.length > 0.001:
        result.normalise()
    return result

def _solve_arty_shot(gunPos, target, speed, gravity, maxElevation):

    dx = target.x - gunPos.x
    dz = target.z - gunPos.z
    horiz = _math.sqrt(dx * dx + dz * dz)
    if horiz < 0.05:
        return None
    hx, hz = dx / horiz, dz / horiz
    dy = target.y - gunPos.y
    g = abs(gravity)
    if g < 0.001:
        return None
    if maxElevation is not None and maxElevation > _math.radians(10.0):
        theta = max(0.01, min(maxElevation, _math.radians(80.0)))
    else:
        theta = _math.radians(35.0)
    A = horiz * _math.tan(theta) - dy
    if A <= 1e-6:
        return None
    v = _math.sqrt((g * horiz * horiz) / (2.0 * A)) / _math.cos(theta)
    if v < 25.0: v = 25.0
    if v > speed: v = speed
    dir3 = Math.Vector3(hx * _math.cos(theta), _math.sin(theta), hz * _math.cos(theta))
    if dir3.length > 0.001: dir3.normalise()
    return dir3, v

_CRUISE_MODES = (
    ('CRUISE_CONTROL_MODE_NONE',    0,  0.00),
    ('CRUISE_CONTROL_MODE_FWD25',   1,  0.25),
    ('CRUISE_CONTROL_MODE_FWD50',   1,  0.50),
    ('CRUISE_CONTROL_MODE_FWD100',  1,  1.00),
    ('CRUISE_CONTROL_MODE_BCKW50', -1,  0.50),
    ('CRUISE_CONTROL_MODE_BCKW100',-1,  1.00),
)
_CRUISE_MODE_DEFAULT = 3

def _player_cruise_available(avatar):

    try:
        _ah = getattr(avatar, 'inputHandler', None)
        if _ah is not None and hasattr(_ah, '_AvatarInputHandler__isArenaStarted'):
            return bool(getattr(_ah, '_AvatarInputHandler__isArenaStarted', True))
        _ob = getattr(avatar, '_offline_battle', None)
        if _ob is not None and hasattr(_ob, '_battle_started'):
            return bool(getattr(_ob, '_battle_started', True))
    except Exception:
        pass
    return True

def _player_cruise_toggle(avatar):

    if not _player_cruise_available(avatar): return
    if not hasattr(avatar, '_cruise_active'): return
    if not hasattr(avatar, '_cruise_mode'):
        avatar._cruise_mode = _CRUISE_MODE_DEFAULT
    if getattr(avatar, '_cruise_active', False):
        avatar._cruise_active = False
        avatar._cruise_target = 0.0
    else:
        avatar._cruise_active = True
        avatar._cruise_target = 0.0

def _player_cruise_step(avatar, delta):

    if not _player_cruise_available(avatar): return
    idx = getattr(avatar, '_cruise_mode', _CRUISE_MODE_DEFAULT)
    try:
        idx = int(idx)
    except Exception:
        idx = _CRUISE_MODE_DEFAULT
    if delta > 0:
        idx = (idx + 1) % len(_CRUISE_MODES)
    else:
        idx = (idx - 1 + len(_CRUISE_MODES)) % len(_CRUISE_MODES)
    avatar._cruise_mode = idx
    try:
        _mName, _mDir, _mFrac = _CRUISE_MODES[idx]
    except Exception:
        _mName, _mDir, _mFrac = _CRUISE_MODES[_CRUISE_MODE_DEFAULT]
    if _mDir == 0:
        avatar._cruise_active = False
        avatar._cruise_target = 0.0
        return
    avatar._cruise_active = True
    try:
        _sl = avatar.vehicleTypeDescriptor.physics['speedLimits']
        if isinstance(_sl, (list, tuple)) and len(_sl) > 1:
            _fwd, _bwd = _sl[0], _sl[1]
        else:
            _fwd = float(_sl)
            _bwd = max(float(_sl) * 0.3, 5.0)
    except Exception:
        _fwd, _bwd = 20.0, 6.0
    if _mDir > 0:
        avatar._cruise_target = max(1.5, _fwd * _mFrac)
    else:
        avatar._cruise_target = -max(1.5, _bwd * _mFrac)

def _player_cruise_cancel(avatar):

    try:
        if getattr(avatar, '_cruise_active', False):
            avatar._cruise_active = False
            avatar._cruise_target = 0.0
    except Exception: pass

def _killfeed_display_name(battle, veh_id):

    try:
        _n = battle.arena.vehicles.get(veh_id, {}).get('name')
        if not _n:
            _ent = BigWorld.entity(veh_id)
            _n = getattr(_ent, 'name', None) or ''
        if not _n:
            _av = getattr(battle, 'playerAvatar', None)
            if _av is not None and veh_id == getattr(_av, 'playerVehicleID', None):
                _n = getattr(_av, 'name', '') or ''
        return _n or '?'
    except Exception:
        return '?'

_KILLFEED_FALLBACK = {
    'player_frag':               (u'[%(target)s] уничтожен!', (90.0, 220.0, 90.0, 255.0)),
    'player_friendly_fire_frag': (u'Уничтожен союзник [%(target)s]', (255.0, 160.0, 0.0, 255.0)),
    'ally_frag':                 (u'[%(attacker)s] уничтожил [%(target)s]', (90.0, 220.0, 90.0, 255.0)),
    'ally_friendly_fire_frag':   (u'Союзник [%(attacker)s] уничтожил союзника [%(target)s]', (255.0, 160.0, 0.0, 255.0)),
    'enemy_frag':                (u'[%(attacker)s] уничтожил [%(target)s]', (230.0, 70.0, 70.0, 255.0)),
    'enemy_friendly_fire_frag':  (u'Враг [%(attacker)s] уничтожил врага [%(target)s]', (255.0, 160.0, 0.0, 255.0)),
    'ally_suicide':              (u'Союзник [%(entity)s] самоуничтожился', (230.0, 70.0, 70.0, 255.0)),
    'enemy_suicide':             (u'Враг [%(entity)s] самоуничтожился', (90.0, 220.0, 90.0, 255.0)),
}

def _killfeed_show(battle, key, args):

    try:
        _bw = getattr(battle, 'battleWindow', None)
        if _bw is None:
            from gui.WindowsManager import g_windowsManager
            _bw = getattr(g_windowsManager, 'battleWindow', None)
        if _bw is None:
            return False
        _pm = getattr(_bw, '_Battle__pMsgsPanel', None)
        if _pm is None or not hasattr(_pm, 'showMessage'):
            return False
        _pm.showMessage(key, args or {})
        return True
    except Exception:
        return False

def _show_dead_damage_panel():

    try:
        from gui.WindowsManager import g_windowsManager
        _bw = getattr(g_windowsManager, 'battleWindow', None)
        if _bw is None:
            return
        _dp = getattr(_bw, 'damagePanel', None)
        if _dp is None:
            return
        try:
            _dp.start()
        except Exception: pass
        try:
            _dp.updateHealth(0)
        except Exception: pass
        try:
            for _m in _MODULE_RU.keys():
                _dp.updateCriticalIcon(_m, 'destroyed')
        except Exception: pass
        try:
            _dp.onVehicleDestroyed()
        except Exception: pass
        try:
            _bw.call('battle.postmortemPanel.show', [])
        except Exception: pass
        try:
            _bw.call('battle.damageInfoPanel.hide', [])
        except Exception: pass
    except Exception: pass

def _offline_timer_setArenaTime(self_win):

    _cb = getattr(self_win, '_offlineTimerCb', None)
    if _cb is not None:
        try: BigWorld.cancelCallback(_cb)
        except Exception: pass
        self_win._offlineTimerCb = None
    if getattr(self_win, '_offlineBattleFinished', False):
        return
    try:
        _arena = getattr(self_win, '_Battle__arena', None)
        if _arena is None:
            return
        _period = getattr(_arena, 'period', 0)
        _length = int(getattr(_arena, 'periodEndTime', 0) - BigWorld.time())
        if _length < 0:
            _length = 0
        if _period != constants.ARENA_PERIOD_AFTERBATTLE:
            try:
                self_win.call('battle.timerBar.setTotalTime', [_length])
            except Exception: pass
        _wasVisible = getattr(self_win, '_offlineTimerVisible', False)
        if _period == constants.ARENA_PERIOD_WAITING:
            try:
                from helpers import i18n
                self_win.call('battle.timerBig.setTimer', [i18n.makeString('#ingame_gui:timer/waiting')])
            except Exception: pass
            self_win._offlineTimerVisible = True
        elif _period == constants.ARENA_PERIOD_PREBATTLE:
            if not _wasVisible:
                try:
                    _ts = getattr(self_win, '_Battle__timerSound', None)
                    if _ts is not None and hasattr(_ts, 'play'):
                        _ts.play()
                except Exception: pass
            try:
                from helpers import i18n
                self_win.call('battle.timerBig.setTimer', [i18n.makeString('#ingame_gui:timer/starting'), _length])
            except Exception: pass
            self_win._offlineTimerVisible = True
        elif _period == constants.ARENA_PERIOD_BATTLE:
            if _wasVisible:
                try:
                    _ts = getattr(self_win, '_Battle__timerSound', None)
                    if _ts is not None and hasattr(_ts, 'stop'):
                        _ts.stop()
                except Exception: pass
                try:
                    from helpers import i18n
                    self_win.call('battle.timerBig.setTimer', [i18n.makeString('#ingame_gui:timer/started')])
                except Exception: pass
                try:
                    self_win.call('battle.timerBig.hide', [])
                except Exception: pass
            self_win._offlineTimerVisible = False
        elif _period == constants.ARENA_PERIOD_AFTERBATTLE:
            try:
                self_win.call('battle.showBattleTimer', [False])
            except Exception: pass
        if _length > 1 and _period != constants.ARENA_PERIOD_AFTERBATTLE:
            self_win._offlineTimerCb = BigWorld.callback(
                1.0, lambda: _offline_timer_setArenaTime(self_win))
    except Exception:
        pass

def _ballistic_flight_time(launchPos, initVelocity, gravityMag, targetPos):

    dx = targetPos.x - launchPos.x
    dz = targetPos.z - launchPos.z
    horizDist = _math.sqrt(dx * dx + dz * dz)
    horizSpeed = _math.sqrt(initVelocity.x * initVelocity.x + initVelocity.z * initVelocity.z)
    if horizSpeed > 0.5 and horizDist > 0.01:
        return max(0.05, horizDist / horizSpeed)
    dist = (targetPos - launchPos).length
    speed = initVelocity.length
    return max(0.05, dist / max(speed, 1.0))

def _trace_ballistic_hit(spaceID, shotPos, initVelocity, gravityMag, maxDist, exceptIDs):

    gravVec = Math.Vector3(0.0, -gravityMag, 0.0)
    v0 = initVelocity.length
    if v0 < 1.0:
        return shotPos, None, 1.0, 0, Math.Vector3(0, -1, 0), None

    dt = max(0.005, min(0.05, 3.0 / v0))
    maxTime = 30.0
    prevPos = Math.Vector3(shotPos)
    t = 0.0
    while t < maxTime:
        t += dt
        newPos = shotPos + initVelocity * t + gravVec * (0.5 * t * t)
        if newPos.y < -300.0:
            break
        segVec = newPos - prevPos
        segLen = segVec.length
        if segLen < 0.001:
            prevPos = newPos
            continue

        staticRes = BigWorld.wg_collideSegment(spaceID, prevPos, newPos, 128)
        if staticRes is not None:
            try:
                _mk = staticRes[2]
                _soft = (_mk >= _const.DESTRUCTIBLE_MATKINDS_MIN and
                         _mk <= _const.DESTRUCTIBLE_MATKINDS_MAX and
                         not _destr_whisker_blocks(spaceID, _mk, staticRes[4], staticRes[5]))
                if _soft:
                    try:
                        _sd = Math.Vector3(segVec)
                        if _sd.length > 0.001:
                            _sd.normalise()
                        _try_destroy_destructible_at(spaceID, staticRes[0], _sd)
                    except Exception:
                        pass
                    staticRes = None
            except Exception:
                pass
        dynRes = _collide_dynamic_component(prevPos, newPos, exceptIDs)

        if staticRes is None and dynRes is None:
            if newPos.y < -300.0: break
            prevPos = newPos
            continue

        curVel = initVelocity + gravVec * t
        curDir = Math.Vector3(curVel)
        if curDir.length > 0.001: curDir.normalise()

        _staticDist = (staticRes[0] - prevPos).length if staticRes is not None else None
        if dynRes is not None and (_staticDist is None or dynRes[1] <= _staticDist):
            _veh, _dist, _cos, _thick, _comp = dynRes
            _segDirN = Math.Vector3(segVec)
            _segDirN.normalise()
            return prevPos + _segDirN * _dist, _veh, _cos, _thick, curDir, _comp
        else:
            return staticRes[0], None, 1.0, 0, curDir, None

    curVel = initVelocity + gravVec * t
    curDir = Math.Vector3(curVel)
    if curDir.length > 0.001: curDir.normalise()
    return prevPos, None, 1.0, 0, curDir, None

_OFFLINE_HP = {}
_OFFLINE_DEAD = set()

def _vehicle_world_matrix(vehicle):

    _om = getattr(vehicle, '_offline_matrix', None)
    if _om is not None:
        try:
            return Math.Matrix(_om)
        except Exception:
            pass
    try:
        return Math.Matrix(vehicle.model.matrix)
    except Exception:
        _m = Math.Matrix()
        try:
            _m.setTranslate(Math.Vector3(getattr(vehicle, 'position', Math.Vector3(0, 0, 0))))
        except Exception:
            pass
        return _m

def _vehicle_gun_world_pos(veh, fallback_height=1.8):

    try:
        gunModel = veh.appearance.modelsDesc['gun']['model']
        hp = Math.Matrix(gunModel.node('HP_gunFire')).translation
        if hp.lengthSquared != 0.0:
            return Math.Vector3(hp)
    except Exception:
        pass
    p = _entity_world_pos(veh)
    try:
        td = veh.typeDescriptor
        p.y += td.chassis['hullPosition'].y + td.hull['turretPositions'][0].y
        return p
    except Exception:
        p.y += fallback_height
        return p

def _entity_world_pos(veh):

    if veh is None:
        return Math.Vector3(0, 0, 0)
    try:
        return Math.Vector3(_vehicle_world_matrix(veh).translation)
    except Exception:
        pass
    try:
        om = getattr(veh, '_offline_matrix', None)
        if om is not None:
            return Math.Vector3(om.translation)
    except Exception:
        pass
    try:
        return Math.Vector3(veh.model.matrix.translation)
    except Exception:
        pass
    try:
        return Math.Vector3(veh.position)
    except Exception:
        return Math.Vector3(0, 0, 0)

def _pt_xyz(pt):

    if pt is None:
        return None
    try:
        return (float(pt.x), float(pt.y), float(pt.z))
    except Exception:
        pass
    try:
        return (float(pt[0]), float(pt[1]), float(pt[2]))
    except Exception:
        return None

def _shell_he_radius(shell):

    r = 0.0
    cal = 0.0
    try:
        r = float((shell or {}).get('explosionRadius', 0.0) or 0.0)
    except Exception:
        r = 0.0
    try:
        cal = float((shell or {}).get('caliber', 0.0) or 0.0)
    except Exception:
        cal = 0.0
    if r <= 0.0 and cal > 0.0:
        r = cal * cal / 5555.0
    if cal > 0.0:
        r = max(r, cal / 10.0)
    if r <= 0.0:
        r = 8.0
    return r

def _shell_he_damage(shell):
    d = (shell or {}).get('damage', (50, 50))
    try:
        if hasattr(d, 'x'):
            return int(d.x)
        if isinstance(d, (list, tuple)):
            return int(d[0])
        return int(d)
    except Exception:
        return 50

def _is_he_shell(kind, is_arty):
    k = str(kind or '')
    if k == 'HIGH_EXPLOSIVE':
        return True
    if k == 'HOLLOW_CHARGE':
        return False
    if k.startswith('ARMOR_PIERCING') and k != 'ARMOR_PIERCING_HE':
        return False
    return bool(is_arty)

def _vehicle_matrix_provider(veh):
    if veh is None:
        return None
    om = getattr(veh, '_offline_matrix', None)
    if om is not None:
        return om
    return getattr(veh, 'matrix', None)

def _set_veh_health(veh, hp):
    if veh is None:
        return
    try:
        hp = int(hp)
    except Exception:
        hp = 0
    try:
        _OFFLINE_HP[veh.id] = hp
        if hp <= 0:
            _OFFLINE_DEAD.add(veh.id)
        else:
            _OFFLINE_DEAD.discard(veh.id)
    except Exception:
        pass
    try:
        veh._offline_health = hp
    except Exception:
        pass
    try:
        veh.health = hp
    except Exception:
        pass

def _vehicle_health(veh):
    if veh is None:
        return 0
    try:
        if veh.id in _OFFLINE_HP:
            return int(_OFFLINE_HP.get(veh.id, 0))
    except Exception:
        pass
    try:
        oh = getattr(veh, '_offline_health', None)
        if oh is not None:
            return int(oh)
    except Exception:
        pass
    try:
        return int(getattr(veh, 'health', 0) or 0)
    except Exception:
        return 0

def _vehicle_is_alive(veh):
    if veh is None:
        return False
    try:
        if veh.id in _OFFLINE_DEAD:
            return False
    except Exception:
        pass
    if _vehicle_health(veh) <= 0:
        return False
    try:
        if not getattr(veh, 'isCrewActive', True):
            return False
    except Exception:
        pass
    try:
        p = BigWorld.player()
        arena = getattr(p, 'arena', None) if p is not None else None
        if arena is not None:
            info = arena.vehicles.get(veh.id)
            if info is not None and not info.get('isAlive', True):
                return False
        dead = getattr(p, '_deadVehicleIDs', None) if p is not None else None
        if dead is not None and veh.id in dead:
            return False
    except Exception:
        pass
    return True

def _stamp_entity_pose(veh, matrix=None):

    if veh is None:
        return
    if matrix is None:
        matrix = getattr(veh, '_offline_matrix', None)
    if matrix is None:
        return
    try:
        t = matrix.translation
        veh.position = Math.Vector3(t.x, t.y, t.z)
    except Exception:
        pass
    try:
        _sync_gui_matrix(veh, matrix)
    except Exception:
        pass
    try:
        _bind_marker_to_hp_gui(veh)
    except Exception:
        pass

def _hp_gui_height(veh):

    off_y = 2.35
    try:
        hull = veh.typeDescriptor.hull['hitTester']
        turret = veh.typeDescriptor.turret['hitTester']
        hy = 0.0
        ty = 0.0
        try:
            hy = float(hull.bbox[1][1])
        except Exception:
            hy = 0.0
        try:
            ty = float(turret.bbox[1][1])
        except Exception:
            ty = 0.0
        if hy > 0.2 or ty > 0.2:
            off_y = hy + max(0.35, ty * 0.55)
    except Exception:
        pass
    return off_y

def _hp_gui_provider(veh):

    if veh is None:
        return None
    try:
        mdl = getattr(veh, 'model', None)
        if mdl is not None:
            node = mdl.node('HP_gui')
            if node is not None:
                return node
    except Exception:
        pass
    return None

def _bind_marker_to_hp_gui(veh):
    if veh is None:
        return None
    mid = getattr(veh, 'marker', -1)
    if mid == -1:
        return _hp_gui_provider(veh)
    prov = _hp_gui_provider(veh)
    if prov is None:
        prov = _sync_gui_matrix(veh)
    try:
        bw = getattr(g_windowsManager, 'battleWindow', None)
        vmm = getattr(bw, 'vMarkersManager', None) if bw is not None else None
        if vmm is not None:
            descs = getattr(vmm, '_VehicleMarkersManager__vMarkerDescs', None)
            if descs is not None and 0 <= mid < len(descs) and descs[mid] is not None:
                gui = descs[mid].gui
                if getattr(gui, 'wg_positionMatProv', None) is not prov:
                    gui.wg_positionMatProv = prov
    except Exception:
        pass
    return prov

def _sync_gui_matrix(veh, matrix=None):

    if veh is None:
        return None
    if matrix is None:
        matrix = getattr(veh, '_offline_matrix', None)
    if matrix is None:
        return None
    gm = getattr(veh, '_offline_gui_matrix', None)
    if gm is None:
        gm = Math.Matrix()
        try:
            gm.notModel = True
        except Exception:
            pass
        veh._offline_gui_matrix = gm
    try:
        t = matrix.translation
        off_y = _hp_gui_height(veh)
        gm.setRotateYPR((matrix.yaw, matrix.pitch, matrix.roll))
        gm.translation = Math.Vector3(t.x, t.y + off_y, t.z)
    except Exception:
        return gm
    return gm

def _camera_shake(duration=0.35, strength=0.14):

    cam = None
    try:
        cam = BigWorld.camera()
    except Exception:
        return False
    ok = False
    try:
        d = cam.direction.scale(float(strength))
        sh = getattr(cam, 'shake', None)
        if sh is not None:
            sh(float(duration), d)
            ok = True
    except Exception:
        pass
    try:
        pvt = cam.pivotPosition
        kick = Math.Vector3(pvt.x, pvt.y + float(strength) * 1.15, pvt.z)
        cam.pivotPosition = kick
        def _restore(_c=cam, _p=Math.Vector3(pvt.x, pvt.y, pvt.z)):
            try:
                _c.pivotPosition = _p
            except Exception:
                pass
        BigWorld.callback(max(0.08, float(duration) * 0.45), _restore)
        ok = True
    except Exception:
        pass
    try:
        p = BigWorld.player()
        aih = getattr(p, 'inputHandler', None)
        ctrl = getattr(aih, 'ctrl', None) if aih is not None else None
        ac = None
        if ctrl is not None:
            ac = getattr(ctrl, '_ArcadeControlMode__cam', None) or getattr(ctrl, '_SniperControlMode__cam', None)
        if ac is not None and hasattr(ac, 'getYawPitch') and hasattr(ac, 'setYawPitch'):
            yaw, pitch = ac.getYawPitch()
            ac.setYawPitch(yaw + (_random.random() - 0.5) * 0.06 * strength / 0.14,
                           pitch + (_random.random() - 0.5) * 0.05 * strength / 0.14)
            def _unjolt(_ac=ac, _y=yaw, _p=pitch):
                try:
                    _ac.setYawPitch(_y, _p)
                except Exception:
                    pass
            BigWorld.callback(max(0.06, float(duration) * 0.25), _unjolt)
            ok = True
    except Exception:
        pass
    return ok

def _apply_hit_impulse(victim, shot_from, penetrated=True):

    try:
        p = BigWorld.player()
        if p is None or getattr(p, 'playerVehicleID', None) != getattr(victim, 'id', None):
            return
    except Exception:
        return
    _camera_shake(0.38 if penetrated else 0.26, 0.18 if penetrated else 0.11)
    try:
        ah = getattr(p, 'inputHandler', None)
        aim = getattr(ah, 'aim', None) if ah is not None else None
        gYaw = (_entity_world_pos(victim) - Math.Vector3(shot_from)).yaw
        if penetrated:
            if aim is not None:
                aim.showHit(gYaw)
        else:
            try:
                p.showNoPenArc(gYaw)
            except Exception:
                pass
            try:
                p.showNoPenHitmarker()
            except Exception:
                pass
    except Exception:
        pass

def _vehicle_collide_segment_component(vehicle, startPoint, endPoint):

    descr = getattr(vehicle, 'typeDescriptor', None)
    if descr is None:
        return None
    _w2v = _vehicle_world_matrix(vehicle)
    try:
        _wPos = Math.Vector3(_w2v.translation)
    except Exception:
        _wPos = getattr(vehicle, 'position', Math.Vector3(0, 0, 0))
    worldToVehMatrix = _w2v
    worldToVehMatrix.invert()
    localStart = worldToVehMatrix.applyPoint(startPoint)
    localEnd = worldToVehMatrix.applyPoint(endPoint)
    res = None
    for compDescr, compMatrix, compName in _vehicle_components_with_names(vehicle):
        try:
            hitTester = compDescr['hitTester']
            if hitTester is None or not hitTester.isBspModelLoaded():
                continue
            collisions = hitTester.localHitTest(compMatrix.applyPoint(localStart), compMatrix.applyPoint(localEnd))
        except Exception:
            continue
        if collisions is None:
            continue
        for dist, _, hitAngleCos, matKind in collisions:
            if res is None or res[0] >= dist:
                res = (dist, hitAngleCos, compDescr['armor'].get(matKind, 0), compName)

    return res

def _vehicle_components_with_names(vehicle):

    vehicleDescr = vehicle.typeDescriptor
    m = Math.Matrix()
    m.setIdentity()
    yield (vehicleDescr.chassis, m, 'chassis')

    hullOffset = vehicleDescr.chassis['hullPosition']
    m = Math.Matrix()
    m.setTranslate(-hullOffset)
    yield (vehicleDescr.hull, m, 'hull')

    turretYaw = 0.0
    gunPitch = 0.0
    try:
        turretYaw = Math.Matrix(vehicle.appearance.turretMatrix).yaw
    except Exception:
        turretYaw = 0.0
    turretMatrix = Math.Matrix()
    turretMatrix.setTranslate(-hullOffset - vehicleDescr.hull['turretPositions'][0])
    m = Math.Matrix()
    m.setRotateY(-turretYaw)
    turretMatrix.postMultiply(m)
    yield (vehicleDescr.turret, turretMatrix, 'turret')

    try:
        gunPitch = Math.Matrix(vehicle.appearance.gunMatrix).pitch
    except Exception:
        gunPitch = 0.0
    gunMatrix = Math.Matrix()
    gunMatrix.setTranslate(-vehicleDescr.turret['gunPosition'])
    m = Math.Matrix()
    m.setRotateX(-gunPitch)
    gunMatrix.postMultiply(m)
    gunMatrix.preMultiply(turretMatrix)
    yield (vehicleDescr.gun, gunMatrix, 'gun')

def _collide_dynamic_component(startPoint, endPoint, exceptIDs):

    res = None
    try:
        dx = endPoint.x - startPoint.x
        dz = endPoint.z - startPoint.z
        trav = dx * dx + dz * dz
    except Exception:
        return None
    if trav < 0.01:
        return None
    cands = []
    try:
        arena = BigWorld.player().arena
        ids = list(arena.vehicles.iterkeys())
    except Exception:
        return None
    for vehicleID in ids:
        if vehicleID in exceptIDs:
            continue
        vehicle = BigWorld.entity(vehicleID)
        if vehicle is None or not getattr(vehicle, 'isStarted', False):
            continue
        try:
            if not _vehicle_is_alive(vehicle):
                continue
        except Exception:
            continue
        p = _offline_entity_pos(vehicle)
        if p is None:
            continue
        try:
            dist = _scenery_seg_dist(p.x, p.z, startPoint.x, startPoint.z, dx, dz, trav)
        except Exception:
            continue
        if dist > 16.0:
            continue
        cands.append((dist, vehicle))
    if not cands:
        return None
    cands.sort(key=lambda item: item[0])
    del cands[8:]
    shotLen = startPoint.distTo(endPoint)
    for _d, vehicle in cands:
        collRes = None
        try:
            collRes = _vehicle_collide_segment_component(vehicle, startPoint, endPoint)
        except Exception:
            collRes = None
        if collRes is None:
            continue
        dist = collRes[0]
        if dist < shotLen:
            if res is None or res[1] > dist:
                res = (vehicle, dist, collRes[1], collRes[2], collRes[3] if len(collRes) > 3 else None)
    return res

def _veh_velocity_2d(veh):

    try:
        _ob = None
        try:
            _ob = getattr(BigWorld.player(), '_offline_battle', None)
        except Exception:
            _ob = None
        _pid = None
        if _ob is not None:
            try:
                _pid = getattr(_ob.playerAvatar, 'playerVehicleID', None)
            except Exception:
                _pid = None
        _is_player = False
        if _ob is not None:
            if veh is getattr(_ob, 'playerAvatar', None):
                _is_player = True
            elif _pid is not None and getattr(veh, 'id', None) == _pid:
                _is_player = True
        if _is_player:
            _spd = float(getattr(_ob, '_cur_speed', 0.0) or 0.0)
            _yaw = float(getattr(_ob, '_cur_yaw', 0.0) or 0.0)
            return Math.Vector3(_math.sin(_yaw) * _spd, 0.0, _math.cos(_yaw) * _spd)
        _hp = getattr(veh, 'health', None)
        if _hp is not None and _hp <= 0:
            return Math.Vector3(0.0, 0.0, 0.0)
        _spd = getattr(veh, '_cur_speed', None)
        if _spd is None:
            _spd = 0.0
        _spd = float(_spd or 0.0)
        _om = getattr(veh, '_offline_matrix', None)
        _yaw = getattr(_om, 'yaw', 0.0) if _om is not None else getattr(veh, 'yaw', 0.0)
        return Math.Vector3(_math.sin(_yaw) * _spd, 0.0, _math.cos(_yaw) * _spd)
    except Exception:
        return Math.Vector3(0.0, 0.0, 0.0)

def _offline_play_collision_fx(veh, hitPt):

    try:
        if veh is None or not getattr(veh, 'isStarted', False):
            return
        if hitPt is None:
            try:
                hitPt = veh.position
            except Exception:
                return
        now = BigWorld.time()
        if (now - float(getattr(veh, '_last_col_fx', -10.0))) < 0.22:
            return
        veh._last_col_fx = now
        veh.showVehicleCollisionEffect(hitPt)
    except Exception:
        pass

def _predict_aim_point(gunPos, aimPoint, speed, gravity, targetVeh, maxIter=4):

    _vel = None
    try:
        _vel = _veh_velocity_2d(targetVeh)
    except Exception:
        _vel = None
    if _vel is None or getattr(_vel, 'length', 0.0) < 0.5:
        return Math.Vector3(aimPoint)
    _pred = Math.Vector3(aimPoint)
    _gp = Math.Vector3(gunPos)
    _t = (_pred - _gp).length / max(float(speed), 1.0)
    _lastDir = None
    for _i in range(maxIter):
        _d = _solve_ballistic_dir(_gp, _pred + _vel * _t, speed, gravity, False)
        if _d is None:
            if _lastDir is None:
                return Math.Vector3(aimPoint)
            return _pred + _vel * _t
        _lastDir = _d
        _ft = _ballistic_flight_time(_gp, Math.Vector3(_d) * speed, gravity, _pred + _vel * _t)
        if abs(_ft - _t) < 0.05:
            return _pred + _vel * _t
        _t = max(_ft, 0.05)
    return _pred + _vel * _t

def _lead_ballistic_dir_for_gun(gunPos, aimPoint, speed, gravity, exceptIDs):

    try:
        _dir = Math.Vector3(aimPoint) - gunPos
        _dl = _dir.length
        if _dl < 0.001:
            return None
        _dirN = _dir / _dl
        _res = _collide_dynamic_component(gunPos, gunPos + _dirN * 3000.0, exceptIDs)
        if _res is None:
            return None
        _aimVeh = _res[0]
        if getattr(_aimVeh, 'health', 0) <= 0:
            return None
        if _veh_velocity_2d(_aimVeh) is None:
            return None
        _pred = _predict_aim_point(gunPos, aimPoint, speed, gravity, _aimVeh)
        if _pred is None:
            return None
        if (_pred - Math.Vector3(aimPoint)).length < 0.2:
            return None
        _d = _solve_ballistic_dir(gunPos, _pred, speed, gravity, False)
        if _d is None:
            return None
        return _d
    except Exception:
        return None

def _ballistic_arrival_pos(launchPos, initVelocity, gravityMag, t):

    try:
        _g = Math.Vector3(0.0, -abs(float(gravityMag)), 0.0)
        _p = Math.Vector3(launchPos) + Math.Vector3(initVelocity) * float(t) + _g * (0.5 * float(t) * float(t))
        return _p
    except Exception:
        _p = Math.Vector3(launchPos)
        return _p

def _clamp_impact_to_veh(veh, arrivalPt, maxProbe=18.0):

    if veh is None or getattr(veh, 'health', 0) <= 0:
        return None
    try:
        _om = getattr(veh, '_offline_matrix', None)
        if _om is not None:
            _tp = Math.Vector3(_om.translation)
        else:
            _tp = Math.Vector3(getattr(veh, 'position', Math.Vector3(0, 0, 0)))
        _arr = Math.Vector3(arrivalPt)
        _seg = _tp - _arr
        _segL = _seg.length
        if _segL < 0.01 or _segL > maxProbe:
            return None
        _segN = Math.Vector3(_seg) / _segL
        _end = _arr + _segN * (_segL + 3.0)
        _col = _vehicle_collide_segment_component(veh, _arr, _end)
        if _col is None:
            return None
        _hitW = _arr + _segN * float(_col[0])
        if (_hitW - _tp).length > maxProbe + 3.0:
            return None
        return _hitW
    except Exception:
        return None

_decal_counter = [0]

def _pick_damage_sticker_id(isPen, isRicochet=False):

    try:
        from items import vehicles
        ds = vehicles.g_cache.damageStickers
        if not isinstance(ds, dict):
            return None
        descrs = ds.get('descrs') or {}
        if not descrs:
            return None
        ids = ds.get('ids', {}) or {}
        if isPen:
            want = ('pierc', 'penetr', 'hole', 'through', 'shot')
        elif isRicochet:
            want = ('ricochet', 'bounce', 'scratch', 'slash')
        else:
            want = ('scratch', 'splash', 'nopen', 'no_pen', 'scuff')
        for _name, _sid in (ids.iteritems() if hasattr(ids, 'iteritems') else ids.items()):
            _low = str(_name).lower()
            if any(_k in _low for _k in want):
                return _sid
        try:
            _lst = sorted(descrs.keys(), key=lambda _k: -int(descrs[_k].get('priority', 0)))
            return _lst[0]
        except Exception:
            _keys = list(descrs.keys())
            return _keys[0] if _keys else None
    except Exception:
        return None

def _comp_local_segment(veh, compName, worldStart, worldEnd):

    _fallback = None
    try:
        worldToVehMatrix = _vehicle_world_matrix(veh)
        worldToVehMatrix.invert()
        localStart = worldToVehMatrix.applyPoint(worldStart)
        localEnd = worldToVehMatrix.applyPoint(worldEnd)
        result = None
        for _cd, _cm, _cn in _vehicle_components_with_names(veh):
            if _cn == compName:
                result = (_cm.applyPoint(localStart), _cm.applyPoint(localEnd))
                break
            if _cn == 'hull':
                _fallback = (_cm.applyPoint(localStart), _cm.applyPoint(localEnd))
        if result is None:
            result = _fallback
        return result
    except Exception:
        return None

def _add_hit_decal(veh, compName, worldHitPos, worldDir, isPen, isRicochet=False, jitter=0.0):

    try:
        va = getattr(veh, 'appearance', None)
        if va is None:
            return
        comp = compName if compName in ('hull', 'turret') else 'hull'
        stickers = getattr(va, '_VehicleAppearance__stickers', None) or {}
        if comp not in stickers:
            comp = 'hull'
        if comp not in stickers:
            return
        sid = _pick_damage_sticker_id(isPen, isRicochet)
        if sid is None:
            return
        d = Math.Vector3(worldDir)
        try:
            d.normalise()
        except Exception:
            d = Math.Vector3(0.0, -1.0, 0.0)
        _hp = Math.Vector3(worldHitPos)
        if jitter > 0.0:
            try:
                _ref = Math.Vector3(0.0, 1.0, 0.0)
                if abs(_ref.dot(d)) > 0.98:
                    _ref = Math.Vector3(1.0, 0.0, 0.0)
                _axA = _ref.cross(d)
                _axA.normalise()
                _axB = d.cross(_axA)
                _rr = jitter * _math.sqrt(_random.random())
                _aa = _random.uniform(0.0, 6.283185307179586)
                _hp += _axA.scale(_rr * _math.cos(_aa)) + _axB.scale(_rr * _math.sin(_aa))
            except Exception: pass
        seg = _comp_local_segment(veh, comp, _hp - d.scale(0.4), _hp + d.scale(0.4))
        if seg is None:
            return
        _decal_counter[0] += 1
        va.addDamageSticker(_decal_counter[0], comp, sid, seg[0], seg[1])
    except Exception:
        pass

def _calc_shot_penetration(shotData, targetDescr, targetYaw, shotDirWorld):

    try:
        hull_pa = targetDescr.hull.get('primaryArmor', (20, 20, 20))
        if isinstance(hull_pa, (int, float)): hull_pa = (hull_pa, hull_pa, hull_pa)

        shot_dir_local = _math.atan2(shotDirWorld.x, shotDirWorld.z) - targetYaw
        shot_dir_local = (shot_dir_local + _math.pi) % (2 * _math.pi) - _math.pi
        abs_ang = abs(shot_dir_local)

        if abs_ang < _math.radians(60):
            thickness = hull_pa[0]
        elif abs_ang > _math.radians(120):
            thickness = hull_pa[1]
        else:
            thickness = hull_pa[2]

        cosAngle = max(0.35, _math.cos(min(abs_ang, _math.radians(75))))

        shellInfo = shotData.get('shell', {}) if shotData else {}

        cosForCalc = max(abs(cosAngle), 0.02)
        ricochetAngleCos = shellInfo.get('ricochetAngleCos')
        if ricochetAngleCos is not None and cosForCalc < ricochetAngleCos:
            return (False, True, thickness, cosAngle)

        normAngle = shellInfo.get('normalizationAngle', 0.0)
        effAngleRad = max(0.0, _math.acos(cosForCalc) - normAngle)
        effCos = max(_math.cos(effAngleRad), 0.02)
        effectiveArmor = thickness / effCos

        pierce = shotData.get('piercingPower', (50, 50)) if shotData else (50, 50)
        pierce_val = pierce.x if hasattr(pierce, 'x') else (pierce[0] if isinstance(pierce, (list, tuple)) else float(pierce))
        actualPiercing = pierce_val * _random.uniform(0.875, 1.125)

        isPenetration = (thickness <= 0) or (actualPiercing >= effectiveArmor)
        return (isPenetration, False, thickness, cosAngle)
    except Exception:
        return (True, False, 0.0, 1.0)

def _offline_stop_vehicle_visual(veh):

    if veh is None:
        return
    try:
        va = getattr(veh, 'appearance', None)
    except Exception:
        va = None
    if va is not None:
        try:
            va._offline_periodic_stopped = True
        except Exception:
            pass
        try:
            _tid = getattr(va, '_VehicleAppearance__periodicTimerID', None)
            if _tid is not None:
                BigWorld.cancelCallback(_tid)
                va._VehicleAppearance__periodicTimerID = None
        except Exception:
            pass
        try:
            _fxp = getattr(va, '_VehicleAppearance__effectsPlayer', None)
            _fxm = getattr(_fxp, '_offline_fx_model', None) if _fxp is not None else None
            if _fxp is not None:
                try:
                    _fxp.stop()
                except Exception:
                    pass
            if _fxm is not None:
                try:
                    BigWorld.delAlwaysUpdateModel(_fxm)
                except Exception:
                    pass
                try:
                    BigWorld.delModel(_fxm)
                except Exception:
                    pass
        except Exception:
            pass
    try:
        if getattr(veh, 'isStarted', False):
            try:
                BigWorld.delShadowEntity(veh)
            except Exception:
                pass
            try:
                veh.stopVisual()
            except Exception:
                try:
                    if getattr(veh, 'appearance', None) is not None:
                        veh.appearance.destroy()
                except Exception:
                    pass
            try:
                veh.isStarted = False
            except Exception:
                pass
    except Exception:
        pass

_LAST_OFFLINE_BATTLE = [None]


class OfflineVehicleFilter(object):
    def __init__(self, startPos, startYaw=0.0):
        self.position = Math.Vector3(startPos.x, startPos.y, startPos.z)
        self.yaw      = float(startYaw)
        self.pitch    = 0.0
        self.roll     = 0.0
        self.velocity = Math.Vector3(0.0, 0.0, 0.0)
        self.speed    = 0.0
        self.rotationSpeed = 0.0
        self.movementInfo  = None
        self.enableClientFilters = False

    def reset(self, pos):
        self.position = Math.Vector3(pos.x, pos.y, pos.z)

    def setMovement(self, speed, rotSpeed):
        self.speed = speed
        self.rotationSpeed = rotSpeed
        self.velocity = Math.Vector3(math.sin(self.yaw) * speed, 0.0, math.cos(self.yaw) * speed)

    def getVehicleSpeed(self): return self.speed

class DummyGunRotator:
    def __init__(self):
        self.turretMatrix = Math.WGAdaptiveMatrixProvider()
        self.gunMatrix = Math.WGAdaptiveMatrixProvider()
        self.__loftedTrajectory = False
        self.__turretYaw = 0.0
        self.__gunPitch = 0.0
        self.__updateMatrices()
    def setLoftedTrajectory(self, flag):    pass
    def switchLoftedTrajectory(self):       pass
    def shoot(self):                        pass
    def update(self, *a, **kw):             pass
    def setGunMarker(self, *a, **kw):       pass
    def destroy(self):                      pass
    def onVehicleCollision(self, *a):       pass
    def setShotPosition(self, *a):          pass
    clientMode = True
    shotPointSourceFunctor = None
    def __updateMatrices(self):
        m = Math.Matrix()
        m.setRotateY(self.__turretYaw)
        self.turretMatrix.setStaticTransform(m)
        m2 = Math.Matrix()
        m2.setRotateX(self.__gunPitch)
        self.gunMatrix.setStaticTransform(m2)

    def start(self): pass
    def stop(self): pass

    def getLoftedTrajectory(self): return self.__loftedTrajectory

    def attachToFilter(self, vehicleFilter):
        vehicleFilter.turretMatrix.target = self.turretMatrix
        vehicleFilter.gunMatrix.target = self.gunMatrix

    def updateRotation(self, deltaYaw, deltaPitch):
        self.__turretYaw += deltaYaw
        self.__gunPitch += deltaPitch
        self.__gunPitch = max(-0.3, min(0.3, self.__gunPitch))
        self.__updateMatrices()

class _DummyCell:
    def trackPointWithGun(self, *a): pass
    def stopTrackingWithGun(self, *a): pass
    def __getattr__(self, name): return lambda *a, **kw: None


def _apply_offline_crew_and_eq_bonuses(descr, veh_inv_id):

    if descr is None:
        return
    try:
        from items.tankmen import TankmanDescr, computeCrewSkillsEfficiency
        from Offline import Manager as _OM
        crew_ids = []
        try:
            if _OM._inv_data_ref is not None and len(_OM._inv_data_ref) > 3:
                crew_ids = list(_OM._inv_data_ref[3].get(veh_inv_id) or [])
        except Exception:
            crew_ids = []
        if not crew_ids and _OM._crew_map_ref:
            crew_ids = list(_OM._crew_map_ref.get(veh_inv_id) or [])
        tds = []
        tcache = _OM._t_cache_ref or {}
        for tid in crew_ids:
            if tid is None:
                continue
            cd = tcache.get(int(tid))
            if cd:
                tds.append(TankmanDescr(cd))
        if not tds:
            return
        avg = 0.0
        n = 0
        for td in tds:
            avg += float(td.roleLevel)
            n += 1
        if n:
            avg /= float(n)
            descr.miscAttrs['crewLevelIncrease'] = descr.miscAttrs.get('crewLevelIncrease', 0) + (avg - 100.0)
        try:
            eff = computeCrewSkillsEfficiency(descr, tds)
        except Exception:
            eff = {}
        repair = float(eff.get('repair', 0.0) or 0.0)
        camo = float(eff.get('camouflage', 0.0) or 0.0)
        cmd = float(eff.get('commander', 0.0) or 0.0)
        rad = float(eff.get('radioman', 0.0) or 0.0)
        gnr = float(eff.get('gunner', 0.0) or 0.0)
        ldr = float(eff.get('loader', 0.0) or 0.0)
        drv = float(eff.get('driver', 0.0) or 0.0)
        descr.miscAttrs['repairSpeedFactor'] = descr.miscAttrs.get('repairSpeedFactor', 1.0) * (1.0 + 0.7 * repair)
        descr.miscAttrs['circularVisionRadiusFactor'] = descr.miscAttrs.get('circularVisionRadiusFactor', 1.0) * (
            1.0 + 0.03 * cmd + 0.02 * rad + 0.05 * camo)
        descr.miscAttrs['gunAimingTimeFactor'] = descr.miscAttrs.get('gunAimingTimeFactor', 1.0) * (1.0 - 0.12 * gnr)
        descr.miscAttrs['gunReloadTimeFactor'] = descr.miscAttrs.get('gunReloadTimeFactor', 1.0) * (1.0 - 0.10 * ldr)
        try:
            sl = descr.physics.get('speedLimits')
            if sl:
                descr.physics['speedLimits'] = [
                    float(sl[0]) * (1.0 + 0.05 * drv),
                    float(sl[1]) * (1.0 + 0.05 * drv)]
        except Exception:
            pass
        LOG_NOTE("[BATTLE] crew/eq bonuses: crewAvg=%.1f repair=%.2f aim=%.2f reload=%.2f" % (
            avg if n else 0.0, descr.miscAttrs.get('repairSpeedFactor', 1.0),
            descr.miscAttrs.get('gunAimingTimeFactor', 1.0),
            descr.miscAttrs.get('gunReloadTimeFactor', 1.0)))
    except Exception as _bon_e:
        LOG_NOTE("[BATTLE] crew/eq bonuses failed: %s" % _bon_e)

class SimpleAvatar:
    def __init__(self, name, team, spaceID):
        self.name = name
        self.team = team
        self.spaceID = spaceID
        self.playerVehicleID = None
        self._deadVehicleIDs  = set()
        self._damagedVehicleIDs = set()
        self._player_killed_ids = set()
        self._veh_col_times = {}
        self._total_damage    = 0
        self._shots_received  = 0
        self._shots_hit       = 0
        self._frags           = 0
        self.vehicleTypeDescriptor = None
        self.isOnArena = True
        self.isVehicleAlive = True
        self.inputHandler = None
        self.gunRotator = None
        try:
            from helpers import SoundGroups as _SG
            if _SG.g_instance is not None:
                _SG.g_instance.loadSounds('arena')
        except Exception:
            pass
        try:
            from gui import IngameSoundNotifications as _ISN
            self.soundNotifications = _ISN.IngameSoundNotifications()
            self.soundNotifications.start()
            LOG_NOTE("[BATTLE] soundNotifications started OK")
        except Exception as _sne:
            self.soundNotifications = None
            LOG_NOTE("[BATTLE] soundNotifications init failed: %s" % _sne)
        self.hitTesters = set()
        self.initialVehicleSpeeds = {}
        self._ownVehicleMProv = Math.WGAdaptiveMatrixProvider()
        self._arena = None
        self._minimap = None
        self._terrainEffects = None
        self._projectileMover = None

        self.__guiConfig = {
            'silhouetteColors': {
                'self':   (0, 0, 0, 0),
                'enemy':  (255, 0, 0, 255),
                'friend': (0, 255, 0, 255),
            }
        }
        self.turretMatrix = Math.WGAdaptiveMatrixProvider()
        self.gunMatrix = Math.WGAdaptiveMatrixProvider()
        self._moveForward = False
        self._moveBack = False
        self._turnLeft = False
        self._turnRight = False
        self._ammo = []
        self._currShellIdx = 0
        self._nextShellIdx = 0
        self.__ammo = {}
        self.__currShellsIdx = None
        self.__nextShellsIdx = None
        self.__equipment = {}
        self.__equipmentFlags = {}
        self.__deviceStates = {}
        self.__nextCSlotIdx = 0
        self._reloadEndTime = 0.0
        self.currentMove = 0.0
        self.currentTurn = 0.0
        self.onSpaceLoaded = lambda: None
        self.onVehicleEnterWorld = Event()
        self.onVehicleLeaveWorld = Event()
        self.onGunShotChanged = Event()
        self._spaceInitialized = False
        self.isPlayer = False
        self.cell = _DummyCell()
        self.base = _DummyCell()
        self._enableOwnVehicleAutorotation = False
        self._cruise_factor = 1.0
        self._cruise_active = False
        self._cruise_target = 0.0
        self._cruise_mode = _CRUISE_MODE_DEFAULT
        self._lockedOnVehID = 0
        self.__autoAimVehID = 0
        self.__autoAimVehID_client = 0
        self._aimingInfo = [0.0, 0.0, 1.0, 1.0]
        self.arenaTypeID = 0
        self.arenaGuiType = getattr(constants, 'ARENA_GUI_TYPE').RANDOM
        self.weatherPresetID = 0
        try:
            from AvatarPositionControl import AvatarPositionControl
            self.positionControl = AvatarPositionControl(self)
        except Exception:
            class _DummyPositionControl:
                def bindToVehicle(self, *a, **k): pass
                def followCamera(self, *a, **k): pass
                def moveTo(self, *a, **k): pass
                def destroy(self): pass
            self.positionControl = _DummyPositionControl()
        LOG_NOTE("[BATTLE][AVATAR] SimpleAvatar created: name=%s team=%d" % (name, team))

    def _resetBattleConsumables(self):
        self._ammo = []
        self._currShellIdx = 0
        self._nextShellIdx = 0
        self.__ammo = {}
        self.__currShellsIdx = None
        self.__nextShellsIdx = None
        self.__equipment = {}
        self.__equipmentFlags = {}
        self.__deviceStates = {}
        self.__nextCSlotIdx = 0

    def initSpace(self):
        _patch_offline_media_fixes()
        if self._spaceInitialized:
            return
        self._spaceInitialized = True
        try:
            from helpers import DecalMap
            if DecalMap.g_instance is not None:
                DecalMap.g_instance.initGroups(1.0)
                LOG_NOTE("[BATTLE] DecalMap groups initialized OK")
        except Exception as _e:
            LOG_NOTE("[BATTLE] DecalMap.initGroups failed: %s" % _e)
    def vehicle_onEnterWorld(self, vehicle): pass
    def vehicle_onLeaveWorld(self, vehicle): pass
    def bindToVehicle(self, doBind, vehicleID=None):
        if vehicleID is None: vehicleID = self.playerVehicleID
        if doBind and vehicleID is not None:
            veh = BigWorld.entity(vehicleID)
            if veh:
                self._ownVehicleMProv.target = _vehicle_matrix_provider(veh) or veh.matrix
        else:
            self._ownVehicleMProv.target = None
    def enableOwnVehicleAutorotation(self, enable): pass

    def moveTo(self, position): self._strategicTargetPos = position

    def getVehicleAttached(self): return BigWorld.entity(self.playerVehicleID) if self.playerVehicleID else None
    def getOwnVehicleMatrix(self): return self._ownVehicleMProv
    def getOwnVehiclePosition(self):
        veh = BigWorld.entity(self.playerVehicleID) if self.playerVehicleID else None
        om = getattr(veh, '_offline_matrix', None) if veh is not None else None
        if om is None:
            om = getattr(self, '_offline_matrix', None)
        if om is not None:
            try:
                return Math.Vector3(om.translation)
            except Exception:
                pass
        target = self._ownVehicleMProv.target
        if target is not None:
            return Math.Matrix(target).translation
        if veh is not None:
            return veh.position
        return Math.Vector3(0, 0, 0)

    def getOwnVehicleSpeeds(self):
        try:
            _ob = getattr(self, '_offline_battle', None)
            if _ob is not None:
                return (float(getattr(_ob, '_cur_speed', 0.0) or 0.0),
                        float(getattr(_ob, '_cur_rot', 0.0) or 0.0))
        except Exception:
            pass
        veh = self.getVehicleAttached()
        if veh is not None and hasattr(veh, 'filter') and hasattr(veh.filter, 'speedInfo'):
            try:
                si = veh.filter.speedInfo.value
                return (si[0], si[1])
            except: pass
        return (self.currentMove * 15.0, self.currentTurn * 0.5)

    def getOwnVehicleShotDispersionAngle(self, turretRotationSpeed, isShot=False):
        descr = self.vehicleTypeDescriptor
        if descr is None: return 0.01

        aimingStartTime, aimingStartFactor, multFactor, aimingTime = self._aimingInfo
        baseAimingTime = descr.gun['aimingTime'] * descr.miscAttrs.get('gunAimingTimeFactor', 1.0)
        crewLevel = 100 + descr.miscAttrs.get('crewLevelIncrease', 0)
        crewFactor = 0.5 + 0.005 * crewLevel
        aimingTime = baseAimingTime / crewFactor
        try:
            _dmgBattle = getattr(self, '_offline_battle', None)
            if _dmgBattle is not None:
                aimingTime *= _dmgBattle._aim_time_factor(self.playerVehicleID)
        except Exception:
            pass
        self._aimingInfo[3] = aimingTime

        vehicleSpeed, vehicleRSpeed = self.getOwnVehicleSpeeds()
        speedFactor, rspeedFactor = descr.chassis['shotDispersionFactors']
        vehicleMovementFactor = (vehicleSpeed * speedFactor) ** 2
        vehicleRotationFactor = (vehicleRSpeed * rspeedFactor) ** 2
        turretRotationFactor = (turretRotationSpeed * descr.gun['shotDispersionFactors']['turretRotation']) ** 2
        shotFactor = (0.0 if not isShot else descr.gun['shotDispersionFactors']['afterShot']) ** 2

        idealFactor = vehicleMovementFactor + vehicleRotationFactor + turretRotationFactor + shotFactor
        idealFactor *= descr.miscAttrs.get('additiveShotDispersionFactor', 1.0) ** 2
        idealFactor = multFactor * _math.sqrt(1 + idealFactor)
        currTime = BigWorld.time()

        if aimingTime > 0.0:
            aimingFactor = aimingStartFactor * _math.exp((aimingStartTime - currTime) / aimingTime)
        else:
            aimingFactor = idealFactor

        if aimingFactor < idealFactor:
            aimingFactor = idealFactor
            self._aimingInfo[0] = currTime
            self._aimingInfo[1] = aimingFactor

        dispersionPenalty = 1.0
        try:
            _dmgBattle = getattr(self, '_offline_battle', None)
            if _dmgBattle is not None:
                dispersionPenalty = _dmgBattle._dispersion_factor(self.playerVehicleID)
        except Exception:
            pass
        return descr.gun['shotDispersionAngle'] * aimingFactor * dispersionPenalty

    def handleKey(self, isDown, key, mods):
        if not getattr(self, 'inputHandler', None): return False
        try: return self.inputHandler.handleKeyEvent(key, isDown)
        except: return False

    def handleKeyEvent(self, event):
        if not getattr(self, 'inputHandler', None): return False
        isDown, key, mods, isRepeat = game.convertKeyEvent(event)
        try:
            if _offline_try_chat_shortcuts(self, isDown, key, mods):
                return True
        except Exception:
            pass

        veh = self.getVehicleAttached()
        if veh and hasattr(veh, 'filter') and veh.filter:
            if key == Keys.KEY_W:   self._moveForward = isDown
            elif key == Keys.KEY_S: self._moveBack    = isDown
            elif key == Keys.KEY_A: self._turnLeft    = isDown
            elif key == Keys.KEY_D: self._turnRight   = isDown

            move = (1.0 if self._moveForward else 0.0) - (1.0 if self._moveBack else 0.0)
            turn = (1.0 if self._turnRight else 0.0) - (1.0 if self._turnLeft else 0.0)

            self.currentMove = move
            self.currentTurn = turn

        if isDown and Keys.KEY_1 <= key <= Keys.KEY_8:
            try:
                idx = key - Keys.KEY_1
                self.onAmmoButtonPressed(idx)
            except Exception as _ke: pass

        try: return self.inputHandler.handleKeyEvent(event)
        except: return False

    def handleMouseEvent(self, dx, dy, dz):
        if getattr(self, 'inputHandler', None):
            try:
                if self.inputHandler.handleMouseEvent(dx, dy, dz): return True
            except: pass
        if self.gunRotator:
            self.gunRotator.updateRotation(dx * 0.005, dy * 0.005)
            return True
        return False

    def prerequisites(self): return []
    def onRecreateDevice(self): pass

    def leaveArena(self):
        try:
            _ob = getattr(self, '_offline_battle', None)
            if _ob is None:
                return
            if getattr(_ob, '_is_finished', False) or getattr(_ob, '_finish_called', False):
                return
            if not getattr(_ob, '_results_shown', False):
                _ob._showEndBattleStats(early_exit=True)
            else:
                _ob._finishBattle()
        except Exception as e:
            LOG_ERROR("[BATTLE][AVATAR] leaveArena() error: %s" % e)
            try:
                _ob2 = getattr(self, '_offline_battle', None)
                if _ob2 is not None and not getattr(_ob2, '_finish_called', False):
                    _ob2._finishBattle()
            except Exception:
                pass

    def setForcedGuiControlMode(self, enable, stopVehicle=True):
        from gui.Cursor import forceShowCursor
        forceShowCursor(enable)
        if self.inputHandler:
            try: self.inputHandler.detachCursor(enable)
            except Exception as e: pass
        mm = getattr(self, '_minimap', None)
        if mm:
            try: mm.setBattleMode(not enable)
            except Exception as e: pass

    def targetFocus(self, entity):
        try:
            _ob = getattr(self, '_offline_battle', None)
            if _ob is not None and not getattr(_ob, '_is_finished', False):
                _eid = getattr(entity, 'id', None)
                _is_friend = False
                try:
                    _eteam = entity.publicInfo['team']
                    _is_friend = (_eteam == self.team)
                except Exception: pass
                if not _is_friend and _eid != getattr(self, 'playerVehicleID', None) and _vehicle_is_alive(entity):
                    if _eid in getattr(_ob, '_spot_hidden_models', set()) or _eid not in getattr(_ob, '_spotted_vehicles', set()):
                        return
        except Exception: pass
        aim = getattr(self.inputHandler, 'aim', None) if self.inputHandler else None
        if aim:
            try: aim.setTarget(entity)
            except Exception as e: pass
        try:
            if _vehicle_is_alive(entity) and getattr(entity, 'id', None) != self.playerVehicleID:
                color_idx = 2 if entity.publicInfo['team'] == self.team else 1
                BigWorld.wgAddEdgeDetectEntity(entity, color_idx)
        except Exception as e: pass

    def targetBlur(self, prevEntity):
        aim = getattr(self.inputHandler, 'aim', None) if self.inputHandler else None
        if aim:
            try: aim.clearTarget()
            except Exception as e: pass
        try: BigWorld.wgDelEdgeDetectEntity(prevEntity)
        except Exception as e: pass

    def addModel(self, model): BigWorld.addModel(model)
    def delModel(self, model): BigWorld.delModel(model)
    def newFakeModel(self):
        try:
            from items import vehicles as _veh
            projModelName = _veh.g_cache.shotEffects[0]['projectile'][0]
            if projModelName: return BigWorld.Model(projModelName)
        except Exception: pass
        try:
            descr = self.vehicleTypeDescriptor
            idx = descr.shot['shell']['effectsIndex']
            from items import vehicles as _veh
            projModelName = _veh.g_cache.shotEffects[idx]['projectile'][0]
            return BigWorld.Model(projModelName)
        except Exception: pass
        try:
            import Settings
            modelName = Settings.g_instance.scriptConfig.readString(Settings.KEY_FAKE_MODEL)
            if modelName: return BigWorld.Model(modelName)
        except Exception: pass
        return None

    def handleVehicleCollidedVehicle(self, vehA, vehB, hitPt, time):

        try:
            cd = getattr(self, '_veh_col_times', None)
            if cd is None:
                cd = {}
                self._veh_col_times = cd
            key = (id(vehA), id(vehB))
            key2 = (id(vehB), id(vehA))
            last = cd.get(key, cd.get(key2, 0.0))
            if time - last < 0.2:
                return
            cd[key] = time
            _offline_play_collision_fx(vehA, hitPt)
        except Exception:
            pass

    def leaveChat(self, channelId=None, *a, **k):
        return

    def enterChat(self, channelId=None, password=None, *a, **k):
        return

    def requestChatChannelMembers(self, channelId=None, *a, **k):
        return

    def requestSystemChatChannels(self, *a, **k):
        return

    def broadcast(self, cid, message):

        try:
            if not message:
                return
            _offline_post_chat_message(cid, -1, self.name, message)
        except Exception as e:
            LOG_ERROR("[BATTLE][CHAT] player broadcast failed: %s" % e)


    def shoot(self):
        veh = self.getVehicleAttached()
        if not veh: return
        _ah = getattr(self, 'inputHandler', None)
        if _ah is not None and not getattr(_ah, '_AvatarInputHandler__isArenaStarted', True): return
        now = BigWorld.time()
        if now < getattr(self, '_reloadEndTime', 0.0): return
        _dmgBattle = getattr(self, '_offline_battle', None)
        if _dmgBattle is not None:
            try:
                if not _dmgBattle._can_fire(veh.id): return
            except Exception: pass
        try:
            _idx = getattr(self, '_currShellIdx', 0)
            if getattr(self, '_ammo', None) and _idx < len(self._ammo) and self._ammo[_idx][1] <= 0: return
        except Exception as _ae: pass
        if hasattr(veh, 'showShooting'):
            try: veh.showShooting(True)
            except Exception as e: pass
        try:
            import Math, random
            descr = self.vehicleTypeDescriptor
            if descr is None: return
            try:
                _idx = getattr(self, '_currShellIdx', 0)
                _shots = descr.gun['shots']
                if _shots and 0 <= _idx < len(_shots):
                    descr.activeGunShotIndex = _idx
                    if veh and hasattr(veh, 'typeDescriptor'):
                        veh.typeDescriptor.activeGunShotIndex = _idx
                    shotDescr = _shots[_idx]
                else:
                    shotDescr = descr.shot
            except Exception:
                shotDescr = descr.shot
            _base_speed = shotDescr['speed']
            _base_grav = shotDescr['gravity']
            try:
                _isSPGveh = 'SPG' in descr.type.tags
            except Exception:
                _isSPGveh = False
            if _isSPGveh:
                _ARTY_SLOW = 0.6
                speed = _base_speed * _ARTY_SLOW
                gravity = _base_grav * _ARTY_SLOW * _ARTY_SLOW
            else:
                speed = _base_speed
                gravity = _base_grav
            _shot_speed = speed

            if hasattr(self, '_offline_matrix'):
                vehMat = Math.Matrix(self._offline_matrix)
            else:
                vehMat = Math.Matrix(veh.matrix)

            veh_entity = self.getVehicleAttached()
            gunWorldPos = None
            gunWorldDir = None
            try:
                gunModel = veh_entity.appearance.modelsDesc['gun']['model']
                try:
                    hpFireMat = Math.Matrix(gunModel.node('HP_gunFire'))
                    hp_pos = hpFireMat.translation
                    if hp_pos.lengthSquared != 0.0:
                        gunWorldPos = hp_pos
                except Exception: pass
                if gunWorldPos is None:
                    gunMat = Math.Matrix(gunModel.matrix)
                    gunBase = gunMat.translation
                    gunForward = gunMat.applyVector(Math.Vector3(0, 0, 1))
                    gunForward.normalise()
                    gunLength = descr.gun.get('length', 2.0)
                    gunWorldPos = gunBase + gunForward * gunLength
                gunMat = Math.Matrix(gunModel.matrix)
                gunForward = gunMat.applyVector(Math.Vector3(0, 0, 1))
                gunForward.normalise()
                gunWorldDir = gunForward
            except Exception as e: pass

            if gunWorldPos is None or gunWorldDir is None:
                if hasattr(self, '_offline_matrix'): vehMat = Math.Matrix(self._offline_matrix)
                else: vehMat = Math.Matrix(veh.matrix)
                turretMat = Math.Matrix(self.turretMatrix)
                gunMat2   = Math.Matrix(self.gunMatrix)

                worldDir = Math.Matrix()
                worldDir.setIdentity()
                worldDir.postMultiply(gunMat2)
                worldDir.postMultiply(turretMat)
                worldDir.postMultiply(vehMat)
                gunWorldDir = worldDir.applyVector(Math.Vector3(0, 0, 1))
                descr_turret = descr.turret
                gunOffsetLocal = Math.Vector3(descr_turret['gunPosition'])
                turretWorld = Math.Matrix()
                turretWorld.setIdentity()
                turretWorld.postMultiply(turretMat)
                turretWorld.postMultiply(vehMat)
                gunWorldPos = turretWorld.applyPoint(gunOffsetLocal)

            is_arty_shot = False
            try:
                aih = self.inputHandler
                strat_ctrl = aih._AvatarInputHandler__ctrls.get('strategic')
                if strat_ctrl and getattr(strat_ctrl, '_StrategicControlMode__isEnabled', False):
                    target = strat_ctrl.getDesiredShotPoint()
                    if target is not None:
                        _maxElev = None
                        try:
                            _pitchLimits = descr.gun['pitchLimits']
                            _maxElev = abs(_math.radians(_pitchLimits[0]))
                        except Exception: pass
                        _artyRes = _solve_arty_shot(gunWorldPos, target, speed, gravity, _maxElev)
                        if _artyRes is not None:
                            gunWorldDir, _shot_speed = _artyRes
                        else:
                            diff = target - gunWorldPos
                            diff.normalise()
                            gunWorldDir = diff
                            _shot_speed = speed
                        is_arty_shot = True
            except Exception as e: pass
            if _isSPGveh:
                is_arty_shot = True

            _battle = getattr(self, '_offline_battle', None)
            if _battle is not None:
                for _bID, _, _bIsPl in _battle.vehicles:
                    if _bID == self.playerVehicleID or _bIsPl: continue
                    _bot = BigWorld.entity(_bID)
                    if not _bot: continue

                    _bot_team = _battle.arena.vehicles.get(_bID, {}).get('team', 0)
                    if _bot_team == self.team: continue

                    _bot_mat = getattr(_bot, '_offline_matrix', None)
                    _bot_pos = _bot_mat.translation if _bot_mat is not None else _bot.position

                    _dist = (_bot_pos - gunWorldPos).length
                    if _dist < 250.0:
                        if not hasattr(_battle, '_bot_state'): _battle._bot_state = {}
                        _st = _battle._bot_state.setdefault(
                            _bID, {'target': None, 'last_shot': 0.0, 'last_hit_dir': None, 'retreat_until': 0.0})
                        if not _st.get('target'):
                            _st['last_attacker'] = self.playerVehicleID
                            _st['last_provoke_time'] = BigWorld.time()

            _burstTmp = (1, 0.0)
            try:
                _burstTmp = descr.gun['burst']
            except Exception:
                _burstTmp = (1, 0.0)
            _burstCount = 1
            if isinstance(_burstTmp, (tuple, list)) and len(_burstTmp) >= 1:
                try:
                    _burstCount = max(1, int(_burstTmp[0]))
                except Exception:
                    _burstCount = 1
            _burstSpread = 0.0 if _burstCount <= 1 else _math.radians(0.8)
            _burstBaseDir = Math.Vector3(gunWorldDir)
            _mgJitter = 0.15 if _burstCount > 1 else 0.0
            _burstInterval = 0.2 if _burstCount > 1 else 0.0

            def _fire_burst_shell(_bi):
                gunWorldDir = Math.Vector3(_burstBaseDir)
                if _burstSpread > 0.0:
                    gunWorldDir.x += _random.uniform(-_burstSpread, _burstSpread)
                    gunWorldDir.y += _random.uniform(-_burstSpread, _burstSpread)
                    gunWorldDir.normalise()
                shotPos = gunWorldPos
                refVelocity = gunWorldDir * _shot_speed
                _tracerVelocity = Math.Vector3(refVelocity)
                shotID      = random.randint(1, 999999)

                pm = self.projectileMover
                try:
                    shotEffectsIndex = descr.shot['shell']['effectsIndex']
                    effectsDescr = vehicles.g_cache.shotEffects[shotEffectsIndex]
                except Exception as e:
                    try: effectsDescr = vehicles.g_cache.shotEffects[0]
                    except: effectsDescr = None
                if effectsDescr is None: return
                try: tracerCamPos = BigWorld.camera().position
                except Exception: tracerCamPos = Math.Vector3(0, 0, 0)

                pm.add(shotID, effectsDescr, gravity, shotPos, _tracerVelocity, shotPos, True, tracerCamPos)

                try:
                    maxDist = shotDescr.get('maxDistance', 700.0)

                    exceptIDs = set()
                    if self.playerVehicleID is not None: exceptIDs.add(self.playerVehicleID)
                    for _vid in getattr(self, '_deadVehicleIDs', set()): exceptIDs.add(_vid)
                    try:
                        for _vid in self.arena.vehicles.iterkeys():
                            _ve = BigWorld.entity(_vid)
                            if _ve is not None and not _vehicle_is_alive(_ve):
                                exceptIDs.add(_vid)
                    except Exception: pass

                    hitComponent = None
                    hitNormal = 1.0
                    hitMat = 0.0
                    dynRes = None
                    if is_arty_shot:
                        impactPoint, hitVehicle, _arcCos, _arcThick, impactDir, hitComponent = _trace_ballistic_hit(
                            self.spaceID, shotPos, refVelocity, gravity, maxDist, exceptIDs)
                        effectMat = 'armor' if hitVehicle is not None else 'ground'
                        if hitVehicle is not None:
                            hitNormal, hitMat = _arcCos, _arcThick
                            dynRes = (hitVehicle, 0.0, _arcCos, _arcThick, hitComponent)
                        else:
                            dynRes = None

                    else:
                        farPoint = shotPos + gunWorldDir * maxDist
                        staticPoint, staticDist, _pReached = _shot_static_hit(
                            self.spaceID, shotPos, farPoint, gunWorldDir)
                        if _pReached:
                            staticDist = 99999.0
                            dynCheckEnd = farPoint
                        else:
                            dynCheckEnd = shotPos + gunWorldDir * staticDist
                        dynRes = _collide_dynamic_component(shotPos, dynCheckEnd, exceptIDs)

                        hitVehicle   = None
                        impactPoint  = staticPoint
                        impactDir    = Math.Vector3(gunWorldDir)
                        effectMat    = 'ground'

                        hitComponent = None
                        if dynRes is not None:
                            _hitCandidate, hitDist, hitNormal, hitMat, hitComponent = dynRes
                            if hitDist < staticDist + 3.5:
                                hitVehicle  = _hitCandidate
                                dynHitPoint = shotPos + gunWorldDir * hitDist
                                impactPoint = dynHitPoint
                                effectMat   = 'armor'

                    flightTime = _ballistic_flight_time(shotPos, _tracerVelocity, gravity, impactPoint)
                    try: flightTime = min(max(flightTime, 0.25), 20.0)
                    except Exception: flightTime = 1.0

                    _shotID, _pm, _effDescr, _impactPt, _impactDir = shotID, pm, effectsDescr, impactPoint, impactDir
                    _effMat, _hitVeh, _shotShell, _shotPosOrig = effectMat, hitVehicle, shotDescr, shotPos
                    _hitArmorThickness = hitMat if dynRes is not None else 0.0
                    _hitAngleCos       = hitNormal if dynRes is not None else 1.0
                    _hitComponent = hitComponent if dynRes is not None and hitVehicle is not None else None
                    _flightT, _trVel, _gravNum = flightTime, _tracerVelocity, gravity

                    def _do_explode(sid=_shotID, p=_pm, ed=_effDescr, pt=_impactPt, d=_impactDir, mat=_effMat, veh=_hitVeh,
                                    armorThickness=_hitArmorThickness, hitAngleCos=_hitAngleCos, shellData=_shotShell,
                                    origShotPos=_shotPosOrig, hitComponent=_hitComponent,
                                    arrT=_flightT, trVel=_trVel, gravNum=_gravNum, jit=_mgJitter,
                                    isArty=is_arty_shot):
                        try:
                            if veh is not None and _vehicle_is_alive(veh):
                                _cpt = _clamp_impact_to_veh(veh, _ballistic_arrival_pos(origShotPos, trVel, gravNum, arrT))
                                if _cpt is not None:
                                    pt = _cpt
                        except Exception: pass
                        try: p.hide(sid, pt)
                        except Exception: pass
                        try: p.explode(sid, ed, mat, pt, d)
                        except Exception: pass
                        if veh is None:
                            try: _try_destroy_destructible_at(BigWorld.player().spaceID, pt, d)
                            except Exception: pass
                        if veh is not None and getattr(veh, 'id', None) == self.playerVehicleID:
                            self._shots_received = getattr(self, '_shots_received', 0) + 1

                        try:
                            _shellInfoForSplash = shellData.get('shell', {}) if shellData else {}
                        except Exception:
                            _shellInfoForSplash = {}

                        _kind = _shellInfoForSplash.get('kind')
                        _is_he = _is_he_shell(_kind, isArty)
                        _xyz = _pt_xyz(pt)
                        if _xyz is None:
                            try:
                                _xyz = _pt_xyz(_ballistic_arrival_pos(origShotPos, trVel, gravNum, arrT))
                            except Exception:
                                _xyz = None
                        if _xyz is not None:
                            pt = Math.Vector3(_xyz[0], _xyz[1], _xyz[2])
                        if _is_he:
                            radius = _shell_he_radius(_shellInfoForSplash)
                            _fullDmg = _shell_he_damage(_shellInfoForSplash)
                            _nSplash = 0
                            _px, _py, _pz = (_xyz if _xyz is not None else (0.0, 0.0, 0.0))
                            try:
                                _ids = set()
                                try:
                                    _ids.update(self.arena.vehicles.iterkeys())
                                except Exception:
                                    pass
                                try:
                                    _ob = getattr(self, '_offline_battle', None)
                                    if _ob is not None:
                                        for _row in getattr(_ob, 'vehicles', []):
                                            _ids.add(_row[0])
                                except Exception:
                                    pass
                                if veh is not None:
                                    try:
                                        _ids.add(veh.id)
                                    except Exception:
                                        pass
                                for _vid in list(_ids):
                                    _ve = BigWorld.entity(_vid)
                                    if _ve is None or not _vehicle_is_alive(_ve):
                                        continue
                                    _direct = (veh is not None and getattr(_ve, 'id', None) == getattr(veh, 'id', None))
                                    _vp = _entity_world_pos(_ve)
                                    _dist = _math.sqrt((_vp.x - _px) ** 2 + (_vp.z - _pz) ** 2)
                                    try:
                                        _hw, _hlf, _hlb = _tank_hull_dims(_ve.typeDescriptor)
                                        _body = max(1.5, float(_hw), 0.5 * (float(_hlf) + float(_hlb)))
                                        _dist = max(0.0, _dist - _body)
                                    except Exception:
                                        pass
                                    if (not _direct) and _dist > radius:
                                        continue
                                    if _direct:
                                        _falloff = 1.0
                                    else:
                                        _falloff = max(0.1, 1.0 - _dist / max(radius, 0.01))
                                    _splashDmg = int(round(_fullDmg * _falloff))
                                    if _splashDmg <= 0:
                                        continue
                                    self._apply_vehicle_damage(_ve, _splashDmg, origShotPos,
                                                                shell_type=ShellType.HE, penetrated=False)
                                    _nSplash += 1
                                    try:
                                        from gui.WindowsManager import g_windowsManager
                                        bw = getattr(g_windowsManager, 'battleWindow', None)
                                        if bw and hasattr(bw, 'vMarkersManager') and getattr(_ve, 'marker', -1) != -1 and _vehicle_is_alive(_ve):
                                            bw.vMarkersManager.updateMarkerState(_ve.marker, 'hit_pierced', False)
                                    except Exception:
                                        pass
                            except Exception:
                                LOG_CURRENT_EXCEPTION()
                            try:
                                LOG_NOTE('[BATTLE][HE] splash r=%.2f dmg=%s pt=(%.1f,%.1f,%.1f) hits=%d kind=%s arty=%s' % (
                                    radius, _fullDmg, _px, _py, _pz, _nSplash, _kind, bool(isArty)))
                            except Exception:
                                pass
                            if veh is not None and _vehicle_is_alive(veh):
                                try:
                                    self.playShotResultNotification('pierced', veh)
                                except Exception:
                                    pass
                                _add_hit_decal(veh, hitComponent, pt, d, True, False, jitter=jit)
                            return
                        else:
                            try:
                                LOG_NOTE('[BATTLE][HE] skip splash kind=%s arty=%s' % (_kind, bool(isArty)))
                            except Exception:
                                pass

                        if veh is not None and _vehicle_is_alive(veh):
                            try:
                                self._shots_hit = getattr(self, '_shots_hit', 0) + 1
                            except Exception: pass
                            try:
                                isPenetration = True
                                isRicochet    = False
                                try:
                                    shellInfo = shellData.get('shell', {}) if shellData else {}
                                    thickness = float(armorThickness)
                                    cosAngle = max(abs(max(-1.0, min(1.0, float(hitAngleCos)))), 0.02)
                                    ricochetAngleCos = shellInfo.get('ricochetAngleCos')
                                    if ricochetAngleCos is not None and cosAngle < ricochetAngleCos:
                                        isRicochet = True; isPenetration = False
                                    else:
                                        normAngle = shellInfo.get('normalizationAngle', 0.0)
                                        effAngleRad = max(0.0, _math.acos(cosAngle) - normAngle)
                                        effCos = max(_math.cos(effAngleRad), 0.02)
                                        effectiveArmor = thickness / effCos
                                        pierce = shellData.get('piercingPower', (50, 50)) if shellData else (50, 50)
                                        pierce_val = pierce.x if hasattr(pierce, 'x') else (pierce[0] if isinstance(pierce, (list, tuple)) else float(pierce))
                                        actualPiercing = pierce_val * _random.uniform(0.875, 1.125)
                                        isPenetration = (thickness <= 0) or (actualPiercing >= effectiveArmor)
                                except Exception:
                                    isPenetration = True; isRicochet = False

                                if not isPenetration:
                                    try: p.explode(sid, ed, 'armor', pt, d)
                                    except Exception: pass
                                    try:
                                        from gui.WindowsManager import g_windowsManager
                                        bw = getattr(g_windowsManager, 'battleWindow', None)
                                        if bw and hasattr(bw, 'vMarkersManager') and getattr(veh, 'marker', -1) != -1:
                                            bw.vMarkersManager.updateMarkerState(veh.marker, 'hit', False)
                                    except Exception: pass
                                    try:
                                        self.playShotResultNotification('ricochet' if isRicochet else 'not_pierced', veh)
                                    except Exception: pass
                                    _add_hit_decal(veh, hitComponent, pt, d, False, isRicochet, jitter=jit)
                                    return

                                try:
                                    shellInfo = shellData.get('shell', {}) if shellData else {}
                                except Exception: shellInfo = {}
                                try:
                                    dmg = shellInfo.get('damage', (50, 50))
                                except Exception: dmg = (50, 50)
                                dmg_val = dmg[0] if isinstance(dmg, (list, tuple)) else int(dmg)
                                _hitShellType = _SHELL_KIND_TO_TYPE.get(shellInfo.get('kind'), ShellType.AP)
                                _add_hit_decal(veh, hitComponent, pt, d, True, False, jitter=jit)
                                self._apply_vehicle_damage(veh, dmg_val, origShotPos,
                                                            shell_type=_hitShellType, penetrated=True,
                                                            hit_component=hitComponent)
                            except Exception: pass

                    BigWorld.callback(flightTime, _do_explode)
                except Exception as _e:
                    LOG_NOTE("[BATTLE][DMG] direct-fire hit resolution failed: %s" % _e)

            for _bi in range(_burstCount):
                BigWorld.callback(_bi * _burstInterval, lambda b=_bi: _fire_burst_shell(b))

            try:
                reload_time = self.vehicleTypeDescriptor.gun['reloadTime'] * self.vehicleTypeDescriptor.miscAttrs.get('gunReloadTimeFactor', 1.0)
                crewLevel = 100 + self.vehicleTypeDescriptor.miscAttrs.get('crewLevelIncrease', 0)
                reload_time /= (0.5 + 0.005 * crewLevel)
                try:
                    _dmgBattle = getattr(self, '_offline_battle', None)
                    if _dmgBattle is not None:
                        reload_time *= _dmgBattle._reload_multiplier(veh.id)
                except Exception: pass
                self._reloadEndTime = BigWorld.time() + reload_time
                try:
                    _my_reload_token = self._reloadEndTime
                    def _on_reload_done(_tok=_my_reload_token):
                        if getattr(self, '_reloadEndTime', 0.0) == _tok and getattr(self, 'isVehicleAlive', True):
                            self._playCrewVoice('gun_reloaded')
                            try:
                                self.updateVehicleGunReloadTime(0.0)
                            except Exception:
                                pass
                    BigWorld.callback(reload_time, _on_reload_done)
                except Exception: pass
                try:
                    self.updateVehicleGunReloadTime(reload_time)
                except Exception:
                    try: self.inputHandler.setReloading(reload_time)
                    except Exception:
                        try: self.inputHandler.setReloading(reload_time, 0)
                        except Exception: pass
            except Exception: pass

            self._shots_fired = getattr(self, '_shots_fired', 0) + 1

            try:
                idx = self.__currShellsIdx
                if idx is None:
                    idx = getattr(self, '_currShellIdx', 0)
                if idx in self.__ammo:
                    compactDescr, quantity = self.__ammo[idx]
                    if quantity > 0:
                        quantity -= 1
                    self.updateVehicleAmmo(compactDescr, quantity, 0)
                elif self._ammo and idx < len(self._ammo):
                    if self._ammo[idx][1] > 0: self._ammo[idx][1] -= 1
                    new_count = self._ammo[idx][1]
                    try:
                        aim = self.inputHandler.aim
                        if aim: aim.setAmmoStock(new_count)
                    except Exception: pass
            except Exception: pass

            try:
                currIdx = self.__currShellsIdx
                if currIdx is None:
                    currIdx = getattr(self, '_currShellIdx', 0)
                nextIdx = self.__nextShellsIdx
                if nextIdx is None:
                    nextIdx = getattr(self, '_nextShellIdx', currIdx)
                if nextIdx != currIdx and nextIdx in self.__ammo and self.__ammo[nextIdx][1] > 0:
                    self.updateVehicleSetting(constants.VEHICLE_SETTING.CURRENT_SHELLS, self.__ammo[nextIdx][0])
            except Exception: pass

        except Exception as e: LOG_NOTE("[BATTLE] shoot() failed: %s" % e)

    def _apply_vehicle_damage(self, veh, dmg_val, origShotPos, shell_type=ShellType.AP, penetrated=True, hit_component=None, killer_id=None, fire_damage=False):

        try:
            if veh is None or not _vehicle_is_alive(veh) or dmg_val <= 0:
                return 0
            old_hp = _vehicle_health(veh)
            new_hp = max(0, old_hp - dmg_val)
            prev_hp = old_hp

            _resolved_killer_id = killer_id if killer_id is not None else self.playerVehicleID

            if new_hp <= 0:
                if veh.id == self.playerVehicleID:
                    self.isVehicleAlive = False
                    self.currentMove = 0.0
                    self.currentTurn = 0.0
                    try:
                        _battle = getattr(self, '_offline_battle', None)
                        if _battle is not None:
                            _battle._cur_speed = 0.0
                            _battle._cur_rot = 0.0
                    except Exception: pass
                    try:
                        _apply_hit_impulse(veh, origShotPos, True)
                    except Exception: pass
                    if self.inputHandler:
                        try:
                            _kb = getattr(self, '_offline_battle', None)
                            _reveal = True
                            if _kb is not None and not _kb._killer_revealed(_resolved_killer_id):
                                _reveal = False
                            self.inputHandler.setKillerVehicleID(_resolved_killer_id if _reveal else None)
                        except Exception: pass
                        try: self.inputHandler.activatePostmortem()
                        except Exception:
                            try: self.inputHandler.onControlModeChanged('postmortem')
                            except Exception: pass
                    try:
                        if not getattr(self, '_offlineDeathVoicePlayed', False):
                            self._offlineDeathVoicePlayed = True
                            self._playCrewVoice('vehicle_destroyed')
                            try:
                                _dmo = getattr(self, '_offline_battle', None)
                                if _dmo is not None: _dmo._post_death_message(_resolved_killer_id)
                            except Exception: pass
                    except Exception: pass
                    try:
                        _dmgO = getattr(self, '_offline_battle', None)
                        if _dmgO is not None: _dmgO._destroy_player_modules_and_crew(veh)
                    except Exception: pass
                    try:
                        _show_dead_damage_panel()
                    except Exception: pass

            _set_veh_health(veh, new_hp)

            actual_dmg = old_hp - new_hp
            self._total_damage = getattr(self, '_total_damage', 0) + actual_dmg
            self._damagedVehicleIDs = getattr(self, '_damagedVehicleIDs', set())
            self._damagedVehicleIDs.add(veh.id)

            try:
                _dmgBattle = getattr(self, '_offline_battle', None)
                if _dmgBattle is not None and actual_dmg > 0:
                    _dmgBattle.on_penetrating_hit(veh, actual_dmg, shell_type, penetrated, hit_component=hit_component)
            except Exception: pass

            try:
                _battle = getattr(self, '_offline_battle', None)
                if _battle is not None:
                    if not hasattr(_battle, '_bot_state'): _battle._bot_state = {}
                    t_st = _battle._bot_state.setdefault(veh.id, {'target': None, 'last_shot': 0.0, 'last_hit_dir': None, 'retreat_until': 0.0})
                    try:
                        _vt = _battle.arena.vehicles.get(veh.id, {}).get('team', 0)
                        _kt = _battle.arena.vehicles.get(_resolved_killer_id, {}).get('team', 0)
                        if not (_vt and _kt and _vt == _kt):
                            t_st['last_attacker'] = _resolved_killer_id
                    except Exception:
                        t_st['last_attacker'] = _resolved_killer_id
                    _hit_at = _entity_world_pos(veh)
                    diff_to_player = origShotPos - _hit_at
                    t_st['last_hit_dir'] = _math.atan2(diff_to_player.x, diff_to_player.z)
                    t_st['last_hit_time'] = BigWorld.time()
                    t_st['_flank_side'] = -t_st.get('_flank_side', 1)
                    if hasattr(veh, '_ai_idle_turret_time'): veh._ai_idle_turret_time = 0.0
            except Exception: pass

            try:
                if getattr(veh, 'isStarted', False) and getattr(veh, 'appearance', None):
                    veh.set_health(prev_hp)
            except Exception: pass
            try:
                if veh.id == self.playerVehicleID and actual_dmg > 0:
                    _apply_hit_impulse(veh, origShotPos, True)
            except Exception: pass
            if not fire_damage:
                try:
                    from gui.WindowsManager import g_windowsManager
                    bw = getattr(g_windowsManager, 'battleWindow', None)
                    if bw and hasattr(bw, 'vMarkersManager') and getattr(veh, 'marker', -1) != -1:
                        bw.vMarkersManager.updateMarkerState(veh.marker, 'hit_pierced', False)
                except Exception: pass
                if new_hp > 0:
                    try:
                        self.playShotResultNotification('pierced', veh)
                    except Exception: pass
            try:
                from gui.WindowsManager import g_windowsManager
                bw = getattr(g_windowsManager, 'battleWindow', None)
                if bw and hasattr(bw, 'vMarkersManager') and getattr(veh, 'marker', -1) != -1:
                    bw.vMarkersManager.onVehicleHealthChanged(veh.marker, new_hp, veh.typeDescriptor.maxHealth)
            except Exception: pass

            if new_hp <= 0:
                _set_veh_health(veh, 0)
                self._deadVehicleIDs.add(veh.id)
                try:
                    if getattr(veh, 'isStarted', False) and getattr(veh, 'appearance', None):
                        veh.set_health(prev_hp)
                        try: veh.appearance.changeEngineMode((0, 0))
                        except Exception: pass
                except Exception: pass
                veh.isCrewActive = False
                try:
                    if getattr(veh, 'isStarted', False) and getattr(veh, 'appearance', None):
                        veh.set_isCrewActive(True)
                except Exception: pass

                try:
                    arena = self.arena
                    if veh.id in arena.vehicles: arena.vehicles[veh.id]['isAlive'] = False
                except Exception: pass
                try:
                    if getattr(self, '_lockedOnVehID', 0) == veh.id:
                        self.onLockedVehicleLost()
                except Exception: pass

                try:
                    _battle = getattr(self, '_offline_battle', None)
                    if _battle is not None:
                        _battle._register_kill(veh.id, _resolved_killer_id, self.team, killer_is_player=(_resolved_killer_id == self.playerVehicleID))
                        if not getattr(_battle, '_is_finished', False): _battle._check_win_condition()
                except Exception: pass
                try:
                    from gui.WindowsManager import g_windowsManager
                    bw = getattr(g_windowsManager, 'battleWindow', None)
                    if bw and hasattr(bw, 'vMarkersManager') and getattr(veh, 'marker', -1) != -1:
                        bw.vMarkersManager.updateMarkerState(veh.marker, 'dead', False)
                except Exception: pass
                try: veh.filter.allowLagProcessing = True
                except Exception: pass
            return actual_dmg
        except Exception:
            return 0

    def _playCrewVoice(self, eventName):
        try:
            if getattr(self, 'soundNotifications', None):
                try: self.soundNotifications.play(eventName)
                except Exception: pass
        except Exception: pass

    def lockOn(self, target):

        import Vehicle as VehicleModule
        import constants as _c
        vehID = 0
        if target is not None and isinstance(target, VehicleModule.Vehicle):
            try:
                if target.publicInfo['team'] != self.team and _vehicle_is_alive(target):
                    vehID = target.id
            except Exception:
                vehID = 0
        if vehID:
            try:
                _ob = getattr(self, '_offline_battle', None)
                if _ob is not None:
                    if vehID in getattr(_ob, '_spot_hidden_models', ()):
                        vehID = 0
                    else:
                        _sp = getattr(_ob, '_spotted_vehicles', None)
                        if _sp is not None and vehID not in _sp:
                            vehID = 0
                    _dead = getattr(self, '_deadVehicleIDs', None)
                    if _dead is not None and vehID in _dead:
                        vehID = 0
            except Exception:
                vehID = 0
        if getattr(self, '_lockedOnVehID', 0) == vehID:
            return
        self._lockedOnVehID = vehID
        try:
            if self.inputHandler is not None:
                self.inputHandler.setAimingMode(bool(vehID), _c.AIMING_MODE_TARGET_LOCK)
            if self.gunRotator is not None:
                self.gunRotator.clientMode = True
            if self.soundNotifications is not None:
                self.soundNotifications.play('target_captured' if vehID else 'target_lost')
        except Exception:
            pass
        if vehID:
            LOG_NOTE("[BATTLE] auto-aim lock %s" % vehID)

    def onLockedVehicleLost(self):

        lockedOnVehID = getattr(self, '_lockedOnVehID', 0)
        self._lockedOnVehID = 0
        try:
            import constants as _c
            if self.inputHandler is not None:
                self.inputHandler.setAimingMode(False, _c.AIMING_MODE_TARGET_LOCK)
            if self.gunRotator is not None:
                self.gunRotator.clientMode = True
            if lockedOnVehID and lockedOnVehID not in getattr(self, '__frags', set()) and self.soundNotifications is not None:
                self.soundNotifications.play('target_lost')
        except Exception:
            pass

    def onAmmoButtonPressed(self, idx):
        if not getattr(self, 'isOnArena', False) or not getattr(self, 'isVehicleAlive', True):
            return
        if idx in self.__equipment.keys():
            self.onEquipmentButtonPressed(idx)
            return
        if idx == self.__currShellsIdx and idx == self.__nextShellsIdx:
            return
        if idx not in self.__ammo.keys():
            return
        compactDescr, quantity = self.__ammo[idx]
        if quantity <= 0:
            return
        if idx == self.__nextShellsIdx:
            code = constants.VEHICLE_SETTING.CURRENT_SHELLS
        else:
            code = constants.VEHICLE_SETTING.NEXT_SHELLS
        self.updateVehicleSetting(code, compactDescr)

    def autoAim(self, target):
        from constants import AIMING_MODE
        from Vehicle import Vehicle as _Veh
        if target is None:
            vehID = 0
        elif not isinstance(target, _Veh):
            vehID = 0
        else:
            try:
                if target.publicInfo['team'] == self.team:
                    vehID = 0
                elif not target.isAlive():
                    vehID = 0
                else:
                    vehID = target.id
            except Exception:
                vehID = 0
        if self.__autoAimVehID != vehID:
            self.__autoAimVehID = vehID
            self._lockedOnVehID = vehID
            try:
                if vehID != 0:
                    self.inputHandler.setAimingMode(True, AIMING_MODE.TARGET_LOCK)
                    if self.gunRotator is not None:
                        self.gunRotator.clientMode = True
                        self.gunRotator.shotPointSourceFunctor = self.predictAutoAimTargetPoint
                    if self.soundNotifications is not None:
                        self.soundNotifications.play('target_captured')
                else:
                    self.inputHandler.setAimingMode(False, AIMING_MODE.TARGET_LOCK)
                    if self.gunRotator is not None:
                        self.gunRotator.clientMode = True
                        self.gunRotator.shotPointSourceFunctor = None
                    if self.soundNotifications is not None:
                        self.soundNotifications.play('target_lost')
            except Exception:
                pass
            self.autoAim_client(target if vehID else None)
        return

    def autoAim_client(self, target):
        from Vehicle import Vehicle as _Veh
        if target is None:
            vehID = 0
        elif not isinstance(target, _Veh):
            vehID = 0
        else:
            try:
                if target.publicInfo['team'] == self.team or not target.isAlive():
                    vehID = 0
                else:
                    vehID = target.id
            except Exception:
                vehID = 0
        if self.__autoAimVehID_client != vehID:
            self.__autoAimVehID_client = vehID
            if self.gunRotator is not None:
                if vehID != 0:
                    self.gunRotator.shotPointSourceFunctor = self.predictAutoAimTargetPoint
                else:
                    self.gunRotator.shotPointSourceFunctor = None
        return

    def predictAutoAimTargetPoint(self):
        if self.__autoAimVehID_client == 0:
            return
        targVeh = BigWorld.entities.get(self.__autoAimVehID_client)
        if targVeh is None:
            return
        targVehMatr = Math.Matrix(targVeh.matrix)
        try:
            shellAvgFlyTime = (targVehMatr.translation - self.getOwnVehiclePosition()).length / self.vehicleTypeDescriptor.shot['speed']
        except Exception:
            shellAvgFlyTime = 0.5
        yOffset = targVeh.typeDescriptor.chassis['hullPosition'].y + targVeh.typeDescriptor.hull['turretPositions'][0].y
        point = Math.Vector3(0.0, yOffset, 0.0)
        point = targVehMatr.applyPoint(point)
        try:
            targVelMagn = targVeh.filter.speedInfo.value[0]
        except Exception:
            targVelMagn = 0.0
        targVel = targVehMatr.applyVector(Math.Vector3(0.0, 0.0, 1.0)) * targVelMagn
        point += targVel * shellAvgFlyTime
        return point

    def onEquipmentButtonPressed(self, idx, deviceName=None):
        LOG_NOTE('[BATTLE] equipment press idx=%s device=%s slots=%s' % (idx, deviceName, dict(self.__equipment)))
        if not getattr(self, 'isOnArena', False) or not getattr(self, 'isVehicleAlive', True):
            return
        if idx not in self.__equipment.keys():
            return
        compactDescr, quantity, time = self.__equipment[idx]
        if quantity <= 0 or compactDescr == 0 or time > 0:
            return
        artefact = vehicles.getDictDescr(compactDescr)
        if not artefact.tags or artefact.tags & frozenset(('fuel',)):
            return
        consumablesPanel = g_windowsManager.battleWindow.consumablesPanel
        if artefact.tags & frozenset(('medkit', 'repairkit')):
            entitySuffix = 'Health'
            if deviceName is None:
                vTypeDescr = self.vehicleTypeDescriptor.type
                if artefact.tags & frozenset(('medkit',)):
                    tagName = 'medkit'
                    enumRoles = {'gunner': 1, 'loader': 1, 'radioman': 1}
                    tankmen = []
                    for roles in vTypeDescr.crewRoles:
                        mainRole = roles[0]
                        if mainRole in enumRoles.keys():
                            tankmen.append(mainRole + str(enumRoles[mainRole]))
                            enumRoles[mainRole] += 1
                        else:
                            tankmen.append(mainRole)
                    entityStates = dict.fromkeys(tankmen)
                else:
                    tagName = 'repairkit'
                    entityStates = dict.fromkeys(tuple((device.name[:-len(entitySuffix)] for device in vTypeDescr.devices)))
                _ob = getattr(self, '_offline_battle', None)
                _st = None
                try:
                    _st = _ob._get_dmg_vehicle_state(self.getVehicleAttached()) if _ob is not None else None
                except Exception:
                    _st = None
                for eName in entityStates.keys():
                    state = None
                    try:
                        if _st is not None:
                            if artefact.tags & frozenset(('medkit',)):
                                for _cm in getattr(_st, 'crew', []) or []:
                                    _role = getattr(_cm, 'role', None)
                                    if _role == eName or getattr(_cm, 'name', None) == eName or (eName.startswith(_role or '---') and _role):
                                        _cs = getattr(_cm, 'state', None)
                                        if _cs in ('wounded', 'killed'):
                                            state = _cs
                                        break
                            elif eName == 'chassis':
                                _lt = _st.get_module('leftTrack')
                                _rt = _st.get_module('rightTrack')
                                _ls = getattr(_lt, 'state', None) if _lt is not None else None
                                _rs = getattr(_rt, 'state', None) if _rt is not None else None
                                if _ls in ('critical', 'destroyed'):
                                    state = _ls
                                elif _rs in ('critical', 'destroyed'):
                                    state = _rs
                            else:
                                _mod = _st.get_module(eName)
                                if _mod is not None:
                                    _ms = getattr(_mod, 'state', None)
                                    if _ms in ('critical', 'destroyed'):
                                        state = _ms
                    except Exception:
                        state = None
                    entityStates[eName] = state
                try:
                    ds = getattr(self, '_SimpleAvatar__deviceStates', None) or {}
                    for eName, _dsv in ds.iteritems():
                        if _dsv in ('critical', 'destroyed', 'wounded', 'killed'):
                            if entityStates.get(eName) is None:
                                entityStates[eName] = _dsv
                except Exception:
                    pass
                if artefact.tags & frozenset(('repairkit',)):
                    try:
                        if _st is not None:
                            for _tn in ('leftTrack', 'rightTrack'):
                                _tm = _st.get_module(_tn)
                                _ts = getattr(_tm, 'state', None) if _tm is not None else None
                                if _ts in ('critical', 'destroyed'):
                                    entityStates[_tn] = _ts
                    except Exception:
                        pass
                    _pick = None
                    for _pref in ('leftTrack', 'rightTrack', 'chassis'):
                        if entityStates.get(_pref) in ('critical', 'destroyed'):
                            _pick = 'chassis' if _pref == 'chassis' else _pref
                            break
                    if _pick is None:
                        for _en, _es in entityStates.iteritems():
                            if _es in ('critical', 'destroyed'):
                                _pick = _en
                                break
                    if _pick is not None:
                        LOG_NOTE('[BATTLE] repairkit auto device=%s states=%s' % (_pick, entityStates))
                        self.onEquipmentButtonPressed(idx, deviceName=_pick)
                        return
                elif artefact.tags & frozenset(('medkit',)):
                    _pick = None
                    for _en, _es in entityStates.iteritems():
                        if _es in ('wounded', 'killed'):
                            _pick = _en
                            break
                    if _pick is not None:
                        LOG_NOTE('[BATTLE] medkit auto tankman=%s' % _pick)
                        self.onEquipmentButtonPressed(idx, deviceName=_pick)
                        return
                consumablesPanel.expandEquipmentSlot(idx, tagName, entityStates)
                return
            quantity = max(0, quantity - 1)
            self.updateVehicleAmmo(compactDescr, quantity, 0)
            try:
                from Offline import Manager as _OM
                from CurrentVehicle import g_currentVehicle as _gcv
                _vid = int(_gcv.vehicle.inventoryId) if _gcv.vehicle else 1
                _OM._consumed_eq_by_veh.setdefault(_vid, []).append(int(compactDescr))
            except Exception:
                pass
            _ob = getattr(self, '_offline_battle', None)
            if _ob is not None:
                try:
                    if artefact.tags & frozenset(('repairkit',)):
                        _ob._offline_repair_device(deviceName)
                    else:
                        try:
                            _st = _ob._get_dmg_vehicle_state(self.getVehicleAttached())
                            if _st is not None:
                                for _cm in getattr(_st, 'crew', []) or []:
                                    if getattr(_cm, 'role', None) == deviceName or getattr(_cm, 'name', None) == deviceName:
                                        if hasattr(_cm, 'heal'):
                                            _cm.heal()
                                        else:
                                            _cm.hp = getattr(_cm, 'max_hp', 100)
                                            _cm.state = 'ok'
                            bw = _ob.battleWindow
                            if bw and hasattr(bw, 'damagePanel'):
                                bw.damagePanel.updateCriticalIcon(deviceName, 'normal')
                        except Exception:
                            pass
                except Exception:
                    pass
            return
        if artefact.tags & frozenset(('extinguisher',)):
            _ob = getattr(self, '_offline_battle', None)
            _st = None
            try:
                _st = _ob._get_dmg_vehicle_state(self.getVehicleAttached()) if _ob is not None else None
            except Exception:
                _st = None
            if _st is None or not getattr(_st, 'on_fire', False):
                return
            quantity = max(0, quantity - 1)
            self.updateVehicleAmmo(compactDescr, quantity, 0)
            try:
                from Offline import Manager as _OM
                from CurrentVehicle import g_currentVehicle as _gcv
                _vid = int(_gcv.vehicle.inventoryId) if _gcv.vehicle else 1
                _OM._consumed_eq_by_veh.setdefault(_vid, []).append(int(compactDescr))
            except Exception:
                pass
            try:
                if _ob is not None:
                    _ob._stop_fire(self.getVehicleAttached())
            except Exception:
                try:
                    _st.on_fire = False
                    bw = _ob.battleWindow if _ob is not None else None
                    if bw and hasattr(bw, 'damagePanel'):
                        bw.damagePanel.onFireInVehicle(False)
                except Exception:
                    pass
            return
        if artefact.tags & frozenset(('stimulator', 'trigger')):
            quantity = max(0, quantity - 1)
            self.__equipmentFlags[idx] = 1
            self.updateVehicleAmmo(compactDescr, quantity, 5.0)
            try:
                from Offline import Manager as _OM
                from CurrentVehicle import g_currentVehicle as _gcv
                _vid = int(_gcv.vehicle.inventoryId) if _gcv.vehicle else 1
                _OM._consumed_eq_by_veh.setdefault(_vid, []).append(int(compactDescr))
            except Exception:
                pass
            return

    def onDamageIconButtonPressed(self, tag, deviceName):
        for idx, (compactDescr, _, _) in self.__equipment.iteritems():
            if compactDescr == 0:
                continue
            eDescr = vehicles.getDictDescr(compactDescr)
            if eDescr.tags & frozenset((tag,)):
                self.onEquipmentButtonPressed(idx, deviceName=deviceName)
                return

    def getCurrentShots(self):
        try:
            idx = self.__currShellsIdx
            if idx is None:
                return (0, self.vehicleTypeDescriptor.shot)
            return (self.__ammo[idx][1], self.vehicleTypeDescriptor.shot)
        except Exception:
            try:
                idx = getattr(self, '_currShellIdx', 0)
                count = self._ammo[idx][1] if (self._ammo and idx < len(self._ammo)) else 0
                return (count, self.vehicleTypeDescriptor.shot)
            except Exception:
                return (0, None)

    def updateVehicleGunReloadTime(self, timeLeft):
        if timeLeft == 0.0:
            try:
                if self.soundNotifications is not None:
                    self.soundNotifications.play('gun_reloaded')
            except Exception:
                pass
        if timeLeft < 0.0:
            timeLeft = -1
        try:
            self.inputHandler.setReloading(timeLeft)
        except Exception:
            try:
                self.inputHandler.setReloading(timeLeft, 0)
            except Exception:
                pass
        try:
            aim = self.inputHandler.aim
            if aim is not None:
                aim.setReloading(timeLeft)
        except Exception:
            pass
        try:
            _idx = self.__currShellsIdx
            if _idx is None:
                _idx = getattr(self, '_currShellIdx', 0)
            g_windowsManager.battleWindow.consumablesPanel.setCoolDownTime(_idx, timeLeft)
        except Exception:
            pass

    def updateVehicleAmmo(self, compactDescr, quantity, timeRemaining):
        if compactDescr == 0:
            self.__processEmptyVehicleEquipment()
            return
        itemTypeIdx = getTypeOfCompactDescr(compactDescr)
        if itemTypeIdx == ITEM_TYPE_INDICES['shell']:
            self.__processVehicleAmmo(compactDescr, quantity, timeRemaining)
        elif itemTypeIdx == ITEM_TYPE_INDICES['equipment']:
            self.__processVehicleEquipments(compactDescr, quantity, timeRemaining)
        else:
            LOG_WARNING('Not supported item type index', itemTypeIdx)

    def updateVehicleSetting(self, code, value):
        try:
            consumablesPanel = g_windowsManager.battleWindow.consumablesPanel
        except Exception:
            return
        if code == constants.VEHICLE_SETTING.CURRENT_SHELLS:
            idx = self.__findIndexInAmmo(value)
            if idx is None:
                return
            if idx == self.__currShellsIdx:
                return
            consumablesPanel.setCurrentShell(idx)
            self.__currShellsIdx = idx
            self._currShellIdx = idx
            shellDescr = vehicles.getDictDescr(value)
            for shotIdx, descr in enumerate(self.vehicleTypeDescriptor.gun['shots']):
                if descr['shell']['id'] == shellDescr['id']:
                    self.vehicleTypeDescriptor.activeGunShotIndex = shotIdx
                    vehicle = BigWorld.entity(self.playerVehicleID)
                    if vehicle is not None:
                        vehicle.typeDescriptor.activeGunShotIndex = shotIdx
                    self.onGunShotChanged()
                    break
            aim = None
            try:
                aim = self.inputHandler.aim
            except Exception:
                aim = None
            if aim is not None:
                aim.setAmmoStock(self.__ammo[idx][1])
            return
        elif code == constants.VEHICLE_SETTING.NEXT_SHELLS:
            idx = self.__findIndexInAmmo(value)
            if idx is None:
                return
            if idx == self.__nextShellsIdx:
                return
            consumablesPanel.setNextShell(idx)
            self.__nextShellsIdx = idx
            self._nextShellIdx = idx
            return

    def __findIndexInAmmo(self, compactDescr):
        for idx, (value, _) in self.__ammo.iteritems():
            if compactDescr == value:
                return idx
        return None

    def __findIndexInEquipment(self, compactDescr):
        for idx, (value, _, _) in self.__equipment.iteritems():
            if compactDescr == value:
                return idx
        return None

    def __processVehicleAmmo(self, compactDescr, quantity, timeRemaining):
        consumablesPanel = g_windowsManager.battleWindow.consumablesPanel
        idx = self.__findIndexInAmmo(compactDescr)
        if idx is not None:
            self.__ammo[idx] = (compactDescr, quantity)
            if idx < len(self._ammo):
                self._ammo[idx][1] = quantity
            consumablesPanel.setItemQuantityInSlot(idx, quantity)
            if idx == self.__currShellsIdx:
                self.getOwnVehicleShotDispersionAngle(0.0, True)
                aim = None
                try:
                    aim = self.inputHandler.aim
                except Exception:
                    aim = None
                if aim is not None:
                    aim.setAmmoStock(quantity)
            return
        idx = self.__nextCSlotIdx
        self.__nextCSlotIdx += 1
        self.__ammo[idx] = (compactDescr, quantity)
        self._ammo.append([compactDescr, quantity])
        shellDescr = vehicles.getDictDescr(compactDescr)
        shotDescr = self.vehicleTypeDescriptor.gun['shots'][0]
        for _, shotDescr in enumerate(self.vehicleTypeDescriptor.gun['shots']):
            if shotDescr['shell']['id'] == shellDescr['id']:
                break
        consumablesPanel.addShellSlot(idx, quantity, shellDescr, shotDescr['piercingPower'])

    def __processVehicleEquipments(self, compactDescr, quantity, timeRemaining):
        consumablesPanel = g_windowsManager.battleWindow.consumablesPanel
        idx = self.__findIndexInEquipment(compactDescr)
        if idx is not None:
            self.__equipment[idx] = (compactDescr, quantity, timeRemaining)
            consumablesPanel.setItemQuantityInSlot(idx, quantity)
            consumablesPanel.setCoolDownTime(idx, timeRemaining)
            return
        self.__nextCSlotIdx = consumablesPanel.checkEquipmentSlotIdx(self.__nextCSlotIdx)
        idx = self.__nextCSlotIdx
        self.__nextCSlotIdx += 1
        self.__equipment[idx] = (compactDescr, quantity, timeRemaining)
        eDescr = vehicles.getDictDescr(compactDescr)
        consumablesPanel.addEquipmentSlot(idx, quantity, eDescr)
        if timeRemaining != 0:
            consumablesPanel.setCoolDownTime(idx, timeRemaining)

    def __processEmptyVehicleEquipment(self):
        consumablesPanel = g_windowsManager.battleWindow.consumablesPanel
        self.__nextCSlotIdx = consumablesPanel.checkEquipmentSlotIdx(self.__nextCSlotIdx)
        idx = self.__nextCSlotIdx
        self.__nextCSlotIdx += 1
        self.__equipment[idx] = (0, 0, 0)
        consumablesPanel.addEmptyEquipmentSlot(idx)

    def playShotResultNotification(self, result, target=None):
        try:
            if not getattr(self, 'soundNotifications', None): return
            if result == 'ricochet':
                self.soundNotifications.play('armor_ricochet_by_player')
            elif result == 'not_pierced':
                self.soundNotifications.play('armor_not_pierced_by_player')
            elif result == 'pierced':
                self.soundNotifications.play('armor_pierced_by_player')
        except Exception: pass

    def showNoPenHitmarker(self):
        try:
            import GUI
        except Exception:
            return
        try:
            comp = getattr(self, '_noPenMarkerGUI', None)
            if comp is None:
                comp = GUI.Text('----------')
                comp.horizontalAnchor = 'CENTER'
                comp.verticalAnchor = 'CENTER'
                comp.heightMode = 'PIXEL'
                comp.widthMode = 'PIXEL'
                comp.verticalPositionMode = 'PIXEL'
                comp.horizontalPositionMode = 'PIXEL'
                comp.colour = Math.Vector4(160, 160, 160, 255)
                try: comp.font = 'default_large.font'
                except Exception: pass
                comp.position = Math.Vector3(0.0, 0.0, 0.0)
                comp.visible = False
                GUI.addRoot(comp)
                self._noPenMarkerGUI = comp

            comp.visible = True
            self._noPenMarkerToken = getattr(self, '_noPenMarkerToken', 0) + 1
            _tok = self._noPenMarkerToken

            def _hideMarker(tok=_tok):
                try:
                    if tok == getattr(self, '_noPenMarkerToken', None):
                        comp.visible = False
                except Exception: pass

            BigWorld.callback(0.35, _hideMarker)
        except Exception: pass

    def destroyNoPenHitmarker(self):
        try:
            import GUI
            comp = getattr(self, '_noPenMarkerGUI', None)
            if comp is not None:
                GUI.delRoot(comp)
                self._noPenMarkerGUI = None
        except Exception: pass

    def showNoPenArc(self, gYaw):

        try:
            import GUI
        except Exception:
            return
        try:
            self.destroyNoPenArc()
        except Exception:
            pass
        try:
            cam = BigWorld.camera()
            rel = float(gYaw) - float(cam.direction.yaw)
            while rel > _math.pi:
                rel -= 2.0 * _math.pi
            while rel < -_math.pi:
                rel += 2.0 * _math.pi
        except Exception:
            rel = float(gYaw)
        comps = []
        try:
            radius = 210.0
            for i in xrange(-4, 5):
                ang = rel + (i * 0.12)
                comp = GUI.Text('=')
                comp.horizontalAnchor = 'CENTER'
                comp.verticalAnchor = 'CENTER'
                comp.heightMode = 'PIXEL'
                comp.widthMode = 'PIXEL'
                comp.verticalPositionMode = 'PIXEL'
                comp.horizontalPositionMode = 'PIXEL'
                comp.colour = Math.Vector4(8, 8, 8, 255)
                try:
                    comp.font = 'default_large.font'
                except Exception:
                    pass
                sx = _math.sin(ang) * radius
                sy = -_math.cos(ang) * radius
                comp.position = Math.Vector3(sx, sy, 0.0)
                try:
                    comp.rotation = (0.0, 0.0, -ang)
                except Exception:
                    pass
                GUI.addRoot(comp)
                comps.append(comp)
            self._noPenArcGUI = comps
            self._noPenArcToken = getattr(self, '_noPenArcToken', 0) + 1
            tok = self._noPenArcToken

            def _hideArc(_tok=tok):
                try:
                    if _tok == getattr(self, '_noPenArcToken', None):
                        self.destroyNoPenArc()
                except Exception:
                    pass
            BigWorld.callback(0.85, _hideArc)
        except Exception:
            try:
                self.destroyNoPenArc()
            except Exception:
                pass

    def destroyNoPenArc(self):
        try:
            _prev = getattr(self, '_noPenFlash', None)
            if _prev is not None:
                try:
                    _prev.close()
                except Exception:
                    pass
                self._noPenFlash = None
        except Exception:
            pass
        try:
            import GUI
            for _c in getattr(self, '_noPenArcGUI', []):
                try: GUI.delRoot(_c)
                except Exception: pass
            self._noPenArcGUI = None
        except Exception: pass

    def onAvatarReady(self): pass

    def onMinimapCellClicked(self, cellIdx):
        mm = getattr(self, '_minimap', None)
        if mm is not None:
            try: mm.markCell(cellIdx, 3.0)
            except Exception: pass

    def onMinimapClicked(self, worldPos):
        if self.inputHandler:
            try: self.inputHandler.onMinimapClicked(worldPos)
            except Exception: pass

    def getCurrentVehicleId(self): return self.playerVehicleID

    @property
    def minimap(self):
        if self._minimap is None:
            from gui.Minimap import Minimap
            self._minimap = Minimap()
        return self._minimap

    @property
    def terrainEffects(self):
        if self._terrainEffects is None:
            from helpers import bound_effects
            self._terrainEffects = bound_effects.StaticSceneBoundEffects()
        return self._terrainEffects

    @property
    def projectileMover(self):
        if self._projectileMover is None:
            from ProjectileMover import ProjectileMover
            self._projectileMover = ProjectileMover()
        return self._projectileMover

    @property
    def guiConfig(self): return self.__guiConfig

    @property
    def position(self):
        return self.getOwnVehiclePosition()

def make_vehicle_state(name, max_hp, descr=None):

    if descr is not None:
        return build_vehicle_from_descriptor(descr, name=name, max_hp=max_hp)
    from tank_battle_damage import _build_demo_vehicle
    v = _build_demo_vehicle(name=name, max_hp=max_hp)
    return v


class OfflineBattle:

    def _createSingleMarker(self, vehID):
        try:
            bw = getattr(g_windowsManager, 'battleWindow', None)
            if not bw or not hasattr(bw, 'vMarkersManager'): return
            vmm = bw.vMarkersManager

            veh = BigWorld.entity(vehID)
            if not veh or not getattr(veh, 'isStarted', False): return
            try:
                if vehID == getattr(self.playerAvatar, 'playerVehicleID', None): return
            except Exception: pass
            if getattr(veh, 'marker', -1) != -1: return

            descr = getattr(veh, 'typeDescriptor', None)
            if not descr: return

            team = self.arena.vehicles.get(vehID, {}).get('team', 0)
            isFriend = (team == self.playerAvatar.team)
            vName    = veh.publicInfo['name']
            vType    = descr.type
            maxHealth = descr.maxHealth
            mProv = _hp_gui_provider(veh)
            if mProv is None:
                mProv = _sync_gui_matrix(veh)
            if mProv is None:
                return

            from items import vehicles as _veh
            vClass   = 'lightTank'
            for vc in _veh.VEHICLE_CLASS_TAGS:
                if vc in descr.type.tags: vClass = vc; break

            veh.marker = vmm.createMarker(veh.proxy, vClass, vType, vName, _vehicle_health(veh), maxHealth, isFriend, mProv)
        except Exception: pass

    def _vehicleSpotModel(self, veh):
        m = None
        try:
            appr = getattr(veh, 'appearance', None)
            if appr is not None:
                md = getattr(appr, 'modelsDesc', {})
                hm = md.get('hull', {}).get('model', None)
                if hm is not None: m = hm
        except Exception: pass
        if m is None:
            try: m = veh.model
            except Exception: m = None
        return m

    def _setVehicleModelVisible(self, veh, visible):
        if veh is None: return
        try:
            appr = getattr(veh, 'appearance', None)
            if appr is not None:
                descs = getattr(appr, 'modelsDesc', None)
                if descs is not None:
                    for _comp in ('chassis', 'hull', 'turret', 'gun'):
                        try:
                            _m = descs.get(_comp, {}).get('model', None)
                            if _m is None: continue
                            try: _m.visible = visible
                            except Exception: pass
                            try: _m.visibleAttachments = visible
                            except Exception: pass
                        except Exception: pass
        except Exception: pass
        try: veh.model.visible = visible
        except Exception: pass
        try:
            _vid = getattr(veh, 'id', None)
            if _vid is not None:
                _is_enemy = False
                try:
                    if self.playerAvatar is not None and self.arena is not None:
                        _vt = self.arena.vehicles.get(_vid, {}).get('team', 0)
                        if _vt != 0 and _vt != getattr(self.playerAvatar, 'team', 1):
                            _is_enemy = True
                except Exception: pass
                if _is_enemy:
                    _sa = getattr(self, '_shadow_active', None)
                    if _sa is None:
                        self._shadow_active = _sa = {}
                    _st = _sa.get(_vid)
                    if visible:
                        if _st is False and hasattr(BigWorld, 'addShadowEntity'):
                            try:
                                BigWorld.addShadowEntity(veh)
                                _sa[_vid] = True
                            except Exception: pass
                    else:
                        if _st is not False and hasattr(BigWorld, 'delShadowEntity'):
                            try:
                                BigWorld.delShadowEntity(veh)
                                _sa[_vid] = False
                            except Exception: pass
        except Exception: pass

    def _setVehicleTraces(self, veh, enabled):
        try:
            appr = getattr(veh, 'appearance', None)
            if appr is None: return
            _m = None
            try: _m = appr.modelsDesc.get('chassis', {}).get('model', None)
            except Exception: _m = None
            if _m is None: return
            fashion = getattr(_m, 'wg_fashion', None)
            if fashion is None:
                try: fashion = appr.fashion
                except Exception: fashion = None
            if fashion is None: return
            tracesCfg = veh.typeDescriptor.chassis['traces']
            if not enabled:
                for _tti in (-1, 0):
                    try:
                        fashion.setTrackTraces(tracesCfg['decalGroup'], _tti, tracesCfg['centerOffset'], 0.0)
                    except Exception: pass
            else:
                _idx = 0
                try:
                    from helpers import DecalMap as _DM
                    if _DM.g_instance is not None:
                        _idx = _DM.g_instance.getIndex(tracesCfg['decalTexture'])
                        if _idx is None or _idx < 0: _idx = 0
                except Exception: _idx = 0
                try:
                    fashion.setTrackTraces(tracesCfg['decalGroup'], _idx, tracesCfg['centerOffset'], tracesCfg['size'])
                except Exception: pass
        except Exception: pass

    def _finishVehicleGhostHide(self, vehID):
        self._spot_ghost_callbacks.pop(vehID, None)
        veh = BigWorld.entity(vehID)
        if not veh: return
        m = self._vehicleSpotModel(veh)
        if m is not None:
            try: m.stipple = False
            except Exception: pass
        self._spot_hidden_models.add(vehID)
        self._setVehicleModelVisible(veh, False)
        self._setVehicleTraces(veh, False)

    def _startVehicleGhost(self, vehID, veh):
        prev = self._spot_ghost_callbacks.get(vehID)
        if prev is not None:
            try: BigWorld.cancelCallback(prev)
            except Exception: pass
        m = self._vehicleSpotModel(veh)
        if m is not None:
            try: m.stipple = True
            except Exception: pass
        self._spot_ghost_callbacks[vehID] = BigWorld.callback(
            _SPOT_GHOST_TIME, lambda vid=vehID: self._finishVehicleGhostHide(vid))

    def _finishSpotEntryFlash(self, vehID, m):
        self._spot_entry_callbacks.pop(vehID, None)
        if m is not None:
            try: m.stipple = False
            except Exception: pass

    def _flashSpotEntry(self, vehID):
        veh = BigWorld.entity(vehID)
        if not veh: return
        prev = self._spot_entry_callbacks.get(vehID)
        if prev is not None:
            try: BigWorld.cancelCallback(prev)
            except Exception: pass
        m = self._vehicleSpotModel(veh)
        if m is None: return
        try: m.stipple = True
        except Exception: pass
        self._spot_entry_callbacks[vehID] = BigWorld.callback(
            _SPOT_ENTRY_FLASH, lambda mref=m, vid=vehID: self._finishSpotEntryFlash(vid, mref))

    def _showVehicleVisual(self, vehID):
        veh = BigWorld.entity(vehID)
        if not veh: return
        try:
            _own_vid = getattr(self.playerAvatar, 'playerVehicleID', None)
            if _own_vid is not None and vehID == _own_vid:
                try:
                    bw = getattr(g_windowsManager, 'battleWindow', None)
                    if bw and hasattr(bw, 'vMarkersManager') and getattr(veh, 'marker', -1) != -1:
                        bw.vMarkersManager.destroyMarker(veh.marker)
                        veh.marker = -1
                except Exception: pass
                return
        except Exception: pass
        cb = self._spot_ghost_callbacks.pop(vehID, None)
        if cb is not None:
            try: BigWorld.cancelCallback(cb)
            except Exception: pass
        self._spot_hidden_models.discard(vehID)
        self._setVehicleModelVisible(veh, True)
        self._setVehicleTraces(veh, True)
        try:
            mm = getattr(self.playerAvatar, '_minimap', None)
            if mm: mm.notifyVehicleStart(vehID)
        except Exception: pass
        try:
            bw = getattr(g_windowsManager, 'battleWindow', None)
            if bw and hasattr(bw, 'vMarkersManager'):
                if getattr(veh, 'marker', -1) == -1:
                    self._createSingleMarker(vehID)
        except Exception: pass

    def _hideVehicleVisual(self, vehID):
        veh = BigWorld.entity(vehID)
        if not veh: return
        self._setVehicleTraces(veh, False)
        try:
            _avAim = getattr(self.playerAvatar, 'inputHandler', None)
            if _avAim is not None:
                _aim3 = getattr(_avAim, 'aim', None)
                if _aim3 is not None:
                    _curTgt = None
                    for _an in ('target', '_aim_target', '_Aim__target', '__target'):
                        _curTgt = getattr(_aim3, _an, None)
                        if _curTgt is not None: break
                    if _curTgt is not None and getattr(_curTgt, 'id', None) == vehID:
                        try: _aim3.clearTarget()
                        except Exception: pass
        except Exception: pass
        try:
            _avLock = self.playerAvatar
            if _avLock is not None and getattr(_avLock, '_lockedOnVehID', 0) == vehID:
                _avLock.onLockedVehicleLost()
        except Exception: pass
        try:
            mm = getattr(self.playerAvatar, '_minimap', None)
            if mm: mm.notifyVehicleStop(vehID)
        except Exception: pass
        try:
            bw = getattr(g_windowsManager, 'battleWindow', None)
            if bw and hasattr(bw, 'vMarkersManager'):
                if getattr(veh, 'marker', -1) != -1:
                    bw.vMarkersManager.destroyMarker(veh.marker)
                    veh.marker = -1
        except Exception: pass
        self._startVehicleGhost(vehID, veh)

    def _setVehicleVisibility(self, vehID, visible):
        if visible:
            self._showVehicleVisual(vehID)
        else:
            self._hideVehicleVisual(vehID)

    def _createVehicleMarkers(self):
        if getattr(self, '_is_finished', False): return
        try:
            bw = getattr(g_windowsManager, 'battleWindow', None)
            if bw is None or not hasattr(bw, 'vMarkersManager'):
                BigWorld.callback(0.3, self._createVehicleMarkers)
                return
            try:
                from gui.Scaleform.Battle import _VehicleMarker as _VM
                if not getattr(_VM, '_offline_gui_mat_patched', False):
                    def _omc_keep_gui(self_m):
                        try:
                            prov = _hp_gui_provider(self_m.vProxy)
                            if prov is None:
                                prov = _sync_gui_matrix(self_m.vProxy)
                            if prov is not None:
                                self_m.gui.wg_positionMatProv = prov
                        except Exception:
                            pass
                    _VM._VehicleMarker__onModelChanged = _omc_keep_gui
                    _VM._offline_gui_mat_patched = True
            except Exception:
                pass

            for vehID, descr, isPlayer in self.vehicles:
                if isPlayer: continue

                team = self.arena.vehicles.get(vehID, {}).get('team', 0)
                isFriend = (team == self.playerAvatar.team)

                if isFriend:
                    self._setVehicleVisibility(vehID, True)
                else:
                    pass
        except Exception: pass

    def _spottingTick(self):
        if getattr(self, '_is_finished', False): return
        try:
            if not self.playerAvatar or self.arena is None:
                raise Exception('not ready yet')

            if not getattr(self, '_battle_started', False):
                _own_team = None
                try: _own_team = getattr(self.playerAvatar, 'team', None)
                except Exception: pass
                _pre_ally = set()
                for vehID, descr, isPlayerFlag in self.vehicles:
                    if isPlayerFlag:
                        _pre_ally.add(vehID)
                        continue
                    try:
                        _v = BigWorld.entity(vehID)
                        if _v is None or not getattr(_v, 'isStarted', False): continue
                        if getattr(_v, 'health', 1) <= 0: continue
                    except Exception: continue
                    _is_enemy = False
                    try:
                        _vt = self.arena.vehicles.get(vehID, {}).get('team', 0)
                        _is_enemy = (_own_team is not None and _vt != _own_team)
                    except Exception: pass
                    if _is_enemy:
                        try: self._setVehicleVisibility(vehID, False)
                        except Exception: pass
                    else:
                        _pre_ally.add(vehID)
                        try: self._setVehicleVisibility(vehID, True)
                        except Exception: pass
                self._spotted_vehicles = set(_pre_ally)
                self._spotted_timers = getattr(self, '_spotted_timers', {})
                _now_t = BigWorld.time()
                for _vid in self._spotted_vehicles:
                    self._spotted_timers[_vid] = _now_t
                BigWorld.callback(_SPOT_INTERVAL, self._spottingTick)
                return

            my_team = self.playerAvatar.team
            observers = []
            targets = []
            if not hasattr(self, '_spot_prev_pos'): self._spot_prev_pos = {}

            for vehID, descr, isPlayerFlag in self.vehicles:
                veh = BigWorld.entity(vehID)
                if veh is None or not getattr(veh, 'isStarted', False): continue
                team = self.arena.vehicles.get(vehID, {}).get('team', 0)
                is_dead = not _vehicle_is_alive(veh)
                om = getattr(veh, '_offline_matrix', None)
                p = om.translation if om is not None else veh.position

                if team == my_team:
                    try:
                        _mm = getattr(self.playerAvatar, '_minimap', None)
                        if _mm: _mm.notifyVehicleStart(vehID)
                    except Exception: pass
                    if is_dead: continue
                    try: viewRange = descr.turret['circularVisionRadius']
                    except Exception: viewRange = 250.0
                    try: viewRange *= descr.miscAttrs.get('circularVisionRadiusFactor', 1.0)
                    except Exception: pass
                    try: viewRange *= self._view_range_factor(vehID)
                    except Exception: pass
                    observers.append((p.x, p.y, p.z, viewRange))
                    _pvid_check = getattr(getattr(self, 'playerAvatar', None), 'playerVehicleID', None)
                    if vehID == _pvid_check:
                        self._player_observer_pos = (p.x, p.y, p.z)
                        self._player_observer_vr = viewRange
                else:
                    _pxz = self._spot_prev_pos.get(vehID)
                    _moved = _pxz is None or _math.sqrt((p.x - _pxz[0]) ** 2 + (p.z - _pxz[1]) ** 2) > 0.6
                    isMoving = abs(getattr(veh, '_ai_target_speed', 0.0)) > 1.0 or _moved
                    targets.append((vehID, p.x, p.y, p.z, is_dead, isMoving))
                    self._spot_prev_pos[vehID] = (p.x, p.z)

            spotted_now = set()
            for vehID, tx, ty, tz, isDead, isMoving in targets:
                if isDead:
                    spotted_now.add(vehID)
                    continue
                for ox, oy, oz, viewRange in observers:
                    dx, dz = tx - ox, tz - oz
                    dist = _math.sqrt(dx * dx + dz * dz)
                    effRange = max(viewRange * (1.0 if isMoving else 0.55), _SPOT_ALWAYS_RANGE)
                    if dist > effRange: continue
                    res = BigWorld.wg_collideSegment(self.spaceID, Math.Vector3(ox, oy + 2.0, oz), Math.Vector3(tx, ty + 1.0, tz), 128)
                    if res is None:
                        spotted_now.add(vehID)
                        break

            _p_spot_tick = set()
            try:
                _pop = getattr(self, '_player_observer_pos', None)
                _pvr = getattr(self, '_player_observer_vr', 0.0) or 0.0
                if _pop is not None and _pvr > 0.0:
                    for vehID, tx, ty, tz, isDead, isMoving in targets:
                        if isDead: continue
                        _edx, _edz = tx - _pop[0], tz - _pop[2]
                        _edist = _math.sqrt(_edx * _edx + _edz * _edz)
                        _effP = max(_pvr * (1.0 if isMoving else 0.55), _SPOT_ALWAYS_RANGE)
                        if _edist > _effP: continue
                        _rp = BigWorld.wg_collideSegment(self.spaceID, Math.Vector3(_pop[0], _pop[1] + 2.0, _pop[2]), Math.Vector3(tx, ty + 1.0, tz), 128)
                        if _rp is None:
                            _p_spot_tick.add(vehID)
            except Exception: pass

            prev = getattr(self, '_spotted_vehicles', set())
            hidden_now = set(getattr(self, '_spot_hidden_models', set()))
            timers = self._spotted_timers if hasattr(self, '_spotted_timers') else {}
            now_t = BigWorld.time()
            final_spotted = set()
            for vehID, tx, ty, tz, isDead, isMoving in targets:
                is_now = vehID in spotted_now
                if is_now:
                    timers[vehID] = now_t
                    is_final = True
                else:
                    is_final = vehID in prev and now_t - timers.get(vehID, 0.0) <= _SPOT_LINGER
                if is_final: final_spotted.add(vehID)

                was = vehID in prev
                if is_final and not was:
                    self._setVehicleVisibility(vehID, True)
                    if not isDead:
                        try: self._first_spotted_ids.add(vehID)
                        except Exception: pass
                        if vehID in _p_spot_tick:
                            try: self._player_detected_ids.add(vehID)
                            except Exception: pass
                    self._flashSpotEntry(vehID)
                    if not isDead:
                        try:
                            _sna = getattr(self, '_spot_announce_times', {})
                            if now_t - _sna.get(vehID, 0.0) >= _SPOT_ANNOUNCE_RATE:
                                _sna[vehID] = now_t
                                self._spot_announce_times = _sna
                                _sn = getattr(self, 'soundNotifications', None)
                                if _sn is not None:
                                    _sn.play('enemy_sighted')
                                else:
                                    try:
                                        import BigWorld as _BW
                                        _snd = _BW.playSound('/ingame_voice/notifications_VO/enemy_sighted')
                                        if _snd is not None:
                                            _snd.setCallback('SOUNDDEF_END', lambda *a, **k: None)
                                    except Exception: pass
                        except Exception: pass
                elif not is_final:
                    if was or vehID not in hidden_now:
                        self._setVehicleVisibility(vehID, False)

            self._spotted_vehicles = final_spotted
            self._spotted_timers = timers
        except Exception: pass

        BigWorld.callback(_SPOT_INTERVAL, self._spottingTick)

    def _patch_vehicle(self):
        import Vehicle as V
        def _offline_isAlive(self_v):
            return _vehicle_is_alive(self_v)
        V.Vehicle.isAlive = _offline_isAlive
        if getattr(V.Vehicle, '_patched_alive', False):
            return
        V.Vehicle.onLeaveWorld   = lambda self: setattr(self, 'isStarted', False)
        def _set_isCrewActive_offline(self, prev=None):
            if not getattr(self, 'isStarted', False): return
            if getattr(self, 'appearance', None):
                try: self.appearance.onVehicleHealthChanged()
                except Exception: pass
        V.Vehicle.set_isCrewActive = _set_isCrewActive_offline
        V.Vehicle.collideDynamic = lambda self_veh, mass, damage, direction: None
        V.Vehicle._patched_alive = True

    def _patch_effects(self):
        from helpers import EffectsList as EL
        if getattr(EL, '_patched_fx_v2', False): return
        _orig_shock_create = EL._ShockWaveEffectDesc.create
        def _shock_create(self_eff, model, lst, args):
            args = dict(args)
            try:
                p = BigWorld.player()
                pv = getattr(p, 'playerVehicleID', None) if p is not None else None
                ent = args.get('entity')
                eid = getattr(ent, 'id', None) if ent is not None else None
                if eid is None or eid != pv:
                    args['showShockWave'] = False
            except Exception:
                args['showShockWave'] = False
            return _orig_shock_create(self_eff, model, lst, args)
        EL._ShockWaveEffectDesc.create = _shock_create
        _orig_flash_create = EL._FlashBangEffectDesc.create
        def _flash_create(self_eff, model, lst, args):
            args = dict(args)
            try:
                p = BigWorld.player()
                pv = getattr(p, 'playerVehicleID', None) if p is not None else None
                ent = args.get('entity')
                eid = getattr(ent, 'id', None) if ent is not None else None
                if eid is None or eid != pv:
                    args['showFlashBang'] = False
            except Exception:
                args['showFlashBang'] = False
            return _orig_flash_create(self_eff, model, lst, args)
        EL._FlashBangEffectDesc.create = _flash_create
        try:
            _orig_find_node = EL._findTargetNode
            def _safe_find_node(model, nodes, localTransform=None):
                try:
                    return _orig_find_node(model, nodes, localTransform)
                except Exception:
                    try:
                        return model.node('Scene Root', localTransform)
                    except Exception:
                        return model
            EL._findTargetNode = _safe_find_node
        except Exception:
            pass
        EL._patched_fx_v2 = True

    def __init__(self):
        global _OFFLINE_HP, _OFFLINE_DEAD
        try:
            _OFFLINE_HP.clear()
            _OFFLINE_DEAD.clear()
        except Exception:
            _OFFLINE_HP = {}
            _OFFLINE_DEAD = set()
        self.spaceID = None
        self.arena = None
        self.playerAvatar = None
        self.vehicles = []
        self._oldPlayer = None
        self.battleWindow = None
        self._prebattleStartTime = None
        self._is_finished = False
        self._hud_hull_mat = Math.Matrix()
        self._finish_called = False
        self._results_shown = False
        self._current_arena_id = None
        self._dmg_resolver = DamageResolver()
        self._dmg_vehicle_states = {}
        self._repair_in_progress = {}
        self._battle_started = False
        self._spot_entry_callbacks = {}
        self._spot_ghost_callbacks = {}
        self._spot_hidden_models = set()
        self._first_spotted_ids = set()
        self._player_detected_ids = set()
        self._player_observer_pos = None
        self._player_observer_vr = 0.0
        self._shadow_active = {}
        try:
            from gui.Scaleform.Battle import Battle
            if not hasattr(Battle, '_patched_for_offline_cleanup'):
                if hasattr(Battle, 'beforeDelete'):
                    orig_beforeDelete = Battle.beforeDelete
                    def safe_beforeDelete(self_window, *a, **kw):
                        try: orig_beforeDelete(self_window, *a, **kw)
                        except: pass
                    Battle.beforeDelete = safe_beforeDelete
                if hasattr(Battle, 'destroy'):
                    orig_destroy = Battle.destroy
                    def safe_destroy(self_window, *a, **kw):
                        try: orig_destroy(self_window, *a, **kw)
                        except: pass
                    Battle.destroy = safe_destroy
                if hasattr(Battle, '_Battle__setArenaTime'):
                    def _offline_setArenaTime_patched(self_window, *a, **k):
                        try: _offline_timer_setArenaTime(self_window)
                        except Exception: pass
                    Battle._Battle__setArenaTime = _offline_setArenaTime_patched
                Battle._patched_for_offline_cleanup = True
        except: pass

    def start(self, arenaTypeID=None, playerVehicleName=None, botCount=14, trainingMode=False, trainingBots=None, roundLength=None, playerTeam=None):
        try:
            from Offline import Manager as _MgrSnap
            _MgrSnap._snapshot_service_channel()
        except Exception:
            pass
        self._training_mode = bool(trainingMode)
        self._training_bots = list(trainingBots or [])
        self._battle_duration = 900.0
        try:
            if BotDirector is not None:
                self._bot_director = BotDirector(self)
            else:
                self._bot_director = None
                self._bot_state = {}
        except Exception:
            self._bot_director = None
            self._bot_state = {}
        try:
            from Offline import Manager as _Mgr
            _Mgr._reopen_training_after_battle = bool(self._training_mode)
            if not self._training_mode:
                _Mgr._TRAINING_ROOM['keep'] = False
        except Exception:
            pass
        if self._training_mode:
            try:
                _rl = float(roundLength)
                if _rl > 0:
                    self._battle_duration = _rl
            except Exception:
                pass
        if playerVehicleName is None:
            try:
                from CurrentVehicle import g_currentVehicle
                veh = g_currentVehicle.vehicle
                if veh and veh.descriptor:
                    playerDescr = veh.descriptor
                    playerCompDescr = playerDescr.makeCompactDescr()
                else: playerCompDescr = None
            except Exception: playerCompDescr = None
        else: playerCompDescr = None

        if isinstance(arenaTypeID, (tuple, list)): arenaTypeID = arenaTypeID[0]
        _typeID, _typeName = _arena_name_to_id(arenaTypeID)
        if _typeID is None:
            LOG_ERROR("[BATTLE] start: cannot resolve arenaTypeID=%r (ArenaType.g_list empty or unknown map)" % (arenaTypeID,))
            return
        self._current_arena_type_id = _typeID
        self._current_arena_id = _typeName
        arenaTypeID = _typeName
        try:
            _wall_mem_clear()
        except Exception:
            pass
        try:
            _G_DESTR_BROKEN.clear()
        except Exception:
            pass
        try:
            _scenery_reset()
        except Exception:
            pass

        self._patch_decalmap()
        try:
            from gui.Scaleform.Waiting import Waiting
            Waiting.hide()
        except: pass

        _load_cfg(arenaTypeID)
        self._battleEnterTime = BigWorld.time()

        try:
            import MusicController as MC
            import FMOD
            mc = MC.g_musicController
            _offline_prepare_music(mc)
            self._music_bank_loaded = True
            if mc is not None:
                try:
                    mc.stopAmbient()
                except Exception:
                    pass
                mc.play(_music_loading_event(MC))
                LOG_NOTE("[BATTLE] loading music via MusicController")
            else:
                _snd = FMOD.getSound(_MAP_MUSIC_COMBAT)
                _fmod_sound_play(_snd)
                LOG_NOTE("[BATTLE] loading music via FMOD combat fallback")
        except Exception as _e:
            LOG_NOTE("[BATTLE] load music failed: %s" % _e)

        try: BigWorld.wg_addDecal = lambda *a, **k: 0
        except: pass

        if not hasattr(BigWorld, '_orig_player_fn'): BigWorld._orig_player_fn = BigWorld.player
        self._oldPlayer = BigWorld._orig_player_fn()
        self._prebattle_id = 0
        try:
            _prb = getattr(self._oldPlayer, 'prebattle', None)
            if _prb is not None:
                self._prebattle_id = int(getattr(_prb, 'id', 0) or 0)
        except Exception:
            self._prebattle_id = 0

        try:
            from Offline import Manager
            playerName = Manager._player_name
        except Exception: playerName = "Commander"

        if getattr(self, '_training_mode', False):
            try:
                _pt = int(playerTeam)
            except Exception:
                _pt = 1
            if _pt not in (1, 2):
                _pt = 1
            self._player_team = _pt
        else:
            self._player_team = _random.choice((1, 2))
        self._enemy_team = 3 - self._player_team

        self.playerAvatar = SimpleAvatar(playerName, self._player_team, -1)
        self.playerAvatar._offline_battle = self
        self.playerAvatar.arenaTypeID = self._current_arena_type_id
        self.playerAvatar.arenaGuiType = constants.ARENA_GUI_TYPE.RANDOM
        self.playerAvatar.weatherPresetID = 0
        BigWorld.player = lambda: self.playerAvatar

        arenaType = load_arena_type(self._current_arena_type_id)
        if arenaType is None: return

        BigWorld.worldDrawEnabled(False)
        BigWorld.wg_useAttachmentBboxesInShadowCasting(True)
        BigWorld.wg_setIndoorMainLightDir(_SHADOW_LIGHT_DIR)

        self.spaceID = BigWorld.createSpace()
        geometry = getattr(arenaType, 'geometry', None) or ('spaces/' + arenaTypeID)
        if not str(geometry).startswith('spaces/'):
            geometry = 'spaces/' + geometry
        BigWorld.addSpaceGeometryMapping(self.spaceID, None, geometry)

        import ResMgr
        geomDir = geometry
        try:
            for file in ResMgr.listFiles(geomDir):
                if file.endswith('.bsp'): BigWorld.addSpaceGeometryMapping(self.spaceID, None, geomDir + '/' + file)
        except: pass

        self.playerAvatar.spaceID = self.spaceID
        BigWorld.createEntity("Account", self.spaceID, 0, _V_START_POS,
            (math.radians(_V_START_ANGLES[2]), math.radians(_V_START_ANGLES[1]), math.radians(_V_START_ANGLES[0])), {})

        cam = BigWorld.CursorCamera()
        cam.spaceID       = self.spaceID
        cam.pivotMaxDist  = _CAM_START_DIST
        cam.maxDistHalfLife  = _CAM_FLUENCY
        cam.turningHalfLife  = _CAM_FLUENCY
        cam.movementHalfLife = 0.0
        cam.pivotPosition    = _CAM_PIVOT_POS

        matSrc = Math.Matrix()
        matSrc.setRotateYPR((math.radians(_CAM_START_ANGLES[1]), math.radians(_CAM_START_ANGLES[0]), 0.0))
        cam.source = matSrc
        matTgt = Math.Matrix()
        matTgt.setTranslate(_CAM_START_TARGET_POS)
        cam.target = matTgt

        BigWorld.camera(cam)
        BigWorld.worldDrawEnabled(True)
        try:
            BigWorld.wg_enableTreeHiding(False)
            BigWorld.projection().farPlane = 1000.0
            BigWorld.projection().nearPlane = 0.25
        except Exception: pass
        g_destructiblesManager.startSpace(self.spaceID)
        try:
            _scenery_install(self.spaceID)
        except Exception:
            LOG_CURRENT_EXCEPTION()

        self._chunk_destr_counts = {}
        _orig_wg_onChunkLoad = game.wg_onChunkLoad
        _battle_self_ref = self
        def _patched_wg_onChunkLoad(spaceID, chunkID, numDestructibles, isOutside):
            if spaceID == _battle_self_ref.spaceID:
                _battle_self_ref._chunk_destr_counts[chunkID] = numDestructibles
                _scenery_remember(chunkID, numDestructibles)
            _orig_wg_onChunkLoad(spaceID, chunkID, numDestructibles, isOutside)
        game.wg_onChunkLoad = _patched_wg_onChunkLoad

        self.arena = ClientArena(self._current_arena_type_id, constants.ARENA_GUI_TYPE.RANDOM, 0)
        self.playerAvatar.arena = self.arena

        try: g_playerEvents.onAvatarBecomePlayer()
        except: pass

        try:
            from gui.WindowsManager import g_windowsManager
            from gui.Scaleform.CommonPage import CommonPage
            g_windowsManager.window = CommonPage
            g_windowsManager.window.processBattleLoading()
            print "[OFFLINE] manual battle-loading rebuild OK"
        except Exception as e:
            print "[OFFLINE] manual battle-loading rebuild failed: %s" % e

        def get_pos_on_ground(x, z):
            groundY = get_ground_height(self.spaceID, Math.Vector3(x, 0, z))
            if groundY <= 0.0: groundY = 10.0
            return Math.Vector3(x, groundY + 1.0, z)

        if playerCompDescr: playerDescr = vehicles.VehicleDescr(compactDescr=playerCompDescr)
        else: playerDescr = vehicles.VehicleDescr(typeName=playerVehicleName or "ussr:T-26")
        try:
            from CurrentVehicle import g_currentVehicle as _gcv_bon
            _inv_bon = _gcv_bon.vehicle.inventoryId if _gcv_bon.vehicle else 1
        except Exception:
            _inv_bon = 1
        _apply_offline_crew_and_eq_bonuses(playerDescr, _inv_bon)

        _player_tier = getattr(playerDescr, 'level', 1)

        if not hasattr(OfflineBattle, '_tier_map_cache'): OfflineBattle._tier_map_cache = None

        def _build_tier_map():
            if OfflineBattle._tier_map_cache is not None:
                return OfflineBattle._tier_map_cache
            tmap = {}
            for _name in _BOT_TANK_LIST:
                try:
                    _lvl = vehicles.VehicleDescr(typeName=_name).level
                    tmap.setdefault(_lvl, []).append(_name)
                except Exception:
                    pass
            OfflineBattle._tier_map_cache = tmap
            return tmap

        def _tier_candidates(player_tier):
            if player_tier <= 3:
                cands = [1, 2, 3]
                weights = [max(1, 4 - abs(player_tier - t)) for t in cands]
            else:
                low = max(1, player_tier - 2)
                cands = list(range(low, 11))
                weights = []
                for t in cands:
                    dist = abs(t - player_tier)
                    w = 1 if (t == 10 and player_tier < 8) else max(1, 6 - dist)
                    weights.append(w)
            return cands, weights

        def _pick_weighted_tier(cands, weights):
            total = sum(weights)
            r = _random.uniform(0, total)
            upto = 0.0
            for c, w in zip(cands, weights):
                upto += w
                if upto >= r: return c
            return cands[-1]

        def _pick_bot_vehicle_for_tier(tmap, tier):
            names = tmap.get(tier)
            if not names:
                for d in range(1, 10):
                    for t2 in (tier - d, tier + d):
                        if tmap.get(t2):
                            names = tmap[t2]
                            break
                    if names: break
            return _random.choice(names) if names else "ussr:T-26"

        _tier_map = _build_tier_map()
        _tier_cands, _tier_weights = _tier_candidates(_player_tier)

        def _pick_bot_type():
            _t = _pick_weighted_tier(_tier_cands, _tier_weights)
            return _pick_bot_vehicle_for_tier(_tier_map, _t)

        _spawns_pre = _MAP_SPAWNS.get(arenaTypeID, None)
        self._used_spawn_idx = {1: set(), 2: set()}
        self._spawn_reuse_count = {1: {}, 2: {}}
        self._spawn_yaws = {}
        self._spawn_xyz = {}
        if _spawns_pre and self._player_team in _spawns_pre and len(_spawns_pre[self._player_team]) > 0:
            _idx = _random.randrange(len(_spawns_pre[self._player_team]))
            self._used_spawn_idx[self._player_team].add(_idx)
            _sp = _spawns_pre[self._player_team][_idx]
            _spawnY = _spawn_ground_y(self.spaceID, _sp[0], _sp[2], _sp[1])
            playerPos = Math.Vector3(_sp[0], _spawnY, _sp[2])
            self._spawn_xyz_player_hint = (_sp[0], _sp[1], _sp[2])
        else:
            playerPos = get_pos_on_ground(0, 0)
            self._spawn_xyz_player_hint = (playerPos.x, playerPos.y, playerPos.z)

        _map_cx, _map_cz = _arena_center_xz(self.arena)
        _player_yaw = _face_yaw(playerPos.x, playerPos.z, _map_cx, _map_cz)
        self._cur_yaw = _player_yaw
        self._cur_pos = Math.Vector3(playerPos)

        playerVehID = BigWorld.createEntity("Vehicle", self.spaceID, 0, playerPos, (_player_yaw,0,0),
                                            {"publicInfo": {
                                                "health": playerDescr.maxHealth,
                                                "compDescr": playerDescr.makeCompactDescr(),
                                                "name": self.playerAvatar.name,
                                                "team": self._player_team,
                                                "isAlive": True,
                                                "isAvatarReady": True
                                            }})
        self.playerAvatar.playerVehicleID = playerVehID
        self.playerAvatar.vehicleTypeDescriptor = playerDescr
        self.vehicles.append((playerVehID, playerDescr, True))
        self._spawn_yaws[playerVehID] = _player_yaw
        try:
            _ph = self._spawn_xyz_player_hint
            self._spawn_xyz[playerVehID] = (_ph[0], _ph[1], _ph[2])
        except Exception:
            self._spawn_xyz[playerVehID] = (playerPos.x, playerPos.y, playerPos.z)
        LOG_NOTE("[BATTLE] player spawn ground x=%.1f y=%.2f z=%.1f hint=%.2f" % (
            playerPos.x, playerPos.y, playerPos.z, self._spawn_xyz[playerVehID][1]))

        self.arena.update(constants.ARENA_UPDATE_VEHICLE_ADDED, cPickle.dumps(
            _arena_vehicle_info_tuple(playerVehID, playerDescr.makeCompactDescr(),
                                      self.playerAvatar.name, self._player_team, True, True, 1, self._prebattle_id)))
        self.arena.vehicles[playerVehID].update({
            'health': playerDescr.maxHealth, 'frags': 0, 'clanAbbrev': '', 'vehicleID': playerVehID
        })

        if not hasattr(self.arena, 'statistics'): self.arena.statistics = {}
        self.arena.statistics[playerVehID] = {'frags': 0}
        self._squad_spawn_extra = []
        try:
            _prb = getattr(self._oldPlayer, 'prebattle', None)
            if _prb is not None and getattr(_prb, 'rosters', None) and not getattr(self, '_training_mode', False):
                _prb_type = 0
                try:
                    import constants as _cprb
                    _prb_type = int((_prb.settings or {}).get('type', 0) or 0)
                    if _prb_type != _cprb.PREBATTLE_TYPE.SQUAD:
                        _prb = None
                except Exception:
                    _prb = None
                if _prb is not None:
                    _my = self.playerAvatar.name
                    for _rid, _members in _prb.rosters.items():
                        for _mid, _info in _members.items():
                            if not isinstance(_info, dict):
                                continue
                            if _info.get('name') == _my:
                                continue
                            _bcd = _info.get('vehCompDescr') or playerDescr.makeCompactDescr()
                            self._squad_spawn_extra.append((_info.get('name') or 'SquadMate', _bcd, self._player_team))
        except Exception:
            self._squad_spawn_extra = []

        spawns = _MAP_SPAWNS.get(arenaTypeID, None)

        if spawns:
            team1 = spawns[1]
            team2 = spawns[2]
            _team1_c = _coords_centroid(team1) if team1 else (0.0, 0.0)
            _team2_c = _coords_centroid(team2) if team2 else (0.0, 0.0)

            def _spawn_at(typeName, team, coords):
                try: botDescr = vehicles.VehicleDescr(typeName=typeName)
                except Exception: return
                _randomize_bot_modules(botDescr)
                for sp in coords:
                    _bSpawnY = _spawn_ground_y(self.spaceID, sp[0], sp[2], sp[1])
                    pos = Math.Vector3(sp[0], _bSpawnY, sp[2])
                    _bYaw = _face_yaw(pos.x, pos.z, _map_cx, _map_cz)
                    try:
                        _bname = _generate_bot_name()
                        botID = BigWorld.createEntity("Vehicle", self.spaceID, 0, pos, (_bYaw, 0, 0),
                                                      {"publicInfo": {
                                                          "compDescr": botDescr.makeCompactDescr(),
                                                          "name": _bname,
                                                          "team": team,
                                                          "isAlive": True,
                                                          "isAvatarReady": True
                                                      }})
                        self.vehicles.append((botID, botDescr, False))
                        self._spawn_yaws[botID] = _bYaw
                        try:
                            self._spawn_xyz[botID] = (sp[0], sp[1], sp[2])
                        except Exception:
                            pass
                        self.arena.update(constants.ARENA_UPDATE_VEHICLE_ADDED, cPickle.dumps(
                            _arena_vehicle_info_tuple(botID, botDescr.makeCompactDescr(), _bname, team)))
                        self.arena.vehicles[botID].update({
                            'health': botDescr.maxHealth, 'frags': 0, 'clanAbbrev': '', 'vehicleID': botID, 'name': _bname
                        })
                        if botID not in self.arena.statistics: self.arena.statistics[botID] = {'frags': 0}
                    except Exception: pass

            def _pick_spawn(coords_list, team_num):
                if not coords_list: return None
                _used = self._used_spawn_idx.setdefault(team_num, set())
                _reuse = self._spawn_reuse_count.setdefault(team_num, {})
                _free = [i for i in range(len(coords_list)) if i not in _used]
                if not _free:
                    idx = _random.randrange(len(coords_list))
                    _used.add(idx)
                    base = coords_list[idx]
                    n = _reuse.get(idx, 0)
                    _reuse[idx] = n + 1
                    angle = n * 2.399963229728653
                    radius = 9.0 + 5.0 * n
                    newX = base[0] + radius * _math.cos(angle)
                    newZ = base[2] + radius * _math.sin(angle)
                    newY = _spawn_ground_y(self.spaceID, newX, newZ, base[1])
                    return [(newX, newY, newZ)]
                idx = _random.choice(_free)
                _used.add(idx)
                return [coords_list[idx]]

            _avail_team1 = max(0, len(team1) - 1)
            _avail_team2 = max(0, len(team2) - 1)
            try:
                _bot_cap = int(botCount)
            except Exception:
                _bot_cap = 14
            if getattr(self, '_training_mode', False):
                _bot_cap = 0
            if _bot_cap < 0:
                _bot_cap = 0
            _n = min(_avail_team1, _avail_team2, _bot_cap)

            def _spawn_descr_at(botDescr, bname, team, coords, prebattleID=0):
                if not coords:
                    return
                for sp in coords:
                    _bSpawnY = _spawn_ground_y(self.spaceID, sp[0], sp[2], sp[1])
                    pos = Math.Vector3(sp[0], _bSpawnY, sp[2])
                    _bYaw = _face_yaw(pos.x, pos.z, _map_cx, _map_cz)
                    try:
                        botID = BigWorld.createEntity("Vehicle", self.spaceID, 0, pos, (_bYaw, 0, 0),
                                                      {"publicInfo": {
                                                          "compDescr": botDescr.makeCompactDescr(),
                                                          "name": bname,
                                                          "team": team,
                                                          "isAlive": True,
                                                          "isAvatarReady": True
                                                      }})
                        self.vehicles.append((botID, botDescr, False))
                        self._spawn_yaws[botID] = _bYaw
                        try:
                            self._spawn_xyz[botID] = (sp[0], sp[1], sp[2])
                        except Exception:
                            pass
                        self.arena.update(constants.ARENA_UPDATE_VEHICLE_ADDED, cPickle.dumps(
                            _arena_vehicle_info_tuple(botID, botDescr.makeCompactDescr(), bname, team, True, True, 0, prebattleID)))
                        self.arena.vehicles[botID].update({
                            'health': botDescr.maxHealth, 'frags': 0, 'clanAbbrev': '', 'vehicleID': botID, 'name': bname,
                            'prebattleID': int(prebattleID or 0)
                        })
                        if botID not in self.arena.statistics: self.arena.statistics[botID] = {'frags': 0}
                    except Exception:
                        pass

            def _spawn_prb_extras():
                _tbots = list(getattr(self, '_training_bots', []) or [])
                if getattr(self, '_training_mode', False):
                    for _bname, _bcd, _bteam in _tbots:
                        try:
                            _team = int(_bteam)
                        except Exception:
                            _team = self._enemy_team
                        if _team not in (1, 2):
                            _team = self._enemy_team
                        _use = _bcd or playerDescr.makeCompactDescr()
                        try:
                            _bd = vehicles.VehicleDescr(compactDescr=_use)
                        except Exception:
                            _bd = playerDescr
                        _spawn_descr_at(_bd, _bname or 'Bot', _team, _pick_spawn(spawns[_team], _team))
                for _sname, _scd, _steam in getattr(self, '_squad_spawn_extra', []) or []:
                    _use = _scd or playerDescr.makeCompactDescr()
                    try:
                        _sd = vehicles.VehicleDescr(compactDescr=_use)
                    except Exception:
                        _sd = playerDescr
                    try:
                        _st = int(_steam)
                    except Exception:
                        _st = self._player_team
                    _spawn_descr_at(_sd, _sname or 'SquadMate', _st, _pick_spawn(spawns[_st], _st), self._prebattle_id)

            _spawn_prb_extras()
            if not getattr(self, '_training_mode', False) and _n > 0:
                for _i in range(_n): _spawn_at(_pick_bot_type(), team=self._player_team, coords=_pick_spawn(spawns[self._player_team], self._player_team))
                for _i in range(_n + 1): _spawn_at(_pick_bot_type(), team=self._enemy_team, coords=_pick_spawn(spawns[self._enemy_team], self._enemy_team))

        else:
            playerPos = get_pos_on_ground(0, 0)
            def _spawn_bots(typeName, team, count, startX, startZ, stepX, stepZ, target_regs):
                _cols = max(1, int(_math.ceil(_math.sqrt(count))))
                for i in range(count):
                    row, col = divmod(i, _cols)
                    _botTypeName = _pick_bot_type() if typeName is None else typeName
                    try: botDescr = vehicles.VehicleDescr(typeName=_botTypeName)
                    except Exception: continue
                    _randomize_bot_modules(botDescr)
                    x = startX + col * stepX + _random.uniform(-5.0, 5.0)
                    z = startZ + row * stepZ + _random.uniform(-5.0, 5.0)
                    pos = get_pos_on_ground(x, z)
                    _bYaw = _face_yaw(pos.x, pos.z, _map_cx, _map_cz)
                    try:
                        _bname = _generate_bot_name()
                        botID = BigWorld.createEntity("Vehicle", self.spaceID, 0, pos, (_bYaw, 0, 0),
                                                      {"publicInfo": {
                                                          "compDescr": botDescr.makeCompactDescr(),
                                                          "name": _bname,
                                                          "team": team,
                                                          "isAlive": True,
                                                          "isAvatarReady": True
                                                      }})
                        self.vehicles.append((botID, botDescr, False))
                        self._spawn_yaws[botID] = _bYaw
                        try:
                            self._spawn_xyz[botID] = (pos.x, pos.y, pos.z)
                        except Exception:
                            pass
                        self.arena.update(constants.ARENA_UPDATE_VEHICLE_ADDED, cPickle.dumps(
                            _arena_vehicle_info_tuple(botID, botDescr.makeCompactDescr(), _bname, team)))
                        self.arena.vehicles[botID].update({
                            'health': botDescr.maxHealth, 'frags': 0, 'clanAbbrev': '', 'vehicleID': botID, 'name': _bname
                        })
                        if botID not in self.arena.statistics: self.arena.statistics[botID] = {'frags': 0}
                    except Exception: pass

            def _split_evenly(total, n_regions):
                base = total // n_regions
                rem = total % n_regions
                return [base + (1 if i < rem else 0) for i in range(n_regions)]

            _my_regions = [(5, 5), (-6, 6), (6, -6), (-7, -7)]
            _en_regions = [(100, 100), (-100, 100), (100, -100), (-100, -100)]
            for (sx, sz), cnt in zip(_my_regions, _split_evenly(botCount, len(_my_regions))):
                if cnt > 0 and not getattr(self, '_training_mode', False):
                    _spawn_bots(None, team=self._player_team, count=cnt, startX=sx, startZ=sz, stepX=20, stepZ=20, target_regs=_en_regions)
            if getattr(self, '_training_mode', False):
                for _bname, _bcd, _bteam in getattr(self, '_training_bots', []) or []:
                    try:
                        _team = int(_bteam)
                    except Exception:
                        _team = self._enemy_team
                    if not _bcd:
                        continue
                    try:
                        _bd = vehicles.VehicleDescr(compactDescr=_bcd)
                    except Exception:
                        continue
                    _regs = _en_regions if _team == self._enemy_team else _my_regions
                    sx, sz = _regs[0]
                    _spawn_bots(_bd.type.name, team=_team, count=1, startX=sx, startZ=sz, stepX=25, stepZ=25, target_regs=_my_regions)
            else:
                for (sx, sz), cnt in zip(_en_regions, _split_evenly(botCount + 1, len(_en_regions))):
                    if cnt > 0: _spawn_bots(None, team=self._enemy_team, count=cnt, startX=sx, startZ=sz, stepX=25, stepZ=25, target_regs=_my_regions)

        try:
            roster = []
            extra = {}
            for vId, vData in self.arena.vehicles.items():
                extra[vId] = {
                    'health': vData.get('health'),
                    'frags': vData.get('frags', 0),
                    'clanAbbrev': vData.get('clanAbbrev') or '',
                    'vehicleID': vId,
                    'name': vData.get('name'),
                }
                roster.append(_arena_vehicle_info_tuple(
                    vId, vData['vehicleType'].makeCompactDescr(), vData['name'], vData['team'],
                    vData.get('isAlive', True), vData.get('isAvatarReady', True),
                    vData.get('accountDBID', 0), vData.get('prebattleID', 0) or (
                        self._prebattle_id if (
                            vData.get('name') == self.playerAvatar.name or
                            vData.get('name') in [x[0] for x in (getattr(self, '_squad_spawn_extra', []) or [])]
                        ) else 0)))
            if roster:
                self.arena.update(constants.ARENA_UPDATE_VEHICLE_LIST, cPickle.dumps(roster))
                for vId, fields in extra.items():
                    if vId in self.arena.vehicles:
                        self.arena.vehicles[vId].update(fields)
                stats = [(vId, extra[vId].get('frags') or 0) for vId in extra]
                self.arena.update(constants.ARENA_UPDATE_STATISTICS, cPickle.dumps(stats))
                LOG_NOTE("[BATTLE] arena roster published: %d vehicles" % len(roster))
        except Exception:
            LOG_CURRENT_EXCEPTION()

        def _refresh_prebattle_roster():
            try:
                from Offline import Manager as _Mgr
                vehicles_info = []
                for vehID, descr, isPlayer in self.vehicles:
                    try:
                        vname = self.playerAvatar.name if isPlayer else self.arena.vehicles.get(vehID, {}).get('name', 'Bot')
                        vehicles_info.append((vname, descr.makeCompactDescr()))
                    except Exception: pass
                _Mgr.refresh_prebattle_roster(vehicles_info)
            except Exception: pass

        _refresh_prebattle_roster()
        for _delay in (0.5, 1.0, 2.0, 3.5, 5.0): BigWorld.callback(_delay, _refresh_prebattle_roster)

        prereqs = []
        for vehID, descr, _ in self.vehicles:
            for state in ('undamaged', 'destroyed', 'exploded'):
                prereqs.append(descr.chassis['models'].get(state, descr.chassis['models']['undamaged']))
                prereqs.append(descr.hull['models'].get(state, descr.hull['models']['undamaged']))
                prereqs.append(descr.turret['models'].get(state, descr.turret['models']['undamaged']))
                prereqs.append(descr.gun['models'].get(state, descr.gun['models']['undamaged']))
            prereqs += descr.prerequisites()

        from Settings import g_instance as settings
        fakeModel = settings.scriptConfig.readString('fakeModel', 'objects/fake_model.model')
        prereqs.append(fakeModel)

        self._patch_startVisual()
        BigWorld.loadResourceListBG(prereqs, partial(self._onResourcesLoaded))


    def _onResourcesLoaded(self, resourceRefs):
        BigWorld.callback(0.1, lambda: self._finalizeInit(resourceRefs))

    def _applyAimPatches(self):
        try:
            from AvatarInputHandler import aims
            if not aims._g_aimState: aims.clearState()
            max_health = 200
            if self.playerAvatar.vehicleTypeDescriptor: max_health = self.playerAvatar.vehicleTypeDescriptor.maxHealth
            aims._g_aimState['health']['cur'] = max_health
            aims._g_aimState['health']['max'] = max_health
            aims._g_aimState['reload']['isReloading'] = False
            aims._g_aimState['reload']['duration'] = 0
            aims._g_aimState['reload']['startTime'] = None
            aims._g_aimState['ammoStock'] = 0
            original_setHealth = aims.Aim._setHealth
            def safe_setHealth(self, cur, max):
                if cur is None or max is None or max == 0: return
                original_setHealth(self, cur, max)
            aims.Aim._setHealth = safe_setHealth
        except Exception: pass

        try:
            from AvatarInputHandler import aims as _aims_mod
            _aims_mod._offline_battle_ref = self
            _CruiseCtrl = _aims_mod._CruiseCtrl
            if not hasattr(_CruiseCtrl, '_offline_patched_updateSpeed'):
                _orig_cc_updateSpeed = _CruiseCtrl.updateSpeed
                def _offline_cc_updateSpeed(self_cc, speed):
                    _ob = getattr(_aims_mod, '_offline_battle_ref', None)
                    if _ob is not None and not getattr(_ob, '_is_finished', True):
                        try: speed = _ob._cur_speed
                        except Exception: pass
                    _orig_cc_updateSpeed(self_cc, speed)
                _CruiseCtrl.updateSpeed = _offline_cc_updateSpeed
                _CruiseCtrl._offline_patched_updateSpeed = True
            try:
                if hasattr(_CruiseCtrl, 'setCruiseMode') and not hasattr(_CruiseCtrl, '_offline_patched_cruiseMode'):
                    _orig_cc_setMode = _CruiseCtrl.setCruiseMode
                    def _offline_cc_setMode(self_cc, newMode):
                        _ob = getattr(_aims_mod, '_offline_battle_ref', None)
                        if _ob is not None and not getattr(_ob, '_is_finished', True):
                            try:
                                _act = getattr(getattr(_ob, 'playerAvatar', None), '_cruise_active', False)
                                import Avatar as _AvMod
                                if _act:
                                    _av2 = getattr(_ob, 'playerAvatar', None)
                                    _modeIdx = getattr(_av2, '_cruise_mode', _CRUISE_MODE_DEFAULT)
                                    try:
                                        _cModeName, _mDir, _mFrac = _CRUISE_MODES[_modeIdx]
                                    except Exception:
                                        _cModeName = 'CRUISE_CONTROL_MODE_FWD100'
                                    newMode = getattr(_AvMod, _cModeName, newMode)
                                else:
                                    newMode = getattr(_AvMod, 'CRUISE_CONTROL_MODE_NONE', newMode)
                            except Exception: pass
                        _orig_cc_setMode(self_cc, newMode)
                    _CruiseCtrl.setCruiseMode = _offline_cc_setMode
                    _CruiseCtrl._offline_patched_cruiseMode = True
            except Exception: pass
            try:
                if hasattr(_CruiseCtrl, 'updateTarget') and not hasattr(_CruiseCtrl, '_offline_patched_updateTarget'):
                    _orig_cc_updateTarget = _CruiseCtrl.updateTarget
                    def _offline_cc_updateTarget(self_cc, target):
                        _ob = getattr(_aims_mod, '_offline_battle_ref', None)
                        if _ob is not None and not getattr(_ob, '_is_finished', True):
                            try:
                                _spd = getattr(_ob, '_cur_speed', 0.0) or 0.0
                                _av = getattr(_ob, 'playerAvatar', None)
                                _tgt = 0.0
                                if _av is not None:
                                    try: _tgt = getattr(_av, '_cruise_target', 0.0) or 0.0
                                    except Exception: pass
                                if getattr(_av, '_cruise_active', False):
                                    target = int(_tgt * 3.6)
                                else:
                                    target = int(abs(_spd) * 3.6)
                            except Exception: pass
                        _orig_cc_updateTarget(self_cc, target)
                    _CruiseCtrl.updateTarget = _offline_cc_updateTarget
                    _CruiseCtrl._offline_patched_updateTarget = True
            except Exception: pass
        except Exception: pass

    def _restore_matrix(self):
        try:
            if hasattr(self, '_offline_matrix') and hasattr(self, '_cur_yaw'):
                self._offline_matrix.setRotateYPR((self._cur_yaw, getattr(self, '_cur_pitch', 0.0), getattr(self, '_cur_roll', 0.0)))
                self._offline_matrix.translation = self._cur_pos
        except: pass

    def _patch_control_modes(self):
        try:
            arcade_ctrl = self.playerAvatar.inputHandler._AvatarInputHandler__ctrls.get('arcade')
            if arcade_ctrl is None: return
            arcade_ctrl._ArcadeControlMode__activateAlternateMode = lambda *a, **kw: None
            arcade_ctrl.onChangeControlMode = lambda *args, **kwargs: None
            if hasattr(arcade_ctrl, '_ArcadeControlMode__cam'):
                cam = arcade_ctrl._ArcadeControlMode__cam
                if hasattr(cam, '_ArcadeCamera__onChangeControlMode'): cam._ArcadeCamera__onChangeControlMode = lambda *a, **kw: None
        except Exception: return

        import CommandMapping

        def _enter_alternate_mode():
            try:
                avatar = BigWorld.player()
                aih = avatar.inputHandler
                veh = BigWorld.entity(avatar.playerVehicleID)
                descr = avatar.vehicleTypeDescriptor
                isSPG = descr and ('SPG' in descr.type.tags)
                isATSPG = descr and ('AT-SPG' in descr.type.tags)

                shotPoint = None
                try: shotPoint = aih.getDesiredShotPoint()
                except: pass

                try:
                    _acam = arcade_ctrl._ArcadeControlMode__cam
                    _cdir = _acam.camera.direction
                    _ppos = avatar.getOwnVehiclePosition()
                    _look = _ppos + _cdir * 280.0
                    _fwd = Math.Vector3(_math.sin(getattr(self, '_cur_yaw', 0.0)), 0.0, _math.cos(getattr(self, '_cur_yaw', 0.0)))
                    if shotPoint is None:
                        shotPoint = _look
                    else:
                        _to = shotPoint - _ppos
                        if (_to.x * _fwd.x + _to.z * _fwd.z) < 0.0 and (_cdir.x * _fwd.x + _cdir.z * _fwd.z) > 0.0:
                            shotPoint = _look
                except Exception:
                    pass

                if shotPoint is None and veh:
                    try:
                        _om = getattr(self, '_offline_matrix', None) or getattr(veh, '_offline_matrix', None)
                        if _om is not None:
                            fwd = _om.applyVector(Math.Vector3(0, 0, 1))
                            shotPoint = _om.translation + fwd * 300.0
                        else:
                            fwd = Math.Matrix(veh.matrix).applyVector(Math.Vector3(0, 0, 1))
                            shotPoint = veh.position + fwd * 300.0
                    except: shotPoint = veh.position + Math.Vector3(0, 0, 300)

                if shotPoint is None: return
                if veh and avatar._ownVehicleMProv.target is None: avatar._ownVehicleMProv.target = _vehicle_matrix_provider(veh) or veh.matrix

                _saved_bind = avatar.bindToVehicle
                def _offline_bind(doBind, vehID=None):
                    if doBind and veh: avatar._ownVehicleMProv.target = _vehicle_matrix_provider(veh) or veh.matrix
                avatar.bindToVehicle = _offline_bind
                try:
                    if isSPG:
                        aih.onControlModeChanged('strategic', preferredPos=shotPoint, aimingMode=0, saveDist=False)
                    else:
                        try:
                            sniper_ctrl = aih._AvatarInputHandler__ctrls.get('sniper')
                            if sniper_ctrl:
                                _scam = sniper_ctrl._SniperControlMode__cam
                                _scam._USE_SWINGING = False
                                _scam._USE_ALIGN_TO_VEHICLE = False
                        except: pass
                        aih.onControlModeChanged('sniper', preferredPos=shotPoint, aimingMode=0, saveZoom=False, isATSPG=isATSPG)
                finally:
                    avatar.bindToVehicle = _saved_bind
                    BigWorld.callback(0.1, self._restore_matrix)
            except Exception: pass

        original_handleKey = arcade_ctrl.handleKeyEvent
        def patched_handleKeyEvent(isDown, key, mods, event=None):
            cmdMap = CommandMapping.g_instance
            avatar = BigWorld.player()
            try:
                if _offline_try_chat_shortcuts(avatar, isDown, key, mods):
                    return True
            except Exception:
                pass

            moved = False
            if cmdMap.isFired(CommandMapping.CMD_MOVE_FORWARD, key): avatar._moveForward = isDown; moved = True
            if cmdMap.isFired(CommandMapping.CMD_MOVE_BACKWARD, key): avatar._moveBack = isDown; moved = True
            if cmdMap.isFired(CommandMapping.CMD_ROTATE_LEFT, key): avatar._turnLeft = isDown; moved = True
            if cmdMap.isFired(CommandMapping.CMD_ROTATE_RIGHT, key): avatar._turnRight = isDown; moved = True

            if moved:
                if isDown:
                    try:
                        if cmdMap.isFired(CommandMapping.CMD_MOVE_FORWARD, key) or cmdMap.isFired(CommandMapping.CMD_MOVE_BACKWARD, key):
                            _player_cruise_cancel(avatar)
                    except Exception: pass
                avatar.currentMove = (1.0 if avatar._moveForward else 0.0) - (1.0 if avatar._moveBack else 0.0)
                avatar.currentTurn = (1.0 if avatar._turnRight else 0.0) - (1.0 if avatar._turnLeft else 0.0)
                return True
            if cmdMap.isFiredList(xrange(CommandMapping.CMD_AMMO_CHOICE_1, CommandMapping.CMD_AMMO_CHOICE_0 + 1), key) and isDown and mods == 0:
                try:
                    bw = getattr(g_windowsManager, 'battleWindow', None)
                    if bw and hasattr(bw, 'ammoPanel'): bw.ammoPanel.handleKey(key)
                    else: avatar.onAmmoButtonPressed(key - Keys.KEY_1)
                except Exception: pass
                return True
            if cmdMap.isFired(CommandMapping.CMD_CM_SHOOT, key) and isDown:
                avatar.shoot()
                return True
            if cmdMap.isFired(CommandMapping.CMD_CM_ALTERNATE_MODE, key) and isDown:
                _enter_alternate_mode()
                return True
            if cmdMap.isFired(CommandMapping.CMD_VEHICLE_MARKERS_SHOW_INFO, key):
                try:
                    _bw = getattr(g_windowsManager, 'battleWindow', None)
                    _vmm = getattr(_bw, 'vMarkersManager', None) if _bw is not None else None
                    if _vmm is not None and hasattr(_vmm, 'showExtendedInfo'):
                        _vmm.showExtendedInfo(isDown)
                except Exception: pass
                return True
            try:
                if key == Keys.KEY_R and isDown:
                    _player_cruise_toggle(avatar)
                    return True
            except Exception: pass
            try:
                _cc_cmd = getattr(CommandMapping, 'CMD_CRUISE_CONTROL', None)
                if _cc_cmd is not None and cmdMap.isFired(_cc_cmd, key) and isDown:
                    _player_cruise_toggle(avatar)
                    return True
            except Exception: pass
            if cmdMap.isFired(CommandMapping.CMD_INCREMENT_CRUISE_MODE, key) and isDown:
                _player_cruise_step(avatar, 0.25)
                return True
            if cmdMap.isFired(CommandMapping.CMD_DECREMENT_CRUISE_MODE, key) and isDown:
                _player_cruise_step(avatar, -0.25)
                return True
            return original_handleKey(isDown, key, mods, event)

        arcade_ctrl.handleKeyEvent = patched_handleKeyEvent
        original_mouse = arcade_ctrl.handleMouseEvent
        def patched_handleMouseEvent(dx, dy, dz):
            result = original_mouse(dx, dy, dz)
            avatar = BigWorld.player()
            if dz > 0:
                try:
                    cam = arcade_ctrl._ArcadeControlMode__cam
                    distRange = cam._ArcadeCamera__cfg['distRange']
                    camDist = cam._ArcadeCamera__camDist
                    if camDist <= distRange[0] + 1e-06:
                        _enter_alternate_mode()
                        return True
                except Exception: pass
            gr = getattr(avatar, 'gunRotator', None)
            if gr:
                try: camDir = arcade_ctrl._ArcadeControlMode__cam.camera.direction
                except: pass
            return result
        arcade_ctrl.handleMouseEvent = patched_handleMouseEvent

        import AvatarInputHandler.control_modes as _cm
        _orig_sniper_enable = _cm.SniperControlMode.enable
        def _safe_sniper_enable(self_ctrl, **args):
            try: _orig_sniper_enable(self_ctrl, **args)
            except Exception: self_ctrl._SniperControlMode__isEnabled = True
            try: self_ctrl._SniperControlMode__cam._SniperCamera__cam.spaceID = BigWorld.player().spaceID
            except Exception: pass
        _cm.SniperControlMode.enable = _safe_sniper_enable

        def _exit_sniper_to_arcade(self_ctrl):
            try:
                aih = BigWorld.player().inputHandler
                try: shotPoint = self_ctrl.getDesiredShotPoint()
                except: shotPoint = None
                vehID = BigWorld.player().playerVehicleID
                veh = BigWorld.entity(vehID) if vehID else None
                if veh and BigWorld.player()._ownVehicleMProv.target is None:
                    BigWorld.player()._ownVehicleMProv.target = _vehicle_matrix_provider(veh) or veh.matrix
                _saved_bind = BigWorld.player().bindToVehicle
                def _offline_bind_back(doBind, vid=None):
                    if doBind and veh: BigWorld.player()._ownVehicleMProv.target = _vehicle_matrix_provider(veh) or veh.matrix
                BigWorld.player().bindToVehicle = _offline_bind_back
                try: aih.onControlModeChanged('arcade', preferredPos=shotPoint, yaw=0.0, aimingMode=0, closesDist=False)
                finally: BigWorld.player().bindToVehicle = _saved_bind
            except Exception: pass

        _orig_sniper_mouse = _cm.SniperControlMode.handleMouseEvent
        def _safe_sniper_mouse(self_ctrl, dx, dy, dz):
            if not self_ctrl._SniperControlMode__isEnabled: return False
            if dz < 0:
                try:
                    cam = self_ctrl._SniperControlMode__cam
                    minZoom = cam._SniperCamera__cfg['zooms'][0]
                    curZoom = cam._SniperCamera__zoom
                    if curZoom <= minZoom + 1e-06:
                        _exit_sniper_to_arcade(self_ctrl)
                        return True
                except Exception: pass
            return _orig_sniper_mouse(self_ctrl, dx, dy, dz)
        _cm.SniperControlMode.handleMouseEvent = _safe_sniper_mouse

        import CommandMapping as _cmdMap_sniper
        _orig_sniper_key = _cm.SniperControlMode.handleKeyEvent
        def _safe_sniper_key(self_ctrl, isDown, key, mods, event=None):
            if not self_ctrl._SniperControlMode__isEnabled: return False
            _cm2 = _cmdMap_sniper.g_instance
            if _cm2.isFired(_cmdMap_sniper.CMD_CM_SHOOT, key) and isDown:
                BigWorld.player().shoot()
                return True
            if _cm2.isFired(_cmdMap_sniper.CMD_CM_ALTERNATE_MODE, key) and isDown:
                _exit_sniper_to_arcade(self_ctrl)
                return True
            if _cmdMap_sniper.g_instance.isFired(_cmdMap_sniper.CMD_VEHICLE_MARKERS_SHOW_INFO, key):
                try:
                    _bw = getattr(g_windowsManager, 'battleWindow', None)
                    _vmm = getattr(_bw, 'vMarkersManager', None) if _bw is not None else None
                    if _vmm is not None and hasattr(_vmm, 'showExtendedInfo'):
                        _vmm.showExtendedInfo(isDown)
                except Exception: pass
                return True

            avatar = BigWorld.player()
            try:
                if _offline_try_chat_shortcuts(avatar, isDown, key, mods):
                    return True
            except Exception:
                pass
            moved = False
            if _cm2.isFired(_cmdMap_sniper.CMD_MOVE_FORWARD, key): avatar._moveForward = isDown; moved = True
            if _cm2.isFired(_cmdMap_sniper.CMD_MOVE_BACKWARD, key): avatar._moveBack = isDown; moved = True
            if _cm2.isFired(_cmdMap_sniper.CMD_ROTATE_LEFT, key): avatar._turnLeft = isDown; moved = True
            if _cm2.isFired(_cmdMap_sniper.CMD_ROTATE_RIGHT, key): avatar._turnRight = isDown; moved = True
            if moved:
                if isDown:
                    try:
                        if _cm2.isFired(_cmdMap_sniper.CMD_MOVE_FORWARD, key) or _cm2.isFired(_cmdMap_sniper.CMD_MOVE_BACKWARD, key):
                            _player_cruise_cancel(avatar)
                    except Exception: pass
                avatar.currentMove = (1.0 if avatar._moveForward else 0.0) - (1.0 if avatar._moveBack else 0.0)
                avatar.currentTurn = (1.0 if avatar._turnRight else 0.0) - (1.0 if avatar._turnLeft else 0.0)
                return True

            try:
                if key == Keys.KEY_R and isDown:
                    _player_cruise_toggle(avatar)
                    return True
            except Exception: pass
            try:
                _cc_cmd = getattr(_cmdMap_sniper, 'CMD_CRUISE_CONTROL', None)
                if _cc_cmd is not None and _cm2.isFired(_cc_cmd, key) and isDown:
                    _player_cruise_toggle(avatar)
                    return True
            except Exception: pass
            if _cm2.isFired(_cmdMap_sniper.CMD_INCREMENT_CRUISE_MODE, key) and isDown:
                _player_cruise_step(avatar, 0.25)
                return True
            if _cm2.isFired(_cmdMap_sniper.CMD_DECREMENT_CRUISE_MODE, key) and isDown:
                _player_cruise_step(avatar, -0.25)
                return True

            if _cm2.isFiredList(xrange(CommandMapping.CMD_AMMO_CHOICE_1, CommandMapping.CMD_AMMO_CHOICE_0 + 1), key) and isDown and mods == 0:
                try:
                    bw = getattr(g_windowsManager, 'battleWindow', None)
                    if bw and hasattr(bw, 'ammoPanel'): bw.ammoPanel.handleKey(key)
                    else: avatar.onAmmoButtonPressed(key - Keys.KEY_1)
                except Exception: pass
                return True
            return _orig_sniper_key(self_ctrl, isDown, key, mods, event)
        _cm.SniperControlMode.handleKeyEvent = _safe_sniper_key

        _orig_sniper_marker = _cm.SniperControlMode.showGunMarker
        def _safe_sniper_marker(self_ctrl, flag):
            if not self_ctrl._SniperControlMode__isEnabled: return
            return _orig_sniper_marker(self_ctrl, flag)
        _cm.SniperControlMode.showGunMarker = _safe_sniper_marker

        _orig_strategic_enable = _cm.StrategicControlMode.enable
        def _safe_strategic_enable(self_ctrl, **args):
            try: _orig_strategic_enable(self_ctrl, **args)
            except Exception: self_ctrl._StrategicControlMode__isEnabled = True
            try: self_ctrl._StrategicControlMode__cam._StrategicCamera__cam.spaceID = BigWorld.player().spaceID
            except Exception: pass
            try:
                BigWorld.wg_enableTreeHiding(False)
                BigWorld.projection().farPlane = 1000.0
                BigWorld.projection().nearPlane = 0.25
            except: pass
            try:
                orig_updateTraj = self_ctrl.__class__.updateTrajectory
                def _safe_updateTrajectory(self_c):
                    try:
                        if not self_c._StrategicControlMode__isEnabled: return
                        orig_updateTraj(self_c)
                    except Exception: pass
                self_ctrl.__class__.updateTrajectory = _safe_updateTrajectory
            except Exception: pass
            try:
                if not hasattr(BigWorld.player(), 'moveTo'):
                    BigWorld.player().__class__.moveTo = lambda self, pos: setattr(self, '_strategicTargetPos', pos)
            except: pass
        _cm.StrategicControlMode.enable = _safe_strategic_enable

        _orig_strategic_key = _cm.StrategicControlMode.handleKeyEvent
        def _safe_strategic_key(self_ctrl, isDown, key, mods, event=None):
            if not self_ctrl._StrategicControlMode__isEnabled: return False
            _cm2 = _cmdMap_sniper.g_instance
            if _cm2.isFired(_cmdMap_sniper.CMD_CM_SHOOT, key) and isDown:
                BigWorld.player().shoot()
                return True
            if _cm2.isFired(_cmdMap_sniper.CMD_CM_SWITCH_TRAJECTORY, key) and isDown:
                try: BigWorld.player().gunRotator.switchLoftedTrajectory()
                except Exception: pass
                return True
            if _cmdMap_sniper.g_instance.isFired(_cmdMap_sniper.CMD_VEHICLE_MARKERS_SHOW_INFO, key):
                try:
                    _bw = getattr(g_windowsManager, 'battleWindow', None)
                    _vmm = getattr(_bw, 'vMarkersManager', None) if _bw is not None else None
                    if _vmm is not None and hasattr(_vmm, 'showExtendedInfo'):
                        _vmm.showExtendedInfo(isDown)
                except Exception: pass
                return True
            if _cm2.isFired(_cmdMap_sniper.CMD_CM_ALTERNATE_MODE, key) and isDown:
                try:
                    vehID = BigWorld.player().playerVehicleID
                    veh = BigWorld.entity(vehID) if vehID else None
                    _saved_bind = BigWorld.player().bindToVehicle
                    def _back_bind(doBind, vid=None):
                        if doBind and veh: BigWorld.player()._ownVehicleMProv.target = _vehicle_matrix_provider(veh) or veh.matrix
                    BigWorld.player().bindToVehicle = _back_bind
                    try: BigWorld.player().inputHandler.onControlModeChanged('arcade', preferredPos=None, yaw=0.0, aimingMode=0, closesDist=False)
                    finally: BigWorld.player().bindToVehicle = _saved_bind
                except Exception: pass
                return True
            avatar = BigWorld.player()
            try:
                if _offline_try_chat_shortcuts(avatar, isDown, key, mods):
                    return True
            except Exception:
                pass
            moved = False
            if _cm2.isFired(_cmdMap_sniper.CMD_MOVE_FORWARD, key): avatar._moveForward = isDown; moved = True
            if _cm2.isFired(_cmdMap_sniper.CMD_MOVE_BACKWARD, key): avatar._moveBack = isDown; moved = True
            if _cm2.isFired(_cmdMap_sniper.CMD_ROTATE_LEFT, key): avatar._turnLeft = isDown; moved = True
            if _cm2.isFired(_cmdMap_sniper.CMD_ROTATE_RIGHT, key): avatar._turnRight = isDown; moved = True
            if moved:
                if isDown:
                    try:
                        if _cm2.isFired(_cmdMap_sniper.CMD_MOVE_FORWARD, key) or _cm2.isFired(_cmdMap_sniper.CMD_MOVE_BACKWARD, key):
                            _player_cruise_cancel(avatar)
                    except Exception: pass
                avatar.currentMove = (1.0 if avatar._moveForward else 0.0) - (1.0 if avatar._moveBack else 0.0)
                avatar.currentTurn = (1.0 if avatar._turnRight else 0.0) - (1.0 if avatar._turnLeft else 0.0)
                return True

            try:
                if key == Keys.KEY_R and isDown:
                    _player_cruise_toggle(avatar)
                    return True
            except Exception: pass
            try:
                _cc_cmd = getattr(_cmdMap_sniper, 'CMD_CRUISE_CONTROL', None)
                if _cc_cmd is not None and _cm2.isFired(_cc_cmd, key) and isDown:
                    _player_cruise_toggle(avatar)
                    return True
            except Exception: pass
            if _cm2.isFired(_cmdMap_sniper.CMD_INCREMENT_CRUISE_MODE, key) and isDown:
                _player_cruise_step(avatar, 0.25)
                return True
            if _cm2.isFired(_cmdMap_sniper.CMD_DECREMENT_CRUISE_MODE, key) and isDown:
                _player_cruise_step(avatar, -0.25)
                return True

            if _cm2.isFiredList(xrange(CommandMapping.CMD_AMMO_CHOICE_1, CommandMapping.CMD_AMMO_CHOICE_0 + 1), key) and isDown and mods == 0:
                try:
                    bw = getattr(g_windowsManager, 'battleWindow', None)
                    if bw and hasattr(bw, 'ammoPanel'): bw.ammoPanel.handleKey(key)
                    else: avatar.onAmmoButtonPressed(key - Keys.KEY_1)
                except Exception: pass
                return True
            return _orig_strategic_key(self_ctrl, isDown, key, mods, event)
        _cm.StrategicControlMode.handleKeyEvent = _safe_strategic_key

        _orig_strategic_mouse = _cm.StrategicControlMode.handleMouseEvent
        def _safe_strategic_mouse(self_ctrl, dx, dy, dz):
            if not self_ctrl._StrategicControlMode__isEnabled: return False
            return _orig_strategic_mouse(self_ctrl, dx, dy, dz)
        _cm.StrategicControlMode.handleMouseEvent = _safe_strategic_mouse

        _orig_strategic_marker = _cm.StrategicControlMode.showGunMarker
        def _safe_strategic_marker(self_ctrl, flag):
            if not self_ctrl._StrategicControlMode__isEnabled: return
            return _orig_strategic_marker(self_ctrl, flag)
        _cm.StrategicControlMode.showGunMarker = _safe_strategic_marker

        from VehicleGunRotator import VehicleGunRotator as _VGR
        def _safe_updateShotPoint(self_gr, shotPoint): self_gr._VehicleGunRotator__prevSentShotPoint = shotPoint
        _VGR._VehicleGunRotator__updateShotPointOnServer = _safe_updateShotPoint
        _orig_onTick = _VGR._VehicleGunRotator__onTick
        _tick_err_count = [0]
        def _safe_onTick(self_gr):
            try: _orig_onTick(self_gr)
            except Exception: _tick_err_count[0] += 1
        _VGR._VehicleGunRotator__onTick = _safe_onTick

        _orig_rotate = _VGR._VehicleGunRotator__rotate
        def _safe_rotate(self_gr, shotPoint, timeDiff):
            try:
                _ah = getattr(self_gr._VehicleGunRotator__avatar, 'inputHandler', None)
                if _ah is not None and not getattr(_ah, '_AvatarInputHandler__isArenaStarted', True): shotPoint = None
            except Exception: pass
            try:
                _av = self_gr._VehicleGunRotator__avatar
                _lid = getattr(_av, '_lockedOnVehID', 0)
                if _lid:
                    _locked = BigWorld.entity(_lid)
                    _ob = getattr(_av, '_offline_battle', None)
                    _lost = _locked is None or not _vehicle_is_alive(_locked)
                    if not _lost and _ob is not None:
                        if _lid in getattr(_ob, '_spot_hidden_models', ()):
                            _lost = True
                        else:
                            _sp = getattr(_ob, '_spotted_vehicles', None)
                            if _sp is not None and _lid not in _sp:
                                _lost = True
                    if _lost:
                        _av.onLockedVehicleLost()
                    else:
                        shotPoint = _entity_world_pos(_locked)
                        shotPoint.y += 1.15
            except Exception:
                pass
            return _orig_rotate(self_gr, shotPoint, timeDiff)
        _VGR._VehicleGunRotator__rotate = _safe_rotate

        try:
            from AvatarInputHandler.aims import Aim as _AimHUD
            if not getattr(_AimHUD, '_offline_aim_v5_patched', False):
                _orig_aim_upd = _AimHUD._update
                def _offline_aim_update(self_aim):
                    try:
                        _orig_aim_upd(self_aim)
                    except Exception:
                        pass
                    try:
                        _pl = BigWorld.player()
                        _ob = getattr(_pl, '_offline_battle', None) if _pl is not None else None
                        if _ob is not None and getattr(self_aim, '_Aim__cruiseCtrl', None) is not None:
                            self_aim._Aim__cruiseCtrl.updateSpeed(float(getattr(_ob, '_cur_speed', 0.0) or 0.0))
                    except Exception:
                        pass
                    try:
                        from AvatarInputHandler import aims as _aims_mod
                        _st = _aims_mod._g_aimState['target']
                        _tid = _st.get('id')
                        if _tid is not None:
                            _targ = BigWorld.entity(_tid)
                            if _targ is None:
                                try:
                                    self_aim.clearTarget()
                                except Exception:
                                    pass
                            else:
                                _own = BigWorld.player().getOwnVehiclePosition()
                                _tp = _entity_world_pos(_targ)
                                _st['dist'] = int((_tp - _own).length)
                                if _vehicle_is_alive(_targ):
                                    _mh = float(_targ.typeDescriptor.maxHealth) or 1.0
                                    _st['health'] = int(_math.ceil(100.0 * max(0, _vehicle_health(_targ)) / _mh))
                                else:
                                    _st['health'] = 0
                                try:
                                    self_aim._flashCall('updateTarget', [_st['dist'], _st['health']])
                                except Exception:
                                    pass
                    except Exception:
                        pass
                _AimHUD._update = _offline_aim_update
                _AimHUD._offline_aim_v5_patched = True
                try:
                    _orig_set_tgt = _AimHUD.setTarget
                    def _offline_set_target(self_aim, target):
                        try:
                            from helpers import i18n as _i18n
                            from AvatarInputHandler import aims as _aims_mod
                            _st = _aims_mod._g_aimState['target']
                            _st['id'] = target.id
                            _st['startTime'] = None
                            _name = _i18n.convert(target.publicInfo['name'])
                            _vType = _i18n.convert(target.typeDescriptor.type.userString)
                            _friend = target.publicInfo['team'] == BigWorld.player().team
                            _st['name'] = _name
                            _st['vType'] = _vType
                            _st['isFriend'] = _friend
                            _alive = _vehicle_is_alive(target)
                            if not _alive:
                                _name = '%s [%s]' % (_name, 'X')
                                _st['health'] = 0
                            self_aim._flashCall('setTarget', [_name, _vType, _friend])
                            if not _alive:
                                self_aim._flashCall('updateTarget', [_st.get('dist', 0), 0])
                            return
                        except Exception:
                            pass
                        _orig_set_tgt(self_aim, target)
                    _AimHUD.setTarget = _offline_set_target
                except Exception:
                    pass
                try:
                    from AvatarInputHandler.aims import PostMortemAim as _PMA
                    if not getattr(_PMA, '_offline_pm_hp_v5', False):
                        _orig_pm_upd = _PMA._update
                        def _offline_pm_upd(self_aim):
                            try:
                                _orig_pm_upd(self_aim)
                            except Exception:
                                pass
                            try:
                                from AvatarInputHandler import aims as _aims_mod
                                _st = _aims_mod._g_aimState['target']
                                _tid = _st.get('id')
                                if _tid is not None:
                                    _targ = BigWorld.entity(_tid)
                                    if _targ is None:
                                        try:
                                            self_aim.clearTarget()
                                        except Exception:
                                            pass
                                    else:
                                        try:
                                            _own = BigWorld.player().getOwnVehiclePosition()
                                            _st['dist'] = int((_entity_world_pos(_targ) - _own).length)
                                        except Exception:
                                            pass
                                        if _vehicle_is_alive(_targ):
                                            _mh = float(_targ.typeDescriptor.maxHealth) or 1.0
                                            _st['health'] = int(_math.ceil(100.0 * max(0, _vehicle_health(_targ)) / _mh))
                                        else:
                                            _st['health'] = 0
                                        try:
                                            self_aim._flashCall('updateTarget', [_st['dist'], _st['health']])
                                        except Exception:
                                            pass
                                _vid = getattr(self_aim, '_PostMortemAim__vID', None)
                                if _vid is not None:
                                    _v = BigWorld.entity(_vid)
                                    if _v is not None:
                                        _mh = float(_v.typeDescriptor.maxHealth) or 1.0
                                        if _vehicle_is_alive(_v):
                                            _hp = int(_math.ceil(100.0 * max(0, _vehicle_health(_v)) / _mh))
                                        else:
                                            _hp = 0
                                        _name = _v.publicInfo['name']
                                        _type = _v.typeDescriptor.type.userString
                                        self_aim._PostMortemAim__setText(_name, _type, _hp)
                            except Exception:
                                pass
                        _PMA._update = _offline_pm_upd
                        _PMA._offline_pm_hp_v5 = True
                except Exception:
                    pass
        except Exception:
            pass

        try:
            from AvatarInputHandler.aims import _TankIndicatorCtrl as _TIC
            if not getattr(_TIC, '_offline_hud_mat_patched', False):
                _orig_ti_setup = _TIC._TankIndicatorCtrl__setup
                def _ti_setup_yaw_only(self_ti, appearance):
                    try:
                        _ob = getattr(BigWorld.player(), '_offline_battle', None)
                        _hm = getattr(_ob, '_hud_hull_mat', None) if _ob is not None else None
                        if _hm is None:
                            _hm = BigWorld.player().getOwnVehicleMatrix()
                        _tm = appearance.turretMatrix
                        _ui = getattr(self_ti, '_TankIndicatorCtrl__ui', None)
                        if _ui is not None:
                            _ti = _ui.component.tankIndicator
                            _ti.wg_hullMatProv = _hm
                            _ti.wg_turretMatProv = _tm
                            return
                    except Exception:
                        pass
                    return _orig_ti_setup(self_ti, appearance)
                _TIC._TankIndicatorCtrl__setup = _ti_setup_yaw_only
                _TIC._offline_hud_mat_patched = True
        except Exception:
            pass

        try:
            _orig_pm_enable = _cm.PostMortemControlMode.enable
            def _safe_pm_enable(self_ctrl, **args):
                try: _orig_pm_enable(self_ctrl, **args)
                except Exception: self_ctrl._PostMortemControlMode__isEnabled = True
                try:
                    pm_cam_bw = self_ctrl._PostMortemControlMode__cam.camera
                    pm_cam_bw.spaceID = BigWorld.player().spaceID
                    wreck_id = BigWorld.player().playerVehicleID
                    wreck = BigWorld.entity(wreck_id)
                    if wreck:
                        _battle = getattr(BigWorld.player(), '_offline_battle', None)
                        _pm_target = getattr(_battle, '_offline_matrix', None) if _battle is not None else None
                        if _pm_target is None:
                            _pm_target = getattr(wreck, '_offline_matrix', None)
                        pm_cam_bw.target = _pm_target
                except Exception:
                    LOG_WARNING("[BATTLE] postmortem: failed to aim camera at own wreck")

                try:
                    BigWorld.worldDrawEnabled(True)
                    BigWorld.wg_enableTreeHiding(False)
                except Exception: pass

                try:
                    BigWorld.projection().farPlane = 1000.0
                    BigWorld.projection().nearPlane = 0.25
                except Exception: pass

                try:
                    _show_dead_damage_panel()
                except Exception: pass
            _cm.PostMortemControlMode.enable = _safe_pm_enable

            def _pm_switch_step(self_ctrl, direction):
                try:
                    _vIDs = self_ctrl._PostMortemControlMode__vIDs
                    if not _vIDs: return
                    _cur = self_ctrl._PostMortemControlMode__curIndex
                    _n = len(_vIDs)
                    if _cur < 0 or _cur >= _n: _cur = 0
                    _new = (_cur + direction) % _n
                    self_ctrl._PostMortemControlMode__curIndex = _new
                    self_ctrl._PostMortemControlMode__curVehicleID = _vIDs[_new]
                    _player = BigWorld.player()
                    if _player is None: return
                    try: _player.bindToVehicle(True, _vIDs[_new])
                    except Exception: pass
                    try: self_ctrl._PostMortemControlMode__aim.changeVehicle(_vIDs[_new])
                    except Exception: pass
                    try:
                        if _vIDs[_new] == getattr(_player, 'playerVehicleID', None):
                            _mprov = _player.getOwnVehicleMatrix()
                        else:
                            _ent = BigWorld.entity(_vIDs[_new])
                            _mprov = _vehicle_matrix_provider(_ent) if _ent is not None else None
                        if _mprov is not None:
                            try: self_ctrl._PostMortemControlMode__cam.camera.target = _mprov
                            except Exception: pass
                    except Exception: pass
                except Exception:
                    pass

            _orig_pm_key = _cm.PostMortemControlMode.handleKeyEvent
            def _safe_pm_key(self_ctrl, isDown, key, mods, event=None):
                try:
                    if not getattr(self_ctrl, '_PostMortemControlMode__isEnabled', False):
                        return False
                    _cmd = CommandMapping.g_instance
                    _dir = None
                    if isDown and _cmd.isFired(CommandMapping.CMD_CM_SHOOT, key):
                        _dir = -1
                    elif isDown and _cmd.isFired(CommandMapping.CMD_CM_ALTERNATE_MODE, key):
                        _dir = 1
                    if _dir is not None:
                        if getattr(self_ctrl, '_PostMortemControlMode__postmortemDelay', None) is not None:
                            return True
                        _pm_switch_step(self_ctrl, _dir)
                        return True
                except Exception:
                    pass
                return _orig_pm_key(self_ctrl, isDown, key, mods, event)
            _cm.PostMortemControlMode.handleKeyEvent = _safe_pm_key
        except Exception: pass

    def _patch_decalmap(self):
        try:
            original_getIndex = DecalMap.getIndex
            def safe_getIndex(self, name):
                try:
                    idx = original_getIndex(self, name)
                    return idx if idx is not None and idx >= 0 else 0
                except Exception:
                    return 0
            DecalMap.getIndex = safe_getIndex
        except Exception: pass
        try:
            if hasattr(BigWorld, 'wg_addDecal'):
                original_addDecal = BigWorld.wg_addDecal
                def safe_addDecal(*args, **kwargs):
                    try: return original_addDecal(*args, **kwargs)
                    except Exception: return 0
                BigWorld.wg_addDecal = safe_addDecal
        except Exception: pass
        try:
            if hasattr(BigWorld, 'WGStickerModel'):
                original_addSticker = BigWorld.WGStickerModel.addSticker
                def safe_addSticker(self, layer, texCoords, model, start, end, sizes, up):
                    try: return original_addSticker(self, layer, texCoords, model, start, end, sizes, up)
                    except Exception: return 0
                BigWorld.WGStickerModel.addSticker = safe_addSticker
            if hasattr(BigWorld, 'WGVehicleFashion'):
                original_setTrackTraces = BigWorld.WGVehicleFashion.setTrackTraces
                def safe_setTrackTraces(self, group, textureIndex, centerOffset, size):
                    try: return original_setTrackTraces(self, group, textureIndex, centerOffset, size)
                    except Exception: return None
                BigWorld.WGVehicleFashion.setTrackTraces = safe_setTrackTraces
            try:
                from helpers import bound_effects
                if hasattr(bound_effects.ModelBoundEffects, 'addNew'):
                    original_addNew = bound_effects.ModelBoundEffects.addNew
                    def safe_addNew(self, mat, effects, stages, entity=None):
                        try: return original_addNew(self, mat, effects, stages, entity)
                        except Exception: pass
                    bound_effects.ModelBoundEffects.addNew = safe_addNew
            except: pass
        except Exception: pass

    def _patch_startVisual(self):
        import VehicleAppearance as VA
        if hasattr(VA.VehicleAppearance, '_patched_for_offline'): return
        def safe_setupVehicleFashion(fashion, vehicle, isCrashedTrack=False):
            try:
                vDesc       = vehicle.typeDescriptor
                tracesCfg   = vDesc.chassis['traces']
                tracksCfg   = vDesc.chassis['tracks']
                wheelsCfg   = vDesc.chassis['wheels']
                swingingCfg = vDesc.hull['swinging']

                fashion.movementInfo = vehicle.filter.movementInfo
                fashion.maxMovement  = vDesc.physics['speedLimits'][0]
                fashion.setPitchSwinging('V', *swingingCfg['pitchParams'])
                fashion.setRollSwinging('V', *swingingCfg['rollParams'])
                fashion.setShotSwinging('V', swingingCfg['sensitivityToImpulse'])
                fashion.setLods(tracesCfg['lodDist'], wheelsCfg['lodDist'], tracksCfg['lodDist'], swingingCfg['lodDist'])
                fashion.setTracks(tracksCfg['leftMaterial'], tracksCfg['rightMaterial'], tracksCfg['textureScale'])

                if not isCrashedTrack:
                    try:
                        from VehicleAppearance import _createWheelsListByTemplate
                        for group in wheelsCfg['groups']:
                            try:
                                nodes = _createWheelsListByTemplate(group[3], group[1], group[2])
                                fashion.addWheelGroup(group[0], group[4], nodes)
                            except Exception: pass
                        for wheel in wheelsCfg['wheels']:
                            try: fashion.addWheel(wheel[0], wheel[2], wheel[1])
                            except Exception: pass
                    except Exception: pass

                try:
                    _dgroup = tracesCfg['decalGroup']
                    BigWorld.wg_addDecalGroup(_dgroup, 30.0, 1000)
                except Exception: pass
                try:
                    _traceTexIdx = 0
                    try:
                        from helpers import DecalMap as _DecalMapModule
                        if _DecalMapModule.g_instance is not None:
                            _traceTexIdx = _DecalMapModule.g_instance.getIndex(tracesCfg['decalTexture'])
                            if _traceTexIdx is None or _traceTexIdx < 0: _traceTexIdx = 0
                    except Exception: _traceTexIdx = 0
                    fashion.setTrackTraces(tracesCfg['decalGroup'], _traceTexIdx, tracesCfg['centerOffset'], tracesCfg['size'])
                except Exception: pass
            except Exception: pass

        VA._setupVehicleFashion = safe_setupVehicleFashion

        original_updateMovement = VA.VehicleAppearance._VehicleAppearance__updateMovementSounds
        def safe_updateMovement(self_va):
            try: return original_updateMovement(self_va)
            except Exception: pass
        VA.VehicleAppearance._VehicleAppearance__updateMovementSounds = safe_updateMovement

        def _safe_getDamageModelsState(self, vehicleHealth):
            try:
                hp = _vehicle_health(self._VehicleAppearance__vehicle)
            except Exception:
                hp = vehicleHealth
            if hp > 0: return 'undamaged'
            elif hp == 0: return 'destroyed'
            else: return 'exploded'
        VA.VehicleAppearance._VehicleAppearance__getDamageModelsState = _safe_getDamageModelsState

        _orig_on_hp = VA.VehicleAppearance.onVehicleHealthChanged
        def _offline_on_hp_changed(self_va):
            try:
                _stamp_entity_pose(self_va._VehicleAppearance__vehicle)
            except Exception:
                pass
            return _orig_on_hp(self_va)
        VA.VehicleAppearance.onVehicleHealthChanged = _offline_on_hp_changed

        try:
            from helpers.EffectsList import EffectsListPlayer
        except Exception:
            EffectsListPlayer = None
        original_playEffect = VA.VehicleAppearance._VehicleAppearance__playEffect
        def safe_playEffect(self_va, kind, *modifs):
            _replaced = False
            _fx_base = None
            try:
                _pveh = self_va._VehicleAppearance__vehicle
                if _pveh is None:
                    return
                try:
                    _stamp_entity_pose(_pveh)
                except Exception:
                    pass
                try:
                    _fom = getattr(_pveh, '_offline_matrix', None)
                    if _fom is not None:
                        _fx_base = Math.Vector3(_fom.translation)
                except Exception: pass
                if _fx_base is None:
                    try: _fx_base = Math.Vector3(_pveh.position)
                    except Exception: return
                if EffectsListPlayer is not None:
                    if self_va._VehicleAppearance__effectsPlayer is not None:
                        _pprev = self_va._VehicleAppearance__effectsPlayer
                        try: _pprev.stop()
                        except Exception: pass
                        _pprev_model = getattr(_pprev, '_offline_fx_model', None)
                        if _pprev_model is not None:
                            try:
                                BigWorld.delModel(_pprev_model)
                                BigWorld.delAlwaysUpdateModel(_pprev_model)
                            except Exception: pass
                    _fx = _random.choice(_pveh.typeDescriptor.type.effects[kind])
                    _start_fx = _fx_base + Math.Vector3(0.0, -1.0, 0.0)
                    _end_fx   = _fx_base + Math.Vector3(0.0, 1.0, 0.0)
                    try:
                        _vom2fx = getattr(_pveh, '_offline_matrix', None)
                        _vm2fx = getattr(_pveh, 'model', None)
                        if _vm2fx is not None and _vom2fx is not None:
                            for _m2 in list(getattr(_vm2fx, 'motors', None) or []):
                                try: _vm2fx.delMotor(_m2)
                                except Exception: pass
                            try: _vm2fx.addMotor(BigWorld.Servo(_vom2fx))
                            except Exception: pass
                    except Exception: pass
                    _hull_mdl = None
                    _eff_model = None
                    try:
                        _pl_eff = BigWorld.player()
                        if _pl_eff is not None and hasattr(_pl_eff, 'newFakeModel'):
                            _eff_model = _pl_eff.newFakeModel()
                    except Exception:
                        _eff_model = None
                    if _eff_model is None:
                        try:
                            _eff_model = BigWorld.Model('')
                        except Exception:
                            _eff_model = None
                    if _eff_model is not None:
                        try:
                            _fx_mat = Math.Matrix()
                            _fx_mat.setTranslate(_fx_base)
                            self_va._offline_fx_world_mat = _fx_mat
                            try:
                                _eff_model.addMotor(BigWorld.Servo(_fx_mat))
                            except Exception:
                                pass
                            _eff_model.position = _fx_base
                            BigWorld.addModel(_eff_model)
                            try:
                                BigWorld.addAlwaysUpdateModel(_eff_model)
                            except Exception:
                                pass
                        except Exception:
                            try:
                                BigWorld.delModel(_eff_model)
                            except Exception:
                                pass
                            _eff_model = None
                    _play_on = _eff_model
                    if _play_on is None:
                        _play_on = self_va.modelsDesc.get('hull', {}).get('model', None)
                    if _play_on is not None:
                        _fxp = EffectsListPlayer(
                            _fx[1], _fx[0], entity=_pveh,
                            start=_start_fx, end=_end_fx)
                        if _eff_model is not None:
                            _fxp._offline_fx_model = _eff_model
                        self_va._VehicleAppearance__effectsPlayer = _fxp
                        _fx_start_stage = modifs[0] if modifs else None
                        def _fx_done(_fxp=_fxp, _eff_model=_eff_model):
                            try:
                                _fxp.stop()
                            except Exception:
                                pass
                            if _eff_model is not None:
                                try:
                                    BigWorld.delModel(_eff_model)
                                    BigWorld.delAlwaysUpdateModel(_eff_model)
                                except Exception:
                                    pass
                        try:
                            if _eff_model is not None:
                                _fxp.play(_play_on, _fx_start_stage, _fx_done)
                            else:
                                _fxp.play(_play_on, *modifs)
                            _replaced = True
                        except Exception:
                            try:
                                _fx_done()
                            except Exception:
                                pass
                            raise
            except Exception as _ee:
                _replaced = False
                try:
                    if _fx_base is not None:
                        LOG_WARNING("[BATTLE] fx '%s' play FAILED at %.1f %.1f %.1f, effect skipped: %r" % (kind, _fx_base[0], _fx_base[1], _fx_base[2], _ee))
                    else:
                        LOG_WARNING("[BATTLE] fx '%s' play FAILED (no base), effect skipped: %r" % (kind, _ee))
                except Exception: pass
            if not _replaced:
                try:
                    if _fx_base is not None:
                        LOG_WARNING("[BATTLE] fx '%s' SKIPPED (no _offline_matrix, entity pos %.1f %.1f %.1f)" % (kind, _fx_base[0], _fx_base[1], _fx_base[2]))
                    else:
                        LOG_WARNING("[BATTLE] fx '%s' SKIPPED (EffectsListPlayer missing)" % (kind,))
                except Exception: pass
        VA.VehicleAppearance._VehicleAppearance__playEffect = safe_playEffect

        original_stopEffects = VA.VehicleAppearance._VehicleAppearance__stopEffects
        def safe_stopEffects(self_va):
            try:
                _pp_stop = getattr(self_va, '_VehicleAppearance__effectsPlayer', None)
                _pm_stop = getattr(_pp_stop, '_offline_fx_model', None)
                if _pm_stop is not None:
                    try:
                        BigWorld.delModel(_pm_stop)
                        BigWorld.delAlwaysUpdateModel(_pm_stop)
                    except Exception: pass
            except Exception: pass
            try: return original_stopEffects(self_va)
            except Exception: pass
        VA.VehicleAppearance._VehicleAppearance__stopEffects = safe_stopEffects

        original_setup = VA.VehicleAppearance._VehicleAppearance__setupModels
        def safe_setupModels(self_va):
            try:
                for _comp in ('gun', 'chassis', 'hull', 'turret'):
                    _desc = self_va.modelsDesc.get(_comp, {})
                    for _key in ('model', '_fetchedModel'):
                        _m = _desc.get(_key, None)
                        if _m is not None and not hasattr(_m, 'wg_gunRecoil'):
                            try: _m.wg_gunRecoil = None
                            except: pass
            except: pass
            try:
                veh_inner = self_va._VehicleAppearance__vehicle
                chassis_desc = self_va.modelsDesc.get('chassis', {})
                if getattr(veh_inner, 'model', None) is None:
                    fetched = chassis_desc.get('_fetchedModel', None)
                    if fetched is not None: veh_inner.model = fetched
                gm = self_va.modelsDesc.get('gun', {}).get('model', None)
                if gm is not None and not hasattr(gm, 'wg_gunRecoil'):
                    try: gm.wg_gunRecoil = None
                    except: pass
                state = self_va._VehicleAppearance__curDamageState
                if state in ('destroyed', 'exploded'):
                    try: self_va._VehicleAppearance__attachStickers(0.3, True)
                    except: pass
                    try: self_va.onModelChanged()
                    except: pass
            except: pass

            try:
                _orig_assemble = VA.VehicleAppearance._VehicleAppearance__assembleModels
                def _safe_assemble(self_inner):
                    try: _orig_assemble(self_inner)
                    except Exception:
                        try:
                            _veh2 = self_inner._VehicleAppearance__vehicle
                            _hull = self_inner.modelsDesc['hull']
                            _turret = self_inner.modelsDesc['turret']
                            _gun = self_inner.modelsDesc['gun']
                            _root = _veh2.model.node('')
                            _hull['_node'] = _root
                            _root.attach(_hull['model'])
                            _turret['_node'] = _hull['model'].node('HP_turretJoint', self_inner.turretMatrix)
                            _turret['_node'].attach(_turret['model'])
                            _gun['_node'] = _turret['model'].node('HP_gunJoint', self_inner.gunMatrix)
                            _gun['_node'].attach(_gun['model'])
                        except Exception: pass
                VA.VehicleAppearance._VehicleAppearance__assembleModels = _safe_assemble
                try:
                    original_setup(self_va)
                finally:
                    try:
                        _veh = self_va._VehicleAppearance__vehicle
                        if hasattr(_veh, '_offline_matrix') and _veh._offline_matrix is not None:
                            _mdl = getattr(_veh, 'model', None)
                            if _mdl is not None:
                                if _mdl.motors:
                                    try: _mdl.delMotor(_mdl.motors[0])
                                    except: pass
                                _servo = BigWorld.Servo(_veh._offline_matrix)
                                try: _mdl.addMotor(_servo)
                                except: pass
                                _veh._offline_servo = _servo
                    except Exception: pass

            except Exception as e:
                try:
                    m = getattr(self_va._VehicleAppearance__vehicle, 'model', None)
                    if m is not None and not hasattr(m, 'wg_fashion'):
                        m.wg_fashion = type('FakeFashion', (), {
                            'setTrackTraces': lambda *a, **kw: None,
                            'receiveShotImpulse': lambda *a, **kw: None,
                            'hideTracks': lambda *a, **kw: None,
                            'movementInfo': None,
                            'staticPitchSwingForce': 0,
                            'disableSwinging': False
                        })()
                    if m is not None and not hasattr(m, 'wg_gunRecoil'):
                        try: m.wg_gunRecoil = None
                        except: pass
                except: pass

        VA.VehicleAppearance._VehicleAppearance__setupModels = safe_setupModels

        def safe_start(self_va, vehicle, prereqs=None):
            from helpers.DecalMap import DecalMap as DM
            DM.getIndex = lambda self, name: 0
            BigWorld.wg_addDecal = lambda *a, **k: 0
            self_va._VehicleAppearance__curDamageState = 'undamaged'
            try:
                for _comp in ('gun', 'chassis', 'hull', 'turret'):
                    _desc = self_va.modelsDesc.get(_comp, {})
                    for _key in ('model', '_fetchedModel'):
                        _m = _desc.get(_key)
                        if _m is not None and not hasattr(_m, 'wg_gunRecoil'):
                            try: _m.wg_gunRecoil = None
                            except: pass
            except: pass

            try:
                self_va._VehicleAppearance__vehicle = vehicle
                descr = vehicle.typeDescriptor
                player = BigWorld.player()
                try: BigWorld.addShadowEntity(vehicle)
                except: pass

                import BigWorld as _BW
                flt = _BW.WGVehicleFilter()
                vehicle.filter = flt
                try: flt.vehicleWidth = descr.chassis['topRightCarryingPoint'][0] * 2
                except: pass
                try: flt.vehicleCollisionCallback = player.handleVehicleCollidedVehicle
                except: pass
                try: flt.isLaggingStateChangedCallback = self_va._VehicleAppearance__onIsLaggingStateChanged
                except: pass
                try:
                    _spd_lim2 = descr.physics.get('speedLimits', [10.0, 10.0])
                    if isinstance(_spd_lim2, (int, float)): _spd_lim2 = [_spd_lim2, _spd_lim2]
                    flt.vehicleMaxMove = _spd_lim2[0] * 2.0
                except: pass
                try:
                    flt.vehicleMinNormalY = descr.physics['minPlaneNormalY']
                except Exception:
                    pass
                try:
                    for p1, p2, p3 in descr.physics['carryingTriangles']: flt.addTriangle((p1[0], 0, p1[1]), (p2[0], 0, p2[1]), (p3[0], 0, p3[1]))
                except: pass
                self_va.turretMatrix.target = flt.turretMatrix
                self_va.gunMatrix.target    = flt.gunMatrix

                try: flt.setInitialSpeeds(0.0, 0.0)
                except Exception: pass

                try: self_va._VehicleAppearance__createGunRecoil()
                except: pass
                try: self_va._VehicleAppearance__createStickers()
                except: pass
                try: self_va._VehicleAppearance__createExhaust()
                except: pass
                try: self_va._VehicleAppearance__skeletonCollider = VA._SkeletonCollider(vehicle, self_va)
                except: pass
                try: self_va._VehicleAppearance__crashedTracksCtrl = VA._CrashedTrackController(vehicle, self_va)
                except: pass

                self_va._VehicleAppearance__fashion = _BW.WGVehicleFashion()
                VA._setupVehicleFashion(self_va._VehicleAppearance__fashion, vehicle, isCrashedTrack=False)

                _fsh = self_va._VehicleAppearance__fashion
                _chassis_model = self_va.modelsDesc.get('chassis', {}).get('model', None)
                if _chassis_model is not None and _fsh is not None:
                    try:
                        if not hasattr(_chassis_model, 'wg_fashion'): _chassis_model.wg_fashion = _fsh
                    except Exception: pass

                for desc_comp in self_va.modelsDesc.itervalues():
                    modelName = desc_comp['_stateFunc'](vehicle, self_va._VehicleAppearance__curDamageState)
                    if prereqs is not None:
                        try: desc_comp['model'] = prereqs[modelName]
                        except: pass
                    if desc_comp.get('model') is None:
                        try: desc_comp['model'] = _BW.Model(modelName)
                        except: pass

                try:
                    from helpers import bound_effects as _be
                    for _dc in self_va.modelsDesc.itervalues():
                        if _dc.get('model') is not None and _dc.get('boundEffects') is None:
                            _dc['boundEffects'] = _be.ModelBoundEffects(_dc['model'])
                except: pass

                self_va._VehicleAppearance__firstInit = True
                self_va._VehicleAppearance__setupModels()
                self_va._VehicleAppearance__firstInit = False

                if getattr(vehicle, 'health', 0) > 0:
                    model = self_va.modelsDesc['hull'].get('model')
                    if model:
                        try: self_va._VehicleAppearance__engineSound = VA._getSound(model, descr.engine['sound'])
                        except: self_va._VehicleAppearance__engineSound = None
                        try: self_va._VehicleAppearance__movementSound = VA._getSound(model, descr.chassis['sound'])
                        except: self_va._VehicleAppearance__movementSound = None
                    self_va._VehicleAppearance__isEngineSoundMutedByLOD = False

                try: self_va._VehicleAppearance__setupDustTrails()
                except: pass

                from VehicleAppearance import _PERIODIC_TIME
                def _make_safe_periodic(va_ref):
                    def _safe_periodic():
                        if not getattr(va_ref, '_offline_periodic_stopped', False):
                            _stop_now = False
                            try:
                                _veh_ref = va_ref._VehicleAppearance__vehicle
                                if _veh_ref is None or getattr(_veh_ref, 'isDestroyed', False): _stop_now = True
                            except Exception: _stop_now = True
                            if _stop_now:
                                va_ref._offline_periodic_stopped = True
                                return
                            try:
                                _veh_ref = va_ref._VehicleAppearance__vehicle
                                _mdl = getattr(_veh_ref, 'model', None)
                                if _mdl is not None: _realPos = Math.Matrix(_mdl.matrix).translation
                                else:
                                    _offm = getattr(_veh_ref, '_offline_matrix', None)
                                    _realPos = _offm.translation if _offm is not None else _veh_ref.position
                                _newDist = (BigWorld.camera().position - _realPos).length
                                _wasMuted = va_ref._VehicleAppearance__isEngineSoundMutedByLOD
                                va_ref._VehicleAppearance__distanceFromPlayer = _newDist
                                if getattr(_veh_ref, 'isPlayer', False):
                                    _willMute = _newDist > 80
                            except Exception: pass
                            try: va_ref._VehicleAppearance__updateMovementSounds()
                            except Exception: pass
                            try: va_ref._VehicleAppearance__updateBlockedMovement()
                            except Exception: pass
                            va_ref._VehicleAppearance__periodicTimerID = BigWorld.callback(_PERIODIC_TIME, _safe_periodic)
                    return _safe_periodic

                _safe_cb = _make_safe_periodic(self_va)
                self_va._VehicleAppearance__periodicTimerID = BigWorld.callback(_PERIODIC_TIME * _random.uniform(0.01, 1.0), _safe_cb)

            except Exception: pass

        def _safe_updateBlockedMovement(self_va):
            try:
                flt = getattr(self_va._VehicleAppearance__vehicle, 'filter', None)
                if flt is None: return
                spd = flt.speedInfo.value[0]
                powerMode, dirFlags = self_va._VehicleAppearance__engineMode
                blockingForce = 0.0
                if abs(spd) < 0.25 and powerMode > 1:
                    if dirFlags & 1: blockingForce = -0.5
                    elif dirFlags & 2: blockingForce = 0.5
                self_va._VehicleAppearance__fashion.staticPitchSwingForce = blockingForce
            except Exception: pass
        VA.VehicleAppearance._VehicleAppearance__updateBlockedMovement = _safe_updateBlockedMovement

        VA.VehicleAppearance.start = safe_start
        VA.VehicleAppearance._patched_for_offline = True


    def _retryOfflineChatInit(self):
        if getattr(self, '_is_finished', False): return
        if _generate_offline_chat_channels():
            BigWorld.callback(3.0, self._botChatTick)
        else:
            BigWorld.callback(1.0, self._retryOfflineChatInit)

    def _botChatTick(self):

        if getattr(self, '_is_finished', False): return
        try:
            playerTeam = getattr(self.playerAvatar, 'team', 1)
            alive_bots = []
            for vehID, descr, isPlayer in self.vehicles:
                if isPlayer: continue
                veh = BigWorld.entity(vehID)
                if veh is not None and getattr(veh, 'health', 0) > 0:
                    alive_bots.append(vehID)
            if alive_bots and _random.random() < 0.65:
                botID = _random.choice(alive_bots)
                vdata = self.arena.vehicles.get(botID, {}) if self.arena else {}
                bname = vdata.get('name', 'Bot')
                bteam = vdata.get('team', 1)
                phrase = _random.choice(_BOT_CHAT_PHRASES)
                cid = _OFFLINE_CHAT_TEAM_CID if bteam == playerTeam else _OFFLINE_CHAT_ALL_CID
                _offline_post_chat_message(cid, botID, bname, phrase)
        except Exception as e:
            LOG_ERROR("[BATTLE][CHAT] bot chat tick failed: %s" % e)
        if not getattr(self, '_is_finished', False):
            BigWorld.callback(_random.uniform(5.0, 14.0), self._botChatTick)

    def _get_dmg_vehicle_state(self, veh):

        try:
            vehID = veh.id
        except Exception:
            return None
        state = self._dmg_vehicle_states.get(vehID)
        if state is not None:
            return state
        try:
            descr = getattr(veh, 'typeDescriptor', None)
            maxHP = descr.maxHealth if descr is not None else getattr(veh, 'health', 100)
            name = (getattr(descr, 'name', None) if descr is not None else None) or ('vehicle_%d' % vehID)
        except Exception:
            descr = None
            maxHP, name = getattr(veh, 'health', 100), ('vehicle_%d' % vehID)
        try:
            state = make_vehicle_state(name, maxHP, descr=descr)
        except Exception:
            return None
        self._dmg_vehicle_states[vehID] = state
        return state

    def _destroy_player_modules_and_crew(self, veh):

        try:
            if veh is None: return
            state = self._get_dmg_vehicle_state(veh)
            if state is None: return
            _freshly_broken = []
            try:
                for module_type, module in state.modules.items():
                    if module.state != MODULE_STATE_DESTROYED:
                        module.state = MODULE_STATE_DESTROYED
                        _freshly_broken.append(module_type)
            except Exception: pass
            try:
                for member in state.crew:
                    if member.state != CREW_STATE_KILLED:
                        member.state = CREW_STATE_KILLED
            except Exception: pass
            try:
                bw = self.battleWindow
                if bw is not None and hasattr(bw, 'damagePanel'):
                    for module_type in _freshly_broken:
                        bw.damagePanel.updateCriticalIcon(module_type, 'destroyed')
            except Exception: pass
        except Exception: pass

    def _apply_ramming(self, veh_a, veh_b, nx, nz, hit_pt, mass_a, mass_b, vn):

        try:
            av = self.playerAvatar
            if av is None or veh_a is None or veh_b is None:
                return
            try:
                ta = self.arena.vehicles.get(getattr(veh_a, 'id', -1), {}).get('team', 0)
                tb = self.arena.vehicles.get(getattr(veh_b, 'id', -1), {}).get('team', 0)
                if ta and tb and ta == tb:
                    _offline_play_collision_fx(veh_a, hit_pt)
                    return
            except Exception:
                pass
            _offline_play_collision_fx(veh_a, hit_pt)
            _offline_play_collision_fx(veh_b, hit_pt)
            dmg_b, dmg_a = _ram_damage(vn, mass_a, mass_b)
            aid = getattr(veh_a, 'id', 0)
            bid = getattr(veh_b, 'id', 0)
            if dmg_a > 0:
                av._apply_vehicle_damage(veh_a, dmg_a, hit_pt, killer_id=bid, hit_component='chassis')
            if dmg_b > 0:
                av._apply_vehicle_damage(veh_b, dmg_b, hit_pt, killer_id=aid, hit_component='chassis')
        except Exception:
            pass

    def on_penetrating_hit(self, veh, damage, shell_type=ShellType.AP, penetrated=True, hit_component=None):

        state = self._get_dmg_vehicle_state(veh)
        if state is None or damage <= 0:
            return None
        result = self._dmg_resolver.resolve_hit(state, damage, shell_type, penetrated=penetrated, hit_component=hit_component)

        isPlayerVeh = bool(self.playerAvatar) and veh.id == self.playerAvatar.playerVehicleID

        _deathForced = frozenset(result.get('death_forced_modules', ()))

        for module_type, mstate in result['module_events']:
            LOG_NOTE("[BATTLE][DMG] vehID=%d module=%s -> %s" % (veh.id, module_type, mstate))
            _forcedByDeath = module_type in _deathForced
            if isPlayerVeh:
                try:
                    bw = self.battleWindow
                    if bw and hasattr(bw, 'damagePanel'):
                        _icon = 'chassis' if module_type in (ModuleType.LEFT_TRACK, ModuleType.RIGHT_TRACK) else module_type
                        bw.damagePanel.updateCriticalIcon(_icon, mstate)
                        if module_type in (ModuleType.LEFT_TRACK, ModuleType.RIGHT_TRACK):
                            bw.damagePanel.updateCriticalIcon(module_type, mstate)
                        try:
                            self.playerAvatar._SimpleAvatar__deviceStates[_icon] = mstate
                            if module_type in (ModuleType.LEFT_TRACK, ModuleType.RIGHT_TRACK):
                                self.playerAvatar._SimpleAvatar__deviceStates[module_type] = mstate
                        except Exception:
                            pass
                        if mstate == 'destroyed' and module_type != ModuleType.FUEL_TANK:
                            _repMod = state.get_module(module_type)
                            _repSecs = _repMod.repair_seconds() if _repMod is not None else _random.uniform(10.0, 16.0)
                            BigWorld.callback(
                                _repSecs,
                                lambda m=module_type, v=veh.id: self._repair_dmg_module(v, m))
                except Exception: pass
                if not _forcedByDeath:
                    try:
                        sound = get_module_sound(module_type, mstate)
                        if sound: self.playerAvatar._playCrewVoice(sound)
                    except Exception: pass
                    self._post_damage_message(module_type, mstate, veh.id)

            if not isPlayerVeh:
                if mstate == 'destroyed' and module_type != ModuleType.FUEL_TANK:
                    if module_type in (ModuleType.LEFT_TRACK, ModuleType.RIGHT_TRACK):
                        self._schedule_dmg_repair(veh.id, module_type)
                    else:
                        _track_pending = False
                        try:
                            for _tt in (ModuleType.LEFT_TRACK, ModuleType.RIGHT_TRACK):
                                _tm = state.get_module(_tt)
                                if _tm is not None and _tm.state == MODULE_STATE_DESTROYED:
                                    _track_pending = True
                                    break
                        except Exception:
                            _track_pending = False
                        if not _track_pending:
                            self._schedule_dmg_repair(veh.id, module_type)

            if module_type == ModuleType.FUEL_TANK and mstate in ('critical', 'destroyed'):
                self._maybe_ignite_fire(veh, state, mstate)

            if module_type in (ModuleType.LEFT_TRACK, ModuleType.RIGHT_TRACK) and mstate == 'destroyed':
                self._sync_crashed_track_visual(veh, module_type, True)

        for role, outcome in result['crew_events']:
            LOG_NOTE("[BATTLE][DMG] vehID=%d crew=%s -> %s" % (veh.id, role, outcome))
            if isPlayerVeh:
                if outcome == 'killed':
                    try:
                        sound = get_crew_kill_sound(role)
                        if sound: self.playerAvatar._playCrewVoice(sound)
                    except Exception: pass
                self._post_crew_message(role, outcome, veh.id)

        return result

    def _sync_crashed_track_visual(self, veh, module_type, show):

        try:
            appr = getattr(veh, 'appearance', None)
            if appr is None:
                return
            if module_type == ModuleType.LEFT_TRACK:
                is_left = True
            elif module_type == ModuleType.RIGHT_TRACK:
                is_left = False
            else:
                return
            ctrl = getattr(appr, '_VehicleAppearance__crashedTracksCtrl', None)
            if ctrl is None:
                if show and hasattr(appr, 'addCrashedTrack'):
                    appr.addCrashedTrack(is_left)
                elif not show and hasattr(appr, 'delCrashedTrack'):
                    appr.delCrashedTrack(is_left)
                return
            if show and getattr(veh, 'health', 1) <= 0:
                return
            if show:
                ctrl.addTrack(is_left)
            else:
                ctrl.delTrack(is_left)
        except Exception:
            LOG_CURRENT_EXCEPTION()

    def _post_damage_notice(self, text, colour):

        try:
            import GUI
        except Exception:
            return
        try:
            comp = GUI.Text(text)
            comp.horizontalAnchor = 'CENTER'
            comp.verticalAnchor = 'BOTTOM'
            comp.horizontalPositionMode = 'PIXEL'
            comp.verticalPositionMode = 'PIXEL'
            comp.widthMode = 'PIXEL'
            comp.heightMode = 'PIXEL'
            comp.colour = Math.Vector4(colour[0], colour[1], colour[2], colour[3])
            try: comp.font = 'default_smaller.font'
            except Exception: pass
            _DMG_AMMO_PANEL_H = 110.0
            _DMG_AMMO_GAP      = 26.0
            _n = len(getattr(self, '_ammo_notices', ()))
            comp.position = Math.Vector3(0.0, -(_DMG_AMMO_PANEL_H + _DMG_AMMO_GAP + (_n * 18.0)), 0.0)
            comp.visible = True
            GUI.addRoot(comp)
        except Exception:
            return

        if not hasattr(self, '_ammo_notices'): self._ammo_notices = []
        entry = {'comp': comp}
        self._ammo_notices.append(entry)

        _MAX_AMMO_NOTICES = 6
        while len(self._ammo_notices) > _MAX_AMMO_NOTICES:
            _old = self._ammo_notices.pop(0)
            try: GUI.delRoot(_old['comp'])
            except Exception: pass

        def _expire(tok=entry):
            try:
                if tok in self._ammo_notices:
                    self._ammo_notices.remove(tok)
                    try: GUI.delRoot(tok['comp'])
                    except Exception: pass
            except Exception: pass

        BigWorld.callback(2.5, _expire)

    def destroyCrewNotice(self):
        try:
            import GUI
        except Exception:
            return
        for entry in getattr(self, '_ammo_notices', []):
            try: GUI.delRoot(entry['comp'])
            except Exception: pass
        self._ammo_notices = []

    def _attacker_name(self, vehID):

        try:
            _st = getattr(self, '_bot_state', {}).get(vehID, {})
            _att = _st.get('last_attacker')
            if not _att: return None
            _pa = getattr(self, 'playerAvatar', None)
            if _pa is not None and _att == _pa.playerVehicleID: return None
            _arena = getattr(self, 'arena', None)
            if _arena is None: return None
            return (_arena.vehicles.get(_att, {}) or {}).get('name') or None
        except Exception:
            return None

    def _post_damage_message(self, module_type, mstate, vehID=None):

        ru = _MODULE_RU.get(module_type)
        if ru is None: return
        name = ru[0]
        full, short = (_DMG_MSG_MODULE_DESTR if mstate == 'destroyed' else _DMG_MSG_MODULE_CRIT)
        entity = self._attacker_name(vehID) if vehID is not None else None
        if entity:
            text = full % {'entity': entity, 'device': name}
        else:
            text = short % {'device': name}
        self._post_damage_notice(text, _DMG_COLOUR_ORANGE)

    def _post_crew_message(self, role, outcome, vehID=None):

        name = _CREW_RU.get(role, role)
        entity = self._attacker_name(vehID) if vehID is not None else None
        if entity:
            text = _DMG_MSG_CREW_HIT[0] % {'entity': entity, 'device': name}
        else:
            text = _DMG_MSG_CREW_HIT[1] % {'device': name}
        self._post_damage_notice(text, _DMG_COLOUR_ORANGE)

    def _post_death_message(self, attacker_id=None):

        text = _DMG_MSG_DEATH_SHORT
        try:
            if attacker_id is not None:
                _arena = getattr(self, 'arena', None)
                if _arena is not None:
                    _info = _arena.vehicles.get(attacker_id, {}) or {}
                    _nm = _info.get('name')
                    if _nm: text = _DMG_MSG_DEATH_SHOT % {'entity': _nm}
        except Exception: pass
        self._post_damage_notice(text, _DMG_COLOUR_REDPURPLE)

    def _get_mobility_factor(self, vehID):

        state = self._dmg_vehicle_states.get(vehID)
        if state is None: return 1.0
        left = state.get_module(ModuleType.LEFT_TRACK)
        right = state.get_module(ModuleType.RIGHT_TRACK)
        if (left is not None and left.state == 'destroyed') or (right is not None and right.state == 'destroyed'):
            return 0.0
        f = 1.0
        if left is not None and left.state == 'critical': f *= 0.5
        if right is not None and right.state == 'critical': f *= 0.5
        engine = state.get_module(ModuleType.ENGINE)
        if engine is not None:
            if engine.state == 'destroyed': f *= 0.0
            elif engine.state == 'critical': f *= 0.5
        f *= crew_stat_factor(state, 'mobility')
        return f

    def _can_fire(self, vehID):

        state = self._dmg_vehicle_states.get(vehID)
        if state is None: return True
        gun = state.get_module(ModuleType.GUN)
        return gun is None or gun.state != 'destroyed'

    def _reload_multiplier(self, vehID):

        state = self._dmg_vehicle_states.get(vehID)
        f = 1.0
        if state is not None:
            bay = state.get_module(ModuleType.AMMO_BAY)
            if bay is not None:
                if bay.state == 'destroyed': f *= 5.0
                elif bay.state == 'critical': f *= 2.5
            f *= crew_stat_factor(state, 'reload')
        return f

    def _dispersion_factor(self, vehID):

        state = self._dmg_vehicle_states.get(vehID)
        if state is None: return 1.0
        return module_stat_factor(state, 'dispersion') * crew_stat_factor(state, 'dispersion')

    def _aim_time_factor(self, vehID):

        state = self._dmg_vehicle_states.get(vehID)
        if state is None: return 1.0
        return module_stat_factor(state, 'aim_time') * crew_stat_factor(state, 'aim_time')

    def _view_range_factor(self, vehID):

        state = self._dmg_vehicle_states.get(vehID)
        if state is None: return 1.0
        f = module_stat_factor(state, 'vision')
        radio = state.get_module(ModuleType.RADIO)
        if radio is not None:
            if radio.state == 'destroyed': f *= 0.8
            elif radio.state == 'critical': f *= 0.9
        f *= crew_stat_factor(state, 'vision')
        return clamp_vision_factor(f)

    def _turret_speed_factor(self, vehID):

        state = self._dmg_vehicle_states.get(vehID)
        if state is None: return 1.0
        return module_stat_factor(state, 'turret_speed')

    def _maybe_ignite_fire(self, veh, state, mstate):

        if state.on_fire: return
        if getattr(veh, 'health', 1) <= 0: return
        chance = 0.85 if mstate == 'destroyed' else 0.45
        if _random.random() >= chance: return
        state.on_fire = True
        ticks = 8 if mstate == 'destroyed' else 5
        isPlayerVeh = bool(self.playerAvatar) and veh.id == self.playerAvatar.playerVehicleID
        if isPlayerVeh:
            try: self.playerAvatar._playCrewVoice(FIRE_STARTED_SOUND)
            except Exception: pass
            try:
                self._post_damage_notice(_DMG_MSG_FIRE_BURNING, _DMG_COLOUR_ORANGE)
            except Exception: pass
        BigWorld.callback(1.0, lambda v=veh.id, t=ticks: self._fire_tick(v, t))

    def _stop_fire(self, veh):

        try:
            state = self._get_dmg_vehicle_state(veh)
            if state is None:
                return
            state.on_fire = False
            isPlayerVeh = bool(self.playerAvatar) and veh is not None and veh.id == self.playerAvatar.playerVehicleID
            if isPlayerVeh:
                try:
                    self.playerAvatar._playCrewVoice(FIRE_STOPPED_SOUND)
                except Exception:
                    pass
                try:
                    bw = self.battleWindow
                    if bw and hasattr(bw, 'damagePanel'):
                        bw.damagePanel.onFireInVehicle(False)
                except Exception:
                    pass
        except Exception:
            pass

    def _fire_tick(self, vehID, ticks_left):

        if getattr(self, '_is_finished', False): return
        state = self._dmg_vehicle_states.get(vehID)
        if state is None or not state.on_fire: return
        veh = BigWorld.entity(vehID)
        if veh is None or getattr(veh, 'health', 0) <= 0:
            state.on_fire = False
            return
        dmg = max(1, int(state.max_hp * 0.04))
        try:
            if self.playerAvatar is not None:
                _fire_killer_id = getattr(self, '_bot_state', {}).get(vehID, {}).get('last_attacker')
                self.playerAvatar._apply_vehicle_damage(veh, dmg, veh.position,
                                                          shell_type=ShellType.HE, penetrated=False,
                                                          killer_id=_fire_killer_id, fire_damage=True)
        except Exception: pass
        ticks_left -= 1
        stillAlive = getattr(veh, 'health', 0) > 0
        if ticks_left <= 0 or not stillAlive:
            state.on_fire = False
            if stillAlive and self.playerAvatar is not None and vehID == self.playerAvatar.playerVehicleID:
                try: self.playerAvatar._playCrewVoice(FIRE_STOPPED_SOUND)
                except Exception: pass
                try:
                    self._post_damage_notice(_DMG_MSG_FIRE_STOPPED, _DMG_COLOUR_GREEN)
                except Exception: pass
            return
        BigWorld.callback(1.0, lambda v=vehID, t=ticks_left: self._fire_tick(v, t))

    def _schedule_dmg_repair(self, vehID, module_type):

        if getattr(self, '_is_finished', False): return False
        state = self._dmg_vehicle_states.get(vehID)
        if state is None: return False
        module = state.get_module(module_type)
        if module is None or module.state != MODULE_STATE_DESTROYED:
            return False
        _inflight = self._repair_in_progress.setdefault(vehID, set())
        if module_type in _inflight:
            return False
        try:
            _repSecs = float(module.repair_seconds())
        except Exception:
            _repSecs = 10.0
        if _repSecs <= 0.0:
            _repSecs = 10.0
        _inflight.add(module_type)
        BigWorld.callback(_repSecs, lambda m=module_type, v=vehID: self._repair_dmg_module(v, m))
        return True

    def _drain_bot_repair_queue(self, vehID):

        if getattr(self, '_is_finished', False): return
        state = self._dmg_vehicle_states.get(vehID)
        if state is None: return
        _inflight = self._repair_in_progress.get(vehID)
        def _is_free(_mt):
            if _inflight and _mt in _inflight:
                return False
            _mg = state.get_module(_mt)
            return _mg is not None and _mg.state == MODULE_STATE_DESTROYED
        for _tt in (ModuleType.LEFT_TRACK, ModuleType.RIGHT_TRACK):
            if _is_free(_tt):
                self._schedule_dmg_repair(vehID, _tt)
                return
        for _mt in state.modules:
            if _mt in (ModuleType.LEFT_TRACK, ModuleType.RIGHT_TRACK):
                continue
            if _is_free(_mt):
                self._schedule_dmg_repair(vehID, _mt)
                return

    def _repair_dmg_module(self, vehID, module_type):

        if getattr(self, '_is_finished', False): return
        state = self._dmg_vehicle_states.get(vehID)
        if state is None: return
        module = state.get_module(module_type)
        if module is None or not module.repair():
            try: self._repair_in_progress.get(vehID, set()).discard(module_type)
            except Exception: pass
            return
        try: self._repair_in_progress.get(vehID, set()).discard(module_type)
        except Exception: pass
        if module_type in (ModuleType.LEFT_TRACK, ModuleType.RIGHT_TRACK):
            _veh = BigWorld.entity(vehID)
            if _veh is not None:
                self._sync_crashed_track_visual(_veh, module_type, False)
        if self.playerAvatar is not None and vehID == self.playerAvatar.playerVehicleID:
            try:
                bw = self.battleWindow
                if bw and hasattr(bw, 'damagePanel'):
                    bw.damagePanel.updateCriticalIcon(module_type, 'critical')
            except Exception: pass
            try:
                sound = get_module_sound(module_type, 'functional')
                if sound: self.playerAvatar._playCrewVoice(sound)
            except Exception: pass
            ru = _MODULE_RU.get(module_type)
            if ru is not None:
                try:
                    self._post_damage_notice(_DMG_MSG_REPAIRED % {'device': ru[0]}, _DMG_COLOUR_ORANGE)
                except Exception: pass
        if not (self.playerAvatar is not None and vehID == self.playerAvatar.playerVehicleID):
            try: self._drain_bot_repair_queue(vehID)
            except Exception: pass

    def _repairCriticalIcon(self, moduleType):

        if getattr(self, '_is_finished', False): return
        try:
            veh = BigWorld.entity(self.playerAvatar.playerVehicleID) if self.playerAvatar else None
            if veh is None or getattr(veh, 'health', 0) <= 0: return
            bw = self.battleWindow
            if bw and hasattr(bw, 'damagePanel'):
                bw.damagePanel.updateCriticalIcon(moduleType, 'repaired')
        except Exception: pass

    def _set_player_device_icon(self, deviceName, deviceState):
        try:
            bw = self.battleWindow
            if bw and hasattr(bw, 'damagePanel'):
                bw.damagePanel.updateCriticalIcon(deviceName, deviceState)
        except Exception:
            pass
        try:
            av = self.playerAvatar
            if av is None:
                return
            ds = getattr(av, '_SimpleAvatar__deviceStates', None)
            if ds is None:
                return
            if deviceState in (None, 'normal'):
                ds.pop(deviceName, None)
            else:
                ds[deviceName] = deviceState
        except Exception:
            pass

    def _offline_repair_device(self, deviceName):

        try:
            av = self.playerAvatar
            veh = None
            if av is not None:
                try:
                    veh = av.getVehicleAttached()
                except Exception:
                    veh = None
                if veh is None:
                    veh = BigWorld.entity(getattr(av, 'playerVehicleID', 0) or 0)
            state = self._get_dmg_vehicle_state(veh)
            if state is None or not deviceName:
                LOG_NOTE('[BATTLE][DMG] repairkit ignored: no state or device (%s)' % deviceName)
                return False
            names = [deviceName]
            if deviceName in ('chassis', 'leftTrack', 'rightTrack', 'tracks'):
                names = [ModuleType.LEFT_TRACK, ModuleType.RIGHT_TRACK]
            did = False
            vehID = getattr(veh, 'id', None)
            for n in names:
                mod = state.get_module(n)
                if mod is None:
                    continue
                mod.repair_full()
                did = True
                try:
                    if vehID is not None:
                        self._repair_in_progress.get(vehID, set()).discard(n)
                except Exception:
                    pass
                if n in (ModuleType.LEFT_TRACK, ModuleType.RIGHT_TRACK):
                    self._sync_crashed_track_visual(veh, n, False)
                self._set_player_device_icon(n, 'normal')
            if deviceName in ('chassis', 'leftTrack', 'rightTrack', 'tracks'):
                lt = state.get_module(ModuleType.LEFT_TRACK)
                rt = state.get_module(ModuleType.RIGHT_TRACK)
                ch = 'normal'
                if (lt is not None and lt.state == 'destroyed') or (rt is not None and rt.state == 'destroyed'):
                    ch = 'destroyed'
                elif (lt is not None and lt.state == 'critical') or (rt is not None and rt.state == 'critical'):
                    ch = 'critical'
                self._set_player_device_icon('chassis', ch)
            else:
                self._set_player_device_icon(deviceName, 'normal')
            LOG_NOTE('[BATTLE][DMG] repairkit %s -> %s' % (deviceName, names))
            return did
        except Exception:
            LOG_CURRENT_EXCEPTION()
            return False

    def _updateBattleUI(self):
        if getattr(self, '_is_finished', False): return
        if self.battleWindow:
            try:
                if hasattr(self.battleWindow, 'damagePanel'):
                    veh = BigWorld.entity(self.playerAvatar.playerVehicleID)
                    if veh: self.battleWindow.damagePanel.updateHealth(veh.health)
            except Exception: pass

    def _fixTankIndicator(self):
        if getattr(self, '_is_finished', False): return
        if not self.battleWindow:
            BigWorld.callback(0.5, self._fixTankIndicator)
            return
        try:
            _pa = self.playerAvatar
            _descr = getattr(_pa, 'vehicleTypeDescriptor', None) if _pa else None
            if _descr is None:
                BigWorld.callback(0.5, self._fixTankIndicator)
                return
            _tags = getattr(getattr(_descr, 'type', None), 'tags', None)
            _type = 'Tank'
            try:
                _yawLim = _descr.turret.get('yawLimits') if _descr is not None else None
            except Exception:
                _yawLim = None
            try:
                if _tags:
                    if 'SPG' in _tags: _type = 'SPG'
                    elif 'AT-SPG' in _tags: _type = 'AT-SPG'
                if _type in ('SPG', 'AT-SPG') and _yawLim is None:
                    _type = 'Tank'
            except Exception: pass
            self.battleWindow.call('battle.tankIndicator.setType', [_type])
        except Exception: pass

    def _setPrebattleTimer(self, duration):
        if getattr(self, '_is_finished', False): return
        if not self.arena: return
        if not hasattr(self, '_battleEnterTime'): self._battleEnterTime = BigWorld.time()
        endTime = self._battleEnterTime + duration
        period_data = (constants.ARENA_PERIOD_PREBATTLE, endTime, duration, None)
        self.arena.update(constants.ARENA_UPDATE_PERIOD, cPickle.dumps(period_data))

    def _notifyMinimapVehicles(self):
        if getattr(self, '_is_finished', False): return
        try:
            mm = getattr(self.playerAvatar, '_minimap', None)
            if mm is None:
                BigWorld.callback(0.5, self._notifyMinimapVehicles)
                return
            for vehID in self._spotted_vehicles:
                try: mm.notifyVehicleStart(vehID)
                except Exception: pass
        except Exception: pass

    def _startMinimap(self):
        if getattr(self, '_is_finished', False): return
        try:
            from gui.Minimap import Minimap
            mm = Minimap()
            prereqs = mm.prerequisites()

            def _onReady(resourceRefs):
                if getattr(self, '_is_finished', False): return
                try:
                    mm.start()
                    self.playerAvatar._minimap = mm
                    self._notifyMinimapVehicles()
                except Exception: pass

            if prereqs: BigWorld.loadResourceListBG(prereqs, _onReady)
            else: _onReady(None)
        except Exception: pass

    def _startBattleMusic(self):
        if getattr(self, '_is_finished', False): return
        try:
            import MusicController as MC
            import FMOD
            mc = MC.g_musicController
            if mc is None:
                try:
                    mc = MC.MusicController()
                    MC.g_musicController = mc
                    LOG_NOTE("[BATTLE] music controller created fallback")
                except Exception as _mc_e:
                    LOG_NOTE("[BATTLE] music controller create failed: %s" % _mc_e)
                    mc = None
            _offline_prepare_music(mc)
            try:
                FMOD.loadSoundGroup('/ingame_voice/notifications_VO')
            except Exception:
                pass
            self._music_bank_loaded = True
            if mc is None:
                _fmod_sound_play(FMOD.getSound(_MAP_MUSIC_COMBAT))
                return

            _battle_self = self

            def _patched_getArena(eventId):
                try:
                    arenaType = _battle_self.arena.typeDescriptor
                except Exception:
                    arenaType = None
                soundEventName = None
                _precombat_id = getattr(MC, 'MUSIC_EVENT_PRECOMBAT', None)
                if arenaType is not None:
                    if eventId == MC.MUSIC_EVENT_COMBAT:
                        soundEventName = getattr(arenaType, 'music', None)
                    elif _precombat_id is not None and eventId == _precombat_id:
                        soundEventName = getattr(arenaType, 'loadingMusic', None) or getattr(arenaType, 'music', None)
                    elif eventId == MC.AMBIENT_EVENT_COMBAT:
                        soundEventName = getattr(arenaType, 'ambientSound', None)
                    elif eventId == getattr(MC, 'MUSIC_EVENT_COMBAT_LOADING', None):
                        soundEventName = getattr(arenaType, 'loadingMusic', None) or getattr(arenaType, 'music', None)
                if not soundEventName and eventId == MC.MUSIC_EVENT_COMBAT:
                    soundEventName = _MAP_MUSIC_COMBAT
                if not soundEventName and _precombat_id is not None and eventId == _precombat_id:
                    soundEventName = _MAP_MUSIC_COMBAT
                if soundEventName:
                    snd = FMOD.getSound(soundEventName)
                    if snd is not None:
                        try:
                            snd.stop()
                        except Exception:
                            pass
                    return snd
                return None

            mc._getArenaSoundEvent = _patched_getArena

            def _safe_onArenaStateChanged(*args):
                if getattr(_battle_self, '_is_finished', False):
                    return
                try:
                    import constants as _c
                    period = _battle_self.arena.period
                    if not getattr(_battle_self, '_entry_phase_done', False):
                        mc.play(_music_loading_event(MC))
                        return
                    _precombat = getattr(MC, 'MUSIC_EVENT_PRECOMBAT', None)
                    if period in (_c.ARENA_PERIOD_WAITING, _c.ARENA_PERIOD_PREBATTLE):
                        if _precombat is not None:
                            mc.play(_precombat)
                        mc.play(MC.AMBIENT_EVENT_COMBAT)
                    elif period == _c.ARENA_PERIOD_BATTLE:
                        mc.play(MC.MUSIC_EVENT_COMBAT)
                        mc.play(MC.AMBIENT_EVENT_COMBAT)
                    elif period == _c.ARENA_PERIOD_AFTERBATTLE:
                        mc.stopAmbient()
                        try:
                            winnerTeam = _battle_self.arena.periodAdditionalInfo[0]
                            player_team = _battle_self.playerAvatar.team
                            if winnerTeam == player_team:
                                mc.play(MC.MUSIC_EVENT_COMBAT_VICTORY)
                            elif winnerTeam == 0:
                                mc.play(MC.MUSIC_EVENT_COMBAT_DRAW)
                            else:
                                mc.play(MC.MUSIC_EVENT_COMBAT_LOSE)
                        except Exception:
                            pass
                except Exception:
                    pass

            if not getattr(self, '_music_period_hooked', False):
                self.arena.onPeriodChange += _safe_onArenaStateChanged
                self._music_period_hooked = True
            _safe_onArenaStateChanged()

        except Exception:
            pass

    def _setupAmmoPanel(self):
        if getattr(self, '_is_finished', False):
            return
        if getattr(self, '_ammo_panel_ready', False):
            return
        try:
            bw = self.battleWindow
            if bw is None:
                bw = getattr(g_windowsManager, 'battleWindow', None)
            if bw is None or getattr(bw, 'consumablesPanel', None) is None:
                BigWorld.callback(0.5, self._setupAmmoPanel)
                return
            av = self.playerAvatar
            descr = av.vehicleTypeDescriptor if av is not None else None
            if not descr:
                BigWorld.callback(0.5, self._setupAmmoPanel)
                return
            shots = descr.gun['shots']
            if not shots:
                return

            shell_counts = {}
            try:
                from Offline import Manager
                from CurrentVehicle import g_currentVehicle
                veh = g_currentVehicle.vehicle
                if veh:
                    shells = Manager._vehicle_shells.get(veh.inventoryId, [])
                    for cd, count in zip(shells[::2], shells[1::2]):
                        shell_counts[cd] = count
            except Exception:
                pass

            _raw = []
            for shot in shots:
                shell_descr = shot['shell']
                shell_cd = shell_descr.get('compactDescr', 0)
                if not shell_cd:
                    try:
                        sid = shell_descr['id']
                        shell_cd = makeIntCompactDescrByID('shell', sid[0], sid[1])
                    except Exception:
                        shell_cd = 0
                count = shell_counts.get(shell_cd, 0) if shell_cd else 0
                _raw.append((shell_cd, count))
            totals = 0
            _norm = []
            for cd, count in _raw:
                try:
                    count = int(float(count or 0))
                except Exception:
                    count = 0
                if count < 0:
                    count = 0
                _norm.append((cd, count))
                totals += count
            _raw = _norm
            if totals <= 0:
                try:
                    from items.vehicles import getDefaultAmmoForGun as _gdef
                    _def = _gdef(descr.gun)
                    _raw = []
                    for _i in xrange(0, len(_def) - 1, 2):
                        _raw.append((_def[_i], int(_def[_i + 1] or 0)))
                except Exception:
                    try:
                        _cap = int(descr.gun.get('maxAmmo', 40) or 40)
                    except Exception:
                        _cap = 40
                    if _raw:
                        _raw = [(_raw[0][0], _cap)] + [(cd, 0) for cd, _c in _raw[1:]]
            else:
                try:
                    _cap = int(descr.gun.get('maxAmmo', 0) or 0)
                except Exception:
                    _cap = 0
                if _cap > 0:
                    _maxc = max(item[1] for item in _raw)
                    if _maxc >= _cap:
                        _raw = [(cd, (_cap if count == _maxc else 0)) for cd, count in _raw]
                        _seen = False
                        _fixed = []
                        for cd, count in _raw:
                            if count > 0 and not _seen:
                                _fixed.append((cd, _cap))
                                _seen = True
                            elif count > 0 and _seen:
                                _fixed.append((cd, 0))
                            else:
                                _fixed.append((cd, 0))
                        _raw = _fixed

            av._resetBattleConsumables()
            for shell_cd, count in _raw:
                if not shell_cd:
                    continue
                av.updateVehicleAmmo(shell_cd, count, 0)
            _eq_slots = [0, 0, 0]
            try:
                from Offline import Manager as _OM
                from CurrentVehicle import g_currentVehicle as _gcv
                _vid = _gcv.vehicle.inventoryId if _gcv.vehicle else 1
                try:
                    _eq_slots = list(_OM._ensure_battle_equipments(_vid) or [0, 0, 0])
                except Exception:
                    _eq_slots = list(_OM._vehicle_equipments.get(_vid, [0, 0, 0]) or [0, 0, 0])
                while len(_eq_slots) < NUM_EQUIPMENT_SLOTS:
                    _eq_slots.append(0)
            except Exception:
                _eq_slots = [0, 0, 0]
            LOG_NOTE('[BATTLE] equipment slots: %s' % (_eq_slots,))
            _eq_filled = 0
            for _i in range(NUM_EQUIPMENT_SLOTS):
                _ecd = 0
                try:
                    _ecd = int(_eq_slots[_i] or 0)
                except Exception:
                    _ecd = 0
                if _ecd:
                    try:
                        av.updateVehicleAmmo(_ecd, 1, 0)
                        _eq_filled += 1
                    except Exception:
                        pass
            for _j in range(max(0, NUM_EQUIPMENT_SLOTS - _eq_filled)):
                try:
                    av.updateVehicleAmmo(0, 0, 0)
                except Exception:
                    pass

            first_cd = None
            for shell_cd, count in _raw:
                if shell_cd and count > 0:
                    first_cd = shell_cd
                    break
            if first_cd is None and _raw:
                first_cd = _raw[0][0]
            if first_cd:
                av.updateVehicleSetting(constants.VEHICLE_SETTING.CURRENT_SHELLS, first_cd)
                av.updateVehicleSetting(constants.VEHICLE_SETTING.NEXT_SHELLS, first_cd)
            try:
                bw.bindCommands()
            except Exception:
                pass
            def _push_ammo_to_aim(_left=8):
                if getattr(self, '_is_finished', False):
                    return
                try:
                    aim = av.inputHandler.aim
                    if aim is not None and av._ammo:
                        aim.setAmmoStock(av._ammo[0][1])
                        return
                except Exception:
                    pass
                if _left > 0:
                    BigWorld.callback(0.5, lambda: _push_ammo_to_aim(_left - 1))
            _push_ammo_to_aim()
            self._ammo_panel_ready = True
            LOG_NOTE('[BATTLE] ammo panel: %s equipment: %s' % (av._ammo, dict(av._SimpleAvatar__equipment)))
        except Exception:
            LOG_CURRENT_EXCEPTION()

    def _vehicle_try_break_destructible(self, wallRes, headingYaw, may_break=True):

        if wallRes is None:
            return None
        matKind = wallRes[2]
        if matKind < _const.DESTRUCTIBLE_MATKINDS_MIN or matKind > _const.DESTRUCTIBLE_MATKINDS_MAX:
            return None

        itemIndex = wallRes[4]
        chunkID = wallRes[5]
        if getattr(self, '_destr_keep_bushes', False):
            try:
                _dk = _destr_type_for_matKind(self.spaceID, chunkID, itemIndex)
                if _dk in (DESTR_TYPE_TREE, DESTR_TYPE_FALLING_ATOM, DESTR_TYPE_FRAGILE):
                    return 'soft'
            except Exception:
                pass
        key = (chunkID, itemIndex)
        if not hasattr(self, '_destr_hit_cooldown'): self._destr_hit_cooldown = {}
        if not hasattr(self, '_destr_type_cache'): self._destr_type_cache = {}

        if may_break and key not in _G_DESTR_BROKEN:
            now_t = BigWorld.time()
            if now_t - self._destr_hit_cooldown.get(key, 0.0) >= 0.5:
                self._destr_hit_cooldown[key] = now_t
                try:
                    dirVec = Math.Vector3(_math.sin(headingYaw), 0.0, _math.cos(headingYaw))
                    _dispatch_destructible_hit(self.spaceID, matKind, itemIndex, chunkID, dirVec, False)
                except Exception:
                    pass

        dtype = self._destr_type_cache.get(key, False)
        if dtype is False:
            dtype = _destr_type_for_matKind(self.spaceID, chunkID, itemIndex)
            self._destr_type_cache[key] = dtype

        if (dtype == DESTR_TYPE_STRUCTURE and
                matKind >= _const.DESTRUCTIBLE_MATKINDS_NORMAL_MIN and
                matKind <= _const.DESTRUCTIBLE_MATKINDS_NORMAL_MAX):
            return 'hard'
        if dtype is None:
            return 'hard'
        return 'soft'

    def _building_fields_for_chunk(self, spaceID, cid):

        _BUILDING_FIELD_TTL = 4.0
        _now = BigWorld.time()
        if not hasattr(self, '_bld_fields_cache'):
            self._bld_fields_cache = {}
        if cid in self._bld_fields_cache:
            if _now - self._bld_fields_cache[cid][0] < _BUILDING_FIELD_TTL:
                return self._bld_fields_cache[cid][1]
            _fresh = []
            for _f in self._bld_fields_cache[cid][1]:
                try:
                    _cx = (_f[0] + _f[2]) * 0.5
                    _cz = (_f[1] + _f[3]) * 0.5
                    _res = BigWorld.wg_collideSegment(spaceID,
                        Math.Vector3(_cx, _f[4] + 300.0, _cz),
                        Math.Vector3(_cx, _f[4] - 300.0, _cz), 128)
                    if _res is not None and _destr_structure_intact(spaceID, _res):
                        _fresh.append(_f)
                except Exception:
                    _fresh.append(_f)
            self._bld_fields_cache[cid] = (_now, _fresh)
            return _fresh
        _fields = []
        _cnt = getattr(self, '_chunk_destr_counts', {}).get(cid, 0)
        if _cnt > 0:
            try:
                from AreaDestructibles import g_cache
                _cm = BigWorld.wg_getChunkMatrix(spaceID, cid)
                _cmy = _cm.translation.y
                for _di in xrange(_cnt):
                    try:
                        _dDesc = g_cache.getDestructibleDesc(spaceID, cid, _di)
                        if _dDesc is None or _dDesc.get('type') != DESTR_TYPE_STRUCTURE:
                            continue
                        _dm = Math.Matrix(BigWorld.wg_getDestructibleMatrix(spaceID, cid, _di))
                        _wx = _cm.translation.x + _dm.translation.x
                        _wz = _cm.translation.z + _dm.translation.z
                        _by = _cmy + _dm.translation.y
                        _fp = _measure_building_footprint(spaceID, _wx, _wz, _by + 1.5)
                        if _fp is not None:
                            _fields.append((_fp[0], _fp[1], _fp[2], _fp[3], _by, _di))
                    except Exception:
                        pass
            except Exception:
                pass
        self._bld_fields_cache[cid] = (_now, _fields)
        return _fields

    def _clamp_off_building_fields(self, spaceID, prev_x, prev_z, try_x, try_z, radius):

        try:
            if not hasattr(self, '_bld_fields_cache'):
                self._bld_fields_cache = {}
            _touched = False
            _gx0 = int(_math.floor(try_x * 0.01)) - 1
            _gz0 = int(_math.floor(try_z * 0.01)) - 1
            for _dx in xrange(3):
                for _dz in xrange(3):
                    _cid = ((_gx0 + _dx + 127) << 8) | (_gz0 + _dz + 127)
                    if not g_destructiblesManager.isChunkLoaded(_cid):
                        continue
                    for (_fminx, _fminz, _fmaxx, _fmaxz, _fby, _fdi) in self._building_fields_for_chunk(spaceID, _cid):
                        _fminx -= radius
                        _fminz -= radius
                        _fmaxx += radius
                        _fmaxz += radius
                        if _fminx <= try_x <= _fmaxx and _fminz <= try_z <= _fmaxz:
                            _nx = _fminx if (try_x - _fminx) < (_fmaxx - try_x) else _fmaxx
                            _nz = _fminz if (try_z - _fminz) < (_fmaxz - try_z) else _fmaxz
                            if abs(try_x - _nx) < abs(try_z - _nz):
                                try_x = _nx
                            else:
                                try_z = _nz
                            _touched = True
            return (try_x, try_z, _touched)
        except Exception:
            return (try_x, try_z, False)

    def _compute_accel_decel(self, descr):

        try:
            power = float(descr.physics.get('enginePower', 400.0))
            weight = float(descr.physics.get('weight', 30000.0))
            brake = float(descr.physics.get('brakeForce', 0.0))
            rotLimit = float(descr.physics.get('rotationSpeedLimit', 0.7))
        except Exception:
            power, weight, brake, rotLimit = 400.0, 30000.0, 0.0, 0.7
        if weight <= 1.0: weight = 30000.0
        hpPerTon = power / (weight / 1000.0)
        _norm = min(1.0, max(0.0, (hpPerTon - _WOT_DYN_RATIO_MIN) /
                                      (_WOT_DYN_RATIO_MAX - _WOT_DYN_RATIO_MIN)))
        accel = 1.6 + _norm * 3.4
        if 1.0 <= brake <= 40.0:
            decel = brake
        else:
            decel = accel * 2.3
        decel = max(decel, 5.0)
        coast = max(0.6, min(2.2, accel * _WOT_COAST_RATIO))
        _spin = max(0.4, min(1.2, rotLimit))
        rotAccel = max(0.6, _spin / _WOT_ANG_SPIN_TIME)
        return accel, decel, coast, rotAccel

    def _try_attach_player_motor(self, veh):

        attempts = getattr(self, '_motor_attach_attempts', 0) + 1
        self._motor_attach_attempts = attempts
        try:
            _mdl = getattr(veh, 'model', None)
            if _mdl is None:
                raise Exception("veh.model not ready yet")
            if _mdl.motors:
                _mdl.delMotor(_mdl.motors[0])
            _mdl.addMotor(self._offline_servo)
            self._motor_attached = True
            if attempts > 1:
                LOG_NOTE("[BATTLE] player servo motor attached on attempt %d/60" % attempts)
        except Exception as _e:
            if attempts >= 60:
                LOG_ERROR("[BATTLE] player servo motor attach GAVE UP after %d attempts (%s) - "
                          "player tank visual movement stays on the native filter/motor, not "
                          "the custom accel physics" % (attempts, _e))
            elif attempts == 1 or attempts % 10 == 0:
                LOG_NOTE("[BATTLE] player servo motor attach attempt %d/60 failed (%s), retrying" % (attempts, _e))

    def _store_phys_pose(self, pos):
        self._phys_x = pos.x
        self._phys_y = pos.y
        self._phys_z = pos.z
        self._phys_yaw = getattr(self, '_cur_yaw', 0.0)
        self._phys_pitch = getattr(self, '_cur_pitch', 0.0)
        self._phys_roll = getattr(self, '_cur_roll', 0.0)

    def _apply_visual_hull(self, snap=False):

        if not hasattr(self, '_offline_matrix'):
            return
        px = getattr(self, '_phys_x', None)
        if px is None:
            return
        py = getattr(self, '_phys_y', 0.0)
        pz = getattr(self, '_phys_z', 0.0)
        pyaw = getattr(self, '_phys_yaw', 0.0)
        ppitch = getattr(self, '_phys_pitch', 0.0)
        proll = getattr(self, '_phys_roll', 0.0)
        now = BigWorld.time()
        if not hasattr(self, '_vis_tick_t'):
            self._vis_tick_t = now
        dt = now - self._vis_tick_t
        self._vis_tick_t = now
        if dt <= 0.0 or dt > 0.1:
            dt = 0.01
        if snap or not hasattr(self, '_vis_x'):
            self._vis_x = px
            self._vis_y = py
            self._vis_z = pz
            self._vis_yaw = pyaw
            self._vis_pitch = ppitch
            self._vis_roll = proll
        else:
            k = min(1.0, 18.0 * dt)
            if getattr(self, '_block_tilt', False) or abs(getattr(self, '_cur_speed', 0.0)) < 0.08:
                k = min(1.0, 28.0 * dt)
            self._vis_x += (px - self._vis_x) * k
            self._vis_y += (py - self._vis_y) * k
            self._vis_z += (pz - self._vis_z) * k
            dyaw = (pyaw - self._vis_yaw + _math.pi) % (2.0 * _math.pi) - _math.pi
            self._vis_yaw += dyaw * k
            self._vis_pitch += (ppitch - self._vis_pitch) * k
            self._vis_roll += (proll - self._vis_roll) * k
        self._offline_matrix.setRotateYPR((self._vis_yaw, self._vis_pitch, self._vis_roll))
        self._offline_matrix.translation = Math.Vector3(self._vis_x, self._vis_y, self._vis_z)

    def __visualTick(self):
        if getattr(self, '_is_finished', False):
            return
        try:
            self._apply_visual_hull(False)
            veh = None
            try:
                if self.playerAvatar is not None:
                    veh = BigWorld.entity(self.playerAvatar.playerVehicleID)
            except Exception:
                veh = None
            if veh is not None and hasattr(self, '_offline_matrix'):
                try:
                    _stamp_entity_pose(veh, self._offline_matrix)
                    self.playerAvatar._ownVehicleMProv.target = self._offline_matrix
                except Exception:
                    pass
        except Exception:
            pass
        if not getattr(self, '_is_finished', False):
            _vis_dt = 0.05
            BigWorld.callback(_vis_dt, self.__visualTick)

    def _update_cruise_indicator(self):

        if self.battleWindow is None: return
        avatar = getattr(self, 'playerAvatar', None)
        active = getattr(avatar, '_cruise_active', False) if avatar is not None else False
        _cModeName = 'CRUISE_CONTROL_MODE_NONE'
        if active:
            _modeIdx = getattr(avatar, '_cruise_mode', _CRUISE_MODE_DEFAULT)
            try:
                _cModeName, _mDir, _mFrac = _CRUISE_MODES[_modeIdx]
            except Exception:
                _cModeName = 'CRUISE_CONTROL_MODE_FWD100'
        try:
            import Avatar as _AvMod
            _cc_none = getattr(_AvMod, 'CRUISE_CONTROL_MODE_NONE', 0)
            _cc_mode = getattr(_AvMod, _cModeName, 1)
        except Exception:
            _cc_none, _cc_mode = 0, 1
        _mode = _cc_mode if active else _cc_none
        try:
            if getattr(self, '_cruise_ui_mode', None) != _mode:
                self.battleWindow.call('battle.cruiseCtrl.setCruiseMode', [_mode])
                self._cruise_ui_mode = _mode
        except Exception: pass
        try:
            _tgt = 0.0
            if avatar is not None:
                try: _tgt = getattr(avatar, '_cruise_target', 0.0) or 0.0
                except Exception: pass
            _spd = getattr(self, '_cur_speed', 0.0) or 0.0
            _val = int(_tgt * 3.6) if active else int(abs(_spd) * 3.6)
            if getattr(self, '_cruise_ui_target', None) != _val:
                self.battleWindow.call('battle.cruiseCtrl.updateTarget', [_val])
                self._cruise_ui_target = _val
        except Exception: pass

    def __movementTick(self):
        if getattr(self, '_is_finished', False):
            return
        if getattr(self, '_move_busy', False):
            return
        self._move_busy = True
        try:
            self._movementTickWork()
        except Exception:
            LOG_CURRENT_EXCEPTION()
        self._move_busy = False
        if not getattr(self, '_is_finished', False):
            BigWorld.callback(0.05, self.__movementTick)

    def _movementTickWork(self):
        if getattr(self, '_is_finished', False): return
        self._destr_keep_bushes = False
        try:
            if self.playerAvatar and getattr(self.playerAvatar, 'isOnArena', False):
                self._updateCapturePoints()
        except Exception:
            pass

        if not self.playerAvatar or not self.playerAvatar.isOnArena:
            return

        veh = BigWorld.entity(self.playerAvatar.playerVehicleID)
        if veh is None or not getattr(veh, 'isStarted', False):
            return

        player_is_dead = getattr(veh, 'health', 1) <= 0
        if player_is_dead:
            self._cur_speed = 0.0
            self._cur_rot = 0.0
            self.playerAvatar.currentMove = 0.0
            self.playerAvatar.currentTurn = 0.0
            try:
                self.playerAvatar._cruise_active = False
                self.playerAvatar._cruise_target = 0.0
            except Exception: pass
            self._update_cruise_indicator()

        flt = getattr(veh, 'filter', None)
        if flt is None:
            return

        move = self.playerAvatar.currentMove
        turn = self.playerAvatar.currentTurn
        descr = self.playerAvatar.vehicleTypeDescriptor
        if descr is None:
            return

        _ah = getattr(self.playerAvatar, 'inputHandler', None)
        if _ah is not None and not getattr(_ah, '_AvatarInputHandler__isArenaStarted', True):
            move = 0.0; turn = 0.0

        try:
            _isMovingNow = (move != 0 or turn != 0)
            if getattr(self, '_engineSoundMoving', None) != _isMovingNow:
                self._engineSoundMoving = _isMovingNow
                if hasattr(veh, 'appearance') and veh.appearance is not None:
                    _hasSnd = getattr(veh.appearance, '_VehicleAppearance__engineSound', None) is not None
                    if _isMovingNow: veh.appearance.changeEngineMode((3, 1))
                    else: veh.appearance.changeEngineMode((1, 0))
        except Exception: pass

        now = BigWorld.time()
        if not hasattr(self, '_last_tick_time'): self._last_tick_time = now
        dt = now - self._last_tick_time
        if dt <= 0.0 or dt > 0.2: dt = 0.05
        self._last_tick_time = now

        _spd_lim = descr.physics.get('speedLimits', [10.0, 10.0])
        if isinstance(_spd_lim, (int, float)): _spd_lim = [_spd_lim, _spd_lim]
        fwdLimit = _spd_lim[0]
        bwdLimit = _spd_lim[1]

        rotLimit  = descr.physics.get('rotationSpeedLimit', 0.5)

        _mobility = self._get_mobility_factor(veh.id)

        if not hasattr(self, '_cur_pos'): self._cur_pos = Math.Vector3(veh.position)
        _terrainMatKind = _nav_ground_matkind(self.spaceID, self._cur_pos.x, self._cur_pos.z, self._cur_pos.y)
        _terrain = _terrain_speed_factor(descr, _terrainMatKind)

        _prevSpd = getattr(self, '_cur_speed', 0.0)
        _slopeDir = 1.0 if move > 0 else (-1.0 if move < 0 else
                    (1.0 if _prevSpd > 0.05 else (-1.0 if _prevSpd < -0.05 else 0.0)))
        _slopeTarget = _slope_speed_target(getattr(self, '_last_h_front', None),
                                            getattr(self, '_last_h_back', None),
                                            getattr(self, '_last_half_L', None), _slopeDir)
        if not hasattr(self, '_slope_factor'): self._slope_factor = 1.0
        self._slope_factor = _slope_ease_toward(self._slope_factor, _slopeTarget, dt)

        _moveFactor = _mobility * _terrain * self._slope_factor
        if _moveFactor != 1.0:
            fwdLimit *= _moveFactor
            bwdLimit *= _moveFactor
            rotLimit *= _moveFactor

        _cruise_active = getattr(self.playerAvatar, '_cruise_active', False)
        if _cruise_active and not player_is_dead:
            _modeIdx = getattr(self.playerAvatar, '_cruise_mode', _CRUISE_MODE_DEFAULT)
            try:
                _mName, _mDir, _mFrac = _CRUISE_MODES[_modeIdx]
            except Exception:
                _mName, _mDir, _mFrac = _CRUISE_MODES[_CRUISE_MODE_DEFAULT]
            _cruiseMax = max(_spd_lim[0], 1.5)
            _cruiseBwdMax = max(_spd_lim[1], 1.5)
            _tgt = getattr(self.playerAvatar, '_cruise_target', 0.0) or 0.0
            if abs(_tgt) <= 0.01:
                _cs = getattr(self, '_cur_speed', 0.0) or 0.0
                if _cs > 1.5:
                    _tgt = _cs
                elif _cs < -1.5:
                    _tgt = _cs
                elif _mDir > 0:
                    _tgt = _cruiseMax * _mFrac
                else:
                    _tgt = -(_cruiseBwdMax * _mFrac)
            if _tgt >= 0.0:
                move = 1.0
                _t = min(max(float(_tgt), 1.5), _cruiseMax)
                if fwdLimit > _t:
                    fwdLimit = _t
            else:
                move = -1.0
                _t = min(max(-float(_tgt), 1.5), _cruiseBwdMax)
                if bwdLimit > _t:
                    bwdLimit = _t
            self.playerAvatar._cruise_target = _tgt

        if not hasattr(self, '_cur_speed'): self._cur_speed = 0.0; self._cur_rot = 0.0

        _accel, _decel, _coast, _rotAcc = self._compute_accel_decel(descr)
        if _moveFactor != 1.0:
            _mobFactor = _moveFactor if _moveFactor > 0.0 else 1.0
            _accel *= _mobFactor
            _decel *= _mobFactor
            _coast *= _mobFactor
            _rotAcc *= _mobFactor

        if move > 0:
            if self._cur_speed > fwdLimit:
                self._cur_speed = max(self._cur_speed - _decel * dt, fwdLimit)
            elif self._cur_speed < 0:
                self._cur_speed = min(self._cur_speed + _decel * dt, 0.0)
            else:
                _rate = _accel * max(0.0, 1.0 - (self._cur_speed / max(fwdLimit, 0.1)) ** 1.5)
                self._cur_speed = min(self._cur_speed + _rate * dt, fwdLimit)
        elif move < 0:
            if self._cur_speed < -bwdLimit:
                self._cur_speed = min(self._cur_speed + _decel * dt, -bwdLimit)
            elif self._cur_speed > 0:
                self._cur_speed = max(self._cur_speed - _decel * dt, 0.0)
            else:
                _rate = _accel * _WOT_BKWD_POWER * max(0.0, 1.0 - (abs(self._cur_speed) / max(bwdLimit, 0.1)) ** 1.5)
                self._cur_speed = max(self._cur_speed - _rate * dt, -bwdLimit)
        else:
            if abs(self._cur_speed) < max(_decel * dt, 0.01): self._cur_speed = 0.0
            elif self._cur_speed > 0: self._cur_speed = max(self._cur_speed - _decel * dt, 0.0)
            else: self._cur_speed = min(self._cur_speed + _decel * dt, 0.0)

        _targetRot = turn * rotLimit
        _rotDiff = _targetRot - self._cur_rot
        _rotStep = _rotAcc * dt
        if abs(_rotDiff) <= _rotStep:
            self._cur_rot = _targetRot
        else:
            self._cur_rot += _rotStep if _rotDiff > 0.0 else -_rotStep

        if not hasattr(self, '_cur_yaw'): self._cur_yaw = 0.0
        _prev_yaw = self._cur_yaw
        self._cur_yaw += self._cur_rot * dt

        import math as _math
        if not hasattr(self, '_cur_pos'): self._cur_pos = Math.Vector3(veh.position)
        _prev_x = self._cur_pos.x
        _prev_z = self._cur_pos.z
        self._cur_pos.x += _math.sin(self._cur_yaw) * self._cur_speed * dt
        self._cur_pos.z += _math.cos(self._cur_yaw) * self._cur_speed * dt

        try:
            _trc = descr.chassis['topRightCarryingPoint']
            _pf_rad = max(float(_trc[0]), float(_trc[1]), 1.0)
        except Exception:
            _pf_rad = 2.0
        _nfx, _nfz, _nf_touch = self._clamp_off_building_fields(self.spaceID, _prev_x, _prev_z, self._cur_pos.x, self._cur_pos.z, _pf_rad)
        if _nf_touch:
            self._cur_pos.x = _nfx
            self._cur_pos.z = _nfz
            self._cur_speed = 0.0
            try:
                _offline_play_collision_fx(veh, Math.Vector3(self._cur_pos.x, self._cur_pos.y, self._cur_pos.z))
            except Exception:
                pass

        try:
            _minPlaneY = descr.chassis.get('minPlaneNormalY', 0.766)
            _top = self._cur_pos.y + 3.0
            _bot = self._cur_pos.y - 3.0
            _wallRes = BigWorld.wg_collideSegment(self.spaceID, Math.Vector3(self._cur_pos.x, _top, self._cur_pos.z),
                                                  Math.Vector3(self._cur_pos.x, _bot, self._cur_pos.z), 128)
            _p_hill_now = False

            _destr_kind = self._vehicle_try_break_destructible(_wallRes, self._cur_yaw)
            if _destr_kind == 'hard':
                self._cur_speed *= 0.85

            if _destr_kind == 'soft':
                _blocked_by_wall = False
            else:
                _blocked_by_wall = _destr_structure_intact(self.spaceID, _wallRes)
                _p_hill_now = False
                if abs(self._cur_speed) > 0.5:
                    try:
                        _p_hl2 = max(descr.chassis['topRightCarryingPoint'][1], 1.0)
                    except Exception:
                        _p_hl2 = 2.5
                    _p_hill_now = _nav_drivable_rise(self.spaceID,
                        self._cur_pos.x, self._cur_pos.z, self._cur_pos.y,
                        self._cur_yaw if self._cur_speed >= 0 else (self._cur_yaw + _math.pi),
                        _p_hl2 + 2.0, 1.0 if self._cur_speed >= 0 else -1.0)
                if _p_hill_now and (_destr_kind == 'hard' or _destr_structure_intact(self.spaceID, _wallRes)):
                    _p_hill_now = False
                if not _blocked_by_wall and not _p_hill_now and _destr_kind != 'soft' and _wallRes is not None:
                    _dy_g = _wallRes[0].y - self._cur_pos.y
                    _is_road = _wallRes[1].y >= 0.40 and abs(_dy_g) < 1.6
                    if not _is_road:
                        _step_dx = self._cur_pos.x - _prev_x
                        _step_dz = self._cur_pos.z - _prev_z
                        _step_horiz = _math.sqrt(_step_dx * _step_dx + _step_dz * _step_dz)
                        _max_step = max(_BOT_CLIMB_STEP, _step_horiz * 0.85)
                        if _nav_step_blocked(_dy_g, _max_step):
                            _blocked_by_wall = True

                if not _blocked_by_wall and not _p_hill_now and _destr_kind != 'soft' and _wallRes is not None:
                    _dy_g2 = _wallRes[0].y - self._cur_pos.y
                    if not (_wallRes[1].y >= 0.40 and abs(_dy_g2) < 1.6):
                        if not hasattr(self, '_climb_ref'):
                            self._climb_ref = None
                        self._climb_ref, _climb_blocked = _nav_track_climb(
                            self._climb_ref, self._cur_pos.x, self._cur_pos.z, _wallRes[0].y)
                        if _climb_blocked:
                            _blocked_by_wall = True

                if not _blocked_by_wall and not _p_hill_now:
                    try:
                        _p_hw_tr = max(descr.chassis['topRightCarryingPoint'][0], 1.0)
                        _p_rx = _math.cos(self._cur_yaw)
                        _p_rz = -_math.sin(self._cur_yaw)
                        for _p_side in (-1.0, 1.0):
                            _ptx = self._cur_pos.x + _p_rx * _p_hw_tr * _p_side
                            _ptz = self._cur_pos.z + _p_rz * _p_hw_tr * _p_side
                            _pSideRes = BigWorld.wg_collideSegment(self.spaceID,
                                Math.Vector3(_ptx, self._cur_pos.y + 3.0, _ptz),
                                Math.Vector3(_ptx, self._cur_pos.y - 3.0, _ptz), 128)
                            if _pSideRes is None:
                                continue
                            _p_side_destr = self._vehicle_try_break_destructible(_pSideRes, self._cur_yaw)
                            if _p_side_destr == 'soft':
                                continue
                            elif _p_side_destr == 'hard':
                                _blocked_by_wall = True
                                break
                            else:
                                if _pSideRes[1].y < 0.40:
                                    _p_side_step_dx = _ptx - _prev_x
                                    _p_side_step_dz = _ptz - _prev_z
                                    _p_side_horiz = _math.sqrt(_p_side_step_dx * _p_side_step_dx + _p_side_step_dz * _p_side_step_dz)
                                    _p_side_max = max(_BOT_CLIMB_STEP, _p_side_horiz * 0.85)
                                    if _nav_step_blocked(_pSideRes[0].y - self._cur_pos.y, _p_side_max):
                                        _blocked_by_wall = True
                                        break
                    except Exception:
                        pass

            if not _blocked_by_wall:
                pass

            _contact_clamped = False
            if not _blocked_by_wall and abs(self._cur_speed) > 0.05:
                try:
                    _pHw = max(descr.chassis['topRightCarryingPoint'][0], 1.0)
                    _pHl = max(descr.chassis['topRightCarryingPoint'][1], 1.0)
                except Exception:
                    _pHw, _pHl = 1.5, 2.5
                _whisker_h = 0.85
                _contact_len = 1.4
                _ray_yaw = self._cur_yaw if self._cur_speed > 0 else (self._cur_yaw + _math.pi)
                _perp_yaw = _ray_yaw + _math.pi / 2.0

                for _off in (0.0, _pHw * 0.9, -_pHw * 0.9, _pHw * 0.45, -_pHw * 0.45):
                    _startX = _prev_x + _math.sin(_perp_yaw) * _off + _math.sin(_ray_yaw) * _pHl
                    _startZ = _prev_z + _math.cos(_perp_yaw) * _off + _math.cos(_ray_yaw) * _pHl
                    _endX = _startX + _math.sin(_ray_yaw) * _contact_len
                    _endZ = _startZ + _math.cos(_ray_yaw) * _contact_len
                    _fwdRes = BigWorld.wg_collideSegment(self.spaceID,
                        Math.Vector3(_startX, self._cur_pos.y + _whisker_h, _startZ),
                        Math.Vector3(_endX, self._cur_pos.y + _whisker_h, _endZ), 128)
                    if _fwdRes is not None:
                        if _seg_hit_is_ground(_fwdRes, 0.40, self._cur_pos.y):
                            continue
                        self._vehicle_try_break_destructible(_fwdRes, self._cur_yaw)
                        if _destr_whisker_blocks(self.spaceID, _fwdRes[2], _fwdRes[4], _fwdRes[5]):
                            self._cur_pos.x = _prev_x
                            self._cur_pos.z = _prev_z
                            _blocked_by_wall = True
                            break

            if _blocked_by_wall:
                self._cur_pos.x = _prev_x
                self._cur_pos.z = _prev_z
                if not _p_hill_now:
                    self._cur_speed = 0.0
                    try:
                        _fxpt = None
                        if _wallRes is not None:
                            _fxpt = _wallRes[0]
                        _offline_play_collision_fx(veh, _fxpt)
                    except Exception:
                        pass
                else:
                    self._cur_speed *= 0.85
            try:
                _hcol = _check_horizontal_collision(
                    self.spaceID, self._cur_pos, self._cur_yaw, descr)
                if _hcol:
                    _hit_res = _hcol[2] if isinstance(_hcol, (tuple, list)) and len(_hcol) > 2 else None
                    _hkind = None
                    if _hit_res is not None:
                        try:
                            _hkind = self._vehicle_try_break_destructible(_hit_res, self._cur_yaw)
                        except Exception:
                            _hkind = None
                    if _hkind != 'soft':
                        self._cur_pos.x = _prev_x
                        self._cur_pos.z = _prev_z
                        self._cur_speed = 0.0
                        _blocked_by_wall = True
                        try:
                            _offline_play_collision_fx(veh, Math.Vector3(self._cur_pos.x, self._cur_pos.y, self._cur_pos.z))
                        except Exception:
                            pass
            except Exception:
                pass
            self._block_tilt = bool(_blocked_by_wall)
        except Exception as _we:
            LOG_NOTE("[BATTLE] player wall block check failed: %s" % _we)

        cf = getattr(self.playerAvatar, '_cruise_factor', 1.0)
        fwdLimit = _spd_lim[0] * cf
        bwdLimit = _spd_lim[1] * cf

        def _hull_extents(vdescr):
            try:
                hw, hlf, hlb = _tank_hull_dims(vdescr)
                return max(hw, 0.5), max((hlf + hlb) * 0.5, 1.0)
            except Exception:
                pass
            try:
                trc = vdescr.chassis['topRightCarryingPoint']
                return max(trc[0], 0.5), max(trc[1], 1.0)
            except Exception: return 1.5, 2.5

        def _obb_mtv(ax, az, ayaw, ahw, ahl, bx, bz, byaw, bhw, bhl):

            afx, afz = _math.sin(ayaw), _math.cos(ayaw)
            arx, arz = _math.cos(ayaw), -_math.sin(ayaw)
            bfx, bfz = _math.sin(byaw), _math.cos(byaw)
            brx, brz = _math.cos(byaw), -_math.sin(byaw)
            dx, dz = ax - bx, az - bz
            best_overlap, best_nx, best_nz = None, 1.0, 0.0
            for lx, lz in ((arx, arz), (afx, afz), (brx, brz), (bfx, bfz)):
                dist = dx * lx + dz * lz
                rA = abs(ahw * (arx * lx + arz * lz)) + abs(ahl * (afx * lx + afz * lz))
                rB = abs(bhw * (brx * lx + brz * lz)) + abs(bhl * (bfx * lx + bfz * lz))
                overlap = (rA + rB) - abs(dist)
                if overlap <= 0.0: return None
                if best_overlap is None or overlap < best_overlap:
                    best_overlap = overlap
                    best_nx, best_nz = (lx, lz) if dist >= 0.0 else (-lx, -lz)
            return (best_nx, best_nz, best_overlap)

        def _entity_yaw(v, om):
            if om is not None: return om.yaw
            try: return v.yaw
            except Exception: return 0.0

        def _vehicle_mass(vdescr):
            try: return max(vdescr.physics['weight'], 1000.0)
            except Exception: return 15000.0

        my_id = self.playerAvatar.playerVehicleID
        my_hw, my_hl = _hull_extents(descr)
        my_mass = _vehicle_mass(descr)
        next_x, next_z = self._cur_pos.x, self._cur_pos.z

        for vehID, _, _ in self.vehicles:
            if vehID == my_id: continue
            other_veh = BigWorld.entity(vehID)
            if not other_veh or not getattr(other_veh, 'isStarted', False): continue
            other_descr = getattr(other_veh, 'typeDescriptor', None)
            other_hw, other_hl = _hull_extents(other_descr) if other_descr else (1.5, 2.5)
            other_mass = _vehicle_mass(other_descr) if other_descr else 15000.0
            other_is_wreck = getattr(other_veh, 'health', 1) <= 0

            _other_om = getattr(other_veh, '_offline_matrix', None)
            _other_pos = _other_om.translation if _other_om is not None else other_veh.position
            _other_yaw = _entity_yaw(other_veh, _other_om)

            _mtv = _obb_mtv(next_x, next_z, self._cur_yaw, my_hw, my_hl,
                             _other_pos.x, _other_pos.z, _other_yaw, other_hw, other_hl)
            _mesh_hit = None
            _odx = next_x - _other_pos.x
            _odz = next_z - _other_pos.z
            if (_mtv is not None) or (_odx * _odx + _odz * _odz < 64.0):
                try:
                    _mesh_hit = _tanks_mesh_overlap(
                            self.playerAvatar, next_x, self._cur_pos.y, next_z, self._cur_yaw,
                            getattr(self, '_cur_pitch', 0.0), getattr(self, '_cur_roll', 0.0),
                            other_veh, _other_pos.x, _other_pos.y, _other_pos.z, _other_yaw,
                            getattr(other_veh, '_cur_pitch', 0.0), getattr(other_veh, '_cur_roll', 0.0))
                except Exception:
                    _mesh_hit = None
            if _mesh_hit is False:
                pass
            elif _mesh_hit is True and _mtv is None:
                _oln = _math.sqrt(_odx * _odx + _odz * _odz)
                if _oln < 0.001:
                    _mtv = (1.0, 0.0, 0.45)
                else:
                    _mtv = (_odx / _oln, _odz / _oln, 0.45)
            elif _mtv is not None and _mesh_hit is True:
                _mtv = (_mtv[0], _mtv[1], max(_mtv[2], 0.20))
            if _mtv is not None:
                nx, nz, overlap = _mtv
                total_mass = my_mass + other_mass
                try:
                    _pveh = BigWorld.entity(my_id) if my_id else None
                    if _pveh is None:
                        _pveh = veh
                    _my_v = _veh_velocity_2d(_pveh)
                    _ot_v = _veh_velocity_2d(other_veh)
                    _vn_ram = (_my_v.x - _ot_v.x) * nx + (_my_v.z - _ot_v.z) * nz
                    _spd_abs = abs(float(getattr(self, '_cur_speed', 0.0) or 0.0))
                    if abs(_vn_ram) < _spd_abs:
                        _vn_ram = _spd_abs * (1.0 if _vn_ram >= 0.0 else -1.0)
                    if abs(_vn_ram) < _spd_abs * 0.5:
                        _vn_ram = _spd_abs
                    if abs(_vn_ram) > _RAM_SAFE_SPEED and not (player_is_dead or other_is_wreck):
                        _rk = tuple(sorted((my_id, vehID)))
                        _now = BigWorld.time()
                        if _now - _g_offh_ram_cd.get(_rk, -10) >= _RAM_CD:
                            _g_offh_ram_cd[_rk] = _now
                            _hit_pt = Math.Vector3(
                                0.5 * (self._cur_pos.x + _other_pos.x),
                                0.5 * (self._cur_pos.y + _other_pos.y),
                                0.5 * (self._cur_pos.z + _other_pos.z))
                            self._apply_ramming(_pveh, other_veh, nx, nz, _hit_pt, my_mass, other_mass, _vn_ram)
                            self._cur_speed *= 0.45
                except Exception:
                    pass
                if player_is_dead and other_is_wreck:
                    my_push_ratio, other_push_ratio = 0.0, 0.0
                elif player_is_dead:
                    my_push_ratio, other_push_ratio = 0.0, 1.0
                elif other_is_wreck:
                    my_push_ratio, other_push_ratio = 1.0, 0.0
                else:
                    my_push_ratio    = other_mass / total_mass
                    other_push_ratio = my_mass / total_mass

                next_x += nx * overlap * my_push_ratio
                next_z += nz * overlap * my_push_ratio

                _fxs, _fzs = _math.sin(self._cur_yaw), _math.cos(self._cur_yaw)
                _vn = self._cur_speed * (_fxs * nx + _fzs * nz)
                if _mesh_hit is True and _vn > 0.0:
                    self._cur_speed -= _vn * 0.25
                try:
                    new_bot_pos = Math.Vector3(_other_pos.x - nx * overlap * other_push_ratio, _other_pos.y, _other_pos.z - nz * overlap * other_push_ratio)

                    _push_ok = True
                    _push_probe = BigWorld.wg_collideSegment(self.spaceID,
                        Math.Vector3(new_bot_pos.x, _other_pos.y + 3.0, new_bot_pos.z),
                        Math.Vector3(new_bot_pos.x, _other_pos.y - 3.0, new_bot_pos.z), 128)
                    if _push_probe is not None:
                        if _nav_step_blocked(_push_probe[0].y - _other_pos.y, _BOT_CLIMB_STEP):
                            _push_ok = False
                        else:
                            _other_climb_ref = getattr(other_veh, '_ai_climb_ref', None)
                            _other_climb_ref, _other_climb_blocked = _nav_track_climb(
                                _other_climb_ref, new_bot_pos.x, new_bot_pos.z, _push_probe[0].y)
                            if _other_climb_blocked:
                                _push_ok = False
                            else:
                                other_veh._ai_climb_ref = _other_climb_ref

                    if _push_ok and hasattr(other_veh, '_offline_matrix'): other_veh._offline_matrix.translation = new_bot_pos
                except: pass

        _push_probe = BigWorld.wg_collideSegment(self.spaceID,
            Math.Vector3(next_x, self._cur_pos.y + 3.0, next_z),
            Math.Vector3(next_x, self._cur_pos.y - 3.0, next_z), 128)
        if _push_probe is not None:
            _push_blocked = _nav_step_blocked(_push_probe[0].y - self._cur_pos.y, _BOT_CLIMB_STEP)
            if not _push_blocked:
                if not hasattr(self, '_climb_ref'): self._climb_ref = None
                _next_climb_ref, _push_blocked = _nav_track_climb(self._climb_ref, next_x, next_z, _push_probe[0].y)
                if not _push_blocked:
                    self._climb_ref = _next_climb_ref
            if _push_blocked:
                next_x, next_z = self._cur_pos.x, self._cur_pos.z
        self._cur_pos.x, self._cur_pos.z = next_x, next_z
        try:
            _spush2 = _hull_static_push(
                self.spaceID, self._cur_pos, self._cur_yaw, descr, self._cur_speed,
                veh, getattr(self, '_cur_pitch', 0.0), getattr(self, '_cur_roll', 0.0), 8)
            if _spush2 is not None:
                self._cur_speed = _spush2[2]
                self._vehicle_try_break_destructible(_spush2[3], self._cur_yaw)
        except Exception:
            pass

        try:
            _scenery_run_over(self.playerAvatar,
                              (_prev_x, _prev_z),
                              (self._cur_pos.x, self._cur_pos.z),
                              dt)
        except Exception as _de:
            LOG_NOTE("[BATTLE] scenery run_over failed: %s" % _de)
        try:
            if abs(self._cur_speed) > 0.45:
                if not hasattr(self, '_destr_hit_cooldown'):
                    self._destr_hit_cooldown = {}
                _fell_destructibles_near(
                    self.spaceID, self._cur_pos.x, self._cur_pos.z, self._cur_yaw, descr,
                    self._destr_hit_cooldown, getattr(self, '_chunk_destr_counts', None) or _scenery_counts)
        except Exception as _de:
            LOG_NOTE("[BATTLE] proximity destructibles pass failed: %s" % _de)

        try:
            trc = descr.chassis['topRightCarryingPoint']
            half_W, half_L = max(trc[0], 0.5), max(trc[1], 1.0)
        except Exception: half_L, half_W = 2.5, 1.5
        _hang_snap_y, _hang_block = (None, False)
        if not player_is_dead:
            _hang_snap_y, _hang_block = _hull_hang_snap(
                self.spaceID, self._cur_pos.x, self._cur_pos.z, self._cur_yaw,
                half_W, half_L, self._cur_pos.y, 2.2, _min_plane_ny(descr))
        if _hang_snap_y is not None:
            self._cur_pos.y = _hang_snap_y
        if _hang_block:
            try:
                self._cur_pos.x, self._cur_pos.z = _prev_x, _prev_z
            except Exception:
                pass
            try:
                self._cur_speed *= 0.35
            except Exception:
                pass
        pos = self._cur_pos

        fx, fz = _math.sin(self._cur_yaw), _math.cos(self._cur_yaw)
        rx, rz = _math.cos(self._cur_yaw), -_math.sin(self._cur_yaw)

        target_pitch, target_roll, _tL, _tW, h_front, h_back = _tilt_target_pitch_roll(
            self.spaceID, pos, self._cur_yaw, descr)
        self._last_h_front = h_front
        self._last_h_back  = h_back
        self._last_half_L  = half_L

        if not hasattr(self, '_cur_pitch'): self._cur_pitch = 0.0; self._cur_roll = 0.0
        if not hasattr(self, '_pitch_vel'): self._pitch_vel = 0.0; self._roll_vel = 0.0
        self._pitch_vel = 0.0
        self._roll_vel = 0.0
        if getattr(self, '_block_tilt', False):
            pass
        else:
            self._cur_pitch += (target_pitch - self._cur_pitch) * _TILT_STIFFNESS * dt
            self._cur_roll  += (target_roll  - self._cur_roll)  * _TILT_STIFFNESS * dt
        try:
            if not getattr(self, '_block_tilt', False):
                pos.y = _carrying_sit_y(
                    self.spaceID, pos.x, pos.z, self._cur_yaw,
                    self._cur_pitch, self._cur_roll, half_W, half_L, pos.y,
                    _min_plane_ny(descr))
                self._cur_pos.y = pos.y
        except Exception:
            pass

        try: flt.allowLagProcessing = True; flt.allowStrafeCompensation = False
        except Exception: pass

        _ah_gate = getattr(self.playerAvatar, 'inputHandler', None)
        _battle_started = _ah_gate is None or getattr(_ah_gate, '_AvatarInputHandler__isArenaStarted', True)
        _dir = getattr(self, '_bot_director', None)
        try:
            if _dir is not None:
                _dir.begin_move_tick()
        except Exception:
            pass

        for _bot_vehID, _bot_descr, _bot_is_player in self.vehicles:
            if _bot_is_player: continue
            _bot_veh = BigWorld.entity(_bot_vehID)
            if not _bot_veh or not getattr(_bot_veh, 'isStarted', False): continue
            _bot_flt = getattr(_bot_veh, 'filter', None)
            if _bot_flt is None or getattr(_bot_veh, 'health', 1) <= 0: continue
            _phys_lod = 'full'
            try:
                _bot_flt.allowLagProcessing = True
                _bot_flt.allowStrafeCompensation = False
                _bot_mat = getattr(_bot_veh, '_offline_matrix', None)
                if _bot_mat is not None:
                    _bot_actual_speed, _bot_actual_rot = 0.0, 0.0
                    _spd = 0.0
                    if hasattr(_bot_veh, '_ai_desired_yaw'):
                        _cur_yaw = _bot_mat.yaw
                        if not _battle_started:
                            _bot_veh._cur_speed = 0.0
                            continue
                        _phys_lod = 'full'
                        try:
                            _pp = getattr(self, '_cur_pos', None)
                            if _dir is not None and _pp is not None:
                                _phys_lod = _dir.physics_lod(_bot_mat.translation, _pp)
                            elif _pp is not None:
                                _dxp = _bot_mat.translation.x - _pp.x
                                _dzp = _bot_mat.translation.z - _pp.z
                                if (_dxp * _dxp + _dzp * _dzp) > _BOT_MOVE_LOD_DIST2:
                                    _phys_lod = 'far'
                        except Exception:
                            _phys_lod = 'full'
                        _bot_veh._phys_lod = _phys_lod
                        _new_yaw = _cur_yaw
                        _yaw_step = 0.0
                        _reverse_until_ts = getattr(_bot_veh, '_ai_reverse_until', 0.0)
                        _is_ai_reversing = BigWorld.time() < _reverse_until_ts
                        if _is_ai_reversing:
                            if not getattr(_bot_veh, '_ai_rev_last_active', False):
                                _bot_veh._ai_rev_target = None
                                _bot_veh._ai_rev_scan_time = 0.0
                                _bot_veh._ai_rev_started = None
                                _bot_veh._ai_rev_mark = (_bot_mat.translation.x, _bot_mat.translation.z, BigWorld.time())
                            _bot_veh._ai_rev_last_active = True
                            if BigWorld.time() > _bot_veh._ai_rev_scan_time:
                                _bot_veh._ai_rev_scan_time = BigWorld.time() + 0.4
                                _rv_mark = getattr(_bot_veh, '_ai_rev_mark', None)
                                if _rv_mark is None:
                                    _rv_mark = (_bot_mat.translation.x, _bot_mat.translation.z, BigWorld.time())
                                    _bot_veh._ai_rev_mark = _rv_mark
                                _rv_disp = ((_bot_mat.translation.x - _rv_mark[0]) ** 2 +
                                            (_bot_mat.translation.z - _rv_mark[1]) ** 2) ** 0.5
                                if _rv_disp >= _BOT_REVERSE_FUTILE_DIST:
                                    _bot_veh._ai_rev_mark = (_bot_mat.translation.x, _bot_mat.translation.z, BigWorld.time())
                                elif BigWorld.time() - _rv_mark[2] >= _BOT_REVERSE_FUTILE_DELAY:
                                    _bot_park(_bot_veh, 15.0)
                                    _revs_bot_state = getattr(self, '_bot_state', None)
                                    _revs_st = _revs_bot_state.get(_bot_vehID) if _revs_bot_state is not None else None
                                    if _revs_st is not None:
                                        _revs_st['bad_waypoint'] = _revs_st.get('waypoint')
                                        _revs_st['bad_waypoint_time'] = BigWorld.time()
                                        _revs_st['wp_timer'] = 0.0
                                    _nav_log(('rev0', _bot_vehID),
                                             "[BATTLE][NAV] bot %d reverse making no progress - cancelled, dropping waypoint" % _bot_vehID)
                                    _tick_desired_yaw = _cur_yaw
                                    _tick_target_speed = 0.0
                                    _is_ai_reversing = False
                                else:
                                    if _phys_lod == 'full':
                                        _bot_veh._ai_rev_target = _bot_avoid_scan(
                                            self.spaceID, _bot_mat.translation.x, _bot_mat.translation.y,
                                            _bot_mat.translation.z, _cur_yaw,
                                            getattr(_bot_veh, '_ai_reverse_side', 1))
                                    else:
                                        _bot_veh._ai_rev_target = None
                            _rv_tgt = getattr(_bot_veh, '_ai_rev_target', None)
                            if _rv_tgt is not None:
                                _tick_desired_yaw = _rv_tgt
                                _bot_veh._ai_feeler_yaw = _rv_tgt
                                _bot_veh._ai_feeler_until = BigWorld.time() + _BOT_FEELER_HOLD
                            else:
                                _tick_desired_yaw = _cur_yaw + getattr(_bot_veh, '_ai_reverse_side', 1) * 1.4
                                _rv_start = getattr(_bot_veh, '_ai_rev_started', None)
                                if _rv_start is None:
                                    _rv_start = BigWorld.time()
                                    _bot_veh._ai_rev_started = _rv_start
                                if BigWorld.time() - _rv_start <= _BOT_REVERSE_TRAPPED_MAX:
                                    pass
                                else:
                                    _revs_bot_state = getattr(self, '_bot_state', None)
                                    _revs_st = _revs_bot_state.get(_bot_vehID) if _revs_bot_state is not None else None
                                    if _revs_st is not None:
                                        _revs_st['bad_waypoint'] = _revs_st.get('waypoint')
                                        _revs_st['bad_waypoint_time'] = BigWorld.time()
                                        _revs_st['wp_timer'] = 0.0
                                    _nav_log(('trap', _bot_vehID),
                                             "[BATTLE][NAV] bot %d trapped reverse budget spent (%.0fs no escape) - dropping waypoint" % (
                                            _bot_vehID, _BOT_REVERSE_TRAPPED_MAX))
                                    _bot_park(_bot_veh, 15.0)
                                    _tick_desired_yaw = _cur_yaw
                                    _tick_target_speed = 0.0
                            _rev_splims = _bot_descr.physics.get('speedLimits', [10.0, 10.0])
                            if isinstance(_rev_splims, (int, float)): _rev_splims = [_rev_splims, _rev_splims]
                            _tick_target_speed = -abs(_rev_splims[1]) * _BOT_STUCK_REVERSE_SPEED_FRAC
                        else:
                            _bot_veh._ai_rev_last_active = False
                            _tick_desired_yaw = _bot_veh._ai_desired_yaw
                            _tick_target_speed = getattr(_bot_veh, '_ai_target_speed', 0.0)
                            try:
                                _post_turn = getattr(_bot_veh, '_ai_post_reverse_turn', None)
                                _post_until = getattr(_bot_veh, '_ai_post_reverse_until', 0.0)
                                if _post_turn is not None and BigWorld.time() < _post_until:
                                    _tick_desired_yaw = _post_turn
                                    if abs(_tick_target_speed) > 0.5:
                                        _tick_target_speed = max(2.5, abs(_tick_target_speed) * 0.5) * (1.0 if _tick_target_speed > 0 else -1.0)
                                elif _post_turn is not None:
                                    try:
                                        del _bot_veh._ai_post_reverse_turn
                                    except Exception:
                                        pass
                                    try:
                                        del _bot_veh._ai_post_reverse_until
                                    except Exception:
                                        pass
                            except Exception:
                                pass
                            try:
                                if abs(_tick_target_speed) > 0.5 and BigWorld.time() >= getattr(_bot_veh, '_ai_reverse_blocked_until', 0.0):
                                    _inv_cd = getattr(_bot_veh, '_ai_inv_scan_time', 0.0)
                                    if _phys_lod == 'full' and BigWorld.time() >= _inv_cd:
                                        _bot_veh._ai_inv_scan_time = BigWorld.time() + _BOT_INVISIBLE_SCAN_CD
                                        _fwd_yaw = _tick_desired_yaw
                                        if _bot_see_invisible_wall(self.spaceID,
                                                _bot_mat.translation.x, _bot_mat.translation.y,
                                                _bot_mat.translation.z, _fwd_yaw):
                                            _bot_trigger_unstuck_reverse(_bot_veh,
                                                _bot_mat.translation.x, _bot_mat.translation.z,
                                                _cur_yaw, 'sensor-ahead')
                                            _inv_state = getattr(self, '_bot_state', None)
                                            _inv_st = _inv_state.get(_bot_vehID) if _inv_state is not None else None
                                            if _inv_st is not None:
                                                _inv_st['bad_waypoint'] = _inv_st.get('waypoint')
                                                _inv_st['bad_waypoint_time'] = BigWorld.time()
                                                _inv_st['wp_timer'] = 0.0
                                            _tick_target_speed = 0.0
                            except Exception:
                                pass
                            if BigWorld.time() < getattr(_bot_veh, '_ai_hold_until', 0.0):
                                _tick_target_speed = 0.0

                        if not _is_ai_reversing and abs(_tick_target_speed) > 0.5:
                            _bcf = _bot_mat.translation
                            _nowF = BigWorld.time()
                            if (not hasattr(_bot_veh, '_ai_feeler_time')
                                    or _nowF > _bot_veh._ai_feeler_time):
                                _bot_veh._ai_feeler_time = _nowF + _BOT_FEELER_RESCAN
                                _bot_veh._ai_feeler_until = 0.0
                                _fs_side = 1
                                _fs_ref_y = getattr(_bot_veh, '_ai_desired_yaw', _cur_yaw)
                                _fs_dff = (_fs_ref_y - _cur_yaw + _math.pi) % (2 * _math.pi) - _math.pi
                                if _fs_dff < 0:
                                    _fs_side = -1
                                if _phys_lod == 'full':
                                    _scan_res = _bot_avoid_scan(self.spaceID,
                                        _bcf.x, _bcf.y, _bcf.z, _cur_yaw, _fs_side)
                                else:
                                    _scan_res = None
                                if _scan_res is None:
                                    _bot_veh._ai_feeler_yaw = None
                                else:
                                    _fs_diff = (_scan_res - _cur_yaw + _math.pi) % (2 * _math.pi) - _math.pi
                                    if abs(_fs_diff) < 0.06:
                                        _bot_veh._ai_feeler_yaw = None
                                    else:
                                        _bot_veh._ai_feeler_yaw = _scan_res
                                        _bot_veh._ai_feeler_until = _nowF + _BOT_FEELER_HOLD
                            _pfF = getattr(_bot_veh, '_ai_feeler_yaw', None)
                            if _pfF is not None and _nowF < getattr(_bot_veh, '_ai_feeler_until', 0.0):
                                _tick_desired_yaw = _pfF

                        _yaw_diff = (_tick_desired_yaw - _cur_yaw + _math.pi) % (2 * _math.pi) - _math.pi
                        _bot_mobility = self._get_mobility_factor(_bot_vehID)

                        _bot_terrainMK = _nav_ground_matkind(self.spaceID, _bot_mat.translation.x, _bot_mat.translation.z, _bot_mat.translation.y)
                        _bot_terrain = _terrain_speed_factor(_bot_descr, _bot_terrainMK)

                        _bot_prevSpd = getattr(_bot_veh, '_cur_speed', 0.0)
                        _bot_slopeDir = 1.0 if _tick_target_speed > 0.05 else (-1.0 if _tick_target_speed < -0.05 else
                                        (1.0 if _bot_prevSpd > 0.05 else (-1.0 if _bot_prevSpd < -0.05 else 0.0)))
                        _bot_slopeTarget = _slope_speed_target(getattr(_bot_veh, '_last_h_front', None),
                                                                getattr(_bot_veh, '_last_h_back', None),
                                                                getattr(_bot_veh, '_last_half_L', None), _bot_slopeDir)
                        if not hasattr(_bot_veh, '_slope_factor'): _bot_veh._slope_factor = 1.0
                        _bot_veh._slope_factor = _slope_ease_toward(_bot_veh._slope_factor, _bot_slopeTarget, dt)

                        _bot_moveFactor = _bot_mobility * _bot_terrain * _bot_veh._slope_factor

                        _rot_limit = _bot_descr.physics.get('rotationSpeedLimit', 0.5) * dt * _bot_moveFactor
                        _yaw_step = max(-_rot_limit, min(_rot_limit, _yaw_diff))
                        _new_yaw = _cur_yaw + _yaw_step

                        if not hasattr(_bot_veh, '_cur_speed'): _bot_veh._cur_speed = 0.0

                        if not hasattr(_bot_veh, '_ai_style'):
                            _st_r = _random.random()
                            if _st_r < 0.30:
                                _bot_veh._ai_style = 'aggressive'
                                _bot_veh._ai_style_spd = _random.uniform(1.03, 1.10)
                            elif _st_r < 0.75:
                                _bot_veh._ai_style = 'normal'
                                _bot_veh._ai_style_spd = _random.uniform(0.95, 1.02)
                            else:
                                _bot_veh._ai_style = 'timid'
                                _bot_veh._ai_style_spd = _random.uniform(0.78, 0.92)
                            _bot_veh._ai_style_phase = _random.uniform(0.0, 6.2832)
                        _bot_style_spd = _bot_veh._ai_style_spd
                        _bot_ts_now = BigWorld.time()

                        if abs(_tick_target_speed) > 2.0 and abs(_yaw_diff) < 0.5 and not _is_ai_reversing:
                            if (not hasattr(_bot_veh, '_ai_coast_until')
                                    or _bot_ts_now > _bot_veh._ai_coast_until):
                                if _random.random() < (_BOT_CRUISE_COAST_CHANCE * dt):
                                    _bot_veh._ai_coast_until = _bot_ts_now + _random.uniform(0.35, 0.85)
                                    _bot_veh._ai_coast_mult = _random.uniform(0.45, 0.65)
                            if hasattr(_bot_veh, '_ai_coast_until') and _bot_ts_now < _bot_veh._ai_coast_until:
                                _bot_style_spd *= _bot_veh._ai_coast_mult

                        if abs(_tick_target_speed) > 2.0:
                            _pulse_amp = 0.055 if _bot_veh._ai_style == 'aggressive' else 0.035
                            _bot_style_spd *= (1.0 + _pulse_amp * _math.sin(
                                _bot_ts_now * (1.1 + _bot_veh._ai_style_phase) + _bot_veh._ai_style_phase * 3.0))

                        if _is_ai_reversing:
                            _turn_pen = 1.0
                        else:
                            _turn_pen = 1.0 - _BOT_TURN_SPEED_PENALTY * min(1.0, abs(_yaw_diff) / 0.9)
                        _bot_target_spd = _tick_target_speed * _turn_pen * _bot_moveFactor * _bot_style_spd
                        _bot_accel, _bot_decel, _bot_coast, _ = self._compute_accel_decel(_bot_descr)
                        if _bot_moveFactor != 1.0:
                            _bot_mobFactor = _bot_moveFactor if _bot_moveFactor > 0.0 else 1.0
                            _bot_accel *= _bot_mobFactor
                            _bot_decel *= _bot_mobFactor
                            _bot_coast *= _bot_mobFactor
                        if abs(_bot_target_spd) < 0.35:
                            if abs(_bot_veh._cur_speed) < max(_bot_decel * dt, 0.01):
                                _bot_veh._cur_speed = 0.0
                            elif _bot_veh._cur_speed > 0:
                                _bot_veh._cur_speed = max(_bot_veh._cur_speed - _bot_decel * dt, 0.0)
                            else:
                                _bot_veh._cur_speed = min(_bot_veh._cur_speed + _bot_decel * dt, 0.0)
                        else:
                            _botAccelNow = _bot_accel * max(0.0, 1.0 - (abs(_bot_veh._cur_speed) / max(abs(_bot_target_spd), 0.35)) ** 1.5)
                            if _bot_target_spd > _bot_veh._cur_speed:
                                _bot_rate = _bot_decel if _bot_veh._cur_speed < 0 else _botAccelNow
                                _bot_veh._cur_speed = min(_bot_veh._cur_speed + _bot_rate * dt, _bot_target_spd)
                            elif _bot_target_spd < _bot_veh._cur_speed:
                                _bot_rate = _bot_decel if _bot_veh._cur_speed > 0 else _botAccelNow
                                _bot_veh._cur_speed = max(_bot_veh._cur_speed - _bot_rate * dt, _bot_target_spd)
                        _spd = _bot_veh._cur_speed

                        _cur_pos = _bot_mat.translation
                        _bot_prev_x = _cur_pos.x
                        _bot_prev_z = _cur_pos.z
                        _try_x = _cur_pos.x + _math.sin(_new_yaw) * _spd * dt
                        _try_z = _cur_pos.z + _math.cos(_new_yaw) * _spd * dt
                        _bot_actual_speed, _bot_actual_rot = _spd, (_yaw_step / dt if dt > 0.0001 else 0.0)

                        try:
                            _bthw = max(_bot_descr.chassis['topRightCarryingPoint'][0], 0.3)
                            _btsp = _bot_descr.physics.get('speedLimits', [10.0])
                            if isinstance(_btsp, (int, float)): _btsp = [_btsp, _btsp]
                            _btmax = max(_btsp[0], 0.001)
                        except Exception:
                            _bthw, _btmax = 1.5, 10.0
                        _bot_veh._ai_track_l = max(-1.0, min(1.0, (_spd - _bot_actual_rot * _bthw) / _btmax))
                        _bot_veh._ai_track_r = max(-1.0, min(1.0, (_spd + _bot_actual_rot * _bthw) / _btmax))

                        if abs(_tick_target_speed) > 0.5:
                            if not hasattr(_bot_veh, '_ai_progress_pos'):
                                _bot_veh._ai_progress_pos = (_cur_pos.x, _cur_pos.z)
                                _bot_veh._ai_progress_time = BigWorld.time()
                                _bot_veh._ai_progress_pos2 = (_cur_pos.x, _cur_pos.z)
                                _bot_veh._ai_progress_time2 = BigWorld.time()
                            else:
                                _prog_dx = _cur_pos.x - _bot_veh._ai_progress_pos[0]
                                _prog_dz = _cur_pos.z - _bot_veh._ai_progress_pos[1]
                                if (_prog_dx * _prog_dx + _prog_dz * _prog_dz) > _BOT_STUCK_RADIUS * _BOT_STUCK_RADIUS:
                                    _bot_veh._ai_progress_pos = (_cur_pos.x, _cur_pos.z)
                                    _bot_veh._ai_progress_time = BigWorld.time()
                                _fast_dx = _cur_pos.x - getattr(_bot_veh, '_ai_progress_pos2', (_cur_pos.x, _cur_pos.z))[0]
                                _fast_dz = _cur_pos.z - getattr(_bot_veh, '_ai_progress_pos2', (_cur_pos.x, _cur_pos.z))[1]
                                _fast_moved2 = _fast_dx * _fast_dx + _fast_dz * _fast_dz
                                if _fast_moved2 > _BOT_INVISIBLE_STUCK_RADIUS * _BOT_INVISIBLE_STUCK_RADIUS:
                                    _bot_veh._ai_progress_pos2 = (_cur_pos.x, _cur_pos.z)
                                    _bot_veh._ai_progress_time2 = BigWorld.time()
                                _is_fast_stuck = ((BigWorld.time() - getattr(_bot_veh, '_ai_progress_time2', BigWorld.time()) > _BOT_INVISIBLE_STUCK_TIME) and _fast_moved2 <= _BOT_INVISIBLE_STUCK_RADIUS * _BOT_INVISIBLE_STUCK_RADIUS)
                                _is_slow_stuck = (BigWorld.time() - _bot_veh._ai_progress_time > _BOT_STUCK_TIMEOUT)
                                _need_unstuck = (_is_fast_stuck or _is_slow_stuck) and not _is_ai_reversing
                                if _need_unstuck:
                                    _confirm_wall = _is_slow_stuck
                                    if not _confirm_wall:
                                        try:
                                            _chk_yaw = getattr(_bot_veh, '_ai_desired_yaw', _cur_yaw)
                                            _confirm_wall = _bot_see_invisible_wall(self.spaceID,
                                                _cur_pos.x, _cur_pos.y, _cur_pos.z, _chk_yaw)
                                        except Exception:
                                            _confirm_wall = True
                                    if _confirm_wall:
                                        _prog_bot_state = getattr(self, '_bot_state', None)
                                        _prog_st = _prog_bot_state.get(_bot_vehID) if _prog_bot_state is not None else None
                                        if _prog_st is not None:
                                            _prog_st['bad_waypoint'] = _prog_st.get('waypoint')
                                            _prog_st['bad_waypoint_time'] = BigWorld.time()
                                            _prog_st['wp_timer'] = 0.0
                                        try:
                                            _prog_yaw = getattr(_bot_veh, '_ai_desired_yaw', _cur_yaw)
                                        except Exception:
                                            _prog_yaw = _cur_yaw
                                        _bot_trigger_unstuck_reverse(_bot_veh, _cur_pos.x, _cur_pos.z, _prog_yaw,
                                            'no-progress %.1fs' % (BigWorld.time() - _bot_veh._ai_progress_time))
                                        _bot_veh._ai_progress_pos = (_cur_pos.x, _cur_pos.z)
                                        _bot_veh._ai_progress_time = BigWorld.time()
                                        _bot_veh._ai_progress_pos2 = (_cur_pos.x, _cur_pos.z)
                                        _bot_veh._ai_progress_time2 = BigWorld.time()
                        elif hasattr(_bot_veh, '_ai_progress_pos'):
                            del _bot_veh._ai_progress_pos
                            del _bot_veh._ai_progress_time
                            if hasattr(_bot_veh, '_ai_progress_pos2'):
                                del _bot_veh._ai_progress_pos2
                            if hasattr(_bot_veh, '_ai_progress_time2'):
                                del _bot_veh._ai_progress_time2

                        if not _is_ai_reversing and abs(_tick_target_speed) > 2.0:
                            _sw_t = BigWorld.time()
                            _sw_data = getattr(_bot_veh, '_ai_slope_wp', None)
                            if _sw_data is None:
                                _bot_veh._ai_slope_wp = (_cur_pos.x, _cur_pos.z, _cur_pos.y, _sw_t)
                            else:
                                _swx, _swz, _swy, _swt = _sw_data
                                _sw_h = _math.sqrt((_cur_pos.x - _swx) * (_cur_pos.x - _swx) + (_cur_pos.z - _swz) * (_cur_pos.z - _swz))
                                _sw_rise = _cur_pos.y - _swy
                                if _sw_h >= 5.0 or (_sw_t - _swt) >= _BOT_SLOPE_STUCK_WINDOW:
                                    _slope_stuck_now = False
                                    if (_sw_t - _swt) >= _BOT_SLOPE_STUCK_WINDOW and _sw_h < _BOT_SLOPE_STUCK_HORIZ and _sw_rise >= _BOT_SLOPE_STUCK_RISE:
                                        _slope_stuck_now = True
                                    _bot_veh._ai_slope_wp = (_cur_pos.x, _cur_pos.z, _cur_pos.y, _sw_t)
                                    if _slope_stuck_now:
                                        _prog_bot_state = getattr(self, '_bot_state', None)
                                        _prog_st = _prog_bot_state.get(_bot_vehID) if _prog_bot_state is not None else None
                                        if _prog_st is not None:
                                            _prog_st['bad_waypoint'] = _prog_st.get('waypoint')
                                            _prog_st['bad_waypoint_time'] = _sw_t
                                            _prog_st['wp_timer'] = 0.0
                                            _slope_ang = _math.atan2(_cur_pos.x - _swx, _cur_pos.z - _swz)
                                            _slope_dirs = [d for d in (_prog_st.get('_slope_avoid_dirs') or []) if d[1] > _sw_t]
                                            _slope_dirs.append([_slope_ang, _sw_t + 45.0])
                                            _prog_st['_slope_avoid_dirs'] = _slope_dirs[-4:]
                                            _nav_log(('slope', _bot_vehID),
                                                "[BATTLE][NAV] bot %d slope-stuck (rise=%.1fm horiz=%.1fm dir=%.0f deg) - parking" % (
                                                _bot_vehID, _sw_rise, _sw_h, _math.degrees(_slope_ang)))
                                        _bot_park(_bot_veh, 10.0)
                        elif hasattr(_bot_veh, '_ai_slope_wp'):
                            del _bot_veh._ai_slope_wp

                        _blocked = False
                        _impact_kind = None
                        _impact_dy = None
                        _bot_hill_now = False
                        if _phys_lod == 'full' and abs(_spd) > 0.5:
                            try:
                                _bot_hill_hl = max(_bot_descr.chassis['topRightCarryingPoint'][1], 1.0)
                            except Exception:
                                _bot_hill_hl = 2.5
                            _bot_hill_now = _nav_drivable_rise(self.spaceID,
                                _cur_pos.x, _cur_pos.z,
                                _cur_pos.y,
                                _new_yaw if _spd >= 0 else (_new_yaw + _math.pi),
                                _bot_hill_hl + 2.0, 1.0)
                        if _phys_lod == 'full' and abs(_spd) > 0.5 and not _bot_hill_now:
                            _step_dx = _try_x - _bot_prev_x
                            _step_dz = _try_z - _bot_prev_z
                            _step_dist = _math.sqrt(_step_dx * _step_dx + _step_dz * _step_dz)
                            if _step_dist > 1.2:
                                _n_samples = max(2, int(_step_dist / 1.2))
                                for _si in range(1, _n_samples + 1):
                                    _t = float(_si) / (_n_samples + 1)
                                    _sx = _bot_prev_x + _step_dx * _t
                                    _sz = _bot_prev_z + _step_dz * _t
                                    _segRes = BigWorld.wg_collideSegment(self.spaceID,
                                        Math.Vector3(_sx, _cur_pos.y + 3.0, _sz),
                                        Math.Vector3(_sx, _cur_pos.y - 3.0, _sz), 128)
                                    if _segRes is not None:
                                        _destr_seg = self._vehicle_try_break_destructible(_segRes, _new_yaw, False)
                                        if _destr_seg == 'soft':
                                            pass
                                        else:
                                            if _destr_seg is None and _segRes[1].y < _bot_descr.chassis.get('minPlaneNormalY', 0.766):
                                                try:
                                                    _seg_dy = _segRes[0].y - _cur_pos.y
                                                    _seg_allow = max(_BOT_CLIMB_STEP, abs(_spd) * dt * 0.85)
                                                except Exception:
                                                    _seg_dy = 99.0
                                                    _seg_allow = _BOT_CLIMB_STEP
                                                if _seg_dy <= _seg_allow:
                                                    if _nav_step_blocked(_seg_dy, _seg_allow):
                                                        _blocked = True
                                                        break
                                                else:
                                                    _impact_kind = 'pushable'
                                                    _impact_hx = _segRes[0].x
                                                    _impact_hz = _segRes[0].z
                                                    _impact_dy = _seg_dy
                                                    break
                                            elif _destr_seg == 'hard':
                                                _blocked = True
                                                break
                                            else:
                                                _blocked = True
                                                break
                                    _segFwd = BigWorld.wg_collideSegment(self.spaceID,
                                        Math.Vector3(_sx, _cur_pos.y + _BOT_CLIMB_STEP + 0.3, _sz),
                                        Math.Vector3(_sx + (_step_dx/_step_dist*2.0 if _step_dist>0.001 else 0),
                                                     _cur_pos.y + _BOT_CLIMB_STEP + 0.3,
                                                     _sz + (_step_dz/_step_dist*2.0 if _step_dist>0.001 else 0)), 128)
                                    if _segFwd is not None and not _seg_hit_is_ground(_segFwd, 0.40, _cur_pos.y):
                                        self._vehicle_try_break_destructible(_segFwd, _new_yaw, False)
                                        if _destr_whisker_blocks(self.spaceID, _segFwd[2], _segFwd[4], _segFwd[5]):
                                            _sfw_dx = _segFwd[0].x - _sx
                                            _sfw_dz = _segFwd[0].z - _sz
                                            if (_sfw_dx * _sfw_dx + _sfw_dz * _sfw_dz) <= 2.5 * 2.5 and _impact_kind is None:
                                                _impact_kind = 'pushable'
                                                _impact_hx = _segFwd[0].x
                                                _impact_hz = _segFwd[0].z
                                            break

                        if not _blocked and _impact_kind is None and not _bot_hill_now:
                            try:
                                _minPlaneY = _bot_descr.chassis.get('minPlaneNormalY', 0.766)
                                _wallRes = BigWorld.wg_collideSegment(self.spaceID,
                                    Math.Vector3(_try_x, _cur_pos.y + 3.0, _try_z),
                                    Math.Vector3(_try_x, _cur_pos.y - 3.0, _try_z), 128)

                                _destr_kind = self._vehicle_try_break_destructible(_wallRes, _new_yaw, False)
                                if _destr_kind == 'soft':
                                    pass
                                elif _destr_kind == 'hard':
                                    _blocked = True
                                elif _wallRes is not None and _wallRes[1].y < _minPlaneY:
                                    try:
                                        _ctr_dy = _wallRes[0].y - _cur_pos.y
                                        _ctr_allow = max(_BOT_CLIMB_STEP, abs(_spd) * dt * 0.85)
                                    except Exception:
                                        _ctr_dy = 99.0
                                        _ctr_allow = _BOT_CLIMB_STEP
                                    if _ctr_dy <= _ctr_allow:
                                        if _nav_step_blocked(_ctr_dy, _ctr_allow):
                                            _blocked = True
                                        if not _blocked:
                                            _bot_climb_ref = getattr(_bot_veh, '_ai_climb_ref', None)
                                            _bot_veh._ai_climb_ref, _bot_climb_blocked = _nav_track_climb(
                                                _bot_climb_ref, _try_x, _try_z, _wallRes[0].y)
                                            if _bot_climb_blocked:
                                                _blocked = True
                                    else:
                                        _impact_kind = 'pushable'
                                        _impact_hx = _wallRes[0].x
                                        _impact_hz = _wallRes[0].z
                                        _impact_dy = _ctr_dy
                                else:
                                    if _wallRes is not None:
                                        _bot_step_horiz = abs(_spd) * dt
                                        _bot_max_step = max(_BOT_CLIMB_STEP, _bot_step_horiz * 0.85)
                                        if _nav_step_blocked(_wallRes[0].y - _cur_pos.y, _bot_max_step):
                                            _blocked = True
                                    if not _blocked and _wallRes is not None:
                                        _bot_climb_ref = getattr(_bot_veh, '_ai_climb_ref', None)
                                        _bot_veh._ai_climb_ref, _bot_climb_blocked = _nav_track_climb(
                                            _bot_climb_ref, _try_x, _try_z, _wallRes[0].y)
                                        if _bot_climb_blocked:
                                            _blocked = True

                                if not _blocked and _impact_kind is None and not _bot_hill_now:
                                    try:
                                        _b_hw_side = max(_bot_descr.chassis['topRightCarryingPoint'][0], 1.0)
                                        _b_rx = _math.cos(_new_yaw)
                                        _b_rz = -_math.sin(_new_yaw)
                                        for _b_side in (-1.0, 1.0):
                                            _btx = _try_x + _b_rx * _b_hw_side * _b_side
                                            _btz = _try_z + _b_rz * _b_hw_side * _b_side
                                            _bSideRes = BigWorld.wg_collideSegment(self.spaceID,
                                                Math.Vector3(_btx, _cur_pos.y + 3.0, _btz),
                                                Math.Vector3(_btx, _cur_pos.y - 3.0, _btz), 128)
                                            if _bSideRes is None:
                                                continue
                                            _b_side_destr = self._vehicle_try_break_destructible(_bSideRes, _new_yaw, False)
                                            if _b_side_destr == 'soft':
                                                continue
                                            elif _b_side_destr == 'hard':
                                                _impact_kind = 'destr_hard'
                                                _impact_hx = _bSideRes[0].x
                                                _impact_hz = _bSideRes[0].z
                                                break
                                            elif _bSideRes[1].y < _minPlaneY:
                                                try:
                                                    _side_dy = _bSideRes[0].y - _cur_pos.y
                                                    _side_allow = max(_BOT_CLIMB_STEP, abs(_spd) * dt * 0.85)
                                                except Exception:
                                                    _side_dy = 99.0
                                                    _side_allow = _BOT_CLIMB_STEP
                                                if _side_dy <= _side_allow:
                                                    if _nav_step_blocked(_side_dy, _side_allow):
                                                        _blocked = True
                                                        break
                                                    _b_side_ref = getattr(_bot_veh, '_ai_climb_ref', None)
                                                    _b_side_ref, _b_side_climb = _nav_track_climb(_b_side_ref, _btx, _btz, _bSideRes[0].y)
                                                    if _b_side_climb:
                                                        _blocked = True
                                                        break
                                                    _bot_veh._ai_climb_ref = _b_side_ref
                                                else:
                                                    _impact_kind = 'pushable'
                                                    _impact_hx = _bSideRes[0].x
                                                    _impact_hz = _bSideRes[0].z
                                                    _impact_dy = _side_dy
                                                    break
                                            else:
                                                _b_side_step = max(_BOT_CLIMB_STEP, abs(_spd) * dt * 0.85)
                                                if _nav_step_blocked(_bSideRes[0].y - _cur_pos.y, _b_side_step):
                                                    _blocked = True
                                                    break
                                                _b_side_ref = getattr(_bot_veh, '_ai_climb_ref', None)
                                                _b_side_ref, _b_side_climb = _nav_track_climb(_b_side_ref, _btx, _btz, _bSideRes[0].y)
                                                if _b_side_climb:
                                                    _blocked = True
                                                    break
                                                _bot_veh._ai_climb_ref = _b_side_ref
                                    except Exception:
                                        pass

                                if not _blocked and _impact_kind is None:
                                    pass

                            except Exception:
                                pass

                        _push_dx, _push_dz = 0.0, 0.0
                        _blocked_by_tank = False
                        _blocked_tank_id = None
                        if not _blocked:
                            _bot_hw, _bot_hl = _hull_extents(_bot_descr)
                            _veh_lookahead = 4.5
                            _look_x = _cur_pos.x + _math.sin(_new_yaw) * _veh_lookahead
                            _look_z = _cur_pos.z + _math.cos(_new_yaw) * _veh_lookahead
                            _my_id = self.playerAvatar.playerVehicleID if self.playerAvatar else None
                            for _ovID, _, _ in self.vehicles:
                                if _ovID == _bot_vehID: continue
                                _oveh = BigWorld.entity(_ovID)
                                if _oveh is None or not getattr(_oveh, 'isStarted', False): continue
                                _oom = getattr(_oveh, '_offline_matrix', None)
                                _opos = _oom.translation if _oom is not None else _oveh.position
                                _oyaw = _entity_yaw(_oveh, _oom)
                                _odescr = getattr(_oveh, 'typeDescriptor', None)
                                _o_hw, _o_hl = _hull_extents(_odescr) if _odescr is not None else (1.5, 2.5)
                                _dx_now = _cur_pos.x - _opos.x; _dz_now = _cur_pos.z - _opos.z
                                _mtv_now = _obb_mtv(_cur_pos.x, _cur_pos.z, _cur_yaw, _bot_hw, _bot_hl,
                                                         _opos.x, _opos.z, _oyaw, _o_hw, _o_hl)
                                _bmesh = None
                                if _phys_lod == 'full' and ((_mtv_now is not None) or (_dx_now * _dx_now + _dz_now * _dz_now < 64.0)):
                                    try:
                                        _bmesh = _tanks_mesh_overlap(
                                                _bot_veh, _cur_pos.x, _cur_pos.y, _cur_pos.z, _cur_yaw,
                                                getattr(_bot_veh, '_cur_pitch', 0.0), getattr(_bot_veh, '_cur_roll', 0.0),
                                                _oveh, _opos.x, _opos.y, _opos.z, _oyaw,
                                                getattr(_oveh, '_cur_pitch', 0.0), getattr(_oveh, '_cur_roll', 0.0))
                                    except Exception:
                                        _bmesh = None
                                if _bmesh is False:
                                    pass
                                elif _bmesh is True and _mtv_now is None:
                                    _bln = _math.sqrt(_dx_now * _dx_now + _dz_now * _dz_now)
                                    if _bln < 0.001:
                                        _mtv_now = (1.0, 0.0, 0.4)
                                    else:
                                        _mtv_now = (_dx_now / _bln, _dz_now / _bln, 0.4)
                                elif _bmesh is True and _mtv_now is not None:
                                    _mtv_now = (_mtv_now[0], _mtv_now[1], max(_mtv_now[2], 0.20))
                                _close_now = _mtv_now is not None
                                _close_ahead = False
                                try:
                                    _close_ahead = _obb_mtv(_look_x, _look_z, _new_yaw, _bot_hw, _bot_hl,
                                                         _opos.x, _opos.z, _oyaw, _o_hw, _o_hl) is not None
                                except Exception:
                                    pass
                                if _close_now:
                                    _blocked_by_tank = True
                                    _blocked_tank_id = _ovID
                                    if not hasattr(_bot_veh, '_ai_avoid_side'):
                                        _rx, _rz = _math.cos(_new_yaw), -_math.sin(_new_yaw)
                                        _side_dot = -_dx_now * _rx + -_dz_now * _rz
                                        _bot_veh._ai_avoid_side = -1 if _side_dot > 0 else 1
                                    if _ovID != _my_id:
                                        _pnx, _pnz, _poverlap = _mtv_now
                                        _o_mass = _vehicle_mass(_odescr) if _odescr is not None else 15000.0
                                        _my_push = _o_mass / (_vehicle_mass(_bot_descr) + _o_mass)
                                        _push_dx += _pnx * _poverlap * _my_push * 0.85
                                        _push_dz += _pnz * _poverlap * _my_push * 0.85
                                        try:
                                            if _oveh is not None and hasattr(_oveh, '_offline_matrix'):
                                                _otm = _oveh._offline_matrix
                                                _ott = _otm.translation
                                                _otm.translation = Math.Vector3(
                                                    _ott.x - _pnx * _poverlap * (1.0 - _my_push),
                                                    _ott.y,
                                                    _ott.z - _pnz * _poverlap * (1.0 - _my_push))
                                        except Exception:
                                            pass
                                    if _close_now:
                                        try:
                                            _b_v = _veh_velocity_2d(_bot_veh)
                                            _o_v = _veh_velocity_2d(_oveh)
                                            _nx_ram, _nz_ram, _ = _mtv_now
                                            _vn_ram = (_b_v.x - _o_v.x)*_nx_ram + (_b_v.z - _o_v.z)*_nz_ram
                                            _bspd = abs(float(getattr(_bot_veh, '_cur_speed', 0.0) or 0.0))
                                            if abs(_vn_ram) < _bspd:
                                                _vn_ram = _bspd
                                            if abs(_vn_ram) > _RAM_SAFE_SPEED:
                                                _rk2 = tuple(sorted((_bot_vehID, _ovID)))
                                                _now2 = BigWorld.time()
                                                if _now2 - _g_offh_ram_cd.get(_rk2, -10) >= _RAM_CD:
                                                    _g_offh_ram_cd[_rk2] = _now2
                                                    _b_mass = _vehicle_mass(_bot_descr)
                                                    _o_mass2 = _vehicle_mass(_odescr) if _odescr else 15000.0
                                                    _hit_pt2 = Math.Vector3(
                                                        0.5 * (_cur_pos.x + _opos.x),
                                                        0.5 * (_cur_pos.y + _opos.y),
                                                        0.5 * (_cur_pos.z + _opos.z))
                                                    self._apply_ramming(_bot_veh, _oveh, _nx_ram, _nz_ram, _hit_pt2, _b_mass, _o_mass2, _vn_ram)
                                        except Exception:
                                            pass
                                    break

                        if _impact_kind is not None:
                            _low_press = False
                            try:
                                if _impact_kind == 'pushable' and _impact_dy is not None and _impact_dy <= _BOT_CLIMB_STEP * 1.5:
                                    _low_press = True
                            except Exception:
                                pass
                            if _low_press:
                                _impact_kind = None
                                try:
                                    _bot_veh._cur_speed *= 0.85
                                except Exception:
                                    pass
                            else:
                                _blocked = True
                                _blocked_by_tank = False
                        if _blocked:
                            _is_tank_block = _blocked_by_tank
                            _tank_waiter = False
                            if _is_tank_block:
                                try:
                                    _wait_my_id = self.playerAvatar.playerVehicleID if self.playerAvatar else None
                                except Exception:
                                    _wait_my_id = None
                                _is_vs_player = (_blocked_tank_id == _wait_my_id)
                                _tank_waiter = _is_vs_player
                                if _tank_waiter and not _is_vs_player:
                                    try:
                                        _wait_since = getattr(_bot_veh, '_ai_wait_since', None)
                                        _wait_key = getattr(_bot_veh, '_ai_wait_key', None)
                                        _now_w = BigWorld.time()
                                        if _wait_key != _blocked_tank_id or _wait_since is None:
                                            _bot_veh._ai_wait_since = _now_w
                                            _bot_veh._ai_wait_key = _blocked_tank_id
                                        elif _now_w - _wait_since > 2.0:
                                            _tank_waiter = False
                                            try:
                                                del _bot_veh._ai_wait_since
                                            except Exception:
                                                pass
                                            try:
                                                del _bot_veh._ai_wait_key
                                            except Exception:
                                                pass
                                    except Exception:
                                        pass
                                if _tank_waiter:
                                    _bot_veh._ai_hold_until = BigWorld.time() + 1.2
                                    _try_x = _bot_prev_x + _push_dx
                                    _try_z = _bot_prev_z + _push_dz
                                    _bot_veh._cur_speed = 0.0
                                    if hasattr(_bot_veh, '_ai_blocked_since'): del _bot_veh._ai_blocked_since
                                    if hasattr(_bot_veh, '_ai_stuck_since'): del _bot_veh._ai_stuck_since
                                    _bot_veh._ai_progress_pos = (_try_x, _try_z)
                                    _bot_veh._ai_progress_time = BigWorld.time()
                                    _bot_veh._ai_progress_pos2 = (_try_x, _try_z)
                                    _bot_veh._ai_progress_time2 = BigWorld.time()
                            if not (_is_tank_block and _tank_waiter):
                                _bot_veh._ai_hold_until = BigWorld.time() + _BOT_HOLD_AFTER_BLOCK
                                _try_x = _bot_prev_x + _push_dx
                                _try_z = _bot_prev_z + _push_dz
                                _bot_veh._cur_speed = 0.0
                                if not _is_tank_block:
                                    try:
                                        _bfx = Math.Vector3(_cur_pos.x, _cur_pos.y, _cur_pos.z)
                                        if _impact_kind is not None:
                                            _bfx = Math.Vector3(_impact_hx, _cur_pos.y, _impact_hz)
                                        _offline_play_collision_fx(_bot_veh, _bfx)
                                    except Exception:
                                        pass

                            if not (_is_tank_block and _tank_waiter):
                                if (not hasattr(_bot_veh, '_ai_avoid_scan_time')
                                        or BigWorld.time() > _bot_veh._ai_avoid_scan_time):
                                    _pf = getattr(_bot_veh, '_ai_feeler_yaw', None)
                                    if _pf is not None and BigWorld.time() < getattr(_bot_veh, '_ai_feeler_until', 0.0):
                                        _bot_veh._ai_avoid_target_yaw = _pf
                                        _bot_veh._ai_avoid_scan_time = BigWorld.time() + _BOT_AVOID_RESCAN_INTERVAL
                                    else:
                                        _scan_off = _nav_scan_best_heading(self.spaceID, _cur_pos.x, _cur_pos.y, _cur_pos.z, _cur_yaw)
                                        _bot_veh._ai_avoid_target_yaw = _cur_yaw + _scan_off
                                        _bot_veh._ai_avoid_scan_time = BigWorld.time() + _BOT_AVOID_RESCAN_INTERVAL
                                        _bot_veh._ai_avoid_side = 1 if _scan_off >= 0 else -1

                                if not hasattr(_bot_veh, '_ai_blocked_since'): _bot_veh._ai_blocked_since = BigWorld.time()
                                _blocked_dur = BigWorld.time() - _bot_veh._ai_blocked_since
                                _evade_mult = 1.0 + min(_blocked_dur, 2.5) * 0.6

                                _rb_is_reversing = BigWorld.time() < getattr(_bot_veh, '_ai_reverse_until', 0.0)
                                _avoid_yaw_diff = (_bot_veh._ai_avoid_target_yaw - _cur_yaw + _math.pi) % (2 * _math.pi) - _math.pi
                                _avoid_max_step = 1.2 * _evade_mult * dt
                                if not _rb_is_reversing:
                                    _new_yaw = _cur_yaw + max(-_avoid_max_step, min(_avoid_max_step, _avoid_yaw_diff))

                                if (_avoid_yaw_diff > 2.0 or _avoid_yaw_diff < -2.0) and not _rb_is_reversing                                        and BigWorld.time() >= getattr(_bot_veh, '_ai_reverse_blocked_until', 0.0):
                                    _bot_trigger_unstuck_reverse(_bot_veh, _cur_pos.x, _cur_pos.z, _cur_yaw,
                                        'blocked-avoid %.0f deg' % int(_math.degrees(_avoid_yaw_diff)))
                                    _bot_veh._ai_stuck_since = BigWorld.time()

                                if not hasattr(_bot_veh, '_ai_stuck_since'):
                                    _bot_veh._ai_stuck_since = BigWorld.time()
                                elif BigWorld.time() - _bot_veh._ai_stuck_since > _BOT_REVERSE_AFTER_BLOCKED                                        and not _rb_is_reversing                                        and BigWorld.time() >= getattr(_bot_veh, '_ai_reverse_blocked_until', 0.0):
                                    _stuck_bot_state = getattr(self, '_bot_state', None)
                                    _stuck_st = _stuck_bot_state.get(_bot_vehID) if _stuck_bot_state is not None else None
                                    if _stuck_st is not None:
                                        _stuck_st['bad_waypoint'] = _stuck_st.get('waypoint')
                                        _stuck_st['bad_waypoint_time'] = BigWorld.time()
                                        _stuck_st['wp_timer'] = 0.0
                                    try:
                                        _stuck_prog_yaw = getattr(_bot_veh, '_ai_desired_yaw', _cur_yaw)
                                    except Exception:
                                        _stuck_prog_yaw = _cur_yaw
                                    _bot_trigger_unstuck_reverse(_bot_veh, _cur_pos.x, _cur_pos.z, _stuck_prog_yaw,
                                        'blocked %.1fs' % _BOT_REVERSE_AFTER_BLOCKED)
                                    try:
                                        if hasattr(_bot_veh, '_ai_avoid_side'):
                                            _sd = _bot_veh._ai_avoid_side
                                            _bot_veh._ai_reverse_side = _sd
                                            _turn_deg2 = _random.uniform(_BOT_UNSTUCK_TURN_DEG_MIN, _BOT_UNSTUCK_TURN_DEG_MAX)
                                            _bot_veh._ai_post_reverse_turn = _cur_yaw + _sd * _math.radians(_turn_deg2)
                                    except Exception:
                                        pass
                                    _bot_veh._ai_stuck_since = BigWorld.time()
                        else:
                            if _push_dx != 0.0 or _push_dz != 0.0:
                                _try_x += _push_dx
                                _try_z += _push_dz
                            if hasattr(_bot_veh, '_ai_blocked_since'): del _bot_veh._ai_blocked_since
                            if hasattr(_bot_veh, '_ai_avoid_side'): del _bot_veh._ai_avoid_side
                            if hasattr(_bot_veh, '_ai_stuck_since'): del _bot_veh._ai_stuck_since
                            if hasattr(_bot_veh, '_ai_avoid_scan_time'): del _bot_veh._ai_avoid_scan_time
                            if hasattr(_bot_veh, '_ai_avoid_target_yaw'): del _bot_veh._ai_avoid_target_yaw

                        _bot_mat.setRotateYPR((_new_yaw, 0, 0))
                        try:
                            _bot_mat.translation = Math.Vector3(_bot_prev_x, _cur_pos.y, _bot_prev_z)
                        except Exception:
                            pass

                        _bot_contact_clamped = False
                        if abs(_spd) > 0.05:
                            try:
                                _bchw = max(_bot_descr.chassis['topRightCarryingPoint'][0], 1.0)
                                _bchl = max(_bot_descr.chassis['topRightCarryingPoint'][1], 1.0)
                            except Exception:
                                _bchw, _bchl = 1.5, 2.5
                            _bc_contact_len = 1.4
                            _bc_ray_yaw = _new_yaw if _spd > 0 else (_new_yaw + _math.pi)
                            _bc_perp = _bc_ray_yaw + _math.pi / 2.0
                            for _bc_off in (0.0, _bchw * 0.9, -_bchw * 0.9, _bchw * 0.45, -_bchw * 0.45):
                                try:
                                    _bc_sx = _bot_prev_x + _math.sin(_bc_perp) * _bc_off + _math.sin(_bc_ray_yaw) * _bchl
                                    _bc_sz = _bot_prev_z + _math.cos(_bc_perp) * _bc_off + _math.cos(_bc_ray_yaw) * _bchl
                                    _bc_ex = _bc_sx + _math.sin(_bc_ray_yaw) * _bc_contact_len
                                    _bc_ez = _bc_sz + _math.cos(_bc_ray_yaw) * _bc_contact_len
                                    _bc_fwd = BigWorld.wg_collideSegment(self.spaceID,
                                        Math.Vector3(_bc_sx, _cur_pos.y + _BOT_CLIMB_STEP + 0.3, _bc_sz),
                                        Math.Vector3(_bc_ex, _cur_pos.y + _BOT_CLIMB_STEP + 0.3, _bc_ez), 128)
                                    if _bc_fwd is not None and _seg_hit_is_ground(_bc_fwd, _min_plane_ny(_bot_descr)):
                                        continue
                                    if _bc_fwd is not None and not _destr_whisker_blocks(self.spaceID, _bc_fwd[2], _bc_fwd[4], _bc_fwd[5]):
                                        continue
                                    if _bc_fwd is not None:
                                        _bc_rs = _math.sin(_bc_ray_yaw); _bc_rc = _math.cos(_bc_ray_yaw)
                                        _bc_hitD = max(0.0, min(_bc_contact_len,
                                            (_bc_fwd[0].x - _bc_sx) * _bc_rs + (_bc_fwd[0].z - _bc_sz) * _bc_rc))
                                        _try_x = _bot_prev_x + _bc_rs * _bc_hitD
                                        _try_z = _bot_prev_z + _bc_rc * _bc_hitD
                                        _bot_contact_clamped = True
                                        break
                                except Exception:
                                    pass

                        if (_push_dx != 0.0 or _push_dz != 0.0) and not _bot_contact_clamped:
                            try:
                                _bcp_probe = BigWorld.wg_collideSegment(self.spaceID,
                                    Math.Vector3(_try_x, _cur_pos.y + 3.0, _try_z),
                                    Math.Vector3(_try_x, _cur_pos.y - 3.0, _try_z), 128)
                                if _bcp_probe is not None and _bcp_probe[1].y < 0.40:
                                    _bcp_ref = getattr(_bot_veh, '_ai_climb_ref', None)
                                    _bcp_blocked = _nav_step_blocked(_bcp_probe[0].y - _cur_pos.y, _BOT_CLIMB_STEP)
                                    if not _bcp_blocked:
                                        _bcp_ref, _bcp_blocked = _nav_track_climb(_bcp_ref, _try_x, _try_z, _bcp_probe[0].y)
                                    if _bcp_blocked:
                                        _try_x = _bot_prev_x
                                        _try_z = _bot_prev_z
                                    else:
                                        _bot_veh._ai_climb_ref = _bcp_ref
                            except Exception:
                                pass

                        try:
                            _btrc0 = _bot_descr.chassis['topRightCarryingPoint']
                            _bf_rad = max(float(_btrc0[0]), float(_btrc0[1]), 1.0)
                        except Exception:
                            _bf_rad = 2.0
                        if _phys_lod != 'far':
                            _btfx, _btfz, _btf_touch = self._clamp_off_building_fields(self.spaceID, _bot_prev_x, _bot_prev_z, _try_x, _try_z, _bf_rad)
                            if _btf_touch:
                                _try_x = _btfx
                                _try_z = _btfz
                                _bot_veh._cur_speed = 0.0

                        _bot_mat.translation = Math.Vector3(_try_x, _cur_pos.y, _try_z)

                    try:
                        _btrc = _bot_descr.chassis['topRightCarryingPoint']
                        _bhL, _bhW = max(_btrc[1], 1.0), max(_btrc[0], 0.5)
                    except Exception: _bhL, _bhW = 2.5, 1.5
                    _byaw = _bot_mat.yaw
                    _bfx, _bfz = _math.sin(_byaw), _math.cos(_byaw)
                    _brx, _brz = _math.cos(_byaw), -_math.sin(_byaw)
                    _bp = _bot_mat.translation
                    if _phys_lod != 'far':
                        _tp, _tr, _tL, _tW, _bhf, _bhb = _tilt_target_pitch_roll(
                            self.spaceID, _bp, _byaw, _bot_descr)
                    else:
                        _tp = getattr(_bot_veh, '_cur_pitch', 0.0)
                        _tr = getattr(_bot_veh, '_cur_roll', 0.0)
                        _bhf = _bp.y
                        _bhb = _bp.y
                    _bot_veh._last_h_front = _bhf
                    _bot_veh._last_h_back  = _bhb
                    _bot_veh._last_half_L  = _bhL
                    _b_cur_p = getattr(_bot_veh, '_cur_pitch', 0.0)
                    _b_cur_r = getattr(_bot_veh, '_cur_roll', 0.0)
                    _b_cur_p += (_tp - _b_cur_p) * _TILT_STIFFNESS * dt
                    _b_cur_r += (_tr - _b_cur_r) * _TILT_STIFFNESS * dt
                    _bot_veh._cur_pitch = _b_cur_p
                    _bot_veh._cur_roll = _b_cur_r
                    _bot_mat.setRotateYPR((_byaw, _b_cur_p, _b_cur_r))
                    _b_hang_y, _b_hang_block = _hull_hang_snap(
                        self.spaceID, _bp.x, _bp.z, _byaw, _bhW, _bhL, _bp.y, 2.2, _min_plane_ny(_bot_descr))
                    if _b_hang_y is not None:
                        try:
                            _b_hang_y = _carrying_sit_y(
                                self.spaceID, _bp.x, _bp.z, _byaw,
                                _b_cur_p, _b_cur_r, _bhW, _bhL, _b_hang_y,
                                _min_plane_ny(_bot_descr))
                        except Exception:
                            pass
                    _b_keep_y = _b_hang_y if _b_hang_y is not None else _bp.y
                    try:
                        _bot_mat.translation = Math.Vector3(_bp.x, _b_keep_y, _bp.z)
                    except Exception:
                        pass
                    if _b_hang_block:
                        try:
                            _bot_mat.translation = Math.Vector3(_bot_prev_x, _b_hang_y if _b_hang_y is not None else _bp.y, _bot_prev_z)
                        except Exception:
                            try:
                                _bot_mat.translation = Math.Vector3(_bp.x, _bp.y, _bp.z)
                            except Exception:
                                pass
                        try:
                            _bot_veh._cur_speed = 0.0
                        except Exception:
                            pass
                    try:
                        _sfx, _sfz = _bot_prev_x, _bot_prev_z
                    except Exception:
                        _sfx, _sfz = _bp.x, _bp.z

                    if hasattr(_bot_veh, '_ai_desired_turret_yaw'):
                        _t_yaw = getattr(_bot_veh, '_bot_turret_yaw_cur', 0.0)
                        _g_pitch = getattr(_bot_veh, '_bot_gun_pitch_cur', 0.0)

                        _t_diff = (_bot_veh._ai_desired_turret_yaw - _t_yaw + _math.pi) % (2 * _math.pi) - _math.pi
                        _t_speed = _bot_descr.turret['rotationSpeed'] * dt
                        _t_yaw += max(-_t_speed, min(_t_speed, _t_diff))

                        try:
                            _t_yl = _bot_descr.turret.get('yawLimits')
                            if _t_yl is not None:
                                _t_yaw = max(_t_yl[0], min(_t_yl[1], _t_yaw))
                        except Exception: pass

                        _g_diff = (_bot_veh._ai_desired_gun_pitch - _g_pitch + _math.pi) % (2 * _math.pi) - _math.pi
                        _g_speed = _bot_descr.turret['rotationSpeed'] * 0.5 * dt
                        _g_pitch += max(-_g_speed, min(_g_speed, _g_diff))

                        try:
                            _gun_limits = _bot_descr.gun['pitchLimits']
                            _min_pitch = _gun_limits[0] if isinstance(_gun_limits, list) else -0.3
                            _max_pitch = _gun_limits[1] if isinstance(_gun_limits, list) else 0.3
                            _g_pitch = max(min(_g_pitch, _max_pitch), _min_pitch)
                        except Exception: pass

                        _bot_veh._bot_turret_yaw_cur = _t_yaw
                        _bot_veh._bot_gun_pitch_cur = _g_pitch

                        if not hasattr(_bot_veh, '_bot_turret_mat'):
                            _bot_veh._bot_turret_mat = Math.Matrix(); _bot_veh._bot_gun_mat = Math.Matrix()
                            if getattr(_bot_veh, 'appearance', None):
                                _bot_veh.appearance.turretMatrix.target = _bot_veh._bot_turret_mat
                                _bot_veh.appearance.gunMatrix.target = _bot_veh._bot_gun_mat

                        _bot_veh._bot_turret_mat.setRotateY(_t_yaw)
                        _bot_veh._bot_gun_mat.setRotateX(_g_pitch)

                    try: _bot_flt.newPosition(_bot_mat.translation, _bot_mat, BigWorld.time(), _bot_actual_speed, _bot_actual_rot)
                    except Exception: pass
                    try: _bot_flt.setInitialSpeeds(_bot_actual_speed, _bot_actual_rot)
                    except Exception: pass
                    try:
                        _stamp_entity_pose(_bot_veh, _bot_mat)
                    except Exception:
                        pass
                    try:
                        _bfsh = _bot_veh.appearance._VehicleAppearance__fashion
                        if _bfsh is not None:
                            _bhw = _bot_descr.chassis['topRightCarryingPoint'][0]
                            _bmax_spd = max(_bot_descr.physics.get('speedLimits', [10.0])[0], 0.001)
                            if isinstance(_bot_descr.physics.get('speedLimits'), (int, float)):
                                _bmax_spd = max(_bot_descr.physics['speedLimits'], 0.001)
                            _bleft  = (_bot_actual_speed - _bot_actual_rot * _bhw) / _bmax_spd
                            _bright = (_bot_actual_speed + _bot_actual_rot * _bhw) / _bmax_spd
                            _bfsh.movementInfo = Math.Vector4(0.0, _bleft, _bright, 0.0)
                    except Exception as _bfe:
                        LOG_NOTE("[BATTLE][TRACKS] vehID=%d fashion update FAILED: %s" % (_bot_veh.id, _bfe))
            except Exception as _bte:
                LOG_NOTE("[BATTLE][TRACKS] bot tick outer exception vehID=%s: %s" % (_bot_vehID, _bte))

        for _tvehID, _tdescr, _tisP in self.vehicles:
            if _tisP: continue
            _tveh = BigWorld.entity(_tvehID)
            if not _tveh or not getattr(_tveh, 'isStarted', False): continue
            try:
                if getattr(_tveh, 'health', 1) <= 0:
                    _tl_spd, _tr_spd = 0.0, 0.0
                else:
                    _tl_spd = max(-1.0, min(1.0, getattr(_tveh, '_ai_track_l', 0.0)))
                    _tr_spd = max(-1.0, min(1.0, getattr(_tveh, '_ai_track_r', 0.0)))
                _tfsh = getattr(_tveh.appearance, '_VehicleAppearance__fashion', None)
                if _tfsh is not None:
                    _tfsh.movementInfo = Math.Vector4(0.0, _tl_spd, _tr_spd, 0.0)
            except Exception:
                pass

        try:
            if not hasattr(self, '_offline_matrix'):
                self._offline_matrix = Math.Matrix()
                self._offline_matrix.setRotateYPR((self._cur_yaw, self._cur_pitch, self._cur_roll))
                self._offline_matrix.translation = pos
                self._offline_servo = BigWorld.Servo(self._offline_matrix)
                veh._offline_matrix = self._offline_matrix
                self._motor_attached = False
                self._motor_attach_attempts = 0

                try:
                    _first_gy = get_ground_height(self.spaceID, pos)
                    if _first_gy != 0.0:
                        try: flt.newPosition(pos, self._offline_matrix, BigWorld.time(), self._cur_speed, self._cur_rot)
                        except Exception: pass
                    try: flt.setInitialSpeeds(self._cur_speed, self._cur_rot)
                    except Exception: pass
                    try:
                        fsh = veh.appearance._VehicleAppearance__fashion
                        if fsh is not None:
                            _hw = descr.chassis['topRightCarryingPoint'][0]
                            _max_spd = max(fwdLimit, 0.001)
                            _left  = (self._cur_speed - self._cur_rot * _hw) / _max_spd
                            _right = (self._cur_speed + self._cur_rot * _hw) / _max_spd
                            fsh.movementInfo = Math.Vector4(0.0, _left, _right, 0.0)
                    except Exception: pass
                except Exception as _fe:
                    LOG_NOTE("[BATTLE] player initial flt/fashion setup failed: %s" % _fe)

                self._try_attach_player_motor(veh)
                self._store_phys_pose(pos)
                self._apply_visual_hull(True)
                if not getattr(self, '_visual_tick_on', False):
                    self._visual_tick_on = True
                    BigWorld.callback(0.025, self.__visualTick)
            else:
                self._store_phys_pose(pos)
                try:
                    if getattr(self, '_hud_hull_mat', None) is None:
                        self._hud_hull_mat = Math.Matrix()
                    self._hud_hull_mat.setRotateYPR((self._cur_yaw, 0.0, 0.0))
                    self._hud_hull_mat.translation = pos
                except Exception:
                    pass
                try:
                    _stamp_entity_pose(veh, self._offline_matrix)
                    self.playerAvatar._ownVehicleMProv.target = self._offline_matrix
                except Exception:
                    pass
                try: flt.newPosition(pos, self._offline_matrix, BigWorld.time(), self._cur_speed, self._cur_rot)
                except Exception: pass
                try: flt.setInitialSpeeds(self._cur_speed, self._cur_rot)
                except Exception: pass
                try:
                    fsh = veh.appearance._VehicleAppearance__fashion
                    if fsh is not None:
                        _hw = descr.chassis['topRightCarryingPoint'][0]
                        _max_spd = max(fwdLimit, 0.001)
                        _left  = (self._cur_speed - self._cur_rot * _hw) / _max_spd
                        _right = (self._cur_speed + self._cur_rot * _hw) / _max_spd
                        fsh.movementInfo = Math.Vector4(0.0, _left, _right, 0.0)
                except Exception: pass
                if not getattr(self, '_motor_attached', False) and getattr(self, '_motor_attach_attempts', 0) < 60:
                    self._try_attach_player_motor(veh)
                if not getattr(self, '_visual_tick_on', False):
                    self._visual_tick_on = True
                    BigWorld.callback(0.025, self.__visualTick)
        except Exception as _mte:
            LOG_NOTE("[BATTLE] player offline_matrix/servo tick failed: %s" % _mte)

        try:
            if self.battleWindow is not None:
                self.battleWindow.call('battle.cruiseCtrl.updateSpeed', [int(self._cur_speed * 3.6)])
        except Exception: pass
        try:
            self.playerAvatar._cur_speed = self._cur_speed
            _pveh_spd = self.playerAvatar.getVehicleAttached()
            if _pveh_spd is not None:
                _pveh_spd._cur_speed = self._cur_speed
        except Exception:
            pass

        if not hasattr(self, '_tdiag'): self._tdiag = 0
        self._tdiag += 1

        try:
            flags = 0
            if move > 0:  flags |= 1
            if move < 0:  flags |= 2
            if turn < 0:  flags |= 4
            if turn > 0:  flags |= 8
            veh.showPlayerMovementCommand(flags)
        except: pass

        try: self._update_cruise_indicator()
        except Exception: pass

    def _get_map_bounds(self):

        if hasattr(self, '_cached_map_bounds'): return self._cached_map_bounds
        bounds = None
        try:
            at = self.arena.typeDescriptor
            if hasattr(at, 'boundingBox'):
                bl, ur = at.boundingBox
                if ur[0] > bl[0] and ur[1] > bl[1]:
                    bounds = (float(bl[0]), float(bl[1]), float(ur[0]), float(ur[1]))
        except Exception:
            pass
        if bounds is None:
            arena_id = getattr(self, '_current_arena_id', None)
            bounds = _MAP_BOUNDS.get(arena_id, _DEFAULT_MAP_BOUNDS)
        x0, z0, x1, z1 = bounds
        m = _MAP_BOUNDS_MARGIN
        if x1 - x0 > m * 2.5 and z1 - z0 > m * 2.5:
            bounds = (x0 + m, z0 + m, x1 - m, z1 - m)
        LOG_NOTE("[BATTLE][NAV] Map bounds resolved to %s" % (bounds,))
        self._cached_map_bounds = bounds
        return bounds

    def _start_nav_grid_build(self):

        if getattr(self, '_nav_grid_building', False) or getattr(self, '_nav_grid_ready', False):
            return
        try:
            self._nav_grid_building = True
            self._nav_grid = {}
            self._nav_grid_reachable = set()
            x0, z0, x1, z1 = self._get_map_bounds()
            cell = _NAV_GRID_CELL
            gw = int((x1 - x0) / cell) + 1
            gh = int((z1 - z0) / cell) + 1
            while gw * gh > _NAV_GRID_MAX_CELLS and cell < 200.0:
                cell *= 1.3
                gw = int((x1 - x0) / cell) + 1
                gh = int((z1 - z0) / cell) + 1
            self._nav_grid_cell = cell
            self._nav_grid_x0, self._nav_grid_z0 = x0, z0
            self._nav_grid_dims = (gw, gh)
            self._nav_grid_queue = [(gx, gz) for gx in range(gw) for gz in range(gh)]
            BigWorld.callback(0.05, self._nav_grid_build_step)
        except Exception:
            self._nav_grid_building = False

    def _nav_grid_cell_world(self, gx, gz):
        return self._nav_grid_x0 + gx * self._nav_grid_cell, self._nav_grid_z0 + gz * self._nav_grid_cell

    def _nav_grid_build_step(self):
        if getattr(self, '_is_finished', False): return
        try:
            q = self._nav_grid_queue
            n = 0
            while q and n < _NAV_GRID_BATCH:
                gx, gz = q.pop()
                wx, wz = self._nav_grid_cell_world(gx, gz)
                ok, groundY = _nav_point_ok(self.spaceID, wx, wz)
                if ok:
                    self._nav_grid[(gx, gz)] = groundY
                n += 1
            if q:
                if _scenery_lowmem:
                    self._nav_grid_building = False
                    self._nav_grid_ready = True
                    return
                BigWorld.callback(0.05, self._nav_grid_build_step)
                return
            if self._maybe_retry_sparse_grid():
                return
            self._nav_grid_flood_fill()
            self._nav_grid_ready = True
            self._nav_grid_building = False
            LOG_NOTE("[BATTLE][NAV] Nav grid built: %d/%d drivable, %d reachable from spawn" % (
                len(self._nav_grid), self._nav_grid_dims[0] * self._nav_grid_dims[1], len(self._nav_grid_reachable)))
        except Exception:
            self._nav_grid_building = False

    def _maybe_retry_sparse_grid(self):

        total_cells = self._nav_grid_dims[0] * self._nav_grid_dims[1]
        tries = getattr(self, '_nav_grid_rebuild_tries', 0)
        _sparse_floor = max(_NAV_GRID_SPARSE_FLOOR, int(total_cells * 0.12))
        if total_cells == 0 or len(self._nav_grid) >= _sparse_floor or tries >= _NAV_GRID_MAX_REBUILDS:
            return False
        self._nav_grid_rebuild_tries = tries + 1
        LOG_NOTE("[BATTLE][NAV] Only %d/%d drivable cells found (retry %d/%d) - map geometry "
                 "may still be streaming in, rebuilding grid in %.1fs" % (
            len(self._nav_grid), total_cells, self._nav_grid_rebuild_tries, _NAV_GRID_MAX_REBUILDS, _NAV_GRID_REBUILD_DELAY))
        self._nav_grid_building = False
        self._nav_grid_ready = False
        BigWorld.callback(_NAV_GRID_REBUILD_DELAY, self._start_nav_grid_build)
        return True

    def _nav_grid_flood_fill(self):

        seeds = []
        arena_id = getattr(self, '_current_arena_id', None)
        for _team, pts in _MAP_SPAWNS.get(arena_id, {}).items():
            for pt in pts:
                seeds.append((pt[0], pt[2]))
        for vehID, descr, isPlayerFlag in self.vehicles:
            veh = BigWorld.entity(vehID)
            if veh is not None:
                seeds.append((veh.position.x, veh.position.z))

        def _seed_frontier():
            reachable, frontier = set(), []
            base_h = None
            for sx, sz in seeds:
                gx = int(round((sx - self._nav_grid_x0) / self._nav_grid_cell))
                gz = int(round((sz - self._nav_grid_z0) / self._nav_grid_cell))
                best, best_d = None, 1e18
                for dgx in range(-6, 7):
                    for dgz in range(-6, 7):
                        c = (gx + dgx, gz + dgz)
                        if c in self._nav_grid:
                            wx, wz = self._nav_grid_cell_world(*c)
                            d = (wx - sx) ** 2 + (wz - sz) ** 2
                            if d < best_d: best_d, best = d, c
                if best is not None:
                    h = self._nav_grid[best]
                    base_h = h if base_h is None else min(base_h, h)
                    if best not in reachable:
                        reachable.add(best)
                        frontier.append(best)
            return reachable, frontier, base_h

        def _bfs(reachable, frontier, base_h, height_gated):
            while frontier:
                gx, gz = frontier.pop()
                cur_h = self._nav_grid[(gx, gz)]
                for dgx in (-1, 0, 1):
                    for dgz in (-1, 0, 1):
                        if dgx == 0 and dgz == 0: continue
                        c = (gx + dgx, gz + dgz)
                        if c in self._nav_grid and c not in reachable:
                            if height_gated:
                                c_h = self._nav_grid[c]
                                if _nav_step_blocked(c_h - cur_h, _NAV_GRID_REACHABLE_MAX_RISE):
                                    continue
                                if base_h is not None and _nav_step_blocked(c_h - base_h, _NAV_GRID_MAX_TOTAL_RISE):
                                    continue
                            reachable.add(c)
                            frontier.append(c)
            return reachable

        reachable, frontier, base_h = _seed_frontier()
        reachable = _bfs(reachable, frontier, base_h, height_gated=True)

        if not reachable and self._nav_grid:
            reachable, frontier, base_h = _seed_frontier()
            reachable = _bfs(reachable, frontier, base_h, height_gated=False)
            if reachable:
                LOG_NOTE("[BATTLE][NAV] Height-gated flood fill found nothing - "
                         "retried without height gate, found %d reachable" % len(reachable))

        if not reachable and self._nav_grid:
            reachable = set(self._nav_grid.keys())
            LOG_NOTE("[BATTLE][NAV] Flood fill found 0 reachable cells despite %d drivable - "
                     "falling back to treating all drivable cells as reachable" % len(self._nav_grid))

        self._nav_grid_reachable = reachable

    def _nav_grid_candidates(self, anchor, spread, avoid=None):

        if not getattr(self, '_nav_grid_ready', False) or not self._nav_grid_reachable:
            return []
        cell = self._nav_grid_cell
        gx0 = int((anchor.x - spread - self._nav_grid_x0) / cell) - 1
        gx1 = int((anchor.x + spread - self._nav_grid_x0) / cell) + 1
        gz0 = int((anchor.z - spread - self._nav_grid_z0) / cell) - 1
        gz1 = int((anchor.z + spread - self._nav_grid_z0) / cell) + 1
        out = []
        for gx in range(gx0, gx1 + 1):
            for gz in range(gz0, gz1 + 1):
                c = (gx, gz)
                if c not in self._nav_grid_reachable: continue
                wx, wz = self._nav_grid_cell_world(gx, gz)
                dx, dz = wx - anchor.x, wz - anchor.z
                if dx * dx + dz * dz > spread * spread: continue
                if avoid is not None:
                    adx, adz = wx - avoid.x, wz - avoid.z
                    if adx * adx + adz * adz < 400.0: continue
                try:
                    if _wall_mem_seg_blocked(anchor.x, anchor.z, wx, wz):
                        continue
                except Exception:
                    pass
                out.append((wx, wz, self._nav_grid[c]))
        return out

    def _nav_pick_waypoint(self, cls, anchor, my_pos, spread, avoid=None, seek_dir=None, avoid_dir=None):

        bx0, bz0, bx1, bz1 = self._get_map_bounds()
        try:
            _edge = _MAP_EDGE_INSET
        except Exception:
            _edge = 20.0

        def _clamp(px, pz):
            return max(bx0 + _edge, min(bx1 - _edge, px)), max(bz0 + _edge, min(bz1 - _edge, pz))

        def _near_edge(_cx, _cz):
            return (_cx < bx0 + _edge or _cx > bx1 - _edge or
                    _cz < bz0 + _edge or _cz > bz1 - _edge)

        _tnow = BigWorld.time()

        def _slope_dir_ok(_cx, _cz):
            if not avoid_dir:
                return True
            _adx = _cx - my_pos.x
            _adz = _cz - my_pos.z
            if _adx * _adx + _adz * _adz < 144.0:
                return True
            _b = _math.atan2(_adx, _adz)
            for _dy, _du in avoid_dir:
                if _du <= _tnow:
                    continue
                if abs((_b - _dy + _math.pi) % (2 * _math.pi) - _math.pi) < 1.05:
                    return False
            return True

        def _from_grid():
            pts = self._nav_grid_candidates(anchor, spread, avoid)
            if avoid_dir:
                pts = [p for p in pts if _slope_dir_ok(p[0], p[1])]
            try:
                pts = [p for p in pts if not _near_edge(p[0], p[1])]
            except Exception:
                pass
            if not pts:
                return None
            if cls in ('TD', 'SPG') and seek_dir is not None:
                best_pt, best_score = None, -1e18
                for cx, cz, groundY in pts:
                    try:
                        if _wall_mem_seg_blocked(my_pos.x, my_pos.z, cx, cz):
                            continue
                    except Exception:
                        pass
                    if not _nav_path_clear(self.spaceID, my_pos.x, my_pos.z, cx, cz, samples=3):
                        continue
                    sd = _nav_sightline_dist(self.spaceID, cx, cz, seek_dir.x, seek_dir.z)
                    score = sd + _nav_elevation(self.spaceID, cx, cz) * 8.0
                    if score > best_score:
                        best_score, best_pt = score, Math.Vector3(cx, groundY, cz)
                return best_pt
            else:
                _random.shuffle(pts)
                for cx, cz, groundY in pts:
                    try:
                        if _wall_mem_seg_blocked(my_pos.x, my_pos.z, cx, cz):
                            continue
                    except Exception:
                        pass
                    if _nav_path_clear(self.spaceID, my_pos.x, my_pos.z, cx, cz, samples=3):
                        return Math.Vector3(cx, groundY, cz)
                return None

        def _search(_spread, _tries):
            best_pt, best_score, fallback_pt = None, -1e18, None
            for _i in range(_tries):
                ang = _random.uniform(0.0, 2.0 * _math.pi)
                r = _spread * _math.sqrt(_random.uniform(0.15, 1.0))
                cx, cz = _clamp(anchor.x + _math.sin(ang) * r, anchor.z + _math.cos(ang) * r)
                try:
                    if _near_edge(cx, cz):
                        continue
                except Exception:
                    pass

                if avoid is not None:
                    adx, adz = cx - avoid.x, cz - avoid.z
                    if (adx * adx + adz * adz) < 400.0:
                        continue
                if not _slope_dir_ok(cx, cz):
                    continue
                try:
                    if _wall_mem_seg_blocked(my_pos.x, my_pos.z, cx, cz):
                        continue
                except Exception:
                    pass

                ok, groundY = _nav_point_ok(self.spaceID, cx, cz)
                if not ok:
                    continue
                if fallback_pt is None:
                    fallback_pt = Math.Vector3(cx, groundY, cz)
                if not _nav_path_clear(self.spaceID, my_pos.x, my_pos.z, cx, cz):
                    continue

                if cls in ('TD', 'SPG') and seek_dir is not None:
                    sd = _nav_sightline_dist(self.spaceID, cx, cz, seek_dir.x, seek_dir.z)
                    score = sd + _nav_elevation(self.spaceID, cx, cz) * 8.0
                else:
                    score = -r

                if score > best_score:
                    best_score, best_pt = score, Math.Vector3(cx, groundY, cz)
                if cls not in ('TD', 'SPG') and best_pt is not None:
                    break
            return best_pt, fallback_pt

        _grid_pt = _from_grid()
        if _grid_pt is not None:
            return _grid_pt

        tries = 10 if cls in ('TD', 'SPG') else 6
        best_pt, fallback_pt = _search(spread, tries)

        if best_pt is None and fallback_pt is None:
            best_pt, fallback_pt = _search(spread * 2.5, 6)

        if best_pt is not None: return best_pt
        return Math.Vector3(my_pos.x, my_pos.y, my_pos.z)
        _nav_log(('nodrive', int(anchor.x), int(anchor.z)),
                 "[BATTLE][NAV] No drivable candidate found near (%.0f, %.0f), holding position" % (anchor.x, anchor.z), 12.0)
        return Math.Vector3(my_pos.x, my_pos.y, my_pos.z)

    def _get_base_zones(self):
        if hasattr(self, '_cached_base_zones'): return self._cached_base_zones
        zones = {}
        arena_id = getattr(self, '_current_arena_id', None)
        try:
            at = self.arena.typeDescriptor
            if hasattr(at, 'teamBasePositions'):
                for b_team, bases in at.teamBasePositions.items():
                    for baseID, pos in bases.items():
                        try:
                            x = getattr(pos, 'x', pos[0]); y = getattr(pos, 'y', pos[1])
                            zones[b_team] = {'pos': Math.Vector3(x, 0, y), 'radius': _TEAM_BASE_RADIUS, 'baseID': baseID}
                        except Exception: pass
        except Exception: pass

        if not zones:
            try:
                extracted = _extract_team_bases_from_chunks(arena_id)
                if extracted:
                    zones = _pair_bases_to_spawn_teams(extracted, arena_id)
                    LOG_NOTE("[BATTLE] Base zones from map chunks '%s': %s" % (
                        arena_id, dict((t, (z['pos'].x, z['pos'].z, z['radius'])) for t, z in zones.items())))
            except Exception:
                LOG_CURRENT_EXCEPTION()

        if not zones:
            base_data = _BASE_ZONES.get(arena_id, {})
            for t, data in base_data.items():
                data = dict(data)
                data['baseID'] = 1
                zones[t] = data

        if not zones:
            spawn_data = _MAP_SPAWNS.get(arena_id, None)
            if spawn_data:
                for team_num, coords in spawn_data.items():
                    if not coords: continue
                    avg_x = sum(c[0] for c in coords) / float(len(coords))
                    avg_z = sum(c[2] for c in coords) / float(len(coords))
                    zones[team_num] = {'pos': Math.Vector3(avg_x, 0, avg_z), 'radius': _TEAM_BASE_RADIUS, 'baseID': 1}
                LOG_NOTE("[BATTLE] Base zones from spawn centroids '%s': %s" % (
                    arena_id, dict((t, (z['pos'].x, z['pos'].z)) for t, z in zones.items())))

        self._cached_base_zones = zones
        return zones

    def _get_lane_geometry(self, team):

        if not hasattr(self, '_cached_lane_geom'): self._cached_lane_geom = {}
        if team in self._cached_lane_geom: return self._cached_lane_geom[team]

        zones = self._get_base_zones()
        my_base_pos = Math.Vector3(0, 0, 0)
        enemy_base_pos = Math.Vector3(0, 0, 0)
        for b_team, zone in zones.items():
            if b_team != team: enemy_base_pos = zone['pos']
            else: my_base_pos = zone['pos']

        axis = enemy_base_pos - my_base_pos
        fwd = Math.Vector3(axis.x, 0, axis.z)
        if fwd.length > 0.1: fwd.normalise()
        else: fwd = Math.Vector3(0, 0, 1)
        perp = Math.Vector3(-fwd.z, 0, fwd.x)

        bx0, bz0, bx1, bz1 = self._get_map_bounds()
        corners = ((bx0, bz0), (bx0, bz1), (bx1, bz0), (bx1, bz1))
        projs = [cx * perp.x + cz * perp.z for cx, cz in corners]
        half_extent = max(1.0, (max(projs) - min(projs)) / 2.0)

        geom = (perp, half_extent)
        self._cached_lane_geom[team] = geom
        return geom

    def _resolve_curated_point(self, team, depth_frac, lane_frac):

        zones = self._get_base_zones()
        my_base_pos = Math.Vector3(0, 0, 0)
        enemy_base_pos = Math.Vector3(0, 0, 0)
        for b_team, zone in zones.items():
            if b_team != team: enemy_base_pos = zone['pos']
            else: my_base_pos = zone['pos']
        perp, half_extent = self._get_lane_geometry(team)
        lane_off = lane_frac * half_extent * 0.85
        return (my_base_pos + (enemy_base_pos - my_base_pos) * depth_frac +
                Math.Vector3(perp.x * lane_off, 0, perp.z * lane_off))

    def _get_patrol_zone_defs(self, arena_id, team):

        cache = getattr(self, '_patrol_zone_cache', None)
        if cache is None:
            self._patrol_zone_cache = {}
            cache = self._patrol_zone_cache
        if arena_id in cache:
            return cache[arena_id]
        zl = _PATROL_ZONES.get(arena_id)
        if zl is None:
            try:
                self._get_lane_geometry(team)
                zl = []
                for _d in (0.18, 0.5, 0.82):
                    for _l in (-0.6, 0.0, 0.6):
                        zl.append({'name': 'z%.0f_%.0f' % (_d * 100, _l * 100),
                                   'lane_frac': _l, 'depth_frac': _d, 'r_frac': 0.30})
            except Exception:
                zl = []
        cache[arena_id] = zl
        return zl

    def _patrol_build_order(self, zl):

        try:
            _idx = range(len(zl))
            _idx.sort(key=lambda _i: float(zl[_i].get('depth_frac', 0.5)))
            return _idx
        except Exception:
            return range(len(zl))

    def _patrol_advance(self, st, zl):

        try:
            _order = st.get('patrol_order')
            if not _order or len(_order) != len(zl):
                _order = self._patrol_build_order(zl)
                st['patrol_order'] = _order
                st['patrol_pos'] = 0
                st['patrol_dir'] = 1
            _pos = int(st.get('patrol_pos', 0))
            _dir = int(st.get('patrol_dir', 1))
            if _dir not in (1, -1):
                _dir = 1
            _npos = _pos + _dir
            if _npos >= len(_order):
                _dir = -1
                _npos = max(0, len(_order) - 2) if len(_order) > 1 else 0
            elif _npos < 0:
                _dir = 1
                _npos = 1 if len(_order) > 1 else 0
            st['patrol_pos'] = _npos
            st['patrol_dir'] = _dir
            st['patrol_zone_idx'] = _order[_npos] % len(zl)
            st['patrol_visits'] = 0
            try:
                LOG_NOTE("[BATTLE][TOUR] Bot %d -> zone %d/%d (%s)" % (
                    st.get('_veh_id', -1), st['patrol_zone_idx'], len(zl),
                    zl[st['patrol_zone_idx']].get('name', '?')))
            except Exception:
                pass
        except Exception:
            pass

    def _route_pick(self, st, cls, team, my_real_pos, avoid=None, avoid_dir=None):

        try:
            _variants = ROUTES.get(getattr(self, '_current_arena_id', None), {}).get(cls, [])
        except Exception:
            _variants = []
        if not _variants:
            return None
        try:
            _var = int(st.get('route_var', 0)) % len(_variants)
        except Exception:
            _var = 0
            st['route_var'] = 0
        _chain = _variants[_var]
        if not _chain:
            return None
        try:
            _ridx = int(st.get('route_idx', 0))
        except Exception:
            _ridx = 0
        if _ridx < 0 or _ridx >= len(_chain):
            _ridx = 0
            st['route_idx'] = 0
        _node = _chain[_ridx]
        try:
            _anchor = self._resolve_curated_point(team, _node.get('depth_frac', 0.5), _node.get('lane_frac', 0.0))
        except Exception:
            return None
        try:
            _dx = _anchor.x - my_real_pos.x
            _dz = _anchor.z - my_real_pos.z
            _dist = _math.sqrt(_dx * _dx + _dz * _dz)
        except Exception:
            _dist = 0.0
        try:
            _max_leg = _PATROL_MAX_LEG
        except Exception:
            _max_leg = 160.0
        if _dist > _max_leg:
            try:
                _k = (_max_leg * 0.85) / max(1.0, _dist)
                _mid = Math.Vector3(my_real_pos.x + _dx * _k, 0, my_real_pos.z + _dz * _k)
            except Exception:
                _mid = _anchor
            _wp = self._nav_pick_waypoint(cls, _mid, my_real_pos, 60.0,
                                          avoid=avoid, avoid_dir=avoid_dir)
            st['tour_hopping'] = True
            return _wp
        st['tour_hopping'] = False
        _wp = self._nav_pick_waypoint(cls, _anchor, my_real_pos, 45.0,
                                      avoid=avoid, avoid_dir=avoid_dir)
        for _i in range(2):
            try:
                if (_wp - my_real_pos).length >= _PATROL_MIN_REPICK_DIST:
                    break
            except Exception:
                break
            _wp = self._nav_pick_waypoint(cls, _anchor, my_real_pos, 45.0,
                                          avoid=avoid, avoid_dir=avoid_dir)
        return _wp

    def _route_advance_on_arrive(self, st, cls):

        try:
            _variants = ROUTES.get(getattr(self, '_current_arena_id', None), {}).get(cls, [])
        except Exception:
            _variants = []
        if not _variants:
            return
        try:
            _var = int(st.get('route_var', 0)) % len(_variants)
        except Exception:
            return
        _chain = _variants[_var]
        if not _chain or len(_chain) < 2:
            return
        try:
            _ridx = int(st.get('route_idx', 0))
            _rdir = int(st.get('route_dir', 1))
        except Exception:
            _ridx, _rdir = 0, 1
        if _rdir not in (1, -1):
            _rdir = 1
        _nidx = _ridx + _rdir
        if _nidx >= len(_chain):
            _rdir = -1
            _nidx = len(_chain) - 2
        elif _nidx < 0:
            _rdir = 1
            _nidx = 1 if len(_chain) > 1 else 0
        st['route_idx'] = _nidx
        st['route_dir'] = _rdir

    def _patrol_zone_pick(self, st, cls, team, my_real_pos, avoid=None, avoid_dir=None):

        zl = self._get_patrol_zone_defs(getattr(self, '_current_arena_id', None), team)
        if not zl:
            return None
        try:
            _order = st.get('patrol_order')
            if not _order or len(_order) != len(zl):
                _order = self._patrol_build_order(zl)
                st['patrol_order'] = _order
                _cur_idx = int(st.get('patrol_zone_idx', 0)) % len(zl)
                try:
                    st['patrol_pos'] = _order.index(_cur_idx)
                except Exception:
                    st['patrol_pos'] = 0
                    st['patrol_zone_idx'] = _order[0] % len(zl)
                if 'patrol_dir' not in st:
                    st['patrol_dir'] = 1
                if 'patrol_visits' not in st:
                    st['patrol_visits'] = 0
            try:
                _vis_lim = _PATROL_VISITS_PER_ZONE
            except Exception:
                _vis_lim = 2
            if int(st.get('patrol_visits', 0)) >= _vis_lim:
                self._patrol_advance(st, zl)
        except Exception:
            pass
        try:
            zd = zl[st.get('patrol_zone_idx', 0) % len(zl)]
        except Exception:
            return None
        zcenter = self._resolve_curated_point(team, zd['depth_frac'], zd['lane_frac'])
        _, half_extent = self._get_lane_geometry(team)
        zradius = max(28.0, half_extent * zd.get('r_frac', 0.32) * 0.9)
        st['zone_center'] = zcenter
        st['zone_radius'] = zradius
        try:
            _dx = zcenter.x - my_real_pos.x
            _dz = zcenter.z - my_real_pos.z
            _dist = _math.sqrt(_dx * _dx + _dz * _dz)
        except Exception:
            _dist = 0.0
        try:
            _max_leg = _PATROL_MAX_LEG
        except Exception:
            _max_leg = 160.0
        if _dist > _max_leg:
            try:
                _k = (_max_leg * 0.85) / max(1.0, _dist)
                _mid = Math.Vector3(my_real_pos.x + _dx * _k, 0, my_real_pos.z + _dz * _k)
            except Exception:
                _mid = zcenter
            st['tour_hopping'] = True
            _wp = self._nav_pick_waypoint(cls, _mid, my_real_pos, 60.0,
                                          avoid=avoid, avoid_dir=avoid_dir)
            for _i in range(2):
                try:
                    if (_wp - my_real_pos).length >= _PATROL_MIN_REPICK_DIST:
                        break
                except Exception:
                    break
                _wp = self._nav_pick_waypoint(cls, _mid, my_real_pos, 60.0,
                                              avoid=avoid, avoid_dir=avoid_dir)
            return _wp
        st['tour_hopping'] = False
        wp = self._nav_pick_waypoint(cls, zcenter, my_real_pos, zradius,
                                     avoid=avoid, avoid_dir=avoid_dir)
        for _i in range(3):
            if (wp - my_real_pos).length >= _PATROL_MIN_REPICK_DIST:
                break
            wp = self._nav_pick_waypoint(cls, zcenter, my_real_pos, zradius,
                                         avoid=avoid, avoid_dir=avoid_dir)
        try:
            st['patrol_visits'] = int(st.get('patrol_visits', 0)) + 1
        except Exception:
            st['patrol_visits'] = 1
        return wp


    def _botAITick(self):
        if getattr(self, '_is_finished', False): return
        if getattr(self, '_ai_busy', False):
            return
        self._ai_busy = True
        try:
            self._botAITickWork()
        except Exception:
            LOG_CURRENT_EXCEPTION()
        self._ai_busy = False
        if not getattr(self, '_is_finished', False):
            BigWorld.callback(_BOT_AI_INTERVAL, self._botAITick)

    def _botAITickWork(self):
        if getattr(self, '_is_finished', False): return
        if not hasattr(self, '_ai_tick_count'): self._ai_tick_count = 0
        self._ai_tick_count += 1

        _ah = getattr(self.playerAvatar, 'inputHandler', None)
        if _ah is not None and not getattr(_ah, '_AvatarInputHandler__isArenaStarted', True):
            return

        try:
            now = BigWorld.time()
            _dir = getattr(self, '_bot_director', None)
            if _dir is None and BotDirector is not None:
                try:
                    _dir = BotDirector(self)
                    self._bot_director = _dir
                except Exception:
                    _dir = None
            if not hasattr(self, '_bot_state'):
                if _dir is not None:
                    self._bot_state = _dir.states
                else:
                    self._bot_state = {}
            elif _dir is not None:
                self._bot_state = _dir.states

            alive = []
            for vehID, descr, isPlayer in self.vehicles:
                v = BigWorld.entity(vehID)
                if v is not None and getattr(v, 'health', 0) > 0:
                    team = self.arena.vehicles.get(vehID, {}).get('team', 0)
                    alive.append((vehID, descr, isPlayer, team, v))

            _think = None
            if _dir is not None:
                _dir.set_roster([_vid for _vid, _d, _isp, _t, _v in alive if not _isp])
                _think = set(_dir.next_think())

            def _real_pos(v):
                _om = getattr(v, '_offline_matrix', None)
                return _om.translation if _om is not None else v.position

            def _class_of(d):
                tags = getattr(d.type, 'tags', set())
                if 'lightTank' in tags: return 'LT'
                if 'heavyTank' in tags: return 'HT'
                if 'AT-SPG' in tags: return 'TD'
                if 'SPG' in tags: return 'SPG'
                return 'MT'

            def _view_radius_for(cls, role):
                base = 300.0 if cls in ('LT', 'TD') else 200.0
                return base * 0.75 if role == 'bush' else base

            team_targets = {1: set(), 2: set()}
            for vid, st_data in self._bot_state.items():
                t = st_data.get('target')
                if t:
                    my_team = self.arena.vehicles.get(vid, {}).get('team', 0)
                    if my_team in team_targets:
                        team_targets[my_team].add(t)

            team_spotted = {1: set(), 2: set()}
            for vID, vDescr, vIsPlayer, vTeam, vVehObj in alive:
                if vTeam not in team_spotted: continue
                v_cls = _class_of(vDescr)
                if v_cls == 'SPG': continue
                v_radius = _view_radius_for(v_cls, self._bot_state.get(vID, {}).get('position_role'))
                v_pos = _real_pos(vVehObj)
                for eID, eDescr, eIsPlayer, eTeam, eVehObj in alive:
                    if eTeam == vTeam or eTeam == 0 or eID in team_spotted[vTeam]:
                        continue
                    if (v_pos - _real_pos(eVehObj)).length <= v_radius:
                        team_spotted[vTeam].add(eID)

            def get_random_waypoint():
                _bx0, _bz0, _bx1, _bz1 = self._get_map_bounds()
                return Math.Vector3(_random.uniform(_bx0, _bx1), 0, _random.uniform(_bz0, _bz1))

            for vehID, descr, isPlayer, team, veh in alive:
                if isPlayer: continue
                st = self._bot_state.setdefault(vehID, {'target': None, 'last_shot': 0.0, 'last_hit_dir': None, 'retreat_until': 0.0})

                _avoid_pt = None
                if st.get('bad_waypoint') is not None and (now - st.get('bad_waypoint_time', 0.0)) < 45.0:
                    _avoid_pt = st['bad_waypoint']

                _avoid_dir = None
                if st.get('_slope_avoid_dirs'):
                    _live_dirs = [(d[0], d[1]) for d in st['_slope_avoid_dirs'] if d[1] > now]
                    if _live_dirs:
                        _avoid_dir = _live_dirs
                    else:
                        del st['_slope_avoid_dirs']

                my_real_pos = _real_pos(veh)

                if 'class' not in st:
                    tags = getattr(descr.type, 'tags', set())
                    v_class = 'MT'
                    if 'lightTank' in tags: v_class = 'LT'
                    elif 'mediumTank' in tags: v_class = 'MT'
                    elif 'heavyTank' in tags: v_class = 'HT'
                    elif 'AT-SPG' in tags: v_class = 'TD'
                    elif 'SPG' in tags: v_class = 'SPG'

                    st['class'] = v_class
                    st['spawn_pos'] = my_real_pos
                    st['waypoint'] = my_real_pos
                    st['wp_timer'] = 0.0

                    if v_class == 'SPG':
                        pos_role = 'base'
                    elif v_class == 'TD':
                        pos_role = 'bush'
                    elif v_class in ('MT', 'HT') and _random.random() < 0.25:
                        pos_role = 'bush'
                    else:
                        pos_role = 'push'

                    st['position_role'] = pos_role
                    st['bush_point'] = None
                    st['lane_offset_frac'] = _random.uniform(-1.0, 1.0)
                    _route_variants = ROUTES.get(getattr(self, '_current_arena_id', None), {}).get(v_class, [])
                    st['route_var'] = _random.randrange(len(_route_variants)) if _route_variants else 0
                    st['route_idx'] = 0
                    st['route_dir'] = 1
                    st['_veh_id'] = vehID

                    _patrol_zl = self._get_patrol_zone_defs(getattr(self, '_current_arena_id', None), team)
                    if pos_role == 'push' and _patrol_zl:
                        st['patrol_order'] = self._patrol_build_order(_patrol_zl)
                        st['patrol_pos'] = 0
                        _start_zone = st['patrol_order'][0] if st['patrol_order'] else 0
                        st['patrol_zone_idx'] = _start_zone
                        st['patrol_dir'] = 1
                        st['patrol_visits'] = 0
                        st['tour_hopping'] = False
                        LOG_NOTE("[BATTLE] Bot %d -> tour start zone %d/%d (%s)" % (
                            vehID, st['patrol_zone_idx'], len(_patrol_zl),
                            _patrol_zl[st['patrol_zone_idx']].get('name', '?')))
                    else:
                        st['patrol_zone_idx'] = None
                        st['patrol_order'] = []
                        st['patrol_pos'] = 0
                        st['patrol_dir'] = 1
                        st['patrol_visits'] = 0
                    LOG_NOTE("[BATTLE] Assigned role %s (position=%s) to bot %d" % (v_class, pos_role, vehID))
                    continue

                if _think is not None and vehID not in _think:
                    continue

                if not getattr(self, '_battle_started', False):
                    veh._ai_target_speed = 0.0
                    veh._ai_retreating = False
                    try:
                        _ex_c = 0.0; _ez_c = 0.0; _en_n = 0
                        for _eid2, _ed2, _ep2, _et2, _ev2 in alive:
                            if _et2 == team: continue
                            _ep2_pos = _real_pos(_ev2)
                            _ex_c += _ep2_pos.x; _ez_c += _ep2_pos.z; _en_n += 1
                        if _en_n > 0:
                            _ex_c /= _en_n; _ez_c /= _en_n
                            _hull_ang = _math.atan2(_ex_c - my_real_pos.x, _ez_c - my_real_pos.z)
                            veh._ai_yaw = _hull_ang
                            veh._ai_desired_yaw = _hull_ang
                    except Exception: pass
                    veh._ai_desired_turret_yaw = 0.0
                    veh._ai_desired_gun_pitch = 0.0
                    continue

                best = None
                best_score = -1e18
                best_is_provoked = False

                for eVehID, eDescr, eIsPlayer, eTeam, eVeh in alive:
                    if eTeam == team or eTeam == 0:
                        continue
                    is_provoked = (eVehID == st.get('last_attacker'))

                    dist = (my_real_pos - _real_pos(eVeh)).length
                    if dist < 1.0: continue

                    if st['class'] == 'SPG':
                        if not is_provoked and eVehID not in team_spotted.get(team, ()):
                            continue
                    else:
                        _view_radius = _view_radius_for(st['class'], st.get('position_role'))
                        if not is_provoked and dist > _view_radius:
                            continue

                    hp_frac = float(getattr(eVeh, 'health', 1)) / max(1, getattr(eDescr, 'maxHealth', 1))
                    player_bias = 50.0 if eIsPlayer else 0.0
                    score = -dist + (200.0 * (1.0 - hp_frac)) + player_bias

                    if is_provoked: score += 1000.0

                    _is_wounded_priority = hp_frac < 0.3
                    if _is_wounded_priority: score += 300.0

                    if not is_provoked and not _is_wounded_priority and eVehID in team_targets[team] and st.get('target') != eVehID:
                        score -= 5000.0

                    if score > best_score:
                        best_score = score
                        best = (eVehID, eDescr, eVeh, dist)
                        best_is_provoked = is_provoked

                if best is None or (best[3] > 450.0 and st['class'] != 'SPG' and not best_is_provoked):
                    if best is None:
                        st['target'] = None

                    enemy_base_pos = Math.Vector3(0, 0, 0)
                    my_base_pos = Math.Vector3(0, 0, 0)
                    zones = self._get_base_zones()
                    for b_team, zone in zones.items():
                        if b_team != team: enemy_base_pos = zone['pos']
                        else: my_base_pos = zone['pos']

                    if now > st.get('wp_timer', 0.0):
                        cls = st['class']
                        role = st.get('position_role', 'push')

                        if role in ('base', 'bush') and cls not in ('SPG', 'TD'):
                            _elapsed = now - getattr(self, '_battleEnterTime', now)
                            _time_chance = min(0.75, max(0.0, (_elapsed - 45.0) / 300.0))
                            _team_alive  = sum(1 for _vid2, _d2, _p2, _t2, _v2 in alive if _t2 == team)
                            _enemy_alive = sum(1 for _vid2, _d2, _p2, _t2, _v2 in alive if _t2 not in (0, team))
                            _score_chance = 0.4 if _team_alive >= _enemy_alive + 2 else 0.0
                            _push_chance = min(0.9, _time_chance + _score_chance)
                            if _push_chance > 0.0 and _random.random() < _push_chance:
                                role = 'push'
                                st['position_role'] = 'push'
                                LOG_NOTE("[BATTLE] Bot %d switches base/bush -> push (time=%.0fs, %d vs %d alive)" % (
                                    vehID, _elapsed, _team_alive, _enemy_alive))

                        _enemy_team = 2 if team == 1 else 1
                        _enemy_zone = self._get_base_zones().get(_enemy_team)
                        _capturing_base = False
                        if role == 'push' and cls != 'SPG' and _enemy_zone is not None:
                            _dist_to_base = (my_real_pos - _enemy_zone['pos']).length
                            if _dist_to_base < _enemy_zone['radius'] + 220.0:
                                _nearby_enemy = False
                                for _eid2 in team_spotted.get(team, ()):
                                    _ev2 = BigWorld.entity(_eid2)
                                    if _ev2 is not None and (_real_pos(_ev2) - _enemy_zone['pos']).length < _enemy_zone['radius'] + 80.0:
                                        _nearby_enemy = True
                                        break
                                if not _nearby_enemy:
                                    _capturing_base = True
                                    if st.get('_capture_pt') is None or now > st.get('_capture_wp_timer', 0.0):
                                        _cap_anchor = Math.Vector3(_enemy_zone['pos'].x, 0, _enemy_zone['pos'].z)
                                        _cap_spread = max(5.0, _enemy_zone['radius'] * 0.6)
                                        st['_capture_pt'] = self._nav_pick_waypoint(cls, _cap_anchor, my_real_pos,
                                                                                     _cap_spread, avoid=_avoid_pt, avoid_dir=_avoid_dir)
                                        st['_capture_wp_timer'] = now + 20.0
                                    st['waypoint'] = st['_capture_pt']
                                    st['wp_timer'] = now + 20.0

                        if _capturing_base:
                            pass
                        elif cls == 'SPG':
                            if st.get('hold_point') is None:
                                _seek = None
                                if enemy_base_pos.length > 0.1:
                                    _seek = enemy_base_pos - my_real_pos
                                _cover = _find_cover_point(
                                    self.spaceID, st['spawn_pos'].x, st['spawn_pos'].z,
                                    getattr(self, '_chunk_destr_counts', None) or _scenery_counts,
                                    toward=_seek, max_r=90.0)
                                if _cover is None:
                                    _cover = self._nav_pick_waypoint(
                                        'SPG', st['spawn_pos'], my_real_pos, 25.0,
                                        avoid=_avoid_pt, seek_dir=_seek, avoid_dir=_avoid_dir)
                                st['hold_point'] = _cover
                            st['waypoint'] = st.get('hold_point') or my_real_pos
                            st['wp_timer'] = now + 90.0

                        elif role == 'base':
                            base_pt = my_base_pos if my_base_pos.length > 0.1 else st['spawn_pos']
                            _seek = (enemy_base_pos - base_pt) if enemy_base_pos.length > 0.1 else None
                            st['waypoint'] = self._nav_pick_waypoint(cls, base_pt, my_real_pos, 60.0,
                                                                      avoid=_avoid_pt, seek_dir=_seek, avoid_dir=_avoid_dir)
                            st['wp_timer'] = now + _random.uniform(8.0, 16.0)

                        elif role == 'bush':
                            if st.get('bush_point') is None:
                                _seek = None
                                if enemy_base_pos.length > 0.1:
                                    _seek = enemy_base_pos - my_real_pos
                                _cover = _find_cover_point(
                                    self.spaceID, st['spawn_pos'].x, st['spawn_pos'].z,
                                    getattr(self, '_chunk_destr_counts', None) or _scenery_counts,
                                    toward=_seek, max_r=140.0)
                                if _cover is None:
                                    bdir = Math.Vector3(_random.uniform(-1.0, 1.0), 0, _random.uniform(-1.0, 1.0))
                                    if bdir.length > 0.1:
                                        bdir.normalise()
                                    else:
                                        bdir = Math.Vector3(0, 0, 1)
                                    bdist = _random.uniform(40.0, 90.0)
                                    _anchor = Math.Vector3(st['spawn_pos'].x + bdir.x * bdist,
                                                            0, st['spawn_pos'].z + bdir.z * bdist)
                                    _cover = self._nav_pick_waypoint(cls, _anchor, my_real_pos, 20.0, avoid_dir=_avoid_dir)
                                st['bush_point'] = _cover
                            st['waypoint'] = st['bush_point']
                            st['wp_timer'] = now + 90.0

                        else:
                            arena_id = getattr(self, '_current_arena_id', None)
                            _zlist = self._get_patrol_zone_defs(arena_id, team)
                            _tour_wp = None
                            try:
                                _tour_wp = self._route_pick(st, cls, team, my_real_pos,
                                                            avoid=_avoid_pt, avoid_dir=_avoid_dir)
                            except Exception:
                                _tour_wp = None
                            if _tour_wp is not None:
                                st['waypoint'] = _tour_wp
                                st['wp_timer'] = now + _random.uniform(25.0, 40.0)
                            elif _zlist and st.get('patrol_zone_idx') is not None:
                                st['waypoint'] = self._patrol_zone_pick(
                                    st, cls, team, my_real_pos, avoid=_avoid_pt, avoid_dir=_avoid_dir)
                                st['wp_timer'] = now + _random.uniform(30.0, 50.0)
                            elif enemy_base_pos.length > 0.1:
                                t_frac = _random.uniform(-0.2, 1.1)
                                _lane_perp, _lane_half_extent = self._get_lane_geometry(team)
                                _lane_walk = st.get('lane_walk', st.get('lane_offset_frac', 0.0))
                                _lane_walk = max(-1.0, min(1.0, _lane_walk + _random.uniform(-0.55, 0.55)))
                                st['lane_walk'] = _lane_walk
                                _lane_off = _lane_walk * _lane_half_extent * 0.85
                                base_pt = (my_base_pos + (enemy_base_pos - my_base_pos) * t_frac +
                                           Math.Vector3(_lane_perp.x * _lane_off, 0, _lane_perp.z * _lane_off))
                                _seek = (enemy_base_pos - base_pt)
                                st['waypoint'] = self._nav_pick_waypoint(cls, base_pt, my_real_pos, 60.0,
                                                                          avoid=_avoid_pt, seek_dir=_seek, avoid_dir=_avoid_dir)
                                st['wp_timer'] = now + _random.uniform(10.0, 25.0)
                            else:
                                st['waypoint'] = self._nav_pick_waypoint(cls, get_random_waypoint(), my_real_pos, 50.0,
                                                                          avoid=_avoid_pt, avoid_dir=_avoid_dir)
                                st['wp_timer'] = now + _random.uniform(10.0, 25.0)
                            st['_capture_pt'] = None

                    diff_wp = st['waypoint'] - my_real_pos
                    if st.get('position_role') == 'push'                            and (st.get('patrol_zone_idx') is not None or st.get('route_idx') is not None)                            and st.get('_capture_pt') is None                            and diff_wp.length <= _PATROL_ARRIVE_DIST:
                        if not st.get('tour_hopping'):
                            try:
                                self._route_advance_on_arrive(st, st.get('class', 'MT'))
                            except Exception:
                                pass
                        st['wp_timer'] = 0.0
                    if diff_wp.length > 15.0:
                        desired_yaw = _math.atan2(diff_wp.x, diff_wp.z)
                        veh._ai_desired_yaw = desired_yaw + _random.uniform(-0.1, 0.1)
                        spd_limit = getattr(descr.physics, 'speedLimits', [10.0])[0]
                        if isinstance(spd_limit, (list, tuple)): spd_limit = spd_limit[0]
                        if st['class'] == 'SPG': speed_mult = 0.3
                        elif st['class'] == 'TD': speed_mult = 0.6
                        else: speed_mult = 0.95
                        veh._ai_target_speed = spd_limit * speed_mult
                        veh._ai_retreating = False

                        _wp_t_yaw = (desired_yaw - veh._offline_matrix.yaw + _math.pi) % (2 * _math.pi) - _math.pi

                        try:
                            _yl = descr.turret.get('yawLimits')
                            if _yl is not None:
                                _wp_t_yaw = max(_yl[0], min(_yl[1], _wp_t_yaw))
                        except Exception: pass

                        veh._ai_desired_turret_yaw = _wp_t_yaw
                        veh._ai_desired_gun_pitch = 0.0
                    else:
                        veh._ai_target_speed = 0.0
                        try:
                            if st['class'] in ('TD', 'SPG') and enemy_base_pos.length > 0.1:
                                veh._ai_desired_yaw = _math.atan2(
                                    enemy_base_pos.x - my_real_pos.x,
                                    enemy_base_pos.z - my_real_pos.z)
                        except Exception:
                            pass
                        if not hasattr(veh, '_ai_idle_turret_time'): veh._ai_idle_turret_time = 0.0
                        if now > veh._ai_idle_turret_time:
                            veh._ai_idle_turret_time = now + _random.uniform(2.0, 5.0)
                            _idle_yaw = _random.uniform(-1.5, 1.5)

                            try:
                                _yl = descr.turret.get('yawLimits')
                                if _yl is not None:
                                    _idle_yaw = max(_yl[0], min(_yl[1], _idle_yaw))
                            except Exception: pass

                            veh._ai_desired_turret_yaw = _idle_yaw
                            veh._ai_desired_gun_pitch = _random.uniform(-0.1, 0.1)
                    continue

                tVehID, tDescr, tVeh, dist = best
                st['target'] = tVehID
                t_real_pos = _real_pos(tVeh)

                diff = t_real_pos - my_real_pos
                if diff.length < 0.01: continue

                _los_clear = True
                if st['class'] != 'SPG':
                    _gunPosChk = my_real_pos + Math.Vector3(0, 1.5, 0)
                    _aimPosChk = t_real_pos + Math.Vector3(0, 1.2, 0)
                    _los_clear = not _bot_los_blocked(self.spaceID, _gunPosChk, _aimPosChk, tVeh)

                desired_yaw = _math.atan2(diff.x, diff.z)

                _local_turret_yaw = desired_yaw - veh._offline_matrix.yaw
                _local_turret_yaw = (_local_turret_yaw + _math.pi) % (2 * _math.pi) - _math.pi

                _local_turret_yaw_true = _local_turret_yaw

                try:
                    _yl = descr.turret.get('yawLimits')
                    if _yl is not None:
                        _local_turret_yaw = max(_yl[0], min(_yl[1], _local_turret_yaw))
                except Exception: pass

                veh._ai_desired_turret_yaw = _local_turret_yaw

                _height_diff = (t_real_pos.y + 1.5) - (my_real_pos.y + 2.0)
                _horiz_dist = _math.sqrt(diff.x**2 + diff.z**2)
                veh._ai_desired_gun_pitch = -_math.atan2(_height_diff, _horiz_dist)

                hp_frac_self = float(getattr(veh, 'health', 1)) / max(1, getattr(descr, 'maxHealth', 1))
                retreating = hp_frac_self < 0.20

                _splims = descr.physics.get('speedLimits', [10.0, 10.0])
                if isinstance(_splims, (int, float)): _splims = [_splims, _splims]
                spd_limit = _splims[0]

                cls = st['class']
                if cls == 'SPG':
                    veh._ai_target_speed = 0.0
                elif cls == 'TD' and st.get('position_role') == 'bush':
                    veh._ai_target_speed = 0.0
                elif cls == 'TD':
                    if dist < 120.0:
                        desired_yaw += _math.pi
                        veh._ai_target_speed = _splims[1] * 0.8
                    else:
                        veh._ai_target_speed = 0.0
                elif cls == 'HT':
                    if retreating:
                        desired_yaw += _math.pi
                        veh._ai_target_speed = _splims[1]
                    else:
                        veh._ai_target_speed = spd_limit
                elif cls == 'MT':
                    if dist < 100.0:
                        desired_yaw += _math.pi
                    elif dist < 200.0:
                        flank_side = 1 if st.get('_flank_side', 1) > 0 else -1
                        desired_yaw += _math.radians(60) * flank_side
                    veh._ai_target_speed = spd_limit
                elif cls == 'LT':
                    if dist < 200.0:
                        flank_side = 1 if st.get('_flank_side', 1) > 0 else -1
                        desired_yaw += _math.radians(85) * flank_side
                    veh._ai_target_speed = spd_limit

                if st.get('position_role') == 'bush' and not retreating and dist > 70.0:
                    veh._ai_target_speed = 0.0
                elif st.get('position_role') != 'bush':
                    try:
                        _chase_dist = min(dist, 55.0)
                        _chase_tx = my_real_pos.x + _math.sin(desired_yaw) * _chase_dist
                        _chase_tz = my_real_pos.z + _math.cos(desired_yaw) * _chase_dist
                        if not _nav_path_clear(self.spaceID, my_real_pos.x, my_real_pos.z, _chase_tx, _chase_tz, samples=4):
                            if st.get('terrain_reroute_pt') is None or now > st.get('terrain_reroute_time', 0.0):
                                _chase_anchor = Math.Vector3(_chase_tx, my_real_pos.y, _chase_tz)
                                st['terrain_reroute_pt'] = self._nav_pick_waypoint(cls, _chase_anchor, my_real_pos, 40.0, avoid_dir=_avoid_dir)
                                st['terrain_reroute_time'] = now + 3.0
                            _reroute_pt = st.get('terrain_reroute_pt')
                            if _reroute_pt is not None:
                                _rdiff = _reroute_pt - my_real_pos
                                if _rdiff.length > 1.0:
                                    desired_yaw = _math.atan2(_rdiff.x, _rdiff.z)
                        elif st.get('terrain_reroute_pt') is not None:
                            st['terrain_reroute_pt'] = None
                    except Exception:
                        pass

                veh._ai_yaw = desired_yaw
                veh._ai_desired_yaw = desired_yaw
                veh._ai_retreating = retreating

                aim_diff = abs((_local_turret_yaw_true - getattr(veh, '_bot_turret_yaw_cur', 0.0) + _math.pi) % (2 * _math.pi) - _math.pi)

                max_shoot_dist = 1000.0 if cls == 'SPG' else 450.0

                bot_reload = 5.0
                try:
                    bot_reload = descr.gun.get('reloadTime', 5.0) * descr.miscAttrs.get('gunReloadTimeFactor', 1.0)
                    crewLevel = 100 + descr.miscAttrs.get('crewLevelIncrease', 0)
                    bot_reload /= (0.5 + 0.005 * crewLevel)
                except Exception:
                    bot_reload = 18.0 if cls == 'SPG' else 6.0

                if cls == 'SPG': bot_reload = max(bot_reload, 22.0)

                try:
                    bot_reload *= self._reload_multiplier(vehID)
                except Exception: pass
                _bot_can_fire = True
                try:
                    _bot_can_fire = self._can_fire(vehID)
                except Exception: pass

                _aim_threshold = _math.radians(1.7) if cls in ('SPG', 'TD') else 0.15

                _tgt_spotted = tVehID in team_spotted.get(team, ())

                bot_wants_to_shoot = (_bot_can_fire and not retreating and aim_diff < _aim_threshold
                                       and dist < max_shoot_dist and (now - st['last_shot']) > bot_reload
                                       and _los_clear and _tgt_spotted)

                if bot_wants_to_shoot and cls == 'SPG' and not st.get('preparing_shot', False):
                    st['preparing_shot'] = True
                    st['fire_time'] = now + 1.5

                do_shoot = False
                if st.get('preparing_shot', False):
                    try:
                        _gun_limits = descr.gun['pitchLimits']
                        _min_pitch = _gun_limits[0] if isinstance(_gun_limits, list) else -0.5
                    except Exception:
                        _min_pitch = -0.5

                    veh._ai_desired_gun_pitch = _min_pitch
                    veh._ai_target_speed = 0.0

                    if now >= st.get('fire_time', now):
                        st['preparing_shot'] = False
                        if aim_diff < _aim_threshold and dist < max_shoot_dist and _tgt_spotted:
                            do_shoot = True
                        else:
                            do_shoot = False
                else:
                    do_shoot = bot_wants_to_shoot

                if do_shoot:
                    st['last_shot'] = now

                    gun_pos = _vehicle_gun_world_pos(veh)
                    aim_pos = t_real_pos + Math.Vector3(0, 1.2, 0)

                    _aim_center2d = None
                    if cls != 'SPG':
                        try:
                            _spd_tmp = descr.shot.get('speed', 1000.0)
                            _grv_tmp = 0.05
                            _aim_center2d = _predict_aim_point(gun_pos, t_real_pos, _spd_tmp, _grv_tmp, tVeh)
                            if _aim_center2d is not None:
                                aim_pos = Math.Vector3(_aim_center2d) + Math.Vector3(0, 1.2, 0)
                        except Exception: pass

                    if cls == 'SPG':
                        dispersion = max(2.0, dist * 0.015)
                    else:
                        dispersion = dist * 0.015

                    dispersion *= self._dispersion_factor(vehID)

                    aim_pos.x += _random.uniform(-dispersion, dispersion)
                    aim_pos.z += _random.uniform(-dispersion, dispersion)

                    shoot_dir = aim_pos - gun_pos
                    if shoot_dir.length > 0.001: shoot_dir.normalise()
                    else: shoot_dir = Math.Vector3(0, 0, 1)

                    visual_shoot_dir = shoot_dir
                    if cls == 'SPG':
                        _arc_pitch = getattr(veh, '_ai_desired_gun_pitch', -0.5)
                        _yaw = veh._ai_yaw
                        visual_shoot_dir = Math.Vector3(_math.sin(_yaw)*_math.cos(_arc_pitch), -_math.sin(_arc_pitch), _math.cos(_yaw)*_math.cos(_arc_pitch))
                        if visual_shoot_dir.length > 0.001: visual_shoot_dir.normalise()

                    try:
                        _spgSpeed   = descr.shot.get('speed', 1000.0) * 0.6
                        _spgGravity = descr.shot.get('gravity', 9.8) * 0.36
                        _spgMaxDist = descr.shot.get('maxDistance', 700.0)
                    except Exception:
                        _spgSpeed, _spgGravity, _spgMaxDist = 600.0, 3.5, 700.0

                    is_hit = False
                    impact_pt = aim_pos
                    _botHitComponent = None

                    if cls == 'SPG':
                        _trRes = None
                        try:
                            _trExcepts = set([vehID])
                            try:
                                for _vid0, _d0, _p0, _t0, _v0 in alive:
                                    if _vid0 == vehID: continue
                                    if getattr(_v0, 'health', 1) <= 0:
                                        _trExcepts.add(_vid0)
                            except Exception: pass
                            _trRes = _trace_ballistic_hit(
                                self.spaceID, gun_pos,
                                Math.Vector3(visual_shoot_dir) * _spgSpeed,
                                _spgGravity, _spgMaxDist, _trExcepts)
                        except Exception:
                            _trRes = None
                        if _trRes is not None:
                            impact_pt = _trRes[0]
                            _trVeh = _trRes[1]
                            if _trVeh is not None:
                                if _trVeh.id == tVehID:
                                    is_hit = True
                            else:
                                _distT = (impact_pt - t_real_pos).length
                                is_hit = _distT < 9.0
                    else:
                        _ballistic_hit = False
                        losRes = None
                        _pReached = True
                        _pRes = None
                        try:
                            _botSpeed = descr.shot.get('speed', 1000.0)
                            _botMax = descr.shot.get('maxDistance', 720.0)
                            _trRes = _trace_ballistic_hit(
                                self.spaceID, gun_pos,
                                Math.Vector3(shoot_dir) * _botSpeed,
                                0.05, _botMax, set([vehID]))
                            if _trRes is not None and _trRes[1] is not None and getattr(_trRes[1], 'id', None) == tVehID:
                                is_hit = True
                                impact_pt = _trRes[0]
                                _ballistic_hit = True
                        except Exception:
                            _ballistic_hit = False
                        if not _ballistic_hit:
                            losRes = BigWorld.wg_collideSegment(self.spaceID, gun_pos, aim_pos, 128)
                            _pRes = losRes
                            _pReached = losRes is None
                        _dynEnd = Math.Vector3(aim_pos)
                        if losRes is not None:
                            for _pi in range(3):
                                _pMk = _pRes[2]
                                _pSoft = False
                                try:
                                    _pSoft = (_pMk >= _const.DESTRUCTIBLE_MATKINDS_MIN and
                                              _pMk <= _const.DESTRUCTIBLE_MATKINDS_MAX and
                                              not _destr_whisker_blocks(self.spaceID, _pMk, _pRes[4], _pRes[5]))
                                except Exception:
                                    _pSoft = False
                                impact_pt = _pRes[0]
                                if not _pSoft:
                                    break
                                try:
                                    _try_destroy_destructible_at(self.spaceID, _pRes[0], shoot_dir)
                                except Exception:
                                    pass
                                _pNext = _pRes[0] + shoot_dir * 0.5
                                _probe2 = BigWorld.wg_collideSegment(self.spaceID, _pNext, aim_pos, 128)
                                if _probe2 is None:
                                    _pRes = None
                                    _pReached = True
                                    break
                                _pRes = _probe2
                            if _pReached:
                                _dynEnd = Math.Vector3(aim_pos)
                            elif _pRes is not None:
                                _dynEnd = Math.Vector3(_pRes[0])
                                impact_pt = _dynEnd
                            else:
                                _dynEnd = Math.Vector3(aim_pos)
                        try:
                            _staticDist = (Math.Vector3(_dynEnd) - gun_pos).length
                        except Exception:
                            _staticDist = 99999.0
                        if _pReached:
                            _staticDist = 99999.0
                        try:
                            _botDyn = _collide_dynamic_component(gun_pos, aim_pos, set([vehID]))
                        except Exception:
                            _botDyn = None
                        if not _ballistic_hit and _botDyn is not None and getattr(_botDyn[0], 'id', None) == tVehID:
                            if _botDyn[1] < _staticDist + 3.5:
                                is_hit = True
                                _segN = Math.Vector3(shoot_dir)
                                if _segN.length > 0.001:
                                    _segN.normalise()
                                impact_pt = gun_pos + _segN * _botDyn[1]
                                _botHitComponent = _botDyn[4]

                    isPenetration = True
                    isRicochet    = False
                    if is_hit and cls != 'SPG':
                        try:
                            _t_om  = getattr(tVeh, '_offline_matrix', None)
                            _t_yaw = _t_om.yaw if _t_om is not None else getattr(tVeh, 'yaw', 0.0)
                            _shotData = descr.gun['shots'][0]
                            isPenetration, isRicochet, _hit_thick, _hit_cos = _calc_shot_penetration(
                                _shotData, tDescr, _t_yaw, shoot_dir)
                        except Exception:
                            isPenetration, isRicochet = True, False
                        if _botHitComponent is None:
                            try:
                                _compRes = _vehicle_collide_segment_component(tVeh, gun_pos, aim_pos)
                                if _compRes is not None:
                                    _botHitComponent = _compRes[3]
                            except Exception:
                                _botHitComponent = None

                    if hasattr(veh, 'showShooting'):
                        try: veh.showShooting(True)
                        except: pass

                    for _ovID, _, _ovIsPl, _ovTeam, _ovVeh in alive:
                        if _ovID == vehID or _ovTeam == team: continue
                        _ov_pos = _real_pos(_ovVeh)
                        if (my_real_pos - _ov_pos).length < 250.0:
                            _ov_st = self._bot_state.setdefault(_ovID, {})
                            if not _ov_st.get('target'):
                                _ov_st['last_attacker'] = vehID

                    _botBurst = 1
                    _botBurstInterval = 0.2
                    try:
                        _botBurstTmp = descr.gun['burst']
                        if isinstance(_botBurstTmp, (tuple, list)) and len(_botBurstTmp) >= 1:
                            _botBurst = max(1, int(_botBurstTmp[0]))
                    except Exception:
                        _botBurst = 1
                        _botBurstInterval = 0.2
                    if _botBurst <= 1:
                        _botBurstInterval = 0.0
                    try:
                        def _fire_bot_shell(_fb):
                            _vsdir = Math.Vector3(visual_shoot_dir)
                            if _botBurst > 1:
                                _vsdir.x += _random.uniform(-_math.radians(0.9), _math.radians(0.9))
                                _vsdir.y += _random.uniform(-_math.radians(0.9), _math.radians(0.9))
                                _vsdir.normalise()
                            pm = self.playerAvatar.projectileMover
                            shotID = _random.randint(1, 999999)

                            try:
                                eff_idx = descr.shot['shell'].get('effectsIndex', 0)
                                eff_descr = vehicles.g_cache.shotEffects[eff_idx]
                            except:
                                eff_descr = vehicles.g_cache.shotEffects[0]

                            if cls == 'SPG':
                                speed = descr.shot.get('speed', 1000.0) * 0.6
                                gravity = descr.shot.get('gravity', 9.8) * 0.36
                            else:
                                speed = descr.shot.get('speed', 1000.0)
                                gravity = 0.05

                            pm.add(shotID, eff_descr, gravity, gun_pos, _vsdir * speed, gun_pos, True, BigWorld.camera().position)

                            flight_time = _ballistic_flight_time(gun_pos, _vsdir * speed, gravity, impact_pt)

                            def explode_tracer(s_id, ed, mat, pt, sdir,
                                    _tv=tVeh, _gp=gun_pos, _v0=Math.Vector3(_vsdir), _spd=speed,
                                    _grv=gravity, _ft=flight_time,
                                    _ispen=isPenetration, _hc=_botHitComponent,
                                    _jit=(0.15 if _botBurst > 1 else 0.0)):
                                try:
                                    if _tv is not None and mat == 'armor' and getattr(_tv, 'health', 0) > 0:
                                        _cp = _clamp_impact_to_veh(_tv, _ballistic_arrival_pos(_gp, _v0 * _spd, _grv, _ft))
                                        if _cp is not None:
                                            pt = _cp
                                        _add_hit_decal(_tv, _hc, pt, sdir, _ispen, False, jitter=_jit)
                                except Exception: pass
                                try: pm.hide(s_id, pt)
                                except: pass
                                try: pm.explode(s_id, ed, mat, pt, sdir)
                                except: pass

                            if is_hit and isPenetration:
                                BigWorld.callback(flight_time, partial(explode_tracer, shotID, eff_descr, 'armor', impact_pt, shoot_dir))
                            elif is_hit and not isPenetration:
                                BigWorld.callback(flight_time, partial(explode_tracer, shotID, eff_descr, 'armor', impact_pt, shoot_dir))
                            else:
                                BigWorld.callback(flight_time, partial(explode_tracer, shotID, eff_descr, 'ground', impact_pt, shoot_dir))

                        for _bi in range(_botBurst):
                            BigWorld.callback(_bi * _botBurstInterval, lambda b=_bi: _fire_bot_shell(b))

                    except Exception: pass

                    def _apply_bot_impact(_is_hit=is_hit, _is_pen=isPenetration,
                                          _tVehID=tVehID, _tVeh=tVeh, _killerID=vehID,
                                          _team=team, _now=now, _descr=descr,
                                          _gun_pos=gun_pos, _t_real_pos=t_real_pos,
                                          _botHitComponent=_botHitComponent,
                                          _ai_yaw=getattr(veh, '_ai_yaw', 0.0)):
                        if _is_hit and not _is_pen:
                            try:
                                t_st = self._bot_state.setdefault(_tVehID, {'target': None, 'last_shot': 0.0})
                                t_st['last_attacker'] = _killerID
                                t_st['last_hit_dir'] = _ai_yaw
                                t_st['last_hit_time'] = _now

                                if _tVehID == self.playerAvatar.playerVehicleID:
                                    self.playerAvatar._shots_received = getattr(self.playerAvatar, '_shots_received', 0) + 1
                                    try:
                                        self.playerAvatar.showNoPenHitmarker()
                                    except Exception: pass
                                    try:
                                        _noPenYaw = (_entity_world_pos(_tVeh) - _gun_pos).yaw
                                        self.playerAvatar.showNoPenArc(_noPenYaw)
                                    except Exception: pass
                                    try:
                                        _apply_hit_impulse(_tVeh, _gun_pos, False)
                                    except Exception: pass
                                else:
                                    try:
                                        from gui.WindowsManager import g_windowsManager
                                        bw = getattr(g_windowsManager, 'battleWindow', None)
                                        if bw and hasattr(bw, 'vMarkersManager') and getattr(_tVeh, 'marker', -1) != -1:
                                            bw.vMarkersManager.updateMarkerState(_tVeh.marker, 'hit', False)
                                    except Exception: pass
                            except Exception: pass

                        elif _is_hit:
                            try:
                                if not _vehicle_is_alive(_tVeh):
                                    return
                                dmg = _descr.gun['shots'][0]['shell'].get('damage', (50, 50))
                                dmg_val = dmg[0] if isinstance(dmg, (list, tuple)) else int(dmg)
                                prev_hp = _vehicle_health(_tVeh)
                                new_hp = max(0, prev_hp - dmg_val)

                                try:
                                    _shotShell = _descr.gun['shots'][0]['shell']
                                    _hitShellType = _SHELL_KIND_TO_TYPE.get(_shotShell.get('kind'), ShellType.AP)
                                except Exception:
                                    _hitShellType = ShellType.AP
                                try:
                                    self.on_penetrating_hit(_tVeh, prev_hp - new_hp, _hitShellType, True,
                                                             hit_component=_botHitComponent)
                                except Exception: pass

                                t_st = self._bot_state.setdefault(_tVehID, {'target': None, 'last_shot': 0.0})
                                t_st['last_attacker'] = _killerID
                                t_st['last_hit_dir'] = _ai_yaw
                                t_st['last_hit_time'] = _now
                                t_st['_flank_side'] = -t_st.get('_flank_side', 1)

                                if _tVehID == self.playerAvatar.playerVehicleID:
                                    self.playerAvatar._shots_received = getattr(self.playerAvatar, '_shots_received', 0) + 1
                                    try:
                                        from gui.WindowsManager import g_windowsManager
                                        bw = getattr(g_windowsManager, 'battleWindow', None)
                                        if bw and hasattr(bw, 'damagePanel'): bw.damagePanel.updateHealth(new_hp)
                                    except Exception: pass

                                    try:
                                        _apply_hit_impulse(_tVeh, _gun_pos, True)
                                    except Exception: pass

                                if _tVehID != self.playerAvatar.playerVehicleID:
                                    try:
                                        from gui.WindowsManager import g_windowsManager
                                        bw = getattr(g_windowsManager, 'battleWindow', None)
                                        if bw and hasattr(bw, 'vMarkersManager') and getattr(_tVeh, 'marker', -1) != -1:
                                            bw.vMarkersManager.updateMarkerState(_tVeh.marker, 'hit_pierced', False)
                                    except Exception: pass

                                if new_hp <= 0:
                                    LOG_NOTE("[BATTLE] Bot DESTROYED: vehID=%d" % _tVeh.id)

                                    if _tVehID == self.playerAvatar.playerVehicleID:
                                        self.playerAvatar.isVehicleAlive = False
                                        self.playerAvatar.currentMove = 0.0
                                        self.playerAvatar.currentTurn = 0.0
                                        self._cur_speed = 0.0
                                        self._cur_rot = 0.0

                                        if getattr(self.playerAvatar, 'inputHandler', None):
                                            try:
                                                _revealed_killer = _killerID if self._killer_revealed(_killerID) else None
                                                self.playerAvatar.inputHandler.setKillerVehicleID(_revealed_killer)
                                                self.playerAvatar.inputHandler.activatePostmortem()
                                                pm_ctrl = self.playerAvatar.inputHandler._AvatarInputHandler__ctrls.get('postmortem')
                                                if pm_ctrl:
                                                    pm_cam_bw = pm_ctrl._PostMortemControlMode__cam.camera
                                                    _pm_target = getattr(self, '_offline_matrix', None)
                                                    if _pm_target is None:
                                                        _pm_target = getattr(_tVeh, '_offline_matrix', None)
                                                    pm_cam_bw.target = _pm_target
                                            except Exception:
                                                LOG_WARNING("[BATTLE] postmortem: failed to aim camera at own wreck (bot kill)")

                                        try:
                                            _avd = self.playerAvatar
                                            if _avd is not None and not getattr(_avd, '_offlineDeathVoicePlayed', False):
                                                _avd._offlineDeathVoicePlayed = True
                                                _avd._playCrewVoice('vehicle_destroyed')
                                                try: self._post_death_message(_killerID)
                                                except Exception: pass
                                        except Exception: pass
                                        try:
                                            self._destroy_player_modules_and_crew(_tVeh)
                                        except Exception: pass

                                        try:
                                            _show_dead_damage_panel()
                                        except Exception: pass

                                    _set_veh_health(_tVeh, 0)
                                    self.arena.vehicles.setdefault(_tVehID, {})['isAlive'] = False

                                    try:
                                        if hasattr(self, 'playerAvatar'): self.playerAvatar._deadVehicleIDs.add(_tVeh.id)
                                    except: pass

                                    try:
                                        if getattr(_tVeh, 'isStarted', False) and getattr(_tVeh, 'appearance', None):
                                            _tVeh.set_health(prev_hp)
                                            try: _tVeh.appearance.changeEngineMode((0,0))
                                            except: pass
                                    except Exception: pass

                                    _tVeh.isCrewActive = False
                                    try:
                                        if getattr(_tVeh, 'isStarted', False) and getattr(_tVeh, 'appearance', None):
                                            _tVeh.set_isCrewActive(True)
                                    except Exception: pass

                                    try:
                                        from gui.WindowsManager import g_windowsManager
                                        bw = getattr(g_windowsManager, 'battleWindow', None)
                                        if bw and hasattr(bw, 'vMarkersManager'):
                                            marker = getattr(_tVeh, 'marker', -1)
                                            if marker != -1: bw.vMarkersManager.updateMarkerState(marker, 'dead', False)
                                    except Exception: pass

                                    try: _tVeh.filter.allowLagProcessing = False
                                    except Exception: pass

                                    try:
                                        self._register_kill(_tVehID, _killerID, _team, killer_is_player=False)
                                        self._check_win_condition()
                                    except Exception: pass
                                else:
                                    _set_veh_health(_tVeh, new_hp)
                            except Exception: pass

                    try:
                        _impact_delay = min(max(flight_time, 0.25), 20.0)
                    except Exception:
                        _impact_delay = 0.25
                    try:
                        for _bi in range(_botBurst):
                            BigWorld.callback(_bi * _botBurstInterval + _impact_delay, _apply_bot_impact)
                    except Exception: pass
        except Exception as _tick_e:
            _now_exc = BigWorld.time()
            if _now_exc - getattr(self, '_last_ai_tick_exc_log', 0.0) > 5.0:
                self._last_ai_tick_exc_log = _now_exc
                LOG_ERROR("[BATTLE][AI] _botAITick tick failed: %s" % (_tick_e,))
                try:
                    LOG_CURRENT_EXCEPTION()
                except Exception: pass


    def _updateCapturePoints(self):
        if getattr(self, '_is_finished', False): return
        if not self.arena or getattr(self.arena, 'period', 0) != constants.ARENA_PERIOD_BATTLE: return

        now = BigWorld.time()
        if now - getattr(self, '_capture_last_tick', 0.0) < 1.0: return
        try:
            if now - float(getattr(self, '_battleEnterTime', now) or now) < 2.0:
                return
        except Exception:
            pass
        self._capture_last_tick = now

        zones = self._get_base_zones()
        if not zones: return

        if not hasattr(self, '_capture_points'): self._capture_points = {1: 0, 2: 0}

        for base_team, zone in zones.items():
            attacking_team = 2 if base_team == 1 else 1
            baseID = zone.get('baseID', 1)

            attackers_in_zone = []
            defenders_in_zone = False
            defenders_ids = []
            _player_veh_id = getattr(self.playerAvatar, 'playerVehicleID', None)

            for vehID, descr, isPlayer in self.vehicles:
                veh = BigWorld.entity(vehID)
                if veh is None or not getattr(veh, 'isStarted', False):
                    continue
                if getattr(veh, 'health', 0) <= 0:
                    continue
                pos = _offline_entity_pos(veh)
                if pos is None:
                    continue
                try:
                    horiz_dist = _math.sqrt((pos.x - zone['pos'].x)**2 + (pos.z - zone['pos'].z)**2)
                except Exception:
                    continue
                if horiz_dist <= zone['radius']:
                    veh_team = self.arena.vehicles.get(vehID, {}).get('team', 0)
                    if veh_team == attacking_team:
                        attackers_in_zone.append(vehID)
                    elif veh_team == base_team:
                        defenders_in_zone = True
                        defenders_ids.append(vehID)

            points = self._capture_points.get(attacking_team, 0)

            if attackers_in_zone and not defenders_in_zone:
                speed = 2 if len(attackers_in_zone) < 2 else 3
                points = min(100, points + speed)
                self._capture_points[attacking_team] = points
                try:
                    if _player_veh_id in attackers_in_zone:
                        self._avatar_capture_points = getattr(self, '_avatar_capture_points', 0) + speed
                except Exception: pass
                try: self.arena.update(constants.ARENA_UPDATE_BASE_POINTS, cPickle.dumps((base_team, 1, points)))
                except Exception: pass
                LOG_NOTE("[BATTLE] Team %d is capturing base %d: %d/100" % (attacking_team, base_team, points))

            elif defenders_in_zone and points > 0:
                points = max(0, points - 5)
                self._capture_points[attacking_team] = points
                try:
                    if _player_veh_id in defenders_ids:
                        self._avatar_defense_points = getattr(self, '_avatar_defense_points', 0) + 5
                except Exception: pass
                try: self.arena.update(constants.ARENA_UPDATE_BASE_POINTS, cPickle.dumps((base_team, 1, points)))
                except Exception: pass
                LOG_NOTE("[BATTLE] Team %d base %d contested! %d/100" % (attacking_team, base_team, points))
                try: self.arena.update(constants.ARENA_UPDATE_BASE_POINTS, cPickle.dumps((base_team, 1, points)))
                except Exception: pass
                LOG_NOTE("[BATTLE] Team %d is capturing base %d: %d/100" % (attacking_team, base_team, points))

            elif defenders_in_zone and points > 0:
                points = max(0, points - 5)
                self._capture_points[attacking_team] = points
                try: self.arena.update(constants.ARENA_UPDATE_BASE_POINTS, cPickle.dumps((base_team, 1, points)))
                except Exception: pass
                LOG_NOTE("[BATTLE] Team %d base %d contested! %d/100" % (attacking_team, base_team, points))

            elif not attackers_in_zone and points > 0:
                points = max(0, points - 5)
                self._capture_points[attacking_team] = points
                try: self.arena.update(constants.ARENA_UPDATE_BASE_POINTS, cPickle.dumps((base_team, 1, points)))
                except Exception: pass

        self._check_win_condition()

    def _lock_offline_spawn_pose(self, veh, vehID, isPlayer=False):

        if veh is None:
            return None
        try:
            _sp_yaw = self._spawn_yaws.get(vehID, 0.0)
        except Exception:
            _sp_yaw = 0.0
        try:
            _xyz = self._spawn_xyz.get(vehID)
        except Exception:
            _xyz = None
        m = getattr(veh, '_offline_matrix', None)
        if m is None:
            m = Math.Matrix()
            veh._offline_matrix = m
        m.setRotateYPR((_sp_yaw, 0, 0))
        if _xyz is not None:
            _sy = _spawn_ground_y(self.spaceID, _xyz[0], _xyz[2], _xyz[1])
            m.translation = Math.Vector3(_xyz[0], _sy, _xyz[2])
        else:
            try:
                _p = veh.position
                if abs(_p.x) > 8.0 or abs(_p.z) > 8.0:
                    m.translation = Math.Vector3(_p.x, _p.y, _p.z)
            except Exception:
                pass
        try:
            _stamp_entity_pose(veh, m)
        except Exception:
            pass
        if isPlayer:
            self._offline_matrix = m
            try:
                self._cur_pos = Math.Vector3(m.translation)
                self._cur_yaw = _sp_yaw
            except Exception:
                pass
        return m

    def _finalizeInit(self, resourceRefs):
        for vehID, _, _ in self.vehicles:
            veh = BigWorld.entity(vehID)
            if veh is None or not veh.inWorld:
                BigWorld.callback(0.1, lambda: self._finalizeInit(resourceRefs))
                return

        self._spotted_vehicles = set([v[0] for v in self.vehicles])
        self._spotted_timers = {}
        self._spottingTick()
        self._start_nav_grid_build()

        self._patch_vehicle()
        self._patch_effects()

        for vehID, descr, isPlayer in self.vehicles:
            veh = BigWorld.entity(vehID)
            if not veh: continue
            veh.isPlayer = isPlayer
            veh.isCrewActive = True
            _set_veh_health(veh, descr.maxHealth)
            veh.engineMode = (1, 0)
            veh.damageStickers = ()
            veh.publicStateModifiers = []
            try:
                veh.typeDescriptor.keepPrereqs(resourceRefs)
                veh._Vehicle__prereqs = resourceRefs
            except: pass
            try:
                m = self._lock_offline_spawn_pose(veh, vehID, isPlayer)
                if m is None:
                    m = Math.Matrix()
                    veh._offline_matrix = m
                servo = getattr(veh, '_offline_servo', None)
                if servo is None:
                    servo = BigWorld.Servo(m)
                    veh._offline_servo = servo
                if isPlayer:
                    self._offline_matrix = m
                    self._offline_servo = servo
                    self._motor_attached = False
                    self._motor_attach_attempts = 0
                    self._try_attach_player_motor(veh)
                    LOG_NOTE("[BATTLE] player hull locked to ground y=%.2f" % (m.translation.y,))
                else:
                    def _try_attach_motor(vid=vehID, _servo=servo, _attempt=0):
                        _v = BigWorld.entity(vid)
                        if _v is None: return
                        if getattr(_v, 'model', None) is not None:
                            try:
                                if _v.model.motors: _v.model.delMotor(_v.model.motors[0])
                                _v.model.addMotor(_servo)
                            except Exception: pass
                        elif _attempt < 20:
                            BigWorld.callback(0.2, lambda: _try_attach_motor(vid, _servo, _attempt + 1))
                    _try_attach_motor()

                    try:
                        if getattr(veh, 'appearance', None) is not None:
                            veh._bot_turret_yaw_cur = 0.0
                            veh._bot_gun_pitch_cur = 0.0
                            veh._ai_desired_turret_yaw = 0.0
                            veh._ai_desired_gun_pitch = 0.0
                            _tpin = Math.Matrix()
                            _tpin.setRotateY(0.0)
                            veh.appearance.turretMatrix.target = _tpin
                            _gpin = Math.Matrix()
                            _gpin.setRotateX(0.0)
                            veh.appearance.gunMatrix.target = _gpin
                    except Exception:
                        pass
            except Exception:
                pass

        from VehicleGunRotator import VehicleGunRotator
        try:
            real_gr = VehicleGunRotator(self.playerAvatar)
            descr = self.playerAvatar.vehicleTypeDescriptor
            turretSpeed = descr.turret['rotationSpeed']
            gunSpeed    = turretSpeed * 0.5
            real_gr._VehicleGunRotator__turretRotationSpeed = turretSpeed
            real_gr._VehicleGunRotator__gunRotationSpeed    = gunSpeed
            self.playerAvatar.gunRotator = real_gr
            from VehicleGunRotator import VehicleGunRotator as _VGR2
            if not getattr(_VGR2, '_patched_lofted', False):
                _VGR2.setLoftedTrajectory  = lambda self, v: setattr(self, '_VehicleGunRotator__bLoftedTrajectory', v)
                _VGR2.switchLoftedTrajectory = lambda self: setattr(self, '_VehicleGunRotator__bLoftedTrajectory', not self._VehicleGunRotator__bLoftedTrajectory)
                _VGR2._patched_lofted = True
        except Exception:
            self.playerAvatar.gunRotator = DummyGunRotator()

        self.playerAvatar.turretMatrix = self.playerAvatar.gunRotator.turretMatrix
        self.playerAvatar.gunMatrix = self.playerAvatar.gunRotator.gunMatrix

        from AvatarInputHandler import AvatarInputHandler
        self.playerAvatar.inputHandler = AvatarInputHandler()
        try:
            self.playerAvatar.inputHandler.start()
        except Exception:
            LOG_CURRENT_EXCEPTION()
            LOG_ERROR("[BATTLE] AvatarInputHandler.start failed")
        try:
            clrs = self.playerAvatar.guiConfig['silhouetteColors']
            BigWorld.wgSetEdgeDetectColors((clrs['self'], clrs['enemy'], clrs['friend']))
        except Exception: pass

        try:
            BigWorld.target.caps(1)
            try:
                import Account as _AccMod
                _PA = _AccMod.PlayerAccount
                if not getattr(_PA, '_patched_target_offline', False):
                    def _account_targetFocus(self_acc, entity):
                        try:
                            _p = BigWorld.player()
                            if _p is not self_acc: _p.targetFocus(entity)
                        except Exception: pass
                    def _account_targetBlur(self_acc, prevEntity):
                        try:
                            _p = BigWorld.player()
                            if _p is not self_acc: _p.targetBlur(prevEntity)
                        except Exception: pass
                    _PA.targetFocus = _account_targetFocus
                    _PA.targetBlur  = _account_targetBlur
                    _PA._patched_target_offline = True
            except Exception: pass

            try:
                for _ent in BigWorld.entities.values():
                    import Account as _AccMod2
                    if isinstance(_ent, _AccMod2.PlayerAccount):
                        _ent_id = _ent.id
                        _orig_player_bak = BigWorld.player
                        BigWorld.player = BigWorld._orig_player_fn
                        try: BigWorld.target.caps(1)
                        finally: BigWorld.player = lambda: self.playerAvatar
                        break
            except Exception: pass
        except Exception: pass

        try:
            import sys
            control_modes = sys.modules.get('AvatarInputHandler.control_modes')
            if control_modes:
                def _wrap_flash_enable(cls_name):
                    cls = getattr(control_modes, cls_name, None)
                    if cls is None or getattr(cls, '_offline_enable_wrapped', False):
                        return
                    _orig = cls.enable
                    def _patched_flash_enable(self, state):
                        if state is not None and 'reload' in state:
                            if 'start_time' in state['reload'] and 'startTime' not in state['reload']:
                                state['reload']['startTime'] = state['reload']['start_time']
                        return _orig(self, state)
                    cls.enable = _patched_flash_enable
                    cls._offline_enable_wrapped = True
                _wrap_flash_enable('_FlashGunMarker')
                _wrap_flash_enable('_SPGFlashGunMarker')
        except Exception: pass

        self._patch_control_modes()
        self._applyAimPatches()
        self.playerAvatar.inputHandler.setReloading(0)

        playerVeh = BigWorld.entity(self.playerAvatar.playerVehicleID)
        if playerVeh:
            cam = BigWorld.camera()
            if cam is None: cam = BigWorld.CursorCamera()
            cam.spaceID = self.spaceID
            cam.target = _vehicle_matrix_provider(playerVeh) or playerVeh.matrix
            BigWorld.camera(cam)
            self.playerAvatar.bindToVehicle(True, self.playerAvatar.playerVehicleID)
            BigWorld.worldDrawEnabled(True)

        g_windowsManager.startBattle()
        self.battleWindow = g_windowsManager.battleWindow

        try:
            self.playerAvatar.inputHandler.attachBattleWindow(self.battleWindow)
        except Exception: pass

        if _generate_offline_chat_channels():
            BigWorld.callback(3.0, self._botChatTick)
        else:
            BigWorld.callback(1.0, self._retryOfflineChatInit)

        try:
            from gui.Scaleform.Battle import Battle as _BattleCls
            if not getattr(_BattleCls, '_patched_teamkill_score', False):
                _orig_updatePlayers = _BattleCls._Battle__updatePlayers
                def _patched_updatePlayers(self_bw, *args):
                    _orig_updatePlayers(self_bw, *args)
                    try:
                        _p = BigWorld.player()
                        _battle = getattr(_p, '_offline_battle', None)
                        if _battle is None: return
                        _current_arena = _battle.arena
                        _bonus = getattr(_current_arena, '_teamkill_bonus', {})
                        _pt = getattr(_p, 'team', 1)
                        _et = 2 if _pt == 1 else 1
                        _tf = [0, 0]
                        for _vid, _vdata in _current_arena.vehicles.items():
                            _t = _vdata.get('team', 0)
                            if _t in (1, 2):
                                _fs = _current_arena.statistics.get(_vid, {}).get('frags', 0)
                                _tf[_t - 1] += _fs

                        _tf[0] -= _bonus.get(1, 0)
                        _tf[1] -= _bonus.get(2, 0)

                        _allied = _tf[_pt - 1]; _enemy  = _tf[_et - 1]
                        _fc = getattr(self_bw, '_Battle__fragCorrelation', None)
                        if _fc is not None: _fc.updateFrags(_allied, _enemy)
                        else:
                            _proxy = getattr(self_bw, 'proxy', None) or getattr(self_bw, '_Battle__proxy', None)
                            if _proxy is not None: _proxy.call('battle.fragCorrelationBar.updateFrags', [_allied, _enemy])
                    except Exception: pass
                _BattleCls._Battle__updatePlayers = _patched_updatePlayers
                _BattleCls._patched_teamkill_score = True
        except Exception: pass

        for vehID, descr, isPlayer in self.vehicles:
            veh = BigWorld.entity(vehID)
            if not veh: continue
            veh.health       = descr.maxHealth
            try:
                _set_veh_health(veh, descr.maxHealth)
            except Exception:
                pass
            try:
                veh._offline_health = descr.maxHealth
            except Exception:
                pass
            veh.isCrewActive = True
            if not getattr(veh, 'isStarted', False):
                try:
                    if not isPlayer:
                        try: veh.targetCaps = [1]
                        except Exception: pass
                    veh.startVisual()
                    veh.isStarted = True
                    try:
                        self._lock_offline_spawn_pose(veh, vehID, isPlayer)
                    except Exception:
                        pass
                    if isPlayer and getattr(veh, 'appearance', None):
                        try: self.playerAvatar.gunRotator.start()
                        except Exception: pass
                    if isPlayer:
                        try:
                            self._try_attach_player_motor(veh)
                        except Exception:
                            pass
                    else:
                        try:
                            _om = getattr(veh, '_offline_matrix', None)
                            _mdl = getattr(veh, 'model', None)
                            if _om is not None and _mdl is not None:
                                try:
                                    if _mdl.motors:
                                        _mdl.delMotor(_mdl.motors[0])
                                except Exception:
                                    pass
                                _servo = getattr(veh, '_offline_servo', None)
                                if _servo is None:
                                    _servo = BigWorld.Servo(_om)
                                    veh._offline_servo = _servo
                                try:
                                    _mdl.addMotor(_servo)
                                except Exception:
                                    pass
                        except Exception:
                            pass
                    flt = getattr(veh, 'filter', None)
                except Exception: veh.isStarted = True

            if isPlayer:
                flt = getattr(veh, 'filter', None)
                if flt is not None:
                    try:
                        flt.allowStrafeCompensation = False
                        flt.allowLagProcessing = True
                        try:
                            if hasattr(veh, 'appearance') and veh.appearance:
                                real_fashion = getattr(veh.appearance, '_VehicleAppearance__fashion', None)
                                veh_model = getattr(veh, 'model', None)
                                if real_fashion is not None and veh_model is not None:
                                    if not hasattr(veh_model, 'wg_fashion'): veh_model.wg_fashion = real_fashion
                        except Exception: pass
                        try:
                            _fsh2 = getattr(getattr(veh, 'appearance', None), '_VehicleAppearance__fashion', None)
                            if _fsh2 is not None and flt is not None: _fsh2.movementInfo = flt.movementInfo
                        except Exception: pass
                    except Exception: pass

        for _bvID, _bDescr, _bIsPlayer in self.vehicles:
            if _bIsPlayer: continue
            _bveh = BigWorld.entity(_bvID)
            if not _bveh: continue
            _bflt = getattr(_bveh, 'filter', None)
            if _bflt is None: continue
            try:
                _bflt.vehicleMaxMove = _bDescr.physics['speedLimits'][0] * 2.0
                _bflt.allowLagProcessing = True
                _bflt.allowStrafeCompensation = False
                try: _bflt.setInitialSpeeds(0.0, 0.0)
                except Exception: pass
                _bmat = getattr(_bveh, '_offline_matrix', None)
                if _bmat is not None:
                    try:
                        _bflt.newPosition(_bmat.translation, _bmat, BigWorld.time(), 0.0, 0.0)
                    except Exception:
                        pass
            except Exception: pass

        self.arena.onPeriodChange += self._onPeriodChange

        _BATTLE_DURATION = float(getattr(self, '_battle_duration', 900.0) or 900.0)

        _LOADING_PHASE       = 6.0
        _PREBATTLE_COUNTDOWN = 30.0

        self._startBattleMusic()

        _connect_queue = []
        for _cqID, _cqD, _cqP in self.vehicles:
            if _cqP: continue
            _cqName = None
            if self.arena is not None:
                try: _cqName = self.arena.vehicles.get(_cqID, {}).get('name', None)
                except Exception: _cqName = None
            if not _cqName: _cqName = 'Bot_%d' % _cqID
            _connect_queue.append((_cqID, _cqName))
        self._connect_queue = _connect_queue
        self._entry_phase_done = False

        def _battle_entry_finish_loading():
            if getattr(self, '_is_finished', False): return
            if getattr(self, '_entry_phase_done', False): return
            self._entry_phase_done = True
            try: g_windowsManager.showBattle()
            except Exception: pass
            self.battleWindow = g_windowsManager.battleWindow
            self._battleEnterTime = BigWorld.time()
            self._setPrebattleTimer(_PREBATTLE_COUNTDOWN)
            self._cb_prebattle_repeat = BigWorld.callback(1.0, lambda: self._setPrebattleTimer(_PREBATTLE_COUNTDOWN))

            def _switch_to_battle():
                if getattr(self, '_is_finished', False): return
                if self.arena and self.arena.period == constants.ARENA_PERIOD_PREBATTLE:
                    _now = BigWorld.time()
                    _bpd = (constants.ARENA_PERIOD_BATTLE, _now + _BATTLE_DURATION, _BATTLE_DURATION, None)
                    self.arena.update(constants.ARENA_UPDATE_PERIOD, cPickle.dumps(_bpd))
            self._cb_switch_to_battle = BigWorld.callback(_PREBATTLE_COUNTDOWN, _switch_to_battle)
            self._startBattleMusic()

        def _battle_entry_connect_step():
            if getattr(self, '_is_finished', False): return
            if getattr(self, '_entry_phase_done', False): return
            _q = getattr(self, '_connect_queue', None)
            if _q:
                _cid, _cname = _q.pop(0)
                _offline_post_chat_message(_OFFLINE_CHAT_ALL_CID, _cid, _cname, u'Игрок подключился к бою')
                if _q:
                    BigWorld.callback(0.3, _battle_entry_connect_step)
                    return
            _battle_entry_finish_loading()

        BigWorld.callback(0.25, _battle_entry_connect_step)
        BigWorld.callback(_LOADING_PHASE, _battle_entry_finish_loading)

        if not getattr(self, '_loading_bar_started', False):
            self._loading_bar_started = True
            self._load_bar_start_t = BigWorld.time()
            def _offline_loading_progress_tick():
                if getattr(self, '_is_finished', False): return
                if getattr(self, '_entry_phase_done', False): return
                _frac = min(1.0, (BigWorld.time() - getattr(self, '_load_bar_start_t', BigWorld.time())) / _LOADING_PHASE)
                try:
                    _wm2 = g_windowsManager
                    if _wm2.window is not None:
                        _wm2.window.call('loading.setProgress', [_frac])
                except Exception: pass
                if _frac < 1.0:
                    BigWorld.callback(0.25, _offline_loading_progress_tick)
            BigWorld.callback(0.25, _offline_loading_progress_tick)

        BigWorld.callback(1.0, self._createVehicleMarkers)
        BigWorld.callback(0.05, self.__movementTick)
        from gui.Cursor import forceShowCursor
        forceShowCursor(False)
        BigWorld.callback(0.5, self._fixTankIndicator)
        BigWorld.worldDrawEnabled(True)
        self.playerAvatar.onAvatarReady()
        g_playerEvents.onAvatarReady()

        BigWorld.callback(0.5, self._updateBattleUI)
        BigWorld.callback(1.5, self._startMinimap)
        BigWorld.callback(1.0, self._setupAmmoPanel)

        try:
            from helpers import SoundGroups as SG
            if SG.g_instance is not None:
                SG.g_instance.enableSounds('arena', True)
                SG.g_instance.applyPreferences()
        except Exception: pass

        BigWorld.callback(0.5, self._startBattleMusic)

    def _onPeriodChange(self, period, periodEndTime, periodLength, addInfo):
        if self.playerAvatar and getattr(self.playerAvatar, 'inputHandler', None):
            self.playerAvatar.inputHandler._AvatarInputHandler__isArenaStarted = (period == constants.ARENA_PERIOD_BATTLE)
            if period == constants.ARENA_PERIOD_BATTLE:
                self._battle_started = True
                if not getattr(self, '_bot_ai_started', False):
                    self._bot_ai_started = True
                    self._botAITick()
                if not getattr(self, '_ammo_panel_ready', False):
                    BigWorld.callback(0.2, self._setupAmmoPanel)
                try:
                    _sn = getattr(self.playerAvatar, 'soundNotifications', None) or getattr(self, 'soundNotifications', None)
                    def _play_start_battle(_sn=_sn):
                        try:
                            if _sn is not None:
                                _sn.play('start_battle')
                        except Exception:
                            pass
                    _play_start_battle()
                    BigWorld.callback(0.35, _play_start_battle)
                except Exception: pass
                self._spotted_timers = {}
                self._spot_prev_pos = {}
                self._first_spotted_ids = set()
                self._player_detected_ids = set()
                self._player_observer_pos = None
                self._player_observer_vr = 0.0
                self._spotted_vehicles = set()
                self._avatar_capture_points = 0
                self._avatar_defense_points = 0
                try:
                    from AvatarInputHandler import aims
                    if self.playerAvatar.vehicleTypeDescriptor:
                        max_health = self.playerAvatar.vehicleTypeDescriptor.maxHealth
                        aims._g_aimState['health']['cur'] = max_health
                        aims._g_aimState['health']['max'] = max_health
                except: pass
                try:
                    if self.playerAvatar.playerVehicleID is None: self.playerAvatar.playerVehicleID = self.vehicles[0][0]
                    try: self.playerAvatar.inputHandler._AvatarInputHandler__isArenaStarted = True
                    except Exception: pass

                    _saved_bind = self.playerAvatar.bindToVehicle
                    self.playerAvatar.bindToVehicle = lambda *a, **kw: None
                    try: self.playerAvatar.inputHandler.onControlModeChanged('arcade')
                    finally: self.playerAvatar.bindToVehicle = _saved_bind

                    self.playerAvatar.bindToVehicle(True, self.playerAvatar.playerVehicleID)

                    veh = BigWorld.entity(self.playerAvatar.playerVehicleID)
                    if veh:
                        cam = BigWorld.camera()
                        if cam and hasattr(cam, 'target'): cam.target = veh.matrix
                        BigWorld.worldDrawEnabled(True)
                        self._updateBattleUI()
                        BigWorld.callback(0.5, self._fixTankIndicator)
                except Exception: pass

        if period == constants.ARENA_PERIOD_AFTERBATTLE:
            self._showEndBattleStats()

    def _killer_revealed(self, killer_veh_id):
        try:
            if killer_veh_id is None:
                return False
            _av = getattr(self, 'playerAvatar', None)
            if _av is None:
                return False
            if killer_veh_id == getattr(_av, 'playerVehicleID', None):
                return True
            _arena = getattr(self, 'arena', None)
            if _arena is not None:
                _killer_team = _arena.vehicles.get(killer_veh_id, {}).get('team', 0)
                if _killer_team == getattr(_av, 'team', 1):
                    return True
            return killer_veh_id in getattr(self, '_spotted_vehicles', set())
        except Exception:
            return False

    def _register_kill(self, victim_veh_id, killer_veh_id, killer_team, killer_is_player=False):
        _arena = self.arena
        _av    = self.playerAvatar
        _pvid  = _av.playerVehicleID
        _victim_team = _arena.vehicles.get(victim_veh_id, {}).get('team', 0)
        if not hasattr(_arena, 'statistics'): _arena.statistics = {}

        _is_teamkill = (_victim_team == killer_team and _victim_team != 0)

        if killer_is_player:
            _av._frags = getattr(_av, '_frags', 0) + (-1 if _is_teamkill else 1)
            if not _is_teamkill:
                try: _av._player_killed_ids.add(victim_veh_id)
                except Exception: pass
            if _pvid not in _arena.statistics: _arena.statistics[_pvid] = {'frags': 0}
            _arena.statistics[_pvid]['frags'] = _av._frags

            try: _arena.update(constants.ARENA_UPDATE_VEHICLE_STATISTICS, cPickle.dumps((_pvid, _av._frags, 0, 0)))
            except Exception: pass

            if _is_teamkill:
                try: _av._playCrewVoice('ally_killed_by_player')
                except Exception: pass
            else:
                try:
                    if _pvid in _arena.vehicles: _arena.vehicles[_pvid]['isAvatarReady'] = True
                    _av._playCrewVoice('enemy_killed_by_player')
                except Exception: pass
        else:
            if killer_veh_id not in _arena.statistics: _arena.statistics[killer_veh_id] = {'frags': 0}
            _arena.statistics[killer_veh_id]['frags'] += (-1 if _is_teamkill else 1)

            try: _arena.update(constants.ARENA_UPDATE_VEHICLE_STATISTICS, cPickle.dumps((killer_veh_id, _arena.statistics[killer_veh_id]['frags'], 0, 0)))
            except Exception: pass

            if not _is_teamkill:
                try:
                    if killer_veh_id in _arena.vehicles: _arena.vehicles[killer_veh_id]['isAvatarReady'] = True
                except Exception: pass

        try: _av._deadVehicleIDs.add(victim_veh_id)
        except Exception: pass

        try: self._setVehicleVisibility(victim_veh_id, True)
        except Exception: pass

        try:
            _victim_veh = BigWorld.entity(victim_veh_id)
            if _victim_veh is not None:
                try:
                    _vom = getattr(_victim_veh, '_offline_matrix', None)
                    if _vom is not None:
                        _victim_veh.position = _vom.translation
                except Exception: pass
                _victim_veh.health = 0
                try:
                    _set_veh_health(_victim_veh, 0)
                except Exception:
                    pass

            _arena.onVehicleKilled(victim_veh_id, killer_veh_id, 0)
        except Exception: pass

        try:
            from gui.WindowsManager import g_windowsManager
            _bw = getattr(g_windowsManager, 'battleWindow', None)
            if _bw:
                _pt = getattr(self.playerAvatar, 'team', 1)
                _et = 2 if _pt == 1 else 1
                _tf = [0, 0]
                for _vid, _vdata in _arena.vehicles.items():
                    _t = _vdata.get('team', 0)
                    if _t in (1, 2): _tf[_t - 1] += _arena.statistics.get(_vid, {}).get('frags', 0)

                _allied, _enemy = _tf[_pt - 1], _tf[_et - 1]
                _fc = getattr(_bw, '_Battle__fragCorrelation', None)
                if _fc is not None: _fc.updateFrags(_allied, _enemy)
                if hasattr(_bw, '_Battle__updatePlayers'): _bw._Battle__updatePlayers()
        except Exception: pass

        try:
            _victim_name = _killfeed_display_name(self, victim_veh_id)
            if killer_is_player:
                _killer_name = getattr(self.playerAvatar, 'name', None) or '?'
            else:
                _killer_name = _killfeed_display_name(self, killer_veh_id)
            _pt = getattr(self.playerAvatar, 'team', 1)

            _is_suicide = (victim_veh_id == killer_veh_id)
            if _is_suicide:
                _kf_key = 'ally_suicide' if _victim_team == _pt else 'enemy_suicide'
                _kf_args = {'entity': _victim_name}
            else:
                if _is_teamkill:
                    if killer_is_player:
                        _kf_key = 'player_friendly_fire_frag'
                    elif killer_team == _pt:
                        _kf_key = 'ally_friendly_fire_frag'
                    else:
                        _kf_key = 'enemy_friendly_fire_frag'
                else:
                    if killer_is_player:
                        _kf_key = 'player_frag'
                    elif killer_team == _pt:
                        _kf_key = 'ally_frag'
                    else:
                        _kf_key = 'enemy_frag'
                _kf_args = {'attacker': _killer_name, 'target': _victim_name}

            if not _killfeed_show(self, _kf_key, _kf_args):
                _fb = _KILLFEED_FALLBACK.get(_kf_key)
                if _fb is not None:
                    try:
                        _kf_text = _fb[0] % _kf_args
                    except Exception:
                        _kf_text = _kf_key
                    self._killfeed_add(_kf_text, _fb[1])
        except Exception: pass

    def _killfeed_add(self, text, colour):
        try:
            import GUI
        except Exception:
            return
        if not hasattr(self, '_killfeed_entries'): self._killfeed_entries = []

        try:
            comp = GUI.Text(text)
            comp.horizontalAnchor = 'RIGHT'
            comp.verticalAnchor = 'TOP'
            comp.heightMode = 'PIXEL'
            comp.widthMode = 'PIXEL'
            comp.verticalPositionMode = 'PIXEL'
            comp.horizontalPositionMode = 'PIXEL'
            comp.colour = Math.Vector4(colour[0], colour[1], colour[2], colour[3])
            try: comp.font = 'default_smaller.font'
            except Exception: pass
            GUI.addRoot(comp)
        except Exception:
            return

        entry = {'comp': comp}
        self._killfeed_entries.append(entry)

        _MAX_KILLFEED_LINES = 6
        while len(self._killfeed_entries) > _MAX_KILLFEED_LINES:
            _old = self._killfeed_entries.pop(0)
            try: GUI.delRoot(_old['comp'])
            except Exception: pass

        self._killfeed_relayout()

        def _expire(tok=entry):
            try:
                if tok in self._killfeed_entries:
                    self._killfeed_entries.remove(tok)
                    try: GUI.delRoot(tok['comp'])
                    except Exception: pass
                    self._killfeed_relayout()
            except Exception: pass

        BigWorld.callback(6.0, _expire)

    def _killfeed_relayout(self):
        _top_margin_px = 90
        _line_height_px = 22
        _right_margin_px = 20
        for i, entry in enumerate(getattr(self, '_killfeed_entries', [])):
            try:
                entry['comp'].position = Math.Vector3(-_right_margin_px, _top_margin_px + i * _line_height_px, 0.0)
            except Exception: pass

    def _killfeed_clear(self):
        try:
            import GUI
        except Exception:
            return
        for entry in getattr(self, '_killfeed_entries', []):
            try: GUI.delRoot(entry['comp'])
            except Exception: pass
        self._killfeed_entries = []

    def _check_win_condition(self):
        if getattr(self, '_is_finished', False): return
        if getattr(self, '_finish_called', False): return
        if getattr(self, '_results_shown', False): return

        try:
            if getattr(self, '_capture_points', None):
                if self._capture_points.get(1, 0) >= 100:
                    try: self.arena.update(constants.ARENA_UPDATE_BASE_CAPTURED, cPickle.dumps(2))
                    except Exception: pass
                    BigWorld.callback(1.5, partial(self._showEndBattleStats, forced_winner=1))
                    return
                if self._capture_points.get(2, 0) >= 100:
                    try: self.arena.update(constants.ARENA_UPDATE_BASE_CAPTURED, cPickle.dumps(1))
                    except Exception: pass
                    BigWorld.callback(1.5, partial(self._showEndBattleStats, forced_winner=2))
                    return
        except Exception: pass

        try:
            _av = self.playerAvatar
            if getattr(_av, 'isVehicleAlive', True):
                _my_veh = BigWorld.entity(_av.playerVehicleID)
                if _my_veh is None or getattr(_my_veh, 'health', 1) <= 0:
                    _av.isVehicleAlive = False
        except Exception: pass

        alive_teams = set()
        for vehID, descr, isPlayer in self.vehicles:
            if vehID in getattr(self.playerAvatar, '_deadVehicleIDs', set()): continue
            veh = BigWorld.entity(vehID)
            if veh is not None and getattr(veh, 'health', 0) > 0:
                team = self.arena.vehicles.get(vehID, {}).get('team', 0)
                if team > 0: alive_teams.add(team)

        if len(alive_teams) <= 1:
            BigWorld.callback(1.5, self._showEndBattleStats)

    def _showEndBattleStats(self, forced_winner=None, early_exit=False):
        if getattr(self, '_results_shown', False): return
        if getattr(self, '_is_finished', False): return
        if getattr(self, '_finish_called', False): return
        self._results_shown = True

        if forced_winner is not None:
            winner_team = forced_winner
        else:
            alive_count = {}
            hp_pool = {}
            hp_max_pool = {}
            for vehID, descr, isPlayer in self.vehicles:
                try:
                    team = self.arena.vehicles[vehID]['team']
                except Exception:
                    continue
                if not team:
                    continue
                try:
                    veh = BigWorld.entity(vehID)
                except Exception:
                    veh = None
                try:
                    if veh is not None:
                        _h = max(0, getattr(veh, 'health', 0))
                    else:
                        _h = 0
                    try:
                        _td = getattr(veh, 'typeDescriptor', None) or descr
                        _mh = getattr(_td, 'maxHealth', 0) or 0
                    except Exception:
                        _mh = 0
                except Exception:
                    _h = 0
                    _mh = 0
                if _h > 0:
                    alive_count[team] = alive_count.get(team, 0) + 1
                hp_pool[team] = hp_pool.get(team, 0) + _h
                hp_max_pool[team] = hp_max_pool.get(team, 0) + _mh

            if len(alive_count) == 0:
                winner_team = 0
            elif len(alive_count) == 1:
                winner_team = list(alive_count.keys())[0]
            else:
                try:
                    _t1_alive = alive_count.get(1, 0)
                    _t2_alive = alive_count.get(2, 0)
                    if _t1_alive != _t2_alive:
                        winner_team = 1 if _t1_alive > _t2_alive else 2
                    else:
                        _t1_hp = hp_pool.get(1, 0)
                        _t2_hp = hp_pool.get(2, 0)
                        _t1_mx = hp_max_pool.get(1, 0) or 1
                        _t2_mx = hp_max_pool.get(2, 0) or 1
                        _r1 = float(_t1_hp) / float(max(1, _t1_mx))
                        _r2 = float(_t2_hp) / float(max(1, _t2_mx))
                        if abs(_r1 - _r2) < 0.02:
                            winner_team = 0
                        else:
                            winner_team = 1 if _r1 > _r2 else 2
                except Exception:
                    winner_team = 0

        try:
            player_team = self.playerAvatar.team
        except Exception:
            player_team = 1

        reason = 1
        if forced_winner is not None:
            reason = 2
        elif early_exit:
            try:
                if len(alive_count) == 2:
                    reason = 3
            except Exception:
                reason = 3
        print "[OFFLINE][BATTLE-END] winner=%s reason=%d (%s%s)" % (
            winner_team,
            reason,
            'capture' if reason == 2 else ('timed' if reason == 3 else 'destruction'),
            ', draw' if winner_team == 0 else '',
        )

        try: self.arena.onPeriodChange -= self._onPeriodChange
        except Exception: pass

        import cPickle
        now = BigWorld.time()
        afterbattle_data = (constants.ARENA_PERIOD_AFTERBATTLE, now + 30.0, 30.0, (winner_team, reason))
        try:
            self.arena.update(constants.ARENA_UPDATE_PERIOD, cPickle.dumps(afterbattle_data))
        except Exception: pass

        _av         = self.playerAvatar
        _frags      = getattr(_av, '_frags', 0)
        _dmg        = getattr(_av, '_total_damage', 0)
        _shots      = getattr(_av, '_shots_fired', 0)
        _received   = getattr(_av, '_shots_received', 0)
        _killed_ids = list(getattr(_av, '_player_killed_ids', set()))
        try:
            _damaged_ids = list(getattr(_av, '_damagedVehicleIDs', set()) - set(_killed_ids))
        except Exception:
            _damaged_ids = []
        try:
            _own_id = getattr(_av, 'playerVehicleID', None)
            if _own_id is not None:
                _damaged_ids = [x for x in _damaged_ids if x != _own_id]
        except Exception:
            pass
        _hits_total = len(_killed_ids) + len(_damaged_ids)

        try:
            _pvid = getattr(_av, 'playerVehicleID', None)
            _pveh = BigWorld.entity(_pvid) if _pvid is not None else None
            player_alive = _pveh is not None and getattr(_pveh, 'health', 0) > 0
        except Exception:
            player_alive = False

        def _calc_battle_rewards(frags, total_dmg, shots, hits_total, winner_team, player_team, player_descr, killed_ids, spotted_ids):
            try:
                try:
                    player_level = int(getattr(player_descr, 'level', 1) or 1)
                except Exception:
                    player_level = 1
                if player_level < 1: player_level = 1
                if player_level > 10: player_level = 10
                is_win = (winner_team == player_team)
                mult = 1.5 if is_win else 1.0
                try:
                    xp_dmg = int(float(total_dmg) * 0.12)
                except Exception:
                    xp_dmg = 0
                try:
                    cr_dmg = int(float(total_dmg) * 9.0)
                except Exception:
                    cr_dmg = 0
                xp_kill = 0
                cr_kill = 0
                try:
                    for _kid in (killed_ids or []):
                        try:
                            _kv = BigWorld.entity(_kid)
                            _klevel = getattr(getattr(_kv, 'typeDescriptor', None), 'level', player_level)
                            _klevel = int(_klevel or player_level)
                        except Exception:
                            _klevel = player_level
                        if _klevel < 1: _klevel = 1
                        if _klevel > 10: _klevel = 10
                        xp_kill += 40 + 10 * _klevel
                        cr_kill += 150 + 50 * _klevel
                except Exception:
                    xp_kill = int(frags) * (40 + 10 * player_level)
                    cr_kill = int(frags) * (150 + 50 * player_level)
                xp_spot = 0
                cr_spot = 0
                try:
                    for _sid in (spotted_ids or []):
                        try:
                            _sv = BigWorld.entity(_sid)
                            _slevel = getattr(getattr(_sv, 'typeDescriptor', None), 'level', player_level)
                            _slevel = int(_slevel or player_level)
                        except Exception:
                            _slevel = player_level
                        if _slevel < 1: _slevel = 1
                        if _slevel > 10: _slevel = 10
                        xp_spot += 20 + 5 * _slevel
                        cr_spot += 60 + 12 * _slevel
                except Exception:
                    xp_spot = len(spotted_ids or []) * (20 + 5 * player_level)
                    cr_spot = len(spotted_ids or []) * (60 + 12 * player_level)
                acc_bonus = 0
                try:
                    if shots and shots > 0 and hits_total > 0:
                        _acc = float(hits_total) / float(max(1, shots))
                        if _acc >= 0.6:
                            acc_bonus = int((xp_dmg + xp_kill + xp_spot) * 0.1)
                        elif _acc >= 0.4:
                            acc_bonus = int((xp_dmg + xp_kill + xp_spot) * 0.05)
                except Exception:
                    acc_bonus = 0
                total_xp = int((xp_dmg + xp_kill + xp_spot + acc_bonus) * mult)
                total_cr = int(cr_dmg + cr_kill + cr_spot)
                return total_xp, total_cr
            except Exception:
                return int(total_dmg * 0.12) + int(frags) * 40 + len(spotted_ids or []) * 30,                       int(total_dmg * 9.0) + int(frags) * 150 + len(spotted_ids or []) * 100

        _spot_ids = list(getattr(self, '_first_spotted_ids', set()))
        _calc_xp, _calc_credits = _calc_battle_rewards(_frags, _dmg, _shots, _hits_total, winner_team, player_team, self.playerAvatar.vehicleTypeDescriptor, _killed_ids, _spot_ids)

        try:
            _achieve_ids = self._compute_battle_achievements(reason, winner_team)
        except Exception:
            _achieve_ids = []
        _own_veh_id = getattr(_av, 'playerVehicleID', 0) or 0
        _hero_veh_ids = [_own_veh_id] * len(_achieve_ids)
        self._last_winner_team = winner_team
        self._last_reason = reason

        fake_results = {
            'xp':                   _calc_xp,
            'credits':              _calc_credits,
            'repair':               0,
            'xpFactor':             1,
            'killed':               _killed_ids,
            'damaged':              _damaged_ids,
            'spotted':              _spot_ids,
            'killerID':             0,
            'achieveIndices':       _achieve_ids,
            'heroVehicleIDs':       _hero_veh_ids,
            'shots':                _shots,
            'hits':                 _hits_total,
            'shotsReceived':        _received,
            'capturePoints':        int(getattr(self, '_avatar_capture_points', 0) or 0),
            'droppedCapturePoints': int(getattr(self, '_avatar_defense_points', 0) or 0),
            'isWinner':             1 if winner_team == player_team else (0 if winner_team == 0 else -1),
            'arenaTypeID':          int(getattr(self, '_current_arena_type_id', 0) or 0),
            'arenaCreateTime':      int(__import__('time').time()),
            'vehTypeCompDescr':     None,
        }
        try:
            _vtd = self.playerAvatar.vehicleTypeDescriptor
            fake_results['vehTypeCompDescr'] = vehicles.makeIntCompactDescrByID('vehicle', _vtd.type.id[0], _vtd.type.id[1])
        except Exception:
            pass
        try:
            import time as _time_sch
            if not fake_results['arenaCreateTime'] or fake_results['arenaCreateTime'] < 100000:
                fake_results['arenaCreateTime'] = int(_time_sch.time())
        except Exception:
            pass
        try:
            from Offline import Manager as _OM
            _OM._pending_battle_results = dict(fake_results)
        except Exception:
            pass

        _av._battle_credits    = fake_results['credits']
        _av._battle_xp         = fake_results['xp']
        _av._battle_winner     = (winner_team == player_team)
        _av._battle_is_draw    = (winner_team == 0)
        _av._battle_survived   = player_alive
        try:
            _op = self._oldPlayer
            if _op is not None:
                _op._last_battle_winner   = (winner_team == player_team)
                _op._last_battle_is_draw  = (winner_team == 0)
                _op._last_battle_survived = player_alive
                _op._last_battle_frags    = _frags
                _op._last_battle_dmg      = _dmg
                _op._last_battle_shots    = _shots
                _op._last_battle_hits     = _hits_total
                _op._last_battle_dmg_recv = _received * 200
                _op._last_battle_xp       = _calc_xp
                _op._last_battle_credits  = _calc_credits
                _op._last_battle_spots    = len(_spot_ids)
                _op._last_battle_achiev_ids = _achieve_ids
        except Exception: pass
        try:
            from CurrentVehicle import g_currentVehicle
            _av._battle_veh_inv_id = g_currentVehicle.vehicle.inventoryId if g_currentVehicle.vehicle else 1
        except Exception: _av._battle_veh_inv_id = 1

        try:
            from PlayerEvents import g_playerEvents
            _veh_inv = getattr(_av, '_battle_veh_inv_id', 0) or 0
            g_playerEvents.onBattleResultsReceived(True, _veh_inv, fake_results)
            LOG_NOTE('[BATTLE] battle results posted xp=%s credits=%s' % (_calc_xp, _calc_credits))
        except Exception:
            LOG_CURRENT_EXCEPTION()

        BigWorld.callback(30.0, self._finishBattle)

    def _compute_battle_achievements(self, reason, winner_team):

        out = []
        try:
            _av = self.playerAvatar
            _pl_frags  = len(getattr(_av, '_player_killed_ids', set()))
            _pl_dmg    = getattr(_av, '_total_damage', 0) or 0
            _pl_shotsF = getattr(_av, '_shots_fired', 0) or 0
            _pl_shotsH = getattr(_av, '_shots_hit', 0) or 0
            _pl_recv   = getattr(_av, '_shots_received', 0) or 0
            _pl_cap    = getattr(self, '_avatar_capture_points', 0) or 0
            _pl_def    = getattr(self, '_avatar_defense_points', 0) or 0
            _pl_dam    = getattr(_av, '_damagedVehicleIDs', set())
            _pl_kil    = getattr(_av, '_player_killed_ids', set())
            _pl_dead   = getattr(_av, '_deadVehicleIDs', set())
            _pl_det    = getattr(self, '_player_detected_ids', set())
            try:
                _pl_team = getattr(_av, 'team', 0) or 0
            except Exception:
                _pl_team = 0

            if _pl_frags >= 5:
                out.append(1)
            try:
                if reason == 2 and winner_team == _pl_team and _pl_cap > 0:
                    out.append(2)
            except Exception: pass
            try:
                _acc = (float(_pl_shotsH) / float(_pl_shotsF)) if _pl_shotsF > 0 else 0.0
                if _pl_shotsF >= 10 and _acc >= 0.5:
                    out.append(3)
            except Exception: pass
            if _pl_def >= 50:
                out.append(4)
            if _pl_dmg >= 1000 and _pl_recv >= 11:
                out.append(5)
            try:
                _assists = len((set(_pl_dam) & set(_pl_dead)) - set(_pl_kil))
                if _assists >= 8:
                    out.append(6)
            except Exception: pass
            if len(_pl_det) >= 9:
                out.append(7)

            out.sort()
        except Exception:
            out = []
        return out

    def _finishBattle(self, toHangar=True):
        if getattr(self, '_finish_called', False): return
        self._finish_called = True
        self._is_finished = True

        try:
            if getattr(self.playerAvatar, '_cruise_active', False):
                self.playerAvatar._cruise_active = False
                self.playerAvatar._cruise_target = 0.0
        except Exception: pass
        try: self._update_cruise_indicator()
        except Exception: pass

        try:
            if getattr(self, 'soundNotifications', None):
                self.soundNotifications.destroy()
        except Exception: pass

        try:
            self.playerAvatar.destroyNoPenHitmarker()
        except Exception: pass

        try:
            self.playerAvatar.destroyNoPenArc()
        except Exception: pass

        try:
            self.playerAvatar.destroyCrewNotice()
        except Exception: pass

        try:
            self.destroyCrewNotice()
        except Exception: pass

        try:
            self._killfeed_clear()
        except Exception: pass

        try:
            _av = self.playerAvatar
            _earned_credits = getattr(_av, '_battle_credits', 0) or 0
            _earned_xp      = getattr(_av, '_battle_xp', 0) or 0
            _inv_id         = getattr(_av, '_battle_veh_inv_id', 1) or 1
            if (_earned_credits <= 0 and _earned_xp <= 0) and not getattr(self, '_results_shown', False):
                try:
                    _fb_frags = getattr(_av, '_frags', 0) or 0
                    _fb_dmg = getattr(_av, '_total_damage', 0) or 0
                    _fb_shots = getattr(_av, '_shots_fired', 0) or 0
                    _fb_killed = list(getattr(_av, '_player_killed_ids', set()) or getattr(_av, '_deadVehicleIDs', set()))
                    try:
                        _fb_dam = list(getattr(_av, '_damagedVehicleIDs', set()) - set(_fb_killed))
                    except Exception:
                        _fb_dam = []
                    _fb_hits = len(_fb_killed) + len(_fb_dam)
                    _fb_recv = getattr(_av, '_shots_received', 0) or 0
                    try:
                        _fb_pvid = getattr(_av, 'playerVehicleID', None)
                        _fb_pveh = BigWorld.entity(_fb_pvid) if _fb_pvid is not None else None
                        _fb_alive = _fb_pveh is not None and getattr(_fb_pveh, 'health', 0) > 0
                    except Exception:
                        _fb_alive = False
                    try:
                        _fb_team = getattr(_av, 'team', 1) or 1
                        _fb_descr = getattr(_av, 'vehicleTypeDescriptor', None)
                        _fb_lvl = int(getattr(_fb_descr, 'level', 1) or 1)
                    except Exception:
                        _fb_team = 1
                        _fb_lvl = 1
                    _fb_spots = len(getattr(self, '_first_spotted_ids', set())) or 0
                    _earned_xp = int(float(_fb_dmg) * 0.12 + float(_fb_frags) * 40 + float(_fb_spots) * 30)
                    _earned_credits = int(float(_fb_dmg) * 9.0 + float(_fb_frags) * 150 + float(_fb_spots) * 100)
                    if _earned_xp < 0: _earned_xp = 0
                    if _earned_credits < 0: _earned_credits = 0
                    _av._battle_credits = _earned_credits
                    _av._battle_xp = _earned_xp
                    try:
                        _op0 = self._oldPlayer
                        if _op0 is not None:
                            if not hasattr(_op0, '_last_battle_frags'):
                                _op0._last_battle_winner = False
                                _op0._last_battle_is_draw = False
                                _op0._last_battle_survived = _fb_alive
                                _op0._last_battle_frags = _fb_frags
                                _op0._last_battle_dmg = _fb_dmg
                                _op0._last_battle_shots = _fb_shots
                                _op0._last_battle_hits = _fb_hits
                                _op0._last_battle_dmg_recv = _fb_recv * 200
                                _op0._last_battle_xp = _earned_xp
                                _op0._last_battle_credits = _earned_credits
                                _op0._last_battle_spots = _fb_spots
                                try:
                                    _fb_reason = getattr(self, '_last_reason', 1)
                                    _fb_winner = getattr(self, '_last_winner_team', _fb_team)
                                    _op0._last_battle_achiev_ids = self._compute_battle_achievements(_fb_reason, _fb_winner)
                                except Exception: pass
                    except Exception: pass
                    try:
                        from CurrentVehicle import g_currentVehicle as _gcv0
                        _av._battle_veh_inv_id = _gcv0.vehicle.inventoryId if _gcv0.vehicle else 1
                        _inv_id = _av._battle_veh_inv_id
                    except Exception:
                        _av._battle_veh_inv_id = 1
                        _inv_id = 1
                except Exception: pass
            if (_earned_credits > 0 or _earned_xp > 0) or getattr(self, '_results_shown', False):
                from Offline import Manager
                Manager.apply_battle_rewards(_earned_credits, _earned_xp, _inv_id, self._oldPlayer)
        except Exception: pass

        for _cb_attr in ('_cb_switch_to_battle', '_cb_prebattle_repeat', '_cb_capture_update'):
            try:
                _cbid = getattr(self, _cb_attr, None)
                if _cbid is not None:
                    BigWorld.cancelCallback(_cbid)
                    setattr(self, _cb_attr, None)
            except: pass

        if self.battleWindow:
            try:
                _tcb = getattr(self.battleWindow, '_offlineTimerCb', None)
                if _tcb is not None:
                    BigWorld.cancelCallback(_tcb)
                    self.battleWindow._offlineTimerCb = None
            except: pass
            try: self.battleWindow._offlineBattleFinished = True
            except: pass

        if self.playerAvatar:
            if getattr(self.playerAvatar, '_minimap', None):
                try: self.playerAvatar._minimap.destroy(); self.playerAvatar._minimap = None
                except: pass
            if getattr(self.playerAvatar, 'inputHandler', None):
                try: self.playerAvatar.inputHandler.stop()
                except: pass
            if getattr(self.playerAvatar, 'gunRotator', None):
                try: self.playerAvatar.gunRotator.stop(); self.playerAvatar.gunRotator.destroy()
                except: pass
            if getattr(self.playerAvatar, '_projectileMover', None):
                try: self.playerAvatar._projectileMover.destroy(); self.playerAvatar._projectileMover = None
                except: pass

        try:
            self._battle_music_playing = False
            self._intro_music_playing = False
        except: pass
        try:
            import MusicController as MC
            if MC.g_musicController:
                MC.g_musicController.onLeaveArena()
                MC.g_musicController.stop()
        except: pass
        try:
            from helpers import SoundGroups as SG
            if SG.g_instance:
                SG.g_instance.enableSounds('arena', False)
                SG.g_instance.unloadSounds('arena')
        except: pass
        try:
            if getattr(self, '_music_bank_loaded', False):
                import FMOD
                for _mg in _MUSIC_GROUPS:
                    try: FMOD.unloadSoundGroup(_mg)
                    except Exception: pass
        except: pass

        if self.battleWindow:
            try: self.battleWindow.close()
            except: pass
            self.battleWindow = None

        try: BigWorld.camera(None)
        except: pass

        try:
            from AvatarInputHandler import aims as _aims_fin
            if getattr(_aims_fin, '_offline_battle_ref', None) is self:
                _aims_fin._offline_battle_ref = None
        except Exception:
            pass
        try:
            _wall_mem_clear()
        except Exception:
            pass
        try:
            _scenery_reset()
        except Exception:
            pass
        try:
            from AreaDestructibles import g_destructiblesManager as _gdm
            _gdm.clear()
        except Exception:
            pass

        for vehID, _, _ in self.vehicles:
            try:
                _offline_stop_vehicle_visual(BigWorld.entity(vehID))
            except Exception:
                pass
        _sid = getattr(self, 'spaceID', None)
        if _sid is not None:
            try:
                for _ent in BigWorld.entities.values():
                    try:
                        if getattr(_ent, 'spaceID', None) != _sid:
                            continue
                        _offline_stop_vehicle_visual(_ent)
                        try:
                            BigWorld.destroyEntity(_ent.id)
                        except Exception:
                            pass
                    except Exception:
                        pass
            except Exception:
                pass
        for vehID, _, _ in self.vehicles:
            try:
                BigWorld.destroyEntity(vehID)
            except Exception:
                pass

        if self.spaceID is not None:
            try:
                BigWorld.clearSpace(self.spaceID)
            except Exception:
                pass
            try:
                BigWorld.releaseSpace(self.spaceID)
            except Exception:
                try:
                    BigWorld.clearSpace(self.spaceID)
                except Exception:
                    pass
            self.spaceID = None
        try:
            import gc as _gc_fin
            _gc_fin.collect()
        except Exception:
            pass

        try:
            from post_processing import g_postProcessing
            g_postProcessing.disable()
        except Exception:
            pass

        if hasattr(BigWorld, '_orig_player_fn'): BigWorld.player = BigWorld._orig_player_fn
        elif self._oldPlayer is not None: BigWorld.player = lambda: self._oldPlayer

        try:
            from gui.Cursor import forceShowCursor
            forceShowCursor(True)
        except: pass

        try: g_playerEvents.onAccountBecomePlayer()
        except: pass

        def _go_to_hangar():
            _patch_offline_media_fixes()
            try:
                from Offline import Manager
                Manager.restore_lobby()
            except Exception:
                try: g_windowsManager.showLobby()
                except: pass
                BigWorld.worldDrawEnabled(True)
            try:
                import MusicController as _MCH
                if getattr(_MCH, 'g_musicController', None):
                    try: _MCH.g_musicController.stop()
                    except: pass
                    try: _MCH.g_musicController.stopAmbient()
                    except: pass
            except: pass
            try:
                import FMOD
                try: FMOD.stopAll()
                except: pass
            except: pass

        if toHangar:
            BigWorld.callback(0.5, _go_to_hangar)

_FALLBACK_ARENA_ID = '05_prohorovka'

def _arena_defs_available(arenaTypeID):

    typeID, typeName = _arena_name_to_id(arenaTypeID)
    if typeName is None:
        return False
    try:
        import ResMgr
        sec = ResMgr.openSection('scripts/arena_defs/%s.xml' % typeName)
        return sec is not None
    except Exception:
        return False

def start_offline_battle(arena_id=None, botCount=14, trainingMode=False, trainingBots=None, roundLength=None, playerTeam=None):
    _patch_offline_media_fixes()
    try:
        _prev = _LAST_OFFLINE_BATTLE[0]
        if _prev is not None and not getattr(_prev, '_finish_called', False):
            try:
                LOG_NOTE("[OFFLINE] finishing leftover battle before next start")
                _prev._finishBattle(toHangar=False)
            except Exception:
                LOG_CURRENT_EXCEPTION()
        _LAST_OFFLINE_BATTLE[0] = None
    except Exception:
        pass
    try:
        from Offline import Manager as _OM
        _OM._lobby_restore_id[0] += 1
    except Exception:
        pass
    if arena_id is None or arena_id == -1:
        try:
            from Offline import Manager
            arena_id = Manager._selected_arena or _FALLBACK_ARENA_ID
        except Exception:
            arena_id = _FALLBACK_ARENA_ID
    if isinstance(arena_id, (tuple, list)):
        arena_id = arena_id[0]
    _typeID, _typeName = _arena_name_to_id(arena_id)
    if _typeID is None:
        _fbID, _fbName = _arena_name_to_id(_FALLBACK_ARENA_ID)
        if _fbID is not None:
            LOG_NOTE("[OFFLINE] arena %r не в ArenaType.g_list, запасная '%s'" % (arena_id, _fbName))
            arena_id = _fbName
        else:
            LOG_ERROR("[OFFLINE] no arena in ArenaType.g_list, cannot start battle (asked %r)" % (arena_id,))
            return
    else:
        arena_id = _typeName
    battle = OfflineBattle()
    try:
        _LAST_OFFLINE_BATTLE[0] = battle
    except Exception:
        pass
    if trainingMode:
        botCount = 0
    battle.start(arena_id, botCount=botCount, trainingMode=trainingMode,
                 trainingBots=trainingBots, roundLength=roundLength, playerTeam=playerTeam)
