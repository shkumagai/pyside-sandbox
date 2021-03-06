# -*- coding: utf-8 -*-
import codecs
import logging
import os
import re
import sys
import time
import uuid

from http.cookiejar import Cookie, LWPCookieJar
from contextlib import contextmanager
from functools import wraps

from PyQt5.QtCore import (
    QByteArray,
    QDateTime,
    qInstallMessageHandler,
    QSize,
    QSizeF,
    Qt,
    QtCriticalMsg,
    QtDebugMsg,
    QtFatalMsg,
    QtWarningMsg,
    QUrl,
)
from PyQt5.QtGui import (
    QImage,
    QPainter,
    QRegion,
)
from PyQt5.QtPrintSupport import QPrinter
from PyQt5.QtWidgets import (
    QApplication,
)
from PyQt5.QtNetwork import (
    QNetworkAccessManager,
    QNetworkCookie,
    QNetworkCookieJar,
    QNetworkProxy,
    QNetworkRequest,
)
from PyQt5.QtWebKit import QWebSettings
from PyQt5.QtWebKitWidgets import (
    QWebPage,
    QWebView,
)
from xvfbwrapper import Xvfb

DEFAULT_USERAGENT = (
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_10_5)'
    ' AppleWebKit/537.36 (KHTML, like Gecko)'
    ' CDP/47.0.2526.73 Safari/537.36'
)

logger = logging.getLogger('ghost')
logger.addHandler(logging.NullHandler())


class Error(Exception):
    """Base class for Ghost exceptions."""
    pass


class TimeoutError(Error):
    """Raised when a request times out"""
    pass


class QTMessageProxy(object):
    def __init__(self, logger):
        self.logger = logger

    def __call__(self, msg_type, msg):
        levels = {
            QtDebugMsg: logging.DEBUG,
            QtWarningMsg: logging.WARNING,
            QtCriticalMsg: logging.CRITICAL,
            QtFatalMsg: logging.FATAL,
        }
        self.logger.log(levels[msg_type], msg)


class GhostWebPage(QWebPage):
    """Overrides QtWebKitwidgets.QWebPage in order to intercept some graphical
    behaviours like alert(), confirm0().
    Also intercepts client side console.log().
    """
    def __init__(self, app, session):
        self.session = session
        super(GhostWebPage, self).__init__()

    def choose_file(self, frame, suggested_file=None):
        filename = self.session._upload_file
        self.session.logger.debug('Choosing file %s', filename)
        return filename

    def javaScriptConsoleMessage(self, message, line, source):
        """Prints client console message in current output stream."""
        super(GhostWebPage, self).javaScriptConsoleMessage(
            message,
            line,
            source,
        )
        self.session.logger.log(
            logging.WARNING if "Error" in message else logging.INFO,
            "%s(%d): %s", source or '<unknown>', line, message,
        )

    def javaScriptAlert(self, frame, message):
        """Notifies session for alert, then pass."""
        self.session._alert = message
        self.session.append_popup_message(message)
        self.session.logger.info("alert('%s')", message)

    def _get_value(self, value):
        if callable(value):
            return value()

        return value

    def javaScriptConfirm(self, frame, message):
        """Checks if session is warning for confirm, then returns the right
        value.
        """
        if self.session._confirm_expected is None:
            raise Error(
                'You must specified a value to confirm "%s"' %
                message,
            )
        self.session.append_popup_message(message)
        value = self.session._confirm_expected
        self.session.logger.info("confirm('%s')", message)
        return self._get_value(value)

    def javaScriptPrompt(self, frame, message, default_value, result=None):
        """Checks if ghost is waiting for prompt, then enters the right
        value.
        """
        if self.session._prompt_expected is None:
            raise Error(
                'You must specified a value for prompt "%s"' %
                message,
            )
        self.session.append_popup_message(message)
        value = self.session._prompt_expected
        self.session.logger.info("prompt('%s')", message)
        value = self._get_value(value)
        if value == '':
            self.session.logger.warning(
                "'%s' prompt filled with empty string", message,
            )

        if result is None:
            # PySide
            return True, value

        result.append(str(value))
        return True

    def set_user_agent(self, user_agent):
        self.user_agent = user_agent

    def userAgentForUrl(self, url):
        return self.user_agent


