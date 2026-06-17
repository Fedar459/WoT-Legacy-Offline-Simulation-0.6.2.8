import BigWorld
import new
import traceback
import AccountCommands
import account_helpers.AccountSettings as AS
import random
from ConnectionManager import connectionManager
from PlayerEvents import g_playerEvents
from items import tankmen

_modules_inventory  = {}
_modules_by_type    = {}
_selected_arena     = None
_player_name        = "Commander"
_vehicle_shells     = {}
_vehicle_equipments = {}
_shop_prices_cache  = {}

def _read_tankman_cost():
    return (
        {'credits': 0,     'gold': 0},
        {'credits': 20000, 'gold': 0},
        {'credits': 0,     'gold': 200},
    )

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

def init_offline():
    print "[OFFLINE_LOBBY] World of Tanks Start ! "

    def fake_connect(*args, **kwargs):
        global _modules_inventory, _modules_by_type
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

            arena_list = [
                '01_karelia', '02_malinovka','04_himmelsdorf','05_prohorovka','06_ensk', '07_lakeville','08_ruinberg','10_hills','11_murovanka','13_erlenberg','15_komarin','18_cliff','19_monastery','28_desert','29_el_hallouf',
            ]
            global _selected_arena
            _selected_arena = random.choice(arena_list)

            tank_list = [
                'ussr:MS-1','ussr:A-32','ussr:AT-1','ussr:BT-2','ussr:BT-7','ussr:Churchill_LL','ussr:GAZ-74b','ussr:IS',
                'ussr:IS-3','ussr:IS-4','ussr:IS-7','ussr:ISU-152','ussr:KV','ussr:KV-1s','ussr:KV-3','ussr:KV-220',
                'ussr:M3_Stuart_LL','ussr:Matilda_II_LL','ussr:A-20','ussr:Object_212','ussr:Object_704','ussr:S-51','ussr:SU-5','ussr:SU-8',
                'ussr:SU-14','ussr:SU-18','ussr:SU-26','ussr:SU-76','ussr:SU-85','ussr:SU-100','ussr:SU-152','ussr:T-26',
                'ussr:T-28','ussr:T-34','ussr:T-34-85','ussr:T-43','ussr:T-44','ussr:T-46','ussr:T-54','ussr:Valentine_LL',

                'germany:B-1bis_captured','germany:Bison_I','germany:Ferdinand','germany:G_Panther','germany:Sturmpanzer_II','germany:G_Tiger','germany:G20_Marder_II','germany:Grille',
                'germany:H39_captured','germany:Hetzer','germany:Hummel','germany:JagdPanther','germany:JagdPzIV','germany:JagdTiger','germany:Ltraktor','germany:Maus',
                'germany:Panther_II','germany:PanzerJager_I','germany:Pz35t','germany:Pz38t','germany:PzII','germany:PzII_Luchs','germany:PzIII','germany:PzIII_A',
                'germany:PzIII_IV','germany:PzIV','germany:PzV','germany:PzV_PzIV','germany:PzV_PzIV_ausf_Alfa','germany:PzVI','germany:PzVIB_Tiger_II','germany:StuGIII',
                'germany:VK1602','germany:VK3001H','germany:VK3001P','germany:VK3002DB','germany:VK3601H','germany:VK4502P','germany:Wespe','germany:S35_captured',

                'usa:M2_lt','usa:M2_med','usa:M3_Grant','usa:M3_Stuart','usa:M4_Sherman','usa:M4A3E8_Sherman','usa:M5_Stuart','usa:M6',
                'usa:M7_med','usa:M7_Priest','usa:M12','usa:M37','usa:M40M43','usa:M41','usa:Pershing','usa:Ram-II',
                'usa:T1_Cunningham','usa:T1_hvy','usa:T2_lt','usa:T2_med','usa:T14','usa:T20','usa:T23',
                'usa:T29','usa:T30','usa:T32','usa:T34_hvy','usa:T57',
            ]

            my_garage = {}
            veh_cds   = {}
            for i, name in enumerate(tank_list):
                invID = i + 1
                my_garage[invID] = name
                try:
                    veh_cds[invID] = vehicles.VehicleDescr(typeName=name).makeCompactDescr()
                except Exception as e:
                    print "[OFFLINE] Error loading %s: %s" % (name, e)

            all_unlocks     = []
            premium_unlocks = []
            for nID in range(len(nations.NAMES)):
                try:
                    vList = vehicles.g_list.getList(nID)
                    for vID, vEntry in vList.items():
                        try:
                            cd = vehicles.makeIntCompactDescrByID('vehicle', nID, vID)
                            vDescr = vehicles.VehicleDescr(typeName=vEntry['name'])
                            tags = getattr(vDescr.type, 'tags', set())
                            if 'premium' in tags or 'special' in tags:
                                premium_unlocks.append(cd)
                            else:
                                all_unlocks.append(cd)
                        except: pass
                except: continue
            all_unlocks = all_unlocks + premium_unlocks

            inv_vehicles = dict((invID, cd) for invID, cd in veh_cds.items())

            t_cache         = {}
            t_in_veh        = {}
            crew_map        = {}
            current_tman_id = 10

            for invID, name in my_garage.items():
                if invID not in veh_cds: continue
                descr     = vehicles.VehicleDescr(compactDescr=veh_cds[invID])
                crew_ids  = []
                nationID  = descr.type.id[0]
                vehTypeID = descr.type.id[1]
                for role in descr.type.crewRoles:
                    passport = tankmen.generatePassport(nationID)
                    tman_cd  = tankmen.generateCompactDescr(passport, vehTypeID, role[0], 100)
                    t_cache[current_tman_id]  = tman_cd
                    t_in_veh[current_tman_id] = invID
                    crew_ids.append(current_tman_id)
                    current_tman_id += 1
                crew_map[invID] = crew_ids

            _modules_inventory = {}
            _modules_by_type   = {}

            for nationID in range(len(nations.NAMES)):
                try:
                    for chassis in vehicles.g_cache.chassis(nationID).values():
                        cd = chassis.get('compactDescr')
                        if cd:
                            _modules_inventory[cd] = 1
                            _modules_by_type.setdefault(_CHASSIS_IDX, {})[cd] = 1

                    for turret in vehicles.g_cache.turrets(nationID).values():
                        cd = turret.get('compactDescr')
                        if cd:
                            _modules_inventory[cd] = 1
                            _modules_by_type.setdefault(_TURRET_IDX, {})[cd] = 1

                    for engine in vehicles.g_cache.engines(nationID).values():
                        cd = engine.get('compactDescr')
                        if cd:
                            _modules_inventory[cd] = 1
                            _modules_by_type.setdefault(_ENGINE_IDX, {})[cd] = 1

                    for gun in vehicles.g_cache.guns(nationID).values():
                        cd = gun.get('compactDescr')
                        if cd:
                            _modules_inventory[cd] = 1
                            _modules_by_type.setdefault(_GUN_IDX, {})[cd] = 1

                    for radio in vehicles.g_cache.radios(nationID).values():
                        cd = radio.get('compactDescr')
                        if cd:
                            _modules_inventory[cd] = 1
                            _modules_by_type.setdefault(_RADIO_IDX, {})[cd] = 1

                    try:
                        for od in vehicles.g_cache.optionalDevices().values():
                            cd = od.get('compactDescr')
                            if cd:
                                _modules_inventory[cd] = 1
                                _modules_by_type.setdefault(_OPTDEV_IDX, {})[cd] = 1
                                all_unlocks.append(cd)
                    except Exception as e:
                        print "[OFFLINE] Error loading optional devices: %s" % e

                    try:
                        for eq in vehicles.g_cache.equipments().values():
                            cd = eq.get('compactDescr')
                            if cd:
                                _modules_inventory[cd] = 1
                                _modules_by_type.setdefault(_EQUIP_IDX, {})[cd] = 1
                                all_unlocks.append(cd)
                    except Exception as e:
                        print "[OFFLINE] Error loading equipment: %s" % e

                except Exception as e:
                    pass

            print "[OFFLINE] Loaded %d modules" % len(_modules_inventory)

            xp_per_tank = {
                'ussr:MS-1':        20000,
                'germany:Ltraktor':  5000,
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
                dict((i, {})        for i in my_garage),
                dict((i, [])        for i in my_garage),
                crew_map,
                dict((i, (0, 100))  for i in my_garage),
                dict((i, [0,0,0])   for i in my_garage),
                dict((i, [0,0,0])   for i in my_garage),
                dict((i, 0)         for i in my_garage),
                dict((i, 0)         for i in my_garage),
            ]

            OFFLINE_CREDITS = 100000000
            OFFLINE_GOLD    = 100000000
            OFFLINE_FREE_XP = 100000000
            OFFLINE_SLOTS   = 110
            OFFLINE_BERTHS  = 310

            off_stats = {
                'credits':          OFFLINE_CREDITS,
                'gold':             OFFLINE_GOLD,
                'freeXP':           OFFLINE_FREE_XP,
                'unlocks':          all_unlocks + list(_modules_inventory.keys()),
                'eliteVehicles':    all_unlocks,
                'vehTypeXP':        vehTypeXP,
                'rev':              1,
                'slots':            OFFLINE_SLOTS,
                'berths':           OFFLINE_BERTHS,
                'currentVehInvID':  1,
                'isPremium':        True,
                'premiumExpiryTime':1999999999,
                'accountType': 3,
                'clanInfo':    ('', 0, '', 0),
                'dossier':     '',
            }

            if not BigWorld.player():
                BigWorld.createEntity('Account', spaceID, 0, (0,0,0), (0,0,0), {})
                p = BigWorld.player()
                p.name = 'Commander'
                p.isPremium = True
                p.premiumExpiryTime = 1999999999
                p.accountType = 3

                global _player_name
                try:
                    _player_name = p.name if hasattr(p, 'name') and p.name else "Commander"
                except:
                    _player_name = "Commander"

                p.startOfflineBattle = lambda mapId=-1: __import__('Offline.BattleStarter', fromlist=['_']).start_offline_battle(mapId)
                def _offline_enqueue(*args, **kwargs):
                    p.requestQueueInfo = lambda *a, **kw: None
                    try:
                        from gui.Scaleform.Waiting import Waiting
                        Waiting.hide()
                    except: pass
                    BigWorld.callback(0.2, lambda: p.startOfflineBattle())
                p.enqueueForArenaExt = _offline_enqueue
                p.enqueueForArena    = _offline_enqueue
                p.requestQueueInfo = lambda *a, **kw: None
                p.dequeue = lambda *a, **kw: None
                p.createArenaFromQueue = lambda *a, **kw: p.startOfflineBattle()

                import CurrentVehicle

                class OfflineVehicleWrapper(object):
                    def __init__(self, invID):
                        self.inventoryId = invID
                        self.descriptor  = vehicles.VehicleDescr(compactDescr=veh_cds[invID])
                        self.level       = self.descriptor.level
                        self.crew        = [(tid, idx) for idx, tid in enumerate(crew_map[invID])]
                        self.health      = 1000
                        self.modelState  = 'undamaged'
                        self.lock        = 0
                        self.isPremium   = True
                        self.tags        = self.descriptor.type.tags
                        self.shells      = list(_vehicle_shells.get(invID, []))
                        self.equipments  = list(_vehicle_equipments.get(invID, [0,0,0]))
                        self.equipmentsLayout = list(_vehicle_equipments.get(invID, [0,0,0]))
                        self.repairCost  = 0
                        self.isXPToTmen  = False

                    def isElite(self):
                        from adisp import async as _async
                        @_async
                        def _f(callback):
                            BigWorld.callback(0, lambda: callback(True))
                        return _f()

                    def pack(self):
                        import pickle
                        from gui.Scaleform.utils.gui_items import InventoryVehicle
                        crew_ids = [t[0] for t in self.crew] if self.crew else []
                        return pickle.dumps([InventoryVehicle, (self.descriptor.makeCompactDescr(), self.inventoryId, crew_ids)])

                    def __getattr__(self, n): return None

                def call_smart(cb, data):
                    if not cb: return
                    import inspect
                    try:
                        argspec = inspect.getargspec(cb)
                        count   = len(argspec.args)
                        if hasattr(cb, 'im_self'): count -= 1
                        if count == 1:   BigWorld.callback(0, lambda: cb(data))
                        elif count == 2: BigWorld.callback(0, lambda: cb(0, data))
                        else:            BigWorld.callback(0, lambda: cb(0, data, None))
                    except:
                        try: BigWorld.callback(0, lambda: cb(0, data))
                        except: pass

                def makeMock(obj_name, method_name):
                    def force_cb(self, *args, **kwargs):
                        cb  = next((a for a in args if callable(a)), kwargs.get('callback'))
                        req = args[0] if len(args) > 0 else None
                        res = 0

                        if method_name in ('get', 'getCache', 'request'):
                            if obj_name == 'stats' and req == 'berths':
                                res = off_stats.get('berths', 0)
                            elif obj_name == 'dossierCache':
                                import dossiers
                                try:
                                    if req == 1:
                                        res = (0, dossiers.getVehicleDossierDescr(''))
                                    else:
                                        res = (0, dossiers.getTankmanDossierDescr(''))
                                except:
                                    res = (0, '')
                            else:
                                res = off_stats.get(req, {})

                        elif method_name == 'getItems':
                            if obj_name == 'inventory':
                                if req == _VEHICLE_IDX:
                                    res = {
                                        'compDescr':   inv_vehicles,
                                        'shells':      dict((i, list(_vehicle_shells.get(i, [])))      for i in my_garage),
                                        'shellsLayout':dict((i, {})                                    for i in my_garage),
                                        'crew':        crew_map,
                                        'repair':      dict((i, (0, 100))                              for i in my_garage),
                                        'eqs':         dict((i, list(_vehicle_equipments.get(i, [0,0,0]))) for i in my_garage),
                                        'eqsLayout':   dict((i, list(_vehicle_equipments.get(i, [0,0,0]))) for i in my_garage),
                                        'settings':    dict((i, 0)                                     for i in my_garage),
                                        'lock':        dict((i, 0)                                     for i in my_garage),
                                    }
                                elif req == _TANKMAN_IDX:
                                    res = {
                                        'compDescr': t_cache,
                                        'vehicle':   t_in_veh,
                                    }
                                elif req in _modules_by_type:
                                    res = _modules_by_type[req]
                                else:
                                    res = {}

                            elif obj_name == 'shop':
                                nationID_arg = args[1] if len(args) > 1 else None
                                prices       = _build_shop_prices()
                                import nations as _nat
                                from items import ITEM_TYPE_INDICES as _ITI2
                                if req in (_ITI2.get('optionalDevice', -1), _ITI2.get('equipment', -2)):
                                    nation_data = prices.get(_nat.NONE_INDEX, {})
                                else:
                                    nation_data = prices.get(nationID_arg, {})
                                if req in nation_data:
                                    raw = nation_data[req]
                                    if isinstance(raw, (tuple, list)) and len(raw) == 2:
                                        res = list(raw)
                                    else:
                                        res = [raw, set()]
                                else:
                                    res = [{}, set()]

                        elif obj_name == 'stats' and method_name == 'buySlot':
                            try:
                                price = 0
                                if off_stats.get('gold', 0) >= price:
                                    off_stats['gold']  -= price
                                    off_stats['slots']  = off_stats.get('slots', 0) + 1
                                    print "[OFFLINE][BUY_SLOT] OK: slots=%d gold=%d" % (off_stats['slots'], off_stats['gold'])
                                    res = 0
                                else:
                                    print "[OFFLINE][BUY_SLOT] not enough gold"
                                    res = -1
                            except Exception as e:
                                print "[OFFLINE][BUY_SLOT] error: %s" % e
                                res = -1
                            BigWorld.callback(0.1, lambda: g_playerEvents.onStatsResync())

                        elif obj_name == 'stats' and method_name == 'buyBerths':
                            try:
                                price = 300
                                if off_stats.get('gold', 0) >= price:
                                    off_stats['gold']   -= price
                                    off_stats['berths']  = off_stats.get('berths', 0) + 16
                                    res = 0
                                else:
                                    print "[OFFLINE][BUY_BERTHS] not enough gold"
                                    res = -1
                            except Exception as e:
                                print "[OFFLINE][BUY_BERTHS] error: %s" % e
                                res = -1
                            BigWorld.callback(0.1, lambda: g_playerEvents.onStatsResync())

                        elif obj_name == 'stats' and method_name == 'upgradeToPremium':
                            try:
                                import time as _time
                                days = int(float(args[0])) if args else 1
                                cost_map = {1: 250, 3: 650, 7: 1200}
                                cost = cost_map.get(days, 250)
                                if off_stats.get('gold', 0) >= cost:
                                    off_stats['gold']              -= cost
                                    off_stats['isPremium']          = True
                                    off_stats['premiumExpiryTime']  = int(_time.time()) + days * 86400
                                    try:
                                        p.isPremium         = True
                                        p.premiumExpiryTime = off_stats['premiumExpiryTime']
                                    except: pass
                                    try:
                                        p.stats._Stats__cache['isPremium']         = True
                                        p.stats._Stats__cache['premiumExpiryTime'] = off_stats['premiumExpiryTime']
                                    except: pass
                                    print "[OFFLINE][PREMIUM] OK: %d days, gold=%d" % (days, off_stats['gold'])
                                    res = 0
                                else:
                                    print "[OFFLINE][PREMIUM] not enough gold"
                                    res = -1
                            except Exception as e:
                                print "[OFFLINE][PREMIUM] error: %s" % e
                                res = -1
                            BigWorld.callback(0.05, lambda: g_playerEvents.onStatsResync())
                            BigWorld.callback(0.1,  lambda: g_playerEvents.onInventoryResync())

                        elif obj_name == 'stats' and method_name == 'exchange':
                            try:
                                gold_amount = int(float(args[0])) if args else 0
                                rate        = 400
                                if off_stats.get('gold', 0) >= gold_amount > 0:
                                    off_stats['gold']    = off_stats.get('gold', 0)    - gold_amount
                                    off_stats['credits'] = off_stats.get('credits', 0) + gold_amount * rate
                                    print "[OFFLINE][EXCHANGE] OK: %d gold -> %d cr" % (gold_amount, gold_amount * rate)
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
                                gold_amount = int(float(args[1])) if len(args) > 1 else 0
                                rate        = 25
                                if off_stats.get('gold', 0) >= gold_amount > 0:
                                    off_stats['gold']   = off_stats.get('gold', 0)   - gold_amount
                                    off_stats['freeXP'] = off_stats.get('freeXP', 0) + gold_amount * rate
                                    print "[OFFLINE][CONV_XP] OK: %d gold -> %d freeXP" % (gold_amount, gold_amount * rate)
                                    res = 0
                                else:
                                    print "[OFFLINE][CONV_XP] not enough gold or zero"
                                    res = -1
                            except Exception as e:
                                print "[OFFLINE][CONV_XP] error: %s" % e
                                res = -1
                            BigWorld.callback(0.1, lambda: g_playerEvents.onStatsResync())

                        elif obj_name == 'inventory' and method_name == 'equipEquipments':
                            try:
                                vehicleInvID = args[0] if len(args) > 0 else None
                                equipments   = args[1] if len(args) > 1 else []
                                if vehicleInvID is not None:
                                    _vehicle_equipments[vehicleInvID] = list(equipments)
                                    inv_data[5][vehicleInvID] = list(equipments)
                                    inv_data[6][vehicleInvID] = list(equipments)
                                    from CurrentVehicle import g_currentVehicle
                                    veh = g_currentVehicle.vehicle
                                    if veh is not None:
                                        try:
                                            veh.equipments = list(equipments)
                                        except AttributeError:
                                            try:
                                                object.__setattr__(veh, 'equipments', list(equipments))
                                            except:
                                                veh.__dict__['equipments'] = list(equipments)
                                    print "[OFFLINE] equipEquipments OK: invID=%d eqs=%r" % (vehicleInvID, equipments)
                                    BigWorld.callback(0.05, lambda: g_playerEvents.onInventoryResync())
                                    try:
                                        from CurrentVehicle import g_currentVehicle
                                        BigWorld.callback(0.1, lambda: g_currentVehicle.update())
                                    except Exception as e:
                                        print "[OFFLINE] equipEquipments update error: %s" % e
                                if cb is not None:
                                    BigWorld.callback(0.01, lambda: cb(0))
                                return
                            except Exception as e:
                                print "[OFFLINE] equipEquipments error: %s" % e
                                if cb is not None:
                                    BigWorld.callback(0.01, lambda: cb(-1))
                                return

                        elif obj_name == 'shop':
                            if method_name == 'getBerthsPrices':
                                res = (300, [300, 600, 900])
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
                                    compact = args[2] if len(args) > 2 else None
                                    prices = _build_shop_prices()
                                    res = (0, 0)
                                    if compact is not None:
                                        for nat_data in prices.values():
                                            from items import ITEM_TYPE_INDICES as _ITI2
                                            shell_data = nat_data.get(_ITI2.get('shell', -1), ({}, set()))
                                            raw = shell_data[0] if isinstance(shell_data, tuple) else shell_data
                                            if compact in raw:
                                                res = raw[compact]
                                                break
                                except:
                                    res = (0, 0)
                            elif method_name == 'getVehicleSellPrice':
                                try:
                                    from items import vehicles as _veh_mod, ITEM_TYPE_INDICES as _ITI2
                                    compact = args[0]
                                    vd = _veh_mod.VehicleDescr(compactDescr=compact)
                                    nat_id, veh_id = vd.type.id
                                    prices = _build_shop_prices()
                                    price_entry = prices.get(nat_id, {}).get(_ITI2['vehicle'], ({}, set()))[0].get(veh_id, (0, 0))
                                    buy_cr = price_entry[0] if isinstance(price_entry, tuple) else price_entry
                                    res = buy_cr // 2
                                except Exception as e:
                                    print "[OFFLINE] getVehicleSellPrice error: %s" % e
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
                            elif method_name == 'getSlotsPrices':
                                res = (300, [300, 300, 300])
                            elif method_name == 'getExchangeRate':
                                res = (400, 1)
                            elif method_name == 'getFreeXPConversion':
                                res = (25, 1)
                            elif method_name == 'getPremiumCost':
                                res = {1: 250, 3: 650, 7: 1200}
                            elif method_name == 'getSellPriceModifiers':
                                res = (1, 400, 0.5)
                            elif method_name == 'getPassportChangeCost':
                                res = 50
                            elif method_name == 'getNextSlotPrice':
                                res = 300
                            elif method_name == 'getNextBerthPackPrice':
                                res = 300
                            elif method_name == 'getAccountType':
                                res = 3
                            
                            elif method_name == 'getSellPrice':
                                try:
                                    buy_price = args[0] if len(args) > 0 else (0, 0)
                                    buy_cr    = buy_price[0] if isinstance(buy_price, (tuple, list)) else buy_price
                                    res       = int(buy_cr * 0.5)
                                except:
                                    res = 0
                            
                            elif method_name == 'getVehiclesSellPrices':
                                try:
                                    from items import vehicles as _veh_mod, ITEM_TYPE_INDICES as _ITI2
                                    vehicles_list = args[0] if len(args) > 0 else []
                                    prices = _build_shop_prices()
                                    result_dict = {}
                                    for vcd in (vehicles_list or []):
                                        try:
                                            vd = _veh_mod.VehicleDescr(compactDescr=vcd)
                                            nat_id, veh_id = vd.type.id
                                            price_entry = prices.get(nat_id, {}).get(_ITI2['vehicle'], ({}, set()))[0].get(veh_id, (0, 0))
                                            buy_cr = price_entry[0] if isinstance(price_entry, tuple) else price_entry
                                            result_dict[vcd] = buy_cr // 2
                                        except:
                                            result_dict[vcd] = 0
                                    res = result_dict
                                except Exception as e:
                                    print "[OFFLINE] getVehiclesSellPrices error: %s" % e
                                    res = {}
                            
                            elif method_name == 'buy':
                                try:
                                    type_idx   = args[0] if len(args) > 0 else None
                                    compact_cd = args[2] if len(args) > 2 else None
                                    count_buy  = int(args[3]) if len(args) > 3 else 1
                                    buy_cr   = 0
                                    buy_gold = 0
                                    prices = _build_shop_prices()
                                    for nat_data in prices.values():
                                        for type_data in nat_data.values():
                                            raw = type_data[0] if isinstance(type_data, tuple) else type_data
                                            if compact_cd in raw:
                                                p_val    = raw[compact_cd]
                                                buy_cr   = p_val[0] if isinstance(p_val, tuple) else p_val
                                                buy_gold = p_val[1] if isinstance(p_val, tuple) and len(p_val) > 1 else 0
                                                break
                                        else:
                                            continue
                                        break
                                    if buy_gold > 0:
                                        off_stats['gold']    = off_stats.get('gold', 0)    - buy_gold * count_buy
                                    else:
                                        off_stats['credits'] = off_stats.get('credits', 0) - buy_cr   * count_buy
                                    if compact_cd is not None:
                                        cur = _modules_inventory.get(compact_cd, 0)
                                        _modules_inventory[compact_cd] = cur + count_buy
                                        if type_idx is not None:
                                            _modules_by_type.setdefault(type_idx, {})[compact_cd] = _modules_inventory[compact_cd]
                                    res = 0
                                except Exception as e:
                                    print "[OFFLINE][BUY_MOD] error: %s" % e
                                    res = -1
                                BigWorld.callback(0.1, lambda: g_playerEvents.onInventoryResync())
                                BigWorld.callback(0.1, lambda: g_playerEvents.onStatsResync())

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
                    'getShellPrice', 'getPrice',
                    'getSellPriceModifiers', 'getPassportChangeCost',
                    'equipEquipments',
                    'getVehiclesSellPrices',
                    'getSellPrice',
                ]

                for s in ['inventory', 'stats', 'shop', 'dossierCache']:
                    obj = getattr(p, s, None)
                    if obj:
                        for m in mocked_methods:
                            setattr(obj, m, new.instancemethod(makeMock(s, m), obj, obj.__class__))
                        if s == 'shop':
                            def get_next_berth_price(berths, berths_prices):
                                return 300
                            obj.getNextBerthPackPrice = get_next_berth_price

                inv_obj = getattr(p, 'inventory', None)
                if inv_obj:
                    def fake_equip(self, vehicleInvID, itemCompDescr, callback=None):
                        try:
                            from CurrentVehicle import g_currentVehicle
                            veh = g_currentVehicle.vehicle
                            if veh and veh.descriptor:
                                veh.descriptor.installComponent(itemCompDescr)
                                new_cd = veh.descriptor.makeCompactDescr()
                                veh_cds[vehicleInvID]     = new_cd
                                inv_vehicles[vehicleInvID] = new_cd
                                BigWorld.callback(0.05, lambda: g_currentVehicle.update())
                        except Exception as e:
                            print "[OFFLINE] equip error: %s" % e
                        if callback:
                            BigWorld.callback(0.01, lambda: callback(0))

                    def fake_equipTurret(self, vehicleInvID, itemCompDescr, slotIdx, callback=None):
                        try:
                            from CurrentVehicle import g_currentVehicle
                            veh = g_currentVehicle.vehicle
                            if veh and veh.descriptor:
                                veh.descriptor.installTurret(itemCompDescr, slotIdx)
                                new_cd = veh.descriptor.makeCompactDescr()
                                veh_cds[vehicleInvID]     = new_cd
                                inv_vehicles[vehicleInvID] = new_cd
                                BigWorld.callback(0.05, lambda: g_currentVehicle.update())
                        except Exception as e:
                            print "[OFFLINE] equipTurret error: %s" % e
                        if callback:
                            BigWorld.callback(0.01, lambda: callback(0))

                    inv_obj.equip       = new.instancemethod(fake_equip,       inv_obj, inv_obj.__class__)
                    inv_obj.equipTurret = new.instancemethod(fake_equipTurret, inv_obj, inv_obj.__class__)

                    def fake_equipShells(self, vehicleInvID, shells, callback=None):
                        try:
                            shell_list = list(shells) if shells else []
                            _vehicle_shells[vehicleInvID] = shell_list
                            inv_data[2][vehicleInvID] = shell_list
                            from CurrentVehicle import g_currentVehicle
                            veh = g_currentVehicle.vehicle
                            if veh is not None:
                                veh.shells = shell_list
                                if hasattr(veh, 'setShellsList'):
                                    veh.setShellsList(shell_list)
                            print "[OFFLINE] equipShells OK: invID=%d, count=%d" % (vehicleInvID, len(shell_list))
                        except Exception as e:
                            print "[OFFLINE] equipShells error: %s" % e
                        if callback:
                            BigWorld.callback(0.01, lambda: callback(0))
                        BigWorld.callback(0.15, lambda: g_currentVehicle.update())
                        BigWorld.callback(0.1, lambda: g_playerEvents.onInventoryResync())

                    inv_obj.equipShells = new.instancemethod(fake_equipShells, inv_obj, inv_obj.__class__)

                    def fake_equipOptionalDevice(self, vehicleInvID, itemCompDescr, slotIdx, callback=None):
                        try:
                            from CurrentVehicle import g_currentVehicle
                            veh = g_currentVehicle.vehicle
                            if veh and veh.descriptor:
                                if itemCompDescr == 0:
                                    veh.descriptor.removeOptionalDevice(slotIdx)
                                    print "[OFFLINE] Removed device from slot %d" % slotIdx
                                else:
                                    veh.descriptor.installOptionalDevice(itemCompDescr, slotIdx)
                                    print "[OFFLINE] Equipped device %d in slot %d" % (itemCompDescr, slotIdx)
                                new_cd = veh.descriptor.makeCompactDescr()
                                veh_cds[vehicleInvID]     = new_cd
                                inv_vehicles[vehicleInvID] = new_cd
                                BigWorld.callback(0.05, lambda: g_currentVehicle.update())
                        except Exception as e:
                            print "[OFFLINE] equipOptionalDevice error: %s" % e
                        if callback:
                            BigWorld.callback(0.01, lambda: callback(0))

                    inv_obj.equipOptionalDevice = new.instancemethod(fake_equipOptionalDevice, inv_obj, inv_obj.__class__)

                    def fake_equipTankman(self, vehicleInvID, slot, tankmanID, callback=None):
                        try:
                            vehicleInvID = int(vehicleInvID) if vehicleInvID is not None else None
                            slot         = int(slot)         if slot is not None else None
                            tankmanID    = int(tankmanID)    if tankmanID is not None else None
                            for vid, crew_list in inv_data[3].items():
                                if isinstance(crew_list, list) and tankmanID in crew_list:
                                    idx = crew_list.index(tankmanID)
                                    crew_list[idx] = None
                                    t_in_veh.pop(tankmanID, None)
                                    break
                            if vehicleInvID is not None and slot is not None and tankmanID is not None:
                                crew_list = inv_data[3].setdefault(vehicleInvID, [])
                                while len(crew_list) <= slot:
                                    crew_list.append(None)
                                old_tid = crew_list[slot]
                                if old_tid is not None:
                                    t_in_veh.pop(old_tid, None)
                                crew_list[slot] = tankmanID
                                t_in_veh[tankmanID] = vehicleInvID
                            print "[OFFLINE][TMAN_EQUIP] OK"
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
                                descr.skills    = []
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

                    shop_obj_tman = getattr(p, 'shop', None)
                    if shop_obj_tman:
                        def fake_buyTankman(self, nationID, vehTypeID, role, crew_type, callback=None):
                            try:
                                passport = tankmen.generatePassport(int(nationID))
                                tman_cd  = tankmen.generateCompactDescr(passport, int(vehTypeID), role, 75)
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
                                veh_list  = _v.g_list.getList(nationID)
                                veh_entry = veh_list.get(vehInnationID)
                                if veh_entry is None:
                                    raise Exception("Unknown vehicle nationID=%d id=%d" % (nationID, vehInnationID))
                                type_name = veh_entry['name']
                                new_cd    = _v.VehicleDescr(typeName=type_name).makeCompactDescr()
                                new_inv   = _next_inv_id[0]
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

                                descr    = _v.VehicleDescr(compactDescr=new_cd)
                                nID      = descr.type.id[0]
                                vtID     = descr.type.id[1]
                                crew_ids = []
                                for role in descr.type.crewRoles:
                                    passport = tankmen.generatePassport(nID)
                                    tman_cd  = tankmen.generateCompactDescr(passport, vtID, role[0], 100)
                                    tid = max(t_cache.keys()) + 1 if t_cache else 1
                                    t_cache[tid]  = tman_cd
                                    t_in_veh[tid] = new_inv
                                    crew_ids.append(tid)
                                inv_data[3][new_inv] = crew_ids
                                crew_map[new_inv]    = crew_ids
                                my_garage[new_inv]   = type_name

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
                            print "[OFFLINE] sellVehicle OK: invID=%d" % inventoryId

                            from CurrentVehicle import g_currentVehicle
                            if g_currentVehicle.vehicle and \
                               g_currentVehicle.vehicle.inventoryId == inventoryId:
                                if veh_cds:
                                    first = min(veh_cds.keys())
                                    import CurrentVehicle as _CV
                                    _CV.g_currentVehicle._CurrentVehicle__vehicle = OfflineVehicleWrapper(first)
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
                                    BigWorld.callback(0.1,  lambda: g_playerEvents.onInventoryResync())
                                    BigWorld.callback(0.1,  lambda: g_playerEvents.onStatsResync())
                                except Exception as e:
                                    print "[OFFLINE][SELL_MOD] error: %s" % e
                                    import traceback; traceback.print_exc()
                                    BigWorld.callback(0.05, lambda: callback(-1))

                        inv_obj2.sell = new.instancemethod(fake_sell_dispatch, inv_obj2, inv_obj2.__class__)

                #  переключение танка ес чо
                p.selectVehicle = lambda invID: (
                    setattr(CurrentVehicle.g_currentVehicle, '_CurrentVehicle__vehicle', OfflineVehicleWrapper(invID)),
                    CurrentVehicle.g_currentVehicle.onChanged(),
                    None
                )[2]

                import time as _time
                from chat_shared import CHAT_ACTIONS, CHAT_RESPONSES, buildChatActionData

                _offline_channels = {}
                _offline_next_cid = [1]

                def _make_chat_action(action, cid=0, data=None, nick=None):
                    return buildChatActionData(
                        action             = action,
                        channelId          = cid,
                        originatorNickName = nick or p.name,
                        data               = data or {},
                        actionResponse     = CHAT_RESPONSES.success
                    )

                def fake_requestSystemChatChannels(self):
                    ch_list = []
                    for cid, info in _offline_channels.items():
                        ch_list.append({'id': cid, 'channelName': info['name'],
                                        'flags': 0, 'isReadOnly': False,
                                        'isSystem': True, 'isBattle': False})
                    act = _make_chat_action(CHAT_ACTIONS.requestChannels, data=ch_list)
                    BigWorld.callback(0.05, lambda: self.onChatAction(act))

                def fake_createChatChannel(self, channelName, password=None):
                    cid = _offline_next_cid[0]
                    _offline_next_cid[0] += 1
                    _offline_channels[cid] = {'name': channelName, 'members': [p.name]}
                    act = _make_chat_action(CHAT_ACTIONS.createChannel, cid=cid,
                                            data={'id': cid, 'channelName': channelName, 'flags': 0})
                    if isinstance(act, dict): act['channel'] = cid
                    BigWorld.callback(0.05, lambda: self.onChatAction(act))
                    act2 = _make_chat_action(CHAT_ACTIONS.selfEnter, cid=cid,
                                             data={'id': cid, 'channelName': channelName, 'flags': 0})
                    if isinstance(act2, dict): act2['channel'] = cid
                    BigWorld.callback(0.1, lambda: self.onChatAction(act2))

                def fake_enterChat(self, channelId, password=None):
                    if channelId not in _offline_channels: return
                    info = _offline_channels[channelId]
                    if p.name not in info['members']:
                        info['members'].append(p.name)
                    act = _make_chat_action(CHAT_ACTIONS.selfEnter, cid=channelId,
                                            data={'id': channelId, 'channelName': info['name'], 'flags': 0})
                    if isinstance(act, dict): act['channel'] = channelId
                    BigWorld.callback(0.05, lambda: self.onChatAction(act))

                def fake_leaveChat(self, channelId):
                    if channelId in _offline_channels:
                        info = _offline_channels[channelId]
                        if p.name in info['members']:
                            info['members'].remove(p.name)
                    act = _make_chat_action(CHAT_ACTIONS.selfLeave, cid=channelId, data={})
                    BigWorld.callback(0.05, lambda: self.onChatAction(act))

                def fake_broadcast(self, channelId, message):
                    if not message or not message.strip(): return
                    act = _make_chat_action(CHAT_ACTIONS.broadcast, cid=channelId, data=message, nick=p.name)
                    if isinstance(act, dict):
                        act['channel']   = channelId
                        act['channelId'] = channelId
                    BigWorld.callback(0.0, lambda: self.onChatAction(act))

                def fake_findChatChannels(self, sample):
                    found = []
                    for cid, info in _offline_channels.items():
                        if sample.lower() in info['name'].lower():
                            found.append({'id': cid, 'name': info['name'], 'flags': 0})
                    act = _make_chat_action(CHAT_ACTIONS.requestChannels, data=found)
                    BigWorld.callback(0.05, lambda: self.onChatAction(act))

                def fake_requestChatChannelMembers(self, channelId):
                    members = []
                    if channelId in _offline_channels:
                        for nick in _offline_channels[channelId]['members']:
                            members.append({'id': 1, 'nickName': nick, 'status': 0})
                    act = _make_chat_action(CHAT_ACTIONS.requestMembers, cid=channelId, data=members)
                    BigWorld.callback(0.05, lambda: self.onChatAction(act))

                def fake_setChatActionsCallbacks(self, callbacks):
                    self.__chatActionCallbacks = callbacks

                def fake_subscribeChatAction(self, callback, action, channelId=None):
                    from ChatManager import chatManager
                    chatManager.subscribeChatAction(callback, action, channelId)

                def fake_unsubscribeChatAction(self, callback, action, channelId=None):
                    from ChatManager import chatManager
                    chatManager.unsubscribeChatAction(callback, action, channelId)

                _offline_channels[1] = {'name': 'General', 'members': [p.name]}
                _offline_next_cid[0] = 2

                p.requestSystemChatChannels = new.instancemethod(fake_requestSystemChatChannels, p, p.__class__)

                def _patch_messenger_window():
                    from messenger.gui import MessengerDispatcher as MD
                    if MD.g_instance is None:
                        BigWorld.callback(0.5, _patch_messenger_window)
                        return
                    lobby = MD.g_instance._MessengerDispatcher__lobbyWindow
                    _orig_close = lobby.__class__.close
                    def patched_close(self):
                        _orig_close(self)
                        if self.__dict__.get('_MessengerLobbyInterface__movieViewHandler'):
                            try:
                                self.__dict__['_MessengerLobbyInterface__movieViewHandler'].call('Messenger.Hide', [])
                            except: pass
                    lobby.__class__.close = patched_close
                BigWorld.callback(1.5, _patch_messenger_window)

                p.createChatChannel         = new.instancemethod(fake_createChatChannel,         p, p.__class__)
                p.enterChat                 = new.instancemethod(fake_enterChat,                 p, p.__class__)
                p.leaveChat                 = new.instancemethod(fake_leaveChat,                 p, p.__class__)
                p.broadcast                 = new.instancemethod(fake_broadcast,                 p, p.__class__)
                p.findChatChannels          = new.instancemethod(fake_findChatChannels,          p, p.__class__)
                p.requestChatChannelMembers = new.instancemethod(fake_requestChatChannelMembers, p, p.__class__)
                p.setChatActionsCallbacks   = new.instancemethod(fake_setChatActionsCallbacks,   p, p.__class__)
                p.subscribeChatAction       = new.instancemethod(fake_subscribeChatAction,       p, p.__class__)
                p.unsubscribeChatAction     = new.instancemethod(fake_unsubscribeChatAction,     p, p.__class__)

                _training_room_id = [0]

                def training_create(self, vInventoryID, arenaTypeID, roundLength,
                                    roomLifetime, isPrivate, comment):
                    _training_room_id[0] += 1
                    rid = _training_room_id[0]
                    g_playerEvents.onTrainingJoined(rid)
                    BigWorld.callback(0.1, lambda: g_playerEvents.onTrainingSettingsReceived(
                        p.name, arenaTypeID, roundLength, roomLifetime, isPrivate, comment
                    ))
                    veh_cd = veh_cds.get(vInventoryID, 0)
                    roster = {1: {'team': int(0), 'name': p.name,
                                  'vehCompDescr': int(veh_cd) if veh_cd else 0, 'state': int(0)}}
                    BigWorld.callback(0.2, lambda: g_playerEvents.onTrainingRosterChanged(roster))
                    print "[OFFLINE] training_create: id=%d arena=%s" % (rid, arenaTypeID)

                def training_join(self, roomId, vInventoryID):
                    g_playerEvents.onTrainingJoined(roomId)

                def training_leave(self):
                    g_playerEvents.onTrainingLeft()

                def training_destroy(self):
                    g_playerEvents.onTrainingLeft()

                def training_startArena(self):
                    g_playerEvents.onArenaCreated()

                def training_suspend(self): pass

                def training_changeSettings(self, roundLength, lifetime, isPrivate, callback=None):
                    if callback: BigWorld.callback(0.1, lambda: callback(0))

                def training_changeComment(self, comment, callback=None):
                    if callback: BigWorld.callback(0.1, lambda: callback(0))

                def training_changeArenaType(self, arenaTypeID, callback=None):
                    if callback: BigWorld.callback(0.1, lambda: callback(0))

                def training_assignToTeam(self, id, team, callback=None):
                    try:
                        from CurrentVehicle import g_currentVehicle
                        veh    = g_currentVehicle.vehicle
                        veh_cd = veh_cds.get(veh.inventoryId, 0) if veh else 0
                        roster = {int(id): {'team': int(team), 'name': p.name,
                                            'vehCompDescr': int(veh_cd) if veh_cd else 0, 'state': int(0)}}
                        BigWorld.callback(0.1, lambda: g_playerEvents.onTrainingRosterChanged(roster))
                    except Exception as e:
                        print "[OFFLINE] training_assignToTeam error: %s" % e
                    if callback: BigWorld.callback(0.1, lambda: callback(0))

                def requestTrainingList(self):
                    BigWorld.callback(0.1, lambda: g_playerEvents.onTrainingListReceived({}))

                p.training_create          = new.instancemethod(training_create,          p, p.__class__)
                p.training_join            = new.instancemethod(training_join,            p, p.__class__)
                p.training_leave           = new.instancemethod(training_leave,           p, p.__class__)
                p.training_destroy         = new.instancemethod(training_destroy,         p, p.__class__)
                p.training_startArena      = new.instancemethod(training_startArena,      p, p.__class__)
                p.training_suspend         = new.instancemethod(training_suspend,         p, p.__class__)
                p.training_changeSettings  = new.instancemethod(training_changeSettings,  p, p.__class__)
                p.training_changeComment   = new.instancemethod(training_changeComment,   p, p.__class__)
                p.training_changeArenaType = new.instancemethod(training_changeArenaType, p, p.__class__)
                p.training_assignToTeam    = new.instancemethod(training_assignToTeam,    p, p.__class__)
                p.requestTrainingList      = new.instancemethod(requestTrainingList,      p, p.__class__)

                p.stats._Stats__cache = off_stats
                p.inventory._Inventory__cache = {
                    'items':         dict((cd, 1) for cd in _modules_inventory.keys()),
                    'compDescr':     inv_vehicles,
                    'vehicles':      inv_data,
                    'tankmen':       t_cache,
                    'potapovQuests': {}
                }

                class FakeS(str):
                    def __getattr__(self, n): return lambda *a, **k: FakeS("")
                    def __getitem__(self, k): return FakeS("")
                AS.AccountSettings._AccountSettings__readUserSection = staticmethod(lambda *a: FakeS(""))

                if 1 in veh_cds:
                    CurrentVehicle.g_currentVehicle._CurrentVehicle__vehicle = OfflineVehicleWrapper(1)

            from gui.WindowsManager import g_windowsManager
            g_windowsManager.showLobby()

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

