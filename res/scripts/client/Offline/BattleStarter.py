import BigWorld
import Math
import math
import game
import Keys
import cPickle
from functools import partial
import constants
from ClientArena import ClientArena
from Event import Event, EventManager
from AreaDestructibles import g_destructiblesManager
from items import vehicles
from debug_utils import LOG_NOTE, LOG_ERROR
from PlayerEvents import g_playerEvents
from helpers.DecalMap import DecalMap
from gui.WindowsManager import g_windowsManager

class _FakePositionControl(object):
    def bindToVehicle(self, flag): pass
    def followCamera(self, flag):  pass
    def moveTo(self, pos):         pass

import Vehicle
if hasattr(Vehicle, 'DumbFilter'):
    Vehicle.DumbFilter = BigWorld.WGVehicleFilter
    LOG_NOTE("[BATTLE] DumbFilter replaced with WGVehicleFilter")

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

_MAP_SPAWNS = {
    '01_karelia': {
        1: [
            [-405.14, 53.26, -398.27],
            [-391.14, 53.26, -398.27],
            [-419.14, 53.28, -398.27],
            [-405.14, 53.26, -384.27],
            [-405.14, 53.26, -412.27],
            [-395.14, 53.24, -388.27],
            [-415.14, 53.26, -388.27],
            [-395.14, 53.26, -408.27],
            [-415.14, 53.26, -408.27],
        ],
        2: [
            [396.27, 53.63, 402.37],
            [410.27, 53.72, 402.37],
            [382.27, 53.58, 402.37],
            [396.27, 54.20, 416.37],
            [396.27, 53.28, 388.37],
            [406.27, 54.15, 412.37],
            [386.27, 53.86, 412.37],
            [406.27, 53.40, 392.37],
            [386.27, 53.42, 392.37],
        ],
    },
}

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
    from ArenaType import g_cache, g_list
    if isinstance(arenaTypeID, str):
        numeric_id = None
        for num_id, name in g_list.items():
            if name == arenaTypeID:
                numeric_id = num_id
                break
        if numeric_id is None:
            LOG_ERROR("[BATTLE] load_arena_type: map name '%s' not found in g_list!" % arenaTypeID)
            return None
        arenaTypeID = numeric_id
    result = g_cache.get(arenaTypeID)
    if result is None:
        LOG_ERROR("[BATTLE] load_arena_type: UNKNOWN arenaTypeID=%s" % arenaTypeID)
    else:
        LOG_NOTE("[BATTLE] load_arena_type: OK id=%d name=%s" % (arenaTypeID, result.typeName))
    return result

def get_ground_height(spaceID, pos):
    res = BigWorld.wg_collideSegment(spaceID,
                                     Math.Vector3(pos.x, 500.0, pos.z),
                                     Math.Vector3(pos.x, -500.0, pos.z), 18)
    if res is not None:
        return res[0].y
    res = BigWorld.wg_collideSegment(spaceID,
                                     Math.Vector3(pos.x, 500.0, pos.z),
                                     Math.Vector3(pos.x, -500.0, pos.z), 128)
    if res is not None:
        return res[0].y
    return 0.0

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
        self.velocity = Math.Vector3(
            math.sin(self.yaw) * speed,
            0.0,
            math.cos(self.yaw) * speed
        )

    def getVehicleSpeed(self):
        return self.speed

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
    def __updateMatrices(self):
        m = Math.Matrix()
        m.setRotateY(self.__turretYaw)
        self.turretMatrix.setStaticTransform(m)
        m2 = Math.Matrix()
        m2.setRotateX(self.__gunPitch)
        self.gunMatrix.setStaticTransform(m2)

    def start(self): pass
    def stop(self): pass

    def getLoftedTrajectory(self):
        return self.__loftedTrajectory

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
    def __getattr__(self, name):
        return lambda *a, **kw: None

class SimpleAvatar:
    def __init__(self, name, team, spaceID):
        self.name = name
        self.team = team
        self.spaceID = spaceID
        self.playerVehicleID = None
        self.vehicleTypeDescriptor = None
        self.isOnArena = True
        self.isVehicleAlive = True
        self.inputHandler = None
        self.gunRotator = None
        self.hitTesters = set()
        self.initialVehicleSpeeds = {}
        self._ownVehicleMProv = Math.WGAdaptiveMatrixProvider()
        self._arena = None
        self._minimap = None
        self._terrainEffects = None
        self._projectileMover = None
        self.__guiConfig = {'silhouetteColors': {'self': (0,0,0,0), 'enemy': (0,0,0,0), 'friend': (0,0,0,0)}}
        self.turretMatrix = Math.WGAdaptiveMatrixProvider()
        self.gunMatrix = Math.WGAdaptiveMatrixProvider()
        self._moveForward = False
        self._moveBack = False
        self._turnLeft = False
        self._turnRight = False
        self.getCurrentShots = lambda: (0, None)
        self.currentMove = 0.0
        self.currentTurn = 0.0
        self.onSpaceLoaded = lambda: None
        self.onVehicleLeaveWorld = Event()
        self.cell = _DummyCell()
        self.base = _DummyCell()
        self.enableOwnVehicleAutorotation = False
        self._flyCam         = None
        self.positionControl = _FakePositionControl()
        class _FakeModel(object):
            def getSound(self, *a): return None
            def attach(self, *a): pass
            def detach(self, *a): pass
        self.newFakeModel = lambda *a, **k: _FakeModel()
        self.onGunShotChanged        = Event()
        self.onGunReloadTimeSet      = Event()
        self.onAutoAimVehicleEnter   = Event()
        self.onAutoAimVehicleLeave   = Event()
        self.onShootingStateChanged  = Event()
        self.onCameraChanged         = Event()
        self.onVehicleEnterWorld     = Event()
        self.autoAim = lambda target: None
        self.enableOwnVehicleAutorotation = lambda flag: None
        self.showTracer = lambda *a, **kw: None
        self.explodeProjectile = lambda *a, **kw: None
        self.showDamageFromShot = lambda *a, **kw: None
        self.showDamageFromExplosion = lambda *a, **kw: None
        LOG_NOTE("[BATTLE][AVATAR] SimpleAvatar created: name=%s team=%d" % (name, team))

    def initSpace(self): pass
    def vehicle_onEnterWorld(self, vehicle):
        LOG_NOTE("[BATTLE][AVATAR] vehicle_onEnterWorld: vehID=%s" % getattr(vehicle, 'id', '?'))
    def vehicle_onLeaveWorld(self, vehicle):
        LOG_NOTE("[BATTLE][AVATAR] vehicle_onLeaveWorld: vehID=%s" % getattr(vehicle, 'id', '?'))
    def bindToVehicle(self, doBind, vehicleID=None):
        LOG_NOTE("[BATTLE][AVATAR] bindToVehicle: doBind=%s vehicleID=%s" % (doBind, vehicleID))
        if doBind and vehicleID is not None:
            veh = BigWorld.entity(vehicleID)
            if veh:
                self._ownVehicleMProv.target = veh.matrix
                LOG_NOTE("[BATTLE][AVATAR] bindToVehicle: matrix linked OK")
            else:
                LOG_ERROR("[BATTLE][AVATAR] bindToVehicle: entity %s not found!" % vehicleID)
        else:
            self._ownVehicleMProv.target = None
    def getVehicleAttached(self):
        return BigWorld.entity(self.playerVehicleID) if self.playerVehicleID else None
    def getOwnVehicleMatrix(self):
        return self._ownVehicleMProv
    def getOwnVehiclePosition(self):
        target = self._ownVehicleMProv.target
        if target is not None:
            return Math.Matrix(target).translation
        veh = BigWorld.entity(self.playerVehicleID) if self.playerVehicleID else None
        if veh is not None:
            return veh.position
        return Math.Vector3(0, 0, 0)
    def getOwnVehicleSpeeds(self):
        return (0.0, 0.0)
    def getOwnVehicleShotDispersionAngle(self, turretRotationSpeed, isShot=False):
        return 0.01
    def handleKey(self, isDown, key, mods):
        if not getattr(self, 'inputHandler', None): return False
        try:
            return self.inputHandler.handleKeyEvent(key, isDown)
        except:
            return False
    def handleKeyEvent(self, event):
        if not getattr(self, 'inputHandler', None): return False
        isDown, key, mods, isRepeat = game.convertKeyEvent(event)

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

        try:
            return self.inputHandler.handleKeyEvent(event)
        except:
            return False

    def handleMouseEvent(self, dx, dy, dz):
        if getattr(self, 'inputHandler', None):
            try:
                if self.inputHandler.handleMouseEvent(dx, dy, dz):
                    return True
            except:
                pass
        if self.gunRotator:
            self.gunRotator.updateRotation(dx * 0.005, dy * 0.005)
            return True
        return False

    def prerequisites(self):
        return []
    def onRecreateDevice(self): pass
    def leaveArena(self): pass
    def setForcedGuiControlMode(self, enable, stopVehicle=True):
        from gui.Cursor import forceShowCursor
        forceShowCursor(enable)
    def addModel(self, model): BigWorld.addModel(model)
    def delModel(self, model): BigWorld.delModel(model)
    def handleVehicleCollidedVehicle(self, vehA, vehB, hitPt, time): pass
    def shoot(self):
        veh = self.getVehicleAttached()
        if not veh:
            LOG_NOTE("[BATTLE][AVATAR] shoot: no vehicle attached")
            return
        if hasattr(veh, 'showShooting'):
            try:
                veh.showShooting()
            except Exception as e:
                LOG_NOTE("[BATTLE] showShooting failed: %s" % e)
        try:
            import Math, random
            descr = self.vehicleTypeDescriptor
            if descr is None:
                return
            shotDescr = descr.shot
            speed   = shotDescr['speed']
            gravity = shotDescr['gravity']

            if hasattr(self, '_offline_matrix'):
                vehMat = Math.Matrix(self._offline_matrix)
            else:
                vehMat = Math.Matrix(veh.matrix)

            turretMat = Math.Matrix(self.turretMatrix)
            gunMat    = Math.Matrix(self.gunMatrix)

            worldDir = Math.Matrix()
            worldDir.setIdentity()
            worldDir.postMultiply(gunMat)
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
            shotPos = gunWorldPos
            refVelocity = gunWorldDir * speed
            shotID      = random.randint(1, 999999)

            pm = self.projectileMover
            try:
                shotEffectsIndex = descr.shot['shell']['effectsIndex']
                effectsDescr = vehicles.g_cache.shotEffects[shotEffectsIndex]
            except Exception as e:
                LOG_NOTE("[BATTLE] shotEffects lookup failed: %s, trying index 0" % e)
                try:
                    effectsDescr = vehicles.g_cache.shotEffects[0]
                except:
                    effectsDescr = None
            if effectsDescr is None:
                LOG_NOTE("[BATTLE] shoot: no effectsDescr, skipping projectile")
                return
            pm.add(
                shotID,
                effectsDescr,
                gravity,
                shotPos,
                refVelocity,
                shotPos,
                True
            )
            LOG_NOTE("[BATTLE] Projectile launched: shotID=%d speed=%.1f" % (shotID, speed))
        except Exception as e:
            LOG_NOTE("[BATTLE] shoot() failed: %s" % e)
    def lockOn(self, target): pass
    def onAmmoButtonPressed(self, index):
        try:
            descr = self.vehicleTypeDescriptor
            if not descr:
                return
            shots = descr.gun['shots']
            if index >= len(shots):
                return
            self._currentShellIndex = index
            bw = getattr(self._offline_battle, 'battleWindow', None)
            if bw and hasattr(bw, 'ammoPanel'):
                for i in range(len(shots)):
                    bw.ammoPanel.setSelectedAsCurrent(i, i == index)
            LOG_NOTE("[BATTLE] Shell switched to index %d" % index)
        except Exception as e:
            LOG_NOTE("[BATTLE] onAmmoButtonPressed failed: %s" % e)
    def onAvatarReady(self): pass
    def getCurrentVehicleId(self):
        return self.playerVehicleID
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
    def guiConfig(self):
        return self.__guiConfig