def can_load_page(func):
    """Decorator that specifies if user can expect page loading from
    this action. If expect_loading is set to True, ghost will wait
    for page_loaded event.
    """
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        expect_loading = kwargs.pop('expect_loading', False)
        timeout = kwargs.pop('timeout', None)
        if expect_loading:
            self.loaded = False
            func(self, *args, **kwargs)
            return self.wait_for_page_loaded(
                timeout=timeout)
        return func(self, *args, **kwargs)
    return wrapper


class HttpResource(object):
    """Represents an HTTP resource.
    """
    def __init__(self, session, reply, content):
        self.session = session
        self.url = reply.url().toString()
        self.content = bytes(content.data())
        self.http_status = reply.attribute(
            QNetworkRequest.HttpStatusCodeAttribute)
        self.session.logger.info(
            "Resource loaded: %s %s", self.url, self.http_status,
        )
        self.headers = {}
        for header in reply.rawHeaderList():
            try:
                self.headers[str(header)] = str(
                    reply.rawHeader(header))
            except UnicodeDecodeError:
                # it will lose the header value,
                # but at least not crash the whole process
                self.session.logger.error(
                    "Invalid characters in header %s=%s",
                    header,
                    reply.rawHeader(header),
                )
        self._reply = reply


def replyReadyRead(reply):
    if not hasattr(reply, 'data'):
        reply.data = ''

    reply.data += reply.peek(reply.bytesAvailable())


class NetworkAccessManager(QNetworkAccessManager):
    """Subclass QNetworkAccessManager to always cache the reply content

    :param exclude_regex: A regex use to determine which url exclude
        when sending a request
    """
    def __init__(self, exclude_regex=None, *args, **kwargs):
        self._regex = re.compile(exclude_regex) if exclude_regex else None
        super(NetworkAccessManager, self).__init__(*args, **kwargs)

    def createRequest(self, operation, request, data):
        if self._regex and self._regex.findall(str(request.url().toString())):
            return QNetworkAccessManager.createRequest(
                self, QNetworkAccessManager.GetOperation,
                QNetworkAccessManager(QUrl()))
        reply = QNetworkAccessManager.createRequest(
            self,
            operation,
            request,
            data,
        )
        reply.readyRead.connect(lambda reply=reply: replyReadyRead(reply))
        time.sleep(0.001)
        return reply


class Ghost(object):
    """`Ghost` manages a Qt application.

    :param log_level: The optional logging level.
    :param log_handler: The optional logging hander.
    :param plugin_path: Array with paths to plugin directories
        (default ['/usr/lib/mozilla/plugins'])
    :param defaults: The defaults arguments to pass to new child sessions.
    """
    _app = None

    def __init__(
        self,
        plugin_path=['/usr/lib/mozilla/plugins'],
        defaults=None,
        display_size=(1600, 900),
    ):
        self.logger = logger.getChild('application')

        if (
            sys.platform.startswith('linux') and
            'DISPLAY' not in os.environ
        ):
            try:
                self.xvfb = Xvfb(
                    width=display_size[0],
                    height=display_size[1],
                )
                self.xvfb.start()

            except OSError:
                raise Error('Xvfb is required to a ghost run outside ' +
                            'an X instance')

        self.logger.info('Initializing Qt application')
        Ghost._app = QApplication.instance() or QApplication(['ghost'])

        qInstallMessageHandler(QTMessageProxy(logging.getLogger('qt')))

        if plugin_path:
            for p in plugin_path:
                Ghost._app.addLibraryPath(p)

        self.display_size = display_size
        _defaults = dict(viewport_size=display_size)
        _defaults.update(defaults or dict())
        self.defaults = _defaults

    def exit(self):
        self._app.quit()
        if hasattr(self, 'xvfb'):
            self.xvfb.stop()

    def start(self, **kwargs):
        """Starts a new `Session`."""
        _kwargs = self.defaults.copy()
        _kwargs.update(kwargs)
        return Session(self, **_kwargs)

    def __del__(self):
        self.exit()