init_offline()

from gui.Scaleform.utils.gui_items import FittingItem

def _apply_gui_fixes():
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

_apply_gui_fixes()


def _patch_current_vehicle_property():
    try:
        from gui.Scaleform.utils.gui_items import InventoryTankman
        from adisp import async, process
        from gui.Scaleform.utils.requesters import Requester

        @async
        @process
        def _safe_currentVehicle(self, callback):
            vcls   = yield Requester('vehicle').getFromInventory()
            result = None
            if self.isInTank:
                for vcl in vcls:
                    if vcl.inventoryId == self.vehicleID:
                        result = vcl
                        break
            callback(result)
            return

        def _currentVehicle_getter(self):
            return _safe_currentVehicle(self)

        InventoryTankman.currentVehicle = property(_currentVehicle_getter)
        print "[OFFLINE] InventoryTankman.currentVehicle fixed"
    except Exception as e:
        print "[OFFLINE] patch currentVehicle error: %s" % e

_patch_current_vehicle_property()


def _patch_training():
    try:
        import sys
        training_mod = sys.modules.get('gui.Scaleform.Training')
        if training_mod is None:
            return
        TrainingRoom      = training_mod.TrainingRoom
        original_recive   = TrainingRoom._TrainingRoom__recivePlayersList

        def safe_recivePlayersList(self, players):
            safe = {}
            try:
                for pid, data in players.items():
                    safe[int(pid)] = {
                        'team':         int(data.get('team', 0)),
                        'name':         data.get('name', ''),
                        'vehCompDescr': int(data.get('vehCompDescr', 0)),
                        'state':        int(data.get('state', 0))
                    }
            except Exception as e:
                print "[OFFLINE] safe_recivePlayersList convert error: %s" % e
            original_recive(self, safe)

        TrainingRoom._TrainingRoom__recivePlayersList = safe_recivePlayersList
    except Exception as e:
        print "[OFFLINE] _patch_training failed: %s" % e

BigWorld.callback(1.0, _patch_training)