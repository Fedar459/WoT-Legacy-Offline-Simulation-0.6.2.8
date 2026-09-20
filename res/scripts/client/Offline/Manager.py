import BigWorld
import new
import traceback
import AccountCommands
import account_helpers.AccountSettings as AS
import random
import os
import cPickle
from ConnectionManager import connectionManager
from PlayerEvents import g_playerEvents
from items import tankmen

_modules_inventory = {}
_selected_arena = None
_player_name = "Commander"
_modules_by_type   = {}
_vehicle_shells    = {}
_account_dossier   = None
_veh_type_dossiers = {}
_unlocks_sources_cache = None
_all_veh_cds_cache = None
_econ_cache = None
_lobby_chat_channels = {}
_lobby_chat_next_cid = [2]
_account_prefs_root = None
_favorites_persist_hooked = False
_TRAINING_ROOM = {
    'id': 0,
    'veh_inv_id': 1,
    'settings': {
        'arenaTypeID': '05_prohorovka',
        'roundLength': 900,
        'isPrivate': 0,
        'comment': '',
    },
    'roster': {},
    'keep': False,
}
_reopen_training_after_battle = False
_saved_prebattle = [None]
_keep_squad_after_battle = False
_session_sch_messages = []
_pending_battle_results = None
_consumed_eq_by_veh = {}

def _resolve_arena_type_id(arenaTypeID):

    try:
        import ArenaType as _AT
        glist = _AT.g_list or {}
        if arenaTypeID is None:
            return None
        try:
            if isinstance(arenaTypeID, float):
                arenaTypeID = int(arenaTypeID)
        except Exception:
            pass
        if isinstance(arenaTypeID, (int, long)) and arenaTypeID in glist:
            return int(arenaTypeID)
        name = str(arenaTypeID).strip()
        if name.startswith('spaces/'):
            name = name[7:]
        for typeID, typeName in glist.iteritems():
            if typeName == name or str(typeID) == name:
                return int(typeID)
        try:
            for typeID in glist.iterkeys():
                at = _AT.g_cache.get(typeID)
                if at is None:
                    continue
                if getattr(at, 'name', None) == name or getattr(at, 'typeName', None) == name:
                    return int(typeID)
        except Exception:
            pass
    except Exception:
        pass
    return None

def _set_module_count(cd, count):
    count = int(count or 0)
    if count <= 0:
        _modules_inventory.pop(cd, None)
        for tdict in _modules_by_type.values():
            if isinstance(tdict, dict):
                tdict.pop(cd, None)
        return
    _modules_inventory[cd] = count
    try:
        from items.vehicles import parseIntCompactDescr
        itemType, _n, _i = parseIntCompactDescr(cd)
        _modules_by_type.setdefault(itemType, {})[cd] = count
    except Exception:
        pass

def _refresh_player_inventory_items():
    try:
        p = BigWorld.player()
        inv = getattr(p, 'inventory', None)
        if inv is None:
            return
        cache = getattr(inv, '_Inventory__cache', None)
        if isinstance(cache, dict):
            cache['items'] = dict((cd, int(cnt)) for cd, cnt in _modules_inventory.iteritems() if int(cnt or 0) > 0)
    except Exception:
        pass

def _eq_item_tags(cd):
    try:
        from items import vehicles as _v
        return getattr(_v.getDictDescr(int(cd)), 'tags', None) or frozenset()
    except Exception:
        return frozenset()

def _current_veh_inv_id():
    try:
        from CurrentVehicle import g_currentVehicle as _gcv
        if _gcv.vehicle is not None:
            return int(_gcv.vehicle.inventoryId)
    except Exception:
        pass
    try:
        if _off_stats_ref is not None:
            return int(_off_stats_ref.get('currentVehInvID') or 1)
    except Exception:
        pass
    return 1

def _sync_vehicle_eq_slots(veh_inv_id, slots):
    veh_inv_id = int(veh_inv_id)
    slots = (list(slots) + [0, 0, 0])[:3]
    _vehicle_equipments[veh_inv_id] = slots
    try:
        if _inv_data_ref is not None:
            _inv_data_ref[6][veh_inv_id] = slots
            _inv_data_ref[5][veh_inv_id] = slots
    except Exception:
        pass
    _refresh_player_inventory_items()
    try:
        p = BigWorld.player()
        cache = getattr(p.inventory, '_Inventory__cache', None)
        if isinstance(cache, dict):
            from items import ITEM_TYPE_INDICES as _ITI
            vdict = cache.get(_ITI.get('vehicle'))
            if isinstance(vdict, dict):
                vdict.setdefault('eqs', {})[veh_inv_id] = slots
                vdict.setdefault('eqsLayout', {})[veh_inv_id] = slots
    except Exception:
        pass
    _notify_vehicle_inv(veh_inv_id, eqs=slots, eqsLayout=slots)
    try:
        from CurrentVehicle import g_currentVehicle as _gcv
        veh = _gcv.vehicle
        if veh is not None and int(getattr(veh, 'inventoryId', 0) or 0) == veh_inv_id:
            try:
                veh.equipments = list(slots)
            except Exception:
                pass
            try:
                veh._InventoryVehicle__equipments = list(slots)
            except Exception:
                pass
            try:
                veh._OfflineVehicleWrapper__equipments = list(slots)
            except Exception:
                pass
    except Exception:
        pass

def _try_mount_bought_equipment(compact_cd):
    try:
        compact_cd = int(compact_cd or 0)
    except Exception:
        return
    if not compact_cd:
        return
    tags = _eq_item_tags(compact_cd)
    if not (tags & frozenset(('repairkit', 'medkit', 'extinguisher', 'stimulator', 'fuel'))):
        return
    vid = _current_veh_inv_id()
    slots = list(_vehicle_equipments.get(vid, [0, 0, 0]) or [0, 0, 0])
    while len(slots) < 3:
        slots.append(0)
    have_on_tank = [int(s or 0) for s in slots]
    if compact_cd in have_on_tank:
        return
    for i in xrange(3):
        if int(slots[i] or 0):
            continue
        have = int(_modules_inventory.get(compact_cd, 0) or 0)
        if have <= 0:
            return
        _set_module_count(compact_cd, have - 1)
        slots[i] = compact_cd
        _sync_vehicle_eq_slots(vid, slots)
        print "[OFFLINE] mounted equipment cd=%s slot=%s veh=%s slots=%s" % (compact_cd, i, vid, slots)
        try:
            _save_profile(force=True)
        except Exception:
            pass
        return

def _ensure_battle_equipments(veh_inv_id):
    vid = int(veh_inv_id)
    slots = list(_vehicle_equipments.get(vid, [0, 0, 0]) or [0, 0, 0])
    while len(slots) < 3:
        slots.append(0)
    if any(int(s or 0) for s in slots):
        return slots
    wanted = ('repairkit', 'medkit', 'extinguisher')
    for i, tag in enumerate(wanted):
        for cd, cnt in list(_modules_inventory.items()):
            if int(cnt or 0) <= 0:
                continue
            if tag in _eq_item_tags(cd):
                _set_module_count(cd, int(cnt) - 1)
                slots[i] = int(cd)
                break
    _sync_vehicle_eq_slots(vid, slots)
    print "[OFFLINE] battle equipments veh=%s slots=%s" % (vid, slots)
    try:
        _save_profile(force=True)
    except Exception:
        pass
    return slots

def _patch_sync_controller_fini():
    try:
        import SyncController as _SC
        if getattr(_SC.SyncController, '_offline_none_safe', False):
            return
        _orig_resp = _SC.SyncController._SyncController__onSyncResponse
        _orig_stream = _SC.SyncController._SyncController__onSyncStreamComplete
        def _safe_resp(self, syncID, requestID, resultID, ext={}):
            owner = getattr(self, '_SyncController__onOwnerSyncResponse', None)
            if owner is None:
                try:
                    self._SyncController__syncRequests.pop(syncID, None)
                except Exception:
                    pass
                return
            return _orig_resp(self, syncID, requestID, resultID, ext)
        def _safe_stream(self, syncID, isSuccess, data):
            owner = getattr(self, '_SyncController__onOwnerSyncComplete', None)
            if owner is None:
                try:
                    self._SyncController__syncRequests.pop(syncID, None)
                except Exception:
                    pass
                return
            return _orig_stream(self, syncID, isSuccess, data)
        _SC.SyncController._SyncController__onSyncResponse = _safe_resp
        _SC.SyncController._SyncController__onSyncStreamComplete = _safe_stream
        _SC.SyncController._offline_none_safe = True
        print "[OFFLINE] SyncController None-callback guard installed"
    except Exception as e:
        print "[OFFLINE] SyncController patch failed: %s" % e

def _snapshot_service_channel():
    try:
        from messenger.gui import MessengerDispatcher as _MD
        sch = _MD.g_instance.serviceChannel
        _session_sch_messages[:] = list(sch._ServiceChannelManager__messages)
        _session_sch_messages.append(('__unread__', int(getattr(sch, '_ServiceChannelManager__unreadedMessageCount', 0) or 0)))
    except Exception:
        pass

def _restore_service_channel():
    try:
        from messenger.gui import MessengerDispatcher as _MD
        sch = _MD.g_instance.serviceChannel
        unread = 0
        items = []
        for it in _session_sch_messages:
            if isinstance(it, tuple) and len(it) == 2 and it[0] == '__unread__':
                unread = int(it[1] or 0)
                continue
            items.append(it)
        dq = sch._ServiceChannelManager__messages
        if items and len(dq) < len(items):
            dq.clear()
            for it in items:
                dq.append(it)
            sch._ServiceChannelManager__unreadedMessageCount = max(unread, 0)
    except Exception:
        pass

def _push_battle_results_notification(results):

    if not results:
        return
    try:
        import time as _time
        from chat_shared import SYS_MESSAGE_TYPE, SYS_MESSAGE_IMPORTANCE
        from messenger.wrappers import ServiceChannelMessage
        from messenger import SCH_SERVER_FORMATTERS_DICT
        from messenger.gui import MessengerDispatcher as _MD
        msg = ServiceChannelMessage(
            type=SYS_MESSAGE_TYPE.battleResults.index(),
            importance=SYS_MESSAGE_IMPORTANCE.normal.index(),
            active=True,
            sentTime=_time.time(),
            data=results)
        formatter = SCH_SERVER_FORMATTERS_DICT.get(msg.type)
        formatted = formatter.format(msg) if formatter is not None else None
        if not formatted:
            print "[OFFLINE] battleResults formatter returned empty"
            return
        sch = _MD.g_instance.serviceChannel
        sch._ServiceChannelManager__messages.append((formatted, True, False, False, []))
        sch._ServiceChannelManager__unreadedMessageCount = int(
            getattr(sch, '_ServiceChannelManager__unreadedMessageCount', 0) or 0) + 1
        sch.onReceiveServerMessage(formatted, False, False, [])
    except Exception as e:
        print "[OFFLINE] battleResults service message failed: %s" % e

def _consume_used_equipments(veh_inv_id):
    used = list(_consumed_eq_by_veh.pop(int(veh_inv_id), []) or [])
    if not used:
        return
    slots = list(_vehicle_equipments.get(int(veh_inv_id), [0, 0, 0]) or [0, 0, 0])
    while len(slots) < 3:
        slots.append(0)
    for cd in used:
        try:
            cd = int(cd)
        except Exception:
            continue
        for i, s in enumerate(slots):
            if int(s or 0) == cd:
                slots[i] = 0
                break
    _vehicle_equipments[int(veh_inv_id)] = slots
    try:
        if _inv_data_ref is not None:
            _inv_data_ref[6][int(veh_inv_id)] = slots
            _inv_data_ref[5][int(veh_inv_id)] = slots
    except Exception:
        pass
    _refresh_player_inventory_items()
    _notify_vehicle_inv(int(veh_inv_id), eqs=slots, eqsLayout=slots)

class _MemSection(object):


    def __init__(self):
        self._vals = {}
        self._subs = {}

    def has_key(self, name):
        return name in self._vals or name in self._subs

    def write(self, name, value):
        self._vals[name] = value
        if name not in self._subs:
            self._subs[name] = _MemSection()
        return self

    def readString(self, name, default=''):
        v = self._vals.get(name, default)
        if v is None:
            return default
        return str(v)

    def deleteSection(self, name):
        self._vals.pop(name, None)
        self._subs.pop(name, None)

    def __getitem__(self, name):
        if name not in self._subs:
            self._subs[name] = _MemSection()
        return self._subs[name]

class _ShellCount(object):
    def __init__(self, compactDescr, count):
        self.compactDescr = compactDescr
        self.count = int(count or 0)

_INV_VEHICLE_KEYS = (
    'compDescr', 'shellsLayout', 'shells', 'crew', 'repair',
    'eqsLayout', 'eqs', 'settings', 'lock',
)

def _as_vehicle_inv_dict(data):
    empty = dict((k, {}) for k in _INV_VEHICLE_KEYS)
    if data is None:
        return empty
    if isinstance(data, dict):
        if 'compDescr' in data:
            return data
        return empty
    if isinstance(data, (list, tuple)):
        out = {}
        for i, key in enumerate(_INV_VEHICLE_KEYS):
            out[key] = data[i] if i < len(data) else {}
        return out
    return empty

def _as_tankman_inv_dict(data):
    if isinstance(data, dict) and 'compDescr' in data:
        return data
    if isinstance(data, (list, tuple)) and len(data) >= 2:
        return {'compDescr': data[0] or {}, 'vehicle': data[1] or {}}
    return {'compDescr': {}, 'vehicle': {}}

def _shell_objs_from_ammo(ammo):
    if not ammo:
        return []
    first = ammo[0]
    if hasattr(first, 'count') and not isinstance(first, (int, long, float)):
        return list(ammo)
    try:
        from Inventory import AmmoIterator
        return [_ShellCount(cd, cnt) for cd, cnt in AmmoIterator(ammo)]
    except Exception:
        return []

def _flat_ammo_list(shells):

    if not shells:
        return []
    first = shells[0]
    out = []
    if hasattr(first, 'compactDescr') and hasattr(first, 'count') and not isinstance(first, (int, long, float)):
        for s in shells:
            out.append(s.compactDescr)
            out.append(int(s.count or 0))
        return out
    if isinstance(first, (list, tuple)) and len(first) >= 2:
        for pair in shells:
            out.append(pair[0])
            out.append(int(pair[1] or 0))
        return out
    lst = list(shells)
    if len(lst) % 2 == 1:
        lst = lst[:-1]
    out = []
    i = 0
    while i + 1 < len(lst):
        out.append(lst[i])
        try:
            out.append(int(float(lst[i + 1] or 0)))
        except Exception:
            out.append(0)
        i += 2
    return out

def _normalize_loaded_shells(shell_list, gunDescr=None):

    out = _flat_ammo_list(shell_list)
    if not out:
        return out
    cap = 0
    try:
        if gunDescr is not None:
            cap = int(gunDescr.get('maxAmmo', 0) or 0)
    except Exception:
        cap = 0
    if cap <= 0:
        return out
    counts = [int(c or 0) for c in out[1::2]]
    if not counts:
        return out
    mx = max(counts)
    if mx >= cap:
        seen = False
        for j in xrange(len(counts)):
            if (not seen) and counts[j] == mx:
                out[j * 2 + 1] = cap
                seen = True
            else:
                out[j * 2 + 1] = 0
    return out

def _notify_vehicle_inv(inv_id, **parts):

    try:
        inv1 = {}
        for key, val in parts.iteritems():
            inv1[key] = {inv_id: val}
        g_playerEvents.onClientUpdated({'inventory': {1: inv1}})
    except Exception as e:
        print "[OFFLINE] inventory onClientUpdated failed: %s" % e

def _load_economics():

    global _econ_cache
    if _econ_cache:
        return _econ_cache
    result = {
        'exchangeRate': 400,
        'initialSlots': 2,
        'initialBerths': 16,
        'berthsInPack': 16,
        'slotsPrices': (2, (300,)),
        'berthsPrices': (16, 16, (300,)),
        'freeXPConversion': (25, 1),
        'premiumCost': {1: 250, 3: 650, 7: 1200},
        'passportChangeCost': 50,
        'tankmanCost': (
            {'credits': 0, 'gold': 0, 'roleLevel': 50},
            {'credits': 20000, 'gold': 0, 'roleLevel': 75},
            {'credits': 0, 'gold': 200, 'roleLevel': 100},
        ),
    }
    try:
        import ResMgr
        section = ResMgr.openSection('scripts/economics.xml')
        if section is not None:
            result['exchangeRate'] = section.readInt('exchangeRate', result['exchangeRate'])
            result['initialSlots'] = section.readInt('initialSlots', result['initialSlots'])
            result['initialBerths'] = section.readInt('initialBerths', result['initialBerths'])
            result['berthsInPack'] = section.readInt('berthsInPack', result['berthsInPack'])
            sp = section['slotsPrices']
            if sp is not None:
                prices = [int(x) for x in sp.asString.split() if x]
                if prices:
                    result['slotsPrices'] = (result['initialSlots'], tuple(prices))
            bp = section['berthsPrices']
            if bp is not None:
                prices = [int(x) for x in bp.asString.split() if x]
                if prices:
                    result['berthsPrices'] = (
                        result['initialBerths'], result['berthsInPack'], tuple(prices))
            fxp = section['freeXPConversion']
            if fxp is not None:
                disc = fxp.readInt('discrecity', 25)
                cost = 0
                csec = fxp['cost']
                if csec is not None:
                    cost = csec.asInt
                result['freeXPConversion'] = (disc, cost)
            pc = section['premiumCost']
            if pc is not None:
                prem = {}
                for packetSec in pc.values():
                    days = packetSec.readInt('days', -1)
                    if days < 0:
                        continue
                    csec = packetSec['cost']
                    cost = csec.asInt if csec is not None else -1
                    if cost >= 0:
                        prem[days] = cost
                if prem:
                    result['premiumCost'] = prem
            try:
                pcc = section['passportChangeCost']
                if pcc is not None:
                    result['passportChangeCost'] = pcc.asInt
            except Exception:
                pass
            tc = section['tankmanCost']
            if tc is not None:
                rows = []
                for subSec in tc.values():
                    gold = 0
                    gsec = subSec['gold']
                    if gsec is not None:
                        gold = gsec.asInt
                    rows.append({
                        'roleLevel': subSec.readInt('roleLevel', 0),
                        'credits': subSec.readInt('credits', 0),
                        'gold': gold,
                        'baseXpLoss': subSec.readFloat('baseXpLoss', 0.0),
                        'classChangeXpLoss': subSec.readFloat('classChangeXpLoss', 0.0),
                        'isPremium': gold > 0,
                    })
                if rows:
                    rows.sort(key=lambda ct: ct['roleLevel'])
                    result['tankmanCost'] = tuple(rows)
            ResMgr.purge('scripts/economics.xml', True)
    except Exception:
        pass
    try:
        import Economics
        ei = getattr(Economics, 'g_instance', None)
        if ei is not None:
            for key in ('exchangeRate', 'initialSlots', 'initialBerths', 'berthsInPack',
                        'freeXPConversion', 'premiumCost', 'tankmanCost', 'passportChangeCost'):
                try:
                    result[key] = ei[key]
                except Exception:
                    pass
            try:
                sp = ei['slotsPrices']
                if isinstance(sp, (list, tuple)) and sp and not isinstance(sp[0], (list, tuple)):
                    result['slotsPrices'] = (result.get('initialSlots', 2), tuple(sp))
                elif isinstance(sp, (list, tuple)) and len(sp) >= 2:
                    result['slotsPrices'] = (sp[0], tuple(sp[1]))
            except Exception:
                pass
            try:
                bp = ei['berthsPrices']
                if isinstance(bp, (list, tuple)) and bp and not isinstance(bp[0], (list, tuple)):
                    result['berthsPrices'] = (
                        result.get('initialBerths', 16),
                        result.get('berthsInPack', 16),
                        tuple(bp))
            except Exception:
                pass
    except Exception:
        pass
    _econ_cache = result
    return result

def _reload_hangar_for_account_type():
    try:
        from gui.Scaleform.utils.HangarSpace import g_hangarSpace
        is_prem = False
        if _off_stats_ref is not None:
            is_prem = bool(_off_stats_ref.get('isPremium'))
        g_hangarSpace.refreshSpace(is_prem)
        print "[OFFLINE][PREMIUM] hangar space refresh isPremium=%s" % is_prem
    except Exception as e:
        print "[OFFLINE][PREMIUM] hangar reload failed: %s" % e
    try:
        import MusicController
        MusicController.g_musicController.play(MusicController.MUSIC_EVENT_LOBBY)
        MusicController.g_musicController.play(MusicController.AMBIENT_EVENT_LOBBY)
    except Exception:
        pass
    try:
        import MusicController
        MusicController.g_musicController.play(MusicController.MUSIC_EVENT_LOBBY)
        MusicController.g_musicController.play(MusicController.AMBIENT_EVENT_LOBBY)
    except Exception:
        pass

def _read_nickname_file():

    paths = []
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        paths.append(os.path.normpath(os.path.join(here, '..', '..', '..', '..', 'nickname.txt')))
        paths.append(os.path.normpath(os.path.join(here, '..', '..', '..', 'nickname.txt')))
    except Exception:
        pass
    try:
        paths.append(os.path.join(os.getcwd(), 'nickname.txt'))
    except Exception:
        pass
    seen = set()
    for path in paths:
        if not path or path in seen:
            continue
        seen.add(path)
        try:
            if not os.path.isfile(path):
                continue
            f = open(path, 'rb')
            try:
                raw = f.read()
            finally:
                f.close()
            line = raw.replace('\r', '\n').split('\n', 1)[0].strip()
            if line:
                return line
        except Exception:
            pass
    return None

def _canonical_unlocks(seq):
    out = set()
    if not seq:
        return out
    try:
        for cd in seq:
            try:
                out.add(int(cd))
            except Exception:
                pass
    except Exception:
        pass
    return out

def _vehicle_sell_credits(veh_comp_descr):

    if not veh_comp_descr:
        return 0
    try:
        from items.vehicles import parseVehicleCompactDescr
        from items import ITEM_TYPE_INDICES as _ITI_SELL
        from math import ceil
        nat_id, veh_id = parseVehicleCompactDescr(veh_comp_descr)
        prices = _build_shop_prices()
        buy = prices.get(nat_id, {}).get(_ITI_SELL['vehicle'], ({}, set()))[0].get(veh_id, (0, 0))
        if not isinstance(buy, tuple):
            buy = (int(buy), 0)
        sell_modif = 0.5
        return int(ceil(sell_modif * (int(buy[0]) + int(buy[1]) * 0)))
    except Exception as e:
        print "[OFFLINE][SELL] price lookup failed: %s" % e
        return 0

def _apply_account_premium(p, off_stats):
    import constants as _c
    import time as _t
    expiry = int(off_stats.get('premiumExpiryTime') or 0)
    is_prem = bool(off_stats.get('isPremium')) and expiry > int(_t.time())
    off_stats['isPremium'] = is_prem
    if not is_prem:
        off_stats['premiumExpiryTime'] = 0
        expiry = 0
    try:
        p.isPremium = is_prem
        p.premiumExpiryTime = expiry
        p.accountType = _c.ACCOUNT_TYPE.PREMIUM if is_prem else _c.ACCOUNT_TYPE.BASE
        off_stats['accountType'] = p.accountType
    except Exception:
        pass
    try:
        p.stats._Stats__cache['isPremium'] = is_prem
        p.stats._Stats__cache['premiumExpiryTime'] = expiry
    except Exception:
        pass

def _add_premium_vehicle_unlocks(unlocks):

    from items import vehicles as _v
    import nations as _n
    u = _canonical_unlocks(unlocks)
    for ni in xrange(len(_n.NAMES)):
        try:
            vmap = _v.g_list.getList(ni)
        except Exception:
            continue
        for vt in vmap.itervalues():
            tags = vt.get('tags') or ()
            if 'premium' not in tags:
                continue
            cd = vt.get('compactDescr')
            if not cd:
                continue
            try:
                cd = int(cd)
            except Exception:
                continue
            u.add(cd)
            try:
                _itype, nat, inn = _v.parseIntCompactDescr(cd)
                vtype = _v.g_cache.vehicle(nat, inn)
                for acd in getattr(vtype, 'autounlockedItems', ()):
                    u.add(int(acd))
            except Exception:
                pass
    return u

def _apply_player_name(player=None):

    global _player_name
    nick = _read_nickname_file()
    if not nick:
        nick = _player_name
    if not nick:
        nick = 'Commander'
    _player_name = nick
    p = player
    if p is None:
        try:
            p = BigWorld.player()
        except Exception:
            p = None
    if p is not None:
        try:
            p.name = nick
        except Exception:
            pass
    try:
        from gui.WindowsManager import g_windowsManager
        w = getattr(g_windowsManager, 'window', None)
        if w is not None:
            w.call('common.nameResponse', [nick])
    except Exception:
        pass
    return nick

def _resolve_save_dir():
    try:
        d = os.path.dirname(os.path.abspath(__file__))
    except Exception:
        d = None

    if d:
        parts = d.split(os.sep)
        if 'res' not in parts and 'scripts' in parts:
            idx = parts.index('scripts')
            with_res = os.sep.join(parts[:idx] + ['res'] + parts[idx:])
            if os.path.isdir(with_res):
                return with_res
            try:
                os.makedirs(with_res)
                print "[OFFLINE][SAVE] created missing directory: %s" % with_res
                return with_res
            except Exception as e:
                print "[OFFLINE][SAVE] could not create %s (%s)" % (with_res, e)

        if os.path.isdir(d):
            return d
        try:
            os.makedirs(d)
            print "[OFFLINE][SAVE] created missing directory: %s" % d
            return d
        except Exception as e:
            print "[OFFLINE][SAVE] could not create %s (%s), falling back to cwd" % (d, e)
    try:
        return os.getcwd()
    except Exception:
        return '.'

_SAVE_PATH = os.path.join(_resolve_save_dir(), 'offline_save.dat')
print "[OFFLINE][SAVE] using save path: %s" % _SAVE_PATH
_off_stats_ref = None
_inv_vehicles_ref = None
_inv_data_ref = None
_crew_map_ref = None
_t_cache_ref = None
_t_in_veh_ref = None
_my_garage_ref = None
_veh_cds_ref = None
_sold_vehicles = set()

def _usa_t1_cunningham_name():
    from items import vehicles as _v
    import nations as _n
    for n in ('usa:T1_Cunningham', 'usa:T1Cunningham', 'usa:T1_Cunnigham'):
        try:
            _v.g_list.getIDsByName(n)
            return n
        except Exception:
            continue
    try:
        usa = _n.INDICES.get('usa', 2)
        for vt in _v.g_list.getList(usa).itervalues():
            nm = vt.get('name') or ''
            low = nm.lower()
            if 't1' in low and 'cunn' in low:
                return nm
    except Exception:
        pass
    return None