class Session(object):
    """`Session` manages a QWebPage.

    :param ghost: The parent `Ghost` instance.
    :param user_agent: The default User-Agent header.
    :param wait_timeout: Maximum step duration in second.
    :param wait_callback: An optional callable that is periodically
        executed until Ghost stops waiting.
    :param log_level: The optional logging level.
    :param log_heander: The optional logging hander.
    :param display: A boolean that tells ghost to display UI.
    :param viewport_size: A tuple that sets initial viewport size.
    :param ignore_ssl_errors: A boolean that forces ignore SSL errors.
    :param plugins_enabled: Enable plugins (like Flash).
    :param java_enabled: Enable Java JRE.
    :param download_images: Indicate if the browser should download images
    :param exclude: A regex use to determine which url exclude
        when sending a request
    :param local_storage_enabled: An optional boolean to enable / disable
        local storage.
    """
    _alert = None
    _confirm_expected = None
    _prompt_expected = None
    _upload_file = None
    _app = None

    def __init__(
        self,
        ghost,
        user_agent=DEFAULT_USERAGENT,
        wait_timeout=0,
        wait_callback=None,
        display=False,
        viewport_size=None,
        ignore_ssl_errors=True,
        plugins_enabled=False,
        java_enabled=False,
        javascript_enabled=True,
        download_images=True,
        show_scrollbars=True,
        exclude=None,
        network_access_manager_class=NetworkAccessManager,
        web_page_class=GhostWebPage,
        local_storage_enabled=True,
    ):
        self.ghost = ghost

        self.id = str(uuid.uuid4())

        self.logger = logging.LoggerAdapter(
            logger.getChild('session'),
            {'session': self.id},
        )
        self.logger.info("Starting new session")

        self.http_resources = []

        self.wait_timeout = wait_timeout
        self.wait_callback = wait_callback
        self.ignore_ssl_errors = ignore_ssl_errors
        self.loaded = True

        self.display = display

        self.popup_messages = []
        self.page = web_page_class(self.ghost._app, self)

        if network_access_manager_class is not None:
            self.page.setNetworkAccessManager(network_access_manager_class(exclude_regex=exclude))

        QWebSettings.setMaximumPagesInCache(0)
        QWebSettings.setObjectCacheCapacities(0, 0, 0)
        QWebSettings.globalSettings().setAttribute(QWebSettings.LocalStorageEnabled, local_storage_enabled)

        self.page.setForwardUnsupportedContent(True)
        self.page.settings().setAttribute(QWebSettings.AutoLoadImages, download_images)
        self.page.settings().setAttribute(QWebSettings.PluginsEnabled, plugins_enabled)
        self.page.settings().setAttribute(QWebSettings.JavaEnabled, java_enabled)
        self.page.settings().setAttribute(QWebSettings.JavascriptEnabled, javascript_enabled)

        if not show_scrollbars:
            self.page.mainFrame().setScrollBarPolicy(Qt.Vertical, Qt.ScrollBarAlwaysOff, )
            self.page.mainFrame().setScrollBarPolicy(Qt.Horizontal, Qt.ScrollBarAlwaysOff, )

        # page signals
        self.page.loadFinished.connect(self._page_loaded)
        self.page.loadStarted.connect(self._page_load_started)
        self.page.unsupportedContent.connect(self._unsupported_content)

        self.manager = self.page.networkAccessManager()
        self.manager.finished.connect(self._request_ended)
        self.manager.sslErrors.connect(self._on_manager_ssl_errors)

        # Cookie jar
        self.cookie_jar = QNetworkCookieJar()
        self.manager.setCookieJar(self.cookie_jar)

        # User Agent
        self.page.set_user_agent(user_agent)

        self.page.networkAccessManager().authenticationRequired.connect(self._authenticate)
        self.page.networkAccessManager().proxyAuthenticationRequired.connect(self._authenticate)

        self.main_frame = self.page.mainFrame()

        class GhostQWebView(QWebView):
            def sizeHint(self):
                return QSize(*viewport_size)

        self.webview = GhostQWebView()

        self.set_viewport_size(*viewport_size)

        if plugins_enabled:
            self.webview.settings().setAttribute(QWebSettings.PluginsEnabled, True)
        if java_enabled:
            self.webview.settings().setAttribute(QWebSettings.JavaEnabled, True)

        self.webview.setPage(self.page)

        if self.display:
            self.show()

    def frame(self, selector=None):
        """Set main frame as current main frame's parent.

        :param frame: An optional name or index of the child to descend to.
        """
        if isinstance(selector, str):
            for frame in self.main_frame.childFrames():
                if frame.frameName() == selector:
                    self.main_frame = frame
                    return
            # frame not found so we throw an exception
            raise LookupError(
                "Child frame for name '%s' not found." % selector,
            )

        if isinstance(selector, int):
            try:
                self.main_frame = self.main_frame.childFrames()[selector]
                return
            except IndexError:
                raise LookupError(
                    "Child frame at index '%s' not found." % selector,
                )

        # we can't ascend directly to parent frame because it might have been
        # deleted
        self.main_frame = self.page.mainFrame()

    @can_load_page
    def call(self, selector, method):
        """Call method on element matching given selector.

        :param selector: A CSS selector to the target element.
        :param method: The name of the method to call.
        :param expect_loading: Specifies if a page loading is expected.
        """
        self.logger.debug('Calling `%s` method on `%s`', method, selector)
        element = self.main_frame.findFirstElement(selector)
        return element.evaluateJavaScript('this[%s]();' % repr(method))

    def capture(
        self,
        region=None,
        selector=None,
        format=None,
    ):
        """Returns snapshot as QImage.

        :param region: An optional tuple containing region as pixel
            coordinates.
        :param selector: A selector targeted the element to crop on.
        :param format: The output image format.
        """
        if format is None:
            format = QImage.Format_ARGB32_Premultiplied

        self.main_frame.setScrollBarPolicy(
            Qt.Vertical,
            Qt.ScrollBarAlwaysOff,
        )
        self.main_frame.setScrollBarPolicy(
            Qt.Horizontal,
            Qt.ScrollBarAlwaysOff,
        )
        frame_size = self.main_frame.contentsSize()
        max_size = 23170 * 23170
        if frame_size.height() * frame_size.width() > max_size:
            self.logger.warning("Frame size is too large.")
            default_size = self.page.viewportSize()
            if default_size.height() * default_size.width() > max_size:
                return None
        else:
            self.page.setViewportSize(self.main_frame.contentsSize())

        self.logger.info("Frame size -> %s", str(self.page.viewportSize()))

        image = QImage(self.page.viewportSize(), format)
        painter = QPainter(image)

        if region is None and selector is not None:
            region = self.region_for_selector(selector)

        if region:
            x1, y1, x2, y2 = region
            w, h = (x2 - x1), (y2 - y1)
            reg = QRegion(x1, y1, w, h)
            self.main_frame.render(painter, reg)
        else:
            self.main_frame.render(painter)

        painter.end()

        if region:
            x1, y1, x2, y2 = region
            w, h = (x2 - x1), (y2 - y1)
            image = image.copy(x1, y1, w, h)

        return image

    def capture_to(
        self,
        path,
        region=None,
        selector=None,
        format=None,
    ):
        """Saves snapshot as image.

        :param path: The destination path.
        :param region: An optional tuple containing region as pixel
            coordinates.
        :param selector: A selector targeted the element to crop on.
        :param format: The output image format.
        """
        if format is None:
            format = QImage.Format_ARGB32_Premultiplied

        self.capture(region=region, format=format, selector=selector).save(path)

    def print_to_pdf(
        self,
        path,
        paper_size=(8.5, 11.0),
        paper_margins=(0, 0, 0, 0),
        paper_units=None,
        zoom_factor=1.0,
    ):
        """Saves page as a pdf file.

        :param path: The destination path.
        :param paper_size: A 2-tuple indicating size of page to print to.
        :param paper_margins: A 4-tuple indicating size of each margins.
        :param paper_units: Units for paper size, paper margins.
        :param zoom_factor: Scale the output content.
        """
        assert len(paper_size) == 2
        assert len(paper_margins) == 4

        if paper_units is None:
            paper_units = QPrinter.Inch

        printer = QPrinter(mode=QPrinter.ScreenResolution)
        printer.setOutputFromat(QPrinter.PdfFormat)
        printer.setPaperSize(QSizeF(*paper_size), paper_units)
        printer.setPageMargins(*(paper_margins + (paper_units,)))

        if paper_margins != (0, 0, 0, 0):
            printer.setFullPage(True)
        printer.setOutputFileName(path)
        if self.webview is None:
            self.webview = QWebView()
            self.webview.setPage(self.page)
        self.webview.setZoomFactor(zoom_factor)
        self.webview.print_(printer)

    @can_load_page
    def click(self, selector, btn=0):
        """Click the targeted element.

        :param selector: A CSS3 selector to targeted element.
        :param btn: The number of mouse button.
            0 - left button,
            1 - middle button,
            2 - right button
        """
        if not self.exists(selector):
            raise Error("Can't find element to click")

        return self.evaluate("""
            (function () {
                var element = document.querySelector(%s);
                var evt = document.createEvent("MouseEvents");
                evt.initMouseEvent("click", true, true, window, 1, 1, 1, 1, 1,
                    false, false, false, false, %s, element);
                return element.dispatchEvent(evt);
            })();
        """ % (repr(selector), str(btn)))

    @contextmanager
    def confirm(self, confirm=True):
        """Statement that tells Ghost how to deal with javascript confirm().

        :param confirm: A boolean or a callable to set confirmation.
        """
        self._confirm_expected = confirm
        yield
        self._confirm_expected = None

    @property
    def content(self, to_unicode=True):
        """Returns current frame HTML as a string.

        :param to_unicode: Whether to convert html to unocode or not.
        """
        if to_unicode:
            return str(self.main_frame.toHtml())
        else:
            return self.main_frame.toHtml()

    @property
    def cookies(self):
        """Returns all cookies."""
        return self.cookie_jar.allCookies()

    def delete_cookies(self):
        """Deletes all cookies."""
        self.cookie_jar.setAllCookies([])

    def clear_alert_message(self):
        """Clears the alert message"""
        self._alert = None

    @can_load_page
    def evaluate(self, script):
        """Evaluates script in page frame.

        :param script: The script to evaluate.
        """
        return (
            self.main_frame.evaluateJavaScript("%s" % script),
            self._release_last_resources(),
        )

    def evaluate_js_file(self, path, encoding='utf-8', **kwargs):
        """Evaluates javascript file at given path in current frame.
        Raises native IOException in case of invalid file.

        :param path: The path of the file.
        :param encoding: The file's encoding.
        """
        with codecs.open(path, encoding=encoding) as f:
            return self.evaluate(f.read(), **kwargs)

    def exists(self, selector):
        """Checks if element exists for given selector.

        :param selector: The element selector.
        """
        return not self.main_frame.findFirstElement(selector).isNull()

    def exit(self):
        """Exits all Qt Widgets."""
        self.logger.info("Closing session")
        self.page.deleteLater()
        self.sleep()
        del self.webview
        del self.cookie_jar
        del self.manager
        del self.main_frame

    @can_load_page
    def fill(self, selector, values):
        """Fills a form with provided values.

        :param selector: A CSS selector to the target form to fill.
        :param values: A dict containing the values.
        """
        if not self.exists(selector):
            raise Error("Can't find form")
        resources = []
        for field in values:
            r, res = self.set_field_value(
                "%s [name=%s]" % (selector, repr(field)), values[field])
            resources.append(res)
        return True, resources

    @can_load_page
    def fire(self, selector, event):
        """Fire `event` on element at `selector`

        :param selector: A selector to target the element.
        :param event: The name of the event to trigger.
        """
        self.logger.debug('Fire `%s` on `%s`', event, selector)
        element = self.main_frame.findFirstElement(selector)
        return element.evaluateJavaScript("""
            var event = document.createEvent("HTMLEvents");
            event.initEvent('%s', true, true);
            this.dispatchEvent(event);
        """ % event)

    def global_exists(self, global_name):
        """Checks if javascript global exists.

        :param global_name: The name of the global.
        """
        return self.evaluate(
            '!(typeof this[%s] === "undefined");'
            % repr(global_name)
        )[0]

    def hide(self):
        """Close the webview."""
        try:
            self.webview.close()
        except:
            raise Error("no webview to close")

    def load_cookies(self, cookie_storage, keep_old=False):
        """load from cookielib's CookieJar or Set-Cookie3 format text file.

        :param cookie_storage: file location string on disk or CookieJar
            instance.
        :param keep_old: Don't reset, keep cookies not overridden.
        """
        def toQtCookieJar(PyCookieJar, QtCookieJar):
            allCookies = QtCookieJar.allCookies() if keep_old else []
            for pc in PyCookieJar:
                qc = toQtCookie(pc)
                allCookies.append(qc)
            QtCookieJar.setAllCookies(allCookies)

        def toQtCookie(PyCookie):
            qc = QNetworkCookie(PyCookie.name, PyCookie.value)
            qc.setSecure(PyCookie.secure)
            if PyCookie.path_specified:
                qc.setPath(PyCookie.path)
            if PyCookie.domain != "":
                qc.setDomain(PyCookie.domain)
            if PyCookie.expires and PyCookie.expires != 0:
                t = QDateTime()
                t.setTime_t(PyCookie.expires)
                qc.setExpirationDate(t)
            # not yet handled(maybe less useful):
            #   py cookie.rest / QNetworkCookie.setHttpOnly()
            return qc

        if cookie_storage.__class__.__name__ == 'str':
            cj = LWPCookieJar(cookie_storage)
            cj.load()
            toQtCookieJar(cj, self.cookie_jar)
        elif cookie_storage.__class__.__name__.endswith('CookieJar'):
            toQtCookieJar(cookie_storage, self.cookie_jar)
        else:
            raise ValueError('unsupported cookie_storage type.')

    def open(
        self,
        address,
        method='get',
        headers={},
        auth=None,
        body=None,
        default_popup_response=None,
        wait=True,
        timeout=None,
        encode_url=True,
        user_agent=None,
    ):
        """Opens a web page.

        :param address: The resource URL.
        :param method: The Http method.
        :param headers: An optional dict of extra request headers.
        :param auth: An optional tuple of HTTP auth (username, password).
        :param body: An optional string containing a payload.
        :param default_popup_response: the default response for any confirm/
            alert/prompt popup from the Javascript (replaces the need for the
            with blocks)
        :param wait: If set to True (which is the default), this method
            call waits for the page load to complete becore returning.
            Otherwise, it just starts the page load task and it is the
            caller's responsibility to wait for the load to finish by other
            means (e.g. by calling wait_for_page_loaded()).
        :param timeout: An optional timeout.
        :param encode_url: Set to true if the url have to be encoded
        :param user_agent: An optional User-Agent string.
        :return: Page resource, and all loaded resources, unless wait
            is False, in which case it returns None.
        """
        self.logger.info('Opening %s', address)
        body = body or QByteArray()
        try:
            method = getattr(QNetworkAccessManager, "%sOperation" % method.capitalize())
        except AttributeError:
            raise Error("Invalid http method %s" % method)

        if user_agent is not None:
            self.page.set_user_agent(user_agent)

        if encode_url:
            request = QNetworkRequest(QUrl(address))
        else:
            request = QNetworkRequest(QUrl.fromEncoded(address))
        request.CacheLoadControl(0)
        for header in headers:
            request.setRawHeader(header, headers[header])
        self._auth = auth
        self._auth_attempt = 0  # Avoids reccursion

        self.main_frame.load(request, method, body)
        self.loaded = False

        if default_popup_response is not None:
            self._prompt_expected = default_popup_response
            self._confirm_expected = default_popup_response

        if wait:
            print('waiting ... (in %d seconds)' % timeout)
            return self.wait_for_page_loaded(timeout=timeout)

    def scroll_to_anchor(self, anchor):
        self.main_frame.scrollToAnchor(anchor)

    @contextmanager
    def prompt(self, value=''):
        """Statement that tells Ghost how to deal with javascript prompt().

        :param value: A string or a callable value to fill in prompt.
        """
        self._prompt_expected = value
        yield
        self._prompt_expected = None

    def region_for_selector(self, selector):
        """Returns frame region for given selector as tuple.

        :param selector: The targeted element.
        """
        geo = self.main_frame.findFirstElement(selector).geometry()
        try:
            region = (geo.left(), geo.top(), geo.right(), geo.bottom())
        except:
            raise Error("Can't get region for selector '%s'" % selector)
        return region

    def save_cookies(self, cookie_storage):
        """Save to cookielib's CookieJar or Set-Cookie3 format text file.

        :param cookie_storage: file location string or CookieJar instance.
        """
        def toPyCookieJar(QtCookieJar, PyCookieJar):
            for c in QtCookieJar.allCookies():
                PyCookieJar.set_cookie(toPyCookie(c))

        def toPyCookie(QtCookie):
            port = None
            port_specified = False
            secure = QtCookie.isSecure()
            name = str(QtCookie.name())
            value = str(QtCookie.value())

            v = str(QtCookie.path())
            path_specified = bool(v != "")
            path = v if path_specified else None

            v = str(QtCookie.domain())
            domain_specified = bool(v != "")
            domain = v
            if domain_specified:
                domain_initial_dot = v.startswith('.')
            else:
                domain_initial_dot = None
            v = int(QtCookie.expirationDate().toTime_t())
            # Long type boundary on 32bit platforms; avoid ValueError
            expires = 2147483647 if v > 2147483647 else v
            rest = {}
            discard = False
            return Cookie(
                0,
                name,
                value,
                port,
                port_specified,
                domain,
                domain_specified,
                domain_initial_dot,
                path,
                path_specified,
                secure,
                expires,
                discard,
                None,
                None,
                rest,
            )

        if cookie_storage.__class__.__name__ == 'str':
            cj = LWPCookieJar(cookie_storage)
            toPyCookieJar(self.cookie_jar, cj)
            cj.save()
        elif cookie_storage.__class__.__name__.endswith('CookieJar'):
            toPyCookieJar(self.cookie_jar, cookie_storage)
        else:
            raise ValueError('unsupported cookie_storage type.')

    @can_load_page
    def set_field_value(self, selector, value, blur=True):
        """Sets the value of the field matched by given selector.

        :param selector: A CSS selector that target the field.
        :param value: The value to fill in.
        :param blur: An optional boolean that force blur when filled in.
        """
        self.logger.debug('Setting value "%s" for "%s"', value, selector)

        def _set_checkbox_value(el, value):
            el.setFocus()
            if value is True:
                el.setAttribute('checked', 'checked')
            else:
                el.removeAttribute('checked')

        def _set_checkboxes_value(els, value):
            for el in els:
                if el.attribute('value') == value:
                    _set_checkbox_value(el, True)
                else:
                    _set_checkbox_value(el, False)

        def _set_radio_value(els, value):
            for el in els:
                if el.attribute('value') == value:
                    el.setFocus()
                    el.setAttribute('checked', 'checked')

        def _set_text_value(el, value):
            el.setFocus()
            el.setAttribute('value', value)

        def _set_select_value(el, value):
            el.setFocus()
            index = 0
            for option in el.findAll('option'):
                if option.attribute('value') == value:
                    option.evaluateJavaScript('this.selected = true;')
                    el.evaluateJavaScript('this.selectedIndex = %d' % index)
                    break
                index += 1

        def _set_textarea_value(el, value):
            el.setFocus()
            el.setPlainText(value)

        res, resources = None, []
        element = self.main_frame.findFirstElement(selector)
        if element.isNull():
            raise Error('can\'t find element for "%s"' % selector)

        tag_name = str(element.tagName()).lower()

        if tag_name == "select":
            _set_select_value(element, value)
        elif tag_name == "textarea":
            _set_textarea_value(element, value)
        elif tag_name == "input":
            type_ = str(element.attribute('type')).lower()
            if type_ in (
                "color",
                "date",
                "datatime",
                "datetime-local",
                "email",
                "hidden",
                "month",
                "number",
                "password",
                "range",
                "search",
                "tel",
                "text",
                "time",
                "url",
                "week",
                "",
            ):
                _set_text_value(element, value)
            elif type_ == "checkbox":
                els = self.main_frame.findAllElement(selector)
                if els.count() > 1:
                    _set_checkboxes_value(els, value)
                else:
                    _set_checkbox_value(element, value)
            elif type_ == "radio":
                _set_radio_value(
                    self.main_frame.findAllElement(selector),
                    value,
                )
            elif type_ == "file":
                self._upload_file = value
                res, resources = self.click(selector)
                self._upload_file = None
        else:
            raise Error('unsupported field tag')

        for event in ('input', 'change'):
            self.fire(selector, event)

        if blur:
            self.call(selector, 'blur')

        return res, resources

    def set_proxy(
        self,
        type_,
        host='localhost',
        port=8888,
        user='',
        password='',
    ):
        """Set up proxy for FURTHER connections.

        :param type_: proxy type to use: \
            none/socks5/https/http/default.
        :param host: proxy server ip or host name.
        :param port: proxy port.
        """
        _types = {
            'default': QNetworkProxy.DefaultProxy,
            'none': QNetworkProxy.NoProxy,
            'socks5': QNetworkProxy.SocksProxy,
            'https': QNetworkProxy.HttpProxy,
            'http': QNetworkProxy.HttpCacheProxy,
        }

        if type_ is None:
            type_ = 'none'
        type_ = type_.lower()
        if type_ in ('none', 'default'):
            self.manager.setProxy(QNetworkProxy(_types[type_]))
            return
        elif type_ in _types:
            proxy = QNetworkProxy(
                _types[type_],
                hostName=host,
                port=port,
                user=user,
                password=password,
            )
            self.manager.setProxy(proxy)
        else:
            raise ValueError((
                'Unsupported proxy type: %s' % type_,
                '\nsupported types are: none/socks5/https/http/default',
            ))

    def set_viewport_size(self, width, height):
        """Sets the page viewport size.

        :param width: An integer that sets width pixel count.
        :param height: An integer that sets height pixel count.
        """
        new_size = QSize(width, height)

        self.webview.resize(new_size)
        self.page.setPreferredContentsSize(new_size)
        self.page.setViewportSize(new_size)

        self.sleep()

    def append_popup_message(self, message):
        self.popup_messages.append(str(message))

    def show(self):
        """Show current page inside a QWebView."""
        self.logger.debug('Showing webview')
        self.webview.show()
        self.sleep()

    def sleep(self, value=0.1):
        started_at = time.time()

        while time.time() <= (started_at + value):
            time.sleep(0.01)
            self.ghost._app.processEvents()

    def wait_for(self, condition, timeout_message, timeout=None):
        """Waits until condition is True.

        :param condition: A callable that returns the condition.
        :param timeout_message: The exception message on timeout.
        :param timeout: An optional timeout.
        """
        timeout = self.wait_timeout if timeout is None else timeout
        started_at = time.time()

        while not condition():
            if time.time() > (started_at + timeout):
                raise TimeoutError(timeout_message)
            self.sleep()
            if self.wait_callback is not None:
                self.wait_callback()

    def wait_for_alert(self, timeout=None):
        """Waits for main frame alert().

        :param timeout: An optional timeout.
        """
        self.wait_for(lambda: self._alert is not None, 'User has not been alerted.', timeout)
        msg = self._alert
        self._alert = None
        return msg, self._release_last_resources()

    def wait_for_page_loaded(self, timeout=None):
        """Waits until page is loaded, assumed that a page as been required.

        :param timtout: An optional timeout.
        """
        self.wait_for(lambda: self.loaded, 'Unable to load requested page', timeout)
        resources = self._release_last_resources()
        page = None

        url = self.main_frame.url().toString()
        url_without_hash = url.split("#")[0]

        for resource in resources:
            if url == resource.url or url_without_hash == resource.url:
                page = resource
        self.logger.info('Page adloed %s', url)

        return page, resources

    def wait_for_selector(self, selector, timeout=None):
        """Waits until selector match an element on the frame.

        :param selector: The selector to wait for.
        :param timeout: An optional timeout.
        """
        self.wait_for(
            lambda: self.exists(selector),
            'Can\'t find element matching "%s"' % selector,
            timeout,
        )
        return True, self._release_last_resources()

    def wait_while_selector(self, selector, timeout=None):
        """Waits until the selector no longer matchies an element on the frame.

        :param selector: The selector to wait for.
        :param timeout: An optional timeout.
        """
        self.wait_for(
            lambda: not self.exists(selector),
            'Element matching "%s" is still available' % selector,
            timeout,
        )
        return True, self._release_last_resources()

    def wait_for_text(self, text, timeout=None):
        """Waits until given text appear on main frame.

        :param text: The text to wait for.
        :param timeout: An optional timeout.
        """
        self.wait_for(
            lambda: text in self.content,
            'Can\'t find "%s" in current frame' % text,
            timeout,
        )
        return True, self._release_last_resources()

    def _authenticate(self, mix, authenticator):
        """Called back on basic / proxy http auth.

        :param mix: The QNetworkReply or QNetworkProxy object.
        :param authenticator: The QAuthenticator object.
        """
        if self._auth is not None and self._auth_attempt == 0:
            username, password = self._auth
            authenticator.serUser(username)
            authenticator.setPassword(password)
            self._auth_attempt += 1

    def _page_loaded(self):
        """Called back when page loaded."""
        self.loaded = True
        self.sleep()

    def _page_load_started(self):
        """Called back when page load started."""
        self.loaded = False

    def _release_last_resources(self):
        """Releases last loaded resources.

        :return: The released resources.
        """
        last_resources = self.http_resources
        self.http_resources = []
        return last_resources

    def _request_ended(self, reply):
        """Adds an HttpResource object to http_resources.

        :param reply: The QNetworkReply object.
        """
        if reply.attribute(QNetworkRequest.HttpStatusCodeAttribute):
            self.logger.debug("[%s] bytesAvailable()=%s",
                              str(reply.url()),
                              reply.bytesAvailable())
            try:
                content = reply.data
            except AttributeError:
                content = reply.readAll()

            self.http_resources.append(HttpResource(
                self,
                reply,
                content=content,
            ))

    def _unsupported_content(self, reply):
        self.logger.info("Unsupported content %s", str(reply.url()))

        reply.readyRead.connect(
            lambda reply=reply: self._reply_download_content(reply))

    def _reply_download_content(self, reply):
        """Adds an HttpResource object to http_resources with unsupported
        content.

        :param reply: The QNetworkReply object.
        """
        if reply.attribute(QNetworkRequest.HttpStatusCodeAttribute):
            self.http_resources.append(HttpResource(
                self,
                reply,
                reply.readAll(),
            ))

    def _on_manager_ssl_errors(self, reply, errors):
        url = str(reply.url().toString())
        if self.ignore_ssl_errors:
            reply.ignoreSslErrors()
        else:
            self.logger.warning('SSL certificate error: %s', url)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.exit()