class OfflineBattle:

    def _patch_vehicle(self):
        import Vehicle as V
        if hasattr(V.Vehicle, '_patched_alive'):
            return
        V.Vehicle.onLeaveWorld   = lambda self: setattr(self, 'isStarted', False)
        V.Vehicle.isAlive        = lambda self: True
        V.Vehicle.set_isCrewActive = lambda self, prev=None: None
        V.Vehicle.set_health     = lambda self, prev=None: None
        def safe_collideDynamic(self_veh, mass, damage, direction):
            pass
        V.Vehicle.collideDynamic = safe_collideDynamic
        V.Vehicle._patched_alive = True
        LOG_NOTE("[BATTLE] Vehicle patched (alive/health/leave)")

    def __init__(self):
        self.spaceID = None
        self.arena = None
        self.playerAvatar = None
        self.vehicles = []
        self._oldPlayer = None
        self.battleWindow = None
        self._prebattleStartTime = None
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
                Battle._patched_for_offline_cleanup = True
        except: pass
        LOG_NOTE("[BATTLE] OfflineBattle instance created")

    def start(self, arenaTypeID=None, playerVehicleName=None, botCount=1):
        LOG_NOTE("[BATTLE] start() called: arenaTypeID=%s playerVehicleName=%s botCount=%d" % (arenaTypeID, playerVehicleName, botCount))

        if playerVehicleName is None:
            try:
                from CurrentVehicle import g_currentVehicle
                veh = g_currentVehicle.vehicle
                if veh and veh.descriptor:
                    playerDescr = veh.descriptor
                    playerCompDescr = playerDescr.makeCompactDescr()
                    LOG_NOTE("[BATTLE] Using garage vehicle: %s" % playerDescr.type.name)
                else:
                    LOG_NOTE("[BATTLE] g_currentVehicle.vehicle is None or has no descriptor!")
                    playerCompDescr = None
            except Exception as e:
                LOG_NOTE("[BATTLE] Can't get garage vehicle: %s" % e)
                playerCompDescr = None
        else:
            playerCompDescr = None

        if isinstance(arenaTypeID, (tuple, list)):
            arenaTypeID = arenaTypeID[0]

        LOG_NOTE("[BATTLE] Final arenaTypeID='%s'" % arenaTypeID)
        self._patch_decalmap()
        self._patch_gui()
        try:
            from gui.Scaleform.Waiting import Waiting
            Waiting.hide()
        except: pass

        _load_cfg(arenaTypeID)

        DecalMap.g_instance = None
        try:
            BigWorld.wg_addDecal = lambda *a, **k: 0
        except: pass

        self._oldPlayer = BigWorld.player()
        LOG_NOTE("[BATTLE] Old player saved: %s" % type(self._oldPlayer).__name__)

        try:
            from Offline import Manager
            playerName = Manager._player_name
            LOG_NOTE("[BATTLE] Player name from Manager: '%s'" % playerName)
        except Exception as e:
            LOG_NOTE("[BATTLE] Could not get player name from Manager: %s" % e)
            playerName = "Commander"

        self.playerAvatar = SimpleAvatar(playerName, 1, -1)
        self.playerAvatar._offline_battle = self
        BigWorld.player = lambda: self.playerAvatar
        LOG_NOTE("[BATTLE] BigWorld.player() now returns SimpleAvatar")

        arenaType = load_arena_type(arenaTypeID)
        if arenaType is None:
            LOG_ERROR("[BATTLE] arenaType is None, aborting!")
            return
        
        arenaTypeName = arenaTypeID if isinstance(arenaTypeID, str) else arenaType.typeName
        if isinstance(arenaTypeID, str):
            from ArenaType import g_list
            for num_id, name in g_list.items():
                if name == arenaTypeID:
                    arenaTypeID = num_id
                    break
        LOG_NOTE("[BATTLE] arenaTypeID resolved: int=%s name=%s" % (arenaTypeID, arenaTypeName))
        
        BigWorld.worldDrawEnabled(False)
        BigWorld.wg_useAttachmentBboxesInShadowCasting(True)
        BigWorld.wg_setIndoorMainLightDir(_SHADOW_LIGHT_DIR)

        self.spaceID = BigWorld.createSpace()
        LOG_NOTE("[BATTLE] Created spaceID=%s" % self.spaceID)
        BigWorld.addSpaceGeometryMapping(self.spaceID, None, 'spaces/' + arenaTypeName)
        LOG_NOTE("[BATTLE] Geometry mapped: spaces/%s" % arenaTypeName)

        import ResMgr
        geomDir = 'spaces/' + arenaTypeName
        try:
            for file in ResMgr.listFiles(geomDir):
                if file.endswith('.bsp'):
                    BigWorld.addSpaceGeometryMapping(self.spaceID, None, geomDir + '/' + file)
        except:
            pass

        self.playerAvatar.spaceID = self.spaceID
        BigWorld.createEntity("Account", self.spaceID, 0,
            _V_START_POS,
            (math.radians(_V_START_ANGLES[2]),
             math.radians(_V_START_ANGLES[1]),
             math.radians(_V_START_ANGLES[0])), {})

        cam = BigWorld.CursorCamera()
        cam.spaceID       = self.spaceID
        cam.pivotMaxDist  = _CAM_START_DIST
        cam.maxDistHalfLife  = _CAM_FLUENCY
        cam.turningHalfLife  = _CAM_FLUENCY
        cam.movementHalfLife = 0.0
        cam.pivotPosition    = _CAM_PIVOT_POS

        matSrc = Math.Matrix()
        matSrc.setRotateYPR((math.radians(_CAM_START_ANGLES[1]),
                             math.radians(_CAM_START_ANGLES[0]), 0.0))
        cam.source = matSrc
        matTgt = Math.Matrix()
        matTgt.setTranslate(_CAM_START_TARGET_POS)
        cam.target = matTgt

        BigWorld.camera(cam)
        BigWorld.worldDrawEnabled(True)
        g_destructiblesManager.startSpace(self.spaceID)

        self.arena = ClientArena(arenaTypeID, 0, 0)
        self.playerAvatar.arena = self.arena
        LOG_NOTE("[BATTLE] ClientArena created OK")

        try:
            g_playerEvents.onAvatarBecomePlayer()
            LOG_NOTE("[BATTLE] onAvatarBecomePlayer fired OK")
        except Exception as e:
            LOG_NOTE("[BATTLE] onAvatarBecomePlayer failed: %s" % e)

        def get_pos_on_ground(x, z):
            groundY = get_ground_height(self.spaceID, Math.Vector3(x, 0, z))
            if groundY <= 0.0:
                groundY = 10.0
            return Math.Vector3(x, groundY + 1.0, z)

        if playerCompDescr:
            playerDescr = vehicles.VehicleDescr(compactDescr=playerCompDescr)
        else:
            playerDescr = vehicles.VehicleDescr(typeName=playerVehicleName or "ussr:T-26")

        LOG_NOTE("[BATTLE] Player vehicle descriptor: %s" % playerDescr.type.name)

        _spawns_pre = _MAP_SPAWNS.get(arenaTypeName, None)
        if _spawns_pre and 1 in _spawns_pre and len(_spawns_pre[1]) > 0:
            _sp = _spawns_pre[1][0]
            playerPos = Math.Vector3(_sp[0], _sp[1], _sp[2])
            LOG_NOTE("[BATTLE] Player spawn from _MAP_SPAWNS: %s" % playerPos)
        else:
            playerPos = get_pos_on_ground(0, 0)
        LOG_NOTE("[BATTLE] Player spawn pos: %s" % playerPos)

        playerVehID = BigWorld.createEntity("Vehicle", self.spaceID, 0,
                                            playerPos, (0,0,0),
                                            {"publicInfo": {
                                                "health": playerDescr.maxHealth,
                                                "compDescr": playerDescr.makeCompactDescr(),
                                                "name": self.playerAvatar.name,
                                                "team": 1,
                                                "isAlive": True,
                                                "isAvatarReady": True
                                            }})
        LOG_NOTE("[BATTLE] Player Vehicle entity created: ID=%s" % playerVehID)
        self.playerAvatar.playerVehicleID = playerVehID
        self.playerAvatar.vehicleTypeDescriptor = playerDescr
        self.vehicles.append((playerVehID, playerDescr, True))

        self.arena.vehicles[playerVehID] = {
            'vehicleType': playerDescr,
            'name': self.playerAvatar.name,
            'team': 1,
            'isAlive': True,
            'isAvatarReady': True,
            'health': playerDescr.maxHealth,
            'frags': 0,
            'clanAbbrev': '',
            'prebattleID': 0,
            'vehicleID': playerVehID
        }

        # --- Спавн ботов ---
        spawns = _MAP_SPAWNS.get(arenaTypeID, None)

        if spawns:
            LOG_NOTE("[BATTLE] Using map spawns for '%s'" % arenaTypeID)
            team1 = spawns[1]
            team2 = spawns[2]

            def _spawn_at(typeName, team, coords):
                try:
                    botDescr = vehicles.VehicleDescr(typeName=typeName)
                except Exception as e:
                    LOG_ERROR("[BATTLE] Bot descriptor failed '%s': %s" % (typeName, e))
                    return
                for sp in coords:
                    pos = Math.Vector3(sp[0], sp[1], sp[2])
                    try:
                        botID = BigWorld.createEntity("Vehicle", self.spaceID, 0,
                                                      pos, (0, 0, 0),
                                                      {"publicInfo": {
                                                          "compDescr": botDescr.makeCompactDescr(),
                                                          "name": "Bot_%s" % typeName.split(':')[-1],
                                                          "team": team,
                                                          "isAlive": True,
                                                          "isAvatarReady": True
                                                      }})
                        self.vehicles.append((botID, botDescr, False))
                        self.arena.vehicles[botID] = {
                            'vehicleType': botDescr,
                            'name': "Bot_%s_%d" % (typeName.split(':')[-1], i+1),
                            'team': team,
                            'isAlive': True,
                            'isAvatarReady': True,
                            'health': botDescr.maxHealth,
                            'frags': 0,
                            'clanAbbrev': '',
                            'clanDBID': 0,
                            'accountDBID': 0,
                            'prebattleID': 0,
                            'vehicleID': botID
                        }
                        LOG_NOTE("[BATTLE] Bot spawned: %s team=%d pos=%s" % (typeName, team, pos))
                    except Exception as e:
                        LOG_ERROR("[BATTLE] Bot spawn failed: %s" % e)

            _spawn_at("ussr:IS-7",    team=1, coords=team1[1:2])
            _spawn_at("ussr:MS-1",    team=1, coords=team1[2:3])
            _spawn_at("ussr:ISU-152", team=1, coords=team1[3:4])
            _spawn_at("ussr:SU-26",   team=1, coords=team1[4:5])

            _spawn_at("ussr:IS-4",    team=2, coords=team2[0:1])

        else:
            LOG_NOTE("[BATTLE] Map '%s' not in _MAP_SPAWNS, using fallback" % arenaTypeID)
            playerPos = get_pos_on_ground(0, 0)

            def _spawn_bots(typeName, team, count, startX, startZ, stepX, stepZ):
                try:
                    botDescr = vehicles.VehicleDescr(typeName=typeName)
                except Exception as e:
                    LOG_ERROR("[BATTLE] Cannot create bot descriptor '%s': %s" % (typeName, e))
                    return
                for i in range(count):
                    x = startX + i * stepX
                    z = startZ + i * stepZ
                    pos = get_pos_on_ground(x, z)
                    try:
                        botID = BigWorld.createEntity("Vehicle", self.spaceID, 0,
                                                      pos, (0, 0, 0),
                                                      {"publicInfo": {
                                                          "compDescr": botDescr.makeCompactDescr(),
                                                          "name": "Bot_%s_%d" % (typeName.split(':')[-1], i+1),
                                                          "team": team,
                                                          "isAlive": True,
                                                          "isAvatarReady": True
                                                      }})
                        self.vehicles.append((botID, botDescr, False))
                        self.arena.vehicles[botID] = {
                            'vehicleType': botDescr,
                            'name': "Bot_%s_%d" % (typeName.split(':')[-1], i+1),
                            'team': team,
                            'isAlive': True,
                            'isAvatarReady': True,
                            'health': botDescr.maxHealth,
                            'frags': 0,
                            'clanAbbrev': '',
                            'vehicleID': botID
                        }
                        LOG_NOTE("[BATTLE] Bot spawned: %s ID=%s pos=%s" % (typeName, botID, pos))
                    except Exception as e:
                        LOG_ERROR("[BATTLE] Bot spawn failed '%s' #%d: %s" % (typeName, i, e))

            _spawn_bots("ussr:IS-4",   team=2, count=botCount, startX=100, startZ=100, stepX=25, stepZ=25)
            _spawn_bots("ussr:IS-7",   team=1, count=botCount, startX=5,   startZ=5,   stepX=20, stepZ=20)
            _spawn_bots("ussr:MS-1",   team=1, count=botCount, startX=-6,  startZ=6,   stepX=20, stepZ=20)
            _spawn_bots("ussr:ISU-152",team=1, count=botCount, startX=6,   startZ=-6,  stepX=20, stepZ=20)
            _spawn_bots("ussr:SU-26",  team=1, count=botCount, startX=-7,  startZ=-7,  stepX=20, stepZ=20)

        LOG_NOTE("[BATTLE] Total vehicles in scene: %d" % len(self.vehicles))

        prereqs = []
        for vehID, descr, _ in self.vehicles:
            prereqs.append(descr.chassis['models']['undamaged'])
            prereqs.append(descr.hull['models']['undamaged'])
            prereqs.append(descr.turret['models']['undamaged'])
            prereqs.append(descr.gun['models']['undamaged'])
            prereqs += descr.prerequisites()
            for ht in descr.getHitTesters():
                if ht.bspModelName and not ht.isBspModelLoaded():
                    prereqs.append(ht.bspModelName)

        from Settings import g_instance as settings
        fakeModel = settings.scriptConfig.readString('fakeModel', 'objects/fake_model.model')
        prereqs.append(fakeModel)

        self._patch_startVisual()
        LOG_NOTE("[BATTLE] Loading %d resources..." % len(prereqs))
        BigWorld.loadResourceListBG(prereqs, partial(self._onResourcesLoaded))

    def _onResourcesLoaded(self, resourceRefs):
        LOG_NOTE("[BATTLE] _onResourcesLoaded called, refs count=%d" % len(resourceRefs))
        failed = [k for k in resourceRefs.keys() if resourceRefs[k] is None]
        if failed:
            LOG_NOTE("[BATTLE] WARNING: %d resources failed to load: %s" % (len(failed), failed[:5]))
        else:
            LOG_NOTE("[BATTLE] All resources loaded OK")
        BigWorld.callback(0.1, lambda: self._finalizeInit(resourceRefs))

    def _applyAimPatches(self):
        try:
            from AvatarInputHandler import aims
            if not aims._g_aimState:
                aims.clearState()
            max_health = 200
            if self.playerAvatar.vehicleTypeDescriptor:
                max_health = self.playerAvatar.vehicleTypeDescriptor.maxHealth
            aims._g_aimState['health']['cur'] = max_health
            aims._g_aimState['health']['max'] = max_health
            aims._g_aimState['reload']['isReloading'] = False
            aims._g_aimState['reload']['duration'] = 0
            aims._g_aimState['reload']['startTime'] = None
            aims._g_aimState['ammoStock'] = 0
            original_setHealth = aims.Aim._setHealth
            def safe_setHealth(self, cur, max):
                if cur is None or max is None or max == 0:
                    return
                original_setHealth(self, cur, max)
            aims.Aim._setHealth = safe_setHealth
            LOG_NOTE("[BATTLE] _applyAimPatches OK, maxHealth=%d" % max_health)
        except Exception as e:
            LOG_NOTE("[BATTLE] aims patch skipped/failed: %s" % e)
    
    def _restore_matrix(self_battle):
        try:
            if hasattr(self_battle, '_offline_matrix') and hasattr(self_battle, '_cur_yaw'):
                self_battle._offline_matrix.setRotateYPR((
                    self_battle._cur_yaw,
                    getattr(self_battle, '_cur_pitch', 0.0),
                    getattr(self_battle, '_cur_roll', 0.0)
                ))
                self_battle._offline_matrix.translation = self_battle._cur_pos
        except:
            pass
    
    def _patch_control_modes(self):
        _self = self
        try:
            arcade_ctrl = self.playerAvatar.inputHandler._AvatarInputHandler__ctrls.get('arcade')
            if arcade_ctrl is None:
                LOG_ERROR("[BATTLE] _patch_control_modes: arcade control mode NOT FOUND!")
                return
            LOG_NOTE("[BATTLE] _patch_control_modes: arcade ctrl found: %s" % type(arcade_ctrl).__name__)
            arcade_ctrl._ArcadeControlMode__activateAlternateMode = lambda *a, **kw: None
            arcade_ctrl.onChangeControlMode = lambda *args, **kwargs: None
            if hasattr(arcade_ctrl, '_ArcadeControlMode__cam'):
                cam = arcade_ctrl._ArcadeControlMode__cam
                if hasattr(cam, '_ArcadeCamera__onChangeControlMode'):
                    cam._ArcadeCamera__onChangeControlMode = lambda *a, **kw: None
        except Exception as e:
            LOG_NOTE("[BATTLE] control_modes patch failed: %s" % e)
            return

        import CommandMapping
        original_handleKey = arcade_ctrl.handleKeyEvent
        def patched_handleKeyEvent(isDown, key, mods, event=None):
            cmdMap = CommandMapping.g_instance
            avatar = BigWorld.player()

            moved = False
            if cmdMap.isFired(CommandMapping.CMD_MOVE_FORWARD, key):
                avatar._moveForward = isDown
                moved = True
            if cmdMap.isFired(CommandMapping.CMD_MOVE_BACKWARD, key):
                avatar._moveBack = isDown
                moved = True
            if cmdMap.isFired(CommandMapping.CMD_ROTATE_LEFT, key):
                avatar._turnLeft = isDown
                moved = True
            if cmdMap.isFired(CommandMapping.CMD_ROTATE_RIGHT, key):
                avatar._turnRight = isDown
                moved = True

            if moved:
                avatar.currentMove = (1.0 if avatar._moveForward else 0.0) - (1.0 if avatar._moveBack else 0.0)
                avatar.currentTurn = (1.0 if avatar._turnRight else 0.0) - (1.0 if avatar._turnLeft else 0.0)
                LOG_NOTE("[BATTLE][INPUT] key=%d isDown=%s move=%.1f turn=%.1f" % (key, isDown, avatar.currentMove, avatar.currentTurn))
                return True
            if cmdMap.isFired(CommandMapping.CMD_CM_SHOOT, key) and isDown:
                avatar.shoot()
                return True
            if cmdMap.isFired(CommandMapping.CMD_CM_ALTERNATE_MODE, key) and isDown:
                try:
                    aih = avatar.inputHandler
                    veh = BigWorld.entity(avatar.playerVehicleID)

                    shotPoint = None
                    try:
                        shotPoint = aih.getDesiredShotPoint()
                    except:
                        pass
                    
                    if shotPoint is None and veh:
                        import Math as _M
                        try:
                            turretMat = _M.Matrix(avatar.turretMatrix)
                            gunMat    = _M.Matrix(avatar.gunMatrix)
                            vehMat    = _M.Matrix(veh.matrix)
                            worldDir  = _M.Matrix()
                            worldDir.setIdentity()
                            worldDir.postMultiply(gunMat)
                            worldDir.postMultiply(turretMat)
                            worldDir.postMultiply(vehMat)
                            fwd = worldDir.applyVector(_M.Vector3(0, 0, 1))
                            shotPoint = veh.position + fwd * 300.0
                        except:
                            shotPoint = veh.position + _M.Vector3(0, 0, 300)
                    
                    if shotPoint is None:
                        return True

                    if veh and hasattr(_self, '_offline_matrix'):
                        try:
                            import Math as _M2
                            clean = _M2.Matrix()
                            clean.setRotateYPR((
                                getattr(_self, '_cur_yaw', 0.0),
                                0.0,
                                0.0
                            ))
                            clean.translation = _self._cur_pos
                            _self_battle._offline_matrix.setRotateYPR((
                                getattr(self_battle, '_cur_yaw', 0.0),
                                0.0,
                                0.0
                            ))
                        except:
                            pass
                    
                    descr = avatar.vehicleTypeDescriptor
                    isATSPG = 'AT-SPG' in descr.type.tags if descr else False
                    
                    _saved_bind = avatar.bindToVehicle
                    avatar.bindToVehicle = lambda *a, **kw: None
                    try:
                        aih.onControlModeChanged('sniper',
                            preferredPos=shotPoint,
                            aimingMode=0,
                            saveZoom=False,
                            isATSPG=isATSPG)
                        LOG_NOTE("[BATTLE] Switched to sniper mode OK")
                    finally:
                        avatar.bindToVehicle = _saved_bind
                        BigWorld.callback(0.1, lambda: _restore_matrix(_self))
                except Exception as e:
                    LOG_NOTE("[BATTLE] Sniper switch failed: %s" % e)
                return True
            return original_handleKey(isDown, key, mods, event)

        arcade_ctrl.handleKeyEvent = patched_handleKeyEvent
        LOG_NOTE("[BATTLE] Movement patched into ArcadeControlMode OK")
        original_mouse = arcade_ctrl.handleMouseEvent
        def patched_handleMouseEvent(dx, dy, dz):
            result = original_mouse(dx, dy, dz)
            avatar = BigWorld.player()
            gr = getattr(avatar, 'gunRotator', None)
            if gr:
                try:
                    cam = arcade_ctrl._ArcadeControlMode__cam
                    camDir = cam.camera.direction
                except:
                    pass
            return result
        arcade_ctrl.handleMouseEvent = patched_handleMouseEvent
        LOG_NOTE("[BATTLE] Mouse rotation patched into ArcadeControlMode OK")

        import AvatarInputHandler.control_modes as _cm
        _orig_sniper_enable = _cm.SniperControlMode.enable
        def _safe_sniper_enable(self_ctrl, **args):
            try:
                _orig_sniper_enable(self_ctrl, **args)
            except Exception as e:
                LOG_NOTE("[BATTLE] SniperControlMode.enable partial fail: %s" % e)
                self_ctrl._SniperControlMode__isEnabled = True
        _cm.SniperControlMode.enable = _safe_sniper_enable

        _orig_sniper_mouse = _cm.SniperControlMode.handleMouseEvent
        def _safe_sniper_mouse(self_ctrl, dx, dy, dz):
            if not self_ctrl._SniperControlMode__isEnabled:
                return False
            return _orig_sniper_mouse(self_ctrl, dx, dy, dz)
        _cm.SniperControlMode.handleMouseEvent = _safe_sniper_mouse

        _orig_sniper_key = _cm.SniperControlMode.handleKeyEvent
        def _safe_sniper_key(self_ctrl, isDown, key, mods, event=None):
            if not self_ctrl._SniperControlMode__isEnabled:
                return False
            return _orig_sniper_key(self_ctrl, isDown, key, mods, event)
        _cm.SniperControlMode.handleKeyEvent = _safe_sniper_key

        _orig_sniper_marker = _cm.SniperControlMode.showGunMarker
        def _safe_sniper_marker(self_ctrl, flag):
            if not self_ctrl._SniperControlMode__isEnabled:
                return
            return _orig_sniper_marker(self_ctrl, flag)
        _cm.SniperControlMode.showGunMarker = _safe_sniper_marker

        LOG_NOTE("[BATTLE] SniperControlMode asserts patched OK")

        from VehicleGunRotator import VehicleGunRotator as _VGR
        def _safe_updateShotPoint(self_gr, shotPoint):
            self_gr._VehicleGunRotator__prevSentShotPoint = shotPoint
        _VGR._VehicleGunRotator__updateShotPointOnServer = _safe_updateShotPoint
        LOG_NOTE("[BATTLE] VehicleGunRotator.__updateShotPointOnServer patched OK")
        _orig_onTick = _VGR._VehicleGunRotator__onTick
        def _safe_onTick(self_gr):
            try: _orig_onTick(self_gr)
            except: pass
        _VGR._VehicleGunRotator__onTick = _safe_onTick

    def _patch_decalmap(self):
        try:
            if DecalMap.g_instance:
                DecalMap.g_instance.getIndex = lambda name: 0
            BigWorld.wg_addDecal = lambda *args, **kwargs: 0
            if hasattr(BigWorld, 'WGVehicleFashion'):
                BigWorld.WGVehicleFashion.setTrackTraces = lambda *a, **kw: None
        except Exception as e:
            LOG_NOTE("[BATTLE] DecalMap patch failed: %s" % e)

        try:
            if hasattr(BigWorld, 'WGStickerModel'):
                BigWorld.WGStickerModel.addSticker = lambda self, layer, texCoords, model, start, end, sizes, up: 0
            if hasattr(BigWorld, 'WGVehicleFashion'):
                BigWorld.WGVehicleFashion.setTrackTraces = lambda self, group, textureIndex, centerOffset, size: None
            if hasattr(BigWorld, 'wg_addDecal'):
                BigWorld.wg_addDecal = lambda *args, **kwargs: 0
            try:
                from helpers import bound_effects
                if hasattr(bound_effects.ModelBoundEffects, 'addNew'):
                    original_addNew = bound_effects.ModelBoundEffects.addNew
                    def safe_addNew(self, mat, effects, stages, entity=None):
                        try:
                            return original_addNew(self, mat, effects, stages, entity)
                        except Exception as e:
                            LOG_NOTE("[BATTLE] bound_effects.addNew failed: %s" % e)
                    bound_effects.ModelBoundEffects.addNew = safe_addNew
            except:
                pass
            LOG_NOTE("[BATTLE] Disabled decals and effects OK")
        except Exception as e:
            LOG_NOTE("[BATTLE] Failed to disable decals: %s" % e)
    def _patch_gui(self):
        try:
            from gui.Scaleform import BattleLoading as _BLMod

            if not getattr(_BLMod.BattleLoading, '_patched_offline', False):

                orig_populateUI = _BLMod.BattleLoading.populateUI
                def _safe_populateUI(self_bl, proxy):
                    arena = getattr(BigWorld.player(), 'arena', None)
                    if arena:
                        for vData in arena.vehicles.values():
                            vData.setdefault('prebattleID', 0)
                            vData.setdefault('accountDBID', 0)
                            vData.setdefault('clanDBID', 0)
                            vData.setdefault('clanAbbrev', '')
                    try:
                        orig_populateUI(self_bl, proxy)
                    except Exception as e:
                        LOG_NOTE("[BATTLE] BattleLoading.populateUI failed: %s" % e)
                _BLMod.BattleLoading.populateUI = _safe_populateUI

                orig_BL_up = _BLMod.BattleLoading._BattleLoading__updatePlayers
                def _safe_BL_up(self_bl, *args):
                    arena = getattr(self_bl, '_BattleLoading__arena', None)
                    if arena:
                        for vData in arena.vehicles.values():
                            vData.setdefault('prebattleID', 0)
                            vData.setdefault('accountDBID', 0)
                            vData.setdefault('clanDBID', 0)
                            vData.setdefault('clanAbbrev', '')
                    try:
                        orig_BL_up(self_bl, *args)
                    except Exception as e:
                        LOG_NOTE("[BATTLE] BattleLoading.__updatePlayers failed: %s" % e)
                _BLMod.BattleLoading._BattleLoading__updatePlayers = _safe_BL_up

                _BLMod.BattleLoading._patched_offline = True
                LOG_NOTE("[BATTLE] BattleLoading patched OK")
        except Exception as e:
            LOG_NOTE("[BATTLE] BattleLoading patch failed: %s" % e)

        try:
            from gui.Scaleform import Battle as _BattleMod
            if not getattr(_BattleMod.Battle, '_patched_offline', False):
                orig_Battle_up = _BattleMod.Battle._Battle__updatePlayers
                def _safe_Battle_up(self_b, *args):
                    arena = getattr(self_b, '_Battle__arena', None)
                    if arena:
                        for vData in arena.vehicles.values():
                            vData.setdefault('prebattleID', 0)
                            vData.setdefault('accountDBID', 0)
                            vData.setdefault('clanDBID', 0)
                            vData.setdefault('clanAbbrev', '')
                    try:
                        orig_Battle_up(self_b, *args)
                    except Exception as e:
                        LOG_NOTE("[BATTLE] Battle.__updatePlayers failed: %s" % e)
                _BattleMod.Battle._Battle__updatePlayers = _safe_Battle_up
                _BattleMod.Battle._patched_offline = True
                LOG_NOTE("[BATTLE] Battle.__updatePlayers patched OK")
        except Exception as e:
            LOG_NOTE("[BATTLE] Battle patch failed: %s" % e)
    def _patch_startVisual(self):
        import VehicleAppearance as VA
        if hasattr(VA.VehicleAppearance, '_patched_for_offline'):
            LOG_NOTE("[BATTLE] _patch_startVisual: already patched, skipping")
            return

        original_setupFashion = VA._setupVehicleFashion
        def safe_setupVehicleFashion(fashion, vehicle, isCrashedTrack=False):
            _orig_setTrackTraces = getattr(fashion.__class__, 'setTrackTraces', None)
            fashion.setTrackTraces = lambda *a, **kw: None
            try:
                return original_setupFashion(fashion, vehicle, isCrashedTrack)
            except Exception as e:
                LOG_NOTE("[BATTLE] safe_setupVehicleFashion error: %s" % e)
            finally:
                try:
                    del fashion.setTrackTraces
                except: pass

        original_updateMovement = VA.VehicleAppearance._VehicleAppearance__updateMovementSounds
        def safe_updateMovement(self_va):
            try:
                return original_updateMovement(self_va)
            except Exception as e:
                pass
        VA.VehicleAppearance._VehicleAppearance__updateMovementSounds = safe_updateMovement

        VA.VehicleAppearance._VehicleAppearance__getDamageModelsState = lambda self, h: 'undamaged'

        original_setup = VA.VehicleAppearance._VehicleAppearance__setupModels
        def safe_setupModels(self_va):
            self_va._VehicleAppearance__curDamageState = 'undamaged'
            try:
                original_setup(self_va)
            except Exception as e:
                err = str(e)
                if 'wg_fashion' in err or 'wg_gunRecoil' in err or 'setTrackTraces' in err or 'groupName' in err or 'trace' in err:
                    LOG_NOTE("[BATTLE] safe_setupModels caught known error: %s" % err)
                    try:
                        m = self_va._VehicleAppearance__vehicle.model
                        if not hasattr(m, 'wg_fashion'):
                            m.wg_fashion = type('FakeFashion', (), {
                                'setTrackTraces': lambda *a, **kw: None,
                                'receiveShotImpulse': lambda *a, **kw: None,
                                'hideTracks': lambda *a, **kw: None,
                                'movementInfo': None,
                                'staticPitchSwingForce': 0,
                                'disableSwinging': False
                            })()
                        if not hasattr(m, 'wg_gunRecoil'):
                            m.wg_gunRecoil = None
                    except: pass
                else:
                    LOG_ERROR("[BATTLE] safe_setupModels UNEXPECTED error: %s" % err)
                    raise
        VA.VehicleAppearance._VehicleAppearance__setupModels = safe_setupModels

        original_start = VA.VehicleAppearance.start
        def safe_start(self_va, vehicle, prereqs=None):
            LOG_NOTE("[BATTLE][VA] VehicleAppearance.start called for vehID=%s" % getattr(vehicle, 'id', '?'))
            from helpers.DecalMap import DecalMap as DM
            old_getIndex = DM.getIndex
            DM.getIndex = lambda self, name: 0
            old_addDecal = getattr(BigWorld, 'wg_addDecal', None)
            BigWorld.wg_addDecal = lambda *a, **k: 0
            self_va._VehicleAppearance__curDamageState = 'undamaged'
            try:
                result = original_start(self_va, vehicle, prereqs)
                LOG_NOTE("[BATTLE][VA] VehicleAppearance.start OK for vehID=%s" % getattr(vehicle, 'id', '?'))
                return result
            except Exception as e:
                err = str(e)
                if 'setTrackTraces' in err or 'groupName' in err or 'trace' in err or 'wg_fashion' in err:
                    LOG_NOTE("[BATTLE][VA] safe_start caught known error: %s" % err)
                else:
                    LOG_ERROR("[BATTLE][VA] safe_start UNEXPECTED error: %s" % err)
                    raise
            finally:
                DM.getIndex = old_getIndex
                if old_addDecal:
                    BigWorld.wg_addDecal = old_addDecal

        VA.VehicleAppearance.start = safe_start
        VA.VehicleAppearance._patched_for_offline = True
        if not hasattr(VA, '_original_setupVehicleFashion'):
            VA._original_setupVehicleFashion = VA._setupVehicleFashion
            def safe_setup_fashion(fashion, vehicle, isCrashedTrack=False):
                try:
                    VA._original_setupVehicleFashion(fashion, vehicle, isCrashedTrack)
                except RuntimeError as e:
                    if "setTrackTraces" not in str(e):
                        raise
            VA._setupVehicleFashion = safe_setup_fashion
        LOG_NOTE("[BATTLE] _patch_startVisual: VA.VehicleAppearance patched OK")
        VA.VehicleAppearance._VehicleAppearance__destroyTrackDamageSounds = lambda self: setattr(self, '_VehicleAppearance__trackSounds', [None, None])
        LOG_NOTE("[BATTLE] __setupTrackDamageSounds patched to no-op OK")

    def _updateBattleUI(self):
        if self.battleWindow:
            try:
                if hasattr(self.battleWindow, 'damagePanel'):
                    veh = BigWorld.entity(self.playerAvatar.playerVehicleID)
                    if veh:
                        self.battleWindow.damagePanel.updateHealth(veh.health)
                if hasattr(self.battleWindow, '_Battle__updatePlayers'):
                    self.battleWindow._Battle__updatePlayers = lambda *args: None
                LOG_NOTE("[BATTLE] Battle UI updated OK")
            except Exception as e:
                LOG_NOTE("[BATTLE] UI update error: %s" % e)

    def _fixTankIndicator(self):
        if not self.battleWindow:
            return
        try:
            self.battleWindow.call('battle.tankIndicator.setType', ['Tank'])
            LOG_NOTE("[BATTLE] Tank indicator set to Tank OK")
        except Exception as e:
            LOG_NOTE("[BATTLE] Failed to fix tank indicator: %s" % e)

    def _setPrebattleTimer(self, duration):
        now = BigWorld.time()
        self._prebattleStartTime = now
        _prebattle = getattr(constants, 'ARENA_PERIOD_PREBATTLE', getattr(getattr(constants, 'ARENA_PERIOD', None), 'PREBATTLE', 1))
        period_data = (_prebattle, now + duration, duration, None)
        _upd_period = getattr(constants, 'ARENA_UPDATE_PERIOD',
                      getattr(constants.ARENA_UPDATE, 'PERIOD', 3))
        self.arena.update(_upd_period, cPickle.dumps(period_data))
        LOG_NOTE("[BATTLE] Pre-battle timer set to %.1f seconds" % duration)

    def _startMinimap(self):
        try:
            from gui.Minimap import Minimap
            mm = Minimap()
            prereqs = mm.prerequisites()

            def _onReady(resourceRefs):
                try:
                    mm.start()
                    self.playerAvatar._minimap = mm
                    LOG_NOTE("[BATTLE] Minimap started OK")
                    for vehID, descr, isPlayer in self.vehicles:
                        if not isPlayer:
                            try:
                                mm.notifyVehicleStart(vehID)
                            except Exception as e:
                                LOG_NOTE("[BATTLE] notifyVehicleStart(%d) failed: %s" % (vehID, e))
                    LOG_NOTE("[BATTLE] Minimap: notifyVehicleStart sent for %d bots" % (len(self.vehicles) - 1))
                except Exception as e:
                    LOG_NOTE("[BATTLE] Minimap.start() failed: %s" % e)

            if prereqs:
                BigWorld.loadResourceListBG(prereqs, _onReady)
            else:
                _onReady(None)

        except Exception as e:
            LOG_NOTE("[BATTLE] _startMinimap error: %s" % e)
    
    def _setupTankIndicator(self):
        try:
            bw = g_windowsManager.battleWindow
            if bw is None:
                BigWorld.callback(0.5, self._setupTankIndicator)
                return

            aim = getattr(self.playerAvatar.inputHandler, 'aim', None)
            if aim is None:
                BigWorld.callback(0.5, self._setupTankIndicator)
                return

            try:
                aim.attachTankIndicator(weakref.ref(bw))
                LOG_NOTE("[BATTLE] tankIndicator attached OK")
            except Exception as e:
                LOG_NOTE("[BATTLE] tankIndicator attach failed: %s" % e)

            try:
                aim.attachCruiseCtrl(weakref.ref(bw))
                LOG_NOTE("[BATTLE] cruiseCtrl attached OK")
            except Exception as e:
                LOG_NOTE("[BATTLE] cruiseCtrl attach failed: %s" % e)

            try:
                descr = self.playerAvatar.vehicleTypeDescriptor
                vTags = descr.type.tags
                if 'SPG' in vTags:
                    vtype = 'SPG'
                elif 'AT-SPG' in vTags:
                    vtype = 'AT-SPG'
                else:
                    vtype = 'Tank'
                bw.call('battle.tankIndicator.setType', [vtype])
                LOG_NOTE("[BATTLE] tankIndicator type set: %s" % vtype)
            except Exception as e:
                LOG_NOTE("[BATTLE] tankIndicator setType failed: %s" % e)

            try:
                veh = BigWorld.entity(self.playerAvatar.playerVehicleID)
                if veh and getattr(veh, 'appearance', None):
                    mc = getattr(bw.component, 'tankIndicator', None)
                    if mc:
                        mc.wg_hullMatProv   = self.playerAvatar.getOwnVehicleMatrix()
                        mc.wg_turretMatProv = veh.appearance.turretMatrix
                        LOG_NOTE("[BATTLE] tankIndicator matrices linked OK")
            except Exception as e:
                LOG_NOTE("[BATTLE] tankIndicator matrices failed: %s" % e)

        except Exception as e:
            LOG_NOTE("[BATTLE] _setupTankIndicator failed: %s" % e)
    
    def _setupAmmoPanel(self):
        try:
            bw = g_windowsManager.battleWindow
            if bw is None:
                BigWorld.callback(0.5, self._setupAmmoPanel)
                return
            self.battleWindow = bw

            cp = getattr(bw, 'consumablesPanel', None)
            if cp is None:
                LOG_NOTE("[BATTLE] AmmoPanel: consumablesPanel not ready, retrying...")
                BigWorld.callback(0.5, self._setupAmmoPanel)
                return

            descr = self.playerAvatar.vehicleTypeDescriptor
            if not descr:
                LOG_NOTE("[BATTLE] AmmoPanel: vehicleTypeDescriptor is None")
                return

            # --- Снаряды из ангара ---
            shots = descr.gun['shots']
            if not shots:
                LOG_NOTE("[BATTLE] AmmoPanel: no shots in gun descriptor")
                return

            shell_counts = {}
            try:
                from CurrentVehicle import g_currentVehicle
                veh = g_currentVehicle.vehicle
                if veh:
                    shells = list(getattr(veh, 'shells', []) or [])
                    for j in range(0, len(shells) - 1, 2):
                        shell_counts[shells[j]] = shells[j + 1]
            except Exception as e:
                LOG_NOTE("[BATTLE] AmmoPanel: shell_counts failed: %s" % e)

            for idx, shot in enumerate(shots):
                shellDescr = shot['shell']
                piercingPower = shot['piercingPower']
                shell_cd = shellDescr.get('compactDescr', 0)
                count = shell_counts.get(shell_cd, 0)
                if count == 0:
                    try:
                        default_shells = vehicles.getDefaultAmmoForGun(descr.gun)
                        for di in range(0, len(default_shells) - 1, 2):
                            if default_shells[di] == shell_cd:
                                count = default_shells[di + 1]
                                break
                    except:
                        count = 30
                try:
                    cp.addShellSlot(idx, count, shellDescr, piercingPower)
                    LOG_NOTE("[BATTLE] AmmoPanel: shell idx=%d kind=%s count=%d OK" % (
                        idx, shellDescr.get('kind', '?'), count))
                except Exception as e:
                    LOG_NOTE("[BATTLE] AmmoPanel: addShellSlot idx=%d failed: %s" % (idx, e))

            try:
                cp.setCurrentShell(0)
                cp.setNextShell(0)
            except Exception as e:
                LOG_NOTE("[BATTLE] AmmoPanel: setCurrentShell failed: %s" % e)

            self.playerAvatar._currentShellIndex = 0
            LOG_NOTE("[BATTLE] AmmoPanel: %d shell types loaded" % len(shots))

            from items.vehicles import NUM_EQUIPMENT_SLOTS
            eq_compacts = [0, 0, 0]
            try:
                from CurrentVehicle import g_currentVehicle as _cv
                hangar_veh = _cv.vehicle
                if hangar_veh:
                    eq_compacts = list(getattr(hangar_veh, 'equipments', [0, 0, 0]) or [0, 0, 0])
                    while len(eq_compacts) < NUM_EQUIPMENT_SLOTS:
                        eq_compacts.append(0)
            except Exception as e:
                LOG_NOTE("[BATTLE] AmmoPanel: equipments read failed: %s" % e)

            for eq_idx in range(NUM_EQUIPMENT_SLOTS):
                cd = eq_compacts[eq_idx] if eq_idx < len(eq_compacts) else 0
                if cd and cd != 0:
                    try:
                        from items.vehicles import getDictDescr
                        eq_descr = getDictDescr(cd)
                        cp.addEquipmentSlot(eq_idx, 1, eq_descr)
                        LOG_NOTE("[BATTLE] AmmoPanel: equipment slot %d = %s OK" % (
                            eq_idx, eq_descr.get('name', cd)))
                    except Exception as e:
                        LOG_NOTE("[BATTLE] AmmoPanel: equipment slot %d failed: %s, adding empty" % (eq_idx, e))
                        cp.addEmptyEquipmentSlot(eq_idx)
                else:
                    cp.addEmptyEquipmentSlot(eq_idx)
                    LOG_NOTE("[BATTLE] AmmoPanel: equipment slot %d empty" % eq_idx)

        except Exception as e:
            LOG_NOTE("[BATTLE] _setupAmmoPanel failed: %s" % e)