def _unlock_vehicle_type(type_name):
    from items import vehicles as _v, ITEM_TYPE_INDICES as _ITI
    if _off_stats_ref is None or not type_name:
        return
    unlocks = _add_premium_vehicle_unlocks(_off_stats_ref.get('unlocks'))
    _off_stats_ref['unlocks'] = unlocks
    nID, vID = _v.g_list.getIDsByName(type_name)
    typeCD = _v.makeIntCompactDescrByID('vehicle', nID, vID)
    unlocks.add(int(typeCD))
    descr = _v.VehicleDescr(typeName=type_name)
    for idx, modCD in (
        (_ITI['vehicleChassis'], descr.chassis['compactDescr']),
        (_ITI['vehicleTurret'], descr.turret['compactDescr']),
        (_ITI['vehicleGun'], descr.gun['compactDescr']),
        (_ITI['vehicleEngine'], descr.engine['compactDescr']),
        (_ITI['vehicleRadio'], descr.radio['compactDescr']),
    ):
        if not modCD:
            continue
        unlocks.add(int(modCD))
        _modules_inventory[modCD] = _modules_inventory.get(modCD, 0) + 1
        _modules_by_type.setdefault(idx, {})[modCD] = _modules_inventory[modCD]

def _grant_vehicle_if_missing(type_name):

    from items import vehicles as _v
    if not type_name:
        return False
    if _my_garage_ref is None or _veh_cds_ref is None:
        return False
    for n in _my_garage_ref.values():
        if n == type_name:
            try:
                _unlock_vehicle_type(type_name)
            except Exception:
                pass
            return False
    try:
        descr = _v.VehicleDescr(typeName=type_name)
        new_cd = descr.makeCompactDescr()
    except Exception as e:
        print "[OFFLINE] cannot grant %s: %s" % (type_name, e)
        return False
    new_inv = (max(_my_garage_ref.keys()) + 1) if _my_garage_ref else 1
    _veh_cds_ref[new_inv] = new_cd
    if _inv_vehicles_ref is not None:
        _inv_vehicles_ref[new_inv] = new_cd
    if _inv_data_ref is not None:
        while len(_inv_data_ref) < 9:
            _inv_data_ref.append({})
        _inv_data_ref[0][new_inv] = new_cd
        _inv_data_ref[1][new_inv] = {}
        ammo = []
        try:
            ammo = _v.getDefaultAmmoForGun(descr.gun)
        except Exception:
            ammo = []
        _inv_data_ref[2][new_inv] = ammo
        _vehicle_shells[new_inv] = ammo
        _inv_data_ref[4][new_inv] = (0, 100)
        _inv_data_ref[5][new_inv] = [0, 0, 0]
        _inv_data_ref[6][new_inv] = [0, 0, 0]
        _inv_data_ref[7][new_inv] = 0
        _inv_data_ref[8][new_inv] = 0
    crew_slots = []
    try:
        nID = descr.type.id[0]
        vtID = descr.type.id[1]
        t_cache = _t_cache_ref if _t_cache_ref is not None else {}
        t_in_veh = _t_in_veh_ref if _t_in_veh_ref is not None else {}
        tid = (max(t_cache.keys()) + 1) if t_cache else 10
        for role in descr.type.crewRoles:
            role_name = role[0] if isinstance(role, (list, tuple)) else role
            passport = tankmen.generatePassport(nID)
            tman_cd = tankmen.generateCompactDescr(passport, vtID, role_name, 100)
            t_cache[tid] = tman_cd
            t_in_veh[tid] = new_inv
            crew_slots.append(tid)
            tid += 1
    except Exception as e:
        print "[OFFLINE] grant crew failed %s: %s" % (type_name, e)
        crew_slots = []
    if _crew_map_ref is not None:
        _crew_map_ref[new_inv] = crew_slots
    if _inv_data_ref is not None and len(_inv_data_ref) > 3:
        _inv_data_ref[3][new_inv] = crew_slots
    _my_garage_ref[new_inv] = type_name
    try:
        _unlock_vehicle_type(type_name)
    except Exception as e:
        print "[OFFLINE] grant unlocks failed %s: %s" % (type_name, e)
    if _off_stats_ref is not None:
        owned = len(_my_garage_ref)
        if int(_off_stats_ref.get('slots') or 0) < owned:
            _off_stats_ref['slots'] = owned
    print "[OFFLINE] granted %s as inv=%d" % (type_name, new_inv)
    return True

_DOSSIER_FIELDS = ['battlesCount', 'xp', 'maxXP', 'frags', 'shots', 'hits',
                    'damageDealt', 'damageReceived', 'lastBattleTime', 'wins',
                    'winAndSurvived', 'losses', 'survivedBattles', 'creationTime',
                    'spotted', 'capturePoints', 'droppedCapturePoints',
                    'warrior', 'invader', 'sniper', 'defender', 'steelwall',
                    'supporter', 'scout', 'battleHeroes',
                    'medalKay', 'medalCarius', 'medalKnispel', 'medalPoppel',
                    'medalAbrams', 'medalLeClerc', 'medalLavrinenko', 'medalEkins']

def _build_all_vehicle_cds():

    global _all_veh_cds_cache
    if _all_veh_cds_cache is not None:
        return _all_veh_cds_cache
    pool = []
    try:
        from items import vehicles as _veh
        import nations as _nations
        for _ni in range(len(_nations.NAMES)):
            try:
                _vmap = _veh.g_list.getList(_ni)
            except Exception:
                continue
            for _vt in _vmap.itervalues():
                _nm = _vt.get('name')
                if not _nm:
                    continue
                try:
                    pool.append(_veh.VehicleDescr(typeName=_nm).makeCompactDescr())
                except Exception:
                    try:
                        pool.append(_veh.VehicleDescr(compactDescr=_vt.get('compactDescr')).makeCompactDescr())
                    except Exception:
                        continue
    except Exception:
        pass
    _all_veh_cds_cache = pool
    print "[OFFLINE] vehicle pool ready: %d tanks" % len(pool)
    return pool

def _dget_safe(d, key):
    try:
        return int(d[key])
    except Exception:
        return 0

def _hangar_xp_value():

    if _account_dossier is not None:
        try:
            return int(_account_dossier['xp'] or 0)
        except Exception:
            pass
    if _off_stats_ref is not None:
        try:
            return int(_off_stats_ref.get('freeXP') or 0)
        except Exception:
            pass
    return 0

def _refresh_hangar_xp():
    try:
        from gui.WindowsManager import g_windowsManager
        w = getattr(g_windowsManager, 'window', None)
        if w is not None and hasattr(w, 'call'):
            w.call('common.experienceResponse', [_hangar_xp_value()])
    except Exception:
        pass

def _emit_server_stats():

    try:
        import random as _rnd
        stats = {
            'playersCount': int(_rnd.randint(1400, 4800)),
            'arenasCount': int(_rnd.randint(60, 240)),
            'playersInArenaCount': int(_rnd.randint(320, 1800)),
        }
        g_playerEvents.onServerStatsReceived(stats)
    except Exception:
        pass

def _dossier_to_dict(d):
    if d is None:
        return None
    return dict((f, _dget_safe(d, f)) for f in _DOSSIER_FIELDS)

def _apply_dict_to_dossier(d, saved_dict):
    if d is None or not saved_dict:
        return
    for f in _DOSSIER_FIELDS:
        if f in saved_dict:
            try:
                d[f] = int(saved_dict[f])
            except Exception:
                pass

_last_save_time = [0.0]

def _save_profile(force=False):
    try:
        _now = BigWorld.time()
    except Exception:
        _now = 0.0
    if not force and (_now - _last_save_time[0]) < 3.0:
        return
    _last_save_time[0] = _now
    try:
        data = {
            'player_name': _player_name,
            'credits': _off_stats_ref.get('credits') if _off_stats_ref else None,
            'gold':    _off_stats_ref.get('gold')    if _off_stats_ref else None,
            'freeXP':  _off_stats_ref.get('freeXP')  if _off_stats_ref else None,
            'slots':   _off_stats_ref.get('slots')   if _off_stats_ref else None,
            'berths':  _off_stats_ref.get('berths')  if _off_stats_ref else None,
            'vehTypeXP': dict((str(k), v) for k, v in _off_stats_ref.get('vehTypeXP', {}).items()) if _off_stats_ref else None,
            'account_dossier': _dossier_to_dict(_account_dossier),
            'veh_type_dossiers': dict((str(k), _dossier_to_dict(v)) for k, v in _veh_type_dossiers.items()),
            'account_dossier_cd': '',
            'veh_type_dossiers_cd': {},
            'modules_inventory':  dict((str(k), v) for k, v in _modules_inventory.items()),
            'vehicle_equipments': dict((str(k), v) for k, v in _vehicle_equipments.items()),
            'vehicle_shells':     dict((str(k), v) for k, v in _vehicle_shells.items()),
            'inv_vehicles': dict((str(k), v) for k, v in _inv_vehicles_ref.items()) if _inv_vehicles_ref else None,
            'veh_cds':      dict((str(k), v) for k, v in _veh_cds_ref.items()) if _veh_cds_ref else None,
            'inv_data':     [dict((str(k), v) for k, v in d.items()) for d in _inv_data_ref] if _inv_data_ref else None,
            'crew_map':     dict((str(k), v) for k, v in _crew_map_ref.items()) if _crew_map_ref else None,
            't_cache':      dict((str(k), v) for k, v in _t_cache_ref.items()) if _t_cache_ref else None,
            't_in_veh':     dict((str(k), v) for k, v in _t_in_veh_ref.items()) if _t_in_veh_ref else None,
            'my_garage':    dict((str(k), v) for k, v in _my_garage_ref.items()) if _my_garage_ref else None,
            'sold_vehicles': list(_sold_vehicles),
            'unlocks':       list(_canonical_unlocks(_off_stats_ref.get('unlocks', []))) if _off_stats_ref else None,
            'eliteVehicles': list(_canonical_unlocks(_off_stats_ref.get('eliteVehicles', []))) if _off_stats_ref else None,
            'isPremium':        bool(_off_stats_ref.get('isPremium')) if _off_stats_ref else False,
            'premiumExpiryTime': int(_off_stats_ref.get('premiumExpiryTime') or 0) if _off_stats_ref else 0,
            'lobby_channels': dict((str(k), v) for k, v in _lobby_chat_channels.items()),
            'lobby_next_cid': int(_lobby_chat_next_cid[0]),
            'currentVehInvID': int(_off_stats_ref.get('currentVehInvID') or 0) if _off_stats_ref else 0,
            'favoriteVehicles': list(_off_stats_ref.get('favoriteVehicles') or []) if _off_stats_ref else [],
        }
        try:
            if _account_dossier is not None:
                data['account_dossier_cd'] = _account_dossier.makeCompDescr()
        except Exception:
            data['account_dossier_cd'] = ''
        try:
            data['veh_type_dossiers_cd'] = dict(
                (str(k), v.makeCompDescr()) for k, v in _veh_type_dossiers.items())
        except Exception:
            data['veh_type_dossiers_cd'] = {}
        try:
            with open(_SAVE_PATH, 'wb') as f:
                cPickle.dump(data, f)
            print "[OFFLINE][SAVE] Profile saved to %s" % _SAVE_PATH
        except Exception as e1:
            _fallback = os.path.join(os.getcwd(), 'offline_save.dat')
            print "[OFFLINE][SAVE] primary path failed (%s), retrying at %s" % (e1, _fallback)
            with open(_fallback, 'wb') as f:
                cPickle.dump(data, f)
            print "[OFFLINE][SAVE] Profile saved to fallback %s" % _fallback
    except Exception as e:
        print "[OFFLINE][SAVE] save failed completely: %s" % e

def _load_profile():
    try:
        _path = _SAVE_PATH
        if not os.path.exists(_path):
            _fallback = os.path.join(os.getcwd(), 'offline_save.dat')
            if os.path.exists(_fallback):
                _path = _fallback
            else:
                print "[OFFLINE][SAVE] no save file yet at %s or %s" % (_SAVE_PATH, _fallback)
                return None
        with open(_path, 'rb') as f:
            data = cPickle.load(f)
        print "[OFFLINE][SAVE] Profile loaded from %s" % _path
        data = _coerce_str(data)
        return data
    except Exception as e:
        print "[OFFLINE][SAVE] load failed: %s" % e
        return None

def _coerce_str(obj):
    try:
        if isinstance(obj, unicode):
            try:
                return obj.encode('latin-1')
            except (UnicodeEncodeError, UnicodeDecodeError, AttributeError):
                return obj
        if isinstance(obj, dict):
            return dict((_coerce_str(k), _coerce_str(v)) for k, v in obj.items())
        if isinstance(obj, list):
            return [_coerce_str(x) for x in obj]
        if isinstance(obj, tuple):
            return tuple(_coerce_str(x) for x in obj)
        if isinstance(obj, set):
            return set(_coerce_str(x) for x in obj)
    except Exception:
        pass
    return obj

def _restore_int_keyed(target_dict, saved_dict):
    for k, v in (saved_dict or {}).items():
        try:
            ik = int(k)
        except (TypeError, ValueError):
            ik = k
        target_dict[ik] = v

def _apply_loaded_profile(off_stats):
    global _player_name
    try:
        _saved = _load_profile()
        if not _saved:
            return
        if _saved.get('player_name'):
            _player_name = _saved['player_name']
        if _saved.get('credits') is not None:
            off_stats['credits'] = int(_saved['credits'])
        if _saved.get('gold') is not None:
            off_stats['gold'] = int(_saved['gold'])
        if _saved.get('freeXP') is not None:
            off_stats['freeXP'] = int(_saved['freeXP'])
        if _saved.get('slots') is not None:
            off_stats['slots'] = int(_saved['slots'])
        if _saved.get('berths') is not None:
            off_stats['berths'] = int(_saved['berths'])
        if _saved.get('currentVehInvID') is not None:
            try:
                off_stats['currentVehInvID'] = int(_saved['currentVehInvID'])
            except Exception:
                pass
        if _saved.get('favoriteVehicles') is not None:
            try:
                off_stats['favoriteVehicles'] = [int(x) for x in _saved['favoriteVehicles']]
            except Exception:
                off_stats['favoriteVehicles'] = list(_saved['favoriteVehicles'] or [])

        if _saved.get('vehTypeXP'):
            off_stats.setdefault('vehTypeXP', {})
            for k, v in _saved['vehTypeXP'].items():
                try:
                    off_stats['vehTypeXP'][int(k)] = int(v)
                except (TypeError, ValueError):
                    off_stats['vehTypeXP'][k] = v

        if _saved.get('account_dossier_cd') or _saved.get('account_dossier'):
            global _account_dossier
            try:
                import dossiers as _dossiers_mod
                _cd = _saved.get('account_dossier_cd') or ''
                if _cd:
                    _account_dossier = _dossiers_mod.getAccountDossierDescr(_cd)
                else:
                    if _account_dossier is None:
                        import time as _t_doss
                        _account_dossier = _dossiers_mod.getAccountDossierDescr('')
                        _account_dossier['creationTime'] = int(_t_doss.time())
                    _apply_dict_to_dossier(_account_dossier, _saved.get('account_dossier') or {})
            except Exception as _de:
                print "[OFFLINE][SAVE] account dossier restore failed: %s" % _de

        if _saved.get('veh_type_dossiers_cd') or _saved.get('veh_type_dossiers'):
            try:
                import dossiers as _dossiers_mod
                _cds = _saved.get('veh_type_dossiers_cd') or {}
                if _cds:
                    for k, cd in _cds.items():
                        try:
                            ik = int(k)
                        except (TypeError, ValueError):
                            ik = k
                        try:
                            _veh_type_dossiers[ik] = _dossiers_mod.getVehicleDossierDescr(cd)
                        except Exception:
                            pass
                else:
                    for k, v in (_saved.get('veh_type_dossiers') or {}).items():
                        try:
                            ik = int(k)
                        except (TypeError, ValueError):
                            ik = k
                        if ik not in _veh_type_dossiers:
                            _veh_type_dossiers[ik] = _dossiers_mod.getVehicleDossierDescr('')
                        _apply_dict_to_dossier(_veh_type_dossiers[ik], v)
            except Exception as _ve:
                print "[OFFLINE][SAVE] vehicle dossiers restore failed: %s" % _ve

        _restore_int_keyed(_modules_inventory,  _saved.get('modules_inventory'))
        _restore_int_keyed(_vehicle_equipments, _saved.get('vehicle_equipments'))
        _restore_int_keyed(_vehicle_shells,     _saved.get('vehicle_shells'))
        try:
            if _inv_data_ref is not None:
                for _vid, _slots in _vehicle_equipments.items():
                    sl = (list(_slots or []) + [0, 0, 0])[:3]
                    _inv_data_ref[6][int(_vid)] = sl
                    _inv_data_ref[5][int(_vid)] = sl
        except Exception:
            pass

        if _saved.get('unlocks') is not None:
            off_stats['unlocks'] = _add_premium_vehicle_unlocks(_saved['unlocks'])
            print "[OFFLINE][SAVE] restored %d unlocks" % len(off_stats['unlocks'])
        else:
            off_stats['unlocks'] = _add_premium_vehicle_unlocks(off_stats.get('unlocks'))

        if _saved.get('eliteVehicles') is not None:
            off_stats['eliteVehicles'] = _canonical_unlocks(_saved['eliteVehicles'])
            print "[OFFLINE][SAVE] restored %d elite vehicles" % len(off_stats['eliteVehicles'])
        else:
            off_stats['eliteVehicles'] = _canonical_unlocks(off_stats.get('eliteVehicles'))

        import time as _tprem
        if _saved.get('premiumExpiryTime') is not None:
            off_stats['premiumExpiryTime'] = int(_saved.get('premiumExpiryTime') or 0)
            off_stats['isPremium'] = bool(_saved.get('isPremium')) and off_stats['premiumExpiryTime'] > int(_tprem.time())
        else:
            off_stats['isPremium'] = False
            off_stats['premiumExpiryTime'] = 0

        if _saved.get('lobby_channels'):
            global _lobby_chat_channels, _lobby_chat_next_cid
            _lobby_chat_channels.clear()
            for k, v in _saved['lobby_channels'].items():
                try:
                    _lobby_chat_channels[int(k)] = v
                except Exception:
                    pass
            if _saved.get('lobby_next_cid'):
                _lobby_chat_next_cid[0] = int(_saved['lobby_next_cid'])

        print "[OFFLINE][SAVE] Profile applied OK (credits=%s gold=%s freeXP=%s slots=%s premium=%s)" % (
            off_stats.get('credits'), off_stats.get('gold'), off_stats.get('freeXP'),
            off_stats.get('slots'), off_stats.get('isPremium'))
    except Exception as e:
        print "[OFFLINE][SAVE] apply failed: %s" % e

def _apply_loaded_vehicles_and_crew():
    try:
        _saved = _load_profile()
        if not _saved:
            return
        if _saved.get('inv_vehicles') and _inv_vehicles_ref is not None:
            _restore_int_keyed(_inv_vehicles_ref, _saved['inv_vehicles'])
        if _saved.get('veh_cds') and _veh_cds_ref is not None:
            _restore_int_keyed(_veh_cds_ref, _saved['veh_cds'])
        if _saved.get('inv_data') and _inv_data_ref is not None:
            for _slot_idx, _saved_dict in enumerate(_saved['inv_data']):
                if _slot_idx < len(_inv_data_ref):
                    _restore_int_keyed(_inv_data_ref[_slot_idx], _saved_dict)
        if _saved.get('crew_map') and _crew_map_ref is not None:
            _restore_int_keyed(_crew_map_ref, _saved['crew_map'])
        if _saved.get('t_cache') and _t_cache_ref is not None:
            _restore_int_keyed(_t_cache_ref, _saved['t_cache'])
        if _saved.get('t_in_veh') and _t_in_veh_ref is not None:
            _restore_int_keyed(_t_in_veh_ref, _saved['t_in_veh'])
        if _saved.get('my_garage') and _my_garage_ref is not None:
            _restore_int_keyed(_my_garage_ref, _saved['my_garage'])

        try:
            from items import vehicles as _v2
            for _vid in list(_my_garage_ref.keys()):
                _cd = _veh_cds_ref.get(_vid)
                if _cd is None:
                    continue
                _roles = len(_v2.VehicleDescr(compactDescr=_cd).type.crewRoles)
                _crew  = _crew_map_ref.get(_vid)
                if _crew is None or len(_crew) < _roles:
                    _crew = list(_crew) if _crew else []
                    while len(_crew) < _roles:
                        _crew.append(None)
                    _crew_map_ref[_vid]              = _crew
                    _inv_data_ref[3][_vid]           = _crew
        except Exception as _e2:
            print "[OFFLINE][SAVE] crew slots normalize failed: %s" % _e2

        global _sold_vehicles
        _sold = _saved.get('sold_vehicles')
        if _sold:
            _sold_vehicles = set(int(x) for x in _sold)
            for _sid in _sold_vehicles:
                for _d in (_inv_vehicles_ref, _veh_cds_ref, _my_garage_ref, _crew_map_ref):
                    if _d is not None:
                        _d.pop(_sid, None)
                if _inv_data_ref is not None:
                    for _slot in _inv_data_ref:
                        _slot.pop(_sid, None)
                if _t_in_veh_ref is not None:
                    for _tid in list(_t_in_veh_ref.keys()):
                        if _t_in_veh_ref.get(_tid) == _sid:
                            _t_in_veh_ref.pop(_tid, None)
                            if _t_cache_ref is not None:
                                _t_cache_ref.pop(_tid, None)
            print "[OFFLINE][SAVE] Applied %d sold-vehicle deletions" % len(_sold_vehicles)

        try:
            if _inv_data_ref is not None and len(_inv_data_ref) > 3 and _my_garage_ref is not None:
                _crew_slot = _inv_data_ref[3]
                for _vid in list(_my_garage_ref.keys()):
                    if _vid not in _crew_slot:
                        _crew = list(_crew_map_ref.get(_vid) or []) if _crew_map_ref else []
                        _crew_slot[_vid] = _crew
                    if _crew_map_ref is not None:
                        _crew_map_ref[_vid] = _crew_slot[_vid]
                if _t_in_veh_ref is not None:
                    for _tid in list(_t_in_veh_ref.keys()):
                        _vid = _t_in_veh_ref.get(_tid)
                        if _vid not in _crew_slot:
                            _t_in_veh_ref.pop(_tid, None)
        except Exception as _e3:
            print "[OFFLINE][SAVE] crew/t_in_veh consistency failed: %s" % _e3

        if _inv_data_ref is not None and len(_inv_data_ref) > 2:
            for _vid, _ammo in _inv_data_ref[2].items():
                if _vid not in _vehicle_shells and _ammo:
                    _vehicle_shells[_vid] = _ammo

        print "[OFFLINE][SAVE] Vehicles/crew restored OK"
    except Exception as e:
        print "[OFFLINE][SAVE] vehicles/crew restore failed: %s" % e

def _read_tankman_cost():
    return _load_economics()['tankmanCost']

def _crew_role_level(crew_type):

    table = _read_tankman_cost() or ()
    idx = 0
    try:
        if crew_type is not None:
            idx = int(crew_type)
    except Exception:
        idx = 0
    if idx < 0:
        idx = 0
    if table and idx >= len(table):
        idx = len(table) - 1
    level = 50
    if table:
        try:
            level = int(table[idx].get('roleLevel', 50))
        except Exception:
            level = 50
    if level < tankmen.MIN_ROLE_LEVEL:
        level = tankmen.MIN_ROLE_LEVEL
    if level > tankmen.MAX_SKILL_LEVEL:
        level = tankmen.MAX_SKILL_LEVEL
    return level

def _build_shop_prices():
    global _shop_prices_cache
    if _shop_prices_cache:
        return _shop_prices_cache
    try:
        import ResMgr, nations
        from items import vehicles as _veh, ITEM_TYPE_INDICES as _ITI, _xml, SIMPLE_ITEM_TYPE_INDICES
        AVAILABLE_NAMES = nations.NAMES
        INDICES = dict((name, idx) for idx, name in enumerate(nations.NAMES))
        result = {}
        _nations = nations
        none_idx = _nations.NONE_INDEX
        result[none_idx] = {
            _ITI['optionalDevice']: ({}, set()),
            _ITI['equipment']:      ({}, set()),
        }
        for commonKey, (typeName, cacheFunc) in {
            'optional_devices': ('optionalDevice', _veh.g_cache.optionalDevices),
            'equipments':       ('equipment',      _veh.g_cache.equipments),
        }.items():
            xmlPath = _veh._VEHICLE_TYPE_XML_PATH + 'common/' + commonKey + '.xml'
            section = ResMgr.openSection(xmlPath)
            if section is None: continue
            for odName, odSection in section.items():
                try:
                    ctx  = (None, xmlPath + '/' + odName)
                    oid  = _xml.readInt(ctx, odSection, 'id')
                    price = _xml.readPrice(ctx, odSection, 'price')
                    dev  = cacheFunc()[oid]
                    result[none_idx][_ITI[typeName]][0][dev.compactDescr] = price
                except: pass
            ResMgr.purge(xmlPath, True)

        for nationIdx in INDICES.values():
            result[nationIdx] = dict((t, ({}, set())) for t in SIMPLE_ITEM_TYPE_INDICES)
            result[nationIdx][_ITI['vehicle']] = ({}, set())
            nationName  = AVAILABLE_NAMES[nationIdx]
            listXmlPath = _veh._VEHICLE_TYPE_XML_PATH + nationName + '/list.xml'
            listSection = ResMgr.openSection(listXmlPath)
            if listSection is None: continue
            turretsIDs = ResMgr.openSection(_veh._VEHICLE_TYPE_XML_PATH + nationName + '/components/turrets.xml')
            chassisIDs = ResMgr.openSection(_veh._VEHICLE_TYPE_XML_PATH + nationName + '/components/chassis.xml')
            turretsIDs = turretsIDs['ids'] if turretsIDs else None
            chassisIDs = chassisIDs['ids'] if chassisIDs else None
            for vname, vsection in listSection.items():
                try:
                    ctx   = (None, listXmlPath + '/' + vname)
                    vid   = _xml.readInt(ctx, vsection, 'id', 0, 255)
                    price = _xml.readPrice(ctx, vsection, 'price')
                    result[nationIdx][_ITI['vehicle']][0][vid] = price
                    xmlVehPath = _veh._VEHICLE_TYPE_XML_PATH + nationName + '/' + vname + '.xml'
                    vehSec = ResMgr.openSection(xmlVehPath)
                    if vehSec and turretsIDs:
                        for tname, tsec in vehSec['turrets0'].items():
                            try:
                                tid    = _xml.readInt(ctx, turretsIDs, tname)
                                tprice = _xml.readPrice(ctx, tsec, 'price')
                                turret = _veh.g_cache.turrets(nationIdx)[tid]
                                result[nationIdx][_ITI['vehicleTurret']][0][turret['compactDescr']] = tprice
                            except: pass
                    if vehSec and chassisIDs:
                        for cname, csec in vehSec['chassis'].items():
                            try:
                                cid    = _xml.readInt(ctx, chassisIDs, cname)
                                cprice = _xml.readPrice(ctx, csec, 'price')
                                ch     = _veh.g_cache.chassis(nationIdx)[cid]
                                result[nationIdx][_ITI['vehicleChassis']][0][ch['compactDescr']] = cprice
                            except: pass
                    if vehSec:
                        ResMgr.purge(xmlVehPath, True)
                except: pass

            for compKey, (typeName, cacheFunc) in {
                'guns':    ('vehicleGun',    _veh.g_cache.guns),
                'engines': ('vehicleEngine', _veh.g_cache.engines),
                'radios':  ('vehicleRadio',  _veh.g_cache.radios),
            }.items():
                xmlPath = _veh._VEHICLE_TYPE_XML_PATH + nationName + '/components/' + compKey + '.xml'
                sec = ResMgr.openSection(xmlPath)
                if sec is None: continue
                ids_sec = sec['ids']
                shared  = sec['shared']
                if not ids_sec or not shared: continue
                for mname, msec in shared.items():
                    try:
                        ctx   = (None, xmlPath + '/' + mname)
                        mid   = _xml.readInt(ctx, ids_sec, mname)
                        price = _xml.readPrice(ctx, msec, 'price')
                        mod   = cacheFunc(nationIdx)[mid]
                        result[nationIdx][_ITI[typeName]][0][mod['compactDescr']] = price
                    except: pass
                ResMgr.purge(xmlPath, True)
            xmlPath = _veh._VEHICLE_TYPE_XML_PATH + nationName + '/components/shells.xml'
            sec = ResMgr.openSection(xmlPath)
            if sec:
                for mname, msec in sec.items():
                    if mname == 'icons': continue
                    try:
                        ctx   = (None, xmlPath + '/' + mname)
                        mid   = _xml.readInt(ctx, msec, 'id')
                        price = _xml.readPrice(ctx, msec, 'price')
                        shell = _veh.g_cache.shells(nationIdx)[mid]
                        result[nationIdx][_ITI['shell']][0][shell['compactDescr']] = price
                    except: pass
                ResMgr.purge(xmlPath, True)
        _shop_prices_cache = result
    except Exception:
        import traceback; traceback.print_exc()
    return _shop_prices_cache

