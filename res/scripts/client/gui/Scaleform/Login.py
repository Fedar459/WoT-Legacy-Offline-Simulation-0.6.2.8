# Python bytecode 2.6 (decompiled from Python 2.7)
# Embedded file name: /scripts/client/gui/Scaleform/Login.py
# Compiled at: 2010-11-21 23:41:13
import BigWorld, ResMgr, Settings, MusicController
from ConnectionManager import _getClientUpdateUrl, connectionManager
from debug_utils import LOG_CURRENT_EXCEPTION, LOG_DEBUG, LOG_WARNING, LOG_ERROR
from helpers import i18n
from helpers.obfuscators import PasswordObfuscator
from helpers.time_utils import makeLocalServerTime
from gui import VERSION_FILE_PATH
from gui.Scaleform.Disconnect import Disconnect
from gui.Scaleform.EULA import EULAInterface
from gui.Scaleform.Waiting import Waiting
from constants import IS_DEVELOPMENT
from external_strings_utils import isAccountLoginValid
from gui.Scaleform.windows import UIInterface
import Offline.Manager
Offline.Manager.init_offline()

class Login(UIInterface):
    __APPLICATION_CLOSE_DELAY_DEFAULT = 15

    def __init__(self):
        self.__user = ''
        self.__host = ''
        self.__rememberPwd = False
        self.__predefinedServers = {}
        self.__publicKeys = {}
        self.__closeCallbackId = None
        self.__eula = EULAInterface()
        UIInterface.__init__(self)
        return

    def populateUI(self, proxy):
        UIInterface.populateUI(self, proxy)
        self.uiHolder.movie.backgroundAlpha = 1.0
        self.uiHolder.addExternalCallbacks({'login.Login': self.onLogin,
         'login.Register': self.onRegister,
         'login.SetRememberPassword': self.onSetRememberPassword,
         'login.ResetOrderInQueue': self.onResetOrderInQueue})
        self.__loadUserConfig()
        self.__loadPredefinedServers(Settings.g_instance.scriptConfig['login'])
        connectionManager.connectionStatusCallbacks += self.__handleConnectionStatus
        connectionManager.onConnected += self.__onConnected
        connectionManager.searchServersCallbacks += self.__serversFind
        connectionManager.startSearchServers()
        connectionManager.onDisconnected -= Disconnect.show
        Disconnect.hide()
        self.setOptions(self.__predefinedServers.items())
        self.__loadVersion()
        self.uiHolder._closeWaiting()
        MusicController.g_musicController.stopAmbient()
        MusicController.g_musicController.play(MusicController.MUSIC_EVENT_LOGIN)
        self.__eula.populateUI(proxy)
        self.uiHolder.call('login.ShowLicense', [self.__eula.isShowLicense()])

    def dispossessUI(self):
        connectionManager.connectionStatusCallbacks -= self.__handleConnectionStatus
        connectionManager.onConnected -= self.__onConnected
        connectionManager.stopSearchServers()
        connectionManager.searchServersCallbacks -= self.__serversFind
        connectionManager.onDisconnected += Disconnect.show
        self.uiHolder.removeExternalCallbacks('login.Login', 'login.Register', 'login.SetRememberPassword', 'login.ResetOrderInQueue')
        self.__eula.dispossessUI()
        UIInterface.dispossessUI(self)

    def __loadVersion(self):
        sec = ResMgr.openSection(VERSION_FILE_PATH)
        version = sec.readString('appname') + ' ' + sec.readString('version')
        self.call('Login.SetVersion', [version])

    def __loadUserConfig(self):
        ds = Settings.g_instance.userPrefs[Settings.KEY_LOGIN_INFO]
        password = ''
        if ds:
            self.__user = ds.readString('user')
            self.__host = ds.readString('host')
            password = ds.readString('password')
            if len(password) > 0 and not ds.has_key('rememberPwd'):
                self.__rememberPwd = True
            else:
                self.__rememberPwd = ds.readBool('rememberPwd', False)
            if self.__rememberPwd:
                password = PasswordObfuscator().unobfuscate(password)
        self.call('login.setDefaultValues', [self.__user, password, self.__rememberPwd])

    def __saveUserConfig(self, user, password, rememberPwd, host):
        up = Settings.g_instance.userPrefs
        if up.has_key(Settings.KEY_LOGIN_INFO):
            li = up[Settings.KEY_LOGIN_INFO]
        else:
            li = up.write(Settings.KEY_LOGIN_INFO, '')
        li.writeString('user', user)
        li.writeString('host', host)
        li.writeBool('rememberPwd', rememberPwd)
        li.writeString('password', PasswordObfuscator().obfuscate(password) if rememberPwd else '')
        Settings.g_instance.save()

    def __loadPredefinedServers(self, dataSection):
        if dataSection:
            for name, host in dataSection.items():
                name = code = key_path = None
                if host.has_key('name'):
                    name = host.readString('name')
                if host.has_key('url'):
                    code = host.readString('url')
                if host.has_key('public_key_path'):
                    key_path = host.readString('public_key_path')
                if code is not None:
                    if name is not None:
                        self.__predefinedServers[code] = name
                    if key_path is not None:
                        self.__publicKeys[code] = key_path

        return

    def __serversFind(self, servers=None):
        list = self.__predefinedServers.items()
        if servers is not None:
            for name, key in servers:
                if key not in self.__predefinedServers.keys():
                    list.append((key, name))

        self.setOptions(list)
        return

    def __handleConnectionStatus(self, stage, status, serverMsg):
        if stage == 1:
            if status != 'LOGGED_ON':
                handlerFunc = self.__logOnFailedHandlers.get(status, self.__logOnFailedDefaultHandler)
                if self.__isAutoLoginTimerSet and status != 'LOGIN_REJECTED_LOGIN_QUEUE':
                    self.__clearAutoLoginTimer()
                try:
                    getattr(self, handlerFunc)(status, serverMsg)
                except:
                    LOG_ERROR('Handle logon status error: status = %r, message = %r' % (status, serverMsg))
                    LOG_CURRENT_EXCEPTION()
                    Waiting.hide()

                if connectionManager.isUpdateClientSoftwareNeeded():
                    self.__handleUpdateClientSoftwareNeeded()
                else:
                    connectionManager.disconnect()
        elif stage == 6:
            self.__setStatus(i18n.convert(i18n.makeString('#menu:login/status/disconnected')))
            connectionManager.disconnect()

    def __onConnected(self):
        LOG_DEBUG('onConnected')

    def __handleUpdateClientSoftwareNeeded(self):
        updateUrl = _getClientUpdateUrl()
        text = i18n.convert(i18n.makeString('#menu:login/updateURLAvaialbleAt')) % updateUrl
        self.__setStatus(text)
        LOG_WARNING('Client software update needed. Update URL: %s' % updateUrl)
        if not IS_DEVELOPMENT:
            self.__closeCallbackId = BigWorld.callback(self.__getApplicationCloseDelay(), BigWorld.quit)
            try:
                BigWorld.wg_openWebBrowser(updateUrl)
            except Exception:
                LOG_CURRENT_EXCEPTION()

    def __getApplicationCloseDelay(self):
        prefs = Settings.g_instance.userPrefs
        if prefs is None:
            delay = Login.__APPLICATION_CLOSE_DELAY_DEFAULT
        else:
            if not prefs.has_key(Settings.APPLICATION_CLOSE_DELAY):
                prefs.writeInt(Settings.APPLICATION_CLOSE_DELAY, Login.__APPLICATION_CLOSE_DELAY_DEFAULT)
            delay = prefs.readInt(Settings.APPLICATION_CLOSE_DELAY)
        return delay

    def setOptions(self, optionsList):
        options = [0]
        options.append('offline server')
        options.append('localhost:2001')
        for i, (key, name) in enumerate(optionsList):
            if key == self.__host:
                options[0] = i + 1
            options.append(name)
            options.append(key)

        self.call('login.setServersList', options)

    def __setStatus(self, status):
        self.call('login.setErrorMessage', [status])
        Waiting.hide()

    __isAutoLoginTimerSet = False

    def __setAutoLoginTimer(self, time):
        self.__isAutoLoginTimerSet = True
        self.call('login.setAutoLoginTimer', [time])

    def __clearAutoLoginTimer(self):
        self.__isAutoLoginTimerSet = False
        self.call('login.clearAutoLoginTimer')

    __logOnFailedHandlers = {'LOGIN_REJECTED_BAN': 'handleLoginRejectedBan',
     'LOGIN_REJECTED_LOGIN_QUEUE': 'handleLoginRejectedQueue',
     'LOGIN_CUSTOM_DEFINED_ERROR': 'handleLoginRejectedBan'}
    __logOnFailedDefaultHandler = 'handleLogOnFailed'
    __minOrderInQueue = 18446744073709551615L

    def handleLogOnFailed(self, status, message):
        errorMessage = i18n.makeString('#menu:login/status/' + status)
        self.__setStatus(errorMessage)

    def handleLoginRejectedBan(self, status, message):
        if message.find(';') != -1:
            expiryTime, reason = message.split(';', 1)
            expiryTime = int(expiryTime)
        else:
            self.handleLoginCustomDefinedError(status, message)
            return
        if reason.startswith('#'):
            reason = i18n.makeString(reason)
        if expiryTime != 0:
            expiryTime = makeLocalServerTime(expiryTime)
            expiryTime = BigWorld.wg_getLongDateFormat(expiryTime) + ' ' + BigWorld.wg_getLongTimeFormat(expiryTime)
            errorMessage = i18n.makeString('#menu:login/status/LOGIN_REJECTED_BAN', time=expiryTime, reason=reason)
        else:
            errorMessage = i18n.makeString('#menu:login/status/LOGIN_REJECTED_BAN_UNLIMITED', reason=reason)
        self.__setStatus(errorMessage)

    def handleLoginRejectedQueue(self, status, message):
        orderInQueue = int(message) + 1
        self.__minOrderInQueue = min(orderInQueue, self.__minOrderInQueue)
        errorMessage = i18n.makeString('#menu:login/status/LOGIN_REJECTED_LOGIN_QUEUE', self.__minOrderInQueue)
        self.__setStatus(errorMessage)
        self.__setAutoLoginTimer(5)

    def handleLoginCustomDefinedError(self, status, message):
        errorMessage = i18n.makeString('#menu:login/status/LOGIN_CUSTOM_DEFINED_ERROR', message)
        self.__setStatus(errorMessage)

    def onLogin(self, id, user, password, host):
        if self.__closeCallbackId:
            BigWorld.cancelCallback(self.__closeCallbackId)
            self.__closeCallbackId = None
        user = user.lower().strip()
        if len(user) <= 1:
            self.__setStatus(i18n.convert(i18n.makeString('#menu:login/status/invalid_login_length')))
            return
        else:
            Waiting.show('#menu:waiting/login')
            password = password.strip()
            self.__saveUserConfig(user, password, self.__rememberPwd, host)
            publicKey = self.__publicKeys.get(host, None)
            connectionManager.connect(host, user, password, publicKey)
            return

    def onRegister(self, callbackID):
        from game import openRegistrationWebsite
        openRegistrationWebsite()

    def onSetRememberPassword(self, requestId, remember):
        self.__rememberPwd = bool(remember)

    def onResetOrderInQueue(self, *args):
        self.__minOrderInQueue = 18446744073709551615L