#################### тут танк ЕДИТ ################
#################### тут танк ЕДИТ ################
#################### тут танк ЕДИТ ################

    def __movementTick(self):
        if not self.playerAvatar or not self.playerAvatar.isOnArena:
            BigWorld.callback(0.05, self.__movementTick)
            return

        veh = BigWorld.entity(self.playerAvatar.playerVehicleID)
        if veh is None or not getattr(veh, 'isStarted', False):
            BigWorld.callback(0.05, self.__movementTick)
            return

        flt = getattr(veh, 'filter', None)
        if flt is None:
            BigWorld.callback(0.05, self.__movementTick)
            return

        move = self.playerAvatar.currentMove
        turn = self.playerAvatar.currentTurn
        descr = self.playerAvatar.vehicleTypeDescriptor
        if descr is None:
            BigWorld.callback(0.05, self.__movementTick)
            return

        now = BigWorld.time()
        if not hasattr(self, '_last_tick_time'):
            self._last_tick_time = now
        dt = now - self._last_tick_time
        if dt <= 0.0 or dt > 0.2: 
            dt = 0.05
        self._last_tick_time = now

        fwdLimit = descr.physics['speedLimits'][0]
        bwdLimit = descr.physics['speedLimits'][1]
        rotLimit = (descr.physics.get('rotationSpeedLimit')
            or descr.physics.get('rotationSpeed')
            or descr.physics.get('turretRotationSpeed')
            or 0.5)

        if not hasattr(self, '_cur_speed'):
            self._cur_speed = 0.0
            self._cur_rot   = 0.0

        accel = 3.0
        if move > 0:
            self._cur_speed = min(self._cur_speed + accel * dt, fwdLimit)
        elif move < 0:
            self._cur_speed = max(self._cur_speed - accel * dt, -bwdLimit)
        else:
            if abs(self._cur_speed) < accel * dt:
                self._cur_speed = 0.0
            elif self._cur_speed > 0:
                self._cur_speed -= accel * dt
            else:
                self._cur_speed += accel * dt

        if turn != 0:
            self._cur_rot = turn * rotLimit
        else:
            self._cur_rot = 0.0

        if not hasattr(self, '_cur_yaw'):
            self._cur_yaw = 0.0
        self._cur_yaw += self._cur_rot * dt

        import math as _math
        if not hasattr(self, '_cur_pos'):
            self._cur_pos = Math.Vector3(veh.position)
        self._cur_pos.x += _math.sin(self._cur_yaw) * self._cur_speed * dt
        self._cur_pos.z += _math.cos(self._cur_yaw) * self._cur_speed * dt

        my_id = self.playerAvatar.playerVehicleID
        next_x = self._cur_pos.x
        next_z = self._cur_pos.z
        for vehID, _, _ in self.vehicles:
            if vehID == my_id:
                continue
            other_veh = BigWorld.entity(vehID)
            if not other_veh or not getattr(other_veh, 'isStarted', False):
                continue
            diff_x = next_x - other_veh.position.x
            diff_z = next_z - other_veh.position.z
            dist = _math.sqrt(diff_x * diff_x + diff_z * diff_z)
            min_dist = 3.5
            if dist < min_dist and dist > 0.001:
                overlap = min_dist - dist
                nx = diff_x / dist
                nz = diff_z / dist
                next_x += nx * overlap * 0.6
                next_z += nz * overlap * 0.6
                self._cur_speed *= 0.3
                try:
                    new_bot_pos = Math.Vector3(
                        other_veh.position.x - nx * overlap * 0.4,
                        other_veh.position.y,
                        other_veh.position.z - nz * overlap * 0.4
                    )
                    if hasattr(other_veh, '_offline_matrix'):
                        other_veh._offline_matrix.translation = new_bot_pos
                except:
                    pass
        self._cur_pos.x = next_x
        self._cur_pos.z = next_z

        groundY = get_ground_height(self.spaceID, self._cur_pos)
        if groundY > 0:
            self._cur_pos.y = groundY + 0.5
        pos = self._cur_pos

        groundY = get_ground_height(self.spaceID, pos)
        if groundY > -100:
            pos.y = groundY

        import math as _math

        L = 2.5
        W = 1.5

        fx = _math.sin(self._cur_yaw)
        fz = _math.cos(self._cur_yaw)
        rx = _math.cos(self._cur_yaw)
        rz = -_math.sin(self._cur_yaw)

        h_front = get_ground_height(self.spaceID, Math.Vector3(pos.x + fx*L, 0, pos.z + fz*L))
        h_back  = get_ground_height(self.spaceID, Math.Vector3(pos.x - fx*L, 0, pos.z - fz*L))
        h_right = get_ground_height(self.spaceID, Math.Vector3(pos.x + rx*W, 0, pos.z + rz*W))
        h_left  = get_ground_height(self.spaceID, Math.Vector3(pos.x - rx*W, 0, pos.z - rz*W))


        target_pitch = _math.atan2(h_back - h_front, L * 2.0)
        target_roll  = _math.atan2(h_right - h_left, W * 2.0)

        if not hasattr(self, '_cur_pitch'):
            self._cur_pitch = 0.0
            self._cur_roll  = 0.0
        
        self._cur_pitch += (target_pitch - self._cur_pitch) * 0.15
        self._cur_roll  += (target_roll - self._cur_roll) * 0.15

        direction = Math.Vector3(self._cur_yaw, self._cur_pitch, self._cur_roll)

        try:
            flt.allowLagProcessing = True
            flt.setInitialSpeeds(self._cur_speed, self._cur_rot)
            
            if not hasattr(self, '_offline_matrix'):
                self._offline_matrix = Math.Matrix()
                self._offline_matrix.setRotateYPR((self._cur_yaw, self._cur_pitch, self._cur_roll))
                self._offline_matrix.translation = pos
                self._offline_servo = BigWorld.Servo(self._offline_matrix)
                try:
                    veh.model.delMotor(veh.model.motors[0])
                    veh.model.addMotor(self._offline_servo)
                    LOG_NOTE("[MOVE] Offline Servo motor installed OK")
                except Exception as e2:
                    LOG_NOTE("[MOVE] Servo install failed: %s" % e2)
                    del self._offline_matrix
            else:
                self._offline_matrix.setRotateYPR((self._cur_yaw, self._cur_pitch, self._cur_roll))
                self._offline_matrix.translation = pos

            if abs(self._cur_speed) > 0.01 or abs(self._cur_rot) > 0.001:
                LOG_NOTE("[MOVE] pos=(%.1f,%.1f,%.1f) spd=%.2f yaw=%.3f" % (
                    pos.x, pos.y, pos.z, self._cur_speed, self._cur_yaw))
        except Exception as e:
            LOG_NOTE("[MOVE] tick failed: %s" % e)
        
        try:
            flags = 0
            if move > 0:  flags |= 1
            if move < 0:  flags |= 2
            if turn < 0:  flags |= 4
            if turn > 0:  flags |= 8
            veh.showPlayerMovementCommand(flags)

            if abs(self._cur_speed) < 0.1 and move == 0:
                power = 1
            elif abs(self._cur_speed) > self._cur_speed * 0.5 and move != 0:
                power = 3
            else:
                power = 2
            dirFlags = flags & 0x03
            engine_mode = (power, dirFlags)
            if getattr(veh, 'appearance', None):
                try:
                    if veh.appearance.changeEngineMode(engine_mode) is not False:
                        pass
                except Exception as _em_e:
                    pass
                try:
                    flt = getattr(veh, 'filter', None)
                    if flt and hasattr(flt, 'speedInfo'):
                        flt.setInitialSpeeds(self._cur_speed, self._cur_rot)
                except: pass
        except: pass
        try:
            mm = getattr(self.playerAvatar, '_minimap', None)
            if mm and hasattr(mm, 'onVehicleMove'):
                mm.onVehicleMove(self.playerAvatar.playerVehicleID, pos, self._cur_yaw)
        except:
            pass
        BigWorld.callback(0.05, self.__movementTick)

    def _finalizeInit(self, resourceRefs):
        LOG_NOTE("[BATTLE] _finalizeInit: checking %d vehicles..." % len(self.vehicles))
        for vehID, _, _ in self.vehicles:
            veh = BigWorld.entity(vehID)
            if veh is None or not veh.inWorld:
                LOG_NOTE("[BATTLE] _finalizeInit: vehID=%s not ready yet (inWorld=%s), retrying..." % (
                    vehID, getattr(veh, 'inWorld', 'N/A')))
                BigWorld.callback(0.1, lambda: self._finalizeInit(resourceRefs))
                return

        LOG_NOTE("[BATTLE] All %d vehicles in world, starting visual init..." % len(self.vehicles))
        try:
            from gui.Scaleform.Waiting import Waiting
            Waiting.hide()
        except: pass
        self._patch_vehicle()

        for vehID, descr, isPlayer in self.vehicles:
            veh = BigWorld.entity(vehID)
            if not veh:
                LOG_ERROR("[BATTLE] _finalizeInit: entity %d is None!" % vehID)
                continue
            veh.isPlayer = isPlayer
            veh.isCrewActive = True
            veh.health = descr.maxHealth
            if getattr(veh, 'appearance', None):
                try:
                    veh.appearance.changeEngineMode((1, 0))
                except Exception as e:
                    LOG_NOTE("[BATTLE] changeEngineMode init failed vehID=%d: %s" % (vehID, e))
            veh.damageStickers = ()
            veh.publicStateModifiers = []
            try:
                veh.typeDescriptor.keepPrereqs(resourceRefs)
                veh._Vehicle__prereqs = resourceRefs
            except: pass
            if not isPlayer:
                try:
                    m = Math.Matrix()
                    m.setRotateYPR((0, 0, 0))
                    m.translation = veh.position
                    servo = BigWorld.Servo(m)
                    if hasattr(veh, 'model'):
                        veh.model.addMotor(servo)
                    veh._offline_matrix = m
                    veh._offline_servo = servo
                except: pass
            LOG_NOTE("[BATTLE] Vehicle %d prepared: isPlayer=%s health=%d" % (vehID, isPlayer, descr.maxHealth))

        from VehicleGunRotator import VehicleGunRotator
        try:
            real_gr = VehicleGunRotator(self.playerAvatar)
            descr = self.playerAvatar.vehicleTypeDescriptor
            turretSpeed = descr.turret['rotationSpeed']
            gunSpeed    = turretSpeed * 0.5               
            real_gr._VehicleGunRotator__turretRotationSpeed = turretSpeed
            real_gr._VehicleGunRotator__gunRotationSpeed    = gunSpeed
            self.playerAvatar.gunRotator = real_gr
            LOG_NOTE("[BATTLE] Real VehicleGunRotator installed, turretSpeed=%.4f" % turretSpeed)
        except Exception as e:
            LOG_NOTE("[BATTLE] VehicleGunRotator failed, using Dummy: %s" % e)
            self.playerAvatar.gunRotator = DummyGunRotator()

        self.playerAvatar.turretMatrix = self.playerAvatar.gunRotator.turretMatrix
        self.playerAvatar.gunMatrix = self.playerAvatar.gunRotator.gunMatrix

        from AvatarInputHandler import AvatarInputHandler
        aih = AvatarInputHandler()


        try:
            aih.onCameraChanged += lambda mode: None
        except Exception as _e:
            LOG_NOTE("[BATTLE] onCameraChanged subscribe failed: %s" % _e)

        _orig_start = aih.start
        def _safe_aih_start():
            try:
                _orig_start()
                LOG_NOTE("[BATTLE] AvatarInputHandler.start() OK")
            except TypeError as _te:
                LOG_NOTE("[BATTLE] AvatarInputHandler.start() TypeError (suppressed): %s" % _te)
                try:
                    aih._AvatarInputHandler__isStarted = True
                    aih._AvatarInputHandler__isArenaStarted = False
                    aih._AvatarInputHandler__isGUIVisible = True
                    aih._AvatarInputHandler__curCtrl.enable(
                        ctrlState=__import__('control_modes').dumpStateEmpty()
                    )
                    aih.onCameraChanged('arcade')
                except Exception as _e2:
                    LOG_NOTE("[BATTLE] Manual AIH init also failed: %s" % _e2)
            except Exception as _e:
                LOG_NOTE("[BATTLE] AvatarInputHandler.start() other error: %s" % _e)

        self.playerAvatar.inputHandler = aih
        _safe_aih_start()
        BigWorld.callback(2.0, self._setupTankIndicator)
        LOG_NOTE("[BATTLE] AvatarInputHandler init done")

        try:
            import sys
            control_modes = sys.modules.get('AvatarInputHandler.control_modes')
            if control_modes:
                def _patched_flash_enable(self, state):
                    if state is not None and 'reload' in state:
                        if 'start_time' in state['reload'] and 'startTime' not in state['reload']:
                            state['reload']['startTime'] = state['reload']['start_time']
                    return True
                if hasattr(control_modes, '_FlashGunMarker'):
                    control_modes._FlashGunMarker.enable = _patched_flash_enable
                if hasattr(control_modes, '_SPGFlashGunMarker'):
                    control_modes._SPGFlashGunMarker.enable = _patched_flash_enable
                LOG_NOTE("[BATTLE] GunMarker patch OK")
        except Exception as e:
            LOG_NOTE("[BATTLE] GunMarker patch failed: %s" % e)

        self._patch_control_modes()
        self._applyAimPatches()
        self.playerAvatar.inputHandler.setReloading(0)

        playerVeh = BigWorld.entity(self.playerAvatar.playerVehicleID)
        LOG_NOTE("[BATTLE] Player vehicle entity: %s" % playerVeh)

        if playerVeh and hasattr(playerVeh, 'filter') and playerVeh.filter:
            if hasattr(playerVeh, 'appearance') and playerVeh.appearance:
                try:
                    fashion = playerVeh.appearance.modelsDesc['chassis']['model'].wg_fashion
                    fashion.movementInfo = playerVeh.filter.movementInfo
                    LOG_NOTE("[BATTLE] movementInfo linked to filter OK")
                except Exception as e:
                    LOG_NOTE("[BATTLE] movementInfo link failed: %s" % e)

        if playerVeh:
            cam = BigWorld.camera()
            if cam is None:
                cam = BigWorld.CursorCamera()
                LOG_NOTE("[BATTLE] Created new camera")
            cam.spaceID = self.spaceID
            cam.target = playerVeh.matrix
            BigWorld.camera(cam)
            LOG_NOTE("[BATTLE] Camera targeted to player vehicle OK")
            self.playerAvatar.bindToVehicle(True, self.playerAvatar.playerVehicleID)
            BigWorld.worldDrawEnabled(True)
        else:
            LOG_ERROR("[BATTLE] playerVeh is None at finalizeInit! playerVehicleID=%s" % self.playerAvatar.playerVehicleID)

        g_windowsManager.startBattle()
        self.battleWindow = g_windowsManager.battleWindow
        LOG_NOTE("[BATTLE] g_windowsManager.startBattle() called, battleWindow=%s" % self.battleWindow)

        try:
            import VehicleAppearance as VA
            if not hasattr(VA, '_trackTraces_patched'):
                import BigWorld as BW
                if hasattr(BW, 'WGVehicleFashion'):
                    BW.WGVehicleFashion.setTrackTraces = lambda *a, **kw: None
                VA._trackTraces_patched = True
        except: pass

        for vehID, descr, isPlayer in self.vehicles:
            veh = BigWorld.entity(vehID)
            if not veh:
                LOG_ERROR("[BATTLE] startVisual: entity %d is None!" % vehID)
                continue
            veh.health       = descr.maxHealth
            veh.isCrewActive = True
            if not getattr(veh, 'isStarted', False):
                try:
                    LOG_NOTE("[BATTLE] Calling startVisual for vehID=%d isPlayer=%s" % (vehID, isPlayer))
                    veh.startVisual()
                    veh.isStarted = True
                    LOG_NOTE("[BATTLE] startVisual OK for vehID=%d" % vehID)
                    if isPlayer and getattr(veh, 'appearance', None):
                        try:
                            veh.appearance.turretMatrix.target = self.playerAvatar.gunRotator.turretMatrix
                            veh.appearance.gunMatrix.target    = self.playerAvatar.gunRotator.gunMatrix
                            LOG_NOTE("[BATTLE] turretMatrix/gunMatrix linked to DummyGunRotator OK")
                            try:
                                self.playerAvatar.gunRotator.start()
                                LOG_NOTE("[BATTLE] gunRotator.start() OK")
                            except Exception as e:
                                LOG_NOTE("[BATTLE] gunRotator.start() failed: %s" % e)
                        except Exception as e:
                            LOG_NOTE("[BATTLE] matrix link failed: %s" % e)
                    flt = getattr(veh, 'filter', None)
                    LOG_NOTE("[BATTLE] After startVisual: filter=%s type=%s" % (flt, type(flt).__name__ if flt else 'None'))
                except Exception as e:
                    veh.isStarted = True
                    LOG_ERROR("[BATTLE] startVisual FAILED for vehID=%d: %s" % (vehID, e))

            if isPlayer:
                flt = getattr(veh, 'filter', None)
                if flt is not None:
                    try:
                        flt.allowStrafeCompensation = False
                        flt.allowLagProcessing = False
                        try:
                            if hasattr(veh, 'appearance') and veh.appearance:
                                modelsDesc = getattr(veh.appearance, 'modelsDesc', None)
                                if modelsDesc and 'chassis' in modelsDesc:
                                    model = modelsDesc['chassis'].get('model')
                                    if model and not hasattr(model, 'wg_fashion'):
                                        model.wg_fashion = type('FakeFashion', (), {
                                            'setTrackTraces': lambda *a, **kw: None,
                                            'receiveShotImpulse': lambda *a, **kw: None,
                                            'hideTracks': lambda *a, **kw: None,
                                            'movementInfo': flt.movementInfo,
                                            'staticPitchSwingForce': 0.0,
                                            'disableSwinging': False,
                                        })()
                                        LOG_NOTE("[BATTLE] FakeFashion injected into chassis model OK")
                        except Exception as e:
                            LOG_NOTE("[BATTLE] FakeFashion inject failed: %s" % e)
                        LOG_NOTE("[BATTLE] WGVehicleFilter configured OK for player veh %d (type=%s)" % (vehID, type(flt).__name__))
                    except Exception as e:
                        LOG_NOTE("[BATTLE] WGVehicleFilter config failed: %s" % e)
                else:
                    LOG_ERROR("[BATTLE] CRITICAL: filter=None for PLAYER veh %d after startVisual! Movement will NOT work!" % vehID)

        self.arena.onPeriodChange += self._onPeriodChange

        self._setPrebattleTimer(10.0)

        BigWorld.worldDrawEnabled(True)
        BigWorld.callback(1.5, g_windowsManager.showBattle)

        try:
            import MusicController as _MC
            import SoundGroups
            mc = _MC.g_musicController
            if mc:
                mc.stop()
                LOG_NOTE("[BATTLE] MusicController: stopped lobby music OK")
            SoundGroups.g_instance.enableSounds('arena', True)
            LOG_NOTE("[BATTLE] SoundGroups: arena sounds enabled OK")
        except Exception as e:
            LOG_NOTE("[BATTLE] Music init failed: %s" % e)

        def _startBattleMusic():
            try:
                import MusicController as _MC
                mc = _MC.g_musicController
                if mc is None:
                    return
                arena = self.playerAvatar.arena
                if arena is None:
                    return
                arenaType = arena.typeDescriptor
                import FMOD
                ambientName = getattr(arenaType, 'ambientSound', None)
                if ambientName:
                    snd = FMOD.getSound(ambientName)
                    if snd:
                        snd.play()
                        LOG_NOTE("[BATTLE] Arena ambient started: %s" % ambientName)
                musicName = getattr(arenaType, 'music', None)
                if musicName:
                    snd = FMOD.getSound(musicName)
                    if snd:
                        snd.play()
                        LOG_NOTE("[BATTLE] Arena music started: %s" % musicName)
            except Exception as e:
                LOG_NOTE("[BATTLE] _startBattleMusic failed: %s" % e)

        BigWorld.callback(1.0, _startBattleMusic)
        
        LOG_NOTE("[BATTLE] showBattle called")

        BigWorld.callback(0.05, self.__movementTick)
        from gui.Cursor import forceShowCursor
        forceShowCursor(True)
        BigWorld.callback(0.5, self._fixTankIndicator)
        BigWorld.worldDrawEnabled(True)
        self.playerAvatar.onAvatarReady()
        g_playerEvents.onAvatarReady()
        LOG_NOTE("[BATTLE] onAvatarReady fired OK")

        BigWorld.callback(0.5, self._updateBattleUI)
        BigWorld.callback(1.5, self._startMinimap)
        BigWorld.callback(1.0, self._setupAmmoPanel)

        def switch_to_battle():
            LOG_NOTE("[BATTLE] Switching arena period to BATTLE")
            _prebattle = getattr(constants, 'ARENA_PERIOD_PREBATTLE', getattr(getattr(constants, 'ARENA_PERIOD', None), 'PREBATTLE', 1))
            if self.arena.period == _prebattle:
                _battle = getattr(constants, 'ARENA_PERIOD_BATTLE', getattr(constants.ARENA_PERIOD, 'BATTLE', 2))
                _upd = getattr(constants, 'ARENA_UPDATE_PERIOD', getattr(constants.ARENA_UPDATE, 'PERIOD', 3))
                battle_period_data = (_battle, BigWorld.time(), 1800.0, None)
                self.arena.update(_upd, cPickle.dumps(battle_period_data))
        BigWorld.callback(20.0, switch_to_battle)

        LOG_NOTE("[BATTLE] _finalizeInit completed successfully!")

    def _onPeriodChange(self, period, periodEndTime, periodLength, addInfo):
        LOG_NOTE("[BATTLE] _onPeriodChange: period=%d periodLength=%.1f" % (period, periodLength))
        if self.playerAvatar and getattr(self.playerAvatar, 'inputHandler', None):
            _battle = getattr(constants, 'ARENA_PERIOD_BATTLE', getattr(constants.ARENA_PERIOD, 'BATTLE', 2))
            self.playerAvatar.inputHandler._AvatarInputHandler__isArenaStarted = (period == _battle)
            if period == _battle:
                LOG_NOTE("[BATTLE] Period changed to BATTLE — enabling controls")
                try:
                    from AvatarInputHandler import aims
                    if self.playerAvatar.vehicleTypeDescriptor:
                        max_health = self.playerAvatar.vehicleTypeDescriptor.maxHealth
                        aims._g_aimState['health']['cur'] = max_health
                        aims._g_aimState['health']['max'] = max_health
                except:
                    pass
                try:
                    if self.playerAvatar.playerVehicleID is None:
                        self.playerAvatar.playerVehicleID = self.vehicles[0][0]
                        LOG_NOTE("[BATTLE] Restored playerVehicleID after period change")

                    try:
                        self.playerAvatar.inputHandler._AvatarInputHandler__isArenaStarted = True
                        LOG_NOTE("[BATTLE] __isArenaStarted forced True")
                    except Exception as e:
                        LOG_NOTE("[BATTLE] Could not force __isArenaStarted: %s" % e)

                    _saved_bind = self.playerAvatar.bindToVehicle
                    self.playerAvatar.bindToVehicle = lambda *a, **kw: None
                    try:
                        self.playerAvatar.inputHandler.onControlModeChanged('arcade')
                    finally:
                        self.playerAvatar.bindToVehicle = _saved_bind
                    LOG_NOTE("[BATTLE] onControlModeChanged done, bindToVehicle restored")

                    self.playerAvatar.bindToVehicle(True, self.playerAvatar.playerVehicleID)

                    veh = BigWorld.entity(self.playerAvatar.playerVehicleID)
                    if veh:
                        cam = BigWorld.camera()
                        if cam and hasattr(cam, 'target'):
                            cam.target = veh.matrix
                            LOG_NOTE("[BATTLE] Camera re-targeted after period change OK")
                        BigWorld.worldDrawEnabled(True)
                        self._updateBattleUI()
                        BigWorld.callback(0.5, self._fixTankIndicator)
                    else:
                        LOG_ERROR("[BATTLE] _onPeriodChange: player vehicle entity is None!")
                except Exception as e:
                    LOG_ERROR("[BATTLE] Failed to change control mode: %s" % e)

        _afterbattle = getattr(constants, 'ARENA_PERIOD_AFTERBATTLE', getattr(constants.ARENA_PERIOD, 'AFTERBATTLE', 3))
        if period == _afterbattle:
            LOG_NOTE("[BATTLE] Period AFTERBATTLE — finishing battle")
            self._finishBattle()

    def _finishBattle(self):
        LOG_NOTE("[BATTLE] _finishBattle called")
        if self.playerAvatar and getattr(self.playerAvatar, '_minimap', None):
            try:
                self.playerAvatar._minimap.destroy()
                self.playerAvatar._minimap = None
            except: pass
        if self.playerAvatar and getattr(self.playerAvatar, 'inputHandler', None):
            try:
                self.playerAvatar.inputHandler.stop()
            except: pass
        if self.playerAvatar and getattr(self.playerAvatar, 'gunRotator', None):
            try:
                self.playerAvatar.gunRotator.stop()
                self.playerAvatar.gunRotator.destroy()
            except: pass
        for vehID, _, _ in self.vehicles:
            try:
                veh = BigWorld.entity(vehID)
                if veh and getattr(veh, 'isStarted', False):
                    veh.stopVisual()
                    veh.isStarted = False
            except Exception as e:
                LOG_NOTE("[BATTLE] Ignored error while stopping visual for %d: %s" % (vehID, e))
        if self.battleWindow:
            try:
                self.battleWindow.close()
            except: pass
            self.battleWindow = None
        BigWorld.clearAllSpaces()
        BigWorld.player = lambda: self._oldPlayer
        LOG_NOTE("[BATTLE] Restored old player, returning to lobby")
        try:
            g_windowsManager.showLobby()
        except: pass

def start_offline_battle(arena_id=None):
    LOG_NOTE("[BATTLE] start_offline_battle called: arena_id=%s" % arena_id)
    if arena_id is None or arena_id == -1:
        try:
            from Offline import Manager
            arena_id = Manager._selected_arena or '05_prohorovka'
            LOG_NOTE("[BATTLE] Arena from Manager._selected_arena: '%s'" % arena_id)
        except Exception as e:
            LOG_NOTE("[BATTLE] Could not get arena from Manager: %s" % e)
            arena_id = '05_prohorovka'
    LOG_NOTE("[BATTLE] Final arena: '%s'" % arena_id)
    battle = OfflineBattle()
    battle.start(arena_id, botCount=1)