def _shop_items_for_parser(itemTypeIdx, nationIdx):

    prices = _build_shop_prices() or {}
    import nations as _nat
    from items import vehicles as _veh, ITEM_TYPE_INDICES as _ITI
    if itemTypeIdx in (_ITI.get('optionalDevice', -1), _ITI.get('equipment', -2)):
        nation_data = prices.get(_nat.NONE_INDEX, {})
    else:
        nation_data = prices.get(nationIdx, {})
    raw = nation_data.get(itemTypeIdx)
    if raw is None:
        return ({}, set())
    if isinstance(raw, tuple) and len(raw) >= 2:
        price_map, hidden = raw[0], raw[1]
    else:
        price_map, hidden = raw, set()
    if not isinstance(price_map, dict):
        price_map = {}
    if not isinstance(hidden, (set, list, tuple, dict)):
        hidden = set()
    else:
        hidden = set(hidden)
    if itemTypeIdx == _ITI.get('vehicle'):
        try:
            known = set((_veh.g_list.getList(nationIdx) or {}).keys())
        except Exception:
            known = None
        if known is not None:
            price_map = dict((k, v) for k, v in price_map.iteritems() if k in known)
    return (price_map, hidden)

_vehicle_equipments = {}
_shop_prices_cache  = {}

def init_offline():
    print "[OFFLINE_LOBBY] World of Tanks Closed Beta Start ! "
    _patch_sync_controller_fini()

    def fake_connect(*args, **kwargs):
        global _modules_inventory, _modules_by_type, _lobby_chat_channels, _lobby_chat_next_cid, _account_prefs_root
        _vehicle_shells.clear()
        _vehicle_equipments.clear()

        try:
            connectionManager.connectionStatusCallbacks(1, 'LOGGED_ON', '')

            try:
                from gui import SoundGroups
                if SoundGroups.g_instance: SoundGroups.g_instance.stopAll()
            except: pass

            spaceID = BigWorld.createSpace()
            from items import vehicles
            import nations
            from items import ITEM_TYPE_INDICES

            _VEHICLE_IDX  = ITEM_TYPE_INDICES['vehicle']
            _CHASSIS_IDX  = ITEM_TYPE_INDICES['vehicleChassis']
            _TURRET_IDX   = ITEM_TYPE_INDICES['vehicleTurret']
            _GUN_IDX      = ITEM_TYPE_INDICES['vehicleGun']
            _ENGINE_IDX   = ITEM_TYPE_INDICES['vehicleEngine']
            _RADIO_IDX    = ITEM_TYPE_INDICES['vehicleRadio']
            _OPTDEV_IDX   = ITEM_TYPE_INDICES['optionalDevice']
            _EQUIP_IDX    = ITEM_TYPE_INDICES['equipment']
            _SHELL_IDX    = ITEM_TYPE_INDICES['shell']
            _TANKMAN_IDX  = ITEM_TYPE_INDICES['tankman']

            import random
            try:
                import ArenaType as _AT_maps
                arena_list = list(_AT_maps.g_list.values())
                if not arena_list:
                    raise ValueError('ArenaType.g_list empty')
            except Exception:
                arena_list = [
                    '01_karelia', '02_malinovka','04_himmelsdorf','05_prohorovka', '06_ensk', '07_lakeville','11_murovanka'
                ]
            tank_list = [
                'ussr:MS-1', 'germany:Ltraktor', 'ussr:Matilda_II_LL'
            ]
            try:
                _t1n = _usa_t1_cunningham_name()
                if _t1n and _t1n not in tank_list:
                    tank_list.append(_t1n)
            except Exception:
                pass
            import random
            global _selected_arena
            _selected_arena = random.choice(arena_list)
            my_garage = {}
            veh_cds   = {}
            for i, name in enumerate(tank_list):
                invID = i + 1
                my_garage[invID] = name
                try:
                    veh_cds[invID] = vehicles.VehicleDescr(typeName=name).makeCompactDescr()
                except Exception as e:
                    pass

            all_unlocks = []
            _modules_inventory = {}
            _modules_by_type   = {}

            for invID, name in my_garage.items():
                if invID not in veh_cds: continue

                try:
                    nID, vID = vehicles.g_list.getIDsByName(name)
                    typeCD = vehicles.makeIntCompactDescrByID('vehicle', nID, vID)
                    all_unlocks.append(typeCD)
                except Exception as e:
                    pass

                try:
                    descr = vehicles.VehicleDescr(compactDescr=veh_cds[invID])
                    for idx, modCD in [
                        (_CHASSIS_IDX, descr.chassis['compactDescr']),
                        (_TURRET_IDX,  descr.turret['compactDescr']),
                        (_GUN_IDX,     descr.gun['compactDescr']),
                        (_ENGINE_IDX,  descr.engine['compactDescr']),
                        (_RADIO_IDX,   descr.radio['compactDescr'])
                    ]:
                        if modCD:
                            all_unlocks.append(modCD)
                            _modules_inventory[modCD] = 1
                            _modules_by_type.setdefault(idx, {})[modCD] = 1
                except Exception as e:
                    print "[OFFLINE] Error adding base modules:", e

            inv_vehicles = dict((invID, cd) for invID, cd in veh_cds.items())
            t_cache        = {}
            t_in_veh       = {}
            crew_map       = {}
            current_tman_id = 10

            for invID, name in my_garage.items():
                if invID not in veh_cds: continue
                descr      = vehicles.VehicleDescr(compactDescr=veh_cds[invID])
                crew_ids   = []
                nationID   = descr.type.id[0]
                vehTypeID  = descr.type.id[1]

                for role in descr.type.crewRoles:
                    passport = tankmen.generatePassport(nationID)
                    tman_cd  = tankmen.generateCompactDescr(passport, vehTypeID, role[0], 100)
                    t_cache[current_tman_id]  = tman_cd
                    t_in_veh[current_tman_id] = invID
                    crew_ids.append(current_tman_id)
                    current_tman_id += 1
                crew_map[invID] = crew_ids

            xp_per_tank = {
                'ussr:MS-1':        0,
                'germany:Ltraktor':  0,
            }

            vehTypeXP = {}
            for invID, name in my_garage.items():
                xp = xp_per_tank.get(name, 0)
                if xp > 0:
                    nID, vID = vehicles.g_list.getIDsByName(name)
                    typeCD = vehicles.makeIntCompactDescrByID('vehicle', nID, vID)
                    vehTypeXP[typeCD] = xp

            inv_data = [
                inv_vehicles,
                dict((i, {})         for i in my_garage),
                dict((i, [])         for i in my_garage),
                crew_map,
                dict((i, (0, 100))   for i in my_garage),
                dict((i, [0, 0, 0])  for i in my_garage),
                dict((i, [0, 0, 0])  for i in my_garage),
                dict((i, 0)          for i in my_garage),
                dict((i, 0)          for i in my_garage)
            ]

            global _inv_vehicles_ref, _inv_data_ref, _crew_map_ref, _t_cache_ref, _t_in_veh_ref, _my_garage_ref, _veh_cds_ref
            _inv_vehicles_ref = inv_vehicles
            _inv_data_ref     = inv_data
            _crew_map_ref     = crew_map
            _t_cache_ref      = t_cache
            _t_in_veh_ref     = t_in_veh
            _my_garage_ref    = my_garage
            _veh_cds_ref      = veh_cds
            _apply_loaded_vehicles_and_crew()

            OFFLINE_CREDITS   = 50000000
            OFFLINE_GOLD      = 50000000
            OFFLINE_FREE_XP   = 50000000
            OFFLINE_SLOTS     = 10
            OFFLINE_BERTHS    = 16

            global _account_dossier, _veh_type_dossiers
            import dossiers as _dossiers
            import time as _time_mod
            if _account_dossier is None:
                _account_dossier = _dossiers.getAccountDossierDescr('')
                _account_dossier['creationTime'] = int(_time_mod.time())
            _veh_type_dossiers = _veh_type_dossiers

            off_stats = {
                'credits':          OFFLINE_CREDITS,
                'gold':             OFFLINE_GOLD,
                'freeXP':           OFFLINE_FREE_XP,
                'unlocks':          _add_premium_vehicle_unlocks(all_unlocks + list(_modules_inventory.keys())),
                'eliteVehicles':    set(),
                'vehTypeXP':        vehTypeXP,
                'rev':              1,
                'slots':            OFFLINE_SLOTS,
                'berths':           OFFLINE_BERTHS,
                'currentVehInvID':  1,
                'isPremium':        False,
                'premiumExpiryTime':0,
                'dossier':          None,
                'vehTypeDossier':   {},
                'accountType':      __import__('constants').ACCOUNT_TYPE.BASE,
                'clanInfo':         None,
            }

            global _off_stats_ref
            _off_stats_ref = off_stats
            _apply_loaded_profile(off_stats)
            off_stats['unlocks'] = _add_premium_vehicle_unlocks(
                list(off_stats.get('unlocks') or []) + list(all_unlocks) + list(_modules_inventory.keys()))
            off_stats['eliteVehicles'] = _canonical_unlocks(off_stats.get('eliteVehicles'))
            try:
                if _grant_vehicle_if_missing(_usa_t1_cunningham_name()):
                    _save_profile(force=True)
            except Exception as _ge:
                print "[OFFLINE] T1 Cunningham grant failed: %s" % _ge

            if not BigWorld.player():
                BigWorld.createEntity('Account', spaceID, 0, (0, 0, 0), (0, 0, 0), {})
                p = BigWorld.player()
                _apply_account_premium(p, off_stats)
                _apply_player_name(p)

                g_playerEvents.isPlayerEntityChanging = False
                p.isInQueue = False

                def startOfflineBattle(mapId=-1):
                    if _TRAINING_ROOM.get('keep'):
                        try:
                            p.training_startArena()
                            return
                        except Exception as _tse:
                            print "[OFFLINE] startOfflineBattle training_startArena failed: %s" % _tse
                    __import__('Offline.BattleStarter', fromlist=['_']).start_offline_battle(mapId)
                p.startOfflineBattle = startOfflineBattle
                _battle_cb_handle = []
                _queue_length = [8]

                def _queue_info_pair():
                    import constants as _cq
                    n = max(2, int(_queue_length[0]))
                    levels = [0] * (_cq.MAX_VEHICLE_LEVEL + 1)
                    classes = [0] * len(_cq.VEHICLE_CLASSES)
                    try:
                        from CurrentVehicle import g_currentVehicle as _gcv
                        _lv = int(_gcv.vehicle.descriptor.type.level)
                        if 1 <= _lv <= _cq.MAX_VEHICLE_LEVEL:
                            levels[_lv] = n
                        _tags = _gcv.vehicle.descriptor.type.tags
                        for _cls in _cq.VEHICLE_CLASSES:
                            if _cls in _tags:
                                classes[_cq.VEHICLE_CLASS_INDICES[_cls]] = n
                                break
                    except Exception:
                        levels[1] = n
                        classes[0] = n
                    randoms = {'length': n, 'levels': levels, 'classes': classes}
                    teams = {'length': 0, 'levels': list(levels), 'classes': list(classes)}
                    return randoms, teams

                def _emit_queue_info():
                    try:
                        g_playerEvents.onQueueInfoReceived(*_queue_info_pair())
                    except Exception as e:
                        print "[OFFLINE] onQueueInfoReceived failed: %s" % e

                def _lock_current_vehicle(reason):
                    try:
                        from CurrentVehicle import g_currentVehicle as _gcv
                        if _gcv.vehicle is not None:
                            _gcv.setLocked(reason)
                            return _gcv.vehicle.inventoryId
                    except Exception:
                        pass
                    return 1

                def _do_start_battle():
                    _battle_cb_handle[:] = []
                    p.isInQueue = False
                    try:
                        _saved_prebattle[0] = getattr(p, 'prebattle', None)
                    except Exception:
                        _saved_prebattle[0] = None
                    if not _TRAINING_ROOM.get('keep') and not _keep_squad_after_battle:
                        p.prebattle = None
                    g_playerEvents.isPlayerEntityChanging = True
                    try:
                        g_playerEvents.onArenaCreated()
                    except Exception as e:
                        print "[OFFLINE] onArenaCreated failed: %s" % e
                    try:
                        g_playerEvents.onPlayerEntityChanging()
                    except Exception as e:
                        print "[OFFLINE] onPlayerEntityChanging failed: %s" % e
                    print "[OFFLINE] start_offline_battle arena=%s training=%s" % (
                        _selected_arena, bool(_TRAINING_ROOM.get('keep')))
                    BigWorld.callback(0.5, p.startOfflineBattle)

                def _fake_enqueue(vehInvID=None, arenaTypeID=0, *args, **kwargs):
                    import random
                    global _selected_arena
                    resolved = None
                    if arenaTypeID not in (None, 0, -1):
                        if arenaTypeID in arena_list:
                            resolved = arenaTypeID
                        else:
                            try:
                                import ArenaType as _AT
                                if arenaTypeID in _AT.g_list:
                                    resolved = _AT.g_list[arenaTypeID]
                                else:
                                    _nm = str(arenaTypeID).replace('spaces/', '')
                                    for _tid, _tname in _AT.g_list.iteritems():
                                        if _tname == _nm:
                                            resolved = _tname
                                            break
                            except Exception:
                                resolved = None
                    if resolved:
                        _selected_arena = resolved
                    else:
                        _selected_arena = random.choice(arena_list)
                    _queue_length[0] = random.randint(6, 14)
                    print "[OFFLINE] enqueueForArena vehInvID=%s arenaTypeID=%s -> %s" % (
                        vehInvID, arenaTypeID, _selected_arena)
                    try:
                        if vehInvID:
                            from CurrentVehicle import g_currentVehicle as _gcv
                            if _gcv.vehicle is None or _gcv.vehicle.inventoryId != vehInvID:
                                _gcv.setVehicleById(vehInvID)
                    except Exception:
                        pass
                    p.isInQueue = True
                    _lock_current_vehicle(AccountCommands.LOCK_REASON.IN_QUEUE)
                    g_playerEvents.onEnqueued()
                    BigWorld.callback(0.1, _emit_queue_info)
                    handle = BigWorld.callback(random.uniform(3.0, 9.0), _do_start_battle)
                    _battle_cb_handle[:] = [handle]

                def _fake_dequeue(*args, **kwargs):
                    p.isInQueue = False
                    if _battle_cb_handle:
                        try:
                            BigWorld.cancelCallback(_battle_cb_handle[0])
                        except:
                            pass
                        _battle_cb_handle[:] = []
                    try:
                        g_playerEvents.onDequeued()
                    except Exception as e:
                        print "[OFFLINE] onDequeued failed: %s" % e
                    def _fire_lock_change():
                        try:
                            veh_inv_id = _lock_current_vehicle(AccountCommands.LOCK_REASON.NONE)
                            g_playerEvents.onVehicleLockChanged(veh_inv_id, AccountCommands.LOCK_REASON.NONE)
                        except Exception as e:
                            print "[OFFLINE] onVehicleLockChanged failed: %s" % e
                        try:
                            from gui.Scaleform.Waiting import Waiting
                            Waiting.hide()
                        except:
                            pass
                    BigWorld.callback(0.3, _fire_lock_change)

                def _fake_request_queue_info(queueType=None, *args, **kwargs):
                    if g_playerEvents.isPlayerEntityChanging:
                        return
                    _emit_queue_info()

                def _fake_create_arena_from_queue(*args, **kwargs):
                    if g_playerEvents.isPlayerEntityChanging:
                        return
                    if _battle_cb_handle:
                        try:
                            BigWorld.cancelCallback(_battle_cb_handle[0])
                        except:
                            pass
                        _battle_cb_handle[:] = []
                    _do_start_battle()

                p.enqueueForArena = new.instancemethod(
                    lambda self, *a, **kw: _fake_enqueue(*a, **kw), p, p.__class__)
                p.enqueueForArenaExt = _fake_enqueue
                p.dequeue = new.instancemethod(
                    lambda self, *a, **kw: _fake_dequeue(), p, p.__class__)
                p.requestQueueInfo = new.instancemethod(
                    lambda self, *a, **kw: _fake_request_queue_info(*a, **kw), p, p.__class__)
                p.createArenaFromQueue = new.instancemethod(
                    lambda self, *a, **kw: _fake_create_arena_from_queue(), p, p.__class__)

                import CurrentVehicle

                class OfflineVehicleWrapper(object):
                    def __init__(self, invID):
                        self.inventoryId  = invID
                        self.descriptor   = vehicles.VehicleDescr(compactDescr=veh_cds[invID])
                        self.level        = self.descriptor.level
                        self.crew         = list(crew_map.get(invID) or [])
                        self.health       = 100
                        self.modelState   = 'undamaged'
                        self.lock         = 0
                        self.isPremium    = 'premium' in self.descriptor.type.tags
                        self.tags         = self.descriptor.type.tags
                        ammo = _vehicle_shells.get(invID)
                        if not ammo:
                            try:
                                ammo = inv_data[2].get(invID, [])
                            except Exception:
                                ammo = []
                        self.shells       = _shell_objs_from_ammo(ammo)
                        self.equipments   = list(_vehicle_equipments.get(invID, [0, 0, 0]))
                        try:
                            _rep = inv_data[4].get(invID, (0, 100))
                            self.repairCost = float(_rep[0] or 0)
                            self.health = int(_rep[1] if len(_rep) > 1 else 100)
                        except Exception:
                            self.repairCost = 0
                            self.health = 100
                        try:
                            self.__settings = int(inv_data[7].get(invID, 0) or 0)
                        except Exception:
                            self.__settings = 0
                        if self.repairCost > 0:
                            if self.health <= 0:
                                self.modelState = 'destroyed'
                            else:
                                self.modelState = 'damaged'
                        else:
                            self.modelState = 'undamaged'
                    def getShellsList(self):
                        return _flat_ammo_list(getattr(self, 'shells', None))
                    def getShellsDefaultList(self):
                        try:
                            layout = inv_data[1].get(self.inventoryId) or {}
                            key = (self.descriptor.turret['compactDescr'], self.descriptor.gun['compactDescr'])
                            stored = layout.get(key)
                            if stored:
                                return _flat_ammo_list(stored)
                        except Exception:
                            pass
                        return self.getShellsList()
                    def setShellsList(self, shellsList=None):
                        if shellsList is None:
                            return
                        self.shells = _shell_objs_from_ammo(_flat_ammo_list(shellsList))
                    def loadShells(self, shells, callback=None):
                        BigWorld.player().inventory.equipShells(self.inventoryId, shells, callback)
                    from adisp import async as _async_iselite
                    @_async_iselite
                    def isElite(self, callback):
                        try:
                            _el = off_stats.get('eliteVehicles', [])
                        except Exception:
                            _el = []
                        try:
                            _res = len(self.descriptor.type.unlocksDescrs) == 0 or self.descriptor.type.compactDescr in _el
                        except Exception:
                            _res = False
                        callback(_res)
                    def __getattr__(self, n): return None
                    def _settings_flags(self):
                        try:
                            return int(inv_data[7].get(self.inventoryId, 0) or 0)
                        except Exception:
                            return int(getattr(self, '_OfflineVehicleWrapper__settings', 0) or 0)
                    @property
                    def isAutoRepair(self):
                        from AccountCommands import VEHICLE_SETTINGS_FLAG as _VSF
                        return bool(self._settings_flags() & _VSF.AUTO_REPAIR)
                    @property
                    def isAutoLoad(self):
                        from AccountCommands import VEHICLE_SETTINGS_FLAG as _VSF
                        return bool(self._settings_flags() & _VSF.AUTO_LOAD)
                    @property
                    def isAutoEquip(self):
                        from AccountCommands import VEHICLE_SETTINGS_FLAG as _VSF
                        return bool(self._settings_flags() & _VSF.AUTO_EQUIP)
                    @property
                    def isXPToTmen(self):
                        from AccountCommands import VEHICLE_SETTINGS_FLAG as _VSF
                        return bool(self._settings_flags() & _VSF.XP_TO_TMEN)

                def call_smart(cb, data):
                    if not cb: return
                    def _invoke(_cb=cb, _data=data):
                        try:
                            _cb(0, _data)
                            return
                        except TypeError:
                            pass
                        try:
                            _cb(0, _data, None)
                            return
                        except TypeError:
                            pass
                        try:
                            _cb(_data)
                        except Exception:
                            pass
                    _invoke()

                def makeMock(obj_name, method_name):
                    def force_cb(self, *args, **kwargs):
                        if obj_name == 'shop' and method_name == 'getSellPrice':
                            from math import ceil as _ceil
                            buyPrice = args[0] if args else (0, 0)
                            mods = args[1] if len(args) > 1 else (0, 400, 0.5)
                            try:
                                _ex = float(mods[1])
                                _sm = float(mods[2])
                                return int(_ceil(_sm * (int(buyPrice[0]) + int(buyPrice[1]) * _ex)))
                            except Exception:
                                return int(buyPrice[0]) // 2 if buyPrice else 0
                        cb  = next((a for a in args if callable(a)), kwargs.get('callback'))
                        req = args[0] if len(args) > 0 else None
                        res = 0

                        if obj_name == 'dossierCache' and method_name == 'get':
                            res = ''
                        elif method_name in ('get', 'getCache', 'request'):
                            if obj_name == 'stats' and req == 'berths':
                                res = off_stats.get('berths', 0)
                            elif obj_name == 'stats' and req == 'dossier':
                                try:
                                    res = _account_dossier.makeCompDescr()
                                except Exception:
                                    res = ''
                            elif obj_name == 'stats' and req == 'vehTypeDossier':
                                try:
                                    res = dict(
                                        (cd, d.makeCompDescr())
                                        for cd, d in _veh_type_dossiers.items()
                                    )
                                except Exception:
                                    res = {}
                            else:
                                res = off_stats.get(req, {})
                            if obj_name == 'stats' and req == 'unlocks':
                                res = _add_premium_vehicle_unlocks(off_stats.get('unlocks'))
                                off_stats['unlocks'] = res
                                try:
                                    from gui.Scaleform.ConstructionDepartment import ConstructionDepartment as _CDU
                                    _CDU._ConstructionDepartment__unlocks = res
                                except Exception:
                                    pass
                            elif obj_name == 'stats' and req == 'eliteVehicles':
                                res = _canonical_unlocks(off_stats.get('eliteVehicles'))
                                off_stats['eliteVehicles'] = res
                            if obj_name == 'stats' and req in ('vehTypeXP', 'freeXP', 'eliteVehicles', 'vehTypeDossier', 'unlocks'):
                                print "[OFFLINE][VC] stats.get(%s) responded (cb=%r)" % (req, cb)

                        elif method_name == 'getItems':
                            if obj_name == 'inventory':
                                if req == _VEHICLE_IDX:
                                    res = _as_vehicle_inv_dict(inv_data)
                                elif req == _TANKMAN_IDX:
                                    res = _as_tankman_inv_dict((t_cache, t_in_veh))
                                elif req in _modules_by_type:
                                    res = _modules_by_type[req]
                                else:
                                    res = {}
                                if req == _VEHICLE_IDX:
                                    print "[OFFLINE][VC] inventory.getItems(vehicles) responded (cb=%r)" % (cb,)

                            elif obj_name == 'shop':
                                nationID = args[1] if len(args) > 1 else None
                                res = _shop_items_for_parser(req, nationID)

                        elif obj_name == 'shop' and method_name == 'getSellPriceModifiers':
                            econ = _load_economics()
                            res = (0, int(econ.get('exchangeRate') or 400), 0.5)

                        elif obj_name == 'shop' and method_name == 'getVehiclesSellPrices':
                            veh_list = args[0] if args else []
                            res = [_vehicle_sell_credits(cd) for cd in (veh_list or [])]

                        elif obj_name == 'shop' and method_name in ('getVehicleSellPrice', 'getComponentSellPrice'):
                            cd = args[0] if args else None
                            res = _vehicle_sell_credits(cd) if method_name == 'getVehicleSellPrice' else 0
                            if method_name == 'getComponentSellPrice':
                                try:
                                    from math import ceil
                                    prices = _build_shop_prices()
                                    from items.vehicles import parseIntCompactDescr
                                    (itemType, nationID, compID) = parseIntCompactDescr(cd)
                                    raw = prices.get(nationID, {}).get(itemType, ({}, set()))
                                    pmap = raw[0] if isinstance(raw, tuple) else raw
                                    p_val = pmap.get(cd, (0, 0))
                                    buy_cr = p_val[0] if isinstance(p_val, tuple) else p_val
                                    res = int(ceil(0.5 * int(buy_cr or 0)))
                                except Exception:
                                    res = 0

                        elif obj_name == 'stats' and method_name == 'buySlot':
                            try:
                                econ = _load_economics()
                                slots_now = int(off_stats.get('slots', 0) or 0)
                                price = 0
                                try:
                                    shop_obj = getattr(p, 'shop', None)
                                    if shop_obj is not None:
                                        price = int(shop_obj.getNextSlotPrice(slots_now, econ['slotsPrices']))
                                except Exception:
                                    price = int(econ['slotsPrices'][1][-1])
                                if off_stats.get('gold', 0) >= price:
                                    off_stats['gold']  -= price
                                    off_stats['slots']  = slots_now + 1
                                    print "[OFFLINE][BUY_SLOT] OK: slots=%d gold=%d price=%d" % (
                                        off_stats['slots'], off_stats['gold'], price)
                                    res = 0
                                    _save_profile(force=True)
                                else:
                                    print "[OFFLINE][BUY_SLOT] not enough gold"
                                    res = -1
                            except Exception as e:
                                print "[OFFLINE][BUY_SLOT] error: %s" % e
                                res = -1
                            BigWorld.callback(0.1, lambda: g_playerEvents.onStatsResync())

                        elif obj_name == 'stats' and method_name == 'buyBerths':
                            try:
                                econ = _load_economics()
                                berths_now = int(off_stats.get('berths', 0) or 0)
                                price = 300
                                try:
                                    shop_obj = getattr(p, 'shop', None)
                                    if shop_obj is not None:
                                        price = int(shop_obj.getNextBerthPackPrice(berths_now, econ['berthsPrices']))
                                except Exception:
                                    price = int(econ['berthsPrices'][2][-1])
                                pack = int(econ.get('berthsInPack') or 16)
                                if off_stats.get('gold', 0) >= price:
                                    off_stats['gold']   -= price
                                    off_stats['berths']  = berths_now + pack
                                    res = 0
                                else:
                                    print "[OFFLINE][BUY_BERTHS] not enough gold"
                                    res = -1
                            except Exception as e:
                                print "[OFFLINE][BUY_BERTHS] error: %s" % e
                                res = -1
                            BigWorld.callback(0.1, lambda: g_playerEvents.onStatsResync())

                        elif obj_name == 'shop':
                            if method_name == 'getBerthsPrices':
                                res = _load_economics()['berthsPrices']

                            elif method_name == 'getShellPrice':
                                try:
                                    compact = args[0]
                                    prices = _build_shop_prices()
                                    shell_price = (0, 0)
                                    for nat_data in prices.values():
                                        from items import ITEM_TYPE_INDICES as _ITI2
                                        shell_data = nat_data.get(_ITI2['shell'], ({}, set()))
                                        raw = shell_data[0] if isinstance(shell_data, tuple) else shell_data
                                        if compact in raw:
                                            shell_price = raw[compact]
                                            break
                                    res = shell_price
                                except:
                                    res = (0, 0)

                            elif method_name == 'getPrice':
                                try:
                                    from items import ITEM_TYPE_INDICES as _ITI_PRICE
                                    import nations as _natp
                                    itemTypeIdx = args[0]
                                    nationIdx = args[1]
                                    itemShopID = args[2]
                                    prices = _build_shop_prices()
                                    if itemTypeIdx in (
                                        _ITI_PRICE.get('optionalDevice', -1),
                                        _ITI_PRICE.get('equipment', -2)):
                                        nat_data = prices.get(_natp.NONE_INDEX, {})
                                    else:
                                        nat_data = prices.get(nationIdx, {})
                                    raw = nat_data.get(itemTypeIdx, ({}, set()))
                                    raw = raw[0] if isinstance(raw, tuple) else raw
                                    p_val = raw.get(itemShopID, (0, 0))
                                    if not isinstance(p_val, tuple):
                                        p_val = (int(p_val), 0)
                                    res = p_val
                                except Exception:
                                    res = (0, 0)
                            elif method_name == 'getVehicleSellPrice':
                                try:
                                    compact = args[0]
                                    res = _vehicle_sell_credits(compact)
                                except:
                                    res = 0

                            elif method_name == 'getComponentSellPrice':
                                try:
                                    compact = args[0]
                                    prices = _build_shop_prices()
                                    buy_cr = 0
                                    for nat_data in prices.values():
                                        for type_data in nat_data.values():
                                            raw = type_data[0] if isinstance(type_data, tuple) else type_data
                                            if compact in raw:
                                                price = raw[compact]
                                                buy_cr = price[0] if isinstance(price, tuple) else price
                                                break
                                        else:
                                            continue
                                        break
                                    res = buy_cr // 2
                                except:
                                    res = 0
                            elif method_name == 'getTankmanCost':
                                res = _read_tankman_cost()
                            elif method_name == 'getPassportChangeCost':
                                res = int(_load_economics().get('passportChangeCost', 50) or 50)
                            elif method_name == 'getSlotsPrices':
                                res = _load_economics()['slotsPrices']
                            elif method_name == 'getExchangeRate':
                                res = int(_load_economics()['exchangeRate'])
                            elif method_name == 'getFreeXPConversion':
                                res = _load_economics()['freeXPConversion']
                            elif method_name == 'getPremiumCost':
                                res = dict(_load_economics()['premiumCost'])

                            elif method_name == 'buy':
                                try:
                                    type_idx   = args[0] if len(args) > 0 else None
                                    compact_cd = args[2] if len(args) > 2 else None
                                    count_buy  = int(args[3]) if len(args) > 3 else 1
                                    buy_cr   = 0
                                    buy_gold = 0
                                    prices = _build_shop_prices()
                                    if compact_cd is not None:
                                        try:
                                            from items.vehicles import parseIntCompactDescr
                                            import nations as _natb
                                            (itemType, nationID, compID) = parseIntCompactDescr(compact_cd)
                                            nat_data = prices.get(nationID, {})
                                            type_data = nat_data.get(itemType, ({}, set()))
                                            raw = type_data[0] if isinstance(type_data, tuple) else type_data
                                            if compact_cd not in raw:
                                                nat_data = prices.get(_natb.NONE_INDEX, {})
                                                type_data = nat_data.get(itemType, ({}, set()))
                                                raw = type_data[0] if isinstance(type_data, tuple) else type_data
                                            if compact_cd in raw:
                                                p_val    = raw[compact_cd]
                                                buy_cr   = p_val[0] if isinstance(p_val, tuple) else p_val
                                                buy_gold = p_val[1] if isinstance(p_val, tuple) and len(p_val) > 1 else 0
                                        except:
                                            pass
                                    if buy_gold > 0:
                                        off_stats['gold']    = off_stats.get('gold', 0)    - buy_gold * count_buy
                                    else:
                                        off_stats['credits'] = off_stats.get('credits', 0) - buy_cr   * count_buy
                                    try:
                                        print "[OFFLINE][BUY] type=%s cd=%s count=%s cr=%s gold=%s credits_now=%s" % (
                                            type_idx, compact_cd, count_buy, buy_cr, buy_gold, off_stats.get('credits', 0))
                                    except: pass
                                    if compact_cd is not None:
                                        cur = _modules_inventory.get(compact_cd, 0)
                                        _modules_inventory[compact_cd] = cur + count_buy
                                        if type_idx is not None:
                                            _modules_by_type.setdefault(type_idx, {})[compact_cd] = _modules_inventory[compact_cd]
                                        _refresh_player_inventory_items()
                                        try:
                                            from items import ITEM_TYPE_INDICES as _ITI_BUY
                                            _eq_idx = _ITI_BUY.get('equipment')
                                        except Exception:
                                            _eq_idx = 11
                                        if type_idx == _eq_idx:
                                            _try_mount_bought_equipment(compact_cd)
                                    res = 0
                                except Exception as e:
                                    print "[OFFLINE][BUY_MOD] error: %s" % e
                                    res = -1

                        elif obj_name == 'stats' and method_name == 'upgradeToPremium':
                            try:
                                import time as _time
                                import constants as _cprem
                                days = int(float(args[0])) if args else 1
                                cost_map = dict(_load_economics()['premiumCost'])
                                cost = cost_map.get(days, cost_map.get(1, 250))
                                if off_stats.get('gold', 0) >= cost:
                                    off_stats['gold']              -= cost
                                    off_stats['isPremium']          = True
                                    now_exp = int(off_stats.get('premiumExpiryTime') or 0)
                                    base_t = now_exp if now_exp > int(_time.time()) else int(_time.time())
                                    off_stats['premiumExpiryTime']  = base_t + days * 86400
                                    _apply_account_premium(p, off_stats)
                                    try:
                                        import constants as _cacc
                                        p.accountType = _cacc.ACCOUNT_TYPE.PREMIUM
                                        off_stats['accountType'] = _cacc.ACCOUNT_TYPE.PREMIUM
                                    except Exception:
                                        pass
                                    print "[OFFLINE][PREMIUM] OK: %d days, gold=%d expiry=%d" % (
                                        days, off_stats['gold'], off_stats['premiumExpiryTime'])
                                    res = 0
                                else:
                                    print "[OFFLINE][PREMIUM] not enough gold"
                                    res = -1
                            except Exception as e:
                                print "[OFFLINE][PREMIUM] error: %s" % e
                                res = -1
                            def _premium_gui():
                                try:
                                    g_playerEvents.onClientUpdated({
                                        'account': {
                                            'accountType': off_stats.get('accountType'),
                                            'premiumExpiryTime': off_stats.get('premiumExpiryTime') or 0,
                                        },
                                        'stats': {
                                            'gold': off_stats.get('gold', 0),
                                        },
                                    })
                                except Exception as _pe:
                                    print "[OFFLINE][PREMIUM] onClientUpdated failed: %s" % _pe
                                g_playerEvents.onStatsResync()
                                _reload_hangar_for_account_type()
                            BigWorld.callback(0.05, _premium_gui)

                        elif obj_name == 'stats' and method_name == 'exchange':
                            try:
                                gold_amount = int(float(args[0])) if args else 0
                                rate = int(_load_economics()['exchangeRate'])
                                if gold_amount > 0 and off_stats.get('gold', 0) >= gold_amount:
                                    off_stats['gold'] = off_stats.get('gold', 0) - gold_amount
                                    off_stats['credits'] = off_stats.get('credits', 0) + gold_amount * rate
                                    print "[OFFLINE][EXCHANGE] OK: %d gold * %d -> credits=%s" % (
                                        gold_amount, rate, off_stats.get('credits'))
                                    res = 0
                                else:
                                    print "[OFFLINE][EXCHANGE] not enough gold or zero"
                                    res = -1
                            except Exception as e:
                                print "[OFFLINE][EXCHANGE] error: %s" % e
                                res = -1
                            BigWorld.callback(0.1, lambda: g_playerEvents.onStatsResync())

                        elif obj_name == 'stats' and method_name == 'convertToFreeXP':
                            try:
                                veh_cd = int(args[0]) if len(args) > 0 else 0
                                xp_req = int(float(args[1])) if len(args) > 1 else 0
                                disc, cost = _load_economics()['freeXPConversion']
                                disc = int(disc) or 1
                                cost = int(cost)
                                packs = xp_req / disc
                                xp_take = packs * disc
                                gold_need = packs * cost
                                vxp = off_stats.setdefault('vehTypeXP', {})
                                cur_xp = int(vxp.get(veh_cd, 0) or 0)
                                if packs <= 0:
                                    print "[OFFLINE][CONV_XP] zero packs"
                                    res = -1
                                elif off_stats.get('gold', 0) < gold_need:
                                    print "[OFFLINE][CONV_XP] not enough gold"
                                    res = -1
                                elif cur_xp < xp_take:
                                    print "[OFFLINE][CONV_XP] not enough veh XP: have=%d need=%d" % (cur_xp, xp_take)
                                    res = -1
                                else:
                                    off_stats['gold'] = off_stats.get('gold', 0) - gold_need
                                    vxp[veh_cd] = cur_xp - xp_take
                                    off_stats['freeXP'] = off_stats.get('freeXP', 0) + xp_take
                                    print "[OFFLINE][CONV_XP] OK: veh=%d xp=%d gold=%d freeXP=%s vehXP=%d" % (
                                        veh_cd, xp_take, gold_need, off_stats.get('freeXP'), vxp[veh_cd])
                                    res = 0
                            except Exception as e:
                                print "[OFFLINE][CONV_XP] error: %s" % e
                                res = -1
                            BigWorld.callback(0.1, lambda: g_playerEvents.onStatsResync())

                        elif obj_name == 'stats' and method_name == 'unlock':
                            try:
                                global _unlocks_sources_cache
                                from items import vehicles as _vmod
                                from items import ITEM_TYPE_INDICES as _ITI3
                                _VEH_IDX3 = _ITI3['vehicle']
                                _TURRET_IDX3 = _ITI3['vehicleTurret']
                                vCD = int(args[0]) if len(args) > 0 else None
                                idx = int(args[1]) if len(args) > 1 else None
                                if vCD is None or idx is None:
                                    print "[OFFLINE][UNLOCK] wrong args: %s" % repr(args)
                                    res = -1
                                else:
                                    (_, nat_id, inn_id) = _vmod.parseIntCompactDescr(vCD)
                                    veh_type = _vmod.g_cache.vehicle(nat_id, inn_id)
                                    unlock_descr = veh_type.unlocksDescrs[idx]
                                    xp_cost, item_cd = unlock_descr[0], unlock_descr[1]
                                    unlocks = off_stats.setdefault('unlocks', set())
                                    unlocks = _add_premium_vehicle_unlocks(unlocks)
                                    off_stats['unlocks'] = unlocks
                                    added = []
                                    elite_added = set()
                                    if item_cd in unlocks:
                                        print "[OFFLINE][UNLOCK] already unlocked item=%d" % item_cd
                                        added = [item_cd]
                                        res = 0
                                    else:
                                        cur_xp  = off_stats.get('vehTypeXP', {}).get(vCD, 0)
                                        lack    = xp_cost - cur_xp
                                        free_xp = off_stats.get('freeXP', 0)
                                        if lack > 0 and free_xp < lack:
                                            print "[OFFLINE][UNLOCK] NOT_ENOUGH_XP: veh=%d free=%d need=%d" % (cur_xp, free_xp, xp_cost)
                                            res = -1
                                        else:
                                            off_stats.setdefault('vehTypeXP', {})[vCD] = max(cur_xp - xp_cost, 0)
                                            if lack > 0:
                                                off_stats['freeXP'] = free_xp - lack
                                            unlocks.add(item_cd)
                                            added = [item_cd]
                                            (item_type_idx, nat2, inn2) = _vmod.parseIntCompactDescr(item_cd)
                                            if item_type_idx == _VEH_IDX3:
                                                veh_type2 = _vmod.g_cache.vehicle(nat2, inn2)
                                                for acd in veh_type2.autounlockedItems:
                                                    if acd not in unlocks:
                                                        unlocks.add(acd)
                                                        added.append(acd)
                                            elif item_type_idx == _TURRET_IDX3:
                                                try:
                                                    acd = _vmod.getDictDescr(item_cd)['guns'][0]['compactDescr']
                                                    if acd not in unlocks:
                                                        unlocks.add(acd)
                                                        added.append(acd)
                                                except Exception:
                                                    pass
                                            try:
                                                if _unlocks_sources_cache is None:
                                                    _unlocks_sources_cache = _vmod.getUnlocksSources()
                                                elite = _canonical_unlocks(off_stats.get('eliteVehicles'))
                                                off_stats['eliteVehicles'] = elite
                                                for acd in added:
                                                    for src_veh in _unlocks_sources_cache.get(acd, []):
                                                        src_cd = src_veh.compactDescr
                                                        if src_cd in elite:
                                                            continue
                                                        all_done = True
                                                        for ud in src_veh.unlocksDescrs:
                                                            if ud[1] not in unlocks:
                                                                all_done = False
                                                                break
                                                        if all_done and src_cd not in elite:
                                                            elite.add(src_cd)
                                                            elite_added.add(src_cd)
                                                            print "[OFFLINE][UNLOCK] vehicle %s became elite" % src_veh.name
                                            except Exception as _ux:
                                                print "[OFFLINE][UNLOCK] elite check error: %s" % _ux
                                            print "[OFFLINE][UNLOCK] OK: veh=%d idx=%d item=%d cost=%d vehTypeXP=%d freeXP=%d" % (
                                                vCD, idx, item_cd, xp_cost,
                                                off_stats.get('vehTypeXP', {}).get(vCD, 0),
                                                off_stats.get('freeXP', 0))
                                            try:
                                                if _account_dossier is not None:
                                                    _account_dossier['xp'] = max(
                                                        0, int(_account_dossier['xp'] or 0) - int(xp_cost))
                                            except Exception:
                                                pass
                                            res = 0
                                if res == 0:
                                    try:
                                        _udiff = {'stats': {
                                            'unlocks': set(added or [item_cd]),
                                            'vehTypeXP': {vCD: off_stats.get('vehTypeXP', {}).get(vCD, 0)},
                                            'freeXP': off_stats.get('freeXP', 0),
                                        }}
                                        if elite_added:
                                            _udiff['stats']['eliteVehicles'] = set(elite_added)
                                        g_playerEvents.onClientUpdated(_udiff)
                                    except Exception as _uce:
                                        print "[OFFLINE][UNLOCK] onClientUpdated failed: %s" % _uce
                                BigWorld.callback(0.1, lambda: g_playerEvents.onStatsResync())
                                BigWorld.callback(0.1, lambda: g_playerEvents.onInventoryResync())
                                BigWorld.callback(0.15, _refresh_hangar_xp)
                            except Exception as e:
                                print "[OFFLINE][UNLOCK] error: %s" % e
                                import traceback; traceback.print_exc()
                                res = -1
                            print "[OFFLINE][UNLOCK] callback res=%s cb=%r" % (res, cb)
                            def _unlock_cb(_cb=cb, _res=res):
                                if not _cb:
                                    return
                                try:
                                    _cb(_res)
                                    return
                                except TypeError:
                                    pass
                                try:
                                    _cb(0, _res)
                                except Exception:
                                    pass
                            try:
                                _unlock_cb()
                            except Exception:
                                BigWorld.callback(0.01, _unlock_cb)
                            _save_profile()
                            return

                        _save_profile()
                        call_smart(cb, res)
                    return force_cb

                mocked_methods = [
                    'request', 'get', 'getItems', 'getCache',
                    'setCurrentVehicle', 'changeVehicleSetting', 'respecTankman',
                    'equipTankman', 'buyTankman', 'addTankmanSkill', 'dropTankmanSkill',
                    'getVehicleSellPrice', 'getComponentSellPrice', 'buyVehicle', 'buy',
                    'sell', 'repair', 'exchange', 'convertToFreeXP', 'upgradeToPremium',
                    'buySlot', 'buyBerths',
                    'getBerthsPrices', 'getTankmanCost', 'getSlotsPrices',
                    'getExchangeRate', 'getFreeXPConversion', 'getPremiumCost',
                    'getPassportChangeCost',
                    'getShellPrice', 'getPrice', 'unlock',
                    'getSellPriceModifiers', 'getVehiclesSellPrices', 'getSellPrice',
                ]

                for s in ['inventory', 'stats', 'shop', 'dossierCache']:
                    obj = getattr(p, s, None)
                    if obj:
                        for m in mocked_methods:
                            setattr(obj, m, new.instancemethod(makeMock(s, m), obj, obj.__class__))

                inv_obj = getattr(p, 'inventory', None)
                if inv_obj:
                    def _save_veh_cd(vehicleInvID, new_cd):
                        veh_cds[vehicleInvID] = new_cd
                        inv_vehicles[vehicleInvID] = new_cd
                        inv_data[0][vehicleInvID] = new_cd

                    def _descr_copy(vehicleInvID):
                        from items.vehicles import VehicleDescr as _VD
                        cd = veh_cds.get(vehicleInvID) or inv_data[0].get(vehicleInvID)
                        if not cd:
                            return None
                        return _VD(compactDescr=cd)

                    def fake_equip(self, vehicleInvID, itemCompDescr, callback=None):
                        try:
                            vehicleInvID = int(vehicleInvID)
                            descr = _descr_copy(vehicleInvID)
                            if descr is not None:
                                descr.installComponent(itemCompDescr)
                                _save_veh_cd(vehicleInvID, descr.makeCompactDescr())
                                _save_profile(force=True)
                        except Exception as e:
                            print "[OFFLINE] equip error: %s" % e
                        def _done():
                            if callback:
                                callback(0)
                            _notify_vehicle_inv(vehicleInvID, compDescr=veh_cds.get(vehicleInvID))
                        BigWorld.callback(0.01, _done)

                    def fake_equipTurret(self, vehicleInvID, itemCompDescr, slotIdx, callback=None):
                        try:
                            vehicleInvID = int(vehicleInvID)
                            descr = _descr_copy(vehicleInvID)
                            if descr is not None:
                                descr.installTurret(itemCompDescr, slotIdx)
                                _save_veh_cd(vehicleInvID, descr.makeCompactDescr())
                                _save_profile(force=True)
                        except Exception as e:
                            print "[OFFLINE] equipTurret error: %s" % e
                        def _done():
                            if callback:
                                callback(0)
                            _notify_vehicle_inv(vehicleInvID, compDescr=veh_cds.get(vehicleInvID))
                        BigWorld.callback(0.01, _done)

                    inv_obj.equip       = new.instancemethod(fake_equip,       inv_obj, inv_obj.__class__)
                    inv_obj.equipTurret = new.instancemethod(fake_equipTurret, inv_obj, inv_obj.__class__)

                    def fake_equipShells(self, vehicleInvID, shells, callback=None):
                        try:
                            vehicleInvID = int(vehicleInvID)
                            descr = _descr_copy(vehicleInvID)
                            gun = descr.gun if descr is not None else None
                            shell_list = _normalize_loaded_shells(shells, gun)
                            print "[OFFLINE] equipShells vid=%s ammo=%s" % (vehicleInvID, shell_list)
                            _vehicle_shells[vehicleInvID] = shell_list
                            inv_data[2][vehicleInvID] = shell_list
                            try:
                                if descr is not None:
                                    layout_key = (descr.turret['compactDescr'], descr.gun['compactDescr'])
                                    inv_data[1].setdefault(vehicleInvID, {})[layout_key] = list(shell_list)
                            except Exception:
                                pass
                            from CurrentVehicle import g_currentVehicle
                            veh = g_currentVehicle.vehicle
                            if veh is not None and int(getattr(veh, 'inventoryId', -1) or -1) == vehicleInvID:
                                try:
                                    veh.setShellsList(shell_list)
                                except Exception:
                                    veh.shells = _shell_objs_from_ammo(shell_list)
                            _save_profile(force=True)
                        except Exception as e:
                            print "[OFFLINE] equipShells error: %s" % e
                            import traceback; traceback.print_exc()
                        def _done(_vid=vehicleInvID):
                            if callback:
                                callback(0)
                            _notify_vehicle_inv(
                                _vid,
                                shells=inv_data[2].get(_vid, []),
                                shellsLayout=inv_data[1].get(_vid, {}))
                            try:
                                from CurrentVehicle import g_currentVehicle as _gcv
                                if _gcv.isPresent() and int(_gcv.vehicle.inventoryId) == _vid:
                                    _gcv.onChanged()
                            except Exception:
                                pass
                        BigWorld.callback(0.01, _done)

                    inv_obj.equipShells = new.instancemethod(fake_equipShells, inv_obj, inv_obj.__class__)
                    def fake_equipOptionalDevice(self, vehicleInvID, itemCompDescr, slotIdx, callback=None):
                        try:
                            vehicleInvID = int(vehicleInvID)
                            descr = _descr_copy(vehicleInvID)
                            if descr is not None:
                                if itemCompDescr == 0:
                                    descr.removeOptionalDevice(slotIdx)
                                    print "[OFFLINE] Removed device from slot %d" % slotIdx
                                else:
                                    descr.installOptionalDevice(itemCompDescr, slotIdx)
                                    print "[OFFLINE] Equipped device %d in slot %d" % (itemCompDescr, slotIdx)
                                _save_veh_cd(vehicleInvID, descr.makeCompactDescr())
                                _save_profile(force=True)
                        except Exception as e:
                            print "[OFFLINE] equipOptionalDevice error: %s" % e

                        def _done():
                            if callback:
                                callback(0)
                            _notify_vehicle_inv(vehicleInvID, compDescr=veh_cds.get(vehicleInvID))
                        BigWorld.callback(0.01, _done)

                    inv_obj.equipOptionalDevice = new.instancemethod(fake_equipOptionalDevice, inv_obj, inv_obj.__class__)

                    def fake_equipEquipments(self, vehicleInvID, equipCompDescrs, callback=None):
                        try:
                            vehicleInvID = int(vehicleInvID)
                            if not isinstance(equipCompDescrs, (list, tuple)):
                                equipCompDescrs = list(equipCompDescrs) if equipCompDescrs else [0, 0, 0]
                            old_slots = list(_vehicle_equipments.get(vehicleInvID, [0, 0, 0]) or [0, 0, 0])
                            while len(old_slots) < 3:
                                old_slots.append(0)
                            wanted = (list(equipCompDescrs) + [0, 0, 0])[:3]
                            for cd in old_slots:
                                try:
                                    cd = int(cd or 0)
                                except Exception:
                                    cd = 0
                                if cd:
                                    _set_module_count(cd, _modules_inventory.get(cd, 0) + 1)
                            slots = [0, 0, 0]
                            for i, cd in enumerate(wanted):
                                try:
                                    cd = int(cd or 0)
                                except Exception:
                                    cd = 0
                                if not cd:
                                    continue
                                have = int(_modules_inventory.get(cd, 0) or 0)
                                if have <= 0:
                                    print "[OFFLINE] equipEquipments: no warehouse copy of %s" % cd
                                    continue
                                _set_module_count(cd, have - 1)
                                slots[i] = cd
                            _sync_vehicle_eq_slots(vehicleInvID, slots)
                            print "[OFFLINE] equipEquipments veh=%s slots=%s" % (vehicleInvID, slots)
                            _save_profile(force=True)
                        except Exception as e:
                            print "[OFFLINE] equipEquipments error: %s" % e
                            import traceback; traceback.print_exc()
                        def _done():
                            if callback:
                                callback(0)
                            _notify_vehicle_inv(
                                vehicleInvID,
                                eqs=inv_data[6].get(vehicleInvID, [0, 0, 0]),
                                eqsLayout=inv_data[5].get(vehicleInvID, [0, 0, 0]))
                        BigWorld.callback(0.01, _done)

                    inv_obj.equipEquipments = new.instancemethod(fake_equipEquipments, inv_obj, inv_obj.__class__)

                    def fake_equipTankman(self, vehicleInvID, slot, tankmanID, callback=None):
                        try:
                            vehicleInvID = int(vehicleInvID) if vehicleInvID is not None else None
                            slot         = int(slot)         if slot is not None else None
                            tankmanID    = int(tankmanID)    if tankmanID is not None else None

                            if vehicleInvID is not None and slot is not None:
                                crew_list = inv_data[3].setdefault(vehicleInvID, [])
                                while len(crew_list) <= slot:
                                    crew_list.append(None)

                                if tankmanID is None:
                                    old_tid = crew_list[slot]
                                    if old_tid is not None:
                                        crew_list[slot] = None
                                        t_in_veh.pop(old_tid, None)
                                        print "[OFFLINE][TMAN_EQUIP] UNLOAD tid=%d veh=%d slot=%d OK" % (old_tid, vehicleInvID, slot)
                                else:
                                    for vid, cl in inv_data[3].items():
                                        if isinstance(cl, list) and tankmanID in cl:
                                            cl[cl.index(tankmanID)] = None
                                            break
                                    old_tid = crew_list[slot]
                                    if old_tid is not None:
                                        t_in_veh.pop(old_tid, None)
                                    crew_list[slot] = tankmanID
                                    t_in_veh[tankmanID] = vehicleInvID
                                    print "[OFFLINE][TMAN_EQUIP] LOAD tid=%d veh=%d slot=%d OK" % (tankmanID, vehicleInvID, slot)

                            _save_profile(force=True)
                        except Exception as e:
                            print "[OFFLINE][TMAN_EQUIP] error: %s" % e
                            import traceback; traceback.print_exc()
                        if callback:
                            BigWorld.callback(0.05, lambda: callback(0))
                        BigWorld.callback(0.1, lambda: g_playerEvents.onInventoryResync())

                    inv_obj.equipTankman = new.instancemethod(fake_equipTankman, inv_obj, inv_obj.__class__)

                    def fake_addTankmanSkill(self, tankmanID, skillName, callback=None):
                        try:
                            from items.tankmen import TankmanDescr
                            tman_cd = t_cache.get(int(tankmanID))
                            if tman_cd:
                                descr = TankmanDescr(tman_cd)
                                descr.addSkill(skillName)
                                t_cache[int(tankmanID)] = descr.makeCompactDescr()
                                print "[OFFLINE][TMAN_SKILL] OK: added %s" % skillName
                                _save_profile(force=True)
                        except Exception as e:
                            print "[OFFLINE][TMAN_SKILL] error: %s" % e
                        if callback:
                            BigWorld.callback(0.05, lambda: callback(0))
                        BigWorld.callback(0.1, lambda: g_playerEvents.onInventoryResync())

                    inv_obj.addTankmanSkill = new.instancemethod(fake_addTankmanSkill, inv_obj, inv_obj.__class__)

                    def fake_dropTankmanSkill(self, tankmanID, skillName, callback=None):
                        try:
                            from items.tankmen import TankmanDescr
                            tman_cd = t_cache.get(int(tankmanID))
                            if tman_cd:
                                descr = TankmanDescr(tman_cd)
                                if skillName in descr.skills:
                                    descr.skills.remove(skillName)
                                elif descr.skills:
                                    descr.skills.pop()
                                t_cache[int(tankmanID)] = descr.makeCompactDescr()
                                print "[OFFLINE][TMAN_DROP] OK"
                        except Exception as e:
                            print "[OFFLINE][TMAN_DROP] error: %s" % e
                        if callback:
                            BigWorld.callback(0.05, lambda: callback(0))
                        BigWorld.callback(0.1, lambda: g_playerEvents.onInventoryResync())

                    inv_obj.dropTankmanSkill = new.instancemethod(fake_dropTankmanSkill, inv_obj, inv_obj.__class__)

                    def fake_respecTankman(self, tankmanID, vehTypeCompDescr, respecTypeIdx, callback=None):
                        try:
                            from items.tankmen import TankmanDescr
                            tman_cd = t_cache.get(int(tankmanID))
                            if tman_cd:
                                descr = TankmanDescr(tman_cd)
                                descr.skills = []
                                descr.roleLevel = 100
                                t_cache[int(tankmanID)] = descr.makeCompactDescr()
                                cost_table = _read_tankman_cost()
                                if int(respecTypeIdx) == 2:
                                    cost = cost_table[2].get('gold', 200)
                                    off_stats['gold'] = off_stats.get('gold', 0) - cost
                                else:
                                    cost = cost_table[1].get('credits', 20000)
                                    off_stats['credits'] = off_stats.get('credits', 0) - cost
                                print "[OFFLINE][TMAN_RESPEC] OK"
                        except Exception as e:
                            print "[OFFLINE][TMAN_RESPEC] error: %s" % e
                        if callback:
                            BigWorld.callback(0.05, lambda: callback(0))
                        BigWorld.callback(0.1, lambda: g_playerEvents.onInventoryResync())
                        BigWorld.callback(0.1, lambda: g_playerEvents.onStatsResync())

                    inv_obj.respecTankman = new.instancemethod(fake_respecTankman, inv_obj, inv_obj.__class__)

                    def fake_dismissTankman(self, tankmanID, callback=None):
                        try:
                            tid = int(tankmanID)
                            t_cache.pop(tid, None)
                            t_in_veh.pop(tid, None)
                            for vid, crew_list in inv_data[3].items():
                                if isinstance(crew_list, list) and tid in crew_list:
                                    idx = crew_list.index(tid)
                                    crew_list[idx] = None
                                    break
                            print "[OFFLINE][TMAN_DISMISS] OK"
                        except Exception as e:
                            print "[OFFLINE][TMAN_DISMISS] error: %s" % e
                        if callback:
                            BigWorld.callback(0.05, lambda: callback(0))
                        BigWorld.callback(0.1, lambda: g_playerEvents.onInventoryResync())

                    inv_obj.dismissTankman = new.instancemethod(fake_dismissTankman, inv_obj, inv_obj.__class__)

                    def fake_changeVehicleSetting(self, vehInvID, setting, isOn, callback=None):
                        try:
                            from AccountCommands import VEHICLE_SETTINGS_FLAG as _VSF
                            vid = int(vehInvID)
                            flag = int(setting)
                            cur = int(inv_data[7].get(vid, 0) or 0)
                            if isOn:
                                cur = cur | flag
                            else:
                                cur = cur & ~flag
                            inv_data[7][vid] = cur
                            try:
                                from CurrentVehicle import g_currentVehicle as _gcv
                                if _gcv.vehicle is not None and int(getattr(_gcv.vehicle, 'inventoryId', -1)) == vid:
                                    try:
                                        _gcv.vehicle._InventoryVehicle__settings = cur
                                    except Exception:
                                        pass
                                    try:
                                        _gcv.vehicle._OfflineVehicleWrapper__settings = cur
                                    except Exception:
                                        pass
                            except Exception:
                                pass
                            print "[OFFLINE][VEH_SET] inv=%d flag=%d on=%s -> %d" % (vid, flag, bool(isOn), cur)
                            _save_profile(force=True)
                        except Exception as e:
                            print "[OFFLINE][VEH_SET] error: %s" % e
                        def _done():
                            if callback:
                                callback(0)
                            try:
                                _notify_vehicle_inv(int(vehInvID), settings=inv_data[7].get(int(vehInvID), 0))
                            except Exception:
                                pass
                            try:
                                g_playerEvents.onInventoryResync()
                            except Exception:
                                pass
                        BigWorld.callback(0.01, _done)

                    inv_obj.changeVehicleSetting = new.instancemethod(fake_changeVehicleSetting, inv_obj, inv_obj.__class__)

                    def fake_repair(self, vehInvID, callback=None):
                        try:
                            vid = int(vehInvID)
                            _rep = inv_data[4].get(vid, (0, 100))
                            cost = 0
                            try:
                                cost = int(float(_rep[0] or 0))
                            except Exception:
                                cost = 0
                            if cost > 0:
                                off_stats['credits'] = int(off_stats.get('credits', 0) or 0) - cost
                            inv_data[4][vid] = (0, 100)
                            try:
                                from CurrentVehicle import g_currentVehicle as _gcv
                                if _gcv.vehicle is not None and int(getattr(_gcv.vehicle, 'inventoryId', -1)) == vid:
                                    _gcv.vehicle.repairCost = 0
                                    _gcv.vehicle.health = 100
                                    _gcv.vehicle.modelState = 'undamaged'
                            except Exception:
                                pass
                            _save_profile(force=True)
                            print "[OFFLINE][REPAIR] inv=%d cost=%d" % (vid, cost)
                        except Exception as e:
                            print "[OFFLINE][REPAIR] error: %s" % e
                        def _done():
                            if callback:
                                callback(0)
                            try:
                                _notify_vehicle_inv(int(vehInvID), repair=inv_data[4].get(int(vehInvID), (0, 100)))
                            except Exception:
                                pass
                            try:
                                g_playerEvents.onInventoryResync()
                                g_playerEvents.onStatsResync()
                            except Exception:
                                pass
                        BigWorld.callback(0.01, _done)

                    inv_obj.repair = new.instancemethod(fake_repair, inv_obj, inv_obj.__class__)

                    def fake_replacePassport(self, tmanInvID, isFemale, firstNameID, lastNameID, iconID, callback=None):
                        try:
                            from items.tankmen import TankmanDescr
                            tid = int(tmanInvID)
                            tman_cd = t_cache.get(tid)
                            if not tman_cd:
                                raise Exception('tankman %s not found' % tid)
                            descr = TankmanDescr(tman_cd)
                            ok = descr.replacePassport(isFemale, firstNameID, lastNameID, iconID)
                            if not ok:
                                print "[OFFLINE][PASSPORT] replacePassport rejected by nation config"
                                if callback:
                                    BigWorld.callback(0.01, lambda: callback(-1))
                                return
                            cost = int(_load_economics().get('passportChangeCost', 50) or 50)
                            gold_now = int(off_stats.get('gold', 0) or 0)
                            if gold_now < cost:
                                print "[OFFLINE][PASSPORT] not enough gold"
                                if callback:
                                    BigWorld.callback(0.01, lambda: callback(-1))
                                return
                            off_stats['gold'] = gold_now - cost
                            t_cache[tid] = descr.makeCompactDescr()
                            _save_profile(force=True)
                            print "[OFFLINE][PASSPORT] OK tid=%d gold=%d" % (tid, off_stats['gold'])
                            def _done():
                                if callback:
                                    callback(0)
                                try:
                                    g_playerEvents.onInventoryResync()
                                    g_playerEvents.onStatsResync()
                                except Exception:
                                    pass
                            BigWorld.callback(0.01, _done)
                        except Exception as e:
                            print "[OFFLINE][PASSPORT] error: %s" % e
                            import traceback; traceback.print_exc()
                            if callback:
                                BigWorld.callback(0.01, lambda: callback(-1))

                    inv_obj.replacePassport = new.instancemethod(fake_replacePassport, inv_obj, inv_obj.__class__)

                    shop_obj_tman = getattr(p, 'shop', None)
                    if shop_obj_tman:
                        def fake_buyTankman(self, nationID, vehTypeID, role, crew_type, callback=None):
                            try:
                                passport = tankmen.generatePassport(int(nationID))
                                _crew_level = _crew_role_level(crew_type)
                                tman_cd  = tankmen.generateCompactDescr(passport, int(vehTypeID), role, _crew_level)
                                new_tid  = max(t_cache.keys()) + 1 if t_cache else 100
                                t_cache[new_tid] = tman_cd
                                cost_table = _read_tankman_cost()
                                if int(crew_type) == 2:
                                    cost = cost_table[2].get('gold', 200)
                                    off_stats['gold'] = off_stats.get('gold', 0) - cost
                                else:
                                    cost = cost_table[1].get('credits', 20000)
                                    off_stats['credits'] = off_stats.get('credits', 0) - cost
                                print "[OFFLINE][TMAN_BUY] OK: tid=%d" % new_tid
                                _save_profile(force=True)
                                if callback:
                                    BigWorld.callback(0.05, lambda: callback(0, new_tid, None))
                            except Exception as e:
                                print "[OFFLINE][TMAN_BUY] error: %s" % e
                                if callback:
                                    BigWorld.callback(0.05, lambda: callback(-1, 0))
                            BigWorld.callback(0.1, lambda: g_playerEvents.onInventoryResync())
                            BigWorld.callback(0.1, lambda: g_playerEvents.onStatsResync())

                        shop_obj_tman.buyTankman = new.instancemethod(fake_buyTankman, shop_obj_tman, shop_obj_tman.__class__)

                    shop_obj = getattr(p, 'shop', None)
                    if shop_obj:
                        _next_inv_id = [max(my_garage.keys()) + 1]

                        def fake_buyVehicle(self, nationID, vehInnationID, isShell, isCrew, crew_type, callback=None):
                            try:
                                from items import vehicles as _v
                                from items import ITEM_TYPE_INDICES as _ITI3
                                veh_list = _v.g_list.getList(nationID)
                                veh_entry = veh_list.get(vehInnationID)
                                if veh_entry is None:
                                    raise Exception("Unknown vehicle nationID=%d id=%d" % (nationID, vehInnationID))
                                nation_name = nations.NAMES[nationID]
                                type_name = veh_entry['name']

                                for _exist_cd in veh_cds.values():
                                    try:
                                        _exist = _v.VehicleDescr(compactDescr=_exist_cd)
                                        if _exist.type.id == (nationID, vehInnationID):
                                            print "[OFFLINE][BUY_VEH] already owned: nation=%d veh=%d" % (
                                                nationID, vehInnationID)
                                            if callback:
                                                BigWorld.callback(0.05, lambda: callback(-1, 0))
                                            return
                                    except Exception:
                                        pass

                                prices = _build_shop_prices()
                                veh_prices = prices.get(nationID, {}).get(_ITI3['vehicle'], ({}, set()))[0]
                                buy_cr, buy_gold = veh_prices.get(vehInnationID, (0, 0))
                                if buy_gold > 0:
                                    cur_gold = off_stats.get('gold', 0)
                                    if cur_gold < buy_gold:
                                        print "[OFFLINE][BUY_VEH] not enough gold: need=%d have=%d" % (buy_gold, cur_gold)
                                        if callback:
                                            BigWorld.callback(0.05, lambda: callback(-1, 0))
                                        return
                                    off_stats['gold'] = cur_gold - buy_gold
                                else:
                                    cur_cr = off_stats.get('credits', 0)
                                    if cur_cr < buy_cr:
                                        print "[OFFLINE][BUY_VEH] not enough credits: need=%d have=%d" % (buy_cr, cur_cr)
                                        if callback:
                                            BigWorld.callback(0.05, lambda: callback(-1, 0))
                                        return
                                    off_stats['credits'] = cur_cr - buy_cr
                                print "[OFFLINE][BUY_VEH] OK: nation=%d veh=%d cr=%d gold=%d credits_now=%d gold_now=%d" % (
                                    nationID, vehInnationID, buy_cr, buy_gold, off_stats.get('credits', 0), off_stats.get('gold', 0))

                                new_cd = _v.VehicleDescr(typeName=type_name).makeCompactDescr()
                                new_inv  = _next_inv_id[0]
                                _next_inv_id[0] += 1

                                veh_cds[new_inv]      = new_cd
                                inv_vehicles[new_inv] = new_cd

                                inv_data[0][new_inv] = new_cd
                                inv_data[1][new_inv] = {}
                                inv_data[2][new_inv] = []
                                inv_data[3][new_inv] = []
                                inv_data[4][new_inv] = (0, 100)
                                inv_data[5][new_inv] = [0, 0, 0]
                                inv_data[6][new_inv] = [0, 0, 0]
                                inv_data[7][new_inv] = 0
                                inv_data[8][new_inv] = 0

                                descr = _v.VehicleDescr(compactDescr=new_cd)
                                ammo = []
                                if isShell:
                                    ammo = _v.getDefaultAmmoForGun(descr.gun)
                                    from Inventory import AmmoIterator as _AmmoIt
                                    for shellCompDescr, count in _AmmoIt(ammo):
                                        _, shNat, _ = _v.parseIntCompactDescr(shellCompDescr)
                                        nat_data = prices.get(shNat, {})
                                        type_data = nat_data.get(_ITI3['shell'], ({}, set()))
                                        raw = type_data[0] if isinstance(type_data, tuple) else type_data
                                        sh_price = raw.get(shellCompDescr, (0, 0))
                                        if not isinstance(sh_price, tuple):
                                            sh_price = (int(sh_price), 0)
                                        off_stats['credits'] = off_stats.get('credits', 0) - int(sh_price[0]) * int(count)
                                        if len(sh_price) > 1:
                                            off_stats['gold'] = off_stats.get('gold', 0) - int(sh_price[1]) * int(count)
                                else:
                                    ammo = _v.getEmptyAmmoForGun(descr.gun)
                                inv_data[2][new_inv] = ammo
                                _vehicle_shells[new_inv] = ammo
                                if isCrew:
                                    cost_table = _read_tankman_cost()
                                    ct = cost_table[int(crew_type)] if crew_type is not None and int(crew_type) < len(cost_table) else cost_table[0]
                                    tman_count = len(descr.type.crewRoles)
                                    off_stats['credits'] = off_stats.get('credits', 0) - int(ct.get('credits', 0)) * tman_count
                                    off_stats['gold'] = off_stats.get('gold', 0) - int(ct.get('gold', 0)) * tman_count
                                crew_slots = [None] * len(descr.type.crewRoles)
                                if isCrew:
                                    _crew_level = _crew_role_level(crew_type)
                                    nID   = descr.type.id[0]
                                    vtID  = descr.type.id[1]
                                    for i, role in enumerate(descr.type.crewRoles):
                                        try:
                                            _role_name = role[0] if isinstance(role, (list, tuple)) else role
                                            passport = tankmen.generatePassport(nID)
                                            tman_cd  = tankmen.generateCompactDescr(passport, vtID, _role_name, _crew_level)
                                            tid = max(t_cache.keys()) + 1 if t_cache else 1
                                            t_cache[tid]  = tman_cd
                                            t_in_veh[tid] = new_inv
                                            crew_slots[i] = tid
                                        except Exception as _ce:
                                            print "[OFFLINE][BUY_VEH] crew slot %d failed: %s" % (i, _ce)
                                inv_data[3][new_inv] = crew_slots
                                crew_map[new_inv]    = crew_slots

                                my_garage[new_inv] = veh_entry['name']

                                try:
                                    unlocks = _add_premium_vehicle_unlocks(off_stats.get('unlocks'))
                                    off_stats['unlocks'] = unlocks
                                    nID, vID = _v.g_list.getIDsByName(type_name)
                                    typeCD = _v.makeIntCompactDescrByID('vehicle', nID, vID)
                                    unlocks.add(typeCD)
                                    for idx, modCD in [
                                        (_CHASSIS_IDX, descr.chassis['compactDescr']),
                                        (_TURRET_IDX,  descr.turret['compactDescr']),
                                        (_GUN_IDX,     descr.gun['compactDescr']),
                                        (_ENGINE_IDX,  descr.engine['compactDescr']),
                                        (_RADIO_IDX,   descr.radio['compactDescr'])
                                    ]:
                                        if modCD:
                                            unlocks.add(modCD)
                                            _modules_inventory[modCD] = _modules_inventory.get(modCD, 0) + 1
                                            _modules_by_type.setdefault(idx, {})[modCD] = _modules_inventory[modCD]
                                    print "[OFFLINE][BUY_VEH] unlocks updated, total=%d" % len(unlocks)
                                except Exception as _ue:
                                    print "[OFFLINE][BUY_VEH] unlocks error: %s" % _ue

                                _save_profile(force=True)

                                if callback:
                                    BigWorld.callback(0.05, lambda: callback(0, new_inv))

                                BigWorld.callback(0.1, lambda: g_playerEvents.onInventoryResync())
                                BigWorld.callback(0.1, lambda: g_playerEvents.onStatsResync())

                            except Exception as e:
                                print "[OFFLINE] fake_buyVehicle error: %s" % e
                                import traceback; traceback.print_exc()
                                if callback:
                                    BigWorld.callback(0.05, lambda: callback(-1, 0))

                        shop_obj.buyVehicle = new.instancemethod(fake_buyVehicle, shop_obj, shop_obj.__class__)

                    def fake_sellVehicle(self, typeIdx, inventoryId, count, callback):
                        try:
                            veh_cd = veh_cds.get(inventoryId)
                            sell_cr = _vehicle_sell_credits(veh_cd)
                            if inventoryId in veh_cds:
                                del veh_cds[inventoryId]
                            if inventoryId in inv_vehicles:
                                del inv_vehicles[inventoryId]
                            for slot in range(9):
                                inv_data[slot].pop(inventoryId, None)

                            for tid in list(t_cache.keys()):
                                if t_in_veh.get(tid) == inventoryId:
                                    del t_cache[tid]
                                    del t_in_veh[tid]
                            crew_map.pop(inventoryId, None)
                            my_garage.pop(inventoryId, None)

                            off_stats['credits'] = off_stats.get('credits', 0) + sell_cr
                            print "[OFFLINE] sellVehicle OK: invID=%d cr+%d" % (inventoryId, sell_cr)
                            _sold_vehicles.add(inventoryId)
                            _save_profile(force=True)

                            from CurrentVehicle import g_currentVehicle
                            if g_currentVehicle.vehicle and                               g_currentVehicle.vehicle.inventoryId == inventoryId:
                                if veh_cds:
                                    first = min(veh_cds.keys())
                                    import CurrentVehicle as _CV
                                    _CV.g_currentVehicle._CurrentVehicle__vehicle =                                        OfflineVehicleWrapper(first)
                                    _CV.g_currentVehicle.onChanged()

                            BigWorld.callback(0.05, lambda: callback(0))
                            BigWorld.callback(0.15, lambda: g_playerEvents.onInventoryResync())
                            BigWorld.callback(0.15, lambda: g_playerEvents.onStatsResync())

                        except Exception as e:
                            print "[OFFLINE] fake_sellVehicle error: %s" % e
                            import traceback; traceback.print_exc()
                            BigWorld.callback(0.05, lambda: callback(-1))

                    inv_obj2 = getattr(p, 'inventory', None)
                    if inv_obj2:
                        _orig_sell_mock = inv_obj2.sell

                        def fake_sell_dispatch(self, typeIdx, inventoryId, count, callback):
                            from items import ITEM_TYPE_INDICES as _ITI
                            if typeIdx == _ITI['vehicle']:
                                fake_sellVehicle(self, typeIdx, inventoryId, count, callback)
                            else:
                                try:
                                    if inventoryId in _modules_inventory:
                                        cur = _modules_inventory.get(inventoryId, 0)
                                        if cur <= count:
                                            del _modules_inventory[inventoryId]
                                            for t_dict in _modules_by_type.values():
                                                t_dict.pop(inventoryId, None)
                                        else:
                                            _modules_inventory[inventoryId] = cur - count
                                            for t_dict in _modules_by_type.values():
                                                if inventoryId in t_dict:
                                                    t_dict[inventoryId] = _modules_inventory[inventoryId]
                                    sell_cr = 0
                                    try:
                                        prices = _build_shop_prices()
                                        for nat_data in prices.values():
                                            for type_data in nat_data.values():
                                                raw = type_data[0] if isinstance(type_data, tuple) else type_data
                                                if inventoryId in raw:
                                                    p_val  = raw[inventoryId]
                                                    buy_cr = p_val[0] if isinstance(p_val, tuple) else p_val
                                                    sell_cr = (buy_cr // 2) * count
                                                    break
                                            else:
                                                continue
                                            break
                                    except: pass
                                    off_stats['credits'] = off_stats.get('credits', 0) + sell_cr
                                    print "[OFFLINE][SELL_MOD] OK: cd=%s sold=%d cr+%d" % (inventoryId, count, sell_cr)
                                    BigWorld.callback(0.05, lambda: callback(0))
                                except Exception as e:
                                    print "[OFFLINE][SELL_MOD] error: %s" % e
                                    import traceback; traceback.print_exc()
                                    BigWorld.callback(0.05, lambda: callback(-1))

                        inv_obj2.sell = new.instancemethod(
                            fake_sell_dispatch, inv_obj2, inv_obj2.__class__)

                p.selectVehicle = lambda invID: (
                    setattr(CurrentVehicle.g_currentVehicle, '_CurrentVehicle__vehicle', OfflineVehicleWrapper(invID)),
                    CurrentVehicle.g_currentVehicle.onChanged(),
                    None
                )[2]
                import time as _time
                from chat_shared import CHAT_ACTIONS, CHAT_RESPONSES, buildChatActionData

                _offline_channels = _lobby_chat_channels
                _offline_next_cid = _lobby_chat_next_cid

                def _make_chat_action(action, cid=0, data=None, nick=None):
                    origin = 1
                    try:
                        origin = long(getattr(p, 'id', 1) or 1)
                    except Exception:
                        origin = long(1)
                    return buildChatActionData(
                        action             = action,
                        channelId          = cid,
                        originator         = origin,
                        originatorNickName = nick or p.name,
                        data               = data or {},
                        actionResponse     = CHAT_RESPONSES.success
                    )

                def _emit_chat(act):
                    try:
                        from messenger.gui import MessengerDispatcher as _MD
                        if _MD.g_instance is not None:
                            try:
                                _MD.g_instance.lobbyMessenger.show()
                            except Exception:
                                pass
                    except Exception:
                        pass
                    try:
                        p.onChatAction(act)
                    except Exception:
                        traceback.print_exc()

                def _ensure_lazy_system_channels():
                    try:
                        from messenger.ChannelsManager import CHANNEL_TYPE as _CT
                    except Exception:
                        return
                    common_name = getattr(_CT, 'COMMON', '#chat:channels/common')
                    found = None
                    for cid, info in _offline_channels.items():
                        nm = info.get('name')
                        if nm in (common_name, 'General', 'common', '#chat:channels/common'):
                            found = cid
                            info['name'] = common_name
                            info['system'] = True
                            info['flags'] = int(info.get('flags', 0) or 0)
                            if p.name not in info.get('members', []):
                                info.setdefault('members', []).append(p.name)
                            break
                    if found is None:
                        _offline_channels[1] = {
                            'name': common_name, 'members': [p.name],
                            'system': True, 'flags': 0}
                    if _offline_next_cid[0] < 2:
                        _offline_next_cid[0] = 2

                def fake_requestSystemChatChannels(self):
                    _ensure_lazy_system_channels()
                    ch_list = []
                    for cid, info in _offline_channels.items():
                        ch_list.append({
                            'id':    cid,
                            'channelName': info['name'],
                            'flags': int(info.get('flags', 0) or 0),
                            'isReadOnly': False,
                            'isSystem':   bool(info.get('system')),
                            'isBattle':   False,
                        })
                    act = _make_chat_action(CHAT_ACTIONS.requestChannels, data=ch_list)
                    _emit_chat(act)
                    for cid, info in list(_offline_channels.items()):
                        if info.get('system'):
                            try:
                                fake_enterChat(p, cid)
                            except Exception:
                                pass

                def fake_createChatChannel(self, channelName, password=None):
                    cid = _offline_next_cid[0]
                    _offline_next_cid[0] += 1
                    _offline_channels[cid] = {
                        'name': channelName, 'members': [p.name], 'system': False}
                    act = _make_chat_action(CHAT_ACTIONS.createChannel, cid=cid,
                                            data={'id': cid, 'channelName': channelName,
                                                  'flags': 0, 'isReadOnly': False,
                                                  'isSystem': False, 'isBattle': False,
                                                  'ownerName': p.name})
                    _emit_chat(act)
                    _save_profile(force=True)

                def fake_enterChat(self, channelId, password=None):
                    if channelId not in _offline_channels:
                        return
                    info = _offline_channels[channelId]
                    if p.name not in info['members']:
                        info['members'].append(p.name)
                    act = _make_chat_action(CHAT_ACTIONS.selfEnter, cid=channelId,
                                            data={'id': channelId, 'channelName': info['name'],
                                                  'flags': int(info.get('flags', 0) or 0), 'isReadOnly': False,
                                                  'isSystem': bool(info.get('system')),
                                                  'isBattle': False})
                    _emit_chat(act)
                    origin = 1
                    try:
                        origin = long(getattr(p, 'id', 1) or 1)
                    except Exception:
                        origin = long(1)
                    members = []
                    for nick in info.get('members', []):
                        members.append({'id': origin, 'nickName': nick, 'status': 0})
                    _emit_chat(_make_chat_action(CHAT_ACTIONS.requestMembers, cid=channelId, data=members))

                _prb_chat_cid = [None]

                def _open_prb_channel(flags, channelName):
                    try:
                        if _prb_chat_cid[0]:
                            fake_leaveChat(p, _prb_chat_cid[0])
                    except Exception:
                        pass
                    cid = _offline_next_cid[0]
                    _offline_next_cid[0] += 1
                    _prb_chat_cid[0] = cid
                    _offline_channels[cid] = {
                        'name': channelName, 'members': [p.name], 'system': False, 'flags': flags}
                    act = _make_chat_action(CHAT_ACTIONS.createChannel, cid=cid,
                                            data={'id': cid, 'channelName': channelName,
                                                  'flags': flags, 'isReadOnly': False,
                                                  'isSystem': False, 'isBattle': False,
                                                  'ownerName': p.name})
                    _emit_chat(act)
                    print "[OFFLINE][PRB] chat channel cid=%d flags=%s" % (cid, flags)
                    return cid

                def _close_prb_channel():
                    cid = _prb_chat_cid[0]
                    _prb_chat_cid[0] = None
                    if cid:
                        try:
                            fake_leaveChat(p, cid)
                        except Exception:
                            pass

                def fake_leaveChat(self, channelId):
                    info = _offline_channels.get(channelId)
                    flags = int((info or {}).get('flags', 0) or 0)
                    still_in_squad = False
                    try:
                        from chat_shared import CHAT_CHANNEL_SQUAD as _SQ
                        from account_helpers.AccountPrebattle import AccountPrebattle as _AP
                        still_in_squad = bool(flags & _SQ) and bool(_AP.isSquad())
                    except Exception:
                        still_in_squad = False
                    if still_in_squad:
                        try:
                            fake_enterChat(p, channelId)
                        except Exception:
                            pass
                        return
                    if info and p.name in info.get('members', []):
                        info['members'].remove(p.name)
                    act = _make_chat_action(CHAT_ACTIONS.selfLeave, cid=channelId, data={})
                    _emit_chat(act)
                    if info and not info.get('system'):
                        _offline_channels.pop(channelId, None)
                        dest = _make_chat_action(
                            CHAT_ACTIONS.channelDestroyed, cid=channelId,
                            data={'id': channelId, 'channelName': info.get('name', '')})
                        _emit_chat(dest)
                        _save_profile(force=True)

                def fake_broadcast(self, channelId, message):
                    if not message or not message.strip():
                        return
                    act = _make_chat_action(CHAT_ACTIONS.broadcast, cid=channelId,
                                            data=message, nick=p.name)
                    _emit_chat(act)

                def fake_findChatChannels(self, sample):
                    found = []
                    for cid, info in _offline_channels.items():
                        if sample.lower() in info['name'].lower():
                            found.append({'id': cid, 'channelName': info['name'], 'flags': 0})
                    act = _make_chat_action(CHAT_ACTIONS.requestChannels, data=found)
                    _emit_chat(act)

                def fake_requestChatChannelMembers(self, channelId):
                    members = []
                    origin = 1
                    try:
                        origin = long(getattr(p, 'id', 1) or 1)
                    except Exception:
                        origin = long(1)
                    if channelId in _offline_channels:
                        for nick in _offline_channels[channelId]['members']:
                            members.append({'id': origin, 'nickName': nick, 'status': 0})
                    act = _make_chat_action(CHAT_ACTIONS.requestMembers, cid=channelId,
                                            data=members)
                    _emit_chat(act)

                _ensure_lazy_system_channels()

                p.requestSystemChatChannels   = new.instancemethod(fake_requestSystemChatChannels,   p, p.__class__)
                def _patch_messenger_window():
                    from messenger.gui import MessengerDispatcher as MD
                    from messenger.gui.Scalefrom import CHMS_COMMANDS, JCH_COMMANDS
                    if MD.g_instance is None:
                        BigWorld.callback(0.5, _patch_messenger_window)
                        return
                    lobby = MD.g_instance._MessengerDispatcher__lobbyWindow
                    if getattr(lobby.__class__, '_offline_chat_patched', False):
                        return

                    _orig_close = lobby.__class__.close
                    _orig_add_msg = lobby.__class__.addChannelMessage

                    def patched_close(self):
                        _orig_close(self)

                    def patched_add_msg(self, message, format=True):
                        try:
                            self.show()
                        except Exception:
                            pass
                        try:
                            messageText = _orig_add_msg(self, message, format)
                        except Exception:
                            messageText = getattr(message, 'data', '')
                        handler = self.__dict__.get('_MessengerLobbyInterface__movieViewHandler')
                        cid = 0
                        try:
                            cid = int(getattr(message, 'channel', 0) or 0)
                        except Exception:
                            cid = 0
                        if handler and messageText:
                            text = messageText
                            try:
                                if isinstance(text, unicode):
                                    text = text.encode('utf-8')
                            except Exception:
                                pass
                            try:
                                handler.call(CHMS_COMMANDS.RecieveMessage(), [cid, text, True])
                            except Exception:
                                pass
                        return messageText

                    lobby.__class__.close = patched_close
                    lobby.__class__.addChannelMessage = patched_add_msg
                    lobby.__class__._offline_chat_patched = True
                    try:
                        lobby.show()
                    except Exception:
                        pass
                    try:
                        fake_requestSystemChatChannels(p)
                        for cid, info in list(_offline_channels.items()):
                            if not info.get('system'):
                                fake_enterChat(p, cid)
                    except Exception:
                        pass
                BigWorld.callback(1.5, _patch_messenger_window)
                try:
                    from ChatManager import chatManager as _cm
                    _cm.switchPlayerProxy(p)
                except Exception as _cme:
                    print "[OFFLINE] chatManager.switchPlayerProxy failed: %s" % _cme
                p.createChatChannel           = new.instancemethod(fake_createChatChannel,           p, p.__class__)
                p.enterChat                   = new.instancemethod(fake_enterChat,                   p, p.__class__)
                p.leaveChat                   = new.instancemethod(fake_leaveChat,                   p, p.__class__)
                p.broadcast                   = new.instancemethod(fake_broadcast,                   p, p.__class__)
                p.findChatChannels            = new.instancemethod(fake_findChatChannels,             p, p.__class__)
                p.requestChatChannelMembers   = new.instancemethod(fake_requestChatChannelMembers,   p, p.__class__)

                def _training_make_roster(force_new=False):
                    try:
                        pid = int(getattr(p, 'id', 1) or 1)
                    except Exception:
                        pid = 1
                    inv_id = _TRAINING_ROOM['veh_inv_id']
                    veh_cd = veh_cds.get(inv_id, '')
                    if not veh_cd:
                        try:
                            from CurrentVehicle import g_currentVehicle as _gcv
                            if _gcv.vehicle:
                                veh_cd = _gcv.vehicle.descriptor.makeCompactDescr()
                        except Exception:
                            veh_cd = ''
                    roster = _TRAINING_ROOM.get('roster') or {}
                    if force_new or not roster:
                        roster = {
                            pid: {
                                'team':         1,
                                'name':         p.name,
                                'vehCompDescr': veh_cd,
                                'state':        0
                            },
                            pid + 1: {
                                'team':         2,
                                'name':         'Bot',
                                'vehCompDescr': veh_cd,
                                'state':        0
                            }
                        }
                    elif pid in roster:
                        roster[pid]['vehCompDescr'] = veh_cd or roster[pid].get('vehCompDescr', '')
                        roster[pid]['name'] = p.name
                    _TRAINING_ROOM['roster'] = roster
                    p.trainingRoster = roster
                    return roster

                def _training_push_settings(self):
                    s = _TRAINING_ROOM['settings']
                    g_playerEvents.onTrainingSettingsReceived(
                        p.name,
                        s.get('arenaTypeID', '05_prohorovka'),
                        s.get('roundLength', 900),
                        3600,
                        s.get('isPrivate', 0),
                        s.get('comment', '')
                    )

                def training_create(self, vInventoryID, arenaTypeID, roundLength,
                    roomLifetime, isPrivate, comment):
                    try:
                        inv_id = int(vInventoryID) if not isinstance(vInventoryID, int) else vInventoryID
                    except (TypeError, ValueError):
                        inv_id = 1
                    try:
                        rl = int(roundLength)
                    except Exception:
                        rl = 900
                    if rl < 60:
                        rl = rl * 60
                    _TRAINING_ROOM['veh_inv_id'] = inv_id
                    _TRAINING_ROOM['id'] = int(_TRAINING_ROOM.get('id') or 0) + 1
                    _TRAINING_ROOM['keep'] = True
                    _TRAINING_ROOM['settings']['arenaTypeID'] = arenaTypeID
                    _TRAINING_ROOM['settings']['roundLength'] = rl
                    _TRAINING_ROOM['settings']['isPrivate'] = isPrivate
                    _TRAINING_ROOM['settings']['comment'] = comment or ''
                    rid = _TRAINING_ROOM['id']
                    g_playerEvents.onTrainingJoined(rid)
                    roster = _training_make_roster(force_new=True)
                    BigWorld.callback(0.05, lambda: _training_push_settings(self))
                    BigWorld.callback(0.1, lambda: g_playerEvents.onTrainingRosterChanged(roster))
                    print "[OFFLINE] training_create: id=%d arena=%s round=%ds roster=%d" % (
                        rid, arenaTypeID, rl, len(roster))

                def training_join(self, roomId, vInventoryID):
                    try:
                        _TRAINING_ROOM['veh_inv_id'] = int(vInventoryID)
                    except Exception:
                        pass
                    try:
                        _TRAINING_ROOM['id'] = int(roomId)
                    except Exception:
                        pass
                    _TRAINING_ROOM['keep'] = True
                    g_playerEvents.onTrainingJoined(_TRAINING_ROOM['id'])
                    roster = _training_make_roster(force_new=False)
                    BigWorld.callback(0.05, lambda: _training_push_settings(self))
                    BigWorld.callback(0.1, lambda: g_playerEvents.onTrainingRosterChanged(roster))

                def training_leave(self):
                    _TRAINING_ROOM['keep'] = False
                    _TRAINING_ROOM['id'] = 0
                    _TRAINING_ROOM['roster'] = {}
                    p.trainingRoster = None
                    g_playerEvents.onTrainingLeft()

                def training_destroy(self):
                    _TRAINING_ROOM['keep'] = False
                    _TRAINING_ROOM['id'] = 0
                    _TRAINING_ROOM['roster'] = {}
                    p.trainingRoster = None
                    g_playerEvents.onTrainingLeft()

                def training_startArena(self):
                    g_playerEvents.onArenaCreated()
                    try:
                        global _selected_arena
                        _arena_id = _TRAINING_ROOM['settings'].get('arenaTypeID', '05_prohorovka')
                        try:
                            _prb = getattr(self, 'prebattle', None) or getattr(p, 'prebattle', None)
                            if _prb is not None and getattr(_prb, 'settings', None):
                                _aid = _prb.settings.get('arenaTypeID')
                                if _aid is not None:
                                    _arena_id = _aid
                        except Exception:
                            pass
                        _selected_arena = _arena_id
                        _TRAINING_ROOM['keep'] = True
                        try:
                            from CurrentVehicle import g_currentVehicle
                            if g_currentVehicle.vehicle is not None:
                                _TRAINING_ROOM['veh_inv_id'] = g_currentVehicle.vehicle.inventoryId
                        except Exception as _cve:
                            print "[OFFLINE][TRAINING] current vehicle read failed: %s" % _cve
                        _bots = []
                        _player_team = 1
                        try:
                            _my = p.name
                            _pid, _dbid = _prb_player_ids()
                            _prb = getattr(p, 'prebattle', None)
                            _src = {}
                            if _prb is not None and getattr(_prb, 'rosters', None):
                                for _rid, _members in _prb.rosters.items():
                                    _src[_rid] = _members
                            if not _src:
                                _src = {1: _TRAINING_ROOM.get('roster') or {}}
                            for _rid, _members in _src.items():
                                try:
                                    _rid_i = int(_rid)
                                except Exception:
                                    continue
                                if _rid_i not in (1, 2):
                                    continue
                                for _mid, _info in _members.items():
                                    if not isinstance(_info, dict):
                                        continue
                                    _is_me = (_info.get('name') == _my) or (int(_mid) == int(_pid))
                                    if _is_me:
                                        _player_team = _rid_i
                                        continue
                                    _bcd = _info.get('vehCompDescr') or _prb_veh_cd()
                                    if not _bcd:
                                        continue
                                    _bots.append((
                                        _info.get('name') or 'Bot',
                                        _bcd,
                                        _rid_i
                                    ))
                        except Exception:
                            _bots = []
                        _round = _TRAINING_ROOM['settings'].get('roundLength', 900)
                        from Offline import BattleStarter as _BS
                        BigWorld.callback(0.3, lambda: _BS.start_offline_battle(
                            _arena_id, botCount=0, trainingMode=True,
                            trainingBots=_bots, roundLength=_round, playerTeam=_player_team))
                        print "[OFFLINE][TRAINING] starting training battle: arena=%s veh_inv=%d bots=%d team=%d round=%ds" % (
                            _arena_id, _TRAINING_ROOM['veh_inv_id'], len(_bots), _player_team, _round)
                    except Exception as e:
                        print "[OFFLINE][TRAINING] start failed: %s" % e

                def training_suspend(self):
                    _TRAINING_ROOM['keep'] = True

                def training_changeSettings(self, roundLength, lifetime, isPrivate, callback=None):
                    try:
                        rl = int(roundLength)
                    except Exception:
                        rl = _TRAINING_ROOM['settings'].get('roundLength', 900)
                    if rl < 60:
                        rl = rl * 60
                    _TRAINING_ROOM['settings']['roundLength'] = rl
                    _TRAINING_ROOM['settings']['isPrivate'] = isPrivate
                    BigWorld.callback(0.05, lambda: _training_push_settings(self))
                    if callback: BigWorld.callback(0.1, lambda: callback(0))

                def training_changeComment(self, comment, callback=None):
                    _TRAINING_ROOM['settings']['comment'] = comment or ''
                    BigWorld.callback(0.05, lambda: _training_push_settings(self))
                    if callback: BigWorld.callback(0.1, lambda: callback(0))

                def training_changeArenaType(self, arenaTypeID, callback=None):
                    _TRAINING_ROOM['settings']['arenaTypeID'] = arenaTypeID
                    BigWorld.callback(0.05, lambda: _training_push_settings(self))
                    if callback: BigWorld.callback(0.1, lambda: callback(0))

                def training_assignToTeam(self, id, team, callback=None):
                    try:
                        roster = _TRAINING_ROOM.get('roster') or {}
                        if int(id) in roster:
                            roster[int(id)]['team'] = int(team)
                        _TRAINING_ROOM['roster'] = roster
                        p.trainingRoster = roster
                        BigWorld.callback(0.05, lambda: g_playerEvents.onTrainingRosterChanged(roster))
                    except Exception as e:
                        print "[OFFLINE] training_assignToTeam error: %s" % e
                    if callback: BigWorld.callback(0.1, lambda: callback(0))

                def requestTrainingList(self):
                    BigWorld.callback(0.1, lambda: g_playerEvents.onTrainingListReceived({}))

                p.training_create        = new.instancemethod(training_create,        p, p.__class__)
                p.training_join          = new.instancemethod(training_join,          p, p.__class__)
                p.training_leave         = new.instancemethod(training_leave,         p, p.__class__)
                p.training_destroy       = new.instancemethod(training_destroy,       p, p.__class__)
                p.training_startArena    = new.instancemethod(training_startArena,    p, p.__class__)
                p.training_suspend       = new.instancemethod(training_suspend,       p, p.__class__)
                p.training_changeSettings  = new.instancemethod(training_changeSettings,  p, p.__class__)
                p.training_changeComment   = new.instancemethod(training_changeComment,   p, p.__class__)
                p.training_changeArenaType = new.instancemethod(training_changeArenaType, p, p.__class__)
                p.training_assignToTeam    = new.instancemethod(training_assignToTeam,    p, p.__class__)
                p.requestTrainingList      = new.instancemethod(requestTrainingList,      p, p.__class__)

                def _prb_player_ids():
                    try:
                        pid = int(getattr(p, 'id', 1) or 1)
                    except Exception:
                        pid = 1
                    try:
                        dbid = int(getattr(p, 'databaseID', pid) or pid)
                    except Exception:
                        dbid = pid
                    return pid, dbid

                def _prb_veh_cd():
                    try:
                        from CurrentVehicle import g_currentVehicle as _gcv
                        if _gcv.vehicle is not None:
                            return _gcv.vehicle.descriptor.makeCompactDescr()
                    except Exception:
                        pass
                    inv_id = _TRAINING_ROOM.get('veh_inv_id') or 1
                    return veh_cds.get(inv_id, '')

                def _arena_type_name(arenaTypeID):
                    rid = _resolve_arena_type_id(arenaTypeID)
                    try:
                        import ArenaType as _AT
                        if rid is not None and rid in _AT.g_list:
                            return _AT.g_list[rid]
                    except Exception:
                        pass
                    return arenaTypeID

                def _join_client_prebattle(prb_type, extra_settings, ready=False, extra_members=None):
                    import ClientPrebattle
                    import constants as _c
                    pid, dbid = _prb_player_ids()
                    prb_id = int(_TRAINING_ROOM.get('id') or 0) + 1
                    _TRAINING_ROOM['id'] = prb_id
                    prb = ClientPrebattle.ClientPrebattle(prb_id)
                    veh_cd = _prb_veh_cd()
                    state = _c.PREBATTLE_ACCOUNT_STATE.READY if ready else _c.PREBATTLE_ACCOUNT_STATE.NOT_READY
                    creator_role = _c.PREBATTLE_ROLE.SQUAD_CREATOR
                    default_role = _c.PREBATTLE_ROLE.SQUAD_DEFAULT
                    if prb_type == _c.PREBATTLE_TYPE.TRAINING:
                        creator_role = _c.PREBATTLE_ROLE.TRAINING_CREATOR
                        default_role = _c.PREBATTLE_ROLE.TRAINING_DEFAULT
                    elif prb_type == _c.PREBATTLE_TYPE.TEAM:
                        creator_role = _c.PREBATTLE_ROLE.TEAM_CREATOR
                        default_role = _c.PREBATTLE_ROLE.TEAM_DEFAULT
                    settings = {
                        'type': prb_type,
                        'creator': p.name,
                        'roles': {dbid: creator_role},
                        'defaultRole': default_role,
                        'defaultRoster': 1,
                        'comment': '',
                        'isOpened': True,
                        'roundLength': 900,
                        'useArenaVoip': False,
                        'limit_max_count': {1: 3, 2: 3},
                    }
                    if extra_settings:
                        settings.update(extra_settings)
                    prb.settings = settings
                    member = {
                        'name': p.name,
                        'dbID': dbid,
                        'state': state,
                        'time': 0,
                        'vehCompDescr': veh_cd,
                        'clanDBID': 0,
                        'clanAbbrev': '',
                    }
                    prb.rosters = {1: {pid: member}}
                    if extra_members:
                        for roster_id, members in extra_members.items():
                            prb.rosters.setdefault(roster_id, {}).update(members)
                    prb.teamStates = [None, _c.PREBATTLE_TEAM_STATE.NOT_READY, _c.PREBATTLE_TEAM_STATE.NOT_READY]
                    p.prebattle = prb
                    print "[OFFLINE][PRB] joined type=%s id=%d ready=%s" % (prb_type, prb_id, ready)
                    g_playerEvents.onPrebattleJoined()
                    def _fire_prb(_prb=prb):
                        try:
                            _prb.onSettingsReceived()
                        except Exception as e:
                            print "[OFFLINE][PRB] onSettingsReceived: %s" % e
                        try:
                            _prb.onRosterReceived()
                        except Exception as e:
                            print "[OFFLINE][PRB] onRosterReceived: %s" % e
                    BigWorld.callback(0.05, _fire_prb)
                    return prb

                def prb_createSquad(self):
                    try:
                        import constants as _c
                        from chat_shared import CHAT_CHANNEL_SQUAD, CHAT_CHANNEL_PREBATTLE
                        from helpers.i18n import makeString as _ms
                        global _keep_squad_after_battle
                        _keep_squad_after_battle = True
                        pid, dbid = _prb_player_ids()
                        veh_cd = _prb_veh_cd()
                        bot = {
                            'name': 'SquadMate',
                            'dbID': dbid + 1,
                            'state': _c.PREBATTLE_ACCOUNT_STATE.READY,
                            'time': 0,
                            'vehCompDescr': veh_cd,
                            'clanDBID': 0,
                            'clanAbbrev': '',
                        }
                        _join_client_prebattle(
                            _c.PREBATTLE_TYPE.SQUAD, {}, ready=False,
                            extra_members={1: {pid + 1: bot}})
                        _open_prb_channel(CHAT_CHANNEL_SQUAD | CHAT_CHANNEL_PREBATTLE, _ms('#chat:channels/squad'))
                    except Exception as e:
                        print "[OFFLINE][PRB] createSquad failed: %s" % e
                        import traceback; traceback.print_exc()
                        g_playerEvents.onPrebattleJoinFailure(-1)

                def prb_createTeam(self, isOpened, comment):
                    try:
                        import constants as _c
                        _join_client_prebattle(_c.PREBATTLE_TYPE.TEAM, {
                            'isOpened': bool(isOpened),
                            'comment': comment or '',
                        }, ready=False)
                    except Exception as e:
                        print "[OFFLINE][PRB] createTeam failed: %s" % e
                        g_playerEvents.onPrebattleJoinFailure(-1)

                def prb_createTraining(self, arenaTypeID, roundLength, isOpened, comment):
                    try:
                        import constants as _c
                        try:
                            rl = int(roundLength)
                        except Exception:
                            rl = 900
                        if rl < 60:
                            rl = rl * 60
                        extra = {
                            'arenaTypeID': _resolve_arena_type_id(arenaTypeID) or arenaTypeID,
                            'roundLength': rl,
                            'isOpened': bool(isOpened),
                            'comment': comment or '',
                            'defaultRoster': 17,
                        }
                        pid, dbid = _prb_player_ids()
                        veh_cd = _prb_veh_cd()
                        bot = {
                            'name': 'Bot',
                            'dbID': dbid + 1,
                            'state': _c.PREBATTLE_ACCOUNT_STATE.READY,
                            'time': 0,
                            'vehCompDescr': veh_cd,
                            'clanDBID': 0,
                            'clanAbbrev': '',
                        }
                        try:
                            from CurrentVehicle import g_currentVehicle as _gcv
                            if _gcv.vehicle is not None:
                                _TRAINING_ROOM['veh_inv_id'] = _gcv.vehicle.inventoryId
                        except Exception:
                            pass
                        _TRAINING_ROOM['keep'] = True
                        _TRAINING_ROOM['settings']['arenaTypeID'] = _arena_type_name(arenaTypeID)
                        _TRAINING_ROOM['settings']['roundLength'] = rl
                        _TRAINING_ROOM['settings']['isPrivate'] = 0 if isOpened else 1
                        _TRAINING_ROOM['settings']['comment'] = comment or ''
                        _join_client_prebattle(
                            _c.PREBATTLE_TYPE.TRAINING, extra, ready=True,
                            extra_members={2: {pid + 1: bot}})
                    except Exception as e:
                        print "[OFFLINE][PRB] createTraining failed: %s" % e
                        import traceback; traceback.print_exc()
                        g_playerEvents.onPrebattleJoinFailure(-1)

                def prb_join(self, prebattleID):
                    try:
                        import constants as _c
                        extra = dict(_TRAINING_ROOM.get('settings') or {})
                        extra['arenaTypeID'] = extra.get('arenaTypeID')
                        _join_client_prebattle(_c.PREBATTLE_TYPE.TRAINING, extra, ready=True)
                    except Exception as e:
                        print "[OFFLINE][PRB] join failed: %s" % e
                        g_playerEvents.onPrebattleJoinFailure(-1)

                def prb_leave(self, callback=None):
                    try:
                        global _keep_squad_after_battle
                        _keep_squad_after_battle = False
                        _saved_prebattle[0] = None
                        p.prebattle = None
                        _TRAINING_ROOM['keep'] = False
                        _TRAINING_ROOM['id'] = 0
                        _close_prb_channel()
                        g_playerEvents.onPrebattleLeft()
                    except Exception as e:
                        print "[OFFLINE][PRB] leave failed: %s" % e
                    if callback:
                        BigWorld.callback(0.01, lambda: callback(0))

                def prb_ready(self, vehInvID, callback=None):
                    try:
                        import constants as _c
                        try:
                            _TRAINING_ROOM['veh_inv_id'] = int(vehInvID)
                        except Exception:
                            pass
                        prb = p.prebattle
                        pid, _dbid = _prb_player_ids()
                        if prb is not None:
                            member = None
                            roster_id = 1
                            for rid, roster in prb.rosters.items():
                                if pid in roster:
                                    member = roster[pid]
                                    roster_id = rid
                                    break
                            if member is not None:
                                member['state'] = _c.PREBATTLE_ACCOUNT_STATE.READY
                                try:
                                    member['vehCompDescr'] = veh_cds.get(int(vehInvID), member.get('vehCompDescr', ''))
                                except Exception:
                                    pass
                                prb.onPlayerStateChanged(pid, roster_id)
                    except Exception as e:
                        print "[OFFLINE][PRB] ready failed: %s" % e
                    if callback:
                        BigWorld.callback(0.01, lambda: callback(0))

                def prb_notReady(self, state, callback=None):
                    try:
                        import constants as _c
                        prb = p.prebattle
                        pid, _dbid = _prb_player_ids()
                        if prb is not None:
                            for rid, roster in prb.rosters.items():
                                if pid in roster:
                                    roster[pid]['state'] = state or _c.PREBATTLE_ACCOUNT_STATE.NOT_READY
                                    prb.onPlayerStateChanged(pid, rid)
                                    break
                    except Exception as e:
                        print "[OFFLINE][PRB] notReady failed: %s" % e
                    if callback:
                        BigWorld.callback(0.01, lambda: callback(0))

                def prb_assign(self, playerID, roster, callback=None):
                    try:
                        prb = p.prebattle
                        if prb is not None:
                            acc = None
                            prev = None
                            try:
                                pid = int(float(playerID))
                            except Exception:
                                pid = int(playerID)
                            for rid, members in prb.rosters.items():
                                if pid in members:
                                    acc = members.pop(pid)
                                    prev = rid
                                    break
                            if acc is None:
                                for rid, members in prb.rosters.items():
                                    for _k, _v in members.items():
                                        try:
                                            if int(_k) == pid:
                                                acc = members.pop(_k)
                                                prev = rid
                                                pid = _k
                                                break
                                        except Exception:
                                            continue
                                    if acc is not None:
                                        break
                            if acc is not None:
                                dest = int(float(roster))
                                if dest == 0:
                                    dest = int((prb.settings or {}).get('defaultRoster', 17) or 17)
                                if dest not in (1, 2, 17, 18):
                                    dest = 17
                                prb.rosters.setdefault(dest, {})[pid] = acc
                                try:
                                    roster_ui = {}
                                    for _rid, _members in prb.rosters.items():
                                        for _mid, _info in _members.items():
                                            if isinstance(_info, dict):
                                                _row = dict(_info)
                                                _row['team'] = int(_rid)
                                                roster_ui[int(_mid)] = _row
                                    _TRAINING_ROOM['roster'] = roster_ui
                                    p.trainingRoster = roster_ui
                                except Exception:
                                    pass
                                try:
                                    prb.onPlayerRosterChanged(pid, prev, dest)
                                except Exception:
                                    pass
                                try:
                                    prb.onRosterReceived()
                                except Exception:
                                    pass
                    except Exception as e:
                        print "[OFFLINE][PRB] assign failed: %s" % e
                    if callback:
                        BigWorld.callback(0.01, lambda: callback(0))

                def prb_changeArena(self, arenaTypeID, callback=None):
                    try:
                        prb = p.prebattle
                        resolved = _resolve_arena_type_id(arenaTypeID)
                        if resolved is None:
                            resolved = arenaTypeID
                        if prb is not None and prb.settings is not None:
                            prb.settings['arenaTypeID'] = resolved
                            _TRAINING_ROOM['settings']['arenaTypeID'] = _arena_type_name(resolved)
                            prb.onSettingUpdated('arenaTypeID')
                            prb.onSettingsReceived()
                    except Exception as e:
                        print "[OFFLINE][PRB] changeArena failed: %s" % e
                    if callback:
                        BigWorld.callback(0.01, lambda: callback(0))

                def prb_changeRoundLength(self, roundLength, callback=None):
                    try:
                        rl = int(roundLength)
                        if rl < 60:
                            rl = rl * 60
                        prb = p.prebattle
                        if prb is not None and prb.settings is not None:
                            prb.settings['roundLength'] = rl
                            _TRAINING_ROOM['settings']['roundLength'] = rl
                            prb.onSettingUpdated('roundLength')
                            prb.onSettingsReceived()
                    except Exception as e:
                        print "[OFFLINE][PRB] changeRoundLength failed: %s" % e
                    if callback:
                        BigWorld.callback(0.01, lambda: callback(0))

                def prb_changeOpenStatus(self, isOpened, callback=None):
                    try:
                        prb = p.prebattle
                        if prb is not None and prb.settings is not None:
                            prb.settings['isOpened'] = bool(isOpened)
                            prb.onSettingUpdated('isOpened')
                            prb.onSettingsReceived()
                    except Exception as e:
                        print "[OFFLINE][PRB] changeOpenStatus failed: %s" % e
                    if callback:
                        BigWorld.callback(0.01, lambda: callback(0))

                def prb_changeComment(self, comment, callback=None):
                    try:
                        prb = p.prebattle
                        if prb is not None and prb.settings is not None:
                            prb.settings['comment'] = comment or ''
                            prb.onSettingUpdated('comment')
                            prb.onSettingsReceived()
                    except Exception as e:
                        print "[OFFLINE][PRB] changeComment failed: %s" % e
                    if callback:
                        BigWorld.callback(0.01, lambda: callback(0))

                def prb_changeUseArenaVoip(self, useArenaVoip, callback=None):
                    try:
                        prb = p.prebattle
                        if prb is not None and prb.settings is not None:
                            prb.settings['useArenaVoip'] = bool(useArenaVoip)
                            prb.onSettingUpdated('useArenaVoip')
                    except Exception:
                        pass
                    if callback:
                        BigWorld.callback(0.01, lambda: callback(0))

                def prb_teamReady(self, team, force, callback=None):
                    try:
                        import constants as _c
                        prb = p.prebattle
                        if prb is not None:
                            prb.teamStates[int(team)] = _c.PREBATTLE_TEAM_STATE.READY
                            prb.onTeamStatesReceived()
                            if AccountPrebattle_is_training():
                                if (prb.teamStates[1] == _c.PREBATTLE_TEAM_STATE.READY and
                                        prb.teamStates[2] == _c.PREBATTLE_TEAM_STATE.READY):
                                    p.training_startArena()
                            elif AccountPrebattle_is_squad():
                                _lock_current_vehicle(AccountCommands.LOCK_REASON.IN_QUEUE)
                                p.isInQueue = True
                                handle = BigWorld.callback(3.0, _do_start_battle)
                                _battle_cb_handle[:] = [handle]
                    except Exception as e:
                        print "[OFFLINE][PRB] teamReady failed: %s" % e
                        import traceback; traceback.print_exc()
                    if callback:
                        BigWorld.callback(0.01, lambda: callback(0))

                def prb_teamNotReady(self, team, callback=None):
                    try:
                        import constants as _c
                        prb = p.prebattle
                        if prb is not None:
                            prb.teamStates[int(team)] = _c.PREBATTLE_TEAM_STATE.NOT_READY
                            prb.onTeamStatesReceived()
                    except Exception:
                        pass
                    if callback:
                        BigWorld.callback(0.01, lambda: callback(0))

                def AccountPrebattle_is_training():
                    try:
                        from account_helpers.AccountPrebattle import AccountPrebattle as _AP
                        return _AP.isTraining()
                    except Exception:
                        return False

                def AccountPrebattle_is_squad():
                    try:
                        from account_helpers.AccountPrebattle import AccountPrebattle as _AP
                        return _AP.isSquad()
                    except Exception:
                        return False

                def fake_requestPrebattles(self, type, sort_key, idle, start, end):
                    try:
                        g_playerEvents.onPrebattlesListReceived(type, 0, [])
                    except Exception as e:
                        print "[OFFLINE][PRB] requestPrebattles failed: %s" % e

                def fake_requestPrebattlesByName(self, type, idle, creatorMask):
                    fake_requestPrebattles(self, type, 0, idle, 0, 0)

                p.prb_createSquad = new.instancemethod(prb_createSquad, p, p.__class__)
                p.prb_createTeam = new.instancemethod(prb_createTeam, p, p.__class__)
                p.prb_createTraining = new.instancemethod(prb_createTraining, p, p.__class__)
                p.prb_join = new.instancemethod(prb_join, p, p.__class__)
                p.prb_leave = new.instancemethod(prb_leave, p, p.__class__)
                p.prb_ready = new.instancemethod(prb_ready, p, p.__class__)
                p.prb_notReady = new.instancemethod(prb_notReady, p, p.__class__)
                p.prb_assign = new.instancemethod(prb_assign, p, p.__class__)
                p.prb_changeArena = new.instancemethod(prb_changeArena, p, p.__class__)
                p.prb_changeRoundLength = new.instancemethod(prb_changeRoundLength, p, p.__class__)
                p.prb_changeOpenStatus = new.instancemethod(prb_changeOpenStatus, p, p.__class__)
                p.prb_changeComment = new.instancemethod(prb_changeComment, p, p.__class__)
                p.prb_changeUseArenaVoip = new.instancemethod(prb_changeUseArenaVoip, p, p.__class__)
                p.prb_teamReady = new.instancemethod(prb_teamReady, p, p.__class__)
                p.prb_teamNotReady = new.instancemethod(prb_teamNotReady, p, p.__class__)
                p.requestPrebattles = new.instancemethod(fake_requestPrebattles, p, p.__class__)
                p.requestPrebattlesByName = new.instancemethod(fake_requestPrebattlesByName, p, p.__class__)

                p.stats._Stats__cache = off_stats
                _apply_account_premium(p, off_stats)

                def fake_requestServerStats(self):
                    _emit_server_stats()

                def fake_receiveServerStats(self, stats):
                    try:
                        if not stats:
                            _emit_server_stats()
                            return
                        clean = {}
                        for k in ('playersCount', 'arenasCount', 'playersInArenaCount'):
                            try:
                                clean[k] = int(dict(stats).get(k, 0) or 0)
                            except Exception:
                                clean[k] = 0
                        g_playerEvents.onServerStatsReceived(clean)
                    except Exception:
                        _emit_server_stats()

                p.requestServerStats = new.instancemethod(fake_requestServerStats, p, p.__class__)
                p.receiveServerStats = new.instancemethod(fake_receiveServerStats, p, p.__class__)
                p.inventory._Inventory__cache = {
                    'items':         dict((cd, int(cnt)) for cd, cnt in _modules_inventory.iteritems() if int(cnt or 0) > 0),
                    'compDescr':     inv_vehicles,
                    'vehicles':      inv_data,
                    'tankmen':       t_cache,
                    'potapovQuests': {},
                    _VEHICLE_IDX:    _as_vehicle_inv_dict(inv_data),
                    _TANKMAN_IDX:    _as_tankman_inv_dict((t_cache, t_in_veh)),
                }

                try:
                    dc = getattr(p, 'dossierCache', None)
                    if dc is not None:
                        def fake_dc_get(self, dossierType, ownerID, callback=None):
                            import constants as _cdc
                            data = ''
                            try:
                                if dossierType == _cdc.DOSSIER_TYPE.VEHICLE:
                                    d = _veh_type_dossiers.get(int(ownerID))
                                    if d is not None:
                                        data = d.makeCompDescr()
                            except Exception:
                                data = ''
                            if callback:
                                try:
                                    callback(AccountCommands.RES_CACHE, data)
                                except TypeError:
                                    callback(data)
                        def fake_dc_sync(self, *a, **k):
                            try:
                                self._DossierCache__isSynchronizing = False
                                self._DossierCache__ignore = False
                            except Exception:
                                pass
                        dc.get = new.instancemethod(fake_dc_get, dc, dc.__class__)
                        dc.synchronize = new.instancemethod(fake_dc_sync, dc, dc.__class__)
                        dc.resynchronize = new.instancemethod(fake_dc_sync, dc, dc.__class__)
                        try:
                            dc._DossierCache__isSynchronizing = False
                            dc._DossierCache__ignore = False
                        except Exception:
                            pass
                except Exception as _dce:
                    print "[OFFLINE] dossierCache patch failed: %s" % _dce

                global _account_prefs_root, _favorites_persist_hooked
                if _account_prefs_root is None:
                    _account_prefs_root = _MemSection()
                AS.AccountSettings._AccountSettings__readUserSection = staticmethod(
                    lambda *a: _account_prefs_root)
                if not _favorites_persist_hooked:
                    _orig_set_fav = AS.AccountSettings.setFavorites
                    def _offline_set_favorites(name, value):
                        _orig_set_fav(name, value)
                        try:
                            if _off_stats_ref is None:
                                return
                            if name == 'current':
                                _off_stats_ref['currentVehInvID'] = int(value)
                            elif name == 'vehicles':
                                _off_stats_ref['favoriteVehicles'] = list(value or [])
                            _save_profile(force=True)
                        except Exception:
                            pass
                    AS.AccountSettings.setFavorites = staticmethod(_offline_set_favorites)
                    _favorites_persist_hooked = True
                try:
                    _favs = list(off_stats.get('favoriteVehicles') or [])
                    AS.AccountSettings.setFavorites('vehicles', _favs)
                    _cur_fav = int(off_stats.get('currentVehInvID') or 0)
                    if _cur_fav:
                        AS.AccountSettings.setFavorites('current', _cur_fav)
                except Exception:
                    pass

                if veh_cds:
                    _cur = off_stats.get('currentVehInvID')
                    if _cur not in veh_cds:
                        _cur = min(veh_cds.keys())
                    CurrentVehicle.g_currentVehicle._CurrentVehicle__vehicle = OfflineVehicleWrapper(_cur)

            from gui.WindowsManager import g_windowsManager
            g_windowsManager.showLobby()
            BigWorld.callback(0.2, _patch_common_page_later)

            try:
                from gui.Scaleform.Waiting import Waiting
                Waiting.hide()
            except: pass

            def refresh_gui():
                g_playerEvents.onStatsResync()
                g_playerEvents.onInventoryResync()
                print "[OFFLINE] Garage ready: %d tanks, %d modules" % (len(veh_cds), len(_modules_inventory))

            BigWorld.callback(0.5, refresh_gui)

        except Exception:
            traceback.print_exc()

    connectionManager.connect = fake_connect
    print "[OFFLINE] Ready"

def apply_battle_rewards(earned_credits, earned_xp, veh_inv_id, account_player=None):

    import BigWorld
    from PlayerEvents import g_playerEvents
    try:
        p = account_player
        if p is None:
            p = BigWorld.player()
        if not hasattr(p, 'stats'):
            print "[OFFLINE][REWARD] player has no stats attr, type=%s" % type(p).__name__
            return

        cache = p.stats._Stats__cache

        _is_winner  = getattr(p, '_last_battle_winner', False)
        _frags_d    = getattr(p, '_last_battle_frags', 0) or 0
        _dmg_d      = getattr(p, '_last_battle_dmg', 0) or 0
        try:
            _fb_spots = getattr(p, '_last_battle_spots', 0) or 0
        except Exception:
            _fb_spots = 0
        _fb_mult = 1.5 if _is_winner else 1.0
        calc_xp = int((float(_dmg_d) * 0.12 + float(_frags_d) * 40.0 + float(_fb_spots) * 30.0) * _fb_mult)
        calc_credits = int(float(_dmg_d) * 9.0 + float(_frags_d) * 150.0 + float(_fb_spots) * 100.0)
        if calc_xp < 0: calc_xp = 0
        if calc_credits < 0: calc_credits = 0

        if not earned_xp:
            earned_xp = calc_xp
        if not earned_credits:
            earned_credits = calc_credits
        try:
            earned_xp = int(earned_xp)
            earned_credits = int(earned_credits)
        except Exception:
            pass

        cache['credits'] = cache.get('credits', 0) + earned_credits
        print "[OFFLINE][REWARD] credits +%d = %d" % (earned_credits, cache['credits'])
        off = _off_stats_ref
        if off is not None:
            off['credits'] = off.get('credits', 0) + earned_credits
            print "[OFFLINE][REWARD] off_stats credits = %d" % off['credits']

        try:
            from items import vehicles as _vehicles
            inv_cache = p.inventory._Inventory__cache
            veh_comp_descr_map = {}
            try:
                veh_list = inv_cache.get('vehicles', [])
                if veh_list and isinstance(veh_list[0], dict):
                    veh_comp_descr_map = veh_list[0]
            except Exception:
                pass
            if veh_inv_id not in veh_comp_descr_map:
                try:
                    veh_comp_descr_map = inv_cache.get('compDescr', {})
                except Exception:
                    pass

            veh_comp_descr = veh_comp_descr_map.get(veh_inv_id, None)
            if veh_comp_descr is not None:
                nID, vID = _vehicles.parseVehicleCompactDescr(veh_comp_descr)
                type_cd = _vehicles.makeIntCompactDescrByID('vehicle', nID, vID)
                if 'vehTypeXP' not in cache:
                    cache['vehTypeXP'] = {}
                old_xp = cache['vehTypeXP'].get(type_cd, 0)
                cache['vehTypeXP'][type_cd] = old_xp + earned_xp
                print "[OFFLINE][REWARD] veh inv=%d typeCD=%d xp +%d = %d" % (
                    veh_inv_id, type_cd, earned_xp, old_xp + earned_xp)
                if off is not None:
                    off.setdefault('vehTypeXP', {})[type_cd] = off.get('vehTypeXP', {}).get(type_cd, 0) + earned_xp
                    print "[OFFLINE][REWARD] off_stats vehTypeXP[%d] = %d" % (
                        type_cd, off['vehTypeXP'].get(type_cd, 0))
            else:
                print "[OFFLINE][REWARD] vehCompDescr not found for invID=%d, veh_map keys=%s" % (
                    veh_inv_id, list(veh_comp_descr_map.keys())[:5])

            try:
                from items.tankmen import TankmanDescr as _TMD
                from items import vehicles as _vcrew
                _crew_ids = []
                try:
                    if _inv_data_ref and veh_inv_id in _inv_data_ref[3]:
                        _crew_ids = list(_inv_data_ref[3][veh_inv_id] or [])
                except Exception:
                    _crew_ids = []
                if not _crew_ids and _crew_map_ref:
                    _crew_ids = list(_crew_map_ref.get(veh_inv_id) or [])
                _vtype = None
                if veh_comp_descr is not None:
                    _vtype = _vcrew.VehicleDescr(compactDescr=veh_comp_descr).type
                _surv = getattr(p, '_last_battle_survived', True)
                _tcache = _t_cache_ref if _t_cache_ref is not None else {}
                for _tid in _crew_ids:
                    if _tid is None:
                        continue
                    _tcd = _tcache.get(int(_tid))
                    if not _tcd:
                        continue
                    _td = _TMD(_tcd)
                    _txp = earned_xp
                    if _vtype is not None:
                        _txp = _td.battleXpGain(int(earned_xp), _vtype, _surv)
                    _td.addXP(int(_txp))
                    _tcache[int(_tid)] = _td.makeCompactDescr()
                    print "[OFFLINE][TMAN_XP] tid=%s xp+%d role=%d lastSkill=%d skills=%s" % (
                        _tid, _txp, _td.roleLevel, _td.lastSkillLevel, list(_td.skills))
            except Exception as _cx:
                print "[OFFLINE][TMAN_XP] failed: %s" % _cx
                traceback.print_exc()
        except Exception as _xp_ex:
            import traceback
            print "[OFFLINE][REWARD] tank xp failed: %s" % _xp_ex
            traceback.print_exc()

        try:
            free_xp_bonus = int(earned_xp)
        except Exception:
            free_xp_bonus = 0
        if free_xp_bonus < 0:
            free_xp_bonus = 0
        cache['freeXP'] = cache.get('freeXP', 0) + free_xp_bonus
        print "[OFFLINE][REWARD] freeXP +%d = %d" % (free_xp_bonus, cache['freeXP'])
        if off is not None:
            off['freeXP'] = off.get('freeXP', 0) + free_xp_bonus
            print "[OFFLINE][REWARD] off_stats freeXP = %d" % off['freeXP']

        try:
            global _account_dossier, _veh_type_dossiers
            import dossiers as _dossiers
            import time as _time_doss

            if _account_dossier is None:
                import time as _t2
                _account_dossier = _dossiers.getAccountDossierDescr('')
                _account_dossier['creationTime'] = int(_t2.time())

            _type_cd_doss = None
            try:
                from items import vehicles as _v2
                _inv2 = p.inventory._Inventory__cache
                _vmap2 = {}
                try:
                    _vl2 = _inv2.get('vehicles', [])
                    if _vl2 and isinstance(_vl2[0], dict):
                        _vmap2 = _vl2[0]
                except Exception:
                    _vmap2 = _inv2.get('compDescr', {})
                _vcd2 = _vmap2.get(veh_inv_id)
                if _vcd2:
                    _nid2, _vid2 = _v2.parseVehicleCompactDescr(_vcd2)
                    _type_cd_doss = _v2.makeIntCompactDescrByID('vehicle', _nid2, _vid2)
            except Exception as _tce:
                print "[OFFLINE][DOSSIER] typeCD lookup failed: %s" % _tce

            _is_winner  = getattr(p, '_last_battle_winner', False)
            _is_draw    = getattr(p, '_last_battle_is_draw', False)
            _survived   = getattr(p, '_last_battle_survived', False)
            _frags_d    = getattr(p, '_last_battle_frags', 0)
            _dmg_d      = getattr(p, '_last_battle_dmg', 0)
            _shots_d    = getattr(p, '_last_battle_shots', 0)
            _hits_d     = getattr(p, '_last_battle_hits', 0)
            _dmg_recv_d = getattr(p, '_last_battle_dmg_recv', 0)
            _now_t      = int(_time_doss.time())

            def _dget(d, key):
                try:
                    return d[key]
                except Exception:
                    return 0

            _account_dossier['battlesCount']   = _dget(_account_dossier, 'battlesCount') + 1
            _account_dossier['xp']             = _dget(_account_dossier, 'xp') + earned_xp
            _account_dossier['maxXP']          = max(_dget(_account_dossier, 'maxXP'), earned_xp)
            _account_dossier['frags']          = _dget(_account_dossier, 'frags') + _frags_d
            _account_dossier['shots']          = _dget(_account_dossier, 'shots') + _shots_d
            _account_dossier['hits']           = _dget(_account_dossier, 'hits') + _hits_d
            _account_dossier['damageDealt']    = _dget(_account_dossier, 'damageDealt') + _dmg_d
            _account_dossier['damageReceived'] = _dget(_account_dossier, 'damageReceived') + _dmg_recv_d
            try:
                _account_dossier['spotted'] = _dget(_account_dossier, 'spotted') + int(_fb_spots or 0)
            except Exception:
                pass
            _account_dossier['lastBattleTime'] = _now_t
            if _is_winner:
                _account_dossier['wins'] = _dget(_account_dossier, 'wins') + 1
                if _survived:
                    _account_dossier['winAndSurvived'] = _dget(_account_dossier, 'winAndSurvived') + 1
            elif _is_draw:
                pass
            else:
                _account_dossier['losses'] = _dget(_account_dossier, 'losses') + 1
            if _survived:
                _account_dossier['survivedBattles'] = _dget(_account_dossier, 'survivedBattles') + 1
            try:
                if _type_cd_doss is not None:
                    _cuts = {}
                    for _ck, _cv in (_account_dossier['vehDossiersCut'] or {}).items():
                        try:
                            _ck = int(_ck)
                        except Exception:
                            pass
                        _cuts[_ck] = _cv
                    _bc, _wins = _cuts.get(_type_cd_doss, (0, 0))
                    if _is_winner:
                        _wins = _wins + 1
                    _cuts[_type_cd_doss] = (_bc + 1, _wins)
                    _account_dossier['vehDossiersCut'] = _cuts
            except Exception as _cut_e:
                print "[OFFLINE][DOSSIER] vehDossiersCut failed: %s" % _cut_e

            if _type_cd_doss is not None:
                if _type_cd_doss not in _veh_type_dossiers:
                    _veh_type_dossiers[_type_cd_doss] = _dossiers.getVehicleDossierDescr('')
                _vd = _veh_type_dossiers[_type_cd_doss]
                _vd['battlesCount']   = _dget(_vd, 'battlesCount') + 1
                _vd['xp']             = _dget(_vd, 'xp') + earned_xp
                _vd['maxXP']          = max(_dget(_vd, 'maxXP'), earned_xp)
                _vd['frags']          = _dget(_vd, 'frags') + _frags_d
                _vd['shots']          = _dget(_vd, 'shots') + _shots_d
                _vd['hits']           = _dget(_vd, 'hits') + _hits_d
                _vd['damageDealt']    = _dget(_vd, 'damageDealt') + _dmg_d
                _vd['damageReceived'] = _dget(_vd, 'damageReceived') + _dmg_recv_d
                _vd['lastBattleTime'] = _now_t
                if _is_winner:
                    _vd['wins'] = _dget(_vd, 'wins') + 1
                    if _survived:
                        _vd['winAndSurvived'] = _dget(_vd, 'winAndSurvived') + 1
                elif _is_draw:
                    pass
                else:
                    _vd['losses'] = _dget(_vd, 'losses') + 1
                if _survived:
                    _vd['survivedBattles'] = _dget(_vd, 'survivedBattles') + 1

            try:
                _aids = getattr(p, '_last_battle_achiev_ids', []) or []
                if _aids:
                    import battle_heroes as _bh
                    _name_by_idx = dict((_i, _nm) for _i, _nm in enumerate(_bh.ACHIEVEMENT_NAMES))
                    _hdl = {}
                    for _i in _aids:
                        _nm = _name_by_idx.get(_i)
                        if _nm and _nm != 'reserved':
                            _hdl[_nm] = _hdl.get(_nm, 0) + 1
                    if _hdl:
                        for _nm2, _cnt in _hdl.items():
                            _account_dossier[_nm2] = _dget(_account_dossier, _nm2) + _cnt
                            if _type_cd_doss is not None and _type_cd_doss in _veh_type_dossiers:
                                _veh_type_dossiers[_type_cd_doss][_nm2] = _dget(_veh_type_dossiers[_type_cd_doss], _nm2) + _cnt
                        _account_dossier['battleHeroes'] = _dget(_account_dossier, 'battleHeroes') + 1
                        if _type_cd_doss is not None and _type_cd_doss in _veh_type_dossiers:
                            _veh_type_dossiers[_type_cd_doss]['battleHeroes'] = _dget(_veh_type_dossiers[_type_cd_doss], 'battleHeroes') + 1
                        print "[OFFLINE][DOSSIER] achievements: " + (' + '.join(['%s x%d' % (k, v) for k, v in _hdl.items()]) or '-')
            except Exception as _ae:
                print "[OFFLINE][DOSSIER] achievements update failed: %s" % _ae

            print "[OFFLINE][DOSSIER] updated: battles=%d wins=%d losses=%d xp=%d frags=%d dmg=%d (%s)" % (
                _dget(_account_dossier, 'battlesCount'),
                _dget(_account_dossier, 'wins'),
                _dget(_account_dossier, 'losses'),
                _dget(_account_dossier, 'xp'),
                _dget(_account_dossier, 'frags'),
                _dget(_account_dossier, 'damageDealt'),
                'WIN' if _is_winner else ('DRAW' if _is_draw else 'LOSS'),
            )
        except Exception as _de:
            import traceback
            print "[OFFLINE][DOSSIER] update failed: %s" % _de
            traceback.print_exc()
        BigWorld.callback(1.0, lambda: g_playerEvents.onStatsResync())
        BigWorld.callback(1.2, lambda: g_playerEvents.onInventoryResync())
        BigWorld.callback(1.3, _refresh_hangar_xp)
        try:
            if _inv_data_ref is not None and veh_inv_id:
                from AccountCommands import VEHICLE_SETTINGS_FLAG as _VSF
                _flags = int(_inv_data_ref[7].get(veh_inv_id, 0) or 0)
                _surv_h = getattr(p, '_last_battle_survived', False)
                _rc = 0.0
                _hp_left = 100
                if not _surv_h:
                    try:
                        from items import vehicles as _vrp
                        _vcd_r = None
                        if _inv_vehicles_ref:
                            _vcd_r = _inv_vehicles_ref.get(veh_inv_id)
                        if _vcd_r:
                            _rc = float(_vrp.VehicleDescr(compactDescr=_vcd_r).getMaxRepairCost())
                    except Exception:
                        _rc = 1.0
                    if _rc <= 0:
                        _rc = 1.0
                    _hp_left = 0
                if _flags & _VSF.AUTO_REPAIR:
                    try:
                        off_stats['credits'] = int(off_stats.get('credits', 0) or 0) - int(_rc)
                    except Exception:
                        pass
                    _rc = 0.0
                    _hp_left = 100
                _inv_data_ref[4][veh_inv_id] = (_rc, _hp_left)
                try:
                    from CurrentVehicle import g_currentVehicle as _gcv
                    if _gcv.vehicle is not None and int(_gcv.vehicle.inventoryId) == int(veh_inv_id):
                        _gcv.vehicle.repairCost = _rc
                        _gcv.vehicle.health = _hp_left
                        if _rc > 0:
                            _gcv.vehicle.modelState = 'destroyed' if _hp_left <= 0 else 'damaged'
                        else:
                            _gcv.vehicle.modelState = 'undamaged'
                        _gcv.onChanged()
                except Exception:
                    pass
                print "[OFFLINE][HANGAR] repairCost=%s hp=%s auto=%s" % (
                    _rc, _hp_left, bool(_flags & _VSF.AUTO_REPAIR))
        except Exception as _reph:
            print "[OFFLINE][HANGAR] post-battle repair state failed: %s" % _reph
        try:
            if veh_inv_id:
                _consume_used_equipments(veh_inv_id)
        except Exception as _eqe:
            print "[OFFLINE][HANGAR] consume equipment failed: %s" % _eqe
        _save_profile(force=True)
        print "[OFFLINE][REWARD] resync scheduled OK"

    except Exception as e:
        import traceback
        print "[OFFLINE][REWARD] apply_battle_rewards failed: %s" % e
        traceback.print_exc()

_lobby_restore_id = [0]

def restore_lobby():

    print "[OFFLINE] Restoring lobby..."
    import BigWorld
    _lobby_restore_id[0] += 1
    _my_restore = _lobby_restore_id[0]
    _restore_service_channel()
    try:
        g_playerEvents.isPlayerEntityChanging = False
        p = BigWorld.player()
        if p is not None:
            p.isInQueue = False
            if not (_TRAINING_ROOM.get('keep') or _keep_squad_after_battle):
                p.prebattle = None
            elif _saved_prebattle[0] is not None:
                p.prebattle = _saved_prebattle[0]
    except Exception:
        pass
    try:
        from CurrentVehicle import g_currentVehicle as _gcv
        if _gcv.vehicle is not None:
            _gcv.setLocked(AccountCommands.LOCK_REASON.NONE)
    except Exception:
        pass
    try:
        _is_prem = bool(_off_stats_ref.get('isPremium')) if _off_stats_ref else False
    except Exception:
        _is_prem = False
    try:
        from gui.Scaleform.utils.HangarSpace import g_hangarSpace
        if g_hangarSpace.spaceInited:
            try:
                _space = g_hangarSpace.space
                if _space is not None:
                    _eid = _space._ClientHangarSpace__vEntityId
                    _sid = _space._ClientHangarSpace__spaceId
                    if _eid is not None:
                        _ent = BigWorld.entity(_eid)
                        if _ent is not None:
                            try: BigWorld.delShadowEntity(_ent)
                            except: pass
                            _ent.model = None
                    if _sid is not None:
                        for _e in BigWorld.entities.values():
                            try:
                                if getattr(_e, 'spaceID', None) == _sid:
                                    BigWorld.delShadowEntity(_e)
                            except: pass
            except: pass
            g_hangarSpace.destroy()
        g_hangarSpace.init(_is_prem)
        print "[OFFLINE] g_hangarSpace.init(%s) called OK" % _is_prem
    except Exception as e:
        print "[OFFLINE] restore_lobby g_hangarSpace error:", e
        try:
            from gui.ClientHangarSpace import ClientHangarSpace
            _hs = ClientHangarSpace()
            _hs.create(_is_prem)
            print "[OFFLINE] restore_lobby ClientHangarSpace.create() fallback OK"
        except Exception as e2:
            print "[OFFLINE] restore_lobby ClientHangarSpace fallback error:", e2

    def _delayed_show():
        if _my_restore != _lobby_restore_id[0]:
            return
        try:
            _apply_player_name()
        except Exception:
            pass
        try:
            p = BigWorld.player()
            if p is not None and getattr(p, '_offline_battle', None) is not None:
                return
            if p is None or not hasattr(p, 'shop'):
                try:
                    if hasattr(BigWorld, '_orig_player_fn'):
                        BigWorld.player = BigWorld._orig_player_fn
                    p = BigWorld.player()
                except Exception:
                    pass
            if p is None or not hasattr(p, 'shop'):
                print "[OFFLINE] restore_lobby showLobby skipped: player has no shop"
                return
            from gui.WindowsManager import g_windowsManager
            _ctx = {}
            try:
                if _reopen_training_after_battle and _TRAINING_ROOM.get('keep') and _TRAINING_ROOM.get('id'):
                    _ctx = {
                        'trainingID': _TRAINING_ROOM['id'],
                        'isTrainingOwner': True,
                    }
            except Exception:
                _ctx = {}
            g_windowsManager.showLobby(_ctx)
            def _after_lobby_ui():
                try:
                    _restore_service_channel()
                except Exception:
                    pass
                try:
                    global _pending_battle_results
                    if _pending_battle_results:
                        _push_battle_results_notification(_pending_battle_results)
                        _pending_battle_results = None
                except Exception as _pbe:
                    print "[OFFLINE] pending battle notify failed: %s" % _pbe
            BigWorld.callback(1.0, _after_lobby_ui)
            if _ctx:
                print "[OFFLINE] restore_lobby: reopen training room id=%s" % _TRAINING_ROOM.get('id')
                def _reopen_training(tries=0):
                    try:
                        from gui.WindowsManager import g_windowsManager as _wm
                        w = getattr(_wm, 'window', None)
                        if w is None or getattr(w, 'movie', None) is None:
                            if tries < 20:
                                BigWorld.callback(0.2, lambda t=tries + 1: _reopen_training(t))
                            return
                        p = BigWorld.player()
                        if p is not None and _saved_prebattle[0] is not None:
                            p.prebattle = _saved_prebattle[0]
                        if p is not None and getattr(p, 'prebattle', None) is not None:
                            try:
                                g_playerEvents.onPrebattleJoined()
                            except Exception:
                                pass
                            try:
                                p.prebattle.onSettingsReceived()
                            except Exception:
                                pass
                            try:
                                p.prebattle.onRosterReceived()
                            except Exception:
                                pass
                        if w is not None and hasattr(w, 'movie') and w.movie is not None:
                            w.movie.invoke(('loadTraining',))
                    except Exception as _te:
                        print "[OFFLINE] restore_lobby loadTraining error:", _te
                BigWorld.callback(0.6, _reopen_training)
            elif _keep_squad_after_battle:
                def _reopen_squad():
                    try:
                        p = BigWorld.player()
                        if p is not None and _saved_prebattle[0] is not None:
                            p.prebattle = _saved_prebattle[0]
                            g_playerEvents.onPrebattleJoined()
                            try:
                                p.prebattle.onSettingsReceived()
                                p.prebattle.onRosterReceived()
                            except Exception:
                                pass
                    except Exception as _se:
                        print "[OFFLINE] restore_lobby squad restore error:", _se
                BigWorld.callback(0.6, _reopen_squad)
        except Exception as e:
            print "[OFFLINE] restore_lobby showLobby error:", e
        try:
            _apply_player_name()
        except Exception:
            pass
        BigWorld.worldDrawEnabled(True)
        from PlayerEvents import g_playerEvents
        BigWorld.callback(0.2, _apply_player_name)
        BigWorld.callback(0.5, lambda: g_playerEvents.onStatsResync())
        BigWorld.callback(0.7, lambda: g_playerEvents.onInventoryResync())
        BigWorld.callback(1.2, lambda: g_playerEvents.onStatsResync())
        BigWorld.callback(1.3, _apply_player_name)
        BigWorld.callback(1.4, _emit_server_stats)
        BigWorld.callback(1.5, _refresh_hangar_xp)
        def _rejoin_chats():
            try:
                p = BigWorld.player()
                if p is None:
                    return
                if hasattr(p, 'requestSystemChatChannels'):
                    p.requestSystemChatChannels()
                for cid, info in list(_lobby_chat_channels.items()):
                    if not info.get('system') and hasattr(p, 'enterChat'):
                        p.enterChat(cid)
            except Exception:
                pass
        BigWorld.callback(1.6, _rejoin_chats)

    BigWorld.callback(0.3, _delayed_show)

    BigWorld.worldDrawEnabled(True)

init_offline()

from gui.Scaleform.utils.gui_items import FittingItem
def _patch_fitting_item_level():
    old_level_fget = FittingItem.level.fget

    def safe_level_fget(self):
        try:
            return old_level_fget(self)
        except KeyError as e:
            if 'level' in str(e):
                return 0
            raise

    FittingItem.level = property(safe_level_fget)
_patch_fitting_item_level()
from gui.Scaleform.utils.gui_items import FittingItem

def _patch_common_page_later():
    try:
        import sys
        _mod = sys.modules.get('gui.Scaleform.CommonPage')
        _CP = getattr(_mod, 'CommonPage', None) if _mod is not None else None
        if _CP is None:
            BigWorld.callback(1.0, _patch_common_page_later)
            return
        if getattr(_CP, '_offline_name_patched', False):
            return
        _orig_process_lobby = _CP.processLobby
        def _process_lobby(self):
            _orig_process_lobby(self)
            try:
                _apply_player_name()
            except Exception:
                pass
            try:
                _emit_server_stats()
            except Exception:
                pass
            try:
                _refresh_hangar_xp()
            except Exception:
                pass
        _CP.processLobby = _process_lobby
        _orig_upd_acc = _CP.updateAccountInfo
        def _upd_acc(self):
            try:
                _apply_player_name()
            except Exception:
                pass
            ret = _orig_upd_acc(self)
            try:
                BigWorld.callback(0.05, _refresh_hangar_xp)
            except Exception:
                _refresh_hangar_xp()
            return ret
        _CP.updateAccountInfo = _upd_acc
        _orig_stats = _CP.onStatsReceived
        def _on_stats(self, stats):
            try:
                if not stats:
                    _emit_server_stats()
                    return
                clean = {}
                src = dict(stats)
                for k in ('playersCount', 'arenasCount', 'playersInArenaCount'):
                    try:
                        clean[k] = int(src.get(k, 0) or 0)
                    except Exception:
                        clean[k] = 0
                return _orig_stats(self, clean)
            except Exception:
                _emit_server_stats()
        _CP.onStatsReceived = _on_stats
        _CP._offline_name_patched = True
        print "[OFFLINE] CommonPage patch applied"
    except Exception as e:
        print "[OFFLINE] CommonPage delayed patch failed: %s" % e

def _apply_gui_fixes():
    _patch_sync_controller_fini()
    old_level_fget = FittingItem.level.fget
    def safe_level_fget(self):
        try:
            return old_level_fget(self)
        except (KeyError, AttributeError):
            return 0
    FittingItem.level = property(safe_level_fget)

    old_name_fget = FittingItem.name.fget

    def safe_name_fget(self):
        try:
            return old_name_fget(self)
        except (KeyError, AttributeError):
            if hasattr(self, 'descriptor') and hasattr(self.descriptor, 'name'):
                return self.descriptor.name
            return "Item %s" % str(getattr(self, 'compactDescr', 'Unknown'))

    FittingItem.name = property(safe_name_fget)

    old_longName_fget = FittingItem.longName.fget

    def safe_longName_fget(self):
        try:
            return old_longName_fget(self)
        except (KeyError, AttributeError):
            try:
                return self.name
            except:
                return "Unknown Item"

    FittingItem.longName = property(safe_longName_fget)
    try:
        from gui.Scaleform.graphs.data import VehiclesGraph
        _orig_vg_load = VehiclesGraph.load
        def _vg_load(self, nationId, unlocks=set(), elite=set(), experiences=None):
            if experiences is None:
                experiences = {}
            try:
                unlocks = _canonical_unlocks(unlocks)
            except Exception:
                unlocks = set()
            try:
                elite = _canonical_unlocks(elite)
            except Exception:
                elite = set()
            return _orig_vg_load(self, nationId, unlocks, elite, experiences)
        VehiclesGraph.load = _vg_load
    except Exception as e:
        print "[OFFLINE] VehiclesGraph.load patch failed: %s" % e
    try:
        from gui.Scaleform.graphs.data import VehicleComponentsGraph as _VCG
        _orig_vcg_load = _VCG.load
        _orig_auto = getattr(_VCG, '_VehicleComponentsGraph__buildListOfAutounlockedPoints', None)
        def _vcg_auto(self, vehicleType, vehicleTypeCompactDesc, unlocks):
            unlocks = _canonical_unlocks(unlocks)
            points = list(getattr(vehicleType, 'autounlockedItems', ()) or ())
            rootUnlocked = vehicleTypeCompactDesc in unlocks
            if len(points) > 2:
                points.pop(2)
            from gui.Scaleform.graphs.data import _VehicleComponentsPoint
            self.autounlockedPoints = [
                _VehicleComponentsPoint(point, -1, point in unlocks and rootUnlocked, 0, set())
                for point in points
            ]
        if _orig_auto is not None:
            _VCG._VehicleComponentsGraph__buildListOfAutounlockedPoints = _vcg_auto
        def _vcg_load(self, vehicleType, vehicleTypeCompactDesc, unlocks):
            try:
                unlocks = _canonical_unlocks(unlocks)
            except Exception:
                unlocks = set()
            return _orig_vcg_load(self, vehicleType, vehicleTypeCompactDesc, unlocks)
        _VCG.load = _vcg_load
    except Exception as e:
        print "[OFFLINE] VehicleComponentsGraph.load patch failed: %s" % e
    try:
        from gui.Scaleform.ConstructionDepartment import ConstructionDepartment as _CD
        _orig_cd_rd = _CD.onRequestData
        def _cd_on_request_data(self, *args):
            try:
                ul = _canonical_unlocks(_off_stats_ref.get('unlocks') if _off_stats_ref else None)
                if ul:
                    self._ConstructionDepartment__unlocks = ul
                xp = (_off_stats_ref.get('vehTypeXP') if _off_stats_ref else None) or {}
                self._ConstructionDepartment__experiences = xp
                el = _canonical_unlocks(_off_stats_ref.get('eliteVehicles') if _off_stats_ref else None)
                self._ConstructionDepartment__eliteVehicles = el
            except Exception:
                pass
            return _orig_cd_rd(self, *args)
        _CD.onRequestData = _cd_on_request_data
    except Exception as e:
        print "[OFFLINE] ConstructionDepartment onRequestData patch failed: %s" % e
    try:
        from gui.Scaleform.graphs.VehicleComponentsGraphInterface import VehicleComponentsGraphInterface as _VCGI
        _orig_pop_cur = _VCGI.onPopulateCurrentVehicle
        def _pop_cur(self, *args):
            if getattr(self, '_VehicleComponentsGraphInterface__vehicleType', None) is None:
                try:
                    from CurrentVehicle import g_currentVehicle
                    if g_currentVehicle.isPresent():
                        self.setVehicleType(g_currentVehicle.vehicle.descriptor.type)
                except Exception:
                    pass
            return _orig_pop_cur(self, *args)
        _VCGI.onPopulateCurrentVehicle = _pop_cur
        _orig_vcgi_rd = _VCGI.onRequestData
        def _vcgi_on_request_data(self, *args):
            try:
                g = self.graph
                pts = getattr(g, 'points', None)
                vt = getattr(self, '_VehicleComponentsGraphInterface__vehicleType', None)
                vcd = getattr(self, '_VehicleComponentsGraphInterface__vTypeCompactDesc', None)
                if vt is not None and not pts:
                    ul = _canonical_unlocks(_off_stats_ref.get('unlocks') if _off_stats_ref else None)
                    g.load(vt, vcd, ul)
            except Exception:
                pass
            return _orig_vcgi_rd(self, *args)
        _VCGI.onRequestData = _vcgi_on_request_data
        _orig_get_ul = _VCGI.onGetUnlocks
        def _on_get_unlocks(self, resultID, unlocks=None):
            if unlocks is None and not isinstance(resultID, (int, long)):
                unlocks = resultID
                resultID = 0
            try:
                unlocks = _canonical_unlocks(unlocks)
            except Exception:
                unlocks = set()
            return _orig_get_ul(self, resultID, unlocks)
        _VCGI.onGetUnlocks = _on_get_unlocks
    except Exception as e:
        print "[OFFLINE] VehicleComponentsGraphInterface patch failed: %s" % e
    try:
        _patch_common_page_later()
    except Exception as e:
        print "[OFFLINE] CommonPage name patch failed: %s" % e
        BigWorld.callback(0.5, _patch_common_page_later)
    try:
        from gui.Scaleform.utils.requesters import InventoryParser
        import types
        raw = InventoryParser.__dict__.get('parseVehicles')
        if isinstance(raw, staticmethod):
            _orig_parseVehicles = raw.__get__(None, InventoryParser)
        else:
            _orig_parseVehicles = InventoryParser.parseVehicles

        @staticmethod
        def _safe_parseVehicles(data):
            try:
                parsed = _orig_parseVehicles(_as_vehicle_inv_dict(data))
                print "[OFFLINE] parseVehicles: %d tanks" % len(parsed)
                return parsed
            except Exception as e:
                print "[OFFLINE] parseVehicles failed: %s" % e
                traceback.print_exc()
                return []

        InventoryParser.parseVehicles = _safe_parseVehicles
        raw_tm = InventoryParser.__dict__.get('parseTankmen')
        if isinstance(raw_tm, staticmethod):
            _orig_parseTankmen = raw_tm.__get__(None, InventoryParser)
        else:
            _orig_parseTankmen = InventoryParser.parseTankmen

        @staticmethod
        def _safe_parseTankmen(data):
            try:
                return _orig_parseTankmen(_as_tankman_inv_dict(data))
            except Exception as e:
                print "[OFFLINE] parseTankmen failed: %s" % e
                traceback.print_exc()
                return []

        InventoryParser.parseTankmen = _safe_parseTankmen
        from gui.Scaleform.utils.requesters import ShopParser
        _orig_shop_veh = ShopParser.parseVehicles
        _orig_shop_mod = ShopParser.parseModules

        def _shop_pair(data):
            if isinstance(data, (list, tuple)) and len(data) >= 2 and hasattr(data[0], 'items'):
                return data[0], data[1]
            if isinstance(data, dict):
                return data, set()
            return {}, set()

        @staticmethod
        def _safe_shop_vehicles(data, nationId):
            try:
                prices, hidden = _shop_pair(data)
                return _orig_shop_veh((prices, hidden), nationId)
            except Exception as e:
                print "[OFFLINE] shop.parseVehicles failed: %s" % e
                return []

        @staticmethod
        def _safe_shop_modules(data, itemTypeID, nationId):
            try:
                prices, hidden = _shop_pair(data)
                return _orig_shop_mod((prices, hidden), itemTypeID, nationId)
            except Exception as e:
                print "[OFFLINE] shop.parseModules failed: %s" % e
                return []

        ShopParser.parseVehicles = _safe_shop_vehicles
        ShopParser.parseModules = _safe_shop_modules
    except Exception as e:
        print "[OFFLINE] parseVehicles patch failed: %s" % e
    try:
        from messenger.gui.Scalefrom.JoinedChannelsInterface import JoinedChannelsInterface as _JCI
        def _cid_from_args(args):
            for a in args[1:]:
                if a is None:
                    continue
                try:
                    return long(a)
                except Exception:
                    continue
            return None
        _orig_wait = _JCI.onWaitChannelActivation
        _orig_act = _JCI.onChannelActivated
        _orig_leave = _JCI.onLeaveChannel
        def _on_wait(self, *args):
            cid = _cid_from_args(args)
            if cid is None:
                return
            return _orig_wait(self, args[0], cid)
        def _on_act(self, *args):
            cid = _cid_from_args(args)
            if cid is None:
                return
            return _orig_act(self, args[0], cid)
        def _on_leave(self, *args):
            cid = _cid_from_args(args)
            mgr = self._JoinedChannelsInterface__channelsManager
            if cid is None and mgr is not None:
                chs = mgr.getChannelList(joined=True, isBattle=False)
                if chs:
                    try:
                        cid = sorted(chs, key=lambda c: c.joinedTime or 0)[-1].cid
                    except Exception:
                        cid = chs[-1].cid
            if cid is None:
                return
            return _orig_leave(self, args[0], cid)
        _JCI.onWaitChannelActivation = _on_wait
        _JCI.onChannelActivated = _on_act
        _JCI.onLeaveChannel = _on_leave
    except Exception as e:
        print "[OFFLINE] JoinedChannelsInterface patch failed: %s" % e
    try:
        from gui.Scaleform.BattleDispatcherInterface import BattleDispatcherInterface as _BDI
        _orig_fight = _BDI.onFightButtonClick
        def _logged_fight(self, *a, **kw):
            print "[OFFLINE] FightButtonClick args=%s kw=%s present=%s ready=%s" % (
                a, kw,
                __import__('CurrentVehicle').g_currentVehicle.isPresent(),
                __import__('CurrentVehicle').g_currentVehicle.isReadyToFight() if __import__('CurrentVehicle').g_currentVehicle.isPresent() else False)
            try:
                return _orig_fight(self, *a, **kw)
            except Exception as e:
                print "[OFFLINE] FightButtonClick failed: %s" % e
                traceback.print_exc()
        _BDI.onFightButtonClick = _logged_fight
        if not getattr(_BDI, '_offline_prb_ui_guard', False):
            def _guard_bdi(name):
                orig = getattr(_BDI, name)
                def _safe(self, *a, **k):
                    uh = getattr(self, 'uiHolder', None)
                    if uh is None:
                        return
                    if getattr(uh, 'movie', None) is None and name == 'prb_onPrebattleSettingsRecived':
                        return
                    if getattr(uh, 'currentInterface', None) is None and name != 'prb_onPrebattleSettingsRecived':
                        return
                    return orig(self, *a, **k)
                setattr(_BDI, name, _safe)
            _guard_bdi('prb_onPrebattleSettingsRecived')
            _guard_bdi('prb_onPrebattleRosterRecived')
            _guard_bdi('prb_onTeamStateChanged')
            _BDI._offline_prb_ui_guard = True
    except Exception as e:
        print "[OFFLINE] FightButtonClick patch failed: %s" % e
    try:
        from messenger.gui.MessengerDispatcher import MessengerDispatcher as _MDCls
        if not getattr(_MDCls, '_offline_keep_sch', False):
            _orig_disc = _MDCls.onDisconnect
            def _keep_sch_on_disconnect(self):
                try:
                    sch = self.serviceChannel
                    kept = list(sch._ServiceChannelManager__messages)
                    unread = int(getattr(sch, '_ServiceChannelManager__unreadedMessageCount', 0) or 0)
                except Exception:
                    kept, unread = [], 0
                _orig_disc(self)
                try:
                    dq = self.serviceChannel._ServiceChannelManager__messages
                    if kept and len(dq) == 0:
                        for it in kept:
                            dq.append(it)
                        self.serviceChannel._ServiceChannelManager__unreadedMessageCount = unread
                        _session_sch_messages[:] = kept
                        _session_sch_messages.append(('__unread__', unread))
                except Exception:
                    pass
            _MDCls.onDisconnect = _keep_sch_on_disconnect
            _MDCls._offline_keep_sch = True
    except Exception as e:
        print "[OFFLINE] messenger persist patch failed: %s" % e
_apply_gui_fixes()

def _patch_training():
    try:
        import sys
        training_mod = sys.modules.get('gui.Scaleform.Training')
        if training_mod is None:
            BigWorld.callback(1.0, _patch_training)
            return
        TrainingRoom = training_mod.TrainingRoom
        original_recive = TrainingRoom._TrainingRoom__recivePlayersList
        original_info = TrainingRoom._TrainingRoom__reciveInfo

        def safe_recivePlayersList(self, *args):
            return original_recive(self, *args)

        def safe_reciveInfo(self, *args):
            try:
                s = getattr(self.prebattle, 'settings', None) or {}
                aid = s.get('arenaTypeID')
                resolved = _resolve_arena_type_id(aid)
                if resolved is not None:
                    s['arenaTypeID'] = resolved
            except Exception:
                pass
            try:
                return original_info(self, *args)
            except Exception as e:
                print "[OFFLINE] TrainingRoom.__reciveInfo failed: %s" % e

        TrainingRoom._TrainingRoom__recivePlayersList = safe_recivePlayersList
        TrainingRoom._TrainingRoom__reciveInfo = safe_reciveInfo
        CreateTrainingRoom = getattr(training_mod, 'CreateTrainingRoom', None)
        if CreateTrainingRoom is not None:
            original_maps = CreateTrainingRoom._CreateTrainingRoom__populateMaps
            def safe_populateMaps(self, callbackId):
                try:
                    import ArenaType
                    maps = [callbackId]
                    arenaCache = ArenaType.g_cache
                    for arenaTypeID, arenaTypeName in ArenaType.g_list.iteritems():
                        arenaType = arenaCache.get(arenaTypeID)
                        maps.append(arenaType.name)
                        maps.append(arenaTypeID)
                        maps.append(arenaType.maxPlayersInTeam)
                        maps.append(arenaType.roundLength / 60)
                        maps.append(arenaType.description or '')
                        maps.append(training_mod._ICONS_MASK % {'subtype': '', 'unicName': arenaTypeName})
                    self.uiHolder.respond(maps)
                    return
                except Exception as e:
                    print "[OFFLINE] populateMaps failed: %s" % e
                    return original_maps(self, callbackId)
            CreateTrainingRoom._CreateTrainingRoom__populateMaps = safe_populateMaps
    except Exception as e:
        print "[OFFLINE] _patch_training failed: %s" % e
BigWorld.callback(1.0, _patch_training